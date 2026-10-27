"""Sidebar resizing and the collapsed icon rail against the real desktop UI."""
import unittest
from pathlib import Path

from playwright.sync_api import expect, TimeoutError as PlaywrightTimeoutError

import test_desktop_ui as desktop_ui


class SidebarUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        desktop_ui.DesktopUI.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        desktop_ui.DesktopUI.tearDownClass.__func__(cls)

    setUp = desktop_ui.DesktopUI.setUp
    card = desktop_ui.DesktopUI.card
    edit = desktop_ui.DesktopUI.edit
    save = desktop_ui.DesktopUI.save

    def separator(self):
        return self.page.get_by_role('separator', name='Ширина бокового меню')

    def expect_width(self, width):
        expect(self.page.locator('.sidebar')).to_have_css('width', f'{width}px')
        expect(self.page.locator('.workspace-main')).to_have_css('grid-column-start', '2')

    def expect_rail(self):
        self.expect_width(72)
        self.page.wait_for_function("document.querySelector('.workspace-main').getBoundingClientRect().left === 72")

    def resize_to(self, width):
        edge = self.separator()
        expect(edge).to_have_count(1)
        box = edge.bounding_box()
        self.page.mouse.move(box['x'] + box['width'] / 2, box['y'] + 100)
        self.page.mouse.down()
        self.page.mouse.move(width, box['y'] + 100, steps=8)
        self.page.mouse.up()
        self.expect_width(width)

    def test_drag_keyboard_limits_and_reset_preserve_the_preferred_width(self):
        self.resize_to(420)
        self.separator().focus()
        self.page.keyboard.press('ArrowRight')
        self.expect_width(436)
        self.page.keyboard.press('Home')
        self.expect_width(248)
        self.page.keyboard.press('End')
        self.expect_width(520)
        self.page.set_viewport_size({'width': 1000, 'height': 1000})
        self.expect_width(360)
        self.assertGreaterEqual(self.page.locator('.workspace-main').bounding_box()['width'], 640)
        self.page.set_viewport_size({'width': 1380, 'height': 1000})
        self.expect_width(520)
        self.separator().dblclick()
        self.expect_width(248)
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        self.assertEqual(self.errors, [])

    def test_click_and_cancel_do_not_replace_the_saved_width_with_a_temporary_clamp(self):
        self.resize_to(520)
        self.page.set_viewport_size({'width': 1000, 'height': 1000})
        self.expect_width(360)
        self.separator().click()
        self.page.set_viewport_size({'width': 1380, 'height': 1000})
        self.expect_width(520)
        edge = self.separator()
        box = edge.bounding_box()
        self.page.mouse.move(520, box['y'] + 100)
        self.page.mouse.down()
        self.page.mouse.move(420, box['y'] + 100, steps=5)
        self.expect_width(420)
        edge.dispatch_event('pointercancel', {'pointerId': 1})
        self.page.mouse.up()
        self.expect_width(520)
        expect(self.page.locator('body')).not_to_have_css('cursor', 'col-resize')
        self.assertEqual(self.errors, [])

    def test_collapsing_keeps_draft_and_selection_and_names_rail_icons(self):
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Убрать', exact=True).click()
        self.card(1).get_by_role('checkbox').check()
        expect(self.page.locator('.topbar').get_by_role('button', name='Свернуть меню')).to_have_count(0)
        self.page.locator('.sidebar').get_by_role('button', name='Свернуть меню', exact=True).click()
        self.expect_rail()
        expect(self.separator()).to_have_count(0)
        expect(self.page.locator('.sidebar .nav-label').first).to_have_css('font-size', '0px')
        history = self.page.get_by_role('button', name='История обработки', exact=True).first
        expect(history).to_be_visible()
        history.hover()
        expect(self.page.get_by_role('tooltip')).to_have_text('История обработки')
        # One page of variant 1 is already removed in this draft, so the rail shows the live count.
        variant = self.page.locator('nav').get_by_role('button', name='Вариант 1 1', exact=True)
        variant.focus()
        expect(self.page.get_by_role('tooltip').locator('span')).to_have_text('Вариант 1')
        expect(self.page.get_by_role('tooltip').locator('b')).to_have_text('1')
        self.page.mouse.move(700, 400)
        variant.blur()
        expect(self.page.get_by_role('tooltip')).to_have_count(0)
        expect(self.page.locator('.asset-card')).to_have_count(3)
        expect(self.card(1).get_by_role('checkbox')).to_be_checked()
        expect(self.page.locator('.status-note')).to_contain_text('Есть изменения')
        self.page.get_by_role('button', name='Развернуть меню', exact=True).click()
        self.expect_width(248)
        expect(self.card(1).get_by_role('checkbox')).to_be_checked()
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        expect(self.page.get_by_role('dialog')).to_be_visible()
        self.page.get_by_role('button', name='Вернуться к правкам', exact=True).click()
        expect(self.card(1).get_by_role('checkbox')).to_be_checked()
        self.assertEqual(self.errors, [])

    def test_width_and_hidden_preference_survive_shell_navigation_and_reload(self):
        self.resize_to(420)
        self.page.get_by_role('button', name='Свернуть меню', exact=True).click()
        self.page.get_by_role('button', name='Открыть историю обработки', exact=True).click()
        self.expect_rail()
        self.page.reload()
        expect(self.page.get_by_role('button', name='Развернуть меню', exact=True)).to_be_visible()
        self.expect_rail()
        self.page.get_by_role('button', name='Развернуть меню', exact=True).click()
        self.expect_width(420)
        self.page.get_by_role('button', name='История обработки', exact=True).click()
        self.page.locator('.history-row').first.click()
        self.expect_width(420)
        self.page.locator('nav').get_by_role('button', name='Вариант 1 2', exact=True).click()
        self.expect_width(420)
        self.assertEqual(self.errors, [])

    def test_floating_tools_follow_the_actual_workspace_after_resize_and_hide(self):
        self.page.set_viewport_size({'width': 1380, 'height': 620})
        self.edit()
        self.card(0).get_by_role('checkbox').check()
        self.page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
        toolbar = self.page.get_by_role('region', name='Инструменты редактора')
        expect(toolbar).to_have_css('position', 'fixed')
        for width in (420, 520):
            self.resize_to(width)
            error = toolbar.evaluate('''(el) => {
                const dock = el.getBoundingClientRect();
                const main = document.querySelector('.workspace-main').getBoundingClientRect();
                return Math.abs((dock.left + dock.right) / 2 - (main.left + main.right) / 2);
            }''')
            self.assertLess(error, 2)
        self.page.get_by_role('button', name='Свернуть меню', exact=True).click()
        expect(toolbar).to_have_css('position', 'fixed')
        self.expect_rail()
        self.page.wait_for_function('''() => {
            const dock = document.querySelector('.edit-panel').getBoundingClientRect();
            return Math.abs((dock.left + dock.right) / 2 - (72 + innerWidth) / 2) < 2;
        }''')
        dock = toolbar.bounding_box()
        self.assertLess(abs(dock['x'] + dock['width'] / 2 - 726), 2)
        expect(self.card(0).get_by_role('checkbox')).to_be_checked()
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        self.assertEqual(self.errors, [])

    def test_mobile_drawer_ignores_desktop_collapsed_preference_and_closes_with_escape(self):
        self.page.get_by_role('button', name='Свернуть меню', exact=True).click()
        for width in (800, 360):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 760})
                expect(self.page.get_by_role('button', name='Развернуть меню', exact=True)).to_have_count(0)
                expect(self.separator()).to_have_count(0)
                self.page.get_by_role('button', name='Открыть меню', exact=True).click()
                expect(self.page.locator('.sidebar')).to_be_visible()
                expect(self.page.locator('.sidebar-scrim')).to_be_visible()
                self.page.keyboard.press('Escape')
                expect(self.page.locator('.sidebar')).not_to_be_visible()
                expect(self.page.get_by_role('button', name='Открыть меню', exact=True)).to_be_focused()
                self.page.get_by_role('button', name='Открыть меню', exact=True).click()
                self.page.locator('nav').get_by_role('button', name='Вариант 1 2', exact=True).click()
                expect(self.page.locator('.sidebar')).not_to_be_visible()
                self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        self.page.set_viewport_size({'width': 1380, 'height': 1000})
        self.expect_rail()
        self.assertEqual(self.errors, [])

    def test_saved_layout_is_restored_after_the_desktop_server_restarts_on_a_new_origin(self):
        self.resize_to(420)
        with self.page.expect_response(lambda response: response.url.endswith('/api/sidebar')
                                       and response.request.post_data_json.get('hidden') is True):
            self.page.get_by_role('button', name='Свернуть меню', exact=True).click()
        new_jobs = desktop_ui.JobService(self.root / 'state.json')
        self.addCleanup(new_jobs.close)
        new_server = desktop_ui.DesktopServer(new_jobs, Path(__file__).parent / 'frontend' / 'dist').start()
        self.addCleanup(new_server.stop)
        self.assertNotEqual(new_server.origin, self.server.origin)
        self.page.goto(new_server.url)
        expect(self.page.get_by_role('button', name='Развернуть меню', exact=True)).to_be_visible()
        self.expect_rail()
        self.page.get_by_role('button', name='Развернуть меню', exact=True).click()
        self.expect_width(420)
        self.assertEqual(self.errors, [])

    def test_leaving_during_a_pending_save_flushes_the_latest_preference(self):
        self.resize_to(420)
        self.page.wait_for_load_state('networkidle')
        held = []

        def delay_fetch_but_allow_unload_save(route):
            if route.request.resource_type == 'ping':
                route.continue_()
            else:
                held.append(route)

        self.page.route('**/api/sidebar', delay_fetch_but_allow_unload_save)
        self.page.get_by_role('button', name='Свернуть меню', exact=True).click()
        try:
            with self.page.expect_response(lambda response: response.url.endswith('/api/sidebar')
                                           and response.request.resource_type == 'ping', timeout=1500):
                self.page.goto('about:blank')
        except PlaywrightTimeoutError:
            pass
        self.assertEqual(self.jobs.snapshot()['sidebar'], {'width': 420, 'hidden': True})
        for route in held:
            route.abort()
        self.assertEqual(self.errors, [])

    def test_corrupt_cache_and_disabled_browser_storage_keep_the_menu_usable(self):
        for cached in ('{broken', '[420, true]', '{"width":"500","hidden":"true"}'):
            with self.subTest(cached=cached):
                self.page.evaluate('(value) => localStorage.setItem("pdf-photos.sidebar", value)', cached)
                self.page.reload()
                expect(self.page.get_by_role('button', name='Свернуть меню', exact=True)).to_be_visible()
                self.expect_width(248)
        self.page.add_init_script('''
            Storage.prototype.getItem = Storage.prototype.setItem = () => {
                throw new DOMException('Storage disabled', 'SecurityError');
            };
        ''')
        self.page.reload()
        self.resize_to(420)
        self.page.get_by_role('button', name='Свернуть меню', exact=True).click()
        expect(self.page.get_by_role('button', name='Развернуть меню', exact=True)).to_be_visible()
        self.expect_rail()
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
