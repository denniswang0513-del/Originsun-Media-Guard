"""routers/crm/proposal_quotes.py — 提案的「報價單」分頁後端。

owner 拍板的三件事：**CRM 報價為主清單**（唯讀顯示，正本在 crm_quotations）、
**外部檔案上傳**（多版本 —— 客戶回簽版、廠商比價版這類不是報價模組產的檔）、
**多份報價時 AI 幫忙分析**（services/quote_analyzer，照企劃書的 pending 模式）。

檔案落點：專案資產夾/{folder_subpath}/報價單/ —— 跟這筆提案的其他工作檔住在
一起。**刻意沒有 /uploads web root 退路**（deck 有）：報價單含金額，是內部
敏感資料，不進靜態直出；資產夾建不出來就 400 明講，不靜默落到別處。

🔴 這整個模組的任何資料都**不准**出現在公開 `?t=` token 端點 —— 同
budget_range 金額一律不出公開端點的既有慣例（見 api_proposals shared 白名單）。
所有端點都掛內部守衛，沒有任何一條進 public_router。
"""
from __future__ import annotations

import asyncio
import os
import uuid

from typing import List, Optional

from fastapi import File, Form, HTTPException, Request, UploadFile

from core.bg_status import settle
from core.bg_task import fire
from core.project_folders import safe_rel_path

# 守衛比照提案子分頁的既有先例（briefs.py 同款）：對齊 /proposal-plan.html
# 的頁面守衛 —— 打得開提案工作頁的人（含 preprod_plan），分頁就要能用，
# 否則「給看不給用」。不新增 RBAC key。
from routers.api_proposals import proposal_auth
from routers.api_proposals import _get_proposal_or_404

from .proposal_assets import (_ensure_project_folder, _resolve_asset_file,
                              land_in_home, project_folder_abs)

from ._shared import router, _get_factory, _now, _require_db

try:
    from sqlalchemy import func as safunc
    from ._shared import select
    from db.models import PreprodQuoteFile, PreprodQuoteAnalysis
    from .quotes import _load_items, _to_quotation_dict, project_quotation_rows
except ImportError:  # DB 套件不存在的 agent 環境
    pass

# 報價單上傳上限（契約定死 50MB → 413）
_MAX_QUOTE_BYTES = 50 * 1024 * 1024
# 落在提案家底下的固定子夾名（系統管的 —— 刪檔端點敢動磁碟就是因為這個夾
# 裡的檔全是本模組放進去的）
_QUOTE_SUBDIR = "報價單"


