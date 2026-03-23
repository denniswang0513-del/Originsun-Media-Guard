"""
api_bookmarks.py — 書籤（任務預設）CRUD API（DB 優先，JSON fallback）
"""

import json
import os
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException  # type: ignore

from core.schemas import BookmarkCreateRequest, BookmarkUpdateRequest  # type: ignore
from core.scheduler import build_request, SCHEDULABLE_TYPES  # type: ignore
import core.state as state

router = APIRouter(prefix="/api/v1", tags=["bookmarks"])


# ─── JSON Fallback Helpers ────────────────────────────────

_BOOKMARK_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bookmarks.json")
_lock = threading.Lock()


def _load_json() -> list:
    if not os.path.exists(_BOOKMARK_FILE):
        return []
    try:
        with open(_BOOKMARK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_json(bookmarks: list) -> None:
    tmp = _BOOKMARK_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(bookmarks, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _BOOKMARK_FILE)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _get_machine_id() -> str:
    try:
        from config import load_settings
        return load_settings().get("machine_id", "") or __import__("socket").gethostname()
    except Exception:
        return "unknown"


def _validate_request(task_type: str, request: dict) -> None:
    """驗證 request dict 是否符合對應 Pydantic model。"""
    if task_type not in SCHEDULABLE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"不支援的任務類型: {task_type}，可用: {', '.join(sorted(SCHEDULABLE_TYPES))}",
        )
    if task_type not in ("tts", "clone"):
        try:
            build_request(task_type, request)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"request 欄位驗證失敗: {e}")


# ─── Endpoints ────────────────────────────────────────────

@router.get("/bookmarks")
async def list_bookmarks():
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import bookmarks_repo
                async with factory() as session:
                    return await bookmarks_repo.list_all(session, machine_id=_get_machine_id())
        except Exception:
            pass

    # JSON fallback
    return _load_json()


@router.post("/bookmarks")
async def create_bookmark(body: BookmarkCreateRequest):
    _validate_request(body.task_type, body.request)

    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import bookmarks_repo
                bm_id = uuid.uuid4().hex[:8]
                async with factory() as session:
                    entry = await bookmarks_repo.create(
                        session,
                        id=bm_id,
                        machine_id=_get_machine_id(),
                        name=body.name,
                        task_type=body.task_type,
                        request=body.request,
                    )
                    await session.commit()
                    return entry
        except HTTPException:
            raise
        except Exception:
            pass

    # JSON fallback
    now = datetime.now().isoformat(timespec="seconds")
    entry = {
        "id": uuid.uuid4().hex[:8],
        "name": body.name,
        "task_type": body.task_type,
        "request": body.request,
        "created_at": now,
        "updated_at": now,
    }
    with _lock:
        bookmarks = _load_json()
        bookmarks.append(entry)
        _save_json(bookmarks)
    return entry


@router.put("/bookmarks/{bookmark_id}")
async def update_bookmark(bookmark_id: str, body: BookmarkUpdateRequest):
    if body.request is not None:
        # Need task_type for validation — try DB first, then JSON
        task_type = None
        if state.db_online:
            try:
                from db.session import get_session_factory
                factory = get_session_factory()
                if factory:
                    from db.repos import bookmarks_repo
                    async with factory() as session:
                        all_bm = await bookmarks_repo.list_all(session)
                        target = next((b for b in all_bm if b.get("id") == bookmark_id), None)
                        if target:
                            task_type = target.get("task_type")
            except Exception:
                pass
        if task_type is None:
            bms = _load_json()
            t = next((b for b in bms if b.get("id") == bookmark_id), None)
            if t:
                task_type = t.get("task_type", "backup")
        if task_type:
            _validate_request(task_type, body.request)

    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import bookmarks_repo
                kwargs = {}
                if body.name is not None:
                    kwargs["name"] = body.name
                if body.request is not None:
                    kwargs["request"] = body.request
                async with factory() as session:
                    result = await bookmarks_repo.update_bookmark(session, bookmark_id, **kwargs)
                    if result is None:
                        raise HTTPException(status_code=404, detail=f"找不到書籤: {bookmark_id}")
                    await session.commit()
                    return result
        except HTTPException:
            raise
        except Exception:
            pass

    # JSON fallback
    with _lock:
        bookmarks = _load_json()
        target = next((b for b in bookmarks if b.get("id") == bookmark_id), None)
        if not target:
            raise HTTPException(status_code=404, detail=f"找不到書籤: {bookmark_id}")

        if body.request is not None:
            target["request"] = body.request
        if body.name is not None:
            target["name"] = body.name

        target["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_json(bookmarks)
    return target


@router.delete("/bookmarks/{bookmark_id}")
async def delete_bookmark(bookmark_id: str):
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import bookmarks_repo
                async with factory() as session:
                    removed = await bookmarks_repo.delete_bookmark(session, bookmark_id)
                    await session.commit()
                    if not removed:
                        raise HTTPException(status_code=404, detail=f"找不到書籤: {bookmark_id}")
                    return {"deleted": bookmark_id}
        except HTTPException:
            raise
        except Exception:
            pass

    # JSON fallback
    with _lock:
        bookmarks = _load_json()
        new_list = [b for b in bookmarks if b.get("id") != bookmark_id]
        if len(new_list) == len(bookmarks):
            raise HTTPException(status_code=404, detail=f"找不到書籤: {bookmark_id}")
        _save_json(new_list)
    return {"deleted": bookmark_id}
