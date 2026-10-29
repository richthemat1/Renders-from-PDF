"""Manual corrections of a finished export: copy, remove, move and reorder PDF images."""
from collections import Counter
import copy
from datetime import datetime
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError

from PIL import Image

from photo_export import (COLLAGES_DIR, CUTOUTS_DIR, OTHER_DIR, PDFIUM_LOCK, SERVICE_DIR, VARIANTS_DIR, VERSION,
                          entry_group, group_file, group_title, has_service_dir, image_to_pdf, safe_name,
                          write_gallery)
from photo_layout import variant_key, variant_sort_key

BACKUPS = '_Резервные копии'


class EditError(ValueError):
    pass


class EditConflict(EditError):
    pass


def load_report(output):
    path = Path(output)/'Отчёт.json'
    if not path.is_file():
        raise EditError('В папке нет «Отчёт.json» — это не результат обработки.')
    report = json.loads(path.read_text(encoding='utf-8'))
    report.setdefault('revision', 0)
    return report


def backup_root(report):
    return Path(SERVICE_DIR)/'Резервные копии' if has_service_dir(report) else Path(BACKUPS)


def _backup_io_path(path):
    """Allow long Windows backup paths without putting device prefixes in reports."""
    path = Path(path)
    if os.name != 'nt':
        return path
    value = os.path.abspath(path)
    if value.startswith('\\\\?\\'):
        return Path(value)
    if value.startswith('\\\\'):
        value = 'UNC\\' + value[2:]
    return Path('\\\\?\\' + value)


def variant_members(report):
    members = {}
    for index, entry in enumerate(report['images']):
        key = entry_group(entry)
        if key is not None and not entry.get('removed'):
            members.setdefault(key, []).append(index)
    for ids in members.values():
        ids.sort(key=lambda index: report['images'][index]['pdf_page'])
    return members


def parse_key(report, key):
    if not re.fullmatch(r'\s*\d+(?:[.,]\d+)*\s*', str(key), re.A):
        raise ValueError(key)
    try:
        return variant_key(key)
    except ValueError:
        raise EditError('Номер варианта должен быть от 1 до 999, подварианта — например 3.1.') from None


def parse_payload(report, payload, pieces=0):
    if any(entry.get('group') == 'perspective' for entry in report['images']):
        raise EditError('Этот результат создан тестовой версией 3.12 с общей папкой «Перспективные изображения». '
                        'Обработайте исходный PDF заново, чтобы править варианты.')
    if payload.get('revision') != report['revision']:
        raise EditConflict('Результат уже изменён в другом окне. Обновите страницу и повторите правку.')
    images = report['images']
    try:
        def indices(values):
            if not isinstance(values, list) or any(type(value) not in (int, str) for value in values):
                raise TypeError()
            return [int(index) for index in values]
        variants = {parse_key(report, key): indices(ids) for key, ids in payload['variants'].items()}
        if len(variants) != len(payload['variants']):
            raise EditError('Один вариант указан в правке несколько раз.')
        removed = indices(payload.get('removed', []))
        misc = indices(payload.get('misc', []))
        restored = indices(payload.get('restored', []))
        copies = payload.get('copies', [])
        if not isinstance(copies, list) or any(type(index) is not int for index in copies):
            raise TypeError()
    except EditError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError):
        raise EditError('Некорректный формат правки.') from None
    seen = removed+misc+[index for ids in variants.values() for index in ids]
    if len(seen) != len(set(seen)):
        raise EditError('Одно изображение указано в правке несколько раз.')
    for index in copies:
        if not 0 <= index < len(images):
            raise EditError(f'Изображение {index} недоступно для копирования.')
    # Copies come first, then the frames of cut collages; every new entry needs a place.
    copied_ids = set(range(len(images), len(images)+len(copies)+pieces))
    if copied_ids & set(removed+restored):
        raise EditError('Новую копию нужно поместить в вариант или в папку подписи.')
    for index in seen:
        if (not 0 <= index < len(images)+len(copies)+pieces or
                (index < len(images) and images[index].get('removed') and index not in restored)):
            raise EditError(f'Изображение {index} не найдено в результате.')
    if not copied_ids <= set(seen):
        raise EditError('В правке пропущено место для новой копии изображения.')
    if len(restored) != len(set(restored)):
        raise EditError('Одно изображение указано в восстановлении несколько раз.')
    for index in restored:
        if not 0 <= index < len(images) or not images[index].get('removed') or index not in seen or index in removed:
            raise EditError(f'Изображение {index} недоступно для восстановления.')
    current = variant_members(report)
    lost = [index for ids in current.values() for index in ids if index not in set(seen)]
    if lost:
        raise EditError('В правке пропущены изображения из вариантов. Обновите страницу и повторите правку.')
    for key in current:
        variants.setdefault(key, [])
    return variants, removed, current


def _safe_path(output, relative, *, must_exist=False, mutation=False):
    """Resolve only ordinary relative paths inside this registered result, including symlinks."""
    output = Path(output).resolve()
    relative = Path(relative)
    if relative.is_absolute() or not relative.parts or any(part in ('..', '.') or ':' in part for part in relative.parts):
        raise EditError('Некорректный путь файла в результате.')
    logical = output / relative
    path = logical.resolve()
    if not path.is_relative_to(output) or path == output:
        raise EditError('Файл находится за пределами папки результата.')
    if mutation and os.path.normcase(str(logical)) != os.path.normcase(str(path)):
        raise EditError('Сохранение через ссылку на другую папку или PDF недоступно. Используйте обычные файлы.')
    if must_exist and not _backup_io_path(path).is_file():
        raise EditError('PDF изображения недоступен. Проверьте папку результата и резервные копии.')
    return path


def _entry_reference(output, report, index):
    try:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(report['images']):
            raise ValueError()
        entry = report['images'][index]
        page = int(entry['pdf_page']) - 1
        if isinstance(entry['pdf_page'], bool) or page < 0:
            raise ValueError()
        path = _safe_path(output, entry['file'], must_exist=True)
        if path.suffix.lower() != '.pdf':
            raise ValueError()
    except (TypeError, KeyError, ValueError) as error:
        if isinstance(error, EditError):
            raise
        raise EditError('Некорректная ссылка на страницу PDF изображения.') from None
    return _backup_io_path(path), page


def entry_pdf_source(output, report, index):
    """Return a contained PDF and zero-based page, also for saved removed entries."""
    path, page = _entry_reference(output, report, index)
    try:
        with PdfReader(path) as reader:
            if page >= len(reader.pages):
                raise EditError('В PDF больше нет нужной страницы изображения.')
    except (OSError, ValueError, PyPdfError) as error:
        if isinstance(error, EditError):
            raise
        raise EditError(f'Не удалось прочитать PDF изображения: {error}') from None
    return path, page


def variant_pdf(output, report, ids, key, frames=None):
    """Join entry pages into one PDF; `frames` holds the in-memory pages of new collage frames."""
    writer = PdfWriter()
    readers = {}
    try:
        title = group_title(key, report) if key is not None else (
            report['images'][ids[0]].get('title') or original_location(report['images'][ids[0]])['folder'])
        writer.add_metadata({'/Title': title, '/Creator': f'PDF Photos {VERSION}'})
        for position, index in enumerate(ids):
            entry = report['images'][index]
            if frames and index in frames:
                path, page = ('frame', index), 0
                if path not in readers:
                    readers[path] = PdfReader(BytesIO(frames[index]))
            else:
                path, page = _entry_reference(output, report, index)
            if path not in readers:
                readers[path] = PdfReader(path)
            if page >= len(readers[path].pages):
                raise EditError('В PDF больше нет нужной страницы изображения.')
            writer.add_page(readers[path].pages[page])
            writer.add_outline_item(f'Страница {entry["page"]} — {entry["title"] or entry["folder"]}', position)
        buffer = BytesIO()
        writer.write(buffer)
        return buffer.getvalue()
    except (OSError, PyPdfError) as error:
        raise EditError(f'Не удалось прочитать PDF изображения: {error}') from None
    finally:
        for reader in readers.values():
            reader.close()
        writer.close()


