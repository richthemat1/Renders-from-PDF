"""Thread-safe desktop workspace and cancellable PDF queue, without a GUI dependency."""
import copy
import hashlib
import logging
import os
from pathlib import Path
import secrets
from threading import Event, RLock, Thread, current_thread
from time import monotonic

from photo_edit import can_undo_last_save, load_report
from photo_export import VERSION, OutputPathError, extract, planned_output
from photo_native import Cancelled, dropped_pdfs
from photo_state import WorkspaceState

LOGGER = logging.getLogger('pdf-photos')


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
    def __init__(self, state_path=None, extractor=None):
        self.workspace = WorkspaceState(state_path)
        self.extractor = extractor if extractor is not None else extract
        self._lock, self._cancel = RLock(), Event()
        self._thread, self._busy, self._closing = None, False, False
        self._has_job = False
        self._current_output = None
        self._status, self._detail = 'Ожидаю выбор файлов', 'Выберите PDF или перетащите файлы и папки в окно.'
        self._progress, self._found, self._next_log = 0, 0, 0
        self._totals = dict.fromkeys(('images', 'documents', 'unsigned', 'review'), 0)
        self._job = {'outcome': 'idle', 'total': 0, 'completed': 0, 'failed': 0,
                     'warnings': 0, 'errors': 0, 'active_file': None, 'outputs': []}
        self._logs = []
        self._pending_paths, self._pending_repeat = (), None
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
                             'label': self.workspace.output_label},
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
            paths = self._selection(paths)
            self._pending_paths, self._pending_repeat = (), None
            if not paths:
                return self.snapshot()
            previous = self.snapshot()['history']
            duplicates = []
            for source in paths:
                key = os.path.normcase(str(source))
                item = next((item for item in previous
                             if os.path.normcase(str(Path(item['source']).resolve())) == key), None)
                if item is not None:
                    duplicates.append({'name': source.name, 'source': str(source), 'previous_id': item['id'],
                                       'finished_at': item['finished_at'], 'available': item['available']})
            if duplicates:
                self._pending_paths = paths
                self._pending_repeat = {'token': secrets.token_urlsafe(24), 'files': duplicates}
                return self.snapshot()
            return self.start(paths)

    def confirm_repeat(self, token, proceed):
        with self._lock:
            if (not isinstance(token, str) or type(proceed) is not bool or self._pending_repeat is None
                    or not secrets.compare_digest(token, self._pending_repeat['token'])):
                raise ValueError('Подтверждение устарело. Выберите PDF ещё раз.')
            paths = self._pending_paths
            self._pending_paths, self._pending_repeat = (), None
            return self.start(paths) if proceed else self.snapshot()

    def _record_failure(self, source, error):
        action = ('choose_output' if isinstance(error, (OutputPathError, PermissionError))
                  or getattr(error, 'winerror', None) in (5, 206) else 'retry')
        self.workspace.remember_failure(source, error, action)
        self._append(f'Ошибка: {source.name} — {error}', 'error')
        if self.workspace.warning:
            self._append(self.workspace.warning, 'warn')

    def start(self, paths):
        with self._lock:
            paths = self._selection(paths)
            if not paths:
                return self.snapshot()
            self._pending_paths, self._pending_repeat = (), None
            self._cancel.clear()
            # outputs: result folders of this queue in order, so a batch lists each of its files.
            self._job = {'outcome': 'processing', 'total': len(paths), 'completed': 0, 'failed': 0,
                         'warnings': 0, 'errors': 0, 'active_file': None, 'outputs': []}
            self._current_output = None
            self._totals = dict.fromkeys(self._totals, 0)
            self._progress, self._found = 0, 0
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
            self._status, self._detail = 'Подготовка к обработке…', f'В очереди файлов: {len(paths)}'
            # Freeze queue settings; later files still get a fresh unused destination.
            self._thread = Thread(target=self._worker, args=(paths, settings, failed), daemon=True, name='pdf-photos-jobs')
            self._thread.start()
            return self.snapshot()

    def cancel(self):
        with self._lock:
            if self._busy and not self._cancel.is_set():
                self._cancel.set()
                self._status = 'Останавливаю обработку…'
                self._detail = 'Дождитесь завершения текущего изображения — незавершённая выгрузка будет удалена.'
                self._append('Запрошена отмена обработки.', 'warn')
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

    def set_sidebar(self, width, hidden):
        with self._lock:
            if self._closing:
                self._reject('Программа закрывается. Настройки недоступны.', RuntimeError)
            if not self.workspace.set_sidebar(width, hidden):
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
                raise ValueError('Папка недоступна. Нажмите «Указать новое расположение».')
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

    @staticmethod
    def _destination(source, settings):
        mode, folder = settings
        parent = (source.parent if mode == 'nearby' else
                  source.parent / 'Результаты' if mode == 'results' else Path(folder))
        return planned_output(source, parent)

    def _on_progress(self, info, source, index, total_files):
        with self._lock:
            if self._cancel.is_set():
                raise Cancelled()
            current, total, stage = info['current'], max(info['total'], 1), info['stage']
            if stage == 'labels':
                value, text = 12 * current / total, f'Чтение заголовков · страница {current} из {total}'
            elif stage == 'ocr':
                value, text = 12 + 13 * current / total, f'Распознавание скана · страница {current} из {total}'
            elif stage == 'images':
                value, text = 25 + 55 * (current - 1) / total, f'Сохранение изображений · страница {current} из {total}'
            elif stage == 'merge':
                value, text = 80 + 10 * current / total, f'Объединение PDF по вариантам · {current} из {total}'
            else:
                value, text = 90 + 9.7 * current / total, f'Создание просмотра · {current} из {total}'
            if stage in ('images', 'merge', 'export'):
                self._found = max(self._found, max(0, int(info.get('found', 0))))
            self._progress = max(self._progress, 100 * (index - 1 + value / 100) / total_files)
            self._status = f'Обработка PDF · {index} из {total_files}'
            self._detail = f'{source.name} · {text}'

    def _worker(self, paths, settings, preflight_failed=0):
        successes, partial, started = 0, preflight_failed, monotonic()
        try:
            for index, source in enumerate(paths, 1):
                if self._cancel.is_set():
                    break
                with self._lock:
                    self._found = 0
                    self._job['active_file'] = {'name': source.name, 'index': index, 'total': len(paths)}
                    self._status, self._detail = f'Обработка PDF · {index} из {len(paths)}', source.name
                    self._append(f'Файл {index} из {len(paths)}: {source.name}', 'head')
                try:
                    LOGGER.info('Processing %s', source)
                    report = self.extractor(source, output=self._destination(source, settings),
                                            progress=lambda info: self._on_progress(info, source, index, len(paths)))
                    report = normalized_report(report)
                    with self._lock:
                        self.remember(report, processed=True)
                        counts = report['counts_by_status']
                        self._totals['images'] += sum(not image.get('removed') for image in report['images'])
                        self._totals['documents'] += len(report['documents'])
                        self._totals['unsigned'] += counts.get('unsigned', 0)
                        self._totals['review'] += counts.get('review', 0)
                        self._found = 0
                        successes += 1
                        self._job['completed'] += 1
                        self._job['outputs'].append(report['output'])
                        self._job['warnings'] += len(report['warnings'])
                        self._job['errors'] += len(report['errors'])
                        partial += bool(report['errors'])
                        # Not «N PDF из M изображений»: a variant joins many images into one PDF.
                        self._append(f'Сохранено: изображений — {sum(not image.get("removed") for image in report["images"])}, '
                                     f'готовых PDF — {len(report["documents"])}.', 'ok')
                        for key, title in (('unsigned', 'без подписи'), ('review', 'на проверку')):
                            if counts.get(key):
                                self._append(f'Проверьте вручную: {title} — {counts[key]}.', 'warn')
                        if report['warnings']:
                            self._append(f'Предупреждений: {len(report["warnings"])}. Подробности — в отчёте.', 'warn')
                        if report['errors']:
                            self._append(f'Не сохранено изображений: {len(report["errors"])}. Причины — в «Отчёт.json».', 'error')
                        if report.get('preview_errors'):
                            self._append(f'Не создано миниатюр: {len(report["preview_errors"])}. Сами PDF сохранены.', 'warn')
                        self._append(report['output'], 'muted')
                except Cancelled:
                    self._cancel.set()
                    break
                except Exception as error:
                    LOGGER.exception('Failed: %s', source)
                    with self._lock:
                        self._job['failed'] += 1
                        self._record_failure(source, error)
                finally:
                    with self._lock:
                        self._found = 0
                        if not self._cancel.is_set():
                            self._progress = 100 * index / len(paths)
        finally:
            with self._lock:
                self._busy, self._found = False, 0
                cancelled = self._cancel.is_set()
                self._progress = 100 if successes and not cancelled else 0
                if cancelled:
                    self._job['outcome'] = 'cancelled'
                    self._status = f'Остановлено. Готово файлов: {successes} из {len(paths)}'
                    self._detail = 'Полностью обработанные файлы сохранены. Все результаты доступны в истории.'
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
                self._append(f'Завершено за {seconds // 60}:{seconds % 60:02d}.', 'muted')
