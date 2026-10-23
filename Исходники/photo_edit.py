"""Manual corrections of a finished export: remove, move and reorder images of variant PDFs."""
from collections import Counter
import copy
from datetime import datetime
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil

from pypdf import PdfReader, PdfWriter

from photo_export import VERSION, write_gallery

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


def variant_members(report):
    members = {}
    for index, entry in enumerate(report['images']):
        if entry['variant'] is not None and not entry.get('removed'):
            members.setdefault(entry['variant'], []).append(index)
    for ids in members.values():
        ids.sort(key=lambda index: report['images'][index]['pdf_page'])
    return members


def parse_payload(report, payload):
    if payload.get('revision') != report['revision']:
        raise EditConflict('Результат уже изменён в другом окне. Обновите страницу и повторите правку.')
    images = report['images']
    try:
        variants = {int(number): [int(index) for index in ids] for number, ids in payload['variants'].items()}
        removed = [int(index) for index in payload.get('removed', [])]
    except (AttributeError, KeyError, TypeError, ValueError):
        raise EditError('Некорректный формат правки.') from None
    seen = removed+[index for ids in variants.values() for index in ids]
    if len(seen) != len(set(seen)):
        raise EditError('Одно изображение указано в правке несколько раз.')
    for index in seen:
        if not 0 <= index < len(images) or images[index].get('removed'):
            raise EditError(f'Изображение {index} не найдено в результате.')
    if any(not 1 <= number <= 999 for number in variants):
        raise EditError('Номер варианта должен быть от 1 до 999.')
    current = variant_members(report)
    lost = [index for ids in current.values() for index in ids if index not in set(seen)]
    if lost:
        raise EditError('В правке пропущены изображения из вариантов. Обновите страницу и повторите правку.')
    for number in current:
        variants.setdefault(number, [])
    return variants, removed, current


def variant_pdf(output, report, ids, number):
    writer = PdfWriter()
    readers = {}
    try:
        writer.add_metadata({'/Title': f'Вариант {number}', '/Creator': f'PDF Photos {VERSION}'})
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


def unused_backup(output):
    base = Path(output)/BACKUPS/datetime.now().strftime('%Y-%m-%d %H-%M-%S')
    for number in range(1, 1000):
        candidate = base if number == 1 else base.with_name(f'{base.name} ({number})')
        if not candidate.exists():
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
            'pages': 0, 'sha256': entry['sha256']})
        document['pages'] += 1
    report['documents'] = list(documents.values())


def updated_report(report, variants, removed, current, built, changed, backup_name):
    new = copy.deepcopy(report)
    images = new['images']
    moved, reordered = [], []
    for number in changed:
        target = str(Path(f'Вариант {number}')/f'Вариант {number}.pdf')
        checksum = hashlib.sha256(built[number]).hexdigest() if number in built else None
        kept = [index for index in current.get(number, []) if index in variants[number]]
        if kept != [index for index in variants[number] if index in kept]:
            reordered.append(number)
        for position, index in enumerate(variants[number], 1):
            entry = images[index]
            if entry['variant'] != number:
                moved.append({'image': index, 'from': entry['folder'], 'to': f'Вариант {number}'})
                entry.setdefault('auto_folder', entry['folder'])
                entry.update(status='named', manual=True, reason=f'Перенесено вручную в «Вариант {number}».')
            entry.update(folder=f'Вариант {number}', variant=number, file=target, pdf_page=position, sha256=checksum)
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


def apply_edits(output, payload):
    """Rebuild changed variant PDFs; every replaced or removed file is kept in a backup folder first."""
    output = Path(output).resolve()
    report = load_report(output)
    variants, removed, current = parse_payload(report, payload)
    images = report['images']
    changed = sorted(number for number, ids in variants.items() if ids != current.get(number, []))
    singles = [index for index in removed+[i for ids in variants.values() for i in ids]
               if images[index]['variant'] is None]
    if not changed and not singles:
        return report, None
    built = {number: variant_pdf(output, report, variants[number], number) for number in changed if variants[number]}
    backup = unused_backup(output)
    backup_name = str(backup.relative_to(output))
    new = updated_report(report, variants, removed, current, built, changed, backup_name)
    rebuilt = {str(Path(f'Вариант {number}')/f'Вариант {number}.pdf') for number in built}
    touched = sorted({images[index]['file'] for index in singles}
                     | {str(Path(f'Вариант {number}')/f'Вариант {number}.pdf') for number in changed}
                     | {'Отчёт.json', 'Просмотр.html'})
    saved, created = [], []
    try:
        for relative in touched:
            if (output/relative).is_file():
                (backup/relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(output/relative, backup/relative)
                saved.append(relative)
        for number in built:
            target = output/f'Вариант {number}'/f'Вариант {number}.pdf'
            if not target.exists():
                created.append(target)
            target.parent.mkdir(exist_ok=True)
            temporary = target.with_name(target.name+'.tmp')
            temporary.write_bytes(built[number])
            os.replace(temporary, target)
        for relative in saved:
            if relative not in rebuilt | {'Отчёт.json', 'Просмотр.html'}:
                (output/relative).unlink()
        write_gallery(output, new)
        write_report(output, new)
    except BaseException:
        for relative in saved:
            (output/relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup/relative, output/relative)
        for target in created:
            target.unlink(missing_ok=True)
        raise
    for relative in saved:
        folder = (output/relative).parent
        if folder != output and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    return new, backup_name
