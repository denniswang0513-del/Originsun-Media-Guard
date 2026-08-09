"""routers/crm/brief_templates.py — 企劃**範本庫**（`{提案根目錄}/_範本/`）。

⚠️ 別跟前端的 `tabs/proposals/brief-templates.js` 搞混：那支是**方法論模板**
（三視角 × 四提問的矩陣定義）。這裡的 template 是「以前寫得好的企劃書」，
拿來給 Claude 參考文風與章節結構 —— 兩者都叫 template，一個是矩陣的骨、
一個是成品的樣板。

生成企劃書時給 Claude 參考的「好範本」。兩個入口：直接上傳、或在提案的檔案列
上按「設為範本」（走 folder-view 的 rowActions 擴充點）。

🔴 `_範本` **不是提案資產夾**，所以它必須從「資產資料夾總覽」排除掉 —— 那份
總覽列的是 root 底下每個資料夾，不排除的話 `_範本` 會變成一個可以被「連結專案」
「改名」的夾，連結下去就壞了（rename_and_remap 會把它當專案夾改名）。排除點在
proposal_assets.proposal_assets_overview 與 _folder_or_404，兩處都要，因為那些
folder/* 端點吃的是使用者傳來的資料夾名。

範本的**消化**（抽文字 → 骨架）在 services/plan_template_digest.py，這裡只管
檔案與索引。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from typing import List

from fastapi import File, HTTPException, Request, UploadFile  # type: ignore

from core.auth import check_admin_or_module
from core.project_folders import clean_filename, dedupe, safe_rel_path, save_uploads

from services.brief_template_digest import parse_questions as _parse_questions

from ._shared import router, _get_factory, _now, _require_db
from .proposal_assets import (_project_folder_abs, invalidate_folder_cache,
                              proposals_root)

try:
    from ._shared import select
    from db.models import PreprodBriefTemplate
except ImportError:  # DB 套件不存在的 agent 環境
    pass

# 範本夾名。底線開頭 = 「這不是提案夾」的視覺約定，同時讓它在排序時沉底。
TEMPLATE_DIR = "_範本"

# 能抽得出文字的格式。.key（Keynote）沒有可靠的 Python 解法 —— 擋在門口比
# 讓它進來然後永遠消化失敗好。
ALLOWED_EXTS = (".pdf", ".pptx", ".docx", ".md", ".txt")
MAX_BYTES = 60 * 1024 * 1024


def _auth(request: Request):
    """與提案庫同一道閘 —— owner 2026-08-10：有提案庫權限的人都能上傳範本。"""
    return check_admin_or_module(request, "preprod_proposals", "preprod_plan",
                                 "crm_projects")


async def _templates_dir(create: bool = False) -> str:
    """`{root}/_範本` 的絕對路徑。root 沒設 / 搆不到 → ""（呼叫端決定怎麼回）。"""
    root = await proposals_root()
    if not root:
        return ""
    path = os.path.join(root, TEMPLATE_DIR)
    if create:
        try:
            await asyncio.to_thread(os.makedirs, path, exist_ok=True)
            invalidate_folder_cache()
        except OSError:
            return ""
    return path


def _dict(t) -> dict:
    return {
        "id": t.id, "name": t.name, "filename": t.filename,
        "status": t.status or "pending", "error": t.error or "",
        "skeleton": t.skeleton or "",
        # 從骨架推導，不另存欄位 —— 人改骨架時問題清單跟著變
        "questions": _parse_questions(t.skeleton or ""),
        "has_text": bool(t.extracted_text),
        "source_project_id": t.source_project_id or "",
        "created_by": t.created_by or "",
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _check_ext(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=422,
            detail=f"只收 {'/'.join(ALLOWED_EXTS)}（Keynote 請先另存 PDF 或 PPTX）")
    return ext


# ── 清單 / 單筆 ─────────────────────────────────────────────

@router.get("/brief-templates")
async def list_brief_templates(request: Request):
    """範本清單。生成企劃書的挑選器與範本庫畫面共用這一支。"""
    _auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(PreprodBriefTemplate)
            .order_by(PreprodBriefTemplate.created_at.desc()))).scalars().all()
    return {"templates": [_dict(t) for t in rows], "dir": TEMPLATE_DIR}


@router.post("/brief-templates/upload")
async def upload_brief_template(request: Request, files: List[UploadFile] = File(...)):
    """直接上傳範本（可多檔）。落地後狀態是 pending —— 消化另外觸發，
    因為那要跑 claude，可能要好幾分鐘，不該擋住上傳這個動作。"""
    payload = _auth(request)
    _require_db()
    d = await _templates_dir(create=True)
    if not d:
        raise HTTPException(status_code=400, detail="範本資料夾無法建立（提案根目錄未設定或搆不到）")
    for f in files:
        _check_ext(f.filename or "")
    saved, skipped = await save_uploads(d, files, max_bytes=MAX_BYTES)

    factory = await _get_factory()
    made = []
    async with factory() as session:
        for name in saved:
            t = PreprodBriefTemplate(
                id=uuid.uuid4().hex, name=os.path.splitext(name)[0], filename=name,
                status="pending", created_by=payload.get("username") or "",
                created_at=_now(), updated_at=_now())
            session.add(t)
            made.append(t)
        await session.commit()
        for t in made:
            await session.refresh(t)
    return {"status": "ok", "templates": [_dict(t) for t in made], "skipped": skipped}


@router.post("/brief-templates/from-asset")
async def brief_template_from_asset(request: Request, body: dict):
    """把某個專案資產夾裡的檔案**複製**成範本（body {project_id, rel, name?}）。

    複製而不是記指標：提案會繼續改版，範本應該是凍結的那一份；而且原檔被刪或
    改名時範本不該跟著斷。撞名由 dedupe 補 -2。
    """
    payload = _auth(request)
    _require_db()
    project_id = (body.get("project_id") or "").strip()
    rel = (body.get("rel") or "").strip()
    if not project_id or not rel:
        raise HTTPException(status_code=422, detail="project_id 與 rel 必填")

    factory = await _get_factory()
    async with factory() as session:
        base = await _project_folder_abs(session, project_id)
    src = await asyncio.to_thread(safe_rel_path, base, rel) if base else None
    if not src:
        raise HTTPException(status_code=404, detail="找不到這個檔案")
    filename = clean_filename(os.path.basename(rel))
    _check_ext(filename)

    d = await _templates_dir(create=True)
    if not d:
        raise HTTPException(status_code=400, detail="範本資料夾無法建立（提案根目錄未設定或搆不到）")
    taken = set(await asyncio.to_thread(_listdir_safe, d))
    filename = dedupe(filename, taken)
    try:
        await asyncio.to_thread(shutil.copy2, src, os.path.join(d, filename))
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"複製失敗：{e}")

    async with factory() as session:
        t = PreprodBriefTemplate(
            id=uuid.uuid4().hex,
            name=(body.get("name") or os.path.splitext(filename)[0]).strip(),
            filename=filename, source_project_id=project_id, source_rel=rel,
            status="pending", created_by=payload.get("username") or "",
            created_at=_now(), updated_at=_now())
        session.add(t)
        await session.commit()
        await session.refresh(t)
    return {"status": "ok", "template": _dict(t)}


@router.patch("/brief-templates/{tid}")
async def update_brief_template(tid: str, request: Request, body: dict):
    """改顯示名或**直接改骨架**。骨架是給人改的 —— 生成出來不對味時改這段，
    比回頭調 prompt 直觀得多。"""
    _auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        t = await session.get(PreprodBriefTemplate, tid)
        if not t:
            raise HTTPException(status_code=404, detail="找不到這個範本")
        if "name" in body:
            name = (body.get("name") or "").strip()
            if not name:
                raise HTTPException(status_code=422, detail="名稱不可為空")
            t.name = name
        if "skeleton" in body:
            t.skeleton = (body.get("skeleton") or "").strip() or None
            # 人手改過就算完成 —— 不然畫面會一直掛著「消化失敗」
            if t.skeleton:
                t.status, t.error = "ok", None
        t.updated_at = _now()
        await session.commit()
        await session.refresh(t)
        return {"status": "ok", "template": _dict(t)}


@router.post("/brief-templates/{tid}/digest")
async def digest_brief_template(tid: str, request: Request):
    """把這份範本濃縮成骨架（跑 claude，可能要好幾分鐘）。

    **背景跑、立刻回**：claude --print 一次數十秒到三分鐘，同步等會讓請求逾時，
    而且使用者盯著轉圈也沒有比較快。前端靠清單裡的 status 輪詢。

    冪等但會**覆蓋**現有骨架（含人手改過的）—— 前端負責問一次。
    """
    _auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        t = await session.get(PreprodBriefTemplate, tid)
        if not t:
            raise HTTPException(status_code=404, detail="找不到這個範本")
        t.status, t.error = "pending", None
        t.updated_at = _now()
        await session.commit()

    from services.brief_template_digest import digest_template
    # create_task 不 await —— 這支端點的責任只到「已經開始跑」為止
    asyncio.create_task(digest_template(tid))
    return {"status": "started"}


@router.delete("/brief-templates/{tid}")
async def delete_brief_template(tid: str, request: Request):
    """刪範本：DB 那列 + `_範本` 裡那份檔案。原提案的檔案不動。"""
    _auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        t = await session.get(PreprodBriefTemplate, tid)
        if not t:
            raise HTTPException(status_code=404, detail="找不到這個範本")
        filename = t.filename
        await session.delete(t)
        await session.commit()
    d = await _templates_dir()
    if d and filename:
        # safe_rel_path 再擋一次 —— filename 進 DB 前雖然清過，但刪檔這條路
        # 值得付第二次檢查
        p = await asyncio.to_thread(safe_rel_path, d, filename)
        if p:
            try:
                await asyncio.to_thread(os.remove, p)
            except OSError:
                pass                       # 檔案先被手動刪掉了 —— 索引清掉就算成功
    return {"status": "ok"}


def _listdir_safe(path: str) -> list:
    try:
        return os.listdir(path)
    except OSError:
        return []
