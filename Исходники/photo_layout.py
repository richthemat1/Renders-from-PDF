"""Document-independent geometry: text captions and adjacent raster tiles."""
from collections import Counter
import math
import re
import unicodedata

# «Вариант 3.1» is a subvariant of 3; only a dot joins the parts, so «Вариант 1, 2» still lists two.
VARIANT_NUMBER = r'\d+(?:\.\d+)?'
FULL_VARIANT = re.compile(rf'^Вариант\s*№?\s*({VARIANT_NUMBER})$', re.I)
SHORT_VARIANT = re.compile(rf'^[ВBV]\s*[-№]?\s*({VARIANT_NUMBER})$', re.I)
VARIANT_LABEL = re.compile(rf'\bвариант\s*№?\s*({VARIANT_NUMBER})\b', re.I)
VARIANT_KEY = re.compile(r'(\d{1,3})(?:\.(\d{1,3}))?', re.A)
NUMBERED_NAME = re.compile(r'^(?:№\s*)?(\d+)[.)]?\s+(.+)$')
COMPARISON_HEADING = re.compile(r'\bсравн\w*\b.*\bвариант\w*\b', re.I)
SELECTED_MARKER = re.compile(r'^(?:ВПМ|Выбранный\s+вариант)$', re.I)
SELECTION_REFERENCE = re.compile(
    r'^(ВПМ|Выбранный\s+вариант)\s*[:—–-]?\s*'
    rf'(?:(?:вариант\s*№?\s*({VARIANT_NUMBER}))|(?:№?\s*({VARIANT_NUMBER})\s*(?:вариант)?))$', re.I)
PLAN_CAPTION = re.compile(r'\b(?:схем\w*|ситуационн\w*|генплан\w*|карт[аыуе]\w*|ПОЗУ|планировочн\w*)\b', re.I)
OVERPRINT_NUMBER = re.compile(r'№?\d+[.)]?')
OVERPRINT_BAND = 2


def variant_key(value):
    """A whole variant stays an int (3, as in older reports); a subvariant is the string '3.1'."""
    match = VARIANT_KEY.fullmatch(str(value).strip().replace(',', '.'))
    major = match and int(match[1])
    minor = match and match[2] and int(match[2])
    if not match or not 1 <= major <= 999 or minor == 0:
        raise ValueError(f'Некорректный номер варианта: {value}')
    return f'{major}.{minor}' if minor else major


def found_variant(text):
    """A variant number read from a presentation, or None when it is out of range."""
    try:
        return variant_key(text)
    except ValueError:
        return None


def variant_sort_key(key):
    """Order 3 before 3.1 and 3.2 before 3.10; no variant goes last."""
    if key is None:
        return (1000, 0)
    major, _, minor = str(key).partition('.')
    return (int(major), int(minor or 0))


def variant_parent(key):
    """The whole variant a key belongs to: 3 for '3.1' and for 3."""
    return int(str(key).partition('.')[0])


def clean_text(value):
    text = re.sub(r'\s+', ' ', unicodedata.normalize('NFC', value)).strip(' \t\r\n.')
    text = re.sub(r'^[—–\-•]+\s+', '', text)
    # A known joined caption phrase; avoid guessing word boundaries in arbitrary names.
    return re.sub(r'\b(стеновая)(панель)\b', r'\1 \2', text, flags=re.I)


