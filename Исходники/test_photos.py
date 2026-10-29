"""Behaviour checks for the standalone, general-caption image exporter."""
import contextlib
import gc
import importlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

from PIL import Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from pypdf import PdfReader

pdfmetrics.registerFont(TTFont('FixtureArial', 'C:/Windows/Fonts/arial.ttf'))


def make_pdf(root, pages):
    root = Path(root)
    target = root / 'presentation.pdf'
    pdf = canvas.Canvas(str(target), pagesize=(1000, 800))
    for page_index, spec in enumerate(pages):
        for image_index, image in enumerate(spec.get('images', [])):
            x, top, width, height = image['box']
            path = root / f'image-{page_index}-{image_index}.png'
            Image.new('RGB', image.get('size', (600, 400)),
                      image.get('color', '#456789')).save(path)
            pdf.drawImage(str(path), x, 800-top-height, width, height)
        for text, x, top, size in spec.get('text', []):
            pdf.setFont('FixtureArial', size)
            pdf.drawString(x, 800-top-size, text)
        pdf.showPage()
    pdf.save()
    return target


def exported_image(report, entry):
    path = Path(report['output'], entry['file'])
    if path.suffix.lower() == '.pdf':
        with PdfReader(path) as reader:
            return reader.pages[entry.get('pdf_page', 1)-1].images[0].image.copy()
    return Image.open(path)


