"""Regressions from the 3.29 UI review, against real PDFs and transactions."""
import json
from pathlib import Path
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from playwright.sync_api import expect

import test_desktop_ui as fixture


class ReviewFixesUI(unittest.TestCase):
    setUpClass = classmethod(fixture.DesktopUI.setUpClass.__func__)
    tearDownClass = classmethod(fixture.DesktopUI.tearDownClass.__func__)
    setUp = fixture.DesktopUI.setUp
    card = fixture.DesktopUI.card
    edit = fixture.DesktopUI.edit
    extend_gallery = fixture.DesktopUI.extend_gallery

    def save(self):
        with self.page.expect_response(lambda r: r.url.endswith('/apply') and r.status == 200):
            self.page.get_by_role('button', name='Сохранить', exact=True).click()
        expect(self.page.locator('.editor-save')).to_be_disabled()

    def test_editor_creates_a_subvariant_listed_after_its_variant(self):
        from photo_edit import load_report
        self.page.get_by_role('button', name='Редактировать', exact=True).click()
        self.page.locator('.asset-card[data-id="1"]').get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Новый вариант', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        dialog.get_by_role('button', name='Подвариант 1.1', exact=True).click()
        expect(dialog.get_by_label('Номер варианта или подварианта')).to_have_value('1.1')
        dialog.get_by_role('button', name='Создать и перенести').click()
        navigation = self.page.locator('#variant-navigation .nav-text')
        expect(navigation).to_have_text(['Вариант 1', 'Вариант 1.1', 'Вариант 2'])
        expect(self.page.locator('.library-header h2')).to_have_text('Вариант 1.1')
        self.save()
        report = load_report(self.output)
        self.assertEqual(report['images'][1]['variant'], '1.1')
        self.assertTrue((self.output / 'Вариант 1.1' / 'Вариант 1.1.pdf').is_file())
        self.page.reload()
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        self.page.locator('.history-row').first.click()
        expect(self.page.locator('nav').get_by_role('button', name='Вариант 1.1 1', exact=True)).to_be_visible()
        self.assertEqual(self.errors, [])

    def test_source_page_renders_original_slide_and_missing_source_has_its_own_error(self):
        self.card(0).locator('.cover-button').click()
        self.page.get_by_role('button', name='Исходная страница 1', exact=True).click()
        image = self.page.locator('.preview-image-canvas img')
        expect(image).to_have_attribute('data-preview-kind', 'source')
        self.page.wait_for_function('document.querySelector(".preview-image-canvas img")?.naturalWidth > 0')
        size = image.evaluate('(el) => [el.naturalWidth, el.naturalHeight]')
        self.assertAlmostEqual(size[0] / size[1], 1000 / 800, delta=.01,
                               msg='Render the full original slide, not the wide extracted image')
        self.page.get_by_role('button', name='Извлечённое изображение', exact=True).click()
        Path(self.report['source']).unlink()
        self.page.get_by_role('button', name='Исходная страница 1', exact=True).click()
        expect(self.page.get_by_text('Исходный PDF недоступен', exact=True)).to_be_visible()
        expect(self.page.locator('.preview-image-canvas img')).to_have_count(0)
        self.page.get_by_role('button', name='Извлечённое изображение', exact=True).click()
        expect(self.page.locator('.preview-image-canvas img')).to_be_visible()

    def test_source_endpoint_rejects_unknown_result_and_invalid_indices(self):
        result_id = next(iter(self.server.folders))
        for suffix in (f'{result_id}/source/99999', f'{result_id}/source/-1',
                       f'{result_id}/source/0.5', 'unknown/source/0'):
            with self.subTest(suffix=suffix), self.assertRaises(HTTPError):
                urlopen(self.server.url + 'api/results/' + suffix).close()

    def test_changed_original_cannot_be_presented_as_the_source_slide(self):
        source = Path(self.report['source'])
        with source.open('ab') as stream:
            stream.write(b'\n% changed since extraction\n')
        result_id = next(iter(self.server.folders))
        with self.assertRaisesRegex(ValueError, 'изменился после обработки'):
            self.server.source_preview(result_id, 0)

    def test_low_window_keeps_first_image_and_caption_visible(self):
        for width, height in ((800, 640), (780, 620), (1100, 760)):
            self.page.set_viewport_size({'width': width, 'height': height})
            if self.page.get_by_role('button', name='Редактировать', exact=True).count():
                self.edit()
            self.page.evaluate('window.scrollTo(0, 0)')
            rect = self.card(0).locator('.asset-meta').bounding_box()
            self.assertLess(rect['y'] + rect['height'], height - 10)
            self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))

    def test_short_gallery_keeps_inline_tools_when_they_still_fit(self):
        self.page.set_viewport_size({'width': 1380, 'height': 620})
        self.edit()
        self.page.evaluate('scrollTo(0, document.documentElement.scrollHeight)')
        toolbar = self.page.get_by_role('region', name='Инструменты редактора')
        expect(toolbar).not_to_have_css('position', 'fixed')
        expect(toolbar).to_be_in_viewport(ratio=1)

    def test_navigation_groups_keep_the_drawer_open_and_have_names_in_the_rail(self):
        self.page.get_by_role('button', name='Свернуть меню', exact=True).click()
        expect(self.page.get_by_role('button', name='Варианты: 2', exact=True)).to_be_visible()
        expect(self.page.get_by_role('button', name='Подписи: 1', exact=True)).to_be_visible()
        self.page.set_viewport_size({'width': 800, 'height': 640})
        self.page.get_by_role('button', name='Открыть меню', exact=True).click()
        variants = self.page.get_by_role('button', name='Варианты: 2', exact=True)
        variants.click()
        expect(self.page.locator('.sidebar')).to_be_visible()
        expect(self.page.locator('#variant-navigation')).to_be_hidden()
        variants.click()
        expect(self.page.locator('.sidebar')).to_be_visible()
        self.page.locator('nav').get_by_role('button', name='Вариант 1 2', exact=True).click()
        expect(self.page.locator('.sidebar')).not_to_be_visible()

    def test_clear_all_includes_hidden_selection(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.locator('nav').get_by_role('button', name='Вариант 2 1', exact=True).click()
        expect(self.page.locator('.selection-hidden')).to_have_text('Скрыто: 1')
        self.page.get_by_role('button', name='Снять всё выделение', exact=True).click()
        expect(self.page.locator('.selection-count')).to_contain_text('Выбрано: 0')
        self.page.locator('nav').get_by_role('button', name='Все изображения 4', exact=True).click()
        expect(self.page.locator('.asset-card input:checked')).to_have_count(0)

    def test_technical_pdf_failure_is_explained_and_folder_recovery_is_conditional(self):
        def broken(*args, **kwargs):
            raise ValueError('No /Root object! - Is this really a PDF?')
        self.jobs.extractor = broken
        with self.assertLogs('pdf-photos', level='ERROR'):
            self.jobs.start([self.report['source']])
            self.jobs.wait(10)
        self.page.get_by_role('button', name='Обработка PDF', exact=True).click()
        failures = self.page.get_by_role('region', name='Необработанные файлы')
        expect(failures).to_contain_text('Файл может быть повреждён')
        expect(failures.get_by_role('button', name='Выбрать другую папку', exact=True)).to_have_count(0)
        expect(failures.get_by_text('No /Root object!', exact=False)).not_to_be_visible()
        failures.get_by_text('Технические подробности', exact=True).click()
        expect(failures.get_by_text('No /Root object!', exact=False)).to_be_visible()
        def denied(*args, **kwargs):
            raise PermissionError("[Errno 13] Permission denied: 'C:\\Результаты\\файл.pdf'")
        self.jobs.extractor = denied
        with self.assertLogs('pdf-photos', level='ERROR'):
            self.jobs.start([self.report['source']])
            self.jobs.wait(10)
        expect(failures).to_contain_text('Проверьте доступ к папке')
        expect(failures.get_by_role('button', name='Выбрать другую папку', exact=True)).to_be_visible()

    def test_preview_mouse_actions_change_draft_and_can_restore(self):
        self.edit()
        self.card(0).locator('.asset-title button').first.click()
        dialog = self.page.get_by_role('dialog')
        dialog.get_by_role('button', name='Перенести изображение', exact=True).click()
        self.page.get_by_role('menuitem', name='Вариант 2', exact=True).click()
        expect(dialog.locator('.preview-location')).to_contain_text('Вариант 2')
        dialog.get_by_role('button', name='Убрать изображение', exact=True).click()
        expect(dialog.locator('.preview-location')).to_contain_text('Убрано')
        dialog.get_by_role('button', name='Вернуть изображение', exact=True).click()
        expect(dialog.locator('.preview-location')).not_to_contain_text('Убрано')
        dialog.get_by_role('button', name='Закрыть диалог').click()
        self.save()
        saved = json.loads((self.output / 'Отчёт.json').read_text(encoding='utf-8'))
        self.assertFalse(saved['images'][0].get('removed', False))
        self.assertEqual(self.errors, [])

    def test_review_completion_distinguishes_draft_saved_and_search(self):
        report_path = self.output / 'Отчёт.json'
        report = json.loads(report_path.read_text(encoding='utf-8'))
        report['images'][3]['status'] = 'review'
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        self.page.reload()
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        self.page.locator('.history-row').first.click()
        # «Разобрать» opens the doubtful images ready to move.
        self.page.get_by_role('button', name='Разобрать', exact=True).click()
        self.card(3).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Перенести в вариант', exact=True).click()
        self.page.get_by_role('menuitem', name='Вариант 1', exact=True).click()
        expect(self.page.get_by_role('heading', name='Спорные изображения распределены в черновике')).to_be_visible()
        self.save()
        expect(self.page.get_by_role('heading', name='Спорные изображения распределены', exact=True)).to_be_visible()
        self.page.get_by_role('searchbox').fill('нет такого изображения')
        expect(self.page.get_by_role('heading', name='Изображения не найдены', exact=True)).to_be_visible()

    def test_edit_is_the_main_action_and_sorting_opens_the_doubtful_images_for_editing(self):
        report_path = self.output / 'Отчёт.json'
        report = json.loads(report_path.read_text(encoding='utf-8'))
        report['images'][3]['status'] = 'review'
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        self.page.reload()
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        self.page.locator('.history-row').first.click()
        expect(self.page.get_by_role('button', name='Редактировать', exact=True)).to_have_class('btn primary')
        sort = self.page.get_by_role('button', name='Разобрать', exact=True)
        expect(sort).to_have_class('btn secondary review-summary-start')
        sort.click()
        expect(self.page.locator('.library-header h2')).to_have_text('Нужно проверить')
        expect(self.page.locator('.asset-card')).to_have_count(1)
        expect(self.page.get_by_role('button', name='Завершить правки', exact=True)).to_have_class('btn secondary')
        expect(self.card(3).get_by_role('checkbox')).to_be_visible()
        self.assertEqual(self.errors, [])

    def test_collages_are_listed_even_when_the_program_found_none(self):
        section = self.page.locator('nav').get_by_role('button', name='Склейки 0', exact=True)
        expect(section).to_be_visible()
        section.click()
        empty = self.page.get_by_role('region', name='Склейки').locator('.collages-empty-state')
        expect(empty).to_contain_text('Склеек не найдено')
        expect(self.page.locator('.collages-tabs')).to_have_count(0)
        empty.get_by_role('button', name='Перейти к изображениям').click()
        expect(self.page.locator('.asset-card')).to_have_count(4)

    def test_wheel_over_the_menu_or_a_window_leaves_the_gallery_in_place(self):
        self.extend_gallery()
        expect(self.page.locator('.asset-card')).to_have_count(40)
        self.page.set_viewport_size({'width': 1380, 'height': 700})
        self.page.evaluate('scrollTo(0, 300)')
        self.assertEqual(self.page.evaluate('scrollY'), 300)
        for point in ((120, 40), (120, 400), (120, 680)):
            self.page.mouse.move(*point)
            self.page.mouse.wheel(0, 1500)
        self.page.wait_for_timeout(200)
        self.assertEqual(self.page.evaluate('scrollY'), 300)
        self.card(1).locator('.cover-button').click()
        expect(self.page.get_by_role('dialog')).to_be_visible()
        for point in ((700, 100), (4, 400)):
            self.page.mouse.move(*point)
            self.page.mouse.wheel(0, 1500)
        self.page.wait_for_timeout(200)
        self.assertEqual(self.page.evaluate('scrollY'), 300)
        self.page.get_by_role('button', name='Закрыть диалог').click()
        # The gallery itself still scrolls.
        self.page.mouse.move(800, 400)
        self.page.mouse.wheel(0, 400)
        self.page.wait_for_timeout(200)
        self.assertGreater(self.page.evaluate('scrollY'), 300)

    def test_status_note_and_badges_mark_only_what_needs_attention(self):
        # A result that was only opened has not been saved here.
        expect(self.page.locator('.status-note')).to_have_count(0)
        # A found caption is the usual case and carries no badge.
        expect(self.card(0).locator('.status-badge')).to_have_count(0)
        self.edit()
        expect(self.page.locator('.status-note')).to_have_count(0)
        self.card(3).get_by_role('checkbox').check()
        self.page.keyboard.press('Delete')
        expect(self.page.locator('.status-note')).to_have_text('Есть изменения')
        self.save()
        expect(self.page.locator('.status-note')).to_have_text('Изменения сохранены')

    def test_same_size_selection_takes_every_image_of_that_pixel_size(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Ещё', exact=True).click()
        # The four pictures of the fixture are drawn in one frame, so they share their pixel size.
        self.page.get_by_role('menuitem', name='Выбрать такие же по размеру (3)', exact=True).click()
        expect(self.page.locator('.selection-count')).to_contain_text('Выбрано: 4')
        self.page.get_by_role('button', name='Ещё', exact=True).click()
        expect(self.page.get_by_role('menuitem', name='Выбрать такие же по размеру', exact=True)).to_be_disabled()

    def test_saved_notice_is_a_card_with_title_detail_and_undo(self):
        self.edit()
        self.card(3).get_by_role('checkbox').check()
        self.page.keyboard.press('Delete')
        self.save()
        toast = self.page.locator('.toast')
        expect(toast.locator('.toast-message strong')).to_have_text('Правки сохранены.')
        expect(toast.locator('.toast-message span')).to_have_text('Резервная копия создана.')
        expect(toast.get_by_role('button', name='Отменить сохранение')).to_be_visible()
        self.assertEqual(round(toast.bounding_box()['width']), 420)
        self.assertEqual(toast.evaluate('t => getComputedStyle(t).backgroundColor'), 'rgb(255, 255, 255)')
        toast.get_by_role('button', name='Скрыть сообщение').click()
        expect(toast).to_have_count(0)

    def test_windows_and_the_notice_fade_in_and_out_when_windows_shows_animations(self):
        self.page.emulate_media(reduced_motion='no-preference')
        sample = """selector => {
          window.__frames = [];
          const tick = () => {
            const el = document.querySelector(selector);
            window.__frames.push(el ? +getComputedStyle(el).opacity : null);
            if (window.__frames.length < 45) requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        }"""

        def frames(selector, action):
            self.page.evaluate(sample, selector)
            action()
            self.page.wait_for_function('window.__frames.length >= 45')
            return self.page.evaluate('window.__frames')

        def passes_through_partial(values):
            self.assertTrue(any(value is not None and 0.05 < value < 0.95 for value in values), values)

        self.edit()
        self.card(3).get_by_role('checkbox').check()
        new_variant = self.page.get_by_role('button', name='Новый вариант', exact=True)
        shown = frames('dialog', new_variant.click)
        passes_through_partial(shown)
        # Still fading in, the window already takes the typing.
        expect(self.page.get_by_role('dialog').get_by_label('Номер варианта или подварианта')).to_be_focused()
        hidden = frames('dialog', lambda: self.page.keyboard.press('Escape'))
        passes_through_partial(hidden)
        self.assertIsNone(hidden[-1])
        expect(new_variant).to_be_focused()

        self.page.keyboard.press('Delete')
        toast = self.page.locator('.toast')
        shown = frames('.toast', self.save)
        passes_through_partial(shown)
        hidden = frames('.toast', toast.get_by_role('button', name='Скрыть сообщение').click)
        passes_through_partial(hidden)
        expect(toast).to_have_count(0)
        self.assertEqual(self.errors, [])

    def test_pages_name_themselves_once_and_section_notes_take_one_line(self):
        # The result keeps its top bar for search and «Редактировать»; the history names itself in its heading only.
        expect(self.page.locator('.topbar .searchbox')).to_be_visible()
        self.page.get_by_role('button', name='Вернуться в историю обработки').click()
        expect(self.page.locator('h1')).to_have_text('История обработки')
        expect(self.page.locator('.topbar')).to_have_count(0)
        self.page.locator('.history-row').first.click()
        self.page.locator('nav').get_by_role('button', name='Убранные', exact=False).click()
        header = self.page.locator('.library-header').bounding_box()
        note = self.page.locator('.section-note')
        expect(note).to_contain_text('Верните нужные карточки и сохраните результат.')
        box = note.bounding_box()
        self.assertLessEqual(box['height'], 40)
        self.assertEqual(round(box['y'] - header['y'] - header['height']), 12)
        # An empty «Склейки» explains itself in its card, so the note is not repeated above it.
        self.page.locator('nav').get_by_role('button', name='Склейки', exact=False).click()
        expect(self.page.locator('.library-header h2')).to_have_text('Склейки')
        expect(self.page.locator('.collages-empty-state')).to_be_visible()
        expect(self.page.locator('.section-note')).to_have_count(0)

    def test_a_dialog_field_takes_the_focus_and_marks_it_inside_its_border(self):
        self.edit()
        self.card(3).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Новый вариант', exact=True).click()
        field = self.page.get_by_role('dialog').get_by_label('Номер варианта или подварианта')
        expect(field).to_be_focused()
        style = field.evaluate('f => [getComputedStyle(f).outlineStyle, getComputedStyle(f).boxShadow]')
        self.assertEqual(style[0], 'none')
        self.assertIn('inset', style[1])
        hints = self.page.get_by_role('group', name='Свободные номера').bounding_box()
        box = field.bounding_box()
        self.assertGreaterEqual(hints['y'], box['y'] + box['height'])
        self.page.keyboard.press('Escape')
        expect(self.page.get_by_role('button', name='Новый вариант', exact=True)).to_be_focused()


if __name__ == '__main__':
    unittest.main()