def _file_dict(f) -> dict:
    return {
        "id": f.id, "version": f.version, "rel": f.rel, "filename": f.filename,
        "note": f.note or "", "created_by": f.created_by or "",
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def _analysis_dict(a) -> Optional[dict]:
    if not a:
        return None
    # 卡太久的 pending 在讀取端改判 failed（伺服器重啟過 → task 沒了）
    status, error = settle(a.status or "ok", a.updated_at, a.error or "")
    return {
        "id": a.id, "status": status, "content": a.content or "",
        "error": error,
        # 分析當下納入了哪些檔/哪些版 —— model docstring 承諾的回溯，前端畫在
        # 結果標題列（檔案與報價之後都會繼續長，不標就說不清這份講的是什麼）
        "inputs": a.inputs or {},
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _crm_quote_dict(q) -> dict:
    """CRM 報價的**唯讀摘要**（主清單顯示用）—— 編輯走報價管理 Tab，這裡
    不重複整包 _to_quotation_dict。"""
    return {
        "id": q.id, "version": q.version, "status": q.status or "草稿",
        "total": q.total, "final_price": q.final_price,
        "quote_date": q.quote_date.isoformat() if q.quote_date else None,
        "valid_until": q.valid_until.isoformat() if q.valid_until else None,
    }


async def _file_row_or_404(session, pid: str, fid: str):
    f = await session.get(PreprodQuoteFile, fid)
    if not f or f.proposal_id != pid:       # 換提案打別人的 fid → 一律 404
        raise HTTPException(status_code=404, detail="找不到這一份報價檔")
    return f


@router.get("/proposals/{pid}/quotes")
async def list_proposal_quotes(pid: str, request: Request, only: str = ""):
    """報價分頁一次撈：上傳檔（version desc）+ CRM 報價摘要 + 最新分析。

    `?only=analysis` 給分析輪詢用 —— 每 5 秒只為了讀一個 status 就跑
    files/crm 兩條 query 是純浪費（1-3 分鐘的分析 = 幾十趟）。"""
    proposal_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        analysis = (await session.execute(
            select(PreprodQuoteAnalysis)
            .where(PreprodQuoteAnalysis.proposal_id == pid)
            .order_by(PreprodQuoteAnalysis.created_at.desc())
            .limit(1))).scalars().first()
        if only == "analysis":
            # 輪詢路徑連提案那趟 SELECT 都省（不存在的 pid 回 analysis:null
            # 而非 404 —— 輪詢端拿的本來就是有效 pid，無差別）
            return {"analysis": _analysis_dict(analysis)}
        prop = await _get_proposal_or_404(session, pid)
        files = (await session.execute(
            select(PreprodQuoteFile).where(PreprodQuoteFile.proposal_id == pid)
            .order_by(PreprodQuoteFile.version.desc()))).scalars().all()
        crm_rows = await project_quotation_rows(session, prop.project_id or "")
    return {"files": [_file_dict(f) for f in files],
            "crm_quotations": [_crm_quote_dict(q) for q in crm_rows],
            "analysis": _analysis_dict(analysis)}


@router.post("/proposals/{pid}/quotes/upload")
async def upload_proposal_quote(pid: str, request: Request,
                                file: UploadFile = File(...),
                                note: str = Form(default="")):
    """上傳一版外部報價檔 → 落在 專案資產夾/{提案的家}/報價單/，版號 max+1。

    **沒有退路**：沒連結專案、或資產夾建不出來 → 400 明講。報價單含金額，
    不做 /uploads web root 靜態直出的備援（deck 那條的退路這裡刻意不抄）。
    """
    payload = proposal_auth(request)
    _require_db()
    # 副檔名黑名單交給 save_uploads（政策單一正本），skipped reason 轉 422；
    # 只有大小例外前置 —— 契約定死 413，save_uploads 只能回 422
    if (getattr(file, "size", None) or 0) > _MAX_QUOTE_BYTES:
        raise HTTPException(status_code=413, detail="報價檔超過 50MB 上限")
    who = str((payload or {}).get("username") or "")

    factory = await _get_factory()
    async with factory() as session:
        prop = await _get_proposal_or_404(session, pid)
        if not prop.project_id:
            raise HTTPException(
                status_code=400,
                detail="這個提案還沒連結專案 —— 報價檔要存進專案資產夾，"
                       "請先在提案詳情連結專案再上傳")
        # 「確保資產夾存在，否則 4xx」走既有正本（殼專案不見 404、建不出來 400）
        folder_abs = await _ensure_project_folder(session, prop.project_id)
        # 落地共用件（清洗檔名、撞名補 -2、黑名單、存不成 422）
        dest, rel = await land_in_home(folder_abs, prop, file,
                                       subdir=_QUOTE_SUBDIR,
                                       max_bytes=_MAX_QUOTE_BYTES)

        max_ver = (await session.execute(
            select(safunc.max(PreprodQuoteFile.version))
            .where(PreprodQuoteFile.proposal_id == pid))).scalar()
        row = PreprodQuoteFile(
            id=uuid.uuid4().hex, proposal_id=pid, version=(max_ver or 0) + 1,
            rel=rel, filename=os.path.basename(dest),
            note=(note or "").strip() or None,
            created_by=who, created_at=_now())
        session.add(row)
        # ensure_folder 可能這一刻才生成 proposal_folder_name —— 同一個 commit 落地
        await session.commit()
        return {"status": "ok", "file": _file_dict(row)}


@router.patch("/proposals/{pid}/quotes/{fid}")
async def update_proposal_quote(pid: str, fid: str, request: Request, body: dict):
    """改備註（json {note}）。檔案本身不改 —— 要換檔就傳新的一版。"""
    proposal_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        f = await _file_row_or_404(session, pid, fid)
        f.note = str(body.get("note") or "").strip() or None
        await session.commit()
    return {"status": "ok"}


@router.delete("/proposals/{pid}/quotes/{fid}")
async def delete_proposal_quote(pid: str, fid: str, request: Request):
    """刪 DB 列 + best-effort 刪磁碟檔。敢動磁碟是因為「報價單」子夾是系統
    管的落點（檔全是 upload 端點放的）；刪不到（NAS 斷線/已被搬走）不擋
    DB 刪除 —— 列不見了才是使用者要的結果。"""
    proposal_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        f = await _file_row_or_404(session, pid, fid)
        prop = await _get_proposal_or_404(session, pid)
        try:
            # _resolve_asset_file 是「檔案怎麼離開共用磁碟」的單一判斷；
            # 它 raise 的 HTTPException（檔不在了）正是這裡要吞的 best-effort
            path = await _resolve_asset_file(session, prop.project_id or "", f.rel)
            await asyncio.to_thread(os.remove, path)
        except (OSError, HTTPException):
            pass                        # best-effort：磁碟清不掉不留殭屍 DB 列
        await session.delete(f)
        await session.commit()
    return {"status": "ok"}


@router.get("/proposals/{pid}/quotes/{fid}/download")
async def download_proposal_quote(pid: str, fid: str, request: Request):
    """帶權限出檔 —— 報價檔在 NAS 資產夾、不在 web root（比照 deck/download）。"""
    proposal_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        f = await _file_row_or_404(session, pid, fid)
        prop = await _get_proposal_or_404(session, pid)
        if not prop.project_id:
            raise HTTPException(status_code=404, detail="找不到檔案（提案未連結專案）")
        path = await _resolve_asset_file(session, prop.project_id, f.rel)
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=f.filename)


@router.post("/proposals/{pid}/quotes/analyze")
async def analyze_proposal_quotes(pid: str, request: Request):
    """AI 分析多份報價（跑 claude，背景跑立刻回；前端輪詢 GET 的 analysis）。

    輸入在**這一刻**收齊（上傳檔路徑 + CRM 各版結構化資料 + 提案背景）並存
    inputs 快照 —— 分析跑到一半有人再傳一版，這一筆講的仍是按下去當時的狀態。
    一檔抽不出文字不整批死（quote_analyzer 標註後繼續）。"""
    payload = proposal_auth(request)
    _require_db()
    who = str((payload or {}).get("username") or "")
    factory = await _get_factory()
    async with factory() as session:
        prop = await _get_proposal_or_404(session, pid)
        file_rows = (await session.execute(
            select(PreprodQuoteFile).where(PreprodQuoteFile.proposal_id == pid)
            .order_by(PreprodQuoteFile.version.desc()))).scalars().all()
        crm_rows = await project_quotation_rows(session, prop.project_id or "")
        if not file_rows and not crm_rows:
            raise HTTPException(
                status_code=422,
                detail="沒有任何資料可分析 —— 請先上傳報價檔，或在 CRM 報價"
                       "模組建立這個專案的報價")

        # 家在哪（純 DB）先問好；UNC 的路徑解析**出了 session 再做** ——
        # NAS 慢/斷線時別抱著 pool 連線（pool_size=5）卡在磁碟上
        folder_abs = ""
        if prop.project_id:
            try:
                folder_abs = await project_folder_abs(session, prop.project_id)
            except HTTPException:
                folder_abs = ""         # 殼專案不見了 → 檔案全標「搆不到」

        # CRM 各版含項目明細 —— 序列化走報價模組的正本（_to_quotation_dict /
        # _load_items），報價之後加欄位分析才不會靜默看不到；prompt 端只讀
        # 白名單 key，多出來的 internal_cost 不會進 prompt
        quotations = [
            _to_quotation_dict(q, items=await _load_items(session, q.id))
            for q in crm_rows]

        a = PreprodQuoteAnalysis(
            id=uuid.uuid4().hex, proposal_id=pid, status="pending",
            inputs={"files": [{"id": f.id, "version": f.version,
                               "filename": f.filename} for f in file_rows],
                    "crm_quotations": [{"id": q.id, "version": q.version,
                                        "status": q.status or "草稿"}
                                       for q in crm_rows]},
            created_by=who, created_at=_now(), updated_at=_now())
        session.add(a)
        await session.commit()
        prop_info = {"title": prop.title or "", "status": prop.status or "",
                     "budget_range": prop.budget_range or ""}
        aid = a.id
        file_meta = [(f.rel, f.version, f.filename, f.note or "")
                     for f in file_rows]

    # 一趟 to_thread 解析整批（safe_rel_path 內含 UNC stat，逐檔 hop 是 n 次
    # 序列網路往返）；解析不到 → path=""，分析裡如實標註
    paths = (await asyncio.to_thread(
        lambda: [safe_rel_path(folder_abs, rel) for rel, *_ in file_meta])
        if folder_abs else [None] * len(file_meta))
    files = [{"version": ver, "filename": name, "note": note, "path": p or ""}
             for (_rel, ver, name, note), p in zip(file_meta, paths)]

    from services.quote_analyzer import analyze_quotes
    # core.bg_task.fire：強參考 + 例外守衛（例外沒接住這筆會永遠 pending）
    fire(analyze_quotes(aid, prop_info=prop_info, files=files,
                        quotations=quotations),
         label=f"quote-analysis {aid}")
    return {"status": "ok", "analysis": {"id": aid, "status": "pending"}}
