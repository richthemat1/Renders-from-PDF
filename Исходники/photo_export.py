"""Offline image export with generic captions, tile assembly and safe fallbacks."""
import argparse
from collections import Counter, defaultdict
import hashlib
import html
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from threading import RLock
from urllib.parse import quote

import pdfplumber
from pdfminer.layout import LTContainer, LTFigure, LTImage
from pdfminer.utils import mult_matrix
from PIL import Image
import pypdf.filters
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, IndirectObject
from reportlab import rl_config

from photo_layout import (bounds, caption_for, caption_with_variant_context, clean_text,
                          prepare_captions, prepare_selected_variants, prepare_variant_context,
                          region_is_clipped, selected_caption_with_variant, SELECTION_REFERENCE,
                          text_lines, tile_groups, found_variant, variant_numbers, variant_sort_key)
from photo_scan import (analyse_scans, blank_layer_ink, BLANK_DRAWN_SHARE, column_caption, drawn_share, is_large_raster,
                        LARGE_RASTER_PIXELS, native_jpeg, open_large_raster, SCRATCH_PNG_LEVEL,
                        prepare_scan_continuations, seam_caption, whole_scan_caption)

# A single poster JPEG can exceed pypdf's 75 MB raw-stream default. A declared length can never
# make pypdf read past the end of the file, so this cap does not admit inflation bombs; limits on
# decompressed output stay at pypdf's defaults.
pypdf.filters.MAX_DECLARED_STREAM_LENGTH = max(pypdf.filters.MAX_DECLARED_STREAM_LENGTH, 2_000_000_000)
# Image streams stay binary. ASCII85 text adds a quarter to every PDF and, without ReportLab's optional
# C accelerator, encodes each image byte in pure Python: it was the slowest step of an export.
rl_config.useA85 = 0
# Whole poster-size rasters are kept at a reduced JPEG scale within this budget.
LARGE_WHOLE_PIXELS = 25_000_000
_large_raster_cache = {}

VERSION = '3.37'
LAYOUT = 'variants-root'
# 3.12 kept every variant inside «Варианты»; such results are still edited in place.
GROUPED_LAYOUT = 'grouped'
VARIANTS_DIR = 'Варианты'
OTHER_DIR = 'Все остальное'
SERVICE_DIR = '_Служебное'
PERSPECTIVE_TITLE = re.compile(r'^\W*перспективн\w*\s+изображени', re.I)
OUTDATED_VARIANT = re.compile(r'\b(?:предыдущ|первоначальн|прежн|устаревш|архивн|отклон[её]нн|неактуальн)\w*'
                              r'|\bне\s+принят\w*', re.I)
SUPPRESSED_DIR = 'Служебные элементы'
# The whole picture of every collage is kept here, so its cut can be checked, changed or undone.
COLLAGES_DIR = 'Склейки'
CUTOUTS_DIR = 'Вырезки'
PDFIUM_LOCK = RLock()


def perspective_conflict(pages):
    """Return the presentation's own evidence that variant 1 is not the current design, if any."""
    for page in pages:
        for line in page['lines']:
            if variant_numbers(line['text']) == {1} and OUTDATED_VARIANT.search(line['text']):
                text = line['text'] if len(line['text']) <= 80 else line['text'][:79].rstrip()+'…'
                return {'kind': 'outdated_variant', 'page': page['number'], 'text': line['text'],
                        'reason': f'вариант 1 в презентации помечен как «{text}» (страница {page["number"]})'}
    selected = next((page['selected_variant'] for page in pages if page.get('selected_variant')), None)
    if selected and selected['variant'] != 1:
        return {'kind': 'selected_variant', 'page': selected['page'], 'variant': selected['variant'],
                'reason': f'в таблице на странице {selected["page"]} выбран вариант {selected["variant"]}'}
    return None


def group_caption(match, conflict=None):
    """Keep the full caption, but group every explicit variant by its number."""
    match = dict(match, variant=None)
    if match['status'] != 'named':
        return match, 'На проверку' if match['status'] == 'review' else 'Без подписи'
    numbers = variant_numbers(match['title'])
    if len(numbers) > 1:
        match.update(status='review', candidates=[match['title']],
                     reason='В подписи указано несколько вариантов; общий номер не выбран.')
        return match, 'На проверку'
    if numbers:
        match['variant'] = numbers.pop()
        return match, f'Вариант {match["variant"]}'
    if PERSPECTIVE_TITLE.match(match['title']) and conflict is not None:
        # The variant 1 default is a guess; the presentation itself contradicts it, so a person decides.
        match.update(status='review', candidates=[match['title']], variant_source='perspective_conflict',
                     reason=(match.get('reason') or '')+' Номер варианта не указан. Перспективное изображение '
                            f'не отнесено к варианту 1: {conflict["reason"]}. Перенесите его в нужный вариант.')
        return match, 'На проверку'
    if PERSPECTIVE_TITLE.match(match['title']):
        # A perspective view without any variant number in its caption, page or section belongs to variant 1.
        match.update(variant=1, variant_source='perspective_default',
                     reason=(match.get('reason') or '')+' Номер варианта не указан; перспективное изображение '
                            'отнесено к варианту 1.')
        return match, 'Вариант 1'
    return match, match['title']


def region_caption(page, box):
    match = caption_for(page, box)
    match = whole_scan_caption(page, match)
    match = column_caption(page, box, match)
    match = selected_caption_with_variant(page, match)
    return caption_with_variant_context(page, box, match)


def entry_group(entry):
    return entry['variant']


def group_dir(folder, key):
    """Variants sit at the top of the result; every other folder goes under one shared folder."""
    if key is not None:
        return Path(folder)
    return Path(OTHER_DIR)/folder


def group_file(report, key):
    name = group_title(key, report)
    if report.get('layout') == GROUPED_LAYOUT:
        return Path(VARIANTS_DIR)/name/f'{name}.pdf'
    return Path(name)/f'{name}.pdf'


def has_service_dir(report):
    return report.get('layout') in (LAYOUT, GROUPED_LAYOUT)


def group_title(key, report=None):
    """A variant keeps its number; a manual rename only changes the name of its folder and PDF."""
    return ((report or {}).get('variant_names') or {}).get(str(key), f'Вариант {key}')


def drop_empty_folders(folder, root):
    """Remove folder and its empty parents up to root, after its only picture was set aside."""
    folder, root = Path(folder), Path(root)
    while folder != root and root in folder.parents and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def hide(path):
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)


def image_to_pdf(image_path, target, bbox, title, matrix=None):
    """One image per PDF; native image pixels and the source display dimensions."""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    width, height = bbox[2]-bbox[0], bbox[3]-bbox[1]
    if width <= 0 or height <= 0:
        raise ValueError('Размер области изображения должен быть положительным.')
    document = canvas.Canvas(str(target), pagesize=(width, height), pageCompression=1)
    document.setTitle(title or 'Изображение из PDF')
    document.setCreator(f'PDF Photos {VERSION}')
    if matrix is not None and (matrix[1] or matrix[2] or matrix[0] < 0 or matrix[3] < 0):
        a, b, c, d = matrix[:4]
        sx, sy = width/(abs(a)+abs(c)), height/(abs(b)+abs(d))
        document.transform(a*sx, b*sy, c*sx, d*sy,
                           -min(0, a, c, a+c)*sx, -min(0, b, d, b+d)*sy)
        document.drawImage(ImageReader(str(image_path)), 0, 0, width=1, height=1, mask='auto')
    else:
        document.drawImage(ImageReader(str(image_path)), 0, 0, width=width, height=height, mask='auto')
    document.showPage()
    document.save()


def safe_name(text, limit=80):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' — ', clean_text(text))
    text = re.sub(r'\s+', ' ', text).strip(' .') or 'Без подписи'
    if len(text) > limit:
        if limit >= 40:
            tail = text[-24:]
            if len(text) > 24 and not text[-25].isspace() and ' ' in tail:
                tail = tail.split(' ', 1)[1]
            head = text[:limit-len(tail)-3]
            if len(head) < len(text) and not text[len(head)].isspace() and ' ' in head:
                head = head.rsplit(' ', 1)[0]
            text = head.rstrip(' .—–-')+' … '+tail.lstrip(' .—–-')
        else:
            shortened = text[:limit]
            if not text[limit].isspace() and ' ' in shortened:
                shortened = shortened.rsplit(' ', 1)[0]
            text = shortened.rstrip(' .—–-')
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', text, re.I):
        text = '_'+text
    return text


def available_folder_name(title, limit, owners, key):
    """Readable disk names stay distinct through numeric suffixes, never opaque hashes."""
    for number in range(1, 10000):
        suffix = '' if number == 1 else f' ({number})'
        folder = safe_name(title, limit-len(suffix))+suffix
        if folder.casefold() not in owners or owners[folder.casefold()] == key:
            return folder
    raise OutputPathError('Не удалось подобрать свободное имя папки результата.')


def unused_folder(path):
    for number in range(1, 10000):
        candidate = path if number == 1 else path.with_name(f'{path.name} ({number})')
        if not candidate.exists():
            return candidate
    raise ValueError('Не удалось подобрать свободное имя результата.')


class OutputPathError(ValueError):
    """A destination that cannot hold the minimum safe export structure."""


def check_output_path(destination):
    destination = Path(destination).resolve()
    # Leave room for «Все остальное», a useful caption, and a numbered PDF.
    if 214 - len(str(destination / OTHER_DIR)) < 16:
        raise OutputPathError('Путь папки сохранения слишком длинный. Нажмите «Выбрать другую папку» '
                              'и выберите папку ближе к корню диска, например C:\\Результаты.')
    return destination


def planned_output(source, parent=None):
    """Pick a readable unused folder within the path budget, without reading the PDF."""
    source = Path(source).resolve()
    parent = Path(parent).resolve() if parent is not None else source.parent
    root_budget = 214 - 16 - len(OTHER_DIR) - 1
    name_budget = root_budget - len(str(parent)) - 1
    if name_budget < 8:
        check_output_path(parent / 'Результат')
        raise OutputPathError('Для результата не хватает места в пути. Выберите другую папку ближе к корню диска.')
    for number in range(1, 10000):
        suffix = '' if number == 1 else f' ({number})'
        available = name_budget - len(suffix)
        if available < 3:
            break
        name = safe_name(source.stem, 78) + ' — PDF'
        if len(name) > available:
            name = safe_name(source.stem, available-6)+' — PDF' if available >= 10 else safe_name('Результат', available)
        destination = check_output_path(parent / (name + suffix))
        if not destination.exists():
            return destination
    raise OutputPathError('Не удалось подобрать короткое свободное имя результата. Выберите другую папку.')


def image_paths(page):
    paths = {}
    def walk(resources, prefix=(), visited=frozenset()):
        resources = resources.get_object() if hasattr(resources, 'get_object') else resources
        objects = resources.get('/XObject', {})
        objects = objects.get_object() if hasattr(objects, 'get_object') else objects
        for name, reference in objects.items():
            obj = reference.get_object()
            object_id = getattr(reference, 'idnum', None)
            path = prefix+(name,)
            if obj.get('/Subtype') == '/Image' and object_id is not None:
                paths[object_id] = list(path) if len(path) > 1 else path[0]
            elif obj.get('/Subtype') == '/Form' and object_id not in visited:
                walk(obj.get('/Resources', {}), path, visited | {object_id})
    walk(page.get('/Resources', {}))
    return paths


def page_images(page):
    """Retain the transform of each placement, including nested Form XObjects."""
    def walk(container, matrix=None):
        for item in container:
            if isinstance(item, LTImage):
                image = page.process_object(item)
                yield {key: image[key] for key in ('x0', 'x1', 'top', 'bottom', 'srcsize', 'name')} | {
                    'object_id': getattr(item.stream, 'objid', None), 'matrix': list(matrix[:4])}
            elif isinstance(item, LTContainer):
                yield from walk(item, item.matrix if isinstance(item, LTFigure) else matrix)
    return list(walk(page.layout))


def selection_table_references(page, lines):
    if not any(SELECTION_REFERENCE.fullmatch(line['text']) for line in lines):
        return []
    references = []
    for table in page.find_tables():
        for row in table.extract():
            for cell in row:
                match = SELECTION_REFERENCE.fullmatch(clean_text(cell or ''))
                if match and found_variant(match[2] or match[3]) is not None:
                    references.append({'marker': match[1], 'variant': found_variant(match[2] or match[3]),
                                       'cell_text': clean_text(cell),
                                       'table_bbox': [round(value, 3) for value in table.bbox]})
    return references


