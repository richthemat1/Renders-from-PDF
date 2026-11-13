"""«Оставить как есть»: an image waiting for a decision is checked by a person and left where it is."""
from pathlib import Path
import tempfile
import unittest

from playwright.sync_api import expect

from photo_desktop_server import DesktopServer
from photo_edit import EditError, apply_edits, load_report, restore_last_save
from photo_export import extract
from photo_jobs import JobService
from test_photos import make_pdf
import test_desktop_ui as desktop_ui


def current_variants(report):
    variants = {}
    for index, image in enumerate(report['images']):
        if image.get('variant') is not None and not image.get('removed'):
            variants.setdefault(str(image['variant']), []).append(index)
    for ids in variants.values():
        ids.sort(key=lambda index: report['images'][index]['pdf_page'])
    return variants


class ReviewKeep(unittest.TestCase):
    def export(self, root):  # also used by the UI test below, without an instance
        source = make_pdf(root, [
            {'images': [{'box': (50, 170, 800, 500), 'color': '#3366aa'}], 'text': [('Вариант 1. Двор', 50, 120, 18)]},
            {'images': [{'box': (50, 170, 800, 500), 'color': '#aa6633'}]},
        ])
        report = extract(source)
        return Path(report['output']), report

    def test_kept_image_stops_waiting_stays_in_place_and_undo_brings_it_back(self):
        with tempfile.TemporaryDirectory(prefix='pdf-keep-') as root:
            output, report = self.export(root)
            waiting = next(index for index, image in enumerate(report['images']) if image['status'] == 'unsigned')
            before = report['images'][waiting]
            saved, _ = apply_edits(output, {'revision': 0, 'variants': current_variants(report), 'removed': [],
                                            'reviewed': [waiting]})
            entry = saved['images'][waiting]
            self.assertTrue(entry['reviewed'])
            # Left as it is: the same folder, file and status; only the counters stop asking for it.
            self.assertEqual((entry['folder'], entry['file'], entry['status']), (before['folder'], before['file'], 'unsigned'))
            self.assertNotIn('unsigned', saved['counts_by_status'])
            self.assertEqual(saved['edits'][-1]['reviewed'], [waiting])
            self.assertTrue(load_report(output)['images'][waiting]['reviewed'])
            undone, _ = restore_last_save(output, {'revision': 1})
            self.assertFalse(undone['images'][waiting].get('reviewed'))
            self.assertEqual(undone['counts_by_status'].get('unsigned'), 1)

    def test_only_images_waiting_for_a_decision_can_be_kept(self):
        with tempfile.TemporaryDirectory(prefix='pdf-keep-') as root:
            output, report = self.export(root)
            named = next(index for index, image in enumerate(report['images']) if image['status'] == 'named')
            with self.assertRaises(EditError):
                apply_edits(output, {'revision': 0, 'variants': current_variants(report), 'removed': [],
                                     'reviewed': [named]})
            with self.assertRaises(EditError):
                apply_edits(output, {'revision': 0, 'variants': current_variants(report), 'removed': [],
                                     'reviewed': ['1']})



class ReviewKeepUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        desktop_ui.DesktopUI.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        desktop_ui.DesktopUI.tearDownClass.__func__(cls)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdf-keep-ui-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.output, report = ReviewKeep.export(None, root)
        self.waiting = next(index for index, image in enumerate(report['images']) if image['status'] == 'unsigned')
        jobs = JobService(root / 'state.json')
        self.addCleanup(jobs.close)
        jobs.remember(report, processed=True)
        server = DesktopServer(jobs, Path(__file__).parent / 'frontend' / 'dist').start()
        self.addCleanup(server.stop)
        context = self.browser.new_context(viewport={'width': 1380, 'height': 1000}, reduced_motion='reduce')
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.goto(server.url)
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        self.page.locator('.history-row').first.click()

    def test_review_leaves_an_unsigned_image_as_it_is_and_the_count_goes(self):
        summary = self.page.get_by_role('region', name='Сводка проверки')
        expect(summary).to_contain_text('Нужно проверить: 1')
        summary.get_by_role('button', name='Разобрать').click()
        card = self.page.locator(f'.asset-card[data-id="{self.waiting}"]')
        card.get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Перенести в вариант', exact=True).click()
        self.page.get_by_role('menuitem', name='Оставить как есть', exact=True).click()
        with self.page.expect_response(lambda response: response.url.endswith('/apply') and response.status == 200):
            self.page.get_by_role('button', name='Сохранить', exact=True).click()
        self.assertTrue(load_report(self.output)['images'][self.waiting]['reviewed'])
        self.page.get_by_role('button', name='Завершить правки', exact=True).click()
        expect(self.page.get_by_role('region', name='Сводка проверки')).not_to_contain_text('Нужно проверить')


if __name__ == '__main__':
    unittest.main()
