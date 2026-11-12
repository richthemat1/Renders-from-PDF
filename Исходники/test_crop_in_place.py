"""«Вырезать…» on a card: the crop takes the card's own place in its variant PDF, also for a copy
that lives in a variant the user made."""
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader

from photo_edit import apply_edits, load_report
from test_photos import EditFixture


def page_sizes(path):
    with PdfReader(path) as reader:
        return [page.images[0].image.size for page in reader.pages]


class CropInPlace(EditFixture, unittest.TestCase):
    def made_variant(self, root):
        """Variant 3 made by the user from copies of the red and the blue render."""
        output, ids = self.export(root)
        apply_edits(output, {'revision': 0, 'copies': [ids['red'], ids['blue']],
                             'variants': {'1': [0, 1], '2': [2], '3': [4, 5]}})
        return output

    def test_crop_of_a_copy_replaces_it_where_it_stood_and_leaves_its_original_whole(self):
        with tempfile.TemporaryDirectory(prefix='pdf-crop-') as root:
            output = self.made_variant(root)
            self.assertEqual(page_sizes(output/'Вариант 3/Вариант 3.pdf'), [(600, 400), (600, 400)])
            before = (output/'Вариант 1/Вариант 1.pdf').read_bytes()
            # As the window sends it: the copy (4) is removed and its frame (6) stands first, before the other copy.
            report, _ = apply_edits(output, {'revision': 1, 'cuts': [{'source': 4, 'boxes': [[100, 50, 400, 300]]}],
                                             'removed': [4], 'variants': {'1': [0, 1], '2': [2], '3': [6, 5]}})
            self.assertEqual(page_sizes(output/'Вариант 3/Вариант 3.pdf'), [(300, 250), (600, 400)])
            self.assertEqual(self.colors(output/'Вариант 3/Вариант 3.pdf'), ['red', 'blue'])
            # One card where there was one: the whole picture is no longer a card of the variant.
            cards = [(index, entry['size']) for index, entry in enumerate(report['images'])
                     if entry['variant'] == 3 and not entry.get('removed')]
            self.assertEqual(sorted(cards), [(5, [600, 400]), (6, [300, 250])])
            # The original render in variant 1 is untouched.
            self.assertEqual((output/'Вариант 1/Вариант 1.pdf').read_bytes(), before)
            self.assertFalse(report['images'][0].get('removed', False))

    def test_crop_can_be_changed_and_the_whole_picture_brought_back(self):
        with tempfile.TemporaryDirectory(prefix='pdf-crop-') as root:
            output = self.made_variant(root)
            apply_edits(output, {'revision': 1, 'cuts': [{'source': 4, 'boxes': [[100, 50, 400, 300]]}],
                                 'removed': [4], 'variants': {'1': [0, 1], '2': [2], '3': [6, 5]}})
            record = next(item for item in load_report(output)['collages'] if item['id'].endswith('-m4'))
            # Cropped again from the whole picture, not from the crop.
            apply_edits(output, {'revision': 2, 'cuts': [{'collage': record['id'], 'boxes': [[0, 0, 600, 200]]}],
                                 'removed': [6], 'variants': {'1': [0, 1], '2': [2], '3': [7, 5]}})
            self.assertEqual(page_sizes(output/'Вариант 3/Вариант 3.pdf'), [(600, 200), (600, 400)])
            apply_edits(output, {'revision': 3, 'cuts': [{'collage': record['id'], 'boxes': [[0, 0, 600, 400]]}],
                                 'removed': [7], 'variants': {'1': [0, 1], '2': [2], '3': [8, 5]}})
            self.assertEqual(page_sizes(output/'Вариант 3/Вариант 3.pdf'), [(600, 400), (600, 400)])


if __name__ == '__main__':
    unittest.main()
