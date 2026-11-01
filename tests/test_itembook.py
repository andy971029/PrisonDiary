"""道具與裝備圖鑑合成測試：挑類別、丟現金裝、能力加成取名、卷軸與藥水的欄位、掉落來源反查。"""

from __future__ import annotations

import unittest

import helpers  # noqa: F401

from mapleexp import itembook

EQUIPS = {
    "01302000": {"info": {"reqLevel": 0, "reqJob": 0, "incPAD": 17, "attackSpeed": 4, "price": 1, "islot": "Wp"}},
    "01002067": {"info": {"reqLevel": 5, "reqSTR": 0, "incPDD": 5, "tuc": 7, "price": 225}},
    "01452000": {"info": {"reqLevel": 30, "reqJob": 4, "reqDEX": 80, "incPAD": 40, "incMaxHP": 10, "incMHP": 20,
                          "attackSpeed": 5}},
    "01702000": {"info": {"cash": 1, "reqLevel": 0}},               # 現金武器
    "01802000": {"info": {"reqLevel": 0}},                          # 寵物裝備，不收的類別
    "01002999": {"info": {"reqLevel": 1}},                          # 沒名字
    "abc": {"info": {}},
}
ITEMS = {
    "0200": {"02000000": {"info": {"price": 25, "slotMax": 3000}, "spec": {"hp": 50, "weird": 1}},
             "02000999": {"info": {"price": 1}}},                   # 沒名字
    "0204": {"02040002": {"info": {"price": 1, "success": 10, "incPDD": 5, "incMDD": 3, "incACC": 1}}},
    "0399": {"03990000": {"info": {"price": 1}}},                   # 活動數字牌，跳過
    "0409": {"04090000": {"info": {"cash": 1}}},                    # 現金道具
    "0400": {"04000019": {"info": {"price": 2, "slotMax": 200, "lv": 3}},
             "04000998": {"info": {"price": 1}}},                   # 名字只有「..」的佔位道具
}
NAMES = {
    "1302000": {"name": "劍"}, "1002067": {"name": "綠髮帶"}, "1452000": {"name": "戰弓", "desc": "弓箭手的弓。\\n很強。"},
    "1702000": {"name": "現金劍"}, "1802000": {"name": "寵物帽"},
    "2000000": {"name": "紅色藥水", "desc": "HP恢復約50。"}, "2040002": {"name": "頭盔防禦卷軸10%"},
    "3990000": {"name": "紅色數字 1"}, "4090000": {"name": "紅心巧克力"}, "4000019": {"name": "嫩寶殼", "desc": "嫩寶的殼"},
    "4000998": {"name": ".."},
}
MOBS = [
    {"id": 100101, "level": 2, "drops": [4000019, 2000000]},
    {"id": 100100, "level": 1, "drops": [4000019, 2000000, 1002067]},
]


class Equips(unittest.TestCase):
    def setUp(self):
        self.out = {e["id"]: e for e in itembook.build_equips(EQUIPS, NAMES, itembook.drop_sources(MOBS))}

    def test_keeps_named_noncash_known_kinds(self):
        self.assertEqual(sorted(self.out), [1002067, 1302000, 1452000])

    def test_fields(self):
        sword = self.out[1302000]
        self.assertEqual((sword["kind"], sword["lv"], sword["job"], sword["spd"], sword["price"]), ("130", 0, 0, 4, 1))
        self.assertEqual(sword["inc"], {"PAD": 17})
        self.assertEqual(sword["req"], [0, 0, 0, 0])
        self.assertEqual(sword["tuc"], 0)
        bow = self.out[1452000]
        self.assertEqual(bow["req"], [0, 80, 0, 0])
        self.assertEqual(bow["inc"], {"PAD": 40, "MHP": 20})      # incMHP 優先於 incMaxHP
        self.assertEqual(bow["desc"], "弓箭手的弓。\n很強。")
        self.assertIsNone(bow["price"])

    def test_drop_sources_by_level(self):
        self.assertEqual(self.out[1002067]["from"], [100100])
        self.assertEqual(self.out[1302000]["from"], [])

    def test_sorted_by_kind_then_level(self):
        order = [e["id"] for e in itembook.build_equips(EQUIPS, NAMES, {})]
        self.assertEqual(order, [1002067, 1302000, 1452000])


class Items(unittest.TestCase):
    def setUp(self):
        self.out = {i["id"]: i for i in itembook.build_items(ITEMS, NAMES, itembook.drop_sources(MOBS))}

    def test_keeps_named_noncash_and_skips_event_numbers(self):
        self.assertEqual(sorted(self.out), [2000000, 2040002, 4000019])

    def test_potion(self):
        potion = self.out[2000000]
        self.assertEqual(potion["kind"], "0200")
        self.assertEqual(potion["spec"], {"hp": 50})               # 不認得的 spec 鍵丟掉
        self.assertEqual((potion["price"], potion["max"], potion["rate"], potion["lv"]), (25, 3000, None, 0))
        self.assertEqual(potion["from"], [100100, 100101])         # 依怪物等級排

    def test_scroll(self):
        scroll = self.out[2040002]
        self.assertEqual(scroll["rate"], 10)
        self.assertEqual(scroll["inc"], {"PDD": 5, "MDD": 3, "ACC": 1})

    def test_etc_level(self):
        self.assertEqual(self.out[4000019]["lv"], 3)
        self.assertEqual(self.out[4000019]["desc"], "嫩寶的殼")


class Drops(unittest.TestCase):
    def test_sources(self):
        self.assertEqual(itembook.drop_sources(MOBS), {4000019: [100100, 100101], 2000000: [100100, 100101], 1002067: [100100]})


if __name__ == "__main__":
    unittest.main()
