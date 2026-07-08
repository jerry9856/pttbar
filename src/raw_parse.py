"""raw_parse.py — 從 PyPtt 的 full_content 原始文字解析推文（websocket 即時路徑）。

背景（NOTES.md）：表頭被作者編輯掉的直播文，PyPtt `_parse_post_content` 解析失敗、
comments 回傳 0；但 websocket 抓回的整篇原始文字仍存在 `post['full_content']`
（_api_get_post.py:190，在格式檢查「之前」就已寫入）。本模組用與 PyPtt `_parse_comments`
相同的規則從原始文字直接解析推文，讓這類文章**不離開 websocket 連線**、維持即時。

輸出 dict 與 PyPtt comment 同形：{'type': 'PUSH'/'BOO'/'ARROW', 'author', 'content',
'time', 'ip'}，CommentData.from_ptt 可直接吃。

限制（與 PyPtt 本身相同）：內文若有長得完全像推文的行（推/噓/→ + 空格 + id: 內容 + 日期時間）
會被誤認，實務上極罕見。
"""

import re

# 與 PyPtt _api_get_post._parse_comments 相同的 pattern
_AUTHOR_RE = re.compile(r"[推|噓|→] [\w| ]+:")
_DATE_RE = re.compile(r"[\d]+/[\d]+ [\d]+:[\d]+")
_IP_RE = re.compile(r"[\d]+\.[\d]+\.[\d]+\.[\d]+")


def parse_comments_from_text(full_content: str) -> list[dict]:
    """從整篇原始文字解析推文列表；解析不到就回空 list。"""
    comments = []
    for line in full_content.split("\n"):
        if line.startswith("推"):
            ctype = "PUSH"
        elif line.startswith("噓 "):
            ctype = "BOO"
        elif line.startswith("→ "):
            ctype = "ARROW"
        else:
            continue

        m = _AUTHOR_RE.search(line)
        if m is None:
            continue  # 不是推文格式的行（例如內文以「推薦」開頭）
        author = m.group(0)[2:-1].strip()

        d = _DATE_RE.search(line)
        if d is None:
            continue
        push_date = d.group(0)

        ip = None
        im = _IP_RE.search(line)
        if im is not None:
            ip = im.group(0)

        content = line[line.find(author) + len(author):]
        content = content[:content.rfind(push_date)]  # PTT1 版型（見 PyPtt 同處）
        if ip:
            content = content.replace(ip, "")
        content = content[content.find(":") + 1:].strip()

        comments.append({
            "type": ctype,
            "author": author,
            "content": content,
            "time": push_date,
            "ip": ip,
        })
    return comments
