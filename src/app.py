"""app.py — PTTBar 狀態列主程式（rumps.App），M3 完整 UI。

架構鐵則（PLAN.md 第 4 節）：
- UI 更新只在 main thread（本檔）；PyPtt 只在 worker thread（watcher.py）。
- 兩者只透過一個 queue.Queue 溝通；本檔用 rumps.Timer(1s) 從 queue 取事件更新 UI。
- worker 絕不碰 rumps 物件；本檔絕不直接呼叫 PyPtt。

UI 行為見 PLAN.md 6.5。帳密/設定存取見 credentials.py。
"""

import queue
import signal
import subprocess
import sys
import time
import webbrowser
from collections import deque

import rumps
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAttributedString,
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSImage,
    NSImageOnly,
    NSNoImage,
    NSSecureTextField,
    NSStatusBar,
    NSSwitchButton,
    NSTextField,
    NSView,
)

from credentials import (
    acquire_single_instance_lock,
    ask_password,
    clear_all,
    clear_password,
    get_article,
    get_password,
    get_ptt_id,
    get_settings,
    set_article,
    set_password,
    set_ptt_id,
    update_setting,
)
from models import (
    EVT_AID,
    EVT_COMMENT,
    EVT_ERROR,
    EVT_FAVORITES,
    EVT_POSTLIST,
    EVT_STATUS,
    EVT_TITLE,
)
from ptt_url import aid_to_filename, parse_article_input
from ui_util import (
    PILL_PALETTES,
    comment_stats,
    get_palette,
    is_continuation,
    match_keywords,
    parse_keywords,
    truncate,
)
from watcher import INITIAL_BACKFILL, PttWatcher

try:
    from gui import GuiWindow   # 需要 pyobjc-framework-WebKit；失敗則選單顯示提示
except Exception:
    GuiWindow = None

try:
    from danmaku import DanmakuOverlay   # 需要 pyobjc-framework-Quartz；失敗則彈幕不可用
except Exception:
    DanmakuOverlay = None

try:
    from chat import ChatOverlay   # 需要 pyobjc-framework-WebKit；失敗則聊天室不可用
except Exception:
    ChatOverlay = None

# 選單項目最大寬度（下拉選單較寬，給多一點）
_MENU_MAX_UNITS = 80
# 最近推文保留則數（PLAN 6.5）
_MAX_COMMENTS = 50

# --- 狀態列跑馬燈（固定寬度 iOS 圓角膠囊 + 像素捲動）---
# PLAN 6.5 原訂固定截斷，但瀏海機種空間有限；逐字捲動又會因英數標點寬度不一而抖動（見 NOTES.md）。
# 改成：固定寬度的膠囊圖片，文字在膠囊內像素捲動——寬度恆定、不抖、不被瀏海擠掉。
# 行為：推文一出現就馬上捲，捲到最右（露出結尾）就停住不回頭；放得下的短推文靜態不捲。
# 幀率：速度是「像素/秒」、與幀率解耦，拉高只會更順、速度不變（停住/靜態時 _last_render_key 會跳過重畫）
_RENDER_FPS = 60
_RENDER_INTERVAL = 1.0 / _RENDER_FPS  # Timer 間隔＝算圖幀率，也兼顧 queue 取事件 / Ctrl+C
_PILL_PAD_X = 8.0                      # 膠囊內文字左右留白（像素）
_TRANS_DUR = 0.25                     # 換推文時「舊上滑出、新下方進來」的轉場秒數

# 視窗寬度（像素）、捲動速度（像素/秒）可在主視窗「設定」用滑桿調整並存 config.json，這裡是預設值。
_DEFAULT_WINDOW_PX = 240
_DEFAULT_SPEED_PX = 110
# 推文自動更新間隔（秒），主視窗「設定」可調（使用者要求，預設 3 秒；PLAN D5 原為 5 秒）
_DEFAULT_POLL_SEC = 3.0

# 彈幕預設值（PLAN M5 選配「彈幕模式」；全部可在主視窗「設定」調整）
_DMK_FONT = (12, 20, 36)        # (下限, 預設, 上限) 文字大小 px
_DMK_SPEED = (60, 180, 400)     # 飛行速度 px/s
_DMK_OPACITY = (0.3, 0.85, 1.0)  # 不透明度
_DMK_AREA = (10, 40, 100)       # 顯示範圍：螢幕上方 %
_DMK_BG_ALPHA = (0.10, 0.30, 0.80)  # 半透明底色的透明度

# 聊天室小視窗（M6；與彈幕互斥）——(下限, 預設, 上限)
_CHAT_FONT = (11, 14, 24)          # 文字大小 px
_CHAT_OPACITY = (0.3, 0.9, 1.0)    # 背景透明度（視窗底色 alpha）
_CHAT_PAGE = 150                   # 開啟/展開時先渲染的最近則數
_CHAT_MORE = 120                   # 上滑載入更多，每次往回補的則數


def _type_colors(theme: str = "ios"):
    """推/→/噓 的 (膠囊底色, 文字色)。色票資料在 ui_util.PILL_PALETTES（F5 可選主題）。"""
    def c(rgba):
        return NSColor.colorWithSRGBRed_green_blue_alpha_(*rgba)
    palette = get_palette(theme)["colors"]
    return {sym: (c(bg), c(fg)) for sym, (bg, fg) in palette.items()}


class _State:
    def __init__(self):
        self.logged_in = False
        self.has_article = False
        self.article_title = None
        self.status = "尚未開始"
        self.comments = deque(maxlen=_MAX_COMMENTS)  # CommentData，到達順序（舊→新）
        self.paused = False


