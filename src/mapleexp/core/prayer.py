"""神聖祈禱的剩餘時間：把每格讀到的圖示狀態變成一個連續的倒數。

畫面只告訴我們兩種事：

* 圖示左下角是白色的分鐘數：還有一分鐘以上，不知道確切秒數。
* 黃色的秒數：真正的剩餘秒數（遊戲是無條件捨去，顯示 56 代表 56.x 秒）。

截圖之間（最長 10 秒一張）由網頁自己往下數，每讀到一次黃色秒數就校正一次。
黃色數字讀不出來（字形還沒學到）時，用「白色剛變成黃色」那一刻當基準：
那時剩下的一定是 60 秒，取上一格與這一格的中間點，誤差不超過半個截圖間隔。

圖示找不到（滑鼠移過去跳出說明、特效或視窗蓋住）不代表結束，倒數照走、不丟掉：
在數秒的話一路數到底，到底了還是找不到，就是 buff 真的沒了（寵物沒有重放），要警示；
一分鐘以上的話，離上次看到還不到一分鐘就維持「一分鐘以上」，超過才回去找。

讀到的秒數不一定可信：串流糊掉時「29」常只切出「2」、字形也可能認錯，信錯一次就會提早警示。
所以每個讀數都要先跟目前的倒數對照（:meth:`PrayerWatch._believe`）：

* 對得上（誤差在基準的容許範圍內）就校正。
* 只讀到十位數（倒數還有兩位數，讀到的一位數剛好是十位數）一律不信。
* 其他對不上的，要連續好幾次、而且彼此一路往下數才改用讀到的（基準本身錯了的時候）。
* 沒有倒數可以對照（剛開始、圖示消失太久）時，要連續兩格讀到彼此對得上的兩位數才信，一位數不信。
"""

from __future__ import annotations

from dataclasses import dataclass

# 黃色秒數第一次出現時的剩餘秒數。
FIRST_YELLOW_SEC = 60.0
# 連續這麼多格找不到圖示才算不見了（一格沒找到常常只是被特效閃過）。
LOST_TICKS = 2
# 讀到的秒數跟讀出來的倒數差 2 秒以上就是讀錯（誤差只有截圖時間的抖動）。
READ_TOLERANCE_SEC = 2.0
# 「白變黃 = 60 秒」推出來的倒數，誤差是兩格間隔的一半（截圖最長 10 秒一張）；至少給這麼多。
GUESS_TOLERANCE_SEC = 6.0
# 對不上的讀數連續這麼多次、而且彼此一路往下數，才改用讀到的。
CONFIRM_READS = 3
# 沒有倒數可以對照時，連續這麼多次對得上的讀數才信。
FIRST_CONFIRM_READS = 2

OFF = "off"             # 沒在追蹤
SEARCHING = "searching" # 正在找圖示，或看到了但還不知道剩幾秒
LONG = "long"           # 一分鐘以上
COUNTING = "counting"   # 不到一分鐘，有倒數（讀到的，或白變黃推的）
EXPIRED = "expired"     # 圖示不見了，而且倒數已經到底


@dataclass(frozen=True)
class PrayerSnapshot:
    status: str
    remaining: float | None     # 這一格當下的剩餘秒數；只有 COUNTING 有


