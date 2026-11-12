"""Clean copies of variant PDFs in folders the user chose: one PDF per variant, no folders around it.

The working result keeps its own structure; this module only copies a variant's PDF out of it and
remembers where each copy went, so the copy follows later saves, renames and removals of the variant."""
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import uuid

from photo_export import SERVICE_DIR, group_title, hide, safe_name, settled

LOGGER = logging.getLogger('pdf-photos')
STATE_FILE = 'Выгрузка вариантов.json'
# Explorer and most programs open a file only within 259 characters; the rest is left for a « (2)»
# the user or Explorer may add to a copy.
PATH_LIMIT = 250
MIN_STEM = 8


class DeliveryError(ValueError):
    """The chosen folder cannot take variant PDFs at all."""


def _state_path(output):
    return Path(output)/SERVICE_DIR/STATE_FILE


def _valid(record):
    return (isinstance(record, dict)
            and all(isinstance(record.get(key), str) and record[key] for key in ('variant', 'folder', 'file', 'name'))
            and isinstance(record.get('sha256'), str) and type(record.get('auto')) is bool
            and Path(record['folder']).is_absolute() and Path(record['file']).name == record['file'])


def load(output):
    """Where copies of this result's variants were put; unreadable notes mean no copies are followed."""
    try:
        value = json.loads(_state_path(output).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError):
        return []
    copies = value.get('copies') if isinstance(value, dict) else None
    return [record for record in copies if _valid(record)] if isinstance(copies, list) else []


