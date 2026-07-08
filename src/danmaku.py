"""danmaku.py — 彈幕透明置頂視窗（PLAN M5 選配「彈幕模式」，只在 main thread 使用）。

作法：
- 一個 borderless、透明、setIgnoresMouseEvents(True)（點擊穿透）、
  NSStatusWindowLevel（蓋過一般視窗）的 NSWindow，蓋住主螢幕 visibleFrame
  （不壓到選單列/Dock）；CollectionBehavior 讓它跟著所有 Space、也出現在
  全螢幕 app 之上（FullScreenAuxiliary）。
- 每則彈幕是一個 CATextLayer，用 CABasicAnimation 讓 position.x 從右緣線性
  飛到左緣外——動畫由 Core Animation（GPU）驅動，Python 不用每幀算位置；
  app 的 Timer 只要定期 tick()：讓排程器放行排隊中的彈幕、清掉飛完的 layer。
- 軌道分配/防碰撞/排隊在 danmaku_core.DanmakuScheduler（純邏輯、可測）。
- 所有繪圖呼叫都包 try/except：彈幕壞掉只會自己停用，絕不拖垮 app。
"""

import time

from AppKit import (
    NSAttributedString,
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSScreen,
    NSStrokeColorAttributeName,
    NSStrokeWidthAttributeName,
    NSStatusWindowLevel,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
)
from Quartz import (
    CABasicAnimation,
    CALayer,
    CAMediaTimingFunction,
    CATextLayer,
    CATransaction,
    kCAMediaTimingFunctionLinear,
)

from danmaku_core import DanmakuScheduler

# 彈幕文字色（疊在任意背景上，配黑色陰影描邊確保可讀）。RGBA 0..1。
_TYPE_RGB = {
    "推": (0.32, 0.88, 0.45, 1.0),
    "→": (1.00, 0.84, 0.10, 1.0),
    "噓": (1.00, 0.32, 0.27, 1.0),
}
_WHITE = (1.0, 1.0, 1.0, 1.0)

_LANE_PAD = 6.0        # 軌道高 = 文字行高 + 上下留白
_GEO_CHECK_SEC = 2.0   # 螢幕大小/解析度變化的檢查週期
# 可讀性（使用者微調過：粗描邊太過 → 預設改半透明深色圓角底）：
# - 底色開（預設）：深色半透明圓角膠囊（透明度可在設定頁調，預設 30%）。白背景上看起來是
#   淺淺的灰膜、深背景上幾乎隱形，亮色文字（推綠/→黃/噓紅/白）在兩種背景都跳得出來；
#   文字本身不描邊、乾淨。
# - 底色關：退回「細描邊 + 柔影」（比先前 -4.5 細很多）。
_BACKDROP_RGB = (0.08, 0.08, 0.10)   # 底色（近黑）；透明度走設定 backdrop_alpha
_PAD_X = 9.0           # 底色膠囊的文字左右留白
_PAD_Y = 2.5           # 上下留白
_STROKE_PCT = -2.0     # 無底色時的細描邊（NSStrokeWidth＝字級 %；負值＝描邊+填色）


def _screen():
    """有選單列的那個螢幕（screens()[0]）；拿不到就退 mainScreen。"""
    screens = NSScreen.screens()
    return screens[0] if screens and len(screens) else NSScreen.mainScreen()


