"""Independent editable PDF copies use the real exporter and transactional editor."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader

import photo_edit
from photo_export import extract
from test_photos import EditFixture, make_pdf


class PdfCopies(EditFixture, unittest.TestCase):
    def payload(self, **changes):
        value = {'revision': 0, 'variants': {'1': [0, 1], '2': [2]}}
        value.update(changes)
        return value

    def live_files(self, output):
        root = str(photo_edit.backup_root(photo_edit.load_report(output)))
        return {name: data for name, data in self.files(output).items() if not name.startswith(root)}

    def page_images(self, path):
        with PdfReader(path) as reader:
            return [page.images[0].data for page in reader.pages]

    def caption_export(self, root, output=None):
        title = 'Материалы благоустройства территории жилого комплекса и общественных пространств'
        source = make_pdf(root, [{
            'images': [{'box': (50, 170, 800, 500), 'color': '#00ff00'}],
            'text': [(title, 50, 120, 14)],
        }])
        with contextlib.redirect_stdout(io.StringIO()):
            report = extract(source, output=output)
        return Path(report['output']), report, title

    def test_same_variant_copy_has_its_own_entry_and_survives_original_removal(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            before = photo_edit.load_report(output)
            original_pdf = (output / before['images'][0]['file']).read_bytes()
            saved, backup = photo_edit.apply_edits(output, self.payload(
                copies=[0], variants={'1': [0, 1, 4], '2': [2]}))
            self.assertEqual(len(saved['images']), 5)
            self.assertEqual(self.colors(output / saved['images'][4]['file']), ['red', 'green', 'red'])
            clone = saved['images'][4]
            for field in ('page', 'sequence', 'preview', 'title', 'size', 'image_sha256',
                          'method', 'pdf_page_size_pt', 'bbox', 'components'):
                self.assertEqual(clone[field], before['images'][0][field], field)
            self.assertEqual((clone['copy_of'], clone['pdf_page'], clone['variant'], clone['manual']), (0, 3, 1, True))
            self.assertIn('копи', clone['reason'].lower())
            self.assertEqual(saved['edits'][-1]['copies'], [{'image': 4, 'source': 0}])
            self.assertEqual((output / backup / before['images'][0]['file']).read_bytes(), original_pdf)
            removed, _ = photo_edit.apply_edits(output, {
                'revision': 1, 'variants': {'1': [1, 4], '2': [2]}, 'removed': [0],
            })
            self.assertTrue(removed['images'][0]['removed'])
            self.assertFalse(removed['images'][4].get('removed', False))
            self.assertEqual(self.colors(output / removed['images'][4]['file']), ['green', 'red'])
            removed_clone, _ = photo_edit.apply_edits(output, {
                'revision': 2, 'variants': {'1': [1], '2': [2]}, 'removed': [4],
            })
            self.assertTrue(removed_clone['images'][4]['removed'])
            self.assertTrue(removed_clone['images'][0]['removed'])
            self.assertEqual(self.colors(output / removed_clone['images'][1]['file']), ['green'])

    def test_complete_variant_copy_keeps_page_order_and_source_pdf_bytes(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            before = photo_edit.load_report(output)
            source = output / before['images'][0]['file']
            source_bytes = source.read_bytes()
            source_images = self.page_images(source)
            saved, _ = photo_edit.apply_edits(output, self.payload(
                copies=[0, 1], variants={'1': [0, 1], '2': [2], '3': [4, 5]}))
            self.assertEqual(source.read_bytes(), source_bytes)
            target = output / saved['images'][4]['file']
            self.assertNotEqual(target, source)
            self.assertEqual(self.colors(target), ['red', 'green'])
            self.assertEqual(self.page_images(target), source_images)
            self.assertEqual([(e['variant'], e['pdf_page'], e['copy_of']) for e in saved['images'][4:]],
                             [(3, 1, 0), (3, 2, 1)])
            self.assertEqual(photo_edit.variant_members(saved), {1: [0, 1], 2: [2], 3: [4, 5]})

    def test_misc_copies_are_unique_single_page_pdfs_and_keep_full_caption_labels(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, before, title = self.caption_export(root)
            source = output / before['images'][0]['file']
            original = source.read_bytes()
            saved, _ = photo_edit.apply_edits(output, {
                'revision': 0, 'variants': {}, 'copies': [0, 0], 'misc': [1, 2],
            })
            files = [entry['file'] for entry in saved['images']]
            self.assertEqual(len(set(files)), 3)
            self.assertEqual(source.read_bytes(), original)
            for index in (1, 2):
                entry = saved['images'][index]
                self.assertEqual((entry['variant'], entry['group'], entry['pdf_page'], entry['copy_of']),
                                 (None, None, 1, 0))
                self.assertEqual(entry['title'], title)
                self.assertEqual(saved['folder_labels'][entry['folder']], title)
                self.assertEqual(self.colors(output / entry['file']), ['green'])
                self.assertRegex(Path(entry['file']).stem, r'\(\d+\)')
            renamed, _ = photo_edit.rename_item(output, {
                'revision': 1, 'kind': 'file', 'target': 1, 'name': 'Материалы — копия',
            })
            self.assertNotEqual(renamed['images'][1]['file'], saved['images'][1]['file'])
            moved, _ = photo_edit.apply_edits(output, {
                'revision': 2, 'variants': {'7': [2]},
            })
            self.assertEqual(moved['images'][2]['variant'], 7)
            self.assertEqual(moved['images'][0]['file'], before['images'][0]['file'])
            self.assertEqual(self.colors(output / moved['images'][1]['file']), ['green'])
            returned, _ = photo_edit.apply_edits(output, {
                'revision': 3, 'variants': {}, 'misc': [2],
            })
            self.assertEqual(returned['folder_labels'][returned['images'][2]['folder']], title)
            self.assertEqual(self.colors(output / returned['images'][2]['file']), ['green'])

    def test_mixed_dirty_moves_removal_and_copies_use_saved_source_pages(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            saved, _ = photo_edit.apply_edits(output, self.payload(
                copies=[0, 3], variants={'1': [0], '2': [3, 2], '3': [4]}, removed=[1], misc=[5]))
            self.assertEqual(self.colors(output / saved['images'][0]['file']), ['red'])
            self.assertEqual(self.colors(output / saved['images'][3]['file']), ['yellow', 'blue'])
            self.assertEqual(self.colors(output / saved['images'][4]['file']), ['red'])
            self.assertEqual(self.colors(output / saved['images'][5]['file']), ['yellow'])
            self.assertEqual(saved['images'][5]['folder'], 'Без подписи')
            self.assertTrue(saved['images'][1]['removed'])
            self.assertEqual(saved['revision'], 1)
            self.assertEqual(photo_edit.load_report(output), saved)

    def test_live_source_can_be_removed_in_the_same_save_as_its_copy(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            saved, _ = photo_edit.apply_edits(output, self.payload(
                copies=[0], variants={'1': [1, 4], '2': [2]}, removed=[0]))
            self.assertEqual(self.colors(output / saved['images'][4]['file']), ['green', 'red'])
            self.assertTrue(saved['images'][0]['removed'])
            self.assertFalse(saved['images'][4].get('removed', False))
            self.assertTrue(photo_edit.can_undo_last_save(saved))

    def test_copy_can_be_made_from_a_previously_saved_copy(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            photo_edit.apply_edits(output, self.payload(copies=[0], misc=[4]))
            saved, _ = photo_edit.apply_edits(output, {
                'revision': 1, 'variants': {'1': [0, 1], '2': [2], '3': [5]}, 'copies': [4],
            })
            self.assertEqual(saved['images'][5]['copy_of'], 4)
            self.assertEqual(self.colors(output / saved['images'][5]['file']), ['red'])
            self.assertEqual(self.colors(output / saved['images'][4]['file']), ['red'])

    def test_bad_copy_sources_and_missing_or_repeated_destinations_change_nothing(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            before = self.files(output)
            invalid = [
                self.payload(copies=value) for value in (None, {}, '0', [True], [0.0], ['0'], [None], [-1], [4])
            ]
            invalid += [
                self.payload(copies=[0]),
                self.payload(copies=[0], misc=[4, 4]),
                self.payload(copies=[0], variants={'1': [0, 1, 4], '2': [2]}, misc=[4]),
                self.payload(copies=[0], removed=[4]),
                self.payload(copies=[0], misc=[4], restored=[4]),
                self.payload(copies=[0], misc=[5]),
            ]
            for payload in invalid:
                with self.subTest(payload=payload):
                    with self.assertRaises(photo_edit.EditError):
                        photo_edit.apply_edits(output, payload)
                    self.assertEqual(self.files(output), before)

    def test_restore_copy_then_remove_original_again_keeps_the_copy_and_recovery_pdf(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            removed, _ = photo_edit.apply_edits(output, self.payload(variants={'1': [1], '2': [2]}, removed=[0]))
            before = self.live_files(output)
            recovery = output / removed['images'][0]['file']
            recovery_bytes = recovery.read_bytes()
            # Restoring, copying, then removing the original again in a draft leaves
            # no `restored` ID in the final wire payload; only the new copy survives.
            saved, _ = photo_edit.apply_edits(output, {
                'revision': 1, 'variants': {'1': [1], '2': [2], '3': [4]}, 'copies': [0],
            })
            self.assertEqual(saved['images'][0], removed['images'][0])
            self.assertEqual(saved['images'][4]['copy_of'], 0)
            self.assertFalse(saved['images'][4].get('removed', False))
            self.assertEqual(self.colors(output / saved['images'][4]['file']), ['red'])
            self.assertEqual(recovery.read_bytes(), recovery_bytes)
            with self.assertRaises(photo_edit.EditConflict):
                photo_edit.apply_edits(output, self.payload(copies=[1], misc=[4]))
            undone, _ = photo_edit.restore_last_save(output, {'revision': 2})
            self.assertEqual(undone['images'], removed['images'])
            self.assertEqual(undone['revision'], 3)
            self.assertFalse((output / saved['images'][4]['file']).exists())
            self.assertEqual(recovery.read_bytes(), recovery_bytes)
            for relative, data in before.items():
                if relative.lower().endswith('.pdf'):
                    self.assertEqual((output / relative).read_bytes(), data)

    def test_restore_and_copy_of_a_saved_removed_source_save_together_and_undo_exactly(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            removed, _ = photo_edit.apply_edits(output, self.payload(variants={'1': [1], '2': [2]}, removed=[0]))
            before = self.live_files(output)
            recovery = output / removed['images'][0]['file']
            recovery_bytes = recovery.read_bytes()
            saved, _ = photo_edit.apply_edits(output, {
                'revision': 1, 'variants': {'1': [0, 1, 4], '2': [2]}, 'restored': [0], 'copies': [0],
            })
            self.assertEqual(self.colors(output / saved['images'][4]['file']), ['red', 'green', 'red'])
            self.assertFalse(saved['images'][0].get('removed', False))
            self.assertFalse(saved['images'][4].get('removed', False))
            self.assertEqual(saved['images'][4]['copy_of'], 0)
            self.assertEqual(recovery.read_bytes(), recovery_bytes)
            undone, _ = photo_edit.restore_last_save(output, {'revision': 2})
            self.assertEqual(undone['images'], removed['images'])
            self.assertEqual(recovery.read_bytes(), recovery_bytes)
            for relative, data in before.items():
                if relative.lower().endswith('.pdf'):
                    self.assertEqual((output / relative).read_bytes(), data)

    def test_saved_removed_source_with_missing_recovery_pdf_cannot_be_copied(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            removed, _ = photo_edit.apply_edits(output, self.payload(variants={'1': [1], '2': [2]}, removed=[0]))
            (output / removed['images'][0]['file']).unlink()
            before = self.files(output)
            with self.assertRaises(photo_edit.EditError):
                photo_edit.apply_edits(output, {
                    'revision': 1, 'variants': {'1': [1], '2': [2], '3': [4]}, 'copies': [0],
                })
            self.assertEqual(self.files(output), before)

    def test_undo_copy_save_restores_original_pdf_bytes_and_removes_created_pdfs(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            before = self.files(output)
            report = photo_edit.load_report(output)
            saved, _ = photo_edit.apply_edits(output, self.payload(
                copies=[0, 3], variants={'1': [1], '2': [2], '3': [4]}, removed=[0], misc=[5]))
            created = [saved['images'][index]['file'] for index in (4, 5)]
            undone, _ = photo_edit.restore_last_save(output, {'revision': 1})
            self.assertEqual(undone['images'], report['images'])
            self.assertEqual(undone['revision'], 2)
            for relative, data in before.items():
                if relative.lower().endswith('.pdf'):
                    self.assertEqual((output / relative).read_bytes(), data)
            self.assertTrue(all(not (output / path).exists() for path in created))
            self.assertFalse(photo_edit.can_undo_last_save(undone))
            self.assertEqual(photo_edit.load_report(output), undone)

    def test_failed_copy_save_rolls_back_every_live_file_and_new_directory(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            before = self.live_files(output)
            with patch.object(photo_edit, 'write_report', side_effect=OSError('fixture disk full')):
                with self.assertRaisesRegex(OSError, 'fixture disk full'):
                    photo_edit.apply_edits(output, self.payload(
                        copies=[0, 3], variants={'1': [1], '2': [2], '3': [4]}, removed=[0], misc=[5]))
            self.assertEqual(self.live_files(output), before)
            self.assertFalse((output / 'Вариант 3').exists())

    def test_failed_undo_copy_save_preserves_the_saved_copies(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            photo_edit.apply_edits(output, self.payload(copies=[0, 3], misc=[4, 5]))
            before = self.live_files(output)
            with patch.object(photo_edit, 'write_report', side_effect=OSError('fixture undo failure')):
                with self.assertRaisesRegex(OSError, 'fixture undo failure'):
                    photo_edit.restore_last_save(output, {'revision': 1})
            self.assertEqual(self.live_files(output), before)

    def test_copy_destination_never_overwrites_an_existing_user_pdf(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            target = output / 'Вариант 3' / 'Вариант 3.pdf'
            target.parent.mkdir()
            target.write_bytes(b'user pdf')
            before = self.files(output)
            with self.assertRaises(photo_edit.EditError):
                photo_edit.apply_edits(output, self.payload(
                    copies=[0], variants={'1': [0, 1], '2': [2], '3': [4]}))
            self.assertEqual(self.files(output), before)

    def test_user_pdf_created_during_backup_is_preserved_instead_of_being_overwritten(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            target = output / 'Вариант 3' / 'Вариант 3.pdf'
            before = self.live_files(output)
            real_copy = photo_edit.shutil.copy2

            def occupy_during_backup(source, destination, *args, **kwargs):
                result = real_copy(source, destination, *args, **kwargs)
                if Path(source).name == 'Отчёт.json':
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b'concurrently created user pdf')
                return result

            with patch.object(photo_edit.shutil, 'copy2', side_effect=occupy_during_backup):
                with self.assertRaises(photo_edit.EditError):
                    photo_edit.apply_edits(output, self.payload(
                        copies=[0], variants={'1': [0, 1], '2': [2], '3': [4]}))
            self.assertEqual(target.read_bytes(), b'concurrently created user pdf')
            after = self.live_files(output)
            after.pop(str(target.relative_to(output)))
            self.assertEqual(after, before)

    def test_missing_copy_source_is_rejected_before_any_disk_change(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            report = photo_edit.load_report(output)
            (output / report['images'][0]['file']).unlink()
            before = self.files(output)
            with self.assertRaises(photo_edit.EditError):
                photo_edit.apply_edits(output, self.payload(copies=[0], misc=[4]))
            self.assertEqual(self.files(output), before)

    def test_old_shared_perspective_results_still_reject_copy_edits(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            report = photo_edit.load_report(output)
            report['images'][0]['group'] = 'perspective'
            photo_edit.write_report(output, report)
            before = self.files(output)
            with self.assertRaisesRegex(photo_edit.EditError, '3.12'):
                photo_edit.apply_edits(output, self.payload(copies=[0], misc=[4]))
            self.assertEqual(self.files(output), before)

    def test_long_path_copies_keep_the_239_character_budget_and_full_label(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            target = Path(root) / ('Рабочий проект с длинным названием ' * 2).strip() / 'Результат'
            output, _, title = self.caption_export(root, target)
            saved, _ = photo_edit.apply_edits(output, {
                'revision': 0, 'variants': {}, 'copies': [0, 0], 'misc': [1, 2],
            })
            self.assertEqual(len({entry['file'] for entry in saved['images']}), 3)
            for entry in saved['images']:
                self.assertLessEqual(len(str(output / entry['file'])), 239)
                self.assertEqual(entry['title'], title)
                self.assertEqual(saved['folder_labels'][entry['folder']], title)
                self.assertEqual(self.colors(output / entry['file']), ['green'])

    def test_copy_destination_symlink_cannot_write_outside_the_result(self):
        with tempfile.TemporaryDirectory(prefix='pdf-copies-') as root:
            output, _ = self.export(root)
            outside = Path(root) / 'outside'
            outside.mkdir()
            try:
                os.symlink(outside, output / 'Вариант 3', target_is_directory=True)
            except OSError:
                self.skipTest('Creating directory symlinks requires Windows developer mode or elevation')
            before = self.files(output)
            with self.assertRaises(photo_edit.EditError):
                photo_edit.apply_edits(output, self.payload(
                    copies=[0], variants={'1': [0, 1], '2': [2], '3': [4]}))
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(self.files(output), before)


if __name__ == '__main__':
    unittest.main()
