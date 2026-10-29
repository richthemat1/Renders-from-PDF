"""The «Склейки» section and the manual cut editor against the real desktop UI."""
from pathlib import Path
import tempfile
import unittest

from playwright.sync_api import expect

from photo_edit import load_report
from photo_export import extract
from photo_jobs import JobService
from photo_desktop_server import DesktopServer
import test_desktop_ui as desktop_ui
from test_scans import collage_pdf, row_pdf


class CollagesUI(unittest.TestCase):
    setUpClass = classmethod(desktop_ui.DesktopUI.setUpClass.__func__)
    tearDownClass = classmethod(desktop_ui.DesktopUI.tearDownClass.__func__)

    def open(self, source):
        self.temp = tempfile.TemporaryDirectory(prefix='pdf-photos-collages-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.report = extract(source(root))
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
        expect(self.page.locator('.status-note')).to_have_text('Изменения сохранены')

    def choose(self, field, option):
        """Pick an option of an app dropdown: its list opens under the field."""
        field.click()
        self.page.get_by_role('option', name=option, exact=True).click()

    def collages(self):
        self.page.locator('nav').get_by_role('button', name='Склейки', exact=False).click()
        return self.page.get_by_role('region', name='Склейки')

    def test_suspected_row_is_cut_in_the_editor_saved_and_returned_whole(self):
        self.open(lambda root: row_pdf(root, gap=0))
        expect(self.page.locator('.review-summary-collages')).to_contain_text('Склейки: 1, проверить 1')
        expect(self.page.locator('.asset-card .collage-badge')).to_have_text('Возможно, склейка')
        section = self.collages()
        card = section.locator('.collage-card')
        expect(card).to_have_count(1)
        expect(card.locator('.collage-frame.is-suggested')).to_have_count(4)
        card.get_by_role('button', name='Разрезать…').click()
        editor = self.page.locator('dialog.cut-editor')
        expect(editor.locator('.cut-list li')).to_have_count(4)
        # Renders edge to edge: trimming light margins would eat a light sky.
        expect(editor.get_by_label('Обрезать светлые поля')).not_to_be_checked()
        # The direction turns the lines already there, and a step back turns them again.
        editor.get_by_role('button', name='Горизонтальная', exact=True).click()
        expect(editor.locator('.cut-line.vertical')).to_have_count(0)
        expect(editor.locator('.cut-line.horizontal')).to_have_count(3)
        expect(editor.locator('.cut-list li').first).to_contain_text('2752 × 384 px')
        expect(editor.locator('.cut-note')).to_contain_text('4 ряда поровну')
        self.page.keyboard.press('Control+z')
        expect(editor.locator('.cut-line.vertical')).to_have_count(3)
        expect(editor.get_by_role('button', name='Вертикальная', exact=True)).to_have_attribute('aria-pressed', 'true')
        # Enter on a button presses that button, not «Разрезать».
        editor.get_by_role('button', name='Отмена', exact=True).focus()
        self.page.keyboard.press('Enter')
        expect(editor).to_have_count(0)
        expect(card).to_contain_text('Возможно, склейка')
        # Nothing to save: the section shows no save line, nor the gallery's card tools and review.
        expect(self.page.locator('.section-save-bar')).to_have_count(0)
        expect(self.page.locator('.edit-panel, .review-summary')).to_have_count(0)
        card.get_by_role('button', name='Разрезать…').click()
        expect(editor.locator('.cut-list li')).to_have_count(4)
        editor.get_by_role('button', name='Убрать линии').click()
        expect(editor.locator('.cut-list li')).to_have_count(1)
        editor.get_by_role('button', name='Найти швы').click()
        expect(editor.locator('.cut-note')).to_contain_text('стыки кадров одинакового размера')
        expect(editor.locator('.cut-list li')).to_have_count(4)
        expect(editor.locator('.cut-list li').first).to_contain_text('688 × 1536 px')
        editor.get_by_role('button', name='Разрезать на 4 кадра').click()
        expect(editor).to_have_count(0)
        expect(card).to_contain_text('не сохранено')
        self.save()
        saved = load_report(self.output)
        frames = [entry for entry in saved['images'] if not entry.get('removed')]
        self.assertEqual([entry['size'] for entry in frames], [[688, 1536]]*4)
        self.assertTrue(saved['images'][0]['collage_replaced'])
        expect(card).to_contain_text('Разрезано вручную · 4 кадра')
        self.page.locator('nav').get_by_role('button', name='Убранные', exact=False).click()
        expect(self.page.locator('.asset-card')).to_have_count(0)
        section = self.collages()
        section.get_by_role('button', name='Вернуть целиком').click()
        self.save()
        whole = [entry for entry in load_report(self.output)['images'] if not entry.get('removed')]
        self.assertEqual([entry['size'] for entry in whole], [[2752, 1536]])
        expect(section.locator('.collage-card')).to_contain_text('Целиком')
        self.assertEqual(self.errors, [])

    def test_confident_automatic_cut_is_ready_and_recut_with_light_margins_trimmed(self):
        self.open(collage_pdf)
        # Wide white gutters and a variant for every frame: nothing for a person to check.
        expect(self.page.locator('.review-summary-collages')).to_have_text('Склейки: 2Открыть склейки')
        expect(self.page.locator('.asset-card .collage-badge').first).to_have_text('Кадр 1 из склейки')
        section = self.collages()
        cards = section.locator('.collage-card')
        expect(cards).to_have_count(2)
        expect(cards.first).to_contain_text('Проверено · 2 кадра')
        cards.first.get_by_role('button', name='Изменить разрезку').click()
        editor = self.page.locator('dialog.cut-editor')
        expect(editor.locator('.cut-line.horizontal')).to_have_count(1)
        expect(editor.get_by_label('Обрезать светлые поля')).to_be_checked()
        expect(editor.get_by_label('Куда поместить кадр 2')).to_have_attribute('data-value', '1')
        self.choose(editor.get_by_label('Куда поместить кадр 2'), 'Вариант 2')
        editor.get_by_role('button', name='Разрезать на 2 кадра').click()
        # Before saving too, the editor shows the frames where they went and the trimming chosen.
        cards.first.get_by_role('button', name='Изменить разрезку').click()
        expect(editor.get_by_label('Обрезать светлые поля')).to_be_checked()
        expect(editor.get_by_label('Куда поместить кадр 2')).to_have_attribute('data-value', '2')
        editor.get_by_role('button', name='Отмена', exact=True).click()
        self.save()
        saved = load_report(self.output)
        first = saved['collages'][0]
        self.assertEqual((first['state'], first['checked']), ('manual', True))
        frames = [entry for entry in saved['images'] if entry.get('cut') == 'manual' and not entry.get('removed')]
        self.assertEqual(len(frames), 2)
        for frame, (top, bottom) in zip(frames, [(8, 420), (462, 872)]):
            # The line ran through the middle of the white band; trimming left one render each.
            self.assertLessEqual(abs(frame['size'][1]-(bottom-top)), 8)
        self.assertEqual([frame['variant'] for frame in frames], [1, 2])
        sizes = [frame['size'] for frame in frames]
        # A re-cut keeps every frame where it is now, with the same trimming.
        cards.first.get_by_role('button', name='Изменить разрезку').click()
        expect(editor.get_by_label('Обрезать светлые поля')).to_be_checked()
        expect(editor.get_by_label('Куда поместить кадр 1')).to_have_attribute('data-value', '1')
        expect(editor.get_by_label('Куда поместить кадр 2')).to_have_attribute('data-value', '2')
        self.choose(editor.get_by_label('Куда поместить кадр 1'), 'Без варианта')
        expect(editor.get_by_label('Куда поместить кадр 1')).to_have_attribute('data-value', '__misc')
        editor.get_by_role('button', name='Разрезать на 2 кадра').click()
        self.save()
        saved = load_report(self.output)
        frames = [entry for entry in saved['images'] if entry.get('collage') == first['id'] and entry.get('cut') == 'manual'
                  and not entry.get('removed') and not entry.get('collage_replaced')]
        self.assertEqual([frame['variant'] for frame in frames], [None, 2])
        self.assertEqual([frame['size'] for frame in frames], sizes)
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
