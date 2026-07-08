"""web_fetch.py — PTT 網頁版讀取（爆文/直播文 fallback）。

PyPtt 對超大型/狂推中的直播文常解析不出推文（回傳 0 則，見 NOTES.md）。
此時 watcher 會改用本模組直接抓 www.ptt.cc 網頁版 HTML：一個 GET 就包含整篇所有推文，
對千推文又快又穩。requests 是 PyPtt 既有的鎖定依賴，沒有新增套件。

不 import rumps / PyPtt。HTML 用 regex 解析（PTT 網頁版 markup 多年穩定，不值得為此加 bs4）。
"""

import html as _html
import re
import threading

import requests

from ptt_url import aid_to_filename

# PTT 網頁版一則推文的結構（push-tag / push-userid / push-content / push-ipdatetime）
_PUSH_RE = re.compile(
    r'<div class="push">.*?push-tag">(.*?)</span>'
    r'.*?push-userid">(.*?)</span>'
    r'.*?push-content">(.*?)</span>'
    r'.*?push-ipdatetime">(.*?)</span>',
    re.S,
)
# 標題在文章開頭的 metaline：<span class="article-meta-tag">標題</span><span class="article-meta-value">...</span>
# （PTT 網頁版沒有 og:title，M3 實測）
_TITLE_RE = re.compile(
    r'article-meta-tag">標題</span>\s*<span class="article-meta-value">(.*?)</span>')
_OG_TITLE_RE = re.compile(r'<meta property="og:title" content="(.*?)"')  # 備援
_TIME_RE = re.compile(r"\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}")
_TAG_RE = re.compile(r"<[^>]+>")  # 去除巢狀 tag（如推文裡的 <a href>）


class WebNotFound(Exception):
    """文章網頁 404（不存在或已刪除）。"""


_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; pttbar)"}

# session 改 thread-local：瀏覽請求跑獨立背景執行緒（加速），與 watcher worker 併發使用
# requests.Session 的連線池非嚴格 thread-safe → 每條執行緒各一個 session（保留 keep-alive）。
_tls = threading.local()


def _get_session() -> "requests.Session":
    s = getattr(_tls, "session", None)
    if s is None:
        s = requests.Session()
        s.cookies.set("over18", "1", domain=".ptt.cc")  # 十八禁看板（如 Gossiping）需要
        _tls.session = s
    return s


def parse_post_html(html_text: str) -> tuple[str | None, list[dict]]:
    """從網頁 HTML 解析 (標題, 推文列表)。推文 dict 與 PyPtt comment 同形
    （type/author/content/time；type 直接是 '推'/'噓'/'→'，CommentData.from_ptt 可原樣吃）。"""
    m = _TITLE_RE.search(html_text) or _OG_TITLE_RE.search(html_text)
    title = _html.unescape(_TAG_RE.sub("", m.group(1))).strip() if m else None
    # 註：直播文若被作者把文章表頭編輯掉（M3 實測 MSI 文），metaline 不存在 → title=None，
    # watcher 只在網頁有回傳標題時才更新 UI，此時沿用 PyPtt 從看板列表抓到的標題。

    comments = []
    for tag, userid, content, ipdt in _PUSH_RE.findall(html_text):
        ctype = _html.unescape(_TAG_RE.sub("", tag)).strip()
        author = _html.unescape(_TAG_RE.sub("", userid)).strip()
        text = _html.unescape(_TAG_RE.sub("", content)).strip()
        if text.startswith(":"):
            text = text[1:].strip()
        tm = _TIME_RE.search(ipdt)
        comments.append({
            "type": ctype or "→",
            "author": author,
            "content": text,
            "time": tm.group(0) if tm else "",
            "ip": None,
        })
    return title, comments


# ---- 看板文章列表（網頁版 index 頁）----
# 結構（M3 實測 www.ptt.cc/bbs/<board>/index.html）：
#   <div class="r-ent"> nrec / title(<a href=文章網址>標題</a>；被刪文無<a>) / author / date
#   <div class="r-list-sep"> 之後的 r-ent 是置頂文（僅最新頁有）
#   上頁按鈕 href="/bbs/<board>/index<N>.html">&lsaquo;（拿 N 可一直往回翻 = 載入更多）

_PREV_RE = re.compile(r'href="/bbs/[^"]+/index(\d+)\.html">\s*&lsaquo;')
_TITLE_LINK_RE = re.compile(r'<div class="title">\s*<a href="([^"]+)">([^<]*)</a>', re.S)
_NREC_RE = re.compile(r'"nrec">(?:<span[^>]*>)?([^<]*)')
_AUTHOR_RE = re.compile(r'"author">([^<]*)')
_DATE_RE = re.compile(r'"date">([^<]*)')


def _parse_ents(chunk_html: str) -> list[dict]:
    """把一段 HTML 裡的 r-ent 區塊解析成文章列表（被刪文跳過）。"""
    posts = []
    for block in chunk_html.split('<div class="r-ent">')[1:]:
        m = _TITLE_LINK_RE.search(block)
        if not m:
            continue  # 被刪除的文章沒有連結
        url, title = m.group(1), _html.unescape(m.group(2)).strip()
        nrec = _NREC_RE.search(block)
        author = _AUTHOR_RE.search(block)
        date = _DATE_RE.search(block)
        posts.append({
            "url": "https://www.ptt.cc" + url if url.startswith("/") else url,
            "title": title,
            "nrec": (nrec.group(1) if nrec else "").strip(),
            "author": (author.group(1) if author else "").strip(),
            "date": (date.group(1) if date else "").strip(),
        })
    return posts


def parse_board_list_html(html_text: str) -> dict:
    """網頁版看板 index 頁 → {posts, pinned, prev}。posts/pinned 皆頁面順序（舊→新）。"""
    m = _PREV_RE.search(html_text)
    prev = int(m.group(1)) if m else None
    sep = html_text.find('<div class="r-list-sep"')
    if sep >= 0:
        return {"posts": _parse_ents(html_text[:sep]),
                "pinned": _parse_ents(html_text[sep:]), "prev": prev}
    return {"posts": _parse_ents(html_text), "pinned": [], "prev": prev}


def fetch_board_list_web(board: str, page: int | None = None,
                         timeout: float = 10.0) -> dict:
    """抓看板文章列表。page=None 為最新頁；否則抓 index<page>.html（載入更多用）。
    404 raise WebNotFound；其他網路錯誤原樣拋出。"""
    name = f"index{page}.html" if page else "index.html"
    url = f"https://www.ptt.cc/bbs/{board}/{name}"
    r = _get_session().get(url, headers=_HEADERS, timeout=timeout)
    if r.status_code == 404:
        raise WebNotFound(url)
    r.raise_for_status()
    return parse_board_list_html(r.text)


def fetch_post_web(board: str, aid: str, timeout: float = 10.0) -> tuple[str | None, list[dict]]:
    """抓網頁版文章 → (標題, 推文列表)。404 raise WebNotFound；其他網路錯誤原樣拋出。"""
    url = f"https://www.ptt.cc/bbs/{board}/{aid_to_filename(aid)}.html"
    r = _get_session().get(url, headers=_HEADERS, timeout=timeout)
    if r.status_code == 404:
        raise WebNotFound(url)
    r.raise_for_status()
    return parse_post_html(r.text)
