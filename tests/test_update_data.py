"""更新圖鑑腳本的測試：數量統計、變少要警示、版號要兩處一起改。"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_data.py"
spec = importlib.util.spec_from_file_location("update_data", SCRIPT)
update_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update_data)


class Counts(unittest.TestCase):
    def test_counts_books_and_tolerates_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp)
            (web / "mapbook.json").write_text(json.dumps({"maps": [{}, {}]}), encoding="utf-8")
            (web / "mapvocab.json").write_text(json.dumps(["a", "b", "c"]), encoding="utf-8")
            (web / "worldmap.json").write_text(json.dumps({"worlds": [{"nodes": [{}, {"via": [0]}]}]}), encoding="utf-8")
            (web / "mobs").mkdir()
            (web / "mobs" / "1.png").write_bytes(b"")
            out = update_data.counts(web)
        self.assertEqual((out["地圖"], out["地圖字庫"], out["世界地圖節點"], out["補的紫色節點"], out["怪物圖"]), (2, 3, 1, 1, 1))
        self.assertEqual(out["怪物"], 0)

    def test_changes_flag_only_decreases(self):
        lines = update_data.changes({"a": 1, "b": 5, "c": 2}, {"a": 3, "b": 4, "c": 2})
        self.assertIn("+2", lines[0])
        self.assertIn("⚠", lines[1])
        self.assertIn("不變", lines[2])


class Version(unittest.TestCase):
    def test_bump_is_a_patch_bump(self):
        self.assertEqual(update_data.bump("0.18.9"), "0.18.10")

    def test_set_version_changes_both_and_keeps_line_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            py, html = Path(tmp) / "v.py", Path(tmp) / "i.html"
            py.write_bytes(b'__version__ = "1.2.3"\r\n')
            html.write_bytes(b'<span id="app-version">v1.2.3</span>\r\n')
            with mock.patch.object(update_data, "VERSION_PY", py), mock.patch.object(update_data, "INDEX", html):
                update_data.set_version("1.2.3", "1.2.4")
                with self.assertRaises(ValueError):
                    update_data.set_version("9.9.9", "9.9.10")
            self.assertEqual(py.read_bytes(), b'__version__ = "1.2.4"\r\n')
            self.assertEqual(html.read_bytes(), b'<span id="app-version">v1.2.4</span>\r\n')


if __name__ == "__main__":
    unittest.main()
