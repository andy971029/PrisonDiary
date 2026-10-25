"""神聖祈禱：找畫面右上角的 buff 圖示、讀左下角的秒數、變成連續的倒數。

畫面是合成的：雜訊背景的最上面一排放幾個 buff 圖示，祈禱圖示用內建模板的亮度畫，
快結束時下半部蓋一層灰（亮度壓低、偏藍），左下角用種子字形畫黃色秒數。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
import numpy as np

from mapleexp.core import prayer as core
from mapleexp.core.prayer import PrayerWatch
from mapleexp.vision import prayer

WEB = Path(__file__).resolve().parents[1] / "web"
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))

import bridge  # noqa: E402
from test_web_bridge import rgba_bytes  # noqa: E402

YELLOW_BGR = (8, 170, 210)
WASHED_YELLOW_BGR = (91, 172, 189)     # 分享視窗串流洗淡後的黃色數字
TAN_BGR = (88, 160, 190)        # 楓葉外框的土黃色：不能被當成黃色數字


def digit_mask(char: str) -> np.ndarray:
    rows = prayer.SEED_DIGITS[char][0]
    return np.array([[c == "#" for c in row] for row in rows], dtype=bool)


def draw_icon(screen: np.ndarray, left: int, top: int, seconds: int | None = None,
              grey_from: int | None = None, colour=YELLOW_BGR) -> None:
    """畫一個祈禱圖示；``grey_from`` 以下的列蓋灰，``seconds`` 不是 None 就在左下角畫黃色秒數。"""
    icon = np.repeat(prayer.TEMPLATE[..., None], 3, axis=2)
    if grey_from is not None:
        grey = icon[grey_from:]
        icon[grey_from:] = grey * 0.35 + np.array([70.0, 55.0, 45.0])     # 壓暗、偏藍（BGR）
    screen[top : top + prayer.ICON_H, left : left + prayer.ICON_W, :3] = np.clip(icon, 0, 255)
    if seconds is not None:
        x = left + 1
        for char in str(seconds):
            mask = digit_mask(char)
            region = screen[top + 20 : top + 20 + mask.shape[0], x : x + mask.shape[1], :3]
            region[mask] = colour
            x += mask.shape[1] + 1


def draw_other_icon(screen: np.ndarray, left: int, top: int, seed: int) -> None:
    """別的 buff：同樣大小的方塊，內容是另一種圖案。"""
    rng = np.random.default_rng(seed)
    blocks = rng.integers(0, 255, size=(6, 6, 3)).astype(np.uint8)
    screen[top : top + 30, left : left + 30, :3] = np.kron(blocks, np.ones((5, 5, 1), dtype=np.uint8))


def make_screen(icon_at: tuple[int, int] | None = (1180, 1), seconds: int | None = None,
                grey_from: int | None = None, size=(1280, 720), seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    width, height = size
    screen = np.zeros((height, width, 4), dtype=np.uint8)
    screen[..., :3] = rng.integers(20, 200, size=(height, width, 3), dtype=np.uint8)
    screen[..., 3] = 255
    draw_other_icon(screen, width - 33, 1, seed=1)
    draw_other_icon(screen, width - 140, 1, seed=2)
    if icon_at is not None:
        draw_icon(screen, *icon_at, seconds=seconds, grey_from=grey_from)
    return screen


def reader_without(chars: str) -> prayer.TimerReader:
    templates = prayer.seed_templates()
    for char in chars:
        templates.remove_char(char)
    return prayer.TimerReader(templates)


class TestFindIcon(unittest.TestCase):
    def test_finds_icon_wherever_it_is_in_the_top_row(self):
        for at in ((1180, 1), (1000, 3), (900, 0)):
            with self.subTest(at=at):
                found = prayer.find_icon(make_screen(at))
                self.assertIsNotNone(found)
                self.assertEqual(found[:2], at)

    def test_greyed_icon_still_matches(self):
        """快結束時下半部（甚至大半）變灰，輪廓還在，照樣要認得出來。"""
        for grey_from in (20, 12, 5):
            with self.subTest(grey_from=grey_from):
                found = prayer.find_icon(make_screen(seconds=33, grey_from=grey_from))
                self.assertEqual(found[:2], (1180, 1))
                self.assertGreater(found[2], 0.8)

    def test_no_prayer_icon(self):
        self.assertIsNone(prayer.find_icon(make_screen(None)))

    def test_only_searches_the_top_row(self):
        """一般搜尋只看最上面一排：低一點的同一個圖案不算。"""
        self.assertIsNone(prayer.find_icon(make_screen((1000, 300))))

    def test_wide_search_finds_icon_below_a_title_bar(self):
        """視窗模式：buff 在遊戲視窗頂端，上面還有標題列（或整個螢幕的其他東西）。"""
        for at in ((1000, 31), (900, 106), (1100, 300)):
            with self.subTest(at=at):
                found = prayer.find_icon(make_screen(at, seconds=33, grey_from=12), wide=True)
                self.assertIsNotNone(found)
                self.assertEqual(found[:2], at)

    def test_wide_search_stays_in_the_upper_half(self):
        self.assertIsNone(prayer.find_icon(make_screen((1000, 500)), wide=True))
        self.assertIsNone(prayer.find_icon(make_screen(None), wide=True))

    def test_hint_follows_a_shifted_icon(self):
        """其他 buff 消失時圖示會往右移：附近找不到就整排重找。"""
        found = prayer.find_icon(make_screen((1100, 1)), hint=(1180, 1))
        self.assertEqual(found[:2], (1100, 1))


class TestReadTimer(unittest.TestCase):
    def read(self, screen, reader=None):
        found = prayer.find_icon(screen)
        zone = prayer.cut_timer(screen, found[:2])
        return zone, (reader or prayer.TimerReader()).read_templates(zone)

    def test_full_colour_icon_means_over_a_minute(self):
        zone, value = self.read(make_screen(seconds=None))
        self.assertFalse(zone.has_seconds)
        self.assertIsNone(value)

    def test_reads_yellow_seconds(self):
        for seconds in (56, 33, 39, 5):
            with self.subTest(seconds=seconds):
                zone, value = self.read(make_screen(seconds=seconds, grey_from=10))
                self.assertTrue(zone.has_seconds)
                self.assertEqual(value, seconds)

    def test_washed_out_stream_colours_still_count_as_yellow(self):
        screen = make_screen(icon_at=None)
        draw_icon(screen, 1180, 1, seconds=56, grey_from=10, colour=WASHED_YELLOW_BGR)
        zone, value = self.read(screen)
        self.assertTrue(zone.has_seconds)
        self.assertEqual(value, 56)

    def test_tan_leaf_outline_is_not_a_digit(self):
        screen = make_screen(seconds=None)
        screen[1 + 20 : 1 + 28, 1181:1186, :3] = TAN_BGR
        zone, _ = self.read(screen)
        self.assertFalse(zone.has_seconds)

    def test_unknown_digit_reads_nothing_until_taught(self):
        reader = reader_without("5")
        zone, value = self.read(make_screen(seconds=35, grey_from=10), reader)
        self.assertTrue(zone.has_seconds)
        self.assertIsNone(value)
        self.assertEqual(reader.teach(zone, 35), 1)
        self.assertEqual(reader.read_templates(zone), 35)


class TestPrayerWatch(unittest.TestCase):
    def watch(self) -> PrayerWatch:
        w = PrayerWatch()
        w.set_enabled(True)
        return w

    def test_off_until_enabled(self):
        w = PrayerWatch()
        w.feed(0.0, seen=True)
        self.assertEqual(w.snapshot(0.0).status, core.OFF)

    def test_long_then_counting_then_recast(self):
        w = self.watch()
        self.assertEqual(w.snapshot(0.0).status, core.SEARCHING)
        w.feed(0.0, seen=True, yellow=False)
        self.assertEqual(w.snapshot(0.0).status, core.LONG)
        w.feed(1.0, seen=True, seconds=56, yellow=True)
        snap = w.snapshot(3.0)
        self.assertEqual(snap.status, core.COUNTING)
        self.assertAlmostEqual(snap.remaining, 54.5)
        # 寵物重放：秒數變回白色。
        w.feed(60.0, seen=True, yellow=False)
        self.assertEqual(w.snapshot(60.0).status, core.LONG)

    def test_reading_corrects_the_countdown(self):
        w = self.watch()
        w.feed(0.0, seen=True, seconds=50, yellow=True)
        w.feed(5.0, seen=True, seconds=43, yellow=True)
        self.assertAlmostEqual(w.snapshot(5.0).remaining, 43.5)

    def test_white_to_yellow_anchors_at_sixty(self):
        """黃色數字還讀不出來：用白色變黃色的那一刻當 60 秒。"""
        w = self.watch()
        w.feed(0.0, seen=True, yellow=False)
        w.feed(4.0, seen=True, seconds=None, yellow=True)
        snap = w.snapshot(4.0)
        self.assertEqual(snap.status, core.COUNTING)
        self.assertAlmostEqual(snap.remaining, 58.0)
        w.feed(8.0, seen=True, seconds=None, yellow=True)
        self.assertAlmostEqual(w.snapshot(8.0).remaining, 54.0)

    def test_unreadable_yellow_from_the_start_keeps_searching(self):
        w = self.watch()
        w.feed(0.0, seen=True, seconds=None, yellow=True)
        self.assertEqual(w.snapshot(0.0).status, core.SEARCHING)

    def test_brief_miss_is_ignored(self):
        w = self.watch()
        w.feed(0.0, seen=True, seconds=30, yellow=True)
        w.feed(1.0, seen=False)
        self.assertEqual(w.snapshot(1.0).status, core.COUNTING)

    def test_icon_gone_at_the_end_means_expired(self):
        w = self.watch()
        w.feed(0.0, seen=True, seconds=3, yellow=True)
        w.feed(4.0, seen=False)
        w.feed(5.0, seen=False)
        self.assertEqual(w.snapshot(5.0).status, core.EXPIRED)
        w.feed(6.0, seen=True, yellow=False)        # 重新放了祈禱
        self.assertEqual(w.snapshot(6.0).status, core.LONG)

    def test_icon_gone_mid_way_is_just_lost(self):
        """還剩很多秒就不見了：多半是被擋住，回去找，不警示。"""
        w = self.watch()
        w.feed(0.0, seen=True, seconds=40, yellow=True)
        w.feed(1.0, seen=False)
        w.feed(2.0, seen=False)
        self.assertEqual(w.snapshot(2.0).status, core.SEARCHING)


class TestBridgePrayer(unittest.TestCase):
    def tick(self, session: bridge.WebSession, screen: np.ndarray) -> dict:
        h, w = screen.shape[:2]
        session.feed_frame(rgba_bytes(screen), w, h)
        return json.loads(session.tick())

    def test_off_by_default(self):
        session = bridge.WebSession(require_bracket=False)
        out = self.tick(session, make_screen(seconds=33, grey_from=10))
        self.assertEqual(out["prayer"]["status"], "off")
        self.assertIsNone(out["rois"]["prayer"])

    def test_reads_seconds_once_enabled(self):
        session = bridge.WebSession(require_bracket=False)
        session.set_prayer(True)
        clock = iter([10.0, 10.0, 10.0, 10.0])
        with mock.patch("bridge.time.monotonic", lambda: next(clock)):
            out = self.tick(session, make_screen(seconds=33, grey_from=10))
        self.assertEqual(out["prayer"]["status"], "counting")
        self.assertAlmostEqual(out["prayer"]["remaining"], 33.5)
        self.assertEqual(out["rois"]["prayer"], [1180, 1, 1180 + prayer.ICON_W, 1 + prayer.ICON_H])

    def test_windowed_mode_icon_is_found_and_then_tracked(self):
        session = bridge.WebSession(require_bracket=False)
        session.set_prayer(True)
        screen = make_screen((1000, 106), seconds=33, grey_from=10)
        out = self.tick(session, screen)
        self.assertEqual(out["prayer"]["status"], "counting")
        self.assertEqual(out["rois"]["prayer"][:2], [1000, 106])
        with mock.patch.object(prayer, "_wide_search", side_effect=AssertionError("不該再大範圍找")):
            out = self.tick(session, screen)
        self.assertEqual(out["rois"]["prayer"][:2], [1000, 106])

    def test_wide_search_is_rate_limited(self):
        session = bridge.WebSession(require_bracket=False)
        session.set_prayer(True)
        screen = make_screen(None)
        calls = []
        real = prayer._wide_search
        with mock.patch.object(prayer, "_wide_search", lambda *a: calls.append(1) or real(*a)):
            for now in (100.0, 101.0, 102.0, 100.0 + bridge.PRAYER_WIDE_SEARCH_SEC):
                with mock.patch("bridge.time.monotonic", lambda now=now: now):
                    self.tick(session, screen)
        self.assertEqual(len(calls), 2)

    def test_over_a_minute(self):
        session = bridge.WebSession(require_bracket=False)
        session.set_prayer(True)
        out = self.tick(session, make_screen(seconds=None))
        self.assertEqual(out["prayer"]["status"], "long")
        self.assertIsNone(out["prayer"]["remaining"])

    def test_unknown_digit_goes_to_ocr_and_is_learned(self):
        session = bridge.WebSession(require_bracket=False)
        session.prayer_reader = reader_without("5")
        session.set_ocr_ready(True)
        session.set_prayer(True)
        screen = make_screen(seconds=35, grey_from=10)
        out = self.tick(session, screen)
        requests = [r for r in out["ocr"] if r["id"].startswith("prayer:")]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["kind"], "digits")
        session.ocr_result(requests[0]["id"], json.dumps(["35", "3 5", "35", ""]))
        out = self.tick(session, screen)
        self.assertEqual(out["prayer"]["status"], "counting")
        self.assertEqual(int(out["prayer"]["remaining"]), 35)

    def test_ocr_over_sixty_is_not_trusted(self):
        session = bridge.WebSession(require_bracket=False)
        session.prayer_reader = reader_without("5")
        session.set_ocr_ready(True)
        session.set_prayer(True)
        screen = make_screen(seconds=35, grey_from=10)
        out = self.tick(session, screen)
        rid = [r for r in out["ocr"] if r["id"].startswith("prayer:")][0]["id"]
        session.ocr_result(rid, json.dumps(["85", "85", "85", "85"]))
        self.assertIsNone(session.prayer_reader.read_templates(
            prayer.cut_timer(screen, (1180, 1))))


if __name__ == "__main__":
    unittest.main()
