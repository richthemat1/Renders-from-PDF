"""Regressions from four real presentations: a page that does not start at zero, a photo stored
as strips, a slide title at the left margin and «Визуализация» as the name of a perspective."""
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject

import photo_export
from test_export_quality import active
from test_photos import make_pdf


def shifted(source, left=-500, bottom=-400):
    """The same slides on pages whose boxes are centred on zero, as a CAD export writes them."""
    target = Path(source).with_name('shifted.pdf')
    with PdfReader(source) as reader, PdfWriter() as writer:
        for page in reader.pages:
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            page.add_transformation(Transformation().translate(left, bottom))
            page.mediabox = page.cropbox = RectangleObject([left, bottom, left+width, bottom+height])
            writer.add_page(page)
        writer.write(target)
    return target


def sorted_as(report):
    return [(entry['page'], entry['status'], entry['title'], entry['variant']) for entry in active(report)]


class PageOrigin(unittest.TestCase):
    def test_slides_are_sorted_the_same_when_the_page_box_does_not_start_at_zero(self):
        with tempfile.TemporaryDirectory() as root:
            header = ('Жилой дом на улице Примерной', 300, 30, 16)
            logo = {'box': (50, 20, 200, 45), 'size': (800, 180), 'color': '#dddddd'}
            pages = [{'images': [logo, {'box': (50, 170, 900, 500), 'color': color}], 'text': [header] + text}
                     for color, text in (('#884422', [('Вид с улицы', 50, 130, 18)]),
                                         ('#228844', [('Вариант 2', 50, 70, 18), ('Вид со двора', 50, 130, 18)]),
                                         ('#224488', []), ('#444444', []))]
            source = make_pdf(root, pages)
            plain = photo_export.extract(source)
            self.assertEqual(sorted_as(plain), [(1, 'named', 'Вид с улицы', None),
                                                (2, 'named', 'Вариант 2 — Вид со двора', 2),
                                                (3, 'unsigned', None, None), (4, 'unsigned', None, None)])
            moved = photo_export.extract(shifted(source))
            self.assertEqual(sorted_as(moved), sorted_as(plain))
            self.assertEqual(moved['counts_by_suppression'], plain['counts_by_suppression'])


class PhotoInStrips(unittest.TestCase):
    def test_photo_stored_as_strips_and_repeated_on_three_slides_is_no_service_element(self):
        with tempfile.TemporaryDirectory() as root:
            # Each strip is 3 % of the page, the photo they make 15 %.
            strips = [{'box': (50, 180+index*60, 400, 60), 'size': (800, 120), 'color': color}
                      for index, color in enumerate(['#705030', '#806040', '#907050', '#a08060', '#b09070'])]
            pages = [{'images': strips + [{'box': (500, 180, 400, 300), 'color': color}],
                      'text': [('Фотофиксация', 50, 140, 18), ('Фотомонтаж', 500, 140, 18)]}
                     for color in ('#112233', '#223344', '#334455')]
            report = photo_export.extract(make_pdf(root, pages))
            self.assertEqual(report['suppressed'], [])
            photos = [entry for entry in active(report) if entry['title'] == 'Фотофиксация']
            self.assertEqual([(entry['page'], entry['size'], entry['method']) for entry in photos],
                             [(page, [800, 600], 'stitched_originals') for page in (1, 2, 3)])


class SlideTitle(unittest.TestCase):
    def export(self, pages):
        with tempfile.TemporaryDirectory() as root:
            report = photo_export.extract(make_pdf(root, pages))
            return [(entry['page'], entry['title']) for entry in active(report)]

    def test_title_at_the_left_margin_names_a_centred_picture_it_does_not_stand_over(self):
        self.assertEqual(self.export([{'images': [{'box': (350, 170, 300, 500)}],
                                       'text': [('Вид с улицы', 30, 110, 12)]}]), [(1, 'Вид с улицы')])

    def test_title_names_every_picture_of_the_first_row_only(self):
        pages = [{'images': [{'box': (300, 300, 300, 200)}, {'box': (620, 300, 300, 200), 'color': '#775533'},
                             {'box': (300, 540, 300, 200), 'color': '#335577'}],
                  'text': [('Фотофиксация. Фотомонтаж', 30, 110, 12)]}]
        self.assertEqual(self.export(pages), [(1, 'Фотофиксация. Фотомонтаж'), (1, 'Фотофиксация. Фотомонтаж'),
                                              (1, None)])

    def test_title_does_not_name_a_small_picture_beside_the_main_one(self):
        pages = [{'images': [{'box': (300, 170, 400, 500)}, {'box': (750, 170, 200, 150), 'color': '#998877'}],
                  'text': [('Вид с улицы', 30, 110, 12)]}]
        self.assertEqual(self.export(pages), [(1, 'Вид с улицы'), (1, None)])

    def test_numbered_title_names_a_single_picture_but_not_one_of_two(self):
        title = ('Визуализация №6', 30, 110, 12)
        self.assertEqual(self.export([{'images': [{'box': (350, 170, 300, 500)}], 'text': [title]}]),
                         [(1, 'Визуализация №6')])
        pair = [{'box': (300, 300, 300, 200)}, {'box': (620, 300, 300, 200), 'color': '#775533'}]
        self.assertEqual(self.export([{'images': pair, 'text': [title]}]), [(1, None), (1, None)])
        montage = 'Фотофиксация существующего положения. Фотомонтаж №1'
        self.assertEqual(self.export([{'images': pair, 'text': [(montage, 30, 110, 12)]}]),
                         [(1, montage), (1, montage)])

    def test_title_far_above_or_away_from_the_margin_names_nothing(self):
        for text in (('Вид с улицы', 30, 20, 12), ('Вид с улицы', 200, 110, 12)):
            with self.subTest(text=text):
                self.assertEqual(self.export([{'images': [{'box': (350, 300, 300, 400)}], 'text': [text]}]),
                                 [(1, None)])


class Visualisation(unittest.TestCase):
    def test_numbered_visualisation_is_a_perspective_of_variant_one(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [{'images': [{'box': (50, 170, 900, 500)}], 'text': [(title, 50, 130, 18)]}
                     for title in ('Визуализация №3', 'Визуализация №8. День', 'Визуализация участка')]
            report = photo_export.extract(make_pdf(root, pages))
            self.assertEqual([(entry['title'], entry['variant'], entry.get('variant_source')) for entry in active(report)],
                             [('Визуализация №3', 1, 'perspective_default'),
                              ('Визуализация №8. День', 1, 'perspective_default'),
                              ('Визуализация участка', None, None)])


if __name__ == '__main__':
    unittest.main()
