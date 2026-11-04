"""世界圖鑑：任務、NPC、地圖三份快照，網頁的「圖鑑」頁籤後三個分類讀這些。

來源都在客戶端裡：

- **任務**：``Quest.wz`` 搬過來的 WZJS 一包四張：``QuestInfo``（名稱鍵、區域、自動開始／完成的旗標）、
  ``Check``（接任務與交任務的條件：NPC、等級、職業、前置任務、要交的道具與要打的怪）、``Act``（接任務時
  給的道具、交任務時給的經驗、楓幣、人氣、道具、技能與接續任務）、``Say``（對話鍵，不匯出）。文字在
  ``QuestData`` JSON 字串表：``name``、``Info`` 的 ``0``／``1``／``2`` 是接任務前、進行中、完成後的說明。
  區域名稱在 ``QuestCategory`` 表。
- **NPC**：``Npc`` JSON 字串表（名字、職稱、對話），``Npc.wz`` 搬過來的 WZJS（``info/script`` 是伺服器腳本名，
  帶 refine／make 的就是製作 NPC），``NpcLocation`` 資產（NPC → 地圖，含還沒開放的）。
- **地圖**：``Map.wz`` 搬過來的 WZJS（只有已開放區域；``info`` 有回程地圖、城鎮旗標、BGM，``life`` 是怪與
  NPC 的生成點，``portal`` 的 ``tm`` 是通往哪張地圖）與 ``Map`` JSON 字串表（區域、名稱、說明）。

輸出格式：

``web/questbook.json``::

    {"cats": {"30": "維多利亞島", …},
     "quests": [{"id": 1001, "name": "給希娜弄來鏡子", "parent": "莎麗的鏡子", "area": 20, "order": 2,
                 "lv": [10, null], "job": 0, "start": 2100, "end": 2101, "pre": [[1000, 2]],
                 "auto": "", "interval": null, "until": null,
                 "hold": [[1042003, 1]], "need": {"items": [[4031003, 1]], "mobs": [[100100, 5]]},
                 "give": {"exp": 10, "meso": 0, "pop": 0, "items": [[1002008, 1, -1, 0, 2]], "next": 1002, "skills": []},
                 "take": [[4031001, 1]], "startItems": [[4031003, 1]],
                 "info": ["接任務前的說明", "進行中", "完成後"]}, …],
     "items": {"4031003": "鏡子", …}}

``lv`` 是最低、最高等級（沒限制是 null）；``job`` 跟裝備一樣是職業位元（0 全職業、-1 初心者）；``pre`` 的
第二個值是前置任務要到的狀態（2 完成、1 進行中）；``auto`` 是 ``start``／``accept``／``complete`` 或空字串；
``interval`` 是可重複的間隔（分鐘，0 是隨時）；``until`` 是活動任務的截止日。``give.items`` 每筆是
``[道具, 數量, prop, 職業位元, 性別]``：``prop`` 沒有是固定給、-1 是擇一、正數是隨機（權重）；性別 0 男 1 女 2 不限。
``items`` 是任務提到的道具名稱（道具圖鑑裡沒有的也有名字可顯示）。

``web/npcbook.json``::

    {"npcs": [{"id": 2100, "name": "莎麗", "func": "", "script": "", "maps": [10000], "away": 0,
               "talk": ["要晒的衣服怎麼這麼多呀～", …], "pic": [48, 70]}, …]}

``maps`` 是客戶端有資料的地圖裡站著牠的（已開放）；``away`` 是 ``NpcLocation`` 列的、客戶端沒有資料的地圖數。
``pic`` 是站姿圖寬高（``web/npcs/<id>.png``，匯出腳本裁的），沒圖就沒有這個欄位。

``web/mapbook.json``::

    {"maps": [{"id": 100000000, "street": "維多利亞", "name": "弓箭手村", "mark": "Henesys", "town": true,
               "ret": 100000000, "limit": 0, "bgm": "FloralLife", "desc": "…",
               "mobs": [[100100, 3]], "npcs": [1012000], "to": [100000001], "away": 0,
               "mini": {"w": 211, "h": 196, "npcs": [[1012000, 120, 80]], "portals": [[100000001, 30, 90]]}}, …]}

``mobs`` 每筆是 ``[怪物, 生成點數]``；``to`` 是傳送點通往的、客戶端有資料的地圖；``away`` 是通往沒資料
（還沒開放）地圖的傳送點數。``mini`` 是小地圖（``web/minimaps/<id>.png``，匯出腳本裁的）的像素尺寸與上面的
標記，座標是小地圖像素：``npcs`` 每筆 ``[NPC, x, y]``，``portals`` 每筆 ``[目標地圖, x, y]``，目標沒資料（還沒開放）
是 0；沒小地圖的沒有這個欄位。
"""

