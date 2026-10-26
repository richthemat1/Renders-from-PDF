"""One owner-provided image supplies Windows and web icons without touching its source."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image


class AppIcon(unittest.TestCase):
    def test_windows_sizes_keep_transparency_and_original_file(self):
        from photo_icon import create_icon, ICON_SIZES
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'logo.png', Path(directory) / 'app.ico'
            image = Image.new('RGBA', (512, 256), '#357bd4')
            image.save(source)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            create_icon(source, target)
            with Image.open(target) as icon:
                self.assertEqual(icon.format, 'ICO')
                self.assertEqual(icon.ico.sizes(), set(ICON_SIZES))
                self.assertEqual(icon.convert('RGBA').getpixel((0, 0))[3], 0)
                self.assertEqual(icon.convert('RGBA').getpixel((128, 128)), (53, 123, 212, 255))
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_invalid_image_keeps_the_previously_working_icon(self):
        from photo_icon import create_icon
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'bad.png', root / 'app.ico'
            source.write_bytes(b'not an image')
            target.write_bytes(b'previous icon')
            with self.assertRaises(ValueError):
                create_icon(source, target)
            self.assertEqual(target.read_bytes(), b'previous icon')

    def test_window_resolves_the_same_icon_in_packaged_assets(self):
        from photo_native import app_icon_path
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'assets').mkdir()
            with patch('photo_native.sys._MEIPASS', str(root), create=True):
                self.assertIsNone(app_icon_path())
                (root / 'assets' / 'app-icon.ico').write_bytes(b'icon')
                self.assertEqual(app_icon_path(), root / 'assets' / 'app-icon.ico')


if __name__ == '__main__':
    unittest.main()
