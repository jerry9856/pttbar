"""fav_parse.py — 修正 PyPtt「我的最愛」解析（看板名第一字被切掉的 bug）。

根因（M3 使用者實測 + 原始碼分析，見 NOTES.md）：
PyPtt `_api_get_favourite_board` 用固定字元位置切欄位（`x[10:...]`）。收藏列表裡
有「ˇ」標記的看板，ˇ 在畫面佔 2 格、在 Python 字串只佔 1 個字元 → 該行整體左移
一個字元 → 看板名第一個字母被切掉；沒有 ˇ 的行正常。

本模組：
- `parse_fav_lines(lines)`：**不靠固定欄位**、用 regex 解析每一行（純函式、可測）。
- `install()`：把 PyPtt 模組層的 `get_favourite_board` 換成使用上述解析的修正版
  （送指令/翻頁流程照抄 PyPtt，僅解析換掉）。watcher 匯入時呼叫。
"""

import re

import PyPtt
import PyPtt._api_get_favourite_board as _fav_mod
from PyPtt import _api_util, command, connect_core, exceptions, i18n
from PyPtt.data_type import FavouriteBoardField

# 行格式（去掉游標後）： 編號 [ˇ]看板名 類別 ◎中文敘述 …（右側還有人氣/板主欄）
# 看板名一定是 ASCII（字母數字開頭，可含 _ - .），這是 PTT 的規則。
_LINE_RE = re.compile(
    r"^\s*\d+\s*ˇ?\s*([A-Za-z0-9][A-Za-z0-9_\-.]*)\s+(\S+)\s*(.*)$")


def parse_fav_lines(lines: list[str]) -> list[dict]:
    """把「我的最愛」畫面的行解析成 [{board,type,title}]。容錯：不符合格式的行跳過。"""
    out = []
    for line in lines:
        if not line.strip() or "------------" in line:
            continue
        # 去掉行首游標（> 或 ●），避免影響 regex
        cleaned = re.sub(r"^[>●]", " ", line)
        m = _LINE_RE.match(cleaned)
        if not m:
            continue  # 資料夾（中文名）、分隔線等
        board, btype, rest = m.group(1), m.group(2), m.group(3)
        # 敘述欄：去掉開頭 ◎；右側的人氣/板主欄通常以連續空白分隔，切掉
        title = re.split(r"\s{2,}", rest.strip())[0]
        if title.startswith("◎"):
            title = title[1:]
        out.append({
            FavouriteBoardField.board: board,
            FavouriteBoardField.type: btype,
            FavouriteBoardField.title: title.strip(),
        })
    return out


def _get_favourite_board_fixed(api) -> list:
    """PyPtt get_favourite_board 的修正版：流程照抄、解析換成 parse_fav_lines。"""
    _api_util.one_thread(api)
    if not api._is_login:
        raise exceptions.RequireLogin(i18n.require_login)

    cmd = "".join([command.go_main_menu, "F", command.enter, "0"])
    target_list = [connect_core.TargetUnit("選擇看板", break_detect=True)]

    seen: set = set()
    result: list = []
    while True:
        api.connect_core.send(cmd, target_list)
        screen = api.connect_core.get_screen_queue()[-1]
        lines = screen.split("\n")[3:-1]

        parsed = parse_fav_lines(lines)
        new_count = 0
        for item in parsed:
            b = item[FavouriteBoardField.board]
            if b in seen:
                return result  # 翻頁繞回開頭 → 結束（與 PyPtt 原邏輯相同）
            seen.add(b)
            result.append(item)
            new_count += 1

        if new_count == 0 or len(parsed) < 20:
            break  # 最後一頁
        cmd = command.ctrl_f  # 下一頁

    return result


def install() -> None:
    """把修正版掛進 PyPtt（PTT.py 呼叫的是模組層函式，直接替換即可）。"""
    _fav_mod.get_favourite_board = _get_favourite_board_fixed
