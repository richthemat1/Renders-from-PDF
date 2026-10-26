"""The native workspace: processing, real result history, settings and result details."""
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from photo_export import VERSION
from photo_widgets import Button, COLORS as C, DropZone, ElideLabel, RoundedPanel, ScrollableFrame, Theme, Tooltip


def file_size(size):
    if size < 1024 * 1024:
        return f'{size / 1024:.0f} КБ'
    return f'{size / (1024 * 1024):.1f} МБ'.replace('.', ',')


def result_date(item):
    stamp = datetime.fromisoformat(item['finished_at'])
    today = datetime.now().date()
    if item.get('imported'):
        return f'Добавлен {stamp:%d.%m.%Y}'
    day = 'Сегодня' if stamp.date() == today else 'Вчера' if stamp.date() == today - timedelta(days=1) else f'{stamp:%d.%m.%Y}'
    return f'{day}, {stamp:%H:%M}'


class WorkspaceView:
    def __init__(self, root, workspace, callbacks, status, scale=1):
        self.root, self.workspace, self.callbacks, self.status = root, workspace, callbacks, status
        self.theme, self.busy, self.has_result = Theme(root, scale), False, False
        self.page, self.events_open, self.records = 'work', False, workspace.history
        self._sidebar_width, self._compact_table = None, None
        t, p = self.theme, self.theme.px
        root.configure(bg=C['bg'])
        self.shell = tk.Frame(root, bg=C['bg'])
        self.shell.pack(fill='both', expand=True)
        self.sidebar = tk.Frame(self.shell, bg=C['sidebar'], width=p(244))
        self.sidebar.pack(side='left', fill='y')
        self.sidebar.pack_propagate(False)
        tk.Frame(self.shell, bg='#e4e8e2', width=1).pack(side='left', fill='y')
        self.main = tk.Frame(self.shell, bg=C['bg'])
        self.main.pack(side='left', fill='both', expand=True)
        self._build_sidebar()
        top = tk.Frame(self.main, bg=C['top'], height=p(65))
        top.pack(fill='x')
        top.pack_propagate(False)
        crumbs = tk.Frame(top, bg=C['top'])
        crumbs.pack(side='left', padx=p(32))
        t.label(crumbs, 'Рабочее пространство', 10, color='#959b93').pack(side='left')
        t.icon_label(crumbs, 'chevron_right', 13, '#959b93').pack(side='left', padx=p(12))
        self.crumb = t.label(crumbs, 'Обработка файлов', 10, color='#5a645b')
        self.crumb.pack(side='left')
        self.journal_button = Button(top, t, 'Технический журнал', callbacks['journal'], 'description',
                                     'text', size=10, height=32, width=166)
        self.journal_button.pack(side='right', padx=p(25))
        tk.Frame(self.main, bg='#e8ebe5', height=1).pack(fill='x')
        footer = tk.Frame(self.main, bg=C['footer'], height=p(39))
        footer.pack(side='bottom', fill='x')
        footer.pack_propagate(False)
        tk.Frame(footer, bg='#e5e9df', height=1).pack(side='top', fill='x')
        self.footer_status = ttk.Label(footer, textvariable=status, style='Footer.TLabel')
        self.footer_status.pack(side='left', padx=p(28))
        t.label(footer, f'v{VERSION}  ·  Работает локально', 10, color=C['subtle']).pack(side='right', padx=p(28))
        self.page_holder = tk.Frame(self.main, bg=C['bg'])
        self.page_holder.pack(fill='both', expand=True)
        self.pages = {name: ScrollableFrame(self.page_holder, t) for name in ('work', 'history', 'settings')}
        self._build_work()
        self._build_history()
        self._build_settings()
        self.pages['work'].pack(fill='both', expand=True)
        root.bind('<Configure>', self._resize, add='+')
        root.bind_all('<MouseWheel>', self._wheel, add='+')
        self.refresh_history()
        self._resize()

    def _build_sidebar(self):
        t, p = self.theme, self.theme.px
        brand = tk.Frame(self.sidebar, bg=C['sidebar'])
        brand.pack(fill='x', padx=p(27), pady=(p(34), p(52)))
        mark = tk.Label(brand, image=t.brand(), bg=C['sidebar'], borderwidth=0, padx=0, pady=0)
        mark.pack(side='left')
        self.brand_title = t.label(brand, 'Фото из PDF', 20, True)
        self.brand_title.pack(side='left', padx=(p(11), 0))
        self.brand = brand
        self.nav_label = t.label(self.sidebar, 'РАБОЧЕЕ ПРОСТРАНСТВО', 9, color='#929991', anchor='w')
        self.nav_label.pack(fill='x', padx=p(32), pady=(0, p(15)))
        nav = tk.Frame(self.sidebar, bg=C['sidebar'])
        nav.pack(fill='x', padx=p(18))
        self.nav, self.nav_buttons = nav, {}
        self.nav_names = {'work': 'Обработка файлов', 'history': 'История обработки', 'settings': 'Настройки'}
        for name, icon in (('work', 'grid_view'), ('history', 'history'), ('settings', 'settings')):
            button = Button(nav, t, self.nav_names[name], lambda page=name: self.show_page(page), icon,
                            'active' if name == 'work' else 'nav', height=45, size=12, width=208, anchor='w')
            button.pack(fill='x', pady=(0, p(7)))
            Tooltip(button, self.nav_names[name])
            self.nav_buttons[name] = button
        self.history_badge = t.label(self.nav_buttons['history'], '', 10, color='#747d77', bg='#e6e9e4', padx=0)
        self.history_badge.bind('<ButtonRelease-1>', lambda e: self.show_page('history'))
        self.sidebar_bottom = tk.Frame(self.sidebar, bg=C['sidebar'])
        self.sidebar_bottom.pack(side='bottom', fill='x', padx=p(23), pady=p(20))
        Button(self.sidebar_bottom, t, 'Помощь и поддержка', self.callbacks['help'], 'help', 'nav',
               width=195, height=44, size=11, anchor='w').pack(fill='x', pady=(0, p(12)))
        tk.Frame(self.sidebar_bottom, bg='#e1e6dc', height=1).pack(fill='x')
        t.label(self.sidebar_bottom, f'Версия {VERSION}', 10, color='#a0a69e', anchor='w').pack(fill='x', pady=(p(17), 0))

    def _page_body(self, name, title):
        t, p = self.theme, self.theme.px
        outer = self.pages[name].body
        frame = tk.Frame(outer, bg=C['bg'])
        frame.pack(fill='both', expand=True, padx=p(42), pady=(p(38), p(25)))
        t.label(frame, title, 29, True, anchor='w').pack(fill='x', pady=(0, p(29)))
        return frame

    def _build_work(self):
        t, p = self.theme, self.theme.px
        self.work_body = self._page_body('work', 'Обработка PDF')
        self.upload_card = RoundedPanel(self.work_body, t, bottom_fill='#fcfdf9')
        self.upload_card.pack(fill='x')
        card = self.upload_card.content
        header = tk.Frame(card, bg=C['surface'])
        header.pack(fill='x', padx=p(23), pady=p(18))
        step = t.label(header, '1', 10, color='#618365', bg='#f3f6ef')
        step.configure(width=3, height=1)
        step.pack(side='left', padx=(0, p(10)))
        self.card_title = t.label(header, 'Добавьте презентации', 13, True)
        self.card_title.pack(side='left')
        self.more_button = Button(header, t, 'Добавить PDF', self.callbacks['choose'], 'folder_open', 'text',
                                  height=28, size=11, width=126)
        self.pdf_label = t.label(header, 'PDF-файлы', 10, color='#a1a69c')
        self.pdf_label.pack(side='right')
        tk.Frame(card, bg=C['soft_line'], height=1).pack(fill='x')
        self.dropzone = DropZone(card, t, self.callbacks['choose'])
        self.dropzone.pack(fill='x', padx=p(23), pady=p(20))
        self.choose_button = self.dropzone.choose
        self.processing = tk.Frame(card, bg=C['surface'])
        progress_top = tk.Frame(self.processing, bg=C['surface'])
        progress_top.pack(fill='x', pady=(0, p(8)))
        self.status_label = ttk.Label(progress_top, textvariable=self.status, style='Workspace.TLabel', wraplength=p(540))
        self.status_label.pack(side='left', fill='x', expand=True)
        self.cancel_button = Button(progress_top, t, 'Остановить', self.callbacks['cancel'], 'stop',
                                    width=136, height=34, size=11)
        self.progress = ttk.Progressbar(self.processing, mode='determinate', maximum=100,
                                        style='Workspace.Horizontal.TProgressbar')
        self.progress.pack(fill='x', pady=(p(10), p(10)))
        detail_row = tk.Frame(self.processing, bg=C['surface'])
        detail_row.pack(fill='x')
        self.detail_var = tk.StringVar(value='')
        self.detail = t.label(detail_row, '', 11, color=C['muted'], anchor='w', justify='left')
        self.detail.configure(textvariable=self.detail_var, wraplength=p(650))
        self.detail.pack(side='left', fill='x', expand=True)
        self.percent = t.label(detail_row, '', 11, color=C['subtle'])
        self.percent.pack(side='right', padx=(p(12), 0))
        stats = tk.Frame(self.processing, bg=C['surface'])
        stats.pack(fill='x', pady=(p(23), p(18)))
        self.stats = {}
        for column, (key, caption) in enumerate((('images', 'изображений'), ('documents', 'PDF-файлов'),
                                                ('unsigned', 'без подписи'), ('review', 'на проверку'))):
            tile = tk.Frame(stats, bg='#f7f9f3')
            tile.grid(row=0, column=column, sticky='nsew', padx=(0 if column == 0 else p(8), 0))
            stats.columnconfigure(column, weight=1, uniform='stats')
            value = t.label(tile, '0', 26, color=C['green'], bg='#f7f9f3', anchor='w')
            value.pack(fill='x', padx=p(14), pady=(p(12), p(4)))
            t.label(tile, caption, 10, color=C['muted'], bg='#f7f9f3', anchor='w').pack(fill='x', padx=p(14), pady=(0, p(12)))
            self.stats[key] = value
        self.result_actions = tk.Frame(self.processing, bg=C['surface'])
        self.open_folder_button = Button(self.result_actions, t, 'Открыть папку', self.callbacks['folder'], 'folder_open',
                                         width=164, height=40)
        self.open_folder_button.pack(side='left')
        self.preview_button = Button(self.result_actions, t, 'Открыть просмотр', self.callbacks['preview'], 'open_in_new',
                                     'primary', width=193, height=40)
        self.preview_button.pack(side='left', padx=p(10))
        self.new_hint = t.label(self.processing, 'Проверьте распределение изображений в просмотре перед использованием.',
                                11, color=C['muted'], anchor='w', justify='left')
        output = tk.Frame(card, bg='#fcfdf9')
        output.pack(fill='x')
        self.output_row = output
        tk.Frame(output, bg='#edf0e8', height=1).pack(fill='x')
        output_inner = tk.Frame(output, bg='#fcfdf9')
        output_inner.pack(fill='x', padx=p(24), pady=p(16))
        icon = tk.Frame(output_inner, bg='#f1f4ec', width=p(34), height=p(34))
        icon.pack(side='left', padx=(0, p(12)))
        icon.pack_propagate(False)
        t.icon_label(icon, 'folder_open', 20, '#8a987c').pack(expand=True)
        text = tk.Frame(output_inner, bg='#fcfdf9')
        text.pack(side='left', fill='x', expand=True)
        t.label(text, 'Куда сохранять результат', 10, color='#9a9f94', anchor='w').pack(fill='x')
        self.output_value = ElideLabel(text, t, self.workspace.output_label, 11, color='#5d6857')
        self.output_value.pack(fill='x', pady=(p(4), 0))
        self.output_change = Button(output_inner, t, 'Изменить', self.callbacks['change_output'], 'chevron_right',
                                    'text', width=99, height=32, size=11)
        self.output_change.pack(side='right', padx=(p(10), 0))
        self.events_section = tk.Frame(self.work_body, bg=C['bg'])
        self.event_toggle = Button(self.events_section, t, 'События обработки', self.toggle_events, 'description',
                                   'text', width=209, height=32, size=11, anchor='w')
        self.event_toggle.pack(anchor='w')
        self.event_panel = RoundedPanel(self.events_section, t)
        self.log = tk.Text(self.event_panel.content, height=5, wrap='word', font=t.font(11), background=C['surface'],
                           foreground=C['text'], borderwidth=0, highlightthickness=0, padx=p(14), pady=p(10), state='disabled')
        self.log.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(self.event_panel.content, orient='vertical', command=self.log.yview,
                                style='Workspace.Vertical.TScrollbar')
        scroll.pack(side='right', fill='y')
        self.log.configure(yscrollcommand=scroll.set)
        for name, color in (('ok', C['green']), ('error', C['error']), ('warn', C['warning']), ('muted', C['subtle']), ('time', '#9ba392')):
            self.log.tag_configure(name, foreground=color)
        self.log.tag_configure('head', font=t.font(11, True))
        self.recent_section = tk.Frame(self.work_body, bg=C['bg'])
        self.recent_section.pack(fill='x', pady=(p(32), 0))
        head = tk.Frame(self.recent_section, bg=C['bg'])
        head.pack(fill='x', pady=(0, p(18)))
        t.label(head, 'Последние результаты', 16, True).pack(side='left')
        Button(head, t, 'Вся история', lambda: self.show_page('history'), 'arrow_forward', 'text',
               width=109, height=28, size=11).pack(side='right')
        self.recent_table = RoundedPanel(self.recent_section, t)
        self.recent_table.pack(fill='x')
        self.previous_button = Button(self.recent_section, t, 'Открыть прошлый результат…', self.callbacks['previous'],
                                      'folder_open', 'text', width=245, height=34, size=11)
        self.previous_button.pack(anchor='w', pady=(p(10), 0))
        self.processing.bind('<Configure>', self._processing_resize)

    def _processing_resize(self, event):
        self.status_label.configure(wraplength=max(self.theme.px(240), event.width - self.theme.px(160)))
        self.detail.configure(wraplength=max(self.theme.px(260), event.width - self.theme.px(55)))
        self.new_hint.configure(wraplength=max(self.theme.px(260), event.width))

    def _build_history(self):
        t, p = self.theme, self.theme.px
        body = self._page_body('history', 'История обработки')
        row = tk.Frame(body, bg=C['bg'])
        row.pack(fill='x', pady=(0, p(18)))
        t.label(row, 'Все результаты', 16, True).pack(side='left')
        Button(row, t, 'Открыть прошлый результат…', self.callbacks['previous'], 'folder_open',
               width=245, height=34, size=11).pack(side='right')
        self.history_table = RoundedPanel(body, t)
        self.history_table.pack(fill='x')
        t.label(body, 'Здесь сохраняются последние 100 результатов. Исходные PDF остаются без изменений.', 11,
                color=C['subtle'], anchor='w', justify='left', wraplength=p(650)).pack(fill='x', pady=(p(18), 0))

    def _build_settings(self):
        t, p = self.theme, self.theme.px
        body = self._page_body('settings', 'Настройки')
        panel = RoundedPanel(body, t)
        panel.pack(fill='x')
        inner = tk.Frame(panel.content, bg=C['surface'])
        inner.pack(fill='x', padx=p(30), pady=p(30))
        t.label(inner, 'Сохранение результатов', 18, True, anchor='w').pack(fill='x', pady=(0, p(25)))
        t.label(inner, 'Папка результатов', 13, anchor='w').pack(fill='x', pady=(0, p(10)))
        self.mode_names = ['Рядом с исходным файлом', 'В папке «Результаты»', 'В выбранной папке']
        self.mode_combo = ttk.Combobox(inner, values=self.mode_names, state='readonly',
                                       style='Workspace.TCombobox', font=t.font(13))
        self.mode_combo.current(('nearby', 'results', 'custom').index(self.workspace.output_mode))
        self.mode_combo.pack(fill='x')
        self.mode_combo.bind('<<ComboboxSelected>>', lambda e: self.callbacks['output_mode'](
            ('nearby', 'results', 'custom')[self.mode_combo.current()]))
        self.settings_output = t.label(inner, self.workspace.output_label, 11, color=C['muted'], anchor='w',
                                       justify='left', wraplength=p(600))
        self.settings_output.pack(fill='x', pady=(p(10), p(12)))
        self.settings_choose = Button(inner, t, 'Выбрать другую папку…', self.callbacks['change_output'], 'folder_open',
                                      width=229, height=36, size=12)
        self.settings_choose.pack(anchor='w')
        tk.Frame(inner, height=1, bg=C['soft_line']).pack(fill='x', pady=p(25))
        group = tk.Frame(inner, bg=C['surface'])
        group.pack(fill='x')
        t.icon_label(group, 'check', 20, C['green']).pack(side='right', padx=p(8))
        t.label(group, 'Собирать изображения по вариантам', 13, anchor='w').pack(side='left')
        t.label(inner, 'Каждый вариант сохраняется в отдельный многостраничный PDF. Это правило обработки всегда включено.',
                11, color=C['muted'], anchor='w', justify='left', wraplength=p(620)).pack(fill='x', pady=(p(10), p(25)))
        self.settings_notice = t.label(inner, 'Настройки сохраняются на этом компьютере и применяются к следующей обработке.',
                                       11, color=C['subtle'], anchor='w', justify='left', wraplength=p(620))
        self.settings_notice.pack(fill='x')

    def show_page(self, name):
        if name not in self.pages:
            return
        self.pages[self.page].pack_forget()
        self.page = name
        self.pages[name].pack(fill='both', expand=True)
        self.pages[name].canvas.yview_moveto(0)
        self.crumb.configure(text=self.nav_names[name])
        for key, button in self.nav_buttons.items():
            button.configure(variant='active' if key == name else 'nav')
        self.refresh_history()
        self.root.after_idle(self.pages[name].content_resize)

    def _resize(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        logical_width = self.root.winfo_width() / self.theme.scale
        if logical_width < 10:
            logical_width = 1280
        width = 76 if logical_width <= 850 else 214 if logical_width <= 1150 else 244
        if self._sidebar_width != width:
            self._sidebar_width = width
            self.sidebar.configure(width=self.theme.px(width))
            small = width == 76
            self.brand.pack_configure(padx=self.theme.px(17 if small else 23))
            if small:
                self.brand_title.pack_forget()
                self.nav_label.pack_forget()
                self.sidebar_bottom.pack_forget()
            else:
                self.brand_title.pack(side='left', padx=(self.theme.px(11), 0))
                self.nav_label.pack(fill='x', padx=self.theme.px(25), pady=(0, self.theme.px(15)), before=self.nav)
                self.sidebar_bottom.pack(side='bottom', fill='x', padx=self.theme.px(18), pady=self.theme.px(20))
            self.nav.pack_configure(padx=self.theme.px(13 if small else 12 if width == 214 else 18))
            for key, button in self.nav_buttons.items():
                button.anchor = 'center' if small else 'w'
                button.configure(text='' if small else self.nav_names[key])
        compact = logical_width <= 1100
        if self._compact_table != compact:
            self._compact_table = compact
            self.refresh_history()
        if self.workspace.history and width > 76:
            self.history_badge.place(relx=1, x=-self.theme.px(12), rely=.5, anchor='e')
        else:
            self.history_badge.place_forget()

    def _wheel(self, event):
        if isinstance(event.widget, (tk.Text, ttk.Combobox)):
            return
        page = self.pages[self.page]
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is not None and str(widget).startswith(str(page)):
            page.wheel(event.delta)

    def refresh_output(self):
        self.output_value.full_text = self.workspace.output_label
        self.output_value.fit()
        self.settings_output.configure(text=self.workspace.output_label)
        self.mode_combo.current(('nearby', 'results', 'custom').index(self.workspace.output_mode))
        self.settings_notice.configure(text=self.workspace.warning or
            'Настройки сохраняются на этом компьютере и применяются к следующей обработке.',
            fg=C['warning'] if self.workspace.warning else C['subtle'])

    def set_busy(self, busy):
        self.busy = busy
        for button in (self.choose_button, self.more_button, self.output_change, self.settings_choose):
            button.configure(state='disabled' if busy else 'normal')
        self.mode_combo.configure(state='disabled' if busy else 'readonly')
        self.dropzone.pack_forget()
        self.processing.pack(fill='x', padx=self.theme.px(24), pady=self.theme.px(24), before=self.output_row)
        self.pdf_label.pack_forget()
        self.more_button.pack(side='right')
        self.card_title.configure(text='Обработка презентаций' if busy else 'Результат обработки')
        if busy:
            self.cancel_button.configure(state='normal')
            self.cancel_button.pack(side='right', padx=(self.theme.px(12), 0))
            self.result_actions.pack_forget()
            self.new_hint.pack_forget()
            self.progress.configure(style='Workspace.Horizontal.TProgressbar')
            self.show_page('work')
            self.pages['work'].canvas.yview_moveto(0)
        else:
            self.cancel_button.pack_forget()
            if self.has_result:
                self.result_actions.pack(fill='x', pady=(0, self.theme.px(12)))
                self.new_hint.pack(fill='x')
            self.percent.configure(text='')

    def set_progress(self, value, text=None):
        self.progress.configure(value=value)
        self.percent.configure(text=f'{value:.0f}%' if self.busy else '')
        if text is not None:
            self.status.set(text)

    def set_totals(self, totals):
        for key, value in self.stats.items():
            count = totals.get(key, 0)
            value.configure(text=str(count), fg=C['warning'] if key in ('unsigned', 'review') and count else C['green'])

    def finish(self, status, detail, error=False):
        self.status.set(status)
        self.detail_var.set(detail)
        self.status_label.configure(foreground=C['error'] if error else C['green_text'])
        self.progress.configure(style='Error.Horizontal.TProgressbar' if error else 'Done.Horizontal.TProgressbar')

    def append(self, text, *tags):
        if not self.events_section.winfo_manager():
            self.events_section.pack(fill='x', pady=(self.theme.px(16), 0), after=self.upload_card)
        self.log.configure(state='normal')
        if self.log.get('1.0', 'end-1c'):
            self.log.insert('end', '\n')
        self.log.insert('end', f'{datetime.now():%H:%M:%S}  ', 'time')
        self.log.insert('end', text, tags)
        self.log.see('end')
        self.log.configure(state='disabled')
        if 'error' in tags or 'warn' in tags:
            self.set_events(True)

    def set_events(self, opened):
        self.events_open = opened
        if opened:
            self.event_panel.pack(fill='x', pady=(self.theme.px(7), 0))
        else:
            self.event_panel.pack_forget()

    def toggle_events(self):
        self.set_events(not self.events_open)

    def refresh_history(self):
        self.records = list(self.workspace.history)
        self.history_badge.configure(text=f' {len(self.records)} ')
        if self.records and self._sidebar_width and self._sidebar_width > 76:
            self.history_badge.place(relx=1, x=-self.theme.px(12), rely=.5, anchor='e')
        else:
            self.history_badge.place_forget()
        self._draw_table(self.recent_table.content, self.records[:5])
        self._draw_table(self.history_table.content, self.records)

    def _draw_table(self, parent, records):
        for widget in parent.winfo_children():
            widget.destroy()
        t, p = self.theme, self.theme.px
        if not records:
            empty = tk.Frame(parent, bg=C['surface'])
            empty.pack(fill='x', padx=p(25), pady=p(28))
            t.icon_label(empty, 'history', 27, '#a1b198').pack(pady=(0, p(12)))
            t.label(empty, 'Здесь появятся обработанные презентации', 13, True).pack()
            t.label(empty, 'Добавьте PDF или откройте прошлый результат из папки.', 11,
                    color=C['subtle']).pack(pady=(p(7), 0))
            return
        columns = [('НАЗВАНИЕ ФАЙЛА', 1), ('ИЗОБРАЖЕНИЯ', 0), ('СТАТУС', 0)]
        if not self._compact_table:
            columns.append(('ДАТА ОБРАБОТКИ', 0))
        columns.append(('', 0))
        head = tk.Frame(parent, bg='#f7f9f3', height=p(35))
        head.pack(fill='x')
        head.grid_propagate(False)
        self._table_columns(head)
        for i, (text, _) in enumerate(columns):
            t.label(head, text, 9, color='#939d88', anchor='w').grid(row=0, column=i, sticky='w', pady=p(11))
        for index, item in enumerate(records):
            tk.Frame(parent, height=1, bg=C['soft_line']).pack(fill='x')
            row = tk.Frame(parent, bg=C['surface'], height=p(72), cursor='hand2', takefocus=True)
            row.pack(fill='x')
            row.grid_propagate(False)
            self._table_columns(row)
            file_cell = tk.Frame(row, bg=C['surface'])
            file_cell.grid(row=0, column=0, sticky='ew', pady=p(15))
            file_cell.columnconfigure(1, weight=1)
            color = ('mint', 'sand', 'lavender')[index % 3]
            icon = tk.Frame(file_cell, bg=C[color], width=p(36), height=p(41))
            icon.grid(row=0, column=0, rowspan=2, padx=(0, p(12)))
            icon.grid_propagate(False)
            t.icon_label(icon, 'description', 22, C[color + '_text']).place(relx=.5, rely=.5, anchor='center')
            ElideLabel(file_cell, t, item['name'], 11, color=C['text']).grid(row=0, column=1, sticky='ew')
            t.label(file_cell, f'PDF-документ  ·  {file_size(item["size"])}', 10, color=C['faint'], anchor='w').grid(
                row=1, column=1, sticky='w', pady=(p(5), 0))
            t.label(row, str(item['images']), 12, color='#7f8b75', anchor='w').grid(row=0, column=1, sticky='w')
            missing = not Path(item['output']).is_dir()
            review = item['unsigned'] + item['review']
            status = 'Не найден' if missing else 'Частично' if item['errors'] else 'Проверить' if review else 'Готово'
            tone = 'warning' if missing or item['errors'] or review else 'success'
            t.label(row, status, 10, color=C[tone], bg=C[tone + '_bg'], padx=0).grid(row=0, column=2, sticky='w')
            if not self._compact_table:
                t.label(row, result_date(item), 10, color=C['subtle'], anchor='w').grid(row=0, column=3, sticky='w')
            t.icon_label(row, 'chevron_right', 17, '#a9b09f').grid(row=0, column=len(columns) - 1, sticky='e')
            command = lambda event=None, record=item: self.callbacks['result'](record)
            self._bind_row(row, command)
            row.bind('<Return>', command)
            row.bind('<space>', command)
            row.bind('<FocusIn>', lambda event, widget=row: widget.configure(highlightbackground='#6ca793', highlightthickness=1))
            row.bind('<FocusOut>', lambda event, widget=row: widget.configure(highlightthickness=0))

    def _table_columns(self, row):
        p = self.theme.px
        row.columnconfigure(0, weight=1, minsize=p(210))
        row.columnconfigure(1, minsize=p(104))
        row.columnconfigure(2, minsize=p(90))
        if self._compact_table:
            row.columnconfigure(3, minsize=p(28))
        else:
            row.columnconfigure(3, minsize=p(145))
            row.columnconfigure(4, minsize=p(28))
        row.configure(padx=p(20))

    def _bind_row(self, widget, command):
        widget.bind('<ButtonRelease-1>', command)
        for child in widget.winfo_children():
            self._bind_row(child, command)

    def dialog(self, title, width=530):
        top = tk.Toplevel(self.root)
        top.withdraw()
        top.title(title)
        top.configure(bg='#fcfdf9')
        top.transient(self.root)
        top.resizable(False, False)
        frame = tk.Frame(top, bg='#fcfdf9')
        frame.pack(fill='both', expand=True, padx=self.theme.px(32), pady=self.theme.px(30))
        top.bind('<Escape>', lambda e: top.destroy())
        top._content = frame
        top._desired_width = self.theme.px(width)
        return top, frame

    def reveal_dialog(self, top):
        top.update_idletasks()
        width = top._desired_width
        height = top.winfo_reqheight()
        x = min(max(0, self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2), top.winfo_screenwidth() - width)
        y = min(max(0, self.root.winfo_rooty() + (self.root.winfo_height() - height) // 2), top.winfo_screenheight() - height - 50)
        top.geometry(f'{width}x{height}+{max(0, x)}+{max(0, y)}')
        top.deiconify()
        top.grab_set()
        top.focus_set()

    def show_result(self, item):
        t, p = self.theme, self.theme.px
        top, body = self.dialog('Результат обработки')
        t.icon_label(body, 'description', 35, '#6f965f').pack(anchor='w', pady=(0, p(18)))
        t.label(body, item['name'], 21, True, anchor='w', justify='left', wraplength=p(465)).pack(fill='x')
        t.label(body, 'Результат обработки сохранён на этом компьютере.', 12, color=C['muted'],
                anchor='w', justify='left', wraplength=p(465)).pack(fill='x', pady=(p(10), p(24)))
        stats = tk.Frame(body, bg=body.cget('bg'))
        stats.pack(fill='x')
        for i, (value, name) in enumerate(((item['images'], 'изображений'), (item['documents'], 'PDF-файлов'),
                                         (item['variants'], 'вариантов'))):
            tile = tk.Frame(stats, bg=body.cget('bg'))
            tile.grid(row=0, column=i, sticky='w')
            stats.columnconfigure(i, weight=1)
            t.label(tile, str(value), 27).pack(anchor='w')
            t.label(tile, name, 11, color=C['subtle']).pack(anchor='w', pady=(p(7), 0))
        missing = not Path(item['output']).is_dir()
        count = item['unsigned'] + item['review']
        note = ('Папка результата не найдена. Возможно, она была перемещена. Откройте её через «Открыть прошлый результат…».' if missing else
                f'Требуют ручной проверки: {count}. Проверьте группы «Без подписи» и «На проверку».' if count else
                'Откройте просмотр, чтобы проверить подписи, состав вариантов и порядок изображений.')
        if item['errors']:
            note += f' Не удалось сохранить изображений: {item["errors"]}. Подробности — в отчёте.'
        t.label(body, note, 12, color=C['warning'] if count or missing or item['errors'] else C['muted'],
                anchor='w', justify='left', wraplength=p(465)).pack(fill='x', pady=(p(24), p(18)))
        path = t.label(body, item['output'], 10, color=C['subtle'], anchor='w', justify='left', wraplength=p(465))
        path.pack(fill='x', pady=(0, p(24)))
        actions = tk.Frame(body, bg=body.cget('bg'))
        actions.pack(fill='x')
        def act(callback):
            top.destroy()
            callback(item['output'])
        Button(actions, t, 'Открыть папку', lambda: act(self.callbacks['folder']), 'folder_open', width=175,
               state='disabled' if missing else 'normal').pack(side='left')
        Button(actions, t, 'Открыть просмотр', lambda: act(self.callbacks['preview']), 'open_in_new', 'primary', width=204,
               state='disabled' if missing else 'normal').pack(side='right')
        Button(body, t, 'Закрыть', top.destroy, variant='text', width=90, height=32, size=11).pack(anchor='e', pady=(p(12), 0))
        self.reveal_dialog(top)
        return top

    def show_message(self, title, message, error=False):
        t, p = self.theme, self.theme.px
        top, body = self.dialog(title)
        t.icon_label(body, 'warning' if error else 'info', 30, C['error'] if error else C['green']).pack(anchor='w')
        t.label(body, title, 19, True, anchor='w', justify='left', wraplength=p(465)).pack(fill='x', pady=(p(18), p(12)))
        t.label(body, message, 13, color=C['muted'], anchor='w', justify='left', wraplength=p(465)).pack(fill='x')
        Button(body, t, 'Понятно', top.destroy, variant='primary', width=112).pack(anchor='e', pady=(p(24), 0))
        self.reveal_dialog(top)
        return top
