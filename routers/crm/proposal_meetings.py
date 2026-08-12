"""routers/crm/proposal_meetings.py — 提案的「會議記錄」分頁後端。

一次會議一筆。**沒有版本快照** —— 會議記錄是創意發想與企劃書的**輸入素材**，
不是產出物；企劃書那套「重新生成＝新增一版」的機制套過來只會讓人不敢改字。
AI 只做**輸入輔助**：上傳錄音檔 → whisper 逐字稿 → claude 整理
（services/meeting_transcriber）；`content` 是人的正本，AI 只在它全空時代填
一次，之後絕不覆蓋。

🔴 內部資料：不出公開 `?t=` token 端點（客戶會議也會記到內部判斷、競品、
報價底線）。要給客戶看的東西走「提案資料」的勾選。所有端點都掛內部守衛，
沒有任何一條進 public_router。錄音檔與逐字稿同樣不進 /uploads web root ——
落點是專案資產夾的「會議記錄」子夾，下載走帶權限端點（比照報價單）。

日期沿用提案日的下錨規則（`api_proposals._parse_date/_fmt_date`）—— 會議日期
是「日曆日」不是時刻，各自寫一套就會出現 v2.4.33 修過的差一天。
"""
from __future__ import annotations

import asyncio
import os
import uuid

from typing import Optional

from fastapi import File, HTTPException, Request, UploadFile

from core.bg_status import settle
from core.bg_task import fire

# 守衛與日期規則都用提案那邊的正本（子分頁一律對齊 proposal_auth，含
# preprod_plan —— 打得開提案工作頁的人，分頁就要能用）
from routers.api_proposals import (_fmt_date, _get_proposal_or_404, _parse_date,
                                   proposal_auth)

from .proposal_assets import (home_ready, land_in_home,
                              project_folder_abs, resolve_asset_file)

# 頂層可 import：meeting_transcriber 模組層只有 stdlib（whisper/db 都在函式內 lazy）
from services.meeting_transcriber import (disk_artifacts, process_meeting_audio,
                                          summarize_meeting)

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
# 錄音上傳：白名單副檔名（會議錄音/錄影的常見格式）+ 上限（兩小時 m4a 遠低於
# 此數；設 500MB 是收得下手機錄影的等級）
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
               ".webm", ".mp4", ".mov", ".mkv", ".mts"}
_MAX_AUDIO_BYTES = 500 * 1024 * 1024
_AUDIO_SUBDIR = "會議記錄"             # 系統管的落點（敢動磁碟的前提，同報價單）


def _dict(m) -> dict:
    # 卡太久的 pending 在讀取端改判 failed（伺服器重啟過 → task 沒了）。
    # 長錄音靠 meeting_transcriber 的心跳蓋 updated_at 撐過 STALE_AFTER。
    status, error = settle(m.status or "", m.updated_at, m.error or "")
    return {
        "id": m.id,
        "met_at": _fmt_date(m.met_at),
        "title": m.title or "",
        "attendees": m.attendees or "",
        "content": m.content or "",
        "created_by": m.created_by or "",
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "audio_name": os.path.basename(m.audio_rel) if m.audio_rel else "",
        # pending 期間前端只畫 phase —— 一小時錄音的逐字稿幾十 KB，
        # 別讓每 5 秒的輪詢白載它幾十趟
        "transcript": "" if status == "pending" else (m.transcript or ""),
        "ai_summary": "" if status == "pending" else (m.ai_summary or ""),
        "status": status,
        "error": error,
        "phase": (m.phase or "") if status == "pending" else "",
    }


def _ctx(m) -> dict:
    """給 AI 整理的背景快照（services/meeting_transcriber 的 prompt 背景段）。"""
    return {"title": m.title or "", "met_at": _fmt_date(m.met_at) or "",
            "attendees": m.attendees or "", "content": m.content or ""}


async def _remove_audio_files(folder_abs: str, rel: str) -> None:
    """best-effort 清一個錄音的全部磁碟檔（換檔/刪列共用）。哪些檔算「它的」
    由 service 的 `disk_artifacts` 定義（寫入端同一份，多 sidecar 不會漏）。
    清不掉（NAS 斷線/已被搬走）不擋 —— 同報價單刪檔的理由。"""
    from core.project_folders import safe_rel_path

    def _rm():
        p = safe_rel_path(folder_abs, rel)
        if not p:
            return
        for path in disk_artifacts(p):
            try:
                os.remove(path)
            except OSError:
                pass
    await asyncio.to_thread(_rm)


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
        audio_rel, folder_abs = m.audio_rel or "", ""
        if audio_rel:
            prop = await _get_proposal_or_404(session, pid)
            try:
                folder_abs = await project_folder_abs(session, prop.project_id or "")
            except HTTPException:
                folder_abs = ""         # 殼專案不見了 → 磁碟檔已無從清起
        await session.delete(m)
        await session.commit()
    if audio_rel and folder_abs:
        await _remove_audio_files(folder_abs, audio_rel)
    return {"status": "ok"}


