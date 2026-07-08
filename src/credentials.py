"""credentials.py — 帳密與設定存取（不 import rumps；密碼對話框用 osascript）。

- 密碼存 macOS Keychain（keyring），不落地明文。
- 帳號 id 與追蹤中的文章存 config.json（PLAN.md 6.5），app 重開自動恢復。
- 首次登入的密碼輸入用 osascript 隱藏輸入對話框（rumps.Window 會明碼顯示，不能用）。
  帳號 id 的輸入用 rumps.Window，放在 app.py（本檔不碰 rumps，保持可測）。

config.json 結構：
    {
      "ptt_id": "myid",
      "article": {"input": "原始輸入", "board": "Test", "aid": "#xxxxxxxx", "title": "文章標題"}
    }
"""

import fcntl
import json
import os
import subprocess

import keyring

APP_NAME = "pttbar"  # keyring service 名稱
CONFIG_DIR = os.path.expanduser("~/Library/Application Support/pttbar")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


# ---- config.json ----

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_config(cfg: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)  # 原子寫入，避免寫到一半壞檔


def get_ptt_id() -> str | None:
    return load_config().get("ptt_id")


def set_ptt_id(ptt_id: str) -> None:
    cfg = load_config()
    cfg["ptt_id"] = ptt_id
    save_config(cfg)


def get_article() -> dict | None:
    art = load_config().get("article")
    return art if isinstance(art, dict) else None


def set_article(article: dict) -> None:
    cfg = load_config()
    cfg["article"] = article
    save_config(cfg)


def get_settings() -> dict:
    s = load_config().get("settings")
    return s if isinstance(s, dict) else {}


def update_setting(key: str, value) -> None:
    cfg = load_config()
    s = cfg.get("settings")
    if not isinstance(s, dict):
        s = {}
    s[key] = value
    cfg["settings"] = s
    save_config(cfg)


def clear_all() -> None:
    """清空帳號與追蹤文章（登出用），但**保留 settings**（顯示偏好不該因登出被清掉）。"""
    settings = load_config().get("settings")
    save_config({"settings": settings} if isinstance(settings, dict) else {})


# ---- 單一實例鎖 ----

def acquire_single_instance_lock():
    """搶「一次只能開一個 PTTBar」的檔案鎖（flock，行程死掉自動釋放、無殘留問題）。

    成功回傳鎖檔物件——呼叫端必須保持引用到行程結束，鎖才不會被 GC 提早放掉。
    已有另一個實例（.app 或 python src/app.py）持鎖時回傳 None。
    """
    os.makedirs(CONFIG_DIR, exist_ok=True)
    f = open(os.path.join(CONFIG_DIR, "pttbar.lock"), "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    f.write(str(os.getpid()))
    f.flush()
    return f


# ---- Keychain（keyring）----

def get_password(ptt_id: str) -> str | None:
    if not ptt_id:
        return None
    try:
        return keyring.get_password(APP_NAME, ptt_id)
    except Exception:
        return None


def set_password(ptt_id: str, password: str) -> None:
    keyring.set_password(APP_NAME, ptt_id, password)


def clear_password(ptt_id: str) -> None:
    if not ptt_id:
        return
    try:
        keyring.delete_password(APP_NAME, ptt_id)
    except Exception:
        pass  # 本來就沒有也無所謂


# ---- 密碼輸入對話框（osascript 隱藏輸入）----

def ask_password(prompt: str = "請輸入 PTT 密碼（不會顯示）") -> str | None:
    """跳出 macOS 隱藏輸入對話框取得密碼；取消或失敗回傳 None。"""
    script = (
        'display dialog "%s" default answer "" with hidden answer '
        'buttons {"取消","確定"} default button "確定"' % prompt.replace('"', '\\"')
    )
    r = subprocess.run(
        ["osascript", "-e", script, "-e", "text returned of result"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    pw = r.stdout.strip()
    return pw or None
