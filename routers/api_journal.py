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

2026-09-05（docs/JOURNAL_WORKLOG_PLAN.md §13–§14）：
- 草稿→送出：PUT /mine 只存草稿（status=draft；已送出的再改不降回草稿），POST /mine/submit 送出。
  /week、/person、/learnings、/people、/help **只回 submitted** —— 草稿只有本人（/mine）看得到。
- 「上週做了什麼」自動區：本人該週 timesheets 按案子分組（core.journal_logic.group_worklog，不含 hours）。
- 條目可掛案子（project_id）、「挑戰」「其他」可標 help／discuss；主管（timesheets 模組）逐條回覆
  （journal_replies）。回覆不推通知（owner 2026-09-05 先拿掉；要加回來走個人通道）。
- PUT 全量替換時盡量沿用條目 id（帶 id 或內容相同）—— 回覆才跟得住自動存草稿。

權限：check_admin_or_module(request, 'journal')；回覆／求助清單另守 'timesheets'。純函式在 core/journal_logic.py。
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request  # type: ignore
from sqlalchemy import delete as sa_delete, func, or_, select  # type: ignore

from core.auth import check_admin_or_module
from core.db_guard import db_factory_or_503
from core.hr_logic import iso_ts, midnight_of, parse_ymd
from core.journal_logic import (REACTION_KINDS, FLAG_SECTIONS, clean_rich_entries, editable_window_ok, flag_counts,
                                group_worklog, norm_reaction, reaction_summary, shell_status, status_after_put,
                                unanswered_flagged, week_start_of)
from core.schemas import JournalPut, JournalReactPost, JournalReplyPost
from db.models import (CrmStaff, JournalChallenge, JournalLearning, JournalOther, JournalReaction, JournalReply,
                       JournalWin, Timesheet, User, WorkJournal)
from services.timesheet_lookup import project_names
from services.timesheet_self import ts_dict

router = APIRouter(prefix="/api/v1/journal", tags=["journal"])

# 四區塊 ↔ 表 對映。key 集合必須與 JournalPut 四區欄位、前端 journal-core.js 的
# BLOCKS 一致（守衛：tests/unit/test_journal.py TestSectionRegistryConsistency）
# —— 前端漏 key 的後果是 PUT 全量替換把該區已存資料靜默清空，不是顯示問題。
_SECTION_MODELS = (("wins", JournalWin),
                   ("challenges", JournalChallenge),
                   ("learnings", JournalLearning),
                   ("others", JournalOther))
# 回覆用的 entry_table（表名）→ (區 key, model)
_TABLE_MODELS = {m.__tablename__: (key, m) for key, m in _SECTION_MODELS}


def _submitted_only():
    """只回送出的：status 空（migration 前的舊殼）視同 submitted。"""
    return or_(WorkJournal.status == "submitted", WorkJournal.status.is_(None))


def _week_from_param(start: str) -> date:
    """start 參數（YYYY-MM-DD；缺省=今天）→ 正規化為該週週一。壞格式 422。"""
    if not (start or "").strip():
        return week_start_of(date.today())
    dt = parse_ymd(start)
    if dt is None:
        raise HTTPException(status_code=422, detail="start 需為 YYYY-MM-DD")
    return week_start_of(dt.date())


_iso = iso_ts


def _empty_sections() -> dict:
    return {key: [] for key, _ in _SECTION_MODELS}


async def _entries_by_journal(session, journal_ids: list) -> dict:
    """{journal_id: {各區 key: [{id, content, project_id, flag}]}}（依 sort_order）。"""
    out = {jid: _empty_sections() for jid in journal_ids}
    if not journal_ids:
        return out
    for key, model in _SECTION_MODELS:
        rows = (await session.execute(
            select(model).where(model.journal_id.in_(journal_ids))
            .order_by(model.sort_order, model.id)
        )).scalars().all()
        for r in rows:
            out[r.journal_id][key].append({"id": r.id, "content": r.content or "",
                                           "project_id": r.project_id or None, "flag": r.flag or None})
    return out


def _strings(rich: dict) -> dict:
    """§13 之前的字串陣列形狀（journal-core personCard 還在用）—— 從 rich 導出，不另查。"""
    return {key: [it["content"] for it in rich.get(key, [])] for key, _ in _SECTION_MODELS}


