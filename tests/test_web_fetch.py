"""tests/test_web_fetch.py — PTT 網頁版 HTML 解析測試（不連網）。

樣本 HTML 取自 www.ptt.cc 文章頁的實際 markup 結構（push-tag/push-userid/
push-content/push-ipdatetime + og:title meta）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from web_fetch import parse_post_html  # noqa: E402

_SAMPLE = """<!DOCTYPE html>
<html><head>
<meta property="og:title" content="[電競] 2026 MSI-分組賽 D2 &amp; 測試">
</head><body>
<div id="main-content">
內文...
<div class="push"><span class="hl push-tag">推 </span><span class="f3 hl push-userid">alice</span><span class="f3 push-content">: 加油</span><span class="push-ipdatetime"> 1.2.3.4 07/04 18:00
</span></div>
<div class="push"><span class="f1 hl push-tag">噓 </span><span class="f3 hl push-userid">bob</span><span class="f3 push-content">: <a href="https://x.example">https://x.example</a> 樓上錯了</span><span class="push-ipdatetime"> 07/04 18:01
</span></div>
<div class="push"><span class="f1 hl push-tag">→ </span><span class="f3 hl push-userid">carol</span><span class="f3 push-content">: 1 &gt; 2 對吧</span><span class="push-ipdatetime"> 07/04 18:02
</span></div>
</div>
</body></html>"""


def test_parse_title_unescapes_entities():
    # _SAMPLE 只有 og:title → 走備援路徑
    title, _ = parse_post_html(_SAMPLE)
    assert title == "[電競] 2026 MSI-分組賽 D2 & 測試"


def test_parse_title_from_metaline():
    """真實 PTT 網頁版沒有 og:title，標題在 article-metaline（M3 實測）。"""
    html = ('<div class="article-metaline"><span class="article-meta-tag">標題</span>'
            '<span class="article-meta-value">[電競] 2026 MSI D2</span></div>')
    title, _ = parse_post_html(html)
    assert title == "[電競] 2026 MSI D2"


def test_parse_three_pushes_with_types():
    _, comments = parse_post_html(_SAMPLE)
    assert len(comments) == 3
    assert [c["type"] for c in comments] == ["推", "噓", "→"]
    assert [c["author"] for c in comments] == ["alice", "bob", "carol"]


def test_parse_strips_nested_tags_and_colon():
    _, comments = parse_post_html(_SAMPLE)
    assert comments[0]["content"] == "加油"
    assert comments[1]["content"] == "https://x.example 樓上錯了"  # <a> 已剝掉
    assert comments[2]["content"] == "1 > 2 對吧"                  # entities 已還原


def test_parse_times():
    _, comments = parse_post_html(_SAMPLE)
    assert [c["time"] for c in comments] == ["07/04 18:00", "07/04 18:01", "07/04 18:02"]


def test_parse_empty_page():
    title, comments = parse_post_html("<html><body>nothing</body></html>")
    assert title is None and comments == []


def test_comment_dict_compatible_with_commentdata():
    """網頁版推文 dict 應可直接餵給 CommentData.from_ptt（type 已是 推/噓/→ 原樣保留）。"""
    from models import CommentData
    _, comments = parse_post_html(_SAMPLE)
    cd = CommentData.from_ptt(comments[0], 0)
    assert cd.type == "推" and cd.author == "alice" and cd.content == "加油"
    cd2 = CommentData.from_ptt(comments[1], 1)
    assert cd2.type == "噓"


# ---- 看板文章列表（網頁版）----

from web_fetch import parse_board_list_html  # noqa: E402

_LIST_SAMPLE = """
<div class="btn-group btn-group-paging">
  <a class="btn wide" href="/bbs/Stock/index10180.html">&lsaquo; 上頁</a>
</div>
<div class="r-ent">
  <div class="nrec"><span class="hl f3">25</span></div>
  <div class="title">
    <a href="/bbs/Stock/M.1783151802.A.F42.html">[新聞] 三星 &amp; 台積電</a>
  </div>
  <div class="meta"><div class="author">win8719</div><div class="date"> 7/04</div></div>
</div>
<div class="r-ent">
  <div class="nrec"></div>
  <div class="title">
    (本文已被刪除) [someone]
  </div>
  <div class="meta"><div class="author">-</div><div class="date"> 7/04</div></div>
</div>
<div class="r-ent">
  <div class="nrec"><span class="hl f1">爆</span></div>
  <div class="title">
    <a href="/bbs/Stock/M.1783151900.A.AAA.html">[標的] 大盤多</a>
  </div>
  <div class="meta"><div class="author">bull</div><div class="date"> 7/04</div></div>
</div>
<div class="r-list-sep"></div>
<div class="r-ent">
  <div class="nrec"></div>
  <div class="title">
    <a href="/bbs/Stock/M.1000000000.A.111.html">[公告] 板規 2026</a>
  </div>
  <div class="meta"><div class="author">mod</div><div class="date"> 1/01</div></div>
</div>
"""


def test_board_list_posts_and_pinned_split():
    data = parse_board_list_html(_LIST_SAMPLE)
    assert [p["title"] for p in data["posts"]] == ["[新聞] 三星 & 台積電", "[標的] 大盤多"]
    assert [p["title"] for p in data["pinned"]] == ["[公告] 板規 2026"]


def test_board_list_deleted_skipped_and_fields():
    data = parse_board_list_html(_LIST_SAMPLE)
    p = data["posts"][0]
    assert p["url"] == "https://www.ptt.cc/bbs/Stock/M.1783151802.A.F42.html"
    assert p["nrec"] == "25" and p["author"] == "win8719" and p["date"] == "7/04"
    assert data["posts"][1]["nrec"] == "爆"


def test_board_list_prev_page():
    data = parse_board_list_html(_LIST_SAMPLE)
    assert data["prev"] == 10180
    assert parse_board_list_html("<html></html>")["prev"] is None


def test_board_list_url_parses_to_aid():
    """列表的 URL 應可直接餵 parse_article_input 得到 (board, aid)。"""
    from ptt_url import parse_article_input
    data = parse_board_list_html(_LIST_SAMPLE)
    board, aid = parse_article_input(data["posts"][0]["url"])
    assert board == "Stock" and aid.startswith("#") and len(aid) == 9
