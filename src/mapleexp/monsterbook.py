"""怪物圖鑑：把客戶端裡散在幾處的怪物資料合成網頁用的一份 JSON。

來源有四種，各在客戶端的不同地方：

- **數值**（等級、HP、EXP、命中、迴避、防禦、是否 Boss）：``Mob.wz`` 搬過來的 WZJS，
  一隻怪一包，名字是七位數 ID（``0100100``）。
- **掉落物與出現地圖**：``Etc.wz/MonsterBook.img`` 搬過來的 WZJS，只有 343 隻有圖鑑卡的怪。
  這是遊戲裡怪物卡上列的清單，沒有機率；掉落率在伺服器端，客戶端沒有。
- **實際出現的地圖**：``Map.wz`` 搬過來的 WZJS，一張地圖一包（九位數 ID），``life`` 底下是生成清單。
  這份**不是**整包搬：客戶端只放已開放區域的地圖（維多利亞、天空之城、艾納斯島…），武陵、
  童話王國這些沒開的連地圖資料都沒有。怪物數值表倒是整包，所以「哪些怪現在打得到」要靠這份反推。
- **名稱**：``Mob``、``Item``、``Map`` 三張 JSON 字串表。
- **圖鑑說明**：``MonsterBook`` JSON 字串表，開頭兩行是「等級：Lv.1」「型態：動物」。

輸出格式（``web/monsterbook.json``）::

    {"mobs": [{"id": 100100, "name": "嫩寶", "level": 1, "hp": 8, "mp": 0, "exp": 3,
               "pad": 12, "pdd": 0, "mad": 0, "mdd": 0, "acc": 20, "eva": 0,
               "boss": false, "undead": false, "type": "動物", "desc": "…",
               "drops": [4000019, …], "maps": [40000, …], "spawn": [40000, …], "pic": [37, 26]}, …],
     "items": {"4000019": "嫩寶殼", …},
     "maps": {"40000": ["楓之島", "嫩寶狩獵場Ⅰ"], …}}

``maps`` 是圖鑑卡上列的地圖（舊資料，含還沒開放的）；``spawn`` 是現在真的會出現的地圖：客戶端有
資料的地圖裡生成清單有牠的，加上圖鑑卡列的地圖裡客戶端有資料的（召喚出來的王不在生成清單上）。
``spawn`` 是空的就代表這隻怪還沒開放。``pic`` 是站姿圖的寬高（匯出腳本裁好的 ``web/mobs/<id>.png``）；沒有自己的圖、外觀借用別隻怪
（客戶端的 ``info/link``）的，第三個元素是借用對象的 ID，圖在 ``web/mobs/<那個 ID>.png``。
完全沒圖的怪沒有這個欄位。
掉落物與地圖只放 ID，名稱另外查表，一個名字只存一次。``Item`` 表裡沒有名字的物品 ID
（舊資料有、經典版沒放進來的卷軸、技能書）直接拿掉，網頁上才不會出現一堆空白。
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path
from typing import Any

from . import gamedata, wzjs

MOB_TABLE = "Mob"
ITEM_TABLE = "Item"
BOOK_TABLE = "MonsterBook"
BOOK_ASSET = "MonsterBook"
# 怪物數值那包：掃 bundle 時認「有一包叫 0100100（嫩寶）」。
MOB_MARKER = struct.pack("<i", 7) + b"0100100\0"
BOOK_MARKER = struct.pack("<i", len(BOOK_ASSET)) + BOOK_ASSET.encode() + b"\0"
# 地圖那包：認「有一包叫 100000000（弓箭手村）」。壓縮後 58 MB，比預設肯解的上限大，要另外給。
MAP_MARKER = struct.pack("<i", 9) + b"100000000\0"
MAP_BUNDLE_LIMIT = 128 << 20

# 數值欄位：客戶端的名字 -> 輸出的名字
STAT_FIELDS = (
    ("level", "level"), ("maxHP", "hp"), ("maxMP", "mp"), ("exp", "exp"),
    ("PADamage", "pad"), ("PDDamage", "pdd"), ("MADamage", "mad"), ("MDDamage", "mdd"),
    ("acc", "acc"), ("eva", "eva"),
)


def _load_json_table(blob: bytes | None) -> dict:
    if not blob:
        return {}
    raw = json.loads(blob.decode("utf-8-sig"))
    return raw if isinstance(raw, dict) else {}


def parse_book_text(text: str) -> tuple[str, str]:
    """圖鑑說明 -> ``(型態, 描述)``。

    文字裡的換行是字面上的 ``\\n`` 兩個字（JSON 裡又被跳脫過一次），也有真的換行，
    兩種都當換行。「等級」那行跟數值表重複，丟掉；「型態」或「型態 : 」那行抽出來。
    """
    lines = [line.strip() for line in text.replace("\\n", "\n").split("\n")]
    kind = ""
    desc: list[str] = []
    for line in lines:
        if line.startswith("等級"):
            continue
        if line.startswith("型態"):
            kind = line.split("：", 1)[-1].split(":", 1)[-1].strip().rstrip(".。")
            kind = KIND_ALIASES.get(kind, kind)
            continue
        desc.append(line)
    return kind, "\n".join(desc).strip()


# 圖鑑文字裡同一種型態有幾種寫法，統一成一種，網頁上篩選才對得起來。
KIND_ALIASES = {"動物型": "動物", "鳥": "鳥類"}


def _int_list(node: Any) -> list[int]:
    if not isinstance(node, dict):
        return []
    return [v for v in node.values() if isinstance(v, int)]


def spawns(fields: dict[str, dict]) -> tuple[set[int], dict[int, set[int]]]:
    """地圖 WZJS → ``(有資料的地圖 ID, {怪物 ID: 生成牠的地圖 ID})``。

    bundle 裡名字是數字的包不只地圖（物件、背景也是），有 ``info`` 又有傳送點或踏板的才算地圖。
    ``life`` 裡 ``type`` 是 ``m`` 的是怪，``n`` 是 NPC。
    """
    shipped: set[int] = set()
    by_mob: dict[int, set[int]] = {}
    for raw_id, tree in fields.items():
        if not raw_id.isdigit() or not isinstance(tree, dict) or "info" not in tree:
            continue
        if "portal" not in tree and "foothold" not in tree:
            continue
        map_id = int(raw_id)
        shipped.add(map_id)
        life = tree.get("life")
        if not isinstance(life, dict):
            continue
        for entry in life.values():
            if not isinstance(entry, dict) or entry.get("type") != "m":
                continue
            mob = entry.get("id")
            if isinstance(mob, str) and mob.isdigit():
                by_mob.setdefault(int(mob), set()).add(map_id)
    return shipped, by_mob


def build(
    stats: dict[str, dict],
    names: dict,
    book: dict[str, dict],
    book_text: dict,
    items: dict,
    maps: dict,
    shipped: set[int] | None = None,
    spawn: dict[int, set[int]] | None = None,
) -> dict[str, Any]:
    """把各來源合成輸出格式。

    *stats* 的鍵是七位數 ID（``0100100``），其他表都是不補零的（``100100``）。
    沒名字或沒等級的怪不要（都是活動或測試用的，玩家看不到）。
    *shipped* 與 *spawn* 是 :func:`spawns` 的結果，沒給就當沒有任何地圖資料（``spawn`` 全空）。
    """
    shipped = shipped or set()
    spawn = spawn or {}
    item_names: dict[str, str] = {}
    map_names: dict[str, list[str]] = {}
    mobs = []
    for raw_id, tree in stats.items():
        try:
            mob_id = int(raw_id)
        except ValueError:
            continue
        key = str(mob_id)
        name = str((names.get(key) or {}).get("name") or "").strip()
        info = tree.get("info") if isinstance(tree, dict) else None
        if not name or not isinstance(info, dict) or not isinstance(info.get("level"), int):
            continue
        entry: dict[str, Any] = {"id": mob_id, "name": name}
        for src, dst in STAT_FIELDS:
            value = info.get(src)
            entry[dst] = value if isinstance(value, int) else None
        entry["boss"] = bool(info.get("boss"))
        link = info.get("link")
        if isinstance(link, str) and link.isdigit():
            entry["link"] = int(link)            # 外觀借用哪隻怪的圖；匯出圖時解掉，不進 JSON
        entry["undead"] = bool(info.get("undead"))
        kind, desc = parse_book_text(str(book_text.get(key) or ""))
        entry["type"] = kind
        entry["desc"] = desc
        card = book.get(key) or {}
        drops = []
        for item_id in _int_list(card.get("reward")):
            item_name = str((items.get(str(item_id)) or {}).get("name") or "").strip()
            if not item_name:
                continue
            item_names[str(item_id)] = item_name
            drops.append(item_id)
        def named(map_ids: Any) -> list[int]:
            out = []
            for map_id in map_ids:
                record = maps.get(str(map_id)) or {}
                map_name = str(record.get("mapName") or "").strip()
                if not map_name:
                    continue
                map_names[str(map_id)] = [str(record.get("streetName") or "").strip(), map_name]
                out.append(map_id)
            return out

        entry["drops"] = drops
        entry["maps"] = named(_int_list(card.get("map")))
        actual = set(spawn.get(mob_id, ())) | {m for m in entry["maps"] if m in shipped}
        entry["spawn"] = named(sorted(actual))
        mobs.append(entry)
    mobs.sort(key=lambda m: (m["level"], m["id"]))
    return {"mobs": mobs, "items": item_names, "maps": map_names}


def load(directory: str | os.PathLike[str]) -> dict[str, Any] | None:
    """從 bundle 目錄讀齊所有來源並合成；缺了數值或名稱表就回傳 None。"""
    found = gamedata.find_bundle_containing(directory, MOB_MARKER)
    if found is None:
        return None
    stats = {name: tree for name, tree in wzjs.iter_assets(found[1]) if name.isdigit()}
    book_bundle = gamedata.find_bundle_containing(directory, BOOK_MARKER)
    book = wzjs.find_asset(book_bundle[1], BOOK_ASSET) if book_bundle else None

    def table(name: str) -> dict:
        hit = gamedata.read_table(directory, name)
        return _load_json_table(hit[1] if hit else None)

    names = table(MOB_TABLE)
    if not stats or not names:
        return None
    map_bundle = gamedata.find_bundle_containing(directory, MAP_MARKER, limit=MAP_BUNDLE_LIMIT)
    shipped, spawn = spawns(dict(wzjs.iter_assets(map_bundle[1]))) if map_bundle else (set(), {})
    return build(stats, names, book or {}, table(BOOK_TABLE), table(ITEM_TABLE), table(gamedata.MAP_TABLE),
                 shipped, spawn)


def dump(data: dict[str, Any], path: str | os.PathLike[str]) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
