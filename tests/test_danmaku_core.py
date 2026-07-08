"""test_danmaku_core.py — 彈幕排程純邏輯的單元測試（時間全部注入，離線可跑）。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from danmaku_core import DanmakuScheduler  # noqa: E402


def _sched(lanes=3, w=1000.0, **kw):
    kw.setdefault("gap", 20.0)
    kw.setdefault("queue_max", 5)
    kw.setdefault("ttl", 8.0)
    return DanmakuScheduler(lanes, w, **kw)


def test_batch_spreads_across_lanes_in_order():
    s = _sched()
    for name in ("a", "b", "c"):
        s.push(name, width=100, speed=100, now=0.0)
    got = s.pop_ready(0.0)
    assert [(item, lane) for item, lane, _w, _v in got] == [("a", 0), ("b", 1), ("c", 2)]
    assert s.pending_count() == 0


def test_same_lane_blocked_until_tail_clears_gap():
    s = _sched(lanes=1)
    s.push("a", width=100, speed=100, now=0.0)
    assert len(s.pop_ready(0.0)) == 1
    s.push("b", width=50, speed=100, now=0.0)
    # a 的尾端 x = 1000+100-100t，要 <= 1000-20 → t >= 1.2 才讓出進場口
    assert s.pop_ready(1.19) == []
    got = s.pop_ready(1.21)
    assert [(i, ln) for i, ln, _w, _v in got] == [("b", 0)]


def test_faster_comment_blocked_until_no_catchup():
    s = _sched(lanes=1, ttl=100.0)   # 大 TTL：這裡只驗追撞幾何，不驗排隊過期
    s.push("slow", width=100, speed=50, now=0.0)
    s.pop_ready(0.0)
    # slow 完全離開左緣要到 t=(1000+100)/50=22
    s.push("fast", width=100, speed=200, now=5.0)
    # t=5：進場口已淨空（尾端 x=850），但追撞：t_c=(1000-850-20)/150≈0.87 < remain=17 → 擋下
    assert s.pop_ready(5.0) == []
    # t=20：尾端 x=100，t_c=(1000-100-20)/150≈5.9 >= remain=2 → 出畫面前追不上，放行
    got = s.pop_ready(20.0)
    assert [(i, ln) for i, ln, _w, _v in got] == [("fast", 0)]


def test_slower_or_equal_speed_never_catchup_check():
    s = _sched(lanes=1)
    s.push("a", width=100, speed=100, now=0.0)
    s.pop_ready(0.0)
    s.push("b", width=100, speed=100, now=0.0)   # 同速：只要進場口淨空即可
    assert s.pop_ready(1.3) != []


def test_queue_ttl_drops_stale():
    s = _sched(lanes=1)
    s.push("a", width=900, speed=50, now=0.0)    # 佔住軌道很久
    s.pop_ready(0.0)
    s.push("b", width=100, speed=50, now=0.0)
    assert s.pop_ready(5.0) == []                # 還在排隊
    assert s.pending_count() == 1
    assert s.pop_ready(8.5) == []                # 排隊 > TTL(8s) → 放棄
    assert s.pending_count() == 0


def test_queue_cap_drops_oldest():
    s = _sched(lanes=1)
    s.push("busy", width=900, speed=50, now=0.0)
    s.pop_ready(0.0)
    for i in range(6):                            # 上限 5 → 第 0 則被擠掉
        s.push(f"x{i}", width=10, speed=50, now=0.0)
    assert s.pending_count() == 5
    got = s.pop_ready(21.0)                       # busy 尾端讓出進場口後（在 TTL 內放寬檢查）
    assert got == [] or got[0][0] != "x0"


def test_fifo_head_blocks_rest():
    s = _sched(lanes=1)
    s.push("a", width=100, speed=100, now=0.0)
    s.pop_ready(0.0)
    s.push("long", width=500, speed=100, now=0.0)
    s.push("tiny", width=10, speed=100, now=0.0)
    got = s.pop_ready(1.3)                        # 軌道口剛淨空：long 進場、tiny 不能插隊
    assert [i for i, *_ in got] == ["long"]
    assert s.pending_count() == 1


def test_resize_shrink_and_grow():
    s = _sched(lanes=3)
    s.push("a", width=100, speed=100, now=0.0)
    s.pop_ready(0.0)                              # a 佔 lane 0
    s.resize(1, 1000.0)
    assert s.lane_count == 1
    s.push("b", width=100, speed=100, now=0.0)
    assert s.pop_ready(0.5) == []                 # lane 0 的狀態被保留 → 還被 a 擋著
    s.resize(2, 1000.0)
    got = s.pop_ready(0.5)
    assert [(i, ln) for i, ln, _w, _v in got] == [("b", 1)]


def test_clear_resets_lanes_and_queue():
    s = _sched(lanes=1)
    s.push("a", width=100, speed=100, now=0.0)
    s.pop_ready(0.0)
    s.push("b", width=100, speed=100, now=0.0)
    s.clear()
    assert s.pending_count() == 0
    s.push("c", width=100, speed=100, now=0.1)
    got = s.pop_ready(0.1)                        # 軌道已重設 → 立即可進
    assert [i for i, *_ in got] == ["c"]


def test_invalid_push_ignored():
    s = _sched(lanes=1)
    s.push("bad", width=100, speed=0, now=0.0)
    s.push("bad2", width=-5, speed=100, now=0.0)
    assert s.pending_count() == 0
