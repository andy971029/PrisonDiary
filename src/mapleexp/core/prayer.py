"""神聖祈禱的剩餘時間：把每格讀到的東西變成一個「結束時刻」。

畫面能告訴我們的只有四種事（``vision/prayer.py`` 的 ``read_timer``）：

* 白色的分鐘數：還有一分鐘以上，不知道確切秒數。
* 黃色的秒數：真正的剩餘秒數（遊戲是無條件捨去，顯示 56 代表 56.x 秒）。
* 圖示在、上面沒有數字：歸零之後圖示還會閃幾秒才消失。
* 找不到圖示：結束了，或只是被擋住（滑鼠移過去跳出說明、特效、別的視窗）。

整個狀態只有一個數：**祈禱在什麼時候結束**。每讀到一次黃色秒數，就等於有人說
「現在再過這麼多秒就結束」，是一張票；最近幾張票裡有夠多張說的是同一個時刻才採用。
讀錯的那一格（29 只讀到 2）算出來的結束時刻跟別張對不上，自然湊不成數，
不必一條一條規則去猜「這個讀數可不可信」。白色剛變成黃色的那一刻剩下的一定是 60 秒，
這也是一張票，而且在讀數還不夠多的頭一兩格先頂著用。

有了結束時刻，看不看得到圖示都一樣往下數：被擋住就照數，時間到了還看不到就是結束了。
沒有數字的圖示只在快結束時才當作「結束了」，它絕對不是「一分鐘以上」。以前把
「沒有黃色數字」一律當成一分鐘以上，祈禱自然結束（寵物沒重放）時倒數會從 0 跳回 1m+，
警示也跟著被重置。現在要明確讀到白色的數字才算一分鐘以上。
"""

from __future__ import annotations

from dataclasses import dataclass

# 每一格讀到什麼（``vision/prayer.py`` 的 ``TimerRead.kind``）。
SECONDS = "seconds"         # 黃色的秒數
MINUTES = "minutes"         # 白色的分鐘數：還有一分鐘以上
BLANK = "blank"             # 圖示上沒有數字：歸零之後、消失之前那幾秒
UNREADABLE = "unreadable"   # 有東西但認不出來（被擋住一半、畫面太糊）

# 黃色秒數第一次出現時的剩餘秒數。
FIRST_YELLOW_SEC = 60.0
# 白色與黃色之間隔了這麼久以上（中間被擋住），就不知道是哪一刻變黃的，不拿來推。截圖最長 10 秒一張。
TURN_MAX_GAP_SEC = 12.0
# 看最近幾張票、其中幾張一致才採用、差多少以內算一致。每張票本身有 ±0.5 秒的誤差
# （遊戲無條件捨去），所以同一個結束時刻的票彼此最多差 1 秒。
VOTES = 5
AGREE = 3
AGREE_SEC = 1.5
# 新算出來的結束時刻跟目前的差不到這麼多就不動它，免得倒數前後跳。往後調（倒數變多）的門檻
# 比往前調高：同一個結束時刻的票最多差 1 秒，差不到 1 秒就往後調只會讓數字跳回去；寧可早一點提醒。
EARLIER_STEADY_SEC = 0.6
LATER_STEADY_SEC = 1.0
# 圖示上沒有數字：離結束這麼近以內，就是已經歸零、正在消失。
BLANK_END_SEC = 5.0
# 歸零後圖示還會閃這麼久才消失。從快結束到這段時間過完，底圖上沒有數字，偶爾會有幾分像某個白色的字；
# 這段時間裡的「白色分鐘數」要像到這個程度才信（真的重放時白字實測 0.87 以上，沒有數字的圖示最高 0.54）。
BLINK_SEC = 8.0
SURE_SCORE = 0.8
# 上次看到的是一分鐘以上：之後看不到圖示，一分鐘內不可能結束；超過就不知道走到哪了。
LONG_HOLD_SEC = 60.0

OFF = "off"             # 沒在追蹤
SEARCHING = "searching" # 正在找圖示，或看到了但還不知道剩幾秒
LONG = "long"           # 一分鐘以上
COUNTING = "counting"   # 不到一分鐘，有倒數
EXPIRED = "expired"     # 結束了：時間到了而且看不到圖示，或圖示上已經沒有數字


@dataclass(frozen=True)
class PrayerSnapshot:
    status: str
    remaining: float | None     # 這一格當下的剩餘秒數；只有 COUNTING 有


