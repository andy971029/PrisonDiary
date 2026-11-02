"""世界圖鑑合成測試：任務的條件與獎勵要拆對、說明文字的代碼要換成名字、NPC 與地圖只算客戶端有資料的。"""

from __future__ import annotations

import unittest

import helpers  # noqa: F401

from mapleexp import worldbook

NAMES = {
    "p": {"2100": "莎麗", "2101": "希娜", "12100": "麥加"},
    "t": {"4031003": "鏡子", "1002008": "紅髮帶", "1002053": "藍髮帶", "4031001": "信", "2000000": "紅色藥水"},
    "m": {"10000": "菇菇村", "50000": "東部小山"},
    "o": {"100100": "嫩寶", "100101": "藍寶"},
}
INFO = {
    "1001": {"name": "name", "area": 20, "order": 2, "parent": "parent", "0": "0", "1": "1", "2": "2"},
    "1016": {"name": "name", "area": 20, "autoStart": 1},
    "9999": {"name": "name", "area": 30},                                  # 字串表沒有：不要
    "2000": {"name": "name", "area": 30, "autoComplete": 1},
}
CHECK = {
    "1001": {"0": {"npc": 2100, "lvmin": 10, "job": {"0": 0}, "quest": {"0": {"id": 1000, "state": 2}}, "item": {"0": {"id": 1042003, "count": 1}}},
             "1": {"npc": 2101, "item": {"0": {"id": 4031003, "count": 1}}}},
    "1016": {"0": {"npc": 12100, "lvmax": 7, "interval": 1440, "end": "2009020200", "job": {"0": 100, "1": 110, "2": 111}},
             "1": {"npc": 12100, "mob": {"0": {"id": 100100, "count": 5}, "1": {"id": 100101, "count": 3}}}},
    "2000": {"0": {"job": {"0": 100, "1": 200, "2": 300, "3": 400, "4": 500}}},
}
ACT = {
    "1001": {"0": {"item": {"0": {"id": 4031003, "count": 1}}},
             "1": {"exp": 10, "item": {"0": {"id": 4031003, "count": -1}, "1": {"id": 1002008, "count": 1, "prop": -1},
                                      "2": {"id": 1002053, "count": 1, "prop": -1, "job": 0x4, "gender": 1}},
                   "nextQuest": 1002, "skill": {"0": {"id": 1003, "job": {"0": 0}}}}},
    "1016": {"1": {"exp": 100, "money": 500, "pop": 1, "item": {"0": {"id": 2000000, "count": 5, "prop": 9}}}},
}
TEXTS = {
    "1001": {"name": "給希娜弄來鏡子", "Info": {"parent": "莎麗的鏡子", "1": "找到#b#p2100##k借到鏡子。\\n\\n#i4031003# #t4031003# #c4031003#/1\\n把它拿給#b#p2101##k。",
                                              "2": "把鏡子交給了#p2101#。"}},
    "1016": {"name": "麥加的修煉", "Info": {"0": "位在#b#m50000##k的#b#p12100##k要我打#r#o100100##k。", "1": "#o100100# #r#a10161# #k"}},
    "2000": {"name": "全職業任務", "Info": {}},
}
CATS = {"20": {"name": "楓之島"}, "30": {"name": "維多利亞島"}, "0": {"name": "empty"}}


class Quests(unittest.TestCase):
    def setUp(self):
        self.out = worldbook.build_quests(INFO, CHECK, ACT, TEXTS, CATS, NAMES)
        self.q = {q["id"]: q for q in self.out["quests"]}

    def test_only_named_quests_and_categories(self):
        self.assertEqual(sorted(self.q), [1001, 1016, 2000])
        self.assertEqual(self.out["cats"], {"20": "楓之島", "30": "維多利亞島"})

    def test_conditions(self):
        q = self.q[1001]
        self.assertEqual((q["start"], q["end"]), (2100, 2101))
        self.assertEqual(q["lv"], [10, None])
        self.assertEqual(q["job"], -1)                       # 只有初心者
        self.assertEqual(q["pre"], [[1000, 2]])
        self.assertEqual(q["hold"], [[1042003, 1]])
        self.assertEqual(q["need"], {"items": [[4031003, 1]], "mobs": []})
        self.assertEqual(q["parent"], "莎麗的鏡子")
        m = self.q[1016]
        self.assertEqual(m["lv"], [None, 7])
        self.assertEqual(m["job"], 1)                        # 劍士系
        self.assertEqual(m["need"]["mobs"], [[100100, 5], [100101, 3]])
        self.assertEqual((m["interval"], m["until"], m["auto"]), (1440, "2009-02-02", "start"))
        self.assertEqual(self.q[2000]["job"], 0)             # 五個職業都在 = 全職業
        self.assertEqual(self.q[2000]["auto"], "complete")

    def test_rewards(self):
        g = self.q[1001]["give"]
        self.assertEqual(g["exp"], 10)
        self.assertEqual(g["items"], [[1002008, 1, -1, 0, 2], [1002053, 1, -1, 2, 1]])   # 擇一；第二件法師、女
        self.assertEqual(g["next"], 1002)
        self.assertEqual(g["skills"], [1003])
        self.assertEqual(self.q[1001]["take"], [[4031003, 1]])
        self.assertEqual(self.q[1001]["startItems"], [[4031003, 1]])
        self.assertEqual(self.q[1016]["give"], {"exp": 100, "meso": 500, "pop": 1, "items": [[2000000, 5, 9, 0, 2]], "next": None, "skills": []})

    def test_item_names_collected(self):
        self.assertEqual(self.out["items"], {"4031003": "鏡子", "1002008": "紅髮帶", "1002053": "藍髮帶", "2000000": "紅色藥水"})

    def test_text(self):
        self.assertEqual(self.q[1001]["info"], ["", "找到莎麗借到鏡子。\n\n把它拿給希娜。", "把鏡子交給了希娜。"])
        self.assertEqual(self.q[1016]["info"][0], "位在東部小山的麥加要我打嫩寶。")
        self.assertEqual(self.q[1016]["info"][1], "")                       # 整行是進度佔位


