"""Standalone Windows UI. No external Python installation or network service."""
from datetime import datetime
import logging
import os
from pathlib import Path
import sys

from photo_export import VERSION

LOGGER = logging.getLogger('pdf-photos')


class Cancelled(Exception):
    pass


def configure_logging():
    if LOGGER.handlers:
        return next((getattr(handler, 'baseFilename', None) for handler in LOGGER.handlers), None)
    try:
        folder = Path(os.environ.get('LOCALAPPDATA', Path.home()))/'PDF-Photos/logs'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder/f'run-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}.log'
        handler = logging.FileHandler(path, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
        LOGGER.info('PDF Photos %s started', VERSION)
        return str(path)
    except OSError:
        LOGGER.addHandler(logging.NullHandler())
        return None


def enable_dpi_awareness():
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def app_icon_path():
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    icon = base / 'assets' / 'app-icon.ico'
    return icon if icon.is_file() else None


def accept_dropped_files(root, callback):
    """Let Explorer drop files onto the window (WM_DROPFILES); returns the handler that must stay referenced.

    The callback runs inside the window procedure and must not call Tk; it should only queue the names.
    """
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes
    user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.CallWindowProcW.restype = LRESULT
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_wchar_p, wintypes.UINT]
    shell32.DragFinish.argtypes = [ctypes.c_void_p]
    root.update_idletasks()  # before this, wm_frame() still names the inner window, not the frame Explorer drops on
    hwnd = int(root.wm_frame(), 16)
    # An elevated window would otherwise silently refuse drops from Explorer.
    for message in (0x0233, 0x004A, 0x0049):
        user32.ChangeWindowMessageFilterEx(hwnd, message, 1, None)
    shell32.DragAcceptFiles(hwnd, True)
    previous = None

    def procedure(window, message, wparam, lparam):
        if message == 0x0233:
            names = []
            for index in range(shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)):
                size = shell32.DragQueryFileW(wparam, index, None, 0)+1
                buffer = ctypes.create_unicode_buffer(size)
                shell32.DragQueryFileW(wparam, index, buffer, size)
                names.append(buffer.value)
            shell32.DragFinish(wparam)
            # Runs inside Tk's event loop: no Tk calls here, only hand the names over.
            callback(names)
            return 0
        return user32.CallWindowProcW(previous, window, message, wparam, lparam)

    handler = WNDPROC(procedure)
    previous = user32.SetWindowLongPtrW(hwnd, -4, ctypes.cast(handler, ctypes.c_void_p))
    return handler


def dropped_pdfs(names):
    """PDF files from a drop; a dropped folder contributes the PDFs directly inside it."""
    found, skipped = [], []
    for name in names:
        path = Path(name)
        if path.is_dir():
            inside = sorted(child for child in path.iterdir() if child.is_file() and child.suffix.lower() == '.pdf')
            found += inside
            if not inside:
                skipped.append(path.name)
        elif path.suffix.lower() == '.pdf' and path.is_file():
            found.append(path)
        else:
            skipped.append(path.name)
    return [str(path) for path in dict.fromkeys(found)], skipped


def app_icon(tk, color):
    icon = tk.PhotoImage(width=32, height=32)
    icon.put(color, to=(2, 2, 30, 30))
    icon.put('#ffffff', to=(9, 6, 23, 26))
    icon.put(color, to=(12, 16, 20, 22))
    icon.put('#cfe5e0', to=(12, 10, 20, 12))
    return icon
