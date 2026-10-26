"""Small native Tk widgets using the colors, Roboto and Material Symbols of the Figma reference."""
import json
from pathlib import Path
import sys
import tkinter as tk
from tkinter import font as tkfont, ttk

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk

ASSETS = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'assets'
COLORS = {'bg': '#fafbf9', 'sidebar': '#f2f4f1', 'surface': '#ffffff', 'top': '#fdfefb',
          'text': '#242d2a', 'muted': '#7c8877', 'subtle': '#929b88', 'faint': '#a3a89c',
          'line': '#e1e6dd', 'soft_line': '#eef0ea', 'green': '#2c7155', 'green_hover': '#205d43',
          'green_soft': '#e1eae1', 'green_text': '#22634d', 'green_border': '#d9e4d9',
          'mint': '#edf4ec', 'mint_text': '#78a077', 'sand': '#f6f0e4', 'sand_text': '#b09d72',
          'lavender': '#f0eef6', 'lavender_text': '#a098b8', 'success_bg': '#edf5e8',
          'success': '#5f804f', 'warning_bg': '#fbf3df', 'warning': '#946c20',
          'error': '#b64436', 'error_bg': '#fbeeea', 'footer': '#f7f9f3'}


class Theme:
    def __init__(self, root, scale=1):
        self.root, self.scale = root, scale
        self._icons, self._fonts = {}, {}
        self.codepoints = json.loads((ASSETS / 'icons.json').read_text(encoding='utf-8'))
        if sys.platform == 'win32':
            import ctypes
            for name in ('Roboto-Regular.ttf', 'Roboto-Medium.ttf', 'Roboto-Bold.ttf'):
                ctypes.windll.gdi32.AddFontResourceExW(str(ASSETS / name), 0x10, None)
        self.family = 'Roboto' if 'Roboto' in tkfont.families(root) else 'Segoe UI'
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=self.font(12), background=COLORS['bg'], foreground=COLORS['text'])
        style.configure('Workspace.TLabel', background=COLORS['surface'], foreground=COLORS['text'], font=self.font(16, True))
        style.configure('Footer.TLabel', background=COLORS['footer'], foreground=COLORS['muted'], font=self.font(10))
        style.configure('Workspace.Horizontal.TProgressbar', troughcolor='#e9eee3', background=COLORS['green'],
                        lightcolor=COLORS['green'], darkcolor=COLORS['green'], borderwidth=0,
                        thickness=self.px(6))
        style.configure('Done.Horizontal.TProgressbar', troughcolor='#e9eee3', background=COLORS['green'],
                        lightcolor=COLORS['green'], darkcolor=COLORS['green'], borderwidth=0,
                        thickness=self.px(6))
        style.configure('Error.Horizontal.TProgressbar', troughcolor='#f2e7e2', background=COLORS['error'],
                        lightcolor=COLORS['error'], darkcolor=COLORS['error'], borderwidth=0,
                        thickness=self.px(6))
        style.configure('Workspace.Vertical.TScrollbar', background='#d7ddd3', troughcolor=COLORS['bg'],
                        borderwidth=0, arrowsize=self.px(9), gripcount=0, relief='flat')
        style.configure('Workspace.TCombobox', font=self.font(13), padding=self.px(9),
                        fieldbackground='#f8faf4', background=COLORS['surface'], bordercolor='#dce3d5')
        style.map('Workspace.TCombobox', fieldbackground=[('readonly', '#f8faf4'), ('disabled', '#f2f4ef')],
                  foreground=[('readonly', COLORS['text']), ('disabled', '#a1a99b')],
                  selectbackground=[('readonly', '#f8faf4')], selectforeground=[('readonly', COLORS['text'])])

    def px(self, number):
        return round(number * self.scale)

    def font(self, size=12, bold=False):
        key = size, bold
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(self.root, family=self.family, size=-self.px(size),
                                          weight='bold' if bold else 'normal')
        return self._fonts[key]

    def icon(self, name, size=20, color=None):
        color = color or COLORS['muted']
        size = self.px(size)
        key = name, size, color
        if key not in self._icons:
            image = Image.new('RGBA', (size * 3, size * 3))
            draw = ImageDraw.Draw(image)
            font = ImageFont.truetype(str(ASSETS / 'MaterialSymbols.ttf'), size * 3)
            glyph = chr(self.codepoints[name])
            x0, y0, x1, y1 = draw.textbbox((0, 0), glyph, font=font)
            draw.text(((size * 3 - (x1 - x0)) / 2 - x0, (size * 3 - (y1 - y0)) / 2 - y0),
                      glyph, font=font, fill=color)
            image = image.resize((size, size), Image.Resampling.LANCZOS)
            self._icons[key] = ImageTk.PhotoImage(image, master=self.root)
        return self._icons[key]

    def brand(self):
        if not hasattr(self, '_brand'):
            width, height = self.px(39) * 3, self.px(41) * 3
            image = Image.new('RGB', (width, height), COLORS['sidebar'])
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=self.px(11) * 3, fill='#26745d')
            font = ImageFont.truetype(str(ASSETS / 'MaterialSymbols.ttf'), self.px(23) * 3)
            glyph = chr(self.codepoints['image'])
            x0, y0, x1, y1 = draw.textbbox((0, 0), glyph, font=font)
            draw.text(((width - x1 + x0) / 2 - x0, (height - y1 + y0) / 2 - y0),
                      glyph, font=font, fill='white')
            self._brand = ImageTk.PhotoImage(image.resize((self.px(39), self.px(41)), Image.Resampling.LANCZOS), master=self.root)
        return self._brand

    def label(self, parent, text='', size=12, bold=False, color=None, bg=None, **kwargs):
        kwargs.setdefault('padx', 0)
        kwargs.setdefault('pady', 0)
        return tk.Label(parent, text=text, font=self.font(size, bold), fg=color or COLORS['text'],
                        bg=bg or parent.cget('bg'), borderwidth=0, highlightthickness=0,
                        **kwargs)

    def icon_label(self, parent, name, size=20, color=None, bg=None):
        return tk.Label(parent, image=self.icon(name, size, color), bg=bg or parent.cget('bg'),
                        borderwidth=0, padx=0, pady=0)


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text, self.window, self.timer = widget, text, None, None
        widget.bind('<Enter>', self.enter, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')
        widget.bind('<Destroy>', self.hide, add='+')

    def enter(self, event=None):
        self.hide()
        if self.text:
            self.timer = self.widget.after(650, self.show)

    def show(self):
        self.timer = None
        if not self.widget.winfo_exists():
            return
        self.window = tk.Toplevel(self.widget)
        self.window.withdraw()
        self.window.overrideredirect(True)
        tk.Label(self.window, text=self.text, bg='#263c30', fg='white', padx=10, pady=6,
                 font=('Segoe UI', 9), wraplength=480, justify='left').pack()
        self.window.update_idletasks()
        x = min(self.widget.winfo_rootx(), self.widget.winfo_screenwidth() - self.window.winfo_reqwidth() - 10)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        if y + self.window.winfo_reqheight() > self.widget.winfo_screenheight():
            y = self.widget.winfo_rooty() - self.window.winfo_reqheight() - 6
        self.window.geometry(f'+{max(0, x)}+{max(0, y)}')
        self.window.deiconify()

    def hide(self, event=None):
        if self.timer:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.window:
            self.window.destroy()
            self.window = None


class ElideLabel(tk.Label):
    def __init__(self, parent, theme, text, size=12, bold=False, color=None, **kwargs):
        super().__init__(parent, text=text, font=theme.font(size, bold), fg=color or COLORS['text'],
                         bg=parent.cget('bg'), anchor='w', padx=0, pady=0, width=1, **kwargs)
        self.full_text, self.text_font = text, theme.font(size, bold)
        self.tooltip = Tooltip(self, text)
        self.bind('<Configure>', self.fit)

    def fit(self, event=None):
        self.tooltip.text = self.full_text
        width = self.winfo_width() - 2
        if self.text_font.measure(self.full_text) <= width:
            self.configure(text=self.full_text)
            return
        low, high = 0, len(self.full_text)
        while low < high:
            middle = (low + high + 1) // 2
            if self.text_font.measure(self.full_text[:middle] + '…') <= width:
                low = middle
            else:
                high = middle - 1
        self.configure(text=self.full_text[:low] + '…')


class RoundedPanel(tk.Frame):
    def __init__(self, parent, theme, fill=None, bottom_fill=None, radius=10, **kwargs):
        self.theme, self.fill = theme, fill or COLORS['surface']
        super().__init__(parent, bg=COLORS['line'], **kwargs)
        self.content = tk.Frame(self, bg=self.fill)
        self.content.pack(fill='both', expand=True, padx=1, pady=1)
        self._corners = []
        radius = max(2, theme.px(radius))
        for bottom in (False, True):
            for right in (False, True):
                image = Image.new('RGB', (radius * 6, radius * 6), parent.cget('bg'))
                draw = ImageDraw.Draw(image)
                draw.rounded_rectangle((0, 0, radius * 6 - 1, radius * 6 - 1), radius=radius * 3,
                                       fill=(bottom_fill if bottom and bottom_fill else self.fill),
                                       outline=COLORS['line'], width=3)
                x, y = (radius * 3 if right else 0), (radius * 3 if bottom else 0)
                image = image.crop((x, y, x + radius * 3, y + radius * 3)).resize((radius, radius), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image, master=self)
                label = tk.Label(self, image=photo, borderwidth=0, padx=0, pady=0)
                label.place(relx=1 if right else 0, rely=1 if bottom else 0,
                            anchor=('s' if bottom else 'n') + ('e' if right else 'w'))
                self._corners.append((label, photo))


class Button(tk.Canvas):
    def __init__(self, parent, theme, text='', command=None, icon=None, variant='secondary',
                 width=None, height=40, size=12, shortcut='', anchor='center', state='normal'):
        self.theme, self.text, self.command, self.icon_name = theme, text, command, icon
        self.variant, self.shortcut, self.anchor = variant, shortcut, anchor
        self.button_state, self.hover, self.focused, self.size = state, False, False, size
        icon_width = theme.px(26) if icon else 0
        self.natural_width = width or (theme.font(size).measure(text) / theme.scale + icon_width / theme.scale + 32 + (48 if shortcut else 0))
        super().__init__(parent, width=theme.px(self.natural_width), height=theme.px(height),
                         bg=parent.cget('bg'), borderwidth=0, highlightthickness=0, takefocus=True,
                         cursor='hand2' if state == 'normal' else 'arrow')
        self._image = None
        self.bind('<Configure>', self.draw)
        self.bind('<Enter>', lambda e: self._set('hover', True))
        self.bind('<Leave>', lambda e: self._set('hover', False))
        self.bind('<FocusIn>', lambda e: self._set('focused', True))
        self.bind('<FocusOut>', lambda e: self._set('focused', False))
        self.bind('<ButtonRelease-1>', self.invoke)
        self.bind('<Return>', self.invoke)
        self.bind('<space>', self.invoke)
        self.draw()

    def _set(self, name, value):
        setattr(self, name, value)
        self.draw()

    def configure(self, cnf=None, **kwargs):
        for key, attr in (('text', 'text'), ('command', 'command'), ('state', 'button_state'),
                          ('variant', 'variant'), ('icon', 'icon_name')):
            if key in kwargs:
                setattr(self, attr, kwargs.pop(key))
        result = super().configure(cnf, **kwargs) if cnf or kwargs else None
        if hasattr(self, 'theme'):
            self.draw()
        return result

    config = configure

    def draw(self, event=None):
        if not self.winfo_exists():
            return
        self.delete('all')
        p, c = self.theme.px, COLORS
        width = max(self.winfo_width(), p(self.natural_width)) if self.winfo_width() <= 1 else self.winfo_width()
        height = max(self.winfo_height(), int(float(super().cget('height'))))
        disabled = self.button_state == 'disabled'
        fill, line, foreground = c['surface'], c['line'], c['text']
        if self.variant == 'primary':
            fill = '#a3bcae' if disabled else c['green_hover'] if self.hover else c['green']
            line, foreground = fill, '#ffffff'
        elif self.variant in ('nav', 'active'):
            fill = c['green_soft'] if self.variant == 'active' else '#e7ece5' if self.hover else c['sidebar']
            line = c['green_border'] if self.variant == 'active' else fill
            foreground = c['green_text'] if self.variant == 'active' else '#747d77'
        elif self.variant == 'text':
            fill = '#eef3e9' if self.hover else self.cget('bg')
            line, foreground = fill, c['green']
        elif self.variant == 'danger':
            fill, foreground = c['error_bg'] if self.hover else c['surface'], c['error']
        elif self.hover and not disabled:
            fill = '#f5f8f1'
        if disabled and self.variant != 'primary':
            foreground = '#aab1a4'
        image = Image.new('RGB', (width * 3, height * 3), self.cget('bg'))
        draw = ImageDraw.Draw(image)
        inset = p(2) if self.focused else 0
        draw.rounded_rectangle((inset * 3, inset * 3, (width - 1 - inset) * 3, (height - 1 - inset) * 3),
                               radius=p(6) * 3, fill=fill,
                               outline='#6ca793' if self.focused else line, width=3 if not self.focused else p(2) * 3)
        self._image = ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS), master=self)
        self.create_image(0, 0, image=self._image, anchor='nw')
        text_width = self.theme.font(self.size, self.variant == 'active').measure(self.text)
        group_width = text_width + (p(27) if self.icon_name and self.text else p(20) if self.icon_name else 0) + (p(48) if self.shortcut else 0)
        x = p(13) if self.anchor == 'w' else (width - group_width) / 2
        if self.icon_name:
            self.create_image(x + p(10), height / 2, image=self.theme.icon(self.icon_name, 20, foreground))
            x += p(27) if self.text else p(20)
        if self.text:
            self.create_text(x, height / 2, text=self.text, fill=foreground, anchor='w',
                             font=self.theme.font(self.size, self.variant == 'active'))
        if self.shortcut:
            self.create_text(width - p(17), height / 2, text=self.shortcut, fill='#c7d9ce', anchor='e', font=self.theme.font(10))
        self.configure_cursor(disabled)

    def configure_cursor(self, disabled):
        super().configure(cursor='arrow' if disabled else 'hand2', takefocus=not disabled)

    def invoke(self, event=None):
        if self.button_state != 'disabled' and self.command:
            self.focus_set()
            self.command()
        return 'break'


