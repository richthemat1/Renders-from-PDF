"""Manual corrections of a finished export: remove, move and reorder images of variant PDFs."""
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

from pypdf import PdfReader, PdfWriter

from photo_export import (OTHER_DIR, SERVICE_DIR, VARIANTS_DIR, VERSION, entry_group, group_file, group_title,
                          has_service_dir, write_gallery)

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
        variants = {parse_key(report, key): [int(index) for index in ids] for key, ids in payload['variants'].items()}
        removed = [int(index) for index in payload.get('removed', [])]
    except EditError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError):
        raise EditError('Некорректный формат правки.') from None
    seen = removed+[index for ids in variants.values() for index in ids]
    if len(seen) != len(set(seen)):
        raise EditError('Одно изображение указано в правке несколько раз.')
    for index in seen:
        if not 0 <= index < len(images) or images[index].get('removed'):
            raise EditError(f'Изображение {index} не найдено в результате.')
    current = variant_members(report)
    lost = [index for ids in current.values() for index in ids if index not in set(seen)]
    if lost:
        raise EditError('В правке пропущены изображения из вариантов. Обновите страницу и повторите правку.')
    for key in current:
        variants.setdefault(key, [])
    return variants, removed, current


def variant_pdf(output, report, ids, key):
    writer = PdfWriter()
    readers = {}
    try:
        writer.add_metadata({'/Title': group_title(key, report), '/Creator': f'PDF Photos {VERSION}'})
        for position, index in enumerate(ids):
            entry = report['images'][index]
            if entry['file'] not in readers:
                readers[entry['file']] = PdfReader(Path(output)/entry['file'])
            writer.add_page(readers[entry['file']].pages[entry['pdf_page']-1])
            writer.add_outline_item(f'Страница {entry["page"]} — {entry["title"] or entry["folder"]}', position)
        buffer = BytesIO()
        writer.write(buffer)
        return buffer.getvalue()
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


def updated_report(report, variants, removed, current, built, changed, backup_name):
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
            if entry_group(entry) != key:
                moved.append({'image': index, 'from': entry['folder'], 'to': group_title(key, report)})
                entry.setdefault('auto_folder', entry['folder'])
                entry.update(status='named', manual=True, reason=f'Перенесено вручную в «{group_title(key, report)}».')
            entry.update(folder=group_title(key, report), variant=key, group='variant',
                         file=target, pdf_page=position, sha256=checksum)
    for key in changed:
        if not variants[key]:
            new.get('variant_names', {}).pop(str(key), None)
    for index in removed:
        entry = images[index]
        entry.setdefault('auto_folder', entry['folder'])
        entry.update(removed=True, manual=True, file=str(Path(backup_name)/entry['file']),
                     reason='Удалено вручную; копия сохранена в резервной папке.')
    summarize(new)
    new['revision'] += 1
    new.setdefault('edits', []).append({
        'time': datetime.now().isoformat(timespec='seconds'), 'version': VERSION, 'backup': backup_name,
        'removed': [{'image': index, 'from': images[index]['auto_folder']} for index in removed],
        'moved': moved, 'reordered': reordered})
    return new


def write_report(output, report):
    path = Path(output)/'Отчёт.json'
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def remove_empty_folders(output, folder):
    while folder != output and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


def apply_edits(output, payload):
    """Rebuild changed variant PDFs; every replaced or removed file is kept in a backup folder first."""
    output = Path(output).resolve()
    report = load_report(output)
    variants, removed, current = parse_payload(report, payload)
    images = report['images']
    changed = sorted(key for key, ids in variants.items() if ids != current.get(key, []))
    singles = [index for index in removed+[i for ids in variants.values() for i in ids]
               if entry_group(images[index]) is None]
    if not changed and not singles:
        return report, None
    built = {key: variant_pdf(output, report, variants[key], key) for key in changed if variants[key]}
    backup = unused_backup(output, report)
    backup_name = str(backup.relative_to(output))
    backup_io = _backup_io_path(backup)
    new = updated_report(report, variants, removed, current, built, changed, backup_name)
    rebuilt = {str(group_file(report, key)) for key in built}
    touched = sorted({images[index]['file'] for index in singles}
                     | {str(group_file(report, key)) for key in changed}
                     | {'Отчёт.json', 'Просмотр.html'})
    saved, created = [], []
    try:
        for relative in touched:
            if (output/relative).is_file():
                (backup_io/relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(output/relative, backup_io/relative)
                saved.append(relative)
        for key in built:
            target = output/group_file(report, key)
            if not target.exists():
                created.append(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name+'.tmp')
            temporary.write_bytes(built[key])
            os.replace(temporary, target)
        for relative in saved:
            if relative not in rebuilt | {'Отчёт.json', 'Просмотр.html'}:
                (output/relative).unlink()
        write_gallery(output, new)
        write_report(output, new)
    except BaseException:
        for relative in saved:
            (output/relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_io/relative, output/relative)
        for target in created:
            target.unlink(missing_ok=True)
            remove_empty_folders(output, target.parent)
        raise
    for relative in saved:
        remove_empty_folders(output, (output/relative).parent)
    return new, backup_name


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
    name = clean_name(payload.get('name'))
    images = report['images']
    new = copy.deepcopy(report)
    moves = []
    if kind == 'variant':
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
    elif kind == 'folder':
        members = [i for i, e in enumerate(images)
                   if e['folder'] == target and e['variant'] is None and not e.get('removed')]
        if not members or str(target).casefold() in SPECIAL_FOLDERS:
            raise EditError('Эту папку нельзя переименовать.' if members else 'Папка не найдена в результате.')
        if name.casefold() in SPECIAL_FOLDERS:
            raise EditError('Это имя занято служебной папкой.')
        before = target
        old_dir = Path(images[members[0]]['file']).parent
        new_dir = old_dir.with_name(name)
        check_free(output, output/old_dir, output/new_dir)
        moves = [(output/old_dir, output/new_dir)]
        for i in members:
            new['images'][i].update(folder=name, file=str(new_dir/Path(images[i]['file']).name))
    elif kind == 'file':
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
    else:
        raise EditError('Некорректный запрос переименования.')
    if before == name:
        return report, None
    summarize(new)
    new['revision'] += 1
    change = {'kind': kind, 'from': before, 'to': name}
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
