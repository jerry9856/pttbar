"""watcher.py — PttWatcher：連線、輪詢、diff、暫停、退避重連、切換文章。

**不准 import rumps / AppKit**（PLAN.md 第 5 節分層原則），才能在純 CLI 開發測試。
所有 PyPtt 呼叫只發生在本檔的單一 worker thread（PyPtt 非 thread-safe，見 NOTES.md
MultiThreadOperated）。與 UI 只透過一個 queue.Queue 溝通，事件格式見 models.py。

介面（PLAN.md 第 7 節 M2）：
    w = PttWatcher(ptt_id, ptt_pw, event_queue)
    w.start(); w.track(board, aid); w.pause(); w.resume(); w.stop()

行為（PLAN.md 6.3 / 6.4）：
- 每輪 get_post 結束後再等 poll_interval 秒（預設 5，可由設定調整；非固定週期），
  小步長 sleep 讓暫停/停止/換文章 1 秒內生效。
- diff：seen_count = 上輪推文總數；新推文 = comments[seen_count:]，逐則丟 queue。
- 切換文章：seen_count 歸零，首輪先補「最後 10 則」再開始只丟新的；送出標題事件。
- comments 變少（推文被刪）→ 重設 seen_count 不 crash。
- 暫停：不斷線，只跳過 get_post。
- 例外（斷線/被踢/逾時）：退避重連 5→10→20→40→60s（上限），重連=重新 login。
  **連線問題永遠不永久停止**——持續以退避重試直到網路恢復或使用者按「重新連線」
  （retry()）為止（PLAN 6.4 原訂連續失敗 > 10 次停止，但使用者實測會卡在停止狀態、
  網路回來也不重連，故改為永久重試，見 NOTES.md）。只有帳號/密碼錯誤才進 halted，
  須重設帳密才能解除。
"""

import logging
import queue
import threading
import time
import traceback as _real_traceback
from datetime import datetime

import PyPtt
import PyPtt.connect_core as _connect_core
import PyPtt.log as _pyptt_log

import fav_parse
import raw_parse
import web_fetch

fav_parse.install()   # 修 PyPtt 我的最愛解析（看板名第一字被切，見 NOTES.md）

# --- 消音 PyPtt 的 [INFO] 訊息（workaround，見 NOTES.md）---
# 光給 API(log_level=SILENT) 沒用：_api_get_board_info.py 每次抓文都呼叫 log.init(log.INFO)，
# 把全域 logger 等級重設回 INFO。改成把 PyPtt「共用的 console handler」等級拉到 CRITICAL——
# handler 層級過濾不受 log.init 重設 logger 等級影響（re-init 只改 logger level、不動 handler
# level），一勞永逸地擋掉所有 INFO/DEBUG 輸出。我們自己的狀態走 queue 事件，不受影響。
_pyptt_log._console_handler.setLevel(logging.CRITICAL)

# --- 消音 PyPtt 連線失敗時「寫死」的除錯輸出（workaround，見 NOTES.md）---
# PyPtt connect_core.py 在連線失敗時直接 `traceback.print_tb(...)` + `print(e)`（第 231-232、
# 315-316 行），不受 log_level 控制。斷線重連時會在 terminal 噴一大坨 traceback。
# 這裡只覆蓋 connect_core「模組層級」的 print / traceback 參照為 no-op，
# 不影響其他執行緒的 stdout/stderr，也不影響我們自己的輸出（比全域重導 stdout 安全）。


class _SilentTraceback:
    @staticmethod
    def print_tb(*_a, **_k):
        pass

    def __getattr__(self, name):  # 其餘屬性仍轉發給真正的 traceback 模組
        return getattr(_real_traceback, name)


_connect_core.traceback = _SilentTraceback()
_connect_core.print = lambda *_a, **_k: None

from models import (
    EVT_AID,
    EVT_COMMENT,
    EVT_ERROR,
    EVT_FAVORITES,
    EVT_POSTLIST,
    EVT_STATUS,
    EVT_TITLE,
    CommentData,
)

# 看板文章列表一次抓幾篇（瀏覽用）
POSTLIST_LIMIT = 20

