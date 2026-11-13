"""UI stand: generates states of the React workspace and screenshots them.

Not a test. Runs DesktopServer + JobService like test_desktop_ui.py, without the native bridge
(file dialogs, clipboard, drop from Explorer), so states that need it are not covered here.
The same states before and after a change, compared by tools/ui_compare.py, show what a change
did to the screen.

    python -X utf8 -B tools/ui_audit_shots.py --out <shots dir> [--work <temp dir>] [--result <folder> ...]
                                             [--only 1380x960|extra]

`--work` keeps the synthetic PDFs, their results and the history between runs (default: a folder
in %TEMP%), so two runs show the same data. `--result` adds already processed result folders,
for example real presentations, to the history; their names never appear in this file.
"""
import argparse
import colorsys
import json
import os
from pathlib import Path
import random
import re
import shutil
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from photo_desktop_server import DesktopServer  # noqa: E402
from photo_edit import load_report  # noqa: E402
from photo_jobs import JobService  # noqa: E402
from test_photos import make_pdf  # noqa: E402

VIEWPORTS = [(780, 620), (960, 1040), (1380, 960), (1920, 1080)]
LONG = ('Вариант 12. Перспективное изображение со стороны набережной реки при вечернем освещении '
        'с благоустройством территории и малыми архитектурными формами')


def picture(seed, size=(600, 400)):
    """A gradient with a shape: unique, not a light empty plate, not a repeated service element."""
    rng = random.Random(seed)
    hue = rng.random()
    image = Image.new('RGB', size)
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        r, g, b = colorsys.hsv_to_rgb(hue, 0.55, 0.35 + 0.4 * y / size[1])
        draw.line([(0, y), (size[0], y)], fill=(int(r * 255), int(g * 255), int(b * 255)))
    for _ in range(6):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        draw.ellipse([x, y, x + rng.randrange(30, 160), y + rng.randrange(30, 120)],
                     fill=tuple(rng.randrange(256) for _ in range(3)))
    return image


def build(root, name, pages):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    source = make_pdf(folder, pages)
    target = folder / f'{name}.pdf'
    source.replace(target)
    for leftover in folder.glob('image-*.png'):
        leftover.unlink()
    return target


def synthetic(root):
    seed = iter(range(1, 100000))
    def img(box, size=(600, 400)):
        return {'box': box, 'picture': picture(next(seed), size)}
    sets = {}
    sets['zero'] = build(root, 'Пустая презентация', [
        {'text': [('Пояснительная записка', 50, 100, 24), ('Только текст, без изображений', 50, 160, 16)]}
        for _ in range(3)])
    many = []
    for page in range(105):
        many.append({'images': [img((40 + 320 * i, 200, 300, 200)) for i in range(3)],
                     'text': [(f'Вариант {page % 4 + 1}', 40, 100, 20)] if page % 5 else []})
    sets['many'] = build(root, 'Большая презентация 315 изображений', many)
    variants = []
    labels = [f'Вариант {n}' for n in range(1, 17)] + ['Вариант 3.1', 'Вариант 3.2', 'Вариант 12.1']
    for label in labels:
        title = LONG.replace('Вариант 12', label) if label.startswith('Вариант 12') else f'{label}. Перспективное изображение'
        variants.append({'images': [img((50, 170, 420, 280)), img((530, 170, 420, 280))],
                         'text': [(title[:95], 50, 120, 16)]})
    sets['variants'] = build(root, 'Девятнадцать вариантов и подварианты', variants)
    long_name = ('Очень длинное название презентации жилого комплекса с подземной парковкой и '
                 'благоустройством дворовой территории корпус 3 этап 2 редакция финальная')
    sets['long'] = build(root, long_name, [
        {'images': [img((50, 170, 800, 500))], 'text': [(LONG[:95], 50, 120, 14)]},
        {'images': [img((50, 170, 800, 500))], 'text': [('Длинноесловобезпробеловдлянаименованияподписиизображения', 50, 120, 14)]}])
    sets['scan'] = build(root, 'Скан без текста', [
        {'images': [img((0, 0, 1000, 800), (1500, 1200))]} for _ in range(2)])
    broken = root / 'Битый файл'
    broken.mkdir(exist_ok=True)
    (broken / 'Битый файл.pdf').write_bytes(b'%PDF-1.7\nnot a real pdf body\n%%EOF\n')
    sets['broken'] = broken / 'Битый файл.pdf'
    return sets


