"""Regression cases for section boundaries, image transforms and shutdown during a save."""
from contextlib import closing
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw
import pypdfium2
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

import photo_edit
from photo_export import VERSION, extract
from photo_server import PreviewServer
from test_photos import make_pdf


class ExportRegressions(unittest.TestCase):
    def test_comparison_heading_ends_a_section_without_confirmed_variant_labels(self):
        cases = [([], [None, None, None]),
                 ([('1 Бюро Север', 65, 180, 18), ('2 Студия Юг', 565, 180, 18)], [None, None, None]),
                 ([('Вариант 1', 50, 120, 18)], [1, None, None])]
        for labels, expected in cases:
            with self.subTest(labels=labels), tempfile.TemporaryDirectory() as root:
                pages = [
                    {'text': [('ВАРИАНТ 9', 440, 380, 20)]},
                    {'images': [{'box': (50, 170, 400, 270)},
                                {'box': (550, 170, 400, 270), 'color': '#778899'}],
                     'text': [('Сравнение вариантов', 50, 40, 18)] + labels},
                    {'images': [{'box': (50, 300, 900, 430)}]},
                ]
                report = extract(make_pdf(root, pages))
                self.assertEqual([entry['variant'] for entry in report['images']], expected)
                self.assertIsNone(report['page_contexts'][1]['context'])
                self.assertIsNone(report['page_contexts'][2]['context'])

    def test_image_rotation_mirroring_and_form_transform_match_the_visible_source(self):
        cases = [(90, 1, False, False), (180, 1, False, False), (270, 1, False, False),
                 (0, -1, False, False), (0, 1, True, False), (90, 1, False, True),
                 (0, 1, False, False)]
        for angle, mirror, page_rotation, form in cases:
            with self.subTest(angle=angle, mirror=mirror, page_rotation=page_rotation, form=form):
                with tempfile.TemporaryDirectory() as root:
                    root = Path(root)
                    raster = root/'quadrants.png'
                    with Image.new('RGB', (600, 300), '#ff0000') as image:
                        draw = ImageDraw.Draw(image)
                        draw.rectangle((300, 0, 599, 149), fill='#00ff00')
                        draw.rectangle((0, 150, 299, 299), fill='#0000ff')
                        draw.rectangle((300, 150, 599, 299), fill='#ffff00')
                        image.save(raster, 'PNG')
                    source = root/'transformed.pdf'
                    pdf = canvas.Canvas(str(source), pagesize=(1000, 1000))
                    pdf.setFont('Helvetica', 18)
                    pdf.drawString(40, 950, 'Render')
                    if form:
                        pdf.beginForm('render', -300, -150, 300, 150)
                        pdf.drawImage(str(raster), -300, -150, width=600, height=300)
                        pdf.endForm()
                    pdf.saveState()
                    pdf.translate(500, 500)
                    pdf.rotate(angle)
                    pdf.scale(mirror, 1)
                    if form:
                        pdf.doForm('render')
                    else:
                        pdf.drawImage(str(raster), -300, -150, width=600, height=300)
                    pdf.restoreState()
                    pdf.showPage()
                    pdf.save()
                    if page_rotation:
                        rotated = root/'rotated-page.pdf'
                        with PdfReader(source) as reader, PdfWriter() as writer:
                            writer.add_page(reader.pages[0].rotate(90))
                            writer.write(rotated)
                        source = rotated
                    report = extract(source)
                    entry = report['images'][0]
                    target = Path(report['output'])/entry['file']
                    self.assertEqual(report['errors'], [])
                    self.assertEqual(report['warnings'], [])
                    self.assertEqual(entry['method'], 'original')
                    x0, top, x1, bottom = entry['bbox']
                    with pypdfium2.PdfDocument(source) as document:
                        with closing(document[0]) as page:
                            bitmap = page.render(scale=1, crop=(x0, page.get_height()-bottom,
                                                               page.get_width()-x1, top))
                            with closing(bitmap):
                                expected = bitmap.to_pil().copy().convert('RGB')
                    with pypdfium2.PdfDocument(target) as document:
                        with closing(document[0]) as page:
                            with closing(page.render(scale=1)) as bitmap:
                                actual = bitmap.to_pil().copy().convert('RGB')
                    with expected, actual:
                        self.assertEqual(actual.size, expected.size)
                        for x, y in ((.2, .2), (.8, .2), (.2, .8), (.8, .8)):
                            position = (round(actual.width*x), round(actual.height*y))
                            self.assertEqual(actual.getpixel(position), expected.getpixel(position))