class Text(unittest.TestCase):
    def test_unknown_ids_keep_number_and_codes_go(self):
        self.assertEqual(worldbook.quest_text("#e注意#n：#p777#在#m10000#", NAMES), "注意：777在菇菇村")
        self.assertEqual(worldbook.quest_text("打#o0100100#", NAMES), "打嫩寶")
        self.assertEqual(worldbook.quest_text("a\r\nb\\r\\nc\\n\\n\\n\\nd", {}), "a\nb\nc\n\nd")
        self.assertEqual(worldbook.quest_text(None, {}), "")


class Jobs(unittest.TestCase):
    def test_job_bits(self):
        self.assertEqual(worldbook.job_bits([]), 0)
        self.assertEqual(worldbook.job_bits([0]), -1)
        self.assertEqual(worldbook.job_bits([0, 200, 210]), 2)
        self.assertEqual(worldbook.job_bits([100, 200, 300, 400, 500, 1000]), 0)
        self.assertEqual(worldbook.reward_job(None), 0)
        self.assertEqual(worldbook.reward_job(0x1), -1)
        self.assertEqual(worldbook.reward_job(0x2 | 0x10), 9)
        self.assertEqual(worldbook.reward_job(0x3e), 0)


FIELDS = {
    "100000000": {"info": {"town": 1, "returnMap": 100000000, "mapMark": "Henesys", "bgm": "Bgm00/FloralLife", "fieldLimit": 0},
                  "portal": {"0": {"tm": 999999999}, "1": {"tm": 100000001}, "2": {"tm": 100000001}, "3": {"tm": 100000000}, "4": {"tm": 200000000}},
                  "life": {"0": {"type": "n", "id": "1012000"}, "1": {"type": "m", "id": "0100100"}, "2": {"type": "m", "id": "0100100"},
                           "3": {"type": "n", "id": "1012000"}, "4": {"type": "n", "id": "9999999", "hide": 1}}},
    "100000001": {"info": {"returnMap": 100000000, "mapMark": "Henesys", "bgm": "Bgm00/FloralLife"}, "foothold": {}, "life": {"0": {"type": "n", "id": "1012002"}}},
    "100000002": {"info": {"returnMap": 100000000}, "foothold": {}},                  # 字串表沒名字：不要
    "9999": {"info": {}, "0": {}},                                                 # 不是地圖
}
MAP_NAMES = {"100000000": {"streetName": "維多利亞", "mapName": "弓箭手村", "mapDesc": "可以轉職成為弓箭手。"},
             "100000001": {"streetName": "維多利亞", "mapName": "弓箭手村市集"}}
NPC_NAMES = {"1012000": {"name": "弓手村計程車", "talk": {"n0": "要坐車嗎？", "n1": "要坐車嗎？", "d0": "#b便宜#k喔"}},
             "1012002": {"name": "比休斯", "func": "道具製作家"},
             "1012003": {"name": ""},
             "9999999": {"name": "隱藏"}}
NPC_FIELDS = {"1012002": {"info": {"script": {"0": {"script": "refine_henesys"}}}}}
LOCATION = {"1012000": {"0": 100000000, "1": 100000005}, "1012002": {"0": 100000001}}


class NpcsAndMaps(unittest.TestCase):
    def setUp(self):
        from mapleexp.monsterbook import spawns
        self.shipped, _ = spawns(FIELDS)
        self.by_npc, self.by_map = worldbook.placements(FIELDS, self.shipped)

    def test_placements_skip_hidden_and_dedupe(self):
        self.assertEqual(self.by_npc, {1012000: {100000000}, 1012002: {100000001}})
        self.assertEqual(self.by_map, {100000000: [1012000], 100000001: [1012002]})

    def test_npcs(self):
        npcs = {n["id"]: n for n in worldbook.build_npcs(NPC_NAMES, NPC_FIELDS, LOCATION, self.shipped, self.by_npc)}
        self.assertEqual(sorted(npcs), [1012000, 1012002, 9999999])
        taxi = npcs[1012000]
        self.assertEqual(taxi["maps"], [100000000])
        self.assertEqual(taxi["away"], 1)
        self.assertEqual(taxi["talk"], ["便宜喔", "要坐車嗎？"])
        self.assertEqual((npcs[1012002]["func"], npcs[1012002]["script"]), ("道具製作家", "refine_henesys"))
        self.assertEqual(npcs[9999999]["maps"], [])

    def test_maps(self):
        maps = {m["id"]: m for m in worldbook.build_maps(FIELDS, MAP_NAMES, self.shipped, self.by_map)}
        self.assertEqual(sorted(maps), [100000000, 100000001])
        town = maps[100000000]
        self.assertEqual((town["street"], town["name"], town["town"], town["mark"], town["bgm"]), ("維多利亞", "弓箭手村", True, "Henesys", "FloralLife"))
        self.assertEqual(town["mobs"], [[100100, 2]])
        self.assertEqual(town["npcs"], [1012000])
        self.assertEqual((town["to"], town["away"]), ([100000001], 1))     # 自己、沒目標的不算；天空之城沒資料算 away
        self.assertEqual(town["desc"], "可以轉職成為弓箭手。")
        self.assertEqual(maps[100000001]["ret"], 100000000)
        self.assertFalse(maps[100000001]["town"])


if __name__ == "__main__":
    unittest.main()
