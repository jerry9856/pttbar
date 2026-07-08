"""chat.py — 聊天室小視窗（實況聊天室風格的浮動視窗，只在 main thread 使用）。

與「彈幕」互斥（一次只能開一種顯示模式，由 app.py 控制）。特色：
- 可移動、四角可拉伸（原生 Resizable 視窗）、置頂浮動（NSFloatingWindowLevel）、
  跟著所有 Space（CanJoinAllSpaces + FullScreenAuxiliary）。
- 由上往下更新留言、最新在最下面（實況聊天室），自動貼底；使用者往上捲看歷史時
  不會被新訊息拉回去（改顯示「新訊息 ↓」小膠囊）。
- 上滑到頂會向 app 要更舊的留言（緩衝載入 chatMore → prepend）。
- 可調背景透明度（視窗底色 alpha）與文字大小（CSS 變數）。
- 隨時收合／展開：收合後視窗隱藏、改顯示一顆可拖曳的小 icon（💬），點它再展開。

渲染用 WKWebView（HTML 聊天 UI，_CHAT_HTML）。所有 Cocoa 呼叫包 try/except：
聊天室壞掉只會自己停用，不拖垮 app（與 danmaku 同哲學）。
"""

import json

import objc
from AppKit import (
    NSApp,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSEvent,
    NSFloatingWindowLevel,
    NSFont,
    NSFontAttributeName,
    NSMakeRect,
    NSScreen,
    NSView,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject
from WebKit import WKWebView, WKWebViewConfiguration

# 視窗預設幾何
_DEF_W, _DEF_H = 320.0, 460.0
_MIN_W, _MIN_H = 220.0, 200.0
_HEAD = 54.0                 # 收合後小 icon 邊長
_MARGIN = 24.0               # 預設離螢幕邊的距離
# 底色（近黑）；alpha 走設定 opacity
_BG_RGB = (0.10, 0.11, 0.14)


def _screen():
    screens = NSScreen.screens()
    return screens[0] if screens and len(screens) else NSScreen.mainScreen()


class _ChatBridge(NSObject):
    """JS postMessage → Python callback。（類別名稱必須與 gui.py 的 _Bridge 不同——
    Objective-C 類別名稱在同一行程內全域唯一，重名會在 import 時炸掉。）"""

    def initWithHandler_(self, handler):
        self = objc.super(_ChatBridge, self).init()
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
            pass


class _ChatWinDelegate(NSObject):
    """關閉視窗（Cmd-W 等）→ 當成收合，不真的銷毀。（名稱亦須與 gui.py 的 _WinDelegate 不同。）"""

    def initWithOnClose_(self, on_close):
        self = objc.super(_ChatWinDelegate, self).init()
        if self is None:
            return None
        self._on_close = on_close
        return self

    def windowShouldClose_(self, sender):
        try:
            self._on_close()
        except Exception:
            sender.orderOut_(None)
        return False


class _ChatHead(NSView):
    """收合後的小 icon：可拖曳移動，點一下（沒拖）就展開。"""

    def initWithFrame_onClick_(self, frame, on_click):
        self = objc.super(_ChatHead, self).initWithFrame_(frame)
        if self is None:
            return None
        self._on_click = on_click
        self._down = None
        self._dragged = False
        return self

    def acceptsFirstMouse_(self, _e):
        return True

    def drawRect_(self, _rect):
        try:
            b = self.bounds()
            r = min(b.size.width, b.size.height) / 2.0
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, r, r)
            NSColor.colorWithSRGBRed_green_blue_alpha_(*_BG_RGB, 0.94).setFill()
            path.fill()
            font = NSFont.systemFontOfSize_(b.size.height * 0.5)
            astr = NSAttributedString.alloc().initWithString_attributes_(
                "💬", {NSFontAttributeName: font})
            sz = astr.size()
            astr.drawAtPoint_(((b.size.width - sz.width) / 2.0,
                               (b.size.height - sz.height) / 2.0 - 1.0))
        except Exception:
            pass

    def mouseDown_(self, event):
        self._down = event.locationInWindow()
        self._dragged = False

    def mouseDragged_(self, event):
        self._dragged = True
        try:
            p = NSEvent.mouseLocation()
            self.window().setFrameOrigin_((p.x - self._down.x, p.y - self._down.y))
        except Exception:
            pass

    def mouseUp_(self, _event):
        if not self._dragged and self._on_click:
            try:
                self._on_click()
            except Exception:
                pass


