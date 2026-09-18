# -*- coding: utf-8 -*-
"""routers/api_knowledge_public.py — 一本書的**公開分享**（docs/KNOWLEDGE_BASE_PLAN.md §9.9）。

owner 2026-09-19：「可以有一個公開分享的連結，讓我把這本書的研究分享出去
（但是點不到我其他的地方）」。

🔴 **這是整個知識庫唯一不需要登入的檔案**，所以它獨立成一支 —— 想知道「哪些東西是公開的」，
看這一個檔就夠了，不用在一百多行的私有端點裡找哪一支忘了守。
（`api_knowledge.py` 那支有「每一支端點都要 `_guard`」的測試，公開的混進去會把那條變成有例外。）

守則，每一條都有測試釘著（tests/unit/test_knowledge_share.py）：
  1. 只認 `meta.share.id`（32 hex，隨機）。書 id 不能當分享網址用。
  2. 沒開分享（meta 沒有 share）就是 404 —— 不是 403，不透露這本書存不存在。
  3. **只回一本書**，而且只回他**勾選**的那幾項（`knowledge_logic.SHARE_PARTS`，預設只有
     基本資訊／標籤／延伸）。書名與作者一定在；討論與全文任何情況都不出去。
  4. 只有 GET。沒有任何一支會寫東西。
  5. 不提供列表。拿不到 share id 就什麼都看不到。
  6. 圖那一支另外再確認一次「書裡的圖」有沒有被勾 —— 沒勾就當那個檔不存在。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from core.no_store import no_store_file
from services import knowledge_share as kshare

router = APIRouter(prefix="/api/v1/knowledge/public", tags=["knowledge-public"])
# 🔴 這三支的讀檔一律 `asyncio.to_thread`：`_find_book` 每個請求都掃整個書架（每本讀 meta）、
#    `public_view` 讀每一章；公開頁一張圖一個請求，80 張圖＝80 次整架掃描 —— 同步跑等於
#    在 8000 的事件迴圈上卡住所有 HTTP（2026-09-19 /polish BUG-9，同 BUG-7 那一類）。


@router.get("/{share_id}")
async def shared_book(share_id: str):
    """公開的那一本：`{title, author, info, tags, extend, shared_at}`。

    沒開分享、id 不合格式、或那本書不在 —— 一律 404 同一句話（不透露差別）。
    """
    data = await asyncio.to_thread(lambda: kshare.public_view(share_id))
    if data is None:
        raise HTTPException(status_code=404, detail="這個分享連結不存在或已經關閉")
    return data


@router.get("/{share_id}/pdf")
async def shared_pdf(share_id: str):
    """收到連結的人也可以把資料帶走（owner 2026-09-19：「讓大家可以下載資料」）。

    🔴 印的是 `public_view` 回的那一包 —— **他沒勾的東西不會出現在 PDF 裡**。
    圖只有勾了「書裡的圖」才會被內嵌（沒勾的話 `public_view` 連 gallery 鍵都沒有）。
    """
    data = await asyncio.to_thread(lambda: kshare.public_view(share_id))
    if data is None:
        raise HTTPException(status_code=404, detail="這個分享連結不存在或已經關閉")
    from services import knowledge_pdf as kpdf
    return await kpdf.pdf_response(await asyncio.to_thread(kshare.book_of, share_id), data)


@router.get("/{share_id}/assets/{name}")
async def shared_asset(share_id: str, name: str):
    """公開頁的圖。**只有勾了「書裡的圖」才給**（`knowledge_share.public_asset` 把關）。

    不走快取（同私有那支）—— 這是原書的內容，不要讓中間的代理留一份。
    """
    path = await asyncio.to_thread(kshare.public_asset, share_id, name)
    if not path:
        raise HTTPException(status_code=404, detail="找不到這張圖")
    return no_store_file(path, media_type=("image/png" if name.endswith(".png") else "image/jpeg"))