class Stand:
    def __init__(self, out, work, results):
        self.out, self.work = Path(out), Path(work)
        self.out.mkdir(parents=True, exist_ok=True)
        self.results = [Path(item) for item in results]
        self.log = []

    def shot(self, page, screen, state, scale=''):
        size = page.viewport_size
        name = f"{screen}__{state}__{size['width']}x{size['height']}{scale}.png"
        page.screenshot(path=str(self.out / name))
        return name

    def step(self, title, action):
        try:
            action()
            self.log.append(f'ok   {title}')
        except Exception as error:  # one broken state must not stop the others
            self.log.append(f'FAIL {title}: {type(error).__name__}: {str(error).splitlines()[0][:200]}')
            traceback.print_exc()


def wait_idle(jobs, limit=900):
    start = time.time()
    while jobs.busy and time.time() - start < limit:
        time.sleep(0.5)
    jobs.wait(5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--work', default=str(Path(os.environ.get('TEMP', '.')) / 'pdf-photos-ui-shots'))
    parser.add_argument('--result', action='append', default=[])
    parser.add_argument('--only', default='')
    args = parser.parse_args()
    stand = Stand(args.out, args.work, args.result)
    work = Path(args.work)
    data = work / 'synthetic'
    if not (data / '.ready').is_file():
        shutil.rmtree(data, ignore_errors=True)
        data.mkdir(parents=True)
        synthetic(data)
        (data / '.ready').write_text('ok', encoding='utf-8')
    sets = {key: next((data / folder).glob('*.pdf')) for key, folder in {
        'zero': 'Пустая презентация', 'many': 'Большая презентация 315 изображений',
        'variants': 'Девятнадцать вариантов и подварианты', 'scan': 'Скан без текста',
        'broken': 'Битый файл'}.items()}
    sets['long'] = next(p for p in data.glob('Очень длинное*/*.pdf'))
    dist = ROOT / 'frontend' / 'dist'

    # Empty workspace: a separate state file.
    empty_state = work / 'state-empty.json'
    empty_state.unlink(missing_ok=True)
    empty_jobs = JobService(empty_state)
    empty_server = DesktopServer(empty_jobs, dist).start()

    # Full workspace: synthetic results, failures, real results and a 100-entry history.
    full_state = work / 'state-full.json'
    jobs = JobService(full_state)
    processed = json.loads(full_state.read_text(encoding='utf-8')) if full_state.is_file() else {}
    if not processed.get('history'):
        for key in ('zero', 'variants', 'long', 'scan', 'broken'):
            jobs.prepare([str(sets[key])])
            wait_idle(jobs)
            print('processed', key, jobs.snapshot()['job']['outcome'], flush=True)
        jobs.prepare([str(sets['many'])])
        wait_idle(jobs)
        print('processed many', flush=True)
        # 100 history entries: copies of a small result under different names.
        small = next(Path(item['output']) for item in jobs.snapshot()['history'] if 'Скан' in item['name'])
        clones = work / 'history-clones'
        clones.mkdir(exist_ok=True)
        for number in range(1, 91):
            target = clones / f'Презентация {number:03d} — PDF'
            if not target.exists():
                shutil.copytree(small, target)
            report = load_report(target)
            report['output'] = str(target)
            report['source'] = str(clones / f'Презентация {number:03d}.pdf')
            jobs.remember(report)
    if not any(item['name'].startswith('Очень длинное') for item in jobs.snapshot()['history']):
        # The full long name fails on a deep folder (path budget): retry a shorter name near the disk root.
        short_root = Path(os.environ.get('TEMP', work)) / 'pl'
        short_root.mkdir(exist_ok=True)
        target = short_root / (sets['long'].stem[:110] + '.pdf')
        shutil.copy2(sets['long'], target)
        jobs.prepare([str(target)])
        wait_idle(jobs)
        print('processed long (short path)', jobs.snapshot()['job']['outcome'], flush=True)
    for folder in stand.results:
        jobs.remember(load_report(folder))
    server = DesktopServer(jobs, dist).start()
    snapshot = jobs.snapshot()
    history = {item['name']: item['id'] for item in snapshot['history']}
    print('history', len(history), 'failures', len(snapshot.get('failures', [])), flush=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            run_states(stand, browser, empty_server, server, history, args.only)
        finally:
            browser.close()
    empty_server.stop(); server.stop(); empty_jobs.close(); jobs.close()
    log = f'_shots-log-{args.only}.txt' if args.only else '_shots-log.txt'
    (stand.out / log).write_text('\n'.join(stand.log), encoding='utf-8')
    print('\n'.join(stand.log))


def new_page(browser, size, scale=1, motion='reduce'):
    context = browser.new_context(viewport={'width': size[0], 'height': size[1]}, device_scale_factor=scale,
                                  reduced_motion=motion, locale='ru-RU')
    page = context.new_page()
    page.set_default_timeout(4000)
    return context, page


def nav(page, name):
    """Sidebar button; narrow windows hide the sidebar behind «Открыть меню»."""
    button = page.get_by_role('button', name=name, exact=True).first
    if not button.is_visible():
        opener = page.get_by_role('button', name='Открыть меню')
        if opener.count() and opener.first.is_visible():
            opener.first.click()
            page.wait_for_timeout(350)
    button = page.locator('button:visible', has_text=name).first if not button.is_visible() else button
    button.click()


def sidebar_button(page, name):
    """A button of the sidebar; on a narrow window the sidebar opens first."""
    button = page.locator('.sidebar').get_by_role('button', name=name).first
    if not button.is_visible():
        opener = page.get_by_role('button', name='Открыть меню')
        if opener.count() and opener.first.is_visible():
            opener.first.click()
            page.wait_for_timeout(350)
    return button


def open_result(page, server, history, name_part):
    page.goto(server.url)
    nav(page, 'История обработки')
    page.locator('.history-search').fill(name_part)
    page.locator('.history-row').first.click()
    page.locator('.asset-card, .empty, .viewer-content').first.wait_for()
    page.wait_for_timeout(600)


def settle(page, ms=500):
    page.wait_for_timeout(ms)


def run_states(stand, browser, empty_server, server, history, only):
    real = [name for name in history if not name.startswith('Презентация ') and name not in (
        'Пустая презентация.pdf',)]
    def real_names():
        return [n for n in history if n.endswith('.pdf') and not any(n.startswith(p) for p in (
            'Презентация ', 'Пустая', 'Большая', 'Девятнадцать', 'Скан', 'Битый', 'Очень длинное'))]
    reals = real_names()
    print('real results:', len(reals), flush=True)

    for size in VIEWPORTS:
        w, h = size
        if only and only != f'{w}x{h}':
            continue
        context, page = new_page(browser, size)

        def s(screen, state):
            settle(page, 350)
            stand.shot(page, screen, state)

        # ---- Start
        def start_empty():
            page.goto(empty_server.url)
            page.get_by_role('heading', name='Обработка PDF').wait_for()
            s('start', 'empty')
        stand.step(f'start empty {w}', start_empty)

        def start_drag():
            page.evaluate("""() => { const dt = new DataTransfer(); dt.items.add(new File(['x'], 'a.pdf', {type:'application/pdf'}));
                window.dispatchEvent(new DragEvent('dragenter', {dataTransfer: dt, bubbles: true})); }""")
            s('start', 'drag-over')
            page.evaluate("() => window.dispatchEvent(new DragEvent('drop', {bubbles:true}))")
        stand.step(f'start drag {w}', start_drag)

        def start_native_error():
            page.evaluate("() => window.dispatchEvent(new CustomEvent('pdf-photos-error', {detail: 'Не найдено PDF среди перетащенных файлов.'}))")
            s('start', 'drop-unsupported-toast')
        stand.step(f'start unsupported {w}', start_native_error)

        def empty_history():
            nav(page, 'История обработки')
            s('history', 'empty')
        stand.step(f'history empty {w}', empty_history)

        def empty_settings():
            nav(page, 'Настройки')
            s('settings', 'default')
            page.mouse.wheel(0, 3000)
            s('settings', 'scrolled')
        stand.step(f'settings {w}', empty_settings)

        def help_dialog():
            nav(page, 'Как пользоваться')
            page.get_by_role('dialog').wait_for()
            s('help', 'start-topic')
            page.keyboard.press('Escape')
        stand.step(f'help {w}', help_dialog)

        # ---- Work page with history and failures
        def work_full():
            page.goto(server.url)
            page.get_by_role('heading', name='Обработка PDF').wait_for()
            s('start', 'with-history-and-failure')
            page.mouse.wheel(0, 3000)
            s('start', 'with-history-scrolled')
        stand.step(f'work full {w}', work_full)

        def history_full():
            nav(page, 'История обработки')
            s('history', '100-entries')
            page.mouse.wheel(0, 4000)
            s('history', '100-entries-scrolled')
            page.locator('.history-search').fill('несуществующее')
            s('history', 'search-no-match')
            page.locator('.history-search').fill('')
            page.locator('.history-card').first.click(button='right')
            s('history', 'context-menu')
            page.keyboard.press('Escape')
        stand.step(f'history full {w}', history_full)

        # ---- Results
        def result_shots(tag, name_part):
            open_result(page, server, history, name_part)
            s('result', f'{tag}-overview')
            page.mouse.wheel(0, 900)
            s('result', f'{tag}-scrolled')
            page.mouse.wheel(0, -5000)
        for index, name in enumerate(reals):
            stand.step(f'real{index+1} {w}', lambda name=name, index=index: result_shots(f'real{index+1}', name[:20]))
        stand.step(f'variants {w}', lambda: result_shots('variants19', 'Девятнадцать'))
        stand.step(f'long {w}', lambda: result_shots('longnames', 'Очень длинное'))
        stand.step(f'scan {w}', lambda: result_shots('scan', 'Скан'))

        def gallery_many():
            open_result(page, server, history, 'Большая')
            s('result', 'gallery315-top')
            for _ in range(6):
                page.mouse.wheel(0, 2500)
            s('result', 'gallery315-deep')
        stand.step(f'gallery many {w}', gallery_many)

        # Detailed states on the first real result (or the variants one).
        target = reals[0][:20] if reals else 'Девятнадцать'
        def sidebar_sections():
            open_result(page, server, history, target)
            for label, tag in (('Служебные', 'service'), ('Склейки', 'collages'), ('Вырезки', 'cutouts'),
                               ('Убранные', 'removed'), ('На проверку', 'status-review'), ('Без подписи', 'status-unsigned')):
                button = sidebar_button(page, label)
                if button.count() and button.is_visible():
                    button.click()
                    s('result', f'section-{tag}')
        stand.step(f'sections {w}', sidebar_sections)

        def captions_open():
            open_result(page, server, history, target)
            toggle = sidebar_button(page, 'Подписи')
            if toggle.count():
                toggle.click()
            s('sidebar', 'captions-expanded')
        stand.step(f'captions {w}', captions_open)

        def search():
            open_result(page, server, history, target)
            page.get_by_role('searchbox').first.fill('план')
            s('result', 'search-plan')
            page.get_by_role('searchbox').first.fill('zzzz')
            s('result', 'search-no-match')
        stand.step(f'search {w}', search)

        def review_filter():
            open_result(page, server, history, target)
            group = page.get_by_role('group', name='Фильтр по статусу')
            if group.count():
                group.get_by_role('button', name='На проверку').click()
                s('result', 'filter-review')
        stand.step(f'filter {w}', review_filter)

        def card_menu():
            open_result(page, server, history, target)
            page.locator('.asset-card').first.click(button='right')
            s('menu', 'card-context')
            item = page.get_by_role('menuitem', name='Повернуть')
            if item.count():
                item.hover(); settle(page, 300)
                s('menu', 'card-rotate-submenu')
            page.keyboard.press('Escape'); page.keyboard.press('Escape')
            sidebar_button(page, 'Вариант 1').click(button='right')
            s('menu', 'variant-context')
            page.keyboard.press('Escape')
        stand.step(f'menus {w}', card_menu)

        def viewer():
            open_result(page, server, history, target)
            page.locator('.asset-card .cover-button').first.click()
            page.get_by_role('dialog').wait_for()
            settle(page, 800)
            s('viewer', 'image')
            source = page.get_by_role('button', name='Исходная страница')
            if source.count():
                source.first.click(); settle(page, 1000)
                s('viewer', 'source-page')
            page.keyboard.press('Escape')
            review = page.locator('.asset-card:has(.status-badge), .asset-card').nth(6)
            review.locator('.cover-button').click()
            settle(page, 800)
            s('viewer', 'review-candidate')
            page.keyboard.press('Escape')
        stand.step(f'viewer {w}', viewer)

        def crop():
            open_result(page, server, history, target)
            page.locator('.asset-card').nth(4).click(button='right')
            page.get_by_role('menuitem', name=re.compile(r'^(Обрезать|Вырезать)…$')).click()
            page.get_by_role('dialog').wait_for(); settle(page, 800)
            s('crop', 'open')
            page.keyboard.press('Escape')
        stand.step(f'crop {w}', crop)

        def editing():
            open_result(page, server, history, target)
            page.get_by_role('button', name='Редактировать', exact=True).click()
            s('edit', 'mode')
            cards = page.locator('.asset-card')
            cards.nth(0).get_by_role('checkbox').check()
            cards.nth(1).get_by_role('checkbox').check()
            s('edit', 'two-selected')
            page.get_by_role('button', name='Перенести в вариант', exact=True).click()
            s('edit', 'move-menu')
            page.keyboard.press('Escape')
            more = page.get_by_role('button', name='Ещё', exact=True)
            if more.count():
                more.first.click(); s('edit', 'more-menu'); page.keyboard.press('Escape')
            page.get_by_role('button', name='Убрать', exact=True).first.click()
            s('edit', 'after-remove-unsaved')
            nav(page, 'История обработки')
            s('dialog', 'unsaved-leave')
            page.get_by_role('button', name='Вернуться к правкам').click()
            for _ in range(3):
                page.mouse.wheel(0, 700)
            settle(page, 600)
            s('edit', 'dock-floating')
            # The wheel scrolls smoothly and may still move during the shot: jump back at once.
            page.evaluate('window.scrollTo(0, 0)'); settle(page, 300)
            cards.nth(2).get_by_role('checkbox').check()
            page.get_by_role('button', name='Новый вариант').first.click()
            settle(page, 400); s('dialog', 'new-variant'); page.keyboard.press('Escape')
        stand.step(f'editing {w}', editing)

        def review_flow():
            open_result(page, server, history, target)
            page.get_by_role('button', name='Разобрать').click()
            settle(page, 600)
            s('edit', 'review-flow')
        stand.step(f'review {w}', review_flow)

        def collages_cutouts():
            open_result(page, server, history, 'Скан')
            s('result', 'scan-overview2')
            open_result(page, server, history, target)
            sidebar_button(page, 'Вырезки').click()
            button = page.get_by_role('button', name=re.compile(r'^Вырезать (из страниц|со страницы)…$'))
            if button.count():
                button.first.click(); page.locator('dialog.cutout-editor').wait_for(); settle(page, 1200)
                s('cutouts', 'editor')
                page.keyboard.press('Escape')
        stand.step(f'cutouts {w}', collages_cutouts)

        context.close()

    # ---- States, DPR 1.5, motion, dark/forced colors on key screens (1380x960)
    if only and only not in ('extra',):
        return
    context, page = new_page(browser, (1380, 960))
    target = reals[0][:20] if reals else 'Девятнадцать'

    def controls():
        page.goto(empty_server.url)
        choose = page.get_by_role('button', name='Выбрать PDF')
        choose.hover(); stand.shot(page, 'control', 'choose-pdf-hover')
        page.keyboard.press('Tab')
        for _ in range(12):
            if page.evaluate("document.activeElement?.textContent?.includes('Выбрать PDF')"):
                break
            page.keyboard.press('Tab')
        stand.shot(page, 'control', 'choose-pdf-focus-visible')
        page.mouse.move(10, 10)
        open_result(page, server, history, target)
        edit = page.get_by_role('button', name='Редактировать', exact=True)
        edit.hover(); stand.shot(page, 'control', 'edit-hover')
        box = edit.bounding_box(); page.mouse.move(box['x'] + 5, box['y'] + 5); page.mouse.down()
        stand.shot(page, 'control', 'edit-active')
        page.mouse.move(5, 300); page.mouse.up()
        page.locator('.asset-card').first.hover(); stand.shot(page, 'control', 'card-hover')
        # Tab order through the result: record the focused elements.
        page.mouse.click(5, 5)
        order = []
        for _ in range(40):
            page.keyboard.press('Tab')
            order.append(page.evaluate("""() => { const e = document.activeElement; if (!e) return '';
                const r = e.getBoundingClientRect(); const st = getComputedStyle(e);
                return `${e.tagName}.${(e.className||'').toString().split(' ')[0]} «${(e.getAttribute('aria-label')||e.textContent||'').trim().slice(0,40)}» outline=${st.outlineStyle}/${st.outlineWidth} shadow=${st.boxShadow!=='none'} @${Math.round(r.x)},${Math.round(r.y)}`; }"""))
        (stand.out / '_tab-order-result.txt').write_text('\n'.join(order), encoding='utf-8')
        stand.shot(page, 'control', 'focus-after-40-tabs')
        open_result(page, server, history, target)
        page.get_by_role('button', name='Редактировать', exact=True).click()
        settle(page, 400)
        stand.shot(page, 'control', 'save-disabled-no-changes')
        page.locator('.asset-card').first.get_by_role('checkbox').focus()
        page.keyboard.press('Space'); settle(page, 300)
        stand.shot(page, 'control', 'checkbox-focus-checked')
    stand.step('controls', controls)

    def keyboard_gallery():
        open_result(page, server, history, target)
        page.locator('.asset-card .cover-button').first.focus()
        for key in ('ArrowRight', 'ArrowDown', 'Delete', 'F2', 'Control+a', 'Shift+F10'):
            page.keyboard.press(key); settle(page, 250)
            stand.shot(page, 'keyboard', f"gallery-{key.replace('+', '-')}")
            if key == 'Shift+F10':
                page.keyboard.press('Escape')
    stand.step('keyboard gallery', keyboard_gallery)

    def modes():
        for label, emulate in (('dark', {'color_scheme': 'dark'}), ('forced-colors', {'forced_colors': 'active'}),
                               ('contrast-more', {'contrast': 'more'})):
            page.emulate_media(**emulate)
            open_result(page, server, history, target)
            stand.shot(page, 'mode', f'{label}-result')
            page.goto(empty_server.url); settle(page, 500)
            stand.shot(page, 'mode', f'{label}-start')
            page.emulate_media(color_scheme='light', forced_colors='none', contrast='no-preference')
    stand.step('modes', modes)

    def large_text():
        open_result(page, server, history, target)
        page.add_style_tag(content='html{font-size:150% !important}')
        settle(page, 400)
        stand.shot(page, 'mode', 'text-150-result')
        page.goto(empty_server.url)
        page.add_style_tag(content='html{font-size:150% !important}')
        settle(page, 400)
        stand.shot(page, 'mode', 'text-150-start')
    stand.step('large text', large_text)
    context.close()

    for size in ((1380, 960), (960, 1040)):
        context, page = new_page(browser, size, scale=1.5)
        def dpr():
            page.goto(empty_server.url); settle(page, 600)
            stand.shot(page, 'start', 'empty', '@1.5x')
            open_result(page, server, history, target)
            stand.shot(page, 'result', 'real1-overview', '@1.5x')
            page.get_by_role('button', name='Редактировать', exact=True).click()
            page.locator('.asset-card').nth(0).get_by_role('checkbox').check()
            stand.shot(page, 'edit', 'one-selected', '@1.5x')
        stand.step(f'dpr {size}', dpr)
        context.close()

    # Motion on: does GSAP animate when reduced motion is not requested? (timings in the log)
    context, page = new_page(browser, (1380, 960), motion='no-preference')
    def motion():
        page.goto(server.url)
        start = time.time()
        open_result(page, server, history, 'Большая')
        stand.log.append(f'info open 315-card result with motion: {time.time() - start:.2f}s')
        stand.shot(page, 'motion', 'gallery315-opened-with-motion')
    stand.step('motion', motion)
    context.close()


if __name__ == '__main__':
    main()
