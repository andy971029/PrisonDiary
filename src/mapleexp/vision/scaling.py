"""遊戲把小畫面放大顯示時，找出原本的大小，讓辨識照原尺寸做。

視窗開得比遊戲的解析度大（實測 2560x1599 的視窗）時，遊戲不是多畫一點，而是把
1366x768 的整張畫面放大 1.874 倍、上下補黑邊。狀態列的字跟著變成 13 像素高，
內建的 7 像素字形一個都比不到，經驗值就找不到。

做法是猜原本的解析度：從狀態列（一定在畫面最下面、橫跨整個寬度）量出遊戲畫面的
左右與下緣，對每個常見解析度算出放大倍率，用最近鄰取樣縮回去，經驗值欄位找得到
就是它。之後每一格都由頁面照同一個框縮好再送進來（``drawImage`` 關掉平滑），
這裡的 ``resample`` 取樣點跟它一樣落在每個輸出像素的中心。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .locate import locate_exp_field
from .templates import TemplateSet

# 遊戲可選的解析度；畫面比它大這麼多倍以上才算是被放大，差一點點的是視窗邊框之類。
LOGICAL_SIZES = ((1366, 768), (1920, 1080), (1280, 720), (1600, 900), (1024, 768))
MIN_SCALE = 1.15

# 黑邊：整列最亮的通道都不超過這個值。
DARK = 16


@dataclass(frozen=True)
class View:
    """原始畫面上的遊戲範圍 (x, y, w, h) 與它原本的大小 (width, height)。"""

    x: float
    y: float
    w: float
    h: float
    width: int
    height: int

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h,
                "width": self.width, "height": self.height}


def content_bottom_edges(bgra: np.ndarray) -> tuple[int, int, int] | None:
    """遊戲畫面的下緣（不含）與左右邊界 (bottom, left, right)；整張都是黑的回傳 None。

    左右看整張畫面：狀態列兩端的邊框是暗的，只看最下面幾列會少算幾個像素，倍率一偏，
    縮回去的字就會在右半邊錯開一格。
    """
    bright = bgra[..., :3].max(axis=2) > DARK
    rows = np.nonzero(bright.any(axis=1))[0]
    cols = np.nonzero(bright.any(axis=0))[0]
    if rows.size == 0:
        return None
    return int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def resample(bgra: np.ndarray, view: View) -> np.ndarray:
    """最近鄰取樣縮回原本大小，取樣點在每個輸出像素的中心（跟瀏覽器關掉平滑的 drawImage 一樣）。"""
    height, width = bgra.shape[:2]
    ys = np.floor(view.y + (np.arange(view.height) + 0.5) * (view.h / view.height)).astype(np.int64)
    xs = np.floor(view.x + (np.arange(view.width) + 0.5) * (view.w / view.width)).astype(np.int64)
    return bgra[np.clip(ys, 0, height - 1)][:, np.clip(xs, 0, width - 1)]


def candidate_views(bgra: np.ndarray) -> list[View]:
    edges = content_bottom_edges(bgra)
    if edges is None:
        return []
    bottom, left, right = edges
    span = right - left
    views = []
    for width, height in LOGICAL_SIZES:
        scale = span / width
        if scale < MIN_SCALE:
            continue
        h = height * scale
        top = bottom - h
        if top < -1:
            continue
        views.append(View(x=float(left), y=float(max(0.0, top)), w=float(span), h=float(h),
                          width=width, height=height))
    return views


def find_view(bgra: np.ndarray, templates: TemplateSet) -> View | None:
    """原始畫面找不到經驗值欄位時呼叫：猜出放大前的大小，縮回去找得到就回傳那個框。"""
    for view in candidate_views(bgra):
        found = locate_exp_field(resample(bgra, view), templates)
        if found is not None and found.confident:
            return view
    return None