from __future__ import annotations

import json
import os
import re
import struct
from collections import Counter
from pathlib import Path
from typing import Any

from . import gamedata, wzjs
from .monsterbook import MAP_BUNDLE_LIMIT, MAP_MARKER, spawns

QUEST_TABLE = "QuestData"
CATEGORY_TABLE = "QuestCategory"
NPC_TABLE = "Npc"
MOB_TABLE = "Mob"
ITEM_TABLE = "Item"
QUEST_MARKER = struct.pack("<i", 9) + b"QuestInfo\0"
NPC_MARKER = struct.pack("<i", 7) + b"0002100\0"          # 莎麗，菇菇村第一個 NPC
NPCLOC_ASSET = "NpcLocation"
NPCLOC_MARKER = struct.pack("<i", len(NPCLOC_ASSET)) + NPCLOC_ASSET.encode() + b"\0"
NO_MAP = 999999999

# 客戶端的職業代碼（100 劍士、200 法師…）→ 網頁用的職業位元，跟裝備的一樣
JOB_BIT_BY_HUNDRED = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16}
ALL_JOBS = 31
# 任務獎勵道具的 job 是另一種位元遮罩：0x1 初心者、0x2 劍士、0x4 法師、0x8 弓箭手、0x10 盜賊、0x20 海盜
REWARD_JOB_BITS = ((0x2, 1), (0x4, 2), (0x8, 4), (0x10, 8), (0x20, 16))


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _values(node: Any) -> list:
    """WZJS 的清單是 ``{"0": …, "1": …}``，照鍵的數字順序取值。"""
    if not isinstance(node, dict):
        return []
    keys = sorted(node, key=lambda k: (not str(k).isdigit(), int(k) if str(k).isdigit() else 0, str(k)))
    return [node[k] for k in keys]


