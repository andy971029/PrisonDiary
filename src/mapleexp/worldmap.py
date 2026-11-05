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

客戶端的節點只涵蓋約一半的地圖。:func:`add_inferred` 用地圖圖鑑的傳送點關係替其他地圖補節點，接在 ``nodes`` 後面，
多一個 ``via``：傳送點連到同一張世界地圖的哪幾個節點（客戶端的節點沒有這個欄位）。
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter, deque
from typing import Any

from . import gamedata
from .worldbook import quest_text

TABLE = "WorldMap"
ROOT = "WorldMap"
ROOT_TITLE = "楓之谷世界"
BASE_FRAME = "BaseImg/0"
INFERRED_TYPE = 3
SPOT_GAP = 16                                       # 補的節點離其他節點至少這麼遠（底圖像素）
SPOT_RADII = range(20, 86, 6)
SPOT_ANGLES = 16
SPOT_MARGIN = 12


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


def _spot(world: dict, parent: dict, back: dict | None) -> tuple[int, int]:
    """替接在 *parent* 的節點找位置：由近到遠繞圈，第一個有空位的圈裡，順著 *back* → *parent* 的方向走（一條鏈才拉得直），
    沒方向就取最空的一點；每圈都太擠就取最空的。"""
    fallback, fallback_room = (parent["x"], parent["y"]), -1.0
    for radius in SPOT_RADII:
        picks = []
        for step in range(SPOT_ANGLES):
            angle = 2 * math.pi * step / SPOT_ANGLES
            dx, dy = radius * math.cos(angle), radius * math.sin(angle)
            x, y = round(parent["x"] + dx), round(parent["y"] + dy)
            if not (SPOT_MARGIN <= x <= world["w"] - SPOT_MARGIN and SPOT_MARGIN <= y <= world["h"] - SPOT_MARGIN):
                continue
            room = min(math.hypot(x - n["x"], y - n["y"]) for n in world["nodes"])
            if room > fallback_room:
                fallback, fallback_room = (x, y), room
            if room >= SPOT_GAP:
                ahead = 0.0
                if back is not None:
                    bx, by = parent["x"] - back["x"], parent["y"] - back["y"]
                    ahead = round((bx * dx + by * dy) / ((math.hypot(bx, by) or 1) * radius), 3)
                picks.append((ahead, room, x, y))
        if picks:
            _, _, x, y = max(picks)
            return x, y
    return fallback


def add_inferred(data: dict[str, Any], maps: list[dict]) -> int:
    """替不在任何節點上、但有傳送點連回節點地圖的地圖補節點，回傳補了幾個節點。

    上一層涵蓋整區的總節點不算：巨人之林只在維多利亞島的粉色節點上，奇幻村這層卻沒有它，一樣要補，才接得到森林迷宮V。

    傳送點不分方向。沒有節點的地圖彼此連通的算一組，接到傳送點最多的那張節點地圖，放在它最小的節點旁邊（總圖的節點是
    整區的總和，別層有就不用），從那裡沿傳送點一張一張往外放；``via`` 列這個節點用傳送點連到的所有節點。同一個節點底下
    同名、連到的地圖也一樣的（隱藏研究室這種副本的很多份）併成一個點。連不回節點的不補，留給網頁列在地圖下面。
    """
    by_id = {m["id"]: m for m in maps}
    worlds = data["worlds"]
    spots: dict[int, list[tuple[int, int, int]]] = {}
    by_world = {w["id"]: w for w in worlds}
    rolled_up: set[tuple[int, int]] = set()         # 跟下一層的節點有重疊的節點，是整區的總和
    for w_index, world in enumerate(worlds):
        below = {m for link in world.get("links", []) for n in by_world[link["to"]]["nodes"] for m in n["maps"]}
        rolled_up.update((w_index, n_index) for n_index, node in enumerate(world["nodes"]) if below.intersection(node["maps"]))
        for n_index, node in enumerate(world["nodes"]):
            for map_id in node["maps"]:
                spots.setdefault(map_id, []).append((w_index, n_index, len(node["maps"])))
    loose = {map_id for map_id in by_id if all(s[:2] in rolled_up for s in spots.get(map_id, []))}
    near: dict[int, list[int]] = {map_id: [] for map_id in by_id}
    for m in maps:
        for target in m["to"]:
            if target in by_id:
                near[m["id"]].append(target)
                near[target].append(m["id"])

    made: dict[int, tuple[int, int]] = {}           # 補了節點的地圖 → (世界地圖, 節點)
    slots: dict[tuple, int] = {}                    # 併成同一點的鍵 → 節點
    seen: set[int] = set()
    for start in sorted(loose):
        if start in seen:
            continue
        seen.add(start)
        stack, members, anchors = [start], [], Counter()
        while stack:
            here = stack.pop()
            members.append(here)
            for other in near[here]:
                if other not in loose:
                    anchors[other] += 1
                elif other not in seen:
                    seen.add(other)
                    stack.append(other)
        if not anchors:
            continue
        anchor = min(anchors, key=lambda a: (-anchors[a], a))
        w_index, n_index, _ = min(spots[anchor], key=lambda s: (worlds[s[0]]["id"] == ROOT, s[2]))
        world = worlds[w_index]
        # 由近到遠：每張圖放在第一張連到它的圖旁邊，再往前一張決定方向
        queue = deque((m, n_index, None) for m in sorted(set(near[anchor]) & set(members)))
        placed: set[int] = set()
        while queue:
            map_id, parent, back = queue.popleft()
            if map_id in placed:
                continue
            placed.add(map_id)
            key = (w_index, n_index, by_id[map_id]["name"], frozenset(near[map_id]))
            if key in slots:
                index = slots[key]
                world["nodes"][index]["maps"].append(map_id)
                world["nodes"][index]["maps"].sort()
            else:
                x, y = _spot(world, world["nodes"][parent], world["nodes"][back] if back is not None else None)
                index = len(world["nodes"])
                world["nodes"].append({"x": x, "y": y, "type": INFERRED_TYPE, "maps": [map_id], "title": "", "desc": "", "via": []})
                slots[key] = index
            made[map_id] = (w_index, index)
            for other in sorted(set(near[map_id])):
                if other in loose and other not in placed:
                    queue.append((other, index, parent))

    for map_id, (w_index, index) in made.items():
        node = worlds[w_index]["nodes"][index]
        for other in near[map_id]:
            if other in made:
                target = made[other][1] if made[other][0] == w_index else None
            else:
                here = [s for s in spots.get(other, []) if s[0] == w_index]
                target = min(here, key=lambda s: s[2])[1] if here else None
            if target is not None and target != index and target not in node["via"]:
                node["via"].append(target)
        node["via"].sort()
    return len(slots)


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
