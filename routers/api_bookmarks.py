"""
書籤（任務預設）CRUD API — Originsun Media Guard Pro
"""

import json
import os
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException  # type: ignore

from core.schemas import BookmarkCreateRequest, BookmarkUpdateRequest  # type: ignore
from core.scheduler import build_request, SCHEDULABLE_TYPES  # type: ignore

router = APIRouter(prefix="/api/v1", tags=["bookmarks"])

_BOOKMARK_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bookmarks.json")
_lock = threading.Lock()


def _load() -> list:
    if not os.path.exists(_BOOKMARK_FILE):
        return []
    try:
        with open(_BOOKMARK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(bookmarks: list) -> None:
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


@router.get("/bookmarks")
async def list_bookmarks():
    return _load()


@router.post("/bookmarks")
async def create_bookmark(body: BookmarkCreateRequest):
    _validate_request(body.task_type, body.request)
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
        bookmarks = _load()
        bookmarks.append(entry)
        _save(bookmarks)
    return entry


@router.put("/bookmarks/{bookmark_id}")
async def update_bookmark(bookmark_id: str, body: BookmarkUpdateRequest):
    with _lock:
        bookmarks = _load()
        target = next((b for b in bookmarks if b.get("id") == bookmark_id), None)
        if not target:
            raise HTTPException(status_code=404, detail=f"找不到書籤: {bookmark_id}")

        if body.request is not None:
            task_type = target["task_type"]
            _validate_request(task_type, body.request)
            target["request"] = body.request

        if body.name is not None:
            target["name"] = body.name

        target["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save(bookmarks)
    return target


@router.delete("/bookmarks/{bookmark_id}")
async def delete_bookmark(bookmark_id: str):
    with _lock:
        bookmarks = _load()
        new_list = [b for b in bookmarks if b.get("id") != bookmark_id]
        if len(new_list) == len(bookmarks):
            raise HTTPException(status_code=404, detail=f"找不到書籤: {bookmark_id}")
        _save(new_list)
    return {"deleted": bookmark_id}
