"""routers/crm/proposal_meetings.py — 提案的「會議記錄」分頁後端。

一次會議一筆，人手寫。**刻意沒有 AI、沒有版本快照** —— 會議記錄是創意發想
與企劃書的**輸入素材**，不是產出物；企劃書那套「重新生成＝新增一版」的機制
套過來只會讓人不敢改字。

🔴 內部資料：不出公開 `?t=` token 端點（客戶會議也會記到內部判斷、競品、
報價底線）。要給客戶看的東西走「提案資料」的勾選。所有端點都掛內部守衛，
沒有任何一條進 public_router。

日期沿用提案日的下錨規則（`api_proposals._parse_date/_fmt_date`）—— 會議日期
是「日曆日」不是時刻，各自寫一套就會出現 v2.4.33 修過的差一天。
"""
from __future__ import annotations

import uuid

from typing import Optional

from fastapi import HTTPException, Request

# 守衛與日期規則都用提案那邊的正本（子分頁一律對齊 proposal_auth，含
# preprod_plan —— 打得開提案工作頁的人，分頁就要能用）
from routers.api_proposals import (_fmt_date, _get_proposal_or_404, _parse_date,
                                   proposal_auth)

from ._shared import router, _get_factory, _now, _require_db

try:
    from ._shared import select
    from db.models import PreprodMeetingNote
except ImportError:  # DB 套件不存在的 agent 環境
    pass

# 一筆記錄的本文上限（防呆，不是業務規則）—— 貼一整份逐字稿也夠
_CONTENT_MAX = 64 * 1024
_TITLE_MAX = 255
_ATTENDEES_MAX = 512


def _dict(m) -> dict:
    return {
        "id": m.id,
        "met_at": _fmt_date(m.met_at),
        "title": m.title or "",
        "attendees": m.attendees or "",
        "content": m.content or "",
        "created_by": m.created_by or "",
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


async def _row_or_404(session, pid: str, mid: str):
    m = await session.get(PreprodMeetingNote, mid)
    if not m or m.proposal_id != pid:      # 拿別筆提案的 mid 來打 → 一律 404
        raise HTTPException(status_code=404, detail="找不到這一筆會議記錄")
    return m


def _clip(raw, limit: int) -> Optional[str]:
    s = str(raw or "").strip()
    return s[:limit] or None


@router.get("/proposals/{pid}/meetings")
async def list_meeting_notes(pid: str, request: Request):
    """這筆提案的會議記錄（新到舊）。沒有日期的排最後 —— 剛建還沒填日期的
    那筆該留在原地，不該跳到最前面。"""
    proposal_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        await _get_proposal_or_404(session, pid)
        rows = (await session.execute(
            select(PreprodMeetingNote)
            .where(PreprodMeetingNote.proposal_id == pid))).scalars().all()
    # 在 Python 排：met_at 可為 NULL，各家 DB 的 NULLS FIRST/LAST 預設不同
    rows = sorted(rows, key=lambda m: (m.met_at is not None,
                                       m.met_at or m.created_at),
                  reverse=True)
    return {"notes": [_dict(m) for m in rows]}


@router.post("/proposals/{pid}/meetings")
async def create_meeting_note(pid: str, request: Request):
    """新增一筆（欄位全可空 —— 開會當下先建一筆再邊聽邊補是常態）。"""
    payload = proposal_auth(request)
    _require_db()
    body = await request.json()
    factory = await _get_factory()
    async with factory() as session:
        await _get_proposal_or_404(session, pid)
        m = PreprodMeetingNote(
            id=uuid.uuid4().hex, proposal_id=pid,
            met_at=_parse_date(body.get("met_at")),
            title=_clip(body.get("title"), _TITLE_MAX),
            attendees=_clip(body.get("attendees"), _ATTENDEES_MAX),
            content=_clip(body.get("content"), _CONTENT_MAX),
            created_by=str((payload or {}).get("username") or ""),
            created_at=_now(), updated_at=_now())
        session.add(m)
        await session.commit()
        return {"status": "ok", "note": _dict(m)}


@router.patch("/proposals/{pid}/meetings/{mid}")
async def update_meeting_note(pid: str, mid: str, request: Request):
    """部分更新（前端逐欄自動儲存，一次送一個欄位）。

    只認白名單欄位，且**沒送的欄位不動** —— 整包 model_dump 寫回會讓沒送的
    欄位被預設值洗掉（repo 既有地雷，見 v2.0.5/2.0.6）。
    """
    proposal_auth(request)
    _require_db()
    body = await request.json()
    factory = await _get_factory()
    async with factory() as session:
        m = await _row_or_404(session, pid, mid)
        if "met_at" in body:
            m.met_at = _parse_date(body.get("met_at"))
        if "title" in body:
            m.title = _clip(body.get("title"), _TITLE_MAX)
        if "attendees" in body:
            m.attendees = _clip(body.get("attendees"), _ATTENDEES_MAX)
        if "content" in body:
            m.content = _clip(body.get("content"), _CONTENT_MAX)
        m.updated_at = _now()
        await session.commit()
        return {"status": "ok", "note": _dict(m)}


@router.delete("/proposals/{pid}/meetings/{mid}")
async def delete_meeting_note(pid: str, mid: str, request: Request):
    proposal_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        m = await _row_or_404(session, pid, mid)
        await session.delete(m)
        await session.commit()
    return {"status": "ok"}
