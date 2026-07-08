"""tests/test_ui_util.py — 顯示寬度截斷（全形字）測試。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ui_util import display_width, truncate  # noqa: E402


def test_display_width_mixed():
    assert display_width("abc") == 3            # 半形
    assert display_width("中文") == 4            # 全形各 2
    assert display_width("a中b") == 4            # 1+2+1


def test_truncate_no_need():
    assert truncate("hello", 60) == "hello"
    assert truncate("推推推", 60) == "推推推"


def test_truncate_halfwidth():
    # 5 個寬度單位：取 4 個字 + …（… 佔 1）
    assert truncate("abcdefgh", 5) == "abcd…"


def test_truncate_fullwidth_boundary():
    # 每個全形字 2 寬度；限制 5 → budget=4 → 放得下 2 個全形字（=4）再加 …
    assert truncate("一二三四五", 5) == "一二…"


def test_truncate_does_not_split_over_budget():
    # "a一" 寬度 3；限制 2 → budget=1 → 只放 'a' 再加 …
    assert truncate("a一二", 2) == "a…"


# ---- 關鍵字（F1）----

from ui_util import match_keywords, parse_keywords  # noqa: E402


def test_parse_keywords_separators_and_dedup():
    assert parse_keywords("台GG, 大盤  台gg，測試") == ["台GG", "大盤", "測試"]
    assert parse_keywords("") == []
    assert parse_keywords("  ,  ，  ") == []


def test_match_keywords_case_insensitive():
    kws = ["台GG", "TSMC"]
    assert match_keywords("看好台gg上600", kws)
    assert match_keywords("tsmc to the moon", kws)
    assert not match_keywords("完全無關", kws)
    assert not match_keywords("anything", [])


# ---- 推文統計（F2）----

from ui_util import comment_stats  # noqa: E402


def _e(t, a, ago, now=10000.0):
    return {"t": t, "a": a, "ts": now - ago}


def test_comment_stats_counts_and_top():
    now = 10000.0
    entries = [_e("推", "alice", 30), _e("推", "alice", 90), _e("噓", "bob", 120),
               _e("→", "carol", 700), _e("推", "bob", 50)]
    st = comment_stats(entries, now)
    assert (st["push"], st["boo"], st["arrow"], st["total"]) == (3, 1, 1, 5)
    # 近 10 分鐘 4 則（700 秒前那則不算）→ 0.4 則/分
    assert st["rate10"] == 0.4
    # top：alice 2、bob 2（同數量按字母）、carol 1
    assert st["top"][0] == ("alice", 2) and st["top"][1] == ("bob", 2)


def test_comment_stats_buckets():
    now = 10000.0
    # 0 分鐘前 2 則、2 分鐘前 1 則、20 分鐘前 1 則（超出 15 桶不計）
    entries = [_e("推", "a", 5), _e("推", "b", 20), _e("噓", "c", 130), _e("→", "d", 1200)]
    st = comment_stats(entries, now)
    b = st["buckets"]
    assert len(b) == 15
    assert b[-1] == 2      # 最新一分鐘
    assert b[-3] == 1      # 2 分鐘前
    assert sum(b) == 3


def test_comment_stats_empty():
    st = comment_stats([], 0.0)
    assert st["total"] == 0 and st["top"] == [] and len(st["buckets"]) == 15


# ---- 膠囊主題色票（F5）----

from ui_util import PILL_PALETTES, get_palette  # noqa: E402


def test_palettes_complete():
    for name, p in PILL_PALETTES.items():
        assert p["label"]
        for sym in ("推", "→", "噓"):
            bg, fg = p["colors"][sym]
            assert len(bg) == 4 and len(fg) == 4
            assert all(0.0 <= v <= 1.0 for v in bg + fg)


def test_get_palette_fallback():
    assert get_palette("不存在") is PILL_PALETTES["ios"]
    assert get_palette("colorblind")["label"] == "色盲友善"


# ---- 合併被切開的長留言 ----

from ui_util import is_continuation  # noqa: E402


def _cont(**over):
    """預設是一組「應該合併」的接續段，用 over 蓋掉個別欄位做反例。"""
    kw = dict(prev_type="推", prev_author="alice", prev_time="07/05 12:34",
              prev_index=10, new_type="推", new_author="alice",
              new_time="07/05 12:34", new_index=11)
    kw.update(over)
    return is_continuation(**kw)


def test_continuation_same_type():
    assert _cont()  # 同帳號、序號相連、同時間、同型別 → 合併


def test_continuation_arrow_after_push():
    # 連推限制：首段「推」、接續段常改用「→」→ 仍要合併
    assert _cont(new_type="→")
    assert _cont(prev_type="噓", new_type="→")


def test_continuation_rejects_type_upgrade():
    # 接續段不會「→ 之後變推/噓」：那是另一個人格外的新留言
    assert not _cont(prev_type="→", new_type="推")
    assert not _cont(prev_type="推", new_type="噓")


def test_continuation_requires_same_author():
    assert not _cont(new_author="bob")
    assert not _cont(prev_author="", new_author="")  # 空作者不合併


def test_continuation_requires_adjacent_index():
    assert not _cont(new_index=12)   # 中間隔了別人的推文
    assert not _cont(new_index=10)   # 同序號（重複事件）也不合併


def test_continuation_requires_same_time():
    assert not _cont(new_time="07/05 12:35")  # 差一分鐘 → 當成兩則
    assert not _cont(prev_time="", new_time="")  # 沒有時間戳 → 不猜
