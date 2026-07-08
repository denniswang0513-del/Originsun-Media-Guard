"""
api_footage.py — B5 內部素材庫 API（docs/BIZ_PLAN.md B5 段）

掃描既有影片 + 同名逐字稿建索引 → 全文檢索（transcript/檔名/專案/tags ILIKE，
pg_trgm 有裝就加速、沒裝降級純 ILIKE，語法相同）→ 命中回上下文 snippet。
第一版手動「掃描資料夾」入索引；備份完成自動入索引的 hook 後續再接。
"""

import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request  # type: ignore

from core.auth import check_admin_or_module
from core.schemas import FootageScanRequest, FootageTagsPayload

router = APIRouter(prefix="/api/v1/footage", tags=["footage"])

# 掃描是單工背景任務：一次一個，狀態記模組層
_scan_status = {"running": False, "last_result": None, "root": ""}


def _guard(request: Request):
    check_admin_or_module(request, "footage", "transcribe")


from core.db_guard import db_factory_or_503 as _factory_or_503


def _snippet(transcript: str, q: str, width: int = 80) -> str:
    """逐字稿命中上下文：命中詞前後各 width 字。無命中回開頭一段。"""
    if not transcript:
        return ""
    low = transcript.lower()
    pos = low.find(q.lower()) if q else -1
    if pos < 0:
        return transcript[:width * 2].strip()
    start = max(0, pos - width)
    end = min(len(transcript), pos + len(q) + width)
    seg = transcript[start:end].strip()
    return ("…" if start > 0 else "") + seg + ("…" if end < len(transcript) else "")


def _row_dict(f, q: str = "") -> dict:
    return {
        "id": f.id,
        "project_id": f.project_id or "",
        "project_name": f.project_name or "",
        "file_path": f.file_path,
        "file_name": f.file_name or "",
        "ext": f.ext or "",
        "duration_sec": f.duration_sec,
        "resolution": f.resolution or "",
        "fps": f.fps or "",
        "shot_date": f.shot_date.strftime("%Y-%m-%d") if f.shot_date else None,
        "has_transcript": bool(f.transcript),
        "snippet": _snippet(f.transcript or "", q),
        "tags": f.tags or [],
        "size_bytes": f.size_bytes,
        "indexed_at": f.indexed_at.strftime("%Y-%m-%d %H:%M") if f.indexed_at else None,
    }


async def _run_scan(root_path: str, project_id: str, project_name: str):
    """背景掃描 → 批次 upsert by file_path。walk+ffprobe 在 thread，DB 在 async 層。"""
    import asyncio
    from sqlalchemy import select
    from db.models import FootageIndex
    from services.footage_indexer import collect_folder

    try:
        result = await asyncio.to_thread(collect_folder, root_path, project_name)
        records = result["records"]
        indexed = updated = 0
        factory = _factory_or_503()
        async with factory() as session:
            # 一次 IN 撈既有列（避免 per-record SELECT — 掃 2000 檔就是 2000 次往返）
            paths = [r["file_path"] for r in records]
            existing_by_path = {}
            if paths:
                for row in (await session.execute(
                        select(FootageIndex).where(FootageIndex.file_path.in_(paths)))).scalars():
                    existing_by_path[row.file_path] = row
            for rec in records:
                existing = existing_by_path.get(rec["file_path"])
                shot_date = None
                try:
                    shot_date = datetime.fromtimestamp(rec["shot_mtime"], tz=timezone.utc)
                except (KeyError, OSError, ValueError):
                    pass
                fields = dict(
                    project_id=project_id or None,
                    project_name=rec.get("project_name", ""),
                    file_name=rec.get("file_name", ""),
                    ext=rec.get("ext", ""),
                    duration_sec=rec.get("duration_sec"),
                    resolution=rec.get("resolution"),
                    fps=rec.get("fps"),
                    shot_date=shot_date,
                    transcript=rec.get("transcript") or None,
                    size_bytes=rec.get("size_bytes"),
                )
                if existing:
                    for k, v in fields.items():
                        setattr(existing, k, v)
                    existing.indexed_at = datetime.now(timezone.utc)
                    updated += 1
                else:
                    session.add(FootageIndex(id=uuid.uuid4().hex, file_path=rec["file_path"], **fields))
                    indexed += 1
            await session.commit()
        _scan_status["last_result"] = {
            "scanned": result["scanned"], "indexed": indexed, "updated": updated,
            "errors": result["errors"], "truncated": result["truncated"],
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        }
    except Exception as e:
        _scan_status["last_result"] = {"error": f"{type(e).__name__}: {e}"}
    finally:
        _scan_status["running"] = False


