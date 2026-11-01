"""解客戶端的 spritesheet：一包 MonoBehaviour 記「哪一格圖在貼圖的哪裡」。

怪物、NPC、物品的圖在經典版客戶端裡不是 Unity 的 Sprite，而是自訂的圖集：
每隻怪一個 ``<ID>.asset``（MonoBehaviour）配一到多張 ``<ID>_<頁>.png``（Texture2D，RGBA32）。
MonoBehaviour 沒附 type tree，UnityPy 也解不開，所以照它的版面自己讀。這個模組只管
位元組 → 結構，貼圖本身交給呼叫端（``scripts/export_monsterbook.py`` 用 UnityPy 讀）。

版面（實測對照 Unity 6000.3 的客戶端，全部小端序；字串是 ``<i32 長度><內容><對齊到 4>``）::

    <28 bytes：m_GameObject、m_Enabled、m_Script，用不到>
    <字串 名稱>            "0100100"
    <字串 路徑>            "Mob/0100100"
    <i32 貼圖頁數> × { <字串 32 字 hash> <i32> <i32> }
    <i32 格數> × { <字串 名稱> <i32 頁> <f32 x> <f32 y> <f32 寬> <f32 高> <f32 樞紐x> <f32 樞紐y> }
    <i32 筆數> × { <字串 名稱> <i32 格編號> }                     別名：另一個名字指到同一格
    <i32 筆數> × { <字串 名稱> <i32 格編號> <f32> <f32> }         別名，自己帶樞紐
    <i32 0>

座標照 Unity 的慣例，原點在貼圖**左下角**、y 往上長，寬高是像素；要給 PIL 裁切得用貼圖高度
翻過來（:meth:`Frame.box`）。格的名稱是舊版 .wz 的路徑：
``stand/0``、``move/3``、``die1/0``、``attack1/info/effect/0`` 這種。有些怪的站姿只存在別名表
（``stand/0`` 指到某個攻擊格），找格的時候兩張表都要看。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

_PREFIX = 28           # MonoBehaviour 固定欄位
_MAX_STRING = 4096
_MAX_COUNT = 100_000


class SheetError(Exception):
    """資料看起來不是我們認得的 spritesheet 版面。"""


@dataclass(frozen=True)
class Frame:
    page: int
    x: int
    y: int
    width: int
    height: int
    pivot_x: float = 0.0
    pivot_y: float = 0.0

    def box(self, page_height: int) -> tuple[int, int, int, int]:
        """PIL ``crop`` 用的 ``(left, top, right, bottom)``；*page_height* 是這一頁貼圖的高度。"""
        top = page_height - self.y - self.height
        return (self.x, top, self.x + self.width, top + self.height)


@dataclass
class Sheet:
    name: str
    path: str
    pages: list[str] = field(default_factory=list)
    frames: dict[str, Frame] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)      # 格在檔案裡的順序

    def frame(self, name: str) -> Frame | None:
        """照名稱找格，本名找不到就看別名。"""
        found = self.frames.get(name)
        if found is None and name in self.links:
            found = self.frames.get(self.links[name])
        return found

    def names(self) -> list[str]:
        return self.order + [n for n in self.links if n not in self.frames]


def _string(data: bytes, pos: int) -> tuple[str, int]:
    (length,) = struct.unpack_from("<i", data, pos)
    if not 0 <= length <= _MAX_STRING or pos + 4 + length > len(data):
        raise SheetError("字串長度不合理")
    text = data[pos + 4 : pos + 4 + length].decode("utf-8")
    pos += 4 + length
    return text, (pos + 3) & ~3


def _count(data: bytes, pos: int) -> tuple[int, int]:
    (n,) = struct.unpack_from("<i", data, pos)
    if not 0 <= n <= _MAX_COUNT:
        raise SheetError("筆數不合理")
    return n, pos + 4


def parse(data: bytes, start: int = 0) -> Sheet:
    """把 MonoBehaviour 的原始位元組解成 :class:`Sheet`。*start* 是那包在 *data* 裡的起點。"""
    try:
        pos = start + _PREFIX
        name, pos = _string(data, pos)
        path, pos = _string(data, pos)
        sheet = Sheet(name=name, path=path)
        n, pos = _count(data, pos)
        for _ in range(n):
            digest, pos = _string(data, pos)
            sheet.pages.append(digest)
            pos += 8
        n, pos = _count(data, pos)
        order: list[str] = []
        for _ in range(n):
            frame_name, pos = _string(data, pos)
            page, x, y, w, h, px, py = struct.unpack_from("<iffffff", data, pos)
            pos += 28
            if w < 0 or h < 0 or x < 0 or y < 0:
                raise SheetError("格的座標不合理")
            sheet.frames[frame_name] = Frame(page, int(x), int(y), int(w), int(h), px, py)
            order.append(frame_name)
        sheet.order = order
        for extra in (0, 8):
            n, pos = _count(data, pos)
            for _ in range(n):
                alias, pos = _string(data, pos)
                (index,) = struct.unpack_from("<i", data, pos)
                pos += 4 + extra
                if 0 <= index < len(order):
                    sheet.links.setdefault(alias, order[index])
    except (struct.error, UnicodeDecodeError) as exc:
        raise SheetError(str(exc)) from exc
    return sheet


# 圖鑑用哪一格：站姿優先，飛行怪沒有站姿就用飛行，再來是移動；都沒有就挑第一個像「平常樣子」的格。
PORTRAIT_NAMES = ("stand/0", "fly/0", "move/0", "walk/0", "info/default/0")
PORTRAIT_PREFIXES = ("stand/", "fly/", "move/", "walk/", "attack1/", "hit1/")


def pick_portrait(sheet: Sheet) -> Frame | None:
    """挑一格當這隻怪的代表圖，沒有任何格就 None。"""
    for name in PORTRAIT_NAMES:
        frame = sheet.frame(name)
        if frame is not None and frame.width > 0 and frame.height > 0:
            return frame
    names = [n for n in sheet.names() if (f := sheet.frame(n)) and f.width > 0 and f.height > 0]
    for prefix in PORTRAIT_PREFIXES:
        for name in names:
            if name.startswith(prefix):
                return sheet.frame(name)
    return sheet.frame(names[0]) if names else None

