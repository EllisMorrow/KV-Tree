# app_logic/cache_manager.py
# 负责管理解析缓存 (parsing_cache.json)

import json
import os
import threading

from src.utils.file_utils import atomic_write_json

PARSER_REVISION = 2


def _normalize_source_path(file_path):
    """Use one canonical key for cache access across watcher and startup paths."""
    return os.path.normcase(os.path.realpath(os.path.abspath(file_path)))


class CacheManager:
    def __init__(self, cache_file_path):
        self.cache_file = cache_file_path
        self.cache_data = {}
        self._by_output_name = {}
        self.lock = threading.Lock() # 线程锁，确保多线程写入安全
        self.load_cache()

    def load_cache(self):
        """从文件加载缓存到内存中。"""
        with self.lock:
            if os.path.exists(self.cache_file):
                try:
                    with open(self.cache_file, 'r', encoding='utf-8') as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        self.cache_data = {
                            _normalize_source_path(path): entry
                            for path, entry in loaded.items() if isinstance(entry, dict)
                        }
                    else:
                        self.cache_data = {}
                except (json.JSONDecodeError, IOError):
                    # 如果文件损坏或无法读取，则视为空缓存
                    self.cache_data = {}
            else:
                self.cache_data = {}

            self._by_output_name.clear()
            for path, entry in self.cache_data.items():
                self._index_outputs(path, entry.get("outputs", {}))

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

    def get_output_sources(self, name):
        with self.lock:
            return {source for contributors in self._by_output_name.get(name, {}).values()
                    for source in contributors}

    def get_output_contents(self, output):
        with self.lock:
            paths = self._by_output_name.get(os.path.basename(output), {})
            return tuple(paths.get(output, {}).values())

    def save_cache(self):
        """将内存中的缓存数据保存到文件。"""
        with self.lock:
            try:
                atomic_write_json(self.cache_file, self.cache_data, ensure_ascii=False, indent=4)
            except IOError as e:
                print(f"Error saving cache file: {e}")

    def get_entry(self, file_path):
        """获取单个文件的缓存条目。"""
        with self.lock:
            return self.cache_data.get(_normalize_source_path(file_path))

    def update_entry(self, file_path, mtime, generated_outputs):
        """
        更新或添加一个文件的缓存条目。
        
        Args:
            file_path (str): 源文件的绝对路径。
            mtime (float): 源文件的最后修改时间。
            generated_outputs (dict): 由此文件生成的输出文件信息 {output_path: [entry1, entry2], ...}
        """
        with self.lock:
            path = _normalize_source_path(file_path)
            self._remove_indexed_outputs(path)
            self._index_outputs(path, generated_outputs)
            self.cache_data[path] = {
                "parser_revision": PARSER_REVISION,
                "mtime": mtime,
                "outputs": generated_outputs
            }

    def remove_entry(self, file_path):
        """从缓存中移除一个文件的条目。"""
        with self.lock:
            normalized_path = _normalize_source_path(file_path)
            if normalized_path in self.cache_data:
                self._remove_indexed_outputs(normalized_path)
                del self.cache_data[normalized_path]

    def get_all_cached_paths(self):
        """获取所有已缓存的文件路径列表。"""
        with self.lock:
            return list(self.cache_data.keys())

    def get_outputs_for_file(self, file_path):
        """获取一个文件生成的所有输出文件及其条目。"""
        with self.lock:
            entry = self.cache_data.get(_normalize_source_path(file_path), {})
            return entry.get("outputs", {})
