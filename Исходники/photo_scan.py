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

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat
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
    return _reading_order(boxes, image.height)


def _reading_order(boxes, height):
    rows = []
    for box in sorted(boxes, key=lambda b: (b[1], b[0])):
        if not rows or box[1]-rows[-1][0][1] > height*.015:
            rows.append([])
        rows[-1].append(box)
    return [box for row in rows for box in sorted(row, key=lambda b: b[0])]


# A gutter is a band in which almost every pixel is light; a small caption painted inside it is allowed.
GUTTER_LIGHT = .9
# A thin grey separator between renders is not white, yet nearly every pixel of it is pale.
PALE = 215
GUTTER_PALE = .97
# Rows of a caption such as «ВИД №1» painted in a gutter or a margin are still mostly light.
CAPTION_LIGHT = .7
# Each piece must look like a photograph, so drawings and tables on white stay whole.
PIECE_LIGHT = .5
PIECE_SIDE = .15
# A wide white gutter proves a cut by itself. A thin or grey one is accepted only between pieces of
# nearly equal size, as in a row of four renders: a white band on a facade rarely divides it evenly.
WIDE_GUTTER = .012
THIN_GUTTER = .0025
EQUAL_PIECES = 1.1


def _profile(masks, box, rows):
    """Share of light pixels in every line across the box, and whether the line may be part of a gutter."""
    shares = []
    for mask in masks:
        with mask.crop(box) as part, part.resize((1, part.height) if rows else (part.width, 1), Image.BOX) as line:
            shares.append([value/255 for value in line.tobytes()])
    light, pale = shares
    return light, [a > GUTTER_LIGHT or b > GUTTER_PALE for a, b in zip(light, pale)]


def _margin(values):
    """Lines to drop from the start: a light margin, and a caption in it when a light gap still follows.

    Only white counts here: a pale sky at the top of a render is part of the photo."""
    count = 0
    while count < len(values) and values[count] > GUTTER_LIGHT:
        count += 1
    end = count
    while end < len(values) and end-count < len(values)*.06 and values[end] > CAPTION_LIGHT:
        end += 1
    gap = 0
    while gap < end-count and values[end-1-gap] > GUTTER_LIGHT:
        gap += 1
    # Stop only where the photo starts; a long light run is a sky, not a caption.
    if end < len(values) and values[end] <= CAPTION_LIGHT and gap >= 3 and end-gap > count:
        return end
    return count


def _trim_light(masks, box):
    """Drop light margins, together with any caption painted in them."""
    left, top, right, bottom = box
    rows = _profile(masks, box, True)[0]
    top, bottom = top+_margin(rows), bottom-_margin(rows[::-1])
    if top >= bottom:
        return None
    columns = _profile(masks, (left, top, right, bottom), False)[0]
    left, right = left+_margin(columns), right-_margin(columns[::-1])
    return (left, top, right, bottom) if left < right else None


def _gutters(values, gutter):
    """Bands across the whole box as (start, end, wide); a caption line inside a band does not break it."""
    runs, run = [], None
    for index, flag in enumerate(gutter+[False]):
        if flag and run is None:
            run = index
        elif not flag and run is not None:
            runs.append([run, index])
            run = None
    merged = []
    for run in runs:
        if merged:
            gap = values[merged[-1][1]:run[0]]
            if len(gap) < len(values)*.04 and sum(gap)/len(gap) > CAPTION_LIGHT:
                merged[-1][1] = run[1]
                continue
        merged.append(run)
    size = len(values)
    return [(a, b, b-a >= max(3, size*WIDE_GUTTER) and sum(values[a:b])/(b-a) > CAPTION_LIGHT)
            for a, b in merged if b-a >= max(2, size*THIN_GUTTER)]


def _pieces(values, cuts):
    edges = [0]+[edge for cut in cuts for edge in cut[:2]]+[len(values)]
    pieces = [(edges[i], edges[i+1]) for i in range(0, len(edges), 2)]
    if all(end-begin >= len(values)*PIECE_SIDE and sum(values[begin:end])/(end-begin) < PIECE_LIGHT
           for begin, end in pieces):
        return pieces
    return None


class GutterCut(list):
    """Piece boxes; `thin` when a narrow or grey separator was needed, which a person should confirm."""
    thin = False


def _gutter_split(masks, box, depth, cut):
    box = _trim_light(masks, box)
    if box is None:
        return []
    for rows in (True, False):
        values, gutter = _profile(masks, box, rows)
        start = box[1] if rows else box[0]
        cuts = _gutters(values, gutter)
        wide = [cut for cut in cuts if cut[2]]
        pieces = wide and _pieces(values, wide)
        if not pieces and cuts:
            pieces = _pieces(values, cuts)
            sizes = [end-begin for begin, end in pieces or []]
            if sizes and max(sizes) > min(sizes)*EQUAL_PIECES:
                pieces = None
            cut.thin = cut.thin or bool(pieces)
        if pieces:
            if depth >= 2:
                return [box]
            found = []
            for begin, end in pieces:
                piece = ((box[0], start+begin, box[2], start+end) if rows
                         else (start+begin, box[1], start+end, box[3]))
                found += _gutter_split(masks, piece, depth+1, cut)
            return found
    return [box]