class PrayerWatch:
    """狀態是 COUNTING 時一定有結束時刻（``_end``），其他狀態一定沒有。"""

    def __init__(self) -> None:
        self.enabled = False
        self.reset()

    def reset(self) -> None:
        self.status = SEARCHING if self.enabled else OFF
        self._last_long: float | None = None    # 上次讀到白色分鐘數的時間
        self._expired_at: float | None = None   # 什麼時候判定結束的
        self._forget()

    def _forget(self) -> None:
        self._end: float | None = None                  # 估出來的結束時刻
        self._votes: list[tuple[float, float]] = []     # (投票的時間, 它說的結束時刻)，舊的在前

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)
        self.reset()

    def remaining(self, now: float) -> float | None:
        return None if self._end is None else self._end - now

    def feed(self, now: float, seen: bool, kind: str | None = None, seconds: int | None = None,
             score: float = 1.0) -> None:
        """每格呼叫一次。

        ``seen``：找到圖示了沒。``kind``：左下角讀到什麼（上面那四種）。
        ``seconds``：``kind`` 是 SECONDS 時的秒數。``score``：讀到的東西有多像（``TimerRead.score``）。
        """
        if not self.enabled:
            return
        if not seen or kind is None:
            self._unseen(now)
            return
        if kind == MINUTES and self._blinking(now) and score < SURE_SCORE:
            kind = UNREADABLE       # 正在消失的圖示上「好像有白色的字」：不夠像就不算
        if kind == MINUTES:
            self.status = LONG
            self._last_long = now
            self._expired_at = None
            self._forget()
            return
        if self.status == LONG and kind == SECONDS:
            self._turned_yellow(now)
        if kind == SECONDS and seconds is not None:
            self._vote(now, now + seconds + 0.5)
        elif kind == BLANK and self._end is not None and now >= self._end - BLANK_END_SEC:
            self._expire(now)

    def _blinking(self, now: float) -> bool:
        """是不是在「快結束～歸零後圖示閃完」這段時間裡。"""
        if self._end is not None:
            return self._end - BLANK_END_SEC <= now <= self._end + BLINK_SEC
        return self._expired_at is not None and now <= self._expired_at + BLINK_SEC

    def _turned_yellow(self, now: float) -> None:
        """白色剛變黃色：那一刻剩 60 秒，發生在上一次看到白色與這一格之間，取中間點。"""
        gap = now - self._last_long
        self._forget()
        self.status = SEARCHING
        if gap <= TURN_MAX_GAP_SEC:
            self._vote(now, now - gap / 2 + FIRST_YELLOW_SEC)
            self._end = self._votes[-1][1]      # 還沒有別的票可以對照，先用這一張
            self.status = COUNTING

    def _vote(self, now: float, end: float) -> None:
        # 票不會過期：它說的是一個固定的時刻，隔多久都一樣準（10 秒一張時三張票就隔了 20 秒）。
        self._votes = (self._votes + [(now, end)])[-VOTES:]
        agreed = self._agreed()
        if agreed is not None and (self._end is None or agreed < self._end - EARLIER_STEADY_SEC
                                   or agreed > self._end + LATER_STEADY_SEC):
            self._end = agreed
        if self._end is not None:
            self.status = COUNTING
            self._expired_at = None
        elif self.status != EXPIRED:
            self.status = SEARCHING     # 看到黃色了，但還沒有夠多張票

    def _agreed(self) -> float | None:
        """最近的票裡最多張同意的那個結束時刻（取中位數）；不到 ``AGREE`` 張就是 None。"""
        ends = sorted(end for _, end in self._votes)
        best: list[float] = []
        for index, low in enumerate(ends):
            group = [end for end in ends[index:] if end - low <= AGREE_SEC]
            if len(group) > len(best):      # 一樣多張時留比較早的那一組：寧可早一點提醒
                best = group
        if len(best) < AGREE:
            return None
        middle = len(best) // 2
        return best[middle] if len(best) % 2 else (best[middle - 1] + best[middle]) / 2

    def _unseen(self, now: float) -> None:
        if self._end is not None:
            if now >= self._end:        # 被擋住時倒數照走；數到底還是看不到，就是真的結束了
                self._expire(now)
        elif self.status == LONG and now - self._last_long >= LONG_HOLD_SEC:
            self.status = SEARCHING

    def _expire(self, now: float) -> None:
        self.status = EXPIRED
        self._expired_at = now
        self._forget()

    def snapshot(self, now: float) -> PrayerSnapshot:
        if self._end is None:
            return PrayerSnapshot(status=self.status, remaining=None)
        return PrayerSnapshot(status=self.status, remaining=max(0.0, self._end - now))