class PhotoExport(unittest.TestCase):
    def run_export(self, source, output=None):
        self.assertIsNotNone(importlib.util.find_spec('photo_export'),
                             'Новый экспорт по произвольным заголовкам ещё не реализован')
        with contextlib.redirect_stdout(io.StringIO()):
            return importlib.import_module('photo_export').extract(source, output)

    def test_arbitrary_captions_in_two_columns_and_below_a_photo(self):
        with tempfile.TemporaryDirectory() as root:
            source = make_pdf(root, [{'images': [
                {'box': (50, 160, 400, 270)}, {'box': (550, 160, 400, 270), 'color': '#999977'}],
                'text': [('Двор вечером', 50, 120, 18),
                         ('Вход со стороны парка', 550, 440, 18)]}])
            report = self.run_export(source)
            self.assertEqual({e['title'] for e in report['images']},
                             {'Двор вечером', 'Вход со стороны парка'})
            self.assertEqual({e['status'] for e in report['images']}, {'named'})

    def test_comparison_numbers_and_names_inside_images_match_explicit_variant_headings(self):
        with tempfile.TemporaryDirectory() as root:
            names = ['Бюро Север', 'Студия Юг', 'Мастерская Восток', 'Бюро Запад']
            positions = [(50, 170), (550, 170), (50, 470), (550, 470)]
            colors = ['#ff0000', '#00ff00', '#0000ff', '#ffff00']
            overview = {'images': [{'box': (x, top, 400, 260), 'color': color}
                                   for (x, top), color in zip(positions, colors)],
                        'text': [('Сравнение вариантов', 50, 120, 18)]+
                                [(f'{number} {name}', x+15, top+8, 18)
                                 for number, (name, (x, top)) in enumerate(zip(names, positions), 1)]}
            details = [{'images': [{'box': (50, 170, 800, 500)}],
                        'text': [(f'Вариант №{number} {name}', 50, 120, 18)]}
                       for number, name in enumerate(names, 1)]
            report = self.run_export(make_pdf(root, [overview]+details))
            comparison = [entry for entry in report['images'] if entry['page'] == 1]
            self.assertEqual([entry['variant'] for entry in comparison], [1, 2, 3, 4])
            self.assertEqual([entry['source_caption'] for entry in comparison],
                             [f'{n} {name}' for n, name in enumerate(names, 1)])
            self.assertEqual([entry['variant_label_reference_page'] for entry in comparison], [2, 3, 4, 5])
            self.assertEqual([document['pages'] for document in report['documents']], [2, 2, 2, 2])
            for entry, color in zip(comparison, [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]):
                with exported_image(report, entry) as image:
                    self.assertEqual(image.convert('RGB').getpixel((100, 100)), color)

    def test_numbered_names_need_a_comparison_heading_and_matching_number_name_reference(self):
        for comparison, reference, swapped in [(True, False, False), (False, True, False), (True, True, True)]:
            with self.subTest(comparison=comparison, reference=reference, swapped=swapped):
                with tempfile.TemporaryDirectory() as root:
                    names = ['Бюро Север', 'Студия Юг']
                    labels = list(reversed(names)) if swapped else names
                    pages = [{'images': [{'box': (50, 170, 400, 270)}, {'box': (550, 170, 400, 270)}],
                              'text': [('Сравнение вариантов' if comparison else 'Перечень объектов', 50, 120, 18),
                                       ('1 '+labels[0], 65, 180, 18), ('2 '+labels[1], 565, 180, 18)]}]
                    if reference:
                        pages += [{'images': [{'box': (50, 170, 800, 500)}],
                                   'text': [(f'Вариант №{n} {name}', 50, 120, 18)]}
                                  for n, name in enumerate(names, 1)]
                    report = self.run_export(make_pdf(root, pages))
                    self.assertEqual([e['variant'] for e in report['images'] if e['page'] == 1], [None, None])

    def test_numbered_comparison_stops_the_previous_section_for_following_unlabelled_images(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [{'text': [('ВАРИАНТ 9', 440, 380, 20)]},
                     {'images': [{'box': (50, 170, 400, 270)}, {'box': (550, 170, 400, 270)}],
                      'text': [('Сравнение вариантов', 50, 120, 18), ('1 Бюро Север', 65, 180, 18),
                               ('2 Студия Юг', 565, 180, 18)]},
                     {'images': [{'box': (50, 300, 900, 430)}]}]
            pages += [{'images': [{'box': (50, 170, 800, 500)}],
                       'text': [(f'Вариант №{n} {name}', 50, 120, 18)]}
                      for n, name in [(1, 'Бюро Север'), (2, 'Студия Юг')]]
            report = self.run_export(make_pdf(root, pages))
            self.assertEqual([e['variant'] for e in report['images']], [1, 2, None, 1, 2])

    def test_variant_details_and_notes_are_grouped_by_the_same_explicit_variant_number(self):
        with tempfile.TemporaryDirectory() as root:
            titles = ['Вариант 1', 'Вариант 1 (предоставлена НПМ)',
                      'Вариант 1. Перспективное изображение №3',
                      'Вариант 2', 'Благоустройство. Вариант № 2', 'Вариант 10']
            pages = [{'images': [{'box': (50, 170, 800, 500)}],
                      'text': [(title, 50, 120, 14)]} for title in titles]
            report = self.run_export(make_pdf(root, pages))
            self.assertEqual(report['counts_by_folder'], {'Вариант 1': 3, 'Вариант 2': 2, 'Вариант 10': 1})
            self.assertEqual([e['title'] for e in report['images']], titles)
            self.assertEqual(len({e['file'] for e in report['images']}), 3)
            self.assertEqual([e['pdf_page'] for e in report['images']], [1, 2, 3, 1, 2, 1])
            self.assertEqual({d['file']: d['pages'] for d in report['documents']}, {
                str(Path('Вариант 1')/'Вариант 1.pdf'): 3,
                str(Path('Вариант 2')/'Вариант 2.pdf'): 2,
                str(Path('Вариант 10')/'Вариант 10.pdf'): 1})

    def test_subvariant_heading_gets_its_own_pdf_after_its_variant(self):
        with tempfile.TemporaryDirectory() as root:
            titles = ['Вариант 3', 'Вариант 3.1', 'Перспективное изображение. Вариант 3.1',
                      'Вариант 1, 2', 'Вариант 1. Общий вид']
            pages = [{'images': [{'box': (50, 170, 800, 500)}],
                      'text': [(title, 50, 120, 14)]} for title in titles]
            report = self.run_export(make_pdf(root, pages))
            self.assertEqual([e['variant'] for e in report['images']], [3, '3.1', '3.1', None, 1])
            self.assertEqual({d['file']: d['pages'] for d in report['documents'] if d['variant'] is not None}, {
                str(Path('Вариант 1')/'Вариант 1.pdf'): 1,
                str(Path('Вариант 3')/'Вариант 3.pdf'): 1,
                str(Path('Вариант 3.1')/'Вариант 3.1.pdf'): 2})
            gallery = (Path(report['output'])/'Просмотр.html').read_text(encoding='utf-8')
            self.assertLess(gallery.index('data-variant="3"'), gallery.index('data-variant="3.1"'))

    def test_one_variant_pdf_keeps_source_order_pixels_and_links_to_each_page(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [{'images': [
                {'box': (550, 170, 400, 270), 'color': '#00ff00'},
                {'box': (50, 170, 400, 270), 'color': '#ff0000'}],
                'text': [('Вариант 1. Ракурс Б', 50, 120, 18),
                         ('Вариант 1. Ракурс А', 550, 120, 18)]},
                {'images': [{'box': (50, 170, 600, 300), 'color': '#ff00ff'}],
                 'text': [('Вариант 2', 50, 120, 18)]}]
            pages += [{'images': [{'box': (50, 170, 600, 300), 'color': color}],
                       'text': [(title, 50, 120, 18)]} for title, color in [
                           ('Вариант 1. Ракурс В', '#0000ff'), ('Вариант 1. Ракурс Г', '#ffff00')]]
            report = self.run_export(make_pdf(root, pages))
            variant = [e for e in report['images'] if e['variant'] == 1]
            output = Path(report['output'])
            self.assertEqual(len(list((output/'Вариант 1').glob('*.pdf'))), 1)
            target = output/'Вариант 1/Вариант 1.pdf'
            expected = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
            with PdfReader(target) as reader:
                self.assertEqual(len(reader.pages), 4)
                self.assertEqual([page.images[0].image.convert('RGB').getpixel((100, 100))
                                  for page in reader.pages], expected)
                self.assertEqual(reader.metadata.title, 'Вариант 1')
                self.assertEqual(len(reader.outline), 4)
            self.assertEqual([e['page'] for e in variant], [1, 1, 3, 4])
            self.assertEqual([e['pdf_page'] for e in variant], [1, 2, 3, 4])
            gallery = (output/'Просмотр.html').read_text(encoding='utf-8')
            for entry, color in zip(variant, expected):
                url = quote(entry['file'].replace('\\', '/'), safe='/')
                self.assertIn(f'href="{url}#page={entry["pdf_page"]}"', gallery)
                with Image.open(output/entry['preview']) as preview:
                    self.assertEqual(preview.convert('RGB').getpixel((100, 100)), color)

    def test_repeated_nonvariant_caption_still_keeps_separate_pdf_files(self):
        with tempfile.TemporaryDirectory() as root:
            page = {'images': [{'box': (50, 170, 600, 300)}],
                    'text': [('Вход со стороны парка', 50, 120, 18)]}
            report = self.run_export(make_pdf(root, [page, page]))
            self.assertEqual(len({e['file'] for e in report['images']}), 2)
            self.assertEqual([d['pages'] for d in report['documents']], [1, 1])

    def test_variant_dividers_assign_following_pages_and_appendix_ends_the_section(self):
        with tempfile.TemporaryDirectory() as root:
            logo = {'box': (30, 20, 120, 30), 'size': (240, 60), 'color': '#999999'}
            photo = {'box': (50, 300, 900, 430)}
            pages = [
                {'images': [photo]},
                {'images': [logo], 'text': [('ВАРИАНТ 1', 440, 380, 20)]},
                {'images': [photo, logo]},
                {'images': [photo, logo], 'text': [('Вид с реки', 50, 260, 18)]},
                {'images': [photo, logo]},
                {'images': [logo], 'text': [('ВАРИАНТ 2', 440, 380, 20)]},
                {'images': [photo, logo]},
                {'images': [logo], 'text': [('ПРИЛОЖЕНИЕ', 420, 380, 20)]},
                {'images': [photo, logo], 'text': [('Дополнительные материалы', 50, 260, 18)]}]
            report = self.run_export(make_pdf(root, pages))
            photos = [e for e in report['images'] if e['size'] == [600, 400]]
            self.assertEqual([e['variant'] for e in photos], [None, 1, 1, 1, 2, None])
            self.assertEqual([e['pdf_page'] for e in photos if e['variant'] == 1], [1, 2, 3])
            self.assertIn('Вид с реки', photos[2]['title'])
            self.assertEqual(photos[1]['variant_source_page'], 2)
            self.assertTrue(all(e['variant'] is None for e in report['images'] if e['size'] == [240, 60]))
            with PdfReader(Path(report['output'])/'Вариант 1/Вариант 1.pdf') as reader:
                self.assertEqual(len(reader.pages), 3)

    def test_unique_variant_page_heading_applies_across_a_large_blank_gap(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [{'images': [{'box': (50, 300, 900, 430)}],
                      'text': [('ВАРИАНТ 7 | ВИЗУАЛИЗАЦИЯ. ВИД 2', 50, 40, 18)]}]
            report = self.run_export(make_pdf(root, pages))
            self.assertEqual(report['images'][0]['variant'], 7)
            self.assertIn('ВИД 2', report['images'][0]['title'])

    def test_common_variant_heading_does_not_relabel_small_auxiliary_images_around_the_main_render(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [{'images': [{'box': (50, 250, 600, 350)},
                                 {'box': (400, 350, 240, 240), 'size': (480, 480), 'color': '#dddddd'},
                                 {'box': (720, 500, 240, 240), 'size': (480, 480), 'color': '#cccccc'}],
                      'text': [('ВАРИАНТ 7 | ВИЗУАЛИЗАЦИЯ', 50, 40, 18)]}]
            report = self.run_export(make_pdf(root, pages))
            self.assertEqual([e['variant'] for e in report['images']], [7, None, None])

    def test_comparison_page_does_not_carry_the_previous_section_into_unlabelled_images(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [
                {'text': [('ВАРИАНТ 1', 440, 380, 20)]},
                {'images': [{'box': (50, 170, 400, 270)}, {'box': (550, 170, 400, 270)}],
                 'text': [('Вариант 1', 50, 120, 18), ('Вариант 2', 550, 120, 18)]},
                {'images': [{'box': (50, 300, 900, 430)}]}]
            report = self.run_export(make_pdf(root, pages))
            self.assertEqual([e['variant'] for e in report['images']], [1, 2, None])

    def test_local_variant_on_an_illustrated_page_is_not_a_section_divider(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [
                {'images': [{'box': (50, 170, 800, 500)}], 'text': [('Вариант 1', 50, 120, 18)]},
                {'images': [{'box': (50, 300, 900, 430)}]}]
            report = self.run_export(make_pdf(root, pages))
            self.assertEqual([e['variant'] for e in report['images']], [1, None])

    def test_a_divider_naming_multiple_variants_does_not_guess_a_single_section(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [{'text': [('ВАРИАНТ 1 / 2', 430, 380, 20)]},
                     {'images': [{'box': (50, 300, 900, 430)}]}]
            report = self.run_export(make_pdf(root, pages))
            self.assertIsNone(report['images'][0]['variant'])

    def test_deliverables_are_single_page_pdfs_with_native_pixels_and_working_previews(self):
        with tempfile.TemporaryDirectory() as root:
            source = make_pdf(root, [{'images': [{'box': (50, 170, 600, 300), 'size': (1200, 600)}],
                                     'text': [('Вариант 1', 50, 120, 18)]}])
            report = self.run_export(source)
            entry = report['images'][0]
            target = Path(report['output'], entry['file'])
            self.assertEqual(target.suffix, '.pdf')
            with PdfReader(target) as reader:
                self.assertEqual(len(reader.pages), 1)
                page = reader.pages[0]
                self.assertAlmostEqual(float(page.mediabox.width), 600, places=2)
                self.assertAlmostEqual(float(page.mediabox.height), 300, places=2)
                self.assertEqual(page.images[0].image.size, (1200, 600))
            with Image.open(Path(report['output'], entry['preview'])) as preview:
                self.assertLessEqual(max(preview.size), 640)
            self.assertEqual(list(Path(report['output']).rglob('*.jpg')), [])
            self.assertEqual(list(Path(report['output']).rglob('*.jpeg')), [])

    def test_caption_naming_two_variants_does_not_silently_choose_the_first(self):
        with tempfile.TemporaryDirectory() as root:
            source = make_pdf(root, [{'images': [{'box': (50, 170, 800, 500)}],
                                     'text': [('Вариант 1 / Вариант 2', 50, 120, 18)]}])
            report = self.run_export(source)
            self.assertEqual(report['images'][0]['folder'], 'На проверку')
            self.assertEqual(report['images'][0]['status'], 'review')

    def test_jpeg_is_embedded_in_pdf_without_recompression(self):
        import photo_export
        self.assertTrue(hasattr(photo_export, 'image_to_pdf'), 'Нет сохранения исходного изображения в PDF')
        with tempfile.TemporaryDirectory() as root:
            source, target = Path(root)/'image.jpg', Path(root)/'image.pdf'
            with Image.new('RGB', (1200, 600), '#456789') as image:
                image.save(source, 'JPEG', quality=91)
            photo_export.image_to_pdf(source, target, [0, 0, 600, 300], 'Вариант 1')
            with PdfReader(target) as reader:
                self.assertEqual(reader.pages[0].images[0].data, source.read_bytes())

    def test_jpeg_of_the_presentation_reaches_the_variant_pdf_byte_for_byte(self):
        # pypdf 6 re-encodes JPEGs on extraction; a noisy progressive photo would not survive that unchanged.
        with tempfile.TemporaryDirectory() as root:
            photo = Path(root)/'photo.jpg'
            with Image.effect_noise((900, 600), 70) as noise, noise.convert('RGB') as image:
                image.save(photo, 'JPEG', quality=83, progressive=True)
            source = Path(root)/'presentation.pdf'
            pdf = canvas.Canvas(str(source), pagesize=(1000, 800))
            pdf.drawImage(str(photo), 50, 800-160-450, 675, 450)
            pdf.setFont('FixtureArial', 18)
            pdf.drawString(50, 800-120-18, 'Вариант 1')
            pdf.showPage()
            pdf.save()
            report = self.run_export(source)
            entry = report['images'][0]
            self.assertEqual((entry['variant'], entry['method'], entry['size']), (1, 'original', [900, 600]))
            with PdfReader(Path(report['output'], entry['file'])) as reader:
                stream = next(iter(reader.pages[0]['/Resources']['/XObject'].values())).get_object()
                self.assertEqual(stream.get_data(), photo.read_bytes())
                # Binary, without the ASCII85 layer that made ReportLab encode every byte in Python.
                self.assertEqual(stream['/Filter'], ['/DCTDecode'])

    def test_caption_in_the_bottom_five_percent_is_kept_but_page_number_is_not_a_title(self):
        with tempfile.TemporaryDirectory() as root:
            source = make_pdf(root, [{'images': [{'box': (50, 10, 900, 730)}],
                                     'text': [('Вариант 2', 450, 765, 18), ('86', 945, 765, 18)]}])
            report = self.run_export(source)
            self.assertEqual(report['images'][0]['title'], 'Вариант 2')
            self.assertEqual(report['images'][0]['folder'], 'Вариант 2')

    def test_short_variant_labels_inside_each_photo_use_explicit_variants_from_the_document(self):
        with tempfile.TemporaryDirectory() as root:
            images = [{'box': (x, y, 430, 340)} for y in [10, 380] for x in [50, 520]]
            labels = [(f'В{n}', x+15, y+300, 24)
                      for n, (x, y) in enumerate([(50, 10), (520, 10), (50, 380), (520, 380)], 1)]
            pages = [{'images': images, 'text': labels+[('Сравнение фасадов', 350, 765, 18)]}]
            pages += [{'images': [{'box': (50, 10, 900, 730)}],
                       'text': [(f'Вариант {n}', 450, 765, 18)]} for n in [1, 2, 3, 4]]
            report = self.run_export(make_pdf(root, pages))
            comparison = [e for e in report['images'] if e['page'] == 1]
            self.assertEqual([e['title'] for e in comparison], [f'Вариант {n}' for n in [1, 2, 3, 4]])
            self.assertEqual(report['counts_by_folder'], {f'Вариант {n}': 2 for n in [1, 2, 3, 4]})
            self.assertEqual([e['source_caption'] for e in comparison], ['В1', 'В2', 'В3', 'В4'])

    def test_section_and_variant_are_combined_and_repeated_folders_are_reused(self):
        with tempfile.TemporaryDirectory() as root:
            page = {'images': [{'box': (50, 180, 800, 500)}],
                    'text': [('Благоустройство', 50, 40, 28), ('Вариант 1', 50, 120, 18)]}
            report = self.run_export(make_pdf(root, [page, page]))
            self.assertEqual([e['title'] for e in report['images']],
                             ['Благоустройство — Вариант 1'] * 2)
            self.assertEqual(len(report['counts_by_folder']), 1)
            self.assertEqual(len({e['file'] for e in report['images']}), 1)
            self.assertEqual([e['pdf_page'] for e in report['images']], [1, 2])

    def test_no_text_photo_is_saved_and_tiny_fragment_has_an_audit_record(self):
        with tempfile.TemporaryDirectory() as root:
            report = self.run_export(make_pdf(root, [{'images': [
                {'box': (50, 150, 800, 500)},
                {'box': (10, 10, 12, 12), 'size': (16, 16), 'color': '#dddddd'}]}]))
            result = [e for e in report['images'] if not e.get('removed')]
            self.assertEqual(len(result), 1)
            self.assertEqual({e['status'] for e in result}, {'unsigned'})
            # The filtered fragment is kept only as a removed, restorable service copy.
            self.assertEqual([e.get('suppressed') for e in report['images'] if e.get('removed')], [True])
            self.assertTrue(all(Path(report['output'], e['file']).is_file() for e in report['images']))
            self.assertEqual([(item['suppression_code'], item['bbox']) for item in report['suppressed']],
                             [('small_fragment', [10, 10, 22, 22])])

    def test_two_titles_above_one_collage_request_review_without_aborting(self):
        with tempfile.TemporaryDirectory() as root:
            source = make_pdf(root, [{'images': [{'box': (50, 170, 900, 500)}],
                                     'text': [('Северный двор', 60, 120, 18),
                                              ('Южный двор', 600, 120, 18)]}])
            report = self.run_export(source)
            self.assertEqual(len(report['images']), 1)
            self.assertEqual(report['images'][0]['status'], 'review')
            self.assertEqual(report['images'][0]['folder'], 'На проверку')
            self.assertEqual(len(report['images'][0]['candidates']), 2)

    def test_sliced_photo_is_reassembled_at_original_resolution(self):
        with tempfile.TemporaryDirectory() as root:
            source = make_pdf(root, [{'images': [
                {'box': (50, 170+i*100, 600, 100), 'size': (1200, 200), 'color': color}
                for i, color in enumerate(['#ff0000', '#00ff00', '#0000ff'])],
                'text': [('Зелёный двор', 50, 120, 18)]}])
            report = self.run_export(source)
            self.assertEqual(report['source_components'], 3)
            self.assertEqual(len(report['images']), 1)
            entry = report['images'][0]
            self.assertEqual(entry['size'], [1200, 600])
            self.assertEqual(entry['method'], 'stitched_originals')
            with exported_image(report, entry) as image:
                image = image.convert('RGB')
                self.assertEqual([image.getpixel((100, y)) for y in [100, 300, 500]],
                                 [(255, 0, 0), (0, 255, 0), (0, 0, 255)])

    def test_small_annotations_do_not_replace_a_wrapped_photo_heading(self):
        with tempfile.TemporaryDirectory() as root:
            source = make_pdf(root, [{'images': [{'box': (50, 180, 800, 500)},
                {'box': (48, 115, 900, 54), 'size': (1500, 90), 'color': '#eeeeee'}],
                'text': [('Материалы благоустройства', 50, 120, 16),
                         ('Первая очередь', 50, 144, 16),
                         ('3б', 300, 181, 10), ('Зона детской', 100, 685, 10)]}])
            report = self.run_export(source)
            photo = next(e for e in report['images'] if e['size'] == [600, 400])
            banner = next(e for e in report['images'] if e['size'] == [1500, 90])
            self.assertEqual(banner['status'], 'unsigned')
            self.assertEqual(photo['status'], 'named')
            self.assertEqual(photo['title'], 'Материалы благоустройства Первая очередь')

    def test_last_tile_can_have_fewer_pixels_than_its_displayed_height_suggests(self):
        with tempfile.TemporaryDirectory() as root:
            source = make_pdf(root, [{'images': [
                {'box': (50, 170+i*100, 600, 100), 'size': (1200, pixels), 'color': color}
                for i, (pixels, color) in enumerate([(200, '#ff0000'), (200, '#00ff00'), (196, '#0000ff')])],
                'text': [('Изображение двора', 50, 120, 18)]}])
            report = self.run_export(source)
            self.assertEqual(len(report['images']), 1)
            self.assertEqual(report['images'][0]['size'], [1200, 596])
            self.assertEqual(report['images'][0]['method'], 'stitched_originals')
            self.assertEqual(report['warnings'], [])

    def test_repeated_header_logos_do_not_inherit_the_photo_caption_below(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [{'images': [{'box': (50, 180, 800, 450)},
                                 {'box': (50, 30, 200, 45), 'size': (800, 180), 'color': '#dddddd'}],
                      'text': [('Двор '+letter, 50, 120, 18)]} for letter in ['А', 'Б', 'В']]
            report = self.run_export(make_pdf(root, pages))
            self.assertEqual(report['counts_by_status'], {'named': 3})
            result = [entry for entry in report['images'] if not entry.get('removed')]
            self.assertEqual([entry['title'] for entry in result], ['Двор А', 'Двор Б', 'Двор В'])
            self.assertEqual(len(result), 3)
            self.assertTrue(all(entry.get('suppressed') for entry in report['images'] if entry.get('removed')))
            self.assertEqual([item['suppression_code'] for item in report['suppressed']], ['repeated_static']*3)

    def test_windows_folder_names_are_safe_and_collisions_do_not_merge_titles(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [{'images': [{'box': (50, 170, 800, 500)}],
                      'text': [(title, 50, 120, 18)]}
                     for title in ['Двор / Восток', 'Двор : Восток', 'CON']]
            report = self.run_export(make_pdf(root, pages))
            self.assertEqual(len(report['counts_by_folder']), 3)
            for entry in report['images']:
                target = Path(report['output'], entry['file']).resolve()
                self.assertTrue(target.is_relative_to(Path(report['output'])))
                self.assertNotRegex(entry['folder'], r'[<>:"/\\|?*]')
                self.assertNotEqual(entry['folder'].upper(), 'CON')

    def test_existing_output_is_preserved_and_summary_lists_review_reasons(self):
        with tempfile.TemporaryDirectory() as root:
            source = make_pdf(root, [{'images': [{'box': (50, 150, 800, 500)}]}])
            first, second = self.run_export(source), self.run_export(source)
            self.assertNotEqual(first['output'], second['output'])
            for report in [first, second]:
                saved = json.loads(Path(report['output'], 'Отчёт.json').read_text(encoding='utf-8'))
                self.assertEqual(saved['images'][0]['status'], 'unsigned')
                self.assertTrue(Path(report['output'], 'Просмотр.html').is_file())

    def test_long_titles_keep_the_distinguishing_ending_and_gallery_uses_small_previews(self):
        with tempfile.TemporaryDirectory() as root:
            beginning = 'Материалы благоустройства. Перспективное изображение благоустраиваемой территории. '
            pages = [{'images': [{'box': (50, 180, 800, 500), 'size': (2400, 1500)}],
                      'text': [(beginning+str(n)+' очередь', 50, 120, 9)]} for n in [1, 2]]
            report = self.run_export(make_pdf(root, pages))
            self.assertEqual(len(report['counts_by_folder']), 2)
            for index, entry in enumerate(report['images'], 1):
                self.assertIn(str(index)+' очередь', entry['folder'])
                preview = Path(report['output'], entry['preview'])
                self.assertTrue(preview.exists())
                with Image.open(preview) as image:
                    self.assertLessEqual(max(image.size), 640)
                self.assertLess(len(str(Path(report['output'], entry['file']))), 240)

    def test_pdf_without_raster_images_reports_a_clear_error(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNotNone(importlib.util.find_spec('photo_export'), 'Новый экспорт ещё не реализован')
            with self.assertRaisesRegex(ValueError, 'растровых изображений'):
                self.run_export(make_pdf(root, [{'text': [('Только текст', 50, 50, 20)]}]))

    def test_decode_failure_uses_a_visible_page_crop_and_records_it(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            source = make_pdf(root, [{'images': [{'box': (50, 180, 800, 500)}]}])
            self.assertIsNotNone(importlib.util.find_spec('photo_export'))
            with patch('photo_export._decode', side_effect=ValueError('Unsupported image resource')):
                report = self.run_export(source)
            self.assertEqual(report['images'][0]['method'], 'page_crop_fallback')
            self.assertEqual(len(report['warnings']), 1)
            self.assertEqual(report['errors'], [])

    def test_cancellation_during_previews_removes_only_the_new_temporary_output(self):
        with tempfile.TemporaryDirectory() as root:
            source = make_pdf(root, [{'images': [{'box': (50, 180, 800, 500)},
                                                     {'box': (900, 50, 25, 25)}]}])
            destination = Path(root)/'result'
            sentinel = Path(root)/'keep.txt'
            sentinel.write_text('keep')
            def progress(info):
                if info['stage'] == 'export' and info['current'] == 1:
                    raise RuntimeError('cancelled')
            self.assertIsNotNone(importlib.util.find_spec('photo_export'))
            from photo_export import extract
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                extract(source, destination, progress)
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(root).glob('.pdf-photos-*')), [])
            self.assertEqual(sentinel.read_text(), 'keep')
            self.assertTrue(source.exists())

    def test_cancellation_during_variant_merge_removes_the_incomplete_result(self):
        with tempfile.TemporaryDirectory() as root:
            page = {'images': [{'box': (50, 170, 600, 300)}],
                    'text': [('Вариант 1', 50, 120, 18)]}
            source = make_pdf(root, [page, page])
            destination = Path(root)/'result'
            def progress(info):
                if info['stage'] == 'merge' and info['current'] == 1:
                    raise RuntimeError('cancelled')
            from photo_export import extract
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                extract(source, destination, progress)
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(root).glob('.pdf-photos-*')), [])
            self.assertTrue(source.exists())

    def test_gui_success_and_failure_have_explicit_finished_states(self):
        self.assertIsNotNone(importlib.util.find_spec('photo_app'), 'Нет интерфейса новой версии')
        # Destroyed Tk windows must release their Python cycles on this thread; collected later in a
        # server thread, every Tk variable waits for a main loop and a save in another test times out.
        self.addCleanup(gc.collect)
        import time
        import tkinter as tk
        from tkinter import ttk
        from unittest.mock import patch
        from photo_app import open_app
        original = tk.Tk
        with tempfile.TemporaryDirectory() as root:
            valid = make_pdf(root, [{'images': [{'box': (50, 180, 800, 500)}]}])
            invalid = Path(root)/'invalid.pdf'
            invalid.write_bytes(b'not-a-pdf')
            for source, expected in [(valid, 'Готово'), (invalid, 'Ошибка')]:
                observed = {}
                def hidden():
                    window = original()
                    window.withdraw()
                    started = time.monotonic()
                    def check():
                        widgets = []
                        def walk(widget):
                            for child in widget.winfo_children():
                                widgets.append(child)
                                walk(child)
                        walk(window)
                        values = [window.getvar(w.cget('textvariable')) for w in widgets
                                  if isinstance(w, ttk.Label) and w.cget('textvariable')]
                        if any(s.startswith(expected) for s in values) or time.monotonic()-started > 10:
                            observed['status'] = ' '.join(values)
                            observed['progress'] = float(next(w for w in widgets if isinstance(w, ttk.Progressbar)).cget('value'))
                            window.destroy()
                        else:
                            window.after(100, check)
                    window.after(250, check)
                    return window
                with patch('tkinter.Tk', hidden):
                    open_app([str(source)], state_path=Path(root)/'workspace.json')
                self.assertIn(expected, observed['status'])
                self.assertEqual(observed['progress'], 100 if source == valid else 0)



COLORS = {'red': (255, 0, 0), 'green': (0, 255, 0), 'blue': (0, 0, 255), 'yellow': (255, 255, 0)}


class EditFixture:
    def export(self, root):
        pages = [{'images': [{'box': (50, 170, 800, 500), 'color': '#ff0000'}], 'text': [('Вариант 1', 50, 120, 18)]},
                 {'images': [{'box': (50, 170, 800, 500), 'color': '#00ff00'}], 'text': [('Вариант 1', 50, 120, 18)]},
                 {'images': [{'box': (50, 170, 800, 500), 'color': '#0000ff'}], 'text': [('Вариант 2', 50, 120, 18)]},
                 {'images': [{'box': (50, 170, 800, 500), 'color': '#ffff00'}]}]
        with contextlib.redirect_stdout(io.StringIO()):
            report = importlib.import_module('photo_export').extract(make_pdf(root, pages))
        self.assertEqual([(e['page'], e['variant']) for e in report['images']], [(1, 1), (2, 1), (3, 2), (4, None)])
        return Path(report['output']), dict(zip(COLORS, range(4)))

    def colors(self, path):
        with PdfReader(path) as reader:
            pixels = [page.images[0].image.convert('RGB').getpixel((100, 100)) for page in reader.pages]
        return [next(name for name, rgb in COLORS.items() if rgb == pixel) for pixel in pixels]

    def files(self, output):
        return {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file()}


class PhotoEdits(EditFixture, unittest.TestCase):
    def test_remove_move_and_reorder_rebuild_pdfs_and_keep_backups(self):
        from photo_edit import apply_edits
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            before = self.files(output)
            report, backup = apply_edits(output, {'revision': 0, 'removed': [ids['red']],
                                                  'variants': {'1': [ids['green']],
                                                               '2': [ids['yellow'], ids['blue']]}})
            self.assertEqual(self.colors(output/'Вариант 1/Вариант 1.pdf'), ['green'])
            self.assertEqual(self.colors(output/'Вариант 2/Вариант 2.pdf'), ['yellow', 'blue'])
            self.assertFalse((output/'Все остальное'/'Без подписи').exists())
            for relative in ('Вариант 1/Вариант 1.pdf', 'Вариант 2/Вариант 2.pdf', 'Отчёт.json', 'Просмотр.html'):
                self.assertEqual((output/backup/relative).read_bytes(), before[str(Path(relative))])
            unsigned = next(r for r in before if r.startswith(str(Path('Все остальное/Без подписи'))))
            self.assertEqual((output/backup/unsigned).read_bytes(), before[unsigned])
            saved = json.loads((output/'Отчёт.json').read_text(encoding='utf-8'))
            self.assertEqual(saved, report)
            self.assertEqual(saved['revision'], 1)
            self.assertEqual(saved['counts_by_folder'], {'Вариант 1': 1, 'Вариант 2': 2})
            self.assertEqual({d['file']: d['pages'] for d in saved['documents']},
                             {str(Path('Вариант 1/Вариант 1.pdf')): 1, str(Path('Вариант 2/Вариант 2.pdf')): 2})
            yellow, red = saved['images'][ids['yellow']], saved['images'][ids['red']]
            self.assertEqual((yellow['variant'], yellow['pdf_page'], yellow['auto_folder'], yellow['manual']),
                             (2, 1, 'Без подписи', True))
            self.assertTrue(red['removed'])
            self.assertTrue((output/red['file']).is_file())
            self.assertEqual(saved['edits'][0]['reordered'], [])
            self.assertEqual(saved['edits'][0]['moved'], [{'image': ids['yellow'], 'from': 'Без подписи',
                                                           'to': 'Вариант 2'}])
            gallery = (output/'Просмотр.html').read_text(encoding='utf-8')
            self.assertNotIn(f'data-id="{ids["red"]}"', gallery)
            self.assertIn('data-revision="1"', gallery)

    def test_reorder_new_variant_and_emptied_variant(self):
        from photo_edit import apply_edits
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            report, _ = apply_edits(output, {'revision': 0, 'variants': {
                '1': [ids['green'], ids['red'], ids['blue']], '2': [], '3': [ids['yellow']]}})
            self.assertEqual(self.colors(output/'Вариант 1/Вариант 1.pdf'), ['green', 'red', 'blue'])
            self.assertEqual(self.colors(output/'Вариант 3/Вариант 3.pdf'), ['yellow'])
            self.assertFalse((output/'Вариант 2').exists())
            self.assertEqual(report['edits'][0]['reordered'], [1])
            self.assertEqual(report['counts_by_folder'], {'Вариант 1': 3, 'Вариант 3': 1})

    def test_edit_creates_subvariant_and_keeps_its_key_through_reload(self):
        from photo_edit import EditError, apply_edits, load_report
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            report, _ = apply_edits(output, {'revision': 0, 'variants': {
                '1': [ids['green'], ids['red']], '1.1': [ids['blue']], '2': [ids['yellow']]}})
            self.assertEqual(self.colors(output/'Вариант 1.1/Вариант 1.1.pdf'), ['blue'])
            saved = load_report(output)
            self.assertEqual(saved['images'][ids['blue']]['variant'], '1.1')
            self.assertEqual(saved['images'][ids['green']]['variant'], 1)
            report, _ = apply_edits(output, {'revision': 1, 'variants': {
                '1': [ids['green'], ids['red']], '1,1': [ids['blue'], ids['yellow']], '2': []}})
            self.assertEqual(self.colors(output/'Вариант 1.1/Вариант 1.1.pdf'), ['blue', 'yellow'])
            for key in ('1.0', '0.1', '1.1.1'):
                with self.assertRaises(EditError):
                    apply_edits(output, {'revision': 2, 'variants': {key: [ids['green']], '1': [ids['red']],
                                                                     '1.1': [ids['blue'], ids['yellow']]}})

    def test_invalid_or_stale_edits_change_nothing(self):
        from photo_edit import EditConflict, EditError, apply_edits
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            before = self.files(output)
            with self.assertRaises(EditConflict):
                apply_edits(output, {'revision': 5, 'variants': {'1': [ids['green']]}, 'removed': [ids['red']]})
            with self.assertRaisesRegex(EditError, 'пропущены'):
                apply_edits(output, {'revision': 0, 'variants': {'1': [ids['green']]}})
            with self.assertRaisesRegex(EditError, 'несколько раз'):
                apply_edits(output, {'revision': 0, 'variants': {'1': [ids['red'], ids['red'], ids['green']]}})
            with self.assertRaises(EditError):
                apply_edits(output, {'revision': 0, 'variants': {'1': [ids['red'], ids['green'], 99]}})
            self.assertEqual(self.files(output), before)

    def test_failed_save_restores_previous_files(self):
        import photo_edit
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            before = self.files(output)
            with patch.object(photo_edit, 'write_report', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    photo_edit.apply_edits(output, {'revision': 0, 'removed': [ids['yellow']],
                                                    'variants': {'1': [ids['green']], '2': [ids['blue'], ids['red']]}})
            after = {k: v for k, v in self.files(output).items()
                     if not k.startswith(str(photo_edit.backup_root(photo_edit.load_report(output))))}
            self.assertEqual(after, before)



class PreviewServerEdits(EditFixture, unittest.TestCase):
    def request(self, url, data=None, origin=None):
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        headers = {'Content-Type': 'application/json'} if data is not None else {}
        if origin:
            headers['Origin'] = origin
        body = None if data is None else json.dumps(data).encode('utf-8')
        try:
            with urlopen(Request(url, body, headers), timeout=30) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()

    def test_served_preview_applies_edits_only_from_its_own_page(self):
        from photo_server import PreviewServer
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            server = PreviewServer()
            try:
                url = server.register(output)
                base = url.rsplit('/', 1)[0]
                status, page = self.request(url)
                self.assertEqual(status, 200)
                self.assertIn(b'data-revision="0"', page)
                self.assertEqual(self.request(base+'/api/state'), (200, b'{"revision": 0}'))
                self.assertEqual(self.request(base+'/..%2F..%2Fpresentation.pdf')[0], 404)
                self.assertEqual(self.request(server.origin+'/wrong-key/'+url.rsplit('/', 1)[1])[0], 404)
                edit = {'revision': 0, 'removed': [ids['red']],
                        'variants': {'1': [ids['green']], '2': [ids['blue']]}}
                self.assertEqual(self.request(base+'/api/apply', edit)[0], 403)
                self.assertEqual(self.request(base+'/api/apply', edit, 'http://evil.example')[0], 403)
                status, body = self.request(base+'/api/apply', edit, server.origin)
                self.assertEqual(status, 200, body.decode('utf-8'))
                self.assertEqual(json.loads(body)['revision'], 1)
                self.assertEqual(self.colors(output/'Вариант 1/Вариант 1.pdf'), ['green'])
                status, body = self.request(base+'/api/apply', edit, server.origin)
                self.assertEqual(status, 409)
                self.assertIn('Обновите страницу', json.loads(body)['error'])
            finally:
                server.stop()



class GroupedLayout(EditFixture, unittest.TestCase):
    TITLES = ['Вариант 1', 'Перспективное изображение №1', 'Перспективное изображение №2 рендер',
              'Материалы благоустройства. Перспективное изображение двора',
              'Перспективное изображение №3. Вариант 2', None]
    COLORS = ['#ff0000', '#00ff00', '#0000ff', '#ffff00', '#00ffff', '#ff00ff']

    def export_grouped(self, root):
        pages = [{'images': [{'box': (50, 170, 800, 500), 'color': color}],
                  **({'text': [(title, 50, 120, 18)]} if title else {})} for title, color in zip(self.TITLES, self.COLORS)]
        with contextlib.redirect_stdout(io.StringIO()):
            report = importlib.import_module('photo_export').extract(make_pdf(root, pages))
        return report, Path(report['output'])

    def pixels(self, path):
        with PdfReader(path) as reader:
            return [page.images[0].image.convert('RGB').getpixel((100, 100)) for page in reader.pages]

    def test_unnumbered_perspective_views_join_variant_one(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [{'images': [{'box': (50, 170, 800, 500), 'color': color}],
                      **({'text': [(title, 50, 120, 18)]} if title else {})} for title, color in zip(self.TITLES, self.COLORS)]
            pages += [{'images': [], 'text': [('Вариант 3', 50, 250, 40)]},
                      {'images': [{'box': (50, 170, 800, 500), 'color': '#808080'}],
                       'text': [('Перспективное изображение №5', 50, 120, 18)]}]
            with contextlib.redirect_stdout(io.StringIO()):
                report = importlib.import_module('photo_export').extract(make_pdf(root, pages))
            output = Path(report['output'])
            self.assertEqual(report['layout'], 'variants-root')
            self.assertEqual({p.name for p in output.iterdir()},
                             {'Вариант 1', 'Вариант 2', 'Вариант 3', 'Все остальное', '_Служебное', 'Просмотр.html', 'Отчёт.json'})
            self.assertEqual([(e['variant'], e.get('group'), e['folder'], e.get('variant_source')) for e in report['images']], [
                (1, 'variant', 'Вариант 1', None),
                (1, 'variant', 'Вариант 1', 'perspective_default'),
                (1, 'variant', 'Вариант 1', 'perspective_default'),
                (None, None, 'Материалы благоустройства. Перспективное изображение двора', None),
                (2, 'variant', 'Вариант 2', None),
                (None, None, 'Без подписи', None),
                (3, 'variant', 'Вариант 3', 'section_heading')])
            self.assertIn('отнесено к варианту 1', report['images'][1]['reason'])
            variant_one = output/'Вариант 1/Вариант 1.pdf'
            self.assertEqual(self.pixels(variant_one), [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
            with PdfReader(variant_one) as reader:
                self.assertEqual([item.title for item in reader.outline], [
                    'Страница 1 — Вариант 1', 'Страница 2 — Перспективное изображение №1',
                    'Страница 3 — Перспективное изображение №2 рендер'])
            self.assertEqual(self.pixels(output/'Вариант 2/Вариант 2.pdf'), [(0, 255, 255)])
            self.assertEqual(len(list((output/'Все остальное'/'Без подписи').glob('*.pdf'))), 1)
            self.assertEqual(len(list((output/'Все остальное').glob('Материалы благоустройства*/*.pdf'))), 1)
            self.assertTrue(all(e['preview'].startswith(str(Path('_Служебное/Миниатюры'))) for e in report['images']))
            import ctypes
            self.assertTrue(ctypes.windll.kernel32.GetFileAttributesW(str(output/'_Служебное')) & 0x2)
            gallery = (output/'Просмотр.html').read_text(encoding='utf-8')
            self.assertNotIn('Перспективные изображения', gallery)

    def test_results_with_a_shared_perspective_folder_are_not_edited(self):
        import photo_edit
        with tempfile.TemporaryDirectory() as root:
            report, output = self.export_grouped(root)
            report['images'][1].update(variant=None, group='perspective', folder='Перспективные изображения')
            photo_edit.write_report(output, report)
            before = self.files(output)
            with self.assertRaisesRegex(photo_edit.EditError, '3.12'):
                photo_edit.apply_edits(output, {'revision': 0, 'removed': [5], 'variants': {'1': [0, 2]}})
            self.assertEqual(self.files(output), before)

    def test_deep_output_shortens_caption_folders_to_fit_the_path_budget(self):
        caption = 'Схема планировочной организации земельного участка с указанием транспортной и пешеходной связи'
        with tempfile.TemporaryDirectory() as root:
            source = make_pdf(root, [{'images': [{'box': (50, 170, 800, 500)}], 'text': [(caption, 50, 120, 14)]}])
            output = Path(root)/('Очень длинная папка проекта '*3).strip()/'Результат с длинным именем — PDF'
            self.assertGreater(len(str(output)), 150)
            with contextlib.redirect_stdout(io.StringIO()):
                report = importlib.import_module('photo_export').extract(source, output)
            entry = report['images'][0]
            self.assertEqual(entry['title'], caption)
            self.assertEqual(Path(entry['file']).parts[0], 'Все остальное')
            self.assertLess(len(entry['folder']), len(caption))
            self.assertTrue(all(len(str(path)) <= 239 for path in output.rglob('*')))
            self.assertTrue((output/entry['file']).is_file())

    def test_earlier_layout_keeps_its_paths_when_edited(self):
        import photo_edit
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            report = photo_edit.load_report(output)
            (output/'Все остальное'/'Без подписи').rename(output/'Без подписи')
            (output/'Все остальное').rmdir()
            for entry in report['images']:
                entry['file'] = entry['file'].replace(str(Path('Все остальное'))+'\\', '')
                entry.pop('group', None)
            del report['layout']
            photo_edit.write_report(output, report)
            with self.assertRaisesRegex(photo_edit.EditError, 'Некорректный формат'):
                photo_edit.apply_edits(output, {'revision': 0, 'variants': {'P': [ids['yellow']]}})
            report, backup = photo_edit.apply_edits(output, {'revision': 0, 'removed': [ids['red']],
                                                             'variants': {'1': [ids['green'], ids['yellow']],
                                                                          '2': [ids['blue']]}})
            self.assertTrue(backup.startswith('_Резервные копии'))
            self.assertEqual(self.colors(output/'Вариант 1/Вариант 1.pdf'), ['green', 'yellow'])
            self.assertFalse((output/'Варианты').exists())
            self.assertEqual(report['images'][ids['yellow']]['file'], str(Path('Вариант 1/Вариант 1.pdf')))

    def test_results_of_3_12_keep_variants_inside_their_folder_when_edited(self):
        import photo_edit
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            report = photo_edit.load_report(output)
            (output/'Варианты').mkdir()
            for number in (1, 2):
                (output/f'Вариант {number}').rename(output/'Варианты'/f'Вариант {number}')
            for entry in report['images']:
                if entry['variant'] is not None:
                    entry['file'] = str(Path('Варианты')/entry['file'])
            report['layout'] = 'grouped'
            photo_edit.write_report(output, report)
            report, backup = photo_edit.apply_edits(output, {'revision': 0, 'removed': [ids['red']],
                                                             'variants': {'1': [ids['green']],
                                                                          '3': [ids['yellow'], ids['blue']]}})
            self.assertTrue(backup.startswith('_Служебное'))
            self.assertEqual(self.colors(output/'Варианты/Вариант 1/Вариант 1.pdf'), ['green'])
            self.assertEqual(self.colors(output/'Варианты/Вариант 3/Вариант 3.pdf'), ['yellow', 'blue'])
            self.assertFalse((output/'Вариант 3').exists())
            report, _ = photo_edit.rename_item(output, {'revision': report['revision'], 'kind': 'variant',
                                                        'target': 3, 'name': 'Вариант 3 — Южный'})
            self.assertTrue((output/'Варианты/Вариант 3 — Южный/Вариант 3 — Южный.pdf').is_file())



def post_dropped_files(hwnd, paths):
    """Send the same WM_DROPFILES message Explorer sends: a DROPFILES header and NUL-separated wide paths."""
    import ctypes
    from ctypes import wintypes
    kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p]
    names = ('\0'.join(str(p) for p in paths)+'\0\0').encode('utf-16-le')
    header = (20).to_bytes(4, 'little')+bytes(8)+bytes(4)+(1).to_bytes(4, 'little')
    memory = kernel32.GlobalAlloc(0x0042, len(header)+len(names))
    pointer = kernel32.GlobalLock(memory)
    ctypes.memmove(pointer, header+names, len(header)+len(names))
    kernel32.GlobalUnlock(memory)
    assert user32.PostMessageW(hwnd, 0x0233, memory, None)


class AppFileDrop(unittest.TestCase):
    def test_pdfs_dropped_from_explorer_start_processing(self):
        self.addCleanup(gc.collect)  # Tk cycles, see test_gui_success_and_failure_have_explicit_finished_states.
        import time
        import tkinter as tk
        from tkinter import ttk
        from unittest.mock import patch
        from photo_app import open_app
        original = tk.Tk
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)/'Папка с PDF'
            folder.mkdir()
            first = make_pdf(folder, [{'images': [{'box': (50, 180, 800, 500)}], 'text': [('Вариант 1', 50, 120, 18)]}])
            note = Path(root)/'заметка.txt'
            note.write_text('не PDF', encoding='utf-8')
            observed = {}

            def hidden():
                window = original()
                window.withdraw()
                started = time.monotonic()

                def texts():
                    widgets = []

                    def walk(widget):
                        for child in widget.winfo_children():
                            widgets.append(child)
                            walk(child)
                    walk(window)
                    labels = [window.getvar(w.cget('textvariable')) for w in widgets
                              if isinstance(w, ttk.Label) and w.cget('textvariable')]
                    log = next(w for w in widgets if isinstance(w, tk.Text)).get('1.0', 'end')
                    return labels, log

                def drop():
                    window.update_idletasks()
                    post_dropped_files(int(window.wm_frame(), 16), [folder, note])
                    window.after(200, check)

                def check():
                    labels, log = texts()
                    if any(s.startswith('Готово') for s in labels) or time.monotonic()-started > 20:
                        observed.update(labels=labels, log=log)
                        window.destroy()
                    else:
                        window.after(100, check)
                window.after(300, drop)
                return window

            with patch('tkinter.Tk', hidden):
                open_app(state_path=Path(root)/'workspace.json')
            self.assertTrue(any(s.startswith('Готово') for s in observed['labels']), observed)
            self.assertIn(first.name, observed['log'])
            self.assertIn('Пропущено, это не PDF: заметка.txt', observed['log'])
            self.assertTrue((folder/'presentation — PDF'/'Вариант 1'/'Вариант 1.pdf').is_file())



class Renames(EditFixture, unittest.TestCase):
    def captioned(self, root):
        pages = [{'images': [{'box': (50, 170, 800, 500), 'color': color}], 'text': [('Западный двор', 50, 120, 18)]}
                 for color in ('#ff0000', '#00ff00')]
        with contextlib.redirect_stdout(io.StringIO()):
            report = importlib.import_module('photo_export').extract(make_pdf(root, pages))
        return Path(report['output'])

    def test_variant_rename_keeps_its_number_for_later_edits(self):
        from photo_edit import apply_edits, rename_item
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            report, change = rename_item(output, {'revision': 0, 'kind': 'variant', 'target': '1', 'name': 'Вариант 1 — Левел'})
            self.assertEqual(change, {'kind': 'variant', 'from': 'Вариант 1', 'to': 'Вариант 1 — Левел'})
            renamed = output/'Вариант 1 — Левел'/'Вариант 1 — Левел.pdf'
            self.assertEqual(self.colors(renamed), ['red', 'green'])
            self.assertFalse((output/'Вариант 1').exists())
            self.assertEqual(report['variant_names'], {'1': 'Вариант 1 — Левел'})
            self.assertEqual({e['folder'] for e in report['images'] if e['variant'] == 1}, {'Вариант 1 — Левел'})
            self.assertIn('<h2>Вариант 1 — Левел</h2>', (output/'Просмотр.html').read_text(encoding='utf-8'))
            report, _ = apply_edits(output, {'revision': 1, 'variants': {'1': [ids['red'], ids['green'], ids['yellow']],
                                                                          '2': [ids['blue']]}})
            self.assertEqual(self.colors(renamed), ['red', 'green', 'yellow'])
            self.assertFalse((output/'Вариант 1').exists())
            self.assertEqual(report['images'][ids['yellow']]['folder'], 'Вариант 1 — Левел')

    def test_caption_folder_and_single_pdf_can_be_renamed(self):
        from photo_edit import rename_item
        with tempfile.TemporaryDirectory() as root:
            output = self.captioned(root)
            report, _ = rename_item(output, {'revision': 0, 'kind': 'folder', 'target': 'Западный двор', 'name': 'Двор'})
            self.assertFalse((output/'Все остальное'/'Западный двор').exists())
            self.assertEqual(len(list((output/'Все остальное'/'Двор').glob('*.pdf'))), 2)
            self.assertTrue(all((output/e['file']).is_file() and e['folder'] == 'Двор' for e in report['images']))
            report, change = rename_item(output, {'revision': 1, 'kind': 'file', 'target': 0, 'name': 'Двор — вид 1.pdf'})
            self.assertEqual(change['to'], 'Двор — вид 1')
            self.assertEqual(report['images'][0]['file'], str(Path('Все остальное/Двор/Двор — вид 1.pdf')))
            self.assertTrue((output/report['images'][0]['file']).is_file())
            self.assertEqual(report['revision'], 2)
            self.assertEqual([e['renamed']['kind'] for e in report['edits']], ['folder', 'file'])

    def test_invalid_or_conflicting_renames_change_nothing(self):
        from photo_edit import EditConflict, EditError, rename_item
        with tempfile.TemporaryDirectory() as root:
            output, ids = self.export(root)
            before = self.files(output)
            for payload, message in [
                    ({'kind': 'variant', 'target': '1', 'name': 'Вариант: 1'}, 'символы'),
                    ({'kind': 'variant', 'target': '1', 'name': 'Вариант 2'}, 'уже существует'),
                    ({'kind': 'variant', 'target': '1', 'name': '  '}, 'Введите'),
                    ({'kind': 'variant', 'target': '1', 'name': 'CON'}, 'зарезервировано'),
                    ({'kind': 'variant', 'target': '1', 'name': '_Служебное'}, 'зарезервировано'),
                    ({'kind': 'variant', 'target': '1', 'name': 'все остальное'}, 'служебной'),
                    ({'kind': 'folder', 'target': 'Без подписи', 'name': 'Прочее'}, 'нельзя'),
                    ({'kind': 'file', 'target': ids['red'], 'name': 'Отдельно'}, 'вместе с вариантом'),
                    ({'kind': 'variant', 'target': '7', 'name': 'Новый'}, 'не найден'),
                    ({'kind': 'variant', 'target': '1', 'name': 'x'*300}, 'длинное')]:
                with self.subTest(payload=payload), self.assertRaisesRegex(EditError, message):
                    rename_item(output, dict(payload, revision=0))
            with self.assertRaises(EditConflict):
                rename_item(output, {'revision': 3, 'kind': 'variant', 'target': '1', 'name': 'Новое имя'})
            self.assertEqual(self.files(output), before)

    def test_failed_rename_restores_the_previous_names(self):
        import photo_edit
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            output, _ = self.export(root)
            before = self.files(output)
            with patch.object(photo_edit, 'write_report', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    photo_edit.rename_item(output, {'revision': 0, 'kind': 'variant', 'target': '2', 'name': 'Другой'})
            self.assertEqual(self.files(output), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
