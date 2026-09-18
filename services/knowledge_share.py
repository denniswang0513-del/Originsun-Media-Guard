# -*- coding: utf-8 -*-
"""services/knowledge_share.py — 公開分享一本書的研究（docs/KNOWLEDGE_BASE_PLAN.md §9.9）。

owner 2026-09-19：「可以有一個公開分享的連結，讓我把這本書的研究分享出去
（但是點不到我其他的地方）」。

🔴 這支決定「外面的人看得到什麼」。改它之前先想清楚：那一頁**不需要登入**。

分享出去的是 **延伸**（研究助理從網路上找到的資料）＋ 這本書是哪一本。
理由：延伸每一則本來就是公開發表的東西、而且附出處與連結，轉給別人看沒有問題。
**不分享**筆記、結論、討論（那是他自己的），也不分享章節重點、骨架、全文、書裡的圖
（那是原書的內容，公開有版權問題）。
"""
from __future__ import annotations

import re
import secrets
from typing import Optional

from core import knowledge_logic as kl
from services import knowledge_service as ks

#: 分享網址上的那一段：32 hex（隨機，猜不到）。跟書 id（16 hex）刻意不同長度，一眼分得出來。
_SHARE_RE = re.compile(r"^[0-9a-f]{32}$")
#: 公開頁的網址（office-api 那一面不供這頁，要絕對網址）
PUBLIC_BASE = "https://foundry.originsun-studio.com/share.html"


def is_valid_share_id(share_id) -> bool:
    return bool(_SHARE_RE.match(str(share_id or "")))


def new_share_id() -> str:
    return secrets.token_hex(16)


def share_url(share_id: str) -> str:
    return f"{PUBLIC_BASE}#{share_id}"


def share_of(book_id: str) -> dict:
    """這本書的分享狀態：`{on, id, url, at}`。沒開就 `on=False`。"""
    sh = ks.read_meta(book_id).get("share") or {}
    sid = sh.get("id") if is_valid_share_id(sh.get("id")) else ""
    return {"on": bool(sid), "id": sid, "url": share_url(sid) if sid else "", "at": sh.get("at") or ""}


def set_share(book_id: str, on: bool) -> dict:
    """開或關。關掉再開會換一組新的 id —— 舊連結立刻失效（那才是「關掉」該有的意思）。"""
    if not on:
        ks.update_meta_share(book_id, None)
        return {"on": False, "id": "", "url": "", "at": ""}
    cur = share_of(book_id)
    if cur["on"]:
        return cur
    sid = new_share_id()
    ks.update_meta_share(book_id, {"id": sid, "at": ks.now_iso()})
    return share_of(book_id)


def _find_book(share_id: str) -> Optional[str]:
    """share id → book id。掃書架的 meta（幾十本，很快），沒有索引檔要維護。"""
    if not is_valid_share_id(share_id):
        return None
    for b in ks.list_books():
        try:
            if (ks.read_meta(b["id"]).get("share") or {}).get("id") == share_id:
                return b["id"]
        except ks.BookNotFound:
            continue
    return None


def public_view(share_id: str) -> Optional[dict]:
    """公開頁要的那一包。查不到就回 None（端點一律回同一句 404）。

    🔴 這裡列出來的就是外面看得到的**全部**。不要加 notes／conclusion／chapters／skill／full_text。
    """
    book_id = _find_book(share_id)
    if not book_id:
        return None
    meta = ks.read_meta(book_id)
    rows = [i for i in ks.read_extend(book_id) if i.get("rating") != "useless"]
    return {
        "title": meta.get("title") or "",
        "author": meta.get("author") or "",
        "info": kl.normalize_info(meta.get("info")),
        "tags": kl.normalize_tags(meta.get("tags")),
        "extend": [{k: i.get(k, "") for k in
                    ("date", "title_zh", "title_original", "url", "lang", "source",
                     "published", "summary_zh", "why_it_matters", "chapter_guess")}
                   for i in rows],
        "shared_at": (meta.get("share") or {}).get("at") or "",
    }
