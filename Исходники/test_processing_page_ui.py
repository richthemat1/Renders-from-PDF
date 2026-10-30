"""Processing page transitions against the real desktop queue and generated PDFs."""
from pathlib import Path
import tempfile
from threading import Event
import unittest

from playwright.sync_api import expect, sync_playwright

from photo_desktop_server import DesktopServer
from photo_export import extract
from photo_jobs import JobService
from test_photos import make_pdf


class ProcessingPageUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pdf-processing-ui-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.jobs = JobService(self.root / 'workspace.json')
        self.addCleanup(self.jobs.close)
        self.server = DesktopServer(self.jobs, Path(__file__).parent / 'frontend/dist').start()
        self.addCleanup(self.server.stop)
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.server.url)
        expect(self.page.get_by_role('heading', name='Обработка PDF', exact=True)).to_be_visible()

    def source(self, name):
        source = make_pdf(self.root, [{
            'images': [{'box': (50, 170, 800, 500), 'color': '#adc3de'}],
            'text': [('Вариант 1. Южный двор', 50, 120, 18)],
        }])
        target = self.root / (name + '.pdf')
        source.rename(target)
        return target

    def refresh(self):
        self.page.reload()
        expect(self.page.get_by_role('heading', name='Обработка PDF', exact=True)).to_be_visible()

    def block(self, blocked):
        entered, release = Event(), Event()
        self.addCleanup(release.set)

        def delayed(source, output=None, progress=None):
            if source == blocked:
                progress({'stage': 'labels', 'current': 1, 'total': 2, 'found': 0})
                entered.set()
                if not release.wait(20):
                    raise RuntimeError('Test did not release the processing queue')
            return extract(source, output=output, progress=progress)

        self.jobs.extractor = delayed
        return entered, release

    def test_fresh_page_has_one_native_picker_and_no_completed_panel(self):
        expect(self.page.locator('.job-panel')).to_have_count(0)
        expect(self.page.get_by_role('button', name='Открыть результат', exact=True)).to_have_count(0)
        self.page.evaluate('''snapshot => {
            window.pickerCalls = 0;
            window.pywebview = {api: {choose_pdfs: async () => {
                window.pickerCalls += 1;
                return snapshot;
            }}};
        }''', self.jobs.snapshot())
        self.page.get_by_role('button', name='Выбрать PDF', exact=True).click()
        expect(self.page.get_by_role('button', name='Выбрать PDF', exact=True)).to_be_enabled()
        self.assertEqual(self.page.evaluate('window.pickerCalls'), 1)
        expect(self.page.locator('.job-panel')).to_have_count(0)
        self.assertEqual(self.errors, [])

    def test_busy_queue_uses_exact_filename_and_survives_history_navigation(self):
        source = self.source('ЖК · этап 2 — длинное имя, Южный двор')
        entered, release = self.block(source)
        self.jobs.start([source])
        self.assertTrue(entered.wait(10))
        try:
            panel = self.page.locator('.job-panel[data-outcome="processing"]')
            expect(panel).to_be_visible()
            expect(panel).to_contain_text(source.name)
            expect(panel).to_contain_text('Готовлю ваши материалы')
            expect(self.page.get_by_role('button', name='Выбрать PDF', exact=True)).to_have_count(0)
            expect(self.page.locator('.destination').get_by_role('button')).to_be_disabled()
            self.page.get_by_role('button', name='История обработки', exact=True).click()
            expect(self.page.get_by_role('heading', name='История обработки', exact=True)).to_be_visible()
            self.assertTrue(self.jobs.snapshot()['busy'])
            self.assertFalse(self.jobs.snapshot()['cancelling'])
            self.page.get_by_role('button', name='Обработка PDF', exact=True).click()
            expect(panel).to_be_visible()
            expect(panel).to_contain_text(source.name)
        finally:
            release.set()
            self.jobs.wait(15)
        expect(self.page.locator('.job-panel[data-outcome="complete"]')).to_be_visible()
        self.assertEqual(self.errors, [])

    def test_current_result_opens_last_processed_file_after_visiting_older_history(self):
        first, second = self.source('Первый PDF'), self.source('Последний PDF')
        self.jobs.start([first, second])
        state = self.jobs.wait(15)
        self.assertEqual(state['job']['outcome'], 'complete')
        # Backward-compatible path spelling must resolve to the same history ID.
        shown = dict(state, current_output=state['current_output'].replace('\\', '/') + '/')
        self.page.route('**/api/state', lambda route: route.fulfill(json=shown))
        self.refresh()
        panel = self.page.locator('.job-panel[data-outcome="complete"]')
        expect(panel).to_contain_text(second.name)
        # The totals add both files up, so each file of the queue is listed with its own result.
        rows = panel.locator('.processing-batch .history-row')
        expect(rows).to_have_count(2)
        expect(rows.nth(0)).to_contain_text(first.name)
        expect(rows.nth(1)).to_contain_text(second.name)
        older = next(item for item in state['history'] if item['source'] == str(first))
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        self.page.locator('.history-row').filter(has_text=older['name']).click()
        expect(self.page.locator('.asset-card')).to_have_count(1)
        self.page.get_by_role('button', name='Обработка PDF', exact=True).click()
        expect(panel).to_contain_text(second.name)
        self.page.get_by_role('button', name='Открыть результат', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(1)
        expect(self.page.locator('.viewer-heading h1')).to_have_text(second.stem)
        self.assertEqual(self.jobs.snapshot()['current_output'], state['current_output'])
        self.assertEqual(self.errors, [])

    def test_finished_result_keeps_next_pdf_beside_it_and_the_picker_works(self):
        source = self.source('Жилой дом — длинное название презентации для проверки следующего файла')
        self.jobs.start([source])
        state = self.jobs.wait(15)
        self.assertEqual(state['job']['outcome'], 'complete')
        self.refresh()
        result = self.page.locator('.processing-operation')
        next_pdf = self.page.get_by_role('complementary', name='Новая обработка', exact=True)
        # «Открыть результат» is the one main action; the next choice steps back beside it.
        expect(self.page.locator('.processing-page .btn.primary')).to_have_count(1)
        expect(next_pdf.get_by_role('button', name='Выбрать PDF', exact=True)).to_have_class('btn secondary')
        for width, height in ((1600, 1000), (1440, 900), (1100, 760), (800, 640), (780, 620)):
            with self.subTest(viewport=(width, height)):
                self.page.set_viewport_size({'width': width, 'height': height})
                expect(next_pdf).to_be_visible()
                expect(next_pdf.get_by_role('button', name='Выбрать PDF', exact=True)).to_be_in_viewport(ratio=1)
                left, right = result.bounding_box(), next_pdf.bounding_box()
                self.assertAlmostEqual(left['y'], right['y'], delta=2)
                self.assertGreaterEqual(right['x'], left['x'] + left['width'] + 16)
                self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        self.page.evaluate('''snapshot => {
            window.nextPickerCalls = 0;
            window.pywebview = {api: {choose_pdfs: async () => {
                window.nextPickerCalls += 1;
                return snapshot;
            }}};
        }''', state)
        next_pdf.get_by_role('button', name='Выбрать PDF', exact=True).click()
        self.assertEqual(self.page.evaluate('window.nextPickerCalls'), 1)
        expect(next_pdf.get_by_role('button', name='Выбрать PDF', exact=True)).to_be_enabled()
        self.page.keyboard.press('Control+o')
        self.page.wait_for_function('window.nextPickerCalls === 2')
        expect(result).to_contain_text(source.name)
        expect(self.page.get_by_role('button', name='Выбрать PDF', exact=True)).to_have_count(1)
        self.assertEqual(self.errors, [])

    def test_narrow_finished_view_puts_next_pdf_before_the_result(self):
        self.jobs.start([self.source('Узкое окно')])
        self.jobs.wait(15)
        self.page.set_viewport_size({'width': 640, 'height': 620})
        self.refresh()
        next_pdf = self.page.get_by_role('complementary', name='Новая обработка', exact=True)
        expect(next_pdf.get_by_role('button', name='Выбрать PDF', exact=True)).to_be_in_viewport(ratio=1)
        next_rect = next_pdf.bounding_box()
        result_rect = self.page.locator('.processing-operation').bounding_box()
        self.assertLessEqual(next_rect['y'] + next_rect['height'], result_rect['y'])
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))

    def test_failed_new_batch_cannot_display_old_success(self):
        old, broken = self.source('Старый успешный'), self.source('Неудачная очередь')
        self.jobs.start([old])
        self.assertEqual(self.jobs.wait(15)['job']['outcome'], 'complete')

        def failing(*args, **kwargs):
            raise ValueError('Проверочный сбой нового PDF')

        self.jobs.extractor = failing
        self.jobs.start([broken])
        self.jobs.wait(10)
        self.refresh()
        panel = self.page.locator('.job-panel[data-outcome="failed"]')
        expect(panel).to_be_visible()
        expect(panel).not_to_contain_text('Материалы готовы')
        expect(self.page.get_by_role('button', name='Открыть результат', exact=True)).to_have_count(0)
        expect(self.page.get_by_role('region', name='Необработанные файлы')).to_contain_text('Проверочный сбой нового PDF')
        self.assertIsNone(self.jobs.snapshot()['current_output'])
        self.assertEqual(self.errors, [])

    def test_partial_batch_keeps_successful_result_filename_and_action(self):
        good, broken = self.source('Сохранённый PDF'), self.source('Последний неуспешный PDF')

        def mixed(source, **kwargs):
            if source == broken:
                raise ValueError('Ошибка второго файла')
            return extract(source, **kwargs)

        self.jobs.extractor = mixed
        self.jobs.start([good, broken])
        self.assertEqual(self.jobs.wait(15)['job']['outcome'], 'partial')
        self.refresh()
        panel = self.page.locator('.job-panel[data-outcome="partial"]')
        expect(panel).to_contain_text(good.name)
        expect(panel).not_to_contain_text(broken.name)
        expect(panel).not_to_contain_text('Материалы готовы')
        self.page.get_by_role('button', name='Открыть сохранённый результат', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(1)
        self.assertEqual(self.errors, [])

    def test_cancel_during_second_pdf_keeps_first_without_green_success(self):
        first, second = self.source('Уже сохранённый PDF'), self.source('Остановленный PDF')
        entered, release = self.block(second)
        self.jobs.start([first, second])
        self.assertTrue(entered.wait(15))
        try:
            expect(self.page.locator('.job-panel[data-outcome="processing"]')).to_be_visible()
            self.page.get_by_role('button', name='Остановить обработку', exact=True).click()
            self.assertTrue(self.jobs.snapshot()['cancelling'])
        finally:
            release.set()
            self.jobs.wait(15)
        panel = self.page.locator('.job-panel[data-outcome="cancelled"]')
        expect(panel).to_be_visible()
        expect(panel).not_to_contain_text('Материалы готовы')
        expect(panel).to_contain_text(first.name)
        self.page.get_by_role('button', name='Открыть сохранённый результат', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(1)
        self.assertEqual(self.errors, [])