def opaque_paints(pdf_page, reader):
    """Prove only axis-aligned opaque paints, respecting alpha, masks and rectangular clips.

    Complex clipping, blends, transparency groups and rotated rectangles deliberately
    cannot prove an occluder. Coordinates here stay in PDF space, independent of page rotation.
    """
    paints = []
    def rectangle(values, matrix):
        if abs(matrix[1]) > 1e-6 or abs(matrix[2]) > 1e-6:
            return None
        x, y, width, height = map(float, values)
        points = [(px*matrix[0]+matrix[4], py*matrix[3]+matrix[5])
                  for px, py in ((x, y), (x+width, y+height))]
        return [min(p[0] for p in points), min(p[1] for p in points),
                max(p[0] for p in points), max(p[1] for p in points)]
    def intersect(first, second):
        return [max(first[0], second[0]), max(first[1], second[1]),
                min(first[2], second[2]), min(first[3], second[3])]
    def opaque(state):
        return (state['alpha'] == 1 and state['normal'] and not state['mask']
                and not state['unsafe'] and state['clip_known'])
    def walk(stream, resources, initial, ancestors=frozenset()):
        state, stack, path = dict(initial), [], []
        for operands, operator in ContentStream(stream, reader).operations:
            if operator == b'q':
                stack.append(dict(state))
            elif operator == b'Q':
                if stack:
                    state = stack.pop()
            elif operator == b'cm':
                state['ctm'] = mult_matrix(tuple(map(float, operands)), state['ctm'])
            elif operator == b'gs':
                graphics = resources.get('/ExtGState', {}).get(operands[0])
                if graphics is None:
                    state['unsafe'] = True
                    continue
                graphics = graphics.get_object()
                state['alpha'] = float(graphics.get('/ca', state['alpha']))
                if '/BM' in graphics:
                    state['normal'] = graphics['/BM'] in ('/Normal', ['/Normal'])
                if '/SMask' in graphics:
                    state['mask'] = graphics['/SMask'] != '/None'
                if graphics.get('/op') == True or graphics.get('/OP') == True:
                    state['unsafe'] = True
            elif operator == b're':
                if path is not None:
                    path.append(rectangle(operands, state['ctm']))
            elif operator in (b'm', b'l', b'c', b'v', b'y', b'h'):
                path = None
            elif operator in (b'W', b'W*'):
                if path is not None and len(path) == 1 and path[0] is not None:
                    state['clip'] = intersect(state['clip'], path[0])
                else:
                    state['clip_known'] = False
            elif operator in (b'cs', b'scn'):
                if operator == b'cs':
                    state['fill_simple'] = str(operands[0]) != '/Pattern'
                elif operands and str(operands[-1]).startswith('/'):
                    state['fill_simple'] = False
            elif operator in (b'g', b'rg', b'k'):
                state['fill_simple'] = True
            elif operator in (b'f', b'F', b'f*', b'B', b'B*', b'b', b'b*'):
                if (path is not None and len(path) == 1 and path[0] is not None
                        and opaque(state) and state['fill_simple']):
                    paints.append({'kind': 'rectangle', 'opaque_bbox': intersect(path[0], state['clip']),
                                   'order': len(paints)})
                path = []
            elif operator in (b'n', b'S', b's'):
                path = []
            elif operator == b'Tr':
                # Modes 4-7 add the glyph outlines to the clip at ET; text clips are never rectangles.
                state['text_clip'] = bool(operands) and int(operands[0]) >= 4
            elif operator == b'ET' and state.get('text_clip'):
                state['clip_known'] = False
                state['text_clip'] = False
                state['clipped_by_text'] = True
            elif operator == b'Do':
                reference = resources.get('/XObject', {}).get(operands[0])
                if reference is None:
                    continue
                obj = reference.get_object()
                object_id = getattr(reference, 'idnum', None)
                if obj.get('/Subtype') == '/Image':
                    box = rectangle((0, 0, 1, 1), state['ctm'])
                    visible = intersect(box, state['clip']) if box is not None and state['clip_known'] else None
                    covers = (visible if opaque(state) and not obj.get('/SMask') and not obj.get('/Mask')
                              and obj.get('/ImageMask') != True else None)
                    paints.append({'kind': 'image', 'object_id': object_id, 'bbox': box,
                                   'opaque_bbox': covers, 'order': len(paints),
                                   'text_clip': state.get('clipped_by_text', False)})
                elif obj.get('/Subtype') == '/Form' and object_id not in ancestors and len(ancestors) < 16:
                    nested = dict(state)
                    nested['ctm'] = mult_matrix(tuple(map(float, obj.get('/Matrix', (1, 0, 0, 1, 0, 0)))), state['ctm'])
                    form_box = obj.get('/BBox')
                    if form_box:
                        x0, y0, x1, y1 = map(float, form_box)
                        clip = rectangle((x0, y0, x1-x0, y1-y0), nested['ctm'])
                        if clip is None:
                            nested['clip_known'] = False
                        else:
                            nested['clip'] = intersect(nested['clip'], clip)
                    if obj.get('/Group', {}).get('/S') == '/Transparency':
                        nested['unsafe'] = True
                    walk(obj, obj.get('/Resources', resources), nested, ancestors | {object_id})
    initial = {'ctm': (1, 0, 0, 1, 0, 0), 'alpha': 1, 'normal': True, 'mask': False,
               'unsafe': False, 'clip_known': True, 'fill_simple': True,
               'clip': list(map(float, pdf_page.cropbox))}
    walk(pdf_page.get_contents(), pdf_page.get('/Resources', {}), initial)
    return paints


def _plain(value):
    """A PDF value as text-ready data without memory addresses: an indirect reference becomes «12 0 R»."""
    if isinstance(value, IndirectObject):
        return f'{value.idnum} {value.generation} R'
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return value


def resource_identity(obj):
    """SHA-256 of an image resource: pixels, size, colours and masks, the same in every run."""
    identity = hashlib.sha256(obj.get_data())
    identity.update(str(_plain((obj.get('/Width'), obj.get('/Height'), obj.get('/ColorSpace'),
                                obj.get('/BitsPerComponent')))).encode())
    for name in ('/SMask', '/Mask'):
        mask = obj.get(name)
        if mask is not None:
            mask = mask.get_object() if hasattr(mask, 'get_object') else mask
            identity.update(mask.get_data() if hasattr(mask, 'get_data') else str(_plain(mask)).encode())
    return identity.hexdigest()


def prepare_image_quality(source, pages):
    placements, identities = defaultdict(list), {}
    with PdfReader(source) as reader:
        for page in pages:
            try:
                paints = opaque_paints(reader.pages[page['number']-1], reader)
            except Exception as error:
                paints = []
                page['quality_warning'] = {'page': page['number'], 'reason': 'Проверка перекрытий: '+str(error),
                                           'fallback': 'Изображения с недоказанным перекрытием сохранены.'}
            by_object = defaultdict(list)
            for paint in paints:
                if paint['kind'] == 'image':
                    by_object[paint['object_id']].append(paint)
            for image in page['images']:
                object_id = image['object_id']
                if object_id not in identities:
                    try:
                        identities[object_id] = resource_identity(reader.get_object(object_id))
                    except Exception:
                        # An unavailable identity is never evidence that two resources are equal.
                        identities[object_id] = None
                image['identity_sha256'] = identities[object_id]
                if image['identity_sha256']:
                    placements[image['identity_sha256']].append((page, image))
                occurrences = by_object[object_id]
                if not occurrences:
                    continue
                paint = occurrences.pop(0)
                if paint.get('text_clip'):
                    # Seen only through glyph outlines: this raster colours a caption, it is not a picture.
                    image['text_fill'] = True
                box = paint['bbox']
                if box is None:
                    continue
                for covering in paints[paint['order']+1:]:
                    cover = covering.get('opaque_bbox')
                    if cover and cover[0] <= box[0]+1e-6 and cover[1] <= box[1]+1e-6 and cover[2] >= box[2]-1e-6 and cover[3] >= box[3]-1e-6:
                        image['fully_occluded'] = {'paint_order': paint['order'], 'covered_by': covering}
                        break
    for occurrences in placements.values():
        eligible = [(page, image) for page, image in occurrences
                    if (image['x1']-image['x0'])*(image['bottom']-image['top'])/(page['width']*page['height'])
                        <= (.22 if region_is_clipped(page, image) else .08)]
        def position(page, image):
            return (image['x0']/page['width'], image['top']/page['height'],
                    image['x1']/page['width'], image['bottom']/page['height'])
        clusters = []
        for page, image in eligible:
            near = [(other_page, other) for other_page, other in eligible
                    if all(abs(a-b) <= .015 for a, b in zip(position(page, image), position(other_page, other)))]
            if len({other_page['number'] for other_page, _ in near}) >= 3:
                clusters.append(near)
        static_pages = sorted({page['number'] for cluster in clusters for page, _ in cluster})
        if not static_pages:
            # A key plan of the site keeps its pixels but moves to a free corner of each slide.
            # Without a stable place it takes one page more than a static asset: a small photo
            # shown again on three slides is still a photo, on four or more it is an auxiliary picture.
            small = [(page, image) for page, image in occurrences
                     if (image['x1']-image['x0'])*(image['bottom']-image['top'])/(page['width']*page['height']) <= .05
                     and not region_is_clipped(page, image)]
            pages_seen = sorted({page['number'] for page, _ in small})
            if len(small) == len(occurrences) and len(pages_seen) >= 4:
                for page, image in small:
                    image['repeated_resource'] = {'pages': pages_seen, 'identity_sha256': image['identity_sha256']}
            continue
        for page, image in eligible:
            # A stable placement proves the auxiliary asset's role. The same small
            # resource can move on a later slide without becoming a new photograph.
            image['repeated_static'] = {'pages': static_pages, 'identity_sha256': image['identity_sha256']}


def mark_blank_layers(reader, page):
    """Set aside nearly white JPEG layers stacked exactly over an earlier image in the same place."""
    for index, image in enumerate(page['images']):
        base = next((earlier for earlier in page['images'][:index]
                     if all(abs(earlier[key]-image[key]) <= 1 for key in ('x0', 'top', 'x1', 'bottom'))), None)
        if base is None:
            continue
        try:
            ink = blank_layer_ink(reader.get_object(image['object_id']))
        except Exception:
            # Only JPEG layers are measured; anything unreadable stays a normal image.
            continue
        if ink < .005:
            image['blank_layer'] = {'visible_fraction': round(ink, 6), 'base_object': base['object_id']}


def group_suppression(page, group):
    """Apply fragment thresholds to assembled regions, preserving low-resolution large photos."""
    box = bounds(group)
    if all(image.get('text_fill') for image in group):
        return {'suppression_code': 'text_fill',
                'reason': 'Изображение видно только внутри букв надписи: это оформление текста, а не отдельная картинка.'}
    if all(image.get('blank_layer') for image in group):
        return {'suppression_code': 'blank_layer',
                'reason': 'Почти пустой слой поверх изображения того же размера: видимых деталей меньше 0,5 % площади.',
                'evidence': [image['blank_layer'] for image in group]}
    if all(image.get('fully_occluded') for image in group):
        return {'suppression_code': 'fully_occluded', 'reason': 'Изображение полностью закрыто более поздним непрозрачным элементом.',
                'evidence': [image['fully_occluded'] for image in group]}
    if all(image.get('repeated_static') for image in group) and not any('scan_crop_px' in image for image in group):
        return {'suppression_code': 'repeated_static', 'reason': 'Одинаковый вспомогательный элемент повторяется в одном месте не менее чем на трёх страницах.',
                'evidence': [image['repeated_static'] for image in group]}
    if all(image.get('repeated_resource') for image in group) and not any('scan_crop_px' in image for image in group):
        pages = group[0]['repeated_resource']['pages']
        return {'suppression_code': 'repeated_resource',
                'reason': f'Одна и та же небольшая картинка повторяется на {len(pages)} страницах в разных местах слайда — '
                          'например, план-схема участка.',
                'evidence': [image['repeated_resource'] for image in group]}
    left, top = page['origin']
    width = max(0, min(box['x1'], left+page['width'])-max(box['x0'], left))
    height = max(0, min(box['bottom'], top+page['height'])-max(box['top'], top))
    fraction = width*height/(page['width']*page['height'])
    if fraction < .01:
        return {'suppression_code': 'small_fragment', 'reason': 'Мелкий отдельный элемент занимает менее 1 % площади страницы после сборки соприкасающихся фрагментов.',
                'visible_page_fraction': round(fraction, 6)}
    return None


def inspect(source, progress=None):
    pages = []
    with pdfplumber.open(source) as document:
        total = len(document.pages)
        for number, page in enumerate(document.pages, 1):
            if progress:
                progress({'stage': 'labels', 'current': number, 'total': total, 'found': 0})
            images = page_images(page)
            lines = text_lines(page.extract_words(extra_attrs=['size', 'fontname']))
            pages.append({'number': number, 'width': page.width, 'height': page.height,
                          'origin': [page.bbox[0], page.bbox[1]],
                          'lines': lines, 'selection_references': selection_table_references(page, lines),
                          'images': images, 'source_components': len(images)})
            page.close()
    prepare_image_quality(source, pages)
    with PdfReader(source) as reader:
        for page in pages:
            mark_blank_layers(reader, page)
    analyse_scans(source, pages, image_paths, progress)
    headers = Counter()
    for page in pages:
        headers.update({(tuple(im['srcsize']), *(round(im[k]) for k in ('x0', 'top', 'x1', 'bottom')))
                        for im in page['images'] if im['bottom'] < page['height']*.14})
    for page in pages:
        for image in page['images']:
            key = (tuple(image['srcsize']), *(round(image[k]) for k in ('x0', 'top', 'x1', 'bottom')))
            image['repeated_header'] = (image['bottom'] < page['height']*.14
                                        and headers[key] >= max(3, len(pages)*.5))
    prepare_captions(pages)
    prepare_selected_variants(pages)
    prepare_variant_context(pages)
    prepare_scan_continuations(pages)
    return pages


def _decode(page, paths, image):
    key = paths.get(image['object_id'])
    if key is None:
        raise ValueError('Встроенное или inline-изображение не имеет отдельного ресурса.')
    data = native_jpeg(page.pdf.get_object(image['object_id']))
    if data is not None:
        return 'native.jpg', data
    original = page.images[key]
    data, name = original.data, original.name
    original.image.close()
    return name, data


def _large_raster(page, image, budget):
    """Decode a poster-size JPEG once per page and budget; panels of one poster share the copy."""
    key = (id(page.pdf), image['object_id'], budget)
    if key not in _large_raster_cache:
        for cached, _ in _large_raster_cache.values():
            cached.close()
        _large_raster_cache.clear()
        _large_raster_cache[key] = open_large_raster(page.pdf.get_object(image['object_id']), budget)
    return _large_raster_cache[key]


