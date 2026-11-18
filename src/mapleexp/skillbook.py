"""職業與技能的資料：客戶端的 ``Skill.wz`` 與技能字串表 → 網頁「職業」分頁讀的 ``web/skillbook.json``。

客戶端裡的東西：

* ``Skill/<職業代碼>.wzjson``（WZJS，一個職業一包）：``skill/<技能 ID>/level/<等級>`` 是那一級的數值
  （``mpCon``、``damage``、``time``、``x``、``y``…），另外有 ``req``（前置技能與等級）、``masterLevel``
  （四轉技能沒吃技能書之前的上限）、``invisible``（技能視窗裡不列的）。
* 字串表 ``Skill``（JSON）：``Name``、``Desc``，以及 ``H``：每一級效果的句型，裡面的 ``#mpCon``、``#damage``
  這些代號換成那一級的數值就是遊戲裡看到的那行字。少數技能沒有句型、直接每一級寫一句（``LevelDesc``）。

句型裡大部分代號就是等級資料的欄位名；有幾個是客戶端另外算的，名字看得出算法的照著算（:data:`DERIVED`）：
``#xSubHun`` 是 x－100、``#xAddHun`` 是 x＋100、``#timeMin`` 是秒數換成分鐘…。``#M`` 是武器熟練度，
照舊版客戶端的算法 10＋5×``mastery``（精準之劍一級 15%、二十級 60%，跟遊戲裡看到的一樣）。
``#rb`` 是攻擊範圍：等級資料的 ``rb`` 是範圍矩形的右下角，取它的 x（虎咆哮一級 110%、三十級 200%）。
看不出算法的（``#a``、``#b``、``#strX``、``#level``、``#MesoExM``）不猜，網頁上那個位置印「？」。

職業代碼沒有對應的名稱表（見 :mod:`mapleexp.core.jobvocab` 的說明），名字與轉職分支寫在這裡。

輸出::

    {"groups": [{"name": "劍士", "root": 100, "lines": [[110, 111, 112], [120, 121, 122], [130, 131, 132]]}, …],
     "jobs": [{"id": 100, "name": "劍士", "stage": "一轉", "skills": [
         {"id": 1001004, "name": "魔天一擊", "desc": "…", "max": 20, "h": "消耗MP#mpCon，攻擊力#damage%",
          "lv": {"mpCon": [4, …], "damage": [165, …]}, "req": [[1000002, 3]], "master": 10, "hidden": true}, …]}, …]}

``lv`` 的每個陣列是第 1 級到最高級的值，鍵是句型裡的代號（不含 ``#``）；句型裡有、``lv`` 裡沒有的代號就是
算不出來的。用 ``LevelDesc`` 的技能沒有 ``h`` 與 ``lv``，改成 ``texts``：每一級一句。``req``、``master``、
``hidden`` 沒有就不寫。

匯出腳本另外會加上圖示：最外層多 ``cell``、``cols``（圖集每格的像素與一列幾格），有圖示的技能多一個 ``ic``（在圖集
``web/icons/skills.png`` 裡的序號，位置是 ``(ic % cols, ic // cols)`` 格）。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from . import gamedata, wzjs

STRING_TABLE = "Skill"
# 技能那包：認「有一包叫 MobSkill」（怪物技能跟職業技能放在同一包，八個字剛好對齊到 4，沒有填充）
SKILL_MARKER = b"\x08\x00\x00\x00MobSkill"
SKILL_BUNDLE_LIMIT = 64 << 20

# 職業代碼 → 名稱。冒險家的寫法照狀態列顯示的（jobvocab 的清單）；皇家騎士團與狂狼勇士每一轉都叫同一個名字
NAMES: dict[int, str] = {
    0: "初心者",
    100: "劍士", 110: "狂戰士", 111: "十字軍", 112: "英雄", 120: "見習騎士", 121: "騎士", 122: "聖騎士",
    130: "槍騎兵", 131: "龍騎士", 132: "黑騎士",
    200: "法師", 210: "巫師(火、毒)", 211: "魔導士(火、毒)", 212: "大魔導士(火、毒)",
    220: "巫師(冰、雷)", 221: "魔導士(冰、雷)", 222: "大魔導士(冰、雷)", 230: "僧侶", 231: "祭司", 232: "主教",
    300: "弓箭手", 310: "獵人", 311: "遊俠", 312: "箭神", 320: "弩弓手", 321: "狙擊手", 322: "神射手",
    400: "盜賊", 410: "刺客", 411: "暗殺者", 412: "夜使者", 420: "俠盜", 421: "神偷", 422: "暗影神偷",
    500: "海盜", 510: "打手", 511: "格鬥家", 512: "拳霸", 520: "槍手", 521: "神槍手", 522: "槍神",
    1000: "貴族",
    1100: "聖魂劍士", 1110: "聖魂劍士", 1111: "聖魂劍士", 1200: "烈焰巫師", 1210: "烈焰巫師", 1211: "烈焰巫師",
    1300: "破風使者", 1310: "破風使者", 1311: "破風使者", 1400: "暗夜行者", 1410: "暗夜行者", 1411: "暗夜行者",
    1500: "閃雷悍將", 1510: "閃雷悍將", 1511: "閃雷悍將",
    2000: "傳說", 2100: "狂狼勇士", 2110: "狂狼勇士", 2111: "狂狼勇士", 2112: "狂狼勇士",
}
# 轉職分支：(分頁名稱, 起點的職業, 從起點分出去的每一條路)
GROUPS: tuple[tuple[str, int, tuple[tuple[int, ...], ...]], ...] = (
    ("初心者", 0, ()),
    ("劍士", 100, ((110, 111, 112), (120, 121, 122), (130, 131, 132))),
    ("法師", 200, ((210, 211, 212), (220, 221, 222), (230, 231, 232))),
    ("弓箭手", 300, ((310, 311, 312), (320, 321, 322))),
    ("盜賊", 400, ((410, 411, 412), (420, 421, 422))),
    ("海盜", 500, ((510, 511, 512), (520, 521, 522))),
    ("皇家騎士團", 1000, ((1100, 1110, 1111), (1200, 1210, 1211), (1300, 1310, 1311), (1400, 1410, 1411), (1500, 1510, 1511))),
    ("狂狼勇士", 2000, ((2100, 2110, 2111, 2112),)),
)
STARTERS = {0, 1000, 2000}        # 還沒轉職的：初心者、貴族、傳說

PLACEHOLDER = re.compile(r"#([A-Za-z]+)")
# 客戶端另外算的代號：名字 → (要用到的等級欄位, 算法)
DERIVED: dict[str, tuple[str, Callable[[float], float]]] = {
    "M": ("mastery", lambda v: 10 + 5 * v),
    "xSubHun": ("x", lambda v: v - 100),
    "xAddHun": ("x", lambda v: v + 100),
    "damageSubHun": ("damage", lambda v: v - 100),
    "absX": ("x", abs),
    "absPdd": ("pdd", abs),
    "timeMin": ("time", lambda v: v / 60),
    "MagnetRange": ("range", lambda v: v),
    "rb": ("rb", lambda v: v),
}


def stage(job: int) -> str:
    """職業代碼 → 第幾轉。代碼的後兩位：00 是一轉，X0 是二轉，X1 三轉，X2 四轉；還沒轉職的是空字串。"""
    if job in STARTERS:
        return ""
    tens, ones = job // 10 % 10, job % 10
    if tens == 0:
        return "一轉"
    return ("二轉", "三轉", "四轉")[ones] if ones < 3 else ""


def clean_template(text: Any) -> str:
    """效果句型：換行統一成 LF、頭尾的空白去掉，``#代號`` 留著給網頁換成數值。"""
    text = str(text or "")
    for raw in ("\\r\\n", "\\r", "\\n", "\r\n", "\r"):
        text = text.replace(raw, "\n")
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def clean_desc(text: Any) -> str:
    """技能說明：``#c…#`` 的強調色拆掉只留字，換行統一（跟道具說明同一套）。"""
    from .itembook import clean_text

    # 「#c劍技專精3等級以上\#」這種寫法拆掉顏色標記後會剩一個反斜線
    return clean_text(text).replace("\\", "").strip()


