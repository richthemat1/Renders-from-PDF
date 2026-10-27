"""Full caption labels survive physical path shortening and real PDF edits."""
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader

from photo_export import extract
from photo_edit import apply_edits, load_report, rename_item, restore_last_save, write_report
from test_photos import make_pdf


class CaptionLabels(unittest.TestCase):
    def export(self, root, output=None):
        source = make_pdf(root, [{'images': [{'box': (50, 170, 800, 500), 'color': '#00ff00'}],
                                  'text': [('Короткая подпись', 50, 120, 18)]}])
        report = extract(source, output=output)
        return Path(report['output']), report

    def test_unchanged_full_caption_is_a_noop_even_when_it_is_not_a_filename(self):
        with tempfile.TemporaryDirectory(prefix='pdf-caption-') as root:
            output, report = self.export(root)
            folder = report['images'][0]['folder']
            full = 'Материалы: благоустройство / озеленение и территория ' * 4
            full = full.strip()
            report['folder_labels'] = {folder: full}
            write_report(output, report)
            before = (output/'Отчёт.json').read_bytes()
            saved, change = rename_item(output, {'revision': 0, 'kind': 'folder', 'target': folder, 'name': full})
            self.assertIsNone(change)
            self.assertEqual(saved['revision'], 0)
            self.assertEqual((output/'Отчёт.json').read_bytes(), before)

    def test_folder_rename_keeps_a_full_label_with_a_short_readable_disk_name(self):
        with tempfile.TemporaryDirectory(prefix='pdf-caption-') as root:
            target = Path(root)/('Рабочий проект с длинным названием ' * 2).strip()/'Результат'
            output, report = self.export(root, target)
            old = report['images'][0]['folder']
            full = 'Материалы благоустройства территории жилого комплекса вдоль центральной улицы и общественных пространств'
            saved, change = rename_item(output, {'revision': 0, 'kind': 'folder', 'target': old, 'name': full})
            entry = saved['images'][0]
            self.assertLess(len(entry['folder']), len(full))
            parts = entry['folder'].split(' … ')
            self.assertTrue(full.startswith(parts[0]))
            if len(parts) == 2:
                self.assertTrue(full.endswith(parts[1]))
            self.assertEqual(saved['folder_labels'][entry['folder']], full)
            self.assertEqual(change['disk_to'], entry['folder'])
            self.assertEqual(change['to'], full)
            self.assertLessEqual(len(str(output/entry['file'])), 239)
            with PdfReader(output/entry['file']) as reader:
                self.assertEqual(len(reader.pages), 1)

    def test_full_label_survives_move_return_and_undo(self):
        with tempfile.TemporaryDirectory(prefix='pdf-caption-') as root:
            output, report = self.export(root)
            folder = report['images'][0]['folder']
            full = 'Материалы благоустройства и озеленения территории'
            report['folder_labels'] = {folder: full}
            write_report(output, report)
            moved, _ = apply_edits(output, {'revision': 0, 'variants': {'1': [0]}, 'removed': []})
            self.assertEqual(moved['folder_labels'][folder], full)
            returned, _ = apply_edits(output, {'revision': 1, 'variants': {}, 'removed': [], 'misc': [0]})
            self.assertIsNone(returned['images'][0]['variant'])
            self.assertEqual(returned['folder_labels'][returned['images'][0]['folder']], full)
            undone, _ = restore_last_save(output, {'revision': 2})
            self.assertEqual(undone['images'][0]['variant'], 1)
            self.assertEqual(undone['folder_labels'][folder], full)
            self.assertEqual(load_report(output)['folder_labels'][folder], full)

    def test_legacy_report_without_label_map_can_still_rename(self):
        with tempfile.TemporaryDirectory(prefix='pdf-caption-') as root:
            output, report = self.export(root)
            report.pop('folder_labels', None)
            write_report(output, report)
            saved, _ = rename_item(output, {'revision': 0, 'kind': 'folder',
                                          'target': report['images'][0]['folder'], 'name': 'Двор'})
            self.assertEqual(saved['images'][0]['folder'], 'Двор')
            self.assertEqual(saved['folder_labels']['Двор'], 'Двор')
            self.assertTrue((output/saved['images'][0]['file']).is_file())
