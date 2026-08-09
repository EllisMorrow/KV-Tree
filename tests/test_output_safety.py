import os
import tempfile
import unittest
from unittest import mock

from src.core.app_state import AppState
from src.core.task_dispatcher import TaskDispatcher
from src.logic.ast_parser import AstParser
from src.logic.cache_manager import CacheManager


class _State:
    def __init__(self, output_root, selected=False, blacklist=(), source_files=None):
        self.output_root = output_root
        self.selected = selected
        self.blacklist = set(blacklist)
        self.active_outputs = {}
        self.source_files = source_files or {}

    def get_output_path(self):
        return self.output_root

    def get_blacklist(self):
        return self.blacklist

    def get_output_selection(self):
        return {"outside.md": self.selected, "safe.md": self.selected}

    def add_active_output(self, path, source):
        self.active_outputs[path] = source

    def get_source_files(self):
        return self.source_files

    def get_rules(self):
        return {}

    def get_advanced_options(self):
        return {}

    def get_logseq_exclude_keys(self):
        return set()

    def set_active_outputs(self, outputs):
        self.active_outputs = outputs


class _Cache:
    def __init__(self, outputs=None, cached_path="source.md"):
        self.outputs = outputs or {}
        self.cached_path = os.path.normcase(os.path.realpath(os.path.abspath(cached_path)))
        self.updated = []
        self.removed = []
        self.saved = 0

    def get_all_cached_paths(self):
        return [self.cached_path]

    def get_outputs_for_file(self, _path):
        return self.outputs

    def update_entry(self, *args):
        self.updated.append(args)

    def get_entry(self, _path):
        return {"mtime": 0, "outputs": self.outputs}

    def remove_entry(self, path):
        self.removed.append(path)

    def save_cache(self):
        self.saved += 1


def _dispatcher(state, cache):
    return TaskDispatcher(state, cache, {})


class OutputPathSafetyTests(unittest.TestCase):
    def test_parser_rejects_path_like_library_labels(self):
        text = "\n".join((
            "- safe #KV树-安全",
            "- slash #KV树-a/../../outside",
            r"- backslash #KV树-a\..\..\outside",
            "- dots #KV树-..",
        ))

        results, _ = AstParser().parse(text)

        self.assertEqual({"#KV树-安全.md": ["- safe"]}, dict(results))

    def test_outside_path_is_never_deleted_when_unselected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = os.path.join(temp_dir, "output")
            os.mkdir(output_root)
            outside_path = os.path.join(temp_dir, "outside.md")
            with open(outside_path, "w", encoding="utf-8") as file:
                file.write("keep")

            dispatcher = _dispatcher(_State(output_root, selected=False), _Cache())
            dispatcher._update_single_output_file(outside_path)

            self.assertTrue(os.path.exists(outside_path))
            with open(outside_path, "r", encoding="utf-8") as file:
                self.assertEqual("keep", file.read())

    def test_outside_path_is_never_deleted_when_blacklisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = os.path.join(temp_dir, "output")
            os.mkdir(output_root)
            outside_path = os.path.join(temp_dir, "outside.md")
            with open(outside_path, "w", encoding="utf-8") as file:
                file.write("keep")

            state = _State(output_root, blacklist={"outside.md"})
            dispatcher = _dispatcher(state, _Cache())
            dispatcher._update_single_output_file(outside_path)

            self.assertTrue(os.path.exists(outside_path))

    def test_outside_path_is_never_written_when_selected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = os.path.join(temp_dir, "output")
            os.mkdir(output_root)
            outside_path = os.path.join(temp_dir, "outside.md")
            dispatcher = _dispatcher(
                _State(output_root, selected=True),
                _Cache({outside_path: "content"}),
            )

            with mock.patch("src.core.task_dispatcher.atomic_write") as write:
                dispatcher._update_single_output_file(outside_path)

            write.assert_not_called()

    def test_different_windows_drive_is_outside(self):
        if os.name != "nt":
            self.skipTest("Windows drive semantics")
        self.assertFalse(
            TaskDispatcher._is_output_path_within_root(
                r"D:\outside.md", r"C:\configured-output"
            )
        )

    def test_windows_root_comparison_is_case_insensitive(self):
        if os.name != "nt":
            self.skipTest("Windows path case semantics")
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "safe.md")
            self.assertTrue(
                TaskDispatcher._is_output_path_within_root(target, temp_dir.swapcase())
            )


