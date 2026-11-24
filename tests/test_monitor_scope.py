import os
import tempfile
import unittest
from unittest import mock

from src.core.app_state import AppState
from src.logic.file_monitor import AppEventHandler


class _Dispatcher:
    def __init__(self):
        self.tasks = []

    def put_task(self, task):
        self.tasks.append(task)


class MonitorScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = self.temp_dir.name

    def _write_markdown(self, relative_path):
        path = os.path.join(self.root, relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            file.write("# note\n")
        return path

    def _handler(self, sources):
        dispatcher = _Dispatcher()
        return AppEventHandler(dispatcher, AppState({"source_files": sources, "excluded_paths": []})), dispatcher

    def test_unselected_sibling_file_is_not_queued(self):
        selected = self._write_markdown("selected.md")
        sibling = self._write_markdown("sibling.md")
        handler, dispatcher = self._handler({
            selected: {"enabled": True, "type": "file"},
        })

        handler.process_event("modified", sibling)
        self.assertEqual(dispatcher.tasks, [])

        handler.process_event("modified", selected)
        self.assertEqual(len(dispatcher.tasks), 1)
        self.assertEqual(
            dispatcher.tasks[0][2],
            os.path.normcase(os.path.realpath(os.path.abspath(selected))),
        )

    def test_enabled_folder_queues_markdown_within_its_tree(self):
        folder = os.path.join(self.root, "notes")
        nested_file = self._write_markdown(os.path.join("notes", "nested", "inside.md"))
        handler, dispatcher = self._handler({
            folder: {"enabled": True, "type": "folder", "files": {}},
        })

        handler.process_event("modified", nested_file)

        self.assertEqual(len(dispatcher.tasks), 1)
        self.assertEqual(dispatcher.tasks[0][1], "modified")

    def test_deleted_legacy_path_is_removed_without_reading_source_snapshot(self):
        folder = os.path.join(self.root, "notes")
        note = self._write_markdown(os.path.join("notes", "note.md"))
        legacy = os.path.join(folder, ".", "note.md")
        handler, dispatcher = self._handler({
            folder: {"enabled": True, "type": "folder", "files": {legacy: 1}},
        })
        os.remove(note)
        with mock.patch.object(handler.state, "get_source_files", side_effect=AssertionError):
            handler.process_event("deleted", note)
        self.assertEqual({}, handler.state.get_source_files()[folder]["files"])
        self.assertEqual(1, len(dispatcher.tasks))

    def test_similar_prefix_and_disabled_sources_are_not_queued(self):
        enabled_folder = os.path.join(self.root, "notes")
        similar_prefix_file = self._write_markdown(os.path.join("notes-archive", "outside.md"))
        disabled_file = self._write_markdown("disabled.md")
        handler, dispatcher = self._handler({
            enabled_folder: {"enabled": True, "type": "folder", "files": {}},
            disabled_file: {"enabled": False, "type": "file"},
        })

        handler.process_event("modified", similar_prefix_file)
        handler.process_event("modified", disabled_file)

        self.assertEqual(dispatcher.tasks, [])


if __name__ == "__main__":
    unittest.main()
