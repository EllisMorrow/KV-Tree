# app_logic/config_manager.py
# 用于管理应用的配置加载、保存和默认值

import json
import os

from src.utils.file_utils import atomic_write_json
from src.logic.path_filter import DEFAULT_EXCLUDED_PATHS

DEFAULT_RULES = r"""
[排除规则]
; 每行一个正则表达式，匹配到的整行内容都将被忽略
排除行_1 = ^\s*id::.*
排除行_2 = ^\s*collapsed::.*

[替换规则]
; 将匹配到的内容替换为空
替换内容_1 = \(\(.*?\)\)
""".strip()

class ConfigManager:
    def __init__(self, config_file="kv_tree_config.json"):
        self.config_file = config_file
        self.config = {
            "source_files": {},
            "excluded_paths": list(DEFAULT_EXCLUDED_PATHS),
            "output_path": os.getcwd(),
            "rules": DEFAULT_RULES,
            "advanced_options": {
                "theme": "light",
                "logseq_scan_keys": False,
                "logseq_scan_values": False,
                "run_on_startup": False,
                "minimize_to_tray": True,
                "auto_generate": True
            },
            "output_selection": {},
            "blacklist": [],
            "logseq_exclude_keys": [],
            "_logseq_seen_defaults": [],
            "window_geometry": ""
        }

    def load_config(self):
        try:
            with open(self.config_file, encoding="utf-8") as file:
                loaded = json.load(file)
        except (FileNotFoundError, ValueError):
            return self.config
        if not isinstance(loaded, dict):
            return self.config

        sources = loaded.get("source_files")
        if isinstance(sources, dict):
            for path, data in sources.items():
                if not path or "\x00" in path or not isinstance(data, dict):
                    continue
                kind = data.get("type", "file")
                if kind not in ("file", "folder"):
                    continue
                source = {**data, "type": kind}
                if kind == "folder":
                    files = data.get("files")
                    source["files"] = {
                        name: mtime for name, mtime in files.items() if name and "\x00" not in name
                    } if isinstance(files, dict) else {}
                self.config["source_files"][path] = source

        for key in ("output_path", "window_geometry"):
            value = loaded.get(key)
            if isinstance(value, str) and "\x00" not in value:
                self.config[key] = value
        for key in ("excluded_paths", "blacklist", "logseq_exclude_keys", "_logseq_seen_defaults"):
            value = loaded.get(key)
            if isinstance(value, list):
                self.config[key] = [entry for entry in value if isinstance(entry, str) and "\x00" not in entry]

        options = loaded.get("advanced_options")
        if isinstance(options, dict):
            self.config["advanced_options"].update(options)
        selection = loaded.get("output_selection")
        if isinstance(selection, dict):
            self.config["output_selection"] = {
                name: selected for name, selected in selection.items() if isinstance(selected, bool)
            }

        rules = loaded.get("rules")
        if isinstance(rules, str) and rules.strip():
            self.config["rules"] = rules
        elif isinstance(rules, list) and rules:
            self.config["rules"] = self._valid_rules(rules)
        elif isinstance(rules, dict):
            self.config["rules"] = {key: self._valid_rules(rules.get(key))
                                    for key in ("line_rules", "content_rules")}
        return self.config

    @staticmethod
    def _valid_rules(rows):
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)
                and isinstance(row.get("match"), str)
                and isinstance(row.get("replace", ""), str)]

    def save_config(self, app_data):
        """ 使用从主应用传入的最新数据来保存配置 """
        config_to_save = {
            "source_files": app_data.get("source_files", {}),
            "excluded_paths": app_data.get('excluded_paths', list(DEFAULT_EXCLUDED_PATHS)),
            "output_path": app_data.get("output_path", os.getcwd()),
            "rules": app_data.get("rules", DEFAULT_RULES),
            "advanced_options": app_data.get("advanced_options", {}),
            "output_selection": app_data.get("output_selection", {}),
            "blacklist": app_data.get("blacklist", []),
            "logseq_exclude_keys": app_data.get("logseq_exclude_keys", []),
            "_logseq_seen_defaults": app_data.get("_logseq_seen_defaults", []),
            "window_geometry": app_data.get("window_geometry", "")
        }
        atomic_write_json(self.config_file, config_to_save, ensure_ascii=False, indent=4)
        self.config = config_to_save