@router.get("/proposals/{pid}/meetings/{mid}")
async def get_meeting_note(pid: str, mid: str, request: Request):
    """單筆（錄音處理中的輪詢用 —— 每 5 秒為了一個 status 掃整份清單是浪費）。"""
    proposal_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        m = await _row_or_404(session, pid, mid)
        return {"note": _dict(m)}


@router.post("/proposals/{pid}/meetings/{mid}/audio")
async def upload_meeting_audio(pid: str, mid: str, request: Request,
                               file: UploadFile = File(...)):
    """上傳會議錄音 → 落在 專案資產夾/{提案的家}/會議記錄/ → 背景轉逐字稿 +
    AI 整理。換檔＝整條重跑（舊逐字稿/整理/錄音檔一併換掉）。

    比照報價單：**沒有退路** —— 沒連結專案、資產夾建不出來 → 400 明講；
    也不做 /uploads web root 靜態直出。
    """
    proposal_auth(request)
    _require_db()
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _AUDIO_EXTS:
        raise HTTPException(status_code=422,
                            detail="只收錄音/影音檔（mp3、wav、m4a、mp4…）")
    # 大小前置成 413（契約同報價單；save_uploads 只能回 422）
    if (getattr(file, "size", None) or 0) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="錄音檔超過 500MB 上限")

    factory = await _get_factory()
    # 兩段式（同報價單上傳的既有正本）：session1 只做 DB —— 幾百 MB 的 SMB
    # 寫入不抱著 pool 連線（pool_timeout=5s）
    async with factory() as session:
        m = await _row_or_404(session, pid, mid)
        prop = await _get_proposal_or_404(session, pid)
        folder_abs, home_subpath = await home_ready(session, prop)
        old_rel, ctx = m.audio_rel or "", _ctx(m)

    dest, rel = await land_in_home(folder_abs, home_subpath, file,
                                   subdir=_AUDIO_SUBDIR,
                                   max_bytes=_MAX_AUDIO_BYTES)

    async with factory() as session:
        m = await _row_or_404(session, pid, mid)
        m.audio_rel = rel
        m.transcript = None
        m.ai_summary = None
        m.status, m.error, m.phase = "pending", None, "排隊中"
        m.updated_at = _now()
        await session.commit()
        out = _dict(m)
    if old_rel and old_rel != rel:      # 換檔：舊錄音+舊 .txt 清掉（子夾是系統管的）
        await _remove_audio_files(folder_abs, old_rel)

    # core.bg_task.fire：強參考 + 例外守衛（例外沒接住這筆會永遠 pending）
    fire(process_meeting_audio(mid, audio_path=dest, ctx=ctx),
         label=f"meeting-audio {mid}")
    return {"status": "ok", "note": out}


@router.post("/proposals/{pid}/meetings/{mid}/summarize")
async def resummarize_meeting(pid: str, mid: str, request: Request):
    """只重跑 AI 整理（用既有逐字稿）—— claude 那段失敗時不必重付整段
    whisper 的時間。"""
    proposal_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        m = await _row_or_404(session, pid, mid)
        if not (m.transcript or "").strip():
            raise HTTPException(status_code=422,
                                detail="這一筆還沒有逐字稿 —— 先上傳錄音檔")
        if settle(m.status or "", m.updated_at)[0] == "pending":
            raise HTTPException(status_code=409, detail="正在處理中，等它跑完再重試")
        m.status, m.error, m.phase = "pending", None, "AI 整理中"
        m.updated_at = _now()
        await session.commit()
        transcript, ctx, out = m.transcript, _ctx(m), _dict(m)
    fire(summarize_meeting(mid, transcript=transcript, ctx=ctx),
         label=f"meeting-summarize {mid}")
    return {"status": "ok", "note": out}


@router.get("/proposals/{pid}/meetings/{mid}/audio/download")
async def download_meeting_audio(pid: str, mid: str, request: Request):
    """帶權限出檔 —— 錄音在 NAS 資產夾、不在 web root（比照報價單下載）。"""
    proposal_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        m = await _row_or_404(session, pid, mid)
        prop = await _get_proposal_or_404(session, pid)
        if not m.audio_rel or not prop.project_id:
            raise HTTPException(status_code=404, detail="這一筆沒有錄音檔")
        path = await resolve_asset_file(session, prop.project_id, m.audio_rel)
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=os.path.basename(path))
