"""楓幣收益的統計。

跟經驗值的 ``Tracker`` 同一個想法（累計淨增量、最近一段時間窗的速率、整場平均），
但簡單得多：沒有活躍／閒置、沒有升級與死亡。楓幣只有在背包開著時才讀得到，
所以「時間」只算讀得到的時候：

* 短暫讀不到（滑鼠滑過、特效蓋住、背包被拖動）：跟經驗值的「短暫遮蔽照算」一樣，
  回來時把中間的時間與楓幣變化都補進去，不然那幾秒撿的錢會不見。
* 讀不到超過 ``bridge_sec``（背包關了）：那段時間不算，回來的第一筆只當新基準。
  背包關著的時候不知道錢是怎麼變的，算進去只會讓速率失真。

花錢（買藥水）會讓累計變少，這是刻意的：要的是「淨」收益。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

# 同一個新數值要連續讀到幾次才採用。數字跳動的那一格，壓縮過的畫面可能新舊字形混在一起。
CONFIRM_READS = 2
# 有計入時間就算速率，不等暖身：剛開始幾秒撿到一包錢，換算成每小時會偏高，但很快就會平均掉。
MIN_SPAN_SEC = 0.0


@dataclass(frozen=True)
class MesoSnapshot:
    current: int | None         # 目前楓幣（背包讀到的值）
    visible: bool               # 這一格讀不讀得到
    cum_net: int                # 累計淨增量
    tracked_sec: float          # 計入的時間
    recent_gain: int | None     # 時間窗內的淨增量
    recent_span: float          # 時間窗實際涵蓋的秒數
    per_hour: float | None      # 依時間窗推估的每小時
    average: float | None       # 整場平均的每小時


class MesoTracker:
    def __init__(self, window_sec: float = 600.0, bridge_sec: float = 30.0) -> None:
        self.window_sec = float(window_sec)
        self.bridge_sec = float(bridge_sec)
        self.reset()

    def reset(self) -> None:
        self.current: int | None = None
        self.visible = False
        self.cum_net = 0
        self.tracked_sec = 0.0
        self._base: tuple[int, float] | None = None       # (數值, 時間)
        self._candidate: tuple[int, int] | None = None    # (數值, 已連續讀到幾次)
        # (計入時間, 累計) 的軌跡，給時間窗用。保留一個比時間窗更早的點當起點。
        self._trail: deque[tuple[float, int]] = deque()

    def mark_discontinuity(self) -> None:
        """暫停／重新分享：下一筆只當新基準，空窗期間的時間與楓幣都不算。"""
        self._base = None
        self._candidate = None

    def feed(self, now: float, value: int | None) -> None:
        """每格呼叫一次。``value`` 是這格讀到的楓幣，讀不到給 None。"""
        if value is None:
            self.visible = False
            self._candidate = None
            return
        self.visible = True
        base = self._base
        if base is not None and value != base[0]:
            count = self._candidate[1] + 1 if self._candidate and self._candidate[0] == value else 1
            self._candidate = (value, count)
            if count < CONFIRM_READS:
                return
        self._candidate = None
        self.current = value
        if base is None:
            self._base = (value, now)
            self._mark()
            return
        gap = now - base[1]
        self._base = (value, now)
        if gap > self.bridge_sec or gap < 0:
            return      # 背包關了一陣子：那段不算，這筆是新基準
        self.tracked_sec += gap
        self.cum_net += value - base[0]
        self._mark()

    def _mark(self) -> None:
        self._trail.append((self.tracked_sec, self.cum_net))
        cutoff = self.tracked_sec - self.window_sec
        while len(self._trail) >= 2 and self._trail[1][0] <= cutoff:
            self._trail.popleft()

    def snapshot(self) -> MesoSnapshot:
        recent_gain = None
        span = 0.0
        per_hour = None
        if self._trail:
            start_sec, start_net = self._trail[0]
            span = self.tracked_sec - start_sec
            if span > MIN_SPAN_SEC:
                recent_gain = self.cum_net - start_net
                per_hour = recent_gain / span * 3600.0
        average = (
            self.cum_net / self.tracked_sec * 3600.0 if self.tracked_sec > MIN_SPAN_SEC else None
        )
        return MesoSnapshot(
            current=self.current,
            visible=self.visible,
            cum_net=self.cum_net,
            tracked_sec=self.tracked_sec,
            recent_gain=recent_gain,
            recent_span=span,
            per_hour=per_hour,
            average=average,
        )
