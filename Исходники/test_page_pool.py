"""Pages of a heavy PDF are read in worker processes: the result is the one a single process gives."""
import json
import multiprocessing
import subprocess
import tempfile
from time import monotonic, sleep
import unittest
from unittest.mock import patch

import photo_export
from photo_native import Cancelled
from test_photos import make_pdf


def deck(root):
    pages = [{'images': [{'box': (50, 170, 900, 500), 'color': f'#{index*20:02x}6699'},
                         {'box': (50, 20, 200, 45), 'size': (800, 180), 'color': '#dddddd'}],
              'text': [(f'Вариант {index % 3 + 1}', 50, 80, 18), (f'Вид {index}', 50, 130, 18)]}
             for index in range(10)]
    return make_pdf(root, pages)


def idle_worker(source):
    photo_export._open_in_worker(source)
    sleep(120)


def owner(source, pids):
    worker = multiprocessing.get_context('spawn').Process(target=idle_worker, args=(source,))
    worker.start()
    pids.put(worker.pid)
    sleep(120)


def running(pid):
    # Bytes: the console code page of tasklist is not the one Python expects.
    listed = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'], capture_output=True).stdout
    return f' {pid} '.encode() in listed


class PagePool(unittest.TestCase):
    def read(self, source, workers, after):
        events = []
        with patch.object(photo_export, 'page_workers', lambda: workers), \
                patch.object(photo_export, 'PAGE_POOL_AFTER', after):
            pages = photo_export.inspect(source, events.append)
        return json.dumps(pages, ensure_ascii=False, sort_keys=True, default=str), events

    def test_worker_processes_read_the_same_pages_and_report_each_once(self):
        with tempfile.TemporaryDirectory() as root:
            source = deck(root)
            alone, _ = self.read(source, 1, 0)
            with patch.object(photo_export, 'ProcessPoolExecutor', wraps=photo_export.ProcessPoolExecutor) as pool:
                pooled, events = self.read(source, 2, 0)
            self.assertEqual(pool.call_count, 1)
            self.assertEqual(pooled, alone)
            labels = [event['current'] for event in events if event['stage'] == 'labels']
            self.assertEqual(labels, list(range(1, 11)))

    def test_light_pdf_is_read_without_starting_processes(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(photo_export, 'ProcessPoolExecutor') as pool:
                self.read(deck(root), 4, 60)
            pool.assert_not_called()

    def test_stop_during_reading_leaves_no_workers_behind(self):
        with tempfile.TemporaryDirectory() as root:
            source = deck(root)

            def stop(info):
                if info['current'] >= 3:
                    raise Cancelled()
            with patch.object(photo_export, 'page_workers', lambda: 2), patch.object(photo_export, 'PAGE_POOL_AFTER', 0):
                with self.assertRaises(Cancelled):
                    photo_export.inspect(source, stop)
            self.assertEqual(multiprocessing.active_children(), [])


    def test_worker_leaves_when_the_process_of_its_pdf_is_ended(self):
        """A stop that took too long or Windows ends that process at once: nothing tells its workers."""
        # Windows frees the PDF a moment after its last process is gone.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
            context = multiprocessing.get_context('spawn')
            pids = context.Queue()
            process = context.Process(target=owner, args=(str(deck(root)), pids))
            process.start()
            self.addCleanup(process.kill)
            worker = pids.get(timeout=30)
            sleep(1)
            self.assertTrue(running(worker))
            process.terminate()
            process.join(10)
            deadline = monotonic()+15
            while running(worker) and monotonic() < deadline:
                sleep(.2)
            self.assertFalse(running(worker))


if __name__ == '__main__':
    unittest.main()
