"""tests/test_app_chat.py — 聊天室模式的邏輯測試（不建真的視窗、不連網）。

用假的 ChatOverlay/DanmakuOverlay 注入 app，驗證：
- 彈幕與聊天室互斥（開一個會關另一個）。
- 聊天室由 history 驅動：首次 reset、之後 append、接續段 update_last、上滑 prepend。
真正的視窗外觀/拖曳/拉伸由使用者實測（headless 無法驗證繪圖與滑鼠互動）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 需要 AppKit（rumps.App 建構）；環境沒有就跳過整個檔案
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
        self.calls.append(("configure", k))

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

    def configure(self, **k):
        pass

    def set_enabled(self, o):
        self.enabled = bool(o)

    def feed(self, *a, **k):
        pass

    def tick(self, *a, **k):
        pass


@pytest.fixture
def app(monkeypatch, tmp_path):
    # 把 config 導到 tmp，避免動到使用者設定
    import credentials
    d = tmp_path / "pttbar"
    monkeypatch.setattr(credentials, "CONFIG_DIR", str(d))
    monkeypatch.setattr(credentials, "CONFIG_PATH", str(d / "config.json"))
    monkeypatch.setattr(appmod, "ChatOverlay", lambda on_msg: FakeChat(on_msg))
    monkeypatch.setattr(appmod, "DanmakuOverlay", lambda: FakeDmk())
    a = appmod.PTTBarApp()
    a._bootstrapped = True          # 跳過會跳登入視窗的 bootstrap
    a.state.logged_in = True
    a.danmaku_on = False
    a.chat_on = False
    return a


def test_chat_and_gui_overlays_both_importable():
    """回歸：chat.py 與 gui.py 的 Objective-C 類別名稱不可衝突（否則 import 時其中一個會被
    吞掉變 None）。app 匯入後 ChatOverlay 與 GuiWindow 必須都在。"""
    assert appmod.ChatOverlay is not None, "ChatOverlay 沒載入（可能與 gui 的 ObjC 類別重名）"
    assert appmod.GuiWindow is not None, "GuiWindow 沒載入（可能與 chat 的 ObjC 類別重名）"


def _mk(i):
    return CommentData.from_ptt(
        {"type": "PUSH", "author": f"u{i}", "content": f"c{i}",
         "ip": "1", "time": "07/08 12:00"}, i)


def test_chat_and_danmaku_mutually_exclusive(app):
    app._apply_setting("danmaku_on", True)
    assert app.danmaku_on and not app.chat_on

    app._apply_setting("chat_on", True)          # 開聊天室 → 關彈幕
    assert app.chat_on and not app.danmaku_on
    assert app._chat is not None and app._chat.enabled

    app._apply_setting("danmaku_on", True)       # 開彈幕 → 關聊天室
    assert app.danmaku_on and not app.chat_on
    assert not app._chat.enabled


def test_chat_reset_then_append(app):
    app._apply_setting("chat_on", True)
    for i in range(3):
        app.queue.put((EVT_COMMENT, _mk(i)))
    app._on_tick(None)                            # 首輪：needs_reset → reset
    for i in range(3, 5):
        app.queue.put((EVT_COMMENT, _mk(i)))
    app._on_tick(None)                            # 後續：append 新的

    assert ("reset", ["c0", "c1", "c2"]) in app._chat.calls
    assert ("append", ["c3", "c4"]) in app._chat.calls


def test_chat_load_more_prepends(app):
    app._apply_setting("chat_on", True)
    for i in range(10):
        app.queue.put((EVT_COMMENT, _mk(i)))
    app._on_tick(None)
    app._chat_oldest = 5                          # 假裝目前顯示到第 5 則
    app._chat_load_more()
    preps = [c for c in app._chat.calls if c[0] == "prepend"]
    assert preps and preps[-1][1] == ["c0", "c1", "c2", "c3", "c4"]
    assert preps[-1][2] is False                  # 到頂了 → has_more=False


def test_chat_collapsed_persists(app):
    app._apply_setting("chat_on", True)
    app._collapse_chat(True)
    assert app.chat_collapsed is True
    import credentials
    assert credentials.get_settings().get("chat_collapsed") is True
    app._collapse_chat(False)
    assert app.chat_collapsed is False
