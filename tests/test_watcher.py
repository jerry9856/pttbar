"""tests/test_watcher.py — PttWatcher 的純 CLI 單元測試（不需真實帳號、不連網）。

作法：把 watcher 模組裡的 PyPtt.API 換成 FakeAPI，注入腳本化的 get_post 回應，
並把輪詢間隔調小，驗證 diff / backfill / 推文變少 / 重連 / 文章被刪 / stop 乾淨結束。

真實帳號的端對端驗收（推文 10 秒內出現、拔網路重連）在 stream.py，由使用者執行（M2 驗收）。
"""

import os
import queue
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import PyPtt  # noqa: E402
import watcher as watcher_mod  # noqa: E402
from models import EVT_COMMENT, EVT_STATUS, EVT_TITLE  # noqa: E402
from watcher import PttWatcher  # noqa: E402


def _raise(exc_cls):
    """造一個可 raise 的 PyPtt 例外 instance（繞過需要 i18n 的 __init__）。"""
    return exc_cls.__new__(exc_cls)


def _mk_comments(n: int) -> list[dict]:
    return [
        {"type": "PUSH", "author": f"u{i}", "content": f"c{i}", "ip": "1.1.1.1",
         "time": "07/04 12:00"}
        for i in range(n)
    ]


class FakeAPI:
    """腳本化的假 PyPtt.API。responses 是一串「該次 get_post 要做的事」：
    - dict：直接當成 post 回傳
    - Exception 實例：raise 它
    清單用完後停在最後一個 dict（不再有新推文）。
    """

    def __init__(self, responses, favorites=None, post_lists=None):
        self._responses = responses
        self._favorites = favorites or []
        self._post_lists = post_lists or {}   # board -> list[dict]
        self._calls = 0
        self.login_count = 0
        self.logout_count = 0
        self.last_get_post_kwargs = None
        self.lock = threading.Lock()

    def login(self, ptt_id, ptt_pw, kick_other_session=False):
        with self.lock:
            self.login_count += 1

    def logout(self):
        with self.lock:
            self.logout_count += 1

    def get_post(self, board, aid=None, index=None, **kwargs):
        with self.lock:
            self.last_get_post_kwargs = {"aid": aid, "index": index}
            i = min(self._calls, len(self._responses) - 1)
            self._calls += 1
            item = self._responses[i]
        if isinstance(item, Exception):
            raise item
        return item

    def get_favourite_boards(self):
        return self._favorites

    def get_post_list(self, board, limit=20, offset=0):
        return self._post_lists.get(board, [])

    def get_post_calls(self) -> int:
        with self.lock:
            return self._calls


def _install_fake_full(monkeypatch, responses, favorites=None, post_lists=None) -> FakeAPI:
    fake = FakeAPI(responses, favorites=favorites, post_lists=post_lists)
    monkeypatch.setattr(watcher_mod.PyPtt, "API", lambda *a, **k: fake)
    return fake


def _post(comments, status="EXISTS", title="T", full_content=None, aid=None):
    return {"title": title, "post_status": status, "comments": comments,
            "full_content": full_content, "aid": aid}


@pytest.fixture
def fast(monkeypatch):
    """把輪詢/步長調小，讓測試秒殺。"""
    monkeypatch.setattr(watcher_mod, "POLL_INTERVAL", 0.05)
    monkeypatch.setattr(watcher_mod, "_SLEEP_STEP", 0.01)


def _install_fake(monkeypatch, responses) -> FakeAPI:
    fake = FakeAPI(responses)
    monkeypatch.setattr(watcher_mod.PyPtt, "API", lambda *a, **k: fake)
    return fake


def _drain(q, timeout, want_index=None):
    """收 queue 事件直到超時；若指定 want_index，收到該 index 的 comment 就提早返回。"""
    events = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            tag, payload = q.get(timeout=0.05)
        except queue.Empty:
            continue
        events.append((tag, payload))
        if want_index is not None and tag == EVT_COMMENT and payload.index == want_index:
            break
    return events


def _comment_indices(events):
    return [p.index for (t, p) in events if t == EVT_COMMENT]


