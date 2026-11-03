"""世界地圖：客戶端的地圖插畫、上面的節點與區域連結，網頁「圖鑑」的地圖分頁讀這個。

來源都在客戶端裡：

- **結構**：``Etc/WorldMap.json``，``Worlds`` 底下一張世界地圖一筆：``MapList`` 是節點（``SpotX``／``SpotY``
  是相對底圖樞紐的座標，``MapNo`` 是節點涵蓋的地圖，``Title``／``Desc`` 是字串鍵），``MapLinkList`` 是點了
  進入下一層的區域，``ParentMap`` 是上一層。節點在清單裡的位置就是圖集裡 ``MapList/<位置>/path`` 的編號。
- **文字**：``String/TW/WorldMap.json``，第一層的鍵是 ``WorldMapId``（總圖跟楓之島都是 0，共用一份），
  ``values`` 裡是 ``5_title``、``7_desc`` 與區域名稱 ``<MapLinkIdx>_toolTip``（``MapLinkList`` 自己的
  ``Tooltip`` 是空字串）。
- **圖**：``spritesheet_*.bundle`` 的 ``Map/WorldMap/<名稱>.asset``：``BaseImg/0`` 底圖、
  ``MapLink/<編號>/link/linkImg`` 區域亮起來的形狀、``MapList/<編號>/path`` 節點亮起來的路線。

這兩張 ``WorldMap`` 表同名、在不同的 bundle，所以 :func:`load_tables` 兩張都要掃到才停。

輸出格式（``web/worldmap.json``）::

    {"worlds": [{"id": "WorldMap010", "title": "維多利亞島", "parent": "WorldMap", "w": 640, "h": 470,
                 "nodes": [{"x": 292, "y": 377, "type": 0, "maps": [100000000, 100000001], "title": "", "desc": "",
                            "path": [84, 200, 84, 33]}, …],
                 "links": [{"i": 1, "to": "WorldMap012", "tip": "奇幻村", "box": [215, 212, 121, 100]}, …]}, …]}

``x``／``y`` 與 ``box``、``path``（``[左, 上, 寬, 高]``）都是底圖左上角起算的像素。``maps`` 含還沒開放、客戶端
沒有名字的地圖。圖檔（``web/worldmap/``）由 ``scripts/export_worldmap.py`` 輸出：``<id>.webp`` 底圖、
``<id>_link<i>.png`` 區域、``<id>_path<節點位置>.png`` 路線、``node<type>.png`` 節點圖示、``cur.png`` 目前位置。
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import gamedata
from .worldbook import quest_text

TABLE = "WorldMap"
ROOT = "WorldMap"
ROOT_TITLE = "楓之谷世界"
BASE_FRAME = "BaseImg/0"


def link_frame(index: int) -> str:
    return f"MapLink/{index}/link/linkImg"


def path_frame(index: int) -> str:
    return f"MapList/{index}/path"


def origin(base: Any) -> tuple[float, float]:
    """底圖樞紐在圖上的位置（從左上角算）：節點座標與其他圖層都對齊這一點。樞紐是比例，y 往上長。"""
    return base.pivot_x * base.width, (1 - base.pivot_y) * base.height


def place(base: Any, frame: Any) -> list[int]:
    """圖層在底圖上的 ``[左, 上, 寬, 高]``：兩張圖的樞紐疊在一起。圖層的樞紐常常超出自己（比例小於 0 或大於 1）。"""
    ox, oy = origin(base)
    return [round(ox - frame.pivot_x * frame.width), round(oy - (1 - frame.pivot_y) * frame.height), frame.width, frame.height]


def split_tables(tables: list[dict]) -> tuple[dict, dict]:
    """兩張同名的表分開：有 ``Worlds`` 的是結構，其他的是字串。"""
    worlds = next((t["Worlds"] for t in tables if isinstance(t.get("Worlds"), dict)), {})
    strings = next((t for t in tables if not isinstance(t.get("Worlds"), dict)), {})
    return worlds, strings


def load_tables(directory: str | os.PathLike[str]) -> tuple[dict, dict]:
    """掃 bundle 找齊兩張 ``WorldMap`` 表，回傳 ``(結構, 字串)``；缺的那張是空的。"""
    tables: list[dict] = []
    for bundle in gamedata.list_bundles(directory):
        try:
            blob = gamedata.extract_table(bundle, TABLE)
            raw = json.loads(blob.decode("utf-8-sig")) if blob else None
        except (gamedata.BundleError, OSError, ValueError):
            continue
        if isinstance(raw, dict):
            tables.append(raw)
            if len(tables) == 2:
                break
    return split_tables(tables)


def _text(values: dict, key: Any) -> str:
    return quest_text(values.get(key), {}) if key else ""


def build(worlds: dict[str, dict], strings: dict[str, dict], sheets: dict[str, Any]) -> dict[str, Any]:
    """結構 + 字串 + 每張世界地圖的圖集（:class:`mapleexp.spritesheet.Sheet`）→ 輸出格式。

    沒有底圖的世界地圖不要（畫不出來）；指到不要的世界地圖的區域也不要。總圖排第一，其他照名稱。
    """
    usable = {name for name, sheet in sheets.items() if name in worlds and sheet.frame(BASE_FRAME)}
    values_of = {name: (strings.get(str(world.get("WorldMapId"))) or {}).get("values") or {} for name, world in worlds.items()}
    tips = {}                                       # 世界地圖 → 上一層連到它的區域名稱
    for name in usable:
        for link in worlds[name].get("MapLinkList") or []:
            tips.setdefault(link.get("LinkMap"), _text(values_of[name], f"{link.get('MapLinkIdx')}_toolTip"))

    out = []
    for name in sorted(usable, key=lambda n: (n != ROOT, n)):
        world, sheet, values = worlds[name], sheets[name], values_of[name]
        base = sheet.frame(BASE_FRAME)
        ox, oy = origin(base)
        nodes = []
        for index, node in enumerate(world.get("MapList") or []):
            entry: dict[str, Any] = {
                "x": round(ox + node.get("SpotX", 0)), "y": round(oy + node.get("SpotY", 0)), "type": node.get("Type", 1),
                "maps": list(dict.fromkeys(node.get("MapNo") or [])),
                "title": _text(values, node.get("Title")), "desc": _text(values, node.get("Desc")),
            }
            path = sheet.frame(path_frame(index))
            if path:
                entry["path"] = place(base, path)
            nodes.append(entry)
        links = []
        for link in sorted(world.get("MapLinkList") or [], key=lambda l: l.get("MapLinkIdx", 0)):
            index, target, shape = link.get("MapLinkIdx", 0), link.get("LinkMap"), None
            shape = sheet.frame(link_frame(index))
            if target in usable and shape:
                links.append({"i": index, "to": target, "tip": tips.get(target) or target, "box": place(base, shape)})
        parent = world.get("ParentMap")
        out.append({
            "id": name, "title": ROOT_TITLE if name == ROOT else tips.get(name) or name,
            "parent": parent if parent in usable else None, "w": base.width, "h": base.height,
            "nodes": nodes, "links": links,
        })
    return {"worlds": out}
