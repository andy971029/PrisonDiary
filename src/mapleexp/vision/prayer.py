"""讀畫面右上角的神聖祈禱 buff 圖示，取得剩餘時間。

buff 圖示一定排在畫面最上面那一排、從右邊往左排；有幾個 buff、誰先誰後會變，
所以位置不寫死，只在「最上面幾十列、右半邊」這條帶狀區域裡找。

**找圖示**：用亮度模板比對，但每一列各自正規化（每列各算一次相關係數再加權平均）。
buff 快結束時圖示會從下往上蓋一層灰，灰掉的部分只是整體變暗、對比變低，輪廓還在，
而且灰色的交界一定是水平的：同一列裡要嘛全灰、要嘛全彩，對每一列來說都只是
「亮度乘一個數再加一個數」，正規化之後就抵消了。左下角的數字每格都在變，比對時不看。

**讀數字**：圖示左下角的數字。白色代表「分鐘」（白色 1 = 還有一分多鐘），
剩不到一分鐘才變成黃色的秒數；歸零之後圖示還會留幾秒、上面沒有數字，一閃一閃地消失。

數字是固定的點陣字（六欄寬、八列高，有深色描邊），所以不切字、不做文字辨識：
0～59 每個數字在這一小塊裡該長什麼樣，用內建的十個字形排出來，整塊拿去跟畫面比亮度，
看哪一個最像（:func:`read_timer`）。以前的做法是「挑出黃色像素、依空白欄切成一個個字、
逐字比對」，實測真實畫面（原生解析度、很清楚）就有近兩成的格子兩個數字黏成一塊切不開，
畫面一糊還會只切出十位數，「29」變成「2」。整塊比對沒有「切」這一步：
「2」這個答案要求右邊那一格沒有字，右邊有個糊掉的 9 它就不像了。

形狀只看亮度，顏色只用來分辨是白色的分鐘還是黃色的秒數：分享畫面的串流色彩解析度可能只有
亮度的一半，五個像素寬的黃字顏色會糊開，亮度不會。字形取自分享串流的實際畫面，十個數字都內建，
不必像以前那樣在使用者的瀏覽器裡靠文字辨識現學。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..core.prayer import BLANK, MINUTES, SECONDS, UNREADABLE
from .preprocess import to_luma
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

# 比對圖示時從第幾列開始、左邊幾欄不看（左下角的數字蓋住的範圍，再寬鬆一點）。
SKIP_FROM_ROW, SKIP_COLS = 19, 15

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
              wide: bool = False, row: int | None = None) -> tuple[int, int, float] | None:
    """祈禱圖示左上角 (left, top, score)；找不到回傳 None。``hint`` 是上次的位置，先看附近。

    ``row``：上次看到圖示的那一列（左上角的 y）。圖示不見之後再出現（寵物重放）幾乎都在同一列，
    先看那一列，就不用等大範圍搜尋。
    ``wide``：都找不到時，再到畫面上半部找（視窗模式）。比較慢。
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
    if row is not None:
        y0 = max(0, row - TRACK_RADIUS)
        found = _best(to_luma(frame[y0 : min(height, row + ICON_H + TRACK_RADIUS), x0:]), x0, y0)
        if found is not None:
            return found
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
# 讀左下角的數字
# --------------------------------------------------------------------------- #

