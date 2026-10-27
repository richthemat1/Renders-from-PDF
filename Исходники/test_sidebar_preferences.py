"""Sidebar layout persists independently of stale windows and local HTTP origins."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from photo_desktop_server import DesktopServer
from photo_jobs import JobService
from photo_state import WorkspaceState


class SidebarFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='pdf-sidebar-state-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'workspace.json'

    def service(self):
        jobs = JobService(self.path)
        self.addCleanup(jobs.close)
        return jobs

    def report(self, name):
        source = self.root / (name + '.pdf')
        source.write_bytes(b'original PDF')
        output = self.root / (name + ' — PDF')
        output.mkdir()
        return {'source': str(source), 'output': str(output), 'images': [{}],
                'documents': [{'variant': 1, 'pages': 1}], 'counts_by_status': {'named': 1}}


class SidebarPreferencesTests(SidebarFixture):
    def test_default_sidebar_is_returned_in_snapshot_and_can_be_saved(self):
        jobs = self.service()
        self.assertEqual(jobs.snapshot().get('sidebar'), {'width': 248, 'hidden': False})
        self.assertTrue(jobs.workspace.save())
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['sidebar'],
                         {'width': 248, 'hidden': False})

    def test_sidebar_survives_service_restart_and_snapshot_is_independent(self):
        first = self.service()
        self.assertEqual(first.set_sidebar(420, True), {'width': 420, 'hidden': True})
        snapshot = first.snapshot()
        snapshot['sidebar']['width'] = 300
        self.assertEqual(first.snapshot()['sidebar'], {'width': 420, 'hidden': True})
        self.assertEqual(self.service().snapshot()['sidebar'], {'width': 420, 'hidden': True})

    def test_legacy_and_invalid_sidebar_records_keep_history_failures_and_output_settings(self):
        state = WorkspaceState(self.path)
        state.set_output('results')
        state.remember(self.report('Первый'))
        state.remember_failure(self.root / 'Нельзя прочитать.pdf', 'Ошибка чтения')
        baseline = json.loads(self.path.read_text(encoding='utf-8'))
        invalid_records = [None, [], {}, {'width': 248}, {'hidden': False},
                           {'width': 247, 'hidden': False}, {'width': 521, 'hidden': True},
                           {'width': True, 'hidden': False}, {'width': 300.0, 'hidden': False},
                           {'width': float('nan'), 'hidden': False},
                           {'width': float('inf'), 'hidden': False},
                           {'width': '320', 'hidden': False}, {'width': 320, 'hidden': 1},
                           {'width': 320, 'hidden': 'false'},
                           {'width': 320, 'hidden': False, 'unknown': True}]
        legacy = dict(baseline)
        legacy.pop('sidebar', None)
        records = [legacy] + [dict(baseline, sidebar=invalid) for invalid in invalid_records]
        for record in records:
            with self.subTest(sidebar=record.get('sidebar', 'missing')):
                self.path.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
                restored = WorkspaceState(self.path)
                self.assertEqual(getattr(restored, 'sidebar', None), {'width': 248, 'hidden': False})
                self.assertEqual(restored.output_mode, 'results')
                self.assertEqual(restored.history, baseline['history'])
                self.assertEqual(restored.failures, baseline['failures'])
                self.assertFalse(restored.warning)
                self.assertTrue(restored.save())
                self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['sidebar'],
                                 {'width': 248, 'hidden': False})

    def test_strict_sidebar_types_are_rejected_without_mutating_saved_or_active_preferences(self):
        state = WorkspaceState(self.path)
        self.assertTrue(state.set_sidebar(350, True))
        original = self.path.read_bytes()
        invalid = [(True, False), (248.0, False), (float('nan'), False), (float('inf'), False),
                   ('320', False), (None, False), (247, False), (521, False),
                   (248, 0), (248, 1), (248, 'false'), (248, None)]
        for width, hidden in invalid:
            with self.subTest(width=width, hidden=hidden):
                with self.assertRaises(ValueError):
                    state.set_sidebar(width, hidden)
                self.assertEqual(state.sidebar, {'width': 350, 'hidden': True})
                self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(state.set_sidebar(248, False))
        self.assertTrue(state.set_sidebar(520, True))

    def test_stale_windows_merge_history_settings_and_failures_without_reverting_sidebar(self):
        first, second = self.service(), self.service()
        first.set_output('results')
        first.set_sidebar(430, True)
        second.remember(self.report('Второй'))
        second.workspace.remember_failure(self.root / 'Ошибка.pdf', 'Попробуйте снова')
        second.set_output('nearby')
        restored = self.service().snapshot()
        self.assertEqual(restored['sidebar'], {'width': 430, 'hidden': True})
        self.assertEqual(restored['settings']['mode'], 'nearby')
        self.assertEqual(len(restored['history']), 1)
        self.assertEqual(len(restored['failures']), 1)
        # A sidebar update from the older window must merge the latest output setting.
        first.set_sidebar(520, False)
        saved = self.service().snapshot()
        self.assertEqual(saved['sidebar'], {'width': 520, 'hidden': False})
        self.assertEqual(saved['settings']['mode'], 'nearby')
        self.assertEqual(saved['history'], restored['history'])
        self.assertEqual(saved['failures'], restored['failures'])
        # Successful saves consume their explicit change; later plain saves keep newer values.
        second.set_sidebar(248, True)
        self.assertTrue(first.workspace.save())
        self.assertEqual(self.service().snapshot()['sidebar'], {'width': 248, 'hidden': True})

    def test_simultaneous_window_writes_keep_sidebar_history_and_output_settings(self):
        sidebar, history, settings = self.service(), self.service(), self.service()
        report = self.report('Одновременно')
        ready = Barrier(3)

        def update(operation):
            ready.wait(timeout=5)
            operation()

        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(update, [lambda: sidebar.set_sidebar(390, True),
                                   lambda: history.remember(report),
                                   lambda: settings.set_output('results')]))
        restored = self.service().snapshot()
        self.assertEqual(restored['sidebar'], {'width': 390, 'hidden': True})
        self.assertEqual(restored['settings']['mode'], 'results')
        self.assertEqual(restored['history'][0]['name'], 'Одновременно.pdf')

    def test_failed_save_keeps_session_layout_and_reports_workspace_warning(self):
        jobs = self.service()
        # A real file in place of the required state directory makes persistence fail.
        blocked = self.root / 'blocked'
        blocked.write_text('file, not folder', encoding='utf-8')
        jobs.workspace.path = blocked / 'workspace.json'
        with self.assertLogs('pdf-photos', level='WARNING'):
            self.assertFalse(jobs.workspace.set_sidebar(360, True))
        self.assertEqual(jobs.snapshot()['sidebar'], {'width': 360, 'hidden': True})
        self.assertTrue(jobs.snapshot()['warning'])
        with self.assertLogs('pdf-photos', level='WARNING'):
            with self.assertRaises(RuntimeError):
                jobs.set_sidebar(400, False)
        self.assertEqual(jobs.snapshot()['sidebar'], {'width': 400, 'hidden': False})
        self.assertTrue(jobs.snapshot()['warning'])
        self.assertEqual(jobs.snapshot()['logs'][-1]['level'], 'warn')
        jobs.workspace.path = self.path
        self.assertTrue(jobs.workspace.save())
        self.assertFalse(jobs.snapshot()['warning'])
        self.assertEqual(self.service().snapshot()['sidebar'], {'width': 400, 'hidden': False})

    def test_closing_service_rejects_sidebar_updates(self):
        jobs = self.service()
        jobs.close()
        with self.assertRaises(RuntimeError):
            jobs.set_sidebar(400, False)
        self.assertEqual(jobs.snapshot()['sidebar'], {'width': 248, 'hidden': False})


class SidebarApiTests(SidebarFixture):
    def server(self):
        server = DesktopServer(self.service(), self.root).start()
        self.addCleanup(server.stop)
        return server

    def request(self, server, path='api/sidebar', data=None, origin=True, body=None, headers=None):
        headers = dict(headers or {})
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        if body is not None:
            headers.setdefault('Content-Type', 'application/json')
        if origin:
            headers['Origin'] = server.origin if origin is True else origin
        address = path if path.startswith('http://') else server.url + path
        try:
            response = urlopen(Request(address, body, headers), timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.code, json.loads(response.read())

    def test_token_scoped_api_returns_sidebar_only_and_restores_after_origin_change(self):
        first = self.server()
        status, initial = self.request(first, 'api/state')
        self.assertEqual(status, 200)
        self.assertEqual(initial.get('sidebar'), {'width': 248, 'hidden': False})
        status, saved = self.request(first, data={'width': 460, 'hidden': True})
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved, {'width': 460, 'hidden': True})
        # Holding the first port while starting the replacement proves a distinct origin.
        replacement = self.server()
        self.assertNotEqual(first.origin, replacement.origin)
        first.stop()
        status, restored = self.request(replacement, 'api/state')
        self.assertEqual(status, 200)
        self.assertEqual(restored['sidebar'], {'width': 460, 'hidden': True})
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['sidebar'], saved)

    def test_sidebar_api_enforces_app_token_and_same_origin(self):
        server = self.server()
        for origin in (None, 'http://evil.example', 'null'):
            with self.subTest(origin=origin):
                self.assertEqual(self.request(server, data={'width': 320, 'hidden': False}, origin=origin)[0], 403)
        self.assertEqual(self.request(server, server.origin + '/app/wrong-token/api/sidebar',
                                      data={'width': 320, 'hidden': False})[0], 404)
        self.assertEqual(self.request(server, 'api/state', origin='http://evil.example')[0], 403)
        self.assertEqual(server.jobs.snapshot().get('sidebar'), {'width': 248, 'hidden': False})

    def test_sidebar_api_reports_failed_persistence_and_retains_session_preferences(self):
        server = self.server()
        blocked = self.root / 'blocked'
        blocked.write_text('file, not folder', encoding='utf-8')
        server.jobs.workspace.path = blocked / 'workspace.json'
        with self.assertLogs('pdf-photos', level='WARNING'):
            status, failed = self.request(server, data={'width': 400, 'hidden': True})
        self.assertIn(status, (400, 500), failed)
        self.assertTrue(failed['message'])
        self.assertIn('сохранить', failed['message'])
        status, active = self.request(server, 'api/state')
        self.assertEqual(status, 200)
        self.assertEqual(active['sidebar'], {'width': 400, 'hidden': True})
        self.assertTrue(active['warning'])

    def test_sidebar_api_rejects_invalid_fields_types_and_non_json_input(self):
        server = self.server()
        invalid = [{}, {'width': 320}, {'hidden': False}, {'width': 320, 'hidden': False, 'extra': 1},
                   {'width': True, 'hidden': False}, {'width': 320.0, 'hidden': False},
                   {'width': float('nan'), 'hidden': False}, {'width': float('inf'), 'hidden': False},
                   {'width': 247, 'hidden': False}, {'width': 521, 'hidden': False},
                   {'width': '320', 'hidden': False}, {'width': 320, 'hidden': 0},
                   {'width': 320, 'hidden': 'false'}, {'width': 320, 'hidden': None}]
        for data in invalid:
            with self.subTest(data=data):
                self.assertEqual(self.request(server, data=data)[0], 400)
        for body, content_type in ((b'[]', 'application/json'), (b'{broken', 'application/json'),
                                   (b'{}', 'text/plain'), (b'{}', 'application/jsonp')):
            with self.subTest(body=body, content_type=content_type):
                self.assertEqual(self.request(server, body=body, headers={'Content-Type': content_type})[0], 400)
        self.assertEqual(self.request(server, body=b' ' * ((1 << 20) + 1))[0], 400)
        self.assertEqual(server.jobs.snapshot().get('sidebar'), {'width': 248, 'hidden': False})


if __name__ == '__main__':
    unittest.main(verbosity=2)
