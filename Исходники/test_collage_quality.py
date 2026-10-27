"""Compound image quality without private presentation fixtures."""
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas

from photo_export import extract, inspect as inspect_pdf
from test_photos import exported_image


COLORS = ['#447799', '#998844', '#669955', '#995577', '#557788', '#886644']
PANEL_BOXES = [(10, 70, 590, 350), (810, 70, 1390, 350),
               (10, 390, 590, 670), (810, 390, 1390, 670),
               (10, 710, 590, 990), (810, 710, 1390, 990)]


def collage_pdf(root, *, labels=True, table=False, transform=None, background='white', stagger=False):
    """The collage occupies 63% of a text-bearing page with another raster."""
    root = Path(root)
    raster = root/'collage.png'
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 28)
    with Image.new('RGB', (1400, 1000), background) as image:
        draw = ImageDraw.Draw(image)
        for index, (box, color) in enumerate(zip(PANEL_BOXES, COLORS)):
            if stagger and index == 0:
                box = (box[0], box[1]+5, box[2], box[3])
            draw.rectangle(box, fill=color)
            if index >= 4:
                # A small, bright cloud in one corner is still a rectangular photo.
                draw.rectangle((box[2]-18, box[1], box[2], box[1]+18), fill=background)
            if table:
                for row in range(5):
                    draw.text((box[0]+20, box[1]+20+row*45),
                              f'Площадь помещения {row+1}', fill='black', font=font)
        if labels:
            for x, number in [(200, 1), (1000, 2)]:
                draw.rectangle((x-12, 4, x+178, 50), outline='black', width=4)
                draw.text((x, 10), f'Вариант {number}', fill='black', font=font)
        image.save(raster)
    logo = root/'logo.png'
    with Image.new('RGB', (180, 180), '#222222') as image:
        image.save(logo)
    target = root/'compound.pdf'
    pdf = canvas.Canvas(str(target), pagesize=(1000, 800))
    pdf.setFont('Helvetica', 16)
    pdf.drawString(70, 755, 'Selectable text outside the compound image')
    pdf.drawImage(str(logo), 10, 750, 30, 30)
    pdf.saveState()
    if transform == 'mirror':
        pdf.translate(1000, 0)
        pdf.scale(-1, 1)
    elif transform == 'rotate':
        pdf.translate(800, 0)
        pdf.rotate(90)
    pdf.drawImage(str(raster), 80, 60, 840, 600)
    pdf.restoreState()
    pdf.showPage()
    pdf.save()
    return target


class CollageQuality(unittest.TestCase):
    def test_non_full_page_collage_keeps_native_panels_and_column_variants(self):
        with tempfile.TemporaryDirectory() as root:
            source = collage_pdf(root)
            original = source.read_bytes()
            report = extract(source)
            panels = [entry for entry in report['images'] if entry['method'] == 'native_scan_crop']
            self.assertEqual(len(panels), 6)
            self.assertEqual([entry['variant'] for entry in panels], [1, 2, 1, 2, 1, 2])
            self.assertEqual(report['scan_pages'][0]['panels'], 6)
            for entry, expected_box, color in zip(panels, PANEL_BOXES, COLORS):
                component = entry['components'][0]
                self.assertEqual(component['source_bbox'], [80, 140, 920, 740])
                for actual, expected in zip(component['crop_px'], expected_box):
                    self.assertLessEqual(abs(actual-expected), 3)
                crop = component['crop_px']
                expected_page = [80+crop[0]*.6, 140+crop[1]*.6,
                                 80+crop[2]*.6, 140+crop[3]*.6]
                for actual, expected in zip(entry['bbox'], expected_page):
                    self.assertAlmostEqual(actual, expected, places=2)
                with exported_image(report, entry) as image:
                    self.assertEqual(list(image.size), [crop[2]-crop[0], crop[3]-crop[1]])
                    self.assertEqual(image.convert('RGB').getpixel((100, 100)),
                                     tuple(bytes.fromhex(color.lstrip('#'))))
            self.assertEqual(source.read_bytes(), original)

    def test_partial_collage_preserves_external_text_and_other_image(self):
        with tempfile.TemporaryDirectory() as root:
            pages = inspect_pdf(collage_pdf(root))
            page = pages[0]
            self.assertEqual(len([im for im in page['images'] if 'scan_crop_px' in im]), 6)
            self.assertTrue(any(line['text'] == 'Selectable text outside the compound image'
                                for line in page['lines']))
            self.assertTrue(any(line.get('text_source') == 'windows_ocr'
                                and line['text'] == 'Вариант 2' for line in page['lines']))
            other = [im for im in page['images'] if 'scan_crop_px' not in im]
            self.assertEqual(len(other), 1)
            self.assertEqual(list(other[0]['srcsize']), [180, 180])

    def test_light_uniform_background_is_not_mistaken_for_a_solid_photo(self):
        with tempfile.TemporaryDirectory() as root:
            report = extract(collage_pdf(root, background='#ededed'))
            panels = [entry for entry in report['images'] if entry['method'] == 'native_scan_crop']
            self.assertEqual([entry['variant'] for entry in panels], [1, 2, 1, 2, 1, 2])

    def test_small_vertical_offset_keeps_left_to_right_panel_order(self):
        with tempfile.TemporaryDirectory() as root:
            report = extract(collage_pdf(root, stagger=True))
            panels = [entry for entry in report['images'] if entry['method'] == 'native_scan_crop']
            self.assertEqual([entry['variant'] for entry in panels], [1, 2, 1, 2, 1, 2])

    def test_unlabelled_partial_collage_keeps_original_with_review_warning(self):
        with tempfile.TemporaryDirectory() as root:
            report = extract(collage_pdf(root, labels=False))
            self.assertFalse(any(entry['method'] == 'native_scan_crop' for entry in report['images']))
            self.assertTrue(any(entry['size'] == [1400, 1000] for entry in report['images']))
            self.assertTrue(any(warning.get('page') == 1 for warning in report['warnings']))

    def test_colored_table_cells_are_not_exported_as_photographs(self):
        with tempfile.TemporaryDirectory() as root:
            report = extract(collage_pdf(root, table=True))
            self.assertFalse(any(entry['method'] == 'native_scan_crop' for entry in report['images']))
            self.assertTrue(any(entry['size'] == [1400, 1000] for entry in report['images']))

    def test_partial_collage_transform_keeps_original_and_warns(self):
        for transform in ('mirror', 'rotate'):
            with self.subTest(transform=transform), tempfile.TemporaryDirectory() as root:
                source = collage_pdf(root, transform=transform)
                original = source.read_bytes()
                report = extract(source)
                self.assertFalse(any(entry['method'] == 'native_scan_crop' for entry in report['images']))
                self.assertTrue(any(entry['size'] == [1400, 1000] for entry in report['images']))
                self.assertTrue(any('OCR' in warning['reason'] for warning in report['warnings']))
                self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
