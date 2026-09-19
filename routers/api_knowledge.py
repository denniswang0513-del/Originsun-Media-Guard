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
import os
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from core.auth import check_admin, check_admin_or_module, payload_grants
from core.bg_task import fire
from core.knowledge_logic import FOCUS_MAX, TAGS_MAX
from services import knowledge_service as ks
from services import knowledge_report as kr
from services import knowledge_podcast as kpod
from services import knowledge_share as kshare
from services import knowledge_watch as kw
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
    # 研究助理每週要不要管這本（§9.3）。全域還有一道 settings knowledge.watch.enabled，兩道都開才會跑。
    watch: Optional[bool] = None
    # 研究助理要往哪邊找（owner 自己寫的一兩句；正規化與截斷在 knowledge_logic.normalize_focus，
    # 這裡只擋離譜的長度 —— 超過 FOCUS_MAX 的不是 422，是靜默截掉）
    focus: Optional[str] = Field(default=None, max_length=FOCUS_MAX * 5)
    # 書籍基本資訊（§9.8）：白名單與長度在 knowledge_logic.normalize_info，這裡只擋離譜的整包大小
    info: Optional[dict] = None
    # 掛在哪一個案子（§9.11）：`{id, label}`，送 `{}` 就是取消掛案。形狀在 knowledge_logic.normalize_project
    project: Optional[dict] = None


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


class WatchPayload(BaseModel):
    enabled: Optional[bool] = None
    weekday: Optional[int] = Field(default=None, ge=0, le=6)     # 0＝週一…6＝週日
    hour: Optional[int] = Field(default=None, ge=0, le=23)


class SharePayload(BaseModel):
    on: bool
    # 要分享哪幾項（§9.9）。白名單在 knowledge_logic.normalize_parts；None＝沿用現在的設定
    parts: Optional[list[str]] = None


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
    except kr.ReportNotFound:
        raise HTTPException(status_code=404, detail="找不到這份報告")
    except ks.ExtendNotFound:
        raise HTTPException(status_code=404, detail="延伸裡沒有這一則（畫面可能舊了，重新整理看看）")
    except ks.BookBusy:
        raise HTTPException(status_code=409, detail="這本書正在處理中，等它跑完")
    except ks.BookHasNoFile:
        raise HTTPException(status_code=409, detail="這本書還沒有檔案，先補 PDF 再讀")
    except ks.BookHasFile:
        raise HTTPException(status_code=409, detail="這本書已經有檔了；要換檔請刪掉重上")


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
    _check_pdf_upload(content)
    try:
        meta = await asyncio.to_thread(ks.save_upload, content, file.filename or "", title)
    except Exception as e:                      # noqa: BLE001 — pymupdf 對壞檔丟的型別不一
        raise HTTPException(status_code=422, detail=f"讀不了這個 PDF：{str(e)[:200]}")
    return meta


class PendingPayload(BaseModel):
    title: str
    author: str = ""
    tags: Optional[list] = None


def _check_pdf_upload(content: bytes) -> None:
    """上傳與補檔共用的檔案檢查：大小上限、檔頭 `%PDF`（不看副檔名）。"""
    if len(content) > ks.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"檔案超過 {ks.MAX_UPLOAD_BYTES // (1024 * 1024)}MB")
    if not ks.is_pdf(content):
        raise HTTPException(status_code=422, detail="只收 PDF（看的是檔頭，不是副檔名）")


@router.post("/pending")
async def create_pending(body: PendingPayload, request: Request):
    """「待補」：先建書名（可帶作者／標籤），PDF 之後用 POST /{id}/file 補。回 summary（status=pending）。"""
    _guard(request)
    if not (body.title or "").strip():
        raise HTTPException(status_code=422, detail="書名不能空白")
    return await asyncio.to_thread(ks.create_pending, body.title, author=body.author, tags=body.tags)


@router.post("/{book_id}/file")
async def attach_file(book_id: str, request: Request, file: UploadFile = File(...)):
    """給「待補」的書補 PDF（檔頭／大小檢查同上傳）；已有檔 → 409。回 summary（status=uploaded）。"""
    _guard(request)
    content = await file.read()
    _check_pdf_upload(content)
    try:
        return await asyncio.to_thread(_book_or_404, ks.attach_file, book_id, content, file.filename or "")
    except HTTPException:
        raise
    except Exception as e:                      # noqa: BLE001 — pymupdf 對壞檔丟的型別不一
        raise HTTPException(status_code=422, detail=f"讀不了這個 PDF：{str(e)[:200]}")


