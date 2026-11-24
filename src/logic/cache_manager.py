# app_logic/cache_manager.py
# 负责管理解析缓存 (parsing_cache.json)

import json
import os
import threading

from src.utils.file_utils import atomic_write_json

PARSER_REVISION = 4
_PENDING_EXPORTS_KEY = "<pending_exports>"


def _normalize_source_path(file_path):
    """Use one canonical key for cache access across watcher and startup paths."""
    return os.path.normcase(os.path.realpath(os.path.abspath(file_path)))


class CacheManager:
    def __init__(self, cache_file_path):
        self.cache_file = cache_file_path
        self.cache_data = {}
        self._pending_exports = set()
        self._by_output_name = {}
        self._by_directory = {}
        self.lock = threading.Lock() # 线程锁，确保多线程写入安全
        self.load_cache()

    def load_cache(self):
        """从文件加载缓存到内存中。"""
        with self.lock:
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as file:
                    loaded = json.load(file)
            except (ValueError, OSError):
                loaded = {}
            if not isinstance(loaded, dict):
                loaded = {}

            pending = loaded.pop(_PENDING_EXPORTS_KEY, [])
            self._pending_exports = {
                path for path in pending if self._valid_export_path(path)
            } if isinstance(pending, list) else set()
            self.cache_data = {
                _normalize_source_path(path): entry
                for path, entry in loaded.items() if "\x00" not in path and self._valid_entry(entry)
            }

            self._by_output_name.clear()
            self._by_directory.clear()
            for path, entry in self.cache_data.items():
                self._index_outputs(path, entry.get("outputs", {}))
                self._index_source(path)

    @staticmethod
    def _valid_export_path(path):
        return isinstance(path, str) and "\x00" not in path and path.lower().endswith(".md")

    @classmethod
    def _valid_entry(cls, entry):
        if not isinstance(entry, dict):
            return False
        outputs = entry.get("outputs", {})
        return isinstance(outputs, dict) and all(
            cls._valid_export_path(path) and isinstance(content, str)
            for path, content in outputs.items()
        )

    def get_pending_exports(self):
        with self.lock:
            return self._pending_exports.copy()

    @staticmethod
    def _ancestors(path):
        parent = os.path.dirname(path)
        while parent != path:
            yield parent
            path, parent = parent, os.path.dirname(parent)

    def _index_source(self, path):
        for parent in self._ancestors(path):
            self._by_directory.setdefault(parent, set()).add(path)

    def get_paths_below(self, directory):
        with self.lock:
            return self._by_directory.get(directory, set()).copy()

    def _index_outputs(self, source, outputs):
        for output, content in outputs.items():
            if content:
                self._by_output_name.setdefault(os.path.basename(output), {}).setdefault(output, {})[source] = content

    def _remove_indexed_outputs(self, source):
        entry = self.cache_data.get(source, {})
        for output, content in entry.get("outputs", {}).items():
            if not content:
                continue
            name = os.path.basename(output)
            paths = self._by_output_name[name]
            contributors = paths[output]
            del contributors[source]
            if not contributors:
                del paths[output]
            if not paths:
                del self._by_output_name[name]

    def get_output_names(self):
        with self.lock:
            return set(self._by_output_name)

    def get_output_paths(self):
        with self.lock:
            return {path for paths in self._by_output_name.values() for path in paths}

    def get_output_sources(self, name):
        with self.lock:
            return {source for contributors in self._by_output_name.get(name, {}).values()
                    for source in contributors}

    def get_output_contents(self, output):
        with self.lock:
            paths = self._by_output_name.get(os.path.basename(output), {})
            return tuple(paths.get(output, {}).values())

    def save_cache(self, pending_exports=None):
        """Persist source records and outstanding exports in one atomic write."""
        with self.lock:
            if pending_exports is not None:
                self._pending_exports = set(pending_exports)
            data = self.cache_data
            if self._pending_exports:
                data = {**data, _PENDING_EXPORTS_KEY: sorted(self._pending_exports)}
            try:
                atomic_write_json(self.cache_file, data, ensure_ascii=False, indent=4)
            except OSError as error:
                print(f"Error saving cache file: {error}")
                return False
            return True

    def get_entry(self, file_path):
        """获取单个文件的缓存条目。"""
        with self.lock:
            return self.cache_data.get(_normalize_source_path(file_path))

    def update_entry(self, file_path, mtime, generated_outputs, settings_key=None):
        """
        更新或添加一个文件的缓存条目。
        
        Args:
            file_path (str): 源文件的绝对路径。
            mtime (float): 源文件的最后修改时间。
            generated_outputs (dict): 由此文件生成的输出文件信息 {output_path: [entry1, entry2], ...}
        """
        with self.lock:
            path = _normalize_source_path(file_path)
            if path not in self.cache_data:
                self._index_source(path)
            self._remove_indexed_outputs(path)
            self._index_outputs(path, generated_outputs)
            self.cache_data[path] = {
                "parser_revision": PARSER_REVISION,
                "mtime": mtime,
                "settings_key": settings_key,
                "outputs": generated_outputs
            }

    def remove_entry(self, file_path):
        """从缓存中移除一个文件的条目。"""
        with self.lock:
            normalized_path = _normalize_source_path(file_path)
            if normalized_path in self.cache_data:
                self._remove_indexed_outputs(normalized_path)
                del self.cache_data[normalized_path]
                for parent in self._ancestors(normalized_path):
                    paths = self._by_directory[parent]
                    paths.remove(normalized_path)
                    if not paths:
                        del self._by_directory[parent]

    def get_all_cached_paths(self):
        """获取所有已缓存的文件路径列表。"""
        with self.lock:
            return list(self.cache_data.keys())

    def get_outputs_for_file(self, file_path):
        """获取一个文件生成的所有输出文件及其条目。"""
        with self.lock:
            entry = self.cache_data.get(_normalize_source_path(file_path), {})
            return entry.get("outputs", {})
