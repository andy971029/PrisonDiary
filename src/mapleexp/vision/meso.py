"""讀背包視窗裡的楓幣。

楓幣欄位是背包底部的一個**純白底、黑字**的長方框（數字靠右、每三位一個逗號），
它正下方緊貼著一個一模一樣寬的「楓葉點數」框。背包可以被拖到畫面任何位置，
所以**不寫死座標**：整張畫面找「上下疊在一起、左右對齊的兩個白框」，取上面那個。
只有一個白框不算：聊天輸入列、NPC 對話這類白底區塊很少剛好成對又等寬，
靠「成對」這個特徵就能排除，不用 OCR 去認背包標題。

數字用模板比對讀。內建從實際畫面取下來的十個數字，認不出的變形由 OCR 教（跟等級同一招，見
``identity.LevelReader``）：認不出的字形掛號給 OCR，多個倍率一致、位數對得上才教給
模板，之後都走快路。逗號不靠模板，靠位置認：它比數字矮、而且落在基線附近。
逗號的位置還順便拿來驗證讀數 —— 必須剛好每三位一個，對不上的這格不信。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .preprocess import to_luma
from .segment import Glyph, segment
from .templates import TemplateSet

# 楓幣框的合理尺寸（實測 1920x1080 的畫面是 104x14）。放寬一點容許不同的 UI 縮放。
BOX_MIN_W = 50
BOX_MAX_W = 260
BOX_MIN_H = 9
BOX_MAX_H = 30
# 兩個框上下之間的空隙（實測 6px，中間是兩條框線）與左右對齊的容許誤差。
PAIR_MAX_GAP = 16
PAIR_ALIGN_TOLERANCE = 2
# 框內至少這麼大比例是白的才算白框（數字只佔一小塊）。
BOX_MIN_WHITE = 0.6
# 白框至少要有這麼多列從左緣白到右緣（數字上下的留白），才算一個完整的框。
BOX_MIN_FULL_ROWS = 2
# 比這短的白色區段不看（背景雜點）。數字很多位時，數字前面的留白只剩一小段，所以不能訂高。
RUN_MIN_W = 4
# 純白的判斷，跟面板標題列同一組數字（panels.WHITE_MIN／WHITE_MAX_SPREAD）。
WHITE_MIN = 230
WHITE_MAX_SPREAD = 12

# 逗號的判斷：頂端比數字頂端低過數字高度的這個比例。
COMMA_TOP_RATIO = 0.45
# 字形比對門檻比等級更嚴：這個字型 5x9，「8」跟「3」只差左邊幾個像素。
# 寧可這格讀不到改問 OCR，也不要把 8 讀成 3。
MATCH_MIN_SCORE = 0.9
MATCH_MIN_MARGIN = 0.03

_GROUPED = re.compile(r"^\d{1,3}(,\d{3})*$")

# 從 1920x1080 實際畫面取下的字形（楓幣 1,396,576、2,101,248 與楓葉點數 0），十個數字都有。
# 壓縮過的畫面字形會差一兩個像素，認不出來的仍然交給 OCR 補教。
SEED_GLYPHS: dict[str, tuple[str, ...]] = {
    "0": (".###.", "##.##", "#...#", "#...#", "#...#", "#...#", "#...#", "##.##", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", "..#..", "..##.", "#####"),
    "2": ("####.", "#..##", "...##", "...##", "...#.", "..##.", ".##..", "##...", "#####"),
    "3": (".###.", "#..##", "...##", "...##", ".###.", "...##", "....#", "#..##", "####."),
    "4": ("....#.", "...##.", "..###.", "..#.#.", ".#..#.", "##..##", ".#####", "....#.", "....#."),
    "5": ("#####", "##...", "#....", "####.", "#..##", "....#", "....#", "#..##", "####."),
    "6": (".####", "##...", "#....", "#....", "#####", "#...#", "#...#", "##..#", ".###."),
    "7": ("#####", "...##", "...#.", "...#.", "..#..", "..#..", "..#..", ".##..", ".##.."),
    "8": (".###.", "##.##", "#...#", "##.##", ".###.", "#..##", "#...#", "#...#", "####."),
    "9": (".###.", "#..##", "#...#", "#...#", "##.##", ".##.#", "....#", "...##", "####."),
}


def seed_templates() -> TemplateSet:
    templates = TemplateSet()
    for char, rows in SEED_GLYPHS.items():
        templates.add(char, np.array([[c == "#" for c in row] for row in rows], dtype=bool))
    return templates


@dataclass
class MesoZone:
    """楓幣框裡切出來的字形。``tokens`` 由左到右，逗號是 ``","``，數字是它的遮罩。"""

    rect: tuple[int, int, int, int]
    image: np.ndarray = field(repr=False)
    tokens: list = field(default_factory=list, repr=False)
    comma_masks: list[np.ndarray] = field(default_factory=list, repr=False)

    @property
    def digits(self) -> list[np.ndarray]:
        return [t for t in self.tokens if not isinstance(t, str)]

    @property
    def pattern(self) -> str:
        """``1,396,576`` 的形狀寫成 ``D,DDD,DDD``，用來驗證逗號位置。"""
        return "".join("," if isinstance(t, str) else "D" for t in self.tokens)

    @property
    def well_formed(self) -> bool:
        return bool(_GROUPED.match(self.pattern.replace("D", "0")))

    @property
    def shape_key(self) -> str:
        """這組字形的指紋：字形沒變就不用重問 OCR。"""
        parts = []
        for token in self.tokens:
            if isinstance(token, str):
                parts.append(",")
            else:
                parts.append(f"{token.shape[0]}x{token.shape[1]}:{np.packbits(token).tobytes().hex()}")
        return "|".join(parts)


# --------------------------------------------------------------------------- #
# 找框
# --------------------------------------------------------------------------- #


def white_mask(frame: np.ndarray) -> np.ndarray:
    """跟 ``panels.white_mask`` 同一個判準，但全程用 uint8 算：整張畫面要掃，快八倍。"""
    blue, green, red = frame[..., 0], frame[..., 1], frame[..., 2]
    lowest = np.minimum(np.minimum(blue, green), red)
    highest = np.maximum(np.maximum(blue, green), red)
    return (lowest >= WHITE_MIN) & ((highest - lowest) <= WHITE_MAX_SPREAD)


def _white_runs(white: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """每一列連續白色的區段（row, start, end 三個陣列）。整張畫面用 numpy 一次算完。"""
    padded = np.zeros((white.shape[0], white.shape[1] + 2), dtype=np.int8)
    padded[:, 1:-1] = white
    edges = np.diff(padded, axis=1)
    starts = np.argwhere(edges == 1)
    ends = np.argwhere(edges == -1)
    # 每一列的開始與結束數量一定相同、順序對應，所以可以直接配對。
    keep = ends[:, 1] - starts[:, 1] >= RUN_MIN_W
    return starts[keep, 0], starts[keep, 1], ends[keep, 1]


def find_white_boxes(frame: np.ndarray) -> list[tuple[int, int, int, int]]:
    """畫面上所有像楓幣框的白色長方框 (left, top, right, bottom)，由上而下。

    框的左緣是一條直線：同一個起點連續出現好幾列，就是同一個框。右緣不能這樣認，
    數字靠右對齊，有字的那幾列白色會在數字前面斷掉，連逗號的尾巴都會切到最底下那列。
    所以右緣取這幾列裡最遠的結束位置（數字上方的留白列一定延伸到底）。
    """
    white = white_mask(frame)
    if not white.any():
        return []
    rows, starts, ends = _white_runs(white)
    if rows.size == 0:
        return []
    order = np.lexsort((rows, starts))
    rows, starts, ends = rows[order], starts[order], ends[order]
    # 起點換了、或列不連續（容許壓縮雜訊吃掉一列），就是另一塊。
    cut = np.flatnonzero((np.diff(starts) != 0) | (np.diff(rows) > 2)) + 1
    heads = np.concatenate(([0], cut))
    sizes = np.diff(np.concatenate((heads, [rows.size])))
    tops = rows[heads]
    bottoms = np.maximum.reduceat(rows, heads) + 1
    rights = np.maximum.reduceat(ends, heads)
    lefts = starts[heads]
    heights, widths = bottoms - tops, rights - lefts

    boxes = []
    for i in np.flatnonzero(
        (heights >= BOX_MIN_H) & (heights <= BOX_MAX_H)
        & (widths >= BOX_MIN_W) & (widths <= BOX_MAX_W)
        & (sizes >= BOX_MIN_H)
    ):
        left, top, right, bottom = int(lefts[i]), int(tops[i]), int(rights[i]), int(bottoms[i])
        full = int(np.count_nonzero(ends[heads[i] : heads[i] + sizes[i]] >= right - 1))
        if full < BOX_MIN_FULL_ROWS or white[top:bottom, left:right].mean() < BOX_MIN_WHITE:
            continue
        boxes.append((left, top, right, bottom))
    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


def find_meso_box(frame: np.ndarray) -> tuple[int, int, int, int] | None:
    """楓幣框的位置；背包沒開（找不到上下成對的白框）回傳 None。

    上面那個框裡還要切得出「每三位一個逗號」的數字才算，不然畫面上剛好有兩個對齊的
    白塊就會把位置佔住，真的楓幣框永遠不會被找。
    """
    boxes = find_white_boxes(frame)
    pairs = []
    for upper in boxes:
        for lower in boxes:
            gap = lower[1] - upper[3]
            if not 0 < gap <= PAIR_MAX_GAP:
                continue
            if abs(lower[0] - upper[0]) > PAIR_ALIGN_TOLERANCE or abs(lower[2] - upper[2]) > PAIR_ALIGN_TOLERANCE:
                continue
            if abs((lower[3] - lower[1]) - (upper[3] - upper[1])) > PAIR_ALIGN_TOLERANCE:
                continue
            pairs.append((gap, upper))
    for _gap, upper in sorted(pairs):
        zone = cut_zone(frame, upper)
        if zone is not None and zone.well_formed:
            return upper
    return None


def box_still_there(frame: np.ndarray, rect: tuple[int, int, int, int]) -> bool:
    """記住的位置上還是不是那個白框（背包被關掉或拖走就不是了）。每格都要做，所以只看這一小塊。"""
    left, top, right, bottom = rect
    height, width = frame.shape[:2]
    if right > width or bottom > height:
        return False
    white = white_mask(frame[top:bottom, left:right])
    # 最上面那列是數字上方的留白，一定整列白；整塊的白色比例也要夠。
    return bool(white[0].mean() >= 0.95 and white.mean() >= BOX_MIN_WHITE)


# --------------------------------------------------------------------------- #
# 切字形
# --------------------------------------------------------------------------- #


def ink_mask(box_pixels: np.ndarray) -> np.ndarray | None:
    """白底黑字：比白底暗一截的就是字。框內幾乎全白（沒有字）時回傳 None。"""
    luma = to_luma(box_pixels).astype(np.int16)
    low, high = int(luma.min()), int(luma.max())
    if high - low < 80:
        return None
    return luma <= (low + high) // 2


def cut_zone(frame: np.ndarray, rect: tuple[int, int, int, int]) -> MesoZone | None:
    """把楓幣框切成數字與逗號。內容不像數字（例如被游標蓋到）回傳 None。"""
    left, top, right, bottom = rect
    pixels = frame[top:bottom, left:right]
    mask = ink_mask(pixels)
    if mask is None:
        return None
    glyphs = segment(mask, min_width=1, min_height=2, split_wide=False, min_ink=2)
    if not glyphs:
        return None
    # 數字的高度與寬度基準取最高的那些字形。黏在一起的數字（壓縮糊掉）照寬度等分切開。
    digit_h = max(g.height for g in glyphs)
    tall = [g for g in glyphs if g.height >= digit_h - 1]
    typical = sorted(g.width for g in tall)[len(tall) // 2]
    if len(tall) >= 3 and any(g.width >= typical * 1.8 for g in tall):
        glyphs = segment(mask, min_width=1, min_height=2, expected_width=typical, min_ink=2)
    digit_top = min(g.top for g in tall)

    tokens: list = []
    commas: list[np.ndarray] = []
    for glyph in glyphs:
        if glyph.top - digit_top >= digit_h * COMMA_TOP_RATIO:
            tokens.append(",")
            commas.append(_comma_pixels(mask, glyph))
        elif glyph.height >= digit_h - 2:
            tokens.append(glyph.mask)
        else:
            return None     # 又不像數字又不像逗號：被游標或別的東西蓋到了
    return MesoZone(rect=rect, image=pixels.copy(), tokens=tokens, comma_masks=commas)


def _comma_pixels(mask: np.ndarray, glyph: Glyph) -> np.ndarray:
    full = np.zeros_like(mask)
    full[glyph.top : glyph.bottom, glyph.left : glyph.right] = glyph.mask
    return full


def ocr_image(zone: MesoZone) -> np.ndarray:
    """給 OCR 看的版本：把逗號塗白。數字模型沒有逗號可選，留著會被硬讀成某個數字。"""
    image = zone.image.copy()
    for comma in zone.comma_masks:
        image[comma] = 255
    return image


# --------------------------------------------------------------------------- #
# 讀值
# --------------------------------------------------------------------------- #


class MesoReader:
    """模板優先、認不出才請 OCR 教。"""

    def __init__(self, templates: TemplateSet | None = None) -> None:
        self.templates = templates if templates is not None else seed_templates()

    def read_templates(self, zone: MesoZone) -> int | None:
        if not zone.digits or not zone.well_formed:
            return None
        chars = []
        for digit in zone.digits:
            match = self.templates.match(digit, min_score=MATCH_MIN_SCORE, min_margin=MATCH_MIN_MARGIN)
            if not match.char or not match.char.isdigit():
                return None
            chars.append(match.char)
        return int("".join(chars))

    def teach(self, zone: MesoZone, value: int) -> int:
        """已知數值時把這組字形學起來；位數對不上就不教（切出來的跟 OCR 看到的不是同一回事）。"""
        text = str(value)
        digits = zone.digits
        if len(text) != len(digits) or not zone.well_formed:
            return 0
        return sum(1 for char, mask in zip(text, digits) if self.templates.add(char, mask))
