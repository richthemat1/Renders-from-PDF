"""Optional Windows OCR and conservative native-pixel panels in flattened slides."""
from contextlib import contextmanager, ExitStack
from io import BytesIO
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
from statistics import median
import subprocess
import tempfile
from threading import Thread

from PIL import Image, ImageDraw, ImageFilter, ImageStat
from pypdf import PdfReader

from photo_layout import clean_text, variant_numbers

# Poster layers reach 200 Mpx (about 600 MB per decode) and trip Pillow's bomb guard.
# Such JPEGs are opened at a reduced JPEG scale that stays within this pixel budget.
LARGE_RASTER_PIXELS = 50_000_000


def is_large_raster(image):
    width, height = image['srcsize']
    return width*height > LARGE_RASTER_PIXELS


def open_large_raster(obj, budget=None):
    """Open a JPEG XObject at the coarsest 1/2^n scale within budget; return the image and native/decoded factor."""
    budget = LARGE_RASTER_PIXELS if budget is None else budget
    filters = obj.get('/Filter')
    filters = [str(item) for item in filters] if isinstance(filters, list) else [str(filters)]
    # pypdf removes outer encodings such as ASCII85 and returns the JPEG itself.
    if not filters or filters[-1] != '/DCTDecode':
        raise ValueError('Очень большое изображение без сжатия JPEG: сохранён снимок области страницы.')
    width, height = int(obj['/Width']), int(obj['/Height'])
    scale = 1
    while width*height/(scale*scale) > budget and scale < 8:
        scale *= 2
    previous = Image.MAX_IMAGE_PIXELS
    # The size check alone rejects the file; the decode below never materialises full resolution.
    Image.MAX_IMAGE_PIXELS = None
    try:
        image = Image.open(BytesIO(obj.get_data()))
    finally:
        Image.MAX_IMAGE_PIXELS = previous
    try:
        image.draft('RGB', (max(1, width//scale), max(1, height//scale)))
        image.load()
        if image.width*image.height > budget*1.01:
            raise ValueError('JPEG не поддерживает уменьшенное чтение; сохранён снимок области страницы.')
    except BaseException:
        image.close()
        raise
    return image, width/image.width


def blank_layer_ink(obj):
    """Share of visibly drawn pixels in a stacked JPEG layer, measured on a small decode."""
    image, _ = open_large_raster(obj, budget=4_000_000)
    with image, image.convert('L') as gray:
        histogram = gray.histogram()
    return sum(histogram[:235])/max(1, sum(histogram))


@contextmanager
def scan_image(reader, pdf_page, path, original):
    """Yield a decoded raster and the factor from its pixels to the native resource pixels."""
    if is_large_raster(original):
        image, factor = open_large_raster(reader.get_object(original['object_id']))
    else:
        image, factor = pdf_page.images[path].image, 1
    try:
        yield image, factor
    finally:
        image.close()


# Windows PowerShell 5.1 exposes the installed Windows Runtime OCR engine.
# Image contents and recognised text never leave this machine.
OCR_SCRIPT = r'''
$ErrorActionPreference='Stop'
[Console]::InputEncoding=[Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
function Reply($value){[Console]::WriteLine(($value|ConvertTo-Json -Depth 7 -Compress))}
try {
 Add-Type -AssemblyName System.Runtime.WindowsRuntime
 $null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
 $null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
 $null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
 $null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
 $asTask=[System.WindowsRuntimeSystemExtensions].GetMethods()|Where-Object {
  $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetGenericArguments().Count -eq 1
 }|Select-Object -First 1
 function Await($operation,$type){$task=$asTask.MakeGenericMethod($type).Invoke($null,@($operation));$task.Wait();$task.Result}
 $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('ru'))
 if($null -eq $engine){throw 'В Windows не установлен русский пакет распознавания текста (OCR).'}
 Reply @{ready=$true;limit=[Windows.Media.Ocr.OcrEngine]::MaxImageDimension}
} catch {Reply @{error=$_.Exception.Message};exit 1}
while($null -ne ($request=[Console]::ReadLine())) {
 $stream=$null;$bitmap=$null
 try {
  $path=($request|ConvertFrom-Json).path
  $file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
  $stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  $decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bitmap=Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  $lines=@(foreach($line in $result.Lines){@{words=@(foreach($word in $line.Words){
   $r=$word.BoundingRect;@{text=$word.Text;x=$r.X;y=$r.Y;width=$r.Width;height=$r.Height}
  })}})
  $answer=@{lines=$lines;angle=$result.TextAngle}
 } catch {$answer=@{error=$_.Exception.Message}}
 finally {if($null -ne $bitmap){$bitmap.Dispose()};if($null -ne $stream){$stream.Dispose()}}
 Reply $answer
}
'''


class WindowsOcr:
    def __enter__(self):
        self.process = None
        self.counter = 0
        self.workspace = tempfile.TemporaryDirectory(prefix='pdf-photos-ocr-')
        try:
            if os.name != 'nt':
                raise RuntimeError('Локальный OCR доступен в Windows 10/11.')
            script = Path(self.workspace.name)/'ocr.ps1'
            script.write_text(OCR_SCRIPT, encoding='utf-8-sig')
            executable = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
            self.process = subprocess.Popen(
                [str(executable), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
            self.responses = Queue()
            def read():
                for line in self.process.stdout:
                    self.responses.put(line)
                self.responses.put(None)
            self.thread = Thread(target=read, daemon=True)
            self.thread.start()
            self.limit = min(4000, self.response()['limit'])
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def response(self):
        try:
            line = self.responses.get(timeout=35)
        except Empty:
            raise RuntimeError('OCR не ответил за 35 секунд; исходный скан сохранён.') from None
        if line is None:
            raise RuntimeError('Служба локального OCR завершилась без ответа.')
        try:
            result = json.loads(line.lstrip('\ufeff'))
        except ValueError:
            raise RuntimeError('Не удалось прочитать ответ локального OCR.') from None
        if result.get('error'):
            raise RuntimeError(result['error'])
        return result

    def recognise(self, image, box):
        with image.copy() as reduced:
            reduced.thumbnail((self.limit, self.limit))
            # BitmapDecoder may retain a file handle even after stream.Dispose().
            # Never overwrite an image while the OCR host still references it.
            self.counter += 1
            path = Path(self.workspace.name)/f'page-{self.counter}.png'
            reduced.save(path)
            sx = (box['x1']-box['x0'])/reduced.width
            sy = (box['bottom']-box['top'])/reduced.height
        self.process.stdin.write(json.dumps({'path': str(path)}, ensure_ascii=False)+'\n')
        self.process.stdin.flush()
        result = self.response()
        if abs(result.get('angle') or 0) > 2:
            raise RuntimeError('OCR обнаружил поворот текста; автоматическое разрезание отключено.')
        lines = []
        for row in result['lines']:
            segments = []
            size = median(word['height'] for word in row['words'])*sy
            for word in sorted(row['words'], key=lambda word: word['x']):
                if not segments or (word['x']-segments[-1][-1]['x']-segments[-1][-1]['width'])*sx > max(18, size*2):
                    segments.append([])
                segments[-1].append(word)
            for words in segments:
                text = clean_text(' '.join(word['text'] for word in words))
                if not re.search(r'[^\W\d_]', text):
                    continue
                lines.append({'text': text, 'x0': box['x0']+min(w['x'] for w in words)*sx,
                              'x1': box['x0']+max(w['x']+w['width'] for w in words)*sx,
                              'top': box['top']+min(w['y'] for w in words)*sy,
                              'bottom': box['top']+max(w['y']+w['height'] for w in words)*sy,
                              'size': size})
        lines.sort(key=lambda line: (line['top'], line['x0']))
        for line in lines:
            raw = line['text']
            # Only an isolated OCR glyph immediately following 'Вариант'.
            text = re.sub(r'(\bвариант\s*№?\s*)[ЗзZ](?=\s|$)', r'\g<1>3', raw, flags=re.I)
            text = re.sub(r'\bN(?:2|Q|[oоº])(?=\d)', '№', text, flags=re.I)
            if text != raw:
                line.update(source_text=raw, text=text)
            line['text_source'] = 'windows_ocr'
        return lines

    def __exit__(self, *args):
        if self.process is not None:
            self.process.kill()
            self.process.wait(timeout=5)
            self.thread.join(timeout=2) if hasattr(self, 'thread') else None
            self.process.stdin.close()
            self.process.stdout.close()
        self.workspace.cleanup()


def photo_panels(image):
    """Dense rectangular components separated by light gutters; no fixed grid."""
    with image.convert('L') as gray:
        gray.thumbnail((1100, 1100))
        width, height = gray.size
        background = median(gray.getpixel(corner) for corner in
                            ((0, 0), (width-1, 0), (0, height-1), (width-1, height-1)))
        threshold = min(238, background-12) if background >= 210 else 238
        mask = gray.point(lambda value: 255 if value < threshold else 0)
    with mask:
        eroded = mask.filter(ImageFilter.MinFilter(7))
    with eroded:
        mask = eroded.filter(ImageFilter.MaxFilter(7))
    data = bytearray(mask.tobytes())
    boxes, doubtful = [], []
    uncertain = False
    for position in range(len(data)):
        if not data[position]:
            continue
        data[position] = 0
        todo = [position]
        left = right = position % width
        top = bottom = position // width
        count = 0
        while todo:
            current = todo.pop()
            x, y = current % width, current // width
            count += 1
            left, right, top, bottom = min(left, x), max(right, x), min(top, y), max(bottom, y)
            for near in ((current-1 if x else -1), (current+1 if x < width-1 else -1),
                         current-width, current+width):
                if 0 <= near < len(data) and data[near]:
                    data[near] = 0
                    todo.append(near)
        w, h = right-left+1, bottom-top+1
        if w < width*.07 or h < height*.08 or count < width*height*.012:
            continue
        edge = max(3, min(w, h)//20)
        corners = [(left, top, left+edge, top+edge), (right-edge+1, top, right+1, top+edge),
                   (left, bottom-edge+1, left+edge, bottom+1),
                   (right-edge+1, bottom-edge+1, right+1, bottom+1)]
        density = count/(w*h)
        corner_fill = []
        for corner in corners:
            with mask.crop(corner) as crop:
                corner_fill.append(ImageStat.Stat(crop).mean[0] > 80)
        # A bright cloud can occupy one corner of an otherwise dense photo.
        # Sparse diagrams still need all four corners, as before.
        rectangular = density > .55 and (all(corner_fill)
                        or (density > .95 and sum(corner_fill) >= 3))
        if not rectangular:
            doubtful.append([left, top, right+1, bottom+1])
            continue
        boxes.append([left, top, right+1, bottom+1])
    mask.close()
    # A light sky can empty a corner of a real photo. Such a region still counts when the confident
    # panels prove it is a cell of the same grid: aligned with one of them by row and another by column.
    tolerance = .015
    def aligned(box, other, start, end, size):
        return abs(box[start]-other[start]) <= size*tolerance and abs(box[end]-other[end]) <= size*tolerance
    for box in doubtful:
        if not (any(aligned(box, other, 1, 3, height) for other in boxes)
                and any(aligned(box, other, 0, 2, width) for other in boxes)):
            uncertain = True
    # If a substantial part is not rectangular, keep the slide intact.
    if uncertain:
        return []
    sx, sy = image.width/width, image.height/height
    boxes = [[round(box[0]*sx), round(box[1]*sy), round(box[2]*sx), round(box[3]*sy)] for box in boxes+doubtful]
    rows = []
    for box in sorted(boxes, key=lambda b: (b[1], b[0])):
        if not rows or box[1]-rows[-1][0][1] > image.height*.015:
            rows.append([])
        rows[-1].append(box)
    return [box for row in rows for box in sorted(row, key=lambda b: b[0])]


def _area(box):
    return max(0, box['x1']-box['x0'])*max(0, box['bottom']-box['top'])


def _inside(line, box):
    x, y = (line['x0']+line['x1'])/2, (line['top']+line['bottom'])/2
    return box['x0'] <= x <= box['x1'] and box['top'] <= y <= box['bottom']


def _merge_ocr_lines(existing, recognised):
    """Selectable PDF text remains authoritative when an OCR line duplicates it."""
    lines = list(existing)
    for line in recognised:
        if not any(clean_text(old['text']).casefold() == clean_text(line['text']).casefold()
                   and abs(old['top']-line['top']) < max(4, line['size'])
                   and abs(old['x0']-line['x0']) < max(8, line['size']) for old in lines):
            lines.append(line)
    return sorted(lines, key=lambda line: (line['top'], line['x0']))


def _compound_background(image, panels):
    """Reject busy diagrams whose content leaks outside the proposed rectangles."""
    with image.convert('L') as gray:
        gray.thumbnail((320, 320))
        sx, sy = gray.width/image.width, gray.height/image.height
        outside = Image.new('L', gray.size, 255)
        draw = ImageDraw.Draw(outside)
        for left, top, right, bottom in panels:
            draw.rectangle((int(left*sx), int(top*sy), int(right*sx+.99), int(bottom*sy+.99)), fill=0)
        with outside:
            histogram = gray.histogram(mask=outside)
        background = median(gray.getpixel(corner) for corner in
                            ((0, 0), (gray.width-1, 0), (0, gray.height-1), (gray.width-1, gray.height-1)))
        start, end = max(0, int(background)-12), min(256, int(background)+13)
        return background >= 210 and sum(histogram[start:end])/max(1, sum(histogram)) > .93


def _compound_text(engine, image, original, panels, page_lines=()):
    """Remove confirmed photographs only in the OCR copy, never in exported pixels.

    Captions may also be live PDF text printed over the raster, as on posters."""
    full_lines = engine.recognise(image, original)
    sx = (original['x1']-original['x0'])/image.width
    sy = (original['bottom']-original['top'])/image.height
    boxes = [dict(x0=original['x0']+box[0]*sx, top=original['top']+box[1]*sy,
                  x1=original['x0']+box[2]*sx, bottom=original['top']+box[3]*sy) for box in panels]
    for box in boxes:
        text = [line for line in full_lines if _inside(line, box)]
        if len(text) >= 3 and sum(len(line['text']) for line in text) >= 45:
            raise ValueError('Внутри прямоугольников плотный текст таблицы или схемы; требуется проверка.')
    with image.convert('RGB') as labels:
        draw = ImageDraw.Draw(labels)
        for left, top, right, bottom in panels:
            draw.rectangle((left, top, right-1, bottom-1), fill='white')
        lines = _merge_ocr_lines(full_lines, engine.recognise(labels, original))
    live = [line for line in page_lines if _inside(line, original)]
    headings = [line for line in lines+live if not any(_inside(line, box) for box in boxes)
                and len(re.findall(r'[^\W\d_]', line['text'])) >= 3]
    if not headings:
        raise ValueError('У составного растра не прочитаны подписи; требуется проверка разрезания.')
    if re.search(r'\b(?:таблиц\w*|ситуационн\w*|схем\w*|экспликац\w*|ТЭП|карта|план)\b',
                 ' '.join(line['text'] for line in headings), re.I):
        raise ValueError('Подписи указывают на таблицу, план или схему; растр сохранён целиком.')
    return lines


def analyse_scans(source, pages, resource_paths, progress=None):
    candidates = []
    for page in pages:
        full_page = (not page['lines'] and len(page['images']) == 1
                     and _area(page['images'][0]) > page['width']*page['height']*.80)
        images = [im for im in page['images'] if full_page or (
                  not im.get('repeated_static') and not im.get('fully_occluded') and not im.get('blank_layer')
                  and not im.get('text_fill')
                  and _area(im) >= page['width']*page['height']*.15
                  and min(im['srcsize']) >= 300)]
        if images:
            candidates.append((page, images, full_page))
    if not candidates:
        return
    with ExitStack() as stack:
        reader = stack.enter_context(PdfReader(source))
        engine = None
        startup_error = None
        for page, originals, full_page in candidates:
            if progress:
                # Let cancellation escape rather than turning it into an OCR warning.
                progress({'stage': 'ocr', 'current': page['number'], 'total': len(pages), 'found': 0})
            pdf_page = reader.pages[page['number']-1]
            paths = resource_paths(pdf_page)
            for original in originals:
                record = None
                try:
                    path = paths.get(original['object_id'])
                    if path is None:
                        if not full_page:
                            continue
                        raise ValueError('Для скана не найден отдельный ресурс.')
                    with scan_image(reader, pdf_page, path, original) as (image, factor):
                        # A normal photograph must not start OCR or produce scan warnings.
                        panels = None if full_page else photo_panels(image)
                        if not full_page and len(panels) < 2:
                            continue
                        scan = page.setdefault('scan', {'page': page['number'], 'panels': 0,
                                               'text_source': 'windows_ocr', 'full_page': full_page,
                                               'sources': []})
                        record = {'bbox': [original[k] for k in ('x0', 'top', 'x1', 'bottom')],
                                  'object_id': original['object_id'], 'panels': 0}
                        scan['sources'].append(record)
                        a, b, c, d = original.get('matrix', (1, 0, 0, 1))
                        if b or c or a < 0 or d < 0:
                            raise ValueError('Поворот или отражение скана: OCR и разрезание отключены, исходная ориентация сохранена.')
                        ratio = (original['x1']-original['x0'])/(original['bottom']-original['top'])
                        if abs(image.width/image.height/ratio-1) > .025:
                            raise ValueError('Поворот или деформация скана: разрезание отключено.')
                        if not full_page and (original['x0'] < -1 or original['top'] < -1
                                or original['x1'] > page['width']+1 or original['bottom'] > page['height']+1):
                            raise ValueError('Часть составного растра находится за краем страницы; требуется проверка.')
                        if not full_page and not _compound_background(image, panels):
                            raise ValueError('Между прямоугольниками нет простого однотонного фона; требуется проверка.')
                        if startup_error:
                            raise RuntimeError(startup_error)
                        if engine is None:
                            try:
                                engine = stack.enter_context(WindowsOcr())
                            except Exception as error:
                                startup_error = str(error)
                                raise
                        try:
                            lines = (engine.recognise(image, original) if full_page
                                     else _compound_text(engine, image, original, panels, page['lines']))
                        except RuntimeError as error:
                            # A late response after a timeout must never name another page.
                            startup_error = 'Распознавание остановлено после ошибки OCR: '+str(error)
                            raise
                        page['lines'] = _merge_ocr_lines(page['lines'], lines)
                        scan['lines'] = scan.get('lines', 0)+len(lines)
                        # A compound raster may carry no text of its own: its captions passed as live PDF text.
                        if not lines and full_page:
                            continue
                        panels = photo_panels(image) if full_page else panels
                        if not panels:
                            scan['kept_whole'] = True
                            continue
                        sx = (original['x1']-original['x0'])/image.width
                        sy = (original['bottom']-original['top'])/image.height
                        replacements = [dict(original, x0=original['x0']+box[0]*sx,
                                             top=original['top']+box[1]*sy, x1=original['x0']+box[2]*sx,
                                             bottom=original['top']+box[3]*sy,
                                             scan_crop_px=[round(value*factor) for value in box],
                                             source_bbox=record['bbox']) for box in panels]
                        position = next(i for i, im in enumerate(page['images']) if im is original)
                        page['images'][position:position+1] = replacements
                        record['panels'] = len(panels)
                        scan['panels'] += len(panels)
                except Exception as error:
                    scan = page.setdefault('scan', {'page': page['number'], 'panels': 0,
                                          'text_source': 'windows_ocr', 'full_page': full_page, 'sources': []})
                    if record is not None:
                        record.update(error=str(error), kept_whole=True)
                    if not scan['panels']:
                        scan.update(error=str(error), kept_whole=True)
                    page['scan_warning'] = {'page': page['number'], 'reason': 'OCR: '+str(error),
                                            'fallback': 'Исходная картинка страницы сохранена целиком.'}
            if page.get('scan', {}).get('panels'):
                # photo_panels already tolerates small offsets within one row.
                for index, image in enumerate(page['images']):
                    image['scan_order'] = index


def column_caption(page, region, match):
    """A confirmed column header also names panels below the first row."""
    if not page.get('scan', {}).get('panels') or match['status'] == 'review' or variant_numbers(match.get('title')):
        return match
    sources = [source for source in page['scan'].get('sources', []) if source['panels']
               and source['bbox'][0] <= region['x0']+1 and source['bbox'][1] <= region['top']+1
               and source['bbox'][2] >= region['x1']-1 and source['bbox'][3] >= region['bottom']-1]
    if page['scan'].get('full_page') is False and len(sources) != 1:
        return match
    left, top, right, bottom = sources[0]['bbox'] if sources else (0, 0, page['width'], page['height'])
    width, height = right-left, bottom-top
    headers = [line for line in page['captions'] if top <= line['top'] < top+height*.25
               and left <= (line['x0']+line['x1'])/2 <= right
               and len(variant_numbers(line['text'])) == 1]
    if len(set().union(*(variant_numbers(line['text']) for line in headers))) < 2:
        return match
    candidates = []
    for header in headers:
        supports = [box for box in page['regions']
                    if 0 <= box['top']-header['bottom'] < height*.08
                    and box['x0'] <= (header['x0']+header['x1'])/2 <= box['x1']
                    and (abs(box['x0']-header['x0']) < width*.04
                         or abs((box['x0']+box['x1']-header['x0']-header['x1'])/2)
                            < (box['x1']-box['x0'])*.25)]
        for box in supports:
            overlap = max(0, min(box['x1'], region['x1'])-max(box['x0'], region['x0']))
            if overlap/(region['x1']-region['x0']) > .85 and region['top'] >= box['top']-2:
                candidates.append(header)
    if len(candidates) != 1:
        return match
    header = candidates[0]
    return dict(match, status='named', title=header['text'], candidates=[header['text']],
                source_caption=header.get('source_text', header['text']),
                caption_bbox=[header[k] for k in ('x0', 'top', 'x1', 'bottom')],
                variant_source='ocr_column_heading', variant_source_page=page['number'],
                reason='Вариант определён по OCR-заголовку своей колонки на скане.')


def whole_scan_caption(page, match):
    if (not page.get('scan') or page['scan'].get('full_page') is False
            or page['scan'].get('panels') or page['scan'].get('error')):
        return match
    headers = [line for line in page['captions'] if page['height']*.13 <= line['top'] < page['height']*.25]
    if not headers:
        # OCR noise in a table footer must not become the name of the whole slide.
        return dict(status='unsigned', title=None, candidates=[], reason='Для цельного скана не найден однозначный заголовок.')
    top = min(line['top'] for line in headers)
    first = [line for line in headers if abs(line['top']-top) < 5]
    if len(first) != 1:
        return dict(status='review', title=None, candidates=[line['text'] for line in first],
                    reason='На цельном скане несколько заголовков; требуется проверка.')
    header = first[0]
    return dict(status='named', title=header['text'], candidates=[header['text']],
                source_caption=header.get('source_text', header['text']),
                caption_bbox=[header[k] for k in ('x0', 'top', 'x1', 'bottom')],
                reason='Заголовок цельного скана прочитан локальным OCR.')


def prepare_scan_continuations(pages):
    """Only consecutively numbered views can continue an illustrated OCR heading."""
    pattern = re.compile(r'перспективное\s+изображение\s*№\s*(\d+)\b', re.I)
    active = None
    for page in pages:
        if not page.get('scan') or page['scan'].get('error'):
            active = None
            continue
        labels = [line for line in page['captions'] if pattern.search(line['text'])]
        views = sorted({int(pattern.search(line['text'])[1]) for line in labels})
        variants = set().union(*(variant_numbers(line['text']) for line in page['captions']))
        context = page['variant_context']
        if len(variants) == 1 and context and views == [1]:
            active = dict(context, last_view=1)
        elif (not variants and active and views
              and views == list(range(active['last_view']+1, active['last_view']+len(views)+1))):
            page['variant_context'] = {key: value for key, value in active.items() if key != 'last_view'}
            page['variant_context']['kind'] = 'ocr_numbered_continuation'
            active['last_view'] = views[-1]
        else:
            active = None