# ── 研究助理的全域開關（§9.3）────────────────────────────────
# 🔴 這兩支要排在 `/{book_id}` **前面** —— FastAPI 照註冊順序比對，排在後面的話
#    「watch」會先被當成 book_id 吃掉（回「找不到這本書」）。
@router.get("/watch")
async def watch_get(request: Request):
    """`{enabled, weekday, hour, can_edit}`：每本書的開關在書頁，這是全域那一道。"""
    payload = _guard(request)
    conf = dict(kw.settings_watch())
    conf["can_edit"] = payload_grants(payload)      # 零把鑰匙＝只有管理員過得了（單一正本）
    return conf


@router.put("/watch")
async def watch_put(body: WatchPayload, request: Request):
    """改全域開關（管理員）。兩道開關都開，週排程才會自己跑。"""
    # 先過整組端點那把尺，再加一道管理員 —— `_guard` 看起來多餘（管理員本來就過得了），
    # 但 test_every_endpoint_calls_the_guard 掃的是「每一支都有 _guard」這個不變量，
    # 少了它就等於在這組裡開了一個例外，下一支忘了守的就不會被抓到。
    _guard(request)
    check_admin(request)
    return kw.save_watch(enabled=body.enabled, weekday=body.weekday, hour=body.hour)


# ── 研究週報／月報（§9.5）────────────────────────────────────
# owner 2026-09-18：「這些報表可以使用連結」—— Discord 推的那則帶 `#report/<id>`，
# 點進來由前端打這兩支把 md 撈回來畫。
# 🔴 同 `/watch`，這兩支要排在 `/{book_id}` **前面**。
@router.get("/reports")
async def reports_list(request: Request):
    """`[{id, title, kind, bytes}]`，新的排前面。"""
    _guard(request)
    return kr.list_reports()


@router.get("/reports/{report_id}")
async def report_get(report_id: str, request: Request):
    """`{id, title, md}`。id 不合規或檔案不在都是 404（同書的規矩）。"""
    _guard(request)
    return _book_or_404(kr.read_report, report_id)


# ── 公開分享（§9.9）────────────────────────────────────────
# 這兩支是**私有**的（開關由他自己按）；公開讀的那一支在 routers/api_knowledge_public.py。
@router.get("/{book_id}/share")
async def share_get(book_id: str, request: Request):
    """`{on, id, url, at}`。"""
    _guard(request)
    return await asyncio.to_thread(_book_or_404, kshare.share_of, book_id)


@router.put("/{book_id}/share")
async def share_put(book_id: str, body: SharePayload, request: Request):
    """開或關。關掉再開會換一組新的 id —— 舊連結立刻失效。"""
    _guard(request)
    return await asyncio.to_thread(_book_or_404, kshare.set_share, book_id, bool(body.on), body.parts)


@router.get("/{book_id}/pdf")
async def book_pdf(book_id: str, request: Request):
    """整本的研究筆記 PDF（owner 2026-09-19：「這裡多一個 pdf 下載，讓大家可以下載資料」）。

    他自己的那一份是整本 —— 東西都是他的，沒有勾選那回事。公開頁那份在
    `api_knowledge_public.py`，只印他勾的那幾項。
    """
    _guard(request)
    from services import knowledge_pdf as kpdf
    # 讀整本（每一章）丟執行緒，不在事件迴圈上（2026-09-19 /polish BUG-9）
    return await kpdf.pdf_response(book_id, await asyncio.to_thread(_book_or_404, kshare.full_view, book_id))


@router.get("/{book_id}/source")
async def book_source(book_id: str, request: Request):
    """原書的 PDF 原檔（owner 2026-09-19：「我的 pdf 希望放上書的 pdf」）。

    🔴 **只有這一支私有的端點給得出整本原書**。公開分享那一面沒有對應的東西，
    也不要加 —— 分享出去的是他的研究，不是他買的那本書（版權）。
    """
    _guard(request)
    from core.no_store import no_store_file
    path = _book_or_404(ks.source_path, book_id)
    return no_store_file(path, media_type="application/pdf",
                         filename=_book_or_404(ks.source_filename, book_id))


