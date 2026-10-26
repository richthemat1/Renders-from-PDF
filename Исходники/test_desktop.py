"""Headless queue and live HTTP contracts for the unified desktop workspace."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from test_photos import EditFixture


class JobServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='pdf-desktop-jobs-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = self.root / 'workspace.json'

    def jobs(self, extractor=None):
        self.assertIsNotNone(importlib.util.find_spec('photo_jobs'), 'Нет независимого сервиса обработки')
        from photo_jobs import JobService
        jobs = JobService(self.state, extractor=extractor)
        self.addCleanup(jobs.close)
        return jobs

    def source(self, name='Объект'):
        path = self.root / f'{name}.pdf'
        path.write_bytes(b'fixture-source')
        return path

    def report(self, source, output=None, images=2):
        folder = Path(output) if output else source.with_name(source.stem + ' — PDF')
        folder.mkdir(parents=True, exist_ok=True)
        report = {'source': str(source), 'output': str(folder), 'revision': 0,
                  'images': [{} for _ in range(images)], 'documents': [{'variant': 1, 'pages': images}],
                  'counts_by_status': {'named': images}, 'counts_by_folder': {'Вариант 1': images},
                  'errors': [], 'warnings': []}
        (folder / 'Отчёт.json').write_text(json.dumps(report), encoding='utf-8')
        return report

    def test_queue_expands_folders_deduplicates_and_reports_progress_and_real_totals(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        first, second = self.source('А'), self.source('Б')
        ignored = self.root / 'Текст.txt'
        ignored.write_text('not a PDF', encoding='utf-8')

        def extract(source, output=None, progress=None):
            progress({'stage': 'labels', 'current': 1, 'total': 2, 'found': 0})
            if source == first:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('Test did not release the worker')
            progress({'stage': 'images', 'current': 2, 'total': 2, 'found': 2})
            return self.report(source, output)

        jobs = self.jobs(extract)
        started = jobs.start([self.root, first, ignored])
        self.assertTrue(started['busy'])
        self.assertTrue(entered.wait(5))
        during = jobs.snapshot()
        self.assertGreater(during['progress'], 0)
        self.assertLess(during['progress'], 50)
        self.assertIn('А.pdf', during['detail'])
        release.set()
        finished = jobs.wait(5)
        self.assertFalse(finished['busy'])
        self.assertEqual(finished['progress'], 100)
        self.assertEqual(finished['totals'], {'images': 4, 'documents': 2, 'unsigned': 0, 'review': 0})
        self.assertEqual(len(finished['history']), 2)
        self.assertTrue(all(not item['imported'] and item['available'] for item in finished['history']))
        self.assertEqual(Path(jobs.current_output), second.with_name('Б — PDF'))
        self.assertTrue(any(item['level'] == 'warn' and 'Текст.txt' in item['text'] for item in finished['logs']))

    def test_output_destination_is_fixed_and_settings_and_second_queue_are_rejected_while_busy(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        destination = self.root / 'Готовое'
        destination.mkdir()
        first, second = self.source('А'), self.source('Б')

        def extract(source, output=None, progress=None):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test did not release the worker')
            return self.report(source, output)

        jobs = self.jobs(extract)
        jobs.set_output('custom', destination)
        jobs.start([first, second])
        self.assertTrue(entered.wait(5))
        with self.assertRaises((RuntimeError, ValueError)):
            jobs.set_output('nearby')
        with self.assertRaises((RuntimeError, ValueError)):
            jobs.start([first])
        self.assertEqual(jobs.snapshot()['settings']['mode'], 'custom')
        release.set()
        result = jobs.wait(5)
        self.assertEqual({Path(item['output']).parent for item in result['history']}, {destination})
        self.assertEqual(len(result['history']), 2)
        self.assertTrue(any(item['level'] == 'error' for item in result['logs']))

    def test_processing_failure_is_visible_and_new_queue_does_not_reuse_previous_output(self):
        valid, invalid = self.source('Готовый'), self.source('Повреждённый')

        def extract(source, output=None, progress=None):
            if source == invalid:
                raise ValueError('PDF повреждён')
            return self.report(source, output)

        jobs = self.jobs(extract)
        with self.assertLogs('pdf-photos', level='ERROR'):
            jobs.start([valid, invalid])
            partial = jobs.wait(5)
            self.assertIn('частично', partial['status'])
            self.assertEqual(len(partial['history']), 1)
            jobs.start([invalid])
            failed = jobs.wait(5)
        self.assertIsNone(failed['current_output'])
        self.assertEqual(failed['totals']['images'], 0)
        self.assertIn('Ошибка', failed['status'])
        self.assertTrue(any('PDF повреждён' in entry['text'] and entry['level'] == 'error'
                            for entry in failed['logs']))

    def test_invalid_selection_is_rejected_without_starting_a_worker(self):
        jobs = self.jobs()
        with self.assertRaises(ValueError):
            jobs.start([self.root / 'missing.pdf'])
        snapshot = jobs.snapshot()
        self.assertFalse(snapshot['busy'])
        self.assertIn('Ошибка', snapshot['status'])
        self.assertEqual(snapshot['logs'][-1]['level'], 'error')
        self.assertFalse(jobs.start([])['busy'])

    def test_import_normalizes_moved_output_and_history_ids_survive_restart(self):
        jobs = self.jobs()
        report = self.report(self.source())
        moved = self.root / 'Перенесённый результат'
        Path(report['output']).rename(moved)
        imported = jobs.import_result(moved)
        self.assertEqual(imported['output'], str(moved))
        first = jobs.snapshot()['history'][0]
        self.assertTrue(first['imported'])
        jobs.close()
        reopened = self.jobs()
        second = reopened.snapshot()['history'][0]
        self.assertEqual(second['id'], first['id'])
        self.assertEqual(reopened.history_result(first['id'])['output'], str(moved))
        self.assertEqual(len(reopened.snapshot()['history']), 1)
        shutil.rmtree(moved)
        self.assertFalse(reopened.snapshot()['history'][0]['available'])
        with self.assertRaises((ValueError, OSError)):
            reopened.history_result(first['id'])
        with self.assertRaises((KeyError, ValueError)):
            reopened.history_result(str(self.root))

    def test_snapshots_are_independent_and_corrupt_preferences_do_not_prevent_processing(self):
        self.state.write_text('{broken', encoding='utf-8')
        with self.assertLogs('pdf-photos', level='WARNING'):
            jobs = self.jobs()
        snapshot = jobs.snapshot()
        self.assertTrue(snapshot['warning'])
        snapshot['settings']['mode'] = 'custom'
        snapshot['totals']['images'] = 400
        snapshot['logs'].clear()
        self.assertEqual(jobs.snapshot()['settings']['mode'], 'nearby')
        self.assertEqual(jobs.snapshot()['totals']['images'], 0)
        jobs.set_output('results')
        restored = self.jobs()
        self.assertEqual(restored.snapshot()['settings']['mode'], 'results')
        self.assertEqual(len(list(self.root.glob('workspace.json.corrupt-*'))), 1)

    def test_close_waits_for_real_extractor_cancellation_and_rollback(self):
        import photo_export
        from test_photos import make_pdf
        source = make_pdf(self.root, [{'images': [{'box': (50, 170, 800, 500)}]}])
        entered, release, closed = Event(), Event(), Event()
        self.addCleanup(release.set)
        original = photo_export.render_previews

        def previews(folder, report, progress=None):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test did not release the preview stage')
            return original(folder, report, progress)

        jobs = self.jobs()
        closer = Thread(target=lambda: (jobs.close(), closed.set()))
        with patch.object(photo_export, 'render_previews', previews):
            try:
                jobs.start([source])
                self.assertTrue(entered.wait(5))
                closer.start()
                self.assertFalse(closed.wait(.1), 'Close returned before PDF rollback')
                self.assertTrue(jobs.snapshot()['cancelling'])
                self.assertTrue(jobs.wait(.01)['busy'])
            finally:
                release.set()
                if closer.ident is not None:
                    closer.join(5)
        self.assertTrue(closed.is_set())
        self.assertFalse(jobs.busy)
        self.assertIn('Остановлено', jobs.snapshot()['status'])
        self.assertEqual(jobs.snapshot()['history'], [])
        self.assertEqual(list(self.root.glob('.pdf-photos-*')), [])
        self.assertFalse(source.with_name('presentation — PDF').exists())
        with self.assertRaises(RuntimeError):
            jobs.start([source])


class DesktopServerTests(EditFixture, unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('photo_desktop_server'), 'Нет HTTP-сервера единого окна')
        from photo_desktop_server import DesktopServer
        from photo_export import extract
        from photo_jobs import JobService
        from test_photos import make_pdf
        self.directory = tempfile.TemporaryDirectory(prefix='pdf-desktop-http-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        pages = [{'images': [{'box': (50, 170, 800, 500), 'color': color}], 'text': [(title, 50, 120, 18)]}
                 for color, title in [('#ff0000', 'Вариант 1'), ('#00ff00', 'Вариант 1'),
                                      ('#0000ff', 'Вариант 2'), ('#ffff00', 'Западный двор')]]
        report = extract(make_pdf(self.root, pages))
        self.output, self.ids = Path(report['output']), {'red': 0, 'green': 1, 'blue': 2, 'yellow': 3}
        frontend = self.root / 'frontend'
        frontend.mkdir()
        (frontend / 'index.html').write_text('<!doctype html><title>Desktop fixture</title>', encoding='utf-8')
        (frontend / 'assets').mkdir()
        (frontend / 'assets' / 'main.js').write_text('window.fixture = true;', encoding='utf-8')
        (self.root / 'secret.txt').write_text('PRIVATE FILE', encoding='utf-8')
        self.jobs = JobService(self.root / 'workspace.json')
        self.addCleanup(self.jobs.close)
        self.server = DesktopServer(self.jobs, frontend).start()
        self.addCleanup(self.server.stop)
        self.wrapper = self.server.register_result(self.output)

    def request(self, path='', data=None, origin=None, body=None, headers=None, method=None):
        address = path if path.startswith('http://') else self.server.url + path
        headers = dict(headers or {})
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        if body is not None:
            headers.setdefault('Content-Type', 'application/json')
        if origin:
            headers['Origin'] = self.server.origin if origin is True else origin
        request = Request(address, body, headers, method=method)
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            content = json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw
            return response.code, content, response.headers

    def test_app_token_serves_frontend_assets_and_live_state_without_cors(self):
        status, page, headers = self.request()
        self.assertEqual(status, 200)
        self.assertIn(b'Desktop fixture', page)
        self.assertEqual(self.request('assets/main.js')[0], 200)
        status, snapshot, headers = self.request('api/state')
        self.assertEqual(status, 200)
        self.assertEqual(snapshot['history'][0]['images'], 4)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIsNone(headers['Access-Control-Allow-Origin'])
        self.assertEqual(self.request(self.server.origin + '/app/wrong-token/api/state')[0], 404)
        self.assertEqual(self.request('api/missing')[0], 404)

    def test_registered_wrapper_repairs_moved_output_and_exposes_only_registered_files(self):
        moved = self.root / 'Перенесено'
        self.output.rename(moved)
        wrapper = self.server.register_result(moved)
        self.assertEqual(wrapper['report']['output'], str(moved))
        self.assertEqual(wrapper['title'], 'presentation')
        self.assertEqual(self.server.register_result(moved)['id'], wrapper['id'])
        self.assertEqual(wrapper['base_url'], self.server.origin + '/' + wrapper['id'] + '/')
        status, loaded, headers = self.request('api/results/' + wrapper['id'])
        self.assertEqual(status, 200)
        self.assertEqual(loaded, wrapper)
        pdf = wrapper['report']['documents'][0]['file'].replace('\\', '/')
        status, contents, headers = self.request(wrapper['base_url'] + quote(pdf))
        self.assertEqual(status, 200)
        self.assertTrue(contents.startswith(b'%PDF-'))
        self.assertEqual(headers.get_content_type(), 'application/pdf')
        self.assertEqual(self.request(wrapper['base_url'] + quote('Просмотр.html'))[0], 200)
        self.assertEqual(self.request(self.server.origin + '/not-registered/secret.txt')[0], 404)

    def test_history_open_uses_stable_ids_and_http_never_accepts_system_paths(self):
        record = self.jobs.snapshot()['history'][0]
        status, wrapper, _ = self.request('api/open', {'id': record['id']}, origin=True)
        self.assertEqual(status, 200)
        self.assertEqual(wrapper['id'], self.wrapper['id'])
        self.assertEqual(self.request('api/open', {'id': str(self.output)}, origin=True)[0], 404)
        self.assertEqual(self.request('api/open', {'folder': str(self.output)}, origin=True)[0], 400)
        for settings in ({'mode': 'custom', 'folder': str(self.root)}, {'mode': 'custom'},
                         {'mode': 'results', 'folder': str(self.root)}):
            self.assertEqual(self.request('api/settings', settings, origin=True)[0], 400)
        self.assertEqual(self.request('api/start', {'paths': [str(self.root)]}, origin=True)[0], 404)

    def test_settings_save_and_cancel_are_same_origin_json_operations(self):
        for origin in (None, 'http://evil.example', 'null'):
            self.assertEqual(self.request('api/settings', {'mode': 'results'}, origin=origin)[0], 403)
        self.assertEqual(self.request('api/state', origin='http://evil.example')[0], 403)
        status, state, _ = self.request('api/settings', {'mode': 'results'}, origin=True)
        self.assertEqual(status, 200)
        self.assertEqual(state['settings']['mode'], 'results')
        persisted = json.loads((self.root / 'workspace.json').read_text(encoding='utf-8'))
        self.assertEqual(persisted['output_mode'], 'results')
        self.assertFalse(self.request('api/cancel', {}, origin=True)[1]['busy'])
        for body, content_type in ((b'[]', 'application/json'), (b'{broken', 'application/json'),
                                   (b'{}', 'text/plain'), (b'{}', 'application/jsonp')):
            self.assertEqual(self.request('api/settings', origin=True, body=body,
                                          headers={'Content-Type': content_type})[0], 400)
        self.assertEqual(self.request('api/settings', origin=True, body=b' ' * ((1 << 20) + 1))[0], 400)

    def test_encoded_traversal_is_blocked_in_frontend_and_result_routes(self):
        app = self.server.url
        for traversal in ('..%2Fsecret.txt', '..%5Csecret.txt', '%2e%2e/%2e%2e/secret.txt',
                          '%2e%2e%5c%2e%2e%5csecret.txt', quote(str(self.root / 'secret.txt'))):
            with self.subTest(traversal=traversal):
                status, contents, _ = self.request(app + traversal)
                self.assertEqual(status, 404)
                self.assertNotIn(b'PRIVATE FILE', contents if isinstance(contents, bytes) else str(contents).encode())
                self.assertEqual(self.request(self.wrapper['base_url'] + traversal)[0], 404)

    def test_http_cancel_reaches_the_worker_and_busy_settings_do_not_change_destination(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)

        def extract(source, output=None, progress=None):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test did not release the worker')
            progress({'stage': 'labels', 'current': 1, 'total': 1, 'found': 0})
            raise AssertionError('Cancellation did not interrupt the progress callback')

        self.jobs.extractor = extract
        try:
            self.jobs.start([self.root / 'presentation.pdf'])
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.request('api/settings', {'mode': 'results'}, origin=True)[0], 400)
            self.assertEqual(self.jobs.snapshot()['settings']['mode'], 'nearby')
            status, snapshot, _ = self.request('api/cancel', {}, origin=True)
            self.assertEqual(status, 200)
            self.assertTrue(snapshot['busy'])
            self.assertTrue(snapshot['cancelling'])
        finally:
            release.set()
        finished = self.jobs.wait(5)
        self.assertFalse(finished['busy'])
        self.assertIn('Остановлено', finished['status'])
        self.assertIsNone(finished['current_output'])
        self.assertEqual(len(finished['history']), 1)

    def test_apply_uses_transactional_pdf_editor_returns_fresh_wrapper_and_updates_history(self):
        endpoint = 'api/results/' + self.wrapper['id'] + '/apply'
        payload = {'revision': 0, 'removed': [0], 'variants': {'1': [1], '2': [3, 2]}}
        self.assertEqual(self.request(endpoint, payload)[0], 403)
        status, saved, _ = self.request(endpoint, payload, origin=True)
        self.assertEqual(status, 200, saved)
        self.assertTrue(saved['ok'])
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(saved['result']['report']['revision'], 1)
        self.assertTrue((self.output / saved['backup'] / 'Отчёт.json').is_file())
        self.assertEqual(self.colors(self.output / 'Вариант 1' / 'Вариант 1.pdf'), ['green'])
        self.assertEqual(self.colors(self.output / 'Вариант 2' / 'Вариант 2.pdf'), ['yellow', 'blue'])
        self.assertEqual(self.jobs.snapshot()['history'][0]['images'], 3)
        status, stale, _ = self.request(endpoint, payload, origin=True)
        self.assertEqual(status, 409)
        self.assertIn('Обновите страницу', stale['error'])
        invalid = {'revision': 1, 'variants': {'1': [1, 1], '2': [3, 2]}}
        self.assertEqual(self.request(endpoint, invalid, origin=True)[0], 400)
        self.assertEqual(self.request('api/results/unknown/apply', payload, origin=True)[0], 404)

    def test_variant_caption_folder_and_single_pdf_rename_return_current_paths(self):
        endpoint = 'api/results/' + self.wrapper['id'] + '/rename'
        requests = [({'revision': 0, 'kind': 'variant', 'target': 1, 'name': 'Первый вариант'},
                     self.output / 'Первый вариант' / 'Первый вариант.pdf'),
                    ({'revision': 1, 'kind': 'folder', 'target': 'Западный двор', 'name': 'Благоустройство'}, None),
                    ({'revision': 2, 'kind': 'file', 'target': 3, 'name': 'Двор вечером'},
                     self.output / 'Все остальное' / 'Благоустройство' / 'Двор вечером.pdf')]
        for payload, expected_path in requests:
            status, renamed, _ = self.request(endpoint, payload, origin=True)
            self.assertEqual(status, 200, renamed)
            self.assertEqual(renamed['revision'], payload['revision'] + 1)
            self.assertEqual(renamed['result']['report']['revision'], payload['revision'] + 1)
            if expected_path:
                self.assertTrue(expected_path.is_file(), expected_path)
        final = self.request('api/results/' + self.wrapper['id'])[1]
        self.assertEqual(final['report']['images'][3]['folder'], 'Благоустройство')
        self.assertEqual(Path(final['report']['images'][3]['file']).name, 'Двор вечером.pdf')
        self.assertEqual(self.request(endpoint, requests[0][0], origin=True)[0], 409)

    def test_shutdown_waits_for_an_inflight_pdf_transaction_and_never_restarts(self):
        import photo_edit
        entered, release, stopped = Event(), Event(), Event()
        self.addCleanup(release.set)
        original = photo_edit.write_gallery
        response, errors = [], []
        endpoint = self.server.url + 'api/results/' + self.wrapper['id'] + '/apply'

        def slow_gallery(folder, report):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test did not release the pending save')
            return original(folder, report)

        def save():
            try:
                response.append(self.request(endpoint, {'revision': 0, 'variants': {'1': [1], '2': [2, 0]}},
                                             origin=True))
            except Exception as error:
                errors.append(error)

        client = Thread(target=save)
        closer = Thread(target=lambda: (self.server.stop(), stopped.set()))
        with patch.object(photo_edit, 'write_gallery', slow_gallery):
            try:
                client.start()
                self.assertTrue(entered.wait(5))
                closer.start()
                self.assertFalse(stopped.wait(.2), 'Shutdown returned before the transaction committed')
            finally:
                release.set()
                if closer.ident is not None:
                    closer.join(5)
                client.join(5)
        self.assertTrue(stopped.is_set())
        self.assertEqual(errors, [])
        self.assertEqual(response[0][0], 200)
        self.assertEqual(response[0][1]['result']['report']['revision'], 1)
        self.assertEqual(photo_edit.load_report(self.output)['revision'], 1)
        with self.assertRaises(RuntimeError):
            self.server.register_result(self.output)


if __name__ == '__main__':
    unittest.main(verbosity=2)
