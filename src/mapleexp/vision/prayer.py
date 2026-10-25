"""讀畫面右上角的神聖祈禱 buff 圖示，取得剩餘秒數。

buff 圖示一定排在畫面最上面那一排、從右邊往左排；有幾個 buff、誰先誰後會變，
所以位置不寫死，只在「最上面幾十列、右半邊」這條帶狀區域裡找。

**找圖示**：用亮度模板比對，但每一列各自正規化（每列各算一次相關係數再加權平均）。
buff 快結束時圖示會從下往上蓋一層灰，灰掉的部分只是整體變暗、對比變低，輪廓還在，
而且灰色的交界一定是水平的：同一列裡要嘛全灰、要嘛全彩，對每一列來說都只是
「亮度乘一個數再加一個數」，正規化之後就抵消了。左下角的秒數每格都在變，比對時不看。

**讀秒數**：圖示左下角的數字。白色代表「分鐘」（白色 1 = 還有一分多鐘），
剩不到一分鐘才變成黃色的秒數。所以只讀黃色：
沒有黃色數字就是一分鐘以上，有就是真正的剩餘秒數。
黃色非常飽和（藍色分量幾乎是 0），圖示本身楓葉外框的土黃色藍色分量高得多，不會混到。
數字用模板比對讀，內建從實際畫面取下的 3、5、6、9，其他的由 OCR 教（跟楓幣同一招）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .preprocess import to_luma
from .segment import segment
from .templates import TemplateSet

# 只在畫面最上面這麼多列、右邊這個比例的寬度裡找（buff 一定在第一排、從右邊排起）。
SEARCH_ROWS = 48
SEARCH_RIGHT_FRACTION = 0.6
# 模板相關係數門檻。實測祈禱圖示 0.92～1.0（全彩到灰掉大半都是），其他圖示與背景最高約 0.31。
MATCH_MIN_SCORE = 0.6
# 已知位置附近先看一下，對得上就不用整條重找。
TRACK_RADIUS = 2
# 視窗模式：分享整個螢幕或含標題列的視窗時，buff 那一排不在畫面最上面，而是在遊戲視窗的頂端，
# 可能在畫面上半部的任何地方。整片做完整比對太慢，先只用幾列最有特徵的模板粗篩，
# 分數最高的幾個位置再完整比對。這比一般搜尋慢很多，由呼叫端決定多久做一次。
WIDE_SEARCH_FRACTION = 0.5      # 畫面上半部
WIDE_PROBE_ROWS = 4             # 粗篩用幾列模板（只從上半個圖示挑，灰掉時上半部最晚變灰）
WIDE_CANDIDATES = 8
WIDE_MIN_PROBE = 0.45

# 左下角數字區（相對於圖示左上角）：比對圖示時跳過，讀秒數時只看這裡。
DIGIT_TOP, DIGIT_BOTTOM = 17, 30
DIGIT_RIGHT = 16
# 比對時從第幾列開始、左邊幾欄不看（數字蓋住的範圍再寬鬆一點）。
SKIP_FROM_ROW, SKIP_COLS = 19, 15

# 黃色數字的判斷：夠亮、而且紅色明顯高於藍色。原圖的數字約 (210,170,8)，但分享視窗的串流
# 會把顏色洗淡，實測變成 (190,172,91) 左右，所以門檻要放寬。放寬後會連圖示上楓葉的土黃色
# (204,146,88) 一起抓到；不過那只出現在圖示還是全彩、也就是一分鐘以上的時候，
# 這時左下角一定有白色的分鐘數，先看到白色就不算秒數。
YELLOW_MIN_RED = 130
YELLOW_MIN_GREEN = 100
YELLOW_MIN_RED_OVER_BLUE = 60
# 白色分鐘數：三個分量都夠亮而且差不多。灰掉的圖示最亮只到 140 左右。
WHITE_MIN = 190
WHITE_MAX_SPREAD = 40
WHITE_MIN_INK = 6
# 黃色像素少於這個數就當作沒有黃色數字（雜點）。一個數字至少十幾個像素。
YELLOW_MIN_INK = 8
DIGIT_MIN_HEIGHT = 6
MATCH_DIGIT_MIN_SCORE = 0.85
MATCH_DIGIT_MIN_MARGIN = 0.03

# 1920x1080 實際畫面上全彩的祈禱圖示亮度（每格 = 亮度 // 16），30x30。
TEMPLATE_ROWS = (
    "0accccccccccc9ccccccccccccc133",
    "affffffffffd917cfffffffffffb13",
    "ffffffffffd91818cfffffffffffb1",
    "fffffffffd91cad28cffffffffffb1",
    "ffffffffd918dada18cfffffffffb1",
    "ccccffffd91caaad28cffffdcccca1",
    "9999cccc918deadea18cccc9999ca1",
    "9100899c91caeaeae28c9991008ca1",
    "918e200718efaaaefa17100da17ca1",
    "91daeae20daefaefae20daeae27ca1",
    "91dfaaea1dfaeaeaef28eaaef27ca1",
    "918aaafafaf20000dafafaaaa17ca1",
    "91dfffaafa1dfffd18faaffff27ca1",
    "c91dfbaaa1dfffffd19aaaff27cfb1",
    "c919afffb1dfffffd19fffba17cfb1",
    "fd91dffbf2dfffffd1dbfff27cffb1",
    "ffd9109ff2dfffffd1dfb107cfffb1",
    "fffd9910df2dffdd1df2079cffffb1",
    "fffd919afbf2bdd1dbfba17cffffb1",
    "ffc91dffb1000b10009fff27cfffb1",
    "c95609aa1dfffffddd19aa18cfffb1",
    "dcdf8eb1dd1d200b1bd19ff28cffb1",
    "c99e5a1ddd10d2d20bdd19f28cffb1",
    "d86e4e2d2000b1d2000b1db18cffb1",
    "c84e200dffffd1bddddd100008cfb1",
    "c86e4891000000000000899999cfb1",
    "c97e4bca1a1acccc1a18cccccccfb1",
    "876e5799100dfddd1008cfffffffb1",
    "afffffda991dd1dd1899cffffffb13",
    "0422348cca1dd1dd18cccaaaaaa133",
)
TEMPLATE = np.array([[int(c, 16) * 16 + 8 for c in row] for row in TEMPLATE_ROWS], dtype=np.float32)
ICON_H, ICON_W = TEMPLATE.shape

# 從實際畫面取下的黃色數字（8 列高）。同一個數字因為次像素位置不同，會有兩種樣子。
SEED_DIGITS: dict[str, tuple[tuple[str, ...], ...]] = {
    "3": (
        ("####.", "...#.", "...#.", ".###.", "...#.", "...##", "...##", "####."),
        ("####.", "...#.", "...#.", ".##..", "...#.", "...##", "...##", "####."),
    ),
    "5": (
        ("####.", "#....", "#....", "####.", "...##", "...##", "...#.", "####."),
        ("####.", "#....", "#....", "####.", "...##", "....#", "...#.", "####."),
    ),
    "6": (
        (".####", "#....", "#....", "####.", "#..##", "#...#", "#...#", ".###."),
        (".####", "#....", "#....", "####.", "#...#", "#...#", "#..##", ".###."),
    ),
    "9": (
        ("####.", "#..#.", "#...#", "#..##", "#####", "...#.", "...#.", "####."),
    ),
}


def seed_templates() -> TemplateSet:
    templates = TemplateSet()
    for char, variants in SEED_DIGITS.items():
        for rows in variants:
            templates.add(char, np.array([[c == "#" for c in row] for row in rows], dtype=bool))
    return templates


# --------------------------------------------------------------------------- #
# 找圖示
# --------------------------------------------------------------------------- #


def _row_plan() -> list[tuple[int, int, np.ndarray, float, float]]:
    """每一列要比對的範圍：(列, 起始欄, 去平均的模板片段, 片段範數, 權重)。"""
    plan = []
    for r in range(ICON_H):
        c0 = SKIP_COLS if r >= SKIP_FROM_ROW else 0
        t = TEMPLATE[r, c0:] - TEMPLATE[r, c0:].mean()
        norm = float(np.sqrt((t * t).sum()))
        if norm < t.size:       # 幾乎是平的一列，沒有資訊
            continue
        plan.append((r, c0, t, norm, norm / np.sqrt(t.size)))
    return plan


_PLAN = _row_plan()


def score_map(luma: np.ndarray, plan=None) -> np.ndarray:
    """``luma`` 每個位置當圖示左上角時的分數（-1～1）。每一列各自正規化，所以灰掉的部分也對得上。

    ``plan`` 給一部分的模板列就只比那幾列（粗篩用）。
    """
    plan = _PLAN if plan is None else plan
    weight_sum = sum(p[4] for p in plan)
    h, w = luma.shape
    out_h, out_w = h - ICON_H + 1, w - ICON_W + 1
    if out_h <= 0 or out_w <= 0:
        return np.zeros((0, 0), dtype=np.float32)
    luma = luma.astype(np.float64)
    out = np.zeros((out_h, out_w))
    for r, c0, t, norm, weight in plan:
        n = t.size
        for dy in range(out_h):
            row = luma[dy + r, c0 : c0 + out_w + n - 1]
            cs = np.concatenate(([0.0], np.cumsum(row)))
            cs2 = np.concatenate(([0.0], np.cumsum(row * row)))
            s = cs[n:] - cs[:-n]
            var = (cs2[n:] - cs2[:-n]) - s * s / n
            dot = np.correlate(row, t, mode="valid")
            # 畫面這一段幾乎是平的（天空、單色背景）：相關係數沒有意義，當 0。
            ok = var > n * 4.0
            out[dy] += weight * np.where(ok, dot / (np.sqrt(np.where(ok, var, 1.0)) * norm), 0.0)
    return (out / weight_sum).astype(np.float32)


def find_icon(frame: np.ndarray, hint: tuple[int, int] | None = None,
              wide: bool = False) -> tuple[int, int, float] | None:
    """祈禱圖示左上角 (left, top, score)；找不到回傳 None。``hint`` 是上次的位置，先看附近。

    ``wide``：最上面那一排也找不到時，再到畫面上半部找（視窗模式）。比較慢。
    """
    height, width = frame.shape[:2]
    if hint is not None:
        left, top = hint
        x0, y0 = max(0, left - TRACK_RADIUS), max(0, top - TRACK_RADIUS)
        x1 = min(width, left + ICON_W + TRACK_RADIUS)
        y1 = min(height, top + ICON_H + TRACK_RADIUS)
        found = _best(to_luma(frame[y0:y1, x0:x1]), x0, y0)
        if found is not None:
            return found
    x0 = int(width * (1 - SEARCH_RIGHT_FRACTION))
    found = _best(to_luma(frame[: min(height, SEARCH_ROWS), x0:]), x0, 0)
    if found is not None or not wide:
        return found
    return _wide_search(frame, x0)


_PROBE_PLAN = sorted((p for p in _PLAN if p[0] < ICON_H // 2), key=lambda p: -p[4])[:WIDE_PROBE_ROWS]


def _wide_search(frame: np.ndarray, x0: int) -> tuple[int, int, float] | None:
    height = frame.shape[0]
    y1 = max(SEARCH_ROWS, int(height * WIDE_SEARCH_FRACTION))
    luma = to_luma(frame[:y1, x0:])
    probe = score_map(luma, _PROBE_PLAN)
    if probe.size == 0:
        return None
    best = None
    for flat in np.argsort(probe.ravel())[::-1][: WIDE_CANDIDATES]:
        y, x = np.unravel_index(int(flat), probe.shape)
        if probe[y, x] < WIDE_MIN_PROBE:
            break
        ys, xs = max(0, y - 1), max(0, x - 1)
        found = _best(luma[ys : y + ICON_H + 1, xs : x + ICON_W + 1], x0 + xs, ys)
        if found is not None and (best is None or found[2] > best[2]):
            best = found
    return best


def _best(luma: np.ndarray, x0: int, y0: int) -> tuple[int, int, float] | None:
    scores = score_map(luma)
    if scores.size == 0:
        return None
    y, x = np.unravel_index(int(np.argmax(scores)), scores.shape)
    score = float(scores[y, x])
    if score < MATCH_MIN_SCORE:
        return None
    return int(x0 + x), int(y0 + y), score


# --------------------------------------------------------------------------- #
# 讀秒數
# --------------------------------------------------------------------------- #


@dataclass
class TimerZone:
    """圖示左下角的黃色數字。沒有黃色數字（一分鐘以上）時 ``digits`` 是空的。"""

    image: np.ndarray               # 數字區的原圖（BGRA），給 OCR 用
    mask: np.ndarray                # 黃色像素
    digits: list[np.ndarray]

    @property
    def has_seconds(self) -> bool:
        return bool(self.digits)

    @property
    def shape_key(self) -> str:
        return "|".join(f"{d.shape[0]}x{d.shape[1]}:{np.packbits(d).tobytes().hex()}" for d in self.digits)


def yellow_mask(pixels: np.ndarray) -> np.ndarray:
    """黃色數字的像素。``pixels`` 是 BGRA／BGR。"""
    blue = pixels[..., 0].astype(np.int16)
    green = pixels[..., 1].astype(np.int16)
    red = pixels[..., 2].astype(np.int16)
    return (red >= YELLOW_MIN_RED) & (green >= YELLOW_MIN_GREEN) & (red - blue >= YELLOW_MIN_RED_OVER_BLUE)


def white_ink(pixels: np.ndarray) -> int:
    """白色（分鐘數）像素的數量。"""
    channels = pixels[..., :3].astype(np.int16)
    low, high = channels.min(axis=-1), channels.max(axis=-1)
    return int(((low >= WHITE_MIN) & (high - low <= WHITE_MAX_SPREAD)).sum())


def cut_timer(frame: np.ndarray, icon: tuple[int, int]) -> TimerZone | None:
    left, top = icon
    height, width = frame.shape[:2]
    y0, y1 = top + DIGIT_TOP, min(height, top + DIGIT_BOTTOM)
    x0, x1 = max(0, left - 1), min(width, left + DIGIT_RIGHT)
    if y1 <= y0 or x1 <= x0:
        return None
    pixels = frame[y0:y1, x0:x1]
    mask = yellow_mask(pixels)
    if white_ink(pixels) >= WHITE_MIN_INK or int(mask.sum()) < YELLOW_MIN_INK:
        return TimerZone(image=pixels.copy(), mask=mask, digits=[])
    glyphs = segment(mask, min_width=1, min_height=3, split_wide=False, min_ink=3)
    digits = [g.mask for g in glyphs if g.height >= DIGIT_MIN_HEIGHT]
    if not digits:
        return TimerZone(image=pixels.copy(), mask=mask, digits=[])
    return TimerZone(image=pixels.copy(), mask=mask, digits=digits)


def ocr_image(zone: TimerZone) -> np.ndarray:
    """給 OCR 看的版本：白底黑字，只留黃色數字。"""
    image = np.full(zone.mask.shape + (4,), 255, dtype=np.uint8)
    image[zone.mask, :3] = 0
    return image


class TimerReader:
    """模板優先、認不出才請 OCR 教。"""

    def __init__(self, templates: TemplateSet | None = None) -> None:
        self.templates = templates if templates is not None else seed_templates()

    def read_templates(self, zone: TimerZone) -> int | None:
        if not zone.digits:
            return None
        chars = []
        for digit in zone.digits:
            match = self.templates.match(
                digit, min_score=MATCH_DIGIT_MIN_SCORE, min_margin=MATCH_DIGIT_MIN_MARGIN
            )
            if not match.char or not match.char.isdigit():
                return None
            chars.append(match.char)
        value = int("".join(chars))
        return value if value < 60 else None    # 黃色的一定是不到一分鐘的秒數

    def teach(self, zone: TimerZone, value: int) -> int:
        text = str(value)
        if len(text) != len(zone.digits):
            return 0
        return sum(1 for char, mask in zip(text, zone.digits) if self.templates.add(char, mask))
