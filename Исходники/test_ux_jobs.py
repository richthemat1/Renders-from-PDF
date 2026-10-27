"""UX recovery contracts exercised against temporary PDFs, results, state and live HTTP."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from photo_desktop import NativeBridge
from photo_desktop_server import DesktopServer
from photo_export import OTHER_DIR, OutputPathError, extract, planned_output
from photo_jobs import JobService, history_id
from photo_state import WorkspaceState
from test_photos import make_pdf


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pdf-ux-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state_path = self.root / 'workspace.json'
        self.jobs = JobService(self.state_path)
        self.addCleanup(self.jobs.close)

    def source(self, folder=None, name='Презентация', two=False):
        folder = folder or self.root
        folder.mkdir(parents=True, exist_ok=True)
        pages = [{'images': [{'box': (50, 170, 800, 500), 'size': (2400, 1500), 'color': color}],
                  'text': [('Вариант 1. Двор', 50, 120, 18)]}
                 for color in (['#ff0000', '#00ff00'] if two else ['#ff0000'])]
        source = make_pdf(folder, pages)
        target = folder / (name + '.pdf')
        source.rename(target)
        return target

    def finish(self, jobs=None):
        snapshot = (jobs or self.jobs).wait(15)
        self.assertFalse(snapshot['busy'], 'PDF worker did not finish')
        return snapshot

    def server(self):
        frontend = self.root / 'frontend'
        frontend.mkdir(exist_ok=True)
        (frontend / 'index.html').write_text('<!doctype html><title>UX test</title>', encoding='utf-8')
        server = DesktopServer(self.jobs, frontend).start()
        self.addCleanup(server.stop)
        return server

    def request(self, server, path, data=None, origin=True):
        headers = {}
        body = None
        if origin:
            headers['Origin'] = server.origin if origin is True else origin
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        try:
            response = urlopen(Request(server.url + 'api/' + path, body, headers), timeout=15)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return (response.code, json.loads(raw) if response.headers.get_content_type() == 'application/json'
                    else raw)

    def test_generated_long_output_name_is_shortened_before_processing(self):
        parent = self.root
        while len(str(parent)) < 125:
            parent /= 'Документы строительного объекта'
        source = self.source(parent, 'Презентация архитектурных решений ' + 'очень длинное название ' * 2)
        original = source.read_bytes()
        destination = planned_output(source)
        self.assertLessEqual(len(str(destination / OTHER_DIR)), 198)
        self.assertLess(len(destination.name), 84)
        report = extract(source)
        self.assertEqual(Path(report['output']), destination)
        self.assertEqual(source.read_bytes(), original)
        self.assertTrue((destination / report['documents'][0]['file']).is_file())
        self.assertLessEqual(len(str(planned_output(source) / OTHER_DIR)), 198)

    def test_explicit_long_path_is_rejected_before_pdf_analysis(self):
        source = self.source()
        destination = self.root / ('д' * 160)
        with patch('photo_export.inspect') as inspect:
            with self.assertRaisesRegex(OutputPathError, 'Выбрать другую папку'):
                extract(source, destination)
        inspect.assert_not_called()
        self.assertFalse(destination.exists())

    def test_preflight_failure_is_visible_and_persisted_without_starting_pdf_reader(self):
        source = self.source()
        target = self.root / ('д' * 160)
        target.mkdir()
        self.jobs.set_output('custom', target)
        with patch('photo_export.inspect') as inspect:
            snapshot = self.jobs.start([source])
        inspect.assert_not_called()
        self.assertFalse(snapshot['busy'])
        self.assertEqual(snapshot['failures'][0]['name'], source.name)
        self.assertEqual(snapshot['failures'][0]['action'], 'choose_output')
        restored = JobService(self.state_path)
        self.addCleanup(restored.close)
        self.assertEqual(restored.snapshot()['failures'], snapshot['failures'])
        self.assertEqual(restored.snapshot()['history'], [])

    def test_failed_pdf_can_retry_by_trusted_id_and_success_clears_persisted_failure(self):
        source = self.source()
        original = source.read_bytes()
        source.write_bytes(b'%PDF-1.7\nbroken PDF')
        with self.assertLogs('pdf-photos', level='ERROR'):
            self.jobs.start([source])
            failed = self.finish()
        self.assertEqual(len(failed['failures']), 1)
        server = self.server()
        id = failed['failures'][0]['id']
        self.assertEqual(self.request(server, f'failures/{id}/retry', {}, origin=False)[0], 403)
        self.assertEqual(self.request(server, f'failures/{id}/retry', {'paths': [str(source)]})[0], 400)
        self.assertEqual(self.request(server, 'failures/not-registered/retry', {})[0], 400)
        source.write_bytes(original)
        self.assertEqual(self.request(server, f'failures/{id}/retry', {})[0], 200)
        successful = self.finish()
        self.assertEqual(successful['failures'], [])
        self.assertEqual(len(successful['history']), 1)
        self.assertEqual(WorkspaceState(self.state_path).failures, [])

    def test_duplicate_confirmation_never_accepts_paths_or_replays_old_token(self):
        source = self.source()
        self.jobs.start([source])
        completed = self.finish()
        previous = completed['history'][0]
        pending = self.jobs.prepare([source])
        self.assertFalse(pending['busy'])
        self.assertEqual(pending['pending_repeat']['files'][0]['previous_id'], previous['id'])
        self.assertEqual(len(pending['history']), 1)
        server = self.server()
        token = pending['pending_repeat']['token']
        self.assertEqual(self.request(server, 'jobs/confirm', {'token': token, 'proceed': True,
                                                              'paths': [str(source)]})[0], 400)
        self.assertEqual(self.request(server, 'jobs/confirm', {'token': token, 'proceed': False})[0], 200)
        self.assertEqual(self.request(server, 'jobs/confirm', {'token': token, 'proceed': True})[0], 400)
        pending = self.jobs.repeat_history(previous['id'])
        self.assertFalse(pending['busy'])
        self.assertEqual(self.request(server, 'jobs/confirm', {'token': pending['pending_repeat']['token'],
                                                              'proceed': True})[0], 200)
        repeated = self.finish()
        self.assertEqual(len(repeated['history']), 2)
        self.assertTrue(Path(previous['output']).is_dir())
        self.assertIsNone(repeated['pending_repeat'])

    def test_forget_removes_only_history_and_stale_settings_do_not_resurrect_it(self):
        self.jobs.start([self.source()])
        record = self.finish()['history'][0]
        report = Path(record['output']) / 'Отчёт.json'
        original = report.read_bytes()
        stale = WorkspaceState(self.state_path)
        server = self.server()
        self.assertEqual(self.request(server, f'history/{record["id"]}/forget', {})[0], 200)
        stale.set_output('results')
        self.assertEqual(WorkspaceState(self.state_path).history, [])
        self.assertEqual(report.read_bytes(), original)
        self.assertTrue(report.parent.is_dir())

    def test_missing_result_can_relocate_through_native_picker_preserving_its_history(self):
        self.jobs.start([self.source()])
        record = self.finish()['history'][0]
        old = Path(record['output'])
        moved = self.root / 'Перенесённый результат'
        old.rename(moved)
        self.assertFalse(self.jobs.snapshot()['history'][0]['available'])
        server = self.server()
        window = Mock()
        window.get_current_url.return_value = server.url
        window.create_file_dialog.return_value = [str(moved)]
        bridge = NativeBridge(self.jobs, server, None)
        bridge._window = window
        with patch('photo_desktop.open_local') as opened:
            result = bridge.relocate_result(record['id'])
            item = self.jobs.snapshot()['history'][0]
            self.assertEqual(item['id'], history_id(moved))
            self.assertEqual(item['finished_at'], record['finished_at'])
            self.assertEqual(result['report']['output'], str(moved))
            self.assertTrue(bridge.open_history_folder(item['id']))
            opened.assert_called_once_with(moved)
        self.assertEqual(len(self.jobs.snapshot()['history']), 1)
        self.assertEqual(WorkspaceState(self.state_path).history[0]['output'], str(moved))

    def test_failure_forget_does_not_modify_source_and_survives_restart(self):
        source = self.source()
        original = source.read_bytes()
        failure = self.jobs.workspace.remember_failure(source, 'Нет доступа к папке результата', 'choose_output')
        stale = WorkspaceState(self.state_path)
        server = self.server()
        self.assertEqual(self.request(server, f'failures/{failure["id"]}/forget', {})[0], 200)
        stale.set_output('results')
        self.assertEqual(WorkspaceState(self.state_path).failures, [])
        self.assertEqual(source.read_bytes(), original)

    def test_prepared_replacement_invalidates_old_token_and_uses_updated_output_setting(self):
        first, second = self.source(name='Первый'), self.source(name='Второй')
        self.jobs.start([first, second])
        self.assertEqual(len(self.finish()['history']), 2)
        first_token = self.jobs.prepare([first])['pending_repeat']['token']
        custom = self.root / 'Другой результат'
        custom.mkdir()
        self.jobs.set_output('custom', custom)
        replacement = self.jobs.prepare([second])['pending_repeat']
        self.assertNotEqual(replacement['token'], first_token)
        with self.assertRaises(ValueError):
            self.jobs.confirm_repeat(first_token, True)
        self.assertEqual(self.jobs.snapshot()['pending_repeat'], replacement)
        self.jobs.confirm_repeat(replacement['token'], True)
        completed = self.finish()
        self.assertEqual(Path(completed['current_output']).parent, custom)
        self.assertEqual(Path(completed['history'][0]['source']), second)

    def test_canceling_retry_confirmation_keeps_failure_until_success(self):
        source = self.source()
        self.jobs.start([source])
        self.finish()
        target = self.root / ('д' * 160)
        target.mkdir()
        self.jobs.set_output('custom', target)
        failure = self.jobs.start([source])['failures'][0]
        self.jobs.set_output('nearby')
        pending = self.jobs.retry_failure(failure['id'])['pending_repeat']
        self.assertIsNotNone(pending)
        self.jobs.confirm_repeat(pending['token'], False)
        self.assertEqual(self.jobs.snapshot()['failures'][0]['id'], failure['id'])
        self.assertEqual(WorkspaceState(self.state_path).failures[0]['id'], failure['id'])
        pending = self.jobs.retry_failure(failure['id'])['pending_repeat']
        self.jobs.confirm_repeat(pending['token'], True)
        self.assertEqual(self.finish()['failures'], [])
        self.assertEqual(WorkspaceState(self.state_path).failures, [])

    def test_success_from_stale_window_clears_a_failure_saved_by_another_window(self):
        source = self.source()
        stale = WorkspaceState(self.state_path)
        failure = self.jobs.workspace.remember_failure(source, 'Не удалось записать PDF', 'choose_output')
        self.assertEqual(WorkspaceState(self.state_path).failures[0]['id'], failure['id'])
        stale.remember(extract(source), processed=True)
        self.assertEqual(WorkspaceState(self.state_path).failures, [])

    def test_opening_and_editing_another_result_preserves_the_processed_batch_result(self):
        other = extract(self.source(name='Предыдущая презентация', two=True))
        self.jobs.start([self.source(name='Текущая презентация')])
        processed = self.finish()['current_output']
        self.assertIsNotNone(processed)
        server = self.server()
        registered = server.register_result(other['output'])
        self.assertEqual(self.jobs.current_output, processed)
        record = next(item for item in self.jobs.snapshot()['history'] if item['output'] == other['output'])
        status, opened = self.request(server, 'open', {'id': record['id']})
        self.assertEqual(status, 200, opened)
        self.assertEqual(self.jobs.snapshot()['current_output'], processed)
        status, saved = self.request(server, f'results/{registered["id"]}/apply',
                                     {'revision': 0, 'variants': {'1': [1]}, 'removed': [0]})
        self.assertEqual(status, 200, saved)
        self.assertEqual(self.jobs.snapshot()['current_output'], processed)

    def test_remembering_a_result_in_a_fresh_service_does_not_create_a_batch_result(self):
        self.assertFalse(self.jobs.snapshot()['has_job'])
        report = extract(self.source())
        self.jobs.remember(report, processed=False)
        self.assertIsNone(self.jobs.current_output)
        self.assertIsNone(self.jobs.snapshot()['current_output'])
        self.jobs.import_result(report['output'])
        self.assertIsNone(self.jobs.current_output)
        self.assertFalse(self.jobs.snapshot()['has_job'])

    def test_canceling_before_the_first_result_preserves_the_job_status(self):
        import photo_export
        source = self.source()
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        original = photo_export.inspect

        def inspect(path, progress=None):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test did not release the PDF reader')
            return original(path, progress)

        self.assertFalse(self.jobs.snapshot()['has_job'])
        with patch('photo_export.inspect', inspect):
            started = self.jobs.start([source])
            self.assertTrue(started['has_job'])
            self.assertTrue(entered.wait(5))
            self.jobs.cancel()
            release.set()
            stopped = self.finish()
        self.assertTrue(stopped['has_job'])
        self.assertFalse(stopped['busy'])
        self.assertIsNone(stopped['current_output'])
        self.assertEqual(stopped['progress'], 0)
        self.assertEqual(stopped['history'], [])
        self.assertIn('Остановлено', stopped['status'])

    def test_relocating_updates_only_the_current_batch_result_pointer(self):
        other = extract(self.source(name='Другой результат'))
        other_item = self.jobs.remember(other, processed=False)
        self.jobs.start([self.source(name='Результат обработки')])
        current = self.finish()['current_output']
        current_id = history_id(current)
        other_moved = self.root / 'Другая папка перенесена'
        Path(other['output']).rename(other_moved)
        self.jobs.relocate_history(other_item['id'], other_moved)
        self.assertEqual(self.jobs.current_output, current)
        current_moved = self.root / 'Папка обработки перенесена'
        Path(current).rename(current_moved)
        self.jobs.relocate_history(current_id, current_moved)
        self.assertEqual(self.jobs.current_output, str(current_moved))
        self.assertEqual(self.jobs.snapshot()['current_output'], str(current_moved))

    def test_large_preview_uses_report_entry_active_removed_and_undo_save(self):
        self.jobs.start([self.source(two=True)])
        record = self.finish()['history'][0]
        server = self.server()
        result = server.register_result(record['output'])
        id = result['id']
        for suffix in ('-1', '999', '../workspace.json', '0.0'):
            self.assertIn(self.request(server, f'results/{id}/preview/{suffix}')[0], (400, 404))
        self.assertEqual(self.request(server, f'results/{id}/preview/0', origin='https://example.org')[0], 403)
        status, pixels = self.request(server, f'results/{id}/preview/0?revision=0')
        self.assertEqual(status, 200)
        with Image.open(BytesIO(pixels)) as image:
            self.assertEqual(max(image.size), 1600)
            self.assertEqual(image.convert('RGB').getpixel((20, 20)), (255, 0, 0))
        payload = {'revision': 0, 'variants': {'1': [1]}, 'removed': [0]}
        status, saved = self.request(server, f'results/{id}/apply', payload)
        self.assertEqual(status, 200, saved)
        status, pixels = self.request(server, f'results/{id}/preview/0?revision=1')
        self.assertEqual(status, 200)
        with Image.open(BytesIO(pixels)) as image:
            self.assertEqual(image.convert('RGB').getpixel((20, 20)), (255, 0, 0))
        status, undone = self.request(server, f'results/{id}/undo-save', {'revision': 1})
        self.assertEqual(status, 200, undone)
        self.assertEqual(undone['result']['report']['revision'], 2)
        self.assertFalse(any(entry.get('removed') for entry in undone['result']['report']['images']))
        self.assertEqual(self.request(server, f'results/{id}/undo-save', {'revision': 1})[0], 409)

    def test_full_help_bridge_opens_the_shipped_local_instruction(self):
        server = self.server()
        bridge = NativeBridge(self.jobs, server, None)
        bridge._window = Mock()
        bridge._window.get_current_url.return_value = server.url
        with patch('photo_desktop.open_local') as opened:
            self.assertTrue(bridge.open_help())
        path = opened.call_args.args[0]
        self.assertEqual(path.name, 'Как пользоваться.txt')
        self.assertGreater(len(path.read_text(encoding='utf-8')), 1000)


if __name__ == '__main__':
    unittest.main()
