"""神聖祈禱：找畫面右上角的 buff 圖示、讀左下角的數字、變成連續的倒數。

兩種資料：

* 真實畫面：``tests/data/prayer_samples.npz`` 是用「匯出樣本」錄下來的兩輪倒數（別的技能，
  數字跟祈禱的一樣），每一格都標了正確答案：白色的 1 分鐘、黃色從 59 數到 0、歸零後沒有數字的圖示、
  圖示消失；另一輪在剩 3 秒時重放。讀數字的測試主要靠它，連同把它弄糊、把顏色弄淡之後的樣子。
* 合成畫面：雜訊背景的最上面一排放幾個 buff 圖示，祈禱圖示用內建模板的亮度畫，數字用內建字形畫。
  找圖示與整條流程（bridge）的測試用它。
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
import numpy as np

from mapleexp.core import prayer as core
from mapleexp.core.prayer import BLANK, MINUTES, SECONDS, UNREADABLE, PrayerWatch
from mapleexp.vision import prayer

WEB = Path(__file__).resolve().parents[1] / "web"
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))

import bridge  # noqa: E402
from test_web_bridge import rgba_bytes  # noqa: E402

YELLOW_BGR = (57, 168, 194)     # 分享串流裡黃色秒數的平均顏色
WHITE_BGR = (255, 255, 255)

# --------------------------------------------------------------------------- #
# 真實畫面
# --------------------------------------------------------------------------- #

_DATA = np.load(Path(__file__).resolve().parent / "data" / "prayer_samples.npz")
SAMPLE_ICON = tuple(int(v) for v in _DATA["icon"])      # 圖示左上角在每一小塊裡的位置
KIND_NAMES = (SECONDS, MINUTES, BLANK, "gone")


def real_frames(kind: str | None = None, sample: int | None = None) -> list[dict]:
    """樣本裡的每一格：``pixels`` 是圖示左下角附近那一小塊（BGR），``kind``／``value`` 是正確答案。"""
    out = []
    for i in range(len(_DATA["kind"])):
        frame = dict(pixels=_DATA["pixels"][i], kind=KIND_NAMES[int(_DATA["kind"][i])], value=int(_DATA["value"][i]),
                     sample=int(_DATA["sample"][i]), at=float(_DATA["at"][i]))
        if (kind is None or frame["kind"] == kind) and (sample is None or frame["sample"] == sample):
            out.append(frame)
    return out


def blurred(pixels: np.ndarray, sigma: float) -> np.ndarray:
    """串流糊掉：每個色版各自做高斯模糊。"""
    kernel = np.exp(-0.5 * (np.arange(-3, 4) / sigma) ** 2)
    kernel /= kernel.sum()
    out = pixels.astype(np.float64)
    for axis in (0, 1):
        out = np.apply_along_axis(lambda v: np.convolve(np.pad(v, 3, mode="edge"), kernel, mode="valid"), axis, out)
    return out


def chroma_halved(pixels: np.ndarray, phase: tuple[int, int] = (0, 0)) -> np.ndarray:
    """視訊串流常見的 4:2:0：亮度不變，顏色每 2x2 像素只留一個值。``phase`` 是那些 2x2 格子從哪裡對齊。"""
    px, py = phase
    bgr = pixels.astype(np.float64)
    luma = 0.114 * bgr[..., 0] + 0.587 * bgr[..., 1] + 0.299 * bgr[..., 2]

    def halve(channel: np.ndarray) -> np.ndarray:
        padded = np.pad(channel, ((py, 2), (px, 2)), mode="edge")
        h, w = (padded.shape[0] // 2) * 2, (padded.shape[1] // 2) * 2
        small = padded[:h, :w].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
        big = np.repeat(np.repeat(small, 2, axis=0), 2, axis=1)
        return big[py : py + channel.shape[0], px : px + channel.shape[1]]

    blue, red = luma + halve(bgr[..., 0] - luma), luma + halve(bgr[..., 2] - luma)
    green = (luma - 0.114 * blue - 0.299 * red) / 0.587
    return np.clip(np.dstack([blue, green, red]), 0, 255)


def with_background(frame: dict, mode: str, seed: int = 0) -> np.ndarray:
    """把數字以外的地方換成別的東西：祈禱圖示底下的圖跟樣本裡那個技能不一樣，讀數字不能靠背景。"""
    rng = np.random.default_rng(seed)
    pixels = frame["pixels"].astype(np.float64)
    h, w = pixels.shape[:2]
    keep = np.zeros((h, w), dtype=bool)
    left, top = SAMPLE_ICON
    fill = prayer.digit_alpha(frame["value"]) >= prayer.FILL_MIN_ALPHA
    keep[top + prayer.ZONE_TOP : top + prayer.ZONE_BOTTOM, left + prayer.ZONE_LEFT : left + prayer.ZONE_RIGHT] = prayer._dilate(fill)
    if mode == "noise":
        background = rng.integers(40, 160, (h, w, 1)) + rng.integers(-15, 15, (h, w, 3))
    elif mode == "tan":         # 祈禱圖示上楓葉外框的土黃色，深淺不一
        background = np.array([88.0, 146.0, 204.0]) * rng.uniform(0.5, 1.0, (h, w, 1))
    elif mode == "white":
        background = 250.0 - rng.integers(0, 30, (h, w, 1)) * np.ones(3)
    else:                       # 斜條紋
        stripes = (np.add.outer(np.arange(h), np.arange(w)) // 3) % 2 == 0
        background = np.where(stripes[..., None], [90.0, 70.0, 60.0], [150.0, 135.0, 130.0])
    return np.where(keep[..., None], pixels, background)


def read_real(pixels: np.ndarray, shift: tuple[int, int] = (0, 0)) -> prayer.TimerRead:
    return prayer.read_timer(pixels, (SAMPLE_ICON[0] + shift[0], SAMPLE_ICON[1] + shift[1]))


class TestReadRealFrames(unittest.TestCase):
    def test_samples_cover_every_second(self):
        self.assertEqual({f["value"] for f in real_frames(SECONDS)}, set(range(60)))
        self.assertEqual(len(real_frames(SECONDS)), 117)

    def test_every_yellow_second_is_read_exactly(self):
        for frame in real_frames(SECONDS):
            read = read_real(frame["pixels"])
            self.assertEqual((read.kind, read.value), (SECONDS, frame["value"]))
            self.assertGreater(read.score, 0.9)
            self.assertTrue(read.yellow)

    def test_white_digits_are_minutes(self):
        frames = real_frames(MINUTES)
        self.assertEqual({f["value"] for f in frames}, {1, 3})
        for frame in frames:
            read = read_real(frame["pixels"])
            self.assertEqual((read.kind, read.value), (MINUTES, frame["value"]))

    def test_icon_without_digits_is_blank_not_minutes(self):
        """歸零之後圖示還會閃幾秒、上面沒有數字。以前「沒有黃色數字」一律當成一分鐘以上，倒數會從 0 跳回 1m+。"""
        frames = real_frames(BLANK)
        self.assertEqual(len(frames), 4)
        for frame in frames:
            self.assertEqual(read_real(frame["pixels"]).kind, BLANK)
        for frame in frames:
            for pixels in (blurred(frame["pixels"], 0.8), chroma_halved(blurred(frame["pixels"], 0.7), (1, 1))):
                self.assertIn(read_real(pixels).kind, (BLANK, UNREADABLE))

    def test_icon_found_a_pixel_off_reads_the_same(self):
        for shift in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1)):
            for frame in real_frames(SECONDS):
                read = read_real(frame["pixels"], shift)
                self.assertEqual((read.kind, read.value), (SECONDS, frame["value"]), shift)

    def assert_never_wrong(self, degrade, kind: str = SECONDS, min_read: float = 0.97) -> None:
        """弄糊之後可以讀不出來，但讀出來的一定要對；而且絕大部分要讀得出來。"""
        frames = real_frames(kind)
        read_ok = 0
        for frame in frames:
            read = read_real(degrade(frame))
            if kind == SECONDS and read.kind == SECONDS:
                self.assertEqual(read.value, frame["value"])
            self.assertNotEqual(read.kind, MINUTES if kind == SECONDS else SECONDS)
            read_ok += read.kind == kind
        self.assertGreaterEqual(read_ok / len(frames), min_read)

    def test_blurry_stream_never_reads_a_wrong_number(self):
        for sigma in (0.5, 0.7, 0.9):
            with self.subTest(sigma=sigma):
                self.assert_never_wrong(lambda f: blurred(f["pixels"], sigma))
        with self.subTest(sigma=1.2):
            self.assert_never_wrong(lambda f: blurred(f["pixels"], 1.2), min_read=0.9)

    def test_halved_colour_resolution_never_reads_a_wrong_number(self):
        """色彩只有一半解析度時黃字的顏色會糊開、還會隨圖示落在奇數或偶數像素而不同；形狀只看亮度就不受影響。"""
        for phase in ((0, 0), (1, 0), (0, 1), (1, 1)):
            with self.subTest(phase=phase):
                self.assert_never_wrong(lambda f: chroma_halved(f["pixels"], phase), min_read=1.0)
                self.assert_never_wrong(lambda f: chroma_halved(blurred(f["pixels"], 0.7), phase))

    def test_noise_and_low_contrast(self):
        rng = np.random.default_rng(3)
        self.assert_never_wrong(lambda f: np.clip(f["pixels"] + rng.normal(0, 8, f["pixels"].shape), 0, 255), min_read=1.0)
        self.assert_never_wrong(lambda f: f["pixels"] * 0.7 + 20, min_read=1.0)

    def test_white_minutes_survive_a_blurry_stream(self):
        self.assert_never_wrong(lambda f: blurred(f["pixels"], 0.7), kind=MINUTES, min_read=1.0)
        self.assert_never_wrong(lambda f: chroma_halved(blurred(f["pixels"], 0.7), (1, 1)), kind=MINUTES, min_read=1.0)
        # 再糊下去，一個像素寬的白色「1」就認不出來了：可以讀不出來，但不能當成黃色秒數
        self.assert_never_wrong(lambda f: blurred(f["pixels"], 1.2), kind=MINUTES, min_read=0.0)

    def test_white_digits_on_a_yellowish_background_are_not_seconds(self):
        """祈禱圖示的楓葉外框是土黃色，還沒變灰時白色分鐘數就疊在上面：糊掉之後字會沾到底色，不能因此當成黃色秒數。"""
        for sigma in (0.0, 0.7, 1.0, 1.2):
            for frame in real_frames(MINUTES)[::3]:
                pixels = with_background(frame, "tan")
                read = read_real(blurred(pixels, sigma) if sigma else pixels)
                self.assertNotEqual(read.kind, SECONDS, sigma)
                if sigma <= 0.7:
                    self.assertEqual(read.kind, MINUTES, sigma)

    def test_reading_does_not_depend_on_what_is_behind_the_digits(self):
        # 白底最吃虧：字的本體比背景暗，一糊掉連描邊都被洗白，多半只能放棄不讀，但讀出來的不能錯
        for mode, readable in (("noise", 0.9), ("tan", 0.9), ("stripes", 0.9), ("white", 0.3)):
            with self.subTest(mode=mode):
                self.assert_never_wrong(lambda f: with_background(f, mode), min_read=1.0)
                self.assert_never_wrong(lambda f: chroma_halved(blurred(with_background(f, mode), 0.7), (1, 1)), min_read=readable)

    def test_one_digit_and_two_digits_are_told_apart(self):
        """「5」只看左邊那個字，畫面是「56」時它一樣很像：兩位數的答案差不多像時要取兩位數；
        反過來畫面真的是一位數時，右邊沒有字，任何兩位數的答案都明顯不像。"""
        for frame in real_frames(SECONDS):
            pixels = frame["pixels"]
            left, top = SAMPLE_ICON
            zone = pixels[top + prayer.ZONE_TOP - 1 : top + prayer.ZONE_BOTTOM + 1,
                          left + prayer.ZONE_LEFT - 1 : left + prayer.ZONE_RIGHT + 1].astype(np.float64)
            scores, _ = prayer.answer_scores(0.114 * zone[..., 0] + 0.587 * zone[..., 1] + 0.299 * zone[..., 2])
            gap = float(scores[:10].max() - scores[10:].max())
            if frame["value"] < 10:
                self.assertGreater(gap, prayer.TWO_DIGIT_TIE + 0.05)
            else:
                self.assertLess(gap, prayer.TWO_DIGIT_TIE - 0.05)


# --------------------------------------------------------------------------- #
# 合成畫面
# --------------------------------------------------------------------------- #


def draw_digits(screen: np.ndarray, left: int, top: int, value: int, colour=YELLOW_BGR) -> None:
    """用內建字形把數字畫在圖示左下角：本體是 ``colour``，外面一圈壓暗當描邊。"""
    alpha = prayer.digit_alpha(value)
    fill = alpha >= prayer.FILL_MIN_ALPHA
    ring = prayer._dilate(fill) & ~fill
    region = screen[top + prayer.ZONE_TOP : top + prayer.ZONE_BOTTOM, left + prayer.ZONE_LEFT : left + prayer.ZONE_RIGHT, :3]
    pixels = region.astype(np.float64)
    pixels[ring] *= 0.25
    pixels = pixels * (1 - alpha[..., None]) + np.array(colour, dtype=np.float64) * alpha[..., None]
    region[:] = np.clip(pixels, 0, 255).astype(np.uint8)


def draw_icon(screen: np.ndarray, left: int, top: int, seconds: int | None = None, minutes: int | None = None,
              grey_from: int | None = None) -> None:
    """畫一個祈禱圖示；``grey_from`` 以下的列蓋灰。``seconds`` 畫黃色秒數、``minutes`` 畫白色分鐘數，都不給就沒有數字。"""
    icon = np.repeat(prayer.TEMPLATE[..., None], 3, axis=2)
    # 模板是從一分鐘以上的畫面取的，左下角本來就有一個白色的「1」：先換成沒有字的底圖（斜紋）。
    rows, cols = np.mgrid[19:30, 0:9]
    icon[19:30, 0:9] = (120.0 + 35.0 * ((rows + cols) % 4 < 2))[..., None]
    if grey_from is not None:
        grey = icon[grey_from:]
        icon[grey_from:] = grey * 0.35 + np.array([70.0, 55.0, 45.0])     # 壓暗、偏藍（BGR）
    screen[top : top + prayer.ICON_H, left : left + prayer.ICON_W, :3] = np.clip(icon, 0, 255)
    if seconds is not None:
        draw_digits(screen, left, top, seconds, YELLOW_BGR)
    elif minutes is not None:
        draw_digits(screen, left, top, minutes, WHITE_BGR)


def draw_other_icon(screen: np.ndarray, left: int, top: int, seed: int) -> None:
    """別的 buff：同樣大小的方塊，內容是另一種圖案。"""
    rng = np.random.default_rng(seed)
    blocks = rng.integers(0, 255, size=(6, 6, 3)).astype(np.uint8)
    screen[top : top + 30, left : left + 30, :3] = np.kron(blocks, np.ones((5, 5, 1), dtype=np.uint8))


def make_screen(icon_at: tuple[int, int] | None = (1180, 1), seconds: int | None = None, minutes: int | None = None,
                grey_from: int | None = None, size=(1280, 720), seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    width, height = size
    screen = np.zeros((height, width, 4), dtype=np.uint8)
    screen[..., :3] = rng.integers(20, 200, size=(height, width, 3), dtype=np.uint8)
    screen[..., 3] = 255
    draw_other_icon(screen, width - 33, 1, seed=1)
    draw_other_icon(screen, width - 140, 1, seed=2)
    if icon_at is not None:
        draw_icon(screen, *icon_at, seconds=seconds, minutes=minutes, grey_from=grey_from)
    return screen


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

    def test_digits_do_not_disturb_the_match(self):
        for kwargs in ({"seconds": 58}, {"seconds": 7}, {"minutes": 2}, {}):
            with self.subTest(kwargs=kwargs):
                self.assertGreater(prayer.find_icon(make_screen(grey_from=14, **kwargs))[2], 0.9)

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

    def test_known_row_is_searched_without_the_wide_search(self):
        """圖示不見之後再出現（寵物重放）幾乎都在原來那一列，可能換了位置：先看那一列，不必等大範圍搜尋。"""
        screen = make_screen((900, 106), seconds=33, grey_from=12)
        self.assertIsNone(prayer.find_icon(screen))
        with mock.patch.object(prayer, "_wide_search", side_effect=AssertionError("不該大範圍找")):
            self.assertEqual(prayer.find_icon(screen, row=106)[:2], (900, 106))
            self.assertEqual(prayer.find_icon(screen, row=107, wide=True)[:2], (900, 106))
            self.assertIsNone(prayer.find_icon(make_screen(None), row=106))


class TestReadDrawnDigits(unittest.TestCase):
    """數字畫在祈禱圖示上（底下是祈禱圖示自己的圖，跟真實樣本裡那個技能不一樣）。"""

    def read(self, **kwargs) -> prayer.TimerRead:
        screen = make_screen(**kwargs)
        return prayer.read_timer(screen, prayer.find_icon(screen)[:2])

    def test_reads_every_second_on_the_prayer_icon(self):
        for grey_from in (10, 22, None):        # 灰蓋到數字上面、只蓋到數字中間、完全沒蓋
            for seconds in range(60):
                read = self.read(seconds=seconds, grey_from=grey_from)
                self.assertEqual((read.kind, read.value), (SECONDS, seconds), grey_from)

    def test_white_digits_are_minutes(self):
        for minutes in (1, 2, 3, 10):
            for grey_from in (None, 20):
                read = self.read(minutes=minutes, grey_from=grey_from)
                self.assertEqual((read.kind, read.value), (MINUTES, minutes))

    def test_no_digits_is_never_seconds_or_minutes(self):
        for grey_from in (None, 5, 20):
            self.assertIn(self.read(grey_from=grey_from).kind, (BLANK, UNREADABLE))

    def test_texture_without_digits_is_rarely_digit_like_and_never_seconds(self):
        """沒有數字的底圖偶爾會有幾分像某個數字。黃色秒數還要看顏色，灰階的圖案不會被當成秒數；
        白色分鐘數另外要求「字比描邊亮」，剩下的少數由倒數那一層擋（快結束時要非常像才信）。"""
        rng = np.random.default_rng(5)
        minutes = 0
        for k in range(300):
            cells = rng.integers(20, 240, (13, 16, 1)) if k % 2 else rng.integers(20, 240, (7, 8, 1))
            texture = np.kron(cells, np.ones((26 // cells.shape[0] + 1, 32 // cells.shape[1] + 1, 1)))[:26, :32] * np.ones(3)
            read = read_real(texture)
            self.assertNotEqual(read.kind, SECONDS)
            minutes += read.kind == MINUTES
            if read.kind == MINUTES:
                self.assertLess(read.score, core.SURE_SCORE)
        self.assertLess(minutes / 300, 0.05)

    def test_digits_at_the_edge_of_the_frame(self):
        screen = make_screen((2, 1), seconds=47, grey_from=10)     # 比對範圍左邊超出畫面一欄，用邊緣補
        self.assertEqual(prayer.read_timer(screen, (2, 1)).value, 47)
        self.assertIsNone(prayer.read_timer(screen, (100, screen.shape[0] - 10)))


# --------------------------------------------------------------------------- #
# 倒數
# --------------------------------------------------------------------------- #


class TestPrayerWatch(unittest.TestCase):
    def watch(self) -> PrayerWatch:
        w = PrayerWatch()
        w.set_enabled(True)
        return w

    def counting(self, start: float = 0.0, seconds: int = 40) -> PrayerWatch:
        """已經在倒數：連續三格讀到一路往下的秒數，最後一格在 ``start``、剩 ``seconds``。"""
        w = self.watch()
        for back in (2, 1, 0):
            w.feed(start - back, seen=True, kind=SECONDS, seconds=seconds + back)
        self.assertEqual(w.status, core.COUNTING)
        return w

    def shown(self, w: PrayerWatch, now: float) -> int | None:
        remaining = w.snapshot(now).remaining
        return None if remaining is None else math.floor(remaining)

    def test_off_until_enabled(self):
        w = PrayerWatch()
        w.feed(0.0, seen=True, kind=SECONDS, seconds=30)
        self.assertEqual(w.snapshot(0.0), core.PrayerSnapshot(core.OFF, None))
        w.set_enabled(True)
        self.assertEqual(w.status, core.SEARCHING)

    def test_white_digits_mean_over_a_minute(self):
        w = self.watch()
        w.feed(0.0, seen=True, kind=MINUTES)
        self.assertEqual(w.snapshot(0.0), core.PrayerSnapshot(core.LONG, None))

    def test_white_turning_yellow_starts_at_sixty(self):
        """白色剛變黃色的那一刻剩 60 秒，發生在兩格之間：第一格黃色就有倒數，不用等。"""
        w = self.watch()
        w.feed(10.0, seen=True, kind=MINUTES)
        w.feed(11.0, seen=True, kind=SECONDS, seconds=59)
        self.assertEqual(w.status, core.COUNTING)
        self.assertAlmostEqual(w.snapshot(11.0).remaining, 59.5)

    def test_unreadable_after_white_stays_long_until_a_second_is_read(self):
        """數字讀不出來的那幾格不知道變黃了沒有，維持一分鐘以上；讀到第一個秒數時，
        變黃的那一刻落在「上次看到白色」與「現在」之間，取中間點。"""
        w = self.watch()
        w.feed(10.0, seen=True, kind=MINUTES)
        w.feed(11.0, seen=True, kind=UNREADABLE)
        w.feed(12.0, seen=True, kind=BLANK)
        self.assertEqual(w.status, core.LONG)
        w.feed(14.0, seen=True, kind=SECONDS, seconds=57)
        self.assertEqual(w.status, core.COUNTING)
        self.assertAlmostEqual(w.snapshot(14.0).remaining, 58.0)

    def test_white_digits_on_a_vanishing_icon_must_be_a_sure_match(self):
        """歸零後圖示閃著消失的那幾秒，底圖偶爾會有幾分像白色的字：不夠像就不能當成一分鐘以上。"""
        w = self.counting(0.0, 2)                               # 2.5 秒後結束
        w.feed(3.0, seen=True, kind=MINUTES, score=0.7)
        self.assertEqual(w.status, core.COUNTING)
        w.feed(4.0, seen=False)
        self.assertEqual(w.status, core.EXPIRED)
        w.feed(6.0, seen=True, kind=MINUTES, score=0.7)
        self.assertEqual(w.status, core.EXPIRED)
        w.feed(7.0, seen=True, kind=MINUTES, score=0.9)         # 真的重放了：白字很清楚
        self.assertEqual(w.status, core.LONG)

    def test_blurry_white_digits_count_once_the_icon_would_have_vanished(self):
        """串流很糊的人白字永遠不會「很像」：圖示早該閃完了還看得到白字，就是重放了。"""
        w = self.counting(0.0, 2)
        for now in (3.0, 6.0, 10.0):
            w.feed(now, seen=True, kind=MINUTES, score=0.7)
            self.assertEqual(w.status, core.COUNTING)
        w.feed(11.0, seen=True, kind=MINUTES, score=0.7)        # 結束後超過 8 秒
        self.assertEqual(w.status, core.LONG)
        fresh = self.watch()
        fresh.feed(0.0, seen=True, kind=MINUTES, score=0.7)     # 平常（不在快結束的時候）不必那麼像
        self.assertEqual(fresh.status, core.LONG)

    def test_readings_replace_the_sixty_guess_when_frames_are_far_apart(self):
        """10 秒一張：白變黃發生在那 10 秒裡的哪一刻不知道，推的可以差到 5 秒；讀到的秒數湊夠了就改用讀到的。"""
        w = self.watch()
        w.feed(0.0, seen=True, kind=MINUTES)
        w.feed(10.0, seen=True, kind=SECONDS, seconds=52)
        self.assertAlmostEqual(w.snapshot(10.0).remaining, 55.0)        # 先用推的
        w.feed(20.0, seen=True, kind=SECONDS, seconds=42)
        self.assertAlmostEqual(w.snapshot(20.0).remaining, 45.0)
        w.feed(30.0, seen=True, kind=SECONDS, seconds=32)
        self.assertAlmostEqual(w.snapshot(30.0).remaining, 32.5)        # 三張票一致

    def test_white_then_yellow_after_a_long_gap_is_not_guessed(self):
        w = self.watch()
        w.feed(0.0, seen=True, kind=MINUTES)
        w.feed(30.0, seen=True, kind=SECONDS, seconds=41)
        self.assertEqual(w.snapshot(30.0), core.PrayerSnapshot(core.SEARCHING, None))

    def test_starting_in_yellow_needs_three_agreeing_reads(self):
        w = self.watch()
        w.feed(0.0, seen=True, kind=SECONDS, seconds=36)
        w.feed(1.0, seen=True, kind=SECONDS, seconds=35)
        self.assertEqual(w.snapshot(1.0), core.PrayerSnapshot(core.SEARCHING, None))
        w.feed(2.0, seen=True, kind=SECONDS, seconds=34)
        self.assertEqual(w.status, core.COUNTING)
        self.assertAlmostEqual(w.snapshot(2.0).remaining, 34.5)

    def test_scattered_misreads_never_start_a_countdown(self):
        w = self.watch()
        for now, seconds in ((0.0, 8), (1.0, 15), (2.0, 3), (3.0, 17), (4.0, 44), (5.0, 2)):
            w.feed(now, seen=True, kind=SECONDS, seconds=seconds)
        self.assertEqual(w.status, core.SEARCHING)

    def test_one_misread_does_not_move_the_countdown(self):
        """29 的個位數被滑鼠遮住，只讀到 2：這一張票跟別張對不上，不能讓倒數跳到 2 秒、馬上警示。"""
        w = self.counting(0.0, 30)
        w.feed(1.0, seen=True, kind=SECONDS, seconds=2)
        self.assertAlmostEqual(w.snapshot(1.0).remaining, 29.5)
        w.feed(2.0, seen=True, kind=SECONDS, seconds=28)
        w.feed(3.0, seen=True, kind=SECONDS, seconds=2)
        self.assertAlmostEqual(w.snapshot(3.0).remaining, 27.5)

    def test_repeated_identical_misreads_do_not_agree_with_each_other(self):
        """同一個錯的數字連續讀到好幾次：真的在倒數的話每秒會少一，一直是同一個數字算出來的結束時刻就一路往後跑。"""
        w = self.counting(0.0, 29)
        for now in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0):
            w.feed(now, seen=True, kind=SECONDS, seconds=2)
        self.assertAlmostEqual(w.snapshot(8.0).remaining, 21.5)

    def test_new_readings_win_when_they_keep_agreeing(self):
        """不到一分鐘的祈禱重放：黃色直接跳回比較大的秒數。連續三格都這麼說就改。"""
        w = self.counting(0.0, 3)
        w.feed(1.0, seen=True, kind=SECONDS, seconds=44)
        w.feed(2.0, seen=True, kind=SECONDS, seconds=43)
        self.assertEqual(self.shown(w, 2.0), 1)
        w.feed(3.0, seen=True, kind=SECONDS, seconds=42)
        self.assertAlmostEqual(w.snapshot(3.0).remaining, 42.5)

    def test_jittery_frame_times_do_not_make_the_countdown_jump_back(self):
        w = self.watch()
        w.feed(0.0, seen=True, kind=MINUTES)
        shown, end = [], 60.3
        for k, jitter in enumerate((0.0, 0.3, -0.2, 0.4, 0.1, -0.3, 0.45, 0.0, 0.25, -0.1, 0.35, 0.05) * 4):
            now = 1.0 + k + jitter
            w.feed(now, seen=True, kind=SECONDS, seconds=math.floor(end - now))
            shown.append(self.shown(w, now))
            self.assertLessEqual(abs(shown[-1] - math.floor(end - now)), 1)
        self.assertEqual(shown, sorted(shown, reverse=True))

    def test_hidden_icon_keeps_counting_and_expires_on_time(self):
        """滑鼠移過去跳出說明、被視窗或特效擋住：倒數照走；時間到了還看不到就是結束了。"""
        w = self.counting(0.0, 20)
        for now in (1.0, 5.0, 12.0, 20.0):
            w.feed(now, seen=False)
            self.assertEqual(w.status, core.COUNTING)
        self.assertAlmostEqual(w.snapshot(20.0).remaining, 0.5)
        w.feed(21.0, seen=False)
        self.assertEqual(w.snapshot(21.0), core.PrayerSnapshot(core.EXPIRED, None))

    def test_back_from_hiding_carries_on(self):
        w = self.counting(0.0, 30)
        w.feed(5.0, seen=False)
        w.feed(10.0, seen=True, kind=UNREADABLE)
        self.assertAlmostEqual(w.snapshot(10.0).remaining, 20.5)
        w.feed(11.0, seen=True, kind=SECONDS, seconds=19)
        self.assertAlmostEqual(w.snapshot(11.0).remaining, 19.5)

    def test_icon_without_digits_at_the_end_means_expired(self):
        w = self.counting(0.0, 1)
        w.feed(1.0, seen=True, kind=SECONDS, seconds=0)
        w.feed(2.0, seen=True, kind=BLANK)
        self.assertEqual(w.snapshot(2.0), core.PrayerSnapshot(core.EXPIRED, None))
        for now in (3.0, 4.0):
            w.feed(now, seen=True, kind=BLANK)
        w.feed(5.0, seen=False)
        self.assertEqual(w.status, core.EXPIRED)

    def test_icon_without_digits_mid_way_changes_nothing(self):
        w = self.counting(0.0, 30)
        w.feed(1.0, seen=True, kind=BLANK)
        self.assertAlmostEqual(w.snapshot(1.0).remaining, 29.5)
        searching = self.watch()
        searching.feed(0.0, seen=True, kind=BLANK)
        self.assertEqual(searching.status, core.SEARCHING)

    def test_recast_goes_back_to_over_a_minute(self):
        w = self.counting(0.0, 3)
        w.feed(1.0, seen=True, kind=MINUTES)
        self.assertEqual(w.snapshot(1.0), core.PrayerSnapshot(core.LONG, None))
        expired = self.counting(0.0, 1)
        expired.feed(5.0, seen=False)
        self.assertEqual(expired.status, core.EXPIRED)
        expired.feed(6.0, seen=True, kind=MINUTES)
        self.assertEqual(expired.status, core.LONG)

    def test_new_round_after_expiry_without_white_digits(self):
        w = self.counting(0.0, 1)
        w.feed(5.0, seen=False)
        for now, seconds in ((6.0, 40), (7.0, 39)):
            w.feed(now, seen=True, kind=SECONDS, seconds=seconds)
            self.assertEqual(w.status, core.EXPIRED)
        w.feed(8.0, seen=True, kind=SECONDS, seconds=38)
        self.assertAlmostEqual(w.snapshot(8.0).remaining, 38.5)

    def test_hidden_while_over_a_minute_stays_long_for_a_minute(self):
        w = self.watch()
        w.feed(0.0, seen=True, kind=MINUTES)
        w.feed(59.0, seen=False)
        self.assertEqual(w.status, core.LONG)
        w.feed(60.0, seen=False)
        self.assertEqual(w.status, core.SEARCHING)

    def test_disabling_forgets_everything(self):
        w = self.counting(0.0, 30)
        w.set_enabled(False)
        self.assertEqual(w.snapshot(1.0), core.PrayerSnapshot(core.OFF, None))
        w.set_enabled(True)
        self.assertEqual(w.snapshot(1.0), core.PrayerSnapshot(core.SEARCHING, None))


class TestReplayRealSamples(unittest.TestCase):
    """整輪真實畫面照錄下來的時間餵進去：畫面上顯示的秒數要跟遊戲裡的一樣。"""

    def replay(self, sample: int, degrade=None, hide=lambda k, frame: False):
        watch = PrayerWatch()
        watch.set_enabled(True)
        rows = []
        for k, frame in enumerate(real_frames(sample=sample)):
            seen = frame["kind"] != "gone" and not hide(k, frame)
            read = read_real(degrade(frame["pixels"]) if degrade else frame["pixels"]) if seen else None
            watch.feed(frame["at"], seen=seen, kind=read.kind if read else None,
                       seconds=read.value if read and read.kind == SECONDS else None, score=read.score if read else 1.0)
            snap = watch.snapshot(frame["at"])
            frame["seen"] = seen
            rows.append((frame, snap.status, None if snap.remaining is None else math.floor(snap.remaining)))
        return rows

    def assert_matches_the_game(self, rows) -> None:
        """從第一次看到黃色秒數起，每一格都要有倒數，而且跟遊戲差不到一秒。（樣本的時間是處理完才記的，
        每十秒有一格晚了一秒多，那一格畫面上的數字比記下的時間舊，所以容許差一。）"""
        start = next(k for k, (frame, _, _) in enumerate(rows) if frame["kind"] == SECONDS and frame["seen"])
        for frame, status, shown in rows[start:]:
            if frame["kind"] == SECONDS:
                self.assertEqual(status, core.COUNTING)
                self.assertLessEqual(abs(shown - frame["value"]), 1, (frame["at"], frame["value"], shown))

    def test_recast_at_three_seconds(self):
        rows = self.replay(1)
        self.assert_matches_the_game(rows)
        self.assertEqual([status for _, status, _ in rows[:3]], [core.LONG] * 3)
        first_yellow = next(row for row in rows if row[0]["kind"] == SECONDS)
        self.assertEqual(first_yellow[2], 59)
        self.assertEqual(rows[-1][1], core.LONG)                # 重放之後是白色的 3 分鐘
        exact = sum(shown == frame["value"] for frame, _, shown in rows if frame["kind"] == SECONDS)
        self.assertGreater(exact, 45)

    def test_runs_out_and_expires(self):
        rows = self.replay(2)
        self.assert_matches_the_game(rows)
        after = [status for frame, status, _ in rows if frame["kind"] in (BLANK, "gone")]
        self.assertEqual(set(after), {core.EXPIRED})            # 歸零後不能跳回一分鐘以上
        yellow_on = next(k for k, row in enumerate(rows) if row[0]["kind"] == SECONDS)
        self.assertNotIn(core.LONG, [status for _, status, _ in rows[yellow_on:]])

    def test_blinking_icon_not_found_at_the_end_still_expires(self):
        rows = self.replay(2, hide=lambda k, frame: frame["kind"] == BLANK)
        self.assertEqual({status for frame, status, _ in rows if frame["kind"] in (BLANK, "gone")}, {core.EXPIRED})

    def test_hidden_every_other_frame(self):
        for sample in (1, 2):
            self.assert_matches_the_game(self.replay(sample, hide=lambda k, frame: k % 2 == 1 and frame["kind"] == SECONDS))

    def test_hidden_for_the_last_twenty_seconds(self):
        rows = self.replay(2, hide=lambda k, frame: frame["kind"] == BLANK or (frame["kind"] == SECONDS and frame["value"] < 20))
        self.assert_matches_the_game(rows)
        self.assertEqual(rows[-1][1], core.EXPIRED)

    def test_blurry_stream(self):
        for sample in (1, 2):
            self.assert_matches_the_game(self.replay(sample, degrade=lambda p: chroma_halved(blurred(p, 0.9), (1, 1))))


# --------------------------------------------------------------------------- #
# 整條流程
# --------------------------------------------------------------------------- #


class TestBridgePrayer(unittest.TestCase):
    def tick(self, session: bridge.WebSession, screen: np.ndarray, now: float | None = None) -> dict:
        h, w = screen.shape[:2]
        if now is None:
            session.feed_frame(rgba_bytes(screen), w, h)
            return json.loads(session.tick())
        with mock.patch("bridge.time.monotonic", lambda: now):
            session.feed_frame(rgba_bytes(screen), w, h)
            return json.loads(session.tick())

    def session(self) -> bridge.WebSession:
        session = bridge.WebSession(require_bracket=False)
        session.set_prayer(True)
        return session

    def test_off_by_default(self):
        session = bridge.WebSession(require_bracket=False)
        out = self.tick(session, make_screen(seconds=33, grey_from=10))
        self.assertEqual(out["prayer"]["status"], "off")
        self.assertIsNone(out["prayer"]["read"])
        self.assertIsNone(out["rois"]["prayer"])

    def test_counts_down_from_the_first_yellow_frame(self):
        session = self.session()
        out = self.tick(session, make_screen(minutes=1, grey_from=18), now=9.0)
        self.assertEqual((out["prayer"]["status"], out["prayer"]["remaining"]), ("long", None))
        out = self.tick(session, make_screen(seconds=59, grey_from=15), now=10.0)
        self.assertEqual(out["prayer"]["status"], "counting")
        self.assertAlmostEqual(out["prayer"]["remaining"], 59.5)
        self.assertEqual(out["rois"]["prayer"], [1180, 1, 1180 + prayer.ICON_W, 1 + prayer.ICON_H])

    def test_starting_in_yellow_counts_after_three_reads(self):
        session = self.session()
        for now, seconds in ((9.0, 36), (10.0, 35)):
            out = self.tick(session, make_screen(seconds=seconds, grey_from=10), now=now)
            self.assertEqual(out["prayer"]["status"], "searching")
        out = self.tick(session, make_screen(seconds=34, grey_from=10), now=11.0)
        self.assertEqual(out["prayer"]["status"], "counting")
        self.assertAlmostEqual(out["prayer"]["remaining"], 34.5)

    def test_countdown_uses_the_time_the_frame_arrived(self):
        """辨識偶爾要花一秒多：剩幾秒要照收到畫面的那一刻算，回報時再扣掉處理花掉的時間。"""
        session = self.session()
        self.tick(session, make_screen(minutes=1), now=9.0)
        screen = make_screen(seconds=59, grey_from=15)
        with mock.patch("bridge.time.monotonic", lambda: 10.0):
            session.feed_frame(rgba_bytes(screen), screen.shape[1], screen.shape[0])
        with mock.patch("bridge.time.monotonic", lambda: 11.5):
            out = json.loads(session.tick())
        self.assertAlmostEqual(out["prayer"]["remaining"], 58.0)

    def test_expires_when_the_icon_loses_its_digits(self):
        """歸零之後圖示還在、沒有數字：是結束了，不是回到一分鐘以上。"""
        session = self.session()
        for now, seconds in ((1.0, 2), (2.0, 1), (3.0, 0)):
            out = self.tick(session, make_screen(seconds=seconds, grey_from=1), now=now)
        self.assertEqual(out["prayer"]["status"], "counting")
        out = self.tick(session, make_screen(grey_from=1), now=4.0)
        self.assertEqual(out["prayer"]["status"], "expired")
        out = self.tick(session, make_screen(None), now=5.0)
        self.assertEqual(out["prayer"]["status"], "expired")

    def test_icon_without_digits_is_not_over_a_minute(self):
        out = self.tick(self.session(), make_screen())
        self.assertEqual(out["prayer"]["status"], "searching")
        self.assertIn(out["prayer"]["read"]["kind"], ("blank", "unreadable"))

    def test_payload_carries_this_frames_raw_read(self):
        """匯出樣本要對照「這一格實際讀到什麼」：跟倒數信不信無關，讀到幾就是幾。"""
        session = self.session()
        out = self.tick(session, make_screen(seconds=35, grey_from=10))
        read = out["prayer"]["read"]
        self.assertEqual((read["kind"], read["value"], read["yellow"]), ("seconds", 35, True))
        self.assertGreater(read["score"], prayer.MATCH_MIN_SCORE)
        self.assertGreater(read["match"], prayer.READ_MIN_SCORE)
        self.assertGreater(read["margin"], prayer.READ_MIN_MARGIN)
        self.assertEqual(out["prayer"]["status"], "searching")      # 第一格還不夠票，但讀到的照樣回報
        read = self.tick(session, make_screen(minutes=2))["prayer"]["read"]
        self.assertEqual((read["kind"], read["value"]), ("minutes", 2))
        self.assertIsNone(self.tick(session, make_screen(None))["prayer"]["read"])

    def test_never_asks_ocr_for_the_digits(self):
        """十個數字的字形都內建了，不必在使用者的瀏覽器裡現學。"""
        session = self.session()
        session.set_ocr_ready(True)
        for seconds in (35, 34, 20):
            out = self.tick(session, make_screen(seconds=seconds, grey_from=10))
            self.assertEqual([r["id"] for r in out["ocr"] if "prayer" in r["id"]], [])

    def test_windowed_mode_icon_is_found_and_then_tracked(self):
        session = self.session()
        screen = make_screen((1000, 106), seconds=33, grey_from=10)
        out = self.tick(session, screen)
        self.assertEqual(out["rois"]["prayer"][:2], [1000, 106])
        with mock.patch.object(prayer, "_wide_search", side_effect=AssertionError("不該再大範圍找")):
            out = self.tick(session, screen)
            self.assertEqual(out["rois"]["prayer"][:2], [1000, 106])
        # 圖示不見（祈禱結束），之後在同一列的另一個位置再出現（寵物重放、buff 順序變了）：不必等大範圍搜尋
        self.assertIsNone(self.tick(session, make_screen(None))["rois"]["prayer"])
        with mock.patch.object(prayer, "_wide_search", side_effect=AssertionError("不該再大範圍找")):
            out = self.tick(session, make_screen((900, 106), minutes=1))
            self.assertEqual(out["rois"]["prayer"][:2], [900, 106])
            self.assertEqual(out["prayer"]["status"], "long")

    def test_resized_window_forgets_the_row(self):
        session = self.session()
        self.tick(session, make_screen((1000, 106), minutes=1), now=100.0)
        self.assertEqual(session._prayer_row, 106)
        out = self.tick(session, make_screen((700, 80), minutes=1, size=(1000, 600)), now=101.0)
        self.assertEqual(out["rois"]["prayer"][:2], [700, 80])       # 大小變了：馬上重新大範圍找，不等間隔
        self.assertEqual(session._prayer_row, 80)

    def test_wide_search_backs_off_when_there_is_no_prayer(self):
        """身上沒有祈禱：大範圍搜尋每次都會讓頁面頓一下，連續找不到就越找越少次。"""
        session = self.session()
        frame = make_screen(None)
        calls, counts = [], []
        with mock.patch.object(prayer, "_wide_search", lambda *a: calls.append(1)):
            for now in (100, 104, 105, 110, 115, 124, 125, 144, 145, 174, 175, 204, 205):
                self.assertIsNone(session._find_prayer_icon(frame, float(now)))
                counts.append(len(calls))
        # 100、105、110、115 各找一次（頭三次沒找到之後間隔才開始加倍），然後隔 10、20、30、30 秒
        self.assertEqual(counts, [1, 1, 2, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8])
        # 找到一次就從頭算
        session._find_prayer_icon(make_screen((1000, 3), minutes=1), 206.0)
        self.assertEqual(session._prayer_wide_interval(), bridge.PRAYER_WIDE_SEARCH_SEC)

    def test_over_a_minute(self):
        out = self.tick(self.session(), make_screen(minutes=1))
        self.assertEqual(out["prayer"]["status"], "long")
        self.assertIsNone(out["prayer"]["remaining"])


if __name__ == "__main__":
    unittest.main()
