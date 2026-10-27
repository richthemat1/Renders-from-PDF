"""UX review regressions for application navigation and recovery flows."""
import json
import re
from threading import Event
import shutil
import unittest

from playwright.sync_api import expect
import test_desktop_ui as desktop_ui
from photo_export import extract
from photo_edit import load_report


class AppUX(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        desktop_ui.DesktopUI.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        desktop_ui.DesktopUI.tearDownClass.__func__(cls)

    setUp = desktop_ui.DesktopUI.setUp
    card = desktop_ui.DesktopUI.card
    edit = desktop_ui.DesktopUI.edit

    def test_control_o_with_unsaved_edits_offers_save_or_discard(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Убрать', exact=True).click()
        self.page.keyboard.press('Control+o')
        dialog = self.page.get_by_role('dialog')
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role('button', name='Сохранить и продолжить', exact=True)).to_be_visible()
        dialog.get_by_role('button', name='Вернуться к правкам', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(3)

    def test_escape_in_editor_does_not_cancel_a_background_queue(self):
        release = Event()
        self.addCleanup(release.set)
        def delayed(source, **kwargs):
            release.wait(15)
            return extract(source, **kwargs)
        self.jobs.extractor = delayed
        self.jobs.start([self.report['source']])
        try:
            expect(self.page.get_by_role('button', name='Обработка PDF', exact=True).locator('.spin')).to_be_visible()
            self.edit()
            self.card(0).get_by_role('checkbox').check()
            cancelled = []
            self.page.on('request', lambda request: cancelled.append(request.url) if request.url.endswith('/api/cancel') else None)
            self.page.keyboard.press('Escape')
            self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
            self.assertEqual(cancelled, [])
            self.assertFalse(self.jobs.snapshot()['cancelling'])
        finally:
            release.set()
            self.jobs.wait(10)

    def test_navigation_to_history_resets_document_scroll(self):
        for index in range(15):
            folder = self.root / f'history-{index}'
            shutil.copytree(self.output, folder)
            report = dict(self.report, output=str(folder))
            (folder / 'Отчёт.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
            self.jobs.remember(report, processed=True)
        self.page.set_viewport_size({'width':800,'height':620})
        self.edit()
        self.page.evaluate('scrollTo(0,document.documentElement.scrollHeight)')
        self.assertGreater(self.page.evaluate('scrollY'), 0)
        self.page.get_by_role('button', name='Открыть меню', exact=True).click()
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        expect(self.page.get_by_role('heading', name='История обработки', exact=True)).to_be_visible()
        self.page.wait_for_function('scrollY === 0')
        self.assertEqual(self.page.evaluate('scrollY'), 0)

    def test_open_current_result_reports_a_missing_history_record(self):
        self.jobs.start([self.report['source']])
        self.jobs.wait(15)
        snapshot = dict(self.jobs.snapshot(), history=[], current_output=str(self.output))
        self.page.route('**/api/state', lambda route: route.fulfill(json=snapshot))
        with self.page.expect_response(lambda response: response.url.endswith('/api/state')):
            pass
        self.page.get_by_role('button', name='Обработка PDF', exact=True).click()
        self.page.get_by_role('button', name='Открыть результат', exact=True).click()
        expect(self.page.locator('.toast.error')).to_be_visible()
        expect(self.page.locator('.toast.error')).to_contain_text('истории')

    def test_save_and_continue_persists_before_navigation(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.keyboard.press('Delete')
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        dialog.get_by_role('button', name='Сохранить и продолжить', exact=True).click()
        expect(self.page.get_by_role('heading', name='История обработки', exact=True)).to_be_visible()
        report = load_report(self.output)
        self.assertEqual(report['revision'], 1)
        self.assertTrue(report['images'][0]['removed'])

    def test_failed_save_and_continue_keeps_the_draft_and_dialog(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.keyboard.press('Delete')
        self.page.route('**/api/results/*/apply', lambda route: route.fulfill(status=500, json={'error':'Проверочный сбой сохранения'}))
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        dialog.get_by_role('button', name='Сохранить и продолжить', exact=True).click()
        expect(dialog.get_by_role('alert')).to_contain_text('Не удалось сохранить')
        dialog.get_by_role('button', name='Вернуться к правкам', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(3)
        expect(self.page.locator('.status-note')).to_contain_text('Есть изменения')
        self.assertEqual(load_report(self.output)['revision'], 0)

    def test_fresh_workspace_has_no_empty_job_panel_and_marks_external_drop(self):
        snapshot = dict(self.jobs.snapshot(), history=[], current_output=None, progress=0, logs=[])
        self.page.route('**/api/state', lambda route: route.fulfill(json=snapshot))
        with self.page.expect_response(lambda response: response.url.endswith('/api/state')):
            pass
        self.page.get_by_role('button', name='Обработка PDF', exact=True).click()
        expect(self.page.locator('.job-panel')).to_have_count(0)
        self.page.evaluate('''() => {
            const transfer = new DataTransfer();
            transfer.items.add(new File(['pdf'], 'review.pdf', {type:'application/pdf'}));
            window.dispatchEvent(new DragEvent('dragenter', {dataTransfer:transfer}));
        }''')
        expect(self.page.locator('.upload-zone')).to_have_class(re.compile(r'\bis-drag-over\b'))
        expect(self.page.locator('#upload-title')).to_have_text('Отпустите PDF для обработки')
        self.page.evaluate("window.dispatchEvent(new Event('dragend'))")
        expect(self.page.locator('#upload-title')).to_have_text('Перетащите PDF сюда')

    def test_duplicate_dialog_can_open_the_previous_real_result(self):
        self.jobs.prepare([self.report['source']])
        dialog = self.page.get_by_role('dialog', name='Этот PDF уже обработан')
        expect(dialog).to_be_visible(timeout=5000)
        dialog.get_by_role('button', name='Открыть прошлый результат', exact=True).click()
        expect(dialog).to_have_count(0)
        expect(self.page.locator('.asset-card')).to_have_count(4)
        self.assertFalse(self.jobs.snapshot()['busy'])
        self.assertIsNone(self.jobs.snapshot()['pending_repeat'])

    def test_failed_files_are_visible_with_retry_and_forget_actions(self):
        def failing(*args, **kwargs):
            raise ValueError('Проверочная ошибка PDF')
        self.jobs.extractor = failing
        self.jobs.start([self.report['source']])
        self.jobs.wait(10)
        self.page.get_by_role('button', name='Обработка PDF', exact=True).click()
        failures = self.page.get_by_role('region', name='Необработанные файлы')
        expect(failures).to_contain_text('Проверочная ошибка PDF')
        expect(failures.get_by_role('button', name='Повторить', exact=True)).to_be_visible()
        expect(failures.get_by_role('button', name='Выбрать другую папку', exact=True)).to_be_visible()
        failures.get_by_role('button', name='Убрать ошибку из истории:', exact=False).click()
        expect(failures).to_have_count(0)
        self.assertEqual(self.jobs.snapshot()['failures'], [])