def unused_backup(output, report):
    base = Path(output)/backup_root(report)/datetime.now().strftime('%Y-%m-%d %H-%M-%S')
    for number in range(1, 1000):
        candidate = base if number == 1 else base.with_name(f'{base.name} ({number})')
        if not _backup_io_path(candidate).exists():
            return candidate
    raise EditError('Не удалось подобрать имя папки резервной копии.')


def summarize(report):
    entries = [entry for entry in report['images'] if not entry.get('removed')]
    report['counts_by_folder'] = dict(sorted(Counter(entry['folder'] for entry in entries).items()))
    report['counts_by_status'] = dict(Counter(entry['status'] for entry in entries))
    documents = {}
    for entry in entries:
        document = documents.setdefault(entry['file'], {
            'file': entry['file'], 'folder': entry['folder'], 'variant': entry['variant'],
            'group': entry.get('group'), 'pages': 0, 'sha256': entry['sha256']})
        document['pages'] += 1
    report['documents'] = list(documents.values())


def original_location(entry):
    """Keep the caption classification before the first manual move, including old reports."""
    location = entry.get('original_location')
    if isinstance(location, dict) and location.get('folder') and location.get('status') in ('named', 'review', 'unsigned'):
        return copy.deepcopy(location)
    folder = entry.get('auto_folder') or entry.get('folder') or entry.get('title') or 'Без подписи'
    if entry.get('auto_folder'):
        status = 'unsigned' if folder == 'Без подписи' else 'review' if folder == 'На проверку' else 'named'
        location = {'folder': folder, 'status': status}
    else:
        location = {'folder': folder, 'status': entry.get('status', 'named')}
        if 'reason' in entry:
            location['reason'] = entry['reason']
        if entry_group(entry) is None and not entry.get('removed'):
            location['file'] = entry['file']
    return location


def _report_with_copies(report, sources):
    """Append independent entries while their file/page references still point to saved sources."""
    working = copy.deepcopy(report)
    labels = report.get('folder_labels') or {}
    for index in sources:
        source = report['images'][index]
        entry = copy.deepcopy(source)
        location = original_location(source)
        location.setdefault('label', labels.get(location['folder'], location['folder']))
        entry.update(variant=None, group=None, copy_of=index, manual=True,
                     original_location=location, reason='Создана копия изображения вручную.')
        # A copy is a picture of its own: cutting or returning the collage again must not take it away.
        for key in ('removed', 'suppressed', 'collage', 'collage_replaced'):
            entry.pop(key, None)
        working['images'].append(entry)
    return working


def _misc_target(output, report, index, reserved, folder_labels=None):
    entry = report['images'][index]
    location = original_location(entry)
    labels = folder_labels if folder_labels is not None else dict(report.get('folder_labels') or {})
    label = labels.get(location['folder'], location.get('label', location['folder']))
    # A shared variant PDF must never become the destination of a single returned page.
    base = Path(OTHER_DIR) if has_service_dir(report) else Path()
    prefix = f'Страница {int(entry["page"]):03d} — {int(entry.get("sequence", 1)):03d}'
    limit = min(80, PATH_LIMIT - len(str(Path(output)/base)) - len(prefix) - 6)
    if limit < 1:
        raise EditError('Путь результата слишком длинный. Переместите папку результата ближе к корню диска.')
    for number in range(1, 10000):
        suffix = '' if number == 1 else f' ({number})'
        if len(suffix) >= limit:
            raise EditError('Путь результата слишком длинный для папки подписи.')
        folder = safe_name(location['folder'], limit-len(suffix))+suffix
        if folder not in labels or labels[folder] == label:
            break
    else:
        raise EditError('Не удалось подобрать имя папки подписи.')
    labels[folder] = label
    directory = base/folder
    location.update(folder=folder, label=label)
    original_file = location.get('file')
    if original_file:
        original = Path(original_file)
        path = _safe_path(output, original)
        if (original.parent == directory and len(str(path)) <= PATH_LIMIT and
                original.suffix.lower() == '.pdf' and not _backup_io_path(path).exists() and
                str(original).casefold() not in reserved):
            reserved.add(str(original).casefold())
            return str(original), location
    stem = prefix+(' — '+safe_name(entry['title']) if entry.get('title') else '')
    for number in range(1, 10000):
        suffix = '' if number == 1 else f' ({number})'
        available = PATH_LIMIT-len(str(Path(output)/directory))-len(suffix)-len('.pdf')-1
        if available < len(prefix):
            raise EditError('Путь результата слишком длинный для отдельного PDF изображения.')
        relative = directory/(stem[:available].rstrip(' .')+suffix+'.pdf')
        path = _safe_path(output, relative)
        if str(relative).casefold() not in reserved and not _backup_io_path(path).exists():
            reserved.add(str(relative).casefold())
            return str(relative), location
    raise EditError('Не удалось подобрать свободное имя PDF изображения.')


def updated_report(report, variants, removed, current, built, changed, backup_name,
                   *, misc=None, restored=None, singles=None, copies=None, copy_start=None):
    new = copy.deepcopy(report)
    images = new['images']
    moved, reordered = [], []
    for key in changed:
        target = str(group_file(report, key))
        checksum = hashlib.sha256(built[key]).hexdigest() if key in built else None
        kept = [index for index in current.get(key, []) if index in variants[key]]
        if kept != [index for index in variants[key] if index in kept]:
            reordered.append(key)
        for position, index in enumerate(variants[key], 1):
            entry = images[index]
            entry.setdefault('original_location', original_location(entry))
            if entry.get('removed'):
                entry.update(status='named', manual=True,
                             reason=f'Восстановлено вручную в «{group_title(key, report)}».')
            if entry_group(entry) != key:
                moved.append({'image': index, 'from': entry['folder'], 'to': group_title(key, report)})
                entry.setdefault('auto_folder', entry['folder'])
                entry.update(status='named', manual=True, reason=f'Перенесено вручную в «{group_title(key, report)}».')
            entry.update(folder=group_title(key, report), variant=key, group='variant',
                         file=target, pdf_page=position, sha256=checksum)
            entry.pop('removed', None)
            entry.pop('suppressed', None)
    for key in changed:
        recoverable = any(entry_group(entry) == key and (entry.get('removed') or index in removed)
                          for index, entry in enumerate(images))
        if not variants[key] and not recoverable:
            new.get('variant_names', {}).pop(str(key), None)
    for index in removed:
        entry = images[index]
        entry.setdefault('original_location', original_location(entry))
        entry.setdefault('auto_folder', entry['folder'])
        entry.update(removed=True, manual=True, file=str(Path(backup_name)/entry['file']),
                     reason='Удалено вручную; копия сохранена в резервной папке.')
    for index in misc or []:
        entry = images[index]
        target, location, data = singles[index]
        entry.setdefault('original_location', original_location(entry))
        if entry_group(entry) is not None:
            moved.append({'image': index, 'from': entry['folder'], 'to': location['folder']})
        entry.update(folder=location['folder'], status=location['status'], variant=None, group=None,
                     file=target, pdf_page=1, sha256=hashlib.sha256(data).hexdigest(), manual=True)
        if location.get('label'):
            new.setdefault('folder_labels', {})[location['folder']] = location['label']
        entry.pop('removed', None)
        entry.pop('suppressed', None)
        if 'reason' in location:
            entry['reason'] = location['reason']
        else:
            entry.pop('reason', None)
    copied = []
    start = len(images)-len(copies or []) if copy_start is None else copy_start
    for position, source in enumerate(copies or [], start):
        images[position].update(manual=True, reason='Создана копия изображения вручную.')
        copied.append({'image': position, 'source': source})
    summarize(new)
    new['revision'] += 1
    new.setdefault('edits', []).append({
        'time': datetime.now().isoformat(timespec='seconds'), 'version': VERSION, 'backup': backup_name,
        'removed': [{'image': index, 'from': images[index]['auto_folder']} for index in removed],
        'moved': moved, 'reordered': reordered,
        **({'copies': copied} if copied else {}),
        **({'restored': list(restored)} if restored else {})})
    return new


