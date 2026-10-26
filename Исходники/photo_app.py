"""Standalone Windows UI. No external Python installation or network service."""
from datetime import datetime
import logging
import os
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Event, Thread

from photo_export import VERSION, extract, main as cli_main

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


COLORS = {'bg': '#f3f4f6', 'card': '#ffffff', 'border': '#dcdfe4', 'text': '#1d2127', 'muted': '#5f6774',
          'accent': '#1b6b5f', 'accent_hover': '#14564c', 'accent_soft': '#e3f0ed', 'accent_off': '#a8c8c1',
          'ok': '#1a7f37', 'warn': '#9a6700', 'warn_soft': '#fff4d6', 'error': '#c4291c', 'trough': '#e6e8ec'}


def enable_dpi_awareness():
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


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


def open_app(initial_files=()):
    import tkinter as tk
    from tkinter import filedialog, ttk

    enable_dpi_awareness()
    root = tk.Tk()
    root.title(f'Фото из PDF · {VERSION}')
    scale = max(root.winfo_fpixels('1i')/96, 1)
    root.geometry(f'{round(920*scale)}x{round(680*scale)}')
    root.minsize(round(760*scale), round(560*scale))
    root.configure(background=COLORS['bg'])
    try:
        root.iconphoto(True, app_icon(tk, COLORS['accent']))
    except tk.TclError:
        pass
    log_path = configure_logging()
    c = COLORS
    font, semibold = 'Segoe UI', 'Segoe UI Semibold'
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', background=c['bg'], foreground=c['text'], font=(font, 10), bordercolor=c['border'],
                    focuscolor=c['accent'])
    style.configure('TFrame', background=c['bg'])
    style.configure('Card.TFrame', background=c['card'], bordercolor=c['border'], relief='solid', borderwidth=1)
    style.configure('TLabel', background=c['bg'])
    style.configure('Title.TLabel', font=(semibold, 18))
    style.configure('Muted.TLabel', foreground=c['muted'])
    style.configure('Badge.TLabel', background=c['accent_soft'], foreground=c['accent'], font=(semibold, 9),
                    padding=(8, 2))
    for name in ('Card', 'CardMuted', 'CardTitle', 'Status', 'StatValue', 'StatCaption'):
        style.configure(f'{name}.TLabel', background=c['card'])
    style.configure('CardMuted.TLabel', foreground=c['muted'])
    style.configure('CardTitle.TLabel', font=(semibold, 11))
    style.configure('Status.TLabel', font=(semibold, 11))
    style.configure('StatValue.TLabel', font=(semibold, 20))
    style.configure('StatCaption.TLabel', foreground=c['muted'], font=(font, 9))
    style.configure('TButton', background=c['card'], foreground=c['text'], bordercolor=c['border'],
                    lightcolor=c['card'], darkcolor=c['card'], padding=(14, 7), font=(font, 10))
    style.map('TButton', background=[('disabled', c['bg']), ('pressed', c['trough']), ('active', c['bg'])],
              foreground=[('disabled', '#a2a8b1')], bordercolor=[('focus', c['accent'])])
    style.configure('Accent.TButton', background=c['accent'], foreground='#ffffff', bordercolor=c['accent'],
                    lightcolor=c['accent'], darkcolor=c['accent'], font=(semibold, 10), padding=(18, 8))
    style.map('Accent.TButton',
              background=[('disabled', c['accent_off']), ('pressed', c['accent_hover']), ('active', c['accent_hover'])],
              lightcolor=[('disabled', c['accent_off']), ('active', c['accent_hover'])],
              darkcolor=[('disabled', c['accent_off']), ('active', c['accent_hover'])],
              bordercolor=[('disabled', c['accent_off']), ('active', c['accent_hover'])],
              foreground=[('disabled', '#ffffff')])
    style.configure('Link.TButton', background=c['bg'], bordercolor=c['bg'], lightcolor=c['bg'], darkcolor=c['bg'],
                    foreground=c['accent'], padding=(8, 4))
    style.map('Link.TButton', background=[('active', c['accent_soft'])], bordercolor=[('active', c['accent_soft'])],
              lightcolor=[('active', c['accent_soft'])], darkcolor=[('active', c['accent_soft'])])
    for name, color in (('Accent', c['accent']), ('Ok', c['ok']), ('Error', c['error'])):
        style.configure(f'{name}.Horizontal.TProgressbar', troughcolor=c['trough'], background=color,
                        bordercolor=c['trough'], lightcolor=color, darkcolor=color, thickness=8)
    style.configure('TSeparator', background=c['border'])
    style.configure('Vertical.TScrollbar', background=c['card'], troughcolor=c['card'], bordercolor=c['card'],
                    lightcolor=c['card'], darkcolor=c['card'], arrowcolor=c['muted'], gripcount=0)

    frame = ttk.Frame(root, padding=(24, 20, 24, 16))
    frame.pack(fill='both', expand=True)
    header = ttk.Frame(frame)
    header.pack(fill='x')
    ttk.Label(header, text='Фото из PDF', style='Title.TLabel').pack(side='left')
    ttk.Label(header, text=f'версия {VERSION}', style='Badge.TLabel').pack(side='left', padx=(10, 0), pady=(4, 0))
    if log_path:
        ttk.Button(header, text='Технический журнал', style='Link.TButton',
                   command=lambda: os.startfile(log_path)).pack(side='right')
    previous = ttk.Button(header, text='Открыть прошлый результат…', style='Link.TButton')
    previous.pack(side='right')
    subtitle = ttk.Label(frame, style='Muted.TLabel',
                         text='Извлекает изображения из презентаций и раскладывает их по вариантам: '
                              'один вариант — один многостраничный PDF.')
    subtitle.pack(anchor='w', fill='x', pady=(4, 16))

    work = ttk.Frame(frame, style='Card.TFrame', padding=18)
    work.pack(fill='x')
    pick_row = ttk.Frame(work, style='Card.TFrame', padding=0, borderwidth=0)
    pick_row.pack(fill='x')
    actions = ttk.Frame(pick_row, style='Card.TFrame', borderwidth=0)
    actions.pack(side='right', padx=(16, 0))
    pick_text = ttk.Frame(pick_row, style='Card.TFrame', borderwidth=0)
    pick_text.pack(side='left', fill='x', expand=True)
    ttk.Label(pick_text, text='Презентации для обработки', style='CardTitle.TLabel').pack(anchor='w')
    ttk.Label(pick_text, style='CardMuted.TLabel',
              text='Перетащите PDF или папку с PDF в это окно, выберите файлы кнопкой или перетащите их на значок программы. '
                   'Результат появится рядом с каждым файлом.').pack(anchor='w', fill='x', pady=(2, 0))
    ttk.Separator(work).pack(fill='x', pady=16)
    status = tk.StringVar(value='Ожидаю выбор файлов')
    status_row = ttk.Frame(work, style='Card.TFrame', borderwidth=0)
    status_row.pack(fill='x')
    status_label = ttk.Label(status_row, textvariable=status, style='Status.TLabel')
    status_label.pack(side='left', fill='x', expand=True)
    percent = ttk.Label(status_row, text='', style='CardMuted.TLabel')
    percent.pack(side='right')
    progress = ttk.Progressbar(work, mode='determinate', maximum=100, style='Accent.Horizontal.TProgressbar')
    progress.pack(fill='x', pady=(10, 8))
    detail = ttk.Label(work, text='Ctrl+O — выбрать файлы · Esc — остановить обработку', style='CardMuted.TLabel')
    detail.pack(anchor='w', fill='x')

    stats_row = ttk.Frame(frame)
    stats_row.pack(fill='x', pady=(14, 0))
    stats = {}
    for column, (key, caption) in enumerate((('images', 'изображений'), ('documents', 'PDF-файлов'),
                                             ('unsigned', 'без подписи'), ('review', 'на проверку'))):
        tile = ttk.Frame(stats_row, style='Card.TFrame', padding=(16, 10))
        tile.grid(row=0, column=column, sticky='nsew', padx=(0 if column == 0 else 10, 0))
        stats_row.columnconfigure(column, weight=1, uniform='stats')
        value = ttk.Label(tile, text='—', style='StatValue.TLabel', foreground='#c3c8cf')
        value.pack(anchor='w')
        ttk.Label(tile, text=caption, style='StatCaption.TLabel').pack(anchor='w')
        stats[key] = value
    totals = dict.fromkeys(stats, 0)

    result_row = ttk.Frame(frame)
    result_row.pack(fill='x', pady=(14, 10))
    ttk.Label(result_row, text='События', style='CardTitle.TLabel', background=c['bg']).pack(side='left')

    log_card = ttk.Frame(frame, style='Card.TFrame', padding=1)
    log_card.pack(fill='both', expand=True)
    log = tk.Text(log_card, height=8, wrap='word', font=(font, 10), padx=14, pady=12, background=c['card'],
                  foreground=c['text'], relief='flat', borderwidth=0, highlightthickness=0, spacing1=2,
                  spacing3=2, cursor='arrow', state='disabled')
    scrollbar = ttk.Scrollbar(log_card, orient='vertical', command=log.yview)
    log.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side='right', fill='y')
    log.pack(side='left', fill='both', expand=True)
    log.tag_configure('time', foreground='#9aa1ab')
    log.tag_configure('head', font=(semibold, 10))
    log.tag_configure('ok', foreground=c['ok'])
    log.tag_configure('warn', foreground=c['warn'])
    log.tag_configure('error', foreground=c['error'])
    log.tag_configure('muted', foreground=c['muted'], font=(font, 9))
    log.tag_configure('placeholder', foreground='#9aa1ab')
    log.configure(state='normal')
    log.insert('end', 'Здесь появятся сообщения по каждому файлу: сколько сохранено, что требует проверки, '
                      'ошибки и путь к результату.', 'placeholder')
    log.configure(state='disabled')
    footer = ttk.Label(frame, style='Muted.TLabel', font=(font, 9),
                       text='Работает локально, без интернета. Материалы без подписи и с неоднозначной '
                            'подписью сохраняются в папки «Без подписи» и «На проверку».')
    footer.pack(anchor='w', fill='x', pady=(10, 0))

    def rewrap(event, labels=(subtitle, footer)):
        for label in labels:
            label.configure(wraplength=max(event.width-8, 200))
    frame.bind('<Configure>', rewrap, add='+')
    pick_text.bind('<Configure>', lambda event: pick_text.winfo_children()[1].configure(
        wraplength=max(event.width-4, 200)), add='+')

    messages, stop = Queue(), Event()
    state = {'busy': False, 'output': None, 'poll_id': None, 'close': False, 'started': None, 'fresh': True}

    def append(text, *tags):
        log.configure(state='normal')
        if state['fresh']:
            log.delete('1.0', 'end')
            state['fresh'] = False
        elif log.get('1.0', 'end-1c'):
            log.insert('end', '\n')
        log.insert('end', f'{datetime.now():%H:%M:%S}  ', 'time')
        log.insert('end', text, tags)
        log.see('end')
        log.configure(state='disabled')

    def set_progress(value):
        progress.configure(value=value)
        percent.configure(text=f'{value:.0f}%' if state['busy'] else '')

    def show_totals():
        for key, label in stats.items():
            label.configure(text=str(totals[key]),
                            foreground=c['warn'] if key in ('unsigned', 'review') and totals[key] else c['text'])

    def on_progress(info):
        if stop.is_set():
            raise Cancelled()
        messages.put(('progress', info))

    def worker(paths):
        successes = partial = unsigned = review = 0
        for index, source in enumerate(paths, 1):
            if stop.is_set():
                break
            messages.put(('start', (source, index, len(paths))))
            try:
                LOGGER.info('Processing %s', source)
                report = extract(Path(source), progress=on_progress)
                successes += 1
                partial += bool(report['errors'])
                unsigned += report['counts_by_status'].get('unsigned', 0)
                review += report['counts_by_status'].get('review', 0)
                LOGGER.info('Completed: images=%s; status=%s; errors=%s; warnings=%s; output=%s',
                            len(report['images']), report['counts_by_status'], report['errors'],
                            report['warnings'], report['output'])
                messages.put(('success', report))
            except Cancelled:
                LOGGER.info('Cancelled: %s', source)
                break
            except Exception as error:
                LOGGER.exception('Failed: %s', source)
                messages.put(('error', (source, str(error))))
        messages.put(('done', (successes, len(paths), partial, unsigned, review, stop.is_set())))

    def start(paths):
        if state['busy'] or not paths:
            return
        stop.clear()
        state.update(busy=True, started=datetime.now())
        totals.update(dict.fromkeys(totals, 0))
        for label in stats.values():
            label.configure(text='0', foreground=c['text'])
        choose.configure(state='disabled')
        cancel.configure(state='normal')
        cancel.pack(side='right', padx=(0, 12))
        progress.configure(style='Accent.Horizontal.TProgressbar')
        set_progress(0)
        status_label.configure(foreground=c['text'])
        detail.configure(text=f'В очереди файлов: {len(paths)}')
        Thread(target=worker, args=(tuple(paths),), daemon=True).start()

    def drop(names):
        paths, skipped = dropped_pdfs(names)
        if skipped:
            append('Пропущено, это не PDF: '+', '.join(skipped), 'warn')
        if state['busy']:
            if paths:
                append('Идёт обработка. Перетащите файлы ещё раз, когда она закончится.', 'warn')
            return
        if paths:
            root.deiconify()
            root.lift()
            start(paths)
        elif not skipped:
            append('В перетащенной папке нет PDF.', 'warn')

    state['drop_handler'] = accept_dropped_files(root, lambda names: messages.put(('drop', names)))

    def pick(event=None):
        if not state['busy']:
            start(filedialog.askopenfilenames(parent=root, title='Выберите PDF', filetypes=[('PDF', '*.pdf')]))

    def cancel_work(event=None):
        if not state['busy'] or stop.is_set():
            return
        stop.set()
        status.set('Останавливаю обработку…')
        detail.configure(text='Дождитесь завершения текущего изображения — незавершённая выгрузка будет удалена.')
        cancel.configure(state='disabled')

    def open_preview(folder):
        import webbrowser
        from photo_server import PreviewServer
        try:
            server = state['server'] = state.get('server') or PreviewServer()
            webbrowser.open(server.register(folder))
        except Exception as error:
            LOGGER.exception('Preview failed: %s', folder)
            append(f'Не удалось открыть просмотр с редактированием: {error}', 'error')
            if (Path(folder)/'Просмотр.html').is_file():
                os.startfile(str(Path(folder)/'Просмотр.html'))
            return
        if not state.get('preview_hint'):
            state['preview_hint'] = True
            append('Просмотр открыт в браузере. Кнопка «Редактировать» там работает, пока окно программы открыто.', 'muted')

    def show_result(preview=False):
        if state['output']:
            if preview:
                open_preview(state['output'])
            else:
                os.startfile(state['output'])

    def pick_previous():
        folder = filedialog.askdirectory(parent=root, title='Папка результата («… — PDF»)', mustexist=True)
        if not folder:
            return
        if not (Path(folder)/'Отчёт.json').is_file():
            append(f'В папке нет «Отчёт.json» — выберите папку результата обработки: {folder}', 'error')
            return
        state['output'] = str(Path(folder))
        open_folder.configure(state='normal')
        preview.configure(state='normal', style='Accent.TButton')
        append(f'Открыт результат: {folder}', 'head')
        open_preview(folder)

    previous.configure(command=pick_previous)

    choose = ttk.Button(actions, text='Выбрать файлы', style='Accent.TButton', command=pick)
    choose.pack(side='right')
    cancel = ttk.Button(status_row, text='Остановить', command=cancel_work, state='disabled')
    preview = ttk.Button(result_row, text='Открыть просмотр', command=lambda: show_result(True), state='disabled')
    preview.pack(side='right')
    open_folder = ttk.Button(result_row, text='Открыть папку', command=show_result, state='disabled')
    open_folder.pack(side='right', padx=(0, 8))
    root.bind('<Control-o>', pick)
    root.bind('<Control-O>', pick)
    root.bind('<Escape>', cancel_work)

    def finish(text, color, bar):
        status.set(text)
        status_label.configure(foreground=color)
        progress.configure(style=f'{bar}.Horizontal.TProgressbar')

    def poll():
        try:
            while True:
                kind, data = messages.get_nowait()
                if kind == 'drop':
                    drop(data)
                elif kind == 'start':
                    source, index, total = data
                    append(f'Файл {index} из {total}: {Path(source).name}', 'head')
                    status.set(f'Читаю файл {index} из {total}')
                    detail.configure(text=Path(source).name)
                    set_progress(0)
                elif kind == 'progress':
                    if stop.is_set():
                        continue
                    current, total = data['current'], data['total']
                    if data['stage'] == 'labels':
                        status.set(f'Чтение заголовков · страница {current} из {total}')
                        set_progress(12*current/max(total, 1))
                    elif data['stage'] == 'ocr':
                        status.set(f'Распознавание скана · страница {current} из {total}')
                        set_progress(12+13*current/max(total, 1))
                    elif data['stage'] == 'images':
                        status.set(f'Сохранение изображений · страница {current} из {total}')
                        set_progress(25+55*(current-1)/max(total, 1))
                        stats['images'].configure(text=str(totals['images']+data['found']))
                    elif data['stage'] == 'merge':
                        status.set(f'Объединение PDF по вариантам · {current} из {total}')
                        set_progress(80+10*current/max(total, 1))
                    else:
                        status.set(f'Создание просмотра · {current} из {total}')
                        set_progress(90+9.7*current/max(total, 1))
                elif kind == 'success':
                    state['output'] = data['output']
                    open_folder.configure(state='normal')
                    preview.configure(state='normal', style='Accent.TButton')
                    counts = data['counts_by_status']
                    totals['images'] += len(data['images'])
                    totals['documents'] += len(data['documents'])
                    totals['unsigned'] += counts.get('unsigned', 0)
                    totals['review'] += counts.get('review', 0)
                    show_totals()
                    append(f'Сохранено {len(data["documents"])} PDF из {len(data["images"])} изображений, '
                           f'папок: {len(data["counts_by_folder"])}.', 'ok')
                    unclear = [f'{name} — {counts[key]}' for key, name in
                               (('unsigned', 'без подписи'), ('review', 'на проверку')) if counts.get(key)]
                    if unclear:
                        append(f'Проверьте вручную: {", ".join(unclear)}.', 'warn')
                    if data['warnings']:
                        append(f'Для {len(data["warnings"])} изображений сохранён снимок области страницы. '
                               f'Подробности в отчёте.', 'warn')
                    if data['errors']:
                        append(f'Не удалось сохранить {len(data["errors"])} изображений. Причины в «Отчёт.json».', 'error')
                    if data.get('preview_errors'):
                        append(f'Для {len(data["preview_errors"])} PDF не создана миниатюра. Сами файлы сохранены.', 'warn')
                    append(data['output'], 'muted')
                elif kind == 'error':
                    source, error = data
                    append(f'Ошибка: {Path(source).name} — {error}', 'error')
                elif kind == 'done':
                    successes, total, partial, unsigned, review, cancelled = data
                    state['busy'] = False
                    choose.configure(state='normal')
                    cancel.configure(state='disabled')
                    cancel.pack_forget()
                    set_progress(100 if successes and not cancelled else 0)
                    seconds = round((datetime.now()-state['started']).total_seconds())
                    elapsed = f'{seconds//60}:{seconds % 60:02d}'
                    if cancelled:
                        finish(f'Остановлено. Готово файлов: {successes} из {total}', c['warn'], 'Accent')
                        detail.configure(text='Полностью обработанные файлы сохранены.')
                    elif successes == total and not partial:
                        finish(f'Готово. Обработано файлов: {successes} из {total}', c['ok'], 'Ok')
                        detail.configure(text=f'Время обработки {elapsed}. '
                                              + ('Откройте просмотр и проверьте материалы без подписи и на проверку.'
                                                 if unsigned or review else 'Откройте просмотр, чтобы проверить варианты.'))
                    elif successes:
                        finish('Готово частично. Часть изображений не сохранена', c['warn'], 'Ok')
                        detail.configure(text='Причины указаны в событиях ниже и в «Отчёт.json».')
                    else:
                        finish('Ошибка: PDF не обработан', c['error'], 'Error')
                        detail.configure(text='Причина указана в событиях ниже.')
                    append(f'Завершено за {elapsed}.', 'muted')
                    if state['close']:
                        root.destroy()
                        return
        except Empty:
            pass
        state['poll_id'] = root.after(100, poll)

    def close():
        if state['busy']:
            state['close'] = True
            cancel_work()
            status.set('Останавливаю обработку и закрываю окно…')
        else:
            root.destroy()

    root.protocol('WM_DELETE_WINDOW', close)
    def destroyed(event):
        if event.widget is root and state['poll_id'] is not None:
            root.after_cancel(state['poll_id'])
            state['poll_id'] = None
        if event.widget is root and state.get('server'):
            state['server'].stop()
    root.bind('<Destroy>', destroyed, add='+')
    poll()
    if initial_files:
        root.after(150, lambda: start(initial_files))
    root.mainloop()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--batch':
        sys.argv.pop(1)
        return cli_main()
    open_app(sys.argv[1:])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
