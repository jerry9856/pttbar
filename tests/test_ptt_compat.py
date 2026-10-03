"""ptt_compat：新舊版 PTT 主選單狀態列都要被認成「已登入」。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyPtt import screens  # noqa: E402

import ptt_compat  # noqa: E402

NEW_STATUS = "10/3周六 17:31 [ 射手時 ]    線上25655人,我是hayoung98,呼叫器開啟       (h)說明"
OLD_STATUS = "[10/3 星期六 17:31] [ 射手時 ]  線上25655人, 我是hayoung98   [呼叫器]打開"
MENU_BODY = "【主功能表】\n  (G)oodbye         離開，再見…\n"


def _is_main_menu(screen: str) -> bool:
    # 與 PyPtt _api_loginout.login 的判斷相同
    return all(t in screen for t in screens.Target.MainMenu)


def test_new_status_bar_recognised():
    ptt_compat.install()
    assert _is_main_menu(MENU_BODY + NEW_STATUS)


def test_old_status_bar_still_recognised():
    ptt_compat.install()
    assert _is_main_menu(MENU_BODY + OLD_STATUS)


def test_non_menu_screen_rejected():
    ptt_compat.install()
    assert not _is_main_menu("請輸入代號，或以 guest 參觀，或以 new 註冊:")


def test_install_idempotent_and_goodbye_prefix_synced():
    ptt_compat.install()
    ptt_compat.install()
    assert screens.Target.MainMenu == ptt_compat.MAIN_MENU
    n = len(screens.Target.MainMenu)
    assert screens.Target.CursorToGoodbye[:n] == ptt_compat.MAIN_MENU