def _number(value: Any) -> int | float | None:
    value = getattr(value, "x", value)        # 座標（rb）取 x
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        return int(value) if value == int(value) else round(value, 1)
    return value


def level_values(levels: list[dict], name: str) -> list | None:
    """句型裡的一個代號在每一級的值；這個代號不認得（等級資料沒有、也不在 :data:`DERIVED` 裡）回 None。

    某幾級缺值的那一格是 None，網頁印「？」。
    """
    field, rule = DERIVED.get(name, (name, None))
    if all(_number(lv.get(field)) is None for lv in levels):
        return None
    out = []
    for lv in levels:
        value = _number(lv.get(field))
        out.append(None if value is None else _number(rule(value)) if rule else value)
    return out


def build_skill(skill_id: int, node: dict, text: dict) -> dict[str, Any] | None:
    """一個技能。沒有名字、或沒有任何一級資料的不收。"""
    name = str(text.get("Name") or "").strip()
    raw = node.get("level")
    if not name or not isinstance(raw, dict):
        return None
    keys = sorted((k for k in raw if str(k).isdigit() and isinstance(raw[k], dict)), key=int)
    if not keys:
        return None
    levels = [raw[k] for k in keys]
    out: dict[str, Any] = {"id": skill_id, "name": name, "desc": clean_desc(text.get("Desc")), "max": len(levels)}
    per_level = text.get("LevelDesc")
    if isinstance(per_level, dict) and per_level:
        out["texts"] = [clean_template(per_level.get(k) or per_level.get(str(int(k)))) for k in keys]
    else:
        template = clean_template(text.get("H"))
        out["h"] = template
        out["lv"] = {}
        for mark in dict.fromkeys(PLACEHOLDER.findall(template)):
            values = level_values(levels, mark)
            if values is not None:
                out["lv"][mark] = values
    req = node.get("req")
    if isinstance(req, dict):
        pairs = [[int(k), int(v)] for k, v in req.items() if str(k).isdigit() and isinstance(v, int) and not isinstance(v, bool)]
        if pairs:
            out["req"] = pairs
    master = node.get("masterLevel")
    if isinstance(master, int) and not isinstance(master, bool) and 0 < master < len(levels):
        out["master"] = master
    # 技能視窗不列的，還有名字是「[TW]」開頭、沒翻譯的佔位字
    if node.get("invisible") or name.startswith("[TW]"):
        out["hidden"] = True
    return out


