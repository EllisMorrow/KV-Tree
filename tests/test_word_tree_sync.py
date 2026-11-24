import tempfile
import time
from threading import Event
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.core.app_state import AppState
from src.core.task_dispatcher import TaskDispatcher
from src.logic.ast_parser import AstParser
from src.logic.cache_manager import CacheManager
from src.logic.file_monitor import AppEventHandler, FileMonitor
from src.logic.path_filter import normalize, scan_markdown


class WordTreeSyncTests(unittest.TestCase):
    def test_batch_wait_is_bounded_even_with_long_debounce(self):
        processed = Event()
        dispatcher = TaskDispatcher(AppState(), Mock(get_pending_exports=Mock(return_value=set())), {"set_status": Mock()})
        dispatcher.debounce_seconds = 60
        dispatcher.max_batch_wait = 0.1

        def process_batch():
            dispatcher.dirty_files.clear()
            processed.set()

        dispatcher._process_dirty_batch = process_batch
        dispatcher.start()
        try:
            dispatcher.put_task(("process_file", "modified", "note.md"))
            self.assertTrue(processed.wait(3))
        finally:
            dispatcher.stop()

    def test_worker_processes_next_batch_after_unexpected_batch_failure(self):
        failed, processed = Event(), Event()

        def process_batch():
            dispatcher.dirty_files.clear()
            if not failed.is_set():
                failed.set()
                raise OSError("simulated batch failure")
            processed.set()

        dispatcher = TaskDispatcher(AppState(), Mock(get_pending_exports=Mock(return_value=set())), {"set_status": Mock()})
        dispatcher.debounce_seconds = 0
        dispatcher._process_dirty_batch = process_batch
        dispatcher.start()
        try:
            dispatcher.put_task(("process_file", "modified", "first.md"))
            self.assertTrue(failed.wait(3))
            dispatcher.put_task(("process_file", "modified", "second.md"))
            self.assertTrue(processed.wait(3))
        finally:
            dispatcher.stop()

    def test_pending_file_events_share_one_queue_entry_per_path(self):
        dispatcher = TaskDispatcher(AppState(), Mock(get_pending_exports=Mock(return_value=set())), {})
        for event in ("created", "modified", "deleted"):
            dispatcher.put_task(("process_file", event, "first.md"))
        dispatcher.put_task(("process_file", "created", "second.md"))
        self.assertEqual(2, dispatcher.task_queue.qsize())

    def test_export_failure_reports_and_retries_with_latest_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "note.md"
            source.write_text("- 新词条 #KV树-测试", encoding="utf-8")
            output = root / "#KV树-测试.md"
            output.write_text("- 旧词条", encoding="utf-8")
            state = AppState({
                "source_files": {str(source): {"enabled": True, "type": "file"}},
                "output_path": folder,
                "output_selection": {output.name: True},
            })
            cache = CacheManager(root / "cache.json")
            callbacks = {key: Mock() for key in ("set_status", "update_lists")}
            dispatcher = TaskDispatcher(state, cache, callbacks)
            dispatcher.dirty_files.add(("modified", normalize(source)))
            with patch("src.core.task_dispatcher.atomic_write", side_effect=PermissionError("locked")):
                dispatcher._process_dirty_batch()
            self.assertIn("导出失败", callbacks["set_status"].call_args.args[0])
            self.assertEqual("- 旧词条", output.read_text(encoding="utf-8"))
            self.assertEqual(("- 新词条",), cache.get_output_contents(str(output)))

            dispatcher._execute_regenerate_output(str(output))
            self.assertIn("更新完成", callbacks["set_status"].call_args.args[0])
            self.assertEqual("- 新词条", output.read_text(encoding="utf-8"))
            self.assertEqual(set(), dispatcher._failed_exports)

    def test_failed_export_write_and_delete_retry_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "note.md"
            source.write_text("- 旧词条 #KV树-测试", encoding="utf-8")
            output = root / "exports" / "#KV树-测试.md"
            state = AppState({
                "source_files": {str(source): {"enabled": True, "type": "file"}},
                "output_path": str(output.parent),
                "output_selection": {output.name: True},
            })
            callbacks = {key: Mock() for key in ("set_status", "update_lists", "update_progress")}

            def new_dispatcher():
                return TaskDispatcher(state, CacheManager(root / "cache.json"), callbacks)

            dispatcher = new_dispatcher()
            dispatcher._execute_initialize()
            source.write_text("- 新词条 #KV树-测试", encoding="utf-8")
            dispatcher.dirty_files.add(("modified", normalize(source)))
            with patch("src.core.task_dispatcher.atomic_write", side_effect=PermissionError("locked")):
                dispatcher._process_dirty_batch()
            self.assertEqual("- 旧词条", output.read_text(encoding="utf-8"))

            dispatcher = new_dispatcher()
            self.assertEqual({str(output)}, dispatcher.cache_manager.get_pending_exports())
            dispatcher._execute_initialize()
            self.assertEqual("- 新词条", output.read_text(encoding="utf-8"))
            self.assertEqual(set(), new_dispatcher().cache_manager.get_pending_exports())

            source.unlink()
            dispatcher.dirty_files.add(("deleted", normalize(source)))
            with patch.object(dispatcher, "_remove_output_file", side_effect=PermissionError("locked")):
                dispatcher._process_dirty_batch()
            self.assertTrue(output.exists())

            dispatcher = new_dispatcher()
            self.assertIsNone(dispatcher.cache_manager.get_entry(source))
            self.assertEqual({str(output)}, dispatcher.cache_manager.get_pending_exports())
            dispatcher._execute_initialize()
            self.assertFalse(output.exists())
            self.assertEqual(set(), new_dispatcher().cache_manager.get_pending_exports())

    def test_full_scan_keeps_concurrent_file_events(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            removed, added = root / "removed.md", root / "added.md"
            removed.write_text("- removed #KV树-测试", encoding="utf-8")
            stale = {str(removed): removed.stat().st_mtime}
            state = AppState({"source_files": {
                str(root): {"enabled": True, "type": "folder", "files": stale}
            }})
            cache = CacheManager(root / "cache.json")
            callbacks = {key: Mock() for key in ("set_status", "update_lists", "update_progress")}
            dispatcher = TaskDispatcher(state, cache, callbacks)
            watcher = AppEventHandler(dispatcher, state)

            def scan_with_concurrent_changes(*args):
                removed.unlink()
                watcher.process_event("deleted", str(removed))
                added.write_text("- added #KV树-测试", encoding="utf-8")
                watcher.process_event("created", str(added))
                return stale, []

            with patch("src.core.task_dispatcher.scan_markdown", side_effect=scan_with_concurrent_changes):
                dispatcher._execute_initialize()
            self.assertIsNone(cache.get_entry(removed))
            self.assertIsNotNone(cache.get_entry(added))
            self.assertEqual({str(root)}, state.get_source_owners(
                {normalize(added)}, [(str(root), normalize(root), "folder")]
            ))

    def test_nested_directory_events_scan_only_the_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            parent, sibling = root / "parent", root / "parent-sibling"
            child = parent / "child"
            child.mkdir(parents=True)
            sibling.mkdir()
            note = child / "note.md"
            note.touch()
            state = AppState({"source_files": {str(root): {"enabled": True, "type": "folder"}}})
            dispatcher = TaskDispatcher(state, CacheManager(root / "cache.json"), {})
            events = {("directory", normalize(path)) for path in (parent, child, sibling)}
            with patch("src.core.task_dispatcher.scan_markdown", wraps=scan_markdown) as scan:
                self.assertEqual({normalize(note)}, dispatcher._collect_changed_paths(events))
            self.assertEqual({normalize(parent), normalize(sibling)},
                             {call.args[0] for call in scan.call_args_list})
            self.assertEqual(2, scan.call_count)

    def test_unchanged_words_keep_export_and_list_while_refreshing_cache_mtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "note.md"
            source.write_text("- 内容 #KV树-测试", encoding="utf-8")
            output = root / "#KV树-测试.md"
            output.write_text("- 内容", encoding="utf-8")
            state = AppState({
                "source_files": {str(source): {"enabled": True, "type": "file"}},
                "output_path": folder,
                "output_selection": {output.name: True},
            })
            cache = CacheManager(root / "cache.json")
            cache.update_entry(source, 1, {str(output): "- 内容"})
            callbacks = {key: Mock() for key in ("set_status", "update_lists")}
            dispatcher = TaskDispatcher(state, cache, callbacks)
            dispatcher.dirty_files.add(("modified", normalize(source)))
            with patch("src.core.task_dispatcher.atomic_write") as write:
                dispatcher._process_dirty_batch()
            write.assert_not_called()
            callbacks["update_lists"].assert_not_called()
            persisted = CacheManager(root / "cache.json").get_entry(source)
            self.assertEqual(source.stat().st_mtime, persisted["mtime"])
            self.assertEqual("- 内容", output.read_text(encoding="utf-8"))

    def test_indent_increase_after_sibling_stays_in_current_branch(self):
        outputs, _ = AstParser().parse(
            "- A #KV树-A-父与子\n  - child A\n- B #KV树-B-父与子\n    - child B"
        )
        self.assertEqual(["- A", "- child A"], outputs["#KV树-A.md"])
        self.assertEqual(["- B", "- child B"], outputs["#KV树-B.md"])

    def test_export_normalizes_entries_before_deduplicating_across_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first, second = root / "first.md", root / "second.md"
            first.write_text(
                "- parent #KV树-test-父与子\n  - child\n    - grandchild\n"
                "- omitted #KV树-test-不包含父\n  - second parent\n    - child",
                encoding="utf-8",
            )
            second.write_text("- child #KV树-test", encoding="utf-8")
            output = root / "exports" / "#KV树-test.md"
            state = AppState({
                "source_files": {str(path): {"enabled": True, "type": "file"}
                                 for path in (first, second)},
                "output_path": str(output.parent),
                "output_selection": {output.name: True},
            })
            callbacks = {key: Mock() for key in ("set_status", "update_lists", "update_progress")}
            dispatcher = TaskDispatcher(state, CacheManager(root / "cache.json"), callbacks)
            dispatcher._execute_initialize()

            self.assertEqual(["- child", "- grandchild", "- parent", "- second parent"],
                             output.read_text(encoding="utf-8").splitlines())
            output.chmod(0o666)

    def test_new_rules_invalidate_unchanged_source_cache_on_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            note = root / "note.md"
            note.write_text("- demo #KV树-test", encoding="utf-8")
            config = {"source_files": {str(note): {"enabled": True, "type": "file"}}}
            callbacks = {key: Mock() for key in ("set_status", "update_lists", "update_progress")}
            cache_path = root / "cache.json"
            cache = CacheManager(cache_path)
            TaskDispatcher(AppState(config), cache, callbacks)._execute_initialize()
            self.assertTrue(cache.get_outputs_for_file(note))

            rules = {"line_rules": [{"match": "demo", "replace": ""}]}
            config["rules"] = rules
            cache = CacheManager(cache_path)
            dispatcher = TaskDispatcher(AppState(config), cache, callbacks)
            dispatcher._execute_initialize()
            self.assertEqual({}, cache.get_outputs_for_file(note))
            with patch.object(dispatcher, "_parse_single_file_stateless") as parse:
                dispatcher._execute_initialize()
            parse.assert_not_called()

    def test_blacklist_changes_reconcile_export_without_reparsing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            note = root / "note.md"
            note.write_text("- demo #KV树-test", encoding="utf-8")
            output = root / "out" / "#KV树-test.md"
            state = AppState({
                "source_files": {str(note): {"enabled": True, "type": "file"}},
                "output_path": str(output.parent),
                "output_selection": {output.name: True},
            })
            callbacks = {key: Mock() for key in ("set_status", "update_lists", "update_progress")}
            dispatcher = TaskDispatcher(state, CacheManager(root / "cache.json"), callbacks)
            dispatcher._execute_initialize()
            self.assertTrue(output.exists())
            with patch.object(dispatcher, "_parse_single_file_stateless") as parse:
                state.add_to_blacklist(output.name)
                dispatcher._execute_initialize()
                self.assertFalse(output.exists())
                state.remove_from_blacklist(output.name)
                dispatcher._execute_initialize(reconcile_names={output.name})
                self.assertEqual("- demo", output.read_text(encoding="utf-8"))
            parse.assert_not_called()

    def test_recreated_monitor_root_is_reopened_and_rescanned(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            notes = root / "notes"
            notes.mkdir()
            state = AppState({"source_files": {str(notes): {"enabled": True, "type": "folder"}}})
            callbacks = {key: Mock() for key in ("set_status", "update_lists", "update_progress")}
            cache = CacheManager(root / "cache.json")
            dispatcher = TaskDispatcher(state, cache, callbacks)
            monitor = FileMonitor(dispatcher, state, callbacks)
            dispatcher.start()
            try:
                monitor.start()
                notes.rename(root / "moved")
                monitor.check_watches()
                notes.mkdir()
                existing = notes / "before-reconnect.md"
                existing.write_text("- existing #KV树-test", encoding="utf-8")
                monitor.check_watches()
                dispatcher.task_queue.join()
                self.assertIsNotNone(cache.get_entry(existing))

                new = notes / "after-reconnect.md"
                new.write_text("- new #KV树-test", encoding="utf-8")
                deadline = time.monotonic() + 8
                while cache.get_entry(new) is None:
                    self.assertLess(time.monotonic(), deadline, "监控未重新接收文件事件")
                    time.sleep(0.05)
            finally:
                monitor.stop()
                dispatcher.stop()

    def test_clear_cache_rebuild_removes_obsolete_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "note.md"
            source.write_text("- 普通正文", encoding="utf-8")
            exports = root / "exports"
            exports.mkdir()
            stale = exports / "#KV树-旧词库.md"
            stale.write_text("- 旧词条", encoding="utf-8")
            state = AppState({
                "source_files": {str(source): {"enabled": True, "type": "file"}},
                "output_path": str(exports),
                "output_selection": {stale.name: True},
            })
            cache = CacheManager(root / "cache.json")
            cache.update_entry(source, source.stat().st_mtime, {str(stale): "- 旧词条"})
            cache.save_cache()
            callbacks = {key: Mock() for key in ("set_status", "update_progress", "update_lists", "show_error")}
            TaskDispatcher(state, cache, callbacks)._execute_clear_cache()
            callbacks["show_error"].assert_not_called()
            self.assertFalse(stale.exists())
            self.assertEqual({}, CacheManager(root / "cache.json").get_outputs_for_file(source))

    def test_fenced_code_tags_are_ignored(self):
        for opening, closing in (("```markdown", "```"), ("````", "````"), ("~~~", "~~~"), ("- ```", "  ```")):
            with self.subTest(opening=opening):
                text = '\n'.join((opening, '- 示例 #KV树-忽略', closing, '- 正文 #KV树-保留'))
                self.assertEqual({'#KV树-保留.md': ['- 正文']}, AstParser().parse(text)[0])
        self.assertEqual({}, AstParser().parse('```\n#KV树-未关闭')[0])

    def test_code_examples_are_not_tags_and_survive_before_real_tag(self):
        text = (
            '- 见 `#KV树-读书词库.md` 文件\n'
            r'- 见 \`#KV树-读书词库.md\`' '\n'
            '- 见 ``嵌套 `#KV树-读书词库.md` 示例``\n'
            '- 保留 `#KV树-读书词库.md` #KV树-真实\n'
        )
        outputs, _ = AstParser().parse(text)
        self.assertEqual({"#KV树-真实.md": ["- 保留 `#KV树-读书词库.md`"]}, outputs)

    def test_startup_reparses_legacy_cache_with_unchanged_mtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.md"
            source.write_text('- 示例 `#KV树-误识别.md`', encoding="utf-8")
            exports = root / "exports"
            exports.mkdir()
            stale = exports / "#KV树-误识别.md"
            stale.write_text("- 旧缓存", encoding="utf-8")
            state = AppState({
                "source_files": {str(source): {"enabled": True, "type": "file"}},
                "output_path": str(exports),
                "output_selection": {stale.name: True},
            })
            cache = CacheManager(root / "cache.json")
            cache.update_entry(source, source.stat().st_mtime, {str(stale): "- 旧缓存"})
            cache.get_entry(source).pop("parser_revision")
            cache.save_cache()
            cache = CacheManager(root / "cache.json")
            callbacks = {key: Mock() for key in ("set_status", "update_progress", "update_lists")}
            TaskDispatcher(state, cache, callbacks)._execute_initialize()
            self.assertFalse(stale.exists())
            self.assertEqual({}, state.get_active_outputs())
            self.assertEqual({}, cache.get_outputs_for_file(source))

    def test_watchdog_updates_shared_library_and_removes_last_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sources, exports = root / "sources", root / "exports"
            sources.mkdir()
            exports.mkdir()
            first, second = sources / "first.md", sources / "second.md"
            name = "#KV树-测试.md"
            output = exports / name
            state = AppState({
                "source_files": {str(sources): {"enabled": True, "type": "folder", "files": {}}},
                "output_path": str(exports),
                "output_selection": {name: True},
            })
            cache = CacheManager(root / "cache.json")
            callbacks = {key: Mock() for key in ("set_status", "update_lists", "show_error")}
            dispatcher = TaskDispatcher(state, cache, callbacks)
            dispatcher.debounce_seconds = 0.1
            monitor = FileMonitor(dispatcher, state, callbacks)

            def wait_for(predicate):
                deadline = time.monotonic() + 8
                while not predicate():
                    self.assertLess(time.monotonic(), deadline, "监控未同步到预期状态")
                    time.sleep(0.05)

            def lines():
                try:
                    return set(output.read_text(encoding="utf-8").splitlines())
                except FileNotFoundError:
                    return set()

            dispatcher.start()
            try:
                monitor.start()
                first.write_text("- 甲 #KV树-测试\n- 乙 #KV树-测试", encoding="utf-8")
                second.write_text("- 丙 #KV树-测试", encoding="utf-8")
                wait_for(lambda: lines() == {"- 甲", "- 乙", "- 丙"})
                first.write_text("- 乙 #KV树-测试", encoding="utf-8")
                wait_for(lambda: lines() == {"- 乙", "- 丙"})
                second.rename(sources / "renamed.md")
                wait_for(lambda: cache.get_entry(second) is None and cache.get_entry(sources / "renamed.md"))
                self.assertEqual({"- 乙", "- 丙"}, lines())
                (sources / "renamed.md").unlink()
                wait_for(lambda: lines() == {"- 乙"})
                incoming = root / "incoming"
                incoming.mkdir()
                (incoming / "group.md").write_text("- 丁 #KV树-测试", encoding="utf-8")
                group = sources / "group"
                incoming.rename(group)
                wait_for(lambda: lines() == {"- 乙", "- 丁"})
                renamed_group = sources / "renamed.md"
                group.rename(renamed_group)
                wait_for(lambda: cache.get_entry(group / "group.md") is None
                         and cache.get_entry(renamed_group / "group.md") is not None)
                self.assertEqual({"- 乙", "- 丁"}, lines())
                renamed_group.rename(root / "moved-out")
                wait_for(lambda: lines() == {"- 乙"})
                self.assertIsNone(cache.get_entry(renamed_group / "group.md"))
                self.assertNotIn(normalize(renamed_group / "group.md"),
                                 state.get_source_files()[str(sources)]["files"])
                first.write_text("- 见 `#KV树-测试.md`", encoding="utf-8")
                wait_for(lambda: not output.exists() and not state.get_active_outputs())
            finally:
                monitor.stop()
                dispatcher.stop()
