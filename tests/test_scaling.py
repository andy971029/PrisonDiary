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


class TestNameFix(unittest.TestCase):
    """使用者修正名字：同一塊名牌之後直接用修正的名字，OCR 讀成什麼都不管。

    截圖上的角色名是 o婗婗o，Tesseract 不認得「婗」，三個倍率讀成 o媚媚o、o媚如o、o盡媚o。
    """

    WRONG = [["祭司", "祭司", "和 祭司"], ["o 媚 媚 o", "o 媚 如 o", "o 盡 媚 o"]]

    @classmethod
    def setUpClass(cls):
        cls.templates = TemplateSet.load(builtin_templates_path())
        cls.screen = scaled_screen()
        session = bridge.WebSession(templates=cls.templates)
        h, w = cls.screen.shape[:2]
        session.feed_frame(rgba_bytes(cls.screen), w, h)
        cls.small = scaling.resample(cls.screen, scaling.View(**json.loads(session.tick())["view"]))

    def read(self, session, screen) -> str:
        """餵到掛出角色名的 OCR，回那件的 id。"""
        h, w = screen.shape[:2]
        for _ in range(4):
            session.feed_frame(rgba_bytes(screen), w, h)
            for req in json.loads(session.tick()).get("ocr") or []:
                if req["id"].startswith("character:"):
                    return req["id"]
        self.fail("沒有掛角色名的 OCR")

    def session(self) -> bridge.WebSession:
        session = bridge.WebSession(templates=self.templates)
        session.set_ocr_ready(True)
        return session

    def test_wrong_read_then_fix_applies_at_once(self):
        session = self.session()
        rid = self.read(session, self.small)
        session.ocr_result(rid, json.dumps(self.WRONG))
        self.assertEqual(session.character, "o媚媚o")
        self.assertEqual(session.job, "祭司")
        sig = json.loads(session.name_signature())
        self.assertEqual(session.set_name_fixes(json.dumps([{**sig, "name": "o婗婗o"}])), 1)
        self.assertEqual(session.character, "o婗婗o")

    def test_fix_survives_noise_and_beats_ocr(self):
        first = self.session()
        self.read(first, self.small)
        sig = json.loads(first.name_signature())
        noisy = self.small.copy()
        rng = np.random.default_rng(7)
        noisy[..., :3] = np.clip(noisy[..., :3].astype(int) + rng.integers(-3, 4, noisy[..., :3].shape), 0, 255)
        session = self.session()
        session.set_name_fixes(json.dumps([{**sig, "name": "o婗婗o"}]))
        rid = self.read(session, noisy)
        session.ocr_result(rid, json.dumps(self.WRONG))
        self.assertEqual(session.character, "o婗婗o")
        self.assertEqual(session.job, "祭司")

    def test_other_names_do_not_match(self):
        session = self.session()
        self.read(session, self.small)
        mask = session._name_mask
        self.assertTrue(bridge.masks_match(mask, mask))
        other = mask.copy()
        other[:, 8:20] = other[:, 8:20][:, ::-1]          # 換掉一個字
        self.assertFalse(bridge.masks_match(mask, other))
        self.assertFalse(bridge.masks_match(mask, mask[:, 9:]))   # 少了開頭的 o
        packed = bridge.pack_mask(mask)
        self.assertTrue((bridge.unpack_mask(packed) == mask).all())
        self.assertIsNone(bridge.unpack_mask({"w": 5, "h": 5, "bits": "zz"}))
        self.assertIsNone(bridge.unpack_mask({"w": 999, "h": 5, "bits": "00"}))

    def test_bad_fixes_are_ignored(self):
        session = self.session()
        self.assertEqual(session.set_name_fixes(json.dumps([{"w": 1}, {"w": 2, "h": 1, "bits": "c0", "name": " , "}])), 0)


class TestNameVote(unittest.TestCase):
    def test_punctuation_is_dropped_and_scales_vote(self):
        self.assertEqual(bridge.vote_name([",卍米糕玄師寢", "卍米糕玄師卍", "卍 米糕 玄師 卍"]), "卍米糕玄師卍")

    def test_same_length_reads_vote_per_character(self):
        self.assertEqual(bridge.vote_name(["卍米糕玄師寢", "卍米糕玄帥卍", "卍米糕玄師卍"]), "卍米糕玄師卍")

    def test_falls_back_to_longest(self):
        self.assertEqual(bridge.vote_name(["卍弘法艾德卍", "卍弘法艾", ""]), "卍弘法艾德卍")
        self.assertEqual(bridge.vote_name(["", " 。"]), "")


if __name__ == "__main__":
    unittest.main()
