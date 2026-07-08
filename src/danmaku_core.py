"""danmaku_core.py — 彈幕排程純邏輯（不 import rumps/AppKit，可單元測試）。

機制（經典彈幕防碰撞；時間驅動，狀態不需每幀更新）：
- 顯示區域切成若干條水平「軌道」（lane）；每則彈幕從右緣進場、等速往左飛、
  出左緣即結束。同一軌道的兩則要不相撞，進場時需同時滿足：
  (1) 進場口淨空：前一則的「尾端」已完全進場並讓出間距 gap；
  (2) 追撞檢查：若新的一則速度較快，在前一則完全離開左緣之前不得追上它
      （速度相同或較慢則永遠追不上，免查）。
- 排不進任何軌道的彈幕進等待佇列（FIFO、有上限與存活時間 TTL）：
  有軌道釋出才進場、等太久就放棄——彈幕重「即時氛圍」，舊推文不補播；
  推文本身在選單/主視窗照樣看得到，不會漏資訊。
- 佇列頭進不去就整批等（嚴格 FIFO，不讓短的插隊），保持推文順序感。
"""

from collections import deque

GAP_PX = 24.0     # 同軌道前後彈幕的最小水平間距（像素）
QUEUE_MAX = 60    # 等待佇列上限（爆推時丟最舊的）
QUEUE_TTL = 8.0   # 排隊超過此秒數就放棄（秒）


class _Lane:
    """一條軌道上「最後進場那則」的狀態；夠用來判斷下一則能否安全進場。"""
    __slots__ = ("t0", "width", "speed")

    def __init__(self):
        self.t0 = -1e9      # 進場時間（很久以前 = 空軌道）
        self.width = 0.0    # 文字像素寬
        self.speed = 1.0    # 速度（px/s）


class DanmakuScheduler:
    def __init__(self, lanes: int, screen_w: float, *,
                 gap: float = GAP_PX, queue_max: int = QUEUE_MAX,
                 ttl: float = QUEUE_TTL):
        self.gap = float(gap)
        self.queue_max = int(queue_max)
        self.ttl = float(ttl)
        self.screen_w = float(screen_w)
        self._lanes = [_Lane() for _ in range(max(1, int(lanes)))]
        self._pending: deque = deque()   # (排入時間, item, width, speed)

    @property
    def lane_count(self) -> int:
        return len(self._lanes)

    def pending_count(self) -> int:
        return len(self._pending)

    def resize(self, lanes: int, screen_w: float) -> None:
        """字級/顯示範圍/螢幕大小改變時重算軌道數；既有軌道狀態保留（前綴）。"""
        lanes = max(1, int(lanes))
        self.screen_w = float(screen_w)
        cur = len(self._lanes)
        if lanes < cur:
            del self._lanes[lanes:]
        else:
            self._lanes.extend(_Lane() for _ in range(lanes - cur))

    def clear(self) -> None:
        self._pending.clear()
        for ln in self._lanes:
            ln.t0, ln.width, ln.speed = -1e9, 0.0, 1.0

    def push(self, item, width: float, speed: float, now: float) -> None:
        """排入一則彈幕。item 不透明（呼叫端自帶要畫的東西）。"""
        if speed <= 0 or width < 0:
            return
        if len(self._pending) >= self.queue_max:
            self._pending.popleft()   # 滿了丟最舊的
        self._pending.append((float(now), item, float(width), float(speed)))

    def pop_ready(self, now: float) -> list:
        """取出此刻可以進場的彈幕：[(item, lane, width, speed), ...]。"""
        while self._pending and now - self._pending[0][0] > self.ttl:
            self._pending.popleft()   # 過期放棄
        out = []
        while self._pending:
            _t, item, width, speed = self._pending[0]
            lane = self._find_lane(now, speed)
            if lane is None:
                break                 # 排頭進不去 → 整批等（嚴格 FIFO）
            ln = self._lanes[lane]
            ln.t0, ln.width, ln.speed = float(now), width, speed
            out.append((item, lane, width, speed))
            self._pending.popleft()
        return out

    def _find_lane(self, now: float, speed: float):
        for i, ln in enumerate(self._lanes):
            if self._fits(ln, now, speed):
                return i
        return None

    def _fits(self, ln: _Lane, now: float, speed: float) -> bool:
        # (1) 進場口淨空：前一則尾端 x = screen_w + width - speed*(經過時間)，
        #     必須已讓出右緣的 gap。
        tail_x = self.screen_w + ln.width - ln.speed * (now - ln.t0)
        if tail_x > self.screen_w - self.gap:
            return False
        # (2) 追撞檢查：新頭從 screen_w 出發，closing speed = speed - ln.speed；
        #     追到只剩 gap 的時間 t_c 若早於前一則完全離開左緣（remain），會在畫面內撞上。
        if speed > ln.speed:
            t_exit = ln.t0 + (self.screen_w + ln.width) / ln.speed
            remain = t_exit - now
            if remain > 0:
                t_c = (self.screen_w - tail_x - self.gap) / (speed - ln.speed)
                if t_c < remain:
                    return False
        return True