class PrayerWatch:
    """狀態只有 COUNTING 時有倒數（``_anchor``），其他狀態一定是 None。"""

    def __init__(self) -> None:
        self.enabled = False
        self.reset()

    def reset(self) -> None:
        self.status = SEARCHING if self.enabled else OFF
        self._last_seen: float | None = None    # 上次看到圖示的時間
        self._misses = 0                        # 連續幾格沒看到圖示
        self._drop_anchor()

    def _drop_anchor(self) -> None:
        self._anchor: tuple[float, float] | None = None    # (剩餘秒數, 當時的時間)
        self._slack = READ_TOLERANCE_SEC                    # 這個倒數的誤差：讀到的 2 秒，推的看間隔
        self._pending: tuple[float, float] | None = None    # 跟倒數對不上的讀數 (剩餘秒數, 時間)
        self._pending_count = 0                             # 連續幾次讀到彼此對得上的這種讀數

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
            self._lost(now)
            return
        self._misses = 0
        previous, last_seen = self.status, self._last_seen
        self._last_seen = now
        if not yellow:
            self.status = LONG
            self._drop_anchor()
            return
        if previous == LONG:
            # 白色剛變黃色：那一刻剩 60 秒，發生在上一格與這一格之間。先用這個推出來的倒數，
            # 這一格讀到的秒數要跟它對得上才信（「59」只讀到「5」不能直接信，不然一變黃就警示）。
            gap = now - last_seen
            self._drop_anchor()
            self._anchor = (FIRST_YELLOW_SEC - gap / 2, now)
            self._slack = max(GUESS_TOLERANCE_SEC, gap / 2 + 1)
        if seconds is not None and self._believe(now, seconds):
            self._anchor = (seconds + 0.5, now)
            self._slack = READ_TOLERANCE_SEC
            self._pending, self._pending_count = None, 0
        # 黃色卻沒有倒數：一開始（或消失太久後）就是黃色、還沒讀到可信的秒數
        self.status = COUNTING if self._anchor is not None else SEARCHING

    def _lost(self, now: float) -> None:
        self._misses += 1
        if self._misses < LOST_TICKS:
            return
        if self.status == COUNTING:
            # 被擋住而已，倒數照走；數到底（誤差範圍內）還是看不到，就是 buff 沒了
            if self.remaining(now) <= self._slack:
                self.status = EXPIRED
                self._drop_anchor()
        elif self.status == LONG:
            # 上次看到還有一分鐘以上，一分鐘內不可能結束；超過一分鐘不知道走到哪了，回去找
            if now - self._last_seen >= FIRST_YELLOW_SEC:
                self.status = SEARCHING

    def _believe(self, now: float, seconds: int) -> bool:
        """這次讀到的秒數可不可信。"""
        left = self.remaining(now)
        if left is None:
            # 沒有倒數可以對照：一位數多半是只讀到十位數，不信；兩位數要連續兩格對得上
            return seconds >= 10 and self._confirm(now, seconds, FIRST_CONFIRM_READS)
        if self._lost_last_digit(left, seconds):
            return False
        if abs(seconds + 0.5 - left) <= self._slack:
            return True
        return self._confirm(now, seconds, CONFIRM_READS)

    def _confirm(self, now: float, seconds: int, need: int) -> bool:
        """跟倒數對不上（或沒有倒數）的讀數：連續 *need* 次、而且彼此一路往下數才信。
        東一個西一個的誤讀（每次讀錯的字不一樣）不會湊成數。"""
        value = seconds + 0.5
        if self._pending is not None:
            sec, at = self._pending
            same = abs(value - (sec - (now - at))) <= READ_TOLERANCE_SEC
        else:
            same = False
        self._pending_count = self._pending_count + 1 if same else 1
        self._pending = (value, now)
        return self._pending_count >= need

    def _lost_last_digit(self, left: float, seconds: int) -> bool:
        """倒數明明還有兩位數，卻只讀到一個數字、而且剛好是十位數：個位數沒切出來（「29」讀成「2」）。

        串流糊掉時同一個位置常常好幾秒都切不出個位數，連續讀錯也不會變對，所以這種一律不信，
        也不算進「連續對不上」的次數；倒數真的走到個位數時自然就讀得到了。
        """
        if seconds >= 10:
            return False
        low, high = left - 0.5 - self._slack, left - 0.5 + self._slack
        if low < 10:
            return False
        return any(n // 10 == seconds for n in range(int(low), int(high) + 1))

    def snapshot(self, now: float) -> PrayerSnapshot:
        if self.status != COUNTING:
            return PrayerSnapshot(status=self.status, remaining=None)
        return PrayerSnapshot(status=self.status, remaining=max(0.0, self.remaining(now)))