def write_report(output, report):
    path = Path(output)/'Отчёт.json'
    _write_bytes(path, json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))


def _write_bytes(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=_backup_io_path(path.parent), prefix='.pdf-photos-',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, _backup_io_path(path))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def remove_empty_folders(output, folder):
    while folder != output and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def _file_hash(path):
    with _backup_io_path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _report_hash(report):
    value = copy.deepcopy(report)
    edits = value.get('edits') or []
    if edits and isinstance(edits[-1].get('transaction'), dict):
        edits[-1]['transaction'].pop('report_hash', None)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def _commit_transaction(output, report, new, built, retired, backup, backup_only=()):
    """Back up all touched live files, then commit or restore the complete prior state."""
    backup_name = str(backup.relative_to(output))
    _safe_path(output, backup_name, mutation=True)
    if _report_hash(load_report(output)) != _report_hash(report):
        raise EditConflict('Результат изменён во время сохранения. Обновите страницу и повторите правку.')
    backup_io = _backup_io_path(backup)
    touched = sorted(set(built) | set(retired) | set(backup_only) | {'Отчёт.json', 'Просмотр.html'})
    paths = {relative: _safe_path(output, relative, mutation=True) for relative in touched}
    existed = [relative for relative, path in paths.items() if _backup_io_path(path).is_file()]
    created = [relative for relative in touched if relative not in existed]
    live = {os.path.normcase(str(Path(entry['file']))) for entry in report['images'] if not entry.get('removed')}
    if any(os.path.normcase(str(Path(relative))) not in live and _backup_io_path(paths[relative]).exists()
           for relative in built):
        raise EditError('На месте нового PDF уже есть другой файл. Переместите его и повторите сохранение.')
    if any(_backup_io_path(path).exists() and not _backup_io_path(path).is_file() for path in paths.values()):
        raise EditError('На месте PDF находится папка. Переместите её и повторите сохранение.')
    new['edits'][-1]['transaction'] = {
        'saved': existed, 'created': created,
        'written': {relative: hashlib.sha256(data).hexdigest() for relative, data in built.items()},
        'deleted': sorted(retired),
        'saved_hashes': {},
    }
    transaction = new['edits'][-1]['transaction']
    for relative in set(existed)-set(retired)-set(built)-{'Отчёт.json', 'Просмотр.html'}:
        transaction['written'][relative] = _file_hash(paths[relative])
    saved = []
    committing = False
    try:
        for relative in existed:
            destination = backup_io/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_backup_io_path(paths[relative]), destination)
            saved.append(relative)
            transaction['saved_hashes'][relative] = _file_hash(destination)
        if (_report_hash(load_report(output)) != _report_hash(report) or
                any(_file_hash(paths[relative]) != transaction['saved_hashes'][relative] for relative in saved)):
            raise EditConflict('Результат изменён во время сохранения. Обновите страницу и повторите правку.')
        for relative in touched:
            _safe_path(output, relative, mutation=True)
        if any(_backup_io_path(paths[relative]).exists() for relative in created):
            raise EditConflict('На месте нового файла появился другой файл. Обновите результат и повторите сохранение.')
        transaction['report_hash'] = _report_hash(new)
        committing = True
        for relative, data in built.items():
            target = paths[relative]
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_bytes(target, data)
        for relative in retired:
            _backup_io_path(paths[relative]).unlink(missing_ok=True)
        write_gallery(output, new)
        write_report(output, new)
    except BaseException:
        if committing:
            for relative in saved:
                paths[relative].parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_io/relative, _backup_io_path(paths[relative]))
            for relative in created:
                _backup_io_path(paths[relative]).unlink(missing_ok=True)
                remove_empty_folders(output, paths[relative].parent)
        raise
    for relative in touched:
        remove_empty_folders(output, paths[relative].parent)
    return new, backup_name


FRAME_MIN = 16
FRAME_LIMIT = 64


def parse_collage_edits(report, payload):
    """Cuts as (record, boxes, old members, created); plus the ids confirmed or dismissed as one photo."""
    images = report['images']
    records = {record['id']: record for record in report.get('collages') or [] if isinstance(record, dict)}
    try:
        cuts = payload.get('cuts', [])
        checked = payload.get('collage_checked', [])
        dismissed = payload.get('collage_dismissed', [])
        if not all(isinstance(value, list) for value in (cuts, checked, dismissed)):
            raise TypeError()
        parsed, used = [], set()
        for cut in cuts:
            if not isinstance(cut, dict) or not isinstance(cut.get('boxes'), list):
                raise TypeError()
            if 'collage' in cut:
                record, created = records.get(cut['collage']), False
                if record is None or record.get('state') == 'dismissed':
                    raise EditError('Склейка не найдена в результате. Обновите страницу и повторите правку.')
            else:
                index = cut.get('source')
                if type(index) is not int or not 0 <= index < len(images) or images[index].get('removed'):
                    raise EditError('Изображение для разрезания не найдено. Обновите страницу и повторите правку.')
                if images[index].get('collage') in records:
                    record, created = records[images[index]['collage']], False
                else:
                    entry = images[index]
                    record = {'id': f'{entry["page"]}-m{index}', 'page': entry['page'], 'image': index,
                              'size': list(entry['size']), 'title': entry.get('title')}
                    created = True
            if record['id'] in used:
                raise EditError('Одна склейка указана в правке несколько раз.')
            used.add(record['id'])
            width, height = record['size']
            boxes = []
            for box in cut['boxes']:
                if (not isinstance(box, list) or len(box) != 4 or any(type(value) is not int for value in box)
                        or not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height
                        or box[2]-box[0] < FRAME_MIN or box[3]-box[1] < FRAME_MIN):
                    raise EditError('Кадр выходит за край склейки или слишком мал. Проверьте линии разреза.')
                boxes.append(box)
            if not 1 <= len(boxes) <= FRAME_LIMIT:
                raise EditError(f'В склейке должен остаться от 1 до {FRAME_LIMIT} кадров.')
            if not isinstance(cut.get('trim', False), bool):
                raise TypeError()
            # Copies saved by the first 3.34 build still carry the collage id; they are not its frames.
            members = ([record['image']] if created else
                       [index for index, entry in enumerate(images)
                        if entry.get('collage') == record['id'] and not entry.get('removed') and 'copy_of' not in entry])
            parsed.append((record, boxes, members, created, cut.get('trim', False)))
        for value in checked+dismissed:
            if value not in records:
                raise EditError('Склейка не найдена в результате. Обновите страницу и повторите правку.')
        for value in dismissed:
            if records[value].get('state') != 'suspected' or value in used:
                raise EditError('Отметить «Это одно фото» можно только неразрезанную картинку.')
    except EditError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError):
        raise EditError('Некорректный формат правки склеек.') from None
    return parsed, list(dict.fromkeys(checked)), list(dict.fromkeys(dismissed))