class PTTBarApp(rumps.App):
    def __init__(self):
        # quit_button=None：自己管理「結束」，這樣重建選單時不會被 rumps 的自動 Quit 干擾。
        super().__init__("PTTBar", title="PTT ▸", quit_button=None)
        self.queue: queue.Queue = queue.Queue()
        self.watcher: PttWatcher | None = None
        self.state = _State()

        # 選單裡「會就地更新文字」的兩個項目參照（標題、狀態），避免每 5 秒重建選單害選單閃退。
        self._title_item: rumps.MenuItem | None = None
        self._status_item: rumps.MenuItem | None = None

        # 顯示設定（主視窗「設定」調整、存 config.json）
        s = get_settings()
        self.window_px = int(s.get("window_px", _DEFAULT_WINDOW_PX))
        self.speed_px = float(s.get("speed_px", _DEFAULT_SPEED_PX))
        self.poll_sec = max(1.0, min(30.0, float(s.get("poll_sec", _DEFAULT_POLL_SEC))))
        self.keywords: list[str] = list(s.get("keywords", []))     # 關鍵字（高亮/過濾）
        self.keyword_filter = bool(s.get("keyword_filter", False))  # 狀態列只捲含關鍵字推文
        self.pill_theme = str(s.get("pill_theme", "ios"))            # 膠囊主題色票
        self.merge_comments = bool(s.get("merge_comments", True))    # 合併被切開的長留言
        # 彈幕設定（M5；overlay 由 _danmaku_sync 在 bootstrap/開啟時才建立）
        def _clamp(key, lo_def_hi, cast=float):
            lo, default, hi = lo_def_hi
            try:
                return max(lo, min(hi, cast(s.get(key, default))))
            except (TypeError, ValueError):
                return default
        self.danmaku_on = bool(s.get("danmaku_on", False))
        self.danmaku_font = _clamp("danmaku_font", _DMK_FONT, int)
        self.danmaku_speed = _clamp("danmaku_speed", _DMK_SPEED)
        self.danmaku_opacity = _clamp("danmaku_opacity", _DMK_OPACITY)
        self.danmaku_area = _clamp("danmaku_area", _DMK_AREA, int)
        self.danmaku_type_color = bool(s.get("danmaku_type_color", True))
        self.danmaku_author = bool(s.get("danmaku_author", True))
        self.danmaku_backdrop = bool(s.get("danmaku_backdrop", True))  # 半透明深色底
        self.danmaku_bg_alpha = _clamp("danmaku_bg_alpha", _DMK_BG_ALPHA)
        self._danmaku = None   # DanmakuOverlay；壞掉會被設回 None（自動停用，不拖垮 app）

        # 聊天室小視窗（M6；與彈幕互斥，一次只能開一種）
        self.chat_on = bool(s.get("chat_on", False))
        self.chat_font = _clamp("chat_font", _CHAT_FONT, int)
        self.chat_opacity = _clamp("chat_opacity", _CHAT_OPACITY)
        self.chat_collapsed = bool(s.get("chat_collapsed", False))
        cf = s.get("chat_frame")
        self.chat_frame = list(cf) if isinstance(cf, list) and len(cf) == 4 else None
        chp = s.get("chat_head_pos")
        self.chat_head_pos = list(chp) if isinstance(chp, list) and len(chp) == 2 else None
        if self.chat_on and self.danmaku_on:
            self.danmaku_on = False   # 保險：兩者互斥，不該同時開
        self._chat = None      # ChatOverlay；壞掉會被設回 None（自動停用）
        self._chat_needs_reset = False   # 下一輪 tick 要用 history 重繪聊天室
        self._chat_fed = 0     # 已餵給聊天室的 history 則數（append 指標）
        self._chat_oldest = 0  # 聊天室目前顯示到的最舊 history 位置（往上載入用）

        # 最後收到的推文「原始序號」（合併判斷用：接續段序號必須相連）；換文章時歸零
        self._last_raw_index: int | None = None

        # 跑馬燈（像素捲動）狀態
        self._marquee_source: str | None = None   # 目前正在捲的完整字串
        self._cur_symbol: str | None = None        # 目前顯示的推文型別
        self._offset = 0.0                         # 目前水平位移（像素）
        self._text_px = 0.0                        # 目前字串像素寬
        self._at_end = False                       # 已捲到最右（停住不回頭）
        self._last_render_key: tuple | None = None  # 相同就跳過重畫（省 CPU）
        # 換推文的垂直推進轉場：(舊字串, 舊型別, 舊水平位移) 與已過秒數
        self._trans: tuple | None = None
        self._trans_t = 0.0

        # iOS 圓角膠囊(圖片)；繪圖失敗退回純文字 self.title
        self._pill = True
        self._type_colors = None  # 首次用到時建立

        # 瀏覽：我的最愛看板 + 目前瀏覽看板的文章列表
        self._favorites: list[dict] = []       # [{board, title}]
        self._browse_board: str | None = None  # 目前展開的看板
        self._browse_posts: list[dict] = []    # 一般文章（顯示順序：新→舊）
        self._browse_pinned: list[dict] = []   # 置頂文
        self._browse_prev: int | None = None   # 「載入更多」要抓的頁碼（index<N>.html）
        self._browse_loading = False
        # 看板列表快取（stale-while-revalidate）：重訪看板立即顯示上次結果、背景更新
        self._board_cache: dict[str, dict] = {}   # board -> {posts, pinned, prev}

        # 主視窗（WKWebView）與推送節流
        self.gui = None
        self._gui_dirty = False
        self._gui_last_push = 0.0
        self._toast: tuple[str, float] | None = None   # (訊息, 時間戳)，一次性顯示

        # session 內收到的推文歷史（視窗即時推文 feed 用；比選單的 50 則多）
        self._history: list[dict] = []   # {t,a,c,tm,i,ts}

        self._bootstrapped = False
        # 同一個 Timer 兼作：算圖幀 + 每幀順便從 queue 取事件更新 UI + 讓 Ctrl+C 可被處理。
        self._timer = rumps.Timer(self._on_tick, _RENDER_INTERVAL)
        self._timer.start()

    # ================= 主迴圈：每 _RENDER_INTERVAL 秒一次 =================

    def _on_tick(self, _sender):
        if not self._bootstrapped:
            self._bootstrapped = True
            self._bootstrap()
            return

        structure_changed = False
        # 彈幕批次：同一輪 drain 到的推文先收起來、接續段就地合併，drain 完才餵
        # （PTT 切開的長留言幾乎都在同一輪抓到 → 彈幕也能顯示合併後的完整留言，不增加延遲）
        dmk_batch: list[list] = []   # [型別, 作者, 內容]
        # 開新文章時 watcher 會補最後幾則「既有」推文（backfill）——彈幕只飛其中最新一則
        # （使用者要求：點開新文章不要整批彈出）；選單/主視窗 feed 仍照常顯示全部。
        dmk_backfill_last: list | None = None
        # 聊天室：接續段若併進「已餵給聊天室的最後一則」→ 要更新該則（不是新增）
        chat_update_last = False
        while True:
            try:
                tag, payload = self.queue.get_nowait()
            except queue.Empty:
                break

            self._gui_dirty = True
            if tag == EVT_TITLE:
                self.state.article_title = payload
                self.state.has_article = True
                self._apply_title_item()
                self._persist_article_title(payload)
            elif tag == EVT_STATUS:
                self.state.status = payload
                self._apply_status_item()
            elif tag == EVT_COMMENT:
                cont = (self.merge_comments and self.state.comments
                        and self._last_raw_index is not None
                        and is_continuation(
                            prev_type=self.state.comments[-1].type,
                            prev_author=self.state.comments[-1].author,
                            prev_time=self.state.comments[-1].time,
                            prev_index=self._last_raw_index,
                            new_type=payload.type, new_author=payload.author,
                            new_time=payload.time, new_index=payload.index))
                if cont:
                    # PTT 把過長留言切成多則 → 接回同一則顯示（型別/時間/序號保留首段的）
                    last = self.state.comments[-1]
                    last.content += payload.content
                    if self._history:
                        self._history[-1]["c"] = last.content
                        # 這則若已餵給聊天室（是最後一則 DOM）→ 更新它；否則之後 append 會帶合併後全文
                        if (len(self._history) - 1) < self._chat_fed:
                            chat_update_last = True
                    if payload.backfill and dmk_backfill_last is not None:
                        dmk_backfill_last[2] += payload.content   # backfill 的接續段
                    elif dmk_batch:
                        dmk_batch[-1][2] += payload.content   # 首段還沒飛 → 彈幕也合併
                    elif dmk_backfill_last is not None:
                        dmk_backfill_last[2] += payload.content   # 接在 backfill 尾段後
                    else:
                        # 首段在上一輪已經飛出去了（切在兩輪之間，罕見）→ 後段單獨飛
                        dmk_batch.append([payload.type, payload.author, payload.content])
                else:
                    self.state.comments.append(payload)
                    self._history.append({"t": payload.type, "a": payload.author,
                                          "c": payload.content, "tm": payload.time,
                                          "i": payload.index, "ts": time.time()})
                    if len(self._history) > 2000:   # 上限，避免無限成長
                        n = len(self._history) - 2000
                        del self._history[:n]
                        # history 前面被裁掉 n 則 → 聊天室指標同步左移，維持指向同一則
                        self._chat_fed = max(0, self._chat_fed - n)
                        self._chat_oldest = max(0, self._chat_oldest - n)
                    if payload.backfill:
                        # 既有推文一路覆寫，最後只留最新那則
                        dmk_backfill_last = [payload.type, payload.author, payload.content]
                    else:
                        dmk_batch.append([payload.type, payload.author, payload.content])
                self._last_raw_index = payload.index
                structure_changed = True
            elif tag == EVT_ERROR:
                self.state.status = payload
                self._apply_status_item()
            elif tag == EVT_FAVORITES:
                self._favorites = payload or []
            elif tag == EVT_POSTLIST:
                board, data = payload
                self._browse_board = board
                self._browse_loading = False
                page_posts = list(reversed(data.get("posts") or []))  # 顯示順序：新→舊
                if data.get("append"):
                    self._browse_posts.extend(page_posts)  # 較舊的接在後面
                    self._browse_prev = data.get("prev")
                elif data.get("error") and self._browse_posts:
                    pass   # 更新失敗但畫面上還有快取內容 → 保留舊資料，只提示
                else:
                    self._browse_posts = page_posts
                    self._browse_pinned = data.get("pinned") or []
                    self._browse_prev = data.get("prev")
                if data.get("error"):
                    self._toast = (f"讀取失敗（{data['error']}），稍後再試",
                                   time.monotonic())
                else:
                    # 快取目前累積的列表，下次點同看板立即顯示（背景再更新）
                    self._board_cache[board] = {
                        "posts": list(self._browse_posts),
                        "pinned": list(self._browse_pinned),
                        "prev": self._browse_prev,
                    }
            elif tag == EVT_AID:
                board, aid = payload
                art = get_article() or {}
                if art.get("board") == board:   # 用 index 選的文解析出 aid → 存起來以便重開恢復
                    art["aid"] = aid
                    art.pop("index", None)
                    set_article(art)

        if dmk_backfill_last is not None:
            dmk_batch.insert(0, dmk_backfill_last)   # 既有推文的最新一則排最前面飛
        for ctype, author, content in dmk_batch:
            self._danmaku_feed(ctype, author, content)

        if structure_changed:
            self._rebuild_menu()

        # 每一步都推進跑馬燈（內容有變會自動重建捲動影格）
        self._advance_marquee()

        # 彈幕：放行排隊中的、清掉飛完的（動畫本身由 Core Animation 驅動，這裡很便宜）
        if self._danmaku is not None and self.danmaku_on:
            try:
                self._danmaku.tick()
            except Exception:
                self._danmaku = None   # 彈幕壞掉只停用自己，不拖垮 app

        # 聊天室：由 history 驅動（收合時不推、展開時會 reset 重繪）
        if self.chat_on and self._chat is not None and not self.chat_collapsed:
            try:
                self._chat_push(chat_update_last)
            except Exception:
                self._chat = None   # 聊天室壞掉只停用自己，不拖垮 app

        # 視窗開著且有變化 → 節流推送狀態（0.4s 一次）
        if (self.gui and self.gui.visible() and self._gui_dirty
                and time.monotonic() - self._gui_last_push > 0.4):
            self._push_gui()

    # ================= 啟動流程 =================

    def _bootstrap(self):
        ptt_id = get_ptt_id()
        password = get_password(ptt_id) if ptt_id else None

        if ptt_id and password:
            self._start_watcher(ptt_id, password)
            self._resume_tracking()
        else:
            # 首次啟動：要求輸入帳密
            self._do_login()

        self._danmaku_sync()   # 上次開著彈幕 → 重開自動恢復
        self._chat_sync()      # 上次開著聊天室 → 重開自動恢復（含收合狀態）
        self._rebuild_menu()  # 狀態列標題由跑馬燈（_advance_marquee）每步自動更新

    def _resume_tracking(self):
        """開始追蹤 config 裡記住的文章（bootstrap / 原帳號重新登入共用）。"""
        art = get_article()
        if art and art.get("board") and (art.get("aid") or art.get("index")):
            self.state.has_article = True
            self.state.article_title = art.get("title")
            self.state.status = "連線中…"
            if art.get("aid"):
                self.watcher.track(art["board"], aid=art["aid"])
            else:
                self.watcher.track(art["board"], index=art["index"])

    def _relogin(self, _sender=None):
        """用已存的帳號密碼重新登入（不必再輸入），刷新登入/連線狀態並沿用目前追蹤的文章。
        若沒存密碼（未勾「記住帳號密碼」）→ 退回登入對話框（帳號會預填）。"""
        ptt_id = get_ptt_id()
        password = get_password(ptt_id) if ptt_id else None
        if ptt_id and password:
            self._start_watcher(ptt_id, password)   # 停舊 watcher、重建、重新 login
            self._resume_tracking()
            self._rebuild_menu()
            self._gui_dirty = True
        else:
            self._do_login()

    def _do_login(self) -> bool:
        """跳出（單一）帳號密碼對話框、存好並啟動 watcher。成功回傳 True。

        「記住帳號密碼」打勾（預設）→ 密碼存進 Keychain，重開自動登入；
        不打勾 → 這次只用記憶體裡的密碼，不落地，重開會再問一次（帳號仍會記著方便預填）。"""
        remember_default = bool(get_settings().get("remember_creds", True))
        try:
            creds = self._ask_login(default_id=get_ptt_id() or "",
                                    default_remember=remember_default)
        except Exception:
            creds = self._ask_login_fallback()   # NSAlert 失敗 → 退回舊的兩段式
        if not creds:
            return False
        ptt_id, password, remember = creds
        if not ptt_id or not password:
            return False
        set_ptt_id(ptt_id)                       # 帳號一律記著（方便下次預填），非機敏資訊
        update_setting("remember_creds", bool(remember))
        if remember:
            set_password(ptt_id, password)       # 密碼進 Keychain → 重開自動登入
        else:
            clear_password(ptt_id)               # 不記住：清掉舊密碼、這次只用記憶體裡的
        self._start_watcher(ptt_id, password)
        self._rebuild_menu()
        return True

    def _ask_login(self, default_id: str = "", default_remember: bool = True):
        """單一對話框，同時輸入帳號 + 密碼（隱藏）+「記住帳號密碼」勾選。
        回傳 (id, pw, remember) 或 None（取消）。"""
        alert = NSAlert.alloc().init()
        alert.setMessageText_("登入 PTT")
        alert.setInformativeText_("輸入 PTT 帳號與密碼")
        alert.addButtonWithTitle_("登入")   # 第一顆＝預設（Enter）
        alert.addButtonWithTitle_("取消")
        width = 240.0
        acc = NSView.alloc().initWithFrame_(((0.0, 0.0), (width, 82.0)))
        id_field = NSTextField.alloc().initWithFrame_(((0.0, 58.0), (width, 24.0)))
        id_field.setStringValue_(default_id or "")
        id_field.setPlaceholderString_("PTT 帳號")
        pw_field = NSSecureTextField.alloc().initWithFrame_(((0.0, 30.0), (width, 24.0)))
        pw_field.setPlaceholderString_("PTT 密碼")
        remember = NSButton.alloc().initWithFrame_(((0.0, 0.0), (width, 22.0)))
        remember.setButtonType_(NSSwitchButton)   # 核取方塊
        remember.setTitle_("記住帳號密碼")
        remember.setState_(1 if default_remember else 0)
        acc.addSubview_(id_field)
        acc.addSubview_(pw_field)
        acc.addSubview_(remember)
        id_field.setNextKeyView_(pw_field)
        alert.setAccessoryView_(acc)
        alert.window().setInitialFirstResponder_(id_field)
        if alert.runModal() != NSAlertFirstButtonReturn:
            return None
        return (id_field.stringValue().strip(), pw_field.stringValue(),
                bool(remember.state()))

    def _ask_login_fallback(self):
        """NSAlert 不可用時的退路：rumps.Window 問帳號 + osascript 問密碼（預設記住）。"""
        ptt_id = self._ask_text("PTT 帳號", "請輸入你的 PTT 帳號：", default=get_ptt_id() or "")
        if not ptt_id:
            return None
        password = ask_password("請輸入 PTT 密碼（不會顯示）：")
        if not password:
            return None
        return ptt_id, password, True

    def _start_watcher(self, ptt_id: str, password: str):
        if self.watcher:
            self.watcher.stop()
        self.state = _State()
        self.state.logged_in = True
        self._last_raw_index = None
        self._favorites = []
        self._browse_board = None
        self._browse_posts = []
        self._browse_pinned = []
        self._browse_prev = None
        self.watcher = PttWatcher(ptt_id, password, self.queue,
                                  poll_interval=self.poll_sec)
        self.watcher.start()
        self.watcher.request_favorites()   # 登入後抓「我的最愛」看板

    # ================= 選單動作（都在 main thread）=================

    def _track_new(self, _sender=None):
        if not self.state.logged_in:
            if not self._do_login():
                return
        text = self._ask_text("追蹤新文章", "貼上文章網址，或輸入『看板 AID』：")
        if not text:
            return
        try:
            board, aid = parse_article_input(text)
        except ValueError as e:
            rumps.alert("輸入格式錯誤", str(e))
            return

        self._begin_track(board, title=None, aid=aid, input_text=text)

    def _begin_track(self, board, *, title, aid=None, index=None, input_text=None):
        """共用的「開始追蹤某文章」：清空狀態、存 config、交給 watcher。aid 或 index 擇一。"""
        self.state.comments.clear()
        self._history.clear()   # 視窗 feed/歷史/統計只顯示「這一篇」的推文，不混到上一篇
        self._chat_fed = 0
        self._chat_oldest = 0
        self._chat_needs_reset = True   # 換文章 → 聊天室清空重繪
        self._last_raw_index = None   # 序號換一篇文重算，不能跨文章合併
        self.state.article_title = title
        self.state.has_article = True
        self.state.status = "連線中…"
        art = {"input": input_text or f"{board}", "board": board, "title": title}
        if aid:
            art["aid"] = aid
        else:
            art["index"] = index
        set_article(art)
        self.watcher.track(board, aid=aid, index=index)
        self._rebuild_menu()
        self._gui_dirty = True   # 視窗 feed 立即清空重來

    def _pick_post(self, board: str, index: int, title: str):
        """從文章列表點一篇 → 開始追蹤（用 index，worker 會解析出 aid）。"""
        self._begin_track(board, title=title, index=index, input_text=f"{board} @{index}")

    # ================= 主視窗（WKWebView）=================

    def _open_window(self, _sender=None):
        if GuiWindow is None:
            rumps.alert("無法開啟視窗", "缺少 WebKit 元件（pyobjc-framework-WebKit）。")
            return
        if self.gui is None:
            self.gui = GuiWindow(self._on_gui_msg)
        self.gui.show()
        self._push_gui()

    def _on_gui_msg(self, msg: dict):
        """視窗 JS 的指令（main thread）。任何指令失敗都不能炸掉 app。"""
        cmd = msg.get("cmd")
        if cmd == "ready":
            self._push_gui()
        elif cmd == "loadBoards":
            if self.watcher:
                self.watcher.request_favorites()
        elif cmd == "loadPosts":
            board = str(msg.get("board") or "")
            if board and self.watcher:
                self._browse_board = board
                cached = self._board_cache.get(board)
                if cached:
                    # 快取先上（立即可看可點），背景抓最新（回來後整批替換）
                    self._browse_posts = list(cached["posts"])
                    self._browse_pinned = list(cached["pinned"])
                    self._browse_prev = cached["prev"]
                else:
                    self._browse_posts = []
                    self._browse_pinned = []
                    self._browse_prev = None
                self._browse_loading = True
                self.watcher.request_post_list(board)
                self._push_gui()
        elif cmd == "loadMore":
            if self.watcher and self._browse_board and self._browse_prev:
                self._browse_loading = True
                self.watcher.request_post_list_more(self._browse_board, self._browse_prev)
                self._push_gui()
        elif cmd == "track":
            board = str(msg.get("board") or "")
            url = str(msg.get("url") or "")
            index = int(msg.get("index") or 0)
            title = str(msg.get("title") or "")
            if not self.watcher or not board:
                return
            if url:
                # 網頁列表來的文章：URL 直接解析出 aid（穩定、免 index 解析）
                try:
                    b, aid = parse_article_input(url)
                    self._begin_track(b, title=title, aid=aid, input_text=url)
                except ValueError:
                    self._toast = ("無法解析文章網址", time.monotonic())
            elif index:
                self._pick_post(board, index, title)   # PyPtt 備援列表（用 index）
            self._push_gui()
        elif cmd == "trackInput":
            text = str(msg.get("text") or "")
            try:
                board, aid = parse_article_input(text)
            except ValueError as e:
                self._toast = (str(e), time.monotonic())
                self._push_gui()
                return
            if self.watcher:
                self._begin_track(board, title=None, aid=aid, input_text=text)
                self._push_gui()
        elif cmd == "pause":
            if self.watcher and not self.state.paused:
                self._toggle_pause()
                self._push_gui()
        elif cmd == "resume":
            if self.watcher and self.state.paused:
                self._toggle_pause()
                self._push_gui()
        elif cmd == "reconnect":
            self._reconnect()
            self._push_gui()
        elif cmd == "chatCollapse":
            self._collapse_chat(True)
            self._push_gui()
        elif cmd == "chatExpand":
            self._collapse_chat(False)
            self._push_gui()
        elif cmd == "set":
            self._apply_setting(str(msg.get("key") or ""), msg.get("value"))
            self._push_gui()
        elif cmd == "logout":
            self._logout()
            self._push_gui()
        elif cmd == "login":
            self._do_login()
            self._push_gui()
        elif cmd == "relogin":
            self._relogin()
            self._push_gui()
        elif cmd == "openBrowser":
            self._open_in_browser()
        elif cmd == "getHistory":
            self._push_history()

    def _open_in_browser(self, _sender=None):
        url = self._article_url()
        if url:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        else:
            self._toast = ("這篇文章還沒有網址（尚未解析出 AID）", time.monotonic())
            self._push_gui()

    def _push_history(self):
        """把完整推文歷史（session 內至 2000 則、含關鍵字標記）推給視窗歷史分頁。"""
        if self.gui is None or not self.gui.visible():
            return
        import json as _json
        rows = [{**e, "kw": bool(self.keywords) and match_keywords(
                    f"{e['a']} {e['c']}", self.keywords)} for e in self._history]
        try:
            self.gui.eval_js("window.__history && window.__history(%s)"
                             % _json.dumps(rows, ensure_ascii=False))
        except Exception:
            pass

    def _apply_setting(self, key: str, value):
        if key == "window_px":
            self.window_px = max(120, min(400, int(value)))
            update_setting("window_px", self.window_px)
            self._marquee_source = None
            self._last_render_key = None
        elif key == "speed_px":
            self.speed_px = max(20.0, min(300.0, float(value)))
            update_setting("speed_px", self.speed_px)
        elif key == "poll_sec":
            self.poll_sec = max(1.0, min(30.0, float(value)))
            update_setting("poll_sec", self.poll_sec)
            if self.watcher:
                self.watcher.set_poll_interval(self.poll_sec)
        elif key == "merge_comments":
            self.merge_comments = bool(value)
            update_setting("merge_comments", self.merge_comments)
        elif key == "keywords":
            self.keywords = parse_keywords(str(value or ""))
            update_setting("keywords", self.keywords)
            self._marquee_source = None   # 重選顯示推文（★/過濾即時生效）
        elif key == "keyword_filter":
            self.keyword_filter = bool(value)
            update_setting("keyword_filter", self.keyword_filter)
            self._marquee_source = None
        elif key == "pill_theme":
            name = str(value or "ios")
            if name in PILL_PALETTES:
                self.pill_theme = name
                update_setting("pill_theme", name)
                self._type_colors = None      # 下次繪製用新色票
                self._last_render_key = None  # 強制重畫
        elif key == "danmaku_on":
            self.danmaku_on = bool(value)
            update_setting("danmaku_on", self.danmaku_on)
            if self.danmaku_on and self.chat_on:
                self.chat_on = False       # 互斥：開彈幕就關聊天室
                update_setting("chat_on", False)
                self._chat_sync()
            self._danmaku_sync()
            if self.danmaku_on and self._danmaku is not None:
                try:
                    self._danmaku.feed("推", "PTTBar", "彈幕已開啟")  # 立即看得到效果
                except Exception:
                    pass
            self._rebuild_menu()   # 狀態列選單的「開啟/關閉彈幕」字樣
        elif key == "danmaku_font":
            self.danmaku_font = max(_DMK_FONT[0], min(_DMK_FONT[2], int(value)))
            update_setting("danmaku_font", self.danmaku_font)
            self._danmaku_sync()
        elif key == "danmaku_speed":
            self.danmaku_speed = max(_DMK_SPEED[0], min(_DMK_SPEED[2], float(value)))
            update_setting("danmaku_speed", self.danmaku_speed)
            self._danmaku_sync()
        elif key == "danmaku_opacity":
            # 滑桿送 30~100（%），存 0.3~1.0
            frac = max(_DMK_OPACITY[0], min(_DMK_OPACITY[2], float(value) / 100.0))
            self.danmaku_opacity = frac
            update_setting("danmaku_opacity", frac)
            self._danmaku_sync()
        elif key == "danmaku_area":
            self.danmaku_area = max(_DMK_AREA[0], min(_DMK_AREA[2], int(value)))
            update_setting("danmaku_area", self.danmaku_area)
            self._danmaku_sync()
        elif key == "danmaku_type_color":
            self.danmaku_type_color = bool(value)
            update_setting("danmaku_type_color", self.danmaku_type_color)
            self._danmaku_sync()
        elif key == "danmaku_author":
            self.danmaku_author = bool(value)
            update_setting("danmaku_author", self.danmaku_author)
            self._danmaku_sync()
        elif key == "danmaku_backdrop":
            self.danmaku_backdrop = bool(value)
            update_setting("danmaku_backdrop", self.danmaku_backdrop)
            self._danmaku_sync()
        elif key == "danmaku_bg_alpha":
            # 滑桿送 10~80（%），存 0.10~0.80
            frac = max(_DMK_BG_ALPHA[0], min(_DMK_BG_ALPHA[2], float(value) / 100.0))
            self.danmaku_bg_alpha = frac
            update_setting("danmaku_bg_alpha", frac)
            self._danmaku_sync()
        elif key == "chat_on":
            self.chat_on = bool(value)
            update_setting("chat_on", self.chat_on)
            if self.chat_on and self.danmaku_on:
                self.danmaku_on = False    # 互斥：開聊天室就關彈幕
                update_setting("danmaku_on", False)
                self._danmaku_sync()
            self._chat_sync()
            self._rebuild_menu()   # 狀態列選單的「開啟/關閉聊天室」字樣
        elif key == "chat_font":
            self.chat_font = max(_CHAT_FONT[0], min(_CHAT_FONT[2], int(value)))
            update_setting("chat_font", self.chat_font)
            if self._chat is not None:   # 直接套用（不重繪、不跳動）
                try:
                    self._chat.configure(font_px=self.chat_font, opacity=self.chat_opacity)
                except Exception:
                    self._chat = None
        elif key == "chat_opacity":
            # 滑桿送 30~100（%），存 0.30~1.00
            frac = max(_CHAT_OPACITY[0], min(_CHAT_OPACITY[2], float(value) / 100.0))
            self.chat_opacity = frac
            update_setting("chat_opacity", frac)
            if self._chat is not None:
                try:
                    self._chat.configure(font_px=self.chat_font, opacity=self.chat_opacity)
                except Exception:
                    self._chat = None

    def _article_url(self) -> str | None:
        art = get_article() or {}
        board, aid = art.get("board"), art.get("aid")
        if not board or not aid:
            return None
        try:
            return f"https://www.ptt.cc/bbs/{board}/{aid_to_filename(aid)}.html"
        except ValueError:
            return None

    def _gui_state(self) -> dict:
        toast = None
        if self._toast and time.monotonic() - self._toast[1] < 3.0:
            toast = self._toast[0]
            self._toast = None
        return {
            "loggedIn": self.state.logged_in,
            "pttId": get_ptt_id() or "",
            "article": {
                "has": self.state.has_article,
                "board": (get_article() or {}).get("board") or "",
                "title": self.state.article_title or "",
                "status": self.state.status,
                "paused": self.state.paused,
                "url": self._article_url(),
                "backfill": INITIAL_BACKFILL,   # 開始追蹤時只補的既有推文則數（feed 提示用）
            },
            "favorites": [{"b": b.get("board", ""), "t": b.get("title", "")}
                          for b in self._favorites if b.get("board")],
            "browse": {
                "board": self._browse_board or "",
                "loading": self._browse_loading,
                "prev": self._browse_prev,
                "posts": [self._post_row(p) for p in self._browse_posts],
                "pinned": [self._post_row(p) for p in self._browse_pinned],
            },
            "settings": {
                "window_px": int(self.window_px),
                "speed_px": int(self.speed_px),
                "poll_sec": int(self.poll_sec),
                "merge_comments": self.merge_comments,
                "keywords": ", ".join(self.keywords),
                "keyword_filter": self.keyword_filter,
                "pill_theme": self.pill_theme,
                "danmaku_on": self.danmaku_on,
                "danmaku_font": int(self.danmaku_font),
                "danmaku_speed": int(self.danmaku_speed),
                "danmaku_opacity": int(round(self.danmaku_opacity * 100)),
                "danmaku_area": int(self.danmaku_area),
                "danmaku_type_color": self.danmaku_type_color,
                "danmaku_author": self.danmaku_author,
                "danmaku_backdrop": self.danmaku_backdrop,
                "danmaku_bg_alpha": int(round(self.danmaku_bg_alpha * 100)),
                "chat_on": self.chat_on,
                "chat_font": int(self.chat_font),
                "chat_opacity": int(round(self.chat_opacity * 100)),
                "chat_collapsed": self.chat_collapsed,
            },
            "themes": [
                {"name": name, "label": p["label"],
                 "dots": [
                     f"rgba({int(bg[0]*255)},{int(bg[1]*255)},{int(bg[2]*255)},{bg[3]})"
                     for sym in ("推", "→", "噓")
                     for bg in [p["colors"][sym][0]]
                 ]}
                for name, p in PILL_PALETTES.items()
            ],
            "comments": [
                {**e, "kw": bool(self.keywords) and match_keywords(
                    f"{e['a']} {e['c']}", self.keywords)}
                for e in self._history[-400:]
            ],
            "stats": comment_stats(self._history, time.time()),
            "toast": toast,
        }

    @staticmethod
    def _post_row(p: dict) -> dict:
        return {"t": (p.get("title") or "").strip(),
                "a": (p.get("author") or "").strip(),
                "p": (p.get("nrec") or "").strip(),
                "d": (p.get("date") or "").strip(),
                "url": p.get("url"), "i": p.get("index")}

    def _push_gui(self):
        if self.gui is None or not self.gui.visible():
            return
        self._gui_dirty = False
        self._gui_last_push = time.monotonic()
        try:
            self.gui.push(self._gui_state())
        except Exception:
            pass

    # ---- 彈幕（M5）----

    def _danmaku_sync(self):
        """把目前彈幕設定/開關套到 overlay；需要時才建立（lazy）。壞掉只停用自己。"""
        if self.danmaku_on and self._danmaku is None:
            if DanmakuOverlay is None:
                self.danmaku_on = False
                update_setting("danmaku_on", False)
                self._toast = ("缺少 Quartz 元件，無法開啟彈幕", time.monotonic())
                return
            try:
                self._danmaku = DanmakuOverlay()
            except Exception:
                self._danmaku = None
                return
        if self._danmaku is None:
            return
        try:
            self._danmaku.configure(
                font_px=self.danmaku_font, speed_px=self.danmaku_speed,
                opacity=self.danmaku_opacity, area_pct=self.danmaku_area,
                type_color=self.danmaku_type_color, show_author=self.danmaku_author,
                backdrop=self.danmaku_backdrop,
                backdrop_alpha=self.danmaku_bg_alpha)
            self._danmaku.set_enabled(self.danmaku_on)
        except Exception:
            self._danmaku = None

    def _danmaku_feed(self, ctype: str, author: str, content: str):
        """把一則推文丟給彈幕（開著才丟；任何失敗都不影響主流程）。
        內容是 _on_tick 批次合併後的完整留言（關鍵字也比對合併後全文）。"""
        if not self.danmaku_on or self._danmaku is None:
            return
        try:
            kw = bool(self.keywords) and match_keywords(
                f"{author} {content}", self.keywords)
            self._danmaku.feed(ctype, author, content, kw=kw)
        except Exception:
            self._danmaku = None

    def _toggle_danmaku(self, _sender=None):
        self._apply_setting("danmaku_on", not self.danmaku_on)   # 內含 _rebuild_menu

    # ---- 聊天室小視窗（M6；與彈幕互斥）----

    def _chat_sync(self):
        """把目前聊天室設定/開關/收合狀態套到 overlay；需要時才建立（lazy）。壞掉只停用自己。"""
        if self.chat_on and self._chat is None:
            if ChatOverlay is None:
                self.chat_on = False
                update_setting("chat_on", False)
                self._toast = ("缺少 WebKit 元件，無法開啟聊天室", time.monotonic())
                return
            try:
                self._chat = ChatOverlay(self._on_chat_msg)
            except Exception:
                self._chat = None
                return
        if self._chat is None:
            return
        try:
            self._chat.configure(font_px=self.chat_font, opacity=self.chat_opacity,
                                 frame=self.chat_frame, head_pos=self.chat_head_pos)
            self._chat.set_collapsed(self.chat_collapsed)
            self._chat.set_enabled(self.chat_on)
            if self.chat_on and not self.chat_collapsed:
                self._chat_needs_reset = True   # 開啟時用現有 history 重繪
        except Exception:
            self._chat = None

    def _on_chat_msg(self, msg: dict):
        """聊天室 WKWebView 的 JS 事件（main thread）。"""
        cmd = msg.get("cmd")
        if cmd == "chatMore":
            self._chat_load_more()
        elif cmd == "chatCollapse":
            self._collapse_chat(True)
        elif cmd == "chatExpand":
            self._collapse_chat(False)
        elif cmd == "ready":
            self._chat_needs_reset = True   # 頁面載好 → 下一輪 tick 重繪

    def _chat_msg(self, e: dict) -> dict:
        return {"t": e["t"], "a": e["a"], "c": e["c"], "tm": e["tm"],
                "kw": bool(self.keywords) and match_keywords(
                    f"{e['a']} {e['c']}", self.keywords)}

    def _chat_push(self, update_last: bool):
        """把 history 的變化推給聊天室：需要時整批重繪，否則更新最後一則 + append 新的。"""
        if self._chat_needs_reset:
            self._chat_needs_reset = False
            start = max(0, len(self._history) - _CHAT_PAGE)
            self._chat_oldest = start
            self._chat_fed = len(self._history)
            self._chat.reset([self._chat_msg(e) for e in self._history[start:]])
            return
        if update_last and self._history:
            self._chat.update_last(self._history[-1]["c"])
        if self._chat_fed < len(self._history):
            new = self._history[self._chat_fed:]
            self._chat.append([self._chat_msg(e) for e in new])
            self._chat_fed = len(self._history)

    def _chat_load_more(self):
        """上滑到頂 → 往回補一頁更舊的留言（緩衝載入）。"""
        if self._chat is None:
            return
        end = self._chat_oldest
        start = max(0, end - _CHAT_MORE)
        older = self._history[start:end]
        self._chat_oldest = start
        try:
            self._chat.prepend([self._chat_msg(e) for e in older], has_more=(start > 0))
        except Exception:
            self._chat = None

    def _collapse_chat(self, collapsed: bool):
        """收合（→小 icon）或展開聊天室，並存狀態/位置到 config。"""
        if not self.chat_on:
            return
        self.chat_collapsed = bool(collapsed)
        update_setting("chat_collapsed", self.chat_collapsed)
        if self._chat is not None:
            try:
                if collapsed:
                    fr = self._chat.get_frame()
                    if fr:
                        self.chat_frame = fr
                        update_setting("chat_frame", fr)
                else:
                    hp = self._chat.get_head_pos()
                    if hp:
                        self.chat_head_pos = hp
                        update_setting("chat_head_pos", hp)
                self._chat.set_collapsed(self.chat_collapsed)
            except Exception:
                self._chat = None
        if not collapsed:
            self._chat_needs_reset = True   # 展開後用最新 history 重繪
        self._rebuild_menu()
        self._gui_dirty = True

    def _toggle_chat(self, _sender=None):
        self._apply_setting("chat_on", not self.chat_on)   # 內含互斥處理 + _rebuild_menu

    def _toggle_chat_collapsed(self, _sender=None):
        self._collapse_chat(not self.chat_collapsed)

    def _reconnect(self, _sender=None):
        """立即重新連線：解除停止/退避、馬上重連（連線問題本來就會自動重試，
        這個是讓使用者不必枯等退避的手動加速；帳密錯誤則等重設帳密）。"""
        if not self.watcher:
            if not self.state.logged_in:
                self._do_login()
            return
        # 暫停中先恢復，否則重連後仍不會抓文
        if self.state.paused:
            self.watcher.resume()
            self.state.paused = False
        self.watcher.retry()
        self.state.status = "重新連線中…"
        self._apply_status_item()
        self._gui_dirty = True
        self._rebuild_menu()

    def _toggle_pause(self, _sender=None):
        if not self.watcher:
            return
        if self.state.paused:
            self.watcher.resume()
            self.state.paused = False
        else:
            self.watcher.pause()
            self.state.paused = True
        self._rebuild_menu()

    def _logout(self, _sender=None):
        ptt_id = get_ptt_id()
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        if ptt_id:
            clear_password(ptt_id)
        clear_all()
        self.state = _State()
        self._favorites = []
        self._browse_board = None
        self._browse_posts = []
        self._browse_pinned = []
        self._browse_prev = None
        self._rebuild_menu()  # 標題由跑馬燈自動變回「PTT ▸ 點我登入」

    def _quit(self, _sender=None):
        if self.watcher:
            self.watcher.stop()
        self._persist_chat_geometry()   # 記住聊天室視窗/小 icon 的位置與大小
        rumps.quit_application()

    def _persist_chat_geometry(self):
        if self._chat is None:
            return
        try:
            fr = self._chat.get_frame()
            if fr:
                update_setting("chat_frame", fr)
            hp = self._chat.get_head_pos()
            if hp:
                update_setting("chat_head_pos", hp)
        except Exception:
            pass

    def _persist_article_title(self, title: str):
        """把抓到的標題寫回 config，讓下次重開能直接顯示。"""
        art = get_article()
        if art and title and art.get("title") != title:
            art["title"] = title
            set_article(art)

    def _copy_comment(self, text: str):
        try:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
        except Exception:
            pass

    # ================= UI 組裝 =================

    def _ask_text(self, title: str, message: str, default: str = "") -> str | None:
        w = rumps.Window(
            message=message, title=title, default_text=default,
            ok="確定", cancel="取消", dimensions=(340, 24),
        )
        resp = w.run()
        if resp.clicked and resp.text.strip():
            return resp.text.strip()
        return None

    def _latest_comment_text(self) -> str | None:
        c = self._display_comment()
        if c is None:
            return None
        star = "★ " if self.keywords and match_keywords(f"{c.author} {c.content}", self.keywords) else ""
        return f"{star}[{c.type}] {c.author}: {c.content}"

    def _display_comment(self):
        """狀態列要顯示的那則推文。關鍵字過濾開啟時，取最新「含關鍵字」的一則。"""
        if not self.state.comments:
            return None
        if self.keyword_filter and self.keywords:
            for c in reversed(self.state.comments):
                if match_keywords(f"{c.author} {c.content}", self.keywords):
                    return c
            return None   # 還沒有符合關鍵字的推文
        return self.state.comments[-1]

    # ---- 狀態列跑馬燈 ----

    def _compose_title_text(self) -> str:
        """算出狀態列「應該顯示的完整字串」（跑馬燈會在固定窄視窗內捲動它）。"""
        if not self.state.logged_in:
            return "PTT ▸ 點我登入"
        latest = self._latest_comment_text()
        if latest:
            return latest
        if self.keyword_filter and self.keywords and self.state.comments:
            return "PTT ▸ 等待含關鍵字的推文…"   # 過濾中、尚無符合
        if self.state.has_article:
            if self.state.article_title:
                # 抓到文章了、但目前沒有推文（或這篇 PyPtt 解析不出推文）→ 顯示標題而非乾等
                return f"（尚無推文）{self.state.article_title}"
            return "PTT ▸ 讀取中…"   # 還沒抓到標題＝真的還在首次讀取
        return "PTT ▸ 點我輸入文章"

    def _button(self):
        """狀態列按鈕（設 image / title 用）；run() 後才有。"""
        nsapp = getattr(self, "_nsapp", None)
        si = getattr(nsapp, "nsstatusitem", None) if nsapp else None
        return si.button() if si is not None else None

    def _measure_px(self, text: str) -> float:
        astr = NSAttributedString.alloc().initWithString_attributes_(
            text, {NSFontAttributeName: NSFont.menuBarFontOfSize_(0)})
        return float(astr.size().width)

    def _colors_for(self, symbol: str | None):
        if not symbol:
            return None
        if self._type_colors is None:
            self._type_colors = _type_colors(self.pill_theme)
        return self._type_colors.get(symbol)

    def _advance_marquee(self):
        text = self._compose_title_text()
        dc = self._display_comment() if self.state.logged_in else None
        symbol = dc.type if dc is not None else None

        if text != self._marquee_source:      # 新推文/內容變了
            old_text, old_sym, old_off = self._marquee_source, self._cur_symbol, self._offset
            self._marquee_source = text
            self._cur_symbol = symbol
            # 滑鼠 hover 狀態列可看完整內容（膠囊寬度有限時很實用）
            btn = self._button()
            if btn is not None:
                try:
                    tip = text if dc is None else f"{text}（{dc.time}）"
                    btn.setToolTip_(tip)
                except Exception:
                    pass
            self._offset = 0.0
            self._at_end = False
            self._last_render_key = None
            self._text_px = self._measure_px(text) if self._pill else 0.0
            # 舊、新都是推文（有膠囊）才做垂直推進轉場；否則直接換
            if (self._pill and old_text is not None
                    and self._colors_for(old_sym) and self._colors_for(symbol)):
                self._trans = (old_text, old_sym, old_off)
                self._trans_t = 0.0
            else:
                self._trans = None

        # 轉場中：舊膠囊往上滑出、新膠囊從下方往上進來
        if self._trans is not None:
            self._trans_t += _RENDER_INTERVAL
            p = min(1.0, self._trans_t / _TRANS_DUR)
            if not self._render_transition(self._trans, text, symbol, p) or p >= 1.0:
                self._trans = None
            return  # 轉場期間不推進水平捲動

        self._render(text, symbol)

        # 推進位移：一出現就捲，捲到最右（露出結尾）就停住不回頭
        inner = max(1.0, self.window_px - 2 * _PILL_PAD_X)
        max_off = self._text_px - inner
        if not self._at_end and max_off > 0:
            self._offset += self.speed_px * _RENDER_INTERVAL
            if self._offset >= max_off:
                self._offset = max_off
                self._at_end = True
        else:
            self._at_end = True

    def _render(self, text: str, symbol: str | None):
        """畫固定寬度 iOS 膠囊(圖)並依 offset 捲動；提示字/繪圖失敗則退回純文字，確保不會壞。"""
        colors = self._colors_for(symbol)
        btn = self._button()
        # 無法畫圖（headless/失敗）或非推文提示字 → 純文字（清掉圖片）
        if btn is None or not self._pill or not colors:
            if btn is not None:
                try:
                    btn.setImage_(None)
                    btn.setImagePosition_(NSNoImage)
                except Exception:
                    pass
            self.title = text
            return

        key = (text, int(self._offset), symbol, self.window_px)
        if key == self._last_render_key:
            return  # 這一幀與上一幀相同（例如已停在最右）→ 不重畫，省 CPU
        self._last_render_key = key
        try:
            img = self._new_image()
            img.lockFocus()
            self._draw_pill(text, self._offset, colors[0], colors[1], 0.0)
            img.unlockFocus()
            img.setTemplate_(False)  # 一定要 False，template 會把顏色洗成單色（先前失效主因）
            btn.setImage_(img)
            btn.setImagePosition_(NSImageOnly)
        except Exception:
            self._pill = False      # 繪圖失敗 → 永久退回純文字
            self.title = text

    def _render_transition(self, old, new_text: str, new_sym: str | None, p: float) -> bool:
        """在同一張圖裡畫舊膠囊(上滑出)+新膠囊(下方進來)。成功回 True。"""
        btn = self._button()
        oc = self._colors_for(old[1])
        nc = self._colors_for(new_sym)
        if btn is None or not self._pill or not oc or not nc:
            return False
        try:
            ease = 1.0 - (1.0 - p) * (1.0 - p)   # ease-out，尾段放慢比較順
            bar_h = float(NSStatusBar.systemStatusBar().thickness())
            shift = ease * bar_h
            img = self._new_image()
            img.lockFocus()
            self._draw_pill(old[0], old[2], oc[0], oc[1], shift)          # 舊：往上滑出
            self._draw_pill(new_text, 0.0, nc[0], nc[1], shift - bar_h)   # 新：從下方進來
            img.unlockFocus()
            img.setTemplate_(False)
            btn.setImage_(img)
            btn.setImagePosition_(NSImageOnly)
            self._last_render_key = None   # 轉場結束後強制重畫一次
            return True
        except Exception:
            self._pill = False
            return False

    def _new_image(self):
        w = float(self.window_px)
        bar_h = float(NSStatusBar.systemStatusBar().thickness())
        return NSImage.alloc().initWithSize_((w, bar_h))

    def _draw_pill(self, text: str, offset: float, bg, fg, y_shift: float):
        """在目前 lockFocus 的畫布上，於垂直位移 y_shift 處畫一顆固定寬度圓角膠囊+捲動文字。"""
        ctx = NSGraphicsContext.currentContext()
        ctx.saveGraphicsState()
        try:
            font = NSFont.menuBarFontOfSize_(0)
            astr = NSAttributedString.alloc().initWithString_attributes_(
                text, {NSFontAttributeName: font, NSForegroundColorAttributeName: fg})
            tsize = astr.size()
            w = float(self.window_px)
            bar_h = float(NSStatusBar.systemStatusBar().thickness())
            pill_h = min(bar_h - 4.0, float(round(tsize.height)) + 5.0)  # 上下留白＝浮動膠囊
            y0 = (bar_h - pill_h) / 2.0 + y_shift
            rect = ((0.5, y0), (w - 1.0, pill_h))
            radius = pill_h / 2.0   # 半高圓角＝膠囊兩端全圓
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, radius, radius)
            bg.setFill()
            path.fill()
            # 文字裁切用內縮矩形（左右各內縮 _PILL_PAD_X、垂直限定此膠囊帶），離圓角有距離才進出
            clip = NSBezierPath.bezierPathWithRect_(
                ((_PILL_PAD_X, y0), (w - 2 * _PILL_PAD_X, pill_h)))
            clip.addClip()
            astr.drawAtPoint_((_PILL_PAD_X - offset, y0 + (pill_h - tsize.height) / 2.0))
        finally:
            ctx.restoreGraphicsState()

    def _apply_title_item(self):
        if self._title_item is not None:
            self._title_item.title = self._menu_title_text()

    def _apply_status_item(self):
        if self._status_item is not None:
            self._status_item.title = self.state.status

    def _menu_title_text(self) -> str:
        t = self.state.article_title or "（尚未追蹤文章）"
        return truncate(t, _MENU_MAX_UNITS)

    def _rebuild_menu(self):
        self.menu.clear()
        self._title_item = None
        self._status_item = None

        if not self.state.logged_in:
            self.menu.update([
                rumps.MenuItem("登入…", callback=(lambda s: self._do_login())),
                rumps.MenuItem("開啟 PTTBar 視窗…", callback=self._open_window),
                rumps.separator,
                rumps.MenuItem("結束", callback=self._quit),
            ])
            return

        items: list = []

        self._title_item = rumps.MenuItem(self._menu_title_text())   # 無 callback = disabled
        self._status_item = rumps.MenuItem(self.state.status)         # 無 callback = disabled
        items.append(self._title_item)
        items.append(self._status_item)
        items.append(rumps.separator)

        if self.state.comments:
            # 新的在上面；點了複製整則到剪貼簿。
            # 用零寬空格讓「顯示相同」的推文也有唯一的 title（rumps 選單以 title 當 key）。
            for i, c in enumerate(reversed(self.state.comments)):
                line = f"[{c.type}] {c.author}: {c.content}"
                full = f"{c.type} {c.author}: {c.content} ({c.time})"
                mi = rumps.MenuItem(
                    truncate(line, _MENU_MAX_UNITS) + "​" * i,
                    callback=(lambda sender, t=full: self._copy_comment(t)),
                )
                items.append(mi)
            items.append(rumps.separator)

        # 我的最愛 / 看板文章 / 設定 / 登出都在主視窗（PLAN 之外的 UX 擴充，使用者要求）
        items.append(rumps.MenuItem("開啟 PTTBar 視窗…", callback=self._open_window))
        items.append(rumps.MenuItem(
            "繼續更新" if self.state.paused else "暫停更新", callback=self._toggle_pause))
        items.append(rumps.MenuItem("重新連線", callback=self._reconnect))
        items.append(rumps.MenuItem(
            "關閉彈幕" if self.danmaku_on else "開啟彈幕", callback=self._toggle_danmaku))
        items.append(rumps.MenuItem(
            "關閉聊天室" if self.chat_on else "開啟聊天室", callback=self._toggle_chat))
        if self.chat_on:
            items.append(rumps.MenuItem(
                "展開聊天室" if self.chat_collapsed else "收合聊天室",
                callback=self._toggle_chat_collapsed))
        items.append(rumps.MenuItem("追蹤新文章…", callback=self._track_new))
        if self._article_url():
            items.append(rumps.MenuItem("在瀏覽器開啟文章", callback=self._open_in_browser))
        items.append(rumps.separator)
        items.append(rumps.MenuItem("結束", callback=self._quit))

        self.menu.update(items)


def _install_sigint_handler():
    signal.signal(signal.SIGINT, lambda *_: rumps.quit_application())


if __name__ == "__main__":
    # 一次只能開一個（使用者要求）：.app 與 python src/app.py 混開時，
    # 後開的直接退出，避免狀態列出現兩顆、兩個實例互踢 PTT 連線。
    _lock = acquire_single_instance_lock()   # 保持引用到行程結束
    if _lock is None:
        try:
            subprocess.run(
                ["osascript", "-e",
                 'display notification "PTTBar 已經在執行中了" with title "PTTBar"'],
                check=False)
        except Exception:
            pass
        sys.exit(0)
    _install_sigint_handler()
    PTTBarApp().run()
