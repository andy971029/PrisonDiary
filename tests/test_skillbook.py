"""職業與技能的合成測試：句型裡的代號換成每一級的值、客戶端另外算的代號、前置技能、分支、不收的技能。"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

import helpers  # noqa: F401

from mapleexp import skillbook

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_skillbook.py"
spec = importlib.util.spec_from_file_location("export_skillbook", SCRIPT)
export_skillbook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_skillbook)


@dataclass
class Point:
    """客戶端的座標值（測試用的替身，真的是 wzjs.Vector）。"""

    x: float
    y: float


def levels(count: int, **fields) -> dict:
    """``count`` 級的等級資料；每個欄位給 (第一級的值, 每級加多少)。"""
    return {str(n): {"hs": f"h{n}", **{k: first + step * (n - 1) for k, (first, step) in fields.items()}} for n in range(1, count + 1)}


ASSETS = {
    "000": {"skill": {
        "0001000": {"level": levels(3, mpCon=(5, 2), fixdamage=(10, 15))},
        "0001004": {"level": levels(1), "invisible": 1},                     # 技能視窗不列的
        "0001999": {"level": levels(2)},                                     # 字串表沒有名字
    }},
    "100": {"skill": {
        "1001004": {"level": levels(4, mpCon=(4, 1), damage=(165, 5)), "req": {"1000002": 3}},
        "1000002": {"level": levels(2, x=(105, 5))},
    }},
    "110": {"skill": {
        "1100000": {"level": levels(4, mastery=(1, 1), x=(1, 1))},
        "1101006": {"level": levels(2, mpCon=(12, 1), pad=(3, 1), pdd=(-3, -1), time=(60, 60))},
        "1101007": {"level": {"1": {"rb": Point(110.0, 83.0), "lt": Point(-110.0, -82.0)}, "2": {"rb": Point(120.0, 90.0)}}},
    }},
    "112": {"skill": {
        "1121008": {"level": levels(30, damage=(100, 5)), "masterLevel": 10},
        "1120004": {"level": levels(2)},
        "1120009": {"level": levels(2, x=(1, 1))},
    }},
    "120": {"skill": {}},                                                    # 一個技能都沒有的職業
    "900": {"skill": {"9001000": {"level": levels(1)}}},                     # 管理員，不在職業清單裡
    "MobSkill": {"skill": {}},
}
STRINGS = {
    "1000": {"Name": "嫩寶丟擲術", "Desc": "[最高等級：3]\\n投擲蝸牛殼。", "H": "MP #mpCon後，殺傷力#fixdamage"},
    "1004": {"Name": "怪物騎乘", "Desc": "騎。"},
    "1001004": {"Name": "魔天一擊", "Desc": "[最高等級：20]\\n給敵人一擊。\\n所需技能：#c體力增加3等級以上\\\\#", "H": "消耗MP#mpCon，攻擊力#damage%"},
    "1000002": {"Name": "盾防精通", "Desc": "", "H": "盾牌的物理防禦力增加#xSubHun％，原本是#x"},
    "1100000": {"Name": "精準之劍", "Desc": "", "H": "熟練度+#M%，命中率+#x"},
    "1101006": {"Name": "激勵", "Desc": "", "H": "消耗MP#mpCon, 持續#timeMin分鐘，物理防禦力#absPdd，神祕的#strX"},
    "1101007": {"Name": "虎咆哮", "Desc": "", "H": "攻擊範圍 #rb%"},
    "1121008": {"Name": "無雙劍舞", "Desc": "", "H": "殺傷力#damage%"},
    "1120004": {"Name": "武神防禦", "Desc": "", "LevelDesc": {"1": "降低傷害0.5% ", "2": "降低傷害1% "}},
    "1120009": {"Name": "[TW]풀 스윙", "Desc": "", "H": "#x"},
    "9001000": {"Name": "加速", "Desc": "", "H": ""},
}


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.data = skillbook.build(ASSETS, STRINGS)
        self.jobs = {job["id"]: job for job in self.data["jobs"]}
        self.skills = {skill["id"]: skill for job in self.data["jobs"] for skill in job["skills"]}

    def test_only_listed_jobs_that_have_named_skills(self):
        self.assertEqual([(j["id"], j["name"], j["stage"]) for j in self.data["jobs"]],
                         [(0, "初心者", ""), (100, "劍士", "一轉"), (110, "狂戰士", "二轉"), (112, "英雄", "四轉")])
        # 字串表沒有名字的技能不收；技能照編號排
        self.assertEqual([s["id"] for s in self.jobs[0]["skills"]], [1000, 1004])
        self.assertEqual([s["id"] for s in self.jobs[100]["skills"]], [1000002, 1001004])

    def test_groups_keep_only_the_jobs_present(self):
        self.assertEqual(self.data["groups"], [
            {"name": "初心者", "root": 0, "lines": []},
            {"name": "劍士", "root": 100, "lines": [[110, 112]]},
        ])

    def test_template_placeholders_become_per_level_values(self):
        skill = self.skills[1001004]
        self.assertEqual((skill["name"], skill["max"], skill["h"]), ("魔天一擊", 4, "消耗MP#mpCon，攻擊力#damage%"))
        self.assertEqual(skill["lv"], {"mpCon": [4, 5, 6, 7], "damage": [165, 170, 175, 180]})
        self.assertEqual(skill["req"], [[1000002, 3]])
        # 說明：換行攤開、顏色標記與它留下的反斜線拆掉
        self.assertEqual(skill["desc"], "[最高等級：20]\n給敵人一擊。\n所需技能：體力增加3等級以上")

    def test_derived_placeholders(self):
        self.assertEqual(self.skills[1000002]["lv"], {"xSubHun": [5, 10], "x": [105, 110]})
        self.assertEqual(self.skills[1100000]["lv"], {"M": [15, 20, 25, 30], "x": [1, 2, 3, 4]})
        # 秒換成分鐘、取絕對值；看不出算法的代號不出現在 lv 裡，網頁印「？」
        self.assertEqual(self.skills[1101006]["lv"], {"mpCon": [12, 13], "timeMin": [1, 2], "absPdd": [3, 4]})
        self.assertEqual(self.skills[1101007]["lv"], {"rb": [110, 120]})
        self.assertEqual(skillbook.unknown_placeholders(self.data), {"strX": ["激勵"]})

    def test_master_level_hidden_and_per_level_texts(self):
        self.assertEqual(self.skills[1121008]["master"], 10)
        self.assertNotIn("master", self.skills[1001004])
        self.assertTrue(self.skills[1004]["hidden"])
        self.assertTrue(self.skills[1120009]["hidden"])                      # 名字沒翻譯的佔位字
        self.assertNotIn("hidden", self.skills[1000])
        # 沒有句型、每一級各寫一句的
        skill = self.skills[1120004]
        self.assertEqual(skill["texts"], ["降低傷害0.5%", "降低傷害1%"])
        self.assertNotIn("h", skill)
        self.assertNotIn("lv", skill)

    def test_stage_from_job_code(self):
        self.assertEqual([skillbook.stage(j) for j in (0, 100, 110, 111, 112, 232, 1000, 1100, 1110, 1111, 2000, 2100, 2112)],
                         ["", "一轉", "二轉", "三轉", "四轉", "四轉", "", "一轉", "二轉", "三轉", "", "一轉", "四轉"])

    def test_every_listed_job_has_a_name_and_a_place(self):
        placed = {job for _label, root, lines in skillbook.GROUPS for job in (root, *[j for line in lines for j in line])}
        self.assertEqual(placed, set(skillbook.NAMES))

    def test_missing_values_stay_none(self):
        node = {"level": {"1": {"x": 1}, "2": {}, "3": {"x": 3.5}}}
        skill = skillbook.build_skill(1, node, {"Name": "測試", "H": "#x"})
        self.assertEqual(skill["lv"], {"x": [1, None, 3.5]})
        self.assertIsNone(skillbook.build_skill(1, {"level": {}}, {"Name": "沒有等級"}))
        self.assertIsNone(skillbook.build_skill(1, node, {"Name": "  "}))


class CarryIconsTest(unittest.TestCase):
    """沒有 UnityPy 的環境重匯文字時，圖示的序號要照技能 ID 從上一次的結果抄過來。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.out, self.icons = Path(folder.name) / "skillbook.json", Path(folder.name) / "skills.png"
        self.keep = (export_skillbook.OUT, export_skillbook.ICONS)
        export_skillbook.OUT, export_skillbook.ICONS = self.out, self.icons
        self.addCleanup(lambda: setattr(export_skillbook, "OUT", self.keep[0]) or setattr(export_skillbook, "ICONS", self.keep[1]))

    def fresh(self):
        return {"jobs": [{"id": 100, "skills": [{"id": 1}, {"id": 2}, {"id": 9}]}]}

    def test_indexes_follow_the_skill_id(self):
        self.out.write_text(json.dumps({"jobs": [{"id": 100, "skills": [{"id": 2, "ic": 7}, {"id": 1, "ic": 0}, {"id": 5, "ic": 3}]}]}), encoding="utf-8")
        self.icons.write_bytes(b"png")
        data = self.fresh()
        self.assertEqual(export_skillbook.carry_icons(data), 2)
        # 新加的技能（9）沒有圖示，網頁會用名稱的第一個字
        self.assertEqual(data["jobs"][0]["skills"], [{"id": 1, "ic": 0}, {"id": 2, "ic": 7}, {"id": 9}])

    def test_nothing_to_carry_without_the_atlas_or_the_old_file(self):
        data = self.fresh()
        self.assertEqual(export_skillbook.carry_icons(data), 0)
        self.out.write_text(json.dumps({"jobs": [{"id": 100, "skills": [{"id": 1, "ic": 0}]}]}), encoding="utf-8")
        self.assertEqual(export_skillbook.carry_icons(data), 0)          # 圖集不在：序號指不到東西，不抄
        self.assertEqual(data, self.fresh())

    def test_the_shipped_data_points_inside_the_shipped_atlas(self):
        data = json.loads(self.keep[0].read_text(encoding="utf-8"))
        indexes = [skill["ic"] for skill in export_skillbook.skills_of(data) if "ic" in skill]
        self.assertTrue(self.keep[1].is_file())
        self.assertEqual((data["cell"], data["cols"]), (export_skillbook.CELL, export_skillbook.COLS))
        self.assertEqual(len(indexes), len(set(indexes)))
        self.assertEqual(sorted(indexes), list(range(len(indexes))))


if __name__ == "__main__":
    unittest.main()