class CaptionRegressions(unittest.TestCase):
    """UX test 3.39, Б7 and Б18: labels of other pictures, callouts, joined words, a false scan warning."""

    def titles(self, root, pages):
        report = extract(make_pdf(root, pages))
        return [(entry['page'], entry['status'], entry['title']) for entry in report['images']
                if not entry.get('removed')], report

    def test_words_placed_without_a_space_are_split_by_their_gap(self):
        first = 'Схема функционального зонирования'
        # «зонированияобъекта»: the second word starts 0.25 em after the first, closer than pdfplumber's 3 pt.
        after = 50+pdfmetrics.stringWidth(first, 'FixtureArial', 10)+2.5
        with tempfile.TemporaryDirectory() as root:
            titles, _ = self.titles(root, [{'images': [{'box': (50, 170, 800, 500)}],
                                            'text': [(first, 50, 150, 10), ('объекта (аксонометрия)', after, 150, 10)]}])
            self.assertEqual(titles, [(1, 'named', 'Схема функционального зонирования объекта (аксонометрия)')])

    def test_callout_under_a_render_does_not_compete_with_its_heading(self):
        heading = ('Материалы благоустройства. Перспективное изображение территории', 50, 128, 14)
        with tempfile.TemporaryDirectory() as root:
            # «спортивные площадки» stands at a leader line near the right edge, not under the picture's middle.
            titles, _ = self.titles(root, [{'images': [{'box': (50, 170, 800, 500)}],
                                            'text': [heading, ('спортивные площадки', 680, 672, 12)]}])
            self.assertEqual(titles, [(1, 'named', heading[0])])
        with tempfile.TemporaryDirectory() as root:
            # A line under the picture's left edge is a caption of its own: a person decides.
            titles, _ = self.titles(root, [{'images': [{'box': (50, 170, 800, 500)}],
                                            'text': [heading, ('Условные обозначения функциональных зон', 50, 672, 12)]}])
            self.assertEqual(titles, [(1, 'review', None)])

    def test_legend_item_names_its_swatch_not_the_plan_below(self):
        with tempfile.TemporaryDirectory() as root:
            titles, _ = self.titles(root, [{
                'images': [{'box': (50, 170, 500, 450), 'color': '#ff0000'},
                           {'box': (740, 295, 80, 40), 'color': '#33aa33'},
                           {'box': (600, 420, 250, 200), 'color': '#cc66cc'}],
                'text': [('Перспектива участка', 50, 140, 18), ('Условные обозначения:', 600, 190, 14),
                         ('2. Газон', 600, 302, 10)]}])
            self.assertEqual(titles, [(1, 'named', 'Перспектива участка'), (1, 'unsigned', None)])

    def test_lamp_labels_name_the_lamps_not_the_plan(self):
        side = 'Опора контактной сети'
        lamp = 100+pdfmetrics.stringWidth(side, 'FixtureArial', 10)+4
        with tempfile.TemporaryDirectory() as root:
            titles, _ = self.titles(root, [{
                'images': [{'box': (600, 120, 150, 100), 'color': '#aa7744'},
                           {'box': (600, 240, 250, 150), 'color': '#cc66cc'},
                           {'box': (620, 416, 60, 70), 'color': '#777777'},
                           {'box': (lamp, 598, 100, 120), 'color': '#555555'}],
                'text': [('Скамейка «Энигма»', 600, 98, 10), ('Дорожный светильник', 620, 400, 10),
                         (side, 100, 600, 10)]}])
            # The bench keeps its label; the plan under the bench takes neither the bench label above it
            # nor the label of the small lamp under it; the lamp described on its left is named by it.
            self.assertEqual(titles, [(1, 'named', 'Скамейка «Энигма»'), (1, 'unsigned', None), (1, 'named', side)])

    def test_table_heading_and_legend_under_a_map_do_not_name_it(self):
        with tempfile.TemporaryDirectory() as root:
            titles, _ = self.titles(root, [{
                'images': [{'box': (500, 100, 400, 300), 'color': '#557799'},
                           {'box': (500, 408, 50, 16), 'color': '#ff0000'}],
                'text': [('подготовки проекта планировки территории', 560, 410, 9),
                         ('Чек-лист соответствия нормативам', 600, 432, 12),
                         ('Показатели', 610, 456, 10), ('Решение', 700, 456, 10), ('Норматив', 780, 456, 10)]}])
            self.assertEqual(titles, [(1, 'unsigned', None)])

    def test_photo_with_rectangles_but_no_plain_ground_raises_no_scan_warning(self):
        # «Подпись на скане не распознана» on a photograph: rectangles found inside it, no ground between them.
        with tempfile.TemporaryDirectory() as root, \
                patch('photo_scan.photo_panels', return_value=[(0, 0, 300, 400), (300, 0, 600, 400)]):
            titles, report = self.titles(root, [{'images': [{'box': (50, 170, 800, 500)}],
                                                 'text': [('Фотофиксация существующего положения', 50, 140, 18)]}])
            self.assertEqual(titles, [(1, 'named', 'Фотофиксация существующего положения')])
            self.assertEqual(report['warnings'], [])
            self.assertEqual(report['scan_pages'], [])
            self.assertEqual(report['images'][0]['size'], [600, 400])