def _collage_source(output, report, record):
    """The whole collage picture as a PIL image, its PDF page size in points and that page's bytes."""
    if record.get('file'):
        path, page = _backup_io_path(_safe_path(output, record['file'], must_exist=True)), 0
    else:
        path, page = entry_pdf_source(output, report, record['image'])
    entry = report['images'][record['image']] if 'image' in record else None
    for component in (entry or {}).get('components') or []:
        a, b, c, d = (component.get('matrix') or (1, 0, 0, 1))[:4]
        if b or c or a < 0 or d < 0:
            raise EditError('Повёрнутую или отражённую картинку разрезать нельзя.')
    try:
        with PdfReader(path) as reader:
            pdf_page = reader.pages[page]
            pictures = pdf_page.images
            if len(pictures) != 1:
                raise EditError('В PDF склейки должна быть ровно одна картинка.')
            image = pictures[0].image.copy()
            size = float(pdf_page.mediabox.width), float(pdf_page.mediabox.height)
            writer = PdfWriter()
            writer.add_page(pdf_page)
            buffer = BytesIO()
            writer.write(buffer)
            writer.close()
    except (OSError, ValueError, PyPdfError) as error:
        if isinstance(error, EditError):
            raise
        raise EditError(f'Не удалось прочитать картинку склейки: {error}') from None
    return image, size, buffer.getvalue()


def _cut_frames(output, report, record, boxes, trim=False):
    """Single-page PDFs and previews of the frames, cut from the native pixels of the collage.

    With `trim`, light gutter margins left at a frame edge are cut off, so a line may run through
    the middle of a white band between renders."""
    from photo_scan import trim_margins
    image, (page_width, page_height), page_bytes = _collage_source(output, report, record)
    width, height = record['size']
    sx, sy = image.width/width, image.height/height
    if trim:
        trimmed = []
        for box in boxes:
            pixels = [round(box[0]*sx), round(box[1]*sy), round(box[2]*sx), round(box[3]*sy)]
            left, top, right, bottom = trim_margins(image, pixels)
            box = [round(left/sx), round(top/sy), round(right/sx), round(bottom/sy)]
            if box[2]-box[0] < FRAME_MIN or box[3]-box[1] < FRAME_MIN:
                raise EditError('После обрезки светлых полей кадр стал пустым. Передвиньте линии разреза.')
            trimmed.append(box)
        boxes[:] = trimmed
    if image.mode in ('CMYK', 'I;16'):
        converted = image.convert('RGB')
        image.close()
        image = converted
    frames = []
    try:
        with tempfile.TemporaryDirectory(prefix='pdf-photos-cut-') as folder:
            for number, box in enumerate(boxes, 1):
                pixels = [round(box[0]*sx), round(box[1]*sy), round(box[2]*sx), round(box[3]*sy)]
                raster, target = Path(folder)/f'{number}.png', Path(folder)/f'{number}.pdf'
                with image.crop(pixels) as crop:
                    crop.save(raster, 'PNG')
                    size = list(crop.size)
                    with crop.copy() as thumbnail:
                        thumbnail.thumbnail((640, 480), Image.Resampling.LANCZOS)
                        preview = BytesIO()
                        thumbnail.save(preview, 'PNG')
                image_to_pdf(raster, target, [0, 0, page_width*(box[2]-box[0])/width,
                                              page_height*(box[3]-box[1])/height], record.get('title'))
                frames.append({'pdf': target.read_bytes(), 'size': size, 'preview': preview.getvalue()})
    finally:
        image.close()
    return frames, page_bytes


def _report_with_frames(report, cuts, start, output, aux):
    """Append frame entries after the copies; return their in-memory pages and the cut summary."""
    images = report['images']
    frames, summary = {}, []
    revision = report.get('revision', 0)+1
    service = Path(SERVICE_DIR)
    for record, boxes, members, created, trim in cuts:
        cut, page_bytes = _cut_frames(output, report, record, boxes, trim)
        template = images[members[0]] if members else images[record['image']]
        identifier = record['id']
        source = record.get('file')
        if not source:
            source = str(service/COLLAGES_DIR/f'Склейка {identifier} r{revision}.pdf')
            aux[source] = page_bytes
        pieces = []
        whole = len(boxes) == 1 and boxes[0] == [0, 0, *record['size']]
        for number, (box, frame) in enumerate(zip(boxes, cut), 1):
            index = start+len(frames)
            preview = str(service/'Миниатюры'/f'кадр {identifier} r{revision} {number}.png')
            aux[preview] = frame['preview']
            entry = copy.deepcopy(template)
            for key in ('removed', 'suppressed', 'copy_of', 'collage_replaced', 'sha256'):
                entry.pop(key, None)
            entry.setdefault('original_location', original_location(template))
            entry.update(collage=identifier, crop_px=box, size=frame['size'], method='manual_crop', manual=True,
                         cut='manual', preview=preview, pdf_page=1,
                         reason=('Склейка возвращена целиком вручную.' if whole else
                                 f'Кадр {number} из склейки на странице {record["page"]}, вырезан вручную.'))
            entry['components'] = [dict(component, crop_px=box) for component in (template.get('components') or [])[:1]]
            images.append(entry)
            frames[index] = frame['pdf']
            pieces.append(index)
        summary.append({'collage': identifier, 'pieces': pieces, 'replaced': members, 'source': source,
                        'boxes': boxes, 'created': created})
    return frames, summary


def find_seams(output, payload):
    """Suggested frame boxes for the cut editor: gutters between renders, else an even split."""
    from photo_scan import equal_split_hint, gutter_panels
    output = Path(output).resolve()
    report = load_report(output)
    records = {record['id']: record for record in report.get('collages') or []}
    if 'collage' in payload and payload['collage'] in records:
        record = records[payload['collage']]
    elif type(payload.get('source')) is int and 0 <= payload['source'] < len(report['images']):
        entry = report['images'][payload['source']]
        record = {'id': 'seams', 'image': payload['source'], 'size': list(entry['size'])}
    else:
        raise EditError('Картинка для поиска швов не найдена.')
    image, _, _ = _collage_source(output, report, record)
    try:
        width, height = record['size']
        sx, sy = width/image.width, height/image.height
        found = gutter_panels(image)
        kind = 'gutters' if found else 'even'
        boxes = list(found) or equal_split_hint(image)
    finally:
        image.close()
    return {'kind': kind if boxes else 'none',
            'boxes': [[round(b[0]*sx), round(b[1]*sy), round(b[2]*sx), round(b[3]*sy)] for b in boxes]}


SNAPSHOT_DPI = 300
SNAPSHOT_PIXELS = 36_000_000
# A frame may stray this far (a share of the page) past the picture and still be cut from its pixels.
AREA_TOLERANCE = .006
_SOURCE_CHECKED = {}
_GEOMETRY = {}


def source_pdf(report):
    """The presentation the result was made from, checked against the SHA-256 the report keeps."""
    path = Path(report['source']).resolve()
    if path.suffix.lower() != '.pdf' or not path.is_file():
        raise EditError('Исходный PDF недоступен. Он мог быть перемещён или удалён. '
                        'Готовые PDF остаются в папке результата.')
    expected = report.get('source_sha256')
    if expected:
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if _SOURCE_CHECKED.get(key) != expected:
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise EditError('Исходный PDF изменился после обработки. '
                                    'Для проверки нужна прежняя копия исходного файла.')
            _SOURCE_CHECKED[key] = expected
    return path


