"""Several PDFs of one queue at once: in threads with a test extractor and in processes of their own."""
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from unittest.mock import patch

from photo_export import extract, planned_output
from photo_jobs import JobService
from test_photos import make_pdf


class ParallelQueue(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pdf-parallel-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.jobs = JobService(self.root / 'workspace.json', workers=2)
        self.addCleanup(self.jobs.close)

    def source(self, name, folder=None, pages=1):
        folder = self.root if folder is None else folder
        folder.mkdir(parents=True, exist_ok=True)
        source = make_pdf(folder, [{'images': [{'box': (50, 170, 800, 500), 'size': (600, 400)}],
                                    'text': [(f'Вариант 1. Двор {page}', 50, 120, 18)]} for page in range(pages)])
        return source.rename(folder / f'{name}.pdf')

    def gate(self, *sources):
        entered = {source: Event() for source in sources}
        release = {source: Event() for source in sources}
        for event in release.values():
            self.addCleanup(event.set)

        def gated(source, output=None, progress=None):
            if source in entered:
                progress({'stage': 'labels', 'current': 1, 'total': 1, 'found': 0})
                entered[source].set()
                if not release[source].wait(10):
                    raise RuntimeError('Test did not release the extractor')
            return extract(source, output=output, progress=progress)
        self.jobs.extractor = gated
        return entered, release

    def finish(self, timeout=60):
        snapshot = self.jobs.wait(timeout)
        self.assertFalse(snapshot['busy'], 'The queue did not finish')
        return snapshot

    def test_files_waiting_for_a_slot_are_listed_until_they_start(self):
        first, second, third = self.source('Первый'), self.source('Второй'), self.source('Третий')
        entered, release = self.gate(first, second, third)
        self.jobs.start([first, second, third])
        self.assertTrue(entered[first].wait(10))
        waiting = self.jobs.snapshot()['job']['waiting']
        # Two slots, or one when memory is short: either way the last file waits, in queue order.
        self.assertIn(waiting, ([third.name], [second.name, third.name]))
        for event in release.values():
            event.set()
        self.assertNotIn('waiting', self.finish()['job'])

    def test_two_files_run_at_once_and_results_keep_queue_order(self):
        first, second = self.source('Первый'), self.source('Второй')
        entered, release = self.gate(first, second)
        self.jobs.start([first, second])
        self.assertTrue(entered[first].wait(10) and entered[second].wait(10))
        snapshot = self.jobs.snapshot()
        self.assertEqual([(item['name'], item['index']) for item in snapshot['job']['active_files']],
                         [(first.name, 1), (second.name, 2)])
        self.assertEqual(snapshot['status'], 'Обработка PDF · одновременно 2 из 2')
        self.assertIn(f'{first.name} · Чтение заголовков', snapshot['detail'])
        # The second file finishes first; the queue still lists and opens its results in queue order.
        release[second].set()
        deadline = monotonic() + 20
        while self.jobs.snapshot()['job']['completed'] < 1 and monotonic() < deadline:
            sleep(.05)
        self.assertEqual([item['name'] for item in self.jobs.snapshot()['job']['active_files']], [first.name])
        release[first].set()
        done = self.finish()
        sources = {item['output']: item['source'] for item in done['history']}
        self.assertEqual([sources[output] for output in done['job']['outputs']], [str(first), str(second)])
        self.assertEqual(sources[done['current_output']], str(second))
        self.assertEqual((done['job']['outcome'], done['job']['active_files'], done['progress']), ('complete', [], 100))
        heads = [item['text'] for item in done['logs'] if item['level'] == 'head']
        self.assertEqual(heads, [f'Файл 2 из 2: {second.name}', f'Файл 1 из 2: {first.name}'])

    def test_same_names_from_two_folders_get_their_own_result_folders(self):
        target = self.root / 'Результаты всех'
        target.mkdir()
        self.jobs.set_output('custom', str(target))
        first = self.source('Презентация', self.root / 'Первая')
        second = self.source('Презентация', self.root / 'Вторая')
        entered, release = self.gate(first, second)
        self.jobs.start([first, second])
        self.assertTrue(entered[first].wait(10) and entered[second].wait(10))
        release[first].set()
        release[second].set()
        done = self.finish()
        self.assertEqual(done['job']['outcome'], 'complete')
        self.assertEqual(len(set(done['job']['outputs'])), 2)
        self.assertEqual({Path(output).parent for output in done['job']['outputs']}, {target.resolve()})

    def test_planned_output_skips_folders_reserved_by_running_files(self):
        source = self.source('Отчёт')
        taken = planned_output(source, self.root)
        other = planned_output(source, self.root, reserved={os.path.normcase(str(taken))})
        self.assertNotEqual(taken, other)
        self.assertTrue(other.name.endswith('(2)'))

    def test_a_failed_file_does_not_stop_the_other(self):
        valid, broken = self.source('Готовый'), self.source('Повреждённый')
        broken.write_bytes(b'%PDF-1.7\nbroken PDF')
        with self.assertLogs('pdf-photos', level='ERROR'):
            self.jobs.start([valid, broken])
            done = self.finish()
        job = done['job']
        self.assertEqual((job['outcome'], job['completed'], job['failed']), ('partial', 1, 1))
        self.assertEqual([item['source'] for item in done['history']], [str(valid)])

    def test_stop_keeps_running_and_waiting_files_for_later(self):
        first, second, third = self.source('Первый'), self.source('Второй'), self.source('Третий')
        entered, release = self.gate(first, second)
        self.jobs.start([first, second, third])
        self.assertTrue(entered[first].wait(10) and entered[second].wait(10))
        self.jobs.cancel()
        release[first].set()
        release[second].set()
        done = self.finish()
        self.assertEqual((done['job']['outcome'], done['job']['remaining']), ('cancelled', 3))
        self.assertEqual(done['history'], [])
        self.assertEqual(self.jobs._remaining, (first, second, third))

    def test_low_memory_runs_one_file_at_a_time(self):
        first, second = self.source('Первый'), self.source('Второй')
        entered, release = self.gate(first, second)
        with patch('photo_jobs.available_memory', return_value=0):
            self.jobs.start([first, second])
            self.assertTrue(entered[first].wait(10))
            sleep(.6)
            self.assertFalse(entered[second].is_set())
            self.assertEqual(len(self.jobs.snapshot()['job']['active_files']), 1)
            for event in release.values():
                event.set()
            done = self.finish()
        self.assertEqual(done['job']['completed'], 2)


class ProcessQueue(unittest.TestCase):
    """The real extractor in processes of its own, as the window runs it."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pdf-processes-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.jobs = JobService(self.root / 'workspace.json', workers=2, processes=True)
        self.addCleanup(self.jobs.close)

    def source(self, name, pages=1):
        source = make_pdf(self.root, [{'images': [{'box': (50, 170, 800, 500), 'size': (600, 400)}],
                                       'text': [(f'Вариант {page % 3 + 1}. Фасад', 50, 120, 18)]}
                                      for page in range(pages)])
        return source.rename(self.root / f'{name}.pdf')

    def test_files_are_processed_in_their_own_processes(self):
        first, second = self.source('Первый'), self.source('Второй', pages=3)
        self.jobs.start([first, second])
        done = self.jobs.wait(120)
        self.assertFalse(done['busy'])
        self.assertEqual((done['job']['outcome'], done['job']['completed']), ('complete', 2))
        sources = {item['output']: item['source'] for item in done['history']}
        self.assertEqual([sources[output] for output in done['job']['outputs']], [str(first), str(second)])
        self.assertTrue(all((Path(output) / 'Отчёт.json').is_file() for output in done['job']['outputs']))
        self.assertTrue(any(item['text'].startswith('Начата обработка: ') for item in done['logs']))
        self.assertEqual(multiprocessing.active_children(), [])

    def test_a_stopped_queue_leaves_no_unfinished_work(self):
        first, second = self.source('Первый', pages=40), self.source('Второй', pages=40)
        self.jobs.start([first, second])
        # Stop only once both files are being read, so each process has work to remove.
        deadline = monotonic() + 30
        while monotonic() < deadline and not (
                len(phases := [item['phase'] for item in self.jobs.snapshot()['job']['active_files']]) == 2
                and all(phases)):
            sleep(.02)
        self.assertEqual(len(multiprocessing.active_children()), 2)
        # A heavy PDF reads its pages in worker processes of its own, which a daemon may not start.
        self.assertFalse(any(child.daemon for child in multiprocessing.active_children()))
        self.jobs.cancel()
        done = self.jobs.wait(120)
        self.assertEqual((done['job']['outcome'], done['job']['remaining']), ('cancelled', 2))
        self.assertEqual(list(self.root.glob('.pdf-photos-*')), [])
        self.assertEqual(done['history'], [])

    def test_a_process_ended_by_windows_fails_only_its_file(self):
        source = self.source('Прерванный')
        with self.assertLogs('pdf-photos', level='ERROR'):
            self.jobs.start([source])
            deadline = monotonic() + 20
            while not multiprocessing.active_children() and monotonic() < deadline:
                sleep(.02)
            for child in multiprocessing.active_children():
                child.terminate()
            done = self.jobs.wait(60)
        self.assertEqual((done['job']['outcome'], done['job']['failed']), ('failed', 1))
        self.assertIn('Обработка прервалась', done['failures'][0]['reason'])


if __name__ == '__main__':
    unittest.main()