class ShutdownRegressions(unittest.TestCase):
    def test_opening_an_older_result_upgrades_only_the_gallery(self):
        with tempfile.TemporaryDirectory() as root:
            report = extract(make_pdf(root, [{'images': [{'box': (50, 170, 800, 500)}]}]))
            output = Path(report['output'])
            gallery = output/'Просмотр.html'
            gallery.write_text('<html><body data-revision="0">Old editor</body></html>', encoding='utf-8')
            originals = {path: path.read_bytes() for path in output.rglob('*')
                         if path.is_file() and path != gallery}
            server = PreviewServer()
            try:
                server.register(output)
                self.assertIn(f'data-gallery-version="{VERSION}"', gallery.read_text(encoding='utf-8'))
                self.assertNotIn('Old editor', gallery.read_text(encoding='utf-8'))
                for path, contents in originals.items():
                    self.assertEqual(path.read_bytes(), contents)
            finally:
                server.stop()

    def test_stop_waits_until_pdf_and_report_changes_are_committed(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [{'images': [{'box': (50, 170, 800, 500), 'color': color}],
                      'text': [(f'Вариант {number}', 50, 120, 18)]}
                     for color, number in [('#ff0000', 1), ('#00ff00', 1), ('#0000ff', 2)]]
            report = extract(make_pdf(root, pages))
            output = Path(report['output'])
            entered, release, stopped = Event(), Event(), Event()
            original = photo_edit.write_gallery
            response, errors = [], []

            def slow_gallery(folder, new):
                entered.set()
                if not release.wait(10):
                    raise RuntimeError('Test did not release the pending save')
                return original(folder, new)

            server = PreviewServer()
            url = server.register(output)
            origin = server.origin

            def save():
                try:
                    body = json.dumps({'revision': 0, 'variants': {'1': [1], '2': [2, 0]}}).encode()
                    request = Request(url.rsplit('/', 1)[0]+'/api/apply', body,
                                      {'Content-Type': 'application/json', 'Origin': origin})
                    with urlopen(request, timeout=15) as result:
                        response.append(json.loads(result.read()))
                except Exception as error:
                    errors.append(error)

            def close():
                server.stop()
                stopped.set()

            client = Thread(target=save)
            closer = Thread(target=close)
            with patch.object(photo_edit, 'write_gallery', slow_gallery):
                try:
                    client.start()
                    self.assertTrue(entered.wait(10), 'The save must reach the commit phase')
                    closer.start()
                    self.assertFalse(stopped.wait(.8), 'Closing must wait for the pending save')
                finally:
                    release.set()
                    if closer.ident is not None:
                        closer.join(15)
                    client.join(15)
                    server.stop()
            self.assertTrue(stopped.is_set())
            self.assertEqual(errors, [])
            self.assertTrue(response[0]['ok'])
            saved = photo_edit.load_report(output)
            self.assertEqual(saved['revision'], 1)
            for document in saved['documents']:
                with PdfReader(output/document['file']) as reader:
                    self.assertEqual(document['pages'], len(reader.pages))



class TransientFileLocks(unittest.TestCase):
    """Windows Defender and the search indexer hold a file the program has just written for a moment;
    deleting or moving it then fails with «Отказано в доступе» (PermissionError)."""
    pages = [{'images': [{'box': (50, 170, 800, 500), 'color': color}], 'text': [(title, 50, 120, 18)]}
             for title, color in [('Вариант 1. Двор', '#e4b5a3'), ('Вариант 1. Сквер', '#a4cbb7'), ('Парковка', '#adc3de')]]

    def test_a_file_held_for_a_moment_does_not_fail_the_processing(self):
        import photo_export
        unlink, rename = Path.unlink, Path.rename
        held = {'unlink': 0, 'rename': 0}

        def busy(kind):
            held[kind] += 1
            raise PermissionError(13, 'Процесс не может получить доступ к файлу, так как этот файл занят другим процессом')

        def held_unlink(path, *args, **kwargs):
            return busy('unlink') if held['unlink'] < 3 else unlink(path, *args, **kwargs)

        def held_rename(path, target):
            return busy('rename') if held['rename'] < 2 else rename(path, target)

        with tempfile.TemporaryDirectory(prefix='pdf-held-') as root:
            source = make_pdf(root, self.pages)
            with patch.object(Path, 'unlink', held_unlink), patch.object(Path, 'rename', held_rename):
                report = photo_export.extract(source)
            self.assertEqual(held, {'unlink': 3, 'rename': 2})
            self.assertEqual(report['errors'], [])
            output = Path(report['output'])
            self.assertTrue((output/'Отчёт.json').is_file())
            self.assertEqual([path.name for path in Path(root).iterdir() if path.name.startswith('.pdf-photos-')], [])

    def test_a_saved_pdf_held_for_a_moment_is_still_replaced(self):
        import os
        replace, held = os.replace, []

        def held_replace(source, target):
            if len(held) < 2:
                held.append(target)
                raise PermissionError(13, 'Процесс не может получить доступ к файлу')
            return replace(source, target)

        with tempfile.TemporaryDirectory(prefix='pdf-held-') as root, patch.object(os, 'replace', held_replace):
            path = Path(root)/'Вариант 1.pdf'
            path.write_bytes(b'old')
            photo_edit._write_bytes(path, b'new')
            self.assertEqual((path.read_bytes(), len(held)), (b'new', 2))
            self.assertEqual([item.name for item in Path(root).iterdir()], ['Вариант 1.pdf'])

    def test_a_failure_stays_itself_when_its_temporary_folder_cannot_be_removed(self):
        import photo_export
        with tempfile.TemporaryDirectory(prefix='pdf-held-') as root:
            source = make_pdf(root, self.pages)
            with patch.object(photo_export, 'SETTLE_SECONDS', 0.2),                     patch.object(photo_export, 'render_previews', side_effect=ValueError('Сбой миниатюр')),                     patch.object(photo_export, 'shutil') as shutil:
                shutil.rmtree.side_effect = PermissionError(13, 'Отказано в доступе')
                with self.assertRaisesRegex(ValueError, 'Сбой миниатюр'):
                    photo_export.extract(source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
