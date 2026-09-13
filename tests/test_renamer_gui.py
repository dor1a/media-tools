"""Test GUI state and asynchronous callbacks without opening a window."""

import importlib.util
from pathlib import Path
from types import ModuleType
import unittest
from unittest.mock import MagicMock, Mock, patch


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tkinter = ModuleType('tkinter')
        tkinter.Tk = object
        tkinter.filedialog = Mock()
        tkinter.messagebox = Mock()
        tkinter.ttk = Mock()
        spec = importlib.util.spec_from_file_location(
            'renamer_gui_test', Path(__file__).resolve().parents[1] / 'renamer/gui.py')
        cls.gui = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', {'tkinter': tkinter}):
            spec.loader.exec_module(cls.gui)

    def setUp(self):
        self.app = self.gui.App.__new__(self.gui.App)
        self.app.generation = 0
        self.app.renames = []
        for name in ('provider_var', 'numbering_var', 'numbering_box', 'series_box',
                     'season_box', 'apply_btn', 'tree', 'status'):
            setattr(self.app, name, MagicMock())
        self.app.update_idletasks = Mock()

    def test_provider_switch_clears_previous_ids_and_plan(self):
        self.app.provider_var.get.return_value = 'TMDB'
        self.app.series = {'id': 42}
        self.app.results = [self.app.series]
        self.app.seasons = [1]
        self.app.renames = ['old plan']
        self.app.on_provider()
        self.assertIsNone(self.app.series)
        self.assertEqual(self.app.results, [])
        self.assertEqual(self.app.seasons, [])
        self.assertEqual(self.app.renames, [])
        self.app.apply_btn.config.assert_called_with(state='disabled')
        self.app.numbering_box.config.assert_called_with(state='disabled')

    def run_background(self, work, done):
        queued = []
        self.app.after = lambda delay, callback: queued.append(callback)
        with patch.object(self.gui.threading, 'Thread') as thread:
            thread.side_effect = lambda **kwargs: Mock(start=kwargs['target'])
            self.app.run_bg(work, done)
        return queued

    def test_provider_switch_discards_inflight_result(self):
        done = Mock()
        queued = self.run_background(lambda: ['old provider result'], done)
        self.app.on_provider()
        queued[0]()
        done.assert_not_called()

    def test_current_result_is_delivered(self):
        done = Mock()
        queued = self.run_background(lambda: ['current result'], done)
        queued[0]()
        done.assert_called_once_with(['current result'])

    def test_error_message_survives_exception_scope(self):
        self.gui.messagebox.reset_mock()
        queued = self.run_background(Mock(side_effect=RuntimeError('request failed')), Mock())
        queued[0]()
        self.gui.messagebox.showerror.assert_called_once_with('오류', 'request failed')
