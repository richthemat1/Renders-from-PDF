"""Real-browser regressions for search, drag and drop, and saving manual corrections."""
from pathlib import Path
import re
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader

import photo_edit
from photo_export import extract
from photo_server import PreviewServer
from test_photos import make_pdf


class GalleryRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except BaseException:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix='pdf-photos-browser-test-')
        self.addCleanup(self.workspace.cleanup)
        pages = [{'images': [{'box': (50, 170, 800, 500), 'color': color}],
                  'text': [(title, 50, 120, 18)]} for title, color in [
                      ('Вариант 1. Северный двор', '#ff0000'),
                      ('Вариант 1. Южный двор', '#00ff00'),
                      ('Вариант 2. Восточный двор', '#0000ff'),
                      ('Западный двор', '#ffff00')]]
        report = extract(make_pdf(self.workspace.name, pages))
        self.output = Path(report['output'])
        self.server = PreviewServer()
        self.addCleanup(self.server.stop)
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 1000})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.server.register(self.output))
        self.page.get_by_role('button', name='Редактировать', exact=True).click()

    def card(self, index):
        return self.page.locator(f'.card[data-id="{index}"]')

    def test_search_in_edit_mode_ignores_move_menu_options(self):
        self.page.get_by_role('searchbox').fill('Вариант 1')
        shown = self.page.locator('.card:visible').evaluate_all('(cards)=>cards.map(c=>+c.dataset.id)')
        self.assertEqual(shown, [0, 1])
        expect(self.page.locator('#found')).to_have_text('Найдено: 2')

    def test_search_after_drag_keeps_counters_and_new_variant_drop_target_working(self):
        self.card(0).drag_to(self.card(2))
        self.page.get_by_role('searchbox').fill('двор')
        self.assertEqual(self.errors, [])
        expect(self.page.locator('#found')).to_have_text('Найдено: 4')
        transfer = self.page.evaluate_handle('new DataTransfer()')
        self.card(1).dispatch_event('dragstart', {'dataTransfer': transfer})
        try:
            expect(self.page.locator('.nav-new')).to_be_visible()
        finally:
            self.card(1).dispatch_event('dragend')
            transfer.dispose()

    def test_move_to_a_filtered_variant_keeps_the_matching_card_visible(self):
        for bulk in (False, True):
            with self.subTest(bulk=bulk):
                if bulk:
                    self.page.locator('#discard').click()
                    self.page.get_by_role('button', name='Редактировать', exact=True).click()
                self.page.get_by_role('searchbox').fill('Северный')
                if bulk:
                    self.card(0).locator('.cover').click()
                    self.page.locator('[data-bulk="move"]').select_option('2')
                else:
                    self.card(0).locator('select[data-act="move"]').select_option('2')
                expect(self.card(0)).to_be_visible()
                self.assertEqual(self.card(0).locator('xpath=ancestor::section').get_attribute('data-variant'), '2')
                expect(self.page.locator('#found')).to_have_text('Найдено: 1')

    def test_pending_save_blocks_more_changes_and_commits_the_submitted_state(self):
        entered, release = Event(), Event()
        original = photo_edit.write_gallery

        def slow_gallery(folder, report):
            entered.set()
            if not release.wait(15):
                raise RuntimeError('Test did not release the pending save')
            return original(folder, report)

        self.card(0).locator('[data-act="remove"]').click()
        with patch.object(photo_edit, 'write_gallery', slow_gallery):
            try:
                self.page.locator('#save').click()
                self.assertTrue(entered.wait(10))
                expect(self.card(2).locator('[data-act="remove"]')).to_be_disabled(timeout=1000)
                expect(self.card(2).locator('select[data-act="move"]')).to_be_disabled()
                expect(self.page.locator('#discard')).to_be_disabled()
                self.assertEqual(self.card(2).get_attribute('draggable'), 'false')
                self.page.evaluate('document.querySelector(\'.card[data-id="2"] [data-act="remove"]\').click()')
                self.page.locator('body').press('Delete')
                self.assertNotIn('removed', self.card(2).get_attribute('class'))
            finally:
                release.set()
            expect(self.page.locator('body')).to_have_attribute('data-revision', '1')
        self.assertEqual(self.card(0).count(), 0)
        self.assertEqual(self.card(2).count(), 1)
        saved = photo_edit.load_report(self.output)
        self.assertEqual([i for i, entry in enumerate(saved['images']) if entry.get('removed')], [0])
        self.assertEqual(self.errors, [])

    def test_failed_save_unlocks_the_editor_without_discarding_changes(self):
        self.card(0).locator('[data-act="remove"]').click()
        with patch('photo_server.apply_edits', side_effect=photo_edit.EditError('Проверочная ошибка')):
            self.page.locator('#save').click()
            expect(self.page.locator('#toast')).to_have_text('Проверочная ошибка')
        expect(self.card(2).locator('[data-act="remove"]')).to_be_enabled()
        expect(self.page.locator('#save')).to_be_enabled()
        self.assertIn('removed', self.card(0).get_attribute('class'))
        self.assertEqual(photo_edit.load_report(self.output)['revision'], 0)
        self.page.locator('#save').click()
        expect(self.page.locator('body')).to_have_attribute('data-revision', '1')

    def test_moving_to_a_variant_scrolls_to_the_destination(self):
        pages = [{'images': [{'box': (50, 170, 800, 500), 'color': '#ff0000'}], 'text': [('Вариант 1', 50, 120, 18)]}]
        pages += [{'images': [{'box': (50, 170, 800, 500), 'color': '#0000ff'}]} for _ in range(24)]
        with tempfile.TemporaryDirectory(prefix='pdf-photos-scroll-test-') as root:
            output = Path(extract(make_pdf(root, pages))['output'])
            self.page.goto(self.server.register(output))
            self.page.get_by_role('button', name='Редактировать', exact=True).click()
            on_screen = '(e)=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight}'
            unsigned = self.page.locator('.group.unsigned .card')
            for way in ('sidebar', 'sidebar-new', 'bulk', 'single'):
                with self.subTest(way=way):
                    card = unsigned.last
                    card.scroll_into_view_if_needed()
                    moved = self.page.locator(f'.card[data-id="{card.get_attribute("data-id")}"]')
                    if way == 'single':
                        card.locator('select[data-act="move"]').select_option('1')
                    elif way == 'bulk':
                        card.locator('.cover').click()
                        unsigned.nth(unsigned.count()-2).locator('.cover').click(modifiers=['Shift'])
                        self.page.locator('[data-bulk="move"] option').last.wait_for(state='attached')
                        self.page.locator('[data-bulk="move"]').select_option('1')
                    else:
                        target = '.nav-new .nav-item' if way == 'sidebar-new' else '.nav-item[href="#g2"]'
                        transfer = self.page.evaluate_handle('new DataTransfer()')
                        card.dispatch_event('dragstart', {'dataTransfer': transfer})
                        self.page.locator(target).dispatch_event('dragover', {'dataTransfer': transfer})
                        self.page.locator(target).dispatch_event('drop', {'dataTransfer': transfer})
                        card.dispatch_event('dragend', {'dataTransfer': transfer})
                        transfer.dispose()
                    self.page.wait_for_function(
                        f'(()=>{{const e=document.querySelector(\'.card[data-id="{moved.get_attribute("data-id")}"]\');'
                        f'const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight}})()', timeout=3000)
                    self.assertTrue(moved.evaluate(on_screen))
                    expect(moved).to_have_class(re.compile(r'\barrived\b'))
                    self.assertTrue(moved.locator('xpath=ancestor::section').get_attribute('data-variant'))
                    self.page.keyboard.press('Escape')
            self.assertEqual(self.errors, [])


    def test_unnumbered_perspective_views_are_shown_and_edited_in_variant_one(self):
        pages = [{'images': [{'box': (50, 170, 800, 500), 'color': color}],
                  **({'text': [(title, 50, 120, 18)]} if title else {})} for title, color in [
                      ('Вариант 1', '#ff0000'), ('Перспективное изображение №1', '#00ff00'),
                      ('Перспективное изображение №2 рендер', '#0000ff'), (None, '#ffff00')]]
        with tempfile.TemporaryDirectory(prefix='pdf-photos-perspective-test-') as root:
            output = Path(extract(make_pdf(root, pages))['output'])
            self.page.goto(self.server.register(output))
            variants_block = self.page.locator('.nav-group', has=self.page.locator('h3', has_text='Варианты'))
            expect(variants_block.locator('.nav-item')).to_have_text([re.compile('Вариант 1')])
            variant_one = self.page.locator('section[data-variant="1"]')
            expect(variant_one.locator('.card')).to_have_count(3)
            self.page.get_by_role('button', name='Редактировать', exact=True).click()
            self.page.locator('.card[data-id="3"] .cover').click()
            options = self.page.locator('[data-bulk="move"] option').all_text_contents()
            self.assertNotIn('Перспективные изображения', options)
            self.page.locator('[data-bulk="move"]').select_option('1')
            expect(variant_one.locator('.card')).to_have_count(4)
            self.page.locator('#save').click()
            expect(self.page.locator('body')).to_have_attribute('data-revision', '1')
            with PdfReader(output/'Варианты'/'Вариант 1'/'Вариант 1.pdf') as reader:
                self.assertEqual(len(reader.pages), 4)
            self.assertFalse((output/'Все остальное').exists())
            self.assertEqual(self.errors, [])

    def test_variant_is_renamed_from_the_preview(self):
        self.card(3).locator('[data-act="remove"]').click()
        self.page.locator('section[data-variant="1"] [data-rename-variant]').click()
        expect(self.page.locator('#toast')).to_contain_text('Сначала сохраните или отмените')
        expect(self.page.locator('#rename')).not_to_be_visible()
        self.card(3).locator('[data-act="remove"]').click()
        self.page.locator('section[data-variant="1"] [data-rename-variant]').click()
        dialog = self.page.locator('#rename')
        expect(dialog).to_be_visible()
        expect(dialog.locator('input')).to_have_value('Вариант 1')
        dialog.locator('input').fill('Вариант 2')
        dialog.locator('#rn-ok').click()
        expect(dialog.locator('#rn-error')).to_contain_text('уже существует')
        dialog.locator('input').fill('Вариант 1 — Северный')
        dialog.locator('input').press('Enter')
        expect(self.page.locator('body')).to_have_attribute('data-revision', '1')
        expect(self.page.locator('section[data-variant="1"] h2')).to_have_text('Вариант 1 — Северный')
        expect(self.page.locator('#toast')).to_contain_text('Переименовано')
        expect(self.page.get_by_role('button', name='Готово')).to_be_visible()
        self.assertTrue((self.output/'Варианты'/'Вариант 1 — Северный'/'Вариант 1 — Северный.pdf').is_file())
        options = self.card(3).locator('select[data-act="move"] option').all_text_contents()
        self.assertIn('Вариант 1 — Северный', options)
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