# 首次切換文章時，先補進 queue 的既有推文則數（PLAN 6.3）
INITIAL_BACKFILL = 10
# 每輪之間的等待秒數預設值（PLAN 6.4；app 可用設定覆寫，見 set_poll_interval）
POLL_INTERVAL = 5.0
# 使用者可設定的輪詢間隔上下限（秒）
POLL_MIN, POLL_MAX = 1.0, 30.0
# 小步長 sleep，讓暫停/停止/換文章能快速生效
_SLEEP_STEP = 0.2
# 退避重連上限（PLAN 6.4）；連線問題不再有「連續失敗上限」——永久重試直到恢復
_BACKOFF_CAP = 60
# 曾成功抓到過的文章突然讀到「被刪」，需連續確認幾次才判定真的刪除（過濾斷線誤判）
_DELETED_CONFIRM = 3
# 單輪 > 此秒數視為爆文，提示更新較慢（PLAN 6.6）
_SLOW_ROUND_SEC = 20

# 連線相關例外（碰到就退避重連），其餘未預期例外也當連線問題處理但一樣計入失敗次數
_CONNECTION_ERRORS = (
    PyPtt.ConnectError,
    PyPtt.ConnectionClosed,
    PyPtt.UnknownError,
)


class PttWatcher:
    def __init__(self, ptt_id: str, ptt_pw: str, event_queue: "queue.Queue",
                 poll_interval: float | None = None):
        self._id = ptt_id
        self._pw = ptt_pw
        self._q = event_queue
        # 每輪之間的等待秒數；None = 用模組預設 POLL_INTERVAL（執行期讀取，測試會覆寫它）。
        # 主執行緒寫、worker 讀（float 指派為原子操作，免鎖）。
        self._poll_interval: float | None = (
            max(POLL_MIN, min(POLL_MAX, float(poll_interval)))
            if poll_interval is not None else None)

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()  # set = 暫停中

        # 追蹤目標與切換旗標，受 _lock 保護（主執行緒寫、worker 讀）
        # target = (board, aid|None, index|None)：aid 優先；用 index 追蹤時首輪會解析出 aid。
        self._lock = threading.Lock()
        self._target: tuple[str, str | None, int | None] | None = None
        self._target_dirty = False   # 有新目標，worker 首輪要 backfill + 歸零 seen_count
        self._halted = False         # 連續失敗過多而停止；track()/resume() 可解除

        # 瀏覽請求佇列（主執行緒丟、worker 在輪詢空檔處理）：('fav',) / ('list', board)
        self._cmd_q: "queue.Queue" = queue.Queue()

        # 「立即重新連線」訊號：主執行緒 set、worker 在退避 sleep 中提早醒來並重設失敗計數，
        # 不必枯等退避（可能長達 60 秒）。retry()/resume()/track() 都會觸發。
        self._retry_now = threading.Event()

    # ---- 對外介面（都在主執行緒呼叫）----

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="PttWatcher", daemon=True)
        self._thread.start()

    @property
    def poll_interval(self) -> float:
        return self._poll_interval if self._poll_interval is not None else POLL_INTERVAL

    def set_poll_interval(self, seconds: float) -> None:
        """調整輪詢間隔（秒），下一次等待即生效；退避重連的基準仍固定（PLAN 6.4）。"""
        self._poll_interval = max(POLL_MIN, min(POLL_MAX, float(seconds)))

    def track(self, board: str, aid: str | None = None, index: int | None = None) -> None:
        with self._lock:
            self._target = (board, aid, index)
            self._target_dirty = True
            self._halted = False  # 換文章視為重新啟動，清除停止狀態
        self._retry_now.set()  # 若正卡在退避 sleep，立即醒來抓新文章（不必枯等）

    def request_favorites(self) -> None:
        """請 worker 抓「我的最愛」看板（結果以 EVT_FAVORITES 回報）。"""
        self._cmd_q.put(("fav",))

    def request_post_list(self, board: str) -> None:
        """抓某看板最新文章列表（EVT_POSTLIST 回報）。
        走網頁版、跑獨立背景執行緒 → 不用等 worker 的輪詢空檔，點了立即開抓（加速）。"""
        self._spawn_web_list(board, page=None, append=False)

    def request_post_list_more(self, board: str, page: int) -> None:
        """載入更多：抓 index<page>.html 的較舊文章（EVT_POSTLIST append 模式回報）。"""
        self._spawn_web_list(board, page=page, append=True)

    def _spawn_web_list(self, board: str, page: int | None, append: bool) -> None:
        """網頁列表抓取執行緒（web_fetch 不碰 PyPtt、session 是 thread-local，可安全併發）。"""
        def run():
            try:
                data = web_fetch.fetch_board_list_web(board, page=page)
                data["append"] = append
                self._emit(EVT_POSTLIST, (board, data))
            except Exception as e:
                if not append:
                    # 首頁失敗 → 排進 worker 用 PyPtt 備援（罕見路徑，可以慢）
                    self._cmd_q.put(("list_pyptt", board))
                else:
                    self._emit(EVT_STATUS, f"讀取失敗（{type(e).__name__}）")
                    self._emit(EVT_POSTLIST, (board, {"posts": [], "pinned": [],
                                                      "prev": None, "append": True,
                                                      "error": type(e).__name__}))
        threading.Thread(target=run, name="BoardList", daemon=True).start()

    def pause(self) -> None:
        self._paused.set()
        self._emit(EVT_STATUS, "已暫停")

    def resume(self) -> None:
        self._paused.clear()
        with self._lock:
            self._halted = False  # 繼續也解除停止狀態，讓 worker 重試
        self._retry_now.set()     # 若卡在退避 sleep 就立即醒來
        self._emit(EVT_STATUS, "繼續追蹤")

    def retry(self) -> None:
        """使用者要求「立即重新連線」：解除 halted、中斷退避 sleep 馬上重連
        （worker 醒來後會把失敗計數歸零，重新從 5 秒退避起算）。"""
        with self._lock:
            self._halted = False
        self._retry_now.set()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)

    # ---- 以下都在 worker thread 執行 ----

    def _emit(self, tag: str, payload) -> None:
        self._q.put((tag, payload))

    def _interruptible_sleep(self, seconds: float, *, wake_on_new_target: bool = False,
                             wake_on_command: bool = False, wake_on_retry: bool = False) -> None:
        """小步長 sleep；stop 設定時立即返回；wake_on_new_target 遇到換文章提早返回；
        wake_on_command 有待處理的瀏覽指令（我的最愛等）也提早返回（加速，不用等滿 5 秒）；
        wake_on_retry 使用者按「重新連線」（_retry_now）時提早返回——退避 sleep 專用，
        讓使用者不必枯等退避（瀏覽指令 wake_on_command 則刻意不接退避，避免破壞退避節奏）。"""
        deadline = seconds
        elapsed = 0.0
        while elapsed < deadline and not self._stop.is_set():
            if wake_on_new_target:
                with self._lock:
                    if self._target_dirty:
                        return
            if wake_on_command and not self._cmd_q.empty():
                return
            if wake_on_retry and self._retry_now.is_set():
                return
            time.sleep(_SLEEP_STEP)
            elapsed += _SLEEP_STEP

    def _run(self) -> None:
        # log_level=SILENT 去掉 PyPtt 的 [INFO] 連線/登入訊息（M2 使用者反映太吵）。
        ptt = PyPtt.API(log_level=PyPtt.LogLevel.SILENT)
        seen_count = 0
        consecutive_failures = 0
        logged_in = False
        ever_fetched = False  # 目前這個 target 是否曾成功抓到過（區分「輸入錯誤」vs「斷線」）
        deleted_reads = 0     # 連續讀到「被刪」的次數（曾抓到過後，用來過濾斷線誤判）
        web_mode = False      # PyPtt 解析不出這篇的推文（爆文/直播文）→ 改抓網頁版（NOTES.md）
        current_title = None  # 已送給 UI 的標題（避免重複送）

        def _note_transient(reason: str, relogin: bool = True) -> None:
            """把一次可重試的失敗（斷線/逾時/未預期例外）記為連線問題並退避重連。
            網頁模式的失敗不需要重登 PTT（relogin=False），只退避重試。
            **連線問題永不永久停止**：持續以退避（上限 60 秒）重試，網路一恢復（或使用者
            按「重新連線」）就自動接回（見 NOTES.md / 使用者回報）。"""
            nonlocal consecutive_failures, logged_in
            if relogin:
                logged_in = False
            consecutive_failures += 1
            self._emit(EVT_STATUS, reason)
            self._interruptible_sleep(self._backoff(consecutive_failures), wake_on_retry=True)

        try:
            while not self._stop.is_set():
                # 使用者按「重新連線」（或換文章/繼續）→ 中斷退避、失敗計數歸零、立即重試
                if self._retry_now.is_set():
                    self._retry_now.clear()
                    consecutive_failures = 0

                # halted 只會因帳密錯誤而發生（連線問題不再永久停止）；idle 等 retry/track/resume
                with self._lock:
                    halted = self._halted
                if halted:
                    self._interruptible_sleep(0.5, wake_on_new_target=True, wake_on_retry=True)
                    continue

                # 確保已登入（含退避重連，永久重試直到恢復）
                if not logged_in:
                    ok = self._try_login(ptt, consecutive_failures)
                    if ok:
                        logged_in = True
                        consecutive_failures = 0
                        # 不重設 seen_count：重連後文章內容不變，沿用舊 seen_count 正常 diff，
                        # 只送真正新增的推文，不會把最後 10 則重送洗版。
                    else:
                        with self._lock:
                            if self._halted:   # 帳密錯誤 → _try_login 已進 halted，交回上面 idle
                                continue
                        consecutive_failures += 1
                        self._interruptible_sleep(self._backoff(consecutive_failures),
                                                  wake_on_retry=True)
                        continue

                # 處理瀏覽請求（我的最愛 / 看板文章列表）——即使沒在追蹤文章、暫停中也要能用
                self._handle_commands(ptt)

                # 暫停：不斷線，跳過 get_post
                if self._paused.is_set():
                    self._interruptible_sleep(0.5, wake_on_new_target=True)
                    continue

                # 取得目標
                with self._lock:
                    target = self._target
                    dirty = self._target_dirty
                    if dirty:
                        self._target_dirty = False
                if target is None:
                    self._interruptible_sleep(0.5, wake_on_new_target=True)
                    continue

                board, aid, index = target
                if dirty:
                    seen_count = 0        # 切換文章 seen_count 歸零
                    ever_fetched = False  # 尚未成功抓到這篇
                    deleted_reads = 0
                    web_mode = False      # 新文章先走 PyPtt，需要時才再切網頁模式
                    current_title = None

                # ---- 輪詢一輪：網頁模式（爆文 fallback）----
                if web_mode:
                    try:
                        started = time.monotonic()
                        wtitle, comments = web_fetch.fetch_post_web(board, aid)
                        elapsed = time.monotonic() - started
                    except web_fetch.WebNotFound:
                        deleted_reads += 1
                        if deleted_reads >= _DELETED_CONFIRM:
                            self._emit(EVT_STATUS, "文章已刪除")
                            self._clear_target()
                            deleted_reads = 0
                        else:
                            self._emit(EVT_STATUS, "讀取異常，重試確認中…")
                            self._interruptible_sleep(self.poll_interval, wake_on_new_target=True, wake_on_command=True)
                        continue
                    except Exception as e:
                        _note_transient(f"網頁讀取失敗，重試中…（{type(e).__name__}）",
                                        relogin=False)
                        continue
                    deleted_reads = 0
                    consecutive_failures = 0
                    ever_fetched = True
                    if wtitle and wtitle != current_title:
                        current_title = wtitle
                        self._emit(EVT_TITLE, wtitle)
                    seen_count = self._emit_new_comments(comments, dirty, seen_count)
                    now = datetime.now().strftime("%H:%M")
                    self._emit(EVT_STATUS, f"● 追蹤中（網頁），{now} 更新")
                    self._interruptible_sleep(self.poll_interval, wake_on_new_target=True, wake_on_command=True)
                    continue

                # ---- 輪詢一輪：PyPtt（主要路徑）----
                try:
                    started = time.monotonic()
                    if aid:
                        post = ptt.get_post(board, aid=aid)
                    else:
                        post = ptt.get_post(board, index=index)  # 由看板列表選文（用 index）
                    elapsed = time.monotonic() - started
                except (PyPtt.NoSuchPost, PyPtt.NoSuchBoard) as e:
                    # 關鍵：斷線時 PyPtt 也會丟 NoSuchBoard/NoSuchPost。
                    # 若這篇「曾經成功抓到過」，現在抓不到多半是斷線 → 走重連保留目標；
                    # 只有「第一次就抓不到」才視為輸入錯誤，停止輪詢等使用者換文章。
                    if ever_fetched:
                        _note_transient("連線中斷，重連中…")
                    elif isinstance(e, PyPtt.NoSuchBoard):
                        self._emit(EVT_STATUS, f"沒有這個看板：{board}")
                        self._clear_target()
                    else:
                        self._emit(EVT_STATUS, "文章不存在或已刪除")
                        self._clear_target()
                    continue
                except PyPtt.RequireLogin:
                    logged_in = False  # 掉登入 → 走重連
                    continue
                except _CONNECTION_ERRORS as e:
                    _note_transient(f"連線中斷，重連中…（{type(e).__name__}）")
                    continue
                except Exception as e:  # 未預期例外：當連線問題處理，計入失敗
                    _note_transient(f"發生錯誤，重連中…（{type(e).__name__}）")
                    continue

                consecutive_failures = 0  # get_post 有回傳（連線正常）

                # 文章被刪：post_status != 'EXISTS'（不會 raise，見 NOTES）。
                # 但斷線時 get_post 可能回傳誤判成「被刪」的畫面（M2 使用者實測），
                # 故「曾成功抓到過」的文章突然變被刪，先重連確認、連續 _DELETED_CONFIRM 次才判定真刪。
                if post.get("post_status") != "EXISTS":
                    if not ever_fetched:
                        # 一開始就抓不到 → 使用者多半給了已刪除/無效的文章
                        self._emit(EVT_STATUS, "文章不存在或已刪除")
                        self._clear_target()
                        continue
                    deleted_reads += 1
                    if deleted_reads >= _DELETED_CONFIRM:
                        self._emit(EVT_STATUS, "文章已刪除")
                        self._clear_target()
                        deleted_reads = 0
                        continue
                    # 重連取得乾淨連線後再確認一次，避免斷線誤判
                    self._emit(EVT_STATUS, "讀取異常，重連確認中…")
                    logged_in = False
                    self._interruptible_sleep(self.poll_interval, wake_on_new_target=True, wake_on_command=True)
                    continue

                deleted_reads = 0    # 正常讀到文章 → 清掉誤判計數
                ever_fetched = True  # 成功抓到一次；之後的 NoSuchBoard/NoSuchPost/被刪 都先當斷線處理

                # 由看板列表用 index 選的文：從結果拿到穩定 aid，之後改用 aid 追蹤並回報給 UI 存檔
                if not aid:
                    resolved = post.get("aid")
                    if resolved:
                        aid = resolved
                        with self._lock:
                            if self._target and self._target[0] == board:
                                self._target = (board, aid, None)
                        self._emit(EVT_AID, (board, aid))

                comments = post.get("comments") or []

                # 標題（只在變化時送，網頁 fallback 也共用 current_title 去重）
                ptitle = post.get("title") or ""
                if ptitle and ptitle != current_title:
                    current_title = ptitle
                    self._emit(EVT_TITLE, ptitle)
                elif dirty and current_title is None:
                    current_title = ptitle
                    self._emit(EVT_TITLE, ptitle)  # 首輪即使標題空也送一次，讓 UI 知道抓到文章了

                # 爆文/表頭被編輯掉的文：PyPtt 解析不出推文（EXISTS 但 0 則，見 NOTES.md）。
                # Fallback 1（優先）：websocket 抓回的原始文字 full_content 仍在 → 自己解析推文，
                #   不離開 socket 連線、維持即時（網頁版有 CDN 快取 max-age=900，可能舊幾分鐘）。
                if not comments:
                    raw = post.get("full_content")
                    if raw:
                        comments = raw_parse.parse_comments_from_text(raw)
                # Fallback 2（最後手段）：連 full_content 都沒有/解析不到 → 網頁版。
                if not comments:
                    try:
                        wtitle, wcomments = web_fetch.fetch_post_web(board, aid)
                        if wcomments:
                            web_mode = True
                            comments = wcomments
                            if wtitle and wtitle != current_title:
                                current_title = wtitle
                                self._emit(EVT_TITLE, wtitle)
                    except Exception:
                        pass  # 網頁也失敗就照 0 推文處理，下一輪再試

                seen_count = self._emit_new_comments(comments, dirty, seen_count)

                now = datetime.now().strftime("%H:%M")
                if web_mode:
                    self._emit(EVT_STATUS, f"● 追蹤中（網頁），{now} 更新")
                elif elapsed > _SLOW_ROUND_SEC:
                    self._emit(EVT_STATUS, f"● 追蹤中（更新較慢·爆文），{now} 更新")
                else:
                    self._emit(EVT_STATUS, f"● 追蹤中，{now} 更新")

                # 每輪結束後再等 poll_interval 秒（換文章會提早喚醒）
                self._interruptible_sleep(self.poll_interval, wake_on_new_target=True, wake_on_command=True)
        finally:
            if logged_in:
                try:
                    ptt.logout()
                except Exception:
                    pass

    # ---- worker 內部小工具 ----

    def _handle_commands(self, ptt: "PyPtt.API") -> None:
        """處理主執行緒排入的瀏覽請求（我的最愛 / 看板文章列表）。都在 worker thread 執行。"""
        while True:
            try:
                cmd = self._cmd_q.get_nowait()
            except queue.Empty:
                return
            try:
                if cmd[0] == "fav":
                    self._emit(EVT_FAVORITES, ptt.get_favourite_boards() or [])
                elif cmd[0] == "list_pyptt":
                    # 備援：網頁版失敗才走這裡（無置頂/翻頁；轉成同一種形狀）
                    board = cmd[1]
                    posts = ptt.get_post_list(board, limit=POSTLIST_LIMIT)
                    self._emit(EVT_POSTLIST, (board, {
                        "posts": [{"url": None, "title": (p.get("title") or "").strip(),
                                   "nrec": (p.get("push_number") or "").strip(),
                                   "author": (p.get("author") or "").strip(),
                                   "date": (p.get("list_date") or "").strip(),
                                   "index": p.get("index")}
                                  for p in (posts or [])],
                        "pinned": [], "prev": None, "append": False}))
            except Exception as e:
                self._emit(EVT_STATUS, f"讀取失敗（{type(e).__name__}）")
                if cmd[0] == "list_pyptt":
                    # 讓 UI 離開「載入中」狀態
                    self._emit(EVT_POSTLIST, (cmd[1], {"posts": [], "pinned": [],
                                                       "prev": None, "append": False,
                                                       "error": type(e).__name__}))

    def _emit_new_comments(self, comments: list, dirty: bool, seen_count: int) -> int:
        """共用的 diff/backfill：dirty 首輪補最後 INITIAL_BACKFILL 則，否則只送新增。
        回傳新的 seen_count。PyPtt 與網頁模式兩條路徑都走這裡。"""
        if dirty:
            start = max(0, len(comments) - INITIAL_BACKFILL)
            for i in range(start, len(comments)):
                self._emit(EVT_COMMENT,
                           CommentData.from_ptt(comments[i], i, backfill=True))
        else:
            if len(comments) < seen_count:
                seen_count = len(comments)  # 推文被刪，重設不 crash
            for i in range(seen_count, len(comments)):
                self._emit(EVT_COMMENT, CommentData.from_ptt(comments[i], i))
        return len(comments)

    def _try_login(self, ptt: "PyPtt.API", failures: int) -> bool:
        try:
            self._emit(EVT_STATUS,
                       "連線中…" if failures == 0 else f"重連中…（第 {failures} 次）")
            ptt.login(self._id, self._pw, kick_other_session=True)
            self._emit(EVT_STATUS, "● 已登入")
            return True
        except (PyPtt.LoginError, PyPtt.WrongIDorPassword, PyPtt.WrongPassword,
                PyPtt.UnregisteredUser):
            # 帳密錯誤：重連也沒用，直接停止
            self._emit(EVT_ERROR, "登入失敗：帳號或密碼錯誤")
            self._enter_halted("登入失敗，請重新設定帳密")
            return False
        except Exception as e:
            self._emit(EVT_STATUS, f"登入失敗，重連中…（{type(e).__name__}）")
            return False

    def _backoff(self, failures: int) -> float:
        # 夾住指數避免失敗次數很大時算出天文數字（反正 min 會蓋成 _BACKOFF_CAP）
        return min(_BACKOFF_CAP, POLL_INTERVAL * (2 ** (min(failures, 8) - 1)))

    def _clear_target(self) -> None:
        with self._lock:
            self._target = None
            self._target_dirty = False

    def _enter_halted(self, status: str) -> None:
        with self._lock:
            self._halted = True
        self._emit(EVT_STATUS, status)