def page_geometry(report):
    """Crop box and rotation of every source page, in the pdfplumber coordinates of the report boxes."""
    path = source_pdf(report)
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _GEOMETRY:
        import pdfplumber
        with pdfplumber.open(path) as document:
            pages = [{'box': [float(value) for value in page.cropbox], 'rotation': page.rotation}
                     for page in document.pages]
        if len(pages) != report['pages']:
            raise EditError('Исходный PDF не совпадает с результатом: другое число страниц.')
        _GEOMETRY.clear()
        _GEOMETRY[key] = pages
    return _GEOMETRY[key]


def snapshot_scale(page):
    """Pixels per point of a page snapshot: 300 dpi, less on a poster so the page stays within budget."""
    x0, top, x1, bottom = page['box']
    return min(SNAPSHOT_DPI/72, (SNAPSHOT_PIXELS/max((x1-x0)*(bottom-top), 1))**.5)


def picture_area(entry, page):
    """The share of the page an entry covers: left, top, right, bottom from 0 to 1."""
    x0, top, x1, bottom = page['box']
    width, height = x1-x0, bottom-top
    box = entry['bbox']
    return [round(min(1, max(0, value)), 6) for value in
            ((box[0]-x0)/width, (box[1]-top)/height, (box[2]-x0)/width, (box[3]-top)/height)]


def cutout_sources(report, geometry=None):
    """Pictures a frame drawn on the page is cut from in their own pixels; anything else is a page snapshot."""
    geometry = page_geometry(report) if geometry is None else geometry
    pictures = []
    for index, entry in enumerate(report['images']):
        page = entry.get('page')
        if (entry.get('removed') or 'copy_of' in entry or entry.get('cutout') or entry.get('method') == 'manual_crop'
                or not isinstance(entry.get('bbox'), list) or len(entry['bbox']) != 4
                or type(page) is not int or not 1 <= page <= len(geometry) or geometry[page-1]['rotation']):
            continue
        if any((matrix := component.get('matrix')) and (matrix[1] or matrix[2] or matrix[0] < 0 or matrix[3] < 0)
               for component in entry.get('components') or []):
            continue
        area = picture_area(entry, geometry[page-1])
        if area[2]-area[0] > 0 and area[3]-area[1] > 0:
            pictures.append({'image': index, 'page': page, 'area': area, 'size': list(entry['size'])})
    return pictures


def pages_info(output):
    """What the cutout editor draws on: page sizes, snapshot sizes and the pictures that may be cut."""
    report = load_report(Path(output).resolve())
    geometry = page_geometry(report)
    pages = []
    for number, page in enumerate(geometry, 1):
        x0, top, x1, bottom = page['box']
        scale = snapshot_scale(page)
        pages.append({'number': number, 'size': [round(x1-x0, 3), round(bottom-top, 3)],
                      'snapshot': [round((x1-x0)*scale), round((bottom-top)*scale)]})
    return {'pages': pages, 'pictures': cutout_sources(report, geometry)}


def _rect_box(record, rect):
    """A frame from shares of the page to pixels of the record's source: its picture or the page snapshot."""
    if (not isinstance(rect, list) or len(rect) != 4 or any(type(value) not in (int, float) for value in rect)
            or not 0 <= rect[0] < rect[2] <= 1 or not 0 <= rect[1] < rect[3] <= 1):
        raise EditError('Рамка вырезки выходит за край страницы. Проверьте вырезки.')
    left, top, right, bottom = record['area']
    if (rect[0] < left-AREA_TOLERANCE or rect[1] < top-AREA_TOLERANCE or
            rect[2] > right+AREA_TOLERANCE or rect[3] > bottom+AREA_TOLERANCE):
        raise EditError('Рамка вырезки выходит за край картинки. Обновите страницу и повторите правку.')
    width, height = record['size']
    box = [round((min(max(rect[0], left), right)-left)/(right-left)*width),
           round((min(max(rect[1], top), bottom)-top)/(bottom-top)*height),
           round((min(max(rect[2], left), right)-left)/(right-left)*width),
           round((min(max(rect[3], top), bottom)-top)/(bottom-top)*height)]
    if box[2]-box[0] < FRAME_MIN or box[3]-box[1] < FRAME_MIN:
        raise EditError(f'Вырезка меньше {FRAME_MIN} px. Увеличьте рамку или удалите её.')
    return box


def parse_cutout_edits(report, payload):
    """Cutouts as records with their new boxes, live earlier pieces and whether the record is new."""
    images = report['images']
    raw = payload.get('cutouts', [])
    if not raw:
        return []
    records = {record['id']: record for record in report.get('cutouts') or [] if isinstance(record, dict)}
    by_image = {record['image']: record for record in records.values() if record.get('kind') == 'image'}
    geometry = None
    try:
        if not isinstance(raw, list):
            raise TypeError()
        parsed, used = [], set()
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get('rects'), list):
                raise TypeError()
            created = False
            if 'record' in item:
                record = records.get(item['record'])
                if record is None:
                    raise EditError('Вырезка не найдена в результате. Обновите страницу и повторите правку.')
            elif 'source' in item:
                index = item['source']
                if type(index) is not int or not 0 <= index < len(images):
                    raise EditError('Картинка для вырезки не найдена. Обновите страницу и повторите правку.')
                record = by_image.get(index)
                if record is None:
                    geometry = geometry or page_geometry(report)
                    picture = next((value for value in cutout_sources(report, geometry) if value['image'] == index), None)
                    if picture is None:
                        raise EditError('Из этой картинки вырезать нельзя. Обновите страницу и повторите правку.')
                    entry = images[index]
                    record = {'id': f'{entry["page"]}-v{index}', 'page': entry['page'], 'kind': 'image', 'image': index,
                              'area': picture['area'], 'size': list(entry['size']), 'title': entry.get('title')}
                    created = True
            elif 'page' in item:
                number = item['page']
                if type(number) is not int or not 1 <= number <= report['pages']:
                    raise EditError('Страница для вырезки не найдена.')
                record = records.get(f'{number}-p')
                if record is None:
                    geometry = geometry or page_geometry(report)
                    page = geometry[number-1]
                    scale = snapshot_scale(page)
                    x0, top, x1, bottom = page['box']
                    record = {'id': f'{number}-p', 'page': number, 'kind': 'page', 'area': [0, 0, 1, 1],
                              'size': [round((x1-x0)*scale), round((bottom-top)*scale)], 'scale': scale, 'title': None}
                    created = True
            else:
                raise TypeError()
            if record['id'] in used:
                raise EditError('Одна картинка указана в правке вырезок несколько раз.')
            used.add(record['id'])
            boxes = [_rect_box(record, rect) for rect in item['rects']]
            if len(boxes) > FRAME_LIMIT:
                raise EditError(f'Из одной картинки можно сделать не больше {FRAME_LIMIT} вырезок.')
            members = [index for index, entry in enumerate(images)
                       if entry.get('cutout') == record['id'] and not entry.get('removed') and 'copy_of' not in entry]
            parsed.append({'record': record, 'boxes': boxes, 'rects': item['rects'], 'members': members,
                           'created': created})
    except EditError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError):
        raise EditError('Некорректный формат правки вырезок.') from None
    return parsed


