"""解客戶端的 ``WZJS`` 資料：舊版 ``.wz`` 的節點樹，經典版客戶端序列化成的二進位格式。

怪物數值（``Mob.wz``）、怪物圖鑑的掉落與地圖清單（``Etc.wz/MonsterBook.img``）都是這種格式，
一個 ``.img`` 就是 bundle 裡的一個 MonoBehaviour，名字是原本的檔名（怪物是七位數 ID，
圖鑑是 ``MonsterBook``）。跟 :mod:`mapleexp.gamedata` 的 JSON 表不同，這裡沒有現成的文字，
要照它的版面一塊一塊讀出來。

版面（實測對照 Unity 6000.3 的客戶端，全部小端序 int32）::

    <i32 名稱長度><名稱><對齊到 4 的填充>
    <36 個 i32 的 header>
    "WZJS" <i32 版本>
    <節點表> <各種值表> <字串表>

header 裡的數字都是相對於 ``WZJS`` 那四個字的位移，依序是::

    [0..1]   節點：筆數、位移（永遠是 8，緊接在版本後面）
    [2..3]   i32 陣列：筆數＝節點數－1（用途不明，讀的時候用不到）
    [4..9]   三個沒看過有內容的陣列
    [10..11] 整數值：筆數、位移
    [12..13] 沒看過有內容的陣列
    [14..15] 浮點值（float32）
    [16..17] 座標值（兩個 float32 一組：origin、lt、rb 那些）
    [18..25] 四個沒看過有內容的陣列
    [26..28] 名稱字串表：筆數、內容位移、位移表位移
    [29..31] 完整路徑表（``100100/info/level`` 這種，讀的時候用不到）
    [32..34] 字串值表：筆數、內容位移、位移表位移
    [35]     整包的長度

節點每筆 8 個 i32：``型別、名稱索引、值索引、第一個子節點、子節點數、父節點、路徑索引、保留``。
子節點是連續排的，從「第一個子節點」數「子節點數」筆就是。型別：2 是容器、6 整數、8 浮點、
11 與 12 字串（12 是 ``../stand/0`` 這種指向別的節點的連結）、14 座標、18 圖片。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Iterator

MAGIC = b"WZJS"
HEADER_INTS = 36
HEADER_SIZE = HEADER_INTS * 4
NODE_INTS = 8
NODE_SIZE = NODE_INTS * 4

TYPE_DIR = 2
TYPE_INT = 6
TYPE_FLOAT = 8
TYPE_STRING = 11
TYPE_LINK = 12
TYPE_VECTOR = 14
TYPE_SPRITE = 18
TYPE_SPRITE_LINK = 19   # 圖借別格（$inlink），origin、map、z 是自己的

_MAX_NAME = 64


class WzError(Exception):
    """資料看起來不是我們認得的 WZJS 版面。"""


@dataclass(frozen=True)
class Vector:
    """座標型的值（origin、lt、rb）。"""

    x: float
    y: float


def _ints(data: bytes, offset: int, count: int) -> list[int]:
    return list(struct.unpack_from(f"<{count}i", data, offset)) if count else []


def _string_table(data: bytes, base: int, count: int, content: int, offsets: int) -> list[str]:
    """字串表：``count + 1`` 個位移（最後一個是結尾），指到內容區裡的 UTF-8。"""
    if count <= 0:
        return []
    bounds = _ints(data, base + offsets, count + 1)
    out = []
    for n in range(count):
        start, end = base + content + bounds[n], base + content + bounds[n + 1]
        if not base <= start <= end <= len(data):
            raise WzError("字串表位移超出範圍")
        out.append(data[start:end].decode("utf-8", "replace"))
    return out


def asset_name(data: bytes, header_at: int) -> str | None:
    """header 前面那段 ``<i32 長度><名稱><填充>`` 的名稱；對不上就回傳 None。"""
    for length in range(1, _MAX_NAME):
        field = 4 + ((length + 3) & ~3)
        start = header_at - field
        if start < 0:
            return None
        if struct.unpack_from("<i", data, start)[0] == length:
            raw = data[start + 4 : start + 4 + length]
            if raw.isascii() and raw.replace(b"_", b"").replace(b".", b"").isalnum():
                return raw.decode("ascii")
    return None


def parse(data: bytes, magic_at: int, canvas: bool = False) -> dict[str, Any]:
    """把 *magic_at*（``WZJS`` 的位置）那一包解成巢狀 dict。

    容器是 dict（保留客戶端裡的順序），葉節點是 int、float、str 或 :class:`Vector`；
    圖片之類讀不到內容的型別放 None，鍵還留著，看得出有這個節點。*canvas* 為真時，有子節點的圖片（origin、map、z）
    也展開成 dict，紙娃娃要靠這些對位。
    """
    if data[magic_at : magic_at + 4] != MAGIC:
        raise WzError("不是 WZJS")
    if magic_at < HEADER_SIZE:
        raise WzError("header 放不下")
    h = _ints(data, magic_at - HEADER_SIZE, HEADER_INTS)
    base = magic_at
    total = h[35]
    if total <= 0 or base + total > len(data):
        raise WzError("長度超出資料範圍")
    node_count, node_at = h[0], h[1]
    if node_count <= 0 or node_at + node_count * NODE_SIZE > total:
        raise WzError("節點表超出範圍")
    ints = _ints(data, base + h[11], h[10])
    floats = list(struct.unpack_from(f"<{h[14]}f", data, base + h[15])) if h[14] else []
    vectors = list(struct.unpack_from(f"<{h[16] * 2}f", data, base + h[17])) if h[16] else []
    names = _string_table(data, base, h[26], h[27], h[28])
    values = _string_table(data, base, h[32], h[33], h[34])

    nodes = [
        struct.unpack_from(f"<{NODE_INTS}i", data, base + node_at + n * NODE_SIZE)
        for n in range(node_count)
    ]

    def leaf(kind: int, index: int) -> Any:
        try:
            if kind == TYPE_INT:
                return ints[index]
            if kind == TYPE_FLOAT:
                return floats[index]
            if kind in (TYPE_STRING, TYPE_LINK):
                return values[index]
            if kind == TYPE_VECTOR:
                return Vector(vectors[index * 2], vectors[index * 2 + 1])
        except IndexError as exc:
            raise WzError(f"值索引超出範圍（型別 {kind}）") from exc
        return None

    def build(index: int, depth: int) -> Any:
        if depth > 64:
            raise WzError("節點巢狀太深")
        kind, name_idx, value_idx, first, count, *_ = nodes[index]
        if kind != TYPE_DIR and not (canvas and kind in (TYPE_SPRITE, TYPE_SPRITE_LINK) and count > 0):
            return leaf(kind, value_idx)
        out: dict[str, Any] = {}
        if count == 0:          # 空容器的「第一個子節點」是 -1
            return out
        if count < 0 or first < 0 or first + count > node_count:
            raise WzError("子節點範圍不合理")
        for child in range(first, first + count):
            if not 0 <= nodes[child][1] < len(names):
                raise WzError("名稱索引超出範圍")
            out[names[nodes[child][1]]] = build(child, depth + 1)
        return out

    root = build(0, 0)
    return root if isinstance(root, dict) else {}


def iter_assets(data: bytes, canvas: bool = False) -> Iterator[tuple[str, dict[str, Any]]]:
    """掃過整段資料，逐一產出 ``(名稱, 樹)``。

    壞掉或認不得的那包直接跳過：bundle 裡偶爾會有別的東西剛好含 ``WZJS`` 四個字。
    """
    pos = 0
    while True:
        at = data.find(MAGIC, pos)
        if at < 0:
            return
        pos = at + len(MAGIC)
        try:
            name = asset_name(data, at - HEADER_SIZE)
            if name is None:
                continue
            tree = parse(data, at, canvas)
        except (WzError, struct.error):
            continue
        yield name, tree


def find_asset(data: bytes, name: str) -> dict[str, Any] | None:
    """只要某一包：照名稱找，沒有就 None。"""
    for found, tree in iter_assets(data):
        if found == name:
            return tree
    return None
