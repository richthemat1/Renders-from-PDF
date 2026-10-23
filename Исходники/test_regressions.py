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


if __name__ == '__main__':
    unittest.main(verbosity=2)
