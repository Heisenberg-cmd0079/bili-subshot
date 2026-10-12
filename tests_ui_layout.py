# -*- coding: utf-8 -*-
"""GUI layout regression checks. Run on a desktop: python tests_ui_layout.py.

DPI/resolution cases are simulated through CTk scaling and monitor fixtures;
they do not change the operating system display settings.
"""
import importlib.machinery
import importlib.util
import sys
import unittest
from pathlib import Path

import customtkinter as ctk


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).with_name('gui_app.pyw')
        loader = importlib.machinery.SourceFileLoader('ui_under_test', str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cls.module = importlib.util.module_from_spec(spec)
        loader.exec_module(cls.module)
        # Fixture data: no developer config, personal titles, or network calls.
        cls.original_load_config = cls.module.sc.load_config
        cls.module.sc.load_config = lambda: dict(cls.module.sc.DEFAULT_CONFIG,
            url='', filename='示例视频', start='00:00:00', end='00:00:10')
        try:
            cls.app = cls.module.App()
        except Exception as error:
            if 'display' in str(error).lower():
                raise unittest.SkipTest('Requires a graphical desktop')
            raise
        cls.errors = []
        cls.app.report_callback_exception = lambda *error: cls.errors.append(error)
        cls.app.update()
        cls.dpi = cls.app._window_scale()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()  # Never invoke _on_close: it writes user configuration.
        cls.module.sc.load_config = cls.original_load_config

    def settle(self):
        for _ in range(3):
            self.app.update()
            self.app._reflow_layout()
        self.app.update()

    def assert_bounds(self, parent):
        for child in parent.winfo_children():
            if child.winfo_manager() and child.winfo_ismapped():
                # The scroll frame intentionally extends beyond its viewport.
                if not isinstance(child, ctk.CTkScrollableFrame):
                    with self.subTest(widget=child.__class__.__name__):
                        self.assertGreaterEqual(child.winfo_x(), -2)
                        self.assertGreaterEqual(child.winfo_y(), -2)
                        self.assertLessEqual(child.winfo_x()+child.winfo_width(), parent.winfo_width()+4)
                        self.assertLessEqual(child.winfo_y()+child.winfo_height(), parent.winfo_height()+4)
            self.assert_bounds(child)

    def test_resolution_and_dpi_matrix(self):
        cases = [(1366, 768, 1.0), (1920, 1080, 1.25), (1920, 1080, 1.5),
                 (2560, 1440, 1.75), (3840, 2160, 2.0)]
        for width, height, dpi in cases:
            with self.subTest(resolution=(width, height), dpi=dpi):
                ctk.set_widget_scaling(dpi / self.dpi)
                ctk.set_window_scaling(dpi / self.dpi)
                self.app._set_scaled_min_max()
                self.app._available_screen = lambda w=width, h=height: (w, h-40)
                self.app._fit_geometry(850, 1050)
                self.settle()
                self.assertLessEqual(self.app.winfo_width(), width-35)
                self.assertLessEqual(self.app.winfo_height(), height-110)
                self.assertEqual(self.app.f_in.cget('size'), 16)
                self.assertEqual(self.app.f_log.cget('size'), 14)
                self.assertTrue(self.app.log_box.winfo_ismapped())
                self.assertTrue(self.app.run_btn.winfo_ismapped())
                self.assert_bounds(self.app)
                print(f'PASS layout {width}x{height} @ {dpi*100:g}%')
        self.assertFalse(self.errors, self.errors)

    def test_narrow_wide_reflow_preserves_values(self):
        before = self.app._collect()
        for width in (560, 620, 850, 560, 850):
            self.app.geometry(f'{width}x700')
            self.settle()
            self.assertEqual(self.app._collect(), before)
            self.assertEqual(self.app.cookie_menu.grid_info()['row'], 3 if width < 720 else 1)
            self.assertEqual(self.app._preview_button.grid_info()['row'], 1 if width < 720 else 0)
            self.assert_bounds(self.app)
        self.assertFalse(self.errors, self.errors)

    def test_stale_title_failure_keeps_current_hint(self):
        self.app.url_entry.delete(0, 'end')
        self.app.url_entry.insert(0, 'https://www.bilibili.com/video/BV1xx411c7mD')
        self.app._set_url_hint('当前视频标题')
        self.app._on_title_fetched('https://www.bilibili.com/video/BV1aBhR69Ef6', '')
        self.assertEqual(self.app.url_hint.cget('text'), '当前视频标题')


if __name__ == '__main__':
    unittest.main(verbosity=2)
