"""Standalone Windows entry point; batch mode and native file-drop compatibility."""
import sys

from photo_export import main as cli_main
from photo_native import (Cancelled, accept_dropped_files, app_icon, configure_logging,
                          dropped_pdfs, enable_dpi_awareness)
from photo_application import PhotoApplication


def open_app(initial_files=(), state_path=None):
    import tkinter as tk
    enable_dpi_awareness()
    root = tk.Tk()
    app = PhotoApplication(root, state_path=state_path)
    if initial_files:
        root.after(150, lambda: app.start(initial_files))
    root.mainloop()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--batch':
        sys.argv.pop(1)
        return cli_main()
    open_app(sys.argv[1:])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
