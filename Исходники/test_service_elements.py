"""Unnumbered perspectives respect the presentation; filtered service elements stay recoverable."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader

from photo_edit import apply_edits, load_report, restore_last_save
from photo_export import extract
from test_photos import make_pdf

FULL = (50, 170, 800, 500)


def run(root, pages):
    with contextlib.redirect_stdout(io.StringIO()):
        report = extract(make_pdf(root, pages))
    return Path(report['output']), report


def pixel(path, page=0):
    with PdfReader(path) as reader:
        return reader.pages[page].images[0].image.convert('RGB').getpixel((2, 2))


class PerspectiveConflict(unittest.TestCase):
    def comparison(self, label):
        return {'images': [{'box': (50, 170, 400, 300), 'color': '#ff0000'},
                           {'box': (550, 170, 400, 300), 'color': '#00ff00'}],
                'text': [(label, 50, 120, 16), ('Вариант 2', 550, 120, 16)]}

    def test_outdated_variant_one_keeps_unnumbered_perspectives_for_review(self):
        with tempfile.TemporaryDirectory(prefix='pdf-perspective-') as root:
            output, report = run(root, [
                self.comparison('Вариант 1 (предыдущая версия)'),
                {'images': [{'box': FULL, 'color': '#0000ff'}], 'text': [('Перспективное изображение №1', 50, 120, 18)]},
                {'images': [{'box': FULL, 'color': '#ffff00'}], 'text': [('Перспективное изображение №2', 50, 120, 18)]}])
            perspectives = [entry for entry in report['images'] if entry['page'] > 1]
            self.assertEqual([(e['status'], e['variant'], e['folder'], e.get('variant_source')) for e in perspectives],
                             [('review', None, 'На проверку', 'perspective_conflict')]*2)
            self.assertEqual(perspectives[0]['title'], 'Перспективное изображение №1')
            self.assertIn('предыдущая версия', perspectives[0]['reason'])
            self.assertEqual(report['perspective_conflict']['kind'], 'outdated_variant')
            self.assertEqual(report['perspective_conflict']['page'], 1)
            with PdfReader(output/'Вариант 1'/'Вариант 1.pdf') as reader:
                self.assertEqual(len(reader.pages), 1)
            self.assertEqual(len(list((output/'Все остальное'/'На проверку').glob('*.pdf'))), 2)

    def test_current_variant_one_still_receives_unnumbered_perspectives(self):
        with tempfile.TemporaryDirectory(prefix='pdf-perspective-') as root:
            output, report = run(root, [
                self.comparison('Вариант 1'),
                {'images': [{'box': FULL, 'color': '#0000ff'}], 'text': [('Перспективное изображение №1', 50, 120, 18)]}])
            self.assertIsNone(report['perspective_conflict'])
            last = report['images'][-1]
            self.assertEqual((last['variant'], last['variant_source']), (1, 'perspective_default'))
            with PdfReader(output/'Вариант 1'/'Вариант 1.pdf') as reader:
                self.assertEqual(len(reader.pages), 2)


class ServiceElements(unittest.TestCase):
    PAGES = [{'images': [{'box': FULL, 'color': '#ff0000'}, {'box': (900, 700, 40, 30), 'color': '#00ff00'}],
              'text': [('Вариант 1', 50, 120, 18)]},
             {'images': [{'box': FULL, 'color': '#0000ff'}], 'text': [('Схема участка', 50, 120, 18)]}]

    def test_filtered_element_is_kept_as_a_removed_service_card(self):
        with tempfile.TemporaryDirectory(prefix='pdf-service-') as root:
            output, report = run(root, self.PAGES)
            service = [entry for entry in report['images'] if entry.get('suppressed')]
            self.assertEqual(len(service), 1)
            entry = service[0]
            self.assertTrue(entry['removed'])
            self.assertEqual((entry['page'], entry['status'], entry['suppression_code']), (1, 'unsigned', 'small_fragment'))
            self.assertTrue(entry['file'].startswith(str(Path('_Служебное', 'Служебные элементы'))))
            self.assertEqual(pixel(output/entry['file']), (0, 255, 0))
            self.assertTrue(entry['preview'])
            self.assertEqual(report['suppressed'][0]['image'], report['images'].index(entry))
            # Counts, documents and the portable gallery describe only the actual result.
            self.assertEqual(sum(report['counts_by_status'].values()), 2)
            self.assertNotIn(entry['file'], {document['file'] for document in report['documents']})
            self.assertFalse((output/'Все остальное'/'Без подписи').exists())
            with PdfReader(output/'Вариант 1'/'Вариант 1.pdf') as reader:
                self.assertEqual(len(reader.pages), 1)

    def test_service_element_can_be_restored_and_the_save_undone(self):
        with tempfile.TemporaryDirectory(prefix='pdf-service-') as root:
            output, report = run(root, self.PAGES)
            index = next(i for i, entry in enumerate(report['images']) if entry.get('suppressed'))
            service_file = output/report['images'][index]['file']
            variants = {'1': [i for i, e in enumerate(report['images']) if e['variant'] == 1]}
            saved, backup = apply_edits(output, {'revision': 0, 'variants': variants, 'removed': [],
                                                 'restored': [index], 'misc': [index]})
            entry = saved['images'][index]
            self.assertNotIn('removed', entry)
            self.assertNotIn('suppressed', entry)
            self.assertEqual((entry['folder'], entry['status']), ('Без подписи', 'unsigned'))
            self.assertEqual(entry['reason'], 'Возвращено вручную из служебных элементов.')
            self.assertTrue(entry['file'].startswith(str(Path('Все остальное', 'Без подписи'))))
            self.assertEqual(pixel(output/entry['file']), (0, 255, 0))
            self.assertEqual(saved['counts_by_status'].get('unsigned'), 1)
            self.assertTrue(service_file.is_file(), 'Копия служебного элемента должна остаться источником для отмены')
            # Undoing the save returns the element to the service section.
            undone, _ = restore_last_save(output, {'revision': 1})
            self.assertTrue(undone['images'][index].get('suppressed'))
            self.assertTrue(undone['images'][index].get('removed'))
            self.assertEqual(undone['images'][index]['file'], report['images'][index]['file'])
            self.assertFalse(list((output/'Все остальное').glob('Без подписи/*.pdf')))
            # Restoring straight into a variant is an ordinary edit as well.
            moved, _ = apply_edits(output, {'revision': undone['revision'], 'removed': [],
                                            'variants': {'1': variants['1']+[index]}, 'restored': [index]})
            self.assertNotIn('suppressed', moved['images'][index])
            self.assertEqual(moved['images'][index]['variant'], 1)
            with PdfReader(output/'Вариант 1'/'Вариант 1.pdf') as reader:
                self.assertEqual(len(reader.pages), 2)
            self.assertEqual(pixel(output/'Вариант 1'/'Вариант 1.pdf', 1), (0, 255, 0))
            self.assertEqual(load_report(output)['revision'], moved['revision'])


if __name__ == '__main__':
    unittest.main()
