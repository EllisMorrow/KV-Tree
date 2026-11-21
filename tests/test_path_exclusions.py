import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.core.app_state import AppState
from src.core.task_dispatcher import TaskDispatcher
from src.logic.cache_manager import CacheManager
from src.logic.config_manager import ConfigManager
from src.logic.file_monitor import AppEventHandler
from src.logic.path_filter import DEFAULT_EXCLUDED_PATHS, scan_markdown


class PathExclusionTests(unittest.TestCase):
    def test_scan_and_monitor_share_exclusions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in DEFAULT_EXCLUDED_PATHS + ['normal']:
                directory = root / name / 'nested'
                directory.mkdir(parents=True)
                (directory / 'note.MD').write_text('- 词条 #KV树-测试', encoding='utf-8')
            files, errors = scan_markdown(temp, DEFAULT_EXCLUDED_PATHS)
            self.assertFalse(errors)
            self.assertEqual([str(root / 'normal' / 'nested' / 'note.MD')], list(files))
            state = AppState({'source_files': {temp: {'type': 'folder', 'enabled': True}}})
            dispatcher = Mock()
            handler = AppEventHandler(dispatcher, state)
            for name in DEFAULT_EXCLUDED_PATHS:
                handler.process_event('created', str(root / name / 'nested' / 'note.MD'))
            dispatcher.put_task.assert_not_called()

    def test_disable_restore_and_exclude_remove_contributions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / 'output'
            output.mkdir()
            source = root / 'note.md'
            source.write_text('- 词条 #KV树-测试', encoding='utf-8')
            filename = '#KV树-测试.md'
            state = AppState({'source_files': {str(source): {'type': 'file', 'enabled': True}},
                              'output_path': str(output), 'output_selection': {filename: True}})
            callbacks = {key: Mock() for key in ('set_status', 'update_progress', 'update_lists')}
            dispatcher = TaskDispatcher(state, CacheManager(root / 'cache.json'), callbacks)
            dispatcher._execute_initialize()
            self.assertTrue((output / filename).exists())
            state.update_source_file(str(source), {'type': 'file', 'enabled': False})
            dispatcher._execute_initialize()
            self.assertFalse((output / filename).exists())
            self.assertFalse(state.get_active_outputs())
            state.update_source_file(str(source), {'type': 'file', 'enabled': True})
            dispatcher._execute_initialize()
            self.assertTrue((output / filename).exists())
            state.set_excluded_paths([str(source)])
            dispatcher._execute_initialize()
            self.assertFalse((output / filename).exists())
            manager = ConfigManager(root / 'config.json')
            manager.save_config(state.get_all_data())
            self.assertEqual([str(source)], manager.load_config()['excluded_paths'])
            state.set_excluded_paths([])
            manager.save_config(state.get_all_data())
            self.assertEqual([], manager.load_config()['excluded_paths'])
