"""Raster-only presentations: real local OCR, panels and conservative fallbacks."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas

from photo_export import extract, render_previews
from test_photos import exported_image  # also registers the FixtureArial font


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


def render(draw, box, shade):
    """A render with light corners (sky, snow on roofs): too doubtful for photo_panels on its own."""
    left, top, right, bottom = box
    for y in range(top, bottom):
        draw.line((left, y, right-1, y), fill=(shade, 90+(y*7) % 90, 150-(y*3) % 60))
    width, height = (right-left)*15//100, (bottom-top)*15//100
    for x0, y0 in ((left, top), (right-width, top), (left, bottom-height), (right-width, bottom-height)):
        draw.rectangle((x0, y0, x0+width-1, y0+height-1), fill='#f6f6f6')


def collage(size=(740, 910), label_size=26, grid=False):
    """Two renders stacked in one raster, as «ВИД №1 / ВИД №2» columns of a comparison slide."""
    image = Image.new('RGB', size, 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', label_size)
    width, height = size
    rows = [(8, 420), (462, 872)]
    columns = [(4, width//2-20), (width//2+20, width-4)] if grid else [(4, width-4)]
    for row, (top, bottom) in enumerate(rows):
        for column, (left, right) in enumerate(columns):
            render(draw, (left, top, right, bottom), 60+40*row+20*column)
        draw.text((width-140, bottom+6), f'ВИД №{row+1}', fill='black', font=font)
    return image


def render_row(count=4, size=(2752, 1536), separator='#e4e6ea', widths=None, gap=5):
    """Renders side by side, edge to edge, divided by thin grey lines (or touching when gap=0)."""
    image = Image.new('RGB', size, separator)
    draw = ImageDraw.Draw(image)
    width, height = size
    widths = widths or [width//count]*count
    left = 0
    for index, piece in enumerate(widths):
        box = (left+(gap if index else 0), 0, left+piece-(gap if index < len(widths)-1 else 0), height)
        if gap:
            render(draw, box, 50+30*index)
        else:
            for y in range(height):
                draw.line((box[0], y, box[2]-1, y), fill=((40+55*index) % 230, 70+(y*7+index*45) % 120, 160-(y*3+index*35) % 90))
        left += piece
    return image


def row_pdf(root, gap=5):
    root = Path(root)
    target = root/'row.pdf'
    raster = root/'row.png'
    render_row(gap=gap).save(raster)
    pdf = canvas.Canvas(str(target), pagesize=(1208, 859))
    pdf.setFont('FixtureArial', 9)
    pdf.drawString(4, 859-62, 'Перспективное изображение №1. Вариант 3')
    pdf.drawString(1000, 859-40, 'по адресу: ул. Примерная, д. 1')
    pdf.drawImage(str(raster), 0, 859-67-674, 1208, 674)
    pdf.showPage()
    pdf.save()
    return target


def collage_pdf(root):
    root = Path(root)
    target = root/'collage.pdf'
    pdf = canvas.Canvas(str(target), pagesize=(1000, 750))
    for column in range(2):
        raster = root/f'collage-{column}.png'
        collage().save(raster)
        pdf.setFont('FixtureArial', 16)
        pdf.drawString(60+470*column, 750-110, f'Вариант {column+1}')
        pdf.drawImage(str(raster), 60+470*column, 750-130-541, 440, 541)
    pdf.showPage()
    pdf.save()
    return target


class CollageEdits(unittest.TestCase):
    def variants(self, report):
        members = {}
        for index, entry in enumerate(report['images']):
            if entry['variant'] is not None and not entry.get('removed'):
                members.setdefault(str(entry['variant']), []).append(index)
        for ids in members.values():
            ids.sort(key=lambda index: report['images'][index]['pdf_page'])
        return members

    def test_auto_collage_keeps_its_whole_picture_can_be_returned_whole_and_undone(self):
        from photo_edit import apply_edits, load_report, restore_last_save
        with tempfile.TemporaryDirectory() as root:
            report = extract(collage_pdf(root))
            output = Path(report['output'])
            self.assertEqual([c['state'] for c in report['collages']], ['auto', 'auto'])
            first = report['collages'][0]
            self.assertTrue((output/first['file']).is_file())
            self.assertTrue((output/first['preview']).is_file())
            self.assertEqual(len(first['boxes']), 2)
            self.assertEqual([e.get('collage') for e in report['images']], [first['id']]*2+[report['collages'][1]['id']]*2)
            width, height = first['size']
            variants = self.variants(report)
            members = variants['1']
            variants['1'] = [len(report['images'])]
            saved, backup = apply_edits(output, {'revision': 0, 'variants': variants, 'removed': members,
                                                 'cuts': [{'collage': first['id'], 'boxes': [[0, 0, width, height]]}]})
            whole = saved['images'][-1]
            self.assertEqual(whole['size'], [width, height])
            self.assertEqual(whole['variant'], 1)
            self.assertTrue(all(saved['images'][i]['removed'] and saved['images'][i]['collage_replaced'] for i in members))
            record = next(c for c in saved['collages'] if c['id'] == first['id'])
            self.assertEqual((record['state'], record['boxes'], record['checked']), ('whole', [], True))
            with exported_image(saved, whole) as image:
                self.assertEqual(list(image.size), [width, height])
            restored, _ = restore_last_save(output, {'revision': 1})
            self.assertEqual(len(restored['images']), 4)
            self.assertEqual(next(c for c in restored['collages'] if c['id'] == first['id'])['state'], 'auto')
            self.assertEqual(load_report(output)['images'][0].get('removed'), None)

    def test_a_copy_of_a_frame_is_a_picture_of_its_own_and_survives_a_new_cut(self):
        from photo_edit import apply_edits
        with tempfile.TemporaryDirectory() as root:
            report = extract(collage_pdf(root))
            output = Path(report['output'])
            first = report['collages'][0]
            variants = self.variants(report)
            copy = len(report['images'])
            variants['2'] = variants['2']+[copy]
            saved, _ = apply_edits(output, {'revision': 0, 'variants': variants, 'removed': [], 'copies': [0]})
            self.assertNotIn('collage', saved['images'][copy])
            variants = self.variants(saved)
            frames = [len(saved['images']), len(saved['images'])+1]
            variants['1'] = frames
            cut, _ = apply_edits(output, {'revision': 1, 'variants': variants, 'removed': [0, 1],
                                          'cuts': [{'collage': first['id'], 'boxes': first['boxes']}]})
            self.assertFalse(cut['images'][copy].get('removed'))
            self.assertEqual(cut['images'][copy]['variant'], 2)
            self.assertEqual([cut['images'][index]['collage'] for index in frames], [first['id']]*2)

    def test_suspected_row_is_offered_with_equal_lines_and_cut_into_native_frames(self):
        from photo_edit import apply_edits
        with tempfile.TemporaryDirectory() as root:
            with patch('photo_scan.WindowsOcr', side_effect=AssertionError('OCR не нужен')):
                report = extract(row_pdf(root, gap=0))
            self.assertEqual(len(report['images']), 1)
            record = report['collages'][0]
            self.assertEqual(record['state'], 'suspected')
            self.assertEqual([box[0] for box in record['suggestion']], [0, 688, 1376, 2064])
            self.assertEqual(report['images'][0]['collage'], record['id'])
            variants = self.variants(report)
            new = list(range(1, 5))
            variants['3'] = new
            saved, _ = apply_edits(Path(report['output']), {
                'revision': 0, 'variants': variants, 'removed': [0],
                'cuts': [{'collage': record['id'], 'boxes': record['suggestion']}]})
            frames = saved['images'][1:]
            self.assertEqual([f['size'] for f in frames], [[688, 1536]]*4)
            self.assertEqual([f['pdf_page'] for f in frames], [1, 2, 3, 4])
            self.assertTrue(saved['images'][0]['collage_replaced'])
            self.assertEqual(next(c for c in saved['collages'] if c['id'] == record['id'])['state'], 'manual')
            original = render_row(gap=0)
            for frame, left in zip(frames, (0, 688, 1376, 2064)):
                self.assertTrue((Path(report['output'])/frame['preview']).is_file())
                with exported_image(saved, frame) as image:
                    self.assertEqual(image.convert('RGB').getpixel((100, 700)), original.getpixel((left+100, 700)))

    def test_one_photo_dismisses_the_hint_and_any_picture_can_be_cut_by_hand(self):
        from photo_edit import EditError, apply_edits
        with tempfile.TemporaryDirectory() as root:
            report = extract(row_pdf(root, gap=0))
            output = Path(report['output'])
            identifier = report['collages'][0]['id']
            saved, _ = apply_edits(output, {'revision': 0, 'variants': self.variants(report), 'removed': [],
                                            'collage_dismissed': [identifier]})
            self.assertEqual(saved['collages'][0]['state'], 'dismissed')
            self.assertNotIn('collage', saved['images'][0])
            with self.assertRaises(EditError):
                apply_edits(output, {'revision': 1, 'variants': self.variants(saved), 'removed': [],
                                     'cuts': [{'collage': identifier, 'boxes': [[0, 0, 100, 100]]}]})
            variants = self.variants(saved)
            key = next(iter(variants), None)
            misc = [] if key else [1, 2]
            if key:
                variants[key] = [1, 2]
            cut, _ = apply_edits(output, {'revision': 1, 'variants': variants, 'removed': [0], 'misc': misc,
                                          'cuts': [{'source': 0, 'boxes': [[0, 0, 1376, 1536], [1376, 0, 2752, 1536]]}]})
            manual = next(c for c in cut['collages'] if c['id'].endswith('-m0'))
            self.assertEqual((manual['state'], len(manual['boxes'])), ('manual', 2))
            self.assertTrue((output/manual['file']).is_file())
            self.assertEqual([e['size'] for e in cut['images'][1:]], [[1376, 1536]]*2)

    def test_invalid_cuts_change_nothing(self):
        from photo_edit import EditError, apply_edits
        with tempfile.TemporaryDirectory() as root:
            report = extract(collage_pdf(root))
            output = Path(report['output'])
            before = (output/'Отчёт.json').read_bytes()
            first = report['collages'][0]
            variants = self.variants(report)
            for cut, removed in (({'collage': first['id'], 'boxes': [[0, 0, 99999, 10]]}, [0, 1]),
                                 ({'collage': first['id'], 'boxes': [[0, 0, 200, 200]]}, []),
                                 ({'collage': 'нет', 'boxes': [[0, 0, 200, 200]]}, [0, 1])):
                with self.assertRaises(EditError):
                    apply_edits(output, {'revision': 0, 'variants': variants, 'removed': removed, 'cuts': [cut]})
            self.assertEqual((output/'Отчёт.json').read_bytes(), before)


class CollageCuts(unittest.TestCase):
    def test_light_gutter_splits_stacked_renders_and_lower_view_keeps_its_column_variant(self):
        with tempfile.TemporaryDirectory() as root:
            # The gutters prove these cuts; Windows OCR is neither needed nor started.
            with patch('photo_scan.WindowsOcr', side_effect=AssertionError('OCR не нужен')):
                report = extract(collage_pdf(root))
            self.assertEqual(report['warnings'], [])
            self.assertEqual([e['variant'] for e in report['images']], [1, 1, 2, 2])
            self.assertEqual([e['cut'] for e in report['images']], ['seam']*4)
            self.assertEqual([d['pages'] for d in report['documents']], [2, 2])
            self.assertIn('общая для всех кадров этой склейки', report['images'][1]['reason'])
            original = collage()
            for entry, (top, bottom) in zip(report['images'], [(8, 420), (462, 872)]*2):
                self.assertEqual(entry['method'], 'native_scan_crop')
                width, height = entry['size']
                # The light corners and the «ВИД №» captions stay outside: each piece is one render.
                self.assertLessEqual(abs(height-(bottom-top)), 6)
                self.assertGreaterEqual(width, 720)
                left, crop_top = entry['components'][0]['crop_px'][:2]
                with exported_image(report, entry) as image:
                    self.assertEqual(image.convert('RGB').getpixel((width//2, height//2)),
                                     original.getpixel((left+width//2, crop_top+height//2)))

    def test_row_of_renders_with_thin_grey_lines_is_cut_and_shares_the_heading(self):
        with tempfile.TemporaryDirectory() as root:
            with patch('photo_scan.WindowsOcr', side_effect=AssertionError('OCR не нужен')):
                report = extract(row_pdf(root))
            self.assertEqual(report['warnings'], [])
            self.assertEqual([e['variant'] for e in report['images']], [3]*4)
            # Header text above the last render must not rename that piece.
            self.assertEqual({e['title'] for e in report['images']}, {'Перспективное изображение №1. Вариант 3'})
            self.assertEqual([d['pages'] for d in report['documents']], [4])
            for entry in report['images']:
                self.assertLess(abs(entry['size'][0]-688), 12)
                self.assertEqual(entry['size'][1], 1536)

    def test_thin_line_needs_pieces_of_equal_size(self):
        from photo_scan import gutter_panels
        self.assertEqual(len(gutter_panels(render_row())), 4)
        self.assertEqual(gutter_panels(render_row(count=2, widths=[900, 1852])), [])

    def test_gutter_panels_find_a_grid_in_reading_order(self):
        from photo_scan import gutter_panels
        boxes = gutter_panels(collage(size=(1200, 910), grid=True))
        self.assertEqual(len(boxes), 4)
        self.assertEqual([box[1] < 440 for box in boxes], [True, True, False, False])
        self.assertLess(boxes[0][0], boxes[1][0])

    def test_a_large_caption_inside_the_gutter_does_not_break_it(self):
        from photo_scan import gutter_panels
        boxes = gutter_panels(collage(label_size=40))
        self.assertEqual(len(boxes), 2)
        self.assertLess(boxes[0][3], 440)
        self.assertLess(boxes[1][3], 880)

    def test_gutter_panels_keep_drawings_single_renders_and_partial_gaps_whole(self):
        from photo_scan import gutter_panels
        drawing = Image.new('RGB', (900, 900), 'white')
        draw = ImageDraw.Draw(drawing)
        for x in range(40, 860, 60):
            draw.line((x, 40, x, 400), fill='black', width=2)
            draw.line((x, 480, x, 860), fill='black', width=2)
        self.assertEqual(gutter_panels(drawing), [])
        single = Image.new('RGB', (800, 600), 'white')
        render(ImageDraw.Draw(single), (0, 120, 800, 600), 80)
        self.assertEqual(gutter_panels(single), [])
        bridged = collage()
        ImageDraw.Draw(bridged).rectangle((300, 400, 420, 480), fill='#336699')
        self.assertEqual(gutter_panels(bridged), [])


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
        # A broken sequence does not continue variant 2; such unnumbered perspective views default to variant 1.
        for next_number, expected in [(2, [2, 2, 2]), (4, [2, 1, 1])]:
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