def _store(output, records):
    path = _state_path(output)
    if not records and not path.exists():
        return
    created = not path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if created:
        hide(path.parent)
    temporary = path.with_name(f'.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps({'version': 1, 'copies': records}, ensure_ascii=False, indent=2),
                             encoding='utf-8')
        settled(os.replace, temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def targets(output):
    """{variant: [{folder, file, auto}]} for the interface."""
    result = {}
    for record in load(output):
        result.setdefault(record['variant'], []).append(
            {'folder': record['folder'], 'file': record['file'], 'auto': record['auto']})
    return result


def variant_documents(report):
    """{variant key as text: its document}; only variants hold a ready multi-page PDF."""
    return {str(document['variant']): document for document in report.get('documents', [])
            if document.get('variant') is not None}


def checked_folder(folder, output):
    try:
        folder = Path(folder).resolve()
        usable = folder.is_dir()
    except OSError:
        usable = False
    if not usable:
        raise DeliveryError('Папка недоступна. Выберите существующую папку.')
    output = Path(output).resolve()
    if folder == output or output in folder.parents:
        raise DeliveryError('Это рабочая папка результата. Выберите для готовых вариантов другую папку.')
    if PATH_LIMIT-len(str(folder))-1-len('.pdf')-len(' (2)') < MIN_STEM:
        raise DeliveryError('Путь к папке слишком длинный: имя PDF в нём не поместится. '
                            'Выберите папку ближе к корню диска, например D:\\Варианты.')
    return folder


def file_name(folder, title, presentation=None):
    """«Вариант 1.pdf», or «Презентация — Вариант 1.pdf» in the folder shared by all presentations.

    Long names are cut on words so that the whole path opens in Explorer."""
    room = PATH_LIMIT-len(str(folder))-1-len('.pdf')-len(' (2)')
    if room < MIN_STEM:
        raise DeliveryError('Путь к папке слишком длинный: имя PDF в нём не поместится.')
    stem = safe_name(title, min(room, 100), keep_end=True)
    if presentation:
        prefix_room = min(room-len(stem)-3, 90)
        if prefix_room >= MIN_STEM:
            stem = safe_name(presentation, prefix_room, keep_end=True)+' — '+stem
    return stem+'.pdf'


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _free(folder, name, taken):
    stem = name[:-len('.pdf')]
    for number in range(1, 1000):
        candidate = name if number == 1 else f'{stem} ({number}).pdf'
        if candidate.casefold() not in taken and not (folder/candidate).exists():
            return candidate
    raise OSError('не удалось подобрать свободное имя файла')


def _drop_copy(record):
    """Remove a copy the program wrote, unless it was changed since: then it is the user's file."""
    path = Path(record['folder'])/record['file']
    try:
        if path.is_file() and _digest(path) == record['sha256']:
            settled(path.unlink)
    except OSError as error:
        LOGGER.warning('Не удалось убрать прежнюю копию варианта %s: %s', path, error)


def _place(output, report, document, record, presentation):
    """Write the variant's current PDF where the record says; returns the updated record."""
    folder = Path(record['folder'])
    if not folder.is_dir():
        raise OSError('папка недоступна')
    source = Path(output)/document['file']
    name = file_name(folder, group_title(document['variant'], report), presentation)
    previous = record.get('file')
    if previous and record.get('name') == name:
        file = previous
    else:
        taken = set() if not previous else {previous.casefold()}
        file = _free(folder, name, taken)
    target = folder/file
    if (previous == file and record.get('sha256') == document['sha256'] and target.is_file()
            and target.stat().st_size == source.stat().st_size):
        return record
    temporary = folder/f'.{uuid.uuid4().hex}.tmp'
    try:
        shutil.copyfile(source, temporary)
        settled(os.replace, temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    if previous and previous != file:
        _drop_copy(record)
    return dict(record, file=file, name=name, sha256=document['sha256'])


def _problem(title, folder, error):
    if isinstance(error, PermissionError):
        reason = 'файл открыт в другой программе или папка защищена от записи. Закройте PDF и сохраните ещё раз'
    elif isinstance(error, DeliveryError):
        reason = str(error)
    else:
        reason = str(error.strerror or error) if isinstance(error, OSError) else str(error)
    return f'«{title}» не сохранён в «{folder}»: {reason}.'


def _refresh(output, report, records, auto_folder=None):
    """Bring every followed copy up to date with the saved result."""
    documents = variant_documents(report)
    presentation = Path(report['source']).stem
    wanted = []
    for record in records:
        if record['auto'] and (auto_folder is None
                               or os.path.normcase(record['folder']) != os.path.normcase(str(auto_folder))):
            # The shared folder was switched off or changed: copies made there stay the user's files.
            continue
        wanted.append(record)
    if auto_folder is not None:
        followed = {record['variant'] for record in wanted if record['auto']}
        wanted.extend({'variant': key, 'folder': str(auto_folder), 'auto': True}
                      for key in documents if key not in followed)
    kept, saved, errors = [], [], []
    for record in wanted:
        document = documents.get(record['variant'])
        if document is None:
            if record.get('file'):
                _drop_copy(record)
            continue
        title = group_title(document['variant'], report)
        try:
            updated = _place(output, report, document, record, presentation if record['auto'] else None)
        except (OSError, DeliveryError) as error:
            LOGGER.warning('Не удалось сохранить вариант %s в %s: %s', title, record['folder'], error)
            errors.append(_problem(title, record['folder'], error))
            if record.get('file'):
                kept.append(record)
            continue
        kept.append(updated)
        saved.append(str(Path(updated['folder'])/updated['file']))
    try:
        _store(output, kept)
    except OSError as error:
        LOGGER.warning('Не удалось записать расположение готовых вариантов: %s', error)
        errors.append('Расположение готовых вариантов не записано: при следующем сохранении '
                      'они не обновятся сами.')
    return {'saved': saved, 'errors': errors}


def sync(output, report, auto_folder=None):
    """After processing or a save: update the copies made before and the shared folder, if one is set."""
    records = load(output)
    if auto_folder:
        try:
            auto_folder = checked_folder(auto_folder, output)
        except DeliveryError as error:
            result = _refresh(output, report, records, None) if any(not r['auto'] for r in records) else \
                {'saved': [], 'errors': []}
            result['errors'].insert(0, f'Папка для готовых вариантов «{auto_folder}»: {error}')
            return result
    elif not records:
        return {'saved': [], 'errors': []}
    return _refresh(output, report, records, auto_folder or None)


def deliver(output, report, folder, variants=None):
    """Put the chosen variants (all by default) into folder as bare PDFs and follow them from now on."""
    folder = checked_folder(folder, output)
    documents = variant_documents(report)
    keys = list(documents) if variants is None else [str(key) for key in variants]
    missing = [key for key in keys if key not in documents]
    if missing or not keys:
        raise DeliveryError('В сохранённом результате нет такого варианта. Если вариант только что создан '
                            'или изменён, сначала нажмите «Сохранить».')
    records = load(output)
    chosen = []
    for key in keys:
        same = next((record for record in records if record['variant'] == key and not record['auto']
                     and os.path.normcase(record['folder']) == os.path.normcase(str(folder))), None)
        # One followed place per variant: a copy in a folder chosen earlier stays there as the user's file.
        records = [record for record in records if record['variant'] != key or record['auto']]
        chosen.append(same or {'variant': key, 'folder': str(folder), 'auto': False})
    auto = next((record['folder'] for record in records if record['auto']), None)
    result = _refresh(output, report, records+chosen, auto)
    wanted = {os.path.normcase(str(folder))}
    result['saved'] = [path for path in result['saved'] if os.path.normcase(str(Path(path).parent)) in wanted
                       and any(Path(path).name == record['file'] for record in load(output)
                               if not record['auto'] and record['variant'] in keys)]
    result['folder'] = str(folder)
    return result