def build(assets: dict[str, dict], strings: dict) -> dict[str, Any]:
    """*assets*：職業代碼（``"100"``、``"000"``）→ 那一包的節點樹；*strings*：技能字串表。"""
    by_job = {int(name): tree for name, tree in assets.items() if str(name).isdigit()}
    jobs = []
    for job, name in NAMES.items():
        tree = by_job.get(job)
        skills = tree.get("skill") if isinstance(tree, dict) else None
        if not isinstance(skills, dict):
            continue
        built = []
        for raw_id, node in skills.items():
            if not str(raw_id).isdigit() or not isinstance(node, dict):
                continue
            skill_id = int(raw_id)
            text = strings.get(str(skill_id))
            skill = build_skill(skill_id, node, text) if isinstance(text, dict) else None
            if skill:
                built.append(skill)
        if built:
            built.sort(key=lambda s: s["id"])
            jobs.append({"id": job, "name": name, "stage": stage(job), "skills": built})
    have = {j["id"] for j in jobs}
    groups = []
    for label, root, lines in GROUPS:
        kept = [[job for job in line if job in have] for line in lines]
        kept = [line for line in kept if line]
        if root in have or kept:
            groups.append({"name": label, "root": root if root in have else None, "lines": kept})
    return {"groups": groups, "jobs": jobs}


def unknown_placeholders(data: dict[str, Any]) -> dict[str, list[str]]:
    """句型裡有、但算不出值的代號 → 用到它的技能名稱。匯出時印出來，方便看要不要補算法。"""
    out: dict[str, list[str]] = {}
    for job in data["jobs"]:
        for skill in job["skills"]:
            for mark in PLACEHOLDER.findall(skill.get("h", "")):
                if mark not in skill.get("lv", {}):
                    out.setdefault(mark, []).append(skill["name"])
    return out


def load(directory: str | os.PathLike[str]) -> dict[str, Any] | None:
    """從 bundle 目錄讀技能資料與字串表並合成；缺了哪個就回傳 None。"""
    hit = gamedata.read_table(directory, STRING_TABLE)
    strings = json.loads(hit[1].decode("utf-8-sig")) if hit else None
    if not isinstance(strings, dict):
        return None
    bundle = gamedata.find_bundle_containing(directory, SKILL_MARKER, limit=SKILL_BUNDLE_LIMIT)
    if not bundle:
        return None
    assets = {name: tree for name, tree in wzjs.iter_assets(bundle[1]) if name.isdigit() and len(name) in (3, 4)}
    return build(assets, strings)


def dump(data: dict[str, Any], path: str | os.PathLike[str]) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