class ChatOverlay:
    def __init__(self, on_msg):
        """on_msg(dict)：JS 端事件（ready / chatMore / chatCollapse）回呼到 app。"""
        self._on_msg = on_msg
        self._win = None
        self._web = None
        self._bridge = None
        self._delegate = None
        self._head = None            # 收合小 icon 視窗
        self._enabled = False
        self._collapsed = False
        self._ready = False          # WKWebView 已載入、可接受 JS
        # 設定
        self._font_px = 14
        self._opacity = 0.90
        # 記住的幾何（app 存 config；build 時帶入）
        self._saved_frame = None     # (x, y, w, h)
        self._saved_head = None      # (x, y)

    # ---------- 設定 ----------

    def configure(self, *, font_px: int, opacity: float,
                  frame=None, head_pos=None):
        self._font_px = int(font_px)
        self._opacity = max(0.2, min(1.0, float(opacity)))
        if frame:
            self._saved_frame = tuple(frame)
        if head_pos:
            self._saved_head = tuple(head_pos)
        if self._win is not None:
            try:
                self._win.setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(
                    *_BG_RGB, self._opacity))
            except Exception:
                pass
        self._push_config()

    def set_enabled(self, on: bool):
        on = bool(on)
        self._enabled = on
        if on:
            if self._win is None:
                self._build()
            self._show_current()
        else:
            if self._win is not None:
                self._win.orderOut_(None)
            if self._head is not None:
                self._head.orderOut_(None)

    def set_collapsed(self, collapsed: bool):
        self._collapsed = bool(collapsed)
        if self._enabled:
            self._show_current()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def _show_current(self):
        """依 enabled/collapsed 決定顯示聊天視窗還是小 icon。"""
        if not self._enabled:
            return
        if self._collapsed:
            if self._win is not None:
                self._win.orderOut_(None)
            self._ensure_head()
            if self._head is not None:
                self._head.orderFrontRegardless()
        else:
            if self._head is not None:
                self._head.orderOut_(None)
            if self._win is not None:
                self._win.orderFrontRegardless()

    # ---------- 內容（Python → JS）----------

    def reset(self, msgs: list):
        self._js("window.__chatReset && window.__chatReset(%s)" % _dumps(msgs))

    def append(self, msgs: list):
        if msgs:
            self._js("window.__chatAppend && window.__chatAppend(%s)" % _dumps(msgs))

    def prepend(self, msgs: list, has_more: bool):
        self._js("window.__chatPrepend && window.__chatPrepend(%s,%s)"
                 % (_dumps(msgs), "true" if has_more else "false"))

    def update_last(self, text: str):
        self._js("window.__chatUpdateLast && window.__chatUpdateLast(%s)"
                 % _dumps(text))

    def _push_config(self):
        self._js("window.__chatConfig && window.__chatConfig(%s)"
                 % _dumps({"font": self._font_px}))

    def _js(self, script: str):
        if self._web is None or not self._ready:
            return
        try:
            self._web.evaluateJavaScript_completionHandler_(script, None)
        except Exception:
            pass

    # ---------- 幾何存取（app 存 config 用）----------

    def get_frame(self):
        try:
            if self._win is not None:
                f = self._win.frame()
                return [float(f.origin.x), float(f.origin.y),
                        float(f.size.width), float(f.size.height)]
        except Exception:
            pass
        return None

    def get_head_pos(self):
        try:
            if self._head is not None:
                o = self._head.frame().origin
                return [float(o.x), float(o.y)]
        except Exception:
            pass
        return None

    # ---------- 內部：建立視窗 ----------

    def _default_frame(self):
        vf = _screen().visibleFrame()
        x = vf.origin.x + vf.size.width - _DEF_W - _MARGIN
        y = vf.origin.y + vf.size.height - _DEF_H - _MARGIN
        return NSMakeRect(x, y, _DEF_W, _DEF_H)

    def _build(self):
        mask = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                | NSWindowStyleMaskResizable)
        # 先用預設 content rect 建立，之後若有存檔的「視窗外框」再 setFrame（get_frame 存的是
        # 視窗外框、含標題列；用 setFrame 還原才不會每次還原都被標題列高度撐大）。
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            self._default_frame(), mask, NSBackingStoreBuffered, False)
        if self._saved_frame:
            x, y, w, h = self._saved_frame
            win.setFrame_display_(NSMakeRect(x, y, max(_MIN_W, w), max(_MIN_H, h)), False)
        win.setTitle_("PTT 聊天室")
        win.setReleasedWhenClosed_(False)
        win.setMinSize_((_MIN_W, _MIN_H))
        win.setLevel_(NSFloatingWindowLevel)
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary)
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(
            *_BG_RGB, self._opacity))
        win.setHasShadow_(True)
        win.setMovableByWindowBackground_(True)   # 拖曳視窗任意處即可移動（滾輪照常捲動）
        # 透明標題列、藏標題字與紅綠燈按鈕 → 上緣留一條可拖曳的深色細條
        try:
            win.setTitlebarAppearsTransparent_(True)
            win.setTitleVisibility_(1)   # NSWindowTitleHidden
            for i in (0, 1, 2):          # close / miniaturize / zoom
                b = win.standardWindowButton_(i)
                if b is not None:
                    b.setHidden_(True)
        except Exception:
            pass

        self._delegate = _ChatWinDelegate.alloc().initWithOnClose_(self._on_close_clicked)
        win.setDelegate_(self._delegate)

        conf = WKWebViewConfiguration.alloc().init()
        self._bridge = _ChatBridge.alloc().initWithHandler_(self._handle_js)
        conf.userContentController().addScriptMessageHandler_name_(self._bridge, "py")
        web = WKWebView.alloc().initWithFrame_configuration_(
            win.contentView().bounds(), conf)
        web.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        try:
            web.setValue_forKey_(False, "drawsBackground")   # 透明 → 露出視窗底色
        except Exception:
            pass
        win.contentView().addSubview_(web)
        web.loadHTMLString_baseURL_(_CHAT_HTML, None)
        self._win, self._web = win, web

    def _ensure_head(self):
        if self._head is not None:
            return
        if self._saved_head:
            x, y = self._saved_head
        else:
            vf = _screen().visibleFrame()
            x = vf.origin.x + vf.size.width - _HEAD - _MARGIN
            y = vf.origin.y + vf.size.height - _HEAD - _MARGIN
        head = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, _HEAD, _HEAD), NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered, False)
        head.setOpaque_(False)
        head.setBackgroundColor_(NSColor.clearColor())
        head.setHasShadow_(True)
        head.setLevel_(NSFloatingWindowLevel)
        head.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary)
        head.setReleasedWhenClosed_(False)
        view = _ChatHead.alloc().initWithFrame_onClick_(
            ((0.0, 0.0), (_HEAD, _HEAD)), self._on_head_clicked)
        head.setContentView_(view)
        self._head = head

    # ---------- 內部：事件 ----------

    def _handle_js(self, msg: dict):
        cmd = msg.get("cmd")
        if cmd == "ready":
            self._ready = True
            self._push_config()
        # 其餘（chatMore/chatCollapse）交給 app 決定（要讀 history / 存 config）
        try:
            self._on_msg(msg)
        except Exception:
            pass

    def _on_close_clicked(self):
        # 標題列（隱藏的）close 或 Cmd-W → 收合
        self._on_msg({"cmd": "chatCollapse"})

    def _on_head_clicked(self):
        self._on_msg({"cmd": "chatExpand"})


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


