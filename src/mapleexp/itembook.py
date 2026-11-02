"""道具與裝備圖鑑：把客戶端的裝備數值、道具效果、名稱說明合成網頁用的一份 JSON。

來源：

- **裝備**（``Character.wz`` 搬過來的 WZJS）：一件一包，名字是八位數 ID（``01302000``），``info`` 底下是
  需求等級、職業、能力加成、可升級次數、攻擊速度。現金裝沒有數值，不收。
- **道具**（``Item.wz`` 搬過來的 WZJS）：一包是一個四位數前綴（``0200`` 藥水、``0204`` 卷軸、``0400``
  怪物掉落物…），底下才是各道具；``info`` 是價格、堆疊上限、卷軸的成功率與加成，``spec`` 是使用效果
  （回復、增益、持續時間）。
- **名稱與說明**：``Item`` JSON 字串表。
- **掉落來源**：怪物圖鑑卡上的掉落清單反過來查（``web/monsterbook.json`` 的 ``drops``）。只有「哪隻怪會
  掉」，沒有機率。哪些怪已開放交給網頁對照怪物圖鑑。

輸出格式（``web/itembook.json``）::

    {"cell": 36, "cols": 56,                                   圖示圖集：每格幾像素、一列幾格
     "equips": [{"id": 1302000, "name": "劍", "kind": "130", "lv": 0, "job": 0,
                 "req": [0, 0, 0, 0], "inc": {"PAD": 17}, "tuc": 7, "spd": 4, "price": 1,
                 "desc": "", "from": [100100, …], "ic": 0}, …],
     "items": [{"id": 2000000, "name": "紅色藥水", "kind": "0200", "desc": "…", "price": 25,
                "max": 3000, "spec": {"hp": 50}, "inc": {}, "rate": null, "lv": 0,
                "from": [100100, …], "ic": 0}, …]}

``kind`` 是 ID 的前綴，類別名稱在網頁那邊對；``req`` 是 STR、DEX、INT、LUK 的需求；``inc`` 的鍵是客戶端的
``incXXX`` 去掉 ``inc``；``rate`` 是卷軸成功率；``ic`` 是圖示在圖集裡的序號（沒圖示就沒有這個欄位），
位置是 ``(ic % cols, ic // cols)`` 格。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import gamedata, wzjs

ITEM_TABLE = "Item"
# 裝備那包：認「有一包叫 01302000（劍）」；道具那包：認「有一包叫 0200」。八位與四位剛好對齊到 4，沒有填充。
EQUIP_MARKER = b"\x08\x00\x00\x0001302000"
ITEM_MARKER = b"\x04\x00\x00\x000200"
EQUIP_BUNDLE_LIMIT = 512 << 20

# 收哪些裝備：ID 的第 2～4 位。現金武器（170）、寵物裝備（180）、騎寵（19x）、技能特效（160）不收。
EQUIP_KINDS = {
    "100", "101", "102", "103", "104", "105", "106", "107", "108", "109", "110", "111", "112", "113", "114",
    "130", "131", "132", "133", "137", "138", "140", "141", "142", "143", "144", "145", "146", "147", "148", "149",
}
# 收哪些道具：消耗（02）、裝飾（03）、其他（04）。現金道具（05）、特殊（09）不收；0399 是活動用的數字牌。
ITEM_PREFIXES = ("02", "03", "04")
ITEM_SKIP = {"0399", "0247"}

INC_KEYS = ("STR", "DEX", "INT", "LUK", "MHP", "MMP", "PAD", "MAD", "PDD", "MDD", "ACC", "EVA", "Speed", "Jump")
SPEC_KEYS = ("hp", "mp", "hpR", "mpR", "time", "pad", "mad", "pdd", "mdd", "acc", "eva", "speed", "jump",
             "morph", "expBuff", "moveTo", "poison", "darkness", "weakness", "curse", "seal")


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def clean_text(text: Any) -> str:
    """客戶端的說明文字 → 網頁能直接印的純文字。

    客戶端的字串用 ``#c…#`` 把一段字標成強調色（道具說明幾乎只用這一種；NPC 對話還有 ``#r`` 紅、``#b`` 藍、
    ``#e`` 粗體等，都是一個 ``#`` 收尾），網頁不照客戶端的顏色畫，所以整個拆掉只留字。換行有的是真的
    CR／LF，有的是寫成兩個字元的 ``\\r``、``\\n``，統一成 LF；三個以上的連續換行縮成一個空行。
    """
    text = str(text or "")
    for raw in ("\\r\\n", "\\r", "\\n"):          # 寫成兩個字元的換行
        text = text.replace(raw, "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("#c", "").replace("#", "")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def _name(record: dict) -> str:
    """名稱表的名字；只有標點（``..``、``-``）的是佔位用的，當作沒名字。"""
    name = str(record.get("name") or "").strip()
    return name if any(ch.isalnum() for ch in name) else ""


def _inc(info: dict) -> dict[str, int]:
    out = {}
    for key in INC_KEYS:
        value = _int(info.get("inc" + key))
        if value:
            out[key] = value
    # 寶石用 incMaxHP／incMaxMP，裝備用 incMHP／incMMP，統一成後者
    for src, dst in (("incMaxHP", "MHP"), ("incMaxMP", "MMP")):
        value = _int(info.get(src))
        if value and dst not in out:
            out[dst] = value
    return out


def build_equips(assets: dict[str, dict], names: dict, drops: dict[int, list[int]]) -> list[dict[str, Any]]:
    """裝備 WZJS（鍵是八位數 ID）→ 輸出清單。沒名字、現金裝、不收的類別都丟掉。"""
    out = []
    for raw_id, tree in assets.items():
        if len(raw_id) != 8 or not raw_id.isdigit() or raw_id[1:4] not in EQUIP_KINDS:
            continue
        info = tree.get("info") if isinstance(tree, dict) else None
        if not isinstance(info, dict) or info.get("cash"):
            continue
        item_id = int(raw_id)
        record = names.get(str(item_id)) or {}
        name = _name(record)
        if not name:
            continue
        job = _int(info.get("reqJob"))
        out.append({
            "id": item_id, "name": name, "kind": raw_id[1:4],
            "lv": _int(info.get("reqLevel")) or 0,
            "job": job if job is not None else 0,
            "req": [_int(info.get("req" + k)) or 0 for k in ("STR", "DEX", "INT", "LUK")],
            "inc": _inc(info),
            "tuc": _int(info.get("tuc")) or 0,
            "spd": _int(info.get("attackSpeed")),
            "price": _int(info.get("price")),
            "desc": clean_text(record.get("desc")),
            "from": drops.get(item_id, []),
        })
    out.sort(key=lambda e: (e["kind"], e["lv"], e["id"]))
    return out


def build_items(groups: dict[str, dict], names: dict, drops: dict[int, list[int]]) -> list[dict[str, Any]]:
    """道具 WZJS（鍵是四位數前綴，值是那組的道具）→ 輸出清單。"""
    out = []
    for prefix, group in groups.items():
        if not prefix[:2] in ITEM_PREFIXES or prefix in ITEM_SKIP or not isinstance(group, dict):
            continue
        for raw_id, tree in group.items():
            if len(raw_id) != 8 or not raw_id.isdigit():
                continue
            info = tree.get("info") if isinstance(tree, dict) else None
            if not isinstance(info, dict) or info.get("cash"):
                continue
            item_id = int(raw_id)
            record = names.get(str(item_id)) or {}
            name = _name(record)
            if not name:
                continue
            spec = tree.get("spec") if isinstance(tree.get("spec"), dict) else {}
            kept = {k: v for k, v in spec.items() if k in SPEC_KEYS and (_int(v) is not None or isinstance(v, str))}
            out.append({
                "id": item_id, "name": name, "kind": raw_id[:4],
                "desc": clean_text(record.get("desc")),
                "price": _int(info.get("price")),
                "max": _int(info.get("slotMax")),
                "spec": kept,
                "inc": _inc(info),
                "rate": _int(info.get("success")),
                "lv": _int(info.get("reqLevel")) or _int(info.get("lv")) or 0,
                "from": drops.get(item_id, []),
            })
    out.sort(key=lambda e: (e["kind"], e["id"]))
    return out


def drop_sources(mobs: list[dict]) -> dict[int, list[int]]:
    """怪物圖鑑的 ``mobs`` → ``{道具 ID: [會掉的怪物 ID, …]}``，怪物依等級排。"""
    out: dict[int, list[int]] = {}
    for mob in sorted(mobs, key=lambda m: (m.get("level") or 0, m.get("id") or 0)):
        for item_id in mob.get("drops") or ():
            out.setdefault(int(item_id), []).append(mob["id"])
    return out


def load(directory: str | os.PathLike[str], mobs: list[dict]) -> dict[str, Any] | None:
    """從 bundle 目錄讀齊裝備、道具與名稱表並合成；缺了哪個就回傳 None。"""
    hit = gamedata.read_table(directory, ITEM_TABLE)
    names = json.loads(hit[1].decode("utf-8-sig")) if hit else None
    if not isinstance(names, dict):
        return None
    equip_bundle = gamedata.find_bundle_containing(directory, EQUIP_MARKER, limit=EQUIP_BUNDLE_LIMIT)
    item_bundle = gamedata.find_bundle_containing(directory, ITEM_MARKER)
    if not equip_bundle or not item_bundle:
        return None
    drops = drop_sources(mobs)
    equips = {n: t for n, t in wzjs.iter_assets(equip_bundle[1]) if len(n) == 8 and n.startswith("01")}
    groups = {n: t for n, t in wzjs.iter_assets(item_bundle[1]) if len(n) == 4 and n.isdigit()}
    return {"equips": build_equips(equips, names, drops), "items": build_items(groups, names, drops)}


def dump(data: dict[str, Any], path: str | os.PathLike[str]) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
