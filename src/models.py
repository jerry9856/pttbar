"""models.py — 共用資料模型與事件型別（純資料，不 import rumps/AppKit）。

watcher 把 PyPtt 回傳的 comment dict 轉成 CommentData 這個統一格式，UI 層只認 CommentData
（PLAN.md 6.2）。PyPtt 的 comment['type'] 實測為字串 'PUSH'/'BOO'/'ARROW'（見 NOTES.md）。

事件（worker → UI，唯一跨執行緒通道 queue.Queue，PLAN.md 第 4 節）統一為 tuple：
  (EVT_COMMENT, CommentData)  一則新推文
  (EVT_TITLE,   str)          目前追蹤文章的標題（切換/首次抓到時送）
  (EVT_STATUS,  str)          連線/狀態字串（如 "● 追蹤中，12:34 更新"）
  (EVT_ERROR,   str)          錯誤（如登入失敗、連續重連失敗停止）
"""

from dataclasses import dataclass

# 事件標籤
EVT_COMMENT = "comment"
EVT_TITLE = "title"
EVT_STATUS = "status"
EVT_ERROR = "error"
EVT_FAVORITES = "favorites"   # payload: list[dict]（board/title），我的最愛看板
EVT_POSTLIST = "postlist"     # payload: (board, list[dict])（index/title/author/push_number…）
EVT_AID = "aid"               # payload: (board, aid)：用 index 追蹤時解析出穩定 aid 後回報

# PyPtt comment['type'] 字串 → 顯示符號（NOTES.md 實測確認）
COMMENT_TYPE_SYMBOL = {"PUSH": "推", "BOO": "噓", "ARROW": "→"}


@dataclass
class CommentData:
    type: str      # "推" / "噓" / "→"
    author: str    # 推文者 id
    content: str   # 推文內容（去頭尾空白）
    time: str      # PTT 顯示的時間字串，原樣保存（如 "07/04 12:34"）
    index: int     # 在文章中的序號（從 0），diff 用
    backfill: bool = False  # True＝開始追蹤時補的「既有」推文（非新推文；彈幕只飛最新一則）

    @classmethod
    def from_ptt(cls, comment: dict, index: int,
                 backfill: bool = False) -> "CommentData":
        """把 PyPtt 的一則 comment dict 轉成 CommentData。"""
        raw_type = comment.get("type")
        return cls(
            type=COMMENT_TYPE_SYMBOL.get(raw_type, raw_type or "→"),
            author=comment.get("author") or "",
            content=(comment.get("content") or "").strip(),
            time=comment.get("time") or "",
            index=index,
            backfill=backfill,
        )
