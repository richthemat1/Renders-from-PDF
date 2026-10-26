"""Backup and rollback paths on Windows without the optional long-path policy."""
import ctypes
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import photo_edit
from photo_export import extract
from test_photos import EditFixture, make_pdf


@unittest.skipUnless(os.name == 'nt', 'Windows file path handling')
class LongBackupPaths(EditFixture, unittest.TestCase):
    def export_long_caption(self, root):
        source = make_pdf(root, [{
            'images': [{'box': (50, 170, 800, 500), 'color': '#0000ff'}],
            'text': [('Декоративные панели для кровли ' * 6, 50, 120, 10)],
        }])
        report = extract(source)
        output = Path(report['output'])
        relative = Path(report['images'][0]['file'])
        self.assertIsNone(report['images'][0]['variant'])
        self.assertGreater(len(str(output / relative)), 220)
        self.assertLess(len(str(output / relative)), 260)
        return output, relative

    def legacy_copy(self):
        real_copy = shutil.copy2

        def copy(source, target, *args, **kwargs):
            # CopyFileW without longPathAware rejects these paths with WinError 3.
            # Keep the real filesystem and emulate only that optional OS policy.
            for path in (source, target):
                value = os.fspath(path)
                if len(value) >= 260 and not value.startswith('\\\\?\\'):
                    raise ctypes.WinError(3)
            return real_copy(source, target, *args, **kwargs)

        return copy

    def test_move_keeps_a_long_caption_pdf_in_its_backup(self):
        with tempfile.TemporaryDirectory(prefix='pdf-long-backup-') as root:
            output, relative = self.export_long_caption(root)
            before = self.files(output)
            with patch.object(photo_edit.shutil, 'copy2', side_effect=self.legacy_copy()):
                try:
                    saved, backup = photo_edit.apply_edits(output, {
                        'revision': 0, 'variants': {'1': [0]},
                    })
                except OSError as error:
                    self.fail(f'Saving a long-caption image must work: {error}')
            self.assertGreater(len(str(output / backup / relative)), 260)
            self.assertEqual((output / backup / relative).read_bytes(), before[str(relative)])
            self.assertEqual((output / backup / 'Отчёт.json').read_bytes(), before['Отчёт.json'])
            self.assertEqual(saved['revision'], 1)
            self.assertEqual(saved['images'][0]['variant'], 1)
            self.assertEqual(saved['images'][0]['file'], str(Path('Вариант 1/Вариант 1.pdf')))
            self.assertEqual(saved['output'], str(output))
            self.assertEqual(photo_edit.load_report(output), saved)
            self.assertEqual(self.colors(output / 'Вариант 1/Вариант 1.pdf'), ['blue'])

    def test_removed_long_caption_pdf_has_a_relative_recovery_path(self):
        with tempfile.TemporaryDirectory(prefix='pdf-long-backup-') as root:
            output, relative = self.export_long_caption(root)
            original = (output / relative).read_bytes()
            with patch.object(photo_edit.shutil, 'copy2', side_effect=self.legacy_copy()):
                saved, backup = photo_edit.apply_edits(output, {
                    'revision': 0, 'variants': {}, 'removed': [0],
                })
            removed = saved['images'][0]
            self.assertTrue(removed['removed'])
            self.assertEqual(removed['file'], str(Path(backup) / relative))
            self.assertFalse(Path(removed['file']).is_absolute())
            self.assertEqual((output / removed['file']).read_bytes(), original)
            self.assertFalse((output / relative).exists())

    def test_failed_save_restores_long_caption_files_from_the_backup(self):
        with tempfile.TemporaryDirectory(prefix='pdf-long-backup-') as root:
            output, _ = self.export_long_caption(root)
            before = self.files(output)
            with patch.object(photo_edit.shutil, 'copy2', side_effect=self.legacy_copy()), \
                    patch.object(photo_edit, 'write_report', side_effect=OSError('fixture write failure')):
                with self.assertRaisesRegex(OSError, 'fixture write failure'):
                    photo_edit.apply_edits(output, {'revision': 0, 'variants': {'1': [0]}})
            backups = str(photo_edit.backup_root(photo_edit.load_report(output)))
            after = {key: value for key, value in self.files(output).items() if not key.startswith(backups)}
            self.assertEqual(after, before)

    def test_backup_io_paths_support_local_unc_and_existing_extended_paths(self):
        cases = [
            ('C:\\results\\backup.pdf', '\\\\?\\C:\\results\\backup.pdf'),
            ('\\\\server\\share\\backup.pdf', '\\\\?\\UNC\\server\\share\\backup.pdf'),
            ('\\\\?\\C:\\results\\backup.pdf', '\\\\?\\C:\\results\\backup.pdf'),
            ('\\\\?\\UNC\\server\\share\\backup.pdf', '\\\\?\\UNC\\server\\share\\backup.pdf'),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(str(photo_edit._backup_io_path(Path(source))), expected)


if __name__ == '__main__':
    unittest.main()