async def _project_names_for(session, rich_list) -> dict:
    ids = {it["project_id"] for rich in rich_list for items in rich.values() for it in items if it.get("project_id")}
    return await project_names(session, ids) if ids else {}


def _with_names(rich: dict, pnames: dict) -> dict:
    return {key: [{**it, "project_name": pnames.get(it["project_id"], "") if it.get("project_id") else ""}
                  for it in items] for key, items in rich.items()}


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


async def _worklog_by_user(session, usernames, week: date) -> dict:
    """{username: worklog}（§2-B1）：本人列＝users.staff_id 或人員本名（同 timesheet_self.own_filter 的規則），
    該週 [週一, 下週一)，分組在 core.journal_logic.group_worklog（不含 hours）。"""
    usernames = list(usernames)
    out = {u: [] for u in usernames}
    if not usernames:
        return out
    people = (await session.execute(
        select(User.username, User.staff_id, CrmStaff.name)
        .outerjoin(CrmStaff, CrmStaff.id == User.staff_id)
        .where(User.username.in_(usernames)))).all()
    by_sid = {sid: u for u, sid, _n in people if sid}
    by_name = {n: u for u, _sid, n in people if n}
    if not by_sid and not by_name:
        return out
    d0 = midnight_of(week)
    d1 = d0 + timedelta(days=7)
    conds = []
    if by_sid:
        conds.append(Timesheet.staff_id.in_(list(by_sid)))
    if by_name:
        conds.append(Timesheet.staff_name.in_(list(by_name)))
    rows = (await session.execute(
        select(Timesheet).where(or_(*conds))
        .where(Timesheet.work_date >= d0).where(Timesheet.work_date < d1)
        .order_by(Timesheet.work_date, Timesheet.created_at))).scalars().all()
    per = {u: [] for u in usernames}
    for r in rows:
        u = by_sid.get(r.staff_id) or by_name.get(r.staff_name)
        if u in per:
            per[u].append(ts_dict(r))
    return {u: group_worklog(rs) for u, rs in per.items()}


async def _names_plus(session, names, usernames) -> dict:
    """端點已經查過的顯示名再補上還沒查到的（回覆／心情的人可能不在殼的名單裡）—— 一個請求最多兩趟。"""
    names = dict(names or {})
    missing = {u for u in usernames if u} - set(names)
    if missing:
        names.update(await _display_names(session, missing))
    return names


async def _replies_by_journal(session, journal_ids: list, names=None) -> dict:
    """{journal_id: [{id, entry_table, entry_id, username, display_name, content, created_at}]}（時間升冪）。"""
    out = {jid: [] for jid in journal_ids}
    if not journal_ids:
        return out
    rows = (await session.execute(
        select(JournalReply).where(JournalReply.journal_id.in_(journal_ids))
        .order_by(JournalReply.created_at, JournalReply.id))).scalars().all()
    names = await _names_plus(session, names, {r.username for r in rows})
    for r in rows:
        out[r.journal_id].append({
            "id": r.id, "entry_table": r.entry_table, "entry_id": r.entry_id,
            "username": r.username, "display_name": names.get(r.username, r.username),
            "content": r.content or "", "created_at": _iso(r.created_at),
        })
    return out


async def _reactions_by_journal(session, journal_ids: list, me: str, names=None) -> dict:
    """{journal_id: {entry_id: {"like": n, "love": n, "laugh": n, "mine": [kind]}}}（規則在 core.journal_logic.reaction_summary）。"""
    out = {jid: {} for jid in journal_ids}
    if not journal_ids:
        return out
    rows = (await session.execute(
        select(JournalReaction.journal_id, JournalReaction.entry_id, JournalReaction.username, JournalReaction.kind)
        .where(JournalReaction.journal_id.in_(journal_ids)).order_by(JournalReaction.created_at, JournalReaction.id))).all()
    names = await _names_plus(session, names, {u for _j, _e, u, _k in rows})
    by = {}
    for jid, eid, u, k in rows:
        by.setdefault(jid, []).append((eid, u, k))
    for jid, lst in by.items():
        summ = reaction_summary(lst, me)
        for d in summ.values():                       # 帳號 → 顯示名（頭像與浮層用；帳號不外露）
            d["names"] = {k: [names.get(u, u) for u in us] for k, us in d.pop("users", {}).items()}
        out[jid] = summ
    return out


