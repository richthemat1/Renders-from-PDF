"""Real PDF regressions for recovery and the editor's reverse operations."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
import shutil
from unittest.mock import patch

import photo_edit
from pypdf import PdfReader
from test_photos import EditFixture


class RecoverableEdits(EditFixture, unittest.TestCase):
    def active_files(self, output):
        root = str(photo_edit.backup_root(photo_edit.load_report(output)))
        return {key: value for key, value in self.files(output).items() if not key.startswith(root)}

    def test_removed_variant_page_restores_from_backup_after_another_save(self):
        with tempfile.TemporaryDirectory(prefix='pdf-recover-') as root:
            output, _ = self.export(root)
            removed, first_backup = photo_edit.apply_edits(output, {
                'revision': 0, 'variants': {'1': [1], '2': [2]}, 'removed': [0],
            })
            original_recovery = removed['images'][0]['file']
            photo_edit.apply_edits(output, {
                'revision': 1, 'variants': {'1': [1, 3], '2': [2]},
            })
            saved, _ = photo_edit.apply_edits(output, {
                'revision': 2, 'variants': {'1': [0, 1, 3], '2': [2]}, 'restored': [0],
            })
            self.assertFalse(saved['images'][0].get('removed', False))
            self.assertEqual(self.colors(output / saved['images'][0]['file']), ['red', 'green', 'yellow'])
            self.assertEqual(saved['images'][0]['pdf_page'], 1)
            self.assertTrue((output / original_recovery).is_file())
            self.assertTrue((output / first_backup / 'Отчёт.json').is_file())
            self.assertEqual(saved['revision'], 3)

    def test_return_to_misc_extracts_only_the_selected_pdf_page(self):
        with tempfile.TemporaryDirectory(prefix='pdf-return-') as root:
            output, _ = self.export(root)
            saved, _ = photo_edit.apply_edits(output, {
                'revision': 0, 'variants': {'1': [1], '2': [2]}, 'misc': [0],
            })
            entry = saved['images'][0]
            self.assertIsNone(entry['variant'])
            self.assertIsNone(entry['group'])
            self.assertEqual(entry['pdf_page'], 1)
            self.assertTrue(Path(entry['file']).is_relative_to('Все остальное'))
            self.assertEqual(self.colors(output / entry['file']), ['red'])
            with PdfReader(output / entry['file']) as reader:
                self.assertEqual(reader.metadata.title, 'Вариант 1')
            self.assertEqual(self.colors(output / 'Вариант 1/Вариант 1.pdf'), ['green'])

    def test_return_restores_original_caption_status_and_reason_after_saved_move(self):
        with tempfile.TemporaryDirectory(prefix='pdf-return-') as root:
            output, _ = self.export(root)
            before = copy.deepcopy(photo_edit.load_report(output)['images'][3])
            moved, _ = photo_edit.apply_edits(output, {
                'revision': 0, 'variants': {'1': [0, 1, 3], '2': [2]},
            })
            self.assertEqual(moved['images'][3]['status'], 'named')
            saved, _ = photo_edit.apply_edits(output, {
                'revision': 1, 'variants': {'1': [0, 1], '2': [2]}, 'misc': [3],
            })
            entry = saved['images'][3]
            self.assertEqual((entry['folder'], entry['status'], entry['reason']),
                             (before['folder'], before['status'], before['reason']))
            self.assertEqual(self.colors(output / entry['file']), ['yellow'])

    def test_saved_misc_removal_can_be_restored_without_overwriting_a_user_file(self):
        with tempfile.TemporaryDirectory(prefix='pdf-restore-') as root:
            output, _ = self.export(root)
            original = photo_edit.load_report(output)['images'][3]['file']
            photo_edit.apply_edits(output, {
                'revision': 0, 'variants': {'1': [0, 1], '2': [2]}, 'removed': [3],
            })
            occupied = output / original
            occupied.parent.mkdir(parents=True, exist_ok=True)
            occupied.write_bytes(b'personal file, do not overwrite')
            saved, _ = photo_edit.apply_edits(output, {
                'revision': 1, 'variants': {'1': [0, 1], '2': [2]}, 'restored': [3], 'misc': [3],
            })
            self.assertEqual(occupied.read_bytes(), b'personal file, do not overwrite')
            self.assertNotEqual(saved['images'][3]['file'], original)
            self.assertEqual(self.colors(output / saved['images'][3]['file']), ['yellow'])

    def test_failed_return_and_recovery_preserve_all_previous_files(self):
        with tempfile.TemporaryDirectory(prefix='pdf-rollback-') as root:
            output, _ = self.export(root)
            photo_edit.apply_edits(output, {
                'revision': 0, 'variants': {'1': [1], '2': [2]}, 'removed': [0],
            })
            before = self.active_files(output)
            with patch.object(photo_edit, 'write_report', side_effect=OSError('fixture failed write')):
                with self.assertRaisesRegex(OSError, 'fixture failed write'):
                    photo_edit.apply_edits(output, {
                        'revision': 1, 'variants': {'1': [1], '2': [2]},
                        'restored': [0], 'misc': [0],
                    })
            self.assertEqual(self.active_files(output), before)

    def test_recovery_rejects_missing_or_outside_pdf_before_writing_any_files(self):
        with tempfile.TemporaryDirectory(prefix='pdf-safe-source-') as root:
            output, _ = self.export(root)
            report = photo_edit.load_report(output)
            report['images'][0]['file'] = '../outside.pdf'
            photo_edit.write_report(output, report)
            before = self.files(output)
            with self.assertRaises(photo_edit.EditError):
                photo_edit.apply_edits(output, {
                    'revision': 0, 'variants': {'1': [1], '2': [2]}, 'misc': [0],
                })
            self.assertEqual(self.files(output), before)

    def test_last_save_can_be_undone_without_reusing_an_old_revision(self):
        self.assertTrue(callable(getattr(photo_edit, 'restore_last_save', None)))
        with tempfile.TemporaryDirectory(prefix='pdf-undo-save-') as root:
            output, _ = self.export(root)
            before = self.active_files(output)
            photo_edit.apply_edits(output, {
                'revision': 0, 'variants': {'1': [1], '2': [2, 3]}, 'removed': [0],
            })
            restored, backup = photo_edit.restore_last_save(output, {'revision': 1})
            self.assertEqual(restored['revision'], 2)
            self.assertEqual(self.colors(output / 'Вариант 1/Вариант 1.pdf'), ['red', 'green'])
            self.assertEqual(self.colors(output / 'Вариант 2/Вариант 2.pdf'), ['blue'])
            self.assertEqual(self.colors(output / restored['images'][3]['file']), ['yellow'])
            self.assertFalse(restored['images'][0].get('removed', False))
            self.assertTrue((output / backup / 'Отчёт.json').is_file())
            for name, data in before.items():
                if name not in ('Отчёт.json', 'Просмотр.html'):
                    self.assertEqual((output / name).read_bytes(), data)
            with self.assertRaises(photo_edit.EditConflict):
                photo_edit.restore_last_save(output, {'revision': 1})

    def test_undo_does_not_overwrite_files_changed_after_save(self):
        with tempfile.TemporaryDirectory(prefix='pdf-safe-undo-') as root:
            output, _ = self.export(root)
            photo_edit.apply_edits(output, {
                'revision': 0, 'variants': {'1': [1], '2': [2, 3]}, 'removed': [0],
            })
            changed = output / 'Вариант 1/Вариант 1.pdf'
            changed.write_bytes(b'user changed this PDF')
            before = self.files(output)
            with self.assertRaises(photo_edit.EditError):
                photo_edit.restore_last_save(output, {'revision': 1})
            self.assertEqual(self.files(output), before)

    def test_undo_rejects_manual_report_changes_even_without_a_revision_increment(self):
        with tempfile.TemporaryDirectory(prefix='pdf-report-conflict-') as root:
            output, _ = self.export(root)
            saved, _ = photo_edit.apply_edits(output, {
                'revision': 0, 'variants': {'1': [1], '2': [2]}, 'removed': [0],
            })
            saved['images'][1]['title'] = 'External caption change'
            photo_edit.write_report(output, saved)
            before = self.files(output)
            with self.assertRaises(photo_edit.EditConflict):
                photo_edit.restore_last_save(output, {'revision': 1})
            self.assertEqual(self.files(output), before)

    def test_a_report_changed_during_backup_is_not_overwritten_by_save_or_rollback(self):
        with tempfile.TemporaryDirectory(prefix='pdf-commit-conflict-') as root:
            output, _ = self.export(root)
            current_report = output / 'Отчёт.json'
            before = self.active_files(output)
            real_copy = shutil.copy2
            concurrent = photo_edit.load_report(output)
            concurrent['revision'] = 99

            def change_during_backup(source, target, *args, **kwargs):
                result = real_copy(source, target, *args, **kwargs)
                if Path(source).name == 'Отчёт.json':
                    photo_edit.write_report(output, concurrent)
                return result

            with patch.object(photo_edit.shutil, 'copy2', side_effect=change_during_backup):
                with self.assertRaises(photo_edit.EditConflict):
                    photo_edit.apply_edits(output, {
                        'revision': 0, 'variants': {'1': [1], '2': [2]}, 'removed': [0],
                    })
            self.assertEqual(photo_edit.load_report(output)['revision'], 99)
            after = self.active_files(output)
            for name, data in before.items():
                if name != 'Отчёт.json':
                    self.assertEqual(after[name], data)

    def test_failed_undo_restores_current_pdfs_and_report(self):
        with tempfile.TemporaryDirectory(prefix='pdf-rollback-undo-') as root:
            output, _ = self.export(root)
            photo_edit.apply_edits(output, {
                'revision': 0, 'variants': {'1': [1], '2': [2]}, 'misc': [0],
            })
            before = self.active_files(output)
            with patch.object(photo_edit, 'write_report', side_effect=OSError('fixture undo failed')):
                with self.assertRaisesRegex(OSError, 'fixture undo failed'):
                    photo_edit.restore_last_save(output, {'revision': 1})
            self.assertEqual(self.active_files(output), before)

    def test_a_shared_misc_source_is_backed_up_even_when_another_page_still_uses_it(self):
        with tempfile.TemporaryDirectory(prefix='pdf-shared-source-') as root:
            output, _ = self.export(root)
            report = photo_edit.load_report(output)
            # Two per-page entries can share a PDF in imported legacy reports.
            for index in (0, 1):
                report['images'][index].update(variant=None, group=None, folder='Общая подпись')
            photo_edit.summarize(report)
            photo_edit.write_report(output, report)
            saved, _ = photo_edit.apply_edits(output, {
                'revision': 0, 'variants': {'2': [2]}, 'removed': [0],
            })
            self.assertTrue((output / saved['images'][0]['file']).is_file())
            restored, _ = photo_edit.apply_edits(output, {
                'revision': 1, 'variants': {'2': [2]}, 'restored': [0], 'misc': [0],
            })
            self.assertEqual(self.colors(output / restored['images'][0]['file']), ['red'])

    def test_recovery_rejects_an_out_of_range_page_before_creating_a_backup(self):
        with tempfile.TemporaryDirectory(prefix='pdf-missing-page-') as root:
            output, _ = self.export(root)
            report = photo_edit.load_report(output)
            report['images'][0]['pdf_page'] = 999
            photo_edit.write_report(output, report)
            before = self.files(output)
            with self.assertRaises(photo_edit.EditError):
                photo_edit.apply_edits(output, {'revision': 0, 'variants': {'1': [1], '2': [2]}, 'misc': [0]})
            self.assertEqual(self.files(output), before)

    def test_imported_forward_slash_paths_remain_editable_and_undoable(self):
        with tempfile.TemporaryDirectory(prefix='pdf-path-alias-') as root:
            output, _ = self.export(root)
            report = photo_edit.load_report(output)
            for entry in report['images']:
                entry['file'] = entry['file'].replace('\\', '/')
                if os.name == 'nt':
                    entry['file'] = entry['file'].lower()
            photo_edit.summarize(report)
            photo_edit.write_report(output, report)
            saved, _ = photo_edit.apply_edits(output, {
                'revision': 0, 'variants': {'1': [0, 1, 3], '2': [2]},
            })
            self.assertEqual(self.colors(output / saved['images'][0]['file']), ['red', 'green', 'yellow'])
            restored, _ = photo_edit.restore_last_save(output, {'revision': 1})
            self.assertEqual(self.colors(output / restored['images'][0]['file']), ['red', 'green'])

    def test_caption_rename_is_preserved_by_a_later_saved_move_and_return(self):
        with tempfile.TemporaryDirectory(prefix='pdf-caption-rename-') as root:
            output, _ = self.export(root)
            report = photo_edit.load_report(output)
            entry = report['images'][3]
            old = Path(entry['file'])
            new = old.parent.with_name('Двор') / old.name
            (output / old.parent).rename(output / new.parent)
            entry.update(folder='Двор', status='named', file=str(new))
            photo_edit.summarize(report)
            photo_edit.write_report(output, report)
            photo_edit.apply_edits(output, {'revision': 0, 'variants': {'1': [0, 1, 3], '2': [2]}})
            photo_edit.apply_edits(output, {'revision': 1, 'variants': {'1': [0, 1], '2': [2]}, 'misc': [3]})
            photo_edit.rename_item(output, {'revision': 2, 'kind': 'folder', 'target': 'Двор', 'name': 'Новый двор'})
            photo_edit.apply_edits(output, {'revision': 3, 'variants': {'1': [0, 1, 3], '2': [2]}})
            saved, _ = photo_edit.apply_edits(output, {
                'revision': 4, 'variants': {'1': [0, 1], '2': [2]}, 'misc': [3],
            })
            self.assertEqual(saved['images'][3]['folder'], 'Новый двор')
            self.assertEqual(Path(saved['images'][3]['file']).parent, Path('Все остальное/Новый двор'))

    def test_backup_directory_symlink_cannot_write_outside_the_registered_result(self):
        with tempfile.TemporaryDirectory(prefix='pdf-backup-link-') as root:
            output, _ = self.export(root)
            outside = Path(root) / 'outside'
            outside.mkdir()
            link = output / photo_edit.backup_root(photo_edit.load_report(output))
            try:
                os.symlink(outside, link, target_is_directory=True)
            except OSError:
                self.skipTest('Creating directory symlinks requires Windows developer mode or elevated privileges')
            before = self.active_files(output)
            with self.assertRaises(photo_edit.EditError):
                photo_edit.apply_edits(output, {'revision': 0, 'variants': {'1': [1], '2': [2]}, 'removed': [0]})
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(self.active_files(output), before)

    def test_removing_a_pdf_symlink_never_deletes_its_target(self):
        with tempfile.TemporaryDirectory(prefix='pdf-source-link-') as root:
            output, _ = self.export(root)
            report = photo_edit.load_report(output)
            kept = output / 'user-owned.pdf'
            shutil.copy2(output / report['images'][0]['file'], kept)
            alias = output / 'alias.pdf'
            try:
                os.symlink(kept, alias)
            except OSError:
                self.skipTest('Creating symlinks requires Windows developer mode or elevated privileges')
            report['images'][0].update(file='alias.pdf', variant=None, group=None)
            photo_edit.summarize(report)
            photo_edit.write_report(output, report)
            before = self.files(output)
            with self.assertRaises(photo_edit.EditError):
                photo_edit.apply_edits(output, {'revision': 0, 'variants': {'1': [1], '2': [2]}, 'removed': [0]})
            self.assertTrue(kept.is_file())
            self.assertEqual(self.files(output), before)

    def test_restoring_the_last_removed_pages_keeps_the_renamed_variant(self):
        with tempfile.TemporaryDirectory(prefix='pdf-restore-name-') as root:
            output, _ = self.export(root)
            photo_edit.rename_item(output, {'revision': 0, 'kind': 'variant', 'target': 1, 'name': 'Северный фасад'})
            photo_edit.apply_edits(output, {'revision': 1, 'variants': {'2': [2]}, 'removed': [0, 1]})
            saved, _ = photo_edit.apply_edits(output, {
                'revision': 2, 'variants': {'1': [0, 1], '2': [2]}, 'restored': [0, 1],
            })
            self.assertEqual(saved['images'][0]['folder'], 'Северный фасад')
            self.assertEqual(self.colors(output / 'Северный фасад/Северный фасад.pdf'), ['red', 'green'])

    def test_fractional_indices_are_rejected_without_changing_files(self):
        with tempfile.TemporaryDirectory(prefix='pdf-malformed-edit-') as root:
            output, _ = self.export(root)
            before = self.files(output)
            with self.assertRaises(photo_edit.EditError):
                photo_edit.apply_edits(output, {'revision': 0, 'variants': {'1': [1.1], '2': [2]}, 'removed': [0]})
            self.assertEqual(self.files(output), before)


if __name__ == '__main__':
    unittest.main()
