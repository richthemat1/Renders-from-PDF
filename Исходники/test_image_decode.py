"""Pictures read directly instead of through pypdf keep pypdf's pixels and the same scratch files."""
from io import BytesIO
import tempfile
import unittest
import zlib
from pathlib import Path

from PIL import Image, ImageChops
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, ByteStringObject, DictionaryObject, EncodedStreamObject, NameObject, NumberObject

import photo_export
from photo_scan import direct_image, pypdf_image


def picture(mode='RGB', size=(160, 120)):
    """Colour gradients with fine detail, so a second JPEG encoding visibly changes pixels."""
    width, height = size
    image = Image.new(mode, size)
    image.putdata([((x*7+y*3) % 256, (x*x+y) % 256, (y*5) % 256) if mode == 'RGB' else (x*3+y*y) % 256
                   for y in range(height) for x in range(width)])
    return image


def encoded(image, kind):
    if kind == 'flate':
        return zlib.compress(image.tobytes()), '/FlateDecode'
    stream = BytesIO()
    image.save(stream, 'JPEG' if kind == 'jpeg' else 'JPEG2000', **({'quality': 80} if kind == 'jpeg' else {}))
    return stream.getvalue(), '/DCTDecode' if kind == 'jpeg' else '/JPXDecode'


class ImagePdf:
    def __init__(self):
        self.writer = PdfWriter()
        self.page = self.writer.add_blank_page(400, 400)
        self.xobjects = DictionaryObject()

    def stream(self, image, kind, space=None, **extra):
        data, coding = encoded(image, kind)
        obj = EncodedStreamObject()
        obj._data = data
        obj.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                    NameObject('/Width'): NumberObject(image.width), NameObject('/Height'): NumberObject(image.height),
                    NameObject('/ColorSpace'): space or NameObject('/DeviceRGB' if image.mode == 'RGB' else '/DeviceGray'),
                    NameObject('/BitsPerComponent'): NumberObject(8), NameObject('/Filter'): NameObject(coding)})
        obj.update({NameObject(key): value for key, value in extra.items()})
        return obj

    def add(self, name, image, kind, mask=None, mask_kind='flate', **extra):
        if mask is not None:
            extra['/SMask'] = self.writer._add_object(self.stream(mask, mask_kind))
        self.xobjects[NameObject(name)] = self.writer._add_object(self.stream(image, kind, **extra))

    def read(self):
        self.page[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): self.xobjects})
        stream = BytesIO()
        self.writer.write(stream)
        return PdfReader(BytesIO(stream.getvalue()))


def same(first, second):
    return (first.mode, first.size) == (second.mode, second.size) and ImageChops.difference(first, second).getbbox() is None


class DirectDecode(unittest.TestCase):
    def fixture(self):
        pdf = ImagePdf()
        mask = picture('L')
        palette = ArrayObject([NameObject('/Indexed'), NameObject('/DeviceRGB'), NumberObject(255),
                               ByteStringObject(bytes(range(256))*3)])
        pdf.add('/MaskedJpeg', picture(), 'jpeg', mask)
        pdf.add('/Jpx', picture(), 'jpx')
        pdf.add('/MaskedJpx', picture(), 'jpx', mask)
        pdf.add('/Flate', picture(), 'flate')
        pdf.add('/MaskedGrey', picture('L'), 'flate', mask)
        pdf.add('/OtherMaskSize', picture(), 'jpeg', picture('L', (80, 60)))
        pdf.add('/Jpeg', picture(), 'jpeg')
        pdf.add('/JpegMask', picture(), 'jpeg', mask, mask_kind='jpeg')
        pdf.add('/Inverted', picture(), 'jpeg', **{'/Decode': ArrayObject([NumberObject(1), NumberObject(0)]*3)})
        pdf.add('/Indexed', picture('L'), 'flate', space=palette)
        reader = pdf.read()
        page = reader.pages[0]
        objects = {str(name): reference.get_object() for name, reference in page['/Resources']['/XObject'].items()}
        return page, objects

    def test_direct_pictures_have_the_pixels_of_pypdf(self):
        page, objects = self.fixture()
        for name, obj in objects.items():
            with self.subTest(name):
                if name == '/JpegMask':
                    continue
                self.assertTrue(same(pypdf_image(page, name, obj), page.images[name].image))

    def test_jpeg_mask_keeps_its_own_pixels(self):
        """pypdf encodes a JPEG mask a second time, with loss; the direct picture has the colours of pypdf
        and the transparency exactly as the mask stores it."""
        page, objects = self.fixture()
        obj = objects['/JpegMask']
        found, expected = pypdf_image(page, '/JpegMask', obj), page.images['/JpegMask'].image
        self.assertEqual(found.mode, 'RGBA')
        self.assertTrue(same(found.convert('RGB'), expected.convert('RGB')))
        with Image.open(BytesIO(obj['/SMask'].get_data())) as mask:
            self.assertTrue(same(found.getchannel('A'), mask))

    def test_only_exact_kinds_skip_pypdf(self):
        page, objects = self.fixture()
        direct = {name for name, obj in objects.items() if direct_image(obj) is not None}
        self.assertEqual(direct, {'/MaskedJpeg', '/Jpx', '/MaskedJpx', '/Flate', '/MaskedGrey', '/OtherMaskSize', '/JpegMask'})

    def test_scratch_files_stay_byte_for_byte(self):
        """image_sha256 and the blank check read the scratch raster: its file does not change."""
        page, objects = self.fixture()
        paths = photo_export.image_paths(page)
        ids = {name: reference.idnum for name, reference in page['/Resources']['/XObject'].items()}
        with tempfile.TemporaryDirectory() as root:
            for name, obj in objects.items():
                with self.subTest(name):
                    found = page.images[name]
                    suffix = Path(found.name).suffix.lower()
                    if photo_export.native_jpeg(obj) is not None or name == '/JpegMask':
                        continue
                    if suffix in ('.jpg', '.jpeg', '.png'):
                        before = found.data
                    else:
                        stream = BytesIO()
                        with Image.open(BytesIO(found.data)) as image:
                            image.save(stream, 'PNG', compress_level=photo_export.SCRATCH_PNG_LEVEL)
                        before = stream.getvalue()
                    group = [{'object_id': ids[name], 'srcsize': [obj['/Width'], obj['/Height']]}]
                    target, _, method = photo_export.save_group(page, paths, group, Path(root)/name.strip('/'))
                    self.assertEqual(method, 'original')
                    self.assertEqual(target.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
