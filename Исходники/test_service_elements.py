"""Unnumbered perspectives respect the presentation; filtered service elements stay recoverable."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader

from photo_edit import apply_edits, load_report, restore_last_save
from photo_export import extract
from photo_scan import BLANK_DRAWN_SHARE
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


class AuxiliaryPictures(unittest.TestCase):
    """Title strips and a key plan of the site are set aside like other service elements."""

    def test_light_blank_strip_is_set_aside_and_leaves_no_empty_folder(self):
        with tempfile.TemporaryDirectory(prefix='pdf-service-') as root:
            output, report = run(root, [{'images': [{'box': FULL, 'color': '#ff0000'},
                                                    {'box': (48, 115, 900, 54), 'size': (1500, 90), 'color': '#f7f7f7'}],
                                         'text': [('Вариант 1', 50, 140, 18)]}])
            strip = next(entry for entry in report['images'] if entry['size'] == [1500, 90])
            self.assertTrue(strip['removed'] and strip['suppressed'])
            self.assertEqual(strip['suppression_code'], 'blank_image')
            record = next(item for item in report['suppressed'] if item['suppression_code'] == 'blank_image')
            self.assertLess(record['drawn_share'], BLANK_DRAWN_SHARE)
            self.assertEqual(report['counts_by_status'], {'named': 1})
            self.assertFalse((output/'Все остальное').exists())

    def test_flat_coloured_or_drawn_picture_is_not_blank(self):
        from PIL import Image, ImageDraw
        from photo_scan import drawn_share
        with tempfile.TemporaryDirectory() as root:
            flat, drawing = Path(root, 'flat.png'), Path(root, 'drawing.png')
            Image.new('RGB', (800, 600), '#456789').save(flat)
            image = Image.new('RGB', (800, 600), 'white')
            ImageDraw.Draw(image).rectangle((100, 100, 300, 260), outline='black', width=4)
            image.save(drawing)
            self.assertIsNone(drawn_share(flat))
            self.assertGreater(drawn_share(drawing), BLANK_DRAWN_SHARE)

    def test_same_small_picture_moving_over_four_slides_is_a_service_element(self):
        plan = {'size': (300, 220), 'color': '#cc8866'}
        pages = [{'images': [{'box': (50, 170, 700, 470), 'color': color}, {'box': spot, **plan}],
                  'text': [(title, 50, 140, 18)]}
                 for color, spot, title in [('#ff0000', (820, 620, 120, 90), 'Перспектива с юга'),
                                            ('#00ff00', (60, 680, 120, 90), 'Перспектива с запада'),
                                            ('#0000ff', (820, 40, 120, 90), 'Перспектива с востока'),
                                            ('#ffff00', (60, 40, 120, 90), 'Перспектива с севера')]]
        with tempfile.TemporaryDirectory(prefix='pdf-service-') as root:
            _, report = run(root, pages)
            plans = [entry for entry in report['images'] if entry['size'] == [300, 220]]
            self.assertEqual(len(plans), 4)
            self.assertTrue(all(entry['removed'] for entry in plans))
            self.assertEqual({entry['suppression_code'] for entry in plans}, {'repeated_resource'})
            self.assertIn('на 4 страницах', plans[0]['reason'])
            self.assertEqual(sorted(entry['title'] for entry in report['images'] if not entry.get('removed')),
                             ['Перспектива с востока', 'Перспектива с запада', 'Перспектива с севера', 'Перспектива с юга'])

    def test_same_small_picture_moving_over_three_slides_stays(self):
        # As test_export_quality: a small photo shown again on three slides is still a photo.
        pages = [{'images': [{'box': (50, 170, 700, 470), 'color': color},
                             {'box': spot, 'size': (300, 220), 'color': '#cc8866'}],
                  'text': [(title, 50, 140, 18)]}
                 for color, spot, title in [('#ff0000', (820, 620, 120, 90), 'Перспектива с юга'),
                                            ('#00ff00', (60, 680, 120, 90), 'Перспектива с запада'),
                                            ('#0000ff', (820, 40, 120, 90), 'Перспектива с востока')]]
        with tempfile.TemporaryDirectory(prefix='pdf-service-') as root:
            _, report = run(root, pages)
            plans = [entry for entry in report['images'] if entry['size'] == [300, 220]]
            self.assertEqual(len(plans), 3)
            self.assertFalse(any(entry.get('removed') for entry in plans))


class CaptionText(unittest.TestCase):
    def test_paragraph_and_elevation_mark_do_not_name_a_picture(self):
        paragraph = ('Проектом предусмотрено 450 машино-мест. В границах участка возможно расположить 13 мест, '
                     'в том числе для маломобильных групп населения. Обязательства по обеспечению объекта '
                     'нормативным количеством мест приняты по договору.')
        self.assertGreater(len(paragraph), 200)
        with tempfile.TemporaryDirectory(prefix='pdf-caption-') as root:
            _, report = run(root, [{'images': [{'box': FULL, 'color': '#ff0000'}],
                                    'text': [(paragraph, 50, 140, 9)]},
                                   {'images': [{'box': FULL, 'color': '#00ff00'}],
                                    'text': [('на отм . +3,200', 50, 140, 18)]},
                                   {'images': [{'box': FULL, 'color': '#0000ff'}],
                                    'text': [('Схемы разрезов с указанием высотных отметок', 50, 140, 18)]}])
            titles = [entry['title'] for entry in report['images']]
            self.assertEqual(titles, [None, None, 'Схемы разрезов с указанием высотных отметок'])

    def test_grid_axes_do_not_name_a_picture(self):
        with tempfile.TemporaryDirectory(prefix='pdf-caption-') as root:
            _, report = run(root, [{'images': [{'box': FULL, 'color': '#ff0000'}],
                                    'text': [('1а - 2/А - Б', 50, 140, 18)]}])
            self.assertIsNone(report['images'][0]['title'])

    def test_heading_over_a_row_names_each_plan_of_one_height(self):
        heading = [('ПЛАН 7-ГО ЭТАЖА', 40, 120, 16)]
        with tempfile.TemporaryDirectory(prefix='pdf-caption-') as root:
            # Two tower plans side by side; the heading at the left margin stands over the first only.
            _, report = run(root, [{'images': [{'box': (40, 200, 420, 320), 'color': '#ff0000'},
                                               {'box': (520, 210, 420, 320), 'color': '#00ff00'}],
                                    'text': heading},
                                   # A small legend beside the plan is not part of the row.
                                   {'images': [{'box': (40, 200, 420, 320), 'color': '#0000ff'},
                                               {'box': (620, 420, 150, 90), 'color': '#ffff00'}],
                                    'text': heading},
                                   # A numbered caption names its own picture, not the one beside it.
                                   {'images': [{'box': (40, 200, 420, 320), 'color': '#ff00ff'},
                                               {'box': (520, 210, 420, 320), 'color': '#00ffff'}],
                                    'text': [('Перспективное изображение №1', 40, 120, 16)]}])
            titles = [(entry['page'], entry['title']) for entry in report['images']]
            self.assertEqual(titles, [(1, 'ПЛАН 7-ГО ЭТАЖА'), (1, 'ПЛАН 7-ГО ЭТАЖА'),
                                      (2, 'ПЛАН 7-ГО ЭТАЖА'), (2, None),
                                      (3, 'Перспективное изображение №1'), (3, None)])
            self.assertEqual(report['images'][1]['reason'], 'Общий заголовок над рядом изображений одного размера.')


if __name__ == '__main__':
    unittest.main()