@router.get("/{book_id}")
async def get_book(book_id: str, request: Request):
    _guard(request)
    return await asyncio.to_thread(_book_or_404, ks.book_detail, book_id)


@router.put("/{book_id}")
async def patch_book(book_id: str, body: BookPatch, request: Request):
    """`{title?, author?, tags?}`：沒帶的欄位不動（Optional＋None，舊分頁的 PUT 不會洗掉別人剛填的）。"""
    _guard(request)
    return _book_or_404(ks.update_book, book_id, title=body.title, author=body.author,
                        tags=body.tags, watch=body.watch, focus=body.focus, info=body.info,
                        project=body.project)


@router.delete("/{book_id}")
async def delete_book(book_id: str, request: Request):
    _guard(request)
    kpod.cancel_all(book_id)          # 還掛著的 podcast 進度一起清掉，不然那本書的鍵永遠留著
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


# ── 每一章一集 podcast（§9.13；owner 2026-09-19：「每一章一個podcast」）──
# 產一集要十幾分鐘（兩趟 claude ＋ 200 句配音），所以是背景工作＋輪詢進度，同編譯。
@router.get("/{book_id}/podcast")
async def podcast_state(book_id: str, request: Request):
    """`{items: [{n, title, has, mb, minutes, stage}], done, total, making}`。"""
    _guard(request)
    return _book_or_404(kpod.podcast_state, book_id)


@router.post("/{book_id}/podcast")
async def podcast_make_all(book_id: str, request: Request, body: Optional[ModelPayload] = None):
    """整本：把還沒有的那幾章排進去（一次一集，claude 那邊本來就有閘）。"""
    _guard(request)
    _require_claude()
    state = _book_or_404(kpod.podcast_state, book_id)
    todo = [c["n"] for c in state["items"] if not c["has"] and not c["stage"]]
    model = (body.model if body else "") or ""
    for n in todo:
        _book_or_404(kpod.start_podcast, book_id, n)
        fire(kpod.run_podcast(book_id, n, model), label=f"knowledge podcast {book_id}/{n}")
    return {"status": "making", "queued": len(todo)}


@router.post("/{book_id}/podcast/{n}")
async def podcast_make_one(book_id: str, n: int, request: Request,
                           body: Optional[ModelPayload] = None):
    """單章：重產會直接蓋掉舊的那一集。"""
    _guard(request)
    _require_claude()
    _book_or_404(kpod.start_podcast, book_id, n)
    fire(kpod.run_podcast(book_id, n, (body.model if body else "") or ""),
         label=f"knowledge podcast {book_id}/{n}")
    return {"status": "making", "n": n}


@router.get("/{book_id}/podcast/{n}.mp3")
async def podcast_audio(book_id: str, n: int, request: Request):
    """那一集的音檔。🔴 書是私有的 → no_store（同章節的圖）。"""
    _guard(request)
    from core.no_store import no_store_file
    path = _book_or_404(kpod.podcast_path, book_id, n)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="這一章還沒有 podcast")
    return no_store_file(path, media_type="audio/mpeg")


@router.get("/{book_id}/podcast/{n}/script")
async def podcast_script(book_id: str, n: int, request: Request):
    """那一集的逐字稿（畫面上可以跟著讀）。"""
    _guard(request)
    return {"items": _book_or_404(kpod.read_script, book_id, n)}


# ── 章節裡的圖（§9.7；owner 2026-09-18：「如果章節有重要圖片 或表格 我希望你也可以截取出來」）──
# 表格不走這裡 —— 它是文字，已經在章節 md 裡面了。
@router.get("/{book_id}/assets")
async def assets_list(book_id: str, request: Request):
    """`[{name, page, caption}]`：這本書從 PDF 抽出來的圖。"""
    _guard(request)
    return _book_or_404(ks.list_assets, book_id)


@router.get("/{book_id}/assets/{name}")
async def asset_get(book_id: str, name: str, request: Request):
    """回那張圖的檔案。🔴 檔名過白名單才拼路徑（同章節檔名的規矩）。"""
    _guard(request)
    from core.no_store import no_store_file
    path = _book_or_404(ks.asset_path, book_id, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="找不到這張圖")
    # 🔴 書是私有的，圖也是：走 no_store，不要讓中間的代理或瀏覽器留一份在磁碟上
    return no_store_file(path, media_type=("image/png" if name.endswith(".png") else "image/jpeg"))


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
