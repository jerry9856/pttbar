"""tests/test_credentials.py — config.json 存取測試（不碰 Keychain / osascript）。

用 monkeypatch 把 CONFIG_DIR/CONFIG_PATH 導到 tmp，避免動到使用者真正的設定檔。
password（keyring）與 ask_password（osascript）需要系統資源，不在此單元測試。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import credentials  # noqa: E402


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    d = tmp_path / "pttbar"
    monkeypatch.setattr(credentials, "CONFIG_DIR", str(d))
    monkeypatch.setattr(credentials, "CONFIG_PATH", str(d / "config.json"))
    return d


def test_load_missing_returns_empty(tmp_config):
    assert credentials.load_config() == {}


def test_save_and_load_roundtrip(tmp_config):
    credentials.save_config({"ptt_id": "abc", "article": {"board": "Test"}})
    cfg = credentials.load_config()
    assert cfg["ptt_id"] == "abc"
    assert cfg["article"]["board"] == "Test"


def test_ptt_id_helpers(tmp_config):
    assert credentials.get_ptt_id() is None
    credentials.set_ptt_id("myid")
    assert credentials.get_ptt_id() == "myid"


def test_article_helpers(tmp_config):
    assert credentials.get_article() is None
    credentials.set_ptt_id("myid")
    credentials.set_article({"board": "Gossiping", "aid": "#1abCdEfG", "title": "標題"})
    art = credentials.get_article()
    assert art["board"] == "Gossiping" and art["aid"] == "#1abCdEfG"
    # set_article 不應覆蓋掉 ptt_id
    assert credentials.get_ptt_id() == "myid"


def test_clear_all(tmp_config):
    credentials.set_ptt_id("myid")
    credentials.set_article({"board": "Test", "aid": "#12345678"})
    credentials.clear_all()
    assert credentials.load_config() == {}


def test_load_corrupt_json_returns_empty(tmp_config):
    os.makedirs(str(tmp_config), exist_ok=True)
    with open(str(tmp_config / "config.json"), "w") as f:
        f.write("{ not valid json ")
    assert credentials.load_config() == {}


def test_settings_helpers(tmp_config):
    assert credentials.get_settings() == {}
    credentials.update_setting("tick_interval", 0.2)
    credentials.update_setting("window_units", 48)
    s = credentials.get_settings()
    assert s["tick_interval"] == 0.2 and s["window_units"] == 48


def test_clear_all_preserves_settings(tmp_config):
    credentials.set_ptt_id("myid")
    credentials.set_article({"board": "Test", "aid": "#12345678"})
    credentials.update_setting("window_units", 64)
    credentials.clear_all()
    cfg = credentials.load_config()
    assert "ptt_id" not in cfg and "article" not in cfg  # 帳號/文章清掉
    assert cfg.get("settings", {}).get("window_units") == 64  # 但顯示偏好保留


def test_unicode_title_preserved(tmp_config):
    credentials.save_config({"article": {"title": "[LIVE] 籃球 威靈頓聖徒"}})
    assert credentials.get_article()["title"] == "[LIVE] 籃球 威靈頓聖徒"


def test_single_instance_lock(tmp_config):
    lock1 = credentials.acquire_single_instance_lock()
    assert lock1 is not None                                  # 第一個實例搶到鎖
    assert credentials.acquire_single_instance_lock() is None  # 第二個實例被擋
    lock1.close()                                              # 第一個結束（釋放鎖）
    lock2 = credentials.acquire_single_instance_lock()
    assert lock2 is not None                                   # 之後可以再開
    lock2.close()


def test_single_instance_lock_survives_config_ops(tmp_config):
    lock = credentials.acquire_single_instance_lock()
    credentials.set_ptt_id("myid")            # 鎖檔與 config 同目錄，互不干擾
    assert credentials.get_ptt_id() == "myid"
    assert credentials.acquire_single_instance_lock() is None
    lock.close()
