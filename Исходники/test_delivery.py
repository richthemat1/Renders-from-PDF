"""Clean variant PDFs in folders the user chose: bare files that follow saves, renames and removals."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import photo_delivery
import photo_edit
from photo_delivery import DeliveryError, PATH_LIMIT, deliver, file_name, load, sync, targets
from photo_export import SERVICE_DIR
from photo_jobs import JobService
from photo_state import WorkspaceState
from test_photos import EditFixture


class VariantDelivery(EditFixture, unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory(prefix='pdf-delivery-')
        self.addCleanup(self.root.cleanup)
        self.output, _ = self.export(self.root.name)
        self.folder = Path(self.root.name)/'Готовые'
        self.folder.mkdir()

    def report(self):
        return photo_edit.load_report(self.output)

    def names(self, folder=None):
        return sorted(path.name for path in (folder or self.folder).iterdir())

    def test_variant_goes_straight_into_the_folder_as_one_pdf(self):
        result = deliver(self.output, self.report(), self.folder, ['1'])
        self.assertEqual(self.names(), ['Вариант 1.pdf'])
        self.assertEqual(result['saved'], [str(self.folder/'Вариант 1.pdf')])
        self.assertEqual(result['errors'], [])
        self.assertEqual((self.folder/'Вариант 1.pdf').read_bytes(),
                         (self.output/'Вариант 1'/'Вариант 1.pdf').read_bytes())
        self.assertEqual(targets(self.output), {'1': [{'folder': str(self.folder), 'file': 'Вариант 1.pdf',
                                                       'auto': False}]})
        # The notes live with the other service files and are no part of the report or its revision.
        self.assertTrue((self.output/SERVICE_DIR/photo_delivery.STATE_FILE).is_file())
        self.assertEqual(self.report()['revision'], 0)

    def test_all_variants_and_nothing_else(self):
        result = deliver(self.output, self.report(), self.folder)
        self.assertEqual(self.names(), ['Вариант 1.pdf', 'Вариант 2.pdf'])
        self.assertEqual(len(result['saved']), 2)

    def test_copy_follows_a_save_and_a_removed_variant_takes_its_copy_away(self):
        deliver(self.output, self.report(), self.folder)
        saved, _ = photo_edit.apply_edits(self.output, {'revision': 0, 'variants': {'1': [0, 1, 2]}})
        result = sync(self.output, saved)
        self.assertEqual(result['errors'], [])
        self.assertEqual(self.names(), ['Вариант 1.pdf'])
        self.assertEqual(self.colors(self.folder/'Вариант 1.pdf'), ['red', 'green', 'blue'])
        self.assertEqual(list(targets(self.output)), ['1'])

    def test_rename_moves_the_copy_to_the_new_name(self):
        deliver(self.output, self.report(), self.folder, ['2'])
        renamed, _ = photo_edit.rename_item(self.output, {'revision': 0, 'kind': 'variant', 'target': '2',
                                                          'name': 'Северный двор'})
        sync(self.output, renamed)
        self.assertEqual(self.names(), ['Северный двор.pdf'])

    def test_foreign_file_of_the_same_name_is_kept(self):
        (self.folder/'Вариант 1.pdf').write_bytes(b'mine')
        result = deliver(self.output, self.report(), self.folder, ['1'])
        self.assertEqual(self.names(), ['Вариант 1 (2).pdf', 'Вариант 1.pdf'])
        self.assertEqual((self.folder/'Вариант 1.pdf').read_bytes(), b'mine')
        self.assertEqual(result['saved'], [str(self.folder/'Вариант 1 (2).pdf')])
        # The next save writes to the same copy and never makes «(3)».
        sync(self.output, self.report())
        deliver(self.output, self.report(), self.folder, ['1'])
        self.assertEqual(self.names(), ['Вариант 1 (2).pdf', 'Вариант 1.pdf'])

    def test_copy_changed_by_the_user_is_not_deleted_with_its_variant(self):
        deliver(self.output, self.report(), self.folder, ['2'])
        (self.folder/'Вариант 2.pdf').write_bytes(b'edited by hand')
        saved, _ = photo_edit.apply_edits(self.output, {'revision': 0, 'variants': {'1': [0, 1, 2]}})
        sync(self.output, saved)
        self.assertEqual((self.folder/'Вариант 2.pdf').read_bytes(), b'edited by hand')
        self.assertEqual(targets(self.output), {})

    def test_shared_folder_names_each_pdf_after_its_presentation(self):
        result = sync(self.output, self.report(), self.folder)
        stem = Path(self.report()['source']).stem
        self.assertEqual(self.names(), [f'{stem} — Вариант 1.pdf', f'{stem} — Вариант 2.pdf'])
        self.assertEqual(result['errors'], [])
        self.assertTrue(all(copy['auto'] for copies in targets(self.output).values() for copy in copies))
        # Its own place and the shared folder are followed together.
        own = Path(self.root.name)/'Заказчику'
        own.mkdir()
        deliver(self.output, self.report(), own, ['1'])
        saved, _ = photo_edit.apply_edits(self.output, {'revision': 0, 'variants': {'1': [0], '2': [2, 1]}})
        sync(self.output, saved, self.folder)
        self.assertEqual(self.colors(own/'Вариант 1.pdf'), ['red'])
        self.assertEqual(self.colors(self.folder/f'{stem} — Вариант 2.pdf'), ['blue', 'green'])
        # Switched off: the copies stay as the user's files and are no longer followed.
        sync(self.output, saved, None)
        self.assertEqual(len(self.names()), 2)
        self.assertEqual([copy['auto'] for copies in targets(self.output).values() for copy in copies], [False])

    def test_long_folder_shortens_the_name_within_the_explorer_limit(self):
        deep = Path(self.root.name)
        while len(str(deep)) < 200:
            deep = deep/'Очень длинная папка проекта'
        deep = Path(str(deep)[:215].rstrip(' \\'))
        os.makedirs('\\\\?\\'+str(deep), exist_ok=True)
        title = 'Благоустройство территории жилого комплекса с общественными пространствами и набережной'
        renamed, _ = photo_edit.rename_item(self.output, {'revision': 0, 'kind': 'variant', 'target': '1',
                                                          'name': title[:60]})
        result = deliver(self.output, renamed, deep, ['1'])
        self.assertEqual(result['errors'], [])
        saved = result['saved'][0]
        self.assertLessEqual(len(saved), PATH_LIMIT)
        self.assertTrue(Path(saved).is_file())
        self.assertTrue(Path(saved).name.endswith('.pdf'))
        self.assertIn('…', Path(saved).name)

    def test_folder_too_deep_for_any_name_is_refused_before_writing(self):
        deep = 'C:\\'+'\\'.join(['папка']*45)
        self.assertGreater(len(deep), PATH_LIMIT-20)
        with patch.object(Path, 'is_dir', return_value=True), self.assertRaisesRegex(DeliveryError, 'слишком длинный'):
            photo_delivery.checked_folder(deep, self.output)
        self.assertLessEqual(len('D:\\Варианты\\'+file_name(Path('D:\\Варианты'), 'Я'*300, 'П'*300)), PATH_LIMIT)

    def test_result_folder_itself_and_missing_folders_are_refused(self):
        for folder in (self.output, self.output/'Вариант 1', Path(self.root.name)/'нет такой'):
            with self.assertRaises(DeliveryError):
                deliver(self.output, self.report(), folder, ['1'])
        with self.assertRaisesRegex(DeliveryError, 'Сохранить'):
            deliver(self.output, self.report(), self.folder, ['9'])
        self.assertEqual(self.names(), [])

    def test_open_file_is_reported_and_the_copy_is_still_followed(self):
        deliver(self.output, self.report(), self.folder, ['1'])
        saved, _ = photo_edit.apply_edits(self.output, {'revision': 0, 'variants': {'1': [1, 0], '2': [2]}})
        def held(action, *args):
            # Only the copy in the user's folder is open elsewhere; the program's own notes are written.
            if Path(args[-1]).parent == self.folder:
                raise PermissionError(13, 'Отказано в доступе')
            return action(*args)
        with patch.object(photo_delivery, 'settled', side_effect=held):
            result = sync(self.output, saved)
        self.assertEqual(len(result['errors']), 1)
        self.assertIn('открыт в другой программе', result['errors'][0])
        self.assertEqual(self.names(), ['Вариант 1.pdf'])
        self.assertEqual(sync(self.output, saved)['errors'], [])
        self.assertEqual(self.colors(self.folder/'Вариант 1.pdf'), ['green', 'red'])

    def test_broken_notes_only_stop_following(self):
        deliver(self.output, self.report(), self.folder, ['1'])
        (self.output/SERVICE_DIR/photo_delivery.STATE_FILE).write_text('{', encoding='utf-8')
        self.assertEqual(load(self.output), [])
        self.assertEqual(sync(self.output, self.report()), {'saved': [], 'errors': []})


class DeliverySetting(EditFixture, unittest.TestCase):
    def test_folder_is_kept_between_runs_and_survives_other_settings(self):
        with tempfile.TemporaryDirectory(prefix='pdf-delivery-') as root:
            path, folder = Path(root)/'workspace.json', Path(root)/'Готовые'
            folder.mkdir()
            state = WorkspaceState(path)
            self.assertEqual(state.delivery_folder, '')
            state.set_delivery(folder)
            other = WorkspaceState(path)
            other.set_sidebar(300, False)
            other.set_output('results')
            self.assertEqual(WorkspaceState(path).delivery_folder, str(folder.resolve()))
            with self.assertRaises(ValueError):
                state.set_delivery(Path(root)/'нет')
            state.set_delivery(None)
            self.assertEqual(WorkspaceState(path).delivery_folder, '')

    def test_processing_puts_variants_into_the_shared_folder(self):
        with tempfile.TemporaryDirectory(prefix='pdf-delivery-') as root:
            output, _ = self.export(root)
            folder = Path(root)/'Готовые'
            folder.mkdir()
            jobs = JobService(state_path=Path(root)/'workspace.json')
            self.addCleanup(jobs.close)
            self.assertEqual(jobs.set_delivery(folder)['settings']['delivery'], str(folder.resolve()))
            report = photo_edit.load_report(output)
            jobs._saved(0, [Path(report['source'])], report, False, {})
            self.assertEqual(len(list(folder.glob('* — Вариант ?.pdf'))), 2)
            self.assertTrue(any('Готовые варианты — 2 PDF' in item['text'] for item in jobs.snapshot()['logs']))


class DeliveryBridge(EditFixture, unittest.TestCase):
    """The window's bridge with a fake folder dialog: no real dialog or Explorer window is opened."""
    def setUp(self):
        from unittest.mock import Mock
        from photo_desktop import NativeBridge
        from photo_desktop_server import DesktopServer
        self.temp = tempfile.TemporaryDirectory(prefix='pdf-delivery-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output, _ = self.export(self.root)
        self.folder = self.root/'Готовые'
        self.folder.mkdir()
        self.jobs = JobService(self.root/'state.json')
        self.server = DesktopServer(self.jobs, self.root).start()
        self.addCleanup(self.server.stop)
        self.addCleanup(self.jobs.close)
        self.id = self.server.register_result(self.output)['id']
        self.window = Mock()
        self.window.get_current_url.return_value = self.server.url
        self.window.create_file_dialog.return_value = [str(self.folder)]
        self.bridge = NativeBridge(self.jobs, self.server, None)
        self.bridge._window = self.window

    def test_export_asks_for_a_folder_and_returns_the_result_with_its_copies(self):
        value = self.bridge.export_variants(self.id, ['2'])
        self.assertEqual(value['saved'], [str(self.folder/'Вариант 2.pdf')])
        self.assertEqual(value['result']['delivery'], {'2': [{'folder': str(self.folder), 'file': 'Вариант 2.pdf',
                                                               'auto': False}]})
        self.assertEqual(len(self.bridge.export_variants(self.id, None)['saved']), 2)
        self.window.create_file_dialog.return_value = None
        self.assertIsNone(self.bridge.export_variants(self.id, ['1']))
        for wrong in ([], [1], 'all'):
            with self.assertRaises(ValueError):
                self.bridge.export_variants(self.id, wrong)
        with self.assertRaises(ValueError):
            self.bridge.export_variants('unknown', None)
        self.window.get_current_url.return_value = 'https://example.com/'
        with self.assertRaises(ValueError):
            self.bridge.export_variants(self.id, None)

    def test_reveal_selects_the_copy_in_explorer_and_tells_when_it_is_gone(self):
        self.bridge.export_variants(self.id, ['1'])
        with patch('subprocess.Popen') as popen:
            self.assertTrue(self.bridge.reveal_variant(self.id, '1'))
        self.assertEqual(popen.call_args.args[0], ['explorer.exe', '/select,', str(self.folder/'Вариант 1.pdf')])
        (self.folder/'Вариант 1.pdf').unlink()
        with patch('subprocess.Popen') as popen, self.assertRaisesRegex(ValueError, 'не найдена'):
            self.bridge.reveal_variant(self.id, '1')
        popen.assert_not_called()

    def test_shared_folder_is_chosen_cleared_and_used_by_a_save(self):
        self.assertEqual(self.bridge.choose_delivery()['settings']['delivery'], str(self.folder.resolve()))
        photo_edit.apply_edits(self.output, {'revision': 0, 'variants': {'1': [0, 1, 2]}})
        delivered = self.jobs.deliver(photo_edit.load_report(self.output))
        self.assertEqual(delivered['errors'], [])
        self.assertEqual(len(list(self.folder.glob('* — Вариант 1.pdf'))), 1)
        self.assertEqual(self.bridge.clear_delivery()['settings']['delivery'], '')


if __name__ == '__main__':
    unittest.main()
