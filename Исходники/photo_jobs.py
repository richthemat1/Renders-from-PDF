"""Thread-safe desktop workspace and cancellable PDF queue, without a GUI dependency."""
import copy
import hashlib
import logging
import os
from pathlib import Path
from threading import Event, RLock, Thread, current_thread
from time import monotonic

from photo_edit import load_report
from photo_export import VERSION, extract, safe_name, unused_folder
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
    return result


def read_result(folder):
    return normalized_report(load_report(Path(folder).resolve()), folder)


class JobService:
    def __init__(self, state_path=None, extractor=None):
        self.workspace = WorkspaceState(state_path)
        self.extractor = extractor if extractor is not None else extract
        self._lock, self._cancel = RLock(), Event()
        self._thread, self._busy, self._closing = None, False, False
        self._current_output = None
        self._status, self._detail = 'Ожидаю выбор файлов', 'Выберите PDF или перетащите файлы и папки в окно.'
        self._progress, self._found, self._next_log = 0, 0, 0
        self._totals = dict.fromkeys(('images', 'documents', 'unsigned', 'review'), 0)
        self._logs = []
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
                'version': VERSION, 'busy': self._busy, 'cancelling': self._busy and self._cancel.is_set(),
                'closing': self._closing, 'status': self._status, 'detail': self._detail,
                'progress': min(100, max(0, self._progress)), 'totals': totals, 'logs': self._logs,
                'settings': {'mode': self.workspace.output_mode, 'folder': self.workspace.output_folder,
                             'label': self.workspace.output_label},
                'history': history, 'current_output': self._current_output, 'warning': self.workspace.warning})

    def start(self, paths):
        with self._lock:
            if self._closing:
                self._reject('Программа закрывается. Повторный запуск обработки недоступен.', RuntimeError)
            if self._busy:
                self._reject('Уже идёт обработка. Дождитесь её завершения или отмените очередь.', RuntimeError)
            if not paths:
                return self.snapshot()
            try:
                paths, skipped = dropped_pdfs(paths)
                paths = tuple(dict.fromkeys(Path(path).resolve() for path in paths))
            except (OSError, TypeError, ValueError) as error:
                self._reject(f'Не удалось прочитать выбранные файлы: {error}')
            if skipped:
                self._append('Пропущено, это не PDF: ' + ', '.join(skipped), 'warn')
            if not paths:
                self._reject('Не найдено PDF для обработки. Выберите PDF или папку с PDF.')
            self._cancel.clear()
            self._busy, self._current_output = True, None
            self._totals = dict.fromkeys(self._totals, 0)
            self._progress, self._found = 0, 0
            self._status, self._detail = 'Подготовка к обработке…', f'В очереди файлов: {len(paths)}'
            # Freeze queue settings; later files still get a fresh unused destination.
            settings = self.workspace.output_mode, self.workspace.output_folder
            self._thread = Thread(target=self._worker, args=(paths, settings), daemon=True, name='pdf-photos-jobs')
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

    def remember(self, report, processed=False):
        report = normalized_report(report)
        with self._lock:
            item = self.workspace.remember(report, processed=processed)
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
            item = next((item for item in self.workspace.history if history_id(item['output']) == id), None)
            if item is None:
                raise ValueError('Результат не найден в истории. Выберите папку результата через приложение.')
            return self.import_result(item['output'])

    @staticmethod
    def _destination(source, settings):
        mode, folder = settings
        if mode == 'nearby':
            return None
        parent = source.parent / 'Результаты' if mode == 'results' else Path(folder)
        return unused_folder(parent / (safe_name(source.stem, 78) + ' — PDF'))

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

    def _worker(self, paths, settings):
        successes, partial, started = 0, 0, monotonic()
        try:
            for index, source in enumerate(paths, 1):
                if self._cancel.is_set():
                    break
                with self._lock:
                    self._found = 0
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
                        partial += bool(report['errors'])
                        self._append(f'Сохранено {len(report["documents"])} PDF из '
                                     f'{sum(not image.get("removed") for image in report["images"])} изображений.', 'ok')
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
                        self._append(f'Ошибка: {source.name} — {error}', 'error')
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
                    self._status = f'Остановлено. Готово файлов: {successes} из {len(paths)}'
                    self._detail = 'Полностью обработанные файлы сохранены. Все результаты доступны в истории.'
                elif successes == len(paths) and not partial:
                    self._status = f'Готово. Обработано файлов: {successes} из {len(paths)}'
                    self._detail = 'Откройте результат, чтобы проверить варианты и материалы без подписи.'
                elif successes:
                    self._status = 'Готово частично. Часть файлов или изображений не сохранена'
                    self._detail = 'Готовые результаты доступны в истории. Причины ошибок указаны в событиях обработки.'
                else:
                    self._status, self._detail = 'Ошибка: PDF не обработан', 'Причина указана в событиях обработки.'
                seconds = round(monotonic() - started)
                self._append(f'Завершено за {seconds // 60}:{seconds % 60:02d}.', 'muted')
