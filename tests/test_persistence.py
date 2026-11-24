import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.logic.cache_manager import CacheManager
from src.logic.config_manager import ConfigManager


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def assert_no_json_temp_files(self):
        self.assertEqual([], list(self.directory.glob(".tmp_*.json")))

    def test_config_round_trips_logseq_and_blacklist_state(self):
        config_path = self.directory / "config.json"
        manager = ConfigManager(str(config_path))
        app_data = {
            "source_files": {"notes": {"type": "folder", "files": {}}},
            "output_path": str(self.directory),
            "rules": {"line_rules": [], "content_rules": []},
            "advanced_options": {"auto_generate": False},
            "output_selection": {"terms": True},
            "blacklist": ["discarded", "temporary"],
            "logseq_exclude_keys": ["id", "custom-property"],
            "_logseq_seen_defaults": ["id", "title"],
            "window_geometry": "800x600",
        }

        manager.save_config(app_data)
        loaded = ConfigManager(str(config_path)).load_config()

        for key in ("blacklist", "logseq_exclude_keys", "_logseq_seen_defaults"):
            self.assertEqual(app_data[key], loaded[key])

    def test_invalid_config_shape_uses_defaults_and_preserves_valid_fields(self):
        config_path = self.directory / "config.json"
        config_path.write_text("[]", encoding="utf-8")
        self.assertEqual({}, ConfigManager(config_path).load_config()["source_files"])
        self.assertEqual("[]", config_path.read_text(encoding="utf-8"))

        config_path.write_text(json.dumps({
            "output_path": str(self.directory),
            "advanced_options": [],
            "source_files": {"broken": None, "notes": {"type": "folder", "files": None}},
        }), encoding="utf-8")
        config = ConfigManager(config_path).load_config()
        self.assertEqual(str(self.directory), config["output_path"])
        self.assertEqual({"notes": {"type": "folder", "files": {}}}, config["source_files"])
        self.assertTrue(config["advanced_options"]["auto_generate"])

    def test_old_config_uses_defaults_for_new_persistence_fields(self):
        config_path = self.directory / "legacy.json"
        config_path.write_text(json.dumps({"source_files": {}}), encoding="utf-8")

        loaded = ConfigManager(str(config_path)).load_config()

        self.assertEqual([], loaded["blacklist"])
        self.assertEqual([], loaded["logseq_exclude_keys"])
        self.assertEqual([], loaded["_logseq_seen_defaults"])
        self.assertTrue(loaded["advanced_options"]["minimize_to_tray"])

    def test_config_replace_failure_keeps_existing_file_and_cleans_temp_file(self):
        config_path = self.directory / "config.json"
        original = '{"preserved": true}'
        config_path.write_text(original, encoding="utf-8")

        with mock.patch("src.utils.file_utils.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                ConfigManager(str(config_path)).save_config({"blacklist": ["new"]})

        self.assertEqual(original, config_path.read_text(encoding="utf-8"))
        self.assert_no_json_temp_files()

    def test_cache_write_failure_keeps_existing_file_and_cleans_temp_file(self):
        cache_path = self.directory / "cache.json"
        original = '{"preserved": true}'
        cache_path.write_text(original, encoding="utf-8")
        cache = CacheManager(str(cache_path))
        cache.cache_data = {"new": {"mtime": 1}}

        with (
            mock.patch("src.utils.file_utils.json.dump", side_effect=OSError("write failed")),
            mock.patch("builtins.print"),
        ):
            cache.save_cache()

        self.assertEqual(original, cache_path.read_text(encoding="utf-8"))
        self.assert_no_json_temp_files()

    def test_cache_load_keeps_valid_entries_when_other_entries_are_malformed(self):
        cache_path = self.directory / "cache.json"
        first, second, valid = (self.directory / name for name in ("a.md", "b.md", "c.md"))
        output = str(self.directory / "export.md")
        cache_path.write_text(json.dumps({
            str(first): {"outputs": []},
            str(second): {"outputs": {output: 123}},
            str(valid): {"mtime": 1, "outputs": {output: "valid"}},
            "<pending_exports>": [output, 123, "not-an-export.txt", "bad\x00.md"],
        }), encoding="utf-8")

        cache = CacheManager(cache_path)

        self.assertIsNone(cache.get_entry(first))
        self.assertIsNone(cache.get_entry(second))
        self.assertEqual(("valid",), cache.get_output_contents(output))
        self.assertEqual({output}, cache.get_pending_exports())

    def test_cache_load_normalizes_source_keys(self):
        cache_path = self.directory / "cache.json"
        source_path = self.directory / "notes" / "source.md"
        path_with_parent_segment = self.directory / "notes" / ".." / "notes" / "source.md"
        cache_path.write_text(
            json.dumps({str(path_with_parent_segment): {"mtime": 1, "outputs": {}}}),
            encoding="utf-8",
        )

        cache = CacheManager(str(cache_path))

        self.assertIsNotNone(cache.get_entry(str(source_path)))
        self.assertEqual(1, len(cache.get_all_cached_paths()))


if __name__ == "__main__":
    unittest.main()
