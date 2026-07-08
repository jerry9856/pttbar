"""make_icon.py — 產生 PTTBar 的 app icon（可重現）。

用 AppKit 原生繪圖畫 1024x1024 母圖（不需額外套件），再由 build_icns.sh
（sips + iconutil）產出 PTTBar.icns 給 py2app 用。

設計：Big Sur 大圓角方形、深色終端機底（微綠漸層），三條彈幕膠囊
（推綠/→黃/噓紅，右側拖尾表示往左飛），綠色主膠囊放等寬字 PTT 字樣。

執行：
    ./venv/bin/python assets/make_icon.py   # 產出 assets/icon_1024.png
"""

import os

from AppKit import (
    NSAttributedString,
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSDeviceRGBColorSpace,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGradient,
    NSGraphicsContext,
    NSShadow,
)
from Foundation import NSMakeRect

S = 1024                     # 母圖尺寸
INSET = 100                  # Big Sur 模板：內容 824x824 置中
RADIUS = 186                 # 大圓角（近似 Apple squircle）

def rgba(r, g, b, a=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)

# 配色：背景取終端機深色（微綠），膠囊取 app 的 vivid 色系（推/→/噓）
BG_TOP = rgba(0.055, 0.075, 0.095)
BG_BOT = rgba(0.075, 0.125, 0.105)
GREEN, GREEN_TXT = rgba(0.24, 0.83, 0.40), rgba(0.015, 0.22, 0.09)
YELLOW = rgba(1.00, 0.80, 0.10)
RED = rgba(1.00, 0.30, 0.26)


def capsule(x, y, w, h, color, glow=0.55):
    """畫一顆發光膠囊（y 為中心線）。"""
    path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y - h / 2.0, w, h), h / 2.0, h / 2.0)
    NSGraphicsContext.currentContext().saveGraphicsState()
    if glow > 0:
        sh = NSShadow.alloc().init()
        sh.setShadowColor_(color.colorWithAlphaComponent_(glow))
        sh.setShadowBlurRadius_(46.0)
        sh.setShadowOffset_((0.0, 0.0))
        sh.set()
    color.setFill()
    path.fill()
    NSGraphicsContext.currentContext().restoreGraphicsState()


def trail(x_right, y, h, color):
    """膠囊右側的兩節細速度線（彈幕往左飛的動態感）。"""
    th = h * 0.30
    for dx, w, a in ((30, 120, 0.32), (186, 60, 0.14)):
        p = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x_right + dx, y - th / 2.0, w, th), th / 2.0, th / 2.0)
        color.colorWithAlphaComponent_(a).setFill()
        p.fill()


def draw():
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, S, S, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)

    # --- 底板：大圓角方形 + 垂直漸層 + 淡淡的頂光與內框 ---
    board = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(INSET, INSET, S - 2 * INSET, S - 2 * INSET), RADIUS, RADIUS)
    NSGradient.alloc().initWithStartingColor_endingColor_(
        BG_BOT, BG_TOP).drawInBezierPath_angle_(board, 90.0)
    board.addClip()   # 之後所有東西都裁在底板內

    # 頂部 1/3 淡白高光（玻璃感）
    hi = NSBezierPath.bezierPathWithRect_(NSMakeRect(INSET, S - INSET - 250,
                                                     S - 2 * INSET, 250))
    NSGradient.alloc().initWithStartingColor_endingColor_(
        rgba(1, 1, 1, 0.0), rgba(1, 1, 1, 0.05)).drawInBezierPath_angle_(hi, 90.0)

    # 細掃描線（終端機質感，極淡）
    rgba(1, 1, 1, 0.016).setFill()
    y = INSET + 14
    while y < S - INSET:
        NSBezierPath.fillRect_(NSMakeRect(INSET, y, S - 2 * INSET, 3.0))
        y += 26

    # --- 三條彈幕（右側拖尾＝往左飛）---
    # 上：紅（噓），短，靠右
    capsule(470, 742, 330, 118, RED)
    trail(470 + 330, 742, 118, RED)
    # 中：綠（推），主角，放 PTT 字樣
    capsule(168, 512, 520, 168, GREEN, glow=0.70)
    trail(168 + 520, 512, 168, GREEN)
    # 下：黃（→），中等，偏右
    capsule(360, 282, 300, 118, YELLOW)
    trail(360 + 300, 282, 118, YELLOW)

    # PTT 字樣（等寬粗體，置中在綠膠囊）
    font = NSFont.fontWithName_size_("Menlo-Bold", 118.0) or \
        NSFont.boldSystemFontOfSize_(118.0)
    astr = NSAttributedString.alloc().initWithString_attributes_(
        "PTT", {NSFontAttributeName: font,
                NSForegroundColorAttributeName: GREEN_TXT})
    tsz = astr.size()
    astr.drawAtPoint_((168 + (520 - tsz.width) / 2.0,
                       512 - tsz.height / 2.0 + 4))

    # 內框細線（深色桌面上更有型）
    rgba(1, 1, 1, 0.07).setStroke()
    edge = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(INSET + 2, INSET + 2, S - 2 * INSET - 4, S - 2 * INSET - 4),
        RADIUS - 2, RADIUS - 2)
    edge.setLineWidth_(4.0)
    edge.stroke()

    NSGraphicsContext.restoreGraphicsState()

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_1024.png")
    png = rep.representationUsingType_properties_(4, None)   # 4 = PNG
    png.writeToFile_atomically_(out, True)
    print("wrote", out)


if __name__ == "__main__":
    draw()
