"""Cutouts: parts of source pages cut by hand, in the pixels of their picture or as a page snapshot."""
from io import BytesIO
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image, ImageChops, ImageDraw
from reportlab.pdfgen import canvas

from photo_export import extract
from test_photos import exported_image  # also registers the FixtureArial font
from test_scans import render

PLAN = (560, 420, 780, 580)


def render_with_plan():
    """A render with a site plan baked into its lower right corner, as one raster."""
    image = Image.new('RGB', (800, 600), 'white')
    draw = ImageDraw.Draw(image)
    render(draw, (0, 0, 800, 600), 90)
    draw.rectangle(PLAN, fill='white', outline='#9aa3b5', width=3)
    for box in ((580, 450, 640, 500), (660, 440, 760, 490), (590, 520, 700, 565)):
        draw.rectangle(box, fill='#c9cfe0')
    return image


def cutout_pdf(root):
    """Page 1: the render with its plan and a vector scheme beside it; page 2: text only."""
    root = Path(root)
    raster = root/'render.png'
    render_with_plan().save(raster)
    target = root/'cutouts.pdf'
    pdf = canvas.Canvas(str(target), pagesize=(1000, 750))
    pdf.setFont('FixtureArial', 16)
    pdf.drawString(60, 750-110, 'Перспективное изображение №1. Вариант 2')
    pdf.drawImage(str(raster), 60, 750-130-450, 600, 450)
    pdf.setFillColorRGB(.3, .6, .4)
    pdf.rect(700, 750-400, 240, 250, fill=1, stroke=0)
    pdf.setFillColorRGB(.9, .5, .2)
    pdf.rect(760, 750-300, 80, 60, fill=1, stroke=0)
    pdf.showPage()
    pdf.setFont('FixtureArial', 16)
    pdf.drawString(60, 750-110, 'Технико-экономические показатели')
    pdf.showPage()
    pdf.save()
    return target


def variants(report):
    members = {}
    for index, entry in enumerate(report['images']):
        if entry['variant'] is not None and not entry.get('removed'):
            members.setdefault(str(entry['variant']), []).append(index)
    for ids in members.values():
        ids.sort(key=lambda index: report['images'][index]['pdf_page'])
    return members


# The render without its plan, in shares of the page: x 60..480 of 1000 pt, y 130..580 of 750 pt.
RENDER = [.06, 130/750, .48, 580/750]


