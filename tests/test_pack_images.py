"""部署前把散圖併包的測試：索引指得回原本的位元組、超過上限就分包、檔名跟著內容變。"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pack_images.py"
spec = importlib.util.spec_from_file_location("pack_images", SCRIPT)
pack_images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack_images)


class Pack(unittest.TestCase):
    def test_index_points_back_to_each_file_and_splits_at_the_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            web, out = Path(tmp) / "web", Path(tmp) / "out"
            files = {"mobs/1.png": b"a" * 6, "mobs/2.png": b"bb" * 3, "mobs/3.png": b"c" * 5, "npcs/9.png": b"npc"}
            for path, data in files.items():
                (web / path).parent.mkdir(parents=True, exist_ok=True)
                (web / path).write_bytes(data)
            index = pack_images.pack(web, out, kinds=("mobs", "npcs"), limit=12)
            self.assertEqual(json.loads((out / "packs.json").read_text(encoding="utf-8")), index)
            for path, data in files.items():
                name, start, length = index[path]
                self.assertEqual((out / "packs" / name).read_bytes()[start:start + length], data)
            # 1、2 併一包剛好 12，3 放不下另開一包；種類不同不混在一起
            self.assertEqual(index["mobs/1.png"][0], index["mobs/2.png"][0])
            self.assertNotEqual(index["mobs/2.png"][0], index["mobs/3.png"][0])
            self.assertTrue(index["npcs/9.png"][0].startswith("npcs-"))

    def test_pack_name_changes_with_content(self):
        names = []
        for data in (b"one", b"two"):
            with tempfile.TemporaryDirectory() as tmp:
                web = Path(tmp) / "web"
                (web / "mobs").mkdir(parents=True)
                (web / "mobs" / "1.png").write_bytes(data)
                names.append(pack_images.pack(web, Path(tmp) / "out", kinds=("mobs",))["mobs/1.png"][0])
        self.assertNotEqual(*names)


class CopyExtra(unittest.TestCase):
    def test_listed_files_are_copied_as_they_are(self):
        with tempfile.TemporaryDirectory() as tmp:
            web, out = Path(tmp) / "web", Path(tmp) / "site"
            web.mkdir()
            (web / "skillbook.json").write_text('{"jobs": []}', encoding="utf-8")
            self.assertEqual(pack_images.copy_extra(web, out), ["skillbook.json"])
            self.assertEqual((out / "skillbook.json").read_text(encoding="utf-8"), '{"jobs": []}')

    def test_a_missing_file_stops_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            web, out = Path(tmp) / "web", Path(tmp) / "site"
            web.mkdir()
            with self.assertRaises(FileNotFoundError):
                pack_images.copy_extra(web, out)

    def test_the_real_data_file_is_in_the_repo(self):
        for name in pack_images.EXTRA:
            self.assertTrue((SCRIPT.parents[1] / "web" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
