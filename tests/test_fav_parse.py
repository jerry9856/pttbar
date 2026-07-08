"""tests/test_fav_parse.py — 我的最愛解析修正（看板名第一字被切 bug）。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fav_parse import parse_fav_lines  # noqa: E402


def test_board_with_check_mark_not_truncated():
    """關鍵回歸：有 ˇ 標記的行（PyPtt 舊法會把第一個字母切掉）要完整解析。"""
    lines = [
        ">     1 ˇC_Chat       閒談 ◎[希洽] 動漫討論     爆!mod1/mod2",
        "      2  Stock        學術 ◎[股版] 台股討論     99 modx",
        "      3 ˇBaseball     棒球 ◎[棒球] 板規v10      HOT mody",
    ]
    out = parse_fav_lines(lines)
    assert [b["board"] for b in out] == ["C_Chat", "Stock", "Baseball"]
    assert out[0]["type"] == "閒談"
    assert out[0]["title"].startswith("[希洽]")


def test_cursor_variants_and_folders_skipped():
    lines = [
        "●     1  Gossiping    綜合 ◎[八卦] 板規X",
        "      2  我的資料夾    目錄 ◎自訂",          # 資料夾（中文開頭）→ 跳過
        "     ------------------------------------",  # 分隔線 → 跳過
        "      3  NBA          籃球 ◎NBA 討論",
        "",
    ]
    out = parse_fav_lines(lines)
    assert [b["board"] for b in out] == ["Gossiping", "NBA"]


def test_title_right_columns_trimmed():
    lines = ["      1  Test         測試 ◎測試專用板          12 someone"]
    out = parse_fav_lines(lines)
    assert out[0]["title"] == "測試專用板"   # 右側人氣/板主欄被切掉


def test_empty_and_garbage():
    assert parse_fav_lines([]) == []
    assert parse_fav_lines(["not a board line", "   "]) == []