async def _get_shell(session, username: str, week: date):
    return (await session.execute(
        select(WorkJournal).where(WorkJournal.username == username)
        .where(WorkJournal.week_start == week)
    )).scalar_one_or_none()


async def _mine_payload(session, username: str, week: date, shell) -> dict:
    rich = (await _entries_by_journal(session, [shell.id]))[shell.id] if shell is not None else _empty_sections()
    pnames = await _project_names_for(session, [rich])
    worklog = (await _worklog_by_user(session, [username], week))[username]
    replies = (await _replies_by_journal(session, [shell.id]))[shell.id] if shell is not None else []
    reactions = (await _reactions_by_journal(session, [shell.id], username))[shell.id] if shell is not None else {}
    return {"week_start": week.isoformat(), "id": shell.id if shell is not None else None, **_strings(rich),
            "entries": _with_names(rich, pnames),
            "status": shell_status(shell),
            "submitted_at": _iso(getattr(shell, "submitted_at", None)) if shell is not None else None,
            "worklog": worklog, "replies": replies, "reactions": reactions,
            "editable": editable_window_ok(week)}


@router.get("/mine")
async def my_journal(request: Request, start: str = ""):
    """本人某週日誌（無記錄回各區空陣列、status=none）＋ 自動區 worklog ＋ 主管回覆。"""
    payload = check_admin_or_module(request, "journal")
    username = (payload or {}).get("sub") or ""
    week = _week_from_param(start)
    factory = db_factory_or_503()
    async with factory() as session:
        shell = await _get_shell(session, username, week)
        return await _mine_payload(session, username, week, shell)


async def _upsert_section(session, model, journal_id: str, items: list) -> None:
    """一區的全量替換，但**盡量沿用 id**：帶 id 且還在 → 同一條；沒帶 id 就找內容相同、還沒被認領的
    既有條。沒對上的才是新條；剩下沒被認領的刪掉（連同掛在它上面的回覆）。"""
    existing = (await session.execute(
        select(model).where(model.journal_id == journal_id).order_by(model.sort_order, model.id))).scalars().all()
    by_id = {r.id: r for r in existing}
    unclaimed = list(existing)
    for i, it in enumerate(items):
        row = by_id.get(it.get("id") or "")
        if row is None or row not in unclaimed:
            row = next((r for r in unclaimed if (r.content or "") == it["content"]), None)
        if row is not None:
            unclaimed.remove(row)
        else:
            row = model(id=uuid.uuid4().hex, journal_id=journal_id)
            session.add(row)
        row.content = it["content"]
        row.project_id = it.get("project_id") or None
        row.flag = it.get("flag") or None
        row.sort_order = i
    for r in unclaimed:
        await session.execute(sa_delete(JournalReply).where(JournalReply.entry_id == r.id))
        await session.delete(r)


@router.put("/mine")
async def put_my_journal(body: JournalPut, request: Request, start: str = ""):
    """全量替換四區 entries（sort_order=列表順序；沿用 id 見 _upsert_section）；四區皆空＝刪掉
    整份週誌（殼+entries+回覆，語意=清空）。body.status 只接受 draft；未有殼＝建 draft。
    回應同 GET 形狀。"""
    payload = check_admin_or_module(request, "journal")
    username = (payload or {}).get("sub") or ""
    week = _week_from_param(start)
    if not editable_window_ok(week):
        raise HTTPException(status_code=403, detail="只能編輯到下一週")
    cleaned = {}
    for key, _ in _SECTION_MODELS:
        entries, err = clean_rich_entries(getattr(body, key), allow_flag=key in FLAG_SECTIONS)
        if err:
            raise HTTPException(status_code=400, detail=f"{key}：{err}")
        cleaned[key] = entries
    factory = db_factory_or_503()
    async with factory() as session:
        shell = await _get_shell(session, username, week)
        try:
            new_status = status_after_put(shell_status(shell) if shell is not None else None, body.status)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if not any(cleaned.values()):
            # 四區皆空 → 刪殼 + entries + 回覆 + 讚（清空語意；讚不刪會變孤兒列）
            if shell is not None:
                for _, model in _SECTION_MODELS:
                    await session.execute(sa_delete(model).where(model.journal_id == shell.id))
                await session.execute(sa_delete(JournalReply).where(JournalReply.journal_id == shell.id))
                await session.execute(sa_delete(JournalReaction).where(JournalReaction.journal_id == shell.id))
                await session.delete(shell)
                await session.commit()
            shell = None
        else:
            if shell is None:
                shell = WorkJournal(id=uuid.uuid4().hex, username=username, week_start=week, status=new_status)
                session.add(shell)
                await session.flush()
            else:
                # 殼欄位沒變時 onupdate 不觸發 — 內容更新一律手動戳
                shell.updated_at = datetime.now(timezone.utc)
                shell.status = new_status
            for key, model in _SECTION_MODELS:
                await _upsert_section(session, model, shell.id, cleaned[key])
            await session.commit()          # expire_on_commit=False：shell 還在，不必重抓
        return await _mine_payload(session, username, week, shell)


