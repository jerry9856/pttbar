"""tests/test_raw_parse.py — 從 full_content 原始文字解析推文。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from raw_parse import parse_comments_from_text  # noqa: E402

_SAMPLE = "\n".join([
    "手機好讀 https://i.imgur.com/xxx.png",       # 表頭被編輯掉的直播文內文
    "",
    "比分更新：TES 1 - 0 T1",
    "推薦大家看這場",                              # 「推」開頭但非推文格式 → 不可誤認
    "推 alice: 加油        07/04 18:00",
    "噓 bob: 樓上錯了 1.2.3.4 07/04 18:01",       # 帶 IP
    "→ carol: 1 > 2 對吧   07/04 18:02",
    "推文以外的一般內文行",
])


def test_parse_three_types():
    cs = parse_comments_from_text(_SAMPLE)
    assert [c["type"] for c in cs] == ["PUSH", "BOO", "ARROW"]
    assert [c["author"] for c in cs] == ["alice", "bob", "carol"]


def test_content_and_time():
    cs = parse_comments_from_text(_SAMPLE)
    assert cs[0]["content"] == "加油"
    assert cs[0]["time"] == "07/04 18:00"
    assert cs[2]["content"] == "1 > 2 對吧"


def test_ip_stripped_from_content():
    cs = parse_comments_from_text(_SAMPLE)
    assert cs[1]["ip"] == "1.2.3.4"
    assert cs[1]["content"] == "樓上錯了"


def test_body_noise_not_matched():
    cs = parse_comments_from_text(_SAMPLE)
    assert len(cs) == 3  # 「推薦大家看這場」等內文行沒被誤認


def test_empty_text():
    assert parse_comments_from_text("") == []


def test_compatible_with_commentdata():
    from models import CommentData
    cs = parse_comments_from_text(_SAMPLE)
    cd = CommentData.from_ptt(cs[0], 0)
    assert cd.type == "推" and cd.author == "alice"
