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
  3. **只回一本書**，而且只回這幾樣：書名／作者／基本資訊／標籤／延伸（研究助理找到的網路資料）。
     不回筆記、結論、討論、章節、骨架、全文、圖 —— 那些是他自己的東西或原書的內容。
  4. 只有 GET。沒有任何一支會寫東西。
  5. 不提供列表。拿不到 share id 就什麼都看不到。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services import knowledge_share as kshare

router = APIRouter(prefix="/api/v1/knowledge/public", tags=["knowledge-public"])


@router.get("/{share_id}")
async def shared_book(share_id: str):
    """公開的那一本：`{title, author, info, tags, extend, shared_at}`。

    沒開分享、id 不合格式、或那本書不在 —— 一律 404 同一句話（不透露差別）。
    """
    data = kshare.public_view(share_id)
    if data is None:
        raise HTTPException(status_code=404, detail="這個分享連結不存在或已經關閉")
    return data
