"""UI regressions from the 3.22 real-presentation review."""
import json
import unittest

from PIL import Image
from playwright.sync_api import expect

import test_desktop_ui as desktop_ui


class RealQualityUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        desktop_ui.DesktopUI.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        desktop_ui.DesktopUI.tearDownClass.__func__(cls)

    setUp = desktop_ui.DesktopUI.setUp
    card = desktop_ui.DesktopUI.card
    edit = desktop_ui.DesktopUI.edit

    def reload_report(self, report):
        (self.output / 'Отчёт.json').write_text(
            json.dumps(report, ensure_ascii=False), encoding='utf-8')
        self.jobs.remember(report, processed=True)
        self.page.reload()
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        self.page.locator('.history-row').first.click()
        expect(self.page.locator('.asset-card')).to_have_count(
            sum(not image.get('removed') for image in report['images']))
        self.page.evaluate('document.fonts.ready')

    def test_short_disk_folder_uses_full_caption_in_navigation_cards_search_and_rename(self):
        disk_folder = 'Материа — 041ccedd'
        caption = 'Материалы благоустройства и озеленения территории административно-делового комплекса'
        report = self.report
        report['folder_labels'] = {disk_folder: caption}
        report['images'][3].update(folder=disk_folder, title='Двор со стороны площади')
        self.reload_report(report)
        group = self.card(3).locator('.asset-group')
        expect(group).to_have_text(caption)
        expect(group).to_have_attribute('title', caption)
        self.assertNotIn(disk_folder, self.page.locator('.viewer-content').inner_text())
        self.page.get_by_role('searchbox').fill('озеленения территории')
        expect(self.page.locator('.asset-card')).to_have_count(1)
        self.page.get_by_role('searchbox').fill('')
        navigation = self.page.locator('nav')
        folder = navigation.get_by_role('button', name=caption + ' 1', exact=True)
        expect(folder).to_have_attribute('title', caption)
        folder.click()
        expect(self.page.locator('.library-header h2')).to_have_text(caption)
        self.edit()
        self.page.get_by_role('button', name='Переименовать группу', exact=True).click()
        expect(self.page.get_by_label('Новое имя')).to_have_value(caption)
        requests = []

        def capture_rename(route):
            requests.append(route.request.post_data_json)
            route.fulfill(status=400, json={'error': 'Проверка исходного имени папки'})

        self.page.route('**/api/results/*/rename', capture_rename)
        self.page.get_by_label('Новое имя').fill(caption + ' — проверено')
        with self.page.expect_response(lambda response: response.url.endswith('/rename')):
            self.page.get_by_role('button', name='Переименовать', exact=True).click()
        self.assertEqual(requests[0]['target'], disk_folder)
        self.assertEqual(requests[0]['name'], caption + ' — проверено')
        self.assertEqual(self.errors, [])

    def test_short_variant_disk_name_uses_full_caption_in_groups_and_editor_actions(self):
        disk_folder = 'Вариан — 34ad21ef'
        caption = 'Вариант 1. Комплекс с открытым двором и озеленением перед главным фасадом'
        report = self.report
        report['variant_names'] = {'1': disk_folder}
        report['folder_labels'] = {disk_folder: caption}
        for image in report['images']:
            if image['variant'] == 1:
                image['folder'] = disk_folder
        self.reload_report(report)
        expect(self.card(0).locator('.asset-group')).to_have_text(caption)
        self.page.locator('nav').get_by_role('button', name=caption + ' 2', exact=True).click()
        expect(self.page.locator('.library-header h2')).to_have_text(caption)
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Перенести в вариант', exact=True).click()
        expect(self.page.get_by_role('menuitem', name=caption, exact=True)).to_be_visible()
        self.page.keyboard.press('Escape')
        self.page.get_by_role('button', name='Переименовать группу', exact=True).click()
        expect(self.page.get_by_label('Новое имя')).to_have_value(caption)

    def test_tiny_original_stays_centered_at_natural_size_even_with_an_enlarged_preview(self):
        image = self.report['images'][3]
        image['size'] = [117, 45]
        preview = self.output / image['preview']
        Image.new('RGB', (640, 246), '#d9cfaf').save(preview)
        self.reload_report(self.report)
        tiny = self.card(3).locator('.asset-image img')
        expect(tiny).to_have_js_property('naturalWidth', 640)
        for width in (1440, 800):
            self.page.set_viewport_size({'width': width, 'height': 900})
            geometry = self.page.evaluate('''() => {
                const bounds = selector => document.querySelector(selector).getBoundingClientRect().toJSON();
                return {cover:bounds('[data-id="3"] .cover-button'),
                    image:bounds('[data-id="3"] .asset-image img'),
                    largeCover:bounds('[data-id="0"] .cover-button'),
                    largeImage:bounds('[data-id="0"] .asset-image img')};
            }''')
            cover, bounds = geometry['cover'], geometry['image']
            self.assertAlmostEqual(bounds['width'], 117, delta=.5)
            self.assertAlmostEqual(bounds['height'], 45, delta=.5)
            self.assertAlmostEqual(bounds['x'] + bounds['width'] / 2,
                                   cover['x'] + cover['width'] / 2, delta=.5)
            self.assertAlmostEqual(bounds['y'] + bounds['height'] / 2,
                                   cover['y'] + cover['height'] / 2, delta=.5)
            large = self.card(0).locator('.asset-image img')
            large_cover, large_bounds = geometry['largeCover'], geometry['largeImage']
            self.assertGreater(large_bounds['width'], 117)
            self.assertLessEqual(large_bounds['width'], large_cover['width'] + .5)
            self.assertLessEqual(large_bounds['height'], large_cover['height'] + .5)
            expect(large).to_have_css('object-fit', 'contain')

    def test_brand_tagline_fits_both_sidebar_widths_without_wrapping_or_clipping(self):
        self.page.evaluate('document.fonts.ready')
        for width in (1380, 900, 760, 500, 360):
            self.page.set_viewport_size({'width': width, 'height': 900})
            if width <= 960:
                self.page.get_by_role('button', name='Открыть меню', exact=True).click()
            sidebar = self.page.locator('.sidebar')
            self.assertAlmostEqual(sidebar.bounding_box()['width'], 248 if width > 960 else 270, delta=.5)
            tagline = self.page.locator('.brand div > span')
            expect(tagline).to_have_text('Материалы в одном месте.')
            expect(tagline).to_have_css('white-space', 'nowrap')
            geometry = tagline.evaluate('''element => {
                const brand = element.closest('.brand');
                const close = brand.querySelector('button');
                const rect = element.getBoundingClientRect();
                const button = close.getBoundingClientRect();
                const brandRect = brand.getBoundingClientRect();
                const range = document.createRange(); range.selectNodeContents(element);
                return {lines:range.getClientRects().length, right:rect.right,
                    limit:button.width ? button.left : brandRect.right - 16,
                    scroll:brand.scrollWidth, width:brand.clientWidth,
                    overflow:getComputedStyle(element).overflow,
                    ellipsis:getComputedStyle(element).textOverflow};
            }''')
            self.assertEqual(geometry['lines'], 1)
            self.assertLessEqual(geometry['right'], geometry['limit'] + .5)
            self.assertLessEqual(geometry['scroll'], geometry['width'])
            self.assertNotEqual(geometry['overflow'], 'hidden')
            self.assertNotEqual(geometry['ellipsis'], 'ellipsis')
            if width <= 960:
                self.page.get_by_role('button', name='Закрыть меню', exact=True).click()

    def test_history_and_recent_rows_have_no_detached_chevron_and_open_from_keyboard(self):
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        expect(self.page.locator('.history-row > .lucide-chevron-right')).to_have_count(0)
        row = self.page.locator('.history-row').first
        row.focus()
        self.page.keyboard.press('Enter')
        expect(self.page.locator('.asset-card')).to_have_count(4)
        self.page.get_by_role('button', name='Обработка PDF', exact=True).click()
        recent = self.page.locator('.recent .history-row').first
        expect(recent).to_be_visible()
        # The3.26 Figma row restores a trailing arrow inside the row layout.
        # It must remain within the clickable row instead of floating beside it.
        arrow = recent.locator(':scope > .lucide-chevron-right')
        expect(arrow).to_have_count(1)
        row_box, arrow_box = recent.bounding_box(), arrow.bounding_box()
        self.assertLessEqual(arrow_box['x'] + arrow_box['width'], row_box['x'] + row_box['width'])
        self.assertGreaterEqual(arrow_box['x'], row_box['x'])
        recent.focus()
        self.page.keyboard.press('Enter')
        expect(self.page.locator('.asset-card')).to_have_count(4)
        self.assertEqual(self.errors, [])

    def test_review_counts_use_retained_images_and_suppression_is_a_separate_summary(self):
        report = self.report
        report['images'][0]['removed'] = True
        report['images'][2].update(variant=None, status='review', folder='На проверку')
        report['images'][3].update(variant=None, status='unsigned', folder='Без подписи', title=None)
        report['counts_by_status'] = {'named': 18, 'review': 5, 'unsigned': 79}
        report['suppressed'] = [{'page': 2, 'suppression_code': 'small_raster'} for _ in range(80)]
        report['warnings'] = []
        report['errors'] = []
        self.reload_report(report)
        navigation = self.page.locator('nav')
        expect(navigation.get_by_role('button', name='Все изображения 3', exact=True)).to_be_visible()
        expect(navigation.get_by_role('button', name='На проверку 1', exact=True)).to_be_visible()
        expect(navigation.get_by_role('button', name='Без подписи 1', exact=True)).to_be_visible()
        expect(self.page.locator('.review-summary-count')).to_have_text('Нужно проверить: 2')
        expect(self.page.locator('.review-summary-services')).to_contain_text('Пропущены служебные элементы: 80')
        expect(self.page.locator('.report-notes')).to_have_count(0)
        self.assertEqual(self.page.locator('.asset-card').evaluate_all(
            'cards => cards.map(card => Number(card.dataset.id))'), [1, 2, 3])
        self.page.get_by_role('button', name='Разобрать', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(2)
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