class Cutouts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdf-photos-cutouts-')
        self.addCleanup(self.temp.cleanup)
        self.report = extract(cutout_pdf(self.temp.name))
        self.output = Path(self.report['output'])
        self.picture = next(index for index, entry in enumerate(self.report['images']) if not entry.get('removed'))

    def cut(self, rects, revision=0):
        from photo_edit import apply_edits
        members = variants(self.report)
        new = list(range(len(self.report['images']), len(self.report['images'])+len(rects)))
        members['2'] = new
        return apply_edits(self.output, {'revision': revision, 'variants': members, 'removed': [self.picture],
                                         'cutouts': [{'source': self.picture, 'rects': rects}]})

    def test_pages_show_their_size_snapshot_and_the_picture_that_may_be_cut(self):
        from photo_edit import pages_info
        info = pages_info(self.output)
        self.assertEqual([page['size'] for page in info['pages']], [[1000, 750]]*2)
        # 300 dpi: 1000 pt is 4167 px.
        self.assertEqual(info['pages'][0]['snapshot'], [4167, 3125])
        self.assertEqual(len(info['pictures']), 1)
        picture = info['pictures'][0]
        self.assertEqual((picture['image'], picture['page'], picture['size']), (self.picture, 1, [800, 600]))
        for actual, expected in zip(picture['area'], [.06, 130/750, .66, 580/750]):
            self.assertAlmostEqual(actual, expected, places=4)

    def test_a_cutout_replaces_its_picture_in_native_pixels_and_keeps_the_picture(self):
        saved, backup = self.cut([RENDER])
        piece = saved['images'][-1]
        self.assertEqual((piece['cutout'], piece['crop_px'], piece['size']), (f'1-v{self.picture}', [0, 0, 560, 600], [560, 600]))
        self.assertEqual((piece['variant'], piece['method'], piece['cut']), (2, 'manual_crop', 'cutout'))
        self.assertTrue((self.output/piece['preview']).is_file())
        original = saved['images'][self.picture]
        self.assertTrue(original['removed'])
        self.assertEqual(original['cutout_replaced'], piece['cutout'])
        record = saved['cutouts'][0]
        self.assertEqual((record['kind'], record['image'], record['size']), ('image', self.picture, [800, 600]))
        self.assertTrue((self.output/record['file']).is_file())
        source = render_with_plan()
        with exported_image(saved, piece) as image:
            self.assertEqual(image.size, (560, 600))
            for point in ((10, 10), (300, 300), (555, 595)):
                self.assertEqual(image.convert('RGB').getpixel(point), source.getpixel(point))
        self.assertEqual(saved['edits'][-1]['cutouts'], [{'cutout': record['id'], 'pieces': [len(saved['images'])-1],
                                                          'replaced': []}])

    def test_cutouts_are_cut_again_returned_and_the_save_undone(self):
        from photo_edit import apply_edits, restore_last_save
        saved, _ = self.cut([RENDER])
        first = len(saved['images'])-1
        record = saved['cutouts'][0]['id']
        members = variants(saved)
        start = len(saved['images'])
        members['2'] = [start, start+1]
        top, bottom = RENDER[1], RENDER[3]
        middle = (top+bottom)/2
        recut, _ = apply_edits(self.output, {
            'revision': 1, 'variants': members, 'removed': [first],
            'cutouts': [{'record': record, 'rects': [[RENDER[0], top, RENDER[2], middle], [RENDER[0], middle, RENDER[2], bottom]]}]})
        self.assertEqual([entry['crop_px'] for entry in recut['images'][start:]], [[0, 0, 560, 300], [0, 300, 560, 600]])
        self.assertEqual(recut['images'][first]['cutout_replaced'], record)
        members = variants(recut)
        members['2'] = [self.picture]
        returned, _ = apply_edits(self.output, {'revision': 2, 'variants': members, 'removed': [start, start+1],
                                                'restored': [self.picture], 'cutouts': [{'record': record, 'rects': []}]})
        picture = returned['images'][self.picture]
        self.assertFalse(picture.get('removed'))
        self.assertNotIn('cutout_replaced', picture)
        self.assertEqual(picture['variant'], 2)
        self.assertEqual([index for index, entry in enumerate(returned['images']) if not entry.get('removed')], [self.picture])
        undone, _ = restore_last_save(self.output, {'revision': 3})
        self.assertEqual([index for index, entry in enumerate(undone['images']) if not entry.get('removed')], [start, start+1])

    def test_a_frame_where_there_is_no_picture_is_a_page_snapshot(self):
        from photo_edit import apply_edits
        scheme = [.7, 150/750, .94, 400/750]
        start = len(self.report['images'])
        saved, _ = apply_edits(self.output, {'revision': 0, 'variants': variants(self.report), 'removed': [],
                                             'misc': [start], 'cutouts': [{'page': 1, 'rects': [scheme]}]})
        piece = saved['images'][start]
        self.assertEqual((piece['method'], piece['variant'], piece['folder'], piece['cutout']),
                         ('page_render', None, 'Без подписи', '1-p'))
        self.assertEqual(piece['size'], [1000, 1042])
        self.assertTrue((self.output/piece['file']).is_file())
        with exported_image(saved, piece) as image:
            # The green scheme and the orange site drawn with vectors, rendered at 300 dpi.
            self.assertEqual(image.size, (1000, 1042))
            red, green, blue = image.convert('RGB').getpixel((50, 50))
            self.assertTrue(green > red and green > blue)
            red, green, blue = image.convert('RGB').getpixel((500, 600))
            self.assertTrue(red > green > blue)
        self.assertEqual(saved['cutouts'][0]['kind'], 'page')
        self.assertFalse(saved['images'][self.picture].get('removed'))

    def test_invalid_cutouts_change_nothing(self):
        from photo_edit import EditError, apply_edits
        before = (self.output/'Отчёт.json').read_bytes()
        members = variants(self.report)
        start = len(self.report['images'])
        replaced = dict(members, **{'2': [start]})
        for payload in (
                # Past the picture, too small, outside the page, the picture kept beside its cutout.
                {'variants': replaced, 'removed': [self.picture], 'cutouts': [{'source': self.picture, 'rects': [[.05, .1, .9, .9]]}]},
                {'variants': replaced, 'removed': [self.picture], 'cutouts': [{'source': self.picture, 'rects': [[.1, .2, .1005, .3]]}]},
                {'variants': replaced, 'removed': [self.picture], 'cutouts': [{'source': self.picture, 'rects': [[.1, .2, 1.2, .3]]}]},
                {'variants': dict(members, **{'2': members['2']+[start]}), 'removed': [],
                 'cutouts': [{'source': self.picture, 'rects': [RENDER]}]},
                {'variants': members, 'removed': [], 'misc': [start], 'cutouts': [{'page': 9, 'rects': [RENDER]}]},
                {'variants': members, 'removed': [], 'cutouts': [{'record': 'нет', 'rects': []}]}):
            with self.assertRaises(EditError):
                apply_edits(self.output, {'revision': 0, **payload})
        self.assertEqual((self.output/'Отчёт.json').read_bytes(), before)

    def test_without_the_source_presentation_pages_cannot_be_shown_or_snapshot(self):
        from photo_edit import EditError, apply_edits, pages_info
        moved = Path(self.temp.name)/'moved.pdf'
        shutil.move(self.report['source'], moved)
        with self.assertRaisesRegex(EditError, 'Исходный PDF недоступен'):
            pages_info(self.output)
        start = len(self.report['images'])
        with self.assertRaisesRegex(EditError, 'Исходный PDF недоступен'):
            apply_edits(self.output, {'revision': 0, 'variants': variants(self.report), 'removed': [],
                                      'misc': [start], 'cutouts': [{'page': 1, 'rects': [[.7, .2, .9, .5]]}]})
        moved.write_bytes(moved.read_bytes()+b'%')
        shutil.move(moved, self.report['source'])
        with self.assertRaisesRegex(EditError, 'изменился после обработки'):
            pages_info(self.output)


