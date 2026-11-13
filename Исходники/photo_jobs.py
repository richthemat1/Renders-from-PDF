"""Thread-safe desktop workspace and cancellable PDF queue, without a GUI dependency."""
from collections import deque
import copy
import hashlib
import logging
import multiprocessing
import os
from pathlib import Path
import pickle
from queue import Empty, Queue
import secrets
from threading import Event, RLock, Thread, current_thread
from time import monotonic
import traceback

from photo_edit import can_undo_last_save, load_report
from photo_delivery import sync as sync_delivery
from photo_export import VERSION, OutputPathError, extract, planned_output
from photo_native import Cancelled, available_memory, dropped_pdfs, lower_priority, process_memory
from photo_state import WorkspaceState

LOGGER = logging.getLogger('pdf-photos')
# Most PDFs of a queue that run at once: each takes a core, a heavy one also its page workers.
MAX_PARALLEL = 4
# Memory one more PDF is given room for. At their peak eleven presentations of 17–117 pages took 0.3–2.5 GB:
# a deck with sheets of drawings 2.4 GB in its own process, another 2.5 GB in the workers reading its pages.
FILE_MEMORY = 2_500_000_000


def parallel_limit():
    """PDFs of a queue that run at once: one for every two logical processors, at most MAX_PARALLEL."""
    return max(1, min(MAX_PARALLEL, (os.cpu_count() or 2) // 2))


def stage_progress(info):
    """The share of one PDF done, 0–100, and the words for its current stage."""
    current, total, stage = info['current'], max(info['total'], 1), info['stage']
    if stage == 'labels':
        return 12 * current / total, f'Чтение заголовков · страница {current} из {total}'
    if stage == 'ocr':
        return 12 + 13 * current / total, f'Распознавание скана · страница {current} из {total}'
    if stage == 'images':
        return 25 + 55 * (current - 1) / total, f'Сохранение изображений · страница {current} из {total}'
    if stage == 'merge':
        return 80 + 10 * current / total, f'Объединение PDF по вариантам · {current} из {total}'
    return 90 + 9.7 * current / total, f'Создание просмотра · {current} из {total}'


def _portable(error):
    """The error itself when it reaches the window's process intact, else an error with its text."""
    try:
        pickle.loads(pickle.dumps(error))
        return error
    except Exception:
        return RuntimeError(str(error))


def _finish(slot, events, run):
    """Send the outcome of one PDF: its report, the stop, or the error with its traceback."""
    try:
        events.put(('done', slot, run()))
    except Cancelled:
        events.put(('cancelled', slot, None))
    except BaseException as error:
        events.put(('failed', slot, (_portable(error), traceback.format_exc())))


def process_file(slot, source, output, events, stop):
    """One PDF of a queue in a process of its own: its own core, and all its memory back when it ends."""
    lower_priority()

    def progress(info):
        if stop.is_set():
            raise Cancelled()
        events.put(('progress', slot, info))
    _finish(slot, events, lambda: extract(source, output=output, progress=progress))


def history_id(output):
    """Persist no extra identifier: the same normalized result path always has the same ID."""
    normalized = os.path.normcase(str(Path(output).resolve()))
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:32]


def normalized_report(report, folder=None):
    """Keep the original report format while repairing the location of a moved export."""
    if not isinstance(report, dict) or not isinstance(report.get('source'), str) or not report['source']:
        raise ValueError('В папке находится неполный отчёт обработки: нет исходного PDF.')
    for key in ('images', 'documents'):
        if not isinstance(report.get(key), list) or any(not isinstance(item, dict) for item in report[key]):
            raise ValueError('В папке находится неполный отчёт обработки: нет списка изображений или PDF.')
    if not isinstance(report.get('counts_by_status'), dict):
        raise ValueError('В папке находится неполный отчёт обработки: нет статистики изображений.')
    if any(type(value) is not int or value < 0 for value in report['counts_by_status'].values()):
        raise ValueError('В отчёте обработки указана некорректная статистика изображений.')
    output = folder if folder is not None else report.get('output')
    if not output:
        raise ValueError('В отчёте обработки не указана папка результата.')
    result = copy.deepcopy(report)
    result['output'] = str(Path(output).resolve())
    result.setdefault('revision', 0)
    result.setdefault('counts_by_folder', {})
    result.setdefault('errors', [])
    result.setdefault('warnings', [])
    result['can_undo_save'] = can_undo_last_save(result)
    return result


def read_result(folder):
    return normalized_report(load_report(Path(folder).resolve()), folder)


class JobService:
    def __init__(self, state_path=None, extractor=None, workers=None, processes=False):
        """`processes`: the real extractor runs each PDF in a process of its own, parallel_limit() at once;
        otherwise PDFs run in threads of this process, one at a time. `workers` sets how many run at once."""
        self.workspace = WorkspaceState(state_path)
        self.extractor = extractor if extractor is not None else extract
        self.workers, self.processes = workers, processes
        self._lock, self._cancel = RLock(), Event()
        self._thread, self._busy, self._closing = None, False, False
        self._has_job = False
        self._current_output = None
        self._status, self._detail = 'Ожидаю выбор файлов', 'Выберите PDF или перетащите файлы и папки в окно.'
        self._progress, self._found, self._next_log = 0, 0, 0
        self._totals = dict.fromkeys(('images', 'documents', 'unsigned', 'review'), 0)
        self._job = {'outcome': 'idle', 'total': 0, 'completed': 0, 'failed': 0,
                     'warnings': 0, 'errors': 0, 'active_file': None, 'active_files': [], 'outputs': []}
        # Files in work by queue slot; result folders they will take; files of the queue done with.
        self._active, self._reserved, self._handled, self._queue_size = {}, set(), 0, 0
        # The queue's files and the slots already started: the rest wait, and the window lists them.
        self._queue_paths, self._started_slots = (), set()
        self._logs = []
        self._pending_paths, self._pending_repeat = (), None
        # Files a stopped queue did not reach, so «Продолжить оставшиеся» needs no new selection.
        self._remaining = ()
        if self.workspace.warning:
            self._append(self.workspace.warning, 'warn')

    @property
    def busy(self):
        with self._lock:
            return self._busy

    @property
    def current_output(self):
        with self._lock:
            return self._current_output

    def _append(self, text, level='info'):
        self._next_log += 1
        self._logs.append({'id': self._next_log, 'text': str(text), 'level': level})
        self._logs = self._logs[-500:]

    def _fresh_log(self, mark):
        """A new selection starts its own list of events; what was said about this selection stays."""
        self._logs = [item for item in self._logs if item['id'] > mark]

    def _reject(self, message, error=ValueError):
        self._status, self._detail = 'Ошибка: ' + str(message), str(message)
        self._append(message, 'error')
        raise error(message)

    def snapshot(self):
        with self._lock:
            history = []
            for item in self.workspace.history:
                folder = Path(item['output'])
                try:
                    available = folder.is_dir() and (folder / 'Отчёт.json').is_file()
                except OSError:
                    available = False
                history.append(dict(item, id=history_id(folder), available=available))
            totals = dict(self._totals)
            totals['images'] += self._found
            return copy.deepcopy({
                'version': VERSION, 'busy': self._busy, 'has_job': self._has_job,
                'job': self._job,
                'cancelling': self._busy and self._cancel.is_set(),
                'closing': self._closing, 'status': self._status, 'detail': self._detail,
                'progress': min(100, max(0, self._progress)), 'totals': totals, 'logs': self._logs,
                'settings': {'mode': self.workspace.output_mode, 'folder': self.workspace.output_folder,
                             'label': self.workspace.output_label,
                             'delivery': self.workspace.delivery_folder},
                'sidebar': self.workspace.sidebar,
                'history': history, 'failures': self.workspace.failures, 'pending_repeat': self._pending_repeat,
                'current_output': self._current_output, 'warning': self.workspace.warning})

    def _selection(self, paths):
        if self._closing:
            self._reject('Программа закрывается. Повторный запуск обработки недоступен.', RuntimeError)
        if self._busy:
            self._reject('Уже идёт обработка. Дождитесь её завершения или отмените очередь.', RuntimeError)
        if not paths:
            return ()
        try:
            paths, skipped = dropped_pdfs(paths)
            paths = tuple(dict.fromkeys(Path(path).resolve() for path in paths))
        except (OSError, TypeError, ValueError) as error:
            self._reject(f'Не удалось прочитать выбранные файлы: {error}')
        if skipped:
            self._append('Пропущено, это не PDF: ' + ', '.join(skipped), 'warn')
        if not paths:
            self._reject('Не найдено PDF для обработки. Выберите PDF или папку с PDF.')
        return paths

    def prepare(self, paths):
        """Native-selected paths stay here until a metadata-only duplicate prompt is resolved."""
        with self._lock:
            mark = self._next_log
            paths = self._selection(paths)
            self._pending_paths, self._pending_repeat = (), None
            if not paths:
                return self.snapshot()
            self._fresh_log(mark)
            previous = self.snapshot()['history']
            duplicates, fresh = [], []
            for source in paths:
                key = os.path.normcase(str(source))
                item = next((item for item in previous
                             if os.path.normcase(str(Path(item['source']).resolve())) == key), None)
                if item is not None:
                    duplicates.append({'name': source.name, 'source': str(source), 'previous_id': item['id'],
                                       'finished_at': item['finished_at'], 'available': item['available']})
                else:
                    fresh.append(source.name)
            if duplicates:
                self._pending_paths = paths
                # `new`: files of the same selection never processed; the prompt says what happens to them.
                self._pending_repeat = {'token': secrets.token_urlsafe(24), 'files': duplicates, 'new': fresh}
                return self.snapshot()
            return self.start(paths, fresh=False)

    def confirm_repeat(self, token, proceed):
        """`proceed`: True processes every file again, 'new' only the files never processed, False nothing."""
        with self._lock:
            if (not isinstance(token, str) or (type(proceed) is not bool and proceed != 'new')
                    or self._pending_repeat is None
                    or not secrets.compare_digest(token, self._pending_repeat['token'])):
                raise ValueError('Подтверждение устарело. Выберите PDF ещё раз.')
            paths = self._pending_paths
            repeated = {os.path.normcase(item['source']) for item in self._pending_repeat['files']}
            self._pending_paths, self._pending_repeat = (), None
            if proceed == 'new':
                paths = tuple(path for path in paths if os.path.normcase(str(path)) not in repeated)
            return self.start(paths, fresh=False) if proceed and paths else self.snapshot()

    def resume(self):
        """Process again the files a stopped queue did not finish, with the settings of today."""
        with self._lock:
            if self._busy or not self._remaining:
                raise ValueError('Нет файлов, оставшихся после остановки. Выберите PDF ещё раз.')
            return self.start(self._remaining)

    def _record_failure(self, source, error):
        action = ('choose_output' if isinstance(error, (OutputPathError, PermissionError))
                  or getattr(error, 'winerror', None) in (5, 206) else 'retry')
        self.workspace.remember_failure(source, error, action)
        self._append(f'Ошибка: {source.name} — {error}', 'error')
        if self.workspace.warning:
            self._append(self.workspace.warning, 'warn')

    def start(self, paths, fresh=True):
        """`fresh`: the events of earlier queues go; prepare() has already done it for its selection."""
        with self._lock:
            mark = self._next_log
            paths = self._selection(paths)
            if not paths:
                return self.snapshot()
            if fresh:
                self._fresh_log(mark)
            self._pending_paths, self._pending_repeat = (), None
            self._remaining = ()
            self._cancel.clear()
            # outputs: result folders of this queue in order, so a batch lists each of its files.
            self._job = {'outcome': 'processing', 'total': len(paths), 'completed': 0, 'failed': 0,
                         'warnings': 0, 'errors': 0, 'active_file': None, 'active_files': [], 'outputs': [],
                         'remaining': 0}
            self._current_output = None
            self._totals = dict.fromkeys(self._totals, 0)
            self._progress, self._found = 0, 0
            self._active, self._reserved = {}, set()
            settings = self.workspace.output_mode, self.workspace.output_folder
            valid = []
            for source in paths:
                try:
                    self._destination(source, settings)
                    valid.append(source)
                except (OSError, ValueError) as error:
                    self._record_failure(source, error)
            failed = len(paths) - len(valid)
            self._job['failed'] = failed
            if not valid:
                self._job['outcome'] = 'failed'
                self._status = 'Ошибка: PDF не обработан'
                self._detail = 'Не удалось подготовить папку результата. Причины и действия — в списке «Не обработано».'
                return self.snapshot()
            paths = tuple(valid)
            self._busy = True
            self._has_job = True
            self._handled, self._queue_size = 0, len(paths)
            self._queue_paths, self._started_slots = paths, set()
            self._status, self._detail = 'Подготовка к обработке…', f'В очереди файлов: {len(paths)}'
            limit = self.workers or (parallel_limit() if self.processes else 1)
            # Freeze queue settings; later files still get a fresh unused destination.
            self._thread = Thread(target=self._worker, args=(paths, settings, failed, limit),
                                  daemon=True, name='pdf-photos-jobs')
            self._thread.start()
            return self.snapshot()

    def cancel(self):
        with self._lock:
            if self._busy and not self._cancel.is_set():
                self._cancel.set()
                self._status = 'Останавливаю обработку…'
                self._detail = ('Дождитесь завершения текущих изображений — незавершённые выгрузки будут удалены.'
                                if len(self._active) > 1 else
                                'Дождитесь завершения текущего изображения — незавершённая выгрузка будет удалена.')
                self._append('Обработка останавливается…', 'warn')
            return self.snapshot()

    def wait(self, timeout=None):
        with self._lock:
            worker = self._thread
        if worker is not None and worker is not current_thread():
            worker.join(timeout)
        return self.snapshot()

    def close(self):
        with self._lock:
            self._closing = True
            self._pending_paths, self._pending_repeat = (), None
            self.cancel()
        return self.wait()

    def set_output(self, mode, folder=None):
        with self._lock:
            if self._closing:
                self._reject('Программа закрывается. Настройки недоступны.', RuntimeError)
            if self._busy:
                self._reject('Способ сохранения нельзя менять во время обработки.', RuntimeError)
            try:
                self.workspace.set_output(mode, folder)
            except (OSError, ValueError, TypeError) as error:
                self._reject(str(error))
            if self.workspace.warning:
                self._append(self.workspace.warning, 'warn')
            return self.snapshot()

    def set_delivery(self, folder=None):
        with self._lock:
            if self._closing:
                self._reject('Программа закрывается. Настройки недоступны.', RuntimeError)
            try:
                self.workspace.set_delivery(folder)
            except (OSError, ValueError, TypeError) as error:
                self._reject(str(error))
            if self.workspace.warning:
                self._append(self.workspace.warning, 'warn')
            return self.snapshot()

    def deliver(self, report):
        """Update the clean copies of a saved result's variants; returns what was written and what failed."""
        with self._lock:
            folder = self.workspace.delivery_folder
        try:
            return sync_delivery(report['output'], report, folder or None)
        except Exception as error:
            # Copies are a convenience: the result itself is already saved.
            LOGGER.exception('Не удалось обновить готовые варианты: %s', report.get('output'))
            return {'saved': [], 'errors': [f'Готовые варианты не обновлены: {error}.']}

    def set_sidebar(self, width, hidden, zoom=None):
        with self._lock:
            if self._closing:
                self._reject('Программа закрывается. Настройки недоступны.', RuntimeError)
            if not self.workspace.set_sidebar(width, hidden, zoom):
                self._append(self.workspace.warning, 'warn')
                raise RuntimeError(self.workspace.warning)
            return dict(self.workspace.sidebar)

    def remember(self, report, processed=False):
        report = normalized_report(report)
        with self._lock:
            item = self.workspace.remember(report, processed=processed)
            if processed:
                self._current_output = report['output']
            if self.workspace.warning:
                self._append(self.workspace.warning, 'warn')
            return dict(item, id=history_id(item['output']), available=(Path(item['output']) / 'Отчёт.json').is_file())

    def import_result(self, folder):
        with self._lock:
            if self._closing:
                self._reject('Программа закрывается. Открытие результата недоступно.', RuntimeError)
            report = read_result(folder)
            self.remember(report)
            return report

    def history_result(self, id):
        with self._lock:
            item = self._history_item(id)
            return self.import_result(item['output'])

    def _history_item(self, id):
        item = next((item for item in self.workspace.history if history_id(item['output']) == id), None)
        if item is None:
            raise ValueError('Результат не найден в истории. Выберите папку результата через приложение.')
        return item

    def history_folder(self, id):
        with self._lock:
            folder = Path(self._history_item(id)['output']).resolve()
            if not folder.is_dir():
                raise ValueError('Папка результата не найдена: её переместили или удалили. Нажмите «Указать папку…» и выберите её новое место.')
            return folder

    def forget_history(self, id):
        with self._lock:
            item = self._history_item(id)
            self.workspace.forget(item['output'])
            if self._current_output == item['output']:
                self._current_output = None
            return self.snapshot()

    def repeat_history(self, id):
        with self._lock:
            source = Path(self._history_item(id)['source'])
            if not source.is_file():
                raise ValueError('Исходный PDF недоступен. Выберите его заново через «Выбрать PDF».')
            return self.prepare([source])

    def relocate_history(self, id, folder):
        with self._lock:
            item = self._history_item(id)
            report = read_result(folder)
            same_source = (os.path.normcase(str(Path(item['source']).resolve())) ==
                           os.path.normcase(str(Path(report['source']).resolve())))
            same_hash = (item.get('source_sha256') and item['source_sha256'] == report.get('source_sha256'))
            if not same_source and not same_hash:
                raise ValueError('В этой папке результат другого PDF. Выберите перенесённую папку нужного результата.')
            self.workspace.relocate(item['output'], report)
            if self._current_output is not None and history_id(self._current_output) == id:
                self._current_output = report['output']
            return report

    def retry_failure(self, id):
        with self._lock:
            item = next((item for item in self.workspace.failures if item['id'] == id), None)
            if item is None:
                raise ValueError('Сбой уже устранён или убран из списка.')
            source = Path(item['source'])
            if not source.is_file():
                raise ValueError('Исходный PDF недоступен. Выберите его заново через «Выбрать PDF».')
            return self.prepare([source])

    def forget_failure(self, id):
        with self._lock:
            if not any(item['id'] == id for item in self.workspace.failures):
                raise ValueError('Сбой уже устранён или убран из списка.')
            self.workspace.forget_failure(id)
            return self.snapshot()

    def _destination(self, source, settings):
        mode, folder = settings
        parent = (source.parent if mode == 'nearby' else
                  source.parent / 'Результаты' if mode == 'results' else Path(folder))
        # Two PDFs of one name processed at once must not plan the same folder before either exists.
        return planned_output(source, parent, reserved=self._reserved)

    def _show_active(self):
        """Progress, status line and the list of files in work, from the stage of each file."""
        files = sorted(self._active.values(), key=lambda item: item['index'])
        self._found = sum(item['found'] for item in files)
        done = self._handled + sum(item['progress'] for item in files) / 100
        self._progress = max(self._progress, 100 * done / max(self._queue_size, 1))
        self._job['active_files'] = [{'name': item['name'], 'index': item['index'], 'total': item['total'],
                                      'phase': item['phase'], 'progress': round(item['progress'])} for item in files]
        if files:
            self._job['active_file'] = {key: files[0][key] for key in ('name', 'index', 'total')}
        waiting = [path.name for slot, path in enumerate(self._queue_paths) if slot not in self._started_slots]
        if waiting:
            self._job['waiting'] = waiting
        else:
            self._job.pop('waiting', None)
        lines = [f'{item["name"]} · {item["phase"]}' if item['phase'] else item['name'] for item in files]
        if len(files) == 1:
            self._status, self._detail = f'Обработка PDF · {files[0]["index"]} из {files[0]["total"]}', lines[0]
        elif files:
            self._status = f'Обработка PDF · одновременно {len(files)} из {self._queue_size}'
            self._detail = '; '.join(lines)

    def _apply_progress(self, slot, info):
        value, text = stage_progress(info)
        item = self._active[slot]
        item['progress'], item['phase'] = max(item['progress'], value), text
        if info['stage'] in ('images', 'merge', 'export'):
            item['found'] = max(item['found'], max(0, int(info.get('found', 0))))
        self._show_active()

    def _thread_progress(self, slot, info):
        with self._lock:
            if self._cancel.is_set():
                raise Cancelled()
            self._apply_progress(slot, info)

    @staticmethod
    def _memory_allows(running):
        """Another PDF starts only if memory stays for it once the running ones reach their peak."""
        free = available_memory()
        if free is None:
            return True
        for handle in running.values():
            used = process_memory(handle.pid) if getattr(handle, 'pid', None) else None
            free -= max(0, FILE_MEMORY - (used or 0))
        return free >= FILE_MEMORY

    def _launch(self, slot, paths, settings, events, stop, parallel):
        """Start one PDF of the queue; a folder that cannot be planned now fails only this file."""
        source = paths[slot]
        with self._lock:
            self._started_slots.add(slot)
            try:
                output = self._destination(source, settings)
            except Exception as error:
                LOGGER.exception('Failed: %s', source)
                self._job['failed'] += 1
                self._handled += 1
                self._record_failure(source, error)
                self._show_active()
                return None
            self._reserved.add(os.path.normcase(str(output)))
            self._active[slot] = {'name': source.name, 'index': slot + 1, 'total': len(paths), 'phase': '',
                                  'progress': 0, 'found': 0, 'output': output}
            if parallel:
                self._append(f'Начата обработка: {source.name}', 'muted')
            else:
                self._append(f'Файл {slot + 1} из {len(paths)}: {source.name}', 'head')
            self._show_active()
        LOGGER.info('Processing %s', source)
        if stop is not None:
            # Not a daemon: a heavy PDF reads its pages in worker processes of its own, which a daemon may
            # not start. The queue stops and joins every process it started before the window closes.
            handle = multiprocessing.get_context('spawn').Process(
                target=process_file, args=(slot, source, output, events, stop),
                daemon=False, name=f'pdf-photos-{slot + 1}')
        else:
            def run():
                return self.extractor(source, output=output, progress=lambda info: self._thread_progress(slot, info))
            handle = Thread(target=_finish, args=(slot, events, run), daemon=True, name=f'pdf-photos-{slot + 1}')
        handle.start()
        return handle

    def _saved(self, slot, paths, report, parallel, outputs):
        """Record a finished PDF and return whether it lost images; an unusable report raises."""
        source = paths[slot]
        report = normalized_report(report)
        delivered = self.deliver(report)
        with self._lock:
            self.remember(report, processed=True)
            counts = report['counts_by_status']
            self._totals['images'] += sum(not image.get('removed') for image in report['images'])
            self._totals['documents'] += len(report['documents'])
            self._totals['unsigned'] += counts.get('unsigned', 0)
            self._totals['review'] += counts.get('review', 0)
            self._job['completed'] += 1
            outputs[slot] = report['output']
            self._job['outputs'] = [outputs[key] for key in sorted(outputs)]
            self._job['warnings'] += len(report['warnings'])
            self._job['errors'] += len(report['errors'])
            if parallel:
                # Files of one queue finish in any order: each block of results names its file.
                self._append(f'Файл {slot + 1} из {len(paths)}: {source.name}', 'head')
            # Not «N PDF из M изображений»: a variant joins many images into one PDF.
            self._append(f'Сохранено: изображений — {sum(not image.get("removed") for image in report["images"])}, '
                         f'готовых PDF — {len(report["documents"])}.', 'ok')
            for key, title in (('unsigned', 'без подписи'), ('review', 'на проверку')):
                if counts.get(key):
                    self._append(f'Проверьте вручную: {title} — {counts[key]}.', 'warn')
            if report['warnings']:
                self._append(f'Предупреждений: {len(report["warnings"])}. Подробности — на панели проверки в открытом результате.', 'warn')
            if report['errors']:
                self._append(f'Не сохранено изображений: {len(report["errors"])}. Причины — на панели проверки в открытом результате.', 'error')
            if report.get('preview_errors'):
                self._append(f'Не создано миниатюр: {len(report["preview_errors"])}. Сами PDF сохранены.', 'warn')
            self._append(f'Папка результата: {report["output"]}', 'muted')
            if delivered['saved']:
                self._append(f'Готовые варианты — {len(delivered["saved"])} PDF: '
                             f'{Path(delivered["saved"][0]).parent}', 'ok')
            for message in delivered['errors']:
                self._append(message, 'warn')
        return bool(report['errors'])

    def _worker(self, paths, settings, preflight_failed=0, limit=1):
        """Run the queue: up to `limit` PDFs at once, the real extractor each in a process of its own."""
        successes, partial, started = 0, preflight_failed, monotonic()
        processes, parallel = self.processes and self.extractor is extract, limit > 1 and len(paths) > 1

        def size(path):
            try:
                return path.stat().st_size
            except OSError:
                return 0
        # The largest PDF first: a queue ends sooner when its longest file does not start last.
        slots = sorted(range(len(paths)), key=lambda slot: -size(paths[slot])) if parallel else range(len(paths))
        waiting = deque(slots)
        context = multiprocessing.get_context('spawn')
        events = context.Queue() if processes else Queue()
        stop = context.Event() if processes else None
        running, finished, outputs, silent = {}, set(), {}, {}
        try:
            while waiting or running:
                if self._cancel.is_set():
                    waiting.clear()
                    if stop is not None:
                        stop.set()
                while waiting and len(running) < limit and (not running or self._memory_allows(running)):
                    slot = waiting.popleft()
                    handle = self._launch(slot, paths, settings, events, stop, parallel)
                    if handle is None:
                        finished.add(slot)
                    else:
                        running[slot] = handle
                if not running:
                    continue
                # A process that ends without a word was stopped by Windows, as when memory ran out. Its last
                # message, if it sent one, arrives well within the grace time while the others keep reporting.
                for slot, handle in running.items():
                    if stop is not None and handle.exitcode is not None:
                        silent.setdefault(slot, monotonic())
                lost = next((slot for slot, since in silent.items() if slot in running and monotonic() - since > 2), None)
                if lost is not None:
                    kind, slot, value = 'failed', lost, (RuntimeError(
                        f'Обработка прервалась: процесс завершился с кодом {running[lost].exitcode}. '
                        'Возможно, не хватило памяти — обработайте этот PDF отдельно.'), '')
                else:
                    try:
                        kind, slot, value = events.get(timeout=.25)
                    except Empty:
                        continue
                if kind == 'progress':
                    with self._lock:
                        if slot in self._active:
                            self._apply_progress(slot, value)
                    continue
                running.pop(slot).join(5)
                if kind == 'done':
                    try:
                        partial += self._saved(slot, paths, value, parallel, outputs)
                        successes += 1
                    except Exception as error:
                        kind, value = 'failed', (error, traceback.format_exc())
                if kind == 'failed':
                    error, trace = value
                    LOGGER.error('Failed: %s\n%s', paths[slot], trace or error)
                    with self._lock:
                        self._job['failed'] += 1
                        self._record_failure(paths[slot], error)
                with self._lock:
                    item = self._active.pop(slot, None)
                    if item is not None:
                        self._reserved.discard(os.path.normcase(str(item['output'])))
                    if kind != 'cancelled':
                        finished.add(slot)
                        self._handled += 1
                    self._show_active()
        finally:
            if running:
                # Only a fault here leaves PDFs running: they stop at their next step and remove their work.
                if stop is not None:
                    stop.set()
                for handle in running.values():
                    handle.join(30)
                    if stop is not None and handle.is_alive():
                        handle.terminate()
            with self._lock:
                self._busy, self._found = False, 0
                self._active.clear()
                self._job['active_files'] = []
                # Only a running queue has files waiting; a finished one shows none.
                self._job.pop('waiting', None)
                cancelled = self._cancel.is_set()
                if outputs:
                    # «Открыть результат» opens the last file of the queue, in whatever order the files finished.
                    self._current_output = outputs[max(outputs)]
                # A stopped queue shows the share of files it saved, as its status line counts them.
                self._progress = (100 * successes / len(paths) if cancelled else 100 if successes else 0)
                if cancelled:
                    self._remaining = tuple(path for slot, path in enumerate(paths) if slot not in finished)
                    self._job['remaining'] = len(self._remaining)
                    self._job['outcome'] = 'cancelled'
                    self._status = f'Остановлено. Готово файлов: {successes} из {len(paths)}'
                    self._detail = (f'Готовые результаты ({successes}) — в истории.' if successes else
                                    'Ни один файл не успел обработаться.')
                elif successes == len(paths) and not partial:
                    self._job['outcome'] = 'complete'
                    self._status = f'Готово. Обработано файлов: {successes} из {len(paths)}'
                    self._detail = 'Откройте результат, чтобы проверить варианты и материалы без подписи.'
                elif successes:
                    self._job['outcome'] = 'partial'
                    self._status = 'Готово частично. Часть файлов или изображений не сохранена'
                    self._detail = 'Готовые результаты доступны в истории. Причины и действия — в списке «Не обработано».'
                else:
                    self._job['outcome'] = 'failed'
                    self._status, self._detail = 'Ошибка: PDF не обработан', 'Причины и действия — в списке «Не обработано».'
                seconds = round(monotonic() - started)
                self._append(f'{"Остановлено" if cancelled else "Завершено"} за {seconds // 60}:{seconds % 60:02d}.', 'muted')
