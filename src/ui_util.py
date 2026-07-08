"""ui_util.py — UI 用的純函式（不 import rumps/AppKit，可單元測試）。

目前只有「依全形字寬度截斷字串」，給狀態列標題與選單項目用（PLAN.md 6.5）。
全形字（中日韓）算 2 個寬度單位、半形算 1；所以「約 30 全形字」= 60 個寬度單位。
"""

import unicodedata

_WIDE = ("F", "W")  # east_asian_width：F=Fullwidth, W=Wide


def display_width(s: str) -> int:
    """字串的顯示寬度（全形算 2、半形算 1）。"""
    return sum(2 if unicodedata.east_asian_width(c) in _WIDE else 1 for c in s)


# 膠囊主題色票（F5）：name -> {label, colors: {推/→/噓: (bg RGBA, fg RGBA)}}，值 0..1。
# 純資料（不依賴 AppKit），app 端轉成 NSColor。
PILL_PALETTES: dict = {
    "ios": {
        "label": "iOS 淡色",
        "colors": {
            "推": ((0.62, 0.90, 0.66, 0.82), (0.05, 0.38, 0.15, 1.0)),
            "→": ((0.99, 0.90, 0.60, 0.82), (0.45, 0.33, 0.02, 1.0)),
            "噓": ((0.99, 0.68, 0.68, 0.82), (0.55, 0.08, 0.08, 1.0)),
        },
    },
    "vivid": {
        "label": "鮮明",
        "colors": {
            "推": ((0.20, 0.78, 0.35, 0.92), (1.0, 1.0, 1.0, 1.0)),
            "→": ((1.00, 0.80, 0.00, 0.92), (0.25, 0.18, 0.0, 1.0)),
            "噓": ((1.00, 0.23, 0.19, 0.92), (1.0, 1.0, 1.0, 1.0)),
        },
    },
    "colorblind": {
        "label": "色盲友善",
        "colors": {
            "推": ((0.35, 0.62, 0.92, 0.88), (1.0, 1.0, 1.0, 1.0)),    # 藍
            "→": ((0.85, 0.85, 0.85, 0.85), (0.15, 0.15, 0.15, 1.0)),  # 灰
            "噓": ((0.95, 0.55, 0.15, 0.90), (1.0, 1.0, 1.0, 1.0)),    # 橙
        },
    },
    "mono": {
        "label": "低調",
        "colors": {
            "推": ((0.55, 0.55, 0.58, 0.35), (0.10, 0.55, 0.25, 1.0)),
            "→": ((0.55, 0.55, 0.58, 0.35), (0.55, 0.42, 0.05, 1.0)),
            "噓": ((0.55, 0.55, 0.58, 0.35), (0.75, 0.15, 0.15, 1.0)),
        },
    },
}


def get_palette(name: str) -> dict:
    """取主題色票；不認得的名字回預設 ios。"""
    return PILL_PALETTES.get(name, PILL_PALETTES["ios"])


def parse_keywords(text: str) -> list[str]:
    """把使用者輸入的關鍵字字串（逗號/空白分隔）解析成去重的關鍵字列表。"""
    if not text:
        return []
    parts = [p.strip() for p in text.replace("，", ",").replace(",", " ").split()]
    seen, out = set(), []
    for p in parts:
        low = p.lower()
        if p and low not in seen:
            seen.add(low)
            out.append(p)
    return out


def match_keywords(text: str, keywords: list[str]) -> bool:
    """text 是否含任一關鍵字（不分大小寫）。keywords 空 → False。"""
    if not keywords or not text:
        return False
    low = text.lower()
    return any(k.lower() in low for k in keywords)


def is_continuation(*, prev_type: str, prev_author: str, prev_time: str,
                    prev_index: int, new_type: str, new_author: str,
                    new_time: str, new_index: int) -> bool:
    """判斷新推文是否為前一則被 PTT 切開的接續段（自動合併長留言用）。

    PTT 對過長留言會切成多則連續推文：同帳號、原始序號相連、時間戳相同；
    接續段的型別與首段相同，或因連推限制改用「→」。
    prev_index 是前一則「最後一段」的原始序號（合併過就是最後併入段的序號）。
    """
    return (bool(new_author) and new_author == prev_author
            and new_index == prev_index + 1
            and (new_type == prev_type or new_type == "→")
            and bool(new_time) and new_time == prev_time)


def comment_stats(entries: list[dict], now: float, buckets: int = 15) -> dict:
    """推文統計（F2）。entries: [{'t':推/噓/→, 'a':作者, 'ts':抵達秒}]，now: 目前時間。

    回傳 {push, boo, arrow, total, rate10(近10分鐘每分鐘平均), top:[(作者,數)x5],
          buckets:[近 buckets 分鐘、每分鐘則數，舊→新]}。
    """
    push = sum(1 for e in entries if e.get("t") == "推")
    boo = sum(1 for e in entries if e.get("t") == "噓")
    arrow = len(entries) - push - boo

    recent = [e for e in entries if now - e.get("ts", 0) <= 600]
    rate10 = round(len(recent) / 10.0, 1)

    counts: dict[str, int] = {}
    for e in entries:
        a = e.get("a") or ""
        if a:
            counts[a] = counts.get(a, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]

    per_min = [0] * buckets
    for e in entries:
        age_min = int((now - e.get("ts", 0)) // 60)
        if 0 <= age_min < buckets:
            per_min[buckets - 1 - age_min] += 1

    return {"push": push, "boo": boo, "arrow": arrow, "total": len(entries),
            "rate10": rate10, "top": top, "buckets": per_min}


def truncate(s: str, max_units: int, ellipsis: str = "…") -> str:
    """超過 max_units 寬度就截斷並加上省略號（省略號本身佔 1 寬度）。"""
    if display_width(s) <= max_units:
        return s
    budget = max_units - display_width(ellipsis)
    width = 0
    out = []
    for c in s:
        cw = 2 if unicodedata.east_asian_width(c) in _WIDE else 1
        if width + cw > budget:
            break
        out.append(c)
        width += cw
    return "".join(out) + ellipsis
