"""Regressions from the 3.29 UI review, against real PDFs and transactions."""
import json
from pathlib import Path
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from playwright.sync_api import expect

import test_desktop_ui as fixture


class ReviewFixesUI(unittest.TestCase):
    setUpClass = classmethod(fixture.DesktopUI.setUpClass.__func__)
    tearDownClass = classmethod(fixture.DesktopUI.tearDownClass.__func__)
    setUp = fixture.DesktopUI.setUp
    card = fixture.DesktopUI.card
    edit = fixture.DesktopUI.edit

    def save(self):
        with self.page.expect_response(lambda r: r.url.endswith('/apply') and r.status == 200):
            self.page.get_by_role('button', name='Сохранить', exact=True).click()
        expect(self.page.locator('.editor-save')).to_be_disabled()

    def test_source_page_renders_original_slide_and_missing_source_has_its_own_error(self):
        self.card(0).locator('.cover-button').click()
        self.page.get_by_role('button', name='Исходная страница 1', exact=True).click()
        image = self.page.locator('.preview-image-canvas img')
        expect(image).to_have_attribute('data-preview-kind', 'source')
        self.page.wait_for_function('document.querySelector(".preview-image-canvas img")?.naturalWidth > 0')
        size = image.evaluate('(el) => [el.naturalWidth, el.naturalHeight]')
        self.assertAlmostEqual(size[0] / size[1], 1000 / 800, delta=.01,
                               msg='Render the full original slide, not the wide extracted image')
        self.page.get_by_role('button', name='Извлечённое изображение', exact=True).click()
        Path(self.report['source']).unlink()
        self.page.get_by_role('button', name='Исходная страница 1', exact=True).click()
        expect(self.page.get_by_text('Исходный PDF недоступен', exact=True)).to_be_visible()
        expect(self.page.locator('.preview-image-canvas img')).to_have_count(0)
        self.page.get_by_role('button', name='Извлечённое изображение', exact=True).click()
        expect(self.page.locator('.preview-image-canvas img')).to_be_visible()

    def test_source_endpoint_rejects_unknown_result_and_invalid_indices(self):
        result_id = next(iter(self.server.folders))
        for suffix in (f'{result_id}/source/99999', f'{result_id}/source/-1',
                       f'{result_id}/source/0.5', 'unknown/source/0'):
            with self.subTest(suffix=suffix), self.assertRaises(HTTPError):
                urlopen(self.server.url + 'api/results/' + suffix).close()

    def test_changed_original_cannot_be_presented_as_the_source_slide(self):
        source = Path(self.report['source'])
        with source.open('ab') as stream:
            stream.write(b'\n% changed since extraction\n')
        result_id = next(iter(self.server.folders))
        with self.assertRaisesRegex(ValueError, 'изменился после обработки'):
            self.server.source_preview(result_id, 0)

    def test_low_window_keeps_first_image_and_caption_visible(self):
        for width, height in ((800, 640), (780, 620), (1100, 760)):
            self.page.set_viewport_size({'width': width, 'height': height})
            if self.page.get_by_role('button', name='Редактировать', exact=True).count():
                self.edit()
            self.page.evaluate('window.scrollTo(0, 0)')
            rect = self.card(0).locator('.asset-meta').bounding_box()
            self.assertLess(rect['y'] + rect['height'], height - 10)
            self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))

    def test_short_gallery_keeps_inline_tools_when_they_still_fit(self):
        self.page.set_viewport_size({'width': 1380, 'height': 620})
        self.edit()
        self.page.evaluate('scrollTo(0, document.documentElement.scrollHeight)')
        toolbar = self.page.get_by_role('region', name='Инструменты редактора')
        expect(toolbar).not_to_have_css('position', 'fixed')
        expect(toolbar).to_be_in_viewport(ratio=1)

    def test_navigation_groups_keep_the_drawer_open_and_have_names_in_the_rail(self):
        self.page.get_by_role('button', name='Свернуть меню', exact=True).click()
        expect(self.page.get_by_role('button', name='Варианты: 2', exact=True)).to_be_visible()
        expect(self.page.get_by_role('button', name='Подписи: 1', exact=True)).to_be_visible()
        self.page.set_viewport_size({'width': 800, 'height': 640})
        self.page.get_by_role('button', name='Открыть меню', exact=True).click()
        variants = self.page.get_by_role('button', name='Варианты: 2', exact=True)
        variants.click()
        expect(self.page.locator('.sidebar')).to_be_visible()
        expect(self.page.locator('#variant-navigation')).to_be_hidden()
        variants.click()
        expect(self.page.locator('.sidebar')).to_be_visible()
        self.page.locator('nav').get_by_role('button', name='Вариант 1 2', exact=True).click()
        expect(self.page.locator('.sidebar')).not_to_be_visible()

    def test_clear_all_includes_hidden_selection(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.locator('nav').get_by_role('button', name='Вариант 2 1', exact=True).click()
        expect(self.page.locator('.selection-hidden')).to_have_text('Скрыто: 1')
        self.page.get_by_role('button', name='Снять всё выделение', exact=True).click()
        expect(self.page.locator('.selection-count')).to_contain_text('Выбрано: 0')
        self.page.locator('nav').get_by_role('button', name='Все изображения 4', exact=True).click()
        expect(self.page.locator('.asset-card input:checked')).to_have_count(0)

    def test_technical_pdf_failure_is_explained_and_folder_recovery_is_conditional(self):
        def broken(*args, **kwargs):
            raise ValueError('No /Root object! - Is this really a PDF?')
        self.jobs.extractor = broken
        with self.assertLogs('pdf-photos', level='ERROR'):
            self.jobs.start([self.report['source']])
            self.jobs.wait(10)
        self.page.get_by_role('button', name='Обработка PDF', exact=True).click()
        failures = self.page.get_by_role('region', name='Необработанные файлы')
        expect(failures).to_contain_text('Файл может быть повреждён')
        expect(failures.get_by_role('button', name='Выбрать другую папку', exact=True)).to_have_count(0)
        expect(failures.get_by_text('No /Root object!', exact=False)).not_to_be_visible()
        failures.get_by_text('Технические подробности', exact=True).click()
        expect(failures.get_by_text('No /Root object!', exact=False)).to_be_visible()
        def denied(*args, **kwargs):
            raise PermissionError("[Errno 13] Permission denied: 'C:\\Результаты\\файл.pdf'")
        self.jobs.extractor = denied
        with self.assertLogs('pdf-photos', level='ERROR'):
            self.jobs.start([self.report['source']])
            self.jobs.wait(10)
        expect(failures).to_contain_text('Проверьте доступ к папке')
        expect(failures.get_by_role('button', name='Выбрать другую папку', exact=True)).to_be_visible()

    def test_preview_mouse_actions_change_draft_and_can_restore(self):
        self.edit()
        self.card(0).locator('.asset-title button').first.click()
        dialog = self.page.get_by_role('dialog')
        dialog.get_by_role('button', name='Перенести изображение', exact=True).click()
        self.page.get_by_role('menuitem', name='Вариант 2', exact=True).click()
        expect(dialog.locator('.preview-location')).to_contain_text('Вариант 2')
        dialog.get_by_role('button', name='Убрать изображение', exact=True).click()
        expect(dialog.locator('.preview-location')).to_contain_text('Убрано')
        dialog.get_by_role('button', name='Вернуть изображение', exact=True).click()
        expect(dialog.locator('.preview-location')).not_to_contain_text('Убрано')
        dialog.get_by_role('button', name='Закрыть диалог').click()
        self.save()
        saved = json.loads((self.output / 'Отчёт.json').read_text(encoding='utf-8'))
        self.assertFalse(saved['images'][0].get('removed', False))
        self.assertEqual(self.errors, [])

    def test_review_completion_distinguishes_draft_saved_and_search(self):
        report_path = self.output / 'Отчёт.json'
        report = json.loads(report_path.read_text(encoding='utf-8'))
        report['images'][3]['status'] = 'review'
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        self.page.reload()
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        self.page.locator('.history-row').first.click()
        self.page.get_by_role('button', name='Начать проверку', exact=True).click()
        self.edit()
        self.card(3).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Перенести в вариант', exact=True).click()
        self.page.get_by_role('menuitem', name='Вариант 1', exact=True).click()
        expect(self.page.get_by_role('heading', name='Спорные изображения распределены в черновике')).to_be_visible()
        self.save()
        expect(self.page.get_by_role('heading', name='Спорные изображения распределены', exact=True)).to_be_visible()
        self.page.get_by_role('searchbox').fill('нет такого изображения')
        expect(self.page.get_by_role('heading', name='Изображения не найдены', exact=True)).to_be_visible()


if __name__ == '__main__':
    unittest.main()
