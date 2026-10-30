"""The help window: one window with sections, opened from the menu and from the editor."""
import unittest

from playwright.sync_api import expect

import test_desktop_ui as fixture


class HelpUI(unittest.TestCase):
    setUpClass = classmethod(fixture.DesktopUI.setUpClass.__func__)
    tearDownClass = classmethod(fixture.DesktopUI.tearDownClass.__func__)
    setUp = fixture.DesktopUI.setUp
    edit = fixture.DesktopUI.edit

    def selected(self, dialog, name):
        expect(dialog.get_by_role('tab', name=name, exact=True)).to_have_attribute('aria-selected', 'true')

    def test_menu_opens_the_first_section_and_the_keyboard_and_links_switch_sections(self):
        self.page.get_by_role('button', name='Как пользоваться', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='Как пользоваться')
        first = dialog.get_by_role('tab', name='Начало работы', exact=True)
        self.selected(dialog, 'Начало работы')
        expect(first).to_be_focused()
        panel = dialog.get_by_role('tabpanel')
        expect(panel).to_contain_text('Добавьте PDF')
        self.page.keyboard.press('ArrowDown')
        expect(dialog.get_by_role('tab', name='Проверка результата', exact=True)).to_be_focused()
        self.selected(dialog, 'Проверка результата')
        expect(panel).to_contain_text('Решите по каждому')
        self.page.keyboard.press('End')
        self.selected(dialog, 'Клавиши')
        self.page.keyboard.press('ArrowDown')
        self.selected(dialog, 'Начало работы')
        dialog.get_by_role('tab', name='Редактирование', exact=True).click()
        dialog.get_by_role('button', name='Подробнее о склейках').click()
        self.selected(dialog, 'Склейки')
        dialog.get_by_role('button', name='Дальше: Вырезки').click()
        self.selected(dialog, 'Вырезки')
        expect(panel).to_contain_text('Обведите нужное')
        dialog.get_by_role('button', name='Понятно', exact=True).click()
        expect(self.page.locator('dialog')).to_have_count(0)
        self.assertEqual(self.errors, [])

    def test_editor_help_opens_the_editing_section(self):
        # The «?» stands in the editor panel only while the panel is wide; a narrower one keeps «Ещё → Помощь».
        self.page.set_viewport_size({'width': 1800, 'height': 1000})
        self.edit()
        self.page.get_by_role('button', name='Помощь по редактору').click()
        dialog = self.page.get_by_role('dialog', name='Как пользоваться')
        self.selected(dialog, 'Редактирование')
        expect(dialog.get_by_role('tabpanel')).to_contain_text('Выберите карточки')
        self.page.keyboard.press('Escape')
        expect(self.page.locator('dialog')).to_have_count(0)
        # Escape closed the window only: the editor is still on.
        expect(self.page.locator('.editor-save')).to_be_visible()
        self.page.get_by_role('button', name='Ещё', exact=True).click()
        self.page.get_by_role('menuitem', name='Помощь', exact=True).click()
        self.selected(self.page.get_by_role('dialog', name='Как пользоваться'), 'Редактирование')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