class ScrollableFrame(tk.Frame):
    def __init__(self, parent, theme):
        super().__init__(parent, bg=COLORS['bg'])
        self.theme = theme
        self.canvas = tk.Canvas(self, bg=COLORS['bg'], highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview,
                                       style='Workspace.Vertical.TScrollbar')
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.body = tk.Frame(self.canvas, bg=COLORS['bg'])
        self.window = self.canvas.create_window(0, 0, window=self.body, anchor='n')
        self.canvas.bind('<Configure>', self.resize)
        self.body.bind('<Configure>', self.content_resize)

    def resize(self, event):
        self.canvas.itemconfigure(self.window, width=min(event.width, self.theme.px(1500)))
        self.canvas.coords(self.window, event.width / 2, 0)
        self.content_resize()

    def content_resize(self, event=None):
        width = self.canvas.winfo_width()
        height = max(self.body.winfo_reqheight(), self.canvas.winfo_height())
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def wheel(self, delta):
        if self.body.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-int(delta / 120) or (-1 if delta > 0 else 1), 'units')


class DropZone(tk.Canvas):
    def __init__(self, parent, theme, command):
        self.theme, self._image, self._art = theme, None, None
        super().__init__(parent, height=theme.px(274), bg=parent.cget('bg'), highlightthickness=0, borderwidth=0)
        self.choose = Button(self, theme, 'Выбрать файлы', command, 'folder_open', 'primary',
                             height=44, size=12, shortcut='Ctrl O', width=211)
        self.bind('<Configure>', self.draw)

    def illustration(self):
        p = lambda x: round(x * 3)
        image = Image.new('RGBA', (p(80), p(75)))
        for back in (True, False):
            paper = Image.new('RGBA', (p(46), p(58)))
            draw = ImageDraw.Draw(paper)
            draw.rounded_rectangle((0, 0, p(46) - 1, p(58) - 1), radius=p(6),
                                   fill='#e5eddf' if back else 'white', outline='#d2dfc9' if back else '#bfd3b7', width=3)
            if not back:
                font = ImageFont.truetype(str(ASSETS / 'MaterialSymbols.ttf'), p(27))
                draw.text((p(9), p(7)), chr(self.theme.codepoints['image']), font=font, fill='#78a06a')
                draw.line((p(11), p(43), p(34), p(43)), fill='#d6e2cc', width=p(2))
                draw.line((p(11), p(49), p(26), p(49)), fill='#d6e2cc', width=p(2))
            paper = paper.rotate(13 if back else -7, resample=Image.Resampling.BICUBIC, expand=True)
            image.alpha_composite(paper, (p(3 if back else 9), 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((p(48), p(42), p(77), p(71)), fill='#2d7557', outline='#f6f9f1', width=p(3))
        font = ImageFont.truetype(str(ASSETS / 'MaterialSymbols.ttf'), p(17))
        draw.text((p(54), p(46)), chr(self.theme.codepoints['upload']), font=font, fill='white')
        return ImageTk.PhotoImage(image.resize((self.theme.px(80), self.theme.px(75)), Image.Resampling.LANCZOS), master=self)

    def draw(self, event=None):
        p = self.theme.px
        width, height = max(self.winfo_width(), 10), p(274)
        gradient = ImageOps.colorize(Image.radial_gradient('L').resize((width * 2, height * 2)), '#f4f8ef', '#fbfcf8')
        mask = Image.new('L', gradient.size)
        ImageDraw.Draw(mask).rounded_rectangle((p(2) * 2, p(2) * 2, width * 2 - p(2) * 2, height * 2 - p(2) * 2),
                                               radius=p(8) * 2, fill=255)
        background = Image.new('RGB', gradient.size, self.cget('bg'))
        background.paste(gradient, mask=mask)
        gradient = background
        draw = ImageDraw.Draw(gradient)
        dash, step, inset = p(3) * 2, p(6) * 2, p(2) * 2
        line = '#cbd9c5'
        for x in range(inset + p(6) * 2, width * 2 - inset - p(6) * 2, step):
            draw.line((x, inset, min(x + dash, width * 2 - inset), inset), fill=line, width=2)
            draw.line((x, height * 2 - inset, min(x + dash, width * 2 - inset), height * 2 - inset), fill=line, width=2)
        for y in range(inset + p(6) * 2, height * 2 - inset - p(6) * 2, step):
            draw.line((inset, y, inset, y + dash), fill=line, width=2)
            draw.line((width * 2 - inset, y, width * 2 - inset, y + dash), fill=line, width=2)
        radius = p(8) * 2
        for x, y, angle in ((inset, inset, 180), (width * 2 - inset - 2 * radius, inset, 270),
                             (inset, height * 2 - inset - 2 * radius, 90),
                             (width * 2 - inset - 2 * radius, height * 2 - inset - 2 * radius, 0)):
            for offset in (0, 45):
                draw.arc((x, y, x + 2 * radius, y + 2 * radius), angle + offset, angle + offset + 23,
                         fill=line, width=2)
        self._image = ImageTk.PhotoImage(gradient.resize((width, height), Image.Resampling.LANCZOS), master=self)
        self.delete('all')
        self.create_image(0, 0, image=self._image, anchor='nw')
        if self._art is None:
            self._art = self.illustration()
        self.create_image(width / 2, p(58), image=self._art)
        self.create_text(width / 2, p(132), text='Перетащите PDF-файлы сюда', font=self.theme.font(18, True), fill=COLORS['text'])
        self.create_text(width / 2, p(162), text='Выберите один или несколько файлов или папку с PDF',
                         font=self.theme.font(11), fill='#919b89')
        self.choose.place(x=round(width / 2), y=p(188), anchor='n')
        self.create_text(width / 2, p(251), text='Поддерживаются многостраничные PDF',
                         font=self.theme.font(10), fill='#a8afa1')
