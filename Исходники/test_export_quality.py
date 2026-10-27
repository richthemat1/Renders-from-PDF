"""Quality regressions reproduced from real presentations with small offline PDFs."""
import json
from html.parser import HTMLParser
from pathlib import Path
import re
import tempfile
import unittest

from PIL import Image
from reportlab.pdfgen import canvas

import photo_export
from photo_layout import clean_text, text_lines
from test_photos import make_pdf


def active(report):
    """Cards in the result; filtered service elements are kept only as removed, restorable copies."""
    return [entry for entry in report['images'] if not entry.get('removed')]


def service(report):
    return [entry for entry in report['images'] if entry.get('suppressed')]


class ExportQuality(unittest.TestCase):
    def export(self, root, pages):
        return photo_export.extract(make_pdf(root, pages))

    def test_repeated_small_identity_at_same_position_is_suppressed_but_large_renders_stay(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [{'images': [{'box': (50, 180, 800, 450)},
                                  {'box': (50, 30, 200, 45), 'size': (800, 180), 'color': '#dddddd'}],
                      'text': [(f'Вид двора {number}', 50, 120, 18)]} for number in range(3)]
            report = self.export(root, pages)
            self.assertEqual(len(active(report)), 3)
            self.assertEqual([item['page'] for item in active(report)], [1, 2, 3])
            self.assertEqual(len(report['suppressed']), 3)
            self.assertEqual([item['image'] for item in report['suppressed']],
                             [report['images'].index(entry) for entry in service(report)])
            self.assertTrue(all(item['suppression_code'] == 'repeated_static' for item in report['suppressed']))
            self.assertTrue(all(item['bbox'] == [50, 30, 250, 75] for item in report['suppressed']))

    def test_same_size_different_images_and_moving_photos_are_not_static_assets(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [{'images': [{'box': (50, 180, 800, 450)},
                                  {'box': (50, 30, 200, 45), 'size': (800, 180), 'color': color},
                                  {'box': (350 + index*80, 670, 120, 90), 'size': (360, 270)}]}
                     for index, color in enumerate(['#ff0000', '#00ff00', '#0000ff'])]
            report = self.export(root, pages)
            self.assertEqual(len(report['images']), 9)
            self.assertEqual(report['suppressed'], [])

    def test_tiny_fragments_are_suppressed_after_their_touching_tiles_are_assembled(self):
        with tempfile.TemporaryDirectory() as root:
            pages = [{'images': [{'box': (50, 180+i*30, 800, 30), 'size': (1600, 60), 'color': color}
                                 for i, color in enumerate(['#ff0000', '#00ff00', '#0000ff'])] +
                                [{'box': (900, 700, 30, 30), 'size': (80, 80)}]}]
            report = self.export(root, pages)
            self.assertEqual(len(active(report)), 1)
            self.assertEqual(active(report)[0]['size'], [1600, 180])
            self.assertEqual(active(report)[0]['method'], 'stitched_originals')
            self.assertEqual(report['suppressed'][0]['suppression_code'], 'small_fragment')
            self.assertEqual([entry['suppression_code'] for entry in service(report)], ['small_fragment'])

    def test_low_resolution_large_photo_and_wide_banner_are_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            report = self.export(root, [{'images': [
                {'box': (50, 180, 800, 450), 'size': (120, 80)},
                {'box': (50, 100, 800, 40), 'size': (1600, 80)}]}])
            self.assertEqual(len(report['images']), 2)
            self.assertEqual(report['suppressed'], [])

    def occlusion_pdf(self, root, *, transparent=False, partial=False, clip=False, vector=False, reverse=False, mask=False):
        root = Path(root)
        source = root/'layered.pdf'
        back, front = root/'back.png', root/'front.png'
        Image.new('RGB', (600, 400), '#dd0000').save(back)
        Image.new('RGBA' if mask else 'RGB', (800, 500), (0, 180, 0, 100) if mask else '#00bb00').save(front)
        pdf = canvas.Canvas(str(source), pagesize=(1000, 800))
        def small():
            pdf.drawImage(str(back), 100, 300, 300, 200)
        def cover():
            pdf.saveState()
            if transparent:
                pdf.setFillAlpha(.5)
            if clip:
                path = pdf.beginPath(); path.rect(100, 300, 100, 200)
                pdf.clipPath(path, stroke=0, fill=0)
            if vector:
                pdf.setFillColorRGB(0, 1, 0)
                pdf.rect(100, 200, 600, 350, stroke=0, fill=1)
            else:
                pdf.drawImage(str(front), 100, 200, 200 if partial else 600, 350, mask='auto' if mask else None)
            pdf.restoreState()
        (cover if reverse else small)()
        (small if reverse else cover)()
        pdf.showPage(); pdf.save()
        return source

    def test_only_a_later_proven_opaque_paint_removes_a_fully_occluded_image(self):
        for vector in (False, True):
            with self.subTest(vector=vector), tempfile.TemporaryDirectory() as root:
                report = photo_export.extract(self.occlusion_pdf(root, vector=vector))
                if vector:
                    self.assertEqual(active(report), [])
                else:
                    self.assertEqual(len(active(report)), 1)
                    self.assertEqual(active(report)[0]['size'], [800, 500])
                self.assertEqual([entry['suppression_code'] for entry in service(report)], ['fully_occluded'])
                self.assertEqual(report['suppressed'][0]['suppression_code'], 'fully_occluded')
                self.assertEqual(report['suppressed'][0]['bbox'], [100, 300, 400, 500])

    def test_transparent_partial_clipped_or_earlier_cover_does_not_hide_the_back_image(self):
        for options in ({'transparent': True}, {'partial': True}, {'clip': True}, {'reverse': True}, {'mask': True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as root:
                report = photo_export.extract(self.occlusion_pdf(root, **options))
                self.assertEqual(len(report['images']), 2)
                self.assertEqual(report['suppressed'], [])

    def test_partly_off_page_image_cannot_inherit_the_page_variant(self):
        with tempfile.TemporaryDirectory() as root:
            report = self.export(root, [{'images': [{'box': (-60, 300, 700, 500)}],
                                         'text': [('ВАРИАНТ 7 | ВИЗУАЛИЗАЦИЯ', 50, 40, 18)]}])
            self.assertIsNone(report['images'][0]['variant'])

    def test_plan_caption_cannot_inherit_the_page_variant(self):
        with tempfile.TemporaryDirectory() as root:
            report = self.export(root, [{'images': [{'box': (50, 300, 800, 450)}],
                                         'text': [('ВАРИАНТ 7 | ВИЗУАЛИЗАЦИЯ', 50, 40, 18),
                                                  ('Ситуационный план', 50, 260, 18)]}])
            self.assertIsNone(report['images'][0]['variant'])
            self.assertEqual(report['images'][0]['title'], 'Ситуационный план')

    def test_overlaid_words_are_deduplicated_geometrically_without_removing_real_repetition(self):
        word = lambda text, x0, x1, top=20: {'text': text, 'x0': x0, 'x1': x1,
                                            'top': top, 'bottom': top+14, 'size': 14}
        words = [word('Перспективное', 50, 170), word('изображение', 175, 280), word('№3', 285, 310),
                 word('Перспективное', 50, 170), word('изображение', 175, 280), word('№', 285, 302),
                 word('3', 302, 310), word('очень', 50, 95, 60), word('очень', 100, 145, 60)]
        self.assertEqual([line['text'] for line in text_lines(words)],
                         ['Перспективное изображение №3', 'очень очень'])

    def test_newer_overlaid_number_wins_with_its_coherent_caption(self):
        def word(text, x0, x1, top=20):
            return {'text': text, 'x0': x0, 'x1': x1, 'top': top, 'bottom': top+14, 'size': 14}
        words = [word('Перспективное', 50, 170, 21), word('изображение', 175, 280, 21),
                 word('№4', 285, 310, 21), word('Перспективное', 50, 172),
                 word('изображение', 176, 282), word('№6', 288, 313), word('(Вариант', 319, 386), word('1)', 390, 405)]
        self.assertEqual(text_lines(words)[0]['text'], 'Перспективное изображение №6 (Вариант 1)')

    def test_selected_marker_names_a_centered_render_and_uses_only_unambiguous_table_relation(self):
        with tempfile.TemporaryDirectory() as root:
            source = make_pdf(root, [{'images': [{'box': (300, 200, 500, 400)}],
                                      'text': [('ВПМ', 50, 160, 18)]}])
            report = photo_export.extract(source)
            self.assertEqual(report['images'][0]['title'], 'ВПМ')
            self.assertIsNone(report['images'][0]['variant'])
        # A relation must originate in an actual table cell, never in free page text.
        from photo_layout import prepare_selected_variants, selected_caption_with_variant
        pages = [{'number': 2, 'selection_references': [{'marker': 'ВПМ', 'variant': 2,
                   'table_bbox': [10, 100, 900, 700], 'cell_text': 'ВПМ 2 вариант'}]}]
        prepare_selected_variants(pages)
        match = {'status': 'named', 'title': 'ВПМ', 'candidates': ['ВПМ'], 'reason': ''}
        selected = selected_caption_with_variant(pages[0], match)
        self.assertEqual((selected['variant_source'], selected['variant_source_page']), ('selection_table', 2))
        self.assertEqual(selected['title'], 'Вариант 2 — ВПМ')
        pages.append({'number': 3, 'selection_references': [{'marker': 'ВПМ', 'variant': 1}]})
        prepare_selected_variants(pages)
        self.assertEqual(selected_caption_with_variant(pages[0], match)['title'], 'ВПМ')

    def test_actual_table_cell_links_selected_render_but_conflicting_tables_do_not(self):
        from reportlab.platypus import Table, TableStyle
        for relation, conflict, expected in [('ВПМ 2 вариант', False, 2),
                                               ('Выбранный вариант 2', False, 2),
                                               ('ВПМ 2 вариант', True, None)]:
            with self.subTest(relation=relation, conflict=conflict), tempfile.TemporaryDirectory() as root:
                source = Path(root)/'table.pdf'
                image = Path(root)/'render.png'
                Image.new('RGB', (600, 400), '#447799').save(image)
                pdf = canvas.Canvas(str(source), pagesize=(1000, 800))
                rows = [['Проектные материалы:', relation]]
                if conflict:
                    rows.append(['Другая запись:', 'ВПМ 3 вариант'])
                table = Table(rows, colWidths=[250, 350], rowHeights=32)
                table.setStyle(TableStyle([('FONTNAME', (0, 0), (-1, -1), 'FixtureArial'),
                                           ('GRID', (0, 0), (-1, -1), .5, '#000000')]))
                table.wrapOn(pdf, 600, 100); table.drawOn(pdf, 50, 500)
                pdf.showPage()
                pdf.drawImage(str(image), 300, 200, 500, 400)
                pdf.setFont('FixtureArial', 18); pdf.drawString(50, 620, 'ВПМ')
                pdf.showPage(); pdf.save()
                report = photo_export.extract(source)
                entry = report['images'][0]
                self.assertEqual(entry['variant'], expected)
                self.assertEqual(entry['source_caption'], 'ВПМ')
                if expected:
                    self.assertEqual(entry['variant_source_page'], 1)

    def test_disk_folders_shorten_on_words_and_keep_full_display_names_without_hashes(self):
        caption = 'Материалы благоустройства территории и дополнительных общественных пространств второй очереди'
        self.assertEqual(clean_text('- Стеноваяпанель'), 'Стеновая панель')
        self.assertEqual(photo_export.safe_name(caption, 28), 'Материалы благоустройства')
        with tempfile.TemporaryDirectory() as root:
            pages = [{'images': [{'box': (50, 180, 800, 450)}], 'text': [(title, 50, 120, 18)]}
                     for title in ['Двор / Восток', 'Двор : Восток']]
            report = self.export(root, pages)
            folders = [item['folder'] for item in report['images']]
            self.assertEqual(folders, ['Двор — Восток', 'Двор — Восток (2)'])
            self.assertEqual(report['folder_labels'], {'Двор — Восток': 'Двор / Восток',
                                                      'Двор — Восток (2)': 'Двор : Восток'})
            stored = json.loads((Path(report['output'])/'Отчёт.json').read_text(encoding='utf-8'))
            self.assertEqual(stored['folder_labels'], report['folder_labels'])

    def test_standalone_gallery_displays_full_labels_and_keeps_raw_edit_keys(self):
        class Elements(HTMLParser):
            def __init__(self):
                super().__init__(); self.elements = []
            def handle_starttag(self, tag, attrs):
                self.elements.append((tag, dict(attrs)))
        with tempfile.TemporaryDirectory() as root:
            report = self.export(root, [{'images': [{'box': (50, 180, 800, 450), 'size': (120, 80)}],
                                         'text': [('Материалы благоустройства', 50, 120, 18)]}])
            folder = report['images'][0]['folder']
            full_label = 'Материалы благоустройства и общественные пространства второй очереди'
            report['folder_labels'][folder] = full_label
            report['images'][0]['title'] = 'Рендер двора'
            photo_export.write_gallery(Path(report['output']), report)
            gallery = (Path(report['output'])/'Просмотр.html').read_text(encoding='utf-8')
            parsed = Elements(); parsed.feed(gallery)
            nav = next(attrs for tag, attrs in parsed.elements if tag == 'a' and 'nav-item' in attrs.get('class', ''))
            card = next(attrs for tag, attrs in parsed.elements if tag == 'article')
            thumbnail = next(attrs for tag, attrs in parsed.elements if tag == 'img' and attrs.get('src'))
            self.assertEqual(nav['title'], full_label)
            self.assertEqual(nav['data-folder'], folder)
            self.assertEqual(card['data-folder'], folder)
            self.assertIn(full_label, card['data-search'])
            self.assertIn(full_label, gallery)
            self.assertEqual((thumbnail['width'], thumbnail['height']), ('120', '80'))


if __name__ == '__main__':
    unittest.main()