def deduplicate_words(words):
    """Prefer the later of two overprinted words, keeping real adjacent repetition."""
    joined = []
    index = 0
    while index < len(words):
        word = dict(words[index])
        if word['text'] == '№' and index+1 < len(words):
            next_word = words[index+1]
            if (re.fullmatch(r'\d+', next_word['text'])
                    and abs(word['top']-next_word['top']) < 2
                    and -.5 <= next_word['x0']-word['x1'] <= .75):
                word.update(text='№'+next_word['text'], x1=next_word['x1'],
                            bottom=max(word['bottom'], next_word['bottom']))
                index += 1
        joined.append(word)
        index += 1
    # Only words at nearly the same height can be overprints, so kept words are indexed by height
    # bands of OVERPRINT_BAND points: a page of drawings or tables no longer compares every pair.
    kept, keys, bands = [], [], {}
    for word in joined:
        text = re.sub(r'\s+', '', word['text']).casefold()
        is_number = bool(OVERPRINT_NUMBER.fullmatch(text))
        reach = max(1.5, (word['bottom']-word['top'])*.15)
        # One more band on each side absorbs rounding at the edge of the reach.
        nearby = sorted(index for band in range(math.floor((word['top']-reach)/OVERPRINT_BAND)-1,
                                                math.floor((word['top']+reach)/OVERPRINT_BAND)+2)
                        for index in bands.get(band, ()))
        replacement = None
        for index in nearby:
            old = kept[index]
            old_text, old_number = keys[index]
            same = text == old_text
            number = is_number and old_number
            if not (same or number):
                continue
            height = min(word['bottom']-word['top'], old['bottom']-old['top'])
            width = min(word['x1']-word['x0'], old['x1']-old['x0'])
            if height <= 0 or width <= 0 or abs(word['top']-old['top']) > max(1.5, height*.15):
                continue
            overlap = max(0, min(word['x1'], old['x1'])-max(word['x0'], old['x0']))
            if overlap/width >= (.72 if number else .85) and abs(word['x0']-old['x0']) <= max(2, width*.30):
                replacement = index
                break
        if replacement is not None:
            bands[math.floor(kept[replacement]['top']/OVERPRINT_BAND)].remove(replacement)
            kept[replacement], keys[replacement] = word, (text, is_number)
        else:
            replacement = len(kept)
            kept.append(word)
            keys.append((text, is_number))
        bands.setdefault(math.floor(word['top']/OVERPRINT_BAND), []).append(replacement)
    return kept


def text_lines(words):
    rows = []
    for word in sorted(deduplicate_words(words), key=lambda w: (w['top'], w['x0'])):
        size = float(word.get('size', 12))
        row = next((r for r in reversed(rows[-4:])
                    if abs(r[0]['top']-word['top']) <= max(2, min(4, size*.22))), None)
        if row is None:
            rows.append([word])
        else:
            row.append(word)
    lines = []
    for row in rows:
        segments = []
        for word in sorted(row, key=lambda w: w['x0']):
            if not segments or word['x0']-segments[-1][-1]['x1'] > max(18, float(word.get('size', 12))*2):
                segments.append([])
            segments[-1].append(word)
        for segment in segments:
            text = clean_text(' '.join(w['text'] for w in segment))
            if text and re.search(r'[^\W\d_]', text, re.UNICODE):
                lines.append({'text': text, 'x0': min(w['x0'] for w in segment),
                              'x1': max(w['x1'] for w in segment),
                              'top': min(w['top'] for w in segment),
                              'bottom': max(w['bottom'] for w in segment),
                              'size': max(float(w.get('size', 12)) for w in segment)})
    return sorted(lines, key=lambda line: (line['top'], line['x0']))


def prepare_captions(pages):
    repeated = Counter()
    explicit_variants = {key for page in pages for line in page['lines']
                         if (match := FULL_VARIANT.fullmatch(line['text']))
                         and (key := found_variant(match[1])) is not None}
    for page in pages:
        repeated.update({line['text'].casefold() for line in page['lines']
                         if line['top'] < page['height']*.13})
    threshold = max(3, math.ceil(len(pages)*.65))
    for page in pages:
        lines = [dict(line) for line in page['lines']
                 if len(line['text']) <= 400
                 and not (line['top'] < page['height']*.13
                          and repeated[line['text'].casefold()] >= threshold)]
        merged = []
        for line in lines:
            parent = next((old for old in reversed(merged[-4:])
                           if abs(old['x0']-line['x0']) < 8
                           and abs(old['size']-line['size']) <= old['size']*.10
                           and 0 <= line['top']-old['bottom'] <= max(7, old['size']*.75)
                           and len(old['text'])+len(line['text']) < 400), None)
            if parent is not None:
                parent.update(text=parent['text']+' '+line['text'],
                              x1=max(parent['x1'], line['x1']), bottom=line['bottom'])
            else:
                merged.append(line)
        for line in merged:
            full = FULL_VARIANT.fullmatch(line['text'])
            short = SHORT_VARIANT.fullmatch(line['text'])
            key = found_variant((full or short)[1]) if full or short else None
            if key is not None and (full or key in explicit_variants):
                line.setdefault('source_text', line['text'])
                line['text'] = f'Вариант {key}'
        page['captions'] = merged
    prepare_numbered_variant_labels(pages)