class JpegCutout(unittest.TestCase):
    def test_a_cutout_of_a_jpeg_picture_keeps_the_pixels_of_the_jpeg_in_the_presentation(self):
        # pypdf re-encodes JPEGs it extracts; a noisy photo shows any difference from the original stream.
        from photo_edit import apply_edits
        with tempfile.TemporaryDirectory(prefix='pdf-photos-jpeg-cut-') as root:
            photo = Path(root)/'photo.jpg'
            with Image.effect_noise((800, 600), 70) as noise, noise.convert('RGB') as image:
                image.save(photo, 'JPEG', quality=85)
            source = Path(root)/'photo.pdf'
            pdf = canvas.Canvas(str(source), pagesize=(1000, 750))
            pdf.setFont('FixtureArial', 16)
            pdf.drawString(60, 750-110, 'Перспективное изображение №1. Вариант 2')
            pdf.drawImage(str(photo), 60, 750-130-450, 600, 450)
            pdf.showPage()
            pdf.save()
            report = extract(source)
            output = Path(report['output'])
            picture = next(index for index, entry in enumerate(report['images']) if not entry.get('removed'))
            members = variants(report)
            members['2'] = [len(report['images'])]
            saved, _ = apply_edits(output, {'revision': 0, 'variants': members, 'removed': [picture],
                                            'cutouts': [{'source': picture, 'rects': [[.06, 130/750, .36, 580/750]]}]})
            piece = next(entry for entry in saved['images'] if entry.get('cutout') and not entry.get('removed'))
            self.assertEqual((piece['variant'], piece['size']), (2, [400, 600]))
            with Image.open(photo) as original, original.crop(piece['crop_px']) as expected:
                cut = exported_image(saved, piece)
                self.assertEqual(cut.size, expected.size)
                self.assertIsNone(ImageChops.difference(cut.convert('RGB'), expected.convert('RGB')).getbbox())


class SourcePagesServer(unittest.TestCase):
    def setUp(self):
        from photo_desktop_server import DesktopServer
        from photo_jobs import JobService
        self.temp = tempfile.TemporaryDirectory(prefix='pdf-photos-pages-')
        self.addCleanup(self.temp.cleanup)
        report = extract(cutout_pdf(self.temp.name))
        self.jobs = JobService(Path(self.temp.name)/'state.json')
        self.addCleanup(self.jobs.close)
        self.server = DesktopServer(self.jobs, Path(__file__).parent/'frontend'/'dist').start()
        self.addCleanup(self.server.stop)
        self.id = self.server.register_result(report['output'])['id']

    def test_a_page_is_rendered_once_for_repeat_views(self):
        from unittest.mock import patch
        import photo_desktop_server
        with patch.object(photo_desktop_server, 'render_page', wraps=photo_desktop_server.render_page) as render:
            first = self.server.page_image(self.id, 1, thumb=True)
            self.assertEqual(self.server.page_image(self.id, 1, thumb=True), first)
            self.server.page_image(self.id, 1)
        self.assertEqual(render.call_count, 2)
        with Image.open(BytesIO(first)) as image:
            self.assertEqual(max(image.size), 240)

    def test_app_files_and_state_do_not_wait_for_a_save(self):
        from threading import Event, Thread
        from urllib.request import urlopen
        held, release = Event(), Event()

        def save():
            with self.server.lock:
                held.set()
                release.wait(10)
        saving = Thread(target=save)
        saving.start()
        self.addCleanup(saving.join)
        self.addCleanup(release.set)
        held.wait(5)
        for path in ('', 'api/state'):
            with self.subTest(path=path), urlopen(self.server.url+path, timeout=3) as response:
                self.assertEqual(response.status, 200)


if __name__ == '__main__':
    unittest.main()
