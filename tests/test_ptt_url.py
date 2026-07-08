"""tests/test_ptt_url.py — ptt_url 三種輸入格式解析的單元測試。

策略：
- 網址 → AID 的轉換，用 **PyPtt 自帶的 lib_util.get_aid_from_url 當黃金對照**交叉驗證。
  這比寫死一組更強：直接證明我們算出的 AID 跟「實際會拿去抓文的那個函式庫」一致。
  （已驗算兩者演算法數學等價：低 12 bits=hex3→後 2 碼，高位=timestamp→前 6 碼。）
- 三種格式（網址 / 看板+AID / 看板+檔名）殊途同歸應解析出相同 (board, aid)。
- 錯誤輸入應 raise ValueError。

真實文章的端對端驗證（拿網址轉 AID 再用 PyPtt 抓到同一篇）在 poc.py，需真實帳號，
由使用者執行（PLAN.md 6.1 / M1 驗收）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ptt_url import (  # noqa: E402
    aid_to_filename,
    filename_to_aid,
    parse_article_input,
    url_to_board_aid,
)

from PyPtt import lib_util  # noqa: E402  黃金對照


# 涵蓋不同 timestamp 與 hex3（含邊界 000 / FFF）的合成網址。
_CASES = [
    ("Gossiping", 1720000000, "ABC"),
    ("Test", 1720000000, "000"),
    ("Test", 1720000000, "FFF"),
    ("C_Chat", 1, "001"),
    ("Stock", 1656000000, "1A3"),
    ("movie", 1699999999, "abc".upper()),
]


def _url(board: str, ts: int, hex3: str) -> str:
    return f"https://www.ptt.cc/bbs/{board}/M.{ts}.A.{hex3}.html"


@pytest.mark.parametrize("board,ts,hex3", _CASES)
def test_url_matches_pyptt_golden(board, ts, hex3):
    """我們的 url_to_board_aid 必須與 PyPtt.get_aid_from_url 給出相同結果。"""
    url = _url(board, ts, hex3)
    got_board, got_aid = url_to_board_aid(url)
    ref_board, ref_aid = lib_util.get_aid_from_url(url)
    assert got_board == ref_board == board
    # 我們回傳帶 #，PyPtt 不帶；去掉 # 後應完全相同，且為 8 碼。
    assert got_aid == "#" + ref_aid
    assert len(ref_aid) == 8


@pytest.mark.parametrize("board,ts,hex3", _CASES)
def test_three_formats_agree(board, ts, hex3):
    """三種輸入格式應解析出相同的 (board, aid)。"""
    url = _url(board, ts, hex3)
    board_u, aid = url_to_board_aid(url)

    # 格式 1：完整網址
    assert parse_article_input(url) == (board, aid)
    # 格式 3：看板 + 檔名（帶/不帶 .html 皆可）
    assert parse_article_input(f"{board} M.{ts}.A.{hex3}") == (board, aid)
    assert parse_article_input(f"{board} M.{ts}.A.{hex3}.html") == (board, aid)
    # 格式 2：看板 + AID（帶/不帶 # 皆可）
    assert parse_article_input(f"{board} {aid}") == (board, aid)
    assert parse_article_input(f"{board} {aid[1:]}") == (board, aid)


@pytest.mark.parametrize("board,ts,hex3", _CASES)
def test_aid_to_filename_roundtrip(board, ts, hex3):
    """aid_to_filename 是 filename_to_aid 的反向：轉過去再轉回來要一致。"""
    _, aid = url_to_board_aid(_url(board, ts, hex3))
    assert aid_to_filename(aid) == f"M.{ts}.A.{hex3.upper()}"
    # 不帶 # 也可
    assert aid_to_filename(aid[1:]) == f"M.{ts}.A.{hex3.upper()}"


def test_aid_to_filename_rejects_bad():
    with pytest.raises(ValueError):
        aid_to_filename("#short")
    with pytest.raises(ValueError):
        aid_to_filename("#1abCdEf!")


def test_filename_to_aid_is_8_chars():
    aid = filename_to_aid(1720000000, 0xABC)
    assert aid.startswith("#") and len(aid) == 9  # '#' + 8


def test_whitespace_is_tolerated():
    assert parse_article_input("  Gossiping   #1abCdEfG  ") == ("Gossiping", "#1abCdEfG")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "not a url",
        "Gossiping",                    # 少了 AID
        "Gossiping abc",                # AID 長度不對
        "Gossiping #short",             # AID 長度不對
        "Gossiping #1abCdEf!",          # 含非法字元
        "Gossiping M.abc.A.ABC",        # 檔名 timestamp 非數字
        "https://www.ptt.cc/bbs/Gossiping/index.html",  # 非文章網址
    ],
)
def test_invalid_inputs_raise(bad):
    with pytest.raises(ValueError):
        parse_article_input(bad)
