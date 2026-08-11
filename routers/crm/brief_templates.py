"""routers/crm/brief_templates.py — 企劃**範本庫**（`{提案根目錄}/_範本/`）。

⚠️ 別跟 `tabs/proposals/plan-templates.js` 搞混：那支是**方法論模板**（三視角 ×
四提問的矩陣定義）。這裡的 template 是「以前寫得好的企劃書」，拿來給 Claude
參考文風與章節結構 —— 兩者都叫 template，一個是矩陣的骨、一個是成品的樣板。
這個功能的前端在 `tabs/proposals/brief-templates.js`。

兩個入口：直接上傳、或在提案的檔案列上按「設為範本」（走 folder-view 的
rowActions 擴充點）。

🔴 `_範本` **不是提案資產夾** —— 靠的是底線開頭：`proposal_assets` 的
`_scan_root_folders` 與 `_folder_or_404` 都用「`.`/`_` 開頭就不是」這條述詞，
所以這裡不必登記什麼，改名字時保持底線開頭就好。

範本的**消化**（抽文字 → 骨架）在 services/brief_template_digest.py，這裡只管
檔案與索引。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from typing import List

from fastapi import File, HTTPException, Request, UploadFile  # type: ignore

from core.bg_status import settle
from core.bg_task import fire
from core.doc_text import SUPPORTED_EXTS as ALLOWED_EXTS
from core.project_folders import (clean_filename, dedupe, safe_rel_path,
                                  save_uploads)
# 提案庫的三模組閘門只有一份（正本與 owner 的決策註解都在 api_proposals）
from routers.api_proposals import proposal_auth

from services.brief_template_digest import parse_questions as _parse_questions

from ._shared import router, _get_factory, _now, _require_db
from .proposal_assets import (_resolve_asset_file, invalidate_folder_cache,
                              proposals_root)

try:
    from ._shared import select
    from db.models import PreprodBriefTemplate
except ImportError:  # DB 套件不存在的 agent 環境
    pass

# 範本夾名。🔴 底線開頭是**功能性的**不是裝飾：`proposal_assets` 用
# 「`.`/`_` 開頭＝不是提案資產夾」這條述詞把它擋在總覽與 folder/* 端點外。
TEMPLATE_DIR = "_範本"

# 收哪些格式（ALLOWED_EXTS）正本在 core.doc_text —— 抽得出文字的那些。
# .key 沒有可靠的 Python 解法，擋在門口比讓它進來然後永遠消化失敗好。
MAX_BYTES = 60 * 1024 * 1024


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
    # 卡太久的 pending 在讀取端改判 failed（伺服器重啟過 → task 沒了）
    status, error = settle(t.status or "pending", t.updated_at, t.error or "")
    return {
        "id": t.id, "name": t.name, "filename": t.filename,
        "status": status, "error": error,
        "skeleton": t.skeleton or "",
        # 從骨架推導，不另存欄位 —— 人改骨架時問題清單跟著變
        "questions": _parse_questions(t.skeleton or ""),
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
    proposal_auth(request)
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
    payload = proposal_auth(request)
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
        # 不 refresh：expire_on_commit=False，屬性 commit 後仍有效 —— refresh 是
        # 每列一次額外 SELECT（api_proposals:584 同結論）
        await session.commit()
    return {"status": "ok", "templates": [_dict(t) for t in made], "skipped": skipped}


@router.post("/brief-templates/from-asset")
async def brief_template_from_asset(request: Request, body: dict):
    """把某個專案資產夾裡的檔案**複製**成範本（body {project_id, rel, name?}）。

    複製而不是記指標：提案會繼續改版，範本應該是凍結的那一份；而且原檔被刪或
    改名時範本不該跟著斷。撞名由 dedupe 補 -2。
    """
    payload = proposal_auth(request)
    _require_db()
    project_id = (body.get("project_id") or "").strip()
    rel = (body.get("rel") or "").strip()
    if not project_id or not rel:
        raise HTTPException(status_code=422, detail="project_id 與 rel 必填")

    factory = await _get_factory()
    async with factory() as session:
        # 「檔案怎麼離開共用磁碟」的判斷只有一份（含 404 vs 500 的決定）
        src = await _resolve_asset_file(session, project_id, rel)
    filename = clean_filename(os.path.basename(rel))
    _check_ext(filename)

    d = await _templates_dir(create=True)
    if not d:
        raise HTTPException(status_code=400, detail="範本資料夾無法建立（提案根目錄未設定或搆不到）")
    # keep_ext=True：補號插在副檔名前面（稿.pdf → 稿-2.pdf）。少了它會產出
    # `稿.pdf-2` —— 副檔名檢查早就過了，落地的卻是消化不了的無副檔名檔案。
    filename = dedupe(filename, set(await asyncio.to_thread(_listdir, d)),
                      keep_ext=True)
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
    return {"status": "ok", "template": _dict(t)}


@router.patch("/brief-templates/{tid}")
async def update_brief_template(tid: str, request: Request, body: dict):
    """改顯示名或**直接改骨架**。骨架是給人改的 —— 生成出來不對味時改這段，
    比回頭調 prompt 直觀得多。"""
    proposal_auth(request)
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
        return {"status": "ok", "template": _dict(t)}


@router.post("/brief-templates/{tid}/digest")
async def digest_brief_template(tid: str, request: Request):
    """把這份範本濃縮成骨架（跑 claude，可能要好幾分鐘）。

    **背景跑、立刻回**：claude --print 一次數十秒到三分鐘，同步等會讓請求逾時，
    而且使用者盯著轉圈也沒有比較快。前端靠清單裡的 status 輪詢。

    冪等但會**覆蓋**現有骨架（含人手改過的）—— 前端負責問一次。
    """
    proposal_auth(request)
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
    # 不 await —— 這支端點的責任只到「已經開始跑」為止。走 core.bg_task.fire
    # 才有強參考（裸 create_task 只被 loop 弱參考，GC 可以直接收掉）與例外守衛。
    fire(digest_template(tid), label=f"digest {tid}")
    return {"status": "started"}


@router.delete("/brief-templates/{tid}")
async def delete_brief_template(tid: str, request: Request):
    """刪範本：DB 那列 + `_範本` 裡那份檔案。原提案的檔案不動。"""
    proposal_auth(request)
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


def _listdir(path: str) -> list:
    try:
        return os.listdir(path)
    except OSError:
        return []
