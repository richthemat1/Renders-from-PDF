"""Settings controls keep the native picker and persisted output mode in sync."""
import unittest

from playwright.sync_api import expect

import test_desktop_ui as desktop_ui


class SettingsUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        desktop_ui.DesktopUI.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        desktop_ui.DesktopUI.tearDownClass.__func__(cls)

    setUp = desktop_ui.DesktopUI.setUp

    def settings(self):
        self.page.get_by_role('button', name='Настройки', exact=True).click()
        expect(self.page.get_by_role('group', name='Расположение новых результатов')).to_be_visible()

    def test_picker_cancel_preserves_mode_and_choose_invokes_it_once(self):
        self.jobs.set_output('results')
        snapshot = self.jobs.snapshot()
        self.page.route('**/api/state', lambda route: route.fulfill(json=snapshot))
        self.page.evaluate('''snapshot => {
            window.settingsPickerCalls = 0;
            window.pywebview = {api: {choose_output: async () => {
                window.settingsPickerCalls += 1;
                return snapshot;
            }}};
        }''', snapshot)
        self.settings()
        results = self.page.get_by_role('radio', name='В подпапке «Результаты»', exact=True)
        custom = self.page.get_by_role('radio', name='В выбранной папке', exact=True)
        expect(results).to_be_checked()
        self.page.get_by_role('button', name='Выбрать', exact=True).click()
        expect(results).to_be_checked()
        expect(custom).not_to_be_checked()
        self.assertEqual(self.page.evaluate('window.settingsPickerCalls'), 1)
        self.page.get_by_text('В выбранной папке', exact=True).click()
        expect(results).to_be_checked()
        expect(custom).not_to_be_checked()
        self.assertEqual(self.page.evaluate('window.settingsPickerCalls'), 2)
        self.assertEqual(self.jobs.snapshot()['settings'], snapshot['settings'])
        self.assertEqual(self.errors, [])

    def test_selected_folder_updates_from_native_result_and_cancel_keeps_it(self):
        folder = self.root / 'Папка результатов'
        folder.mkdir()
        def choose_folder():
            return self.jobs.set_output('custom', str(folder))
        self.page.expose_function('chooseSettingsOutput', choose_folder)
        self.page.evaluate('''() => {
            window.settingsPickerCalls = 0;
            window.pywebview = {api: {choose_output: async () => {
                window.settingsPickerCalls += 1;
                return window.chooseSettingsOutput();
            }}};
        }''')
        self.settings()
        self.page.get_by_text('В выбранной папке', exact=True).click()
        custom = self.page.get_by_role('radio', name='В выбранной папке', exact=True)
        expect(custom).to_be_checked()
        expect(self.page.get_by_text(str(folder), exact=True)).to_be_visible()
        self.assertEqual(self.jobs.snapshot()['settings']['mode'], 'custom')
        self.assertEqual(self.jobs.snapshot()['settings']['folder'], str(folder))
        # Cancel returns the same current snapshot from the native picker.
        self.page.evaluate('''snapshot => {
            window.pywebview.api.choose_output = async () => {
                window.settingsPickerCalls += 1;
                return snapshot;
            };
        }''', self.jobs.snapshot())
        self.page.get_by_role('button', name='Выбрать', exact=True).click()
        expect(custom).to_be_checked()
        expect(self.page.get_by_text(str(folder), exact=True)).to_be_visible()
        self.assertEqual(self.page.evaluate('window.settingsPickerCalls'), 2)
        self.assertEqual(self.errors, [])

    def test_keyboard_changes_mode_and_busy_state_disables_output_controls(self):
        self.settings()
        nearby = self.page.get_by_role('radio', name='Рядом с исходным PDF', exact=True)
        nearby.focus()
        self.page.keyboard.press('ArrowDown')
        results = self.page.get_by_role('radio', name='В подпапке «Результаты»', exact=True)
        expect(results).to_be_checked()
        self.assertEqual(self.jobs.snapshot()['settings']['mode'], 'results')
        snapshot = dict(self.jobs.snapshot(), busy=True)
        self.page.route('**/api/state', lambda route: route.fulfill(json=snapshot))
        expect(results).to_be_disabled()
        expect(self.page.get_by_role('button', name='Выбрать', exact=True)).to_be_disabled()
        expect(self.page.get_by_text('Расположение можно изменить после завершения обработки.', exact=True)).to_be_visible()
        self.assertEqual(self.errors, [])