def _snapshot_frames(report, record, boxes):
    """Single-page PDFs and previews of page areas rendered from the source presentation."""
    import pypdfium2
    path = source_pdf(report)
    scale = record['scale']
    width, height = record['size']
    frames = []
    with tempfile.TemporaryDirectory(prefix='pdf-photos-cut-') as folder:
        with PDFIUM_LOCK, pypdfium2.PdfDocument(path) as document:
            page = document[record['page']-1]
            try:
                for number, box in enumerate(boxes, 1):
                    raster, target = Path(folder)/f'{number}.png', Path(folder)/f'{number}.pdf'
                    # Shares of the page in points to cut from the left, bottom, right and top.
                    crop = (box[0]/scale, (height-box[3])/scale, (width-box[2])/scale, box[1]/scale)
                    bitmap = page.render(scale=scale, crop=crop)
                    try:
                        with bitmap.to_pil() as image:
                            image.convert('RGB').save(raster, 'PNG')
                            size = list(image.size)
                            with image.copy() as thumbnail:
                                thumbnail.thumbnail((640, 480), Image.Resampling.LANCZOS)
                                preview = BytesIO()
                                thumbnail.convert('RGB').save(preview, 'PNG')
                    finally:
                        bitmap.close()
                    image_to_pdf(raster, target, [0, 0, (box[2]-box[0])/scale, (box[3]-box[1])/scale],
                                 f'Страница {record["page"]}')
                    frames.append({'pdf': target.read_bytes(), 'size': size, 'preview': preview.getvalue()})
            finally:
                page.close()
    return frames


def _report_with_cutouts(report, cutouts, start, output, aux):
    """Append cutout entries after copies and collage frames; return their in-memory pages and a summary."""
    images = report['images']
    frames, summary = {}, []
    revision = report.get('revision', 0)+1
    service = Path(SERVICE_DIR)
    for item in cutouts:
        record, boxes = item['record'], item['boxes']
        identifier = record['id']
        source = record.get('file')
        if record['kind'] == 'image':
            cut, page_bytes = _cut_frames(output, report, record, boxes) if boxes else ([], None)
            template = images[record['image']]
            if boxes and not source:
                source = str(service/CUTOUTS_DIR/f'Вырезка {identifier} r{revision}.pdf')
                aux[source] = page_bytes
        else:
            cut, template = (_snapshot_frames(report, record, boxes) if boxes else []), None
        pieces = []
        for number, (box, frame) in enumerate(zip(boxes, cut), 1):
            index = start+len(frames)
            preview = str(service/'Миниатюры'/f'вырезка {identifier} r{revision} {number}.png')
            aux[preview] = frame['preview']
            if template is not None:
                entry = copy.deepcopy(template)
                for key in ('removed', 'suppressed', 'copy_of', 'collage', 'collage_replaced', 'cutout_replaced',
                            'sha256', 'cut', 'crop_px'):
                    entry.pop(key, None)
                entry.setdefault('original_location', original_location(template))
                entry['components'] = [dict(component, crop_px=box) for component in (template.get('components') or [])[:1]]
                reason = f'Вырезка {number} со страницы {record["page"]}, из исходных пикселей картинки.'
            else:
                reason = f'Вырезка {number} со страницы {record["page"]}, снимок страницы.'
                entry = {'status': 'unsigned', 'title': None, 'candidates': [], 'variant': None, 'group': None,
                         'page': record['page'], 'sequence': 900+number, 'folder': 'Без подписи', 'components': [],
                         'original_location': {'folder': 'Без подписи', 'status': 'unsigned', 'label': 'Без подписи',
                                               'reason': reason}}
            entry.update(cutout=identifier, crop_px=box, size=frame['size'], manual=True, cut='cutout', preview=preview,
                         pdf_page=1, method='manual_crop' if template is not None else 'page_render', reason=reason)
            entry.setdefault('file', '')
            images.append(entry)
            frames[index] = frame['pdf']
            pieces.append(index)
        summary.append({'record': record, 'pieces': pieces, 'replaced': item['members'], 'source': source})
    return frames, summary


def _update_cutouts(new, summary):
    images = new['images']
    records = new.setdefault('cutouts', [])
    by_id = {record['id']: record for record in records}
    for item in summary:
        record = by_id.get(item['record']['id'])
        if record is None:
            record = copy.deepcopy(item['record'])
            records.append(record)
            by_id[record['id']] = record
        if item['source']:
            record['file'] = item['source']
        # Earlier pieces and the picture itself live on in the record, not among removed cards.
        for index in item['replaced']:
            images[index]['cutout_replaced'] = record['id']
        if record['kind'] == 'image' and item['pieces'] and images[record['image']].get('removed'):
            images[record['image']].update(cutout_replaced=record['id'], reason='Заменено вырезками; картинка сохранена.')
    for entry in images:
        if not entry.get('removed'):
            entry.pop('cutout_replaced', None)
    new['edits'][-1]['cutouts'] = [{'cutout': item['record']['id'], 'pieces': item['pieces'], 'replaced': item['replaced']}
                                   for item in summary]


def apply_edits(output, payload):
    """Rebuild variant and individual PDFs transactionally, preserving every replaced source."""
    output = Path(output).resolve()
    report = load_report(output)
    cuts, checked, dismissed = parse_collage_edits(report, payload)
    cutouts = parse_cutout_edits(report, payload)
    frame_count = sum(len(cut[1]) for cut in cuts)
    variants, removed, current = parse_payload(report, payload,
                                               pieces=frame_count+sum(len(item['boxes']) for item in cutouts))
    for record, boxes, members, created, trim in cuts:
        if any(index not in removed for index in members):
            raise EditError('Прежние кадры склейки не убраны из правки. Обновите страницу и повторите разрезание.')
    restored = [int(index) for index in payload.get('restored', [])]
    for item in cutouts:
        record = item['record']
        if any(index not in removed for index in item['members']):
            raise EditError('Прежние вырезки не убраны из правки. Обновите страницу и повторите правку.')
        if record['kind'] != 'image':
            continue
        picture = record['image']
        saved_removed = bool(report['images'][picture].get('removed'))
        # Cutouts take the place of their picture; without cutouts the picture comes back.
        if item['boxes'] and not saved_removed and picture not in removed:
            raise EditError('Картинка, из которой сделаны вырезки, не убрана из правки. Обновите страницу.')
        if not item['boxes'] and (picture in removed or saved_removed and picture not in restored):
            raise EditError('Без вырезок картинка должна вернуться на место. Обновите страницу и повторите правку.')
    copies = payload.get('copies', [])
    copy_start = len(report['images'])
    working = _report_with_copies(report, copies) if copies else report
    aux = {}
    if cuts or cutouts:
        working = copy.deepcopy(working)
    if cuts:
        frames, cut_summary = _report_with_frames(working, cuts, copy_start+len(copies), output, aux)
    else:
        frames, cut_summary = {}, []
    if cutouts:
        pieces, cutout_summary = _report_with_cutouts(working, cutouts, copy_start+len(copies)+frame_count, output, aux)
        frames.update(pieces)
    else:
        cutout_summary = []
    images = working['images']
    misc = [int(index) for index in payload.get('misc', [])
            if int(index) >= copy_start or images[int(index)].get('removed') or entry_group(images[int(index)]) is not None]
    changed = sorted((key for key, ids in variants.items() if ids != current.get(key, [])), key=variant_sort_key)
    to_variant = [index for ids in variants.values() for index in ids]
    # Copies only borrow source references; they must never retire a live or recovery PDF.
    source_singles = [index for index in removed+to_variant
                      if index < copy_start and entry_group(images[index]) is None and not images[index].get('removed')]
    if not changed and not source_singles and not misc and not cuts and not cutouts and not checked and not dismissed:
        return report, None
    # Read every source before creating backups or replacing any live PDF.
    built = {key: variant_pdf(output, working, variants[key], key, frames) for key in changed if variants[key]}
    reserved = {str(group_file(report, key)).casefold() for key in variants}
    singles = {}
    folder_labels = dict(report.get('folder_labels') or {})
    for index in misc:
        target, location = _misc_target(output, working, index, reserved, folder_labels)
        singles[index] = target, location, variant_pdf(output, working, [index], None, frames)
    # A removal is recoverable only if its source and page really exist.
    for index in removed:
        entry_pdf_source(output, report, index)
    backup = unused_backup(output, report)
    backup_name = str(backup.relative_to(output))
    new = updated_report(working, variants, removed, current, built, changed, backup_name,
                         misc=misc, restored=restored, singles=singles, copies=copies, copy_start=copy_start)
    if cuts or checked or dismissed:
        _update_collages(new, cut_summary, checked, dismissed)
    if cutouts:
        _update_cutouts(new, cutout_summary)
    pdfs = {str(group_file(report, key)): data for key, data in built.items()}
    pdfs.update({value[0]: value[2] for value in singles.values()})
    live_files = {os.path.normcase(str(Path(entry['file']))) for entry in report['images'] if not entry.get('removed')}
    for relative in pdfs:
        path = _safe_path(output, relative, mutation=True)
        if len(str(path)) > PATH_LIMIT:
            raise EditError('Путь результата слишком длинный. Сократите название варианта или путь к результату.')
        if _backup_io_path(path).exists() and os.path.normcase(relative) not in live_files:
            raise EditError('На месте нового PDF уже есть другой файл. Переместите его и повторите сохранение.')
    new_files = {os.path.normcase(str(Path(entry['file']))) for entry in new['images'] if not entry.get('removed')}
    sources = {str(Path(images[index]['file'])) for index in source_singles}
    sources.update(str(Path(images[index]['file'])) for key in changed for index in current.get(key, []))
    retired = {relative for relative in sources if os.path.normcase(relative) not in new_files}
    backup_only = {str(Path(images[index]['file'])) for index in removed}
    # The collage source and frame previews carry names of this revision only; nothing refers to them
    # unless the save succeeds, so «Отменить сохранение» may simply leave them unused.
    written = []
    try:
        for relative, data in aux.items():
            path = _safe_path(output, relative, mutation=True)
            _backup_io_path(path.parent).mkdir(parents=True, exist_ok=True)
            _write_bytes(path, data)
            written.append(path)
        return _commit_transaction(output, report, new, pdfs, retired, backup, backup_only)
    except BaseException:
        for path in written:
            _backup_io_path(path).unlink(missing_ok=True)
        raise