def release_large_rasters():
    for cached, _ in _large_raster_cache.values():
        cached.close()
    _large_raster_cache.clear()


def save_group(page, paths, group, base):
    if len(group) == 1 and is_large_raster(group[0]):
        crop = group[0].get('scan_crop_px')
        image, factor = _large_raster(page, group[0], LARGE_RASTER_PIXELS if crop else LARGE_WHOLE_PIXELS)
        if crop:
            target = base.with_suffix('.png')
            with image.crop([round(value/factor) for value in crop]) as part:
                size = list(part.size)
                part.save(target, 'PNG', compress_level=SCRATCH_PNG_LEVEL)
            return target, size, 'reduced_scan_crop'
        target = base.with_suffix('.jpg')
        image.convert('RGB').save(target, 'JPEG', quality=92)
        return target, list(image.size), 'reduced_original'
    if len(group) == 1:
        name, data = _decode(page, paths, group[0])
        with Image.open(BytesIO(data)) as image:
            if 'scan_crop_px' in group[0]:
                target = base.with_suffix('.png')
                with image.crop(group[0]['scan_crop_px']) as crop:
                    size = list(crop.size)
                    crop.save(target, 'PNG', compress_level=SCRATCH_PNG_LEVEL)
                return target, size, 'native_scan_crop'
            size = list(image.size)
            suffix = Path(name).suffix.lower()
            if suffix in ('.jpg', '.jpeg', '.png'):
                target = base.with_suffix(suffix)
                target.write_bytes(data)
            else:
                target = base.with_suffix('.png')
                image.save(target, 'PNG', compress_level=SCRATCH_PNG_LEVEL)
        return target, size, 'original'
    if any((matrix := image.get('matrix')) and
           (matrix[1] or matrix[2] or matrix[0] < 0 or matrix[3] < 0) for image in group):
        raise ValueError('Поворот или отражение фрагментов: сохранён снимок собранной области страницы.')
    box = bounds(group)
    first = group[0]
    sx = first['srcsize'][0]/(first['x1']-first['x0'])
    sy = first['srcsize'][1]/(first['bottom']-first['top'])
    vertical = all(abs(im['x0']-box['x0']) < .8 and abs(im['x1']-box['x1']) < .8 for im in group)
    horizontal = all(abs(im['top']-box['top']) < .8 and abs(im['bottom']-box['bottom']) < .8 for im in group)
    if vertical:
        group = sorted(group, key=lambda im: im['top'])
        size = [first['srcsize'][0], sum(im['srcsize'][1] for im in group)]
    elif horizontal:
        group = sorted(group, key=lambda im: im['x0'])
        size = [sum(im['srcsize'][0] for im in group), first['srcsize'][1]]
    else:
        size = [round((box['x1']-box['x0'])*sx), round((box['bottom']-box['top'])*sy)]
    if size[0]*size[1] > 180_000_000:
        raise ValueError('Слишком большая сборная картинка для объединения в памяти.')
    with Image.new('RGBA', size, (255, 255, 255, 0)) as merged:
        offset = 0
        for image in group:
            _, data = _decode(page, paths, image)
            with Image.open(BytesIO(data)) as piece:
                if vertical:
                    x, y, expected = 0, offset, (size[0], image['srcsize'][1])
                    offset += image['srcsize'][1]
                elif horizontal:
                    x, y, expected = offset, 0, (image['srcsize'][0], size[1])
                    offset += image['srcsize'][0]
                else:
                    x, y = round((image['x0']-box['x0'])*sx), round((image['top']-box['top'])*sy)
                    expected = (round((image['x1']-image['x0'])*sx), round((image['bottom']-image['top'])*sy))
                if any(abs(a-b) > 2 for a, b in zip(piece.size, expected)):
                    raise ValueError('Размеры фрагментов не позволяют соединить их без масштабирования.')
                merged.paste(piece, (x, y))
        target = base.with_suffix('.png')
        merged.save(target, 'PNG', compress_level=SCRATCH_PNG_LEVEL)
    return target, size, 'stitched_originals'


def save_crop(pdfium_document, page, box, base):
    with PDFIUM_LOCK:
        return _save_crop(pdfium_document, page, box, base)


def _save_crop(pdfium_document, page, box, base):
    pdf_page = pdfium_document[page['number']-1]
    scale = 200/72
    left = max(0, box['x0']-page['origin'][0])
    top = max(0, box['top']-page['origin'][1])
    right = max(0, page['width']-(box['x1']-page['origin'][0]))
    bottom = max(0, page['height']-(box['bottom']-page['origin'][1]))
    try:
        bitmap = pdf_page.render(scale=scale, crop=(left, bottom, right, top))
        try:
            image = bitmap.to_pil()
            target = base.with_suffix('.png')
            image.save(target, 'PNG', compress_level=SCRATCH_PNG_LEVEL)
            size = list(image.size)
            image.close()
        finally:
            bitmap.close()
    finally:
        pdf_page.close()
    return target, size, 'page_crop_fallback'


def merge_variant_pdfs(staging, entries, progress=None):
    variants = {}
    for entry in entries:
        entry['pdf_page'] = 1
        if entry_group(entry) is not None:
            variants.setdefault(entry_group(entry), []).append(entry)
    total = sum(map(len, variants.values()))
    current = 0
    if progress and total:
        progress({'stage': 'merge', 'current': 0, 'total': total, 'found': len(entries)})
    for number, images in variants.items():
        # The extraction sequence already follows page geometry, top to bottom
        # and left to right; captions and filenames must not change that order.
        images.sort(key=lambda entry: (entry['page'], entry['sequence']))
        target = staging/group_file({'layout': LAYOUT}, number)
        originals = [staging/entry['file'] for entry in images]
        with PdfWriter() as writer:
            writer.add_metadata({'/Title': group_title(number), '/Creator': f'PDF Photos {VERSION}'})
            for index, (entry, source) in enumerate(zip(images, originals)):
                with PdfReader(source) as reader:
                    writer.add_page(reader.pages[0])
                writer.add_outline_item(f'Страница {entry["page"]} — {entry["title"]}', index)
                current += 1
                if progress:
                    progress({'stage': 'merge', 'current': current, 'total': total, 'found': len(entries)})
            writer.write(target)
        with target.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        for index, (entry, source) in enumerate(zip(images, originals), 1):
            entry.update(file=str(target.relative_to(staging)), pdf_page=index, sha256=checksum)
            source.unlink()
    documents = {}
    for entry in entries:
        document = documents.setdefault(entry['file'], {
            'file': entry['file'], 'folder': entry['folder'], 'variant': entry['variant'],
            'group': entry.get('group'), 'pages': 0, 'sha256': entry['sha256']})
        document['pages'] += 1
    return list(documents.values())


def render_previews(staging, report, progress=None):
    import pypdfium2
    thumbnails = staging/SERVICE_DIR/'Миниатюры'
    thumbnails.mkdir(parents=True)
    hide(staging/SERVICE_DIR)
    report['preview_errors'] = []
    found = sum(not entry.get('removed') for entry in report['images'])
    for index, entry in enumerate(report['images'], 1):
        if progress:
            progress({'stage': 'export', 'current': index, 'total': len(report['images']), 'found': found})
        path = thumbnails/f'{index:05d}.png'
        try:
            with PDFIUM_LOCK, pypdfium2.PdfDocument(staging/entry['file']) as document:
                pdf_page = document[entry['pdf_page']-1]
                try:
                    scale = min(min(640, entry['size'][0])/pdf_page.get_width(),
                                min(480, entry['size'][1])/pdf_page.get_height())
                    bitmap = pdf_page.render(scale=scale)
                    try:
                        with bitmap.to_pil() as image:
                            # PDFium may round fractional crop dimensions up by one pixel.
                            image.thumbnail((640, 480), Image.Resampling.LANCZOS)
                            image.save(path, 'PNG')
                    finally:
                        bitmap.close()
                finally:
                    pdf_page.close()
            entry['preview'] = str(path.relative_to(staging))
        except Exception as error:
            entry['preview'] = None
            report['preview_errors'].append({'file': entry['file'], 'error': str(error)})
    for collage in report.get('collages', []):
        collage['preview'] = render_collage_preview(staging, report, collage)


COLLAGE_PREVIEW = 1600


def render_collage_preview(output, report, collage):
    """A large picture of the whole collage: cut lines are placed on it to the source pixel."""
    import pypdfium2
    source = report['images'][collage['image']] if 'image' in collage else {'file': collage['file'], 'pdf_page': 1}
    path = Path(output)/SERVICE_DIR/'Миниатюры'/f'склейка {collage["id"]} r{report.get("revision", 0)}.png'
    try:
        with PDFIUM_LOCK, pypdfium2.PdfDocument(Path(output)/source['file']) as document:
            pdf_page = document[source['pdf_page']-1]
            try:
                width, height = collage['size']
                scale = min(COLLAGE_PREVIEW, max(width, height))/max(pdf_page.get_width(), pdf_page.get_height())
                bitmap = pdf_page.render(scale=scale)
                try:
                    with bitmap.to_pil() as image:
                        image.thumbnail((COLLAGE_PREVIEW, COLLAGE_PREVIEW), Image.Resampling.LANCZOS)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        image.save(path, 'PNG')
                finally:
                    bitmap.close()
            finally:
                pdf_page.close()
        return str(path.relative_to(output))
    except Exception as error:
        report.setdefault('preview_errors', []).append({'file': source['file'], 'error': str(error)})
        return None


