"""Poster-style PDFs: huge JPEG layers, text-clipped colour layers, grid cells and row captions."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import pypdf.filters
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas

import photo_export
import photo_scan
from test_photos import make_pdf


def run(source):
    with contextlib.redirect_stdout(io.StringIO()):
        return photo_export.extract(source)


def active(report):
    return [entry for entry in report['images'] if not entry.get('removed')]


def poster(root, layer_text='ВАРИАНТ 1'):
    """A full-page JPEG base, then a white JPEG layer seen only through caption glyphs (Tr 7)."""
    root = Path(root)
    base, layer = root/'base.jpg', root/'layer.jpg'
    image = Image.new('RGB', (800, 600), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 60, 380, 280), fill='#2a6f97')
    draw.rectangle((420, 60, 760, 280), fill='#a23b72')
    draw.rectangle((40, 320, 380, 540), fill='#3d7a3d')
    draw.rectangle((420, 320, 760, 540), fill='#c97b22')
    image.save(base, quality=92)
    Image.new('RGB', (800, 600), 'white').save(layer, quality=92)
    source = root/'poster.pdf'
    pdf = canvas.Canvas(str(source), pagesize=(800, 600))
    pdf.drawImage(str(base), 0, 0, 800, 600)
    pdf.saveState()
    text = pdf.beginText(40, 560)
    text.setTextRenderMode(7)
    text.setFont('Helvetica-Bold', 20)
    text.textLine(layer_text)
    pdf.drawText(text)
    pdf.drawImage(str(layer), 0, 0, 800, 600)
    pdf.restoreState()
    pdf.showPage()
    pdf.save()
    return source


class TextClippedLayers(unittest.TestCase):
    def test_a_layer_seen_only_through_glyphs_neither_hides_the_poster_nor_becomes_a_picture(self):
        with tempfile.TemporaryDirectory(prefix='pdf-poster-') as root:
            report = run(poster(root))
            codes = [item['suppression_code'] for item in report['suppressed']]
            self.assertEqual(codes, ['text_fill'])
            self.assertNotIn('fully_occluded', codes)
            self.assertTrue(active(report), 'Основной растр плаката не должен считаться перекрытым')
            self.assertTrue(all(entry['components'][0]['resource'] != 'layer' for entry in active(report)))


class LargeRasters(unittest.TestCase):
    def test_the_raw_stream_cap_is_raised_for_poster_jpegs_only(self):
        self.assertGreaterEqual(pypdf.filters.MAX_DECLARED_STREAM_LENGTH, 2_000_000_000)
        self.assertEqual(pypdf.filters.ZLIB_MAX_OUTPUT_LENGTH, 75_000_000)

    def test_huge_jpeg_is_split_and_saved_from_a_reduced_decode(self):
        # 480 000 px stand in for a 200 Mpx poster: everything above 100 000 px counts as huge.
        with mock.patch.object(photo_scan, 'LARGE_RASTER_PIXELS', 100_000), \
                mock.patch.object(photo_export, 'LARGE_RASTER_PIXELS', 100_000), \
                mock.patch.object(photo_export, 'LARGE_WHOLE_PIXELS', 50_000), \
                tempfile.TemporaryDirectory(prefix='pdf-poster-') as root:
            source = poster(root)
            obj = pypdf.PdfReader(source).pages[0]['/Resources']['/XObject']
            jpeg = next(item.get_object() for item in obj.values() if item.get_object()['/Width'] == 800)
            image, factor = photo_scan.open_large_raster(jpeg)
            with image:
                self.assertLessEqual(image.width*image.height, 100_000)
                self.assertEqual(factor, 4)
            report = run(source)
            methods = {entry['method'] for entry in active(report)}
            self.assertTrue(methods <= {'reduced_scan_crop', 'reduced_original'}, methods)
            self.assertEqual(report['errors'], [])

    def test_pillow_limits_are_restored_after_a_reduced_decode(self):
        before = Image.MAX_IMAGE_PIXELS
        with tempfile.TemporaryDirectory(prefix='pdf-poster-') as root:
            obj = pypdf.PdfReader(poster(root)).pages[0]['/Resources']['/XObject']
            jpeg = next(iter(obj.values())).get_object()
            photo_scan.open_large_raster(jpeg, budget=10_000)[0].close()
        self.assertEqual(Image.MAX_IMAGE_PIXELS, before)


class GridPanels(unittest.TestCase):
    def grid(self, shift=0):
        image = Image.new('RGB', (900, 900), 'white')
        draw = ImageDraw.Draw(image)
        for row in range(2):
            for column in range(2):
                left, top = 40+column*440, 40+row*440
                draw.rectangle((left, top, left+380, top+380), fill='#35506b')
        # A bright sky empties one corner of the top-right photo.
        draw.rectangle((480+shift, 40, 640, 140), fill='white')
        return image

    def test_a_cell_with_a_light_corner_counts_when_it_lines_up_with_the_grid(self):
        self.assertEqual(len(photo_scan.photo_panels(self.grid())), 4)

    def test_an_unaligned_doubtful_region_still_keeps_the_slide_whole(self):
        image = Image.new('RGB', (900, 900), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 40, 420, 420), fill='#35506b')
        draw.polygon([(500, 60), (860, 300), (520, 600)], fill='#35506b')
        self.assertEqual(photo_scan.photo_panels(image), [])


class RowCaptions(unittest.TestCase):
    def test_a_label_between_rows_names_the_picture_below_it(self):
        with tempfile.TemporaryDirectory(prefix='pdf-rows-') as root:
            report = run(make_pdf(root, [{'images': [{'box': (50, 60, 400, 260), 'color': '#ff0000'},
                                                    {'box': (50, 360, 400, 260), 'color': '#00ff00'}],
                                         'text': [('Вариант 1', 50, 335, 16)]}]))
            entries = sorted(active(report), key=lambda entry: entry['bbox'][1])
            self.assertNotEqual(entries[0]['title'], 'Вариант 1')
            self.assertIsNone(entries[0]['variant'])
            self.assertEqual((entries[1]['title'], entries[1]['variant']), ('Вариант 1', 1))

    def test_a_section_heading_does_not_name_captions_after_a_row_of_pictures(self):
        with tempfile.TemporaryDirectory(prefix='pdf-rows-') as root:
            report = run(make_pdf(root, [{'images': [{'box': (50, 100, 400, 240), 'color': '#ff0000'},
                                                    {'box': (50, 420, 400, 240), 'color': '#00ff00'}],
                                         'text': [('Предложения по облику', 50, 20, 30),
                                                  ('Вариант 1', 50, 76, 16), ('Вариант 4', 50, 396, 16)]}]))
            titles = [entry['title'] for entry in sorted(active(report), key=lambda entry: entry['bbox'][1])]
            self.assertEqual(titles, ['Предложения по облику — Вариант 1', 'Вариант 4'])


if __name__ == '__main__':
    unittest.main()
