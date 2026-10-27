"""Local workspace preferences and the history of actual completed exports."""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import time
import uuid

from photo_export import planned_output

LOGGER = logging.getLogger('pdf-photos')
MODES = ('nearby', 'results', 'custom')
DEFAULT_SIDEBAR = {'width': 248, 'hidden': False}


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
        self.sidebar = dict(DEFAULT_SIDEBAR)
        self.history, self.failures, self.warning, self._corrupt = [], [], '', False
        self._pending_history, self._settings_changed = {}, False
        self._sidebar_changed = False
        self._forgotten_history, self._forgotten_failures, self._pending_failures = set(), set(), {}
        self._load()

    def _load(self):
        try:
            self.output_mode, self.output_folder, self.history, self.failures, self.sidebar = self._read()
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
        failures, seen_failures = [], set()
        candidates = value.get('failures', [])
        for item in candidates[:500] if isinstance(candidates, list) else []:
            if self._valid_failure(item) and item['id'] not in seen_failures:
                failures.append(item)
                seen_failures.add(item['id'])
            if len(failures) == 100:
                break
        sidebar = value.get('sidebar')
        if not self._valid_sidebar(sidebar):
            sidebar = dict(DEFAULT_SIDEBAR)
        return mode, folder, valid, failures, sidebar

    @staticmethod
    def _valid_sidebar(sidebar):
        return (isinstance(sidebar, dict) and set(sidebar) == {'width', 'hidden'}
                and type(sidebar['width']) is int and 248 <= sidebar['width'] <= 520
                and type(sidebar['hidden']) is bool)

    @staticmethod
    def _valid_failure(item):
        if not isinstance(item, dict) or item.get('action') not in ('retry', 'choose_output'):
            return False
        if any(not isinstance(item.get(key), str) or not item[key]
               for key in ('id', 'source', 'name', 'reason', 'failed_at')):
            return False
        if not Path(item['source']).is_absolute() or len(item['id']) != 32:
            return False
        try:
            int(item['id'], 16)
            datetime.fromisoformat(item['failed_at'])
        except ValueError:
            return False
        return True

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
                    mode, folder, history, failures, sidebar = self._read()
                except FileNotFoundError:
                    mode, folder, history, failures, sidebar = 'nearby', '', [], [], dict(DEFAULT_SIDEBAR)
                except (UnicodeError, ValueError):
                    backup = self.path.with_name(self.path.name + '.corrupt-' + uuid.uuid4().hex[:10])
                    shutil.copy2(self.path, backup)
                    mode, folder, history, failures, sidebar = 'nearby', '', [], [], dict(DEFAULT_SIDEBAR)
                if self._settings_changed:
                    mode, folder = self.output_mode, self.output_folder
                if self._sidebar_changed:
                    sidebar = dict(self.sidebar)
                current = {os.path.normcase(item['output']): item for item in history
                           if os.path.normcase(item['output']) not in self._forgotten_history}
                updates = []
                for key, item in reversed(list(self._pending_history.items())):
                    if key in current:
                        item = dict(item, finished_at=current[key]['finished_at'],
                                    imported=current[key].get('imported', False))
                    updates.append(item)
                merged = (updates + [item for key, item in current.items()
                                     if key not in self._pending_history])[:100]
                merged_failures = (list(reversed(list(self._pending_failures.values()))) +
                                   [item for item in failures if item['id'] not in self._pending_failures
                                    and item['id'] not in self._forgotten_failures])[:100]
                value = {'schema': 2, 'output_mode': mode, 'output_folder': folder, 'history': merged,
                         'failures': merged_failures, 'sidebar': sidebar}
                temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(temporary, self.path)
                self.history = merged
                self.failures = merged_failures
                self.sidebar = sidebar
                self._pending_history.clear()
                self._pending_failures.clear()
                self._forgotten_history.clear()
                self._forgotten_failures.clear()
                self._settings_changed = False
                self._sidebar_changed = False
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

    def set_sidebar(self, width, hidden):
        sidebar = {'width': width, 'hidden': hidden}
        if not self._valid_sidebar(sidebar):
            raise ValueError('Ширина боковой панели должна быть целым числом от 248 до 520, а скрытие — true или false.')
        self.sidebar = sidebar
        self._sidebar_changed = True
        saved = self.save()
        if not saved:
            self.warning = 'Не удалось сохранить вид боковой панели. В этом сеансе изменения продолжают работать.'
        return saved

    def destination(self, source):
        source = Path(source).resolve()
        if self.output_mode == 'nearby':
            return None
        parent = source.parent / 'Результаты' if self.output_mode == 'results' else Path(self.output_folder)
        return planned_output(source, parent)

    @property
    def output_label(self):
        return {'nearby': 'Рядом с исходным файлом', 'results': 'В папке «Результаты» рядом с исходным файлом',
                'custom': self.output_folder}[self.output_mode]

    def remember(self, report, processed=True, previous=None):
        output = str(Path(report['output']).resolve())
        key = os.path.normcase(output)
        previous = previous or next((item for item in self.history if os.path.normcase(item['output']) == key), None)
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
        if isinstance(report.get('source_sha256'), str):
            item['source_sha256'] = report['source_sha256']
        self.history = [item] + [entry for entry in self.history if os.path.normcase(entry['output']) != key]
        self.history = self.history[:100]
        self._pending_history.pop(key, None)
        self._pending_history[key] = item
        self._forgotten_history.discard(key)
        if processed:
            self.clear_failures(source, save=False)
        self.save()
        return item

    def forget(self, output, save=True):
        key = os.path.normcase(str(Path(output).resolve()))
        self.history = [item for item in self.history if os.path.normcase(item['output']) != key]
        self._pending_history.pop(key, None)
        self._forgotten_history.add(key)
        return self.save() if save else True

    def relocate(self, output, report):
        key = os.path.normcase(str(Path(output).resolve()))
        previous = next((item for item in self.history if os.path.normcase(item['output']) == key), None)
        if previous is None:
            raise ValueError('Результат не найден в истории.')
        self.forget(output, save=False)
        return self.remember(report, processed=False, previous=previous)

    def remember_failure(self, source, error, action='retry'):
        source = str(Path(source).resolve())
        id = hashlib.sha256(os.path.normcase(source).encode('utf-8')).hexdigest()[:32]
        item = {'id': id, 'source': source, 'name': Path(source).name,
                'reason': str(error) or 'Не удалось обработать PDF.', 'action': action,
                'failed_at': datetime.now().isoformat(timespec='seconds')}
        self.failures = [item] + [entry for entry in self.failures if entry['id'] != id]
        self.failures = self.failures[:100]
        self._pending_failures.pop(id, None)
        self._pending_failures[id] = item
        self._forgotten_failures.discard(id)
        self.save()
        return item

    def forget_failure(self, id, save=True):
        self.failures = [item for item in self.failures if item['id'] != id]
        self._pending_failures.pop(id, None)
        self._forgotten_failures.add(id)
        return self.save() if save else True

    def clear_failures(self, source, save=True):
        key = os.path.normcase(str(Path(source).resolve()))
        # Clear a failure written by another window even when this instance is stale.
        self.forget_failure(hashlib.sha256(key.encode('utf-8')).hexdigest()[:32], save=False)
        for item in self.failures[:]:
            if os.path.normcase(item['source']) == key:
                self.forget_failure(item['id'], save=False)
        return self.save() if save else True
