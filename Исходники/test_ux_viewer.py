"""Focused UX review regressions using the real loopback app and PDF transactions."""
import json
import re
import unittest

from playwright.sync_api import expect

from photo_edit import load_report
import test_desktop_ui as fixture


class ViewerUX(unittest.TestCase):
    setUpClass = classmethod(fixture.DesktopUI.setUpClass.__func__)
    tearDownClass = classmethod(fixture.DesktopUI.tearDownClass.__func__)
    setUp = fixture.DesktopUI.setUp
    card = fixture.DesktopUI.card
    edit = fixture.DesktopUI.edit
    def save(self):
        with self.page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/apply') and response.status == 200):
            self.page.get_by_role('button', name='Сохранить', exact=True).click()
        expect(self.page.locator('.editor-save')).to_have_text('Сохранить')
        expect(self.page.locator('.editor-save')).to_be_disabled()

    def test_checkbox_focus_all_delete_undo_and_save_shortcuts(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.keyboard.press('Control+a')
        expect(self.page.locator('.asset-card input:checked')).to_have_count(4)
        self.page.keyboard.press('Delete')
        expect(self.page.locator('.asset-card')).to_have_count(0)
        self.page.keyboard.press('Control+z')
        expect(self.page.locator('.asset-card')).to_have_count(4)
        self.card(0).get_by_role('checkbox').check()
        self.page.keyboard.press('Delete')
        self.card(1).get_by_role('checkbox').focus()
        with self.page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/apply') and response.status == 200):
            self.page.keyboard.press('Control+s')
        expect(self.page.locator('.editor-save')).to_have_text('Сохранить')
        self.assertTrue(load_report(self.output)['images'][0]['removed'])
        self.assertEqual(self.errors, [])

    def test_saved_removed_card_is_visible_and_can_be_restored(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.keyboard.press('Delete')
        self.save()
        self.page.locator('nav').get_by_role('button', name='Убранные 1', exact=True).click()
        expect(self.card(0)).to_be_visible()
        self.card(0).get_by_role('button', name='Вернуть изображение', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(0)
        self.save()
        self.page.locator('nav').get_by_role('button', name='Все изображения 4', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(4)
        report = load_report(self.output)
        self.assertFalse(report['images'][0].get('removed'))
        self.assertTrue((self.output / report['images'][0]['file']).is_file())

    def test_saved_change_can_be_undone_from_notice(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.keyboard.press('Delete')
        self.save()
        self.page.get_by_role('button', name='Отменить сохранение', exact=True).click()
        expect(self.page.locator('.asset-card')).to_have_count(4)
        report = load_report(self.output)
        self.assertFalse(report['images'][0].get('removed'))
        self.assertEqual(report['revision'], 2)

    def test_readable_warning_can_reveal_related_card(self):
        report = load_report(self.output)
        report['warnings'] = [{'page': 3, 'reason': 'OCR: fixture error'}]
        (self.output / 'Отчёт.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        self.page.reload()
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        self.page.locator('.history-row').first.click()
        self.page.locator('.report-notes summary').first.click()
        expect(self.page.locator('.report-issue-list')).to_contain_text('Проверьте изображение вручную')
        expect(self.page.locator('.report-notes pre')).to_have_count(0)
        self.page.get_by_role('searchbox').fill('нет такой подписи')
        self.page.get_by_role('button', name='Показать страницу', exact=True).click()
        expect(self.page.get_by_role('searchbox')).to_have_value('')
        expect(self.card(2)).to_be_in_viewport()

    def test_return_from_variant_and_original_order_after_move(self):
        self.edit()
        self.card(3).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Перенести в вариант', exact=True).click()
        self.page.get_by_role('menuitem', name='Вариант 1', exact=True).click()
        self.assertEqual(self.page.locator('.asset-card').evaluate_all('(cards) => cards.map(c => Number(c.dataset.id))'), [0, 1, 2, 3])
        self.save()
        self.card(3).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Перенести в вариант', exact=True).click()
        self.page.get_by_role('menuitem', name='Вернуть в папку подписи / Все остальное', exact=True).click()
        self.save()
        self.assertIsNone(load_report(self.output)['images'][3]['variant'])

    def test_group_moves_to_start_and_variant_keeps_its_own_order(self):
        self.edit()
        self.card(3).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Перенести в вариант', exact=True).click()
        self.page.get_by_role('menuitem', name='Вариант 1', exact=True).click()
        self.page.locator('nav').get_by_role('button', name='Вариант 1 3', exact=True).click()
        self.card(1).get_by_role('checkbox').check()
        self.card(3).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Ещё', exact=True).click()
        self.page.get_by_role('menuitem', name='В начало варианта', exact=True).click()
        self.assertEqual(self.page.locator('.asset-card').evaluate_all('(cards) => cards.map(c => Number(c.dataset.id))'), [1, 3, 0])
        self.save()
        report = load_report(self.output)
        self.assertEqual([report['images'][index]['pdf_page'] for index in (1, 3, 0)], [1, 2, 3])

    def test_finish_can_save_without_discarding_changes(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.keyboard.press('Delete')
        self.page.get_by_role('button', name='Завершить правки', exact=True).click()
        self.page.get_by_role('button', name='Сохранить и выйти', exact=True).click()
        expect(self.page.get_by_role('button', name='Редактировать', exact=True)).to_be_visible()
        expect(self.page.locator('.edit-panel')).to_have_count(0)
        self.assertTrue(load_report(self.output)['images'][0]['removed'])

    def test_full_preview_arrows_zoom_and_digit_move(self):
        self.edit()
        self.card(0).locator('.cover-button').dblclick()
        image = self.page.locator('.preview-image-canvas img')
        expect(image).to_have_attribute('src', re.compile(r'/api/results/[^/]+/preview/0\?revision=0$'))
        self.page.wait_for_function('document.querySelector(".preview-image-canvas img").naturalWidth > 640')
        self.page.keyboard.press('ArrowRight')
        expect(self.page.get_by_role('dialog')).to_contain_text('Страница 2')
        self.page.locator('.preview-image-scroll').dblclick()
        expect(self.page.locator('.preview-zoom-tools')).to_contain_text('200%')
        self.page.locator('.preview-image-scroll').hover()
        self.page.mouse.wheel(0, -100)
        expect(self.page.locator('.preview-zoom-tools')).to_contain_text('230%')
        self.page.keyboard.press('2')
        expect(self.page.locator('.preview-location')).to_have_text('Вариант 2')
        expect(self.page.locator('.preview-footer')).to_contain_text('после сохранения')
        self.page.get_by_role('button', name='Закрыть диалог', exact=True).click()
        self.save()
        self.assertEqual(load_report(self.output)['images'][1]['variant'], 2)
        self.assertEqual(self.errors, [])

    def test_caption_rename_keeps_section_and_status_sections_hide_tabs(self):
        self.edit()
        navigation = self.page.locator('nav')
        navigation.get_by_role('button', name='Западный двор 1', exact=True).click()
        expect(self.page.get_by_role('group', name='Фильтр по статусу')).to_have_count(0)
        self.page.get_by_role('button', name='Переименовать группу', exact=True).click()
        self.page.get_by_label('Новое имя').fill('Западный двор — проверено')
        self.page.get_by_role('button', name='Переименовать', exact=True).click()
        expect(self.page.locator('.library-header h2')).to_have_text('Западный двор — проверено')
        expect(self.page.locator('.asset-card')).to_have_count(1)
        navigation.get_by_role('button', name='На проверку 0', exact=True).click()
        expect(self.page.get_by_role('group', name='Фильтр по статусу')).to_have_count(0)

    def test_save_stays_on_inline_row_and_small_stats_leave_cards_visible(self):
        self.page.set_viewport_size({'width': 1366, 'height': 860})
        self.edit()
        save = self.page.locator('.editor-save').bounding_box()
        more = self.page.locator('.editor-overflow').bounding_box()
        self.assertAlmostEqual(save['y'], more['y'], delta=2)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 1366)
        self.page.set_viewport_size({'width': 800, 'height': 640})
        self.assertLess(self.page.locator('.asset-card').first.bounding_box()['y'], 640)
        self.assertLess(self.page.locator('.stats').bounding_box()['height'], 90)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 800)

    def test_selection_overlay_and_hover_handle_are_visible_and_distinct(self):
        self.edit()
        handle = self.card(0).locator('.drag-handle')
        expect(handle).to_have_css('opacity', '0')
        self.card(0).hover()
        expect(handle).to_have_css('opacity', '1')
        expect(handle).to_have_css('pointer-events', 'auto')
        expect(handle).to_have_attribute('title', 'Перетащите карточку')
        self.card(0).get_by_role('checkbox').check()
        selection = self.card(0).evaluate('''el => ({
            shadow:getComputedStyle(el).boxShadow,
            overlay:getComputedStyle(el.querySelector('.asset-image'),'::after').backgroundColor,
            checkbox:el.querySelector('input').getBoundingClientRect().width})''')
        self.assertNotEqual(selection['shadow'], 'none')
        self.assertNotEqual(selection['overlay'], 'rgba(0, 0, 0, 0)')
        self.assertGreaterEqual(selection['checkbox'], 24)
        expect(self.page.locator('.edit-panel-top strong')).to_have_text('Редактирование результата')

    def test_local_group_drag_marks_source_sidebar_and_insertion_target(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.card(1).get_by_role('checkbox').check()
        transfer = self.page.evaluate_handle('new DataTransfer()')
        self.card(0).dispatch_event('dragstart', {'dataTransfer': transfer})
        expect(self.page.locator('.asset-card.is-dragging')).to_have_count(2)
        expect(self.page.locator('.editor-drag-preview')).to_have_text('2 изображения')
        target = self.page.locator('nav').get_by_role('button', name='Вариант 2 1', exact=True)
        target.dispatch_event('dragover', {'dataTransfer': transfer})
        expect(target).to_have_class(re.compile(r'is-drop-target'))
        target.dispatch_event('dragleave', {'dataTransfer': transfer, 'clientX': -20, 'clientY': -20})
        expect(target).not_to_have_class(re.compile(r'is-drop-target'))
        self.card(2).dispatch_event('dragover', {'dataTransfer': transfer})
        expect(self.card(2)).to_have_class(re.compile(r'is-insertion-target'))
        self.card(2).dispatch_event('drop', {'dataTransfer': transfer})
        expect(self.page.locator('.asset-card.is-dragging, .is-drop-target, .is-insertion-target')).to_have_count(0)
        expect(self.page.locator('.editor-drag-preview')).to_have_count(0)
        self.save()
        report = load_report(self.output)
        self.assertEqual([image['variant'] for image in report['images'][:3]], [2, 2, 2])
        self.assertEqual([image['pdf_page'] for image in report['images'][:3]], [1, 2, 3])

    def test_long_group_titles_natural_caption_sort_and_no_duplicate_caption(self):
        report = load_report(self.output)
        title = 'ЖК «Северная долина», корпус 3, панорамный фасад'
        report.setdefault('variant_names', {})['1'] = title
        for page, caption in enumerate(['Вид 2', 'Вид 10', 'Вид 1'], start=4):
            image = dict(report['images'][3], folder=caption, title=caption, page=page)
            if page == 4:
                report['images'][3] = image
            else:
                report['images'].append(image)
        (self.output / 'Отчёт.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        self.page.reload()
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        self.page.locator('.history-row').first.click()
        variant = self.page.locator('nav').get_by_role('button', name=title + ' 2', exact=True)
        expect(variant).to_have_attribute('title', title)
        captions = self.page.locator('.category-list .nav-text').all_text_contents()
        self.assertEqual(captions, ['Вид 1', 'Вид 2', 'Вид 10'])
        expect(self.card(3).locator('.asset-group')).to_have_count(0)
        expect(self.card(0).locator('.asset-group')).to_have_css('text-transform', 'none')
        expect(self.page.locator('nav').get_by_role('button', name='Вид 10 1', exact=True)).to_have_attribute('title', 'Вид 10')
        self.assertNotEqual(self.page.locator('.stats > .passive-stat').first.evaluate('e => getComputedStyle(e).backgroundColor'),
                            self.page.locator('.stats > button').first.evaluate('e => getComputedStyle(e).backgroundColor'))

    def test_medium_toolbar_keeps_move_remove_and_compact_inline_height(self):
        self.page.set_viewport_size({'width': 1100, 'height': 620})
        self.edit()
        toolbar = self.page.get_by_role('region', name='Инструменты редактора')
        self.assertLessEqual(toolbar.bounding_box()['height'], 80)
        self.card(0).get_by_role('checkbox').check()
        self.page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
        expect(toolbar).to_have_css('position', 'fixed')
        expect(toolbar.get_by_role('button', name='Перенести в вариант', exact=True)).to_be_in_viewport()
        expect(toolbar.get_by_role('button', name='Убрать', exact=True)).to_be_in_viewport()
        self.assertLessEqual(toolbar.bounding_box()['height'], 70)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 1100)


if __name__ == '__main__':
    unittest.main()
