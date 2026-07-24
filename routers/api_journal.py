"""api_journal.py — 每週工作日誌 API。

每人每週一份（username=token sub、week_start=該週週一，伺服端正規化），四個
區塊分開存表（wins 順利的事與想感謝的人 / challenges 遇到哪些挑戰 /
learnings 學到了什麼 / others 其他主題 — owner 明確要求分表，「學到了什麼」要能獨立跨週彙整
成學習庫，見 GET /learnings）。全員可讀、本人可寫（/mine 一律以 token sub
為對象，不收 client 傳入的 username）。⚠「全員」現況只對新註冊帳號成立
（api_auth 預設 modules 含 journal）；既有帳號需管理員在使用者管理補開通，
或發版時跑一次性 backfill。

可編輯窗（伺服端強制，owner 規則 2026-07-22）：week_start <= 本週週一 + 7 天
＝ 過往任何週、當週、下一週恆可編；更遠未來 403「只能編輯到下一週」。

權限：check_admin_or_module(request, 'journal')。純函式在 core/journal_logic.py。
"""
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Request  # type: ignore
from sqlalchemy import delete as sa_delete, func, select  # type: ignore

from core.auth import check_admin_or_module
from core.db_guard import db_factory_or_503
from core.hr_logic import parse_ymd
from core.journal_logic import clean_entries, editable_window_ok, week_start_of
from core.schemas import JournalPut
from db.models import (CrmStaff, JournalChallenge, JournalLearning,
                       JournalOther, JournalWin, User, WorkJournal)

router = APIRouter(prefix="/api/v1/journal", tags=["journal"])

# 四區塊 ↔ 表 對映。key 集合必須與 JournalPut 欄位、前端 journal-core.js 的
# BLOCKS 一致（守衛：tests/unit/test_journal.py TestSectionRegistryConsistency）
# —— 前端漏 key 的後果是 PUT 全量替換把該區已存資料靜默清空，不是顯示問題。
_SECTION_MODELS = (("wins", JournalWin),
                   ("challenges", JournalChallenge),
                   ("learnings", JournalLearning),
                   ("others", JournalOther))


def _week_from_param(start: str) -> date:
    """start 參數（YYYY-MM-DD；缺省=今天）→ 正規化為該週週一。壞格式 422。"""
    if not (start or "").strip():
        return week_start_of(date.today())
    dt = parse_ymd(start)
    if dt is None:
        raise HTTPException(status_code=422, detail="start 需為 YYYY-MM-DD")
    return week_start_of(dt.date())


async def _entries_by_journal(session, journal_ids: list) -> dict:
    """{journal_id: {各區 key: [str]}}（key 集合=_SECTION_MODELS，依 sort_order）。"""
    out = {jid: {key: [] for key, _ in _SECTION_MODELS} for jid in journal_ids}
    if not journal_ids:
        return out
    for key, model in _SECTION_MODELS:
        rows = (await session.execute(
            select(model).where(model.journal_id.in_(journal_ids))
            .order_by(model.sort_order, model.id)
        )).scalars().all()
        for r in rows:
            out[r.journal_id][key].append(r.content or "")
    return out


async def _display_names(session, usernames) -> dict:
    """{username: 顯示名}。帳號有綁人員檔案（User.staff_id → crm_staff，N0 橋接）
    時顯示本名（owner 2026-07-24：牆上要顯示人力資料庫本名），沒綁 fallback 帳號名。
    列表端點（week/people/learnings）都走這裡 — 一次查全批，不逐人打 DB。"""
    names = {u: u for u in usernames}
    if not names:
        return names
    rows = (await session.execute(
        select(User.username, CrmStaff.name)
        .join(CrmStaff, CrmStaff.id == User.staff_id)
        .where(User.username.in_(list(names)))
    )).all()
    for u, n in rows:
        if (n or "").strip():
            names[u] = n.strip()
    return names


async def _get_shell(session, username: str, week: date):
    return (await session.execute(
        select(WorkJournal).where(WorkJournal.username == username)
        .where(WorkJournal.week_start == week)
    )).scalar_one_or_none()


@router.get("/mine")
async def my_journal(request: Request, start: str = ""):
    """本人某週日誌（無記錄回各區空陣列）。"""
    payload = check_admin_or_module(request, "journal")
    username = (payload or {}).get("sub") or ""
    week = _week_from_param(start)
    factory = db_factory_or_503()
    async with factory() as session:
        shell = await _get_shell(session, username, week)
        sections = {key: [] for key, _ in _SECTION_MODELS}
        if shell is not None:
            sections = (await _entries_by_journal(session, [shell.id]))[shell.id]
    return {"week_start": week.isoformat(), **sections,
            "editable": editable_window_ok(week)}


