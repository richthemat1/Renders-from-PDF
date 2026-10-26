"""One WebView2 window for the local React workspace, with a narrow native bridge."""
import logging
import json
import os
from pathlib import Path
import sys
from threading import Event, Lock, Thread

from photo_desktop_server import DesktopServer
from photo_export import VERSION
from photo_jobs import JobService
from photo_native import app_icon_path, configure_logging, enable_dpi_awareness

LOGGER = logging.getLogger('pdf-photos')


def open_local(path):
    path = Path(path).resolve(strict=True)
    if sys.platform == 'win32':
        os.startfile(str(path))
    else:
        import webbrowser
        webbrowser.open(path.as_uri())


class NativeBridge:
    """Only these methods are exposed to JS; implementation references stay private."""
    def __init__(self, jobs, server, log_path):
        self._jobs, self._server, self._log_path = jobs, server, log_path
        self._window = None
        self._unsaved = False
        self._unsaved_sequence = -1
        self._unsaved_lock = Lock()
        self._dialogs = Lock()

    def _guard(self):
        if self._window is None or self._window.get_current_url() != self._server.url:
            raise ValueError('Действие доступно только из рабочего окна приложения.')
        if self._server.closing.is_set():
            raise ValueError('Программа закрывается. Дождитесь завершения сохранения.')

    def choose_pdfs(self):
        self._guard()
        if self._jobs.busy:
            raise ValueError('Дождитесь завершения текущей обработки.')
        import webview
        with self._dialogs:
            paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
                                                    file_types=('PDF-презентации (*.pdf)',))
            return self._jobs.start(paths) if paths else self._jobs.snapshot()

    def choose_result(self):
        self._guard()
        import webview
        with self._dialogs:
            folders = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if not folders:
                return None
            return self._server.register_result(folders[0])

    def choose_output(self):
        self._guard()
        if self._jobs.busy:
            raise ValueError('Папку можно изменить после завершения обработки.')
        import webview
        with self._dialogs:
            folders = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            return self._jobs.set_output('custom', folders[0]) if folders else self._jobs.snapshot()

    def open_result(self, id, relative=''):
        self._guard()
        if not isinstance(id, str) or not isinstance(relative, str):
            raise ValueError('Некорректный путь результата.')
        with self._server.lock:
            folder = self._server.folders.get(id)
            if folder is None:
                raise ValueError('Результат не открыт в приложении.')
            path = (folder / relative).resolve()
            if not path.is_relative_to(folder) or not path.exists():
                raise ValueError('Файл результата не найден.')
            if path != folder and (not path.is_file() or path.suffix.lower() != '.pdf'):
                raise ValueError('Из просмотра можно открыть только PDF или папку результата.')
            open_local(path)
        return True

    def open_log(self):
        self._guard()
        if not self._log_path or not Path(self._log_path).is_file():
            raise ValueError('Журнал пока не создан. События доступны на экране обработки.')
        open_local(self._log_path)
        return True

    def set_unsaved(self, value, sequence=None):
        self._guard()
        if type(value) is not bool:
            raise ValueError('Некорректное состояние редактора.')
        if sequence is not None and (type(sequence) is not int or sequence < 0):
            raise ValueError('Некорректная последовательность состояния редактора.')
        with self._unsaved_lock:
            if sequence is None or sequence >= self._unsaved_sequence:
                self._unsaved = value
                if sequence is not None:
                    self._unsaved_sequence = sequence
        return True


def frontend_path():
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / 'frontend' / 'dist'


def webview2_available():
    if sys.platform != 'win32':
        return True
    import winreg
    key = r'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as handle:
                    version = winreg.QueryValueEx(handle, 'pv')[0]
                    if version and version != '0.0.0.0':
                        return True
            except OSError:
                pass
    return False


def runtime_error(message):
    """Show a readable error even in a packaged build with its console hidden."""
    LOGGER.error('Desktop launch failed: %s', message)
    text = (f'Не удалось открыть новый интерфейс.\n\n{message}\n\n'
            'Для нового окна нужен Microsoft Edge WebView2 Runtime.\n'
            'Официальный установщик: https://developer.microsoft.com/microsoft-edge/webview2/\n\n'
            'Для запуска совместимого интерфейса используйте:\nPDF-Photos.exe --legacy-ui')
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, 'Фото из PDF', 0x10)
    else:
        print(text, file=sys.stderr)


def open_desktop(initial_files=(), state_path=None, debug=False):
    enable_dpi_awareness()
    log_path = configure_logging()
    assets = frontend_path()
    if not (assets / 'index.html').is_file():
        runtime_error('Не найдена сборка интерфейса. Выполните npm ci и npm run build в Исходники/frontend.')
        return 1
    if not webview2_available():
        runtime_error('Microsoft Edge WebView2 Runtime не найден на этом компьютере.')
        return 1
    jobs = JobService(state_path)
    server = DesktopServer(jobs, assets).start()
    bridge = NativeBridge(jobs, server, log_path)
    closed = Event()
    closing = Event()
    try:
        import webview
        from webview.dom import DOMEventHandler
        window = webview.create_window(f'Фото из PDF · {VERSION}', server.url, js_api=bridge,
                                       width=1380, height=960, min_size=(780, 620),
                                       background_color='#f4f6f8', text_select=True)
        bridge._window = window

        def finish_close():
            try:
                if bridge._unsaved and not window.create_confirmation_dialog('Есть несохранённые изменения',
                        'Закрыть окно без сохранения правок? Сохранённые PDF останутся на месте.'):
                    closing.clear()
                    return
                jobs.close()
                server.stop()
                closed.set()
                window.destroy()
            except Exception:
                LOGGER.exception('Unable to close desktop')
                closing.clear()

        def on_closing():
            if closed.is_set():
                return True
            if not closing.is_set():
                closing.set()
                Thread(target=finish_close, daemon=False, name='pdf-photos-close').start()
            return False

        def on_drop(event):
            files = event.get('dataTransfer', {}).get('files', [])
            paths = [item['pywebviewFullPath'] for item in files if item.get('pywebviewFullPath')]
            if not paths:
                return
            try:
                bridge._guard()
                if bridge._unsaved:
                    raise ValueError('Сначала сохраните или сбросьте правки текущего результата.')
                jobs.start(paths)
                # The React shell follows this public DOM event without accepting native paths.
                window.evaluate_js('window.dispatchEvent(new Event("pdf-photos-job-started"))')
            except Exception as error:
                LOGGER.warning('Native drop rejected: %s', error)
                window.evaluate_js('window.dispatchEvent(new CustomEvent("pdf-photos-error", {detail:'
                                   + json.dumps(str(error)) + '}))')

        def on_loaded():
            if window.get_current_url() != server.url:
                return
            window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True)
            window.dom.document.events.dragover += DOMEventHandler(lambda _: None, prevent_default=True)

        window.events.loaded += on_loaded
        window.events.closing += on_closing
        def start_initial():
            if initial_files:
                jobs.start(initial_files)
        # Force modern Chromium: the old mshtml fallback cannot run the React editor.
        icon = app_icon_path()
        webview.start(start_initial, gui='edgechromium', debug=debug, private_mode=True,
                      icon=str(icon) if icon else None)
        return 0
    except Exception as error:
        LOGGER.exception('Desktop failed')
        runtime_error(str(error))
        return 1
    finally:
        jobs.close()
        server.stop()
