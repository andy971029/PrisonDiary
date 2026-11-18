"""純 Python 的 LZ4 block 解壓：手寫幾個 block 對照，有裝 lz4 的話再跟它壓出來的互相驗證。"""

from __future__ import annotations

import unittest

import helpers  # noqa: F401

from mapleexp import lz4py

try:
    import lz4.block as lz4_block
except Exception:  # pragma: no cover - 取決於安裝環境
    lz4_block = None


class DecompressTest(unittest.TestCase):
    def test_literals_only(self):
        self.assertEqual(lz4py.decompress(b"\x50hello", 5), b"hello")
        self.assertEqual(lz4py.decompress(b"", 0), b"")

    def test_match_copies_earlier_bytes(self):
        # 「abcd」之後往回 4 個位元組複製 4 個，再接一個只有 literal 的結尾
        self.assertEqual(lz4py.decompress(b"\x40abcd\x04\x00\x10e", 9), b"abcdabcde")

    def test_overlapping_match_repeats_the_tail(self):
        # 往回 1 個位元組複製 10 個：就是把「a」再鋪 10 次
        self.assertEqual(lz4py.decompress(b"\x16a\x01\x00\x10b", 12), b"a" * 11 + b"b")
        # 往回 2 個複製 7 個：ab 重複
        self.assertEqual(lz4py.decompress(b"\x23ab\x02\x00\x10!", 10), b"ababababa!")

    def test_long_lengths_spill_into_extra_bytes(self):
        text = bytes(range(256)) + b"tail" * 5                        # 276 個 literal：15 + 255 + 6
        self.assertEqual(lz4py.decompress(b"\xf0\xff\x06" + text, len(text)), text)
        # match 長度 4 + 15 + 255 + 3 = 277
        self.assertEqual(lz4py.decompress(b"\x1fz\x01\x00\xff\x03\x10!", 279), b"z" * 278 + b"!")

    def test_bad_data_is_reported(self):
        for src, size in ((b"\x50hello", 4), (b"\x50hel", 5), (b"\x10a\x05\x00", 5), (b"\x10a\x00\x00\x10b", 6), (b"\x1fz\x01\x00\xff", 300)):
            with self.subTest(src=src), self.assertRaises(lz4py.Lz4Error):
                lz4py.decompress(src, size)

    @unittest.skipIf(lz4_block is None, "沒有 lz4 可以對照")
    def test_agrees_with_lz4(self):
        import random

        rng = random.Random(7)
        for _ in range(20):
            raw = b"".join(rng.choice((b"maple", b"\x00\x00\x00\x00", bytes([rng.randrange(256)]), b"abc" * 9)) for _ in range(rng.randrange(1, 400)))
            packed = lz4_block.compress(raw, store_size=False)
            self.assertEqual(lz4py.decompress(packed, len(raw)), raw)


if __name__ == "__main__":
    unittest.main()
