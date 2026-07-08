"""gui.py — PTTBar 主視窗（NSWindow + WKWebView，載入 webui.HTML）。

- 只在 main thread 使用（由 app.py 的選單 callback / Timer 呼叫）。
- JS → Python：WKScriptMessageHandler（name="py"），轉成 dict 丟給 handler callback。
- Python → JS：push(state) → evaluateJavaScript("window.__update({...})")。
- 關閉視窗＝隱藏（setReleasedWhenClosed(False) + delegate 攔 close），可重複開啟。
"""

import json

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSMakeRect,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject
from WebKit import WKWebView, WKWebViewConfiguration

from webui import HTML


class _Bridge(NSObject):
    """JS postMessage → Python callback。"""

    def initWithHandler_(self, handler):
        self = objc.super(_Bridge, self).init()
        if self is None:
            return None
        self._handler = handler
        return self

    def userContentController_didReceiveScriptMessage_(self, _ucc, message):
        try:
            body = message.body()
            msg = dict(body) if body else {}
        except Exception:
            return
        try:
            self._handler(msg)
        except Exception:
            pass  # UI 指令失敗不可炸掉 app


class _WinDelegate(NSObject):
    """關閉 = 隱藏（保留 webview 狀態，重開即現）。"""

    def windowShouldClose_(self, sender):
        sender.orderOut_(None)
        return False


class GuiWindow:
    def __init__(self, handler):
        self._handler = handler
        self._win = None
        self._web = None
        self._bridge = None
        self._delegate = None

    def _build(self):
        mask = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
        # 毛玻璃 chrome（iOS/Music 質感）；任何一步失敗就退回不透明的一般視窗
        glass = True
        try:
            mask |= NSWindowStyleMaskFullSizeContentView
        except Exception:
            glass = False
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0.0, 0.0, 900.0, 640.0), mask, NSBackingStoreBuffered, False)
        win.setTitle_("PTTBar")
        win.setReleasedWhenClosed_(False)
        win.setMinSize_((680.0, 460.0))
        self._delegate = _WinDelegate.alloc().init()
        win.setDelegate_(self._delegate)

        if glass:
            try:
                win.setTitlebarAppearsTransparent_(True)
                win.setTitleVisibility_(1)  # NSWindowTitleHidden：留紅綠燈、藏標題字
                effect = NSVisualEffectView.alloc().initWithFrame_(
                    win.contentView().bounds())
                effect.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
                effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
                effect.setMaterial_(17)  # underWindowBackground（Big Sur+ 窗底材質）
                win.contentView().addSubview_(effect)
            except Exception:
                glass = False

        conf = WKWebViewConfiguration.alloc().init()
        self._bridge = _Bridge.alloc().initWithHandler_(self._handler)
        conf.userContentController().addScriptMessageHandler_name_(self._bridge, "py")

        web = WKWebView.alloc().initWithFrame_configuration_(
            win.contentView().bounds(), conf)
        web.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        if glass:
            try:
                web.setValue_forKey_(False, "drawsBackground")  # 透明，讓毛玻璃透出
            except Exception:
                glass = False
        win.contentView().addSubview_(web)

        # glass 成功才讓頁面用透明底（<html class="glass">），失敗用純色（頁面預設）
        html = HTML.replace('<html lang="zh-Hant">',
                            '<html lang="zh-Hant" class="glass">') if glass else HTML
        web.loadHTMLString_baseURL_(html, None)
        win.center()
        self._win, self._web = win, web

    def show(self):
        if self._win is None:
            self._build()
        self._win.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def visible(self) -> bool:
        return self._win is not None and bool(self._win.isVisible())

    def push(self, state: dict):
        """把狀態推給 JS 端重繪。視窗沒開就跳過。"""
        if self._web is None or not self.visible():
            return
        js = "window.__update && window.__update(%s)" % json.dumps(
            state, ensure_ascii=False)
        self._web.evaluateJavaScript_completionHandler_(js, None)

    def eval_js(self, js: str):
        """執行任意 JS（例如推送完整歷史）。"""
        if self._web is not None:
            self._web.evaluateJavaScript_completionHandler_(js, None)
