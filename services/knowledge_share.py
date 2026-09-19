# -*- coding: utf-8 -*-
"""services/knowledge_share.py — 公開分享一本書的研究（docs/KNOWLEDGE_BASE_PLAN.md §9.9）。

owner 2026-09-19：「可以有一個公開分享的連結，讓我把這本書的研究分享出去
（但是點不到我其他的地方）」。

🔴 這支決定「外面的人看得到什麼」。改它之前先想清楚：那一頁**不需要登入**。

**哪些東西出去由他自己勾**（owner 2026-09-19：「公開分享的內容讓我勾選」）——
清單與預設在 `knowledge_logic.SHARE_PARTS`。書名與作者一定會出去（那是在講哪一本書），
其餘沒勾就不在回傳裡，連鍵都不會有。

預設只開三項：基本資訊、標籤、延伸。延伸每一則本來就是公開發表的東西、附出處與連結，
轉給別人看沒有問題。筆記與結論是他自己的；章節重點、骨架、書裡的圖是原書的內容
（公開有版權問題）—— 那幾項預設關，勾了才出去，畫面上也寫著那是誰的東西。

**討論（chat.json）與全文（full_text.txt）任何情況都不分享**，沒有那個選項。
"""
from __future__ import annotations

import os
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
    """分享 id 是 32 hex（跟 16 hex 的書 id 刻意不同長度）。"""
    return bool(_SHARE_RE.match(str(share_id or "")))


def new_share_id() -> str:
    """隨機 32 hex，猜不到。"""
    return secrets.token_hex(16)


def share_url(share_id: str) -> str:
    """公開頁的絕對網址（id 放在 hash，不進伺服器紀錄）。"""
    return f"{PUBLIC_BASE}#{share_id}"


def share_of(book_id: str) -> dict:
    """這本書的分享狀態：`{on, id, url, at, parts}`。沒開就 `on=False`。"""
    sh = ks.read_meta(book_id).get("share") or {}
    sid = sh.get("id") if is_valid_share_id(sh.get("id")) else ""
    return {"on": bool(sid), "id": sid, "url": share_url(sid) if sid else "",
            "at": sh.get("at") or "",
            # 舊的分享沒有 parts（2026-09-19 之前開的）→ 用預設那組，行為不變
            "parts": kl.normalize_parts(sh.get("parts"))}


def set_share(book_id: str, on: bool, parts=None) -> dict:
    """開或關、順便改要分享哪幾項。

    關掉再開會換一組新的 id —— 舊連結立刻失效（那才是「關掉」該有的意思）。
    已經開著的話只換 `parts`，id 不動（他調整勾選不該讓已經發出去的連結失效）。
    """
    if not on:
        ks.update_meta_share(book_id, None)
        return {"on": False, "id": "", "url": "", "at": "", "parts": list(kl.SHARE_DEFAULT)}
    cur = share_of(book_id)
    picked = kl.normalize_parts(parts if parts is not None else cur["parts"])
    if cur["on"]:
        ks.update_meta_share(book_id, {"id": cur["id"], "at": cur["at"], "parts": picked})
    else:
        ks.update_meta_share(book_id, {"id": new_share_id(), "at": ks.now_iso(), "parts": picked})
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


def shared_parts(book_id: str) -> list:
    """這本書目前勾了哪幾項要分享（沒設定過＝預設那組）。"""
    return share_of(book_id)["parts"]


def book_of(share_id: str) -> Optional[str]:
    """分享 id → 書 id。PDF 那支要拿它去讀圖檔（圖要內嵌進 PDF）。"""
    return _find_book(share_id)


def full_view(book_id: str) -> dict:
    """整本（他自己的書頁與他自己的 PDF 用）。沒有勾選這回事 —— 東西都是他的。

    討論與全文一樣不在裡面（`_build` 根本沒有那兩個分支）。
    """
    return _build(book_id, list(kl.SHARE_PART_KEYS))


def public_view(share_id: str) -> Optional[dict]:
    """公開頁要的那一包。查不到就回 None（端點一律回同一句 404）。

    🔴 **沒勾的項目連鍵都不會出現**。書名與作者一定在（那是在講哪一本書）。
    """
    book_id = _find_book(share_id)
    if not book_id:
        return None
    meta = ks.read_meta(book_id)
    return _build(book_id, kl.normalize_parts((meta.get("share") or {}).get("parts")), meta=meta)


