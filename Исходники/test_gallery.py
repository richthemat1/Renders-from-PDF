"""Real-browser regressions for search, drag and drop, and saving manual corrections."""
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
