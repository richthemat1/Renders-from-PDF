"""«В папку…» for a variant, «Варианты в папку…» for a result and the shared folder in the settings."""
from pathlib import Path
import unittest

from playwright.sync_api import expect

from photo_delivery import deliver
from photo_jobs import read_result
import test_desktop_ui as desktop_ui


class DeliveryUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        desktop_ui.DesktopUI.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        desktop_ui.DesktopUI.tearDownClass.__func__(cls)

    def setUp(self):
        desktop_ui.DesktopUI.setUp(self)
        self.folder = self.root / 'Готовые'
        self.folder.mkdir()
        self.calls = []

        def export(id, variants):
            # The native bridge without its folder dialog: the real copy and the real result.
            self.calls.append(variants)
            with self.server.lock:
                output = self.server.folders[id]
                return dict(deliver(output, read_result(output), self.folder, variants, subfolder=variants is None),
                            result=self.server.result(id))
        self.page.expose_function('exportVariantsForTest', export)
        self.page.expose_function('revealVariantForTest', lambda id, variant: self.calls.append(('reveal', variant)) or True)
        self.page.evaluate('''() => { window.pywebview = {api: {
            export_variants: (id, variants) => window.exportVariantsForTest(id, variants),
            reveal_variant: (id, variant) => window.revealVariantForTest(id, variant),
        }}; }''')

    save = desktop_ui.DesktopUI.save
    card = desktop_ui.DesktopUI.card

    def names(self):
        return sorted(path.name for path in self.folder.iterdir())

    def test_variant_button_puts_its_pdf_into_the_folder_and_the_menu_shows_it(self):
        variant = self.page.locator('nav').get_by_role('button', name='Вариант 1 2', exact=True)
        variant.click()
        self.page.get_by_role('button', name='Сохранить PDF варианта в папку', exact=True).click()
        expect(self.page.get_by_text('«Вариант 1.pdf» сохранён в папку', exact=False)).to_be_visible()
        self.assertEqual(self.names(), ['Вариант 1.pdf'])
        self.assertEqual(self.calls, [['1']])
        self.page.get_by_role('button', name='Показать в Проводнике', exact=True).click()
        variant.click(button='right')
        self.page.get_by_role('menuitem', name='Показать копию в Проводнике', exact=True).click()
        self.assertEqual(self.calls[1:], [('reveal', '1'), ('reveal', '1')])
        # The other variant has no copy yet: only the save action is offered.
        self.page.locator('nav').get_by_role('button', name='Вариант 2 1', exact=True).click(button='right')
        expect(self.page.get_by_role('menuitem', name='Сохранить PDF в папку…', exact=True)).to_be_enabled()
        expect(self.page.get_by_role('menuitem', name='Показать копию в Проводнике', exact=True)).to_have_count(0)
        self.assertEqual(self.errors, [])

    def test_all_variants_button_and_unsaved_edits_ask_to_save_first(self):
        self.page.get_by_role('button', name='Варианты в папку…', exact=True).click()
        expect(self.page.get_by_text('Сохранено PDF: 2', exact=False)).to_be_visible()
        # One folder named after the source PDF, and only the variants in it.
        inner = self.folder / Path(read_result(self.output)['source']).stem
        self.assertEqual(self.names(), [inner.name])
        self.assertEqual(sorted(path.name for path in inner.iterdir()), ['Вариант 1.pdf', 'Вариант 2.pdf'])
        expect(self.page.get_by_text(f'в папку «{inner}»', exact=False)).to_be_visible()
        self.assertEqual(self.calls, [None])
        self.page.get_by_role('button', name='Редактировать', exact=True).click()
        self.card(0).get_by_role('checkbox').check()
        self.page.get_by_role('button', name='Перенести в вариант', exact=True).click()
        self.page.get_by_role('menuitem', name='Вариант 2', exact=True).click()
        self.page.get_by_role('button', name='Варианты в папку…', exact=True).click()
        expect(self.page.get_by_text('Сначала сохраните правки', exact=False)).to_be_visible()
        self.assertEqual(self.calls, [None])
        # The save refreshes the copies made before by itself.
        before = (inner / 'Вариант 2.pdf').read_bytes()
        self.save()
        self.assertNotEqual((inner / 'Вариант 2.pdf').read_bytes(), before)
        self.assertEqual((inner / 'Вариант 2.pdf').read_bytes(),
                         (self.output / 'Вариант 2' / 'Вариант 2.pdf').read_bytes())
        self.assertEqual(self.errors, [])

    def test_settings_choose_and_switch_off_the_shared_folder(self):
        self.page.expose_function('chooseDeliveryForTest', lambda: self.jobs.set_delivery(str(self.folder)))
        self.page.expose_function('clearDeliveryForTest', lambda: self.jobs.set_delivery(None))
        self.page.evaluate('''() => {
            window.pywebview.api.choose_delivery = () => window.chooseDeliveryForTest();
            window.pywebview.api.clear_delivery = () => window.clearDeliveryForTest();
        }''')
        self.page.get_by_role('button', name='История обработки', exact=True).first.click()
        self.page.get_by_role('button', name='Настройки', exact=True).click()
        expect(self.page.get_by_text('Не сохранять отдельно', exact=True)).to_be_visible()
        self.page.get_by_role('button', name='Выбрать папку', exact=True).click()
        expect(self.page.get_by_text('Сохранять в папку', exact=True)).to_be_visible()
        expect(self.page.get_by_text(str(self.folder), exact=False)).to_be_visible()
        self.assertEqual(self.jobs.snapshot()['settings']['delivery'], str(self.folder.resolve()))
        self.page.get_by_role('button', name='Отключить', exact=True).click()
        expect(self.page.get_by_text('Не сохранять отдельно', exact=True)).to_be_visible()
        self.assertEqual(self.jobs.snapshot()['settings']['delivery'], '')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