def gutter_panels(image):
    """Photos joined into one raster by bands that cross the whole picture.

    Light corners of a render (sky, snow on roofs) defeat photo_panels, but a band through the
    entire raster is independent proof of the cut: renders stacked as «view 1 / view 2», or a row
    of renders divided by thin white or grey lines."""
    with image.convert('L') as gray:
        # Enough resolution for a separator of a few pixels in a 4000-pixel row of renders.
        gray.thumbnail((1800, 1800))
        width, height = gray.size
        background = median(gray.getpixel(corner) for corner in
                            ((0, 0), (width-1, 0), (0, height-1), (width-1, height-1)))
        threshold = min(238, background-12) if background >= 210 else 238
        masks = (gray.point(lambda value: 255 if value >= threshold else 0),
                 gray.point(lambda value: 255 if value >= PALE else 0))
    cut = GutterCut()
    try:
        boxes = _gutter_split(masks, (0, 0, width, height), 0, cut)
    finally:
        for mask in masks:
            mask.close()
    if len(boxes) < 2 or any((b[2]-b[0])*(b[3]-b[1]) < width*height*.04 for b in boxes):
        return GutterCut()
    sx, sy = image.width/width, image.height/height
    cut.extend(_reading_order([[round(b[0]*sx), round(b[1]*sy), round(b[2]*sx), round(b[3]*sy)] for b in boxes],
                              image.height))
    return cut


def trim_margins(image, box):
    """The box without the light margins (and captions painted in them) of a gutter left around a frame."""
    with image.crop(box) as part, part.convert('L') as gray:
        gray.thumbnail((1800, 1800))
        masks = (gray.point(lambda value: 255 if value >= 238 else 0),
                 gray.point(lambda value: 255 if value >= PALE else 0))
        try:
            trimmed = _trim_light(masks, (0, 0, gray.width, gray.height))
        finally:
            for mask in masks:
                mask.close()
        sx, sy = (box[2]-box[0])/gray.width, (box[3]-box[1])/gray.height
    if trimmed is None:
        return box
    left, top, right, bottom = trimmed
    return [box[0]+round(left*sx), box[1]+round(top*sy), box[0]+round(right*sx), box[1]+round(bottom*sy)]


def equal_split_hint(image):
    """Boxes of renders placed edge to edge with no separator, or [] when there is no such evidence.

    Only a hint for a person: a hard jump between neighbouring pixel columns (or rows) at every
    position of an even split. A tower edge rarely lines up with all of them, but it can, so the
    result is offered as «Возможно, склейка» and never cut automatically."""
    with image.convert('L') as gray:
        gray.thumbnail((1600, 1600))
        width, height = gray.size
        best = None
        for rows in (False, True):
            size, other = (height, width) if rows else (width, height)
            if size < 300:
                continue
            first, second = ((gray.crop((0, 1, width, height)), gray.crop((0, 0, width, height-1))) if rows
                             else (gray.crop((1, 0, width, height)), gray.crop((0, 0, width-1, height))))
            with first, second, ImageChops.difference(first, second) as jump:
                with jump.resize((1, height-1) if rows else (width-1, 1), Image.BOX) as line:
                    profile = list(line.tobytes())
            for count in range(6, 1, -1):
                if not .25 <= size/count/other <= 1.6:
                    continue
                ratios, peaks = [], []
                for index in range(1, count):
                    position = round(index*size/count)
                    near = range(max(0, position-round(size*.004)), min(len(profile), position+round(size*.004)+1))
                    peak = max(near, key=lambda x: profile[x])
                    ring = [profile[x] for x in range(max(0, position-round(size*.06)), min(len(profile), position+round(size*.06)))
                            if abs(x-position) > size*.012]
                    ratios.append(profile[peak]/max(1, median(ring)))
                    peaks.append(peak+1)
                strong = (all(r >= 3 for r in ratios) and min(profile[p-1] for p in peaks) >= 18 if count > 2
                          else ratios[0] >= 4.5 and profile[peaks[0]-1] >= 25)
                if strong and (best is None or count > len(best[1])+1):
                    best = rows, peaks
        if best is None:
            return []
        rows, peaks = best
        edges = [0, *peaks, height if rows else width]
        sx, sy = image.width/width, image.height/height
        boxes = [[0, round(edges[i]*sy), image.width, round(edges[i+1]*sy)] if rows
                 else [round(edges[i]*sx), 0, round(edges[i+1]*sx), image.height] for i in range(len(edges)-1)]
        return boxes


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


