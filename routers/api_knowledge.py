"""routers/api_knowledge.py — 「知識庫」（獨立應用 /knowledge.html；docs/KNOWLEDGE_BASE_PLAN.md §3、§11）：書架＋討論＋結論。

一本書＝`books/<id>/` 一個資料夾（檔案，不進 DB、不進 git），I/O 在 services/knowledge_service.py，
純規則在 core/knowledge_logic.py。編譯（讀完整本、產骨架＋每章筆記）與討論都要 claude CLI ——
🔴 **只掛 master**（main.py），不掛 main_office.py：D:\\ 只有 master 看得到、claude 也只在那台。
手機在 NAS 那條路打到 404 → 畫面寫「書架需要主控主機在線」。

守衛：每支 `_guard(request)`＝`check_admin_or_module(request, "knowledge")`（管理員 OR 有「知識庫」鑰匙；
2026-09-18 owner「不要綁在私帳裡」，從 finance_mine 拆出來自己一把）。沒有排程、不 import notifier。
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from core.auth import check_admin_or_module
from core.bg_task import fire
from core.knowledge_logic import TAGS_MAX
from services import knowledge_service as ks
from services.knowledge_claude import claude_available

router = APIRouter(prefix="/api/v1/knowledge", tags=["知識庫"])
#: 這功能自己一把鑰匙（core.auth.ALL_MODULES 的 'knowledge'；非 tab，管 /knowledge.html 的入口與這整組端點）
MODULE_KEY = "knowledge"
NEED_MASTER = "需要主控主機在線（編譯與討論跑在那台的 claude CLI）"


class BookPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    author: Optional[str] = Field(default=None, max_length=200)
    # 標籤：自由字串；正規化（去空白／去重／每個 ≤ 20 字／最多 TAGS_MAX 個）在 knowledge_logic.normalize_tags，
    # 這裡只擋離譜的（一次送超過 50 個）—— 超過正規化上限的不是 422，是靜默截掉。
    tags: Optional[list[str]] = Field(default=None, max_length=TAGS_MAX * 5)


class CompilePayload(BaseModel):
    model: Optional[str] = None
    force: bool = False          # True＝丟掉已產的章節與骨架整本重編；預設只補缺的


class ChatPayload(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    model: Optional[str] = None


class ModelPayload(BaseModel):
    model: Optional[str] = None


class TextPayload(BaseModel):
    text: str = Field(default="", max_length=200000)


class ExtendPayload(BaseModel):
    # 有給網址＝手動「收錄這篇」那一篇；沒給＝讓它自己依這本書的主題去搜
    url: str = Field(default="", max_length=2000)
    model: Optional[str] = None


class RatePayload(BaseModel):
    # 空字串＝收回評分
    rating: str = Field(default="", max_length=10)


def _guard(request: Request) -> dict:
    """管理員 OR 持有「知識庫」鑰匙（回 token payload）。整組端點一把尺，不分讀寫。"""
    return check_admin_or_module(request, MODULE_KEY)


def _book_or_404(fn, *args, **kwargs):
    """service 的 BookNotFound → 404（id 不合法與資料夾不存在都是它）；BookBusy → 409。"""
    try:
        return fn(*args, **kwargs)
    except ks.BookNotFound:
        raise HTTPException(status_code=404, detail="找不到這本書")
    except ks.ChapterNotFound:
        raise HTTPException(status_code=404, detail="這本書沒有這一章（還沒編到，或編譯失敗了）")
    except ks.ExtendNotFound:
        raise HTTPException(status_code=404, detail="延伸裡沒有這一則（畫面可能舊了，重新整理看看）")
    except ks.BookBusy:
        raise HTTPException(status_code=409, detail="這本書正在處理中，等它跑完")


def _require_claude() -> None:
    """🔴 先確認這台跑得動再收下：NAS 容器沒有 claude CLI。不擋的話使用者要等背景任務跑完，
    才在泡泡裡看到「找不到 claude CLI」—— 快點講實話比較好（同報價助理）。"""
    if not claude_available():
        raise HTTPException(status_code=503, detail=NEED_MASTER)


# ── 書架 ─────────────────────────────────────────────────────
@router.get("")
async def list_books(request: Request, tag: str = ""):
    """書架；`?tag=財務` 只列 meta.tags 含它的書。"""
    _guard(request)
    return await asyncio.to_thread(ks.list_books, tag)


@router.post("")
async def upload_book(request: Request, file: UploadFile = File(...), title: str = Form("")):
    """上傳 PDF：看檔頭 `%PDF`（不看副檔名）、上限 300MB、抽文字＋頁數 → 建資料夾＋meta；回 meta。"""
    _guard(request)
    content = await file.read()
    if len(content) > ks.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"檔案超過 {ks.MAX_UPLOAD_BYTES // (1024 * 1024)}MB")
    if not ks.is_pdf(content):
        raise HTTPException(status_code=422, detail="只收 PDF（看的是檔頭，不是副檔名）")
    try:
        meta = await asyncio.to_thread(ks.save_upload, content, file.filename or "", title)
    except Exception as e:                      # noqa: BLE001 — pymupdf 對壞檔丟的型別不一
        raise HTTPException(status_code=422, detail=f"讀不了這個 PDF：{str(e)[:200]}")
    return meta


@router.get("/{book_id}")
async def get_book(book_id: str, request: Request):
    _guard(request)
    return await asyncio.to_thread(_book_or_404, ks.book_detail, book_id)


@router.put("/{book_id}")
async def patch_book(book_id: str, body: BookPatch, request: Request):
    """`{title?, author?, tags?}`：沒帶的欄位不動（Optional＋None，舊分頁的 PUT 不會洗掉別人剛填的）。"""
    _guard(request)
    return _book_or_404(ks.update_book, book_id, title=body.title, author=body.author, tags=body.tags)


@router.delete("/{book_id}")
async def delete_book(book_id: str, request: Request):
    _guard(request)
    _book_or_404(ks.delete_book, book_id)
    return {"status": "ok"}


# ── 編譯 ─────────────────────────────────────────────────────
@router.post("/{book_id}/compile")
async def compile_book(book_id: str, request: Request, body: Optional[CompilePayload] = None):
    """背景編譯：結構 → 每章 → 骨架。503 沒 claude CLI；409 已在跑。"""
    _guard(request)
    _require_claude()
    body = body or CompilePayload()
    meta = _book_or_404(ks.start_compile, book_id, body.model or "", force=body.force)
    fire(ks.compile_book(book_id, meta.get("model") or ""), label=f"knowledge compile {book_id}")
    return {"status": "compiling", "model": meta.get("model") or ""}


@router.get("/{book_id}/chapters/{n}")
async def get_chapter(book_id: str, n: int, request: Request):
    _guard(request)
    return _book_or_404(ks.read_chapter, book_id, n)


# ── 討論 ─────────────────────────────────────────────────────
@router.post("/{book_id}/chat")
async def chat_send(book_id: str, body: ChatPayload, request: Request):
    """追加 user 那則 ＋ 背景叫 claude；前端輪詢 GET .../chat 看回覆。503 沒 CLI；409 上一輪還沒回。"""
    _guard(request)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="請先輸入內容")
    _require_claude()
    msg = _book_or_404(ks.start_chat, book_id, text)
    fire(ks.run_chat(book_id, body.model or ""), label=f"knowledge chat {book_id}")
    return {"status": "asking", "message": msg}


@router.get("/{book_id}/chat")
async def chat_get(book_id: str, request: Request):
    """`{chat, partial, stage, concluding}`（前端 1 秒輪詢；同報價助理）。"""
    _guard(request)
    return _book_or_404(ks.chat_state, book_id)


# ── 結論／筆記 ────────────────────────────────────────────────
@router.post("/{book_id}/conclusions")
async def add_conclusion(book_id: str, body: TextPayload, request: Request):
    """追加一段到 `結論.md`（`## YYYY-MM-DD HH:MM` 一段）。"""
    _guard(request)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="請先輸入內容")
    return {"status": "ok", "conclusion": _book_or_404(ks.append_conclusion, book_id, text)}


@router.post("/{book_id}/conclude")
async def conclude(book_id: str, request: Request, body: Optional[ModelPayload] = None):
    """背景叫 claude 把整段討論收成 3–7 條 → 追加 `結論.md`；前端輪詢 GET /{id} 看 conclusion 變了。"""
    _guard(request)
    _require_claude()
    try:
        _book_or_404(ks.start_conclude, book_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    fire(ks.run_conclude(book_id, (body.model if body else "") or ""), label=f"knowledge conclude {book_id}")
    return {"status": "asking"}


@router.put("/{book_id}/conclusion")
async def put_conclusion(book_id: str, body: TextPayload, request: Request):
    _guard(request)
    _book_or_404(ks.write_conclusion, book_id, body.text)
    return {"status": "ok"}


@router.put("/{book_id}/notes")
async def put_notes(book_id: str, body: TextPayload, request: Request):
    _guard(request)
    _book_or_404(ks.write_notes, book_id, body.text)
    return {"status": "ok"}


# ── 延伸（研究助理）─────────────────────────────────────────
# 🔴 這三支背後是**唯一**會讀網頁的一發。網頁是不可信輸入：service 只把回來的東西
#    當資料寫進 `延伸.md`，不執行、不寫設定、不觸發任何動作（見 knowledge_service._EXTEND_TOOLS）。
@router.get("/{book_id}/extend")
async def extend_get(book_id: str, request: Request):
    """`{items, stage}`（「延伸」分頁；在找資料時前端輪詢這支）。"""
    _guard(request)
    return _book_or_404(ks.extend_state, book_id)


@router.post("/{book_id}/extend")
async def extend_run(book_id: str, request: Request, body: Optional[ExtendPayload] = None):
    """背景去網路上找 —— 給 `url` 就只收那一篇，不給就依這本書的主題自己搜。409 上一輪還沒跑完。"""
    _guard(request)
    _require_claude()
    url = ((body.url if body else "") or "").strip()
    try:
        _book_or_404(ks.start_extend, book_id, url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    fire(ks.run_extend(book_id, (body.model if body else "") or "", url),
         label=f"knowledge extend {book_id}")
    return {"status": "searching"}


@router.put("/{book_id}/extend/{n}")
async def extend_rate(book_id: str, n: int, body: RatePayload, request: Request):
    """給第 n 則評「有用／沒用」；n 是檔內流水號（刪過的號碼不重用，所以不會對錯人）。
    評成沒用的下次討論就不再帶進提示。"""
    _guard(request)
    try:
        return {"status": "ok", "item": _book_or_404(ks.rate_extend, book_id, n, (body.rating or "").strip())}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
