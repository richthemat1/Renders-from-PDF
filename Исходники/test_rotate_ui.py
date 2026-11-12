"""«Повернуть» in the right-click menu of a card and of the preview: the turn is written at once."""
from pathlib import Path
import re
import tempfile
import unittest

from playwright.sync_api import expect
from pypdf import PdfReader

from photo_edit import load_report
from photo_jobs import JobService
from photo_desktop_server import DesktopServer
import test_desktop_ui as desktop_ui
from test_photos import EditFixture


def rotations(path):
    with PdfReader(path) as reader:
        return [page.rotation for page in reader.pages]


class RotateUI(EditFixture, unittest.TestCase):
    setUpClass = classmethod(desktop_ui.DesktopUI.setUpClass.__func__)
    tearDownClass = classmethod(desktop_ui.DesktopUI.tearDownClass.__func__)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdf-photos-rotate-ui-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.output, self.ids = self.export(root)
        self.jobs = JobService(root/'state.json')
        self.addCleanup(self.jobs.close)
        self.jobs.remember(load_report(self.output), processed=True)
        self.server = DesktopServer(self.jobs, Path(__file__).parent/'frontend'/'dist').start()
        self.addCleanup(self.server.stop)
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 960}, reduced_motion='reduce')
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.server.url)
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        self.page.locator('.history-row').first.click()
        self.page.locator('nav').get_by_role('button', name='Вариант 1', exact=False).click()
        expect(self.page.locator('.asset-card')).to_have_count(2)

    def turn(self, menu, label):
        menu.get_by_role('menuitem', name='Повернуть', exact=True).click()
        with self.page.expect_response(lambda r: r.url.endswith('/apply') and r.status == 200):
            menu.get_by_role('menuitem', name=label, exact=True).click()

    def test_card_turns_at_once_without_edit_mode(self):
        pdf = self.output/'Вариант 1'/'Вариант 1.pdf'
        self.page.locator('.asset-card').first.click(button='right')
        self.turn(self.page.get_by_role('menu'), 'Вправо на 90°')
        expect(self.page.get_by_text('Изображение повёрнуто и записано в PDF', exact=False)).to_be_visible()
        self.assertEqual(rotations(pdf), [90, 0])
        report = load_report(self.output)
        self.assertEqual((report['images'][self.ids['red']]['turn'], report['revision']), (90, 1))
        # Nothing waits to be saved; the card shows the turned thumbnail.
        expect(self.page.get_by_role('button', name='Сохранить', exact=True)).to_have_count(0)
        expect(self.page.locator('.asset-card').first.locator('img')).to_have_attribute(
            'src', re.compile('поворот|%D0%BF%D0%BE%D0%B2%D0%BE%D1%80%D0%BE%D1%82'))
        self.assertEqual(self.errors, [])

    def test_selected_cards_turn_together_and_from_the_preview(self):
        pdf = self.output/'Вариант 1'/'Вариант 1.pdf'
        self.page.get_by_role('button', name='Редактировать', exact=True).click()
        cards = self.page.locator('.asset-card')
        cards.nth(0).locator('.cover-button').click(modifiers=['Shift'])
        cards.nth(1).locator('.cover-button').click(modifiers=['Shift'])
        cards.nth(1).click(button='right')
        self.turn(self.page.get_by_role('menu', name='Выбрано: 2'), 'На 180°')
        self.assertEqual(rotations(pdf), [180, 180])
        # The preview turns its own picture.
        cards.nth(1).click(button='right')
        self.page.get_by_role('menu').get_by_role('menuitem', name='Просмотреть', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        expect(dialog.locator('.preview-image-canvas img')).to_be_visible()
        dialog.locator('.preview-image-scroll').click(button='right')
        self.turn(self.page.get_by_role('menu'), 'Влево на 90°')
        self.assertEqual(rotations(pdf), [180, 90])
        self.assertEqual(self.errors, [])

    def test_unsaved_edits_are_saved_first(self):
        before = (self.output/'Вариант 1'/'Вариант 1.pdf').read_bytes()
        self.page.get_by_role('button', name='Редактировать', exact=True).click()
        self.page.locator('.asset-card').nth(1).click(button='right')
        self.page.get_by_role('menuitem', name='Убрать', exact=True).click()
        self.page.locator('.asset-card').first.click(button='right')
        self.page.get_by_role('menuitem', name='Повернуть', exact=True).click()
        self.page.get_by_role('menuitem', name='Вправо на 90°', exact=True).click()
        expect(self.page.get_by_text('Сначала сохраните или отмените правки', exact=False)).to_be_visible()
        self.assertEqual((self.output/'Вариант 1'/'Вариант 1.pdf').read_bytes(), before)
        self.assertEqual(load_report(self.output)['revision'], 0)
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