def prepare_numbered_variant_labels(pages):
    """Resolve '1 Author' on comparison slides using full labels in this PDF."""
    references = {}
    for page in pages:
        for line in page['lines']+page['captions']:
            full = VARIANT_LABEL.match(line['text'])
            if not full or variant_numbers(line['text']) != {found_variant(full[1])}:
                continue
            name = clean_text(line['text'][full.end():].lstrip(' .:;|—–-'))
            if name and not name[0].isdigit() and re.search(r'[^\W\d_]', name):
                references.setdefault((found_variant(full[1]), name.casefold()), page['number'])
    for page in pages:
        if not any(line['top'] < page['height']*.25 and COMPARISON_HEADING.search(line['text'])
                   for line in page['lines']):
            continue
        recognized = set()
        for line in page['captions']:
            numbered = NUMBERED_NAME.fullmatch(line['text'])
            if not numbered:
                continue
            number, name = int(numbered[1]), clean_text(numbered[2])
            reference_page = references.get((number, name.casefold()))
            if reference_page is None:
                continue
            line['source_text'] = line.get('source_text', line['text'])
            line['text'] = f'Вариант {number} — {name}'
            line['variant_label_reference_page'] = reference_page
            recognized.add(number)
        if recognized:
            page['numbered_comparison_variants'] = sorted(recognized)


def variant_numbers(text):
    found = list(VARIANT_LABEL.finditer(text or ''))
    numbers = {found_variant(item[1]) for item in found}
    for item in found:
        following = re.match(rf'\s*(?:[/,–-]|и)\s*№?\s*({VARIANT_NUMBER})\b', text[item.end():], re.I)
        if following:
            numbers.add(found_variant(following[1]))
    numbers.discard(None)
    return numbers


def prepare_variant_context(pages):
    """A sparse divider starts a section; illustrated headings apply to one page."""
    active = None
    for page in pages:
        area = page['width']*page['height']
        content = [image for image in page['images'] if not image.get('repeated_header')
                   and ((image['x1']-image['x0'])*(image['bottom']-image['top']) >= area*.06
                        or (image['x1']-image['x0'] >= page['width']*.20
                            and image['bottom']-image['top'] >= page['height']*.15))]
        lines = page['captions']
        numbers = variant_numbers(' '.join(line['text'] for line in page['lines']))
        comparison = any(line['top'] < page['height']*.25 and COMPARISON_HEADING.search(line['text'])
                         for line in page['lines'])
        if comparison:
            active = None
        if page.get('numbered_comparison_variants'):
            numbers.update(page['numbered_comparison_variants'])
            active = None
        divider = not content and 0 < len(lines) <= 8 and sum(len(line['text']) for line in lines) <= 240
        heading = None
        if divider:
            # Every new text-only section, including an appendix, ends the old one.
            active = None
            candidates = [line for line in lines if re.match(r'^вариант\b', line['text'], re.I)]
            if not comparison and len(numbers) == 1 and len(candidates) == 1:
                active = {'variant': next(iter(numbers)), 'title': candidates[0]['text'],
                          'page': page['number'], 'kind': 'section_heading'}
        else:
            if len(numbers) > 1 or (active and numbers and numbers != {active['variant']}):
                active = None
            headers = [line for line in page['lines'] if line['top'] < page['height']*.18
                       and variant_numbers(line['text'])]
            if not comparison and len(numbers) == 1 and headers:
                heading = {'variant': next(iter(numbers)), 'title': headers[0]['text'],
                           'page': page['number'], 'kind': 'page_heading'}
        page['variant_context'] = heading or active
        page['variant_divider'] = divider


def caption_with_variant_context(page, region, match):
    context = page.get('variant_context')
    if (not context or page.get('variant_divider') or match['status'] == 'review'
            or variant_numbers(match.get('title'))
            or any(variant_numbers(title) for title in match['candidates'])):
        return match
    if (region_is_clipped(page, region) or region.get('repeated_static')
            or PLAN_CAPTION.search(match.get('title') or '')):
        return match
    # Section headings name the content, not logos and small decorative elements.
    if (region['x1']-region['x0'] < page['width']*.07
            or region['bottom']-region['top'] < page['height']*.08):
        return match
    area = (region['x1']-region['x0'])*(region['bottom']-region['top'])
    largest = max(((box['x1']-box['x0'])*(box['bottom']-box['top'])
                   for box in page.get('regions', [])), default=area)
    if area < largest/3:
        return match
    title = context['title']
    if match['title']:
        title += ' — '+match['title']
    return dict(match, status='named', title=title, local_title=match['title'],
                variant_source=context['kind'], variant_source_page=context['page'],
                reason=match['reason']+' Вариант определён по заголовку '
                       f'на исходной странице {context["page"]}.')


