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
以前找不到兩格就丟掉倒數，之後秒數又讀不出來就一直顯示不出來，真的結束時也不會警示。
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
# 「白變黃 = 60 秒」推出來的倒數，誤差最多半個截圖間隔（截圖最長 10 秒一張）。
GUESS_TOLERANCE_SEC = 6.0

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
        self._odd_reads = 0         # 連續幾次讀到跟倒數對不上、但彼此對得上的秒數
        self._pending: tuple[float, float] | None = None   # 那些對不上的讀數推出來的倒數 (剩餘秒數, 時間)
        self._guess_slack = GUESS_TOLERANCE_SEC             # 推出來的基準誤差有多大
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
            if self.status == COUNTING and left is not None:
                if left <= EXPIRED_MARGIN_SEC:
                    self.status = EXPIRED
                    self._anchor = None
                return                      # 被擋住而已，倒數照走
            if self.status == LONG and self._last_seen is not None and now - self._last_seen < FIRST_YELLOW_SEC:
                return                      # 上次看到還有一分鐘以上，到現在還不可能結束
            if self.status != EXPIRED:
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
            self._pending = None
            return
        if previous == LONG and last_seen is not None:
            # 白色剛變黃色：那一刻剩 60 秒，發生在上一格與這一格之間。先用這個推出來的倒數，
            # 這一格讀到的秒數要跟它對得上才信（「59」只讀到「5」不能直接信，不然一變黃就警示）。
            gap = now - last_seen
            self._anchor = (FIRST_YELLOW_SEC - gap / 2, now)
            self._anchor_read = False
            self._guess_slack = max(GUESS_TOLERANCE_SEC, gap / 2 + 1)
            self._odd_reads = 0
            self._pending = None
            self.status = COUNTING
        if seconds is not None and self._believe(now, seconds):
            self._anchor = (seconds + 0.5, now)
            self._anchor_read = True
        if self._anchor is None:
            # 一開始就是黃色、又讀不出來：不知道確切秒數，先當作還在找。
            self.status = SEARCHING
            return
        self.status = COUNTING

    def _believe(self, now: float, seconds: int) -> bool:
        """這次讀到的秒數可不可信。跟目前的倒數對得上就信；對不上要連續好幾次才信。"""
        left = self.remaining(now)
        if self.status != COUNTING or left is None:
            self._odd_reads, self._pending = 0, None
            return True
        if self._lost_last_digit(left, seconds):
            return False
        slack = READ_TOLERANCE_SEC if self._anchor_read else self._guess_slack
        if abs(seconds + 0.5 - left) <= slack:
            self._odd_reads, self._pending = 0, None
            return True
        # 對不上：要連續好幾次、而且這幾次彼此對得上（一直往下數）才改用讀到的，
        # 東一個西一個的誤讀（每次讀錯的字不一樣）不會湊成數
        if self._pending is not None:
            sec, at = self._pending
            same = abs(seconds + 0.5 - (sec - (now - at))) <= READ_TOLERANCE_SEC
        else:
            same = False
        self._odd_reads = self._odd_reads + 1 if same else 1
        self._pending = (seconds + 0.5, now)
        if self._odd_reads >= CONFIRM_READS:
            self._odd_reads, self._pending = 0, None
            return True
        return False

    def _lost_last_digit(self, left: float, seconds: int) -> bool:
        """倒數明明還有兩位數，卻只讀到一個數字、而且剛好是十位數：個位數沒切出來（「29」讀成「2」）。

        串流糊掉時同一個位置常常好幾秒都切不出個位數，連續讀錯也不會變對，所以這種一律不信，
        也不算進「連續對不上」的次數；倒數真的走到個位數時自然就讀得到了。
        """
        if seconds >= 10:
            return False
        slack = READ_TOLERANCE_SEC if self._anchor_read else self._guess_slack
        low, high = left - 0.5 - slack, left - 0.5 + slack
        if low < 10:
            return False
        return any(n // 10 == seconds for n in range(int(low), int(high) + 1))

    def snapshot(self, now: float) -> PrayerSnapshot:
        if self.status != COUNTING:
            return PrayerSnapshot(status=self.status, remaining=None)
        left = self.remaining(now)
        return PrayerSnapshot(status=self.status, remaining=max(0.0, left) if left is not None else None)
