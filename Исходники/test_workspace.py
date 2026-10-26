"""User-facing workspace state: real history and safe output destinations."""
import gc
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch


class WorkspaceState(unittest.TestCase):
    def state(self, path):
        self.assertIsNotNone(importlib.util.find_spec('photo_state'), 'Нет локального состояния рабочего пространства')
        from photo_state import WorkspaceState
        return WorkspaceState(path)

    def report(self, root, name='presentation', images=4):
        source = root / f'{name}.pdf'
        source.write_bytes(b'source-pdf')
        output = root / f'{name} — PDF'
        output.mkdir(exist_ok=True)
        return {'source': str(source), 'output': str(output), 'images': [{}] * images,
                'documents': [{'variant': 1, 'pages': images}], 'counts_by_status': {'named': images},
                'errors': [], 'warnings': []}

    def test_real_history_and_settings_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = self.state(root / 'state.json')
            self.assertEqual(state.history, [])
            self.assertIsNone(state.destination(root / 'input.pdf'))
            state.set_output('results')
            report = self.report(root)
            state.remember(report)
            restored = self.state(root / 'state.json')
            self.assertEqual(restored.output_mode, 'results')
            self.assertEqual(restored.history[0]['images'], 4)
            self.assertEqual(restored.history[0]['output'], report['output'])
            self.assertEqual(restored.history[0]['variants'], 1)

    def test_custom_destination_preserves_existing_results_and_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'Объект.pdf'
            source.write_bytes(b'original')
            target = root / 'export'
            target.mkdir()
            existing = target / 'Объект — PDF'
            existing.mkdir()
            marker = existing / 'не менять.txt'
            marker.write_text('old', encoding='utf-8')
            state = self.state(root / 'state.json')
            state.set_output('custom', target)
            self.assertEqual(state.destination(source), target / 'Объект — PDF (2)')
            self.assertEqual(source.read_bytes(), b'original')
            self.assertEqual(marker.read_text(encoding='utf-8'), 'old')
            self.assertFalse((target / 'Объект — PDF (2)').exists())

    def test_separate_results_folder_is_relative_to_each_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = self.state(root / 'state.json')
            state.set_output('results')
            self.assertEqual(state.destination(root / 'one' / 'А.pdf'), root / 'one' / 'Результаты' / 'А — PDF')
            self.assertEqual(state.destination(root / 'two' / 'Б.pdf'), root / 'two' / 'Результаты' / 'Б — PDF')

    def test_reopening_a_result_updates_counts_without_duplicating_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = self.state(root / 'state.json')
            report = self.report(root)
            state.remember(report)
            first_time = state.history[0]['finished_at']
            report['images'] = [{}] * 2
            state.remember(report, processed=False)
            self.assertEqual(len(state.history), 1)
            self.assertEqual(state.history[0]['images'], 2)
            self.assertEqual(state.history[0]['finished_at'], first_time)

    def test_corrupt_preferences_do_not_prevent_startup_and_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'state.json'
            path.write_text('{broken', encoding='utf-8')
            state = self.state(path)
            self.assertEqual(state.history, [])
            self.assertEqual(state.output_mode, 'nearby')
            self.assertTrue(state.warning)
            state.set_output('results')
            backups = list(root.glob('state.json.corrupt-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding='utf-8'), '{broken')
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['output_mode'], 'results')

    def test_invalid_history_records_and_settings_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'state.json'
            path.write_text(json.dumps({'output_mode': 'custom', 'output_folder': [],
                                        'history': [None, {}, {'output': '../bad', 'images': 'not-a-number'}]}), encoding='utf-8')
            state = self.state(path)
            self.assertEqual(state.output_mode, 'nearby')
            self.assertEqual(state.history, [])

    def test_custom_mode_requires_an_existing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = self.state(root / 'state.json')
            with self.assertRaises(ValueError):
                state.set_output('custom', root / 'missing')
            self.assertEqual(state.output_mode, 'nearby')

    def test_two_windows_preserve_each_others_history_and_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = self.state(root / 'state.json'), self.state(root / 'state.json')
            report_a, report_b = self.report(root, 'A'), self.report(root, 'B')
            first.set_output('results')
            first.remember(report_a)
            second.remember(report_b)
            restored = self.state(root / 'state.json')
            self.assertEqual({item['name'] for item in restored.history}, {'A.pdf', 'B.pdf'})
            self.assertEqual(restored.output_mode, 'results')
            second.set_output('nearby')
            first.remember(report_a, processed=False)
            restored = self.state(root / 'state.json')
            self.assertEqual(len(restored.history), 2)
            self.assertEqual(restored.output_mode, 'nearby')

    def test_settings_from_a_stale_window_do_not_revert_edited_result_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.state(root / 'state.json')
            report = self.report(root)
            first.remember(report)
            second = self.state(root / 'state.json')
            report['images'] = [{}]
            first.remember(report, processed=False)
            second.set_output('results')
            restored = self.state(root / 'state.json')
            self.assertEqual(restored.history[0]['images'], 1)

    def test_simultaneous_writes_keep_all_completed_results(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            states = [self.state(root / 'state.json') for _ in range(8)]
            reports = [self.report(root, f'File-{number}') for number in range(len(states))]
            ready = Barrier(len(states))
            def remember(pair):
                state, report = pair
                ready.wait(timeout=5)
                state.remember(report)
                return state.warning
            with ThreadPoolExecutor(max_workers=len(states)) as pool:
                self.assertEqual(list(pool.map(remember, zip(states, reports))), [''] * len(states))
            self.assertEqual(len(self.state(root / 'state.json').history), len(states))


class WorkspaceApplication(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        import photo_app
        # Previous destroyed Tk fixtures must release their Python cycles on the
        # interpreter's owning thread, before PDF allocations can trigger worker GC.
        gc.collect()
        self.assertTrue(hasattr(photo_app, 'PhotoApplication'), 'Нет контроллера нового рабочего пространства')
        self.directory = tempfile.TemporaryDirectory(prefix='pdf-workspace-test-')
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.app = None
        self.root = tk.Tk()
        self.addCleanup(self.close)
        self.root.withdraw()
        self.app = photo_app.PhotoApplication(self.root, state_path=self.folder / 'workspace.json')

    def close(self):
        import tkinter as tk
        app, root = self.app, self.root
        try:
            if app is not None and app.busy:
                app.cancel()
                # A failed test still owes the extractor its rollback. The done
                # message arrives after PDF handles close; pump Tk until it does.
                while app.busy:
                    root.update()
                    time.sleep(.01)
            if root is not None:
                try:
                    root.destroy()
                except tk.TclError:
                    pass  # A close event may already have destroyed the test root.
        finally:
            self.app = self.root = None
            del app, root
            gc.collect()

    def source(self, name='Объект'):
        from test_photos import make_pdf
        path = make_pdf(self.folder, [{'images': [{'box': (50, 170, 800, 500)}],
                                       'text': [('Вариант 1', 50, 120, 18)]}])
        target = path.with_name(name + '.pdf')
        path.rename(target)
        return target

    def finish(self):
        deadline = time.monotonic() + 15
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.02)
        self.assertFalse(self.app.busy, 'Обработка не завершилась')

    def test_navigation_does_not_interrupt_processing_or_enable_settings(self):
        self.app.start([str(self.source())])
        self.app.view.show_page('history')
        self.assertTrue(self.app.busy)
        self.assertEqual(str(self.app.view.mode_combo.cget('state')), 'disabled')
        self.finish()
        self.assertEqual(len(self.app.workspace.history), 1)
        self.assertEqual(self.app.workspace.history[0]['images'], 1)
        self.assertEqual(self.app.view.progress.cget('value'), 100)

    def test_custom_output_is_used_by_actual_processing(self):
        target = self.folder / 'Готовые материалы'
        target.mkdir()
        source = self.source()
        original = source.read_bytes()
        self.app.workspace.set_output('custom', target)
        self.app.start([str(source)])
        self.finish()
        self.assertEqual(Path(self.app.output).parent, target)
        self.assertTrue((Path(self.app.output) / 'Вариант 1' / 'Вариант 1.pdf').is_file())
        self.assertEqual(source.read_bytes(), original)

    @unittest.skipUnless(__import__('sys').platform == 'win32', 'Горячая клавиша Windows')
    def test_control_o_opens_the_file_picker_with_the_current_keyboard_layout(self):
        self.root.deiconify()
        self.root.update()
        self.root.focus_force()
        self.root.update()
        with patch('photo_application.filedialog.askopenfilenames', return_value=()) as picker:
            self.root.event_generate('<Control-KeyPress>', keycode=79)
            self.root.update()
            picker.assert_called_once()

    def test_selecting_a_previous_result_opens_its_own_preview(self):
        first, second = self.source('Объект А'), self.source('Объект Б')
        self.app.start([str(first), str(second)])
        self.finish()
        item = self.app.workspace.history[-1]
        with patch.object(self.app.view, 'show_result') as detail:
            self.app.show_result(item)
            self.assertEqual(detail.call_args.args[0]['output'], item['output'])
        with patch('webbrowser.open', return_value=True):
            self.app.open_preview(item['output'])
        self.assertIn(Path(item['output']), self.app.server.folders.values())
        self.assertNotEqual(self.app.output, item['output'])

    def test_failed_new_batch_does_not_show_previous_batch_as_its_result(self):
        self.app.start([str(self.source())])
        self.finish()
        self.assertTrue(self.app.output)
        invalid = self.folder / 'Повреждённый.pdf'
        invalid.write_bytes(b'not-a-pdf')
        self.app.start([str(invalid)])
        self.finish()
        self.assertIsNone(self.app.output)
        self.assertFalse(self.app.view.has_result)
        self.assertTrue(self.app.status.get().startswith('Ошибка'))
        self.assertEqual(len(self.app.workspace.history), 1)


if __name__ == '__main__':
    unittest.main()
