"""遊戲把 1366x768 放大顯示時，縮回原大小再辨識。

``data/scaled_status_2560.npz`` 是 2560x1599 視窗的實機截圖最下面那一段（狀態列＋下方黑邊）；
上面的場景換成一片灰，上方補回黑邊，大小與位置跟原圖一樣。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import helpers  # noqa: F401
import numpy as np

from mapleexp.autosetup import builtin_templates_path
from mapleexp.vision import scaling
from mapleexp.vision.locate import locate_exp_field
from mapleexp.vision.templates import TemplateSet

DATA = Path(__file__).resolve().parent / "data" / "scaled_status_2560.npz"
WEB = Path(__file__).resolve().parents[1] / "web"
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))

import bridge  # noqa: E402

TOP_BAR = 81        # 原圖上方黑邊的高度


def scaled_screen() -> np.ndarray:
    data = np.load(DATA)
    strip, top, height = data["strip"], int(data["top"]), int(data["height"])
    rgb = np.zeros((height, strip.shape[1], 3), dtype=np.uint8)
    rgb[TOP_BAR:top] = (150, 160, 180)
    rgb[top:] = strip
    bgra = np.empty((height, strip.shape[1], 4), dtype=np.uint8)
    bgra[..., :3] = rgb[..., ::-1]
    bgra[..., 3] = 255
    return bgra


def rgba_bytes(bgra: np.ndarray) -> bytes:
    return np.ascontiguousarray(bgra[..., [2, 1, 0, 3]]).tobytes()


class TestScaling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = TemplateSet.load(builtin_templates_path())
        cls.screen = scaled_screen()

    def test_raw_frame_does_not_locate(self):
        """放大 1.874 倍的字比不到內建字形：這就是要修的問題。"""
        self.assertIsNone(locate_exp_field(self.screen, self.templates))

    def test_finds_1366_view(self):
        view = scaling.find_view(self.screen, self.templates)
        self.assertIsNotNone(view)
        self.assertEqual((view.width, view.height), (1366, 768))
        self.assertEqual((view.x, view.w), (0.0, 2560.0))
        self.assertAlmostEqual(view.y, TOP_BAR, delta=1)

    def test_reads_exp_after_resample(self):
        """包括小數點落在最下面、跟 3 黏在一起的 ``.3``。"""
        view = scaling.find_view(self.screen, self.templates)
        found = locate_exp_field(scaling.resample(self.screen, view), self.templates)
        self.assertEqual(found.text, "2477489[74.30%]")
        self.assertEqual(found.exp_abs, 2477489)
        self.assertAlmostEqual(found.exp_pct, 74.30)

    def test_native_size_has_no_candidates(self):
        """畫面沒比遊戲解析度大，就不猜放大。"""
        self.assertEqual(scaling.candidate_views(np.full((768, 1366, 4), 90, dtype=np.uint8)), [])

    def test_black_frame(self):
        self.assertIsNone(scaling.find_view(np.zeros((300, 400, 4), dtype=np.uint8), self.templates))


class TestBridgeView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = TemplateSet.load(builtin_templates_path())
        cls.screen = scaled_screen()

    def feed(self, session, bgra) -> dict:
        h, w = bgra.shape[:2]
        session.feed_frame(rgba_bytes(bgra), w, h)
        return json.loads(session.tick())

    def test_raw_scaled_frame_reads_on_first_tick(self):
        session = bridge.WebSession(templates=self.templates)
        out = self.feed(session, self.screen)
        self.assertTrue(out["located"], out["message"])
        self.assertEqual(out["exp_abs"], 2477489)
        self.assertEqual((out["view"]["width"], out["view"]["height"]), (1366, 768))
        self.assertEqual(out["frame_size"], [1366, 768])

    def test_page_sends_resampled_frames_afterwards(self):
        """之後頁面照 view 縮好才送：Python 收到的就是 1366x768，照常讀。"""
        session = bridge.WebSession(templates=self.templates)
        out = self.feed(session, self.screen)
        view = scaling.View(**out["view"])
        out = self.feed(session, scaling.resample(self.screen, view))
        self.assertEqual(out["exp_abs"], 2477489)
        self.assertEqual(out["view"]["width"], 1366)

    def test_source_change_drops_view(self):
        session = bridge.WebSession(templates=self.templates)
        self.feed(session, self.screen)
        session.source_changed()
        self.assertIsNone(session.view)
        self.assertIsNone(session.reader)


if __name__ == "__main__":
    unittest.main()
