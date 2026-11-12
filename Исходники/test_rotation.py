"""Turned pages and cards: a scanned sheet lying on its side is read upright, and «Повернуть» turns a card
where it stands — the PDF page turns, the pixels stay as they were."""
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from photo_edit import EditError, apply_edits, load_report, pages_info, restore_last_save
from photo_export import extract
from photo_scan import shown_transpose, turned_matrix, TURNS
from test_photos import EditFixture, exported_image

PANELS = [(80, 330, 930, 800), (1070, 330, 1920, 800), (80, 900, 930, 1400), (1070, 900, 1920, 1400)]
PANEL_COLORS = [(68, 119, 153), (153, 136, 68), (102, 153, 85), (153, 85, 119)]
MARK = (20, 20, 20)


def slide():
    """A scanned slide as it should read: a title, two variant labels and four photographs."""
    image = Image.new('RGB', (2000, 1500), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 40)
    draw.text((80, 80), 'Офисно-деловой центр по адресу: Москва, Кунцево', fill='black', font=font)
    draw.text((80, 140), 'Перспективные изображения объекта', fill='black', font=font)
    for box, color in zip(PANELS, PANEL_COLORS):
        draw.rectangle(box, fill=color)
        # A dark mark in the top left corner tells which way up a photograph came out.
        draw.rectangle((box[0], box[1], box[0]+120, box[1]+60), fill=MARK)
    draw.text((80, 260), 'Вариант 3', fill='black', font=font)
    draw.text((1070, 260), 'Вариант 4', fill='black', font=font)
    return image


def sideways_pdf(root, how):
    """The slide scanned on its side, as scanners and offices store it.

    'pixels': the pixels themselves lie on their side and fill a portrait page.
    'rotate': the pixels are stored upside down on a landscape page that /Rotate 270 shows on its side,
    as in a real letter-and-slides scan."""
    root = Path(root)
    raster, target = root/'sheet.png', root/f'{how}.pdf'
    with slide() as image:
        if how == 'pixels':
            with image.transpose(TURNS[90]) as stored:
                stored.save(raster)
            pdf = canvas.Canvas(str(target), pagesize=(595, 794))
            pdf.drawImage(str(raster), 0, 0, 595, 794)
        else:
            with image.transpose(Image.Transpose.ROTATE_180) as stored:
                stored.save(raster)
            pdf = canvas.Canvas(str(target), pagesize=(794, 595))
            pdf.drawImage(str(raster), 0, 0, 794, 595)
        pdf.showPage()
        pdf.save()
    if how == 'rotate':
        writer = PdfWriter(clone_from=str(target))
        writer.pages[0].rotate(270)
        writer.write(str(target))
        writer.close()
    return target


def pdf_rotation(output, entry):
    with PdfReader(Path(output)/entry['file']) as reader:
        return reader.pages[entry['pdf_page']-1].rotation


def marked_corner(image):
    """The mark is in the top left corner of an upright photograph, and in no other."""
    with image.convert('RGB') as rgb:
        corners = [(20, 20), (rgb.width-20, 20), (20, rgb.height-20), (rgb.width-20, rgb.height-20)]
        return [rgb.getpixel(point) == MARK for point in corners] == [True, False, False, False]


class Geometry(unittest.TestCase):
    def test_placement_tells_how_pixels_are_shown(self):
        self.assertIsNone(shown_transpose([600, 0, 0, 400]))
        # A matrix of a real scan: its page draws the stored pixels turned a quarter left.
        self.assertEqual(shown_transpose([0.0, 841.68, -595.2, 0.0]), Image.Transpose.ROTATE_90)
        self.assertEqual(shown_transpose([-841.68, 0, 0, -595.2]), Image.Transpose.ROTATE_180)
        self.assertEqual(shown_transpose([0, -500, 300, 0]), Image.Transpose.ROTATE_270)
        self.assertEqual(shown_transpose([-600, 0, 0, 400]), Image.Transpose.FLIP_LEFT_RIGHT)
        self.assertIs(shown_transpose([600, 100, 0, 400]), False)
        # Turning the page turns the placement with it: four quarter turns come back.
        matrix = [0.0, 841.68, -595.2, 0.0]
        self.assertEqual(shown_transpose(turned_matrix(matrix, 270)), Image.Transpose.ROTATE_180)
        turned = matrix
        for _ in range(4):
            turned = turned_matrix(turned, 90)
        self.assertEqual(turned, matrix)


