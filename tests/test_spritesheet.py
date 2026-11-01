"""spritesheet 版面測試：用程式組一包假的 MonoBehaviour，確認格、別名、代表圖的挑法都對。"""

from __future__ import annotations

import struct
import unittest

import helpers  # noqa: F401

from mapleexp import spritesheet


def _s(text: str) -> bytes:
    raw = text.encode("utf-8")
    out = struct.pack("<i", len(raw)) + raw
    return out + b"\0" * (-len(out) % 4)


def build(name="0100100", path="Mob/0100100", pages=1, frames=(), links=(), pivot_links=()):
    """照 ``spritesheet`` 模組 docstring 的版面組一包。"""
    out = b"\0" * 28 + _s(name) + _s(path) + struct.pack("<i", pages)
    for i in range(pages):
        out += _s(f"{i:032x}") + struct.pack("<ii", 0, 0)
    out += struct.pack("<i", len(frames))
    for fname, page, x, y, w, h in frames:
        out += _s(fname) + struct.pack("<iffffff", page, x, y, w, h, 0.5, 0.0)
    out += struct.pack("<i", len(links))
    for alias, index in links:
        out += _s(alias) + struct.pack("<i", index)
    out += struct.pack("<i", len(pivot_links))
    for alias, index in pivot_links:
        out += _s(alias) + struct.pack("<iff", index, 0.5, 0.5)
    return out + struct.pack("<i", 0)


FRAMES = (("die1/0", 0, 0, 0, 44, 31), ("move/0", 0, 37, 32, 37, 26), ("stand/0", 0, 107, 51, 37, 26))


class Parse(unittest.TestCase):
    def test_frames(self):
        sheet = spritesheet.parse(build(frames=FRAMES))
        self.assertEqual(sheet.name, "0100100")
        self.assertEqual(sheet.path, "Mob/0100100")
        self.assertEqual(sheet.pages, ["0" * 32])
        self.assertEqual(sheet.order, ["die1/0", "move/0", "stand/0"])
        self.assertEqual(sheet.frames["stand/0"], spritesheet.Frame(0, 107, 51, 37, 26, 0.5, 0.0))
        # y 從貼圖底部量：128 高的貼圖，y=51、高 26，PIL 的 top 是 128-51-26=51
        self.assertEqual(sheet.frames["stand/0"].box(128), (107, 51, 144, 77))
        self.assertEqual(sheet.frames["die1/0"].box(128), (0, 97, 44, 128))

    def test_links_resolve_to_frames(self):
        sheet = spritesheet.parse(build(pages=3, frames=FRAMES, links=(("stand/1", 1), ("broken", 99)),
                                        pivot_links=(("attack1/0", 0),)))
        self.assertEqual(len(sheet.pages), 3)
        self.assertEqual(sheet.links, {"stand/1": "move/0", "attack1/0": "die1/0"})
        self.assertEqual(sheet.frame("stand/1"), sheet.frames["move/0"])
        self.assertIsNone(sheet.frame("broken"))
        self.assertEqual(sheet.names(), ["die1/0", "move/0", "stand/0", "stand/1", "attack1/0"])

    def test_offset_into_larger_blob(self):
        blob = b"junk" * 5 + build(frames=FRAMES)
        self.assertEqual(spritesheet.parse(blob, start=20).name, "0100100")

    def test_garbage_raises(self):
        with self.assertRaises(spritesheet.SheetError):
            spritesheet.parse(b"\0" * 28 + struct.pack("<i", 999999) + b"x")
        with self.assertRaises(spritesheet.SheetError):
            spritesheet.parse(b"\0" * 10)


class Portrait(unittest.TestCase):
    def names(self, *frames, **kw):
        """每格給不同的座標，回傳代表圖在檔案裡的本名（別名會解成本名）。"""
        sheet = spritesheet.parse(build(frames=tuple((n, 0, i, 2, 3, 4) for i, n in enumerate(frames)), **kw))
        frame = spritesheet.pick_portrait(sheet)
        return next((n for n in sheet.order if sheet.frames[n] == frame), None)

    def test_stand_first(self):
        self.assertEqual(self.names("move/0", "die1/0", "stand/0", "fly/0"), "stand/0")

    def test_flying_mob(self):
        self.assertEqual(self.names("die1/0", "fly/1", "fly/0"), "fly/0")

    def test_stand_only_as_alias(self):
        self.assertEqual(self.names("attack1/0", "attack1/1", links=(("stand/0", 1),)), "attack1/1")

    def test_fallback_by_prefix_then_anything(self):
        self.assertEqual(self.names("die1/0", "stand/3", "stand/2"), "stand/3")
        self.assertEqual(self.names("die1/0", "hit1/0"), "hit1/0")
        self.assertEqual(self.names("die1/2", "die1/0"), "die1/2")

    def test_empty(self):
        self.assertIsNone(spritesheet.pick_portrait(spritesheet.parse(build())))

    def test_skips_empty_frames(self):
        sheet = spritesheet.parse(build(frames=(("stand/0", 0, 0, 0, 0, 0), ("move/0", 0, 0, 0, 5, 5))))
        self.assertEqual(spritesheet.pick_portrait(sheet), sheet.frames["move/0"])


if __name__ == "__main__":
    unittest.main()