class ParseFailureCacheTests(unittest.TestCase):
    def test_stateless_parser_propagates_failure(self):
        with self.assertRaises(FileNotFoundError):
            TaskDispatcher._parse_single_file_stateless(
                "missing-source.md", {}, {}, "output", set()
            )

    def test_event_parse_failure_preserves_last_successful_cache(self):
        old = {"output/safe.md": "last good"}
        cache = _Cache(old)
        dispatcher = _dispatcher(_State("output"), cache)

        with mock.patch.object(
            dispatcher,
            "_parse_single_file_stateless",
            side_effect=ValueError("bad input"),
        ):
            with mock.patch("src.core.task_dispatcher.os.path.exists", return_value=True):
                previous, current = dispatcher._update_cache_for_file(
                    "source.md", rules={}, adv_opts={}, output_path_base="output",
                    logseq_exclude_keys=set()
                )

        self.assertEqual(old, previous)
        self.assertIsNone(current)
        self.assertEqual([], cache.updated)

    def test_initialize_parse_failure_preserves_last_successful_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, "source.md")
            output_root = os.path.join(temp_dir, "output")
            os.mkdir(output_root)
            with open(source_path, "w", encoding="utf-8") as file:
                file.write("content")

            output_path = os.path.join(output_root, "safe.md")
            old = {output_path: "last good"}
            cache = _Cache(old, cached_path=source_path)
            state = _State(
                output_root,
                source_files={source_path: {"enabled": True, "type": "file"}},
            )
            callbacks = {
                "set_status": mock.Mock(),
                "update_progress": mock.Mock(),
                "update_lists": mock.Mock(),
            }
            dispatcher = TaskDispatcher(state, cache, callbacks)

            with mock.patch.object(
                dispatcher,
                "_parse_single_file_stateless",
                side_effect=ValueError("bad input"),
            ):
                dispatcher._execute_initialize()

            self.assertEqual([], cache.updated)
            self.assertEqual([], cache.removed)
            self.assertEqual({output_path: "多元"}, state.active_outputs)

    def test_valid_empty_parse_replaces_cache(self):
        old = {"output/safe.md": "last good"}
        cache = _Cache(old)
        dispatcher = _dispatcher(_State("output"), cache)

        with mock.patch.object(dispatcher, "_parse_single_file_stateless", return_value={}):
            with mock.patch("src.core.task_dispatcher.os.path.exists", return_value=True):
                with mock.patch("src.core.task_dispatcher.os.path.getmtime", return_value=123):
                    previous, current = dispatcher._update_cache_for_file(
                        "source.md", rules={}, adv_opts={}, output_path_base="output",
                        logseq_exclude_keys=set()
                    )

        self.assertEqual(old, previous)
        self.assertEqual({}, current)
        self.assertEqual([("source.md", 123, {})], cache.updated)


class SourceRemovalTests(unittest.TestCase):
    def test_initialize_removes_cache_and_output_for_removed_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = os.path.join(temp_dir, "source.md")
            output_root = os.path.join(temp_dir, "output")
            output_path = os.path.join(output_root, "safe.md")
            os.mkdir(output_root)
            with open(source_path, "w", encoding="utf-8") as file:
                file.write("source still exists")
            with open(output_path, "w", encoding="utf-8") as file:
                file.write("stale output")

            state = AppState({
                "source_files": {},
                "output_path": output_root,
                "output_selection": {"safe.md": False},
            })
            cache = CacheManager(os.path.join(temp_dir, "cache.json"))
            cache.update_entry(source_path, os.path.getmtime(source_path), {output_path: "- old"})
            callbacks = {
                "set_status": mock.Mock(),
                "update_progress": mock.Mock(),
                "update_lists": mock.Mock(),
            }

            TaskDispatcher(state, cache, callbacks)._execute_initialize()

            self.assertNotIn(source_path, cache.get_all_cached_paths())
            self.assertFalse(os.path.exists(output_path))


if __name__ == "__main__":
    unittest.main()