def write_gallery(staging, report):
    escape = html.escape
    labels = report.get('folder_labels') or {}
    def folder_label(folder):
        return labels.get(folder, folder)
    images = [(index, entry) for index, entry in enumerate(report['images']) if not entry.get('removed')]
    groups = {'variant': [], 'review': [], 'unsigned': [], 'caption': []}
    members = {}
    for index, entry in images:
        members.setdefault((entry_group(entry), entry['folder']), []).append((index, entry))
    group_keys = sorted(members, key=lambda key: (key[1], variant_sort_key(key[0])))
    for number, (variant, folder) in enumerate(group_keys, 1):
        entries = members[variant, folder]
        if variant is not None:
            kind = 'variant'
            entries.sort(key=lambda item: item[1]['pdf_page'])
        elif folder == 'На проверку':
            kind = 'review'
        elif folder == 'Без подписи':
            kind = 'unsigned'
        else:
            kind = 'caption'
        groups[kind].append((f'g{number}', folder, entries))
    groups['variant'].sort(key=lambda group: variant_sort_key(group[2][0][1]['variant']))

    def card(item, kind, anchor=None):
        index, entry = item
        url = quote(entry['file'].replace('\\', '/'), safe='/')+f'#page={entry["pdf_page"]}'
        title = entry['title'] or entry['reason']
        if entry['preview']:
            thumbnail_url = quote(entry['preview'].replace('\\', '/'), safe='/')
            thumbnail = (f'<img loading="lazy" decoding="async" src="{thumbnail_url}" '
                         f'width="{entry["size"][0]}" height="{entry["size"][1]}" '
                         f'alt="{escape(title)}">')
            zoom = (f'<button class="zoom" type="button" data-src="{thumbnail_url}" '
                    f'aria-label="Увеличить изображение">{ICONS["zoom"]}</button>')
        else:
            thumbnail = f'<span class="missing">{ICONS["image"]}Миниатюра не создана</span>'
            zoom = ''
        badge = ''
        if kind == 'caption' and folder_label(entry['folder']) != entry['title']:
            badge = f'<span class="badge">{ICONS["folder"]}{escape(folder_label(entry["folder"]))}</span>'
        if entry.get('manual'):
            badge += f'<span class="badge accent">{ICONS["edit"]}Изменено вручную</span>'
        candidates = ''
        if entry['status'] == 'review' and entry['candidates']:
            candidates = ('<ul class="candidates" aria-label="Возможные подписи">'
                          + ''.join(f'<li>{escape(c)}</li>' for c in entry['candidates'])+'</ul>')
        id_attr = f' id="{anchor}"' if anchor else ''
        return (f'<article class="card"{id_attr} data-id="{index}" '
                f'data-search="{escape(" ".join([title, folder_label(entry["folder"])]+entry["candidates"]))}" '
                f'data-folder="{escape(entry["folder"])}" data-pdf="{url}"'
                + (f' data-file-name="{escape(Path(entry["file"]).stem)}"' if entry['variant'] is None else '')
                + (' data-folder-rename="1"' if kind == 'caption' else '') + '>'
                f'<div class="media"><a class="cover" href="{url}" title="Открыть PDF на странице {entry["pdf_page"]}">'
                f'{thumbnail}</a>{zoom}</div><div class="body">{badge}'
                f'<a class="caption{"" if entry["title"] else " none"}" href="{url}">{escape(title)}</a>{candidates}'
                f'<dl class="meta"><div><dt>Слайд</dt><dd>{entry["page"]}</dd></div>'
                f'<div><dt>Стр. PDF</dt><dd>{entry["pdf_page"]}</dd></div>'
                f'<div><dt>Размер</dt><dd>{entry["size"][0]}×{entry["size"][1]}</dd></div></dl></div></article>')

    notes = {'variant': 'Один многостраничный PDF', 'review': 'Несколько возможных подписей — выберите вручную',
             'unsigned': 'Рядом не найдена читаемая подпись — проверьте вручную'}
    sections, nav = [], []
    for kind in ('variant', 'review', 'unsigned'):
        for anchor, folder, entries in groups[kind]:
            number = f' data-variant="{entry_group(entries[0][1])}"' if kind == 'variant' else ''
            sections.append(f'<section id="{anchor}" class="group {kind}" data-folder="{escape(folder)}"{number}>'
                            f'<header class="group-head"><h2>{escape(folder_label(folder))}</h2>'
                            f'<span class="count">{len(entries)}</span><p class="note">{notes[kind]}</p></header>'
                            f'<div class="grid">{"".join(card(e, kind) for e in entries)}</div></section>')
    if groups['caption']:
        cards = ''.join(card(e, 'caption', anchor if i == 0 else None)
                        for anchor, _, entries in groups['caption'] for i, e in enumerate(entries))
        total = sum(len(entries) for _, _, entries in groups['caption'])
        sections.append(f'<section id="other" class="group caption"><header class="group-head">'
                        f'<h2>Другие подписи</h2><span class="count">{total}</span>'
                        f'<p class="note">Без номера варианта: отдельный PDF для каждого изображения, '
                        f'папка указана на карточке</p></header><div class="grid">{cards}</div></section>')
    titles = {'variant': 'Варианты', 'review': 'Требуют проверки', 'caption': 'Другие подписи'}
    for kind, members in (('variant', groups['variant']), ('review', groups['review']+groups['unsigned']),
                          ('caption', groups['caption'])):
        if not members:
            continue
        items = ''.join(f'<li><a class="nav-item {"warn" if folder in ("На проверку", "Без подписи") else ""}" '
                        f'href="#{anchor}" data-folder="{escape(folder)}" title="{escape(folder_label(folder))}"><span>{escape(folder_label(folder))}</span>'
                        f'<b>{len(entries)}</b></a></li>' for anchor, folder, entries in members)
        nav.append(f'<div class="nav-group"><h3>{titles[kind]}</h3><ul>{items}</ul></div>')

    counts = report['counts_by_status']
    unclear = counts.get('unsigned', 0)+counts.get('review', 0)
    stats = [(len(images), 'изображений', ''), (len(report['documents']), 'PDF-файлов', ''),
             (len(groups['variant']), 'вариантов', 'accent'), (counts.get('review', 0), 'на проверку', 'warn'),
             (counts.get('unsigned', 0), 'без подписи', 'warn')]
    name = escape(Path(report['source']).name)
    page = '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    page += f'<title>{escape(Path(report["source"]).stem)} — просмотр</title><style>' + GALLERY_CSS + EDIT_CSS + '</style>'
    page += f'<body data-revision="{report.get("revision", 0)}" data-gallery-version="{VERSION}">'
    page += '<a class="skip" href="#content">К изображениям</a><div class="layout">'
    page += f'<aside class="sidebar" id="sidebar" aria-label="Папки"><div class="brand"><span class="logo">{ICONS["logo"]}</span>'
    page += f'<div><b>Фото из PDF</b><span>версия {VERSION}</span></div>'
    page += f'<button class="icon-btn close-nav" type="button" aria-label="Закрыть список папок">{ICONS["close"]}</button></div>'
    page += f'<nav class="folders">{"".join(nav)}</nav></aside><div class="scrim" hidden></div>'
    page += '<div class="main"><div class="topbar"><button class="icon-btn open-nav" type="button" aria-label="Папки" '
    page += f'aria-controls="sidebar" aria-expanded="false">{ICONS["menu"]}<span>Папки</span></button>'
    page += f'<label class="search">{ICONS["search"]}<input id="q" type="search" placeholder="Найти папку или подпись" '
    page += 'aria-label="Поиск по папкам и подписям" autocomplete="off"><kbd>/</kbd></label>'
    page += '<span id="found" role="status" aria-live="polite"></span>'
    page += (f'<button id="edit" class="icon-btn edit-btn" type="button" aria-pressed="false" '
             f'title="Убрать, перенести или переставить изображения в PDF вариантов">{ICONS["edit"]}<span>Редактировать</span></button></div>')
    page += f'<header class="hero"><p class="eyebrow">Результат обработки</p><h1>{name}</h1><div class="stats">'
    page += ''.join(f'<div class="stat {tone if value else ""}"><b>{value}</b><span>{label}</span></div>'
                    for value, label, tone in stats)
    page += '</div>'
    if unclear:
        page += (f'<p class="callout">{ICONS["alert"]}<span>Требуют ручной проверки: {unclear}. '
                 f'Эти изображения собраны в группах «На проверку» и «Без подписи».</span></p>')
    page += ('<p class="hint">Названия подобраны автоматически по тексту и расположению на странице — проверьте привязки. '
             'Слайд — физическая страница исходного PDF. Клик по миниатюре открывает PDF на нужной странице, '
             'кнопка с лупой — быстрый просмотр.</p></header>')
    page += f'<main id="content">{"".join(sections)}'
    page += f'<p id="empty" hidden>{ICONS["search"]}Ничего не найдено. Попробуйте другое слово.</p></main>'
    page += ('<div id="changes" class="changes" hidden><div class="changes-text"><b>Режим редактирования</b>'
             '<span id="changes-summary"></span></div><button id="discard" class="icon-btn" type="button">Отменить изменения</button>'
             '<button id="save" class="btn" type="button">Сохранить</button></div></div></div>'
             '<div id="toast" class="toast" role="status" aria-live="polite" hidden></div>')
    page += ('<dialog id="viewer" aria-label="Просмотр изображения"><div class="viewer-top"><div class="viewer-title">'
             '<b id="v-title"></b><span id="v-meta"></span></div>'
             f'<a id="v-open" class="btn" href="#">{ICONS["external"]}Открыть страницу PDF</a>'
             f'<button class="icon-btn" type="button" data-close aria-label="Закрыть">{ICONS["close"]}</button></div>'
             f'<div class="viewer-stage"><button class="icon-btn nav-btn" type="button" data-step="-1" '
             f'aria-label="Предыдущее изображение">{ICONS["left"]}</button><img id="v-img" alt="">'
             f'<button class="icon-btn nav-btn" type="button" data-step="1" aria-label="Следующее изображение">'
             f'{ICONS["right"]}</button></div><p class="viewer-hint">← → — листать · Esc — закрыть</p></dialog>')
    page += '<script>' + GALLERY_JS + EDIT_JS + '</script></body></html>'
    (staging/'Просмотр.html').write_text(page, encoding='utf-8')


def _icon(path):
    return ('<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{path}</svg>')


