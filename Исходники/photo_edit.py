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

from photo_export import (OTHER_DIR, SERVICE_DIR, VARIANTS_DIR, VERSION, entry_group, group_file, group_title,
                          has_service_dir, safe_name, write_gallery)

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
    number = int(key)
    if not 1 <= number <= 999:
        raise EditError('Номер варианта должен быть от 1 до 999.')
    return number


def parse_payload(report, payload):
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
    copied_ids = set(range(len(images), len(images)+len(copies)))
    if copied_ids & set(removed+restored):
        raise EditError('Новую копию нужно поместить в вариант или в папку подписи.')
    for index in seen:
        if (not 0 <= index < len(images)+len(copies) or
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


def variant_pdf(output, report, ids, key):
    writer = PdfWriter()
    readers = {}
    try:
        title = group_title(key, report) if key is not None else (
            report['images'][ids[0]].get('title') or original_location(report['images'][ids[0]])['folder'])
        writer.add_metadata({'/Title': title, '/Creator': f'PDF Photos {VERSION}'})
        for position, index in enumerate(ids):
            entry = report['images'][index]
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
        entry.pop('removed', None)
        entry.pop('suppressed', None)
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
                   *, misc=None, restored=None, singles=None, copies=None):
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
    for position, source in enumerate(copies or [], len(images)-len(copies or [])):
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


def apply_edits(output, payload):
    """Rebuild variant and individual PDFs transactionally, preserving every replaced source."""
    output = Path(output).resolve()
    report = load_report(output)
    variants, removed, current = parse_payload(report, payload)
    copies = payload.get('copies', [])
    copy_start = len(report['images'])
    working = _report_with_copies(report, copies) if copies else report
    images = working['images']
    restored = [int(index) for index in payload.get('restored', [])]
    misc = [int(index) for index in payload.get('misc', [])
            if int(index) >= copy_start or images[int(index)].get('removed') or entry_group(images[int(index)]) is not None]
    changed = sorted(key for key, ids in variants.items() if ids != current.get(key, []))
    to_variant = [index for ids in variants.values() for index in ids]
    # Copies only borrow source references; they must never retire a live or recovery PDF.
    source_singles = [index for index in removed+to_variant
                      if index < copy_start and entry_group(images[index]) is None and not images[index].get('removed')]
    if not changed and not source_singles and not misc:
        return report, None
    # Read every source before creating backups or replacing any live PDF.
    built = {key: variant_pdf(output, working, variants[key], key) for key in changed if variants[key]}
    reserved = {str(group_file(report, key)).casefold() for key in variants}
    singles = {}
    folder_labels = dict(report.get('folder_labels') or {})
    for index in misc:
        target, location = _misc_target(output, working, index, reserved, folder_labels)
        singles[index] = target, location, variant_pdf(output, working, [index], None)
    # A removal is recoverable only if its source and page really exist.
    for index in removed:
        entry_pdf_source(output, report, index)
    backup = unused_backup(output, report)
    backup_name = str(backup.relative_to(output))
    new = updated_report(working, variants, removed, current, built, changed, backup_name,
                         misc=misc, restored=restored, singles=singles, copies=copies)
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
    return _commit_transaction(output, report, new, pdfs, retired, backup, backup_only)


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
        if (not isinstance(previous['images'], list) or not isinstance(copies, list) or
                len(previous['images'])+len(copies) != len(report['images'])):
            raise EditError('Резервная копия не соответствует этому результату.')
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
            key = int(target)
        except (TypeError, ValueError):
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
