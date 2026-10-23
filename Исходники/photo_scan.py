"""Optional Windows OCR and conservative native-pixel panels in flattened slides."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
from statistics import median
import subprocess
import tempfile
from threading import Thread

from PIL import Image, ImageFilter, ImageStat
from pypdf import PdfReader

from photo_layout import clean_text, variant_numbers


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
    """Dense rectangular components separated by white gutters; no fixed grid."""
    with image.convert('L') as gray:
        gray.thumbnail((1100, 1100))
        width, height = gray.size
        mask = gray.point(lambda value: 255 if value < 238 else 0)
    with mask:
        eroded = mask.filter(ImageFilter.MinFilter(7))
    with eroded:
        mask = eroded.filter(ImageFilter.MaxFilter(7))
    data = bytearray(mask.tobytes())
    boxes = []
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
        rectangular = count/(w*h) > .55
        for corner in corners:
            with mask.crop(corner) as crop:
                rectangular &= ImageStat.Stat(crop).mean[0] > 80
        if not rectangular:
            uncertain = True
            continue
        sx, sy = image.width/width, image.height/height
        boxes.append([round(left*sx), round(top*sy), round((right+1)*sx), round((bottom+1)*sy)])
    mask.close()
    # If a substantial part is not rectangular, keep the slide intact.
    if uncertain:
        return []
    rows = []
    for box in sorted(boxes, key=lambda b: (b[1], b[0])):
        if not rows or box[1]-rows[-1][0][1] > image.height*.015:
            rows.append([])
        rows[-1].append(box)
    return [box for row in rows for box in sorted(row, key=lambda b: b[0])]


def analyse_scans(source, pages, resource_paths, progress=None):
    candidates = [page for page in pages if not page['lines'] and len(page['images']) == 1
                  and (page['images'][0]['x1']-page['images'][0]['x0'])*
                      (page['images'][0]['bottom']-page['images'][0]['top']) > page['width']*page['height']*.80]
    if not candidates:
        return
    with ExitStack() as stack:
        reader = stack.enter_context(PdfReader(source))
        engine = None
        startup_error = None
        for page in candidates:
            if progress:
                progress({'stage': 'ocr', 'current': page['number'], 'total': len(pages), 'found': 0})
            original = page['images'][0]
            page['scan'] = {'page': page['number'], 'panels': 0, 'text_source': 'windows_ocr'}
            try:
                if startup_error:
                    raise RuntimeError(startup_error)
                if engine is None:
                    try:
                        engine = stack.enter_context(WindowsOcr())
                    except Exception as error:
                        startup_error = str(error)
                        raise
                pdf_page = reader.pages[page['number']-1]
                path = resource_paths(pdf_page).get(original['object_id'])
                if path is None:
                    raise ValueError('Для скана не найден отдельный ресурс.')
                with pdf_page.images[path].image as image:
                    a, b, c, d = original.get('matrix', (1, 0, 0, 1))
                    if b or c or a < 0 or d < 0:
                        raise ValueError('Поворот или отражение скана: OCR и разрезание отключены, исходная ориентация сохранена.')
                    ratio = (original['x1']-original['x0'])/(original['bottom']-original['top'])
                    if abs(image.width/image.height/ratio-1) > .025:
                        raise ValueError('Поворот или деформация скана: разрезание отключено.')
                    try:
                        page['lines'] = engine.recognise(image, original)
                    except Exception as error:
                        # A late response after a timeout must never name another page.
                        startup_error = 'Распознавание остановлено после ошибки OCR: '+str(error)
                        raise
                    page['scan']['lines'] = len(page['lines'])
                    if not page['lines']:
                        continue
                    panels = photo_panels(image)
                    if not panels:
                        page['scan']['kept_whole'] = True
                        continue
                    sx = (original['x1']-original['x0'])/image.width
                    sy = (original['bottom']-original['top'])/image.height
                    page['images'] = [dict(original, x0=original['x0']+b[0]*sx,
                                           top=original['top']+b[1]*sy, x1=original['x0']+b[2]*sx,
                                           bottom=original['top']+b[3]*sy, scan_crop_px=b, scan_order=index,
                                           source_bbox=[original[k] for k in ('x0', 'top', 'x1', 'bottom')])
                                      for index, b in enumerate(panels)]
                    page['scan']['panels'] = len(panels)
            except Exception as error:
                page['scan'].update(error=str(error), kept_whole=True)
                page['scan_warning'] = {'page': page['number'], 'reason': 'OCR: '+str(error),
                                        'fallback': 'Исходная картинка страницы сохранена целиком.'}


def column_caption(page, region, match):
    """A confirmed column header also names panels below the first row."""
    if not page.get('scan', {}).get('panels') or match['status'] == 'review' or variant_numbers(match.get('title')):
        return match
    headers = [line for line in page['captions'] if line['top'] < page['height']*.25
               and len(variant_numbers(line['text'])) == 1]
    if len(set().union(*(variant_numbers(line['text']) for line in headers))) < 2:
        return match
    candidates = []
    for header in headers:
        supports = [box for box in page['regions']
                    if 0 <= box['top']-header['bottom'] < page['height']*.08
                    and abs(box['x0']-header['x0']) < page['width']*.04]
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
    if not page.get('scan') or page['scan'].get('panels') or page['scan'].get('error'):
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
