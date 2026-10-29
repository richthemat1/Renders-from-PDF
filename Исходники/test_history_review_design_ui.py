"""Figma history cards and review disclosure, using real exported PDF fixtures."""
import json
import shutil
import unittest

from playwright.sync_api import expect
from photo_edit import load_report
import test_desktop_ui as fixture


class HistoryReviewDesignUI(unittest.TestCase):
    setUpClass = classmethod(fixture.DesktopUI.setUpClass.__func__)
    tearDownClass = classmethod(fixture.DesktopUI.tearDownClass.__func__)
    setUp = fixture.DesktopUI.setUp
    card = fixture.DesktopUI.card

    def history(self):
        self.page.get_by_role('button', name='Открыть историю обработки', exact=True).click()
        expect(self.page.locator('.history-page')).to_be_visible()

    def warnings(self, errors=None):
        report = load_report(self.output)
        report['warnings'] = [{'page': page, 'reason':
            'OCR: Между прямоугольниками нет простого однотонного фона; требуется проверка.'}
            for page in range(1, 5)]
        report['errors'] = errors or []
        (self.output / 'Отчёт.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        self.page.reload()
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        self.page.locator('.history-row').first.click()

    def test_history_cards_keep_counts_search_opening_and_independent_folder_action(self):
        copied = self.root / 'Другая выгрузка'
        shutil.copytree(self.output, copied)
        report = load_report(copied)
        report['output'] = str(copied)
        report['source'] = str(self.root / 'Второй объект.pdf')
        (copied / 'Отчёт.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        self.jobs.remember(report)
        self.history()
        expect(self.page.locator('.history-card')).to_have_count(2)
        cards = self.page.locator('.history-card')
        boxes = [cards.nth(index).bounding_box() for index in range(2)]
        self.assertAlmostEqual(boxes[0]['y'], boxes[1]['y'], delta=2)
        self.assertGreater(boxes[1]['x'], boxes[0]['x'] + boxes[0]['width'])
        footer = cards.first.locator('.history-actions').bounding_box()
        self.assertAlmostEqual(footer['width'], boxes[0]['width'] - 2, delta=2)
        self.assertAlmostEqual(footer['x'], boxes[0]['x'] + 1, delta=2)
        expect(cards.first.locator('.history-card-metrics')).to_contain_text('4')
        self.page.get_by_role('searchbox', name='Найти результат').fill('Второй объект')
        expect(cards).to_have_count(1)
        self.page.evaluate('''() => { window.openedFolders = [];
            window.pywebview = {api: {open_history_folder: async id => {window.openedFolders.push(id); return true;}}};
        }''')
        cards.first.get_by_role('button', name='Открыть папку:', exact=False).click()
        self.page.wait_for_function('window.openedFolders.length === 1')
        expect(self.page.locator('.history-page')).to_be_visible()
        cards.first.locator('.history-row').click()
        expect(self.page.locator('.viewer-heading h1')).to_have_text('Второй объект')
        expect(self.page.locator('.asset-card')).to_have_count(4)
        self.assertEqual(self.errors, [])

    def test_history_single_column_and_missing_folder_recovery_stay_accessible(self):
        # Leave the gallery first: Windows refuses to rename a folder while its previews are still being read.
        self.history()
        self.page.wait_for_load_state('networkidle')
        self.output.rename(self.root / 'Перемещено')
        self.page.reload()
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        card = self.page.locator('.history-card')
        expect(card.locator('.history-row')).to_be_disabled()
        expect(card.get_by_role('button', name='Указать папку', exact=True)).to_be_visible()
        for width, height in ((1100, 760), (800, 640), (780, 620)):
            self.page.set_viewport_size({'width': width, 'height': height})
            self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
            expect(card.get_by_role('button', name='Указать папку', exact=True)).to_be_enabled()

    def test_summary_expands_warnings_and_individual_ocr_without_changing_the_result(self):
        self.warnings()
        summary = self.page.get_by_role('region', name='Сводка проверки')
        toggle = summary.get_by_role('button', name='Предупреждения: 4', exact=True)
        expect(toggle).to_have_attribute('aria-expanded', 'false')
        expect(summary.locator('.review-issue-list')).to_be_hidden()
        toggle.click()
        expect(summary.locator('.review-issue')).to_have_count(4)
        expect(summary.locator('.review-issue-message').first).to_have_text('Подпись на скане не распознана')
        first = summary.get_by_role('button', name='Подробности предупреждения на странице 1', exact=True)
        second = summary.get_by_role('button', name='Подробности предупреждения на странице 2', exact=True)
        first.click()
        expect(summary.locator('.review-issue-detail').nth(0)).to_be_visible()
        expect(summary.locator('.review-issue-detail').nth(1)).to_be_hidden()
        second.click()
        first.click()
        expect(summary.locator('.review-issue-detail').nth(0)).to_be_hidden()
        expect(summary.locator('.review-issue-detail').nth(1)).to_be_visible()
        self.page.get_by_role('searchbox').fill('Нет такой подписи')
        summary.locator('.review-issue-find').nth(2).click()
        expect(self.page.get_by_role('searchbox')).to_have_value('')
        expect(self.card(2)).to_be_in_viewport()
        self.assertEqual(load_report(self.output)['revision'], 0)
        self.assertEqual(self.errors, [])

    def test_error_disclosure_and_low_editor_do_not_hide_the_review_controls(self):
        self.warnings([{'page': 1, 'error': 'Ошибка чтения области изображения'}])
        summary = self.page.get_by_role('region', name='Сводка проверки')
        expect(summary.locator('.review-issue.is-error')).to_be_visible()
        summary.get_by_role('button', name='Ошибки: 1', exact=True).click()
        self.page.set_viewport_size({'width': 780, 'height': 620})
        self.page.get_by_role('button', name='Редактировать', exact=True).click()
        self.page.evaluate('scrollTo(0,0)')
        expect(summary).to_be_in_viewport(ratio=1)
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))


if __name__ == '__main__':
    unittest.main()