# 十個數字的字形：每格是那個像素有多少是字（0～f），取自分享串流的實際畫面（1920x1080）。
# 字的本體不透明，外面一圈是深色描邊。
GLYPHS: dict[str, tuple[str, ...]] = {
    "0": ("06eca0", "0f00c4", "3c0089", "4a006b", "6c006b", "4d0088", "1f00d4", "09eeb0"),
    "1": ("05cf00", "015f00", "002f00", "001f00", "001f00", "002f00", "002f00", "0ffffa"),
    "2": ("0dcea0", "0200f1", "0000e2", "0000f0", "000c70", "009a00", "09b000", "0ffff0"),
    "3": ("0dcdb0", "0000e2", "0000f1", "00be60", "0036e1", "0000a8", "0000c7", "0defb0"),
    "4": ("000de0", "007be0", "00e2e0", "0c30e0", "6c03e0", "adcdfb", "0000e0", "0000e0"),
    "5": ("0dfff0", "0d0000", "0f0000", "0ebcd1", "0000c7", "000098", "0000d5", "0defa0"),
    "6": ("02ece0", "0d3000", "1e0000", "3caed2", "5e0089", "2d004c", "1e2089", "06edc2"),
    "7": ("7ffefa", "0000d0", "000870", "000e00", "003c00", "008900", "00b600", "00d700"),
    "8": ("08dbc1", "0f00a4", "0f00a3", "03f8a0", "1c37e1", "5a0079", "6c008a", "2becd2"),
    "9": ("0bcd80", "4c00d3", "690087", "3d00c9", "0aeb99", "000096", "0000e1", "0eee70"),
}
GLYPH_H, GLYPH_W = 8, 6
# 數字靠左排，第二個數字固定往右六欄（相對於圖示左上角的欄）。
DIGIT_LEFT, DIGIT_ADVANCE = 0, 6
# 字的上緣在第幾列：整串裡只要有上下是圓的數字（0、3、5、6、8、9）就高一列，
# 只有 1、2、4、7 時低一列（「11」比「10」低一列）。遊戲是照整串字的外框對齊的。
DIGIT_TOP_ROUND, DIGIT_TOP_FLAT = 20, 21
ROUND_DIGITS = frozenset("035689")
# 比對的範圍（相對於圖示左上角）：字加上描邊，四周再留一點。
ZONE_TOP, ZONE_BOTTOM = 17, 30
ZONE_LEFT, ZONE_RIGHT = -2, 16
ZONE_H, ZONE_W = ZONE_BOTTOM - ZONE_TOP, ZONE_RIGHT - ZONE_LEFT
# 圖示的位置可能差一個像素，每個答案上下左右各挪一格都試，取最像的。
SHIFTS = tuple((dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1))
# 每個答案另外準備一份糊掉的版本：串流糊掉時字的邊緣會暈開，拿清楚的字形去比分數會掉。
TEMPLATE_BLURS = (0.0, 0.8)
# 字的本體（透明度這麼高以上）權重 1，緊貼著的那一圈描邊權重低一點：描邊是半透明的，會透出底下的圖。
FILL_MIN_ALPHA = 0.35
RING_WEIGHT = 0.6

# 最像的答案至少要這麼像才算讀到。實測真實畫面 0.94 以上；模糊加上色彩減半後 0.86 以上；
# 沒有數字的圖示最高 0.53。
READ_MIN_SCORE = 0.62
# 低於這個就是根本沒有數字。
BLANK_MAX_SCORE = 0.5
# 黃色秒數要比同位數裡第二像的高出這麼多才信（實測最接近的是 56 與 55，差 0.07；模糊後 0.044）。
READ_MIN_MARGIN = 0.03
# 兩位數的答案跟一位數的差不多像時，取兩位數：「5」這個答案只看左邊那個字，畫面是「56」時它一樣很像，
# 但畫面真的是一位數時，任何兩位數的答案都會因為右邊沒有字而明顯不像（實測差 0.19 以上）；
# 畫面是兩位數時，兩邊最多差 0.07。
TWO_DIGIT_TIE = 0.11
# 顏色：黃色程度 = (紅 + 綠) / 2 − 藍，取字的本體的平均。黃色秒數實測 78～139，
# 換成各種背景、色彩減半再糊掉最低 35；白色分鐘數是 0 上下，背景偏黃又糊掉時最高 9。
# 比白色還偏藍很多的不是數字（歸零後半透明的圖示，背景的藍天透上來）。
YELLOW_FILL_MIN = 20.0
WHITE_FILL_MAX_TINT = 20.0
# 白色的字要比緊貼著它的描邊亮這麼多（亮度）。沒有數字的底圖偶爾也會有幾分像某個數字，
# 但不會剛好是「亮的字、暗的邊」：真的白字實測差 134 以上，糊掉（0.7）之後 50。
WHITE_MIN_CONTRAST = 40.0
# 數字區裡夠黃又夠亮的像素有這麼多個，就當作有黃色的東西（數字讀不出來時用）。
YELLOW_PIXEL_MIN, YELLOW_PIXEL_MIN_LUMA, YELLOW_PIXELS = 30.0, 90.0, 6


