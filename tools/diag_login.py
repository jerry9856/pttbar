"""登入診斷：用 Keychain 裡的帳密（沒有就跳密碼框）實際登入一次，印出真正的例外與最後畫面。

用法：venv/bin/python tools/diag_login.py
不會印出密碼。會用 kick_other_session=True，若 PTTBar 正在跑會被踢掉一次。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import subprocess  # noqa: E402

import PyPtt  # noqa: E402
import ptt_compat  # noqa: E402
from credentials import ask_password, get_password, get_ptt_id  # noqa: E402


def ask_id() -> str | None:
    r = subprocess.run(
        ["osascript", "-e", 'display dialog "診斷用：請輸入 PTT 帳號" default answer ""',
         "-e", "text returned of result"], capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


ptt_id = get_ptt_id() or ask_id()
if not ptt_id:
    sys.exit("沒有帳號")
pw = get_password(ptt_id) or ask_password("診斷用：請輸入 PTT 密碼")
if not pw:
    sys.exit("沒有密碼")

print(f"帳號：{ptt_id!r}（長度 {len(ptt_id)}）")
print(f"密碼長度：{len(pw)}；前後有空白：{pw != pw.strip()}；超過 8 字元：{len(pw) > 8}")
print(f"密碼含非 ASCII 字元：{any(ord(c) > 127 for c in pw)}")

if "--raw" not in sys.argv:   # --raw：不套修補，重現 PyPtt 原始行為
    ptt_compat.install()
ptt = PyPtt.API(log_level=PyPtt.LogLevel.SILENT)
try:
    ptt.login(ptt_id, pw, kick_other_session=True)
    print("\n✅ 登入成功")
    ptt.logout()
except Exception as e:
    print(f"\n❌ 例外：{type(e).__module__}.{type(e).__name__}: {e}")
    screens = ptt.connect_core.get_screen_queue()
    for i, s in enumerate(screens[-3:], 1):
        print(f"\n------ 最後畫面 {i}/{min(3, len(screens))} ------\n{s}")
