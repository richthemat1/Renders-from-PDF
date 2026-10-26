"""End-to-end React editor against real PDFs and the loopback desktop API."""
import json
from pathlib import Path
import tempfile
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

    def save(self):
        self.page.get_by_role('button', name='Сохранить', exact=True).click()
        expect(self.page.locator('.edit-panel-top strong')).to_have_text('Редактирование результата')

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
        self.page.get_by_role('combobox', name='Перенести в вариант').select_option('2')
        self.save()
        report = load_report(self.output)
        self.assertEqual(report['revision'], 1)
        self.assertEqual([image['variant'] for image in report['images'][:3]], [2, 2, 2])
        self.assertEqual(len(PdfReader(self.output / report['images'][2]['file']).pages), 3)
        self.assertTrue((self.output / report['edits'][-1]['backup'] / 'Отчёт.json').is_file())
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