@dataclass(frozen=True)
class TimerRead:
    kind: str
    value: int | None       # 秒數（SECONDS）或分鐘數（MINUTES），其他是 None
    score: float            # 最像的那個答案有多像（-1～1）
    margin: float           # 比同位數裡第二像的高多少
    yellow: bool            # 數字區有沒有黃色


def digit_alpha(value: int) -> np.ndarray:
    """``value``（0～59）在比對範圍裡的樣子：每個像素有多少是字（0～1）。"""
    text = str(value)
    top = DIGIT_TOP_ROUND if ROUND_DIGITS & set(text) else DIGIT_TOP_FLAT
    alpha = np.zeros((ZONE_H, ZONE_W), dtype=np.float32)
    for index, char in enumerate(text):
        glyph = np.array([[int(c, 16) / 15 for c in row] for row in GLYPHS[char]], dtype=np.float32)
        r, c = top - ZONE_TOP, DIGIT_LEFT + index * DIGIT_ADVANCE - ZONE_LEFT
        cell = alpha[r : r + GLYPH_H, c : c + GLYPH_W]
        np.maximum(cell, glyph, out=cell)
    return alpha


def _dilate(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1)
    h, w = mask.shape
    return np.any([padded[dy : dy + h, dx : dx + w] for dy in range(3) for dx in range(3)], axis=0)