def job_bits(codes: list[int]) -> int:
    """接任務的職業清單 → 職業位元：五個職業都在（或沒限制）是 0，只有初心者是 -1。"""
    bits = 0
    beginner = False
    for code in codes:
        if code == 0:
            beginner = True
        bits |= JOB_BIT_BY_HUNDRED.get(code // 100, 0) if code < 1000 else 0
    if bits == ALL_JOBS or (not bits and not beginner):
        return 0
    return bits or -1


def reward_job(mask: int | None) -> int:
    if not mask:
        return 0
    bits = 0
    for flag, bit in REWARD_JOB_BITS:
        if mask & flag:
            bits |= bit
    if bits == ALL_JOBS or (not bits and not mask & 0x1):
        return 0
    return bits or -1


_REF = re.compile(r"#([ptmo])(\d+)#")          # #p2100# NPC、#t4031003# 道具、#m10000# 地圖、#o100100# 怪物
_PROGRESS = re.compile(r"#[ca]\d+#")           # 持有數、擊殺數的佔位，只在遊戲裡有意義
_ICON = re.compile(r"#[iv]\d+:?#")             # 道具圖示
_CODE = re.compile(r"#[A-Za-z]")               # 顏色、粗體等單字母碼


def quest_text(text: Any, names: dict[str, dict[str, str]]) -> str:
    """任務說明 → 純文字：ID 代碼換成名字、圖示與進度佔位拿掉、顏色碼拆掉、換行統一。

    *names* 是 ``{"p": {NPC ID: 名字}, "t": {道具}, "m": {地圖}, "o": {怪物}}``；查不到的保留數字。
    有進度佔位的整行丟掉（那是「嫩寶殼 3/10」這種計數列）。
    """
    text = str(text or "")
    for raw in ("\\r\\n", "\\r", "\\n"):
        text = text.replace(raw, "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for line in text.split("\n"):
        if _PROGRESS.search(line):
            continue
        line = _ICON.sub("", line)
        # 怪物代碼有時補零成七位（#o0100100#），表的鍵不補零
        line = _REF.sub(lambda m: names.get(m.group(1), {}).get(str(int(m.group(2)))) or m.group(2), line)
        line = _CODE.sub("", line).replace("#", "")
        out.append(re.sub(r"[ \t]+", " ", line).strip())
    text = "\n".join(out)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def _date(value: Any) -> str | None:
    """``2009020200`` → ``2009-02-02``。"""
    s = str(value or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 and s[:8].isdigit() else None


def _pairs(node: Any, key: str = "count", positive: bool = True) -> list[list[int]]:
    """``{"0": {"id": 4031003, "count": 1}, …}`` → ``[[4031003, 1], …]``，只留數量正（或負）的。"""
    out = []
    for entry in _values(node):
        if not isinstance(entry, dict):
            continue
        item_id, count = _int(entry.get("id")), _int(entry.get(key))
        if item_id is None or count is None or count == 0:
            continue
        if (count > 0) == positive:
            out.append([item_id, abs(count)])
    return out


def build_quests(
    info: dict[str, dict],
    check: dict[str, dict],
    act: dict[str, dict],
    texts: dict[str, dict],
    categories: dict[str, dict],
    names: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """四張任務表 + 字串表 → 輸出格式。沒有名字的任務（字串表沒收）不要。"""
    cats = {k: str(v.get("name") or "") for k, v in categories.items()
            if isinstance(v, dict) and v.get("name") and v.get("name") != "empty"}
    item_names: dict[str, str] = {}

    def note_items(pairs: list[list[int]]) -> None:
        for item_id, _ in pairs:
            name = names.get("t", {}).get(str(item_id))
            if name:
                item_names[str(item_id)] = name

    quests = []
    for raw_id, meta in info.items():
        if not raw_id.isdigit() or not isinstance(meta, dict):
            continue
        text = texts.get(raw_id) or {}
        name = str(text.get("name") or "").strip()
        if not name:
            continue
        c0 = (check.get(raw_id) or {}).get("0") or {}
        c1 = (check.get(raw_id) or {}).get("1") or {}
        a0 = (act.get(raw_id) or {}).get("0") or {}
        a1 = (act.get(raw_id) or {}).get("1") or {}
        tinfo = text.get("Info") if isinstance(text.get("Info"), dict) else {}
        entry: dict[str, Any] = {
            "id": int(raw_id), "name": name,
            "parent": str(tinfo.get("parent") or "").strip(),
            "area": _int(meta.get("area")),
            "order": _int(meta.get("order")) or 0,
            "lv": [_int(c0.get("lvmin")), _int(c0.get("lvmax"))],
            "job": job_bits([v for v in _values(c0.get("job")) if isinstance(v, int)]),
            "start": _int(c0.get("npc")), "end": _int(c1.get("npc")),
            "pre": [[p["id"], _int(p.get("state")) if _int(p.get("state")) is not None else 2]
                    for p in _values(c0.get("quest")) if isinstance(p, dict) and _int(p.get("id")) is not None],
            "auto": "start" if meta.get("autoStart") else "accept" if meta.get("autoAccept") else "complete" if meta.get("autoComplete") else "",
            "interval": _int(c0.get("interval")),
            "until": _date(c0.get("end")),
            "hold": _pairs(c0.get("item")),
            "need": {"items": _pairs(c1.get("item")), "mobs": _pairs(c1.get("mob"))},
            "take": _pairs(a1.get("item"), positive=False),
            "startItems": _pairs(a0.get("item")),
            "info": [quest_text(tinfo.get(k), names) for k in ("0", "1", "2")],
        }
        gives = []
        for item in _values(a1.get("item")):
            if not isinstance(item, dict) or _int(item.get("id")) is None or (_int(item.get("count")) or 0) <= 0:
                continue
            gives.append([item["id"], item["count"], _int(item.get("prop")), reward_job(_int(item.get("job"))),
                          _int(item.get("gender")) if _int(item.get("gender")) is not None else 2])
        entry["give"] = {
            "exp": _int(a1.get("exp")) or 0, "meso": _int(a1.get("money")) or 0, "pop": _int(a1.get("pop")) or 0,
            "items": gives, "next": _int(a1.get("nextQuest")),
            "skills": [s["id"] for s in _values(a1.get("skill")) if isinstance(s, dict) and _int(s.get("id")) is not None],
        }
        for pairs in (entry["hold"], entry["need"]["items"], entry["take"], entry["startItems"], [[g[0], g[1]] for g in gives]):
            note_items(pairs)
        quests.append(entry)
    quests.sort(key=lambda q: (q["area"] if q["area"] is not None else 99, q["parent"], q["order"], q["id"]))
    return {"cats": cats, "quests": quests, "items": item_names}


def placements(fields: dict[str, dict], shipped: set[int]) -> tuple[dict[int, set[int]], dict[int, list[int]]]:
    """地圖 WZJS → ``({NPC ID: 站著牠的地圖}, {地圖 ID: 那張圖上的 NPC（照生成清單順序、不重複）})``，只算有資料的地圖。"""
    by_npc: dict[int, set[int]] = {}
    by_map: dict[int, list[int]] = {}
    for raw_id, tree in fields.items():
        if not raw_id.isdigit() or int(raw_id) not in shipped or not isinstance(tree, dict):
            continue
        map_id = int(raw_id)
        for entry in _values(tree.get("life")):
            if not isinstance(entry, dict) or entry.get("type") != "n" or entry.get("hide"):
                continue
            npc = entry.get("id")
            if isinstance(npc, str) and npc.isdigit():
                by_npc.setdefault(int(npc), set()).add(map_id)
                if int(npc) not in by_map.setdefault(map_id, []):
                    by_map[map_id].append(int(npc))
    return by_npc, by_map


def build_npcs(
    names: dict,
    fields: dict[str, dict],
    location: dict,
    shipped: set[int],
    by_npc: dict[int, set[int]],
) -> list[dict[str, Any]]:
    """NPC 字串表 + WZJS + NpcLocation → 輸出清單。沒名字的不要。"""
    out = []
    for raw_id, rec in names.items():
        if not raw_id.isdigit() or not isinstance(rec, dict):
            continue
        name = str(rec.get("name") or "").strip()
        if not name:
            continue
        npc_id = int(raw_id)
        talk = []
        talks = rec.get("talk") if isinstance(rec.get("talk"), dict) else {}
        for key in sorted(talks):
            line = quest_text(talks[key], {})
            if line and line not in talk:
                talk.append(line)
        tree = fields.get(f"{npc_id:07d}") or {}
        info = tree.get("info") if isinstance(tree, dict) and isinstance(tree.get("info"), dict) else {}
        scripts = [s.get("script") for s in _values(info.get("script")) if isinstance(s, dict) and s.get("script")]
        listed = {v for v in _values(location.get(raw_id)) if isinstance(v, int)}
        out.append({
            "id": npc_id, "name": name, "func": str(rec.get("func") or "").strip(),
            "script": str(scripts[0]) if scripts else "",
            "maps": sorted(by_npc.get(npc_id, ())),
            "away": len([m for m in listed if m not in shipped]),
            "talk": talk,
        })
    out.sort(key=lambda n: n["id"])
    return out


def build_maps(
    fields: dict[str, dict],
    names: dict,
    shipped: set[int],
    by_map: dict[int, list[int]],
) -> list[dict[str, Any]]:
    """地圖 WZJS + 字串表 → 輸出清單。只有客戶端有資料的地圖（``shipped``），沒名字的不要。"""
    out = []
    for raw_id, tree in fields.items():
        if not raw_id.isdigit() or int(raw_id) not in shipped or not isinstance(tree, dict):
            continue
        map_id = int(raw_id)
        rec = names.get(str(map_id)) or {}
        name = str(rec.get("mapName") or "").strip()
        if not name:
            continue
        info = tree.get("info") if isinstance(tree.get("info"), dict) else {}
        mobs = Counter()
        for entry in _values(tree.get("life")):
            if isinstance(entry, dict) and entry.get("type") == "m" and isinstance(entry.get("id"), str) and entry["id"].isdigit():
                mobs[int(entry["id"])] += 1
        to: list[int] = []
        away = 0
        for portal in _values(tree.get("portal")):
            target = _int(portal.get("tm")) if isinstance(portal, dict) else None
            if target is None or target == NO_MAP or target == map_id:
                continue
            if target in shipped:
                if target not in to:
                    to.append(target)
            else:
                away += 1
        bgm = str(info.get("bgm") or "")
        mini = build_minimap(tree, map_id, shipped)
        out.append({
            "id": map_id, "street": str(rec.get("streetName") or "").strip(), "name": name,
            "mark": str(info.get("mapMark") or ""), "town": bool(info.get("town")),
            "ret": _int(info.get("returnMap")) if _int(info.get("returnMap")) not in (None, NO_MAP) else None,
            "limit": _int(info.get("fieldLimit")) or 0,
            "bgm": bgm.split("/", 1)[1] if "/" in bgm else bgm,
            "desc": quest_text(rec.get("mapDesc"), {}),
            "mobs": sorted([[mob, count] for mob, count in mobs.items()]),
            "npcs": by_map.get(map_id, []),
            "to": sorted(to), "away": away,
            **({"mini": mini} if mini else {}),
        })
    out.sort(key=lambda m: m["id"])
    return out


def build_minimap(tree: dict, map_id: int, shipped: set[int]) -> dict[str, Any] | None:
    """地圖的 ``miniMap``、``life``、``portal`` → 小地圖尺寸與標記。

    世界座標加上 ``centerX``／``centerY``、除以 ``2 ** mag`` 就是小地圖像素。NPC 用 ``cy``（踩的地面），沒有才用 ``y``。
    """
    meta = tree.get("miniMap")
    if not isinstance(meta, dict):
        return None
    width, height, center_x, center_y, mag = (_int(meta.get(k)) for k in ("width", "height", "centerX", "centerY", "mag"))
    if not width or not height or center_x is None or center_y is None or mag is None or mag < 0:
        return None
    scale = 1 << mag
    w, h = round(width / scale), round(height / scale)

    def at(x: int, y: int) -> list[int]:
        return [min(max(round((x + center_x) / scale), 0), w), min(max(round((y + center_y) / scale), 0), h)]

    npcs = []
    for entry in _values(tree.get("life")):
        if not isinstance(entry, dict) or entry.get("type") != "n" or entry.get("hide"):
            continue
        npc, x, y = entry.get("id"), _int(entry.get("x")), _int(entry.get("cy"))
        y = y if y is not None else _int(entry.get("y"))
        if isinstance(npc, str) and npc.isdigit() and x is not None and y is not None:
            npcs.append([int(npc), *at(x, y)])
    portals = []
    for portal in _values(tree.get("portal")):
        if not isinstance(portal, dict):
            continue
        target, x, y = _int(portal.get("tm")), _int(portal.get("x")), _int(portal.get("y"))
        if target is None or target in (NO_MAP, map_id) or x is None or y is None:
            continue
        portals.append([target if target in shipped else 0, *at(x, y)])
    return {"w": w, "h": h, "npcs": npcs, "portals": portals}


def _table(directory: str | os.PathLike[str], name: str) -> dict:
    hit = gamedata.read_table(directory, name)
    if not hit:
        return {}
    raw = json.loads(hit[1].decode("utf-8-sig"))
    return raw if isinstance(raw, dict) else {}


def load(directory: str | os.PathLike[str]) -> dict[str, Any] | None:
    """從 bundle 目錄讀齊三份圖鑑的來源並合成 ``{"quests": …, "npcs": …, "maps": …}``；缺任務或 NPC 資料就 None。"""
    quest_bundle = gamedata.find_bundle_containing(directory, QUEST_MARKER)
    npc_bundle = gamedata.find_bundle_containing(directory, NPC_MARKER)
    loc_bundle = gamedata.find_bundle_containing(directory, NPCLOC_MARKER)
    map_bundle = gamedata.find_bundle_containing(directory, MAP_MARKER, limit=MAP_BUNDLE_LIMIT)
    if quest_bundle is None or npc_bundle is None:
        return None
    quest = dict(wzjs.iter_assets(quest_bundle[1]))
    npc_fields = dict(wzjs.iter_assets(npc_bundle[1]))
    location = (wzjs.find_asset(loc_bundle[1], NPCLOC_ASSET) if loc_bundle else None) or {}
    map_fields = dict(wzjs.iter_assets(map_bundle[1])) if map_bundle else {}
    shipped, _ = spawns(map_fields)
    by_npc, by_map = placements(map_fields, shipped)

    npc_names = _table(directory, NPC_TABLE)
    map_names = _table(directory, gamedata.MAP_TABLE)
    names = {
        "p": {k: str(v.get("name") or "") for k, v in npc_names.items() if isinstance(v, dict)},
        "t": {k: str(v.get("name") or "") for k, v in _table(directory, ITEM_TABLE).items() if isinstance(v, dict)},
        "m": {k: str(v.get("mapName") or "") for k, v in map_names.items() if isinstance(v, dict)},
        "o": {k: str(v.get("name") or "") for k, v in _table(directory, MOB_TABLE).items() if isinstance(v, dict)},
    }
    return {
        "quests": build_quests(quest.get("QuestInfo") or {}, quest.get("Check") or {}, quest.get("Act") or {},
                               _table(directory, QUEST_TABLE), _table(directory, CATEGORY_TABLE), names),
        "npcs": {"npcs": build_npcs(npc_names, npc_fields, location, shipped, by_npc)},
        "maps": {"maps": build_maps(map_fields, map_names, shipped, by_map)},
    }


def dump(data: dict[str, Any], path: str | os.PathLike[str]) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
