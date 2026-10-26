"""Native bridge boundaries; uses fake dialogs without opening or changing user windows."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from photo_jobs import JobService
from photo_desktop_server import DesktopServer


class NativeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.jobs = JobService(self.root / 'state.json')
        self.server = DesktopServer(self.jobs, self.root).start()
        self.addCleanup(self.server.stop)
        self.addCleanup(self.jobs.close)
        self.window = Mock()
        self.window.get_current_url.return_value = self.server.url
        from photo_desktop import NativeBridge
        self.bridge = NativeBridge(self.jobs, self.server, self.root / 'app.log')
        self.bridge._window = self.window

    def test_cancelled_dialog_does_not_start_a_queue(self):
        self.window.create_file_dialog.return_value = None
        snapshot = self.bridge.choose_pdfs()
        self.assertFalse(snapshot['busy'])
        self.assertEqual(snapshot['history'], [])

    def test_pdf_picker_passes_pywebview_validation_and_processes_multiple_pdfs(self):
        from webview import FileDialog
        from webview.util import parse_file_type
        from webview.window import Window
        from test_photos import make_pdf

        # Keep pywebview's real dialog validation; replace only the OS backend.
        window = Window('pdf-picker-test', 'Фото из PDF', self.server.url)
        window.events.shown.set()
        window.events.loaded.set()
        window.gui = Mock()
        window.gui.get_current_url.return_value = self.server.url
        sources = []
        for name in ('Первая презентация', 'Вторая презентация'):
            folder = self.root / name
            folder.mkdir()
            sources.append(str(make_pdf(folder, [{
                'images': [{'box': (50, 170, 800, 500)}],
                'text': [('Вариант 1. Двор', 50, 120, 18)],
            }])))
        window.gui.create_file_dialog.return_value = tuple(sources)
        self.bridge._window = window

        self.bridge.choose_pdfs()
        finished = self.jobs.wait(10)

        self.assertFalse(finished['busy'])
        self.assertEqual(finished['totals']['images'], 2)
        self.assertEqual(finished['totals']['documents'], 2)
        self.assertEqual(len(finished['history']), 2)
        self.assertTrue(all(item['available'] for item in finished['history']))
        dialog, _, multiple, _, filters, _ = window.gui.create_file_dialog.call_args.args
        self.assertEqual(dialog, FileDialog.OPEN)
        self.assertTrue(multiple)
        self.assertEqual([parse_file_type(value)[1] for value in filters], ['*.pdf'])

    def test_untrusted_document_cannot_open_a_native_dialog(self):
        self.window.get_current_url.return_value = 'https://example.org/'
        with self.assertRaises(ValueError):
            self.bridge.choose_pdfs()
        self.window.create_file_dialog.assert_not_called()

    def test_native_open_only_allows_registered_pdf_or_result_root(self):
        folder = self.root / 'result'
        folder.mkdir()
        (folder / 'photo.pdf').write_bytes(b'pdf')
        (folder / 'unsafe.exe').write_bytes(b'executable')
        self.server.folders['registered'] = folder.resolve()
        with patch('photo_desktop.open_local') as opened:
            self.assertTrue(self.bridge.open_result('registered', 'photo.pdf'))
            self.assertTrue(self.bridge.open_result('registered'))
            self.assertEqual(opened.call_count, 2)
            for key, relative in [('unknown', ''), ('registered', '../state.json'), ('registered', 'unsafe.exe')]:
                with self.assertRaises(ValueError):
                    self.bridge.open_result(key, relative)
            self.assertEqual(opened.call_count, 2)

    def test_custom_destination_comes_from_the_native_folder_dialog(self):
        self.window.create_file_dialog.return_value = [str(self.root)]
        snapshot = self.bridge.choose_output()
        self.assertEqual(snapshot['settings']['mode'], 'custom')
        self.assertEqual(snapshot['settings']['folder'], str(self.root.resolve()))

    def test_unsaved_flag_requires_real_boolean_and_trusted_document(self):
        self.assertTrue(self.bridge.set_unsaved(True))
        self.assertTrue(self.bridge._unsaved)
        with self.assertRaises(ValueError):
            self.bridge.set_unsaved('false')
        self.bridge.set_unsaved(False)
        self.assertFalse(self.bridge._unsaved)

    def test_late_bridge_message_cannot_overwrite_newer_unsaved_state(self):
        self.bridge.set_unsaved(True, 2)
        self.bridge.set_unsaved(False, 1)
        self.assertTrue(self.bridge._unsaved)
        self.bridge.set_unsaved(False, 3)
        self.assertFalse(self.bridge._unsaved)


if __name__ == '__main__':
    unittest.main()