@router.post("/mine/submit")
async def submit_my_journal(request: Request, start: str = ""):
    """送出（status=submitted、submitted_at=now）；可重複送（只更新時間）。沒殼＝建一份空的送出殼。"""
    payload = check_admin_or_module(request, "journal")
    username = (payload or {}).get("sub") or ""
    week = _week_from_param(start)
    if not editable_window_ok(week):
        raise HTTPException(status_code=403, detail="只能編輯到下一週")
    now = datetime.now(timezone.utc)
    factory = db_factory_or_503()
    async with factory() as session:
        shell = await _get_shell(session, username, week)
        if shell is None:
            shell = WorkJournal(id=uuid.uuid4().hex, username=username, week_start=week)
            session.add(shell)
        shell.status = "submitted"
        shell.submitted_at = now
        shell.updated_at = now
        await session.commit()
    return {"week_start": week.isoformat(), "status": "submitted", "submitted_at": now.isoformat()}


async def _people_candidates(session) -> set:
    """「大家的回顧」該列的人＝**寫過週記的人**（任何一週有殼）。
    owner 2026-09-05「這三位不用寫」：光有 journal 模組不算（OriginsunFinance／staf 這類 service 帳號、老闆本人
    會永遠掛「還沒寫」）。第一次寫了之後才進名單。"""
    return {u for (u,) in (await session.execute(select(WorkJournal.username).distinct())).all() if u}


@router.get("/week")
async def week_journals(request: Request, start: str = ""):
    """該週**送出**的人（username 排序）— 全員可讀；每人帶自動區、flag 計數、條目（含 flag／案子）、回覆。
    people_status：還沒送出的人（draft＝草稿中、none＝還沒寫）。"""
    _who = (check_admin_or_module(request, "journal") or {}).get("sub") or ""
    week = _week_from_param(start)
    factory = db_factory_or_503()
    async with factory() as session:
        all_shells = (await session.execute(
            select(WorkJournal).where(WorkJournal.week_start == week)
            .order_by(WorkJournal.username)
        )).scalars().all()
        shells = [s for s in all_shells if shell_status(s) == "submitted"]
        drafts = {s.username for s in all_shells if shell_status(s) == "draft"}
        entries = await _entries_by_journal(session, [s.id for s in shells])
        pnames = await _project_names_for(session, list(entries.values()))
        submitted = {s.username for s in shells}
        pending = sorted(await _people_candidates(session) - submitted)
        names = await _display_names(session, submitted | set(pending))
        replies = await _replies_by_journal(session, [s.id for s in shells], names)     # 名字沿用上面查好的
        reactions = await _reactions_by_journal(session, [s.id for s in shells], _who, names)
        worklog = await _worklog_by_user(session, [s.username for s in shells], week)
        return {"week_start": week.isoformat(), "journals": [{
            "id": s.id,                      # 主管回覆要指定殼（POST /journal/reply 的 journal_id）
            "username": s.username,
            "display_name": names[s.username],
            **_strings(entries[s.id]),
            "entries": _with_names(entries[s.id], pnames),
            "flags": flag_counts(entries[s.id]),
            "worklog": worklog.get(s.username, []),
            "replies": replies[s.id],
            "reactions": reactions[s.id],
            "status": "submitted",
            "submitted_at": _iso(s.submitted_at),
            "updated_at": _iso(s.updated_at),
        } for s in shells], "people_status": [{
            "username": u, "display_name": names[u], "status": "draft" if u in drafts else "none",
        } for u in pending]}