@router.post("/scan")
async def scan_folder(req: FootageScanRequest, request: Request):
    _guard(request)
    if not req.root_path or not os.path.isdir(req.root_path):
        raise HTTPException(status_code=422, detail="資料夾不存在")
    if _scan_status["running"]:
        raise HTTPException(status_code=409, detail="已有掃描進行中，請等它結束")
    _factory_or_503()  # 先確認 DB 在線再開背景任務
    import asyncio
    _scan_status.update({"running": True, "last_result": None, "root": req.root_path})
    asyncio.create_task(_run_scan(req.root_path, req.project_id or "", req.project_name or ""))
    return {"status": "started", "root": req.root_path}


@router.get("/scan/status")
async def scan_status(request: Request):
    _guard(request)
    return _scan_status


@router.get("/search")
async def search(request: Request, q: str = "", project_id: str = "",
                 ext: str = "", limit: int = 50):
    _guard(request)
    limit = max(1, min(limit, 200))
    from sqlalchemy import select, or_, Text as SAText
    from db.models import FootageIndex
    factory = _factory_or_503()
    async with factory() as session:
        stmt = select(FootageIndex)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(
                FootageIndex.transcript.ilike(like),
                FootageIndex.file_name.ilike(like),
                FootageIndex.project_name.ilike(like),
                FootageIndex.tags.cast(SAText).ilike(like),
            ))
        if project_id:
            stmt = stmt.where(FootageIndex.project_id == project_id)
        if ext:
            stmt = stmt.where(FootageIndex.ext == (ext if ext.startswith(".") else "." + ext))
        stmt = stmt.order_by(FootageIndex.indexed_at.desc()).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
    return {"items": [_row_dict(f, q) for f in rows], "count": len(rows)}


@router.get("/stats")
async def stats(request: Request):
    _guard(request)
    from sqlalchemy import select, func as safunc, distinct
    from db.models import FootageIndex
    factory = _factory_or_503()
    async with factory() as session:
        total, total_dur, with_tr, projects = (await session.execute(
            select(
                safunc.count(FootageIndex.id),
                safunc.coalesce(safunc.sum(FootageIndex.duration_sec), 0),
                safunc.count(FootageIndex.transcript),
                safunc.count(distinct(FootageIndex.project_name)),
            ))).one()
    return {
        "total": int(total or 0),
        "total_duration_sec": float(total_dur or 0),
        "with_transcript": int(with_tr or 0),
        "projects": int(projects or 0),
    }


@router.put("/{fid}/tags")
async def update_tags(fid: str, payload: FootageTagsPayload, request: Request):
    _guard(request)
    from db.models import FootageIndex
    factory = _factory_or_503()
    async with factory() as session:
        f = await session.get(FootageIndex, fid)
        if not f:
            raise HTTPException(status_code=404, detail="素材不存在")
        f.tags = [t.strip() for t in (payload.tags or []) if t and t.strip()]
        await session.commit()
        return {"ok": True, "tags": f.tags}


@router.delete("/{fid}")
async def delete_footage(fid: str, request: Request):
    """只刪索引，不刪實體檔案。"""
    _guard(request)
    from db.models import FootageIndex
    factory = _factory_or_503()
    async with factory() as session:
        f = await session.get(FootageIndex, fid)
        if not f:
            raise HTTPException(status_code=404, detail="素材不存在")
        await session.delete(f)
        await session.commit()
    return {"ok": True}


@router.post("/{fid}/open")
async def open_location(fid: str, request: Request):
    """在 Explorer 選取該檔（同 /utils/open_folder 的 explorer /select）。"""
    _guard(request)
    from db.models import FootageIndex
    factory = _factory_or_503()
    async with factory() as session:
        f = await session.get(FootageIndex, fid)
        if not f:
            raise HTTPException(status_code=404, detail="素材不存在")
        path = f.file_path
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="實體檔案已不在原位置")
    try:
        import subprocess
        subprocess.Popen(f'explorer /select,"{path}"')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"開啟失敗: {e}")
    return {"ok": True}