def _blur(image: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return image
    kernel = np.exp(-0.5 * (np.arange(-2, 3) / sigma) ** 2)
    kernel /= kernel.sum()
    h, w = image.shape
    padded = np.pad(image, 2, mode="edge")
    rows = sum(kernel[i] * padded[i : i + h] for i in range(5))
    return sum(kernel[i] * rows[:, i : i + w] for i in range(5))


def _build_answers() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """0～59 每個答案的比對資料，一列一個（每個答案有清楚與糊掉兩份，連著排）。

    回傳 (權重, 權重 × 去平均的字形, 字形的變異數, 字的本體)。加權相關係數要的三個和
    都是畫面亮度的線性式，所以六十個答案、九種位移可以用三次矩陣乘法一次算完。
    """
    weights, products, variances, fills = [], [], [], []
    for value in range(60):
        alpha = digit_alpha(value)
        fill = alpha >= FILL_MIN_ALPHA
        weight = np.where(fill, 1.0, np.where(_dilate(fill), RING_WEIGHT, 0.0)).ravel()
        weight /= weight.sum()
        for sigma in TEMPLATE_BLURS:
            shape = _blur(alpha, sigma).ravel()
            centred = shape - float((weight * shape).sum())
            weights.append(weight)
            products.append(weight * centred)
            variances.append(float((weight * centred * centred).sum()))
        fills.append(fill)
    return (np.array(weights, dtype=np.float64), np.array(products, dtype=np.float64),
            np.array(variances, dtype=np.float64), np.array(fills))


_WEIGHTS, _PRODUCTS, _VARIANCES, _FILLS = _build_answers()
_RINGS = np.array([_dilate(fill) & ~fill for fill in _FILLS])      # 緊貼著字的那一圈描邊


def _zone(frame: np.ndarray, icon: tuple[int, int]) -> np.ndarray | None:
    """比對範圍外加一圈（給位移用）的像素，BGR、浮點數。超出畫面的部分用邊緣補。"""
    left, top = icon
    height, width = frame.shape[:2]
    y0, y1 = top + ZONE_TOP - 1, top + ZONE_BOTTOM + 1
    x0, x1 = left + ZONE_LEFT - 1, left + ZONE_RIGHT + 1
    cy0, cy1, cx0, cx1 = max(0, y0), min(height, y1), max(0, x0), min(width, x1)
    if cy1 - cy0 < ZONE_H or cx1 - cx0 < ZONE_W:
        return None
    pixels = frame[cy0:cy1, cx0:cx1, :3].astype(np.float64)
    pad = ((cy0 - y0, y1 - cy1), (cx0 - x0, x1 - cx1), (0, 0))
    return np.pad(pixels, pad, mode="edge") if any(a or b for a, b in pad[:2]) else pixels


def answer_scores(luma: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``luma`` 是比對範圍外加一圈的亮度。回傳 0～59 各自的分數（-1～1），與各自最像時的位移編號。"""
    views = np.stack([luma[1 + dy : 1 + dy + ZONE_H, 1 + dx : 1 + dx + ZONE_W].ravel() for dy, dx in SHIFTS], axis=1)
    mean = _WEIGHTS @ views
    variance = _WEIGHTS @ (views * views) - mean * mean
    covariance = _PRODUCTS @ views
    with np.errstate(divide="ignore", invalid="ignore"):
        scores = covariance / np.sqrt(np.maximum(variance * _VARIANCES[:, None], 1e-9))
    scores = np.where(variance > 4.0, scores, 0.0)      # 這一塊幾乎是平的：相關係數沒有意義
    flat = scores.reshape(60, len(TEMPLATE_BLURS) * len(SHIFTS))
    return flat.max(axis=1), flat.argmax(axis=1) % len(SHIFTS)


def read_timer(frame: np.ndarray, icon: tuple[int, int]) -> TimerRead | None:
    """讀圖示左下角的數字。``frame`` 是 BGRA／BGR，``icon`` 是圖示左上角。範圍超出畫面回傳 None。"""
    pixels = _zone(frame, icon)
    if pixels is None:
        return None
    blue, green, red = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    luma = 0.114 * blue + 0.587 * green + 0.299 * red
    yellowness = (red + green) / 2 - blue
    yellow = int(((yellowness >= YELLOW_PIXEL_MIN) & (luma >= YELLOW_PIXEL_MIN_LUMA)).sum()) >= YELLOW_PIXELS

    scores, shifts = answer_scores(luma)
    one, two = int(np.argmax(scores[:10])), 10 + int(np.argmax(scores[10:]))
    value = two if scores[two] >= scores[one] - TWO_DIGIT_TIE else one
    score = float(scores[value])
    rivals = np.sort(scores[10:] if value >= 10 else scores[:10])
    margin = float(score - rivals[-2])

    if score < BLANK_MAX_SCORE:
        return TimerRead(BLANK, None, score, margin, yellow)
    if score < READ_MIN_SCORE:
        return TimerRead(UNREADABLE, None, score, margin, yellow)
    dy, dx = SHIFTS[int(shifts[value])]
    fill = _FILLS[value]
    here = (slice(1 + dy, 1 + dy + ZONE_H), slice(1 + dx, 1 + dx + ZONE_W))
    tint = float(yellowness[here][fill].mean())
    if tint >= YELLOW_FILL_MIN:
        if margin >= READ_MIN_MARGIN:
            return TimerRead(SECONDS, value, score, margin, True)
        return TimerRead(UNREADABLE, None, score, margin, True)
    contrast = float(luma[here][fill].mean() - luma[here][_RINGS[value]].mean())
    if tint > -WHITE_FILL_MAX_TINT and contrast >= WHITE_MIN_CONTRAST:
        return TimerRead(MINUTES, value, score, margin, yellow)
    return TimerRead(UNREADABLE, None, score, margin, yellow)
