"""ptt_compat.py — 修正 PyPtt 2.1.3 認不出新版 PTT 主選單（登入永遠失敗）。

根因（2026-10 使用者實測，見 NOTES.md）：
PyPtt 用 `screens.Target.MainMenu` 的三個字串都出現在畫面上來判斷「已進主選單」，
其中狀態列兩個是舊格式：`人, 我是`、`[呼叫器]`。PTT 改版後狀態列變成
`線上25655人,我是hayoung98,呼叫器開啟`（逗號後無空格、呼叫器沒有中括號）→
明明登入成功，PyPtt 仍丟 `LoginError`。

本模組 `install()` 把判斷字串放寬成新舊格式都吻合的版本。watcher 匯入時呼叫。
"""

from PyPtt import screens

# 新舊格式都吻合：舊「人, 我是 … [呼叫器]」、新「人,我是 … 呼叫器開啟」
MAIN_MENU = ["離開，再見", "我是", "呼叫器"]


def install() -> None:
    target = screens.Target
    n_old = len(target.MainMenu)
    # 原地修改：PyPtt 其他地方持有的是同一個 list 參照
    target.MainMenu[:] = MAIN_MENU
    # CursorToGoodbye 在類別定義時就 copy 了舊 MainMenu（login 時再切前 len(MainMenu) 個並補游標）
    target.CursorToGoodbye[:] = MAIN_MENU + target.CursorToGoodbye[n_old:]
