"""Connect the native workspace to the unchanged PDF pipeline and preview editor."""
from datetime import datetime
import logging
import os
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Event, Thread
import tkinter as tk
from tkinter import filedialog

from photo_export import VERSION, extract
from photo_native import Cancelled, accept_dropped_files, app_icon, app_icon_path, configure_logging, dropped_pdfs
from photo_state import WorkspaceState
from photo_ui import WorkspaceView

LOGGER = logging.getLogger('pdf-photos')


class PhotoApplication:
    def __init__(self, root, state_path=None):
        self.root = root
        root.title(f'Фото из PDF · {VERSION}')
        self.scale = max(root.winfo_fpixels('1i') / 96, 1)
        p = lambda value: round(value * self.scale)
        width = min(p(1280), root.winfo_screenwidth() - p(70))
        height = min(p(940), root.winfo_screenheight() - p(90))
        root.geometry(f'{width}x{height}')
        root.minsize(min(p(820), width), min(p(620), height))
        try:
            icon = app_icon_path()
            if icon:
                root.iconbitmap(default=str(icon))
            else:
                root.iconphoto(True, app_icon(tk, '#26745d'))
        except tk.TclError:
            pass
        self.log_path = configure_logging()
        self.workspace = WorkspaceState(state_path)
        self.status = tk.StringVar(root, value='Ожидаю выбор файлов')
        self.messages, self.stop_event = Queue(), Event()
        self.busy, self.output, self.server, self.closing = False, None, None, False
        self.poll_id, self.started, self.preview_hint = None, None, False
        self.totals = dict.fromkeys(('images', 'documents', 'unsigned', 'review'), 0)
        callbacks = {'choose': self.choose, 'cancel': self.cancel, 'previous': self.open_previous,
                     'preview': self.open_preview, 'folder': self.open_folder, 'journal': self.open_journal,
                     'help': self.help, 'result': self.show_result, 'change_output': self.change_output,
                     'output_mode': self.set_output_mode}
        self.view = WorkspaceView(root, self.workspace, callbacks, self.status, self.scale)
        self.drop_handler = accept_dropped_files(root, lambda names: self.messages.put(('drop', names)))
        root.bind('<Control-o>', self.choose)
        root.bind('<Control-O>', self.choose)
        root.bind('<Control-KeyPress>', self.control_key)
        root.bind('<Escape>', self.cancel)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Destroy>', self.destroyed, add='+')
        if self.workspace.warning:
            self.view.append(self.workspace.warning, 'warn')
        self.poll()

    def control_key(self, event):
        # Tk can report an unknown keysym for O in the Russian Windows layout.
        if sys.platform == 'win32' and event.keycode == 79:
            self.choose()
            return 'break'

    def choose(self, event=None):
        if self.busy or self.closing:
            return
        paths = filedialog.askopenfilenames(parent=self.root, title='Выберите PDF-презентации', filetypes=[('PDF', '*.pdf')])
        self.start(paths)

    def start(self, paths):
        if self.busy or self.closing or not paths:
            return
        paths = tuple(dict.fromkeys(str(Path(path).resolve()) for path in paths))
        self.stop_event.clear()
        self.busy, self.output, self.started = True, None, datetime.now()
        self.totals = dict.fromkeys(self.totals, 0)
        self.view.has_result = False
        self.view.set_totals(self.totals)
        self.view.set_busy(True)
        self.view.status_label.configure(foreground='#242d2a')
        self.view.set_progress(0, 'Подготовка к обработке…')
        self.view.detail_var.set(f'В очереди файлов: {len(paths)}')
        Thread(target=self.worker, args=(paths,), daemon=True).start()

    def worker(self, paths):
        successes = partial = unsigned = review = 0
        for index, source in enumerate(paths, 1):
            if self.stop_event.is_set():
                break
            self.messages.put(('start', (source, index, len(paths))))
            try:
                LOGGER.info('Processing %s', source)
                destination = self.workspace.destination(source)
                report = extract(Path(source), output=destination, progress=self.on_progress)
                successes += 1
                partial += bool(report['errors'])
                unsigned += report['counts_by_status'].get('unsigned', 0)
                review += report['counts_by_status'].get('review', 0)
                LOGGER.info('Completed: images=%s; status=%s; errors=%s; warnings=%s; output=%s',
                            len(report['images']), report['counts_by_status'], report['errors'],
                            report['warnings'], report['output'])
                self.messages.put(('success', report))
            except Cancelled:
                LOGGER.info('Cancelled: %s', source)
                break
            except Exception as error:
                LOGGER.exception('Failed: %s', source)
                self.messages.put(('error', (source, str(error))))
        self.messages.put(('done', (successes, len(paths), partial, unsigned, review, self.stop_event.is_set())))

    def on_progress(self, info):
        if self.stop_event.is_set():
            raise Cancelled()
        self.messages.put(('progress', info))

    def cancel(self, event=None):
        if not self.busy or self.stop_event.is_set():
            return
        self.stop_event.set()
        self.status.set('Останавливаю обработку…')
        self.view.detail_var.set('Дождитесь завершения текущего изображения — незавершённая выгрузка будет удалена.')
        self.view.cancel_button.configure(state='disabled')

    def drop(self, names):
        try:
            paths, skipped = dropped_pdfs(names)
        except OSError as error:
            self.view.append(f'Не удалось прочитать перетащенные файлы: {error}', 'error')
            return
        if skipped:
            self.view.append('Пропущено, это не PDF: ' + ', '.join(skipped), 'warn')
        if self.busy:
            if paths:
                self.view.append('Идёт обработка. Перетащите файлы ещё раз, когда она закончится.', 'warn')
            return
        if paths:
            self.root.deiconify()
            self.root.lift()
            self.start(paths)

    def _progress(self, info):
        if self.stop_event.is_set():
            return
        current, total, stage = info['current'], max(info['total'], 1), info['stage']
        if stage == 'labels':
            value, text = 12 * current / total, f'Чтение заголовков · страница {current} из {total}'
        elif stage == 'ocr':
            value, text = 12 + 13 * current / total, f'Распознавание скана · страница {current} из {total}'
        elif stage == 'images':
            value, text = 25 + 55 * (current - 1) / total, f'Сохранение изображений · страница {current} из {total}'
            self.view.set_totals(self.totals | {'images': self.totals['images'] + info['found']})
        elif stage == 'merge':
            value, text = 80 + 10 * current / total, f'Объединение PDF по вариантам · {current} из {total}'
        else:
            value, text = 90 + 9.7 * current / total, f'Создание просмотра · {current} из {total}'
        self.view.set_progress(value, text)

    def _success(self, report):
        self.output = report['output']
        self.view.has_result = True
        self.workspace.remember(report)
        self.view.refresh_history()
        counts = report['counts_by_status']
        for key, value in (('images', len(report['images'])), ('documents', len(report['documents'])),
                           ('unsigned', counts.get('unsigned', 0)), ('review', counts.get('review', 0))):
            self.totals[key] += value
        self.view.set_totals(self.totals)
        self.view.append(f'Сохранено {len(report["documents"])} PDF из {len(report["images"])} изображений, '
                         f'папок: {len(report["counts_by_folder"])}.', 'ok')
        unclear = [f'{name} — {counts[key]}' for key, name in
                   (('unsigned', 'без подписи'), ('review', 'на проверку')) if counts.get(key)]
        if unclear:
            self.view.append('Проверьте вручную: ' + ', '.join(unclear) + '.', 'warn')
        if report['warnings']:
            self.view.append(f'Предупреждений: {len(report["warnings"])}. Возможны ограничения OCR '
                             'или резервное сохранение области страницы. Подробности — в отчёте.', 'warn')
        if report['errors']:
            self.view.append(f'Не удалось сохранить {len(report["errors"])} изображений. Причины — в «Отчёт.json».', 'error')
        if report.get('preview_errors'):
            self.view.append(f'Для {len(report["preview_errors"])} PDF не создана миниатюра. Сами файлы сохранены.', 'warn')
        self.view.append(self.output, 'muted')
        if self.workspace.warning:
            self.view.append(self.workspace.warning, 'warn')

    def _done(self, data):
        successes, total, partial, unsigned, review, cancelled = data
        self.busy = False
        self.view.set_busy(False)
        self.view.set_progress(100 if successes and not cancelled else 0)
        seconds = round((datetime.now() - self.started).total_seconds())
        elapsed = f'{seconds // 60}:{seconds % 60:02d}'
        if cancelled:
            status = f'Остановлено. Готово файлов: {successes} из {total}'
            detail = 'Полностью обработанные файлы сохранены. Все результаты доступны в истории.'
        elif successes == total and not partial:
            status = f'Готово. Обработано файлов: {successes} из {total}'
            detail = f'Время обработки {elapsed}. ' + (
                'Откройте просмотр и проверьте материалы без подписи и на проверку.' if unsigned or review else
                'Откройте просмотр, чтобы проверить варианты.')
        elif successes:
            status = 'Готово частично. Часть файлов или изображений не сохранена'
            detail = 'Готовые результаты доступны в истории. Причины ошибок указаны в событиях и в «Отчёт.json».'
        else:
            status, detail = 'Ошибка: PDF не обработан', 'Причина указана в событиях обработки ниже.'
        self.view.finish(status, detail, error=not successes and not cancelled)
        self.view.append(f'Завершено за {elapsed}.', 'muted')
        if self.closing:
            self.root.destroy()

    def poll(self):
        try:
            while True:
                kind, data = self.messages.get_nowait()
                if kind == 'drop':
                    self.drop(data)
                elif kind == 'start':
                    source, index, total = data
                    self.view.append(f'Файл {index} из {total}: {Path(source).name}', 'head')
                    self.view.set_progress(0, f'Читаю файл {index} из {total}')
                    self.view.detail_var.set(Path(source).name)
                elif kind == 'progress':
                    self._progress(data)
                elif kind == 'success':
                    self._success(data)
                elif kind == 'error':
                    source, error = data
                    self.view.append(f'Ошибка: {Path(source).name} — {error}', 'error')
                elif kind == 'done':
                    self._done(data)
                    if self.closing:
                        return
        except Empty:
            pass
        self.poll_id = self.root.after(100, self.poll)

    def _report(self, folder):
        from photo_edit import load_report
        report = load_report(folder)
        report['output'] = str(Path(folder).resolve())
        for key in ('source', 'images', 'documents', 'counts_by_status'):
            if key not in report:
                raise ValueError('В папке находится неполный отчёт обработки.')
        return report

    def open_previous(self):
        if self.closing:
            return
        folder = filedialog.askdirectory(parent=self.root, title='Папка результата («… — PDF»)', mustexist=True)
        if not folder:
            return
        try:
            item = self.workspace.remember(self._report(folder), processed=False)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.view.show_message('Не удалось открыть результат', str(error), True)
            return
        self.view.refresh_history()
        self.view.show_result(item)

    def show_result(self, item):
        if Path(item['output']).is_dir():
            try:
                item = self.workspace.remember(self._report(item['output']), processed=False)
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.view.show_message('Не удалось открыть результат', str(error), True)
                return
            self.view.refresh_history()
        return self.view.show_result(item)

    def open_preview(self, folder=None):
        import webbrowser
        from photo_server import PreviewServer
        folder = folder or self.output
        if not folder or self.closing:
            return
        try:
            self.server = self.server or PreviewServer()
            address = self.server.register(folder)
            if not webbrowser.open(address):
                raise OSError('Не удалось запустить браузер по умолчанию.')
        except Exception as error:
            LOGGER.exception('Preview failed: %s', folder)
            self.view.append(f'Не удалось открыть просмотр с редактированием: {error}', 'error')
            gallery = Path(folder) / 'Просмотр.html'
            if gallery.is_file():
                self._open_file(gallery)
            else:
                self.view.show_message('Просмотр недоступен', str(error), True)
            return
        if not self.preview_hint:
            self.preview_hint = True
            self.view.append('Просмотр открыт в браузере. Редактирование работает, пока окно программы открыто.', 'muted')

    def _open_file(self, path):
        try:
            if sys.platform == 'win32':
                os.startfile(str(path))
            else:
                import webbrowser
                webbrowser.open(Path(path).as_uri())
        except OSError as error:
            self.view.show_message('Не удалось открыть', str(error), True)

    def open_folder(self, folder=None):
        folder = folder or self.output
        if folder:
            self._open_file(folder)

    def open_journal(self):
        if self.log_path:
            self._open_file(self.log_path)
        else:
            self.view.show_message('Технический журнал недоступен',
                'Не удалось создать файл журнала. Сообщения текущей обработки доступны в разделе «События обработки».')

    def change_output(self):
        if self.busy or self.closing:
            return
        folder = filedialog.askdirectory(parent=self.root, title='Куда сохранять результаты', mustexist=True)
        if folder:
            try:
                self.workspace.set_output('custom', folder)
            except ValueError as error:
                self.view.show_message('Папка недоступна', str(error), True)
        self.view.refresh_output()

    def set_output_mode(self, mode):
        if self.busy or self.closing:
            self.view.refresh_output()
            return
        if mode == 'custom':
            self.change_output()
        else:
            self.workspace.set_output(mode)
            self.view.refresh_output()

    def help(self):
        self.view.show_message('Как пользоваться',
            '1. Выберите PDF-презентации или перетащите файлы и папки в окно программы. Обработка начнётся сразу.\n\n'
            '2. Дождитесь завершения и нажмите «Открыть просмотр». Результаты также доступны в истории.\n\n'
            '3. Проверьте «Без подписи» и «На проверку». При необходимости включите «Редактировать», '
            'перенесите изображения и сохраните изменения.\n\n'
            'Результат можно открыть из другой папки кнопкой «Открыть прошлый результат…». '
            'Для работы редактора оставьте окно программы открытым.\n\n'
            'Ctrl+O — выбрать файлы. Esc — остановить обработку. Исходные презентации не изменяются.')

    def close(self):
        if self.busy:
            self.closing = True
            self.cancel()
            self.status.set('Останавливаю обработку и закрываю окно…')
        else:
            self.closing = True
            self.root.destroy()

    def destroyed(self, event):
        if event.widget is not self.root:
            return
        if self.poll_id is not None:
            self.root.after_cancel(self.poll_id)
            self.poll_id = None
        if self.server:
            self.server.stop()
