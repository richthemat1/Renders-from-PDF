"""Real browser checks for copying PDF pages/variants and fitting the preview."""
from io import BytesIO
import unittest

from PIL import Image
from playwright.sync_api import expect
from pypdf import PdfReader

from photo_edit import load_report
import test_desktop_ui as fixture


class CopyPreviewUI(unittest.TestCase):
    setUpClass = classmethod(fixture.DesktopUI.setUpClass.__func__)
    tearDownClass = classmethod(fixture.DesktopUI.tearDownClass.__func__)
    setUp = fixture.DesktopUI.setUp
    card = fixture.DesktopUI.card
    edit = fixture.DesktopUI.edit
    save = fixture.DesktopUI.save

    def test_selected_copy_is_undoable_then_saved_and_independent(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Ещё', exact=True).click()
        self.page.get_by_role('menuitem', name='Создать копии выбранных', exact=True).click()
        self.page.get_by_role('button', name='Создать копии', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(5)
        expect(self.card(4)).to_contain_text('Копия')
        self.page.keyboard.press('Control+z')
        expect(self.page.locator('.asset-card')).to_have_count(4)
        self.card(0).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Ещё', exact=True).click()
        self.page.get_by_role('menuitem', name='Создать копии выбранных', exact=True).click()
        self.page.get_by_role('button', name='Создать копии', exact=True).click()
        # A pending copy has a real full-size preview from its saved source.
        self.card(4).locator('.cover-button').dblclick()
        expect(self.page.locator('.preview-image-canvas img')).to_have_attribute('src', __import__('re').compile(r'/preview/0\?revision=0$'))
        self.page.get_by_role('button', name='Закрыть диалог', exact=True).click()
        self.save()
        report = load_report(self.output)
        self.assertEqual(report['images'][4]['copy_of'], 0)
        self.assertEqual(len(PdfReader(self.output/report['images'][0]['file']).pages), 3)
        self.card(4).get_by_role('checkbox').check()
        self.page.keyboard.press('Delete')
        self.save()
        report = load_report(self.output)
        self.assertFalse(report['images'][0].get('removed'))
        self.assertTrue(report['images'][4]['removed'])
        self.assertEqual(self.errors, [])

    def test_whole_variant_copy_ignores_search_and_keeps_original_pdf(self):
        original = (self.output/self.report['images'][0]['file']).read_bytes()
        self.edit()
        self.page.locator('nav').get_by_role('button', name='Вариант 1 2', exact=True).click()
        self.page.get_by_role('searchbox').fill('Северный')
        expect(self.page.locator('.asset-card')).to_have_count(1)
        self.page.get_by_role('button', name='Создать копию PDF варианта', exact=True).click()
        expect(self.page.get_by_role('dialog')).to_contain_text('2')
        self.page.get_by_role('button', name='Создать копии', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(2)
        self.save()
        report = load_report(self.output)
        self.assertEqual([image['variant'] for image in report['images']], [1, 1, 2, None, 3, 3])
        self.assertEqual((self.output/report['images'][0]['file']).read_bytes(), original)
        self.assertEqual(len(PdfReader(self.output/report['images'][4]['file']).pages), 2)
        self.page.get_by_role('button', name='Отменить сохранение', exact=True).click()
        expect(self.page.locator('nav').get_by_role('button', name='Все изображения 4', exact=True)).to_be_visible()
        expect(self.page.locator('.asset-card')).to_have_count(4)
        self.assertEqual(len(load_report(self.output)['images']), 4)
        self.assertEqual(self.errors, [])

    def test_restored_page_can_be_copied_then_undo_restores_its_previous_removal(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.keyboard.press('Delete')
        self.save()
        self.page.locator('nav').get_by_role('button', name='Убранные 1', exact=True).click()
        self.card(0).get_by_role('button', name='Вернуть изображение', exact=True).click()
        self.page.locator('nav').get_by_role('button', name='Все изображения 4', exact=True).click()
        self.card(0).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Ещё', exact=True).click()
        self.page.get_by_role('menuitem', name='Создать копии выбранных', exact=True).click()
        self.page.get_by_role('button', name='Создать копии', exact=True).click()
        self.save()
        report = load_report(self.output)
        self.assertFalse(report['images'][0].get('removed'))
        self.assertEqual(report['images'][4]['copy_of'], 0)
        self.page.get_by_role('button', name='Отменить сохранение', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(3)
        report = load_report(self.output)
        self.assertTrue(report['images'][0]['removed'])
        self.assertEqual(len(report['images']), 4)
        self.assertEqual(self.errors, [])

    def test_copy_destination_menu_inside_modal_can_choose_existing_and_new_variants(self):
        self.edit()
        self.card(3).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Ещё', exact=True).click()
        self.page.get_by_role('menuitem', name='Создать копии выбранных', exact=True).click()
        self.page.get_by_label('Куда поместить копии').click()
        self.page.get_by_role('option', name='Вариант 2', exact=True).click(timeout=3000)
        self.page.get_by_role('button', name='Создать копии', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(2)
        self.assertEqual(load_report(self.output)['revision'], 0)
        self.save()
        self.assertEqual(load_report(self.output)['images'][4]['variant'], 2)
        self.card(4).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Ещё', exact=True).click()
        self.page.get_by_role('menuitem', name='Создать копии выбранных', exact=True).click()
        self.page.get_by_label('Куда поместить копии').click()
        self.page.get_by_role('option', name='В новый вариант', exact=True).click(timeout=3000)
        self.page.get_by_label('Номер нового варианта').fill('8')
        self.page.get_by_role('button', name='Создать копии', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(1)
        self.save()
        self.assertEqual(load_report(self.output)['images'][5]['variant'], 8)
        self.assertEqual(self.errors, [])

    def test_fit_portrait_and_landscape_can_zoom_out_and_survive_small_windows(self):
        for image_size in [(200, 1600), (1600, 200)]:
            image = Image.new('RGB', image_size, '#6688bb')
            buffer = BytesIO()
            image.save(buffer, 'PNG')
            self.page.route('**/preview/0?*', lambda route: route.fulfill(body=buffer.getvalue(), content_type='image/png'))
            for width, height in [(1380, 1000), (800, 620), (360, 620)]:
                self.page.set_viewport_size({'width': width, 'height': height})
                self.card(0).locator('.cover-button').click()
                self.page.wait_for_function('''() => {
                    const image = document.querySelector('.preview-image-canvas img');
                    const viewport = document.querySelector('.preview-image-scroll');
                    if (!image?.naturalWidth || !viewport.clientWidth) return false;
                    const fit = Math.min(viewport.clientWidth / image.naturalWidth, viewport.clientHeight / image.naturalHeight);
                    return Math.abs(image.getBoundingClientRect().width - image.naturalWidth * fit) < 1;
                }''')
                scroll = self.page.locator('.preview-image-scroll')
                self.page.evaluate('() => new Promise(requestAnimationFrame)')
                dimensions = scroll.evaluate('''(el) => ({width:el.clientWidth,height:el.clientHeight,
                    scrollWidth:el.scrollWidth,scrollHeight:el.scrollHeight})''')
                self.assertLessEqual(dimensions['scrollWidth'], dimensions['width']+1)
                self.assertLessEqual(dimensions['scrollHeight'], dimensions['height']+1)
                self.page.wait_for_function('''() => {
                    const el = document.querySelector('.preview-image-scroll');
                    return el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1;
                }''')
                before = self.page.locator('.preview-image-canvas img').bounding_box()
                self.page.get_by_role('button', name='Уменьшить', exact=True).click()
                expect(self.page.locator('.preview-zoom-tools')).to_contain_text('50%')
                self.page.wait_for_function('''width =>
                    Math.abs(document.querySelector('.preview-image-canvas img').getBoundingClientRect().width - width / 2) < 1
                ''', arg=before['width'])
                after = self.page.locator('.preview-image-canvas img').bounding_box()
                self.assertLess(after['width'], before['width'])
                self.assertLess(after['height'], before['height'])
                self.page.get_by_role('button', name='Вместить изображение', exact=True).click()
                expect(self.page.locator('.preview-zoom-tools')).to_contain_text('100%')
                self.assertLessEqual(self.page.locator('dialog').evaluate('(el)=>el.scrollHeight-el.clientHeight'), 1)
                self.page.get_by_role('button', name='Закрыть диалог', exact=True).click()
            self.page.unroute('**/preview/0?*')
        self.assertEqual(self.errors, [])