def _update_collages(new, cut_summary, checked, dismissed):
    images = new['images']
    records = new.setdefault('collages', [])
    by_id = {record['id']: record for record in records}
    for cut in cut_summary:
        record = by_id.get(cut['collage'])
        if record is None:
            source = images[cut['replaced'][0]]
            record = {'id': cut['collage'], 'page': source['page'], 'size': list(source['size']),
                      'title': source.get('title')}
            records.append(record)
            by_id[record['id']] = record
        whole = len(cut['boxes']) == 1 and cut['boxes'][0] == [0, 0, *record['size']]
        record.update(file=cut['source'], boxes=[] if whole else cut['boxes'], checked=True,
                      state='whole' if whole else 'manual')
        record.pop('image', None)
        record.pop('suggestion', None)
        for index in cut['replaced']:
            # Old frames and the whole picture live on in the collage itself, not among removed cards.
            images[index]['collage_replaced'] = True
            images[index]['collage'] = record['id']
    for identifier in checked:
        by_id[identifier]['checked'] = True
    for identifier in dismissed:
        by_id[identifier].update(state='dismissed', checked=True)
        for entry in images:
            if entry.get('collage') == identifier and not entry.get('removed'):
                entry.pop('collage', None)
    edit = new['edits'][-1]
    if cut_summary:
        edit['cuts'] = [{'collage': cut['collage'], 'pieces': cut['pieces'], 'replaced': cut['replaced']}
                        for cut in cut_summary]
    if checked:
        edit['collage_checked'] = checked
    if dismissed:
        edit['collage_dismissed'] = dismissed


def can_undo_last_save(report):
    edits = report.get('edits') or []
    return bool(edits and isinstance(edits[-1], dict) and edits[-1].get('backup')
                and isinstance(edits[-1].get('transaction'), dict)
                and edits[-1]['transaction'].get('report_hash') and not edits[-1].get('undo_of'))


def restore_last_save(output, payload):
    """Undo the latest save as a new revision; modified user files are never overwritten."""
    output = Path(output).resolve()
    report = load_report(output)
    if payload.get('revision') != report['revision']:
        raise EditConflict('Результат уже изменён в другом окне. Обновите страницу и повторите правку.')
    if not can_undo_last_save(report):
        raise EditError('Последнее сохранение уже отменено или его резервная копия недоступна.')
    edit = report['edits'][-1]
    if _report_hash(report) != edit['transaction']['report_hash']:
        raise EditConflict('Отчёт изменён после сохранения. Обновите результат, прежде чем отменять сохранение.')
    relative_backup = Path(edit['backup'])
    root = backup_root(report)
    if relative_backup.parent != root:
        raise EditError('Некорректная ссылка на резервную копию.')
    _safe_path(output, relative_backup)
    backup_report = _safe_path(output, relative_backup/'Отчёт.json', must_exist=True)
    try:
        transaction = edit['transaction']
        if (not isinstance(transaction['saved'], list) or not isinstance(transaction['created'], list)
                or not isinstance(transaction['written'], dict) or not isinstance(transaction['deleted'], list)):
            raise ValueError()
        for relative in transaction['saved']:
            path = _safe_path(output, relative_backup/relative, must_exist=True)
            if _file_hash(path) != transaction.get('saved_hashes', {}).get(relative):
                raise EditError('Резервная копия изменена. Отмена недоступна, чтобы сохранить ваши файлы.')
        previous = json.loads(_backup_io_path(backup_report).read_text(encoding='utf-8'))
        copies = edit.get('copies', [])
        frames = [index for cut in edit.get('cuts', []) for index in cut['pieces']]
        if (not isinstance(previous['images'], list) or not isinstance(copies, list) or
                len(previous['images'])+len(copies)+len(frames) != len(report['images'])):
            raise EditError('Резервная копия не соответствует этому результату.')
        if any(type(index) is not int or not len(previous['images'])+len(copies) <= index < len(report['images'])
               or not report['images'][index].get('collage') for index in frames):
            raise EditError('Резервная копия не соответствует кадрам склеек в результате.')
        for index, item in enumerate(copies, len(previous['images'])):
            if (not isinstance(item, dict) or type(item.get('image')) is not int or item['image'] != index or
                    type(item.get('source')) is not int or not 0 <= item['source'] < len(previous['images']) or
                    type(report['images'][index].get('copy_of')) is not int or
                    report['images'][index]['copy_of'] != item['source']):
                raise EditError('Резервная копия не соответствует копиям изображений в результате.')
        metadata = {'Отчёт.json', 'Просмотр.html'}
        def path_key(value):
            return os.path.normcase(str(Path(value)))
        metadata_keys = {path_key(relative) for relative in metadata}
        previous_live = {path_key(entry['file']) for entry in previous['images'] if not entry.get('removed')}
        current_live = {path_key(entry['file']) for entry in report['images'] if not entry.get('removed')}
        saved, created, deleted = ({path_key(relative) for relative in transaction[key]}
                                  for key in ('saved', 'created', 'deleted'))
        if (previous.get('source') != report.get('source')
                or saved & created or not saved <= previous_live | metadata_keys
                or created-metadata_keys != current_live-previous_live or deleted != previous_live-current_live
                or not {path_key(relative) for relative in transaction['written']} <= current_live
                or not deleted <= saved):
            raise EditError('Резервная копия не соответствует этому результату.')
        for relative, expected in transaction['written'].items():
            path = _safe_path(output, relative, must_exist=True)
            if _file_hash(path) != expected:
                raise EditError('PDF изменён после сохранения. Отмена недоступна, чтобы сохранить ваши файлы.')
        for relative in transaction['deleted']:
            if _backup_io_path(_safe_path(output, relative)).exists():
                raise EditError('В папке появился другой файл на месте убранного PDF. Сначала переместите его.')
        pdfs = {}
        for relative in transaction['saved']:
            _safe_path(output, relative)
            if relative not in metadata:
                if Path(relative).suffix.lower() != '.pdf':
                    raise ValueError()
                source = _safe_path(output, relative_backup/relative, must_exist=True)
                pdfs[relative] = _backup_io_path(source).read_bytes()
        retired = set(transaction['created'])-metadata
        if any(Path(relative).suffix.lower() != '.pdf' or relative not in transaction['written'] for relative in retired):
            raise ValueError()
        if not isinstance(previous['images'], list):
            raise ValueError()
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, EditError):
            raise
        raise EditError('Резервная копия не подходит для отмены сохранения.') from None
    new = copy.deepcopy(previous)
    new['revision'] = report['revision']+1
    new['output'] = str(output)
    new['edits'] = copy.deepcopy(report['edits'])
    undo_backup = unused_backup(output, report)
    new['edits'].append({'time': datetime.now().isoformat(timespec='seconds'), 'version': VERSION,
                         'backup': str(undo_backup.relative_to(output)), 'undo_of': report['revision']})
    summarize(new)
    return _commit_transaction(output, report, new, pdfs, retired, undo_backup)


