"""楓幣：找背包裡的楓幣框、讀數字、算收益。

畫面是合成的：雜訊背景上放一個「楓幣框 + 楓葉點數框」上下成對的背包底部，
數字用內建的種子字形排，逗號照實際畫面的樣子放在基線下面。十個數字都內建了，所以「沒看過的
字形要交給 OCR」這條路，用拿掉幾個數字的模板來模擬（實際上是壓縮讓字形差了一兩個像素）。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
import numpy as np

from mapleexp.core.meso import CONFIRM_READS, MesoTracker
from mapleexp.vision import meso

WEB = Path(__file__).resolve().parents[1] / "web"
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))

import bridge  # noqa: E402

GLYPHS = meso.SEED_GLYPHS
COMMA = ("##", ".#", "#.")
BOX_W, BOX_H = 104, 14


def glyph(rows) -> np.ndarray:
    return np.array([[c == "#" for c in row] for row in rows], dtype=bool)


def reader_without(chars: str) -> meso.MesoReader:
    """還沒學過 ``chars`` 這幾個數字的讀取器。"""
    templates = meso.seed_templates()
    for char in chars:
        templates.remove_char(char)
    return meso.MesoReader(templates)


def draw_box(screen: np.ndarray, left: int, top: int, text: str) -> tuple[int, int, int, int]:
    """畫一個白底深框、數字靠右的欄位，回傳白色內部的範圍。"""
    screen[top - 1 : top + BOX_H + 1, left - 1 : left + BOX_W + 1, :3] = (40, 40, 40)
    screen[top : top + BOX_H, left : left + BOX_W, :3] = 241
    x = left + BOX_W - 3
    for char in reversed(text):
        mask = glyph(COMMA if char == "," else GLYPHS[char])
        y = top + 2 + (7 if char == "," else 0)
        x -= mask.shape[1]
        screen[y : y + mask.shape[0], x : x + mask.shape[1], :3][mask] = 20
        x -= 1
    return (left, top, left + BOX_W, top + BOX_H)


def make_screen(meso_text: str | None, at=(900, 400), size=(1280, 720), seed=0) -> np.ndarray:
    """背包底部畫在 ``at``（楓幣框左上角）；``meso_text`` 為 None 代表背包沒開。"""
    rng = np.random.default_rng(seed)
    width, height = size
    screen = np.zeros((height, width, 4), dtype=np.uint8)
    screen[..., :3] = rng.integers(20, 200, size=(height, width, 3), dtype=np.uint8)
    screen[..., 3] = 255
    if meso_text is not None:
        left, top = at
        draw_box(screen, left, top, meso_text)
        draw_box(screen, left, top + BOX_H + 6, "0")
    return screen


class TestFindBox(unittest.TestCase):
    def test_finds_meso_box_wherever_the_inventory_is(self):
        """背包可以拖到任何地方：每個位置都要找得到，而且挑的是上面那個（楓幣，不是點數）。"""
        for at in ((40, 30), (900, 400), (600, 650), (1160, 120)):
            with self.subTest(at=at):
                screen = make_screen("1,396,576", at=at)
                self.assertEqual(meso.find_meso_box(screen), (at[0], at[1], at[0] + BOX_W, at[1] + BOX_H))

    def test_no_inventory_means_no_box(self):
        self.assertIsNone(meso.find_meso_box(make_screen(None)))

    def test_single_white_box_is_not_enough(self):
        """只有一個白框（聊天輸入列之類）不算：一定要上下成對。"""
        screen = make_screen(None)
        draw_box(screen, 300, 300, "1,234")
        self.assertIsNone(meso.find_meso_box(screen))

    def test_empty_white_pair_is_not_the_meso_box(self):
        """兩個對齊的空白框（不是背包）不能把位置佔住，真的楓幣框要找得到。"""
        screen = make_screen("1,396,576", at=(900, 400))
        draw_box(screen, 100, 100, "")
        draw_box(screen, 100, 100 + BOX_H + 6, "")
        self.assertEqual(meso.find_meso_box(screen), (900, 400, 900 + BOX_W, 400 + BOX_H))

    def test_box_still_there_notices_the_inventory_closing(self):
        screen = make_screen("1,396,576")
        rect = meso.find_meso_box(screen)
        self.assertTrue(meso.box_still_there(screen, rect))
        self.assertFalse(meso.box_still_there(make_screen(None), rect))


class TestReadDigits(unittest.TestCase):
    def read(self, text: str, reader: meso.MesoReader | None = None):
        screen = make_screen(text)
        zone = meso.cut_zone(screen, meso.find_meso_box(screen))
        return zone, (reader or meso.MesoReader()).read_templates(zone)

    def test_reads_seeded_digits(self):
        zone, value = self.read("1,396,576")
        self.assertEqual(zone.pattern, "D,DDD,DDD")
        self.assertEqual(value, 1396576)

    def test_reads_every_digit(self):
        self.assertEqual(self.read("1,234,567,890")[1], 1234567890)
        self.assertEqual(self.read("2,101,248")[1], 2101248)

    def test_small_and_zero(self):
        self.assertEqual(self.read("0")[1], 0)
        self.assertEqual(self.read("765")[1], 765)

    def test_unknown_digit_is_not_guessed(self):
        """沒學過 8，而 8 長得又像 3／9：認不出來就交給 OCR，絕不猜成別的數字。"""
        zone, value = self.read("1,388,576", reader_without("8"))
        self.assertIsNone(value)
        self.assertTrue(zone.well_formed)

    def test_teach_then_read(self):
        reader = reader_without("248")
        zone, _ = self.read("2,488,105", reader)
        self.assertGreater(reader.teach(zone, 2488105), 0)
        self.assertEqual(self.read("8,420,000", reader)[1], 8420000)

    def test_teach_refuses_wrong_digit_count(self):
        reader = reader_without("8")
        zone, _ = self.read("1,388,576", reader)
        self.assertEqual(reader.teach(zone, 138857), 0)

    def test_comma_is_removed_for_ocr(self):
        screen = make_screen("1,396,576")
        zone = meso.cut_zone(screen, meso.find_meso_box(screen))
        cleaned = meso.ocr_image(zone)
        self.assertEqual(len(zone.comma_masks), 2)
        for comma in zone.comma_masks:
            self.assertTrue((cleaned[comma][:, :3] == 255).all())


class TestMesoTracker(unittest.TestCase):
    def feed(self, tracker: MesoTracker, start: float, values) -> float:
        t = start
        for value in values:
            tracker.feed(t, value)
            t += 1.0
        return t

    def test_net_gain_and_rates(self):
        tracker = MesoTracker(window_sec=600)
        # 每個新值連讀兩次才採用，所以每兩秒 +10；最後一個值也讀滿兩次。
        values = [1000 + 10 * (i // CONFIRM_READS) for i in range(122)]
        self.feed(tracker, 0.0, values)
        snap = tracker.snapshot()
        self.assertEqual(snap.current, values[-1])
        self.assertEqual(snap.cum_net, values[-1] - 1000)
        self.assertAlmostEqual(snap.tracked_sec, 121.0)
        self.assertAlmostEqual(snap.average, (values[-1] - 1000) / 121 * 3600)
        self.assertAlmostEqual(snap.per_hour, snap.average)

    def test_single_odd_read_is_ignored(self):
        """數字跳動那一格可能讀成新舊混雜的值：只出現一次的不採用。"""
        tracker = MesoTracker()
        self.feed(tracker, 0.0, [1000, 1000, 9999, 1000, 1000])
        self.assertEqual(tracker.snapshot().cum_net, 0)
        self.assertEqual(tracker.snapshot().current, 1000)

    def test_spending_counts_as_negative(self):
        tracker = MesoTracker()
        self.feed(tracker, 0.0, [1000, 800, 800])
        self.assertEqual(tracker.snapshot().cum_net, -200)

    def test_short_gap_is_bridged(self):
        """背包被滑鼠擋住幾秒：中間的時間與撿到的錢都要算。"""
        tracker = MesoTracker(bridge_sec=30)
        t = self.feed(tracker, 0.0, [1000, 1000])
        t = self.feed(tracker, t, [None] * 5)
        self.feed(tracker, t, [1500, 1500])
        snap = tracker.snapshot()
        self.assertEqual(snap.cum_net, 500)
        # 0 秒讀到第一筆，8 秒第二次讀到 1500 才採用：中間全部算進去。
        self.assertAlmostEqual(snap.tracked_sec, 8.0)

    def test_closed_inventory_is_not_counted(self):
        """背包關了很久：那段的時間與楓幣變化都不算，回來的第一筆只當新基準。"""
        tracker = MesoTracker(bridge_sec=30)
        t = self.feed(tracker, 0.0, [1000, 1000, 1000])
        t = self.feed(tracker, t, [None] * 100)
        self.feed(tracker, t, [5000, 5000, 5100, 5100])
        snap = tracker.snapshot()
        self.assertEqual(snap.cum_net, 100)
        self.assertAlmostEqual(snap.tracked_sec, 2.0 + 2.0)

    def test_discontinuity_drops_paused_gain(self):
        tracker = MesoTracker()
        t = self.feed(tracker, 0.0, [1000, 1000])
        tracker.mark_discontinuity()
        self.feed(tracker, t + 5, [3000, 3000])
        self.assertEqual(tracker.snapshot().cum_net, 0)

    def test_window_only_counts_recent(self):
        tracker = MesoTracker(window_sec=600)
        # 前 600 秒不動，後 600 秒每秒 +1（每兩格一次 +2）。
        values = [1000] * 600 + [1000 + 2 * (i // 2) for i in range(1, 601)]
        self.feed(tracker, 0.0, values)
        snap = tracker.snapshot()
        self.assertLessEqual(snap.recent_span, 601)
        self.assertAlmostEqual(snap.per_hour, 3600, delta=60)
        self.assertAlmostEqual(snap.average, 1800, delta=60)

    def test_no_rate_before_a_minute(self):
        tracker = MesoTracker()
        self.feed(tracker, 0.0, [1000, 1000, 2000, 2000])
        snap = tracker.snapshot()
        self.assertIsNone(snap.per_hour)
        self.assertIsNone(snap.average)


def rgba_bytes(screen_bgra: np.ndarray) -> bytes:
    return np.ascontiguousarray(screen_bgra[..., [2, 1, 0, 3]]).tobytes()


class TestBridgeMeso(unittest.TestCase):
    def tick(self, session: bridge.WebSession, screen: np.ndarray) -> dict:
        h, w = screen.shape[:2]
        session.feed_frame(rgba_bytes(screen), w, h)
        return json.loads(session.tick())

    def test_payload_reports_meso_without_exp_field(self):
        """楓幣跟經驗值欄位無關：合成畫面沒有狀態列，也要讀得到楓幣。"""
        session = bridge.WebSession(require_bracket=False)
        out = self.tick(session, make_screen("1,396,576"))
        self.assertTrue(out["meso"]["located"])
        self.assertEqual(out["meso"]["current"], "1,396,576")
        self.assertEqual(out["rois"]["meso"], [900, 400, 900 + BOX_W, 400 + BOX_H])

    def test_closed_inventory_status(self):
        session = bridge.WebSession(require_bracket=False)
        out = self.tick(session, make_screen(None))
        self.assertFalse(out["meso"]["located"])
        self.assertIn("背包", out["meso"]["status"])

    def test_follows_inventory_when_dragged(self):
        session = bridge.WebSession(require_bracket=False)
        clock = iter(range(0, 1000, 5))
        with mock.patch("bridge.time.monotonic", lambda: float(next(clock))):
            self.tick(session, make_screen("1,396,576", at=(900, 400)))
            for _ in range(3):
                out = self.tick(session, make_screen("1,396,576", at=(200, 100)))
        self.assertEqual(out["rois"]["meso"][:2], [200, 100])
        self.assertEqual(out["meso"]["current"], "1,396,576")

    def test_unknown_digit_goes_to_ocr_and_is_learned(self):
        session = bridge.WebSession(require_bracket=False)
        session.meso_reader = reader_without("8")
        session.set_ocr_ready(True)
        out = self.tick(session, make_screen("1,388,576"))
        requests = [r for r in out["ocr"] if r["id"].startswith("meso:")]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["kind"], "digits")
        # OCR 讀得到但不認識逗號（數字模型），而且常在數字間補空白。
        session.ocr_result(requests[0]["id"], json.dumps(["1388576", "1 388 576", "1388576", ""]))
        out = self.tick(session, make_screen("1,388,576"))
        self.assertEqual(out["meso"]["current"], "1,388,576")

    def test_disagreeing_ocr_teaches_nothing(self):
        session = bridge.WebSession(require_bracket=False)
        session.meso_reader = reader_without("8")
        session.set_ocr_ready(True)
        out = self.tick(session, make_screen("1,388,576"))
        rid = next(r["id"] for r in out["ocr"] if r["id"].startswith("meso:"))
        session.ocr_result(rid, json.dumps(["1388576", "1366576", "", ""]))
        out = self.tick(session, make_screen("1,388,576"))
        self.assertEqual(out["meso"]["current"], "--")

    def test_reset_clears_meso(self):
        session = bridge.WebSession(require_bracket=False)
        clock = iter(range(0, 1000))
        with mock.patch("bridge.time.monotonic", lambda: float(next(clock))):
            for text in ("1,396,576", "1,396,576", "1,396,579", "1,396,579"):
                out = self.tick(session, make_screen(text))
        self.assertEqual(out["meso"]["total"], "+3")
        session.reset()
        out = self.tick(session, make_screen("1,396,576"))
        self.assertEqual(out["meso"]["total"], "0")


if __name__ == "__main__":
    unittest.main()