def region_is_clipped(page, region):
    left, top = page.get('origin', (0, 0))
    return (region['x0'] < left-.5 or region['top'] < top-.5
            or region['x1'] > left+page['width']+.5
            or region['bottom'] > top+page['height']+.5)


def prepare_selected_variants(pages):
    """Use only explicit selection-to-number relations found in actual table cells."""
    references = [dict(reference, page=page['number']) for page in pages
                  for reference in page.get('selection_references', [])]
    numbers = {reference['variant'] for reference in references}
    chosen = references[0] if len(numbers) == 1 else None
    for page in pages:
        page['selected_variant'] = chosen


def selected_caption_with_variant(page, match):
    selected = page.get('selected_variant')
    if not selected or match['status'] != 'named' or not SELECTED_MARKER.fullmatch(match.get('title') or ''):
        return match
    return dict(match, title=f'Вариант {selected["variant"]} — {match["title"]}',
                variant_source='selection_table', variant_source_page=selected['page'],
                selection_reference=selected,
                reason=match['reason']+' Номер выбранного варианта прямо указан в ячейке таблицы '
                       f'на исходной странице {selected["page"]}.')


def tile_groups(images):
    """Join only touching, aligned tiles with matching native pixel scales."""
    groups = [[im] for im in images]
    for axis in ('vertical', 'horizontal'):
        changed = True
        while changed:
            changed = False
            for i, first in enumerate(groups):
                a = bounds(first)
                for j in range(i+1, len(groups)):
                    second = groups[j]
                    b = bounds(second)
                    cross = ('x0', 'x1') if axis == 'vertical' else ('top', 'bottom')
                    along = ('top', 'bottom') if axis == 'vertical' else ('x0', 'x1')
                    if any(abs(a[k]-b[k]) > .8 for k in cross):
                        continue
                    if min(abs(a[along[1]]-b[along[0]]), abs(b[along[1]]-a[along[0]])) > .65:
                        continue
                    example_a, example_b = first[0], second[0]
                    def scales(im):
                        return (im['srcsize'][0]/max(im['x1']-im['x0'], .01),
                                im['srcsize'][1]/max(im['bottom']-im['top'], .01))
                    sa, sb = scales(example_a), scales(example_b)
                    if any(abs(x-y)/max(x, y, .01) > .025 for x, y in zip(sa, sb)):
                        continue
                    groups[i] = first+second
                    groups.pop(j)
                    changed = True
                    break
                if changed:
                    break
    return sorted(groups, key=lambda group: (round(bounds(group)['top']/3), bounds(group)['x0']))


def bounds(images):
    return {'x0': min(im['x0'] for im in images), 'x1': max(im['x1'] for im in images),
            'top': min(im['top'] for im in images), 'bottom': max(im['bottom'] for im in images)}


