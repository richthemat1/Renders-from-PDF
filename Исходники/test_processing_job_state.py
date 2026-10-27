"""Structured processing state stays independent of localized messages and history."""
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from photo_export import OutputPathError, extract
from photo_jobs import JobService
from test_photos import make_pdf


class ProcessingJobStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pdf-processing-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.jobs = JobService(self.root / 'workspace.json')
        self.addCleanup(self.jobs.close)

    def source(self, name='Презентация'):
        source = make_pdf(self.root, [{
            'images': [{'box': (50, 170, 800, 500), 'size': (600, 400)}],
            'text': [('Вариант 1. Двор', 50, 120, 18)],
        }])
        target = self.root / (name + '.pdf')
        source.rename(target)
        return target

    def finish(self):
        snapshot = self.jobs.wait(15)
        self.assertFalse(snapshot['busy'], 'PDF worker did not finish')
        return snapshot

    def job(self, snapshot=None):
        snapshot = self.jobs.snapshot() if snapshot is None else snapshot
        self.assertIn('job', snapshot, 'Snapshot needs structured processing metadata')
        return snapshot['job']

    def gate(self, blocked_source, *, progress_first=False):
        entered, release = Event(), Event()
        self.addCleanup(release.set)

        def gated_extract(source, output=None, progress=None):
            if source == blocked_source:
                if progress_first:
                    progress({'stage': 'labels', 'current': 1, 'total': 2, 'found': 0})
                entered.set()
                if not release.wait(10):
                    raise RuntimeError('Test did not release the extractor')
            return extract(source, output=output, progress=progress)

        self.jobs.extractor = gated_extract
        return entered, release

    def test_initial_and_empty_selection_have_idle_metadata(self):
        expected = {'outcome': 'idle', 'total': 0, 'completed': 0, 'failed': 0,
                    'warnings': 0, 'errors': 0, 'active_file': None}
        self.assertEqual(self.job(), expected)
        self.assertEqual(self.job(self.jobs.start([])), expected)
        self.assertEqual(self.job(self.jobs.prepare([])), expected)

    def test_progress_exposes_exact_filename_and_queue_position(self):
        first = self.source('ЖК · этап 2, Южный двор')
        second = self.source('Второй')
        entered, release = self.gate(second, progress_first=True)
        self.jobs.start([first, second])
        self.assertTrue(entered.wait(10))
        snapshot = self.jobs.snapshot()
        self.assertTrue(snapshot['busy'])
        self.assertEqual(self.job(snapshot), {
            'outcome': 'processing', 'total': 2, 'completed': 1, 'failed': 0,
            'warnings': 0, 'errors': 0,
            'active_file': {'name': second.name, 'index': 2, 'total': 2},
        })
        self.assertGreater(snapshot['progress'], 50)
        self.assertLess(snapshot['progress'], 100)
        release.set()
        self.finish()

    def test_snapshot_metadata_is_deeply_independent(self):
        source = self.source('ЖК · очередь, 2')
        entered, release = self.gate(source)
        self.jobs.start([source])
        self.assertTrue(entered.wait(10))
        snapshot = self.jobs.snapshot()
        self.job(snapshot)['completed'] = 900
        snapshot['job']['active_file']['name'] = 'Подменённое имя'
        actual = self.job()
        self.assertEqual(actual['completed'], 0)
        self.assertEqual(actual['active_file'], {'name': source.name, 'index': 1, 'total': 1})
        release.set()
        self.finish()

    def test_successful_batch_counts_committed_reports(self):
        first, second = self.source('Первый'), self.source('Второй')
        self.jobs.start([first, second])
        snapshot = self.finish()
        job = self.job(snapshot)
        self.assertEqual({key: value for key, value in job.items() if key != 'active_file'}, {
            'outcome': 'complete', 'total': 2, 'completed': 2, 'failed': 0, 'warnings': 0, 'errors': 0,
        })
        self.assertEqual(snapshot['progress'], 100)
        self.assertEqual(len(snapshot['history']), 2)
        current = next(item for item in snapshot['history'] if item['output'] == snapshot['current_output'])
        self.assertEqual(current['source'], str(second))

    def test_next_batch_resets_all_counts_and_current_output(self):
        first, second = self.source('Первый'), self.source('Следующий')

        def with_diagnostics(source, output=None, progress=None):
            report = extract(source, output=output, progress=progress)
            report['warnings'] = ['Проверить подпись']
            report['errors'] = [{'error': 'Одно изображение не сохранено'}]
            return report

        self.jobs.extractor = with_diagnostics
        self.jobs.start([first])
        previous = self.finish()
        self.assertEqual(self.job(previous)['outcome'], 'partial')
        self.assertEqual(self.job(previous)['errors'], 1)
        self.assertEqual(self.job(previous)['warnings'], 1)
        self.assertEqual(self.job(self.jobs.start([])), self.job(previous))
        entered, release = self.gate(second)
        started = self.jobs.start([second])
        self.assertTrue(entered.wait(10))
        self.assertIsNone(started['current_output'])
        self.assertEqual(started['totals'], {'images': 0, 'documents': 0, 'unsigned': 0, 'review': 0})
        current = self.job()
        self.assertEqual({key: value for key, value in current.items() if key != 'active_file'}, {
            'outcome': 'processing', 'total': 1, 'completed': 0, 'failed': 0, 'warnings': 0, 'errors': 0,
        })
        release.set()
        self.assertEqual(self.job(self.finish())['outcome'], 'complete')

    def test_worker_exception_counts_a_failed_file_without_counting_report_errors(self):
        valid, broken = self.source('Готовый'), self.source('Повреждённый')
        broken.write_bytes(b'%PDF-1.7\nbroken PDF')
        with self.assertLogs('pdf-photos', level='ERROR'):
            self.jobs.start([valid, broken])
            snapshot = self.finish()
        job = self.job(snapshot)
        self.assertEqual((job['outcome'], job['total'], job['completed'], job['failed'], job['errors']),
                         ('partial', 2, 1, 1, 0))
        self.assertIn('частично', snapshot['status'])
        self.assertEqual(len(snapshot['failures']), 1)
        self.assertEqual(Path(snapshot['current_output']), Path(snapshot['history'][0]['output']))

    def test_only_worker_failures_have_failed_outcome(self):
        broken = self.source('Повреждённый')
        broken.write_bytes(b'%PDF-1.7\nbroken PDF')
        with self.assertLogs('pdf-photos', level='ERROR'):
            self.jobs.start([broken])
            snapshot = self.finish()
        job = self.job(snapshot)
        self.assertEqual((job['outcome'], job['total'], job['completed'], job['failed'], job['errors']),
                         ('failed', 1, 0, 1, 0))
        self.assertIsNone(snapshot['current_output'])
        self.assertEqual(snapshot['history'], [])

    def test_report_diagnostic_counts_accumulate_without_turning_reports_into_failed_files(self):
        first, second = self.source('Первый'), self.source('Второй')

        def with_diagnostics(source, output=None, progress=None):
            report = extract(source, output=output, progress=progress)
            report['warnings'] = ['Проверить подпись', {'reason': 'Снимок области'}] if source == first else ['Предупреждение']
            report['errors'] = [] if source == first else [{'error': 'Ошибка 1'}, {'error': 'Ошибка 2'}]
            return report

        self.jobs.extractor = with_diagnostics
        self.jobs.start([first, second])
        snapshot = self.finish()
        job = self.job(snapshot)
        self.assertEqual((job['outcome'], job['completed'], job['failed'], job['warnings'], job['errors']),
                         ('partial', 2, 0, 3, 2))
        self.assertEqual(len(snapshot['history']), 2)

    def test_warnings_alone_keep_complete_outcome(self):
        source = self.source()

        def with_warnings(source, output=None, progress=None):
            report = extract(source, output=output, progress=progress)
            report['warnings'] = ['Проверить подпись', 'Снимок области']
            return report

        self.jobs.extractor = with_warnings
        self.jobs.start([source])
        job = self.job(self.finish())
        self.assertEqual((job['outcome'], job['completed'], job['failed'], job['warnings'], job['errors']),
                         ('complete', 1, 0, 2, 0))

    def test_preflight_failure_is_counted_before_valid_queue_starts(self):
        invalid, valid = self.source('Недоступный'), self.source('Готовый · этап, 2')
        destination = self.jobs._destination

        def selected_destination(source, settings):
            if source == invalid:
                raise OutputPathError('Выберите другую папку')
            return destination(source, settings)

        entered, release = self.gate(valid)
        with patch.object(self.jobs, '_destination', selected_destination):
            self.jobs.start([invalid, valid])
            self.assertTrue(entered.wait(10))
            self.assertEqual(self.job(), {
                'outcome': 'processing', 'total': 2, 'completed': 0, 'failed': 1,
                'warnings': 0, 'errors': 0,
                'active_file': {'name': valid.name, 'index': 1, 'total': 1},
            })
            release.set()
            snapshot = self.finish()
        job = self.job(snapshot)
        self.assertEqual((job['outcome'], job['total'], job['completed'], job['failed']), ('partial', 2, 1, 1))

    def test_all_preflight_failures_have_failed_metadata_without_creating_a_job(self):
        source = self.source()
        with patch.object(self.jobs, '_destination', side_effect=OutputPathError('Выберите другую папку')):
            snapshot = self.jobs.start([source])
        self.assertFalse(snapshot['busy'])
        self.assertFalse(snapshot['has_job'])
        self.assertEqual(self.job(snapshot), {
            'outcome': 'failed', 'total': 1, 'completed': 0, 'failed': 1,
            'warnings': 0, 'errors': 0, 'active_file': None,
        })

    def test_preflight_failure_after_success_resets_stale_result_and_totals(self):
        source = self.source()
        self.jobs.start([source])
        successful = self.finish()
        self.assertIsNotNone(successful['current_output'])
        with patch.object(self.jobs, '_destination', side_effect=OutputPathError('Выберите другую папку')):
            failed = self.jobs.start([source])
        self.assertIsNone(failed['current_output'])
        self.assertEqual(failed['totals'], {'images': 0, 'documents': 0, 'unsigned': 0, 'review': 0})
        self.assertEqual(failed['progress'], 0)
        self.assertTrue(failed['has_job'])
        self.assertEqual(self.job(failed), {
            'outcome': 'failed', 'total': 1, 'completed': 0, 'failed': 1,
            'warnings': 0, 'errors': 0, 'active_file': None,
        })

    def test_cancellation_before_first_success_has_cancelled_metadata(self):
        source = self.source()
        entered, release = self.gate(source)
        self.jobs.start([source])
        self.assertTrue(entered.wait(10))
        cancelling = self.jobs.cancel()
        self.assertTrue(cancelling['busy'])
        self.assertEqual(self.job(cancelling)['outcome'], 'processing')
        release.set()
        snapshot = self.finish()
        job = self.job(snapshot)
        self.assertEqual((job['outcome'], job['total'], job['completed'], job['failed']), ('cancelled', 1, 0, 0))
        self.assertEqual(snapshot['history'], [])
        self.assertIsNone(snapshot['current_output'])
        self.assertEqual(snapshot['progress'], 0)

    def test_cancellation_keeps_already_completed_reports(self):
        first, second = self.source('Первый'), self.source('Второй')
        entered, release = self.gate(second)
        self.jobs.start([first, second])
        self.assertTrue(entered.wait(10))
        self.assertEqual(self.job()['completed'], 1)
        self.jobs.cancel()
        release.set()
        snapshot = self.finish()
        job = self.job(snapshot)
        self.assertEqual((job['outcome'], job['total'], job['completed'], job['failed']), ('cancelled', 2, 1, 0))
        self.assertEqual(len(snapshot['history']), 1)
        self.assertEqual(snapshot['history'][0]['source'], str(first))
        self.assertEqual(snapshot['current_output'], snapshot['history'][0]['output'])
        self.assertEqual(snapshot['progress'], 0)

    def test_duplicate_prompt_and_cancelling_it_preserve_completed_metadata(self):
        source = self.source()
        self.jobs.start([source])
        previous = self.job(self.finish())
        pending = self.jobs.prepare([source])
        self.assertEqual(self.job(pending), previous)
        token = pending['pending_repeat']['token']
        self.assertEqual(self.job(self.jobs.confirm_repeat(token, False)), previous)
        self.assertEqual(self.job(self.jobs.prepare([])), previous)

    def test_rejected_starts_preserve_active_and_completed_metadata(self):
        source = self.source()
        entered, release = self.gate(source)
        self.jobs.start([source])
        self.assertTrue(entered.wait(10))
        active = self.job()
        with self.assertRaises(RuntimeError):
            self.jobs.start([source])
        self.assertEqual(self.job(), active)
        release.set()
        previous = self.job(self.finish())
        with self.assertRaises(ValueError):
            self.jobs.start([self.root / 'missing.pdf'])
        self.assertEqual(self.job(), previous)
        self.jobs.close()
        with self.assertRaises(RuntimeError):
            self.jobs.start([source])
        self.assertEqual(self.job(), previous)

    def test_remember_and_history_open_do_not_replace_batch_metadata(self):
        imported = extract(self.source('Импортированный'))
        idle = self.job()
        item = self.jobs.remember(imported)
        self.jobs.import_result(imported['output'])
        self.jobs.history_result(item['id'])
        self.assertEqual(self.job(), idle)
        self.jobs.start([self.source('Текущий')])
        completed = self.finish()
        self.jobs.remember(imported)
        self.jobs.import_result(imported['output'])
        self.jobs.history_result(item['id'])
        self.assertEqual(self.job(), self.job(completed))
        self.assertEqual(self.jobs.current_output, completed['current_output'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