def _cut_problem(page, original, image, full_page):
    """Why a raster must not be cut automatically, or None."""
    a, b, c, d = original.get('matrix', (1, 0, 0, 1))
    if b or c or a < 0 or d < 0:
        return 'Поворот или отражение скана: OCR и разрезание отключены, исходная ориентация сохранена.'
    ratio = (original['x1']-original['x0'])/(original['bottom']-original['top'])
    if abs(image.width/image.height/ratio-1) > .025:
        return 'Поворот или деформация скана: разрезание отключено.'
    if not full_page and (original['x0'] < -1 or original['top'] < -1
            or original['x1'] > page['width']+1 or original['bottom'] > page['height']+1):
        return 'Часть составного растра находится за краем страницы; требуется проверка.'
    return None


def analyse_scans(source, pages, resource_paths, progress=None):
    candidates = []
    for page in pages:
        full_page = (not page['lines'] and len(page['images']) == 1
                     and _area(page['images'][0]) > page['width']*page['height']*.80)
        # OCR needs a raster of at least 15 % of the page; a smaller one is still searched for gutters
        # between renders, like a pair of views in one picture of a comparison slide.
        images = [im for im in page['images'] if full_page or (
                  not im.get('repeated_static') and not im.get('fully_occluded') and not im.get('blank_layer')
                  and not im.get('text_fill')
                  and _area(im) >= page['width']*page['height']*.04
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
                        ocr = full_page or _area(original) >= page['width']*page['height']*.15
                        panels = None if full_page else photo_panels(image) if ocr else []
                        seam = False
                        # Renders that fill the raster edge to edge have no plain background around them;
                        # the bands between them prove the cut instead, without OCR.
                        if not full_page and (len(panels) < 2 or not _compound_background(image, panels)):
                            seams = gutter_panels(image)
                            if len(seams) >= 2 and not _cut_problem(page, original, image, full_page):
                                panels, seam = seams, True
                                page.setdefault('seam_sources', []).append({'image': original, 'thin': seams.thin})
                            elif len(panels) < 2:
                                # Anything doubtful keeps the raster whole without a warning, as before;
                                # renders that only meet edge to edge are offered to a person to cut.
                                hint = [] if _cut_problem(page, original, image, full_page) else equal_split_hint(image)
                                if hint:
                                    original['collage_hint'] = [[round(value*factor) for value in box] for box in hint]
                                continue
                        scan = page.setdefault('scan', {'page': page['number'], 'panels': 0,
                                               'text_source': 'pdf_text' if seam else 'windows_ocr',
                                               'full_page': full_page, 'sources': []})
                        if not seam:
                            scan['text_source'] = 'windows_ocr'
                        record = {'bbox': [original[k] for k in ('x0', 'top', 'x1', 'bottom')],
                                  'object_id': original['object_id'], 'panels': 0}
                        scan['sources'].append(record)
                        problem = _cut_problem(page, original, image, full_page)
                        if problem:
                            raise ValueError(problem)
                        if seam:
                            # Renders carry slanted lines that OCR takes for rotated text, and labels such as
                            # «ВИД №1» are too small to read reliably: captions come from the page text.
                            record.update(method='seam', text_source='pdf_text')
                        elif not full_page and not _compound_background(image, panels):
                            raise ValueError('Между прямоугольниками нет простого однотонного фона; требуется проверка.')
                        if not seam:
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
                                             source_bbox=record['bbox'], **({'seam_cut': True} if seam else {}))
                                        for box in panels]
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


def seam_caption(region, match, whole, kept):
    """Caption of a piece cut along a gutter.

    Pieces share the caption the whole raster had before the cut, so header text above the last
    piece of a row cannot rename it. A piece keeps its own caption only when it names another
    variant than the raster, as a column heading «Вариант 2» does, and pieces below it or to its
    right follow it: «Вариант 2» printed above a column names every view of that column."""
    def numbers(caption):
        return variant_numbers(caption.get('title') or ' '.join(caption.get('candidates') or []))
    own = numbers(match)
    if own and own != numbers(whole):
        kept.append((region, match))
        return match
    def overlap(a0, a1, b0, b1):
        return max(0, min(a1, b1)-max(a0, b0))/max(1, a1-a0)
    width, height = region['x1']-region['x0'], region['bottom']-region['top']
    above = [(box, caption) for box, caption in kept if box['bottom'] <= region['top']+height*.05
             and overlap(region['x0'], region['x1'], box['x0'], box['x1']) > .85]
    left = [(box, caption) for box, caption in kept if box['x1'] <= region['x0']+width*.05
            and overlap(region['top'], region['bottom'], box['top'], box['bottom']) > .85]
    if above or left:
        parent = (max(above, key=lambda item: item[0]['bottom']) if above
                  else max(left, key=lambda item: item[0]['x1']))[1]
        return dict(parent, reason=parent['reason']+' Подпись взята у соседнего кадра той же склейки.')
    if whole['status'] == 'unsigned':
        return match
    return dict(whole, reason=whole['reason']+' Подпись общая для всех кадров этой склейки.')


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
