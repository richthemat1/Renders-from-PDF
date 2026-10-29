"""The «Вырезки» section and the page editor against the real desktop UI."""
from pathlib import Path
import tempfile
import unittest

from playwright.sync_api import expect

from photo_edit import load_report
from photo_export import extract
from photo_jobs import JobService
from photo_desktop_server import DesktopServer
import test_desktop_ui as desktop_ui
from test_cutouts import cutout_pdf


class CutoutsUI(unittest.TestCase):
    setUpClass = classmethod(desktop_ui.DesktopUI.setUpClass.__func__)
    tearDownClass = classmethod(desktop_ui.DesktopUI.tearDownClass.__func__)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdf-photos-cutouts-ui-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.report = extract(cutout_pdf(root))
        self.output = Path(self.report['output'])
        self.jobs = JobService(root/'state.json')
        self.addCleanup(self.jobs.close)
        self.jobs.remember(self.report, processed=True)
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
        expect(self.page.locator('.asset-card').first).to_be_visible()

    def save(self):
        with self.page.expect_response(lambda r: r.url.endswith('/apply') and r.status == 200):
            self.page.get_by_role('button', name='Сохранить', exact=True).click()
        expect(self.page.locator('.editor-save')).to_be_disabled()

    def section(self):
        self.page.locator('nav').get_by_role('button', name='Вырезки', exact=False).click()
        return self.page.get_by_role('region', name='Вырезки')

    def at(self, x, y):
        """A point of the open page in shares of its width and height, once the editor has laid the page out."""
        stage = self.page.locator('.cutout-stage')
        box = None
        for _ in range(50):
            previous, box = box, stage.bounding_box()
            if previous == box and box['width'] > 200:
                break
            self.page.wait_for_timeout(50)
        return box['x']+box['width']*x, box['y']+box['height']*y

    def drag(self, start, end):
        self.page.mouse.move(*start)
        self.page.mouse.down()
        self.page.mouse.move((start[0]+end[0])/2, (start[1]+end[1])/2, steps=4)
        self.page.mouse.move(*end, steps=4)
        self.page.mouse.up()

    def test_a_render_is_cut_out_from_under_its_plan_kept_with_a_snapshot_and_its_picture_returned(self):
        section = self.section()
        expect(section).to_contain_text('Вырезок пока нет')
        section.get_by_role('button', name='Вырезать из страниц…').first.click()
        editor = self.page.locator('dialog.cutout-editor')
        expect(editor.locator('.cutout-found')).to_have_count(1)
        expect(editor.locator('.cutout-found')).to_contain_text('Найденная картинка · Вариант 2')
        # A double click frames the whole picture; its right edge is pulled in to leave the plan out.
        self.page.mouse.dblclick(*self.at(.3, .4))
        rows = editor.locator('.cutout-row')
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text('800 × 600 px · из картинки')
        handle = editor.locator('.cutout-frame.is-selected .cutout-handle.e').bounding_box()
        self.drag((handle['x']+5, handle['y']+5), self.at(.48, .5))
        expect(rows.first).to_contain_text('560 × 600 px')
        expect(editor.get_by_label('Куда поместить вырезку 1')).to_have_value('__keep')
        editor.get_by_role('button', name='Добавить 1 вырезку').click()
        expect(editor).to_have_count(0)
        card = section.locator('.cutout-card')
        expect(card).to_contain_text('не сохранено')
        self.save()
        saved = load_report(self.output)
        live = [entry for entry in saved['images'] if not entry.get('removed')]
        self.assertEqual([(entry['size'], entry['variant'], entry['cutout']) for entry in live], [([560, 600], 2, '1-v0')])
        self.assertEqual(saved['images'][0]['cutout_replaced'], '1-v0')
        expect(card).to_contain_text('1 вырезка')
        expect(self.page.locator('nav').get_by_role('button', name='Убранные', exact=False)).to_contain_text('0')
        # Again: the saved frame comes back; a frame over the vector scheme is a page snapshot.
        card.get_by_role('button', name='Изменить вырезки', exact=True).click()
        expect(editor.locator('.cutout-frame')).to_have_count(1)
        expect(editor.locator('.cutout-found')).to_contain_text('Исходная картинка')
        self.drag(self.at(.72, .24), self.at(.92, .5))
        expect(rows.nth(1)).to_contain_text('снимок страницы')
        expect(editor.get_by_label('Куда поместить вырезку 2')).to_have_value('__misc')
        editor.get_by_label('Куда поместить вырезку 2').select_option('2')
        # Delete and a step back.
        self.page.keyboard.press('Delete')
        expect(rows).to_have_count(1)
        self.page.keyboard.press('Control+z')
        expect(rows).to_have_count(2)
        editor.get_by_role('button', name='Применить').click()
        self.save()
        saved = load_report(self.output)
        snapshot = next(entry for entry in saved['images'] if entry.get('cutout') == '1-p')
        self.assertEqual((snapshot['method'], snapshot['variant']), ('page_render', 2))
        expect(section.locator('.cutout-card')).to_have_count(2)
        # The picture comes back where its cutout was; the snapshot stays.
        section.locator('.cutout-card').first.get_by_role('button', name='Вернуть картинку').click()
        self.save()
        saved = load_report(self.output)
        live = [index for index, entry in enumerate(saved['images']) if not entry.get('removed')]
        self.assertEqual([saved['images'][index].get('cutout') for index in live], [None, '1-p'])
        self.assertEqual(saved['images'][0]['variant'], 2)
        expect(section.locator('.cutout-card')).to_have_count(1)
        self.assertEqual(self.errors, [])

    def test_cut_a_part_from_a_card_and_without_the_source_the_editor_explains_why(self):
        self.page.get_by_role('button', name='Редактировать', exact=True).click()
        card = self.page.locator('.asset-card').first
        card.hover()
        card.get_by_role('button', name='Вырезать часть…').click()
        editor = self.page.locator('dialog.cutout-editor')
        expect(editor.locator('.cutout-row')).to_have_count(1)
        expect(editor.locator('.cut-note')).to_contain_text('Рамка поставлена по краям картинки')
        editor.get_by_role('button', name='Отмена', exact=True).click()
        expect(self.page.locator('.editor-save')).to_be_disabled()
        # Pages already asked for may still be rendering from the presentation.
        self.page.wait_for_load_state('networkidle')
        for attempt in range(20):
            try:
                Path(self.report['source']).rename(Path(self.temp.name)/'moved.pdf')
                break
            except PermissionError:
                self.page.wait_for_timeout(100)
        self.section().get_by_role('button', name='Вырезать из страниц…').first.click()
        expect(editor.locator('.cutout-loading')).to_contain_text('Исходный PDF недоступен')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
