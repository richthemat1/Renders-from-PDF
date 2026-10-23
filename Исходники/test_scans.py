"""Raster-only presentations: real local OCR, panels and conservative fallbacks."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas

from photo_export import extract, render_previews
from test_photos import exported_image


def scan_pdf(root, labels=True, irregular=False, copies=1):
    root = Path(root)
    image = Image.new('RGB', (2000, 1500), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 40)
    boxes = [(80, 270, 930, 760), (1070, 270, 1920, 760),
             (80, 850, 930, 1380), (1070, 850, 1920, 1380)]
    if irregular:
        boxes = [(80, 270, 920, 1380), (1080, 270, 1920, 800), (1080, 880, 1920, 1380)]
    for box, color in zip(boxes, ['#447799', '#998844', '#669955', '#995577']):
        draw.rectangle(box, fill=color)
    if labels:
        draw.text((80, 200), 'Вариант 3', fill='black', font=font)
        draw.text((1070, 200), 'Вариант 4', fill='black', font=font)
    raster = root/'scan.png'
    image.save(raster)
    image.close()
    target = root/'scan.pdf'
    pdf = canvas.Canvas(str(target), pagesize=(1000, 750))
    for _ in range(copies):
        pdf.drawImage(str(raster), 0, 0, 1000, 750)
        pdf.showPage()
    pdf.save()
    return target


class ScanExport(unittest.TestCase):
    def test_fractional_scan_crop_dimensions_keep_previews_within_limit(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            raster = root/'image.png'
            Image.new('RGB', (1200, 692), '#446688').save(raster)
            pdf = canvas.Canvas(str(root/'crop.pdf'), pagesize=(539.9184, 311.3889))
            pdf.drawImage(str(raster), 0, 0, 539.9184, 311.3889)
            pdf.showPage()
            pdf.save()
            report = {'images': [{'file': 'crop.pdf', 'pdf_page': 1, 'size': [1200, 692]}]}
            render_previews(root, report)
            with Image.open(root/report['images'][0]['preview']) as image:
                self.assertLessEqual(image.width, 640)
                self.assertLessEqual(image.height, 480)

    def numbered_views(self, root, next_number=2):
        root = Path(root)
        target = root/'views.pdf'
        pdf = canvas.Canvas(str(target), pagesize=(1000, 750))
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 34)
        for index in range(2):
            with Image.new('RGB', (2000, 1500), 'white') as image:
                draw = ImageDraw.Draw(image)
                labels = [('Вариант 2. Перспективное изображение №1', 80)] if index == 0 else [
                    (f'Перспективное изображение №{next_number}', 80),
                    (f'Перспективное изображение №{next_number+1}', 1070)]
                for text, x in labels:
                    draw.text((x, 200), text, fill='black', font=font)
                    draw.rectangle((x, 270, x+830, 1380), fill='#446688')
                raster = root/f'view-{index}.png'
                image.save(raster)
            pdf.drawImage(str(raster), 0, 0, 1000, 750)
            pdf.showPage()
        pdf.save()
        return target

    def test_numbered_ocr_views_continue_the_variant_only_in_sequence(self):
        for next_number, expected in [(2, [2, 2, 2]), (4, [2, None, None])]:
            with self.subTest(next_number=next_number), tempfile.TemporaryDirectory() as root:
                report = extract(self.numbered_views(root, next_number))
                self.assertEqual([e['variant'] for e in report['images']], expected)

    def test_scan_columns_name_both_rows_and_keep_native_pixels(self):
        with tempfile.TemporaryDirectory() as root:
            source = scan_pdf(root)
            before = source.read_bytes()
            report = extract(source)
            self.assertEqual([e['variant'] for e in report['images']], [3, 4, 3, 4])
            self.assertEqual([d['pages'] for d in report['documents']], [2, 2])
            self.assertEqual(report['source_components'], 1)
            self.assertEqual(report['scan_pages'][0]['panels'], 4)
            for entry, color in zip(report['images'], [(68, 119, 153), (153, 136, 68),
                                                       (102, 153, 85), (153, 85, 119)]):
                self.assertEqual(entry['method'], 'native_scan_crop')
                with exported_image(report, entry) as image:
                    self.assertEqual(image.convert('RGB').getpixel((100, 100)), color)
                    self.assertEqual(list(image.size), entry['size'])
            self.assertEqual(source.read_bytes(), before)

    def test_scan_layout_is_not_a_fixed_four_panel_grid(self):
        with tempfile.TemporaryDirectory() as root:
            report = extract(scan_pdf(root, irregular=True))
            self.assertEqual([e['variant'] for e in report['images']], [3, 4, 4])
            self.assertEqual(report['scan_pages'][0]['panels'], 3)

    def test_ocr_reads_successive_scanned_pages_without_file_lock_errors(self):
        with tempfile.TemporaryDirectory() as root:
            report = extract(scan_pdf(root, copies=3))
            self.assertEqual(len(report['images']), 12)
            self.assertEqual(report['warnings'], [])
            self.assertEqual([s['panels'] for s in report['scan_pages']], [4, 4, 4])

    def test_scan_without_readable_labels_keeps_the_original(self):
        with tempfile.TemporaryDirectory() as root:
            report = extract(scan_pdf(root, labels=False))
            self.assertEqual(len(report['images']), 1)
            self.assertEqual(report['images'][0]['size'], [2000, 1500])
            self.assertIsNone(report['images'][0]['variant'])

    def test_missing_ocr_keeps_original_and_explains_the_problem(self):
        with tempfile.TemporaryDirectory() as root:
            source = scan_pdf(root)
            with patch('photo_scan.WindowsOcr.__enter__', side_effect=RuntimeError('Нет русского OCR')):
                report = extract(source)
            self.assertEqual(len(report['images']), 1)
            self.assertEqual(report['images'][0]['size'], [2000, 1500])
            self.assertTrue(any('OCR' in w['reason'] for w in report['warnings']))

    def test_failed_ocr_request_cannot_shift_labels_to_the_next_page(self):
        with tempfile.TemporaryDirectory() as root:
            source = scan_pdf(root, copies=2)
            with patch('photo_scan.WindowsOcr.recognise', side_effect=RuntimeError('OCR timeout')) as recognise:
                report = extract(source)
            self.assertEqual(recognise.call_count, 1)
            self.assertEqual([e['variant'] for e in report['images']], [None, None])
            self.assertEqual(len(report['warnings']), 2)

    def test_cancel_during_ocr_preserves_source_and_creates_no_result(self):
        with tempfile.TemporaryDirectory() as root:
            source = scan_pdf(root, copies=2)
            output = Path(root)/'result'
            def progress(event):
                if event['stage'] == 'ocr' and event['current'] == 2:
                    raise RuntimeError('Отменено')
            with self.assertRaisesRegex(RuntimeError, 'Отменено'):
                extract(source, output, progress)
            self.assertTrue(source.exists())
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