def _build(book_id: str, parts: list, meta: Optional[dict] = None) -> dict:
    """照 `parts` 組一本書的內容。公開頁與 PDF 都走這裡，兩邊看到的東西才會一樣。

    🔴 **沒勾的項目連鍵都不會出現**。討論（chat.json）與全文（full_text.txt）
    任何情況都不出去 —— 這裡沒有那兩個分支，也不要加。
    """
    meta = meta if meta is not None else ks.read_meta(book_id)
    out = {
        "title": meta.get("title") or "",
        "author": meta.get("author") or "",
        "shared_at": (meta.get("share") or {}).get("at") or "",
        "parts": parts,
    }
    if kl.shares(parts, "info"):
        out["info"] = kl.normalize_info(meta.get("info"))
    if kl.shares(parts, "tags"):
        out["tags"] = kl.normalize_tags(meta.get("tags"))
    if kl.shares(parts, "extend"):
        rows = [i for i in ks.read_extend(book_id) if i.get("rating") != "useless"]
        out["extend"] = [{k: i.get(k, "") for k in
                          ("date", "title_zh", "title_original", "url", "lang", "source",
                           "published", "summary_zh", "why_it_matters", "chapter_guess")}
                         for i in rows]
    if kl.shares(parts, "conclusion"):
        out["conclusion"] = ks.read_doc(book_id, "conclusion")
    if kl.shares(parts, "notes"):
        out["notes"] = ks.read_doc(book_id, "notes")
    if kl.shares(parts, "skill"):
        out["skill"] = ks.read_doc(book_id, "skill")
        out["cheatsheet"] = ks.read_doc(book_id, "cheatsheet")
    if kl.shares(parts, "chapters"):
        # 頁碼一起給：公開頁的「圖輯」要靠它把圖分到各章（同他自己的書頁）
        out["chapters"] = [{"n": c["n"], "title": c.get("title") or "",
                            "start_page": c.get("start_page"), "end_page": c.get("end_page"),
                            "md": ks.read_chapter(book_id, c["n"]).get("md") or ""}
                           for c in ks.chapter_rows_public(book_id)]
    if kl.shares(parts, "gallery"):
        out["gallery"] = ks.list_assets(book_id)
    return out


def public_pdf(share_id: str) -> Optional[tuple]:
    """公開頁那顆「下載研究筆記」要的東西：`(book_id, 那一包)`。

    **沒勾「可以下載 PDF」就回 None**（端點回 404，同一句話）——
    他可以讓人在網頁上讀，但不給帶走（owner 2026-09-19）。
    """
    data = public_view(share_id)
    if data is None or not kl.shares(data.get("parts"), "pdf"):
        return None
    return _find_book(share_id), data


def public_source(share_id: str) -> Optional[str]:
    """公開頁那顆「下載原書」要的檔案路徑。

    🔴 **預設是關的**，勾了才給（`SHARE_PARTS` 的 `source`）—— 這一顆給出去的是
    **整本原書**，不是他整理的研究。owner 2026-09-19 明講「研究報告與書籍都可以」，
    所以這個選項存在；但它永遠是一個要他自己按下去的決定。
    """
    book_id = _find_book(share_id)
    if not book_id or not kl.shares(shared_parts(book_id), "source"):
        return None
    try:
        return ks.source_path(book_id)
    except Exception:
        return None


def public_source_name(share_id: str) -> str:
    """原書下載時看到的檔名（同他自己那一面）。"""
    book_id = _find_book(share_id)
    return ks.source_filename(book_id) if book_id else "book.pdf"


def public_asset(share_id: str, name: str) -> Optional[str]:
    """公開頁要看圖時的檔案路徑。**沒勾「書裡的圖」就一律回 None**（連檔名對不對都不用談）。"""
    book_id = _find_book(share_id)
    if not book_id or not kl.shares(shared_parts(book_id), "gallery"):
        return None
    try:
        path = ks.asset_path(book_id, name)
    except Exception:
        return None
    return path if os.path.isfile(path) else None