SPECIAL_FOLDERS = {'без подписи', 'на проверку'}
RESERVED = re.compile(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', re.I)
PATH_LIMIT = 239


def clean_name(name):
    name = re.sub(r'\s+', ' ', str(name or '')).strip()
    if name.lower().endswith('.pdf'):
        name = name[:-4].rstrip()
    name = name.rstrip(' .')
    if not name:
        raise EditError('Введите новое имя.')
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise EditError('Имя не может содержать символы < > : " / \\ | ? *')
    if RESERVED.fullmatch(name) or name.startswith('_'):
        raise EditError('Это имя зарезервировано Windows или программой. Выберите другое.')
    if len(name) > 120:
        raise EditError('Имя слишком длинное: не больше 120 символов.')
    return name


def check_free(output, old, new):
    """The target must not exist (a case-only change of the same item is allowed) and must fit the path budget."""
    if new.exists() and new.resolve() != old.resolve():
        raise EditError(f'«{new.name}» уже существует. Выберите другое имя.')
    longest = max((len(str(p.relative_to(old))) for p in old.rglob('*')), default=0) if old.is_dir() else 0
    if len(str(new))+(longest+1 if longest else 0) > PATH_LIMIT:
        raise EditError('С таким именем путь к файлам станет слишком длинным. Сократите имя.')


def rename_item(output, payload):
    """Rename a variant (folder and PDF), a caption folder or one PDF; nothing else in the result changes."""
    output = Path(output).resolve()
    report = load_report(output)
    if payload.get('revision') != report['revision']:
        raise EditConflict('Результат уже изменён в другом окне. Обновите страницу и повторите правку.')
    kind, target = payload.get('kind'), payload.get('target')
    name = payload.get('name')
    images = report['images']
    new = copy.deepcopy(report)
    moves = []
    if kind == 'variant':
        name = clean_name(name)
        try:
            key = variant_key(target)
        except ValueError:
            raise EditError('Некорректный вариант.') from None
        members = [i for i, e in enumerate(images) if e['variant'] == key and not e.get('removed')]
        if not members:
            raise EditError('Вариант не найден в результате.')
        before = group_title(key, report)
        old_file = Path(images[members[0]]['file'])
        new_dir = old_file.parent.with_name(name)
        if name.casefold() in SPECIAL_FOLDERS | {OTHER_DIR.casefold(), VARIANTS_DIR.casefold()}:
            raise EditError('Это имя занято служебной папкой.')
        check_free(output, output/old_file.parent, output/new_dir)
        moves = [(output/old_file.parent, output/new_dir), (output/new_dir/old_file.name, output/new_dir/f'{name}.pdf')]
        names = new.setdefault('variant_names', {})
        names[str(key)] = name
        if name == f'Вариант {key}':
            names.pop(str(key))
        for i in members:
            new['images'][i].update(folder=name, file=str(new_dir/f'{name}.pdf'))
        if 'folder_labels' in new:
            new['folder_labels'][name] = name
    elif kind == 'folder':
        members = [i for i, e in enumerate(images)
                   if e['folder'] == target and e['variant'] is None and not e.get('removed')]
        if not members or str(target).casefold() in SPECIAL_FOLDERS:
            raise EditError('Эту папку нельзя переименовать.' if members else 'Папка не найдена в результате.')
        before = (report.get('folder_labels') or {}).get(str(target), str(target))
        if str(name or '').strip() == before:
            return report, None
        name = clean_name(name)
        if name.casefold() in SPECIAL_FOLDERS:
            raise EditError('Это имя занято служебной папкой.')
        old_dir = Path(images[members[0]]['file']).parent
        longest = max((len(str(path.relative_to(output/old_dir))) for path in (output/old_dir).rglob('*')), default=0)
        limit = min(120, PATH_LIMIT-len(str(output/old_dir.parent))-longest-2)
        if limit < 1:
            raise EditError('Путь результата слишком длинный. Переместите папку результата ближе к корню диска.')
        disk_name = safe_name(name, limit)
        new_dir = old_dir.with_name(disk_name)
        check_free(output, output/old_dir, output/new_dir)
        moves = [(output/old_dir, output/new_dir)]
        new.setdefault('folder_labels', {})[disk_name] = name
        for i in members:
            entry = new['images'][i]
            entry.update(folder=disk_name, file=str(new_dir/Path(images[i]['file']).name))
            if isinstance(entry.get('original_location'), dict):
                entry['original_location'].update(folder=disk_name, label=name, file=entry['file'])
            elif entry.get('auto_folder') == target:
                entry['auto_folder'] = disk_name
    elif kind == 'file':
        name = clean_name(name)
        try:
            index = int(target)
            entry = images[index]
        except (TypeError, ValueError, IndexError):
            raise EditError('Изображение не найдено в результате.') from None
        if entry['variant'] is not None or entry.get('removed') or entry.get('group') == 'perspective':
            raise EditError('Переименовать можно только отдельный PDF; PDF варианта переименуйте вместе с вариантом.')
        old_file = Path(entry['file'])
        before = old_file.stem
        new_file = old_file.with_name(f'{name}.pdf')
        check_free(output, output/old_file, output/new_file)
        moves = [(output/old_file, output/new_file)]
        new['images'][index]['file'] = str(new_file)
        if isinstance(new['images'][index].get('original_location'), dict):
            new['images'][index]['original_location']['file'] = str(new_file)
    else:
        raise EditError('Некорректный запрос переименования.')
    if before == name:
        return report, None
    summarize(new)
    new['revision'] += 1
    change = {'kind': kind, 'from': before, 'to': name}
    if kind == 'folder':
        change.update(disk_from=str(target), disk_to=disk_name)
    new.setdefault('edits', []).append({'time': datetime.now().isoformat(timespec='seconds'), 'version': VERSION,
                                        'renamed': change})
    done = []
    try:
        for source, target_path in moves:
            if source != target_path:
                os.rename(source, target_path)
                done.append((source, target_path))
        write_gallery(output, new)
        write_report(output, new)
    except BaseException:
        for source, target_path in reversed(done):
            os.rename(target_path, source)
        write_gallery(output, report)
        raise
    return new, change
