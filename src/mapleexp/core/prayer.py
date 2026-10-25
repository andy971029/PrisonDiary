"""神聖祈禱的剩餘時間：把每格讀到的圖示狀態變成一個連續的倒數。

畫面只告訴我們兩種事：

* 圖示左下角是白色的分鐘數：還有一分鐘以上，不知道確切秒數。
* 黃色的秒數：真正的剩餘秒數（遊戲是無條件捨去，顯示 56 代表 56.x 秒）。

截圖之間（最長 10 秒一張）由網頁自己往下數，每讀到一次黃色秒數就校正一次。
黃色數字讀不出來（字形還沒學到）時，用「白色剛變成黃色」那一刻當基準：
那時剩下的一定是 60 秒，取上一格與這一格的中間點，誤差不超過半個截圖間隔。

圖示短暫消失（滑鼠移過去跳出說明、特效蓋住）不算結束；連續好幾格都找不到才算。
找不到時倒數已經數到底（或快到底），就是 buff 真的沒了（寵物沒有重放），要警示。
"""

from __future__ import annotations

from dataclasses import dataclass

# 黃色秒數第一次出現時的剩餘秒數。
FIRST_YELLOW_SEC = 60.0
# 連續這麼多格找不到圖示才算不見了。
LOST_TICKS = 2
# 不見的時候倒數還剩不到這麼多秒，就算 buff 結束；否則只是找不到圖示（被擋住、還沒找到）。
EXPIRED_MARGIN_SEC = 5.0
# 讀到的秒數跟倒數差太多（例如「21」只讀到「2」）先不信；連續這麼多次都一樣離譜才改用讀到的。
# 倒數本身是讀出來的時候，誤差只有截圖間隔的抖動，差 2 秒以上就是讀錯。
READ_TOLERANCE_SEC = 2.0
CONFIRM_READS = 3

OFF = "off"             # 沒在追蹤
SEARCHING = "searching" # 正在找圖示
LONG = "long"           # 一分鐘以上
COUNTING = "counting"   # 不到一分鐘，有確切秒數
EXPIRED = "expired"     # 圖示不見了，而且時間已經到了


@dataclass(frozen=True)
class PrayerSnapshot:
    status: str
    remaining: float | None     # 這一格當下的剩餘秒數；只有 COUNTING 有


class PrayerWatch:
    def __init__(self) -> None:
        self.enabled = False
        self.reset()

    def reset(self) -> None:
        self.status = SEARCHING if self.enabled else OFF
        self._anchor: tuple[float, float] | None = None    # (剩餘秒數, 當時的時間)
        self._anchor_read = False   # 基準是真的讀到的秒數（不是用「白變黃 = 60 秒」推的）
        self._odd_reads = 0         # 連續幾次讀到跟倒數對不上的秒數
        self._misses = 0
        self._last_seen: float | None = None

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)
        self.reset()

    def remaining(self, now: float) -> float | None:
        if self._anchor is None:
            return None
        sec, at = self._anchor
        return sec - (now - at)

    def feed(self, now: float, seen: bool, seconds: int | None = None, yellow: bool = False) -> None:
        """每格呼叫一次。

        ``seen``：找到圖示了沒。``yellow``：左下角有沒有黃色數字。
        ``seconds``：讀出來的黃色秒數（讀不出來給 None）。
        """
        if not self.enabled:
            return
        if not seen:
            self._misses += 1
            if self._misses < LOST_TICKS:
                return
            left = self.remaining(now)
            if self.status == COUNTING and left is not None and left <= EXPIRED_MARGIN_SEC:
                self.status = EXPIRED
                self._anchor = None
            elif self.status != EXPIRED:
                self.status = SEARCHING
                self._anchor = None
            return
        self._misses = 0
        previous, last_seen = self.status, self._last_seen
        self._last_seen = now
        if not yellow:
            self.status = LONG
            self._anchor = None
            self._anchor_read = False
            self._odd_reads = 0
            return
        if seconds is not None:
            if self._believe(now, seconds):
                self._anchor = (seconds + 0.5, now)
                self._anchor_read = True
        elif previous == LONG and last_seen is not None:
            # 白色剛變黃色：那一刻剩 60 秒，發生在上一格與這一格之間。
            self._anchor = (FIRST_YELLOW_SEC - (now - last_seen) / 2, now)
        elif self._anchor is None:
            # 一開始就是黃色、又讀不出來：不知道確切秒數，先當作還在找。
            self.status = SEARCHING
            return
        self.status = COUNTING

    def _believe(self, now: float, seconds: int) -> bool:
        """這次讀到的秒數可不可信。跟目前的倒數對得上就信；對不上要連續好幾次才信。"""
        left = self.remaining(now)
        if self.status != COUNTING or left is None or not self._anchor_read:
            self._odd_reads = 0
            return True
        if abs(seconds + 0.5 - left) <= READ_TOLERANCE_SEC:
            self._odd_reads = 0
            return True
        self._odd_reads += 1
        if self._odd_reads >= CONFIRM_READS:
            self._odd_reads = 0
            return True
        return False

    def snapshot(self, now: float) -> PrayerSnapshot:
        if self.status != COUNTING:
            return PrayerSnapshot(status=self.status, remaining=None)
        left = self.remaining(now)
        return PrayerSnapshot(status=self.status, remaining=max(0.0, left) if left is not None else None)
