"""Local workspace preferences and the history of actual completed exports."""
from contextlib import contextmanager
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import shutil
import time
import uuid

from photo_export import safe_name, unused_folder

LOGGER = logging.getLogger('pdf-photos')
MODES = ('nearby', 'results', 'custom')


@contextmanager
def state_lock(path):
    """Serialize read/merge/replace across independent application windows."""
    with path.with_name(path.name + '.lock').open('a+b') as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b'\0')
            handle.flush()
        if os.name == 'nt':
            import msvcrt
            def acquire():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        deadline = time.monotonic() + 1
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.01)
        try:
            yield
        finally:
            release()


class WorkspaceState:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else Path(
            os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'PDF-Photos' / 'workspace.json'
        self.output_mode, self.output_folder = 'nearby', ''
        self.history, self.warning, self._corrupt = [], '', False
        self._pending_history, self._settings_changed = {}, False
        self._load()

    def _load(self):
        try:
            self.output_mode, self.output_folder, self.history = self._read()
        except FileNotFoundError:
            return
        except (OSError, UnicodeError, ValueError) as error:
            self._corrupt = isinstance(error, (UnicodeError, ValueError))
            self.warning = 'Не удалось прочитать историю и настройки. Используются настройки по умолчанию.'
            LOGGER.warning('%s %s: %s', self.warning, self.path, error)
            return
    def _read(self):
        value = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Некорректное состояние рабочего пространства')
        mode, folder = value.get('output_mode'), value.get('output_folder')
        if mode in MODES and (mode != 'custom' or isinstance(folder, str) and Path(folder).is_absolute()):
            folder = folder if isinstance(folder, str) else ''
        else:
            mode, folder = 'nearby', ''
        history = value.get('history', [])
        if not isinstance(history, list):
            history = []
        seen, valid = set(), []
        for item in history[:500]:
            if not self._valid_record(item):
                continue
            key = os.path.normcase(item['output'])
            if key not in seen:
                valid.append(item)
                seen.add(key)
            if len(valid) == 100:
                break
        return mode, folder, valid

    @staticmethod
    def _valid_record(item):
        if not isinstance(item, dict):
            return False
        for key in ('source', 'name', 'output', 'finished_at'):
            if not isinstance(item.get(key), str) or not item[key]:
                return False
        if not Path(item['output']).is_absolute():
            return False
        try:
            datetime.fromisoformat(item['finished_at'])
        except ValueError:
            return False
        return all(type(item.get(key)) is int and item[key] >= 0
                   for key in ('size', 'images', 'documents', 'variants', 'unsigned', 'review', 'errors', 'warnings'))

    def save(self):
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with state_lock(self.path):
                try:
                    mode, folder, history = self._read()
                except FileNotFoundError:
                    mode, folder, history = 'nearby', '', []
                except (UnicodeError, ValueError):
                    backup = self.path.with_name(self.path.name + '.corrupt-' + uuid.uuid4().hex[:10])
                    shutil.copy2(self.path, backup)
                    mode, folder, history = 'nearby', '', []
                if self._settings_changed:
                    mode, folder = self.output_mode, self.output_folder
                current = {os.path.normcase(item['output']): item for item in history}
                updates = []
                for key, item in reversed(list(self._pending_history.items())):
                    if key in current:
                        item = dict(item, finished_at=current[key]['finished_at'],
                                    imported=current[key].get('imported', False))
                    updates.append(item)
                merged = (updates + [item for key, item in current.items()
                                     if key not in self._pending_history])[:100]
                value = {'schema': 1, 'output_mode': mode, 'output_folder': folder, 'history': merged}
                temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(temporary, self.path)
                self.history = merged
                self._pending_history.clear()
                self._settings_changed = False
                self._corrupt = False
            self.warning = ''
            return True
        except OSError as error:
            self.warning = 'Не удалось сохранить историю и настройки. В этом сеансе они продолжают работать.'
            LOGGER.warning('%s %s', self.warning, error)
            return False
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def set_output(self, mode, folder=None):
        if mode not in MODES:
            raise ValueError('Неизвестный способ сохранения.')
        if mode == 'custom':
            if folder is None or not Path(folder).is_dir():
                raise ValueError('Выберите существующую папку для результатов.')
            folder = str(Path(folder).resolve())
        self.output_mode = mode
        self.output_folder = str(folder or '')
        self._settings_changed = True
        return self.save()

    def destination(self, source):
        source = Path(source).resolve()
        if self.output_mode == 'nearby':
            return None
        parent = source.parent / 'Результаты' if self.output_mode == 'results' else Path(self.output_folder)
        return unused_folder(parent / (safe_name(source.stem, 78) + ' — PDF'))

    @property
    def output_label(self):
        return {'nearby': 'Рядом с исходным файлом', 'results': 'В папке «Результаты» рядом с исходным файлом',
                'custom': self.output_folder}[self.output_mode]

    def remember(self, report, processed=True):
        output = str(Path(report['output']).resolve())
        key = os.path.normcase(output)
        previous = next((item for item in self.history if os.path.normcase(item['output']) == key), None)
        source = Path(report['source'])
        try:
            size = source.stat().st_size
        except OSError:
            size = previous['size'] if previous else 0
        counts = report.get('counts_by_status', {})
        stamp = previous['finished_at'] if previous else datetime.now().isoformat(timespec='seconds')
        item = {'source': str(source), 'name': source.name, 'output': output, 'size': size,
                'images': sum(not image.get('removed') for image in report['images']),
                'documents': len(report['documents']),
                'variants': len({document['variant'] for document in report['documents'] if document.get('variant') is not None}),
                'unsigned': counts.get('unsigned', 0), 'review': counts.get('review', 0),
                'errors': len(report.get('errors', [])), 'warnings': len(report.get('warnings', [])),
                'finished_at': stamp, 'imported': previous.get('imported', False) if previous else not processed}
        self.history = [item] + [entry for entry in self.history if os.path.normcase(entry['output']) != key]
        self.history = self.history[:100]
        self._pending_history.pop(key, None)
        self._pending_history[key] = item
        self.save()
        return item