class SidewaysScans(unittest.TestCase):
    def check_upright(self, report):
        self.assertEqual([e['variant'] for e in report['images']], [3, 4, 3, 4])
        self.assertEqual(report['scan_pages'][0]['panels'], 4)
        for entry, color in zip(report['images'], PANEL_COLORS):
            with exported_image(report, entry) as image:
                self.assertEqual(image.convert('RGB').getpixel((image.width//2, image.height-20)), color)
                self.assertTrue(marked_corner(image), entry['size'])
                self.assertEqual(list(image.size), entry['size'])
                # Photographs of the slide are landscape, as they were drawn.
                self.assertGreater(image.width, image.height)

    def test_scan_whose_pixels_lie_on_their_side_is_read_upright(self):
        with tempfile.TemporaryDirectory() as root:
            report = extract(sideways_pdf(root, 'pixels'))
            self.assertEqual(report['upright_pages'], {'1': 270})
            self.assertEqual(report['warnings'], [])
            self.check_upright(report)

    def test_scan_shown_on_its_side_by_page_rotation_is_read_upright(self):
        with tempfile.TemporaryDirectory() as root:
            source = sideways_pdf(root, 'rotate')
            before = source.read_bytes()
            report = extract(source)
            self.assertEqual(report['upright_pages'], {'1': 270})
            self.check_upright(report)
            self.assertEqual(source.read_bytes(), before)
            # The source page is shown the way it was read: landscape in the cutout editor too.
            info = pages_info(Path(report['output']))
            width, height = info['pages'][0]['size']
            self.assertGreater(width, height)

    def test_page_that_reads_as_it_stands_is_not_turned(self):
        with tempfile.TemporaryDirectory() as root:
            raster, target = Path(root)/'sheet.png', Path(root)/'upright.pdf'
            with slide() as image:
                image.save(raster)
            pdf = canvas.Canvas(str(target), pagesize=(794, 595))
            pdf.drawImage(str(raster), 0, 0, 794, 595)
            pdf.showPage()
            pdf.save()
            report = extract(target)
            self.assertNotIn('upright_pages', report)
            self.check_upright(report)

    def test_sideways_picture_kept_whole_reports_its_size_as_shown(self):
        with tempfile.TemporaryDirectory() as root:
            raster, target = Path(root)/'photo.png', Path(root)/'photo.pdf'
            Image.new('RGB', (900, 500), '#557799').save(raster)
            pdf = canvas.Canvas(str(target), pagesize=(600, 800))
            pdf.setFont('Helvetica', 12)
            pdf.drawString(40, 760, 'Photo')
            pdf.saveState()
            # The photograph is drawn turned a quarter: tall on the page.
            pdf.translate(500, 100)
            pdf.rotate(90)
            pdf.drawImage(str(raster), 0, 0, 450, 250)
            pdf.restoreState()
            pdf.showPage()
            pdf.save()
            report = extract(target)
            entry = report['images'][0]
            self.assertEqual(entry['size'], [500, 900])
            with PdfReader(Path(report['output'])/entry['file']) as reader:
                box = reader.pages[0].mediabox
                self.assertLess(float(box.width), float(box.height))


class TurnCards(EditFixture, unittest.TestCase):
    def variants(self, report):
        members = {}
        for index, entry in enumerate(report['images']):
            if entry['variant'] is not None and not entry.get('removed'):
                members.setdefault(str(entry['variant']), []).append(index)
        for ids in members.values():
            ids.sort(key=lambda index: report['images'][index]['pdf_page'])
        return members

    def turn(self, output, turns, report=None):
        report = report or load_report(output)
        return apply_edits(output, {'revision': report['revision'], 'variants': self.variants(report),
                                    'turns': {str(index): turn for index, turn in turns.items()}})

    def test_turn_in_a_variant_turns_its_page_and_keeps_the_pixels(self):
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            before = load_report(output)
            with PdfReader(output/'Вариант 1/Вариант 1.pdf') as reader:
                stored = reader.pages[0].images[0].data
            with Image.open(output/before['images'][ids['red']]['preview']) as preview:
                thumbnail = preview.size
            report, backup = self.turn(output, {ids['red']: 90})
            red = report['images'][ids['red']]
            self.assertEqual((red['turn'], red['size'], red['pdf_page']), (90, [400, 600], 1))
            self.assertEqual(pdf_rotation(output, red), 90)
            with PdfReader(output/'Вариант 1/Вариант 1.pdf') as reader:
                self.assertEqual(reader.pages[0].images[0].data, stored)
                self.assertEqual(reader.pages[1].rotation, 0)
            self.assertEqual(self.colors(output/'Вариант 1/Вариант 1.pdf'), ['red', 'green'])
            with Image.open(output/red['preview']) as preview:
                self.assertEqual(preview.size, thumbnail[::-1])
            self.assertEqual(report['edits'][-1]['turns'], [{'image': ids['red'], 'turn': 90}])
            self.assertTrue((output/backup/'Вариант 1/Вариант 1.pdf').is_file())
            # A second turn adds up; a full turn leaves no turn behind.
            report, _ = self.turn(output, {ids['red']: 270})
            red = report['images'][ids['red']]
            self.assertNotIn('turn', red)
            self.assertEqual(red['size'], [600, 400])
            self.assertEqual(pdf_rotation(output, red), 0)

    def test_turn_outside_variants_rewrites_its_own_pdf_and_undo_brings_it_back(self):
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            yellow = load_report(output)['images'][ids['yellow']]
            path = output/yellow['file']
            before = path.read_bytes()
            report, _ = self.turn(output, {ids['yellow']: 180})
            entry = report['images'][ids['yellow']]
            self.assertEqual(entry['file'], yellow['file'])
            self.assertEqual((entry['turn'], entry['size']), (180, [600, 400]))
            self.assertEqual(pdf_rotation(output, entry), 180)
            self.assertNotEqual(entry['sha256'], yellow['sha256'])
            restored, _ = restore_last_save(output, {'revision': report['revision']})
            self.assertEqual(path.read_bytes(), before)
            self.assertNotIn('turn', restored['images'][ids['yellow']])

    def test_crop_of_a_turned_card_is_cut_from_the_picture_as_shown(self):
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            report, _ = self.turn(output, {ids['red']: 90})
            # The box is in the turned picture, 400 wide and 600 tall.
            frame = len(report['images'])
            report, _ = apply_edits(output, {'revision': report['revision'],
                                             'cuts': [{'source': ids['red'], 'boxes': [[0, 0, 200, 500]]}],
                                             'removed': [ids['red']], 'variants': {'1': [frame, ids['green']], '2': [ids['blue']]}})
            entry = report['images'][frame]
            self.assertNotIn('turn', entry)
            self.assertEqual(entry['size'], [200, 500])
            self.assertEqual(pdf_rotation(output, entry), 0)
            with PdfReader(output/'Вариант 1/Вариант 1.pdf') as reader:
                self.assertEqual(reader.pages[0].images[0].image.size, (200, 500))
            # Cropped again, the frame comes from the same turned picture.
            record = next(item for item in report['collages'] if item['id'].endswith(f'-m{ids["red"]}'))
            self.assertEqual(record['size'], [400, 600])

    def test_turned_crop_keeps_its_turn_when_cropped_again(self):
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            report, _ = apply_edits(output, {'revision': 0, 'cuts': [{'source': ids['red'], 'boxes': [[100, 50, 400, 300]]}],
                                             'removed': [ids['red']], 'variants': {'1': [4, ids['green']], '2': [ids['blue']]}})
            report, _ = self.turn(output, {4: 90}, report)
            self.assertEqual(report['images'][4]['size'], [250, 300])
            record = next(item for item in report['collages'] if item['id'].endswith(f'-m{ids["red"]}'))
            report, _ = apply_edits(output, {'revision': report['revision'],
                                             'cuts': [{'collage': record['id'], 'boxes': [[0, 0, 600, 200]]}],
                                             'removed': [4], 'variants': {'1': [5, ids['green']], '2': [ids['blue']]}})
            entry = report['images'][5]
            self.assertEqual((entry['turn'], entry['size']), (90, [200, 600]))
            self.assertEqual(pdf_rotation(output, entry), 90)
            with Image.open(output/entry['preview']) as preview:
                self.assertLess(preview.width, preview.height)

    def test_turned_card_is_cut_out_from_a_page_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            self.turn(output, {ids['red']: 90})
            pictures = {picture['image'] for picture in pages_info(output)['pictures']}
            self.assertNotIn(ids['red'], pictures)
            self.assertIn(ids['green'], pictures)

    def test_wrong_turns_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            for turns in ({str(ids['red']): 45}, {str(ids['red']): True}, {'x': 90}, [ids['red']], {'99': 90}):
                with self.subTest(turns=turns), self.assertRaises(EditError):
                    apply_edits(output, {'revision': 0, 'variants': {'1': [0, 1], '2': [2]}, 'turns': turns})
            with self.assertRaisesRegex(EditError, 'Убранное изображение'):
                apply_edits(output, {'revision': 0, 'variants': {'1': [1], '2': [2]}, 'removed': [ids['red']],
                                     'turns': {str(ids['red']): 90}})
            self.assertEqual(load_report(output)['revision'], 0)


if __name__ == '__main__':
    unittest.main()
