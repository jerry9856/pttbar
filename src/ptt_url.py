"""ptt_url.py — 文章輸入解析（三種格式 → (board, aid)）。

純函式，不 import rumps/AppKit，可在 CLI 單元測試（tests/test_ptt_url.py）。

支援輸入（PLAN.md 6.1）：
  1. 完整網址：https://www.ptt.cc/bbs/Gossiping/M.1720000000.A.ABC.html
  2. 看板 + AID： "Gossiping #1abCdEfG"（AID 前的 # 可有可無）
  3. 看板 + 網址檔名："Gossiping M.1720000000.A.ABC"（.html 可有可無）

回傳的 aid 一律是 "#" + 8 碼的形式（PyPtt 的 get_post/check_aid 接受帶不帶 # 皆可，
見 NOTES.md）。網址檔名 → AID 用 PTT 公開的編碼方式（see filename_to_aid）。
"""

import re

# 與 PyPtt lib_util.aid_table 完全一致（M1 實測確認）。
AID_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"

# /bbs/<board>/M.<t>.A.<hex3>.html
_URL_RE = re.compile(r"/bbs/([\w-]+)/M\.(\d+)\.A\.([0-9A-Fa-f]{3})\.html")
# 網址檔名（不含看板、可不帶 .html）：M.<t>.A.<hex3>
_FILENAME_RE = re.compile(r"^M\.(\d+)\.A\.([0-9A-Fa-f]{3})(?:\.html)?$")


def filename_to_aid(timestamp: int, hex3: int) -> str:
    """(unix 時間, 3 碼 hex 值) → '#xxxxxxxx' 的 8 碼 AID。

    PTT 的編碼：v = (timestamp << 12) | hex3，之後每 6 bits 取一個字元，共 8 碼，反轉。
    """
    v = (timestamp << 12) | hex3
    chars = []
    for _ in range(8):
        chars.append(AID_CHARS[v & 0x3F])
        v >>= 6
    return "#" + "".join(reversed(chars))


def aid_to_filename(aid: str) -> str:
    """'#xxxxxxxx'（或不帶 #）→ 'M.<t>.A.<hex3>' 網址檔名（filename_to_aid 的反向）。"""
    a = aid[1:] if aid.startswith("#") else aid
    if len(a) != 8 or any(c not in AID_CHARS for c in a):
        raise ValueError(f"不是合法的 AID: {aid}")
    v = 0
    for c in a:
        v = (v << 6) | AID_CHARS.index(c)
    return f"M.{v >> 12}.A.{v & 0xFFF:03X}"


def url_to_board_aid(url: str) -> tuple[str, str]:
    """完整 PTT 網址 → (board, '#xxxxxxxx')。解析失敗 raise ValueError。"""
    m = _URL_RE.search(url)
    if not m:
        raise ValueError(f"無法解析 PTT 網址: {url}")
    board = m.group(1)
    timestamp = int(m.group(2))
    hex3 = int(m.group(3), 16)
    return board, filename_to_aid(timestamp, hex3)


def _normalize_aid(token: str) -> str:
    """把使用者輸入的 AID token（可帶 #）正規化成 '#xxxxxxxx'；不合法 raise ValueError。"""
    aid = token[1:] if token.startswith("#") else token
    if len(aid) != 8 or any(c not in AID_CHARS for c in aid):
        raise ValueError(f"不是合法的 AID: {token}")
    return "#" + aid


def parse_article_input(text: str) -> tuple[str, str]:
    """把三種格式的使用者輸入統一解析成 (board, '#xxxxxxxx')。

    解析不出來時 raise ValueError（訊息可直接顯示給使用者）。
    """
    text = text.strip()
    if not text:
        raise ValueError("請輸入文章網址、或『看板 AID』、或『看板 檔名』")

    # 格式 1：完整網址（含 /bbs/ 或 http）
    if "/bbs/" in text or text.startswith("http"):
        return url_to_board_aid(text)

    # 格式 2 / 3：看板 + token，用空白分隔
    parts = text.split()
    if len(parts) != 2:
        raise ValueError(f"無法解析輸入: {text!r}（預期『看板 AID』或『看板 檔名』或完整網址）")
    board, token = parts

    # 格式 3：網址檔名 M.<t>.A.<hex3>
    m = _FILENAME_RE.match(token)
    if m:
        return board, filename_to_aid(int(m.group(1)), int(m.group(2), 16))

    # 格式 2：AID（可帶 #）
    return board, _normalize_aid(token)
