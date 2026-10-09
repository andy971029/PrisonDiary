"""瀏覽器版的 ``mapleexp.win32.capture`` 替身。

``vision/reader.py`` 與 ``autosetup.py`` 從這個模組匯入 ``CaptureError`` 與 ``Frame``。
桌面版的真正模組一載入就 ``ctypes.WinDLL("user32")``，在 Pyodide 裡會直接炸。
這裡只定義那兩個跟 Windows 無關的型別，欄位與桌面版一字不差，讓辨識層原封不動
就能在瀏覽器裡跑。擷取本身由 JS 的 getDisplayMedia 做，見 ``web/bridge.py``。

這個 Repo 只有網頁版，所以替身直接放在原位；模組路徑沿用桌面版，辨識層才不用改匯入。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class CaptureError(RuntimeError):
    """擷取失敗。訊息會直接顯示給使用者，所以要寫得具體。"""


@dataclass
class Frame:
    """一張 BGRA 影像。"""

    pixels: np.ndarray  # shape (h, w, 4), dtype uint8, 順序為 B G R A
    backend: str

    @property
    def height(self) -> int:
        return int(self.pixels.shape[0])

    @property
    def width(self) -> int:
        return int(self.pixels.shape[1])


class WindowCapturer:
    """只是個名字。``reader.py`` 與 ``autosetup.py`` 的型別註記匯入它；瀏覽器裡
    真正餵畫面的是 ``bridge.FrameCapturer``，所以這個類別不該被建構。"""

    def __init__(self, *_args, **_kwargs) -> None:
        raise CaptureError("瀏覽器版沒有 Win32 擷取器，請改用 bridge.FrameCapturer")
