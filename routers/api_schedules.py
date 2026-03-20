"""
排程任務 CRUD API — Originsun Media Guard Pro
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException  # type: ignore

from core.schemas import ScheduleCreateRequest, ScheduleUpdateRequest  # type: ignore
from core.scheduler import (  # type: ignore
    load_schedules,
    save_schedules,
    is_valid_cron,
    compute_next_run,
    build_request,
    SCHEDULABLE_TYPES,
    _file_lock,
)

router = APIRouter(prefix="/api/v1", tags=["schedules"])


@router.get("/schedules")
async def list_schedules():
    """列出所有排程。"""
    return load_schedules()


@router.post("/schedules")
async def create_schedule(body: ScheduleCreateRequest):
    """新增排程。"""
    # 驗證 task_type
    if body.task_type not in SCHEDULABLE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"不支援的任務類型: {body.task_type}，"
                   f"可用: {', '.join(SCHEDULABLE_TYPES)}",
        )

    # 決定 next_run
    has_cron = body.cron is not None and body.cron.strip() != ""
    has_run_at = body.run_at is not None and body.run_at.strip() != ""

    if not has_cron and not has_run_at:
        raise HTTPException(
            status_code=422,
            detail="必須提供 cron（重複排程）或 run_at（單次排程）",
        )

    if has_cron:
        if not is_valid_cron(body.cron):
            raise HTTPException(status_code=422, detail=f"無效的 cron 表達式: {body.cron}")
        next_run = compute_next_run(body.cron, datetime.now())
    else:
        # 單次排程：驗證 run_at 格式
        try:
            run_dt = datetime.fromisoformat(body.run_at)
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail=f"無效的日期時間格式: {body.run_at}")
        if run_dt <= datetime.now():
            raise HTTPException(status_code=422, detail="排程時間必須在未來")
        next_run = run_dt.isoformat(timespec="seconds")

    # 驗證 request 欄位（tts/clone 類型跳過 Pydantic 驗證，因為 schema 在 api_tts.py）
    if body.task_type not in ("tts", "clone"):
        try:
            build_request(body.task_type, body.request)
        except Exception as e:
            raise HTTPException(
                status_code=422,
                detail=f"request 欄位驗證失敗: {e}",
            )

    schedule_id = uuid.uuid4().hex[:8]
    now = datetime.now()
    entry = {
        "schedule_id": schedule_id,
        "name": body.name,
        "cron": body.cron if has_cron else None,
        "run_at": body.run_at if has_run_at else None,
        "task_type": body.task_type,
        "request": body.request,
        "enabled": body.enabled,
        "next_run": next_run,
        "last_run": None,
        "created_at": now.isoformat(timespec="seconds"),
    }

    with _file_lock:
        schedules = load_schedules()
        schedules.append(entry)
        save_schedules(schedules)

    return entry


@router.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, body: ScheduleUpdateRequest):
    """更新排程（部分更新）。"""
    with _file_lock:
        schedules = load_schedules()
        target = None
        for sch in schedules:
            if sch.get("schedule_id") == schedule_id:
                target = sch
                break

        if target is None:
            raise HTTPException(status_code=404, detail=f"找不到排程: {schedule_id}")

        # 套用更新欄位
        if body.name is not None:
            target["name"] = body.name
        if body.task_type is not None:
            if body.task_type not in SCHEDULABLE_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail=f"不支援的任務類型: {body.task_type}",
                )
            target["task_type"] = body.task_type
        if body.enabled is not None:
            target["enabled"] = body.enabled
        if body.request is not None:
            task_type = body.task_type or target.get("task_type", "backup")
            if task_type not in ("tts", "clone"):
                try:
                    build_request(task_type, body.request)
                except Exception as e:
                    raise HTTPException(
                        status_code=422,
                        detail=f"request 欄位驗證失敗: {e}",
                    )
            target["request"] = body.request
        if body.cron is not None:
            if not is_valid_cron(body.cron):
                raise HTTPException(
                    status_code=422,
                    detail=f"無效的 cron 表達式: {body.cron}",
                )
            target["cron"] = body.cron
            target["run_at"] = None
            target["next_run"] = compute_next_run(body.cron)
        if body.run_at is not None:
            try:
                run_dt = datetime.fromisoformat(body.run_at)
            except (ValueError, TypeError):
                raise HTTPException(status_code=422, detail=f"無效的日期時間格式: {body.run_at}")
            target["run_at"] = body.run_at
            target["cron"] = None
            target["next_run"] = run_dt.isoformat(timespec="seconds")

        save_schedules(schedules)

    return target


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    """刪除排程。"""
    with _file_lock:
        schedules = load_schedules()
        new_list = [s for s in schedules if s.get("schedule_id") != schedule_id]

        if len(new_list) == len(schedules):
            raise HTTPException(status_code=404, detail=f"找不到排程: {schedule_id}")

        save_schedules(new_list)

    return {"deleted": schedule_id}
