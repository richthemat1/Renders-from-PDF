"""«Обрезать…» in the right-click menu of a card: a frame over the picture, saved into its variant at once."""
from pathlib import Path
import tempfile
import unittest

from playwright.sync_api import expect

from photo_edit import apply_edits, load_report
from photo_jobs import JobService
from photo_desktop_server import DesktopServer
import test_desktop_ui as desktop_ui
from test_crop_in_place import page_sizes
from test_photos import EditFixture


class CropUI(EditFixture, unittest.TestCase):
    setUpClass = classmethod(desktop_ui.DesktopUI.setUpClass.__func__)
    tearDownClass = classmethod(desktop_ui.DesktopUI.tearDownClass.__func__)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdf-photos-crop-ui-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.output, _ = self.export(root)
        # Variant 3 is the user's own: copies of the red and the blue render.
        apply_edits(self.output, {'revision': 0, 'copies': [0, 2], 'variants': {'1': [0, 1], '2': [2], '3': [4, 5]}})
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
        self.page.locator('nav').get_by_role('button', name='Вариант 3', exact=False).click()
        expect(self.page.locator('.asset-card')).to_have_count(2)

    def pull(self, handle, dx, dy):
        """Drag a handle of the frame by a share of the picture's width and height."""
        stage = self.page.locator('.crop-stage').bounding_box()
        box = self.page.locator(f'.crop-stage .cutout-handle.{handle}').bounding_box()
        x, y = box['x']+box['width']/2, box['y']+box['height']/2
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x+stage['width']*dx/2, y+stage['height']*dy/2, steps=3)
        self.page.mouse.move(x+stage['width']*dx, y+stage['height']*dy, steps=3)
        self.page.mouse.up()

    def crop_first_card(self):
        self.page.locator('.asset-card').first.click(button='right')
        self.page.get_by_role('menuitem', name='Обрезать…', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='Обрезать изображение')
        expect(dialog.locator('.crop-stage img')).to_be_visible()
        return dialog

    def test_crop_replaces_the_card_in_its_variant_and_is_saved_at_once(self):
        original = (self.output/'Вариант 1'/'Вариант 1.pdf').read_bytes()
        dialog = self.crop_first_card()
        expect(dialog.locator('.crop-size')).to_contain_text('600 × 400')
        self.pull('se', -.5, -.25)
        expect(dialog.locator('.crop-size')).to_contain_text('300 × 300')
        with self.page.expect_response(lambda r: r.url.endswith('/apply') and r.status == 200):
            dialog.get_by_role('button', name='Обрезать и сохранить', exact=True).click()
        expect(dialog).to_have_count(0)
        # Still two cards, the cropped one first; nothing is left to save and no whole picture beside it.
        expect(self.page.locator('.asset-card')).to_have_count(2)
        expect(self.page.get_by_role('button', name='Сохранить', exact=True)).to_have_count(0)
        self.assertEqual(page_sizes(self.output/'Вариант 3'/'Вариант 3.pdf'), [(300, 300), (600, 400)])
        self.assertEqual(self.colors(self.output/'Вариант 3'/'Вариант 3.pdf'), ['red', 'blue'])
        self.assertEqual((self.output/'Вариант 1'/'Вариант 1.pdf').read_bytes(), original)
        # Again on the same card: the whole picture with the frame where it was left; pulled back, it returns whole.
        dialog = self.crop_first_card()
        expect(dialog.locator('.crop-size')).to_contain_text('300 × 300')
        self.pull('se', .5, .25)
        expect(dialog.locator('.crop-size')).to_contain_text('600 × 400')
        with self.page.expect_response(lambda r: r.url.endswith('/apply') and r.status == 200):
            dialog.get_by_role('button', name='Обрезать и сохранить', exact=True).click()
        self.assertEqual(page_sizes(self.output/'Вариант 3'/'Вариант 3.pdf'), [(600, 400), (600, 400)])
        self.assertEqual(self.errors, [])

    def test_unsaved_edits_are_saved_first_and_escape_changes_nothing(self):
        before = (self.output/'Вариант 3'/'Вариант 3.pdf').read_bytes()
        dialog = self.crop_first_card()
        self.pull('se', -.5, -.25)
        self.page.keyboard.press('Escape')
        expect(dialog).to_have_count(0)
        self.assertEqual((self.output/'Вариант 3'/'Вариант 3.pdf').read_bytes(), before)
        self.page.get_by_role('button', name='Редактировать', exact=True).click()
        self.page.locator('.asset-card').nth(1).click(button='right')
        self.page.get_by_role('menuitem', name='Убрать', exact=True).click()
        self.page.locator('.asset-card').first.click(button='right')
        self.page.get_by_role('menuitem', name='Обрезать…', exact=True).click()
        expect(self.page.get_by_text('Сначала сохраните или отмените правки', exact=False)).to_be_visible()
        expect(self.page.get_by_role('dialog', name='Обрезать изображение')).to_have_count(0)
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
