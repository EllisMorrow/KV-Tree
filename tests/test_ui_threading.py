import queue
import tempfile
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from src.core.app_state import AppState
from src.logic.cache_manager import CacheManager
from src.ui.components import DualRuleWindow
from src.ui.main_window import KvTreeAppUI
from src.ui.tray_icon import AppTrayIcon


class _UiHarness:
    _on_ui_thread = KvTreeAppUI._on_ui_thread
    call_on_ui_thread = KvTreeAppUI.call_on_ui_thread
    _drain_ui_callbacks = KvTreeAppUI._drain_ui_callbacks

    def __init__(self):
        self._ui_callback_queue = queue.Queue()
        self.after_calls = []
        self.reported = []

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))

    def report_callback_exception(self, *error_info):
        self.reported.append(error_info)


class _TrayUi:
    def __init__(self):
        self.queued = []
        self.closed = []

    def call_on_ui_thread(self, callback, *args, **kwargs):
        self.queued.append((callback, args, kwargs))

    def on_closing(self, **kwargs):
        self.closed.append(kwargs)


class UiThreadDispatchTests(unittest.TestCase):
    def test_worker_wrapper_only_queues_until_main_thread_drains(self):
        ui = _UiHarness()
        calls = []
        callback = ui._on_ui_thread(lambda value: calls.append(value))

        callback("ready")
        self.assertEqual([], calls)

        ui._drain_ui_callbacks()
        self.assertEqual(["ready"], calls)
        self.assertEqual(25, ui.after_calls[-1][0])

    def test_incremental_source_refresh_replaces_only_affected_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            notes = root / "notes"
            note = notes / "note.md"
            first, second = str(root / "first.md"), str(root / "second.md")
            cache = CacheManager(root / "cache.json")
            cache.update_entry(note, 1, {first: "- first", second: "- second"})
            cache.save_cache()
            cache = CacheManager(root / "cache.json")
            ui = _UiHarness()
            ui.app_state = AppState({
                "source_files": {str(notes): {"enabled": True, "type": "folder", "files": {str(note): 1}}},
            })
            ui.dispatcher = SimpleNamespace(cache_manager=cache)
            ui._output_sources = {}
            ui._apply_list_refresh = MethodType(KvTreeAppUI._apply_list_refresh, ui)
            ui.update_lists = Mock()

            KvTreeAppUI._prepare_list_refresh(ui)
            ui._drain_ui_callbacks()
            self.assertEqual({"first.md": {str(notes)}, "second.md": {str(notes)}}, ui._output_sources)
            self.assertEqual(("- first",), cache.get_output_contents(first))

            cache.update_entry(note, 2, {second: "- second"})
            with patch.object(cache, "get_output_sources", wraps=cache.get_output_sources) as read_sources:
                KvTreeAppUI._prepare_list_refresh(ui, {first})
                read_sources.assert_called_once_with("first.md")
            ui._drain_ui_callbacks()
            self.assertEqual({"second.md": {str(notes)}}, ui._output_sources)
            self.assertEqual((), cache.get_output_contents(first))

    def test_invalid_rule_is_reported_before_dialog_closes(self):
        for pattern, replacement in (("[", ""), ("a", r"\2")):
            with self.subTest(pattern=pattern, replacement=replacement):
                ui = SimpleNamespace(
                    line_rows=[(Mock(get=Mock(return_value=pattern)),
                                Mock(get=Mock(return_value=replacement)))],
                    content_rows=[], destroy=Mock(), result=None,
                )
                with patch("src.ui.components.messagebox.showerror") as error:
                    DualRuleWindow.save_and_close(ui)
                error.assert_called_once()
                self.assertIsNone(ui.result)
                ui.destroy.assert_not_called()

    def test_tray_callbacks_only_queue_ui_operations(self):
        ui = _TrayUi()
        tray = AppTrayIcon(ui, "test")

        tray.toggle_window()
        tray.on_closing_from_tray()

        self.assertEqual(2, len(ui.queued))
        self.assertEqual({}, ui.queued[0][2])
        self.assertEqual({"from_tray": True}, ui.queued[1][2])
        self.assertEqual([], ui.closed)


if __name__ == "__main__":
    unittest.main()