def test_backfill_then_only_new(fast, monkeypatch):
    """首輪補最後 10 則（12 則→補 index 2..11），下一輪只送新增的 12、13。"""
    responses = [_post(_mk_comments(12)), _post(_mk_comments(14))]
    _install_fake(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        events = _drain(q, timeout=3.0, want_index=13)
    finally:
        w.stop()

    # 有送標題
    assert any(t == EVT_TITLE for t, _ in events)
    idxs = _comment_indices(events)
    # backfill：最後 10 則 = index 2..11；接著新增 12、13
    assert idxs[:10] == list(range(2, 12))
    assert 12 in idxs and 13 in idxs
    # 不重複、遞增
    assert idxs == sorted(idxs)
    assert len(idxs) == len(set(idxs))


def test_backfill_flag_marks_initial_batch(fast, monkeypatch):
    """首輪補的既有推文 backfill=True、之後的新推文 False（彈幕只飛既有的最新一則用）。"""
    responses = [_post(_mk_comments(12)), _post(_mk_comments(14))]
    _install_fake(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        events = _drain(q, timeout=3.0, want_index=13)
    finally:
        w.stop()

    flags = {p.index: p.backfill for (t, p) in events if t == EVT_COMMENT}
    assert all(flags[i] for i in range(2, 12))        # 首輪 backfill 批
    assert not flags[12] and not flags[13]            # 之後的新推文


def test_backfill_all_when_fewer_than_10(fast, monkeypatch):
    """既有推文 < 10 則時，全部補送（3 則→index 0,1,2）。"""
    responses = [_post(_mk_comments(3))]
    _install_fake(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        events = _drain(q, timeout=2.0, want_index=2)
    finally:
        w.stop()

    assert _comment_indices(events)[:3] == [0, 1, 2]


def test_shrink_does_not_crash(fast, monkeypatch):
    """推文變少（14→13）不 crash；之後回到 15 送出位置 13、14。

    註：index 是位置序號，推文被刪後位置位移，位置 13 會再次送出（此時是不同推文），
    這是 PLAN 6.3 以數量做 diff 的預期行為，不是重複 bug。重點是不 crash、seen_count 正確重設。
    """
    responses = [
        _post(_mk_comments(14)),  # 首輪 backfill 4..13
        _post(_mk_comments(13)),  # 變少 → 重設 seen=13（不 crash）
        _post(_mk_comments(15)),  # → 送出位置 13、14
    ]
    _install_fake(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        events = _drain(q, timeout=3.0, want_index=14)
    finally:
        w.stop()

    idxs = _comment_indices(events)
    assert idxs == sorted(idxs)      # 非遞減（含刪後重新出現的位置 13）
    assert idxs[:10] == list(range(4, 14))  # 首輪 backfill 正確
    assert 13 in idxs and 14 in idxs        # 刪後續推正常送出


def test_deleted_post_emits_status_and_stops(fast, monkeypatch):
    """post_status != 'EXISTS' → 送狀態、停止輪詢該篇，不 crash。"""
    responses = [_post([], status="DELETED_BY_AUTHOR")]
    _install_fake(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        deadline = time.monotonic() + 2.0
        saw = False
        while time.monotonic() < deadline and not saw:
            try:
                tag, payload = q.get(timeout=0.05)
            except queue.Empty:
                continue
            if tag == EVT_STATUS and "刪除" in payload:
                saw = True
    finally:
        w.stop()
    assert saw


def test_transient_false_deleted_recovers(fast, monkeypatch):
    """回歸：曾抓到過的文章因斷線被誤判成「被刪」時，應重連確認後恢復，不可立刻清掉目標。

    重現使用者回報：斷線時 get_post 回傳誤判的 deleted 畫面 → 舊版單次就清目標停止。
    """
    responses = [
        _post(_mk_comments(11)),                    # 首輪成功（ever_fetched=True）
        _post([], status="DELETED_BY_UNKNOWN"),     # 斷線誤判「被刪」（單次不算數）
        _post(_mk_comments(13)),                    # 重連後恢復 → 新增 11、12
    ]
    fake = _install_fake(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        events = _drain(q, timeout=4.0, want_index=12)
    finally:
        w.stop()

    idxs = _comment_indices(events)
    assert 11 in idxs and 12 in idxs           # 誤判後有恢復並繼續送新推文
    assert fake.login_count >= 2               # 有重連確認
    statuses = [p for (t, p) in events if t == EVT_STATUS]
    assert not any("已刪除" in s for s in statuses)  # 不可誤判成真的刪除


def test_persistent_deletion_confirmed_stops(fast, monkeypatch):
    """曾抓到過的文章連續 _DELETED_CONFIRM 次都讀到被刪 → 判定真的刪除、停止。"""
    responses = [
        _post(_mk_comments(11)),                    # 首輪成功
        _post([], status="DELETED_BY_AUTHOR"),      # 被刪 1
        _post([], status="DELETED_BY_AUTHOR"),      # 被刪 2
        _post([], status="DELETED_BY_AUTHOR"),      # 被刪 3 → 判定真刪
    ]
    _install_fake(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        deadline = time.monotonic() + 4.0
        saw = False
        while time.monotonic() < deadline and not saw:
            try:
                tag, payload = q.get(timeout=0.05)
            except queue.Empty:
                continue
            if tag == EVT_STATUS and "文章已刪除" in payload:
                saw = True
    finally:
        w.stop()
    assert saw


def test_reconnect_after_transient_error(fast, monkeypatch):
    """單次例外 → 重連（重新 login）後繼續運作，仍能送出新推文。"""
    responses = [
        _post(_mk_comments(11)),   # 首輪 backfill 1..10
        RuntimeError("boom"),      # 模擬斷線 → 走重連
        _post(_mk_comments(13)),   # 重連後新增 11、12
    ]
    fake = _install_fake(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        events = _drain(q, timeout=4.0, want_index=12)
    finally:
        w.stop()

    assert fake.login_count >= 2  # 重連 = 至少登入兩次
    idxs = _comment_indices(events)
    assert 11 in idxs and 12 in idxs
    assert len(idxs) == len(set(idxs))  # 重連未造成重送


def test_nosuchboard_on_disconnect_reconnects_not_stops(fast, monkeypatch):
    """回歸：曾成功抓到後才出現的 NoSuchBoard（＝斷線）應重連並恢復，不可停止。

    重現使用者回報的 bug：拔網路 → PyPtt 丟 NoSuchBoard → 舊版把它當看板錯誤清掉目標，
    網路回來也不動。修正後應走重連、網路回來繼續送新推文。
    """
    responses = [
        _post(_mk_comments(11)),            # 首輪成功 backfill 1..10（ever_fetched=True）
        _raise(PyPtt.NoSuchBoard),          # 斷線（PyPtt 丟 NoSuchBoard）
        _raise(PyPtt.NoSuchBoard),          # 仍斷線
        _post(_mk_comments(13)),            # 網路回來 → 新增 11、12
    ]
    fake = _install_fake(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("SportLottery", "#1gIBD08k")
    try:
        events = _drain(q, timeout=5.0, want_index=12)
    finally:
        w.stop()

    idxs = _comment_indices(events)
    assert 11 in idxs and 12 in idxs           # 網路回來後恢復送新推文
    assert fake.login_count >= 2               # 有重連（重新登入）
    # 不可出現「沒有這個看板」而永久停止
    statuses = [p for (t, p) in events if t == EVT_STATUS]
    assert any("重連" in s for s in statuses)


def test_connection_errors_never_permanently_halt(fast, monkeypatch):
    """回歸（使用者回報）：連續斷線很多次（遠超舊的 10 次上限）也**不會永久停止**，
    網路一恢復就自動接回、繼續送新推文——不需要登出重登。"""
    responses = [_post(_mk_comments(11))]        # 首輪成功（ever_fetched=True）
    responses += [_raise(PyPtt.ConnectError) for _ in range(15)]  # 斷線 15 次（> 舊上限 10）
    responses += [_post(_mk_comments(13))]       # 網路回來 → 新增 11、12
    fake = _install_fake(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        events = _drain(q, timeout=6.0, want_index=12)
    finally:
        w.stop()

    idxs = _comment_indices(events)
    assert 11 in idxs and 12 in idxs   # 斷線 15 次後仍自動恢復送新推文
    statuses = [p for (t, p) in events if t == EVT_STATUS]
    assert not any("已停止" in s for s in statuses)   # 不可進入「已停止」永久狀態


def test_retry_interrupts_backoff(fast, monkeypatch):
    """retry() 會中斷（長）退避 sleep、立即重連，不必枯等退避。"""
    # 退避拉成固定 10 秒（poll_interval 仍然很小），凸顯 retry 的「立即」效果
    monkeypatch.setattr(watcher_mod.PttWatcher, "_backoff", lambda self, failures: 10.0)
    responses = [
        _post(_mk_comments(11)),          # 首輪成功
        _raise(PyPtt.ConnectError),       # 斷線 → 進 10 秒退避
        _post(_mk_comments(13)),          # 重連後新增 11、12
    ]
    fake = _install_fake(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        _drain(q, timeout=2.0, want_index=10)   # 等首輪抓完
        # 等到真的進入退避（收到斷線狀態）才按 retry，否則可能還沒撞上斷線
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                tag, payload = q.get(timeout=0.05)
            except queue.Empty:
                continue
            if tag == EVT_STATUS and "中斷" in payload:
                break
        t0 = time.monotonic()
        w.retry()                                # 立即重連（不必等滿 10 秒退避）
        events = _drain(q, timeout=4.0, want_index=12)
        elapsed = time.monotonic() - t0
    finally:
        w.stop()

    idxs = _comment_indices(events)
    assert 11 in idxs and 12 in idxs
    assert elapsed < 5.0, f"retry 後花了 {elapsed:.2f}s（應遠小於 10 秒退避）"
    assert fake.login_count >= 2   # 有重新登入


def test_credential_error_still_halts(fast, monkeypatch):
    """帳密錯誤仍然進 halted（連線問題永久重試，但帳密錯誤重試也沒用）。"""
    class BadLoginAPI(FakeAPI):
        def login(self, ptt_id, ptt_pw, kick_other_session=False):
            super().login(ptt_id, ptt_pw, kick_other_session)
            raise _raise(PyPtt.WrongIDorPassword)

    fake = BadLoginAPI([_post(_mk_comments(1))])
    monkeypatch.setattr(watcher_mod.PyPtt, "API", lambda *a, **k: fake)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        deadline = time.monotonic() + 2.0
        saw = False
        while time.monotonic() < deadline and not saw:
            try:
                tag, payload = q.get(timeout=0.05)
            except queue.Empty:
                continue
            if tag == EVT_STATUS and "重新設定帳密" in payload:
                saw = True
    finally:
        w.stop()
    assert saw
    assert fake.login_count < 30   # halted 後不會瘋狂重試（idle 等待）


def test_nosuchboard_on_first_fetch_clears_target(fast, monkeypatch):
    """第一次就抓不到看板（真的打錯看板名）→ 提示並停止輪詢，不無限重連。"""
    responses = [_raise(PyPtt.NoSuchBoard)]
    _install_fake(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("NoSuchBoardXYZ", "#12345678")
    try:
        deadline = time.monotonic() + 2.0
        saw = False
        while time.monotonic() < deadline and not saw:
            try:
                tag, payload = q.get(timeout=0.05)
            except queue.Empty:
                continue
            if tag == EVT_STATUS and "沒有這個看板" in payload:
                saw = True
    finally:
        w.stop()
    assert saw


def _mk_web_comments(n: int) -> list[dict]:
    return [
        {"type": "推", "author": f"w{i}", "content": f"web{i}", "time": "07/04 18:00", "ip": None}
        for i in range(n)
    ]


def _raw_text(n: int) -> str:
    """組出含 n 則推文的 full_content 原始文字。"""
    lines = ["內文第一行（表頭已被編輯掉）", ""]
    lines += [f"推 u{i}: c{i}  07/04 18:0{i % 10}" for i in range(n)]
    return "\n".join(lines)


def test_socket_raw_fallback_preferred_over_web(fast, monkeypatch):
    """PyPtt 0 推文但 full_content 有 → 用 socket 原始文字解析（即時），不碰網頁版。"""
    responses = [
        _post([], title="T", full_content=_raw_text(11)),   # 首輪 backfill 1..10
        _post([], title="T", full_content=_raw_text(13)),   # 新增 11、12
    ]
    fake = _install_fake(monkeypatch, responses)

    def fake_web(board, aid, timeout=10.0):
        raise AssertionError("full_content 可解析時不應呼叫網頁版")

    monkeypatch.setattr(watcher_mod.web_fetch, "fetch_post_web", fake_web)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("LoL", "#1gI76O9V")
    try:
        events = _drain(q, timeout=4.0, want_index=12)
    finally:
        w.stop()

    idxs = _comment_indices(events)
    assert idxs[:10] == list(range(1, 11))   # backfill
    assert 11 in idxs and 12 in idxs         # 後續 diff
    assert fake.get_post_calls() >= 2        # 持續走 PyPtt（沒切網頁模式）
    # 推文型別有正確轉換（PUSH → 推）
    comment_types = {p.type for (t, p) in events if t == EVT_COMMENT}
    assert comment_types == {"推"}


def test_web_fallback_when_pyptt_returns_zero_comments(fast, monkeypatch):
    """爆文/直播文：PyPtt 回 EXISTS 但 0 推文 → 自動改抓網頁版，之後都走網頁模式。"""
    responses = [_post([], title="T")] * 10  # PyPtt 永遠 0 推文
    fake = _install_fake(monkeypatch, responses)

    web_calls = {"n": 0}

    def fake_web(board, aid, timeout=10.0):
        web_calls["n"] += 1
        n = 11 if web_calls["n"] == 1 else 13   # 第二次起多 2 則
        return "[電競] 直播文", _mk_web_comments(n)

    monkeypatch.setattr(watcher_mod.web_fetch, "fetch_post_web", fake_web)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("LoL", "#1gI76O9V")
    try:
        events = _drain(q, timeout=4.0, want_index=12)
    finally:
        w.stop()

    idxs = _comment_indices(events)
    assert idxs[:10] == list(range(1, 11))      # 首輪用網頁推文 backfill 最後 10 則
    assert 11 in idxs and 12 in idxs            # 之後網頁模式 diff 出新推文
    titles = [p for (t, p) in events if t == EVT_TITLE]
    assert "[電競] 直播文" in titles            # 網頁標題有送
    assert fake.get_post_calls() == 1           # 切網頁模式後不再走 PyPtt
    statuses = [p for (t, p) in events if t == EVT_STATUS]
    assert any("網頁" in s for s in statuses)   # 狀態有標示網頁模式


def test_web_mode_404_confirms_deletion(fast, monkeypatch):
    """網頁模式下連續 404 → 判定文章已刪除、停止。"""
    responses = [_post([], title="T")] * 10
    _install_fake(monkeypatch, responses)

    web_calls = {"n": 0}

    def fake_web(board, aid, timeout=10.0):
        web_calls["n"] += 1
        if web_calls["n"] == 1:
            return "T", _mk_web_comments(5)     # 先成功一次（進入網頁模式）
        raise watcher_mod.web_fetch.WebNotFound("gone")

    monkeypatch.setattr(watcher_mod.web_fetch, "fetch_post_web", fake_web)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("LoL", "#1gI76O9V")
    try:
        deadline = time.monotonic() + 4.0
        saw = False
        while time.monotonic() < deadline and not saw:
            try:
                tag, payload = q.get(timeout=0.05)
            except queue.Empty:
                continue
            if tag == EVT_STATUS and "文章已刪除" in payload:
                saw = True
    finally:
        w.stop()
    assert saw


def test_no_web_fallback_when_pyptt_has_comments(fast, monkeypatch):
    """一般文章（PyPtt 有推文）不應觸發網頁 fallback。"""
    responses = [_post(_mk_comments(11))]
    _install_fake(monkeypatch, responses)

    def fake_web(board, aid, timeout=10.0):
        raise AssertionError("不應呼叫網頁 fallback")

    monkeypatch.setattr(watcher_mod.web_fetch, "fetch_post_web", fake_web)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        events = _drain(q, timeout=2.0, want_index=10)
    finally:
        w.stop()
    assert 10 in _comment_indices(events)


def test_stop_is_clean_and_quick(fast, monkeypatch):
    """stop() 後 worker thread 2 秒內結束，且有登出。"""
    responses = [_post(_mk_comments(5))]
    fake = _install_fake(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    time.sleep(0.3)
    t0 = time.monotonic()
    w.stop()
    assert (time.monotonic() - t0) < 2.0
    assert not w._thread.is_alive()
    assert fake.logout_count >= 1


def test_pause_skips_polling(fast, monkeypatch):
    """暫停後不再送新推文；繼續後恢復。"""
    responses = [_post(_mk_comments(11)), _post(_mk_comments(20))]
    _install_fake(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        # 收到首輪 backfill 後暫停
        _drain(q, timeout=2.0, want_index=10)
        w.pause()
        time.sleep(0.3)
        # 清掉暫停狀態訊息，記錄暫停期間的 comment 數
        drained = _drain(q, timeout=0.3)
        paused_comments = _comment_indices(drained)
        # 暫停期間不應冒出第二輪的新推文（index >= 11）
        assert all(i <= 10 for i in paused_comments)
    finally:
        w.stop()


# ---- 瀏覽：我的最愛 / 看板文章列表 / 用 index 追蹤 ----

from models import EVT_AID, EVT_FAVORITES, EVT_POSTLIST  # noqa: E402


def test_request_favorites(fast, monkeypatch):
    """request_favorites → 收到 EVT_FAVORITES（清單內容正確）。"""
    favs = [{"board": "Gossiping", "title": "八卦"}, {"board": "Stock", "title": "股票"}]
    _install_fake_full(monkeypatch, [_post([])], favorites=favs)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.request_favorites()
    try:
        deadline = time.monotonic() + 2.0
        got = None
        while time.monotonic() < deadline and got is None:
            try:
                tag, payload = q.get(timeout=0.05)
            except queue.Empty:
                continue
            if tag == EVT_FAVORITES:
                got = payload
    finally:
        w.stop()
    assert got is not None
    assert [b["board"] for b in got] == ["Gossiping", "Stock"]


def _wait_postlist(q, w, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            tag, payload = q.get(timeout=0.05)
        except queue.Empty:
            continue
        if tag == EVT_POSTLIST:
            return payload
    return None


def test_request_post_list_web(fast, monkeypatch):
    """request_post_list → 走網頁版，EVT_POSTLIST 回 {posts, pinned, prev, append=False}。"""
    _install_fake_full(monkeypatch, [_post([])])

    def fake_list(board, page=None, timeout=10.0):
        assert board == "Stock" and page is None
        return {"posts": [{"url": "https://www.ptt.cc/bbs/Stock/M.1.A.000.html",
                           "title": "文章A", "nrec": "5", "author": "aa", "date": "7/04"}],
                "pinned": [{"url": "https://www.ptt.cc/bbs/Stock/M.2.A.000.html",
                            "title": "[公告] 板規", "nrec": "", "author": "mod", "date": "1/01"}],
                "prev": 10180}

    monkeypatch.setattr(watcher_mod.web_fetch, "fetch_board_list_web", fake_list)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.request_post_list("Stock")
    try:
        got = _wait_postlist(q, w)
    finally:
        w.stop()
    assert got is not None
    board, data = got
    assert board == "Stock"
    assert data["append"] is False and data["prev"] == 10180
    assert data["posts"][0]["title"] == "文章A"
    assert data["pinned"][0]["title"] == "[公告] 板規"


def test_post_list_falls_back_to_pyptt(fast, monkeypatch):
    """網頁版失敗 → 退回 PyPtt get_post_list，轉成同一種形狀（index 欄位保留）。"""
    posts = [{"index": 100, "title": "文章A", "author": "aa", "push_number": "5",
              "list_date": "7/04"}]
    _install_fake_full(monkeypatch, [_post([])], post_lists={"Stock": posts})

    def fake_list(board, page=None, timeout=10.0):
        raise RuntimeError("web down")

    monkeypatch.setattr(watcher_mod.web_fetch, "fetch_board_list_web", fake_list)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.request_post_list("Stock")
    try:
        got = _wait_postlist(q, w)
    finally:
        w.stop()
    assert got is not None
    _, data = got
    assert data["posts"][0]["index"] == 100 and data["posts"][0]["url"] is None
    assert data["prev"] is None


def test_request_post_list_more_appends(fast, monkeypatch):
    """request_post_list_more → append 模式回報較舊頁。"""
    _install_fake_full(monkeypatch, [_post([])])

    def fake_list(board, page=None, timeout=10.0):
        assert page == 10180
        return {"posts": [{"url": "https://www.ptt.cc/bbs/Stock/M.0.A.000.html",
                           "title": "更舊的文章", "nrec": "", "author": "old", "date": "7/03"}],
                "pinned": [], "prev": 10179}

    monkeypatch.setattr(watcher_mod.web_fetch, "fetch_board_list_web", fake_list)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.request_post_list_more("Stock", 10180)
    try:
        got = _wait_postlist(q, w)
    finally:
        w.stop()
    assert got is not None
    _, data = got
    assert data["append"] is True and data["prev"] == 10179
    assert data["posts"][0]["title"] == "更舊的文章"


def test_track_by_index_resolves_aid(fast, monkeypatch):
    """用 index 追蹤：get_post 用 index 呼叫、從結果拿到 aid 並回報 EVT_AID，之後改用 aid。"""
    responses = [
        _post(_mk_comments(11), aid="#1abcdefg"),   # 首輪（index）→ 解析出 aid
        _post(_mk_comments(13), aid="#1abcdefg"),   # 後續
    ]
    fake = _install_fake_full(monkeypatch, responses)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("LoL", index=123)
    try:
        events = _drain(q, timeout=3.0, want_index=12)
    finally:
        w.stop()

    # 有回報解析出的 aid
    aids = [p for (t, p) in events if t == EVT_AID]
    assert ("LoL", "#1abcdefg") in aids
    # 首輪確實用 index 呼叫 get_post（而非 aid）
    # （最後一次呼叫已是 aid 模式，故只驗有解析出 aid＋推文正常）
    idxs = _comment_indices(events)
    assert idxs[:10] == list(range(1, 11)) and 11 in idxs and 12 in idxs


def test_post_list_latency_independent_of_poll(fast, monkeypatch):
    """加速回歸：看板列表走獨立執行緒，即使 worker 卡在 5 秒輪詢 sleep 也要 <1 秒回來。"""
    monkeypatch.setattr(watcher_mod, "POLL_INTERVAL", 5.0)   # 模擬真實 5 秒輪詢
    responses = [_post(_mk_comments(11))]
    _install_fake_full(monkeypatch, responses)

    def fake_list(board, page=None, timeout=10.0):
        return {"posts": [{"url": "u", "title": "T", "nrec": "", "author": "a", "date": ""}],
                "pinned": [], "prev": None}

    monkeypatch.setattr(watcher_mod.web_fetch, "fetch_board_list_web", fake_list)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        _drain(q, timeout=2.0, want_index=10)   # 等首輪抓完 → worker 進入 5 秒 sleep
        t0 = time.monotonic()
        w.request_post_list("Stock")
        got = _wait_postlist(q, w, timeout=3.0)
        elapsed = time.monotonic() - t0
    finally:
        w.stop()
    assert got is not None
    assert elapsed < 1.0, f"看板列表花了 {elapsed:.2f}s（應該 <1s，不用等輪詢空檔）"


def test_favorites_wakes_poll_sleep(fast, monkeypatch):
    """加速回歸：我的最愛指令會喚醒輪詢 sleep，不用等滿 POLL_INTERVAL。"""
    monkeypatch.setattr(watcher_mod, "POLL_INTERVAL", 5.0)
    monkeypatch.setattr(watcher_mod, "_SLEEP_STEP", 0.05)
    favs = [{"board": "Stock", "title": "股票"}]
    _install_fake_full(monkeypatch, [_post(_mk_comments(11))], favorites=favs)

    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    w.start()
    w.track("Test", "#12345678")
    try:
        _drain(q, timeout=2.0, want_index=10)   # worker 進入 5 秒 sleep
        t0 = time.monotonic()
        w.request_favorites()
        deadline = time.monotonic() + 3.0
        got = None
        while time.monotonic() < deadline and got is None:
            try:
                tag, payload = q.get(timeout=0.05)
            except queue.Empty:
                continue
            if tag == EVT_FAVORITES:
                got = payload
        elapsed = time.monotonic() - t0
    finally:
        w.stop()
    assert got is not None
    assert elapsed < 1.5, f"我的最愛花了 {elapsed:.2f}s（sleep 應被指令喚醒）"


def test_poll_interval_default_and_clamp():
    """輪詢間隔：未指定 → 執行期讀模組 POLL_INTERVAL（測試 monkeypatch 才有效）；
    指定/set_poll_interval → 夾在 POLL_MIN~POLL_MAX（使用者設定用）。"""
    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    assert w.poll_interval == watcher_mod.POLL_INTERVAL

    w2 = PttWatcher("id", "pw", q, poll_interval=3)
    assert w2.poll_interval == 3.0
    w2.set_poll_interval(0.2)     # 低於下限 → 夾住
    assert w2.poll_interval == watcher_mod.POLL_MIN
    w2.set_poll_interval(999)     # 高於上限 → 夾住
    assert w2.poll_interval == watcher_mod.POLL_MAX
    w2.set_poll_interval(7)
    assert w2.poll_interval == 7.0


def test_poll_interval_follows_module_patch(monkeypatch):
    """回歸保護：既有測試靠 monkeypatch 模組 POLL_INTERVAL 加速，
    未明確指定間隔的 watcher 必須跟著模組值走（不能在 __init__ 綁死）。"""
    q: queue.Queue = queue.Queue()
    w = PttWatcher("id", "pw", q)
    monkeypatch.setattr(watcher_mod, "POLL_INTERVAL", 0.05)
    assert w.poll_interval == 0.05