@router.get("/learnings")
async def learning_library(request: Request, q: str = "", username: str = "",
                           limit: int = 100, offset: int = 0):
    """學習庫 — 跨週彙整「學到了什麼」（week_start DESC、sort_order ASC；
    q = content ILIKE 模糊；limit 上限 300）。只回送出的。"""
    check_admin_or_module(request, "journal")
    limit = max(1, min(int(limit or 100), 300))
    offset = max(0, int(offset or 0))
    factory = db_factory_or_503()
    async with factory() as session:
        base = (select(JournalLearning, WorkJournal.username, WorkJournal.week_start)
                .join(WorkJournal, WorkJournal.id == JournalLearning.journal_id)
                .where(_submitted_only()))
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
            "project_id": r[0].project_id or None,
        } for r in rows], "total": int(total)}


@router.get("/people")
async def journal_people(request: Request):
    """有送出過週誌的人：{username, weeks=送出幾週, last_week=最近一週}。"""
    check_admin_or_module(request, "journal")
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(WorkJournal.username, func.count(WorkJournal.id),
                   func.max(WorkJournal.week_start))
            .where(_submitted_only())
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
    """某人的歷週日誌（week DESC）。只回送出的。"""
    _who = (check_admin_or_module(request, "journal") or {}).get("sub") or ""
    username = (username or "").strip()
    if not username:
        raise HTTPException(status_code=422, detail="username 必填")
    limit = max(1, min(int(limit or 26), 300))
    factory = db_factory_or_503()
    async with factory() as session:
        shells = (await session.execute(
            select(WorkJournal).where(WorkJournal.username == username)
            .where(_submitted_only())
            .order_by(WorkJournal.week_start.desc()).limit(limit)
        )).scalars().all()
        entries = await _entries_by_journal(session, [s.id for s in shells])
        pnames = await _project_names_for(session, list(entries.values()))
        replies = await _replies_by_journal(session, [s.id for s in shells])
        reactions = await _reactions_by_journal(session, [s.id for s in shells], _who)
        return {"journals": [{
            "week_start": s.week_start.isoformat(),
            **_strings(entries[s.id]),
            "entries": _with_names(entries[s.id], pnames),
            "flags": flag_counts(entries[s.id]),
            "replies": replies[s.id],
            "reactions": reactions[s.id],
            "submitted_at": _iso(s.submitted_at),
        } for s in shells]}


# ── 主管面：回覆、求助清單（守衛＝timesheets 模組；owner §14 回覆全員可見）──────────────

# 主管回覆的即時通知：owner 2026-09-05 先拿掉（只在頁面上看到）。要加回來時走個人通道（通知偏好 D3），
# 不要再用全域 Chat webhook 廣播——那會把每個人的週記回覆都推到同一個群。




@router.post("/react")
async def react_entry(body: JournalReactPost, request: Request):
    """按心情（owner 2026-09-05：讚／愛心／笑）。entry_table 可以是四張條目表之一，或 "work_journals"（整份週記一個心情，
    entry_id＝journal_id）。一人對同一個目標只留一種：按同一種＝取消、按別種＝換掉。只能對送出的週記按。"""
    payload = check_admin_or_module(request, "journal")
    who = (payload or {}).get("sub") or ""
    kind = norm_reaction(body.kind)
    if not kind:
        raise HTTPException(status_code=422, detail="kind 只能是 " + "／".join(REACTION_KINDS))
    whole = body.entry_table == WorkJournal.__tablename__
    if not whole and body.entry_table not in _TABLE_MODELS:
        raise HTTPException(status_code=422, detail=f"entry_table 只能是：{'／'.join(_TABLE_MODELS)}／{WorkJournal.__tablename__}")
    factory = db_factory_or_503()
    async with factory() as session:
        shell = await session.get(WorkJournal, body.journal_id)
        if shell is None:
            raise HTTPException(status_code=404, detail="找不到這份週記")
        if shell.status not in (None, "submitted"):
            raise HTTPException(status_code=409, detail="還沒送出的週記不能按")
        if whole:
            target_id = shell.id
        else:
            _key, model = _TABLE_MODELS[body.entry_table]
            entry = await session.get(model, body.entry_id)
            if entry is None or entry.journal_id != shell.id:
                raise HTTPException(status_code=404, detail="找不到這一條（可能已被改掉）")
            target_id = entry.id
        mine = (await session.execute(
            select(JournalReaction).where(JournalReaction.entry_id == target_id)
            .where(JournalReaction.username == who))).scalars().all()
        same = next((r for r in mine if r.kind == kind), None)
        for r in mine:                       # 一人一個目標只留一種
            await session.delete(r)
        on = same is None
        if on:
            session.add(JournalReaction(id=uuid.uuid4().hex, journal_id=shell.id, entry_table=body.entry_table,
                                        entry_id=target_id, username=who, kind=kind))
        try:
            await session.commit()
        except Exception:                 # 連點兩下：兩個請求都讀到「沒按過」、都 INSERT → 撞 UNIQUE；當作已經按了
            await session.rollback()
            on = True
        summary = (await _reactions_by_journal(session, [shell.id], who))[shell.id].get(target_id) or \
            ({k: 0 for k in REACTION_KINDS} | {"mine": []})
    return {"status": "ok", "on": on, "entry_id": target_id, "reactions": summary}