@router.put("/mine")
async def put_my_journal(body: JournalPut, request: Request, start: str = ""):
    """全量替換四區 entries（刪舊插新，sort_order=列表順序）；四區皆空＝刪掉
    整份週誌（殼+entries，語意=清空）。回應同 GET 形狀。"""
    payload = check_admin_or_module(request, "journal")
    username = (payload or {}).get("sub") or ""
    week = _week_from_param(start)
    if not editable_window_ok(week):
        raise HTTPException(status_code=403, detail="只能編輯到下一週")
    cleaned = {}
    for key, _ in _SECTION_MODELS:
        entries, err = clean_entries(getattr(body, key))
        if err:
            raise HTTPException(status_code=400, detail=f"{key}：{err}")
        cleaned[key] = entries
    factory = db_factory_or_503()
    async with factory() as session:
        shell = await _get_shell(session, username, week)
        if not any(cleaned.values()):
            # 四區皆空 → 刪殼 + entries（清空語意）
            if shell is not None:
                for _, model in _SECTION_MODELS:
                    await session.execute(
                        sa_delete(model).where(model.journal_id == shell.id))
                await session.delete(shell)
                await session.commit()
        else:
            if shell is None:
                shell = WorkJournal(id=uuid.uuid4().hex, username=username,
                                    week_start=week)
                session.add(shell)
                await session.flush()
            else:
                # 殼欄位沒變時 onupdate 不觸發 — 內容更新一律手動戳
                shell.updated_at = datetime.now(timezone.utc)
            for key, model in _SECTION_MODELS:
                await session.execute(
                    sa_delete(model).where(model.journal_id == shell.id))
                for i, content in enumerate(cleaned[key]):
                    session.add(model(id=uuid.uuid4().hex, journal_id=shell.id,
                                      content=content, sort_order=i))
            await session.commit()
    return {"week_start": week.isoformat(), **cleaned,
            "editable": editable_window_ok(week)}


@router.get("/week")
async def week_journals(request: Request, start: str = ""):
    """該週有寫的人（username 排序）— 全員可讀。"""
    check_admin_or_module(request, "journal")
    week = _week_from_param(start)
    factory = db_factory_or_503()
    async with factory() as session:
        shells = (await session.execute(
            select(WorkJournal).where(WorkJournal.week_start == week)
            .order_by(WorkJournal.username)
        )).scalars().all()
        entries = await _entries_by_journal(session, [s.id for s in shells])
        names = await _display_names(session, {s.username for s in shells})
        return {"week_start": week.isoformat(), "journals": [{
            "username": s.username,
            "display_name": names[s.username],
            **entries[s.id],
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        } for s in shells]}


@router.get("/learnings")
async def learning_library(request: Request, q: str = "", username: str = "",
                           limit: int = 100, offset: int = 0):
    """學習庫 — 跨週彙整「學到了什麼」（week_start DESC、sort_order ASC；
    q = content ILIKE 模糊；limit 上限 300）。"""
    check_admin_or_module(request, "journal")
    limit = max(1, min(int(limit or 100), 300))
    offset = max(0, int(offset or 0))
    factory = db_factory_or_503()
    async with factory() as session:
        base = (select(JournalLearning, WorkJournal.username, WorkJournal.week_start)
                .join(WorkJournal, WorkJournal.id == JournalLearning.journal_id))
        if (q or "").strip():
            base = base.where(JournalLearning.content.ilike(f"%{q.strip()}%"))
        if (username or "").strip():
            base = base.where(WorkJournal.username == username.strip())
        total = (await session.execute(
            select(func.count()).select_from(base.subquery())
        )).scalar() or 0
        rows = (await session.execute(
            base.order_by(WorkJournal.week_start.desc(),
                          JournalLearning.sort_order)
            .offset(offset).limit(limit)
        )).all()
        names = await _display_names(session, {r[1] for r in rows})
        return {"items": [{
            "username": r[1], "display_name": names[r[1]],
            "week_start": r[2].isoformat(),
            "content": r[0].content or "",
        } for r in rows], "total": int(total)}


@router.get("/people")
async def journal_people(request: Request):
    """有寫過週誌的人：{username, weeks=寫過幾週, last_week=最近一週}。"""
    check_admin_or_module(request, "journal")
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(WorkJournal.username, func.count(WorkJournal.id),
                   func.max(WorkJournal.week_start))
            .group_by(WorkJournal.username)
            .order_by(WorkJournal.username)
        )).all()
        names = await _display_names(session, {u for u, _, _ in rows})
        return {"people": [{
            "username": u, "display_name": names[u], "weeks": int(n or 0),
            "last_week": lw.isoformat() if lw else None,
        } for u, n, lw in rows]}


@router.get("/person")
async def person_journals(request: Request, username: str = "", limit: int = 26):
    """某人的歷週日誌（week DESC）。"""
    check_admin_or_module(request, "journal")
    username = (username or "").strip()
    if not username:
        raise HTTPException(status_code=422, detail="username 必填")
    limit = max(1, min(int(limit or 26), 300))
    factory = db_factory_or_503()
    async with factory() as session:
        shells = (await session.execute(
            select(WorkJournal).where(WorkJournal.username == username)
            .order_by(WorkJournal.week_start.desc()).limit(limit)
        )).scalars().all()
        entries = await _entries_by_journal(session, [s.id for s in shells])
        return {"journals": [{
            "week_start": s.week_start.isoformat(),
            **entries[s.id],
        } for s in shells]}
