"""End-to-end React editor against real PDFs and the loopback desktop API."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader

from photo_edit import load_report
from photo_export import extract
from photo_jobs import JobService
from photo_desktop_server import DesktopServer
from test_photos import make_pdf


class DesktopUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdf-photos-react-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = extract(make_pdf(self.root, [
            {'images': [{'box': (50, 170, 800, 500), 'color': color}], 'text': [(title, 50, 120, 18)]}
            for title, color in [('Вариант 1. Северный двор', '#e4b5a3'),
                                 ('Вариант 1. Южный двор', '#a4cbb7'),
                                 ('Вариант 2. Восточный двор', '#adc3de'),
                                 ('Западный двор', '#d9cfaf')]]))
        self.output = Path(self.report['output'])
        self.jobs = JobService(self.root / 'state.json')
        self.addCleanup(self.jobs.close)
        self.jobs.remember(self.report, processed=True)
        self.server = DesktopServer(self.jobs, Path(__file__).parent / 'frontend' / 'dist').start()
        self.addCleanup(self.server.stop)
        self.context = self.browser.new_context(viewport={'width': 1380, 'height': 1000}, reduced_motion='reduce')
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.server.url)
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        self.page.locator('.history-row').first.click()
        expect(self.page.locator('.asset-card')).to_have_count(4)

    def card(self, index):
        return self.page.locator(f'.asset-card[data-id="{index}"]')

    def edit(self):
        self.page.get_by_role('button', name='Редактировать', exact=True).click()

    def extend_gallery(self, count=40):
        """Long UI fixture: reuse real PDF pages without changing their files."""
        report = load_report(self.output)
        originals = report['images']
        report['images'] = [dict(originals[i % len(originals)]) for i in range(count)]
        (self.output / 'Отчёт.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        self.page.reload()
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        self.page.locator('.history-row').first.click()

    def save(self):
        with self.page.expect_response(lambda response: response.url.endswith('/apply') and response.request.method == 'POST'):
            self.page.get_by_role('button', name='Сохранить', exact=True).click()
        expect(self.page.locator('.status-note')).to_have_text('Изменения сохранены')

    def test_search_filters_and_full_viewer_work_without_js_errors(self):
        self.page.get_by_role('searchbox').fill('Северный')
        expect(self.page.locator('.asset-card')).to_have_count(1)
        self.card(0).locator('.cover-button').click()
        expect(self.page.get_by_role('dialog')).to_be_visible()
        self.page.get_by_role('button', name='Закрыть диалог').click()
        self.page.get_by_role('searchbox').fill('')
        self.page.locator('nav').get_by_role('button', name='Вариант 1 2', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(2)
        self.assertEqual(self.errors, [])

    def test_batch_move_save_rewrites_real_pdf_and_preserves_backup(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.card(1).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Перенести в вариант', exact=True).click()
        self.page.get_by_role('menuitem', name='Вариант 2', exact=True).click()
        self.save()
        report = load_report(self.output)
        self.assertEqual(report['revision'], 1)
        self.assertEqual([image['variant'] for image in report['images'][:3]], [2, 2, 2])
        self.assertEqual(len(PdfReader(self.output / report['images'][2]['file']).pages), 3)
        self.assertTrue((self.output / report['edits'][-1]['backup'] / 'Отчёт.json').is_file())
        self.assertEqual(self.errors, [])

    def test_sidebar_keeps_selection_from_different_sections_for_one_batch_save(self):
        self.edit()
        navigation = self.page.locator('nav')
        navigation.get_by_role('button', name='Вариант 1 2', exact=True).click()
        self.card(0).get_by_role('checkbox').check()
        navigation.get_by_role('button', name='Вариант 2 1', exact=True).click()
        navigation.get_by_role('button', name='Вариант 1 2', exact=True).click()
        expect(self.card(0).get_by_role('checkbox')).to_be_checked()
        navigation.get_by_role('button', name='Вариант 2 1', exact=True).click()
        expect(self.page.locator('.selection-count')).to_contain_text('Выбрано: 1')
        expect(self.page.locator('.selection-hidden')).to_have_text('Скрыто: 1')
        self.card(2).get_by_role('checkbox').check()
        navigation.get_by_role('button', name='Западный двор 1', exact=True).click()
        self.card(3).get_by_role('checkbox').check()
        expect(self.page.locator('.selection-count')).to_contain_text('Выбрано: 3')
        navigation.get_by_role('button', name='Все изображения 4', exact=True).click()
        for index in (0, 2, 3):
            expect(self.card(index).get_by_role('checkbox')).to_be_checked()
        expect(self.card(1).get_by_role('checkbox')).not_to_be_checked()
        self.page.get_by_role('button', name='Перенести в вариант', exact=True).click()
        self.page.get_by_role('menuitem', name='Вариант 2', exact=True).click()
        self.save()
        report = load_report(self.output)
        self.assertEqual([image['variant'] for image in report['images']], [2, 1, 2, 2])
        self.assertEqual(len(PdfReader(self.output / report['images'][2]['file']).pages), 3)
        self.assertEqual(self.errors, [])

    def test_visible_selection_toggle_and_control_a_preserve_hidden_files(self):
        self.edit()
        navigation = self.page.locator('nav')
        navigation.get_by_role('button', name='Вариант 2 1', exact=True).click()
        self.card(2).get_by_role('checkbox').check()
        navigation.get_by_role('button', name='Западный двор 1', exact=True).click()
        choose = self.page.get_by_role('button', name='Выбрать видимые', exact=True)
        expect(choose).to_be_visible()
        choose.click()
        expect(self.page.locator('.selection-count')).to_contain_text('Выбрано: 2')
        self.page.get_by_role('button', name='Снять выделение', exact=True).click()
        expect(self.page.locator('.selection-count')).to_contain_text('Выбрано: 1')
        expect(self.card(3).get_by_role('checkbox')).not_to_be_checked()
        choose.focus()
        self.page.keyboard.press('Control+a')
        expect(self.page.locator('.selection-count')).to_contain_text('Выбрано: 2')
        navigation.get_by_role('button', name='Все изображения 4', exact=True).click()
        expect(self.card(2).get_by_role('checkbox')).to_be_checked()
        expect(self.card(3).get_by_role('checkbox')).to_be_checked()
        choose.click()
        expect(self.page.locator('.selection-count')).to_contain_text('Выбрано: 4')
        self.page.get_by_role('button', name='Снять выделение', exact=True).click()
        expect(self.page.locator('.selection-count')).to_contain_text('Выбрано: 0')

    def test_search_status_and_shift_selection_keep_files_outside_the_current_section(self):
        self.edit()
        self.card(2).get_by_role('checkbox').check()
        self.page.get_by_role('searchbox').fill('Северный')
        expect(self.page.locator('.selection-count')).to_contain_text('Выбрано: 1')
        self.card(0).get_by_role('checkbox').check()
        self.page.get_by_role('searchbox').fill('')
        self.page.locator('nav').get_by_role('button', name='Вариант 1 2', exact=True).click()
        self.card(0).locator('.cover-button').click(modifiers=['Shift'])
        self.card(1).locator('.cover-button').click(modifiers=['Shift'])
        expect(self.page.locator('.selection-count')).to_contain_text('Выбрано: 3')
        expect(self.page.locator('.selection-hidden')).to_have_text('Скрыто: 1')
        self.page.get_by_role('group', name='Фильтр по статусу').get_by_role('button', name='Без подписи', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(0)
        expect(self.page.locator('.selection-hidden')).to_have_text('Скрыто: 3')
        self.page.keyboard.press('Escape')
        expect(self.page.locator('.selection-count')).to_contain_text('Выбрано: 0')
        self.page.locator('nav').get_by_role('button', name='Все изображения 4', exact=True).click()
        expect(self.page.locator('.asset-card input:checked')).to_have_count(0)
        self.assertEqual(self.errors, [])

    def test_delete_undo_and_new_variant_keep_image_indices(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Убрать', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(3)
        self.page.get_by_role('button', name='Отменить шаг').click()
        expect(self.page.locator('.asset-card')).to_have_count(4)
        self.card(3).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Новый вариант').click()
        self.page.get_by_label('Номер варианта').fill('8')
        self.page.get_by_role('button', name='Создать и перенести').click()
        self.save()
        report = load_report(self.output)
        self.assertEqual(report['images'][3]['variant'], 8)
        self.assertTrue((self.output / 'Вариант 8' / 'Вариант 8.pdf').is_file())

    def test_native_drag_order_and_rename_group(self):
        self.edit()
        self.card(1).drag_to(self.card(0))
        self.save()
        report = load_report(self.output)
        self.assertEqual([image['pdf_page'] for image in report['images'][:2]], [2, 1])
        self.page.locator('nav').get_by_role('button', name='Вариант 1 2', exact=True).click()
        self.page.get_by_role('button', name='Переименовать группу').click()
        self.page.get_by_label('Новое имя').fill('Первый корпус')
        self.page.get_by_role('button', name='Переименовать', exact=True).click()
        expect(self.page.get_by_role('dialog')).to_have_count(0)
        self.assertTrue((self.output / 'Первый корпус' / 'Первый корпус.pdf').is_file())

    def test_internal_drag_does_not_cross_the_native_file_bridge(self):
        from webview.dom.element import Element
        from photo_desktop import bind_file_drop

        # Run pywebview's real DOM event registration in Chromium. Only the OS
        # transport is replaced; every bridged event is observed at its boundary.
        self.page.evaluate('''() => {
            window.nativeDropEvents = [];
            window.pywebview = {
                _eventHandlers: {},
                _jsApiCallback: (_, params) => window.nativeDropEvents.push(params.event.type)
            };
        }''')
        host = SimpleNamespace(dom=SimpleNamespace(_elements={}))
        host.evaluate_js = lambda code: self.page.evaluate('(code) => (0, eval)(code)', code)
        host.run_js = host.evaluate_js
        host.dom.document = Element(host, 'document')
        bind_file_drop(host, lambda _: None)
        self.edit()
        self.card(0).drag_to(self.card(2))
        self.assertEqual(self.page.evaluate('window.nativeDropEvents'), [])
        self.save()
        self.assertEqual(load_report(self.output)['images'][0]['variant'], 2)

        # A cancelled drop on blank space must not serialize the gallery either.
        self.page.evaluate('''() => {
            const dataTransfer = new DataTransfer();
            dataTransfer.setData('application/x-pdf-photos', '0');
            document.body.dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer}));
        }''')
        self.assertEqual(self.page.evaluate('window.nativeDropEvents'), [])
        # Explorer files still reach the receiver, without bridging mouse moves.
        prevented = self.page.evaluate('''() => {
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(new File(['%PDF'], 'presentation.pdf', {type: 'application/pdf'}));
            const event = new DragEvent('dragover', {bubbles: true, cancelable: true, dataTransfer});
            document.body.dispatchEvent(event);
            document.body.dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer}));
            return event.defaultPrevented;
        }''')
        self.assertTrue(prevented)
        self.assertEqual(self.page.evaluate('window.nativeDropEvents'), ['drop'])

    def test_selection_actions_stay_accessible_at_the_end_of_a_large_gallery(self):
        self.extend_gallery()
        self.edit()
        self.card(39).get_by_role('checkbox').check()
        self.page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
        toolbar = self.page.get_by_role('region', name='Инструменты редактора')
        expect(toolbar.get_by_role('button', name='Убрать', exact=True)).to_be_in_viewport()
        toolbar.get_by_role('button', name='Убрать', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(39)
        expect(toolbar.get_by_role('button', name='Сохранить', exact=True)).to_be_in_viewport()
        self.page.set_viewport_size({'width': 800, 'height': 620})
        self.card(38).get_by_role('checkbox').check()
        self.page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
        toolbar.get_by_role('button', name='Ещё', exact=True).click()
        expect(self.page.get_by_role('menuitem', name='Убрать', exact=True)).to_be_in_viewport()
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))

    def test_toolbar_docks_below_the_gallery_and_returns_without_losing_selection(self):
        self.extend_gallery()
        self.page.set_viewport_size({'width': 1380, 'height': 620})
        self.edit()
        toolbar = self.page.get_by_role('region', name='Инструменты редактора')
        expect(toolbar).not_to_have_css('position', 'fixed')
        self.card(0).get_by_role('checkbox').check()
        self.page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
        expect(toolbar).to_have_css('position', 'fixed')
        geometry = toolbar.evaluate('''(el) => {
            const rect = el.getBoundingClientRect();
            const main = document.querySelector('.workspace-main').getBoundingClientRect();
            return {height:rect.height, bottom:innerHeight-rect.bottom,
                centered: Math.abs((rect.left+rect.right)/2-(main.left+main.right)/2),
                blur:getComputedStyle(el).backdropFilter};
        }''')
        self.assertLessEqual(geometry['height'], 70)
        self.assertGreaterEqual(geometry['bottom'], 16)
        self.assertLess(geometry['centered'], 2)
        self.assertIn('blur(', geometry['blur'])
        expect(self.card(0).get_by_role('checkbox')).to_be_checked()
        self.page.evaluate('window.scrollTo(0, 0)')
        expect(toolbar).not_to_have_css('position', 'fixed')
        expect(self.card(0).get_by_role('checkbox')).to_be_checked()
        self.assertEqual(self.errors, [])

    def test_compact_toolbar_moves_and_saves_through_the_upward_overflow_menu(self):
        self.page.set_viewport_size({'width': 800, 'height': 620})
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
        toolbar = self.page.get_by_role('region', name='Инструменты редактора')
        expect(toolbar).to_have_css('position', 'fixed')
        more = toolbar.get_by_role('button', name='Ещё', exact=True)
        more.click()
        menu = self.page.get_by_role('menu', name='Ещё', exact=True)
        expect(menu).to_be_visible()
        bounds, trigger = menu.bounding_box(), more.bounding_box()
        self.assertLessEqual(bounds['y'] + bounds['height'], trigger['y'])
        self.assertGreaterEqual(bounds['x'], 16)
        self.assertLessEqual(bounds['x'] + bounds['width'], 784)
        expect(self.page.get_by_role('menuitem', name='Раньше в варианте', exact=True)).to_be_disabled()
        self.page.get_by_role('menuitem', name='Перенести в вариант', exact=True).click()
        self.page.get_by_role('menuitem', name='Вариант 2', exact=True).click()
        expect(self.page.get_by_role('menu')).to_have_count(0)
        more.click()
        expect(self.page.get_by_role('menuitem', name='Убрать', exact=True)).to_be_disabled()
        expect(self.page.get_by_role('menuitem', name='Отменить шаг', exact=True)).to_be_enabled()
        self.page.keyboard.press('Escape')
        expect(more).to_be_focused()
        self.save()
        report = load_report(self.output)
        self.assertEqual(report['images'][0]['variant'], 2)
        self.assertEqual(len(PdfReader(self.output / report['images'][0]['file']).pages), 2)
        toast = self.page.locator('.toast')
        expect(toast).to_be_visible()
        self.assertLessEqual(toast.bounding_box()['y'] + toast.bounding_box()['height'], toolbar.bounding_box()['y'])
        self.assertEqual(self.errors, [])

    def test_rename_shows_the_whole_long_filename_without_horizontal_scrolling(self):
        self.edit()
        name = 'Страница 003 — 001 — История рассмотрения вопроса — Концепция, рассмотренная у Мэра Москвы — дополнение к проекту'
        self.page.get_by_role('button', name='Переименовать PDF 4', exact=True).click()
        field = self.page.get_by_label('Новое имя')
        field.fill(name)
        fits = field.evaluate('(el) => el.scrollWidth <= el.clientWidth && el.scrollHeight <= el.clientHeight')
        self.assertTrue(fits, 'Полное имя должно быть видно без прокрутки поля')
        self.page.get_by_role('button', name='Переименовать', exact=True).click()
        expect(self.page.get_by_role('dialog')).to_have_count(0)
        self.page.get_by_role('button', name='Переименовать PDF 4', exact=True).click()
        expect(self.page.get_by_label('Новое имя')).to_have_value(name)

    def test_move_menu_keyboard_navigation_does_not_clear_selection(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        trigger = self.page.get_by_role('button', name='Перенести в вариант', exact=True)
        trigger.focus()
        self.page.keyboard.press('ArrowDown')
        expect(self.page.get_by_role('menu', name='Перенести в вариант')).to_be_visible()
        self.page.keyboard.press('Escape')
        expect(trigger).to_be_focused()
        expect(self.card(0).get_by_role('checkbox')).to_be_checked()
        self.page.keyboard.press('Enter')
        self.page.get_by_role('menuitem', name='Вариант 2', exact=True).focus()
        self.page.keyboard.press('Enter')
        expect(self.page.get_by_role('menu')).to_have_count(0)
        self.save()
        self.assertEqual(load_report(self.output)['images'][0]['variant'], 2)

    def test_conflict_preserves_draft_until_explicit_reload(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Убрать', exact=True).click()
        latest = load_report(self.output)
        latest['revision'] += 1
        (self.output / 'Отчёт.json').write_text(json.dumps(latest, ensure_ascii=False), encoding='utf-8')
        self.page.get_by_role('button', name='Сохранить', exact=True).click()
        expect(self.page.locator('.edit-panel .inline-warning')).to_be_visible()
        expect(self.page.locator('.asset-card')).to_have_count(3)
        self.page.get_by_role('button', name='Обновить', exact=True).click()
        self.page.get_by_role('button', name='Загрузить', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(4)

    def test_unsaved_navigation_and_narrow_window_keep_controls_accessible(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Убрать', exact=True).click()
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        expect(self.page.get_by_role('dialog')).to_be_visible()
        self.page.get_by_role('button', name='Вернуться к правкам').click()
        expect(self.page.locator('.asset-card')).to_have_count(3)
        self.page.set_viewport_size({'width': 800, 'height': 760})
        overflow = self.page.evaluate('document.documentElement.scrollWidth > innerWidth')
        self.assertFalse(overflow, 'Горизонтальный скролл скрывает управление')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
