"""怪物圖鑑合成測試：各來源的鍵不一樣（補零、不補零）、缺資料的怪要丟掉、沒名字的物品要拿掉。"""

from __future__ import annotations

import unittest

import helpers  # noqa: F401

from mapleexp import monsterbook

STATS = {
    "0100100": {"info": {"level": 1, "maxHP": 8, "maxMP": 0, "exp": 3, "PADamage": 12, "PDDamage": 0,
                         "MADamage": 0, "MDDamage": 0, "acc": 20, "eva": 0, "fs": 10.0}, "stand": {}},
    "8800002": {"info": {"level": 140, "maxHP": 110000000, "maxMP": 40000, "exp": 7000000, "boss": 1,
                         "undead": 0, "PADamage": 1080, "PDDamage": 1200, "MADamage": 570, "MDDamage": 1500,
                         "acc": 250, "eva": 16}},
    "9999999": {"info": {"maxHP": 1}},                 # 沒等級：測試怪
    "0100101": {"info": {"level": 2, "maxHP": 15}},     # 沒名字
    "9300217": {"info": {"level": 2, "maxHP": 15, "link": "0100101"}},   # 活動用的藍寶分身，借藍寶的圖
    "abc": {"info": {"level": 1}},
}
NAMES = {"100100": {"name": "嫩寶"}, "8800002": {"name": "殘暴炎魔"}, "9999999": {"name": "測試"},
         "9300217": {"name": "藍寶"}}
BOOK = {
    "100100": {"episode": "1", "map": {"0": 40000, "1": 999999999}, "reward": {"0": 4000019, "1": 2040024, "2": 2000000}},
}
BOOK_TEXT = {
    "100100": "等級：Lv.1\\n型態：動物.\\n\\n在楓之島隨處可見。\\n走一步就一隻。",
    "8800002": "型態 : 惡魔\n特點 : 很兇。\n\n黑暗與火焰。",
}
ITEMS = {"4000019": {"name": "嫩寶殼"}, "2000000": {"name": "紅色藥水"}}
MAPS = {"40000": {"streetName": "楓之島", "mapName": "嫩寶狩獵場Ⅰ"}}


class Build(unittest.TestCase):
    def setUp(self):
        self.out = monsterbook.build(STATS, NAMES, BOOK, BOOK_TEXT, ITEMS, MAPS)
        self.mobs = {m["id"]: m for m in self.out["mobs"]}

    def test_keeps_only_named_leveled_mobs(self):
        self.assertEqual([m["id"] for m in self.out["mobs"]], [100100, 9300217, 8800002])   # 依等級排

    def test_stats(self):
        snail = self.mobs[100100]
        self.assertEqual((snail["level"], snail["hp"], snail["mp"], snail["exp"]), (1, 8, 0, 3))
        self.assertEqual((snail["pad"], snail["pdd"], snail["mad"], snail["mdd"], snail["acc"], snail["eva"]), (12, 0, 0, 0, 20, 0))
        self.assertFalse(snail["boss"])
        boss = self.mobs[8800002]
        self.assertTrue(boss["boss"])
        self.assertEqual(boss["hp"], 110000000)

    def test_drops_skip_unnamed_items(self):
        self.assertEqual(self.mobs[100100]["drops"], [4000019, 2000000])
        self.assertEqual(self.out["items"], {"4000019": "嫩寶殼", "2000000": "紅色藥水"})
        self.assertEqual(self.mobs[8800002]["drops"], [])

    def test_maps_skip_unknown(self):
        self.assertEqual(self.mobs[100100]["maps"], [40000])
        self.assertEqual(self.out["maps"], {"40000": ["楓之島", "嫩寶狩獵場Ⅰ"]})

    def test_book_text(self):
        self.assertEqual(self.mobs[100100]["type"], "動物")
        self.assertEqual(self.mobs[100100]["desc"], "在楓之島隨處可見。\n走一步就一隻。")
        self.assertEqual(self.mobs[8800002]["type"], "惡魔")
        self.assertEqual(self.mobs[8800002]["desc"], "特點 : 很兇。\n\n黑暗與火焰。")

    def test_link_is_kept_as_int(self):
        self.assertEqual(self.mobs[9300217]["link"], 100101)
        self.assertNotIn("link", self.mobs[100100])

    def test_missing_stat_is_null(self):
        out = monsterbook.build({"0000001": {"info": {"level": 5}}}, {"1": {"name": "x"}}, {}, {}, {}, {})
        self.assertEqual(out["mobs"][0]["hp"], None)
        self.assertEqual(out["mobs"][0]["drops"], [])


class BookText(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(monsterbook.parse_book_text("等級：Lv.3\\n型態：鳥\\n\\n咕")[0], "鳥類")
        self.assertEqual(monsterbook.parse_book_text("型態：動物型\\n\\n汪")[0], "動物")
        self.assertEqual(monsterbook.parse_book_text(""), ("", ""))


if __name__ == "__main__":
    unittest.main()
