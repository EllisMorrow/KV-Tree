import tempfile
import time
from threading import Event
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.core.app_state import AppState
from src.core.task_dispatcher import TaskDispatcher
from src.logic.ast_parser import AstParser
from src.logic.cache_manager import CacheManager
from src.logic.file_monitor import FileMonitor


class WordTreeSyncTests(unittest.TestCase):
    def test_batch_wait_is_bounded_even_with_long_debounce(self):
        processed = Event()
        dispatcher = TaskDispatcher(AppState(), Mock(), {"set_status": Mock()})
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

        dispatcher = TaskDispatcher(AppState(), Mock(), {"set_status": Mock()})
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
                first.write_text("- 见 `#KV树-测试.md`", encoding="utf-8")
                wait_for(lambda: not output.exists() and not state.get_active_outputs())
            finally:
                monitor.stop()
                dispatcher.stop()
