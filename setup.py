"""setup.py — py2app 打包設定（M4）。

打包：
    ./venv/bin/python setup.py py2app
產出：
    dist.noindex/PTTBar.app  （拖到 /Applications、雙擊執行；LSUIElement=不佔 Dock）

輸出資料夾叫 dist.noindex 是刻意的：Spotlight 不索引 *.noindex 資料夾，
這樣搜尋「PTTBar」只會出現 /Applications 那份，不會連打包產物一起跳出來。

備註（PLAN 6.6 / NOTES.md）：
- venv 用 Homebrew Python 3.13（framework build，py2app 可用）。若打包失敗，
  改用 python.org 官方 universal2 Python 重建 venv，再不行改 PyInstaller --windowed。
- tests/ 不會被打包（不在 app.py 的 import 圖上）。
"""

from setuptools import setup

APP = ["src/app.py"]

OPTIONS = {
    "dist_dir": "dist.noindex",   # Spotlight 不索引，避免搜尋出現兩個 PTTBar
    "iconfile": "assets/PTTBar.icns",   # app icon（assets/make_icon.py 產生，可重現）
    # 明確帶入的套件（含資料檔/動態 import 的：certifi 憑證、keyring backends、PyPtt 語系）
    "packages": [
        "PyPtt",
        "keyring",
        "certifi",
        "requests",
        "urllib3",
        "charset_normalizer",
        "idna",
        "websockets",
        "rumps",
        "Quartz",       # 彈幕的 Core Animation（pyobjc-framework-Quartz）
    ],
    "excludes": ["pytest", "py2app", "setuptools", "pip", "wheel", "tests"],
    "plist": {
        "CFBundleName": "PTTBar",
        "CFBundleDisplayName": "PTTBar",
        "CFBundleIdentifier": "io.github.jerry9856.pttbar",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "LSUIElement": True,            # 純選單列 app，不佔 Dock（PLAN M4 必須）
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "PTTBar",
    },
}

setup(
    app=APP,
    name="PTTBar",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