_CHAT_HTML = r"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--fs:14px}
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;background:transparent;
  font-family:-apple-system,"PingFang TC","Helvetica Neue",sans-serif;
  color:#f2f3f5;overflow:hidden;-webkit-user-select:none;user-select:none}
#wrap{display:flex;flex-direction:column;height:100%}
#hd{flex:0 0 auto;display:flex;align-items:center;gap:6px;
  padding:6px 8px 6px 12px;font-size:12px;font-weight:600;color:#c9ccd3;
  border-bottom:1px solid rgba(255,255,255,.08)}
#hd .ttl{flex:1;letter-spacing:.5px}
#hd .btn{width:22px;height:22px;border-radius:6px;display:flex;align-items:center;
  justify-content:center;cursor:pointer;color:#c9ccd3;font-size:15px;line-height:1}
#hd .btn:hover{background:rgba(255,255,255,.12)}
#list{flex:1;overflow-y:auto;overflow-x:hidden;padding:6px 10px 8px;
  display:flex;flex-direction:column;gap:3px;scroll-behavior:auto}
#list::-webkit-scrollbar{width:8px}
#list::-webkit-scrollbar-thumb{background:rgba(255,255,255,.18);border-radius:4px}
.msg{font-size:var(--fs);line-height:1.34;word-break:break-word;padding:1px 0}
.msg .au{font-weight:600;opacity:.92}
.msg .tm{font-size:.72em;opacity:.4;margin-left:5px}
.msg.push .au{color:#5fd97a}
.msg.boo  .au{color:#ff6b5c}
.msg.arrow .au{color:#e9c94b}
.msg.kw{background:rgba(233,201,75,.16);border-radius:5px;padding:1px 5px;margin:0 -5px}
.msg .co{opacity:.96}
#empty{padding:16px 12px;font-size:12px;opacity:.5;text-align:center}
#pill{position:absolute;left:50%;transform:translateX(-50%);bottom:10px;
  background:#2c6cf0;color:#fff;font-size:12px;padding:5px 12px;border-radius:14px;
  cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.4);display:none}
#top{padding:6px;text-align:center;font-size:11px;opacity:.45;display:none}
</style></head>
<body><div id="wrap">
  <div id="hd"><span class="ttl">PTT 聊天室</span>
    <span class="btn" id="b-min" title="收合">▁</span></div>
  <div id="list"><div id="top">載入更多…</div><div id="empty">尚無留言</div></div>
</div>
<div id="pill">新訊息 ↓</div>
<script>
var $=function(id){return document.getElementById(id)};
var py=function(m){try{webkit.messageHandlers.py.postMessage(m)}catch(e){}};
var list=$("list"),pill=$("pill"),topEl=$("top"),emptyEl=$("empty");
var loadingMore=false, hasMore=true;
var SYM={"推":"push","噓":"boo","→":"arrow"};

function atBottom(){return list.scrollHeight-list.scrollTop-list.clientHeight<40}
function toBottom(){list.scrollTop=list.scrollHeight;pill.style.display="none"}
function esc(s){var d=document.createElement("div");d.textContent=s==null?"":s;return d.innerHTML}

function node(m){
  var d=document.createElement("div");
  d.className="msg "+(SYM[m.t]||"arrow")+(m.kw?" kw":"");
  var au=m.a?('<span class="au">'+esc(m.a)+'</span> '):"";
  var tm=m.tm?('<span class="tm">'+esc(m.tm)+'</span>'):"";
  d.innerHTML=au+'<span class="co">'+esc(m.c)+'</span>'+tm;
  return d;
}

window.__chatConfig=function(o){
  if(o&&o.font)document.documentElement.style.setProperty("--fs",o.font+"px");
};
window.__chatReset=function(msgs){
  // 清空後重建（保留 top/empty 節點的參照）
  var kids=list.querySelectorAll(".msg");for(var i=0;i<kids.length;i++)kids[i].remove();
  hasMore=true;loadingMore=false;topEl.style.display="none";
  var frag=document.createDocumentFragment();
  (msgs||[]).forEach(function(m){frag.appendChild(node(m))});
  list.appendChild(frag);
  emptyEl.style.display=(msgs&&msgs.length)?"none":"block";
  requestAnimationFrame(toBottom);
};
window.__chatAppend=function(msgs){
  if(!msgs||!msgs.length)return;
  emptyEl.style.display="none";
  var pinned=atBottom();
  var frag=document.createDocumentFragment();
  msgs.forEach(function(m){frag.appendChild(node(m))});
  list.appendChild(frag);
  if(pinned)requestAnimationFrame(toBottom);
  else pill.style.display="block";
};
window.__chatPrepend=function(msgs,more){
  loadingMore=false;hasMore=!!more;
  topEl.style.display=hasMore?"block":"none";
  if(!msgs||!msgs.length)return;
  var h0=list.scrollHeight, t0=list.scrollTop;
  var frag=document.createDocumentFragment();
  msgs.forEach(function(m){frag.appendChild(node(m))});
  // 插在 top 提示之後、第一則訊息之前
  var first=list.querySelector(".msg");
  if(first)list.insertBefore(frag,first);else list.appendChild(frag);
  list.scrollTop=t0+(list.scrollHeight-h0);   // 維持視覺位置，不跳動
};
window.__chatUpdateLast=function(text){
  var msgs=list.querySelectorAll(".msg");
  if(!msgs.length)return;
  var co=msgs[msgs.length-1].querySelector(".co");
  if(co)co.textContent=text;
  if(atBottom())requestAnimationFrame(toBottom);
};

list.addEventListener("scroll",function(){
  if(atBottom())pill.style.display="none";
  if(list.scrollTop<60&&hasMore&&!loadingMore){loadingMore=true;py({cmd:"chatMore"})}
});
pill.addEventListener("click",toBottom);
$("b-min").addEventListener("click",function(){py({cmd:"chatCollapse"})});

py({cmd:"ready"});
</script></body></html>"""
