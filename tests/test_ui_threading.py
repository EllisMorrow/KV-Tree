import queue
import unittest

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
