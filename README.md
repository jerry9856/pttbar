# PTTBar

macOS 狀態列 app：即時顯示指定 PTT 文章的推文（跑馬燈膠囊 / 彈幕 / 聊天室視窗），
並提供 iOS 風格主視窗（我的最愛看板、文章列表無限捲動、推文歷史/搜尋/統計、
關鍵字高亮、主題色票）。

Python 打造（rumps + PyPtt + WKWebView），不需 Xcode。

## 下載安裝

1. 到 [Releases](../../releases) 下載最新的 `PTTBar.app.zip`，解壓縮後拖到「應用程式」資料夾
2. 雙擊啟動（純選單列 app，**不佔 Dock**）
3. 首次啟動輸入 PTT 帳號密碼（密碼存 macOS 鑰匙圈，不落地明文）

一次只會開一個實例：重複開啟時，後開的會跳系統通知「PTTBar 已經在執行中了」然後自動退出。

### Gatekeeper 擋下時（未簽名 app）

第一次開啟若被「無法打開，因為來自未識別的開發者」擋下：

- 對 `PTTBar.app` **按右鍵 → 打開 → 打開**，或
- 終端機執行：`xattr -cr /Applications/PTTBar.app`
- （可選）ad-hoc 簽名：`codesign --force --deep -s - /Applications/PTTBar.app`

### 重要提醒：PTT 同帳號互踢

PTT 同一帳號重複登入會互踢。PTTBar 登入時帶 `kick_other_session=True`——
**你在其他地方（手機 app、瀏覽器、終端機）的 PTT 連線會被踢掉**，這是預期行為。

## 從原始碼執行 / 開發

```bash
# Python 3.11+（開發用 Homebrew python3.13，framework build）
/opt/homebrew/bin/python3.13 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python src/app.py        # 直接執行（Ctrl+C 結束）
./venv/bin/python -m pytest tests/  # 跑測試
```

## 打包 .app

```bash
./venv/bin/python setup.py py2app   # 產出 dist.noindex/PTTBar.app
```

- 輸出資料夾叫 `dist.noindex`：Spotlight 不索引 `*.noindex`，
  搜尋「PTTBar」才不會連打包產物一起出現兩個
- 打包設定在 `setup.py`（py2app；plist 含 `LSUIElement=True` 不佔 Dock）
- 若 py2app 失敗：改用 python.org 官方 universal2 Python 重建 venv 再打包；
  仍不行則改 `PyInstaller --windowed`

## 隱私與安全

- 密碼只存 macOS Keychain（透過 [keyring](https://github.com/jaraco/keyring)），
  不寫入任何檔案；本機設定檔（`~/Library/Application Support/pttbar/config.json`）
  只存帳號 ID 與追蹤中的文章
- 所有連線只到 PTT 官方（`ws.ptt.cc` / `www.ptt.cc`），沒有任何第三方伺服器

## 免責聲明

本專案為非官方工具，與 PTT（批踢踢實業坊）無任何關聯。
使用本工具登入 PTT 之風險（含帳號互踢、站方使用規範）由使用者自行承擔。

## License

本專案原始碼以 [MIT License](LICENSE) 釋出。

打包後的 `.app` 內含下列第三方套件，各依其原授權散布：
[PyPtt](https://github.com/PyPtt/PyPtt)（LGPLv3）、
[rumps](https://github.com/jaredks/rumps)（BSD-3-Clause）、
[keyring](https://github.com/jaraco/keyring)（MIT）等，
完整清單見 `requirements.txt`。
