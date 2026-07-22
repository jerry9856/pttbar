"""tests/test_app_demo.py — Demo 展示模式的邏輯測試（不建真的視窗、不連網）。

用假的 ChatOverlay/DanmakuOverlay 注入 app、假時鐘控制輪播節奏，驗證：
- 快照最近 _DEMO_COUNT 則、循環輪播順序（舊→新、到尾巴繞回）。
- 跑馬燈顯示的是輪播中的那則（_display_comment 覆蓋），關閉後回到即時最新。
- 每換一則餵彈幕/聊天室各一次；聊天室走直接 append（不寫入 history）。
- 關閉展示會要求聊天室重繪（清掉輪播訊息）；換文章/登出會自動停止展示。
真正的畫面效果（膠囊捲動、彈幕飛行）由使用者實測。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

pytest.importorskip("AppKit")
pytest.importorskip("rumps")

import app as appmod  # noqa: E402
from models import EVT_COMMENT, CommentData  # noqa: E402


class FakeChat:
    def __init__(self, on_msg):
        self.on_msg = on_msg
        self.calls = []
        self.enabled = False
        self.collapsed = False

    def configure(self, **k):
        pass

    def set_collapsed(self, c):
        self.collapsed = bool(c)

    def set_enabled(self, o):
        self.enabled = bool(o)

    def reset(self, m):
        self.calls.append(("reset", [x["c"] for x in m]))

    def append(self, m):
        self.calls.append(("append", [x["c"] for x in m]))

    def prepend(self, m, has_more):
        self.calls.append(("prepend", [x["c"] for x in m], has_more))

    def update_last(self, t):
        self.calls.append(("update_last", t))

    def get_frame(self):
        return [10, 10, 320, 460]

    def get_head_pos(self):
        return [10, 10]


class FakeDmk:
    def __init__(self):
        self.enabled = False
        self.fed = []

    def configure(self, **k):
        pass

    def set_enabled(self, o):
        self.enabled = bool(o)

    def feed(self, ctype, author, content, kw=False):
        self.fed.append((ctype, author, content))

    def tick(self, *a, **k):
        pass


@pytest.fixture
def app(monkeypatch, tmp_path):
    import credentials
    d = tmp_path / "pttbar"
    monkeypatch.setattr(credentials, "CONFIG_DIR", str(d))
    monkeypatch.setattr(credentials, "CONFIG_PATH", str(d / "config.json"))
    monkeypatch.setattr(appmod, "ChatOverlay", lambda on_msg: FakeChat(on_msg))
    monkeypatch.setattr(appmod, "DanmakuOverlay", lambda: FakeDmk())
    a = appmod.PTTBarApp()
    a._bootstrapped = True
    a.state.logged_in = True
    a.danmaku_on = False
    a.chat_on = False
    return a


@pytest.fixture
def clock(monkeypatch):
    """假時鐘：控制 time.monotonic()，測輪播節奏不用真的等。"""
    t = {"now": 100.0}
    monkeypatch.setattr(appmod.time, "monotonic", lambda: t["now"])
    return t


def _mk(i):
    return CommentData.from_ptt(
        {"type": "PUSH", "author": f"u{i}", "content": f"c{i}",
         "ip": "1", "time": "07/08 12:00"}, i)


def _feed(app, n, start=0):
    for i in range(start, start + n):
        app.queue.put((EVT_COMMENT, _mk(i)))
    app._on_tick(None)


def _settle(app, ticks=20):
    """跑完換推文的 0.25s 轉場（20 幀 > 轉場所需幀數），讓 _at_end 有機會變 True。"""
    for _ in range(ticks):
        app._on_tick(None)


def _advance(app, clock):
    """模擬「捲完 + 停留時間到」→ 輪播下一則。"""
    _settle(app)
    clock["now"] += appmod._DEMO_HOLD + 0.1
    app._on_tick(None)


def test_demo_needs_comments(app, clock):
    app._toggle_demo()
    assert not app._demo_items
    assert app._toast and "還沒有推文" in app._toast[0]


def test_demo_snapshots_last_10_and_cycles(app, clock):
    _feed(app, 12)                       # u0..u11 → 快照應為 u2..u11
    app._toggle_demo()
    assert [c.author for c in app._demo_items] == [f"u{i}" for i in range(2, 12)]

    app._on_tick(None)                   # 第一則立即播出
    assert app._display_comment().author == "u2"
    assert "u2" in app._compose_title_text()

    # 短字串捲不動、轉場跑完後 _at_end=True，停留時間到 → 下一則
    for i in range(3, 12):
        _advance(app, clock)
        assert app._display_comment().author == f"u{i}"
    _advance(app, clock)                 # 播完最後一則 → 繞回第一則
    assert app._display_comment().author == "u2"


def test_demo_holds_between_items(app, clock):
    _feed(app, 3)
    app._toggle_demo()
    app._on_tick(None)
    assert app._display_comment().author == "u0"
    clock["now"] += 0.5                  # 未達停留秒數 → 不換
    app._on_tick(None)
    assert app._display_comment().author == "u0"


def test_demo_feeds_danmaku_once_per_item(app, clock):
    _feed(app, 3)
    app._apply_setting("danmaku_on", True)
    app._danmaku.fed.clear()             # 丟掉開啟彈幕時的「彈幕已開啟」問候
    app._toggle_demo()
    app._on_tick(None)
    app._on_tick(None)                   # 同一則不重複餵
    assert app._danmaku.fed == [("推", "u0", "c0")]
    _advance(app, clock)
    assert app._danmaku.fed == [("推", "u0", "c0"), ("推", "u1", "c1")]


def test_demo_feeds_chat_without_touching_history(app, clock):
    _feed(app, 3)
    app._apply_setting("chat_on", True)
    app._on_tick(None)                   # 開聊天室後首輪 reset（真實 history）
    n_history = len(app._history)
    app._toggle_demo()
    app._on_tick(None)
    appends = [c for c in app._chat.calls if c[0] == "append"]
    assert appends[-1] == ("append", ["c0"])
    assert len(app._history) == n_history   # 輪播不寫入 history

    app._toggle_demo()                   # 關閉 → 聊天室要求重繪回真實內容
    assert app._chat_needs_reset is True


def test_demo_marquee_back_to_live_after_stop(app, clock):
    _feed(app, 5)
    app._toggle_demo()
    app._on_tick(None)
    assert app._display_comment().author == "u0"   # 展示中：輪播第一則
    app._toggle_demo()
    assert app._display_comment().author == "u4"   # 關閉：回到即時最新


def test_demo_stops_on_new_article_and_logout(app, clock, monkeypatch):
    _feed(app, 3)
    app._toggle_demo()
    assert app._demo_items
    monkeypatch.setattr(app, "watcher", type("W", (), {
        "track": lambda self, *a, **k: None, "stop": lambda self: None})())
    app._begin_track("Test", title="t")
    assert not app._demo_items

    _feed(app, 3)
    app._toggle_demo()
    assert app._demo_items
    app._logout()
    assert not app._demo_items


def test_demo_menu_item_label(app, clock):
    _feed(app, 2)
    app._rebuild_menu()
    assert any("Demo 展示" in k for k in app.menu.keys())
    app._toggle_demo()
    assert any("停止 Demo 展示" in k for k in app.menu.keys())