@router.post("/reply")
async def reply_entry(body: JournalReplyPost, request: Request):
    """主管回覆某一條（不改原文）。entry_table＝四張條目表之一的表名；條目必須屬於那份週記。"""
    payload = check_admin_or_module(request, "timesheets")
    who = (payload or {}).get("sub") or ""
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="回覆內容必填")
    if body.entry_table not in _TABLE_MODELS:
        raise HTTPException(status_code=422, detail=f"entry_table 只能是：{'／'.join(_TABLE_MODELS)}")
    _key, model = _TABLE_MODELS[body.entry_table]
    factory = db_factory_or_503()
    async with factory() as session:
        shell = await session.get(WorkJournal, body.journal_id)
        if shell is None:
            raise HTTPException(status_code=404, detail="找不到這份週記")
        if shell.status not in (None, "submitted"):
            raise HTTPException(status_code=409, detail="還沒送出的週記不能回覆（草稿只有本人看得到）")
        entry = await session.get(model, body.entry_id)
        if entry is None or entry.journal_id != shell.id:
            raise HTTPException(status_code=404, detail="找不到這一條（可能已被改掉）")
        reply = JournalReply(id=uuid.uuid4().hex, journal_id=shell.id, entry_table=body.entry_table,
                             entry_id=entry.id, username=who, content=content)
        session.add(reply)
        await session.commit()
        await session.refresh(reply)
        names = await _display_names(session, {shell.username, who})
        out = {"id": reply.id, "journal_id": shell.id, "entry_table": reply.entry_table, "entry_id": reply.entry_id,
               "username": who, "display_name": names.get(who, who), "content": content,
               "created_at": _iso(reply.created_at)}
    return {"status": "ok", "reply": out}


@router.get("/help")
async def help_queue(request: Request, weeks: int = 8):
    """等我處理（§2-E2）：近 N 週**送出的**週記裡，標了 help／discuss 且還沒有任何回覆的條目。"""
    check_admin_or_module(request, "timesheets")
    weeks = max(1, min(int(weeks or 8), 52))
    since = week_start_of(date.today()) - timedelta(days=7 * (weeks - 1))
    factory = db_factory_or_503()
    async with factory() as session:
        shells = (await session.execute(
            select(WorkJournal).where(WorkJournal.week_start >= since).where(_submitted_only())
            .order_by(WorkJournal.week_start.desc(), WorkJournal.username))).scalars().all()
        entries = await _entries_by_journal(session, [s.id for s in shells])
        pnames = await _project_names_for(session, list(entries.values()))
        replied = {r for (r,) in (await session.execute(
            select(JournalReply.entry_id).where(JournalReply.journal_id.in_([s.id for s in shells])))).all()} \
            if shells else set()
        names = await _display_names(session, {s.username for s in shells})
        table_of = {key: m.__tablename__ for key, m in _SECTION_MODELS}
        items = []
        for s in shells:
            for it in unanswered_flagged(entries[s.id], replied):
                items.append({"journal_id": s.id, "username": s.username, "display_name": names[s.username],
                              "week_start": s.week_start.isoformat(), "entry_table": table_of[it["section"]],
                              "section": it["section"], "entry_id": it["id"], "content": it["content"],
                              "flag": it["flag"], "project_id": it.get("project_id"),
                              "project_name": pnames.get(it.get("project_id") or "", "")})
        return {"since": since.isoformat(), "weeks": weeks, "items": items}
