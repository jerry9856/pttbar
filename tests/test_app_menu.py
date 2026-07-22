"""tests/test_app_menu.py — 隱形主選單（快捷鍵）的邏輯測試（不建真的視窗、不連網）。

LSUIElement app 沒有選單列，Cmd+Q/Cmd+W/Cmd+C 等快捷鍵要靠主選單的 key equivalent
路由（使用者回報全部無效後補裝）。驗證：
- 主選單的快捷鍵對應（q→結束、w→關閉視窗、編輯選單走 responder chain）。
- Cmd+W：主視窗 key → 隱藏；聊天室 key → 收合（不是關閉）。
真正的按鍵行為（NSApp 事件路由）由使用者實測。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

pytest.importorskip("AppKit")
pytest.importorskip("rumps")

import app as appmod  # noqa: E402


class FakeChat:
    def __init__(self, on_msg):
        self.on_msg = on_msg
        self.enabled = False
        self.collapsed = False

    def configure(self, **k):
        pass

    def set_collapsed(self, c):
        self.collapsed = bool(c)

    def set_enabled(self, o):
        self.enabled = bool(o)

    def reset(self, m):
        pass

    def append(self, m):
        pass

    def get_frame(self):
        return [10, 10, 320, 460]

    def get_head_pos(self):
        return [10, 10]


@pytest.fixture
def app(monkeypatch, tmp_path):
    import credentials
    d = tmp_path / "pttbar"
    monkeypatch.setattr(credentials, "CONFIG_DIR", str(d))
    monkeypatch.setattr(credentials, "CONFIG_PATH", str(d / "config.json"))
    monkeypatch.setattr(appmod, "ChatOverlay", lambda on_msg: FakeChat(on_msg))
    a = appmod.PTTBarApp()
    a._bootstrapped = True
    a.state.logged_in = True
    a.danmaku_on = False
    a.chat_on = False
    return a


def _key_actions(menu):
    """攤平主選單 → {keyEquivalent: action selector}。"""
    out = {}
    for i in range(menu.numberOfItems()):
        sub = menu.itemAtIndex_(i).submenu()
        for j in range(sub.numberOfItems()):
            mi = sub.itemAtIndex_(j)
            out[str(mi.keyEquivalent())] = str(mi.action())
    return out


def test_main_menu_key_equivalents(app):
    keys = _key_actions(app._build_main_menu())
    assert keys["q"] == "pttbarQuit:"
    assert keys["w"] == "pttbarCloseWindow:"
    # 編輯選單走 responder chain（WKWebView 輸入框的 Cmd+C/V/A）
    for k, sel in {"c": "copy:", "v": "paste:", "x": "cut:",
                   "a": "selectAll:", "z": "undo:", "Z": "redo:"}.items():
        assert keys[k] == sel


def _fake_nsapp(monkeypatch, key_window):
    monkeypatch.setattr(appmod, "NSApp", type(
        "FakeNSApp", (), {"keyWindow": staticmethod(lambda: key_window)}))


def test_cmd_w_hides_gui_window(app, monkeypatch):
    class FakeWin:
        hidden = False

        def orderOut_(self, _):
            self.hidden = True

    fw = FakeWin()
    app.gui = type("G", (), {"_win": fw})()
    _fake_nsapp(monkeypatch, fw)
    app._close_key_window()
    assert fw.hidden is True


def test_cmd_w_collapses_chat_instead_of_closing(app, monkeypatch):
    app._apply_setting("chat_on", True)
    marker = object()
    app._chat._win = marker            # 假裝聊天室視窗是 key window
    _fake_nsapp(monkeypatch, marker)
    app._close_key_window()
    assert app.chat_collapsed is True  # 收合，不是關閉
    assert app.chat_on is True


def test_cmd_w_no_key_window_is_noop(app, monkeypatch):
    _fake_nsapp(monkeypatch, None)
    app._close_key_window()            # 不該炸