ICONS = {
    'search': _icon('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
    'zoom': _icon('<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>'),
    'close': _icon('<path d="M18 6 6 18M6 6l12 12"/>'),
    'menu': _icon('<path d="M4 6h16M4 12h16M4 18h16"/>'),
    'left': _icon('<path d="m15 18-6-6 6-6"/>'),
    'right': _icon('<path d="m9 18 6-6-6-6"/>'),
    'external': _icon('<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'),
    'alert': _icon('<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>'
                   '<path d="M12 9v4M12 17h.01"/>'),
    'folder': _icon('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
    'edit': _icon('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>'),
    'image': _icon('<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/>'
                   '<path d="m21 15-5-5L5 21"/>'),
    'logo': _icon('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>'
                  '<circle cx="10" cy="13" r="1.5"/><path d="m20 19-4.5-4.5L8 22"/>'),
}

GALLERY_CSS = '''
:root{--bg:#f4f5f7;--surface:#fff;--surface-2:#f9fafb;--text:#1b1f24;--muted:#5b6370;--line:#e1e4e8;--line-strong:#cdd2d8;
--accent:#1b6b5f;--accent-hover:#14564c;--accent-soft:#e2f0ec;--on-accent:#fff;--warn:#8a5a00;--warn-soft:#fff5d9;
--warn-line:#efd28a;--thumb:#eef0f2;--shadow:0 1px 2px rgba(16,24,40,.05);--shadow-lg:0 12px 32px rgba(16,24,40,.12);
--radius:14px;--side:288px;--top:64px;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#111418;--surface:#1a1e23;--surface-2:#20252b;--text:#e7e9ec;--muted:#a0a8b3;
--line:#2c323a;--line-strong:#3a414a;--accent:#5cc2b0;--accent-hover:#7fd3c4;--accent-soft:#18332e;--on-accent:#0c1f1b;
--warn:#e8b84a;--warn-soft:#33290f;--warn-line:#5e4a16;--thumb:#232930;--shadow:none;--shadow-lg:0 12px 32px rgba(0,0,0,.5);
color-scheme:dark}}
*{box-sizing:border-box}[hidden]{display:none!important}
html{scroll-behavior:smooth;scroll-padding-top:calc(var(--top) + 16px)}
body{margin:0;font:15px/1.5 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--text);
-webkit-font-smoothing:antialiased}
a{color:inherit}button{font:inherit;color:inherit}
svg{flex:none}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
.skip{position:absolute;left:12px;top:-60px;z-index:50;background:var(--accent);color:var(--on-accent);padding:8px 14px;border-radius:8px}
.skip:focus{top:12px}
.layout{display:grid;grid-template-columns:var(--side) minmax(0,1fr);min-height:100vh}
.sidebar{position:sticky;top:0;height:100vh;display:flex;flex-direction:column;background:var(--surface);
border-right:1px solid var(--line);z-index:30}
.brand{display:flex;align-items:center;gap:12px;padding:18px 20px;border-bottom:1px solid var(--line)}
.brand b{display:block;font-size:15px}.brand span{color:var(--muted);font-size:12px}
.logo{display:grid;place-items:center;width:36px;height:36px;border-radius:10px;background:var(--accent);color:var(--on-accent)}
.brand .close-nav{display:none;margin-left:auto}
.folders{flex:1;overflow-y:auto;padding:10px 12px 24px;scrollbar-width:thin;overscroll-behavior:contain}
.nav-group h3{margin:16px 10px 6px;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.nav-group ul{list-style:none;margin:0;padding:0}
.nav-item{display:flex;align-items:center;gap:10px;min-height:36px;padding:7px 10px;border-radius:8px;text-decoration:none;
font-size:14px;color:var(--text);transition:background-color .15s,color .15s}
.nav-item span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nav-item b{font-size:12px;font-weight:600;color:var(--muted);font-variant-numeric:tabular-nums;background:var(--surface-2);
border:1px solid var(--line);border-radius:999px;padding:0 8px}
.nav-item:hover{background:var(--surface-2)}
.nav-item.warn b{background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn)}
.nav-item.active{background:var(--accent-soft);color:var(--accent);font-weight:600}
.nav-item.active b{color:var(--accent);border-color:transparent;background:var(--surface)}
.main{min-width:0}
.topbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:12px;height:var(--top);padding:0 32px;
background:color-mix(in srgb,var(--bg) 85%,transparent);backdrop-filter:saturate(1.4) blur(10px);border-bottom:1px solid var(--line)}
.search{flex:0 1 480px;display:flex;align-items:center;gap:10px;height:40px;padding:0 12px;background:var(--surface);
border:1px solid var(--line-strong);border-radius:10px;color:var(--muted);transition:border-color .15s,box-shadow .15s}
.search:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.search input{flex:1;min-width:0;border:0;outline:0;background:none;font:inherit;color:var(--text)}
.search input::-webkit-search-cancel-button{cursor:pointer}
kbd{font:12px/1 inherit;padding:3px 7px;border:1px solid var(--line-strong);border-radius:6px;color:var(--muted)}
#found{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
.icon-btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-width:40px;height:40px;padding:0 10px;
border:1px solid var(--line-strong);border-radius:10px;background:var(--surface);cursor:pointer;
transition:background-color .15s,border-color .15s}
.icon-btn:hover{background:var(--surface-2);border-color:var(--accent)}
.topbar .open-nav{display:none}
.hero{padding:32px 32px 8px;max-width:1400px}
.eyebrow{margin:0;color:var(--muted);font-size:13px}
h1{margin:4px 0 20px;font-size:28px;line-height:1.25;font-weight:600;overflow-wrap:anywhere;text-wrap:balance}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;max-width:900px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px;box-shadow:var(--shadow)}
.stat b{display:block;font-size:26px;line-height:1.15;font-weight:600;font-variant-numeric:tabular-nums}
.stat span{color:var(--muted);font-size:13px}
.stat.accent b{color:var(--accent)}
.stat.warn{background:var(--warn-soft);border-color:var(--warn-line)}.stat.warn b,.stat.warn span{color:var(--warn)}
.callout{display:flex;gap:10px;align-items:flex-start;max-width:900px;margin:16px 0 0;padding:12px 14px;border-radius:10px;
background:var(--warn-soft);border:1px solid var(--warn-line);color:var(--warn);font-size:14px}
.callout svg{margin-top:2px}
.hint{color:var(--muted);font-size:13px;max-width:900px;margin:14px 0 0}
main{padding:8px 32px 64px;max-width:1400px}
.group{margin-top:36px}
.group-head{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 12px;margin-bottom:16px;padding-bottom:12px;
border-bottom:1px solid var(--line)}
h2{margin:0;font-size:20px;font-weight:600;overflow-wrap:anywhere}
.count{font-size:13px;font-weight:600;color:var(--muted);font-variant-numeric:tabular-nums;background:var(--surface);
border:1px solid var(--line);border-radius:999px;padding:1px 10px}
.note{flex-basis:100%;margin:0;color:var(--muted);font-size:13px}
.group.review .note,.group.unsigned .note{color:var(--warn)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:18px}
.card{position:relative;display:flex;flex-direction:column;background:var(--surface);border:1px solid var(--line);
border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow);transition:border-color .2s,box-shadow .2s}
.card:hover{border-color:var(--line-strong);box-shadow:var(--shadow-lg)}
.card:target{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.media{position:relative;background:var(--thumb)}
.cover{display:flex;align-items:center;justify-content:center;aspect-ratio:4/3;padding:12px;cursor:pointer}
.cover img{width:auto;height:auto;max-width:100%;max-height:100%;object-fit:contain;border-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.18);
transition:transform .25s ease-out}
.card:hover .cover img{transform:scale(1.02)}
.zoom{position:absolute;top:10px;right:10px;display:grid;place-items:center;width:36px;height:36px;border-radius:10px;
border:1px solid var(--line);background:color-mix(in srgb,var(--surface) 90%,transparent);backdrop-filter:blur(6px);
cursor:pointer;opacity:0;transition:opacity .15s,background-color .15s}
.card:hover .zoom,.zoom:focus-visible{opacity:1}.zoom:hover{background:var(--surface);color:var(--accent)}
@media (hover:none){.zoom{opacity:1}}
.missing{display:flex;flex-direction:column;align-items:center;gap:6px;color:var(--muted);font-size:13px}
.body{display:flex;flex-direction:column;gap:10px;padding:14px 16px 16px;flex:1}
.badge{display:inline-flex;align-items:center;gap:6px;align-self:flex-start;max-width:100%;padding:2px 8px;border-radius:6px;
font-size:12px;color:var(--muted);background:var(--surface-2);border:1px solid var(--line)}
.badge svg{width:14px;height:14px}
.badge.warn{color:var(--warn);background:var(--warn-soft);border-color:var(--warn-line)}
.caption{font-weight:600;line-height:1.4;text-decoration:none;overflow-wrap:anywhere}
.caption:hover{color:var(--accent);text-decoration:underline;text-underline-offset:3px}
.caption.none{font-weight:400;color:var(--muted);font-style:italic}
.candidates{margin:0;padding:8px 10px 8px 26px;border-radius:8px;background:var(--warn-soft);color:var(--warn);font-size:13px}
.meta{display:flex;flex-wrap:wrap;gap:4px 16px;margin:auto 0 0;padding-top:10px;border-top:1px dashed var(--line);
font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.meta div{display:flex;gap:4px}.meta dt{margin:0}.meta dd{margin:0;color:var(--text);font-weight:600}
#empty{display:flex;flex-direction:column;align-items:center;gap:10px;color:var(--muted);text-align:center;padding:80px 0}
#empty svg{width:32px;height:32px}
.scrim{position:fixed;inset:0;z-index:25;background:rgba(10,14,18,.45)}
dialog{width:min(1100px,94vw);max-height:92vh;padding:0;border:1px solid var(--line);border-radius:16px;
background:var(--surface);color:var(--text);box-shadow:var(--shadow-lg)}
dialog::backdrop{background:rgba(10,14,18,.7);backdrop-filter:blur(2px)}
dialog[open]{display:flex;flex-direction:column;animation:pop .18s ease-out}
@keyframes pop{from{opacity:0;transform:scale(.98)}}
.viewer-top{display:flex;align-items:center;gap:12px;padding:14px 16px;border-bottom:1px solid var(--line)}
.viewer-title{flex:1;min-width:0}.viewer-title b{display:block;overflow-wrap:anywhere}
.viewer-title span{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
.btn{display:inline-flex;align-items:center;gap:8px;height:40px;padding:0 14px;border-radius:10px;background:var(--accent);
color:var(--on-accent);text-decoration:none;font-weight:600;white-space:nowrap;transition:background-color .15s}
.btn:hover{background:var(--accent-hover)}
.viewer-stage{display:flex;align-items:center;gap:12px;padding:16px;min-height:0;flex:1;background:var(--thumb)}
#v-img{flex:1;min-width:0;max-height:calc(92vh - 150px);object-fit:contain}
.viewer-hint{margin:0;padding:8px 16px;color:var(--muted);font-size:12px;text-align:center}
@media (max-width:1023px){
.layout{grid-template-columns:1fr}
.sidebar{position:fixed;inset:0 auto 0 0;width:min(320px,86vw);transform:translateX(-100%);transition:transform .22s ease-out;
box-shadow:var(--shadow-lg)}
body.nav-open .sidebar{transform:none}body.nav-open{overflow:hidden}
.topbar .open-nav,.brand .close-nav{display:inline-flex}
.topbar{padding:0 16px}.hero{padding:24px 16px 4px}main{padding:8px 16px 48px}}
@media (max-width:600px){.open-nav span,kbd{display:none}h1{font-size:22px}.stats{grid-template-columns:repeat(2,1fr)}
.grid{grid-template-columns:1fr}.btn{padding:0 10px}.btn svg+*{display:none}.viewer-stage .nav-btn{display:none}}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{transition:none!important;animation:none!important}}
'''

GALLERY_JS = '''
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
const q=$("#q"),found=$("#found"),viewer=$("#viewer");let current=-1;
function filter(){const v=q.value.trim().toLocaleLowerCase(),per={};let total=0;
for(const c of $$(".card")){const show=!v||c.dataset.search.toLocaleLowerCase().includes(v)||
c.dataset.folder.toLocaleLowerCase().includes(v);c.hidden=!show;if(show){total++;per[c.dataset.folder]=(per[c.dataset.folder]||0)+1}}
for(const s of $$(".group"))s.hidden=!$$(".card",s).some(c=>!c.hidden);
for(const a of $$(".nav-item[data-folder]")){const n=per[a.dataset.folder]||0;a.parentElement.hidden=!n;a.querySelector("b").textContent=n}
for(const g of $$(".nav-group"))g.hidden=!$$(".nav-item[data-folder]",g).some(a=>!a.parentElement.hidden)&&
!(document.body.classList.contains("dragging")&&$(".nav-new",g));
found.textContent=v?"Найдено: "+total:"";$("#empty").hidden=total>0;spy()}
q.addEventListener("input",filter);
addEventListener("keydown",e=>{if(e.key==="/"&&document.activeElement!==q&&!viewer.open){e.preventDefault();q.focus()}
else if(e.key==="Escape"&&document.activeElement===q&&q.value){q.value="";filter()}});
const items=$$(".nav-item"),targets=items.map(a=>document.getElementById(a.hash.slice(1)));
function spy(){const line=innerHeight*0.25;let active=null;
targets.forEach((t,i)=>{if(t&&!t.hidden&&!items[i].parentElement.hidden&&t.getBoundingClientRect().top<=line)active=items[i]});
items.forEach(a=>a.classList.toggle("active",a===active));
if(active){const box=$(".folders"),r=active.getBoundingClientRect(),b=box.getBoundingClientRect();
if(r.top<b.top||r.bottom>b.bottom)box.scrollTop+=r.top-b.top-b.height/2}}
let ticking=false;addEventListener("scroll",()=>{if(!ticking){ticking=true;requestAnimationFrame(()=>{spy();ticking=false})}},{passive:true});
const openBtn=$(".open-nav"),scrim=$(".scrim");
function nav(open){document.body.classList.toggle("nav-open",open);scrim.hidden=!open;openBtn.setAttribute("aria-expanded",open);
if(open)$(".close-nav").focus()}
openBtn.addEventListener("click",()=>nav(true));$(".close-nav").addEventListener("click",()=>{nav(false);openBtn.focus()});
scrim.addEventListener("click",()=>nav(false));
addEventListener("keydown",e=>{if(e.key==="Escape"&&document.body.classList.contains("nav-open")){nav(false);openBtn.focus()}});
items.forEach(a=>a.addEventListener("click",()=>nav(false)));
function visible(){return $$(".card").filter(c=>!c.hidden&&$(".zoom",c))}
function show(i){const list=visible();if(!list.length)return;current=(i+list.length)%list.length;const c=list[current];
$("#v-img").src=$(".zoom",c).dataset.src;$("#v-img").alt=$(".caption",c).textContent;$("#v-title").textContent=$(".caption",c).textContent;
$("#v-meta").textContent=c.dataset.folder+" · "+$$(".meta div",c).map(d=>d.textContent.replace(/^(\\D+)/,"$1 ")).join(" · ")+
" · "+(current+1)+" из "+list.length;$("#v-open").href=c.dataset.pdf}
document.addEventListener("click",e=>{const z=e.target.closest(".zoom");if(z){show(visible().indexOf(z.closest(".card")));
viewer.showModal()}});
viewer.addEventListener("click",e=>{if(e.target===viewer||e.target.closest("[data-close]"))viewer.close();
const s=e.target.closest("[data-step]");if(s)show(current+Number(s.dataset.step))});
viewer.addEventListener("keydown",e=>{if(e.key==="ArrowRight")show(current+1);else if(e.key==="ArrowLeft")show(current-1)});
viewer.addEventListener("close",()=>setTimeout(()=>{const c=visible()[current];if(c)$(".zoom",c).focus()}));
spy();
'''

EDIT_CSS = '''
.edit-btn{margin-left:auto}.edit-btn[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.tools{display:none;flex-wrap:wrap;gap:6px;padding-top:10px;border-top:1px dashed var(--line)}
body.editing .tools{display:flex}
.tool{display:inline-flex;align-items:center;justify-content:center;gap:6px;height:32px;min-width:32px;padding:0 10px;
border:1px solid var(--line-strong);border-radius:8px;background:var(--surface);font-size:13px;cursor:pointer;
transition:background-color .15s,border-color .15s}
.tool:hover:not(:disabled){border-color:var(--accent);background:var(--surface-2)}.tool:disabled{opacity:.4;cursor:default}
.tool svg{width:16px;height:16px}
select.tool{padding:0 8px;max-width:100%;flex:1 1 120px;color:var(--text)}
.tool.danger{color:#b42318;margin-left:auto}
@media (prefers-color-scheme:dark){.tool.danger{color:#ff8a80}}
.group:not(.variant) .order,.group:not(.variant) .handle{display:none}
.handle{display:inline-flex;align-items:center;color:var(--muted);cursor:grab;padding:0 2px}
body.editing .group.variant .card{cursor:grab}
.card.dragging{opacity:.5;outline:2px dashed var(--accent);outline-offset:2px}
.card.removed .media,.card.removed .caption,.card.removed .meta,.card.removed .badge{opacity:.35}
.card.removed .caption{text-decoration:line-through}
.card.removed::after{content:"Будет убрано";position:absolute;top:12px;left:12px;padding:3px 10px;border-radius:6px;
background:#b42318;color:#fff;font-size:12px;font-weight:600}
.card.moved{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft)}
.badge.accent{color:var(--accent);background:var(--accent-soft);border-color:transparent}
.empty-variant{grid-column:1/-1;padding:24px;border:2px dashed var(--line-strong);border-radius:var(--radius);
color:var(--muted);text-align:center}
.changes{position:sticky;bottom:16px;z-index:22;display:flex;align-items:center;gap:12px;flex-wrap:wrap;
margin:24px 32px 0;padding:12px 14px 12px 18px;max-width:1336px;border:1px solid var(--line-strong);border-radius:14px;
background:var(--surface);box-shadow:var(--shadow-lg)}
.changes-text{flex:1;min-width:220px;font-size:14px}.changes-text b{display:block}
.changes-text span{color:var(--muted);font-size:13px}
.changes.dirty{border-color:var(--accent)}
.btn:disabled{opacity:.5;cursor:default}button.btn{border:0;cursor:pointer;font:inherit}
.toast{position:fixed;left:50%;bottom:24px;z-index:60;transform:translateX(-50%);max-width:min(560px,92vw);padding:12px 16px;
border-radius:12px;background:var(--text);color:var(--bg);box-shadow:var(--shadow-lg);font-size:14px}
.toast.error{background:#b42318;color:#fff}
.pick{position:absolute;top:10px;left:10px;z-index:2;display:none;width:22px;height:22px;margin:0;accent-color:var(--accent);
cursor:pointer}
body.editing .pick{display:block}
body.editing .card{user-select:none}
.card.selected{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent)}
.card.selected .media{background:var(--accent-soft)}
.card.removed::after{left:42px}
.group-head .pick-all{display:none;margin-left:auto}
body.editing .group-head .pick-all{display:inline-flex}
.selection{display:flex;align-items:center;gap:8px;flex-wrap:wrap;flex-basis:100%;padding:10px 0 2px;
border-top:1px dashed var(--line)}
.selection b{font-variant-numeric:tabular-nums}.selection select.tool{flex:0 1 200px}
.card.arrived{animation:arrive 1.8s ease-out}
@keyframes arrive{0%,35%{border-color:var(--accent);box-shadow:0 0 0 4px var(--accent)}100%{}}
.drop-marker{position:fixed;z-index:40;width:4px;border-radius:2px;background:var(--accent);pointer-events:none;
box-shadow:0 0 0 3px var(--accent-soft)}
.group.drop-end .grid{outline:2px dashed var(--accent);outline-offset:6px;border-radius:var(--radius)}
.nav-item.drop-target{background:var(--accent);color:var(--on-accent)}
.nav-item.drop-target b{background:var(--surface);color:var(--accent)}
.nav-new{display:none}
body.dragging .nav-new{display:block}
.nav-new .nav-item{border:1px dashed var(--accent);color:var(--accent)}
body.dragging .nav-group:not(:first-child){opacity:.45}
.drag-ghost{position:fixed;top:-100px;left:-100px;padding:6px 12px;border-radius:999px;background:var(--accent);
color:var(--on-accent);font:600 13px "Segoe UI",system-ui,sans-serif}
.hint-keys{color:var(--muted);font-size:12px}
#rename{width:min(480px,92vw)}#rename[open]{display:block}
.rename-form{display:flex;flex-direction:column;gap:10px;padding:20px 22px}.rename-form h3{margin:0 0 4px;font-size:18px}
.rename-form label{font-size:13px;color:var(--muted)}
.rename-form input{height:40px;padding:0 12px;font:inherit;color:inherit;background:var(--surface);border:1px solid var(--line-strong);border-radius:10px}
.rename-form input:focus{outline:2px solid var(--accent);outline-offset:1px}
.rn-hint{margin:0;font-size:13px;color:var(--muted)}.rn-error{margin:0;font-size:13px;color:#b42318}
.rn-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:6px}
@media (max-width:1023px){.changes{margin:16px 16px 0}}
@media (max-width:600px){.edit-btn span{display:none}.hint-keys{display:none}.selection{gap:6px}.selection select.tool{flex:1 1 140px}}
'''

EDIT_JS = '''
const editBtn=$("#edit"),changes=$("#changes"),summary=$("#changes-summary"),saveBtn=$("#save"),toastBox=$("#toast");
const served=location.protocol.startsWith("http");let editing=false,saving=false,drag=null,initial="",toastTimer=0;
const svg=p=>'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+p+'</svg>';
function toast(text,error){toastBox.textContent=text;toastBox.className="toast"+(error?" error":"");toastBox.hidden=false;
clearTimeout(toastTimer);toastTimer=setTimeout(()=>toastBox.hidden=true,error?8000:5000)}
try{const m=sessionStorage.getItem("pdf-photos-toast");if(m){sessionStorage.removeItem("pdf-photos-toast");toast(m)}}catch(e){}
function variantNumbers(){return $$(".group.variant").map(s=>s.dataset.variant).filter(v=>/^[0-9]+$/.test(v)).map(Number).sort((a,b)=>a-b)}
function groupName(n){const s=$('.group.variant[data-variant="'+n+'"]');return s?s.dataset.folder:"Вариант "+n}
function payload(){const variants={};for(const s of $$(".group.variant"))
variants[s.dataset.variant]=$$(".card",s).filter(c=>!c.classList.contains("removed")).map(c=>+c.dataset.id);
return{revision:+document.body.dataset.revision,variants,removed:$$(".card.removed").map(c=>+c.dataset.id)}}
function section(n){n=String(n);let s=$('.group.variant[data-variant="'+n+'"]');if(s)return s;
s=document.createElement("section");s.className="group variant";s.dataset.variant=n;s.dataset.folder="Вариант "+n;s.id="new"+n;
s.innerHTML='<header class="group-head"><h2>Вариант '+n+'</h2><span class="count">0</span><p class="note">Новый вариант — PDF будет создан при сохранении</p></header><div class="grid"></div>';
const numbered=$$(".group.variant").filter(x=>/^[0-9]+$/.test(x.dataset.variant));
const after=numbered.filter(x=>+x.dataset.variant<+n).pop();
if(after)after.after(s);else{const first=$(".group.variant");if(first)first.before(s);else $("#content").prepend(s)}
if(typeof groupHead==="function"&&editing){groupHead(s)}return s}
function options(card){const own=card.closest(".group.variant")?.dataset.variant;const nums=variantNumbers();
const next=(nums.length?Math.max(...nums):0)+1;
return '<option value="">Перенести в…</option>'+nums.filter(n=>String(n)!==own).map(n=>'<option value="'+n+'">'+groupName(n)+'</option>').join("")+
'<option value="'+next+'">Новый вариант '+next+'</option>'}
function tools(card){if($(".tools",card))return;const t=document.createElement("div");t.className="tools";
t.innerHTML='<span class="handle" title="Перетащите, чтобы изменить порядок">'+svg('<circle cx="9" cy="6" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="18" r="1"/>')+'</span>'+
'<button class="tool order" type="button" data-act="prev" aria-label="Сдвинуть раньше" title="Сдвинуть раньше">'+svg('<path d="m15 18-6-6 6-6"/>')+'</button>'+
'<button class="tool order" type="button" data-act="next" aria-label="Сдвинуть позже" title="Сдвинуть позже">'+svg('<path d="m9 18 6-6-6-6"/>')+'</button>'+
'<select class="tool" data-act="move" aria-label="Перенести в другой вариант"></select>'+
(card.dataset.fileName?'<button class="tool" type="button" data-act="rename-file" title="Переименовать PDF">'+svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>')+'<span>Имя файла</span></button>':"")+
(card.dataset.folderRename?'<button class="tool" type="button" data-act="rename-folder" title="Переименовать папку">'+svg('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>')+'<span>Папка</span></button>':"")+
'<button class="tool danger" type="button" data-act="remove">'+svg('<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>')+'<span>Убрать</span></button>';
$(".body",card).append(t);pickBox(card);card.dataset.origin=card.dataset.origin||card.closest(".group").dataset.folder||"";
for(const el of $$("a,img",card))el.draggable=false}
function refresh(){for(const s of $$(".group")){const live=$$(".card",s).filter(c=>!c.classList.contains("removed"));
$(".count",s).textContent=live.length;const link=$('.nav-item[href="#'+s.id+'"] b');if(link)link.textContent=live.length;
if(!s.classList.contains("variant"))continue;const grid=$(".grid",s);let note=$(".empty-variant",grid);
if(!live.length&&!note){note=document.createElement("p");note.className="empty-variant";
note.textContent="В варианте не осталось изображений — его PDF будет удалён при сохранении.";grid.append(note)}
else if(live.length&&note)note.remove()}
for(const c of $$(".card")){const sel=$("select.tool",c);if(sel)sel.innerHTML=options(c);
const g=c.closest(".group.variant .grid");const list=g?$$(".card",g):[];const i=list.indexOf(c);
const p=$('[data-act="prev"]',c),n=$('[data-act="next"]',c);if(p){p.disabled=saving||i<=0;n.disabled=saving||i<0||i===list.length-1}
const here=c.closest(".group")?.dataset.folder;if(here)c.dataset.folder=here;
c.classList.toggle("moved",!!c.dataset.origin&&here!==c.dataset.origin);
let b=$(".badge.moved-from",c);if(c.classList.contains("moved")){if(!b){b=document.createElement("span");b.className="badge accent moved-from";
$(".body",c).prepend(b)}b.textContent="Перенесено из «"+c.dataset.origin+"»"}else if(b)b.remove();
const r=$('[data-act="remove"] span',c);if(r)r.textContent=c.classList.contains("removed")?"Вернуть":"Убрать"}
const now=JSON.stringify(payload()),dirty=now!==initial;changes.classList.toggle("dirty",dirty);saveBtn.disabled=saving||!dirty;
$("#discard").disabled=saving||!dirty;const removed=$$(".card.removed").length,moved=$$(".card.moved:not(.removed)").length;
const was=JSON.parse(initial||"{}").variants||{},is=payload().variants;const order=Object.keys(is).some(n=>{const a=(was[n]||[]).filter(i=>is[n].includes(i));
return a.join()!==is[n].filter(i=>a.includes(i)).join()});
const parts=[removed&&"убрано: "+removed,moved&&"перенесено: "+moved,order&&"порядок изменён"].filter(Boolean).join(" · ");
summary.textContent=dirty?parts.charAt(0).toUpperCase()+parts.slice(1)+". Изменения появятся в PDF после сохранения."
:"Щёлкните по карточке, чтобы выбрать; Shift и Ctrl — выбрать несколько. Выбранные можно убрать, перенести или перетащить вместе.";filter()}
function setSaving(on){saving=on;document.body.classList.toggle("saving",on);
for(const c of $$(".card"))c.draggable=editing&&!on;
for(const e of $$(".tools button,.tools select,.pick,.pick-all,#selection button,#selection select,#edit,#q"))e.disabled=on;
refresh();syncSelection()}
function setEditing(on){if(saving)return;if(on&&!served){toast("Редактирование доступно, когда просмотр открыт из программы: кнопка «Открыть просмотр» в окне «Фото из PDF».",true);return}
editing=on;document.body.classList.toggle("editing",on);editBtn.setAttribute("aria-pressed",on);$("span",editBtn).textContent=on?"Готово":"Редактировать";
if(on){$$(".card").forEach(tools);$$(".group").forEach(groupHead);syncSelection();if(!initial)initial=JSON.stringify(payload());changes.hidden=false;refresh()}
else{clearSelection();if(JSON.stringify(payload())===initial)changes.hidden=true}
for(const c of $$(".card"))c.draggable=on}
editBtn.addEventListener("click",()=>setEditing(!editing));
document.addEventListener("click",e=>{if(saving)return;const b=e.target.closest(".tool[data-act]");if(!b||b.tagName==="SELECT"||b.dataset.act.startsWith("rename"))return;const c=b.closest(".card");
if(b.dataset.act==="remove")c.classList.toggle("removed");
else{const sib=b.dataset.act==="prev"?c.previousElementSibling:c.nextElementSibling;if(sib&&sib.classList.contains("card")){
if(b.dataset.act==="prev")sib.before(c);else sib.after(c)}b.focus()}refresh()});
document.addEventListener("change",e=>{if(saving)return;const s=e.target.closest('select.tool[data-act="move"]');if(!s||!s.value)return;
const c=s.closest(".card"),n=s.value,dest=section(n);c.classList.remove("removed");$(".grid",dest).append(c);refresh();
reveal([c],dest);toast("Перенесено в «"+groupName(n)+"»")});
let anchor=null,dropTarget=null,autoY=null,autoTimer=0,revealTimer=0;const marker=document.createElement("div");marker.className="drop-marker";
marker.hidden=true;document.body.append(marker);
function cards(){return $$(".card").filter(c=>!c.hidden)}
function selected(){return $$(".card.selected")}
function pickBox(card){if($(".pick",card))return;const p=document.createElement("input");p.type="checkbox";p.className="pick";
p.setAttribute("aria-label","Выбрать изображение");$(".media",card).prepend(p)}
function select(card,mode,range){const list=cards();
if(range&&anchor&&list.includes(anchor)){const a=list.indexOf(anchor),b=list.indexOf(card);
if(mode!=="add")selected().forEach(c=>c.classList.remove("selected"));
list.slice(Math.min(a,b),Math.max(a,b)+1).forEach(c=>c.classList.add("selected"))}
else{if(mode==="only")selected().forEach(c=>c!==card&&c.classList.remove("selected"));
card.classList.toggle("selected",mode==="only"?true:!card.classList.contains("selected"));anchor=card}
syncSelection()}
function clearSelection(){selected().forEach(c=>c.classList.remove("selected"));anchor=null;syncSelection()}
function syncSelection(){for(const c of $$(".card")){const p=$(".pick",c);if(p)p.checked=c.classList.contains("selected")}
const list=selected();let bar=$("#selection");
if(!bar){bar=document.createElement("div");bar.id="selection";bar.className="selection";bar.innerHTML=
'<span>Выбрано: <b id="sel-count">0</b></span><button class="tool" type="button" data-bulk="remove">Убрать</button>'+
'<button class="tool" type="button" data-bulk="restore">Вернуть</button><select class="tool" data-bulk="move" aria-label="Перенести выбранные"></select>'+
'<button class="tool" type="button" data-bulk="clear">Снять выделение</button><span class="hint-keys">Shift — диапазон, Ctrl — по одному, Delete — убрать, Esc — снять</span>';
changes.append(bar)}
bar.hidden=!list.length;$("#sel-count").textContent=list.length;
$('[data-bulk="restore"]',bar).hidden=!list.some(c=>c.classList.contains("removed"));
$('[data-bulk="remove"]',bar).hidden=list.length>0&&list.every(c=>c.classList.contains("removed"));
const nums=variantNumbers(),next=(nums.length?Math.max(...nums):0)+1;
$('[data-bulk="move"]',bar).innerHTML='<option value="">Перенести в…</option>'+nums.map(n=>'<option value="'+n+'">'+groupName(n)+'</option>').join("")+
'<option value="'+next+'">Новый вариант '+next+'</option>'}
function moveCards(list,grid,before){if(saving)return;list=list.filter(c=>c!==before);for(const c of list){c.classList.remove("removed");
if(before)grid.insertBefore(c,before);else grid.append(c)}
refresh();syncSelection()}
function groupHead(s){const h=$(".group-head",s);if(!h||$(".pick-all",h))return;const b=document.createElement("button");
b.type="button";b.className="tool pick-all";b.textContent="Выбрать все";b.addEventListener("click",()=>{if(saving)return;
const list=$$(".card",s).filter(c=>!c.hidden),all=list.every(c=>c.classList.contains("selected"));
list.forEach(c=>c.classList.toggle("selected",!all));anchor=list[0]||null;syncSelection()});h.append(b);
if(s.classList.contains("variant")&&!s.id.startsWith("new")){const r=document.createElement("button");r.type="button";
r.className="tool pick-all";r.dataset.renameVariant="1";r.textContent="Переименовать";r.title="Переименовать папку и PDF варианта";h.append(r)}}
document.addEventListener("click",e=>{if(!editing||saving)return;const card=e.target.closest(".card");if(!card)return;
const pick=e.target.closest(".pick");
if(!pick&&e.target.closest(".tools,.zoom,.caption,select,button"))return;
if(!pick)e.preventDefault();const add=e.ctrlKey||e.metaKey;
select(card,pick||add?"add":"only",e.shiftKey);if(pick)pick.checked=card.classList.contains("selected")},true);
document.addEventListener("click",e=>{if(saving)return;const b=e.target.closest("[data-bulk]");if(!b||b.tagName==="SELECT")return;const list=selected();
if(b.dataset.bulk==="remove")list.forEach(c=>c.classList.add("removed"));
else if(b.dataset.bulk==="restore")list.forEach(c=>c.classList.remove("removed"));
else clearSelection();refresh();syncSelection()});
document.addEventListener("change",e=>{if(saving)return;const s=e.target.closest('select[data-bulk="move"]');if(!s||!s.value)return;const n=s.value,list=selected();
const dest=section(n);moveCards(list,$(".grid",dest),null);toast("Перенесено в «"+groupName(n)+"»: "+list.length);
reveal(list,dest)});
addEventListener("keydown",e=>{if(!editing||saving||viewer.open||document.body.classList.contains("nav-open"))return;
const typing=e.target instanceof Element&&e.target.closest("input:not(.pick),select,textarea");if(typing)return;
if(e.key==="Escape"&&selected().length){e.preventDefault();clearSelection()}
else if(e.key==="Delete"&&selected().length){e.preventDefault();selected().forEach(c=>c.classList.add("removed"));refresh();syncSelection()}
else if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="a"){e.preventDefault();cards().forEach(c=>c.classList.add("selected"));syncSelection()}});
function reveal(list,s){const target=list.find(c=>!c.hidden)||s;if(!target)return;const smooth=!matchMedia("(prefers-reduced-motion: reduce)").matches;target.scrollIntoView({block:target===s?"start":"center",behavior:smooth?"smooth":"instant"});for(const c of list){c.classList.remove("arrived");void c.offsetWidth;c.classList.add("arrived")}clearTimeout(revealTimer);revealTimer=setTimeout(()=>{const r=target.getBoundingClientRect();if(r.bottom<80||r.top>innerHeight-80)target.scrollIntoView({block:target===s?"start":"center",behavior:"instant"});spy()},900)}
function dragged(){return drag?(drag.classList.contains("selected")?selected():[drag]):[]}
function newVariantItem(){const ul=$(".nav-group ul");if(!ul)return;let li=$(".nav-new");if(!li){li=document.createElement("li");li.className="nav-new";
li.innerHTML='<a class="nav-item" href="#" data-new="1"><span>+ Новый вариант</span></a>';ul.append(li)}
li.hidden=false;li.closest(".nav-group").hidden=false}
function showMarker(card,after){const r=card.getBoundingClientRect();marker.style.left=(after?r.right+6:r.left-8)+"px";
marker.style.top=r.top+"px";marker.style.height=r.height+"px";marker.hidden=false}
function clearDrop(){marker.hidden=true;$$(".drop-end").forEach(s=>s.classList.remove("drop-end"));
$$(".nav-item.drop-target").forEach(a=>a.classList.remove("drop-target"));dropTarget=null}
function autoScroll(){if(!drag){clearInterval(autoTimer);autoTimer=0;return}const top=$(".topbar").getBoundingClientRect().bottom+70,
bottom=innerHeight-(changes.hidden?0:changes.offsetHeight+16)-70;let v=0;
if(autoY!==null&&autoY<top)v=-Math.min(40,Math.ceil((top-autoY)/3));else if(autoY!==null&&autoY>bottom)v=Math.min(40,Math.ceil((autoY-bottom)/3));
if(v)scrollBy({top:v,behavior:"instant"})}
document.addEventListener("dragstart",e=>{const c=e.target.closest(".card");if(saving){e.preventDefault();return}if(!editing||!c)return;drag=c;
if(!c.classList.contains("selected"))clearSelection();const list=dragged();list.forEach(x=>x.classList.add("dragging"));
document.body.classList.add("dragging");newVariantItem();e.dataTransfer.effectAllowed="move";
e.dataTransfer.setData("text/plain",list.map(x=>x.dataset.id).join(","));
if(list.length>1){const g=document.createElement("div");g.className="drag-ghost";g.textContent="Изображений: "+list.length;
document.body.append(g);e.dataTransfer.setDragImage(g,20,16);setTimeout(()=>g.remove())}
autoY=null;if(!autoTimer)autoTimer=setInterval(autoScroll,16)});
document.addEventListener("dragover",e=>{if(!drag)return;autoY=e.clientY;const list=dragged();clearDrop();
const nav=e.target.closest(".nav-item");
if(nav){const s=nav.dataset.new?null:document.getElementById(nav.hash.slice(1));
if(nav.dataset.new||(s&&s.classList.contains("variant"))){e.preventDefault();nav.classList.add("drop-target");
dropTarget={nav:nav.dataset.new?"new":s.dataset.variant}}return}
const group=e.target.closest(".group.variant");if(!group)return;e.preventDefault();const grid=$(".grid",group);
const over=e.target.closest(".card");
if(over&&!list.includes(over)){const r=over.getBoundingClientRect(),after=e.clientX-r.left>r.width/2;showMarker(over,after);
let before=after?over.nextElementSibling:over;while(before&&list.includes(before))before=before.nextElementSibling;
dropTarget={grid,before:before&&before.classList.contains("card")?before:null}}
else if(!over){group.classList.add("drop-end");dropTarget={grid,before:null}}});
document.addEventListener("dragleave",e=>{if(drag&&!e.relatedTarget)autoY=null});
document.addEventListener("drop",e=>{if(saving||!drag)return;e.preventDefault();const list=dragged(),t=dropTarget;clearDrop();if(!t)return;
if(t.nav){const n=t.nav==="new"?(variantNumbers().length?Math.max(...variantNumbers()):0)+1:t.nav;
const dest=section(n);moveCards(list,$(".grid",dest),null);toast("Перенесено в «"+groupName(n)+"»: "+list.length);reveal(list,dest)}
else moveCards(list,t.grid,t.before)});
document.addEventListener("dragend",()=>{if(!drag)return;$$(".dragging").forEach(x=>x.classList.remove("dragging"));
document.body.classList.remove("dragging");drag=null;autoY=null;clearInterval(autoTimer);autoTimer=0;clearDrop();refresh();syncSelection()});
$("#discard").addEventListener("click",()=>{if(saving)return;initial=JSON.stringify(payload());location.reload()});
saveBtn.addEventListener("click",async()=>{if(saving)return;const submitted=JSON.stringify(payload());setSaving(true);saveBtn.textContent="Сохраняю…";
try{const r=await fetch("api/apply",{method:"POST",headers:{"Content-Type":"application/json"},body:submitted});
const data=await r.json().catch(()=>({error:"Программа вернула непонятный ответ."}));if(!r.ok)throw new Error(data.error||("Ошибка "+r.status));
initial=submitted;try{sessionStorage.setItem("pdf-photos-toast",data.message)}catch(e){}location.reload()}
catch(err){toast(err.message==="Failed to fetch"?"Нет связи с программой. Откройте «Фото из PDF» и нажмите «Открыть просмотр» ещё раз.":err.message,true);
setSaving(false);saveBtn.textContent="Сохранить"}});
addEventListener("beforeunload",e=>{if(initial&&JSON.stringify(payload())!==initial){e.preventDefault();e.returnValue=""}});
const renameBox=document.createElement("dialog");renameBox.id="rename";renameBox.setAttribute("aria-labelledby","rn-title");
renameBox.innerHTML='<form class="rename-form"><h3 id="rn-title"></h3><label for="rn-input">Новое имя</label>'+
'<input id="rn-input" autocomplete="off" spellcheck="false"><p id="rn-hint" class="rn-hint"></p>'+
'<p id="rn-error" class="rn-error" role="alert" hidden></p><div class="rn-actions">'+
'<button type="button" class="icon-btn" data-rn-cancel>Отмена</button><button type="submit" class="btn" id="rn-ok">Переименовать</button></div></form>';
document.body.append(renameBox);let renameRequest=null;
function openRename(kind,target,current,title,hint){if(saving)return;
if(JSON.stringify(payload())!==initial){toast("Сначала сохраните или отмените изменения, потом переименовывайте.",true);return}
renameRequest={kind,target};$("#rn-title").textContent=title;$("#rn-hint").textContent=hint;const input=$("#rn-input");
input.value=current;$("#rn-error").hidden=true;$("#rn-ok").disabled=false;renameBox.showModal();input.select()}
$("[data-rn-cancel]",renameBox).addEventListener("click",()=>renameBox.close());
$("form",renameBox).addEventListener("submit",async e=>{e.preventDefault();const ok=$("#rn-ok");ok.disabled=true;
try{const r=await fetch("api/rename",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({revision:+document.body.dataset.revision,kind:renameRequest.kind,target:renameRequest.target,name:$("#rn-input").value})});
const data=await r.json().catch(()=>({error:"Программа вернула непонятный ответ."}));if(!r.ok)throw new Error(data.error||("Ошибка "+r.status));
initial="";try{sessionStorage.setItem("pdf-photos-toast",data.message);sessionStorage.setItem("pdf-photos-edit","1")}catch(x){}location.reload()}
catch(err){const box=$("#rn-error");box.textContent=err.message==="Failed to fetch"?"Нет связи с программой. Откройте «Фото из PDF» и нажмите «Открыть просмотр» ещё раз.":err.message;
box.hidden=false;ok.disabled=false;$("#rn-input").focus()}});
document.addEventListener("click",e=>{const b=e.target.closest('[data-act="rename-file"],[data-act="rename-folder"],[data-rename-variant]');if(!b||saving)return;
if(b.dataset.renameVariant){const s=b.closest(".group");openRename("variant",s.dataset.variant,s.dataset.folder,"Переименовать вариант",
"Меняется название папки и PDF варианта. Номер варианта сохраняется: переносить картинки в него можно как раньше.");return}
const c=b.closest(".card");if(b.dataset.act==="rename-file")openRename("file",c.dataset.id,c.dataset.fileName,"Переименовать PDF",
"Имя файла без «.pdf». Файл лежит в папке «"+c.dataset.folder+"».");
else openRename("folder",c.dataset.folder,c.dataset.folder,"Переименовать папку","Переименуется папка «"+c.dataset.folder+"» вместе со всеми её PDF.")});
try{if(sessionStorage.getItem("pdf-photos-edit")){sessionStorage.removeItem("pdf-photos-edit");setEditing(true)}}catch(e){}
'''


def extract(source, output=None, progress=None):
    source = Path(source).resolve(strict=True)
    # Destination errors must be found before PDF analysis and OCR.
    destination = check_output_path(output) if output else planned_output(source)
    if destination.exists():
        raise ValueError(f'Папка уже существует: {destination}')
    pages = inspect(source, progress)
    total_components = sum(page['source_components'] for page in pages)
    if not total_components:
        raise ValueError('В PDF не найдено отдельных растровых изображений. На страницах может быть только текст или векторная графика.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.pdf-photos-', dir=destination.parent)).resolve()
    if staging.parent != destination.parent.resolve() or not staging.name.startswith('.pdf-photos-'):
        raise ValueError('Некорректный путь временной папки.')
    folders, folder_owners = {}, {'без подписи': '__unsigned', 'на проверку': '__review',
                                 '_просмотр': '__preview', 'отчёт.json': '__report', 'просмотр.html': '__gallery'}
    entries, errors, suppressed, folder_labels, collages = [], [], [], {}, []
    warnings = [page[key] for page in pages for key in ('scan_warning', 'quality_warning') if page.get(key)]
    conflict = perspective_conflict(pages)
    pdfium_document = None
    reader = None
    raw_workspace = None

    def record_collages(page, paths, wholes, pieces, hinted):
        """Keep the whole picture of each cut collage and offer edge-to-edge renders for cutting."""
        folder = staging/SERVICE_DIR/COLLAGES_DIR
        for number, item in enumerate(page.get('seam_sources', []), 1):
            original = item['image']
            raster = tuple(original[k] for k in ('x0', 'top', 'x1', 'bottom'))
            members = pieces.get(raster, [])
            if not members:
                continue
            folder.mkdir(parents=True, exist_ok=True)
            target = folder/f'Страница {page["number"]:03d} — склейка {number}.pdf'
            whole = wholes.get(raster, ({},))[0]
            saved = export_region(page, paths, [original], bounds([original]), 900+number, target,
                                  whole.get('title'), quiet=True)
            if 'error' in saved:
                continue
            # A huge raster may be stored reduced; the boxes follow the pixels actually kept.
            scale = saved['size'][0]/original['srcsize'][0]
            identifier = f'{page["number"]}-{number}'
            checked = not item['thin'] and all(entries[index]['variant'] is not None for index in members)
            collages.append({
                'id': identifier, 'page': page['number'], 'state': 'auto', 'checked': checked,
                'file': saved['file'], 'size': saved['size'], 'bbox': saved['bbox'], 'title': whole.get('title'),
                'boxes': [[round(value*scale) for value in entries[index]['components'][0]['crop_px']]
                          for index in members],
                'reason': ('Разрезано по тонким линиям между кадрами одинакового размера.' if item['thin']
                           else 'Разрезано по светлым полосам между кадрами.')})
            for index in members:
                entries[index]['collage'] = identifier
        for number, (index, original) in enumerate(hinted, 1):
            entry = entries[index]
            sx, sy = entry['size'][0]/original['srcsize'][0], entry['size'][1]/original['srcsize'][1]
            identifier = f'{page["number"]}-h{number}'
            collages.append({
                'id': identifier, 'page': page['number'], 'state': 'suspected', 'checked': False,
                'image': index, 'size': entry['size'], 'title': entry['title'],
                'suggestion': [[round(box[0]*sx), round(box[1]*sy), round(box[2]*sx), round(box[3]*sy)]
                               for box in original['collage_hint']],
                'reason': 'Рендеры стоят вплотную без светлых полос: программа предлагает разделить '
                          'картинку на равные части, но сама не режет.'})
            entry['collage'] = identifier

    def set_aside(page, paths, group, box, sequence, components, suppression):
        """Record a service element and keep a recoverable copy: the filters are heuristics."""
        first = group[0]
        if len(group) == 1:
            crop = first.get('scan_crop_px')
            suppressed_size = [crop[2]-crop[0], crop[3]-crop[1]] if crop else list(first['srcsize'])
        else:
            suppressed_size = [round((box['x1']-box['x0'])*first['srcsize'][0]/max(first['x1']-first['x0'], .01)),
                               round((box['bottom']-box['top'])*first['srcsize'][1]/max(first['bottom']-first['top'], .01))]
        record = {'page': page['number'], 'sequence': sequence,
                  'bbox': [round(box[k], 3) for k in ('x0', 'top', 'x1', 'bottom')],
                  'size': suppressed_size, 'components': components, **suppression}
        suppressed.append(record)
        service_folder = staging/SERVICE_DIR/SUPPRESSED_DIR
        name = f'Страница {page["number"]:03d} — {sequence:03d}.pdf'
        if len(str(destination/SERVICE_DIR/SUPPRESSED_DIR/name)) > 239:
            record['export_error'] = 'Путь результата слишком длинный для копии служебного элемента.'
            return
        service_folder.mkdir(parents=True, exist_ok=True)
        saved = export_region(page, paths, group, box, sequence, service_folder/name, None, quiet=True)
        if 'error' in saved:
            record['export_error'] = saved['error']
            return
        record['image'] = len(entries)
        entries.append({'status': 'unsigned', 'title': None, 'candidates': [],
                        'reason': suppression['reason'], 'variant': None, 'group': None,
                        'page': page['number'], 'sequence': sequence, 'folder': 'Без подписи',
                        **saved, 'components': components, 'removed': True, 'suppressed': True,
                        'suppression_code': suppression['suppression_code'],
                        'original_location': {'folder': 'Без подписи', 'status': 'unsigned',
                                              'label': 'Без подписи',
                                              'reason': 'Возвращено вручную из служебных элементов.'}})

    def export_region(page, paths, group, box, sequence, target, title, quiet=False, blank_check=False):
        """Write one region as a single-page PDF; return its report fields or an error record.

        With blank_check, a raster with almost nothing drawn returns {'blank': share} and writes nothing."""
        nonlocal pdfium_document
        base = raw_folder/f'{page["number"]:03d}-{sequence:03d}'
        try:
            raster, size, method = save_group(reader.pages[page['number']-1], paths, group, base)
        except Exception as error:
            try:
                if pdfium_document is None:
                    import pypdfium2
                    with PDFIUM_LOCK:
                        pdfium_document = pypdfium2.PdfDocument(source)
                raster, size, method = save_crop(pdfium_document, page, box, base)
                if not quiet:
                    warnings.append({'page': page['number'], 'image': sequence,
                                     'reason': str(error), 'fallback': 'Снимок области страницы, 200 dpi.'})
            except Exception as fallback_error:
                return {'error': str(error), 'fallback_error': str(fallback_error)}
        if blank_check:
            try:
                share = drawn_share(raster)
            except Exception:
                # The measure only refines the result: a raster it cannot read is exported as before.
                share = None
            if share is not None and share < BLANK_DRAWN_SHARE:
                raster.unlink(missing_ok=True)
                return {'blank': round(share, 6)}
        bbox = [box[k] for k in ('x0', 'top', 'x1', 'bottom')]
        try:
            with raster.open('rb') as stream:
                image_checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
            matrix = group[0].get('matrix') if len(group) == 1 and method == 'original' else None
            image_to_pdf(raster, target, bbox, title, matrix)
        except Exception as error:
            target.unlink(missing_ok=True)
            return {'error': f'Не удалось сохранить PDF: {error}'}
        finally:
            raster.unlink(missing_ok=True)
        with target.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        return {'file': str(target.relative_to(staging)), 'size': size, 'method': method,
                'sha256': checksum, 'image_sha256': image_checksum,
                'pdf_page_size_pt': [round(bbox[2]-bbox[0], 4), round(bbox[3]-bbox[1], 4)],
                'bbox': [round(box[k], 3) for k in ('x0', 'top', 'x1', 'bottom')]}

    try:
        reader = PdfReader(source)
        raw_workspace = tempfile.TemporaryDirectory(prefix='.raster-', dir=staging)
        raw_folder = Path(raw_workspace.name).resolve()
        if raw_folder.parent != staging or not raw_folder.name.startswith('.raster-'):
            raise ValueError('Некорректный путь временных растров.')
        for page in pages:
            paths = image_paths(reader.pages[page['number']-1])
            groups = tile_groups(page['images'])
            if page.get('scan', {}).get('panels'):
                groups.sort(key=lambda group: min(im['scan_order'] for im in group))
            planned_groups = [(group, group_suppression(page, group)) for group in groups]
            page['regions'] = [bounds(group) for group, suppression in planned_groups if suppression is None]
            seam_sources = {}
            seam_pieces, hinted = {}, []
            for sequence, (group, suppression) in enumerate(planned_groups, 1):
                if progress:
                    progress({'stage': 'images', 'current': page['number'], 'total': len(pages),
                              'found': sum(not entry.get('removed') for entry in entries)})
                box = bounds(group)
                components = [{'object_id': im['object_id'], 'resource': im['name'],
                               'matrix': im.get('matrix'), 'size': list(im['srcsize']),
                               **({'crop_px': im['scan_crop_px'], 'source_bbox': im['source_bbox']}
                                  if 'scan_crop_px' in im else {})} for im in group]
                if suppression is not None:
                    set_aside(page, paths, group, box, sequence, components, suppression)
                    continue
                box['repeated_static'] = all(im.get('repeated_static') for im in group)
                if all(im.get('repeated_header') for im in group):
                    match = {'status': 'unsigned', 'title': None, 'candidates': [],
                             'reason': 'Повторяющийся элемент шапки: подпись рендера к нему не применяется.'}
                else:
                    match = region_caption(page, box)
                seam = len(group) == 1 and group[0].get('seam_cut')
                if seam:
                    raster = tuple(group[0]['source_bbox'])
                    if raster not in seam_sources:
                        whole = dict(zip(('x0', 'top', 'x1', 'bottom'), raster), repeated_static=False)
                        seam_sources[raster] = (region_caption(page, whole), [])
                    match = dict(seam_caption(box, match, *seam_sources[raster]), cut='seam')
                if page.get('scan'):
                    match['caption_method'] = ('pdf_text' if seam else 'windows_ocr'
                                               if not page['scan'].get('error') else 'unavailable')
                match, grouping_title = group_caption(match, conflict)
                if match['status'] == 'named':
                    key = clean_text(grouping_title).casefold()
                    if key not in folders:
                        # Caption folders sit one level deeper («Все остальное»); shorten them so that a
                        # file name «Страница 000 — 000.pdf» still fits the 239-character path budget.
                        limit = min(80, 214-len(str(destination/OTHER_DIR)))
                        if limit < 16:
                            raise OutputPathError('Путь результата слишком длинный. Выберите другую папку ближе к корню диска.')
                        folder = available_folder_name(grouping_title, limit, folder_owners, key)
                        folders[key] = folder
                        folder_owners[folder.casefold()] = key
                    folder = folders[key]
                else:
                    folder = 'На проверку' if match['status'] == 'review' else 'Без подписи'
                folder_labels[folder] = grouping_title
                match['group'] = 'variant' if match['variant'] is not None else None
                relative_folder = group_dir(folder, entry_group(match))
                target_folder = staging/relative_folder
                target_folder.mkdir(parents=True, exist_ok=True)
                prefix = f'Страница {page["number"]:03d} — {sequence:03d}'
                stem = prefix+(' — '+safe_name(match['title']) if match['title'] else '')
                available = 239-len(str(destination/relative_folder))-1-len('.pdf')
                if available < len(prefix):
                    raise OutputPathError('Путь результата слишком длинный. Выберите другую папку ближе к корню диска.')
                target = target_folder/(stem[:available].rstrip(' .')+'.pdf')
                # A plain title strip or a white box is no picture; seam frames and scan crops keep their own checks.
                saved = export_region(page, paths, group, box, sequence, target, match['title'],
                                      blank_check=not seam and not any('scan_crop_px' in im for im in group))
                if 'blank' in saved:
                    drop_empty_folders(target_folder, staging)
                    set_aside(page, paths, group, box, sequence, components, {
                        'suppression_code': 'blank_image', 'drawn_share': saved['blank'],
                        'reason': 'Почти однотонная картинка: нарисовано меньше 0,5 % площади — '
                                  'например, пустая плашка заголовка или белая подложка.'})
                    continue
                if 'error' in saved:
                    errors.append({'page': page['number'], 'image': sequence, 'components': len(group), **saved})
                    continue
                entries.append(match | {'page': page['number'], 'sequence': sequence, 'folder': folder,
                                        **saved, 'components': components})
                if seam:
                    seam_pieces.setdefault(raster, []).append(len(entries)-1)
                elif len(group) == 1 and group[0].get('collage_hint'):
                    hinted.append((len(entries)-1, group[0]))
            record_collages(page, paths, seam_sources, seam_pieces, hinted)
            if progress:
                progress({'stage': 'images', 'current': page['number'], 'total': len(pages),
                          'found': sum(not entry.get('removed') for entry in entries)})
        active = [entry for entry in entries if not entry.get('removed')]
        if not active and (errors or not suppressed):
            raise ValueError('Не удалось сохранить изображения. '+json.dumps(errors[:3], ensure_ascii=False))
        raw_workspace.cleanup()
        for entry in entries:
            if entry.get('removed'):
                entry['pdf_page'] = 1
        documents = merge_variant_pdfs(staging, active, progress)
        with source.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        report = {'version': VERSION, 'revision': 0, 'layout': LAYOUT, 'source': str(source), 'source_sha256': checksum,
                  'output_format': 'pdf', 'grouping': 'explicit_variant_then_caption',
                  'variant_output': 'one_multipage_pdf_per_variant',
                  'folders': {'other': OTHER_DIR, 'service': SERVICE_DIR},
                  # A caption whose only picture was set aside as blank names no folder.
                  'folder_labels': {folder: label for folder, label in folder_labels.items()
                                    if any(entry['folder'] == folder for entry in entries)},
                  'page_contexts': [{'page': p['number'], 'divider': p['variant_divider'],
                                     'context': p['variant_context']} for p in pages],
                  'output': str(destination), 'pages': len(pages), 'source_components': total_components,
                  'scan_pages': [p['scan'] for p in pages if p.get('scan')],
                  'counts_by_folder': dict(sorted(Counter(e['folder'] for e in active).items())),
                  'counts_by_status': dict(Counter(e['status'] for e in active)),
                  'warnings': warnings, 'errors': errors, 'images': entries, 'documents': documents,
                  'collages': collages,
                  'suppressed': suppressed, 'counts_by_suppression': dict(Counter(item['suppression_code'] for item in suppressed)),
                  'perspective_conflict': conflict,
                  'note': 'Изображения каждого варианта объединены в один PDF в порядке исходных страниц '
                          'и расположения на странице. Перспективные изображения без номера варианта в подписи, '
                          'на странице и в разделе отнесены к варианту 1, если сама презентация этому не противоречит '
                          '(вариант 1 помечен как предыдущий или таблица указывает другой выбранный вариант); '
                          'иначе они остаются на проверке. Остальные растровые элементы '
                          'сохранены отдельными PDF в папке «Все остальное»; '
                          'соприкасающиеся полосы объединены. '
                          'Повторяющиеся вспомогательные элементы, мелкие области менее 1 % страницы '
                          'после сборки фрагментов и доказанно полностью перекрытые изображения '
                          'не экспортируются в результат; их координаты и основания перечислены в suppressed, '
                          'а копии лежат в «_Служебное/Служебные элементы» как убранные карточки, которые можно вернуть. '
                          'Отдельный слайд варианта задаёт раздел до следующего раздела; '
                          'общий заголовок с одним вариантом применяется к содержимому своей страницы. '
                          'Для цельных сканов без текстового слоя используется локальный русский OCR Windows. '
                          'Раздельные прямоугольные фотографии вырезаются в исходных пикселях; '
                          'неуверенно распознанные коллажи сохраняются целиком. '
                          'Один исходный растр может упоминаться в компонентах нескольких вырезанных областей. '
                          'Векторная графика отдельно не экспортируется.'}
        if progress:
            progress({'stage': 'export', 'current': len(active), 'total': len(active), 'found': len(active)})
        render_previews(staging, report, progress)
        write_gallery(staging, report)
        (staging/'Отчёт.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        staging.rename(destination)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    finally:
        release_large_rasters()
        if raw_workspace is not None:
            raw_workspace.cleanup()
        if pdfium_document is not None:
            with PDFIUM_LOCK:
                pdfium_document.close()
        if reader is not None:
            reader.close()
    return report


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Все изображения PDF в папки по найденным заголовкам.')
    parser.add_argument('pdf', type=Path, nargs='+')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output and len(args.pdf) != 1:
        parser.error('--output используется только с одним PDF')
    failed = False
    for source in args.pdf:
        try:
            report = extract(source, args.output)
            if sys.stdout is not None:
                print(json.dumps({k: report[k] for k in ('output', 'counts_by_folder', 'counts_by_status')}
                                 | {'total': len(report['images']), 'pdf_files': len(report['documents']),
                                    'errors': report['errors']}, ensure_ascii=False))
            failed |= bool(report['errors'])
        except Exception as error:
            if sys.stderr is not None:
                print(f'Ошибка: {source.name}: {error}', file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