class DanmakuOverlay:
    def __init__(self):
        self._win = None
        self._view = None
        self._sched: DanmakuScheduler | None = None
        self._flying: list = []          # [(CATextLayer, 到期時刻), ...]
        self._enabled = False
        self._last_geo = None            # 上次的 visibleFrame（偵測變化用）
        self._last_geo_check = 0.0
        # 顯示設定（configure() 覆寫）
        self._font_px = 20
        self._speed_px = 180.0
        self._opacity = 0.85
        self._area_pct = 40
        self._type_color = True
        self._show_author = True
        self._backdrop = True
        self._backdrop_alpha = 0.30

    # ---------- 設定 ----------

    def configure(self, *, font_px: int, speed_px: float, opacity: float,
                  area_pct: int, type_color: bool, show_author: bool,
                  backdrop: bool = True, backdrop_alpha: float = 0.30):
        """套用顯示設定；新彈幕立即生效（已在飛的不回頭改，飛完自然汰換）。"""
        self._font_px = int(font_px)
        self._speed_px = float(speed_px)
        self._opacity = float(opacity)
        self._area_pct = int(area_pct)
        self._type_color = bool(type_color)
        self._show_author = bool(show_author)
        self._backdrop = bool(backdrop)
        self._backdrop_alpha = float(backdrop_alpha)
        if self._win is not None:
            self._apply_geometry()

    def set_enabled(self, on: bool):
        on = bool(on)
        if on and self._win is None:
            self._build()
        self._enabled = on
        if self._win is None:
            return
        if on:
            self._win.orderFrontRegardless()
        else:
            self._clear_layers()
            if self._sched is not None:
                self._sched.clear()
            self._win.orderOut_(None)

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ---------- 餵彈幕 ----------

    def feed(self, symbol: str, author: str, content: str,
             kw: bool = False, now: float | None = None):
        """排入一則彈幕（推文到達時呼叫）。排程器會找空軌道讓它進場。"""
        if not self._enabled or self._sched is None:
            return
        text = (content or "").strip()
        if not text:
            return
        if self._show_author and author:
            text = f"{author}: {text}"
        if kw:
            text = "★ " + text     # 含關鍵字的醒目標記（同主視窗/狀態列）
        try:
            astr = self._attributed(text, symbol, kw)
            width = self._metrics(astr)[2]   # 排程用整顆膠囊（含留白）的寬
        except Exception:
            return
        self._sched.push((astr, symbol), width, self._speed_px,
                         time.monotonic() if now is None else now)

    def tick(self, now: float | None = None):
        """由 app 的 Timer 每幀呼叫：放行排隊彈幕、清飛完的 layer、追螢幕變化。"""
        if not self._enabled or self._win is None or self._sched is None:
            return
        now = time.monotonic() if now is None else now
        if now - self._last_geo_check > _GEO_CHECK_SEC:
            self._last_geo_check = now
            self._check_geometry()
        for item, lane, width, speed in self._sched.pop_ready(now):
            self._spawn(item[0], lane, width, speed, now)
        if self._flying and any(exp <= now for _l, exp in self._flying):
            keep = []
            for layer, exp in self._flying:
                if exp <= now:
                    try:
                        layer.removeFromSuperlayer()
                    except Exception:
                        pass
                else:
                    keep.append((layer, exp))
            self._flying = keep

    # ---------- 內部：視窗與幾何 ----------

    def _build(self):
        frame = _screen().visibleFrame()
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setHasShadow_(False)
        win.setIgnoresMouseEvents_(True)          # 點擊穿透：完全不擋操作
        win.setLevel_(NSStatusWindowLevel)        # 蓋過一般視窗
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
            | NSWindowCollectionBehaviorFullScreenAuxiliary)
        win.setReleasedWhenClosed_(False)
        view = NSView.alloc().initWithFrame_(((0.0, 0.0), tuple(frame.size)))
        view.setWantsLayer_(True)
        win.setContentView_(view)
        self._win, self._view = win, view
        self._last_geo = (tuple(frame.origin), tuple(frame.size))
        self._sched = DanmakuScheduler(self._lane_count(frame.size.height),
                                       float(frame.size.width))

    def _lane_h(self) -> float:
        return float(self._font_px) * 1.5 + _LANE_PAD

    def _lane_count(self, view_h: float) -> int:
        area_h = view_h * self._area_pct / 100.0
        return max(1, int(area_h // self._lane_h()))

    def _apply_geometry(self):
        """字級/範圍/螢幕改變後重算軌道數與視窗框。"""
        frame = _screen().visibleFrame()
        geo = (tuple(frame.origin), tuple(frame.size))
        if geo != self._last_geo:
            self._win.setFrame_display_(frame, False)
            self._view.setFrame_(((0.0, 0.0), tuple(frame.size)))
            self._last_geo = geo
        self._sched.resize(self._lane_count(frame.size.height),
                           float(frame.size.width))

    def _check_geometry(self):
        try:
            frame = _screen().visibleFrame()
            if (tuple(frame.origin), tuple(frame.size)) != self._last_geo:
                self._apply_geometry()
        except Exception:
            pass

    # ---------- 內部：繪製 ----------

    def _attributed(self, text: str, symbol: str, kw: bool) -> NSAttributedString:
        rgba = _TYPE_RGB.get(symbol, _WHITE) if self._type_color else _WHITE
        color = NSColor.colorWithSRGBRed_green_blue_alpha_(*rgba)
        font = NSFont.boldSystemFontOfSize_(float(self._font_px))
        attrs = {NSFontAttributeName: font,
                 NSForegroundColorAttributeName: color}
        if not self._backdrop:
            # 沒有底色時才描邊（細）＋柔影；有底色時文字保持乾淨
            attrs[NSStrokeColorAttributeName] = NSColor.blackColor()
            attrs[NSStrokeWidthAttributeName] = _STROKE_PCT
        return NSAttributedString.alloc().initWithString_attributes_(text, attrs)

    def _metrics(self, astr) -> tuple[float, float, float, float]:
        """(文字寬, 文字高, 外框寬, 外框高)。外框＝底色膠囊；無底色時只留 1px 呼吸。"""
        tw = float(astr.size().width)
        th = float(astr.size().height)
        if self._backdrop:
            return tw, th, tw + 2 * _PAD_X, th + 2 * _PAD_Y
        return tw, th, tw + 2.0, th + 2.0

    def _spawn(self, astr, lane: int, width: float, speed: float, now: float):
        """建一顆彈幕（容器 CALayer＋CATextLayer）放到指定軌道，交給 Core Animation 飛。"""
        view_w = float(self._view.frame().size.width)
        view_h = float(self._view.frame().size.height)
        lane_h = self._lane_h()
        tw, th, cw, ch = self._metrics(astr)
        # Cocoa y 軸朝上；lane 0 在最上面
        y = view_h - (lane + 1) * lane_h + (lane_h - ch) / 2.0
        dur = (view_w + cw) / max(1.0, speed)

        scale = 2.0
        try:
            scale = float(_screen().backingScaleFactor())
        except Exception:
            pass

        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        try:
            box = CALayer.layer()                  # 容器：底色膠囊（或透明框）
            box.setAnchorPoint_((0.0, 0.0))
            box.setBounds_(((0.0, 0.0), (cw, ch)))
            box.setPosition_((-cw, y))             # 最終落點：完全出左緣
            box.setOpacity_(self._opacity)
            if self._backdrop:
                box.setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(
                    *_BACKDROP_RGB, self._backdrop_alpha).CGColor())
                box.setCornerRadius_(ch / 2.0)     # 半高圓角＝膠囊

            text = CATextLayer.layer()
            text.setString_(astr)
            text.setContentsScale_(scale)          # Retina 不糊
            text.setAnchorPoint_((0.0, 0.0))
            text.setBounds_(((0.0, 0.0), (tw + 1.0, th)))
            text.setPosition_(((cw - tw) / 2.0, (ch - th) / 2.0))
            if not self._backdrop:
                # 無底色 → 柔影補對比（有底色就不需要，保持乾淨）
                text.setShadowColor_(NSColor.blackColor().CGColor())
                text.setShadowOpacity_(0.6)
                text.setShadowRadius_(1.5)
                text.setShadowOffset_((0.0, -1.0))
            box.addSublayer_(text)
            self._view.layer().addSublayer_(box)

            anim = CABasicAnimation.animationWithKeyPath_("position.x")
            anim.setFromValue_(view_w)
            anim.setToValue_(-cw)
            anim.setDuration_(dur)
            anim.setTimingFunction_(
                CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionLinear))
            box.addAnimation_forKey_(anim, "fly")
            self._flying.append((box, now + dur + 0.5))
        finally:
            CATransaction.commit()

    def _clear_layers(self):
        for layer, _exp in self._flying:
            try:
                layer.removeFromSuperlayer()
            except Exception:
                pass
        self._flying = []