def caption_for(page, region):
    width, height = region['x1']-region['x0'], region['bottom']-region['top']
    if (width < page['width']*.025 or height < page['height']*.035
            or (width/max(height, 1) > 12 and height < page['height']*.075)):
        return {'status': 'unsigned', 'title': None, 'candidates': [],
                'reason': 'Мелкий или узкий элемент: отдельная подпись не определена.'}
    choices = []
    for line in page['captions']:
        if len(re.findall(r'[^\W\d_]', line['text'], re.UNICODE)) < 3:
            continue
        overlap = max(0, min(region['x1'], line['x1'])-max(region['x0'], line['x0']))
        selected_marker = SELECTED_MARKER.fullmatch(line['text'])
        margin_marker = (selected_marker and line['x0'] < page['width']*.10
                         and 0 <= region['top']-line['bottom'] <= page['height']*.075
                         and len([box for box in page.get('regions', [region])
                                  if abs(box['top']-region['top']) < page['height']*.06
                                  and (box['x1']-box['x0'])*(box['bottom']-box['top'])
                                      >= page['width']*page['height']*.04]) == 1)
        if overlap/max(1, min(width, line['x1']-line['x0'])) < .45 and not margin_marker:
            continue
        # A label painted inside one photo cannot name an adjacent photo.
        center_x = (line['x0']+line['x1'])/2
        center_y = (line['top']+line['bottom'])/2
        owners = [box for box in page.get('regions', [])
                  if box['x1']-box['x0'] >= max(page['width']*.07, line['size']*3)
                  and box['bottom']-box['top'] >= max(page['height']*.08, line['size']*3)
                  and box['x0'] <= center_x <= box['x1']
                  and box['top'] <= center_y <= box['bottom']]
        contained = False
        if owners:
            owner = min(owners, key=lambda box: (box['x1']-box['x0'])*(box['bottom']-box['top']))
            contained = all(abs(owner[key]-region[key]) < 1 for key in ('x0', 'top', 'x1', 'bottom'))
            if not contained:
                continue
        above = region['top']-line['bottom']
        below = line['top']-region['bottom']
        inset = max(24, min(45, height*.12))
        if -inset <= above <= page['height']*.20:
            gap, side = abs(above), 'above'
        elif -inset <= below <= page['height']*.075:
            # A line that sits closer above the next picture is that picture's caption.
            if any(min(box['x1'], line['x1']) > max(box['x0'], line['x0'])
                   and 0 <= box['top']-line['bottom'] < below
                   and any(abs(box[key]-region[key]) >= 1 for key in ('x0', 'top', 'x1', 'bottom'))
                   for box in page.get('regions', [])):
                continue
            gap, side = abs(below)+(0 if contained else page['height']*.025), 'below'
        else:
            continue
        center_delta = abs((region['x0']+region['x1']-line['x0']-line['x1'])/2)
        score = gap/page['height'] + center_delta/page['width']*.10 - (.06 if contained else 0)
        choices.append((score, side, line))
    choices.sort(key=lambda choice: choice[0])
    if not choices:
        return {'status': 'unsigned', 'title': None, 'candidates': [],
                'reason': 'Рядом с изображением не найдена читаемая подпись.'}
    score, side, best = choices[0]
    siblings = [c[2] for c in choices
                if c[2] is not best and c[1] == side and c[2]['text'] != best['text']
                and abs(c[2]['top']-best['top']) < 7
                and abs(c[2]['x0']-best['x0']) > max(30, width*.18)
                and abs(c[2]['size']-best['size']) < best['size']*.25]
    if siblings:
        return {'status': 'review', 'title': None,
                'candidates': [best['text']]+[line['text'] for line in siblings],
                'reason': 'У одного изображения несколько заголовков. Сохранено целиком.'}
    if len(choices) > 1 and choices[1][0]-score < .012:
        other = choices[1][2]
        # A heading above the nearest caption tops the same stack; the parent rule below names it.
        stacked = side == choices[1][1] == 'above' and other['bottom'] <= best['top']+1
        if (other['text'] != best['text'] and abs(other['top']-best['top']) > 8
                and other['size'] >= best['size']*.85 and not stacked):
            return {'status': 'review', 'title': None,
                    'candidates': [best['text'], other['text']],
                    'reason': 'Неоднозначная связь изображения с соседними подписями.'}
    title = best['text']
    if side == 'above' and not SELECTED_MARKER.fullmatch(title):
        parents = [line for line in page['captions'] if line is not best
                   and line['size'] >= best['size']*1.18
                   and 0 < best['top']-line['bottom'] < page['height']*.20
                   and abs(line['x0']-best['x0']) < page['width']*.06
                   # A row of pictures between them means the heading belongs to an earlier section.
                   and not any(box['top'] >= line['bottom'] and box['bottom'] <= best['top']
                               and min(box['x1'], region['x1']) > max(box['x0'], region['x0'])
                               for box in page.get('regions', []))]
        if parents:
            parent = min(parents, key=lambda line: best['top']-line['bottom'])
            title = parent['text']+' — '+title
    match = {'status': 'named', 'title': title, 'candidates': [title],
             'source_caption': best.get('source_text', best['text']),
             'reason': 'Подпись определена по расположению текста на странице.',
             'caption_bbox': [round(best[k], 3) for k in ('x0', 'top', 'x1', 'bottom')]}
    if 'variant_label_reference_page' in best:
        reference_page = best['variant_label_reference_page']
        match.update(variant_label_reference_page=reference_page,
                     variant_source='numbered_comparison_label', variant_source_page=page['number'],
                     reason='Номер и название на странице сравнения совпадают с полной подписью '
                            f'варианта на исходной странице {reference_page}.')
    return match
