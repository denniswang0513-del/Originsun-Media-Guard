"""services/timesheet_self.py — 「自己的工作項」的守衛、序列化、讀改刪（own-scope 共用件）。

routers/api_me.py（員工端 /my.html）與 routers/api_timesheets.py（CRM tab 的「我的一天」）
都走這裡：兩邊只差守衛用的模組鑰匙。own-scope 一律經 core.identity.resolve_current_staff
（token → users.staff_id），絕不接受 client 傳的 staff_id。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import or_, select

from core.hr_logic import (EDIT_BLOCK_TEXT, by_month, can_edit_timesheet, day_iso, merge_plan, month_span, resolve_stage,
                           stage_index, tw_day)
from services.timesheet_lookup import load_project_lookup
from services.timesheet_manual import insert_manual_rows, names_for, normalize_row

# can_edit_timesheet 的代碼 → HTTP 狀態：不是你的＝403，其餘（Sheet 列／已鎖）＝409
_BLOCK_STATUS = {"not_owner": 403, "not_manual": 409, "locked": 409}


def ts_dict(r, staff_id: str | None = None, *, with_note: bool = False) -> dict:
    """一列 Timesheet 的唯一序列化（看板／時間軸／人員逐日／我的一天都吃這個）。
    給 `staff_id` 才算 `editable`（own-scope 視角）；管理員備註 `note` 只在 with_note（管理員端點）
    才進 JSON —— 員工頁與團隊頁不該讀得到主管寫的話。"""
    out = {
        "id": r.id,
        "date": day_iso(r.work_date) or "",
        "staff_name": r.staff_name or "",
        "project_name": r.project_name or "",
        "project_id": r.project_id or "",
        "task_note": r.task_note or "",
        "remark": r.remark or "",
        "start_time": getattr(r, "start_time", None) or "",   # 格子的起／訖
        "end_time": getattr(r, "end_time", None) or "",
        "hours": round(float(r.hours or 0), 2),
        "planned_hours": round(float(r.planned_hours), 2) if r.planned_hours is not None else None,
        "work_type": r.work_type or "",
        "stage_id": getattr(r, "stage_id", None) or "",        # 工作階段（§12）；stage_name 是鏡射，只由 set_stage 寫
        "stage_name": getattr(r, "stage_name", None) or "",
        "bulletin_id": getattr(r, "bulletin_id", None) or "",  # 從待辦帶入的那筆
        "source": r.source or "",
        "status": r.status or "",
        "edited": r.edited_at is not None,      # 總表改過（Sheet 同格再變會記衝突）
    }
    if with_note:
        out["note"] = r.note or ""
    if staff_id is not None:
        out["editable"] = can_edit_timesheet(r, staff_id) == ""
    return out


def month_or_422(request_month: str):
    """'YYYY-MM'（空＝本月）→ (月初, 下月初)；格式錯 → 422。兩個 router 共用。"""
    try:
        return month_span(request_month)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


def metrics_input(rows) -> list:
    """Timesheet 列 → project_metrics 的輸入（日、人、分類、小時）；計畫列由 project_metrics 自己略過。"""
    return [(tw_day(r.work_date), r.staff_name, r.work_type, r.hours) for r in rows]


def rows_by_month(rows) -> list:
    """Timesheet 列 → [('YYYY-MM', 小時), …]（專案各月／團隊各月同一支）。"""
    return by_month((tw_day(r.work_date), r.hours) for r in rows)



def own_filter(ident: dict):
    """「本人的列」的 WHERE：認 staff_id；舊 Sheet 列若 staff_id 空則退回姓名比對。
    列表（own_rows）與 /my.html 工作台的合計都用這一條，不各自寫一種 own-scope。"""
    from db.models import Timesheet
    return or_(Timesheet.staff_id == ident["staff_id"], Timesheet.staff_name == ident["staff"].name)


def own_rows(ident: dict):
    from db.models import Timesheet
    return select(Timesheet).where(own_filter(ident))


async def get_row(session, row_id: str):
    from db.models import Timesheet
    r = await session.get(Timesheet, row_id)
    if r is None:
        raise HTTPException(status_code=404, detail="找不到這一列")
    return r


async def own_row(session, row_id: str, ident: dict):
    """撈一列並驗「本人＋手填＋未鎖」；不是就 403/409（代碼→狀態，文字給 detail）。"""
    r = await get_row(session, row_id)
    code = can_edit_timesheet(r, ident["staff_id"])
    if code:
        raise HTTPException(status_code=_BLOCK_STATUS[code], detail=EDIT_BLOCK_TEXT[code])
    return r


async def list_rows(session, ident: dict, d0, d1) -> list:
    """本人 [d0, d1) 的列，新的在前，含 editable。"""
    from db.models import Timesheet
    rows = (await session.execute(
        own_rows(ident).where(Timesheet.work_date >= d0).where(Timesheet.work_date < d1)
        .order_by(Timesheet.work_date.desc(), Timesheet.created_at.desc()))).scalars().all()
    return [ts_dict(r, ident["staff_id"]) for r in rows]


async def search_rows(session, ident: dict, d0, d1, *, q: str = "", project_id: str = "", stage_id: str = "",
                      limit: int = 500) -> list:
    """本人 [d0, d1) 的列加篩選（關鍵字＝內容／備註／案名、案、階段）—— **只列不算**（§2-A11）。"""
    from db.models import Timesheet
    stmt = own_rows(ident).where(Timesheet.work_date >= d0).where(Timesheet.work_date < d1)
    if (q or "").strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Timesheet.task_note.ilike(like), Timesheet.remark.ilike(like),
                              Timesheet.project_name.ilike(like)))
    if (project_id or "").strip():
        stmt = stmt.where(Timesheet.project_id == project_id.strip())
    if (stage_id or "").strip():
        stmt = stmt.where(Timesheet.stage_id == stage_id.strip())
    rows = (await session.execute(
        stmt.order_by(Timesheet.work_date.desc(), Timesheet.created_at.desc()).limit(max(1, min(int(limit or 500), 500)))
    )).scalars().all()
    return [ts_dict(r, ident["staff_id"]) for r in rows]


# ── 工作階段（§12）＋ 待辦互連（§2-C4）────────────────────────────────────

def set_stage(row, node) -> None:
    """timesheets.stage_id／stage_name **唯一的寫入點**：node＝resolve_stage 回的 dict，None＝清空。
    鏡射欄只能從這裡動，報表／Sheet 匯出／週記自動區讀的都是 stage_name。"""
    row.stage_id = node["id"] if node else None
    row.stage_name = node["name"] if node else None


async def load_stage_index(session) -> dict:
    from db.models import WorkStageNode
    return stage_index((await session.execute(select(WorkStageNode))).scalars().all())


def _stage_or_422(index: dict, stage_id, work_type):
    try:
        return resolve_stage(stage_id, work_type, index)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


async def _mark_bulletin_done(session, row, username: str) -> None:
    """列標成實際（hours>0）且是從待辦帶入的 → 那筆公布欄 done（只動「與我有關」的那筆）。
    同一交易內做、失敗不擋工時（savepoint 回滾，工時照存）。"""
    if not getattr(row, "bulletin_id", None) or float(row.hours or 0) <= 0:
        return
    from db.models import BulletinItem
    try:
        async with session.begin_nested():
            b = (await session.execute(
                select(BulletinItem).where(BulletinItem.id == row.bulletin_id)
                .where(BulletinItem.mine_filter(username or "")))).scalar_one_or_none()
            if b is not None and b.status != "done":
                b.status = "done"
                b.done_at = datetime.now()
    except Exception:
        pass


async def add_rows(session, ident: dict, rows) -> dict:
    """本人一次填多列（實際或計畫；規則在 insert_manual_rows）。caller 不必 commit。
    階段先驗（不屬於該列分類 → 422，一列都不插）、插完再鏡射；帶 bulletin_id 且是實際列＝待辦 done。"""
    from db.models import Timesheet
    rows = list(rows or [])
    index = await load_stage_index(session) if any((getattr(r, "stage_id", None) or "").strip() for r in rows) else {}
    stages = [_stage_or_422(index, getattr(r, "stage_id", None), (r.work_type or "").strip()) for r in rows]
    result = await insert_manual_rows(session, staff_id=ident["staff_id"], staff_name=ident["staff"].name, rows=rows)
    await session.flush()
    for rid, r, st in zip(result["ids"], rows, stages):
        obj = await session.get(Timesheet, rid)
        set_stage(obj, st)
        obj.bulletin_id = (getattr(r, "bulletin_id", None) or "").strip() or None
        await _mark_bulletin_done(session, obj, ident.get("username") or "")
    await session.commit()
    return result


async def apply_update(session, r, body) -> None:
    """把 body（TimesheetManualRow 形狀）套到一列，員工改自己的（update_row）與管理員總表
    （admin_update_row）同一份。手填列：實際或計畫至少一個 > 0、status 重算；Sheet 列：不套那條、
    status 保留 import。案名沒動就沿用原對映（不載查表）；動了才重新對映。不 commit。"""
    # 「body 沒帶的欄位＝沿用列上的」只有這一條規則：格子沒那欄（計畫 h 拿掉了）、/my.html 改列表單沒備註欄、
    # 手機表單沒起訖……都靠它；有帶（含明確 null／""）才照 body。專案兩欄都沒帶也一樣。
    sent = getattr(body, "model_fields_set", set())
    carry = {k: getattr(r, k) for k in ("planned_hours", "remark", "start_time", "end_time", "hours", "task_note", "work_type")
             if k not in sent and getattr(r, k, None) is not None}
    if not body.project_id and not (body.project_name or "").strip() and (r.project_name or r.project_id):
        carry["project_name"] = r.project_name or ""
    if "plan" not in sent and r.status == "plan":     # 「我的一週」排的卡：改內容／挪日期還是計畫，填了時數才變實際
        carry["plan"] = True
    if carry:
        body = body.model_copy(update=carry)
    unchanged = not body.project_id and (body.project_name or "").strip() == (r.project_name or "")
    lk = None if unchanged else await load_project_lookup(session)
    fields, _why = normalize_row(body, lk, await names_for(session, [body]), manual=r.source == "manual",
                                 keep=(r.project_name or "", r.project_id) if unchanged else None)
    if fields["status"] is None:
        fields.pop("status")
    for k, v in fields.items():
        setattr(r, k, v)
    # 對不到案的名字不擋（project_id 空 → 前端標「未對映」，管理員再指定），跟插入同一規則
    # 工作階段：給了就以它為準（"" ＝清空）；沒給但分類換了、原階段不屬於新分類 → 清空（鏡射不能對不上）
    sid = getattr(body, "stage_id", None)
    if sid is not None:
        set_stage(r, _stage_or_422(await load_stage_index(session), sid, r.work_type or ""))
    elif getattr(r, "stage_id", None):
        st = (await load_stage_index(session)).get(r.stage_id)
        if st is None or st["category"] != (r.work_type or ""):
            set_stage(r, None)
    bid = getattr(body, "bulletin_id", None)
    if bid is not None:
        r.bulletin_id = bid.strip() or None


async def claim_sheet_row(session, r, who: str) -> None:
    """本人改 Sheet 同步／CSV 匯入的列（2026-09-07 同事回饋「上週的紀錄改不了」）：先留指紋（下次拉取不插回），
    再轉成手填列（source=manual、status=draft、新的 manual_ 指紋）。已是手填列就什麼都不做。"""
    if getattr(r, "source", "") == "manual":
        return
    await add_tombstone(session, r.row_hash, who, staff_name=r.staff_name, project_name=r.project_name,
                        work_date=r.work_date, hours=r.hours)
    r.source = "manual"
    r.status = "draft"
    r.row_hash = "manual_" + uuid.uuid4().hex     # sheet_key 留著沒關係：手填列拉取不會再比它


async def update_row(session, ident: dict, row_id: str, body) -> dict:
    r = await own_row(session, row_id, ident)
    await claim_sheet_row(session, r, ident.get("username") or "")
    await apply_update(session, r, body)
    await _mark_bulletin_done(session, r, ident.get("username") or "")
    await session.commit()
    return ts_dict(r, ident["staff_id"])


async def delete_row(session, ident: dict, row_id: str) -> dict:
    r = await own_row(session, row_id, ident)
    if r.source != "manual":      # Sheet 列本人刪：留指紋，拉取不插回
        await add_tombstone(session, r.row_hash, ident.get("username") or "", staff_name=r.staff_name,
                            project_name=r.project_name, work_date=r.work_date, hours=r.hours)
    await session.delete(r)
    await session.commit()
    return {"deleted": row_id}


# ── 合併同案（owner 2026-09-07）：規則在 core.hr_logic.merge_plan；這裡做 I/O＋合併紀錄（可復原）──

_SNAP_COLS = ("id", "work_date", "staff_name", "staff_id", "project_id", "project_name", "task_note", "hours", "planned_hours",
              "work_type", "note", "remark", "stage_id", "stage_name", "bulletin_id", "sheet_key", "start_time", "end_time",
              "edited_at", "edited_by", "status", "source", "row_hash", "created_at")
_SNAP_DT = ("work_date", "edited_at", "created_at")


def _snap(r) -> dict:
    """整列存進 JSON（datetime → isoformat）；復原時 _unsnap 原樣放回、id 不變（待辦連結、指紋都保得住）。"""
    out = {}
    for k in _SNAP_COLS:
        v = getattr(r, k, None)
        out[k] = v.isoformat() if isinstance(v, datetime) else v
    return out


def _unsnap(d: dict) -> dict:
    out = dict(d)
    for k in _SNAP_DT:
        if out.get(k):
            out[k] = datetime.fromisoformat(out[k])
    return out


async def merge_day(session, ident: dict, day: datetime, *, dry_run: bool) -> dict:
    """本人某日的列照 merge_plan 併。dry_run 只回預覽（groups／skipped）；正式跑＝改本體（時數相加、內容去重、起訖清空）、
    刪被併的、記一筆 TimesheetMergeLog，同一個交易。回 {dry_run, log_id?, groups, skipped, rows}。"""
    from db.models import TimesheetMergeLog
    d1 = day + timedelta(days=1)
    plan = merge_plan(await list_rows(session, ident, day, d1))
    if dry_run or not plan["groups"]:
        return {"dry_run": True, "log_id": None, **plan}
    snapshot = []
    for g in plan["groups"]:
        kept = await own_row(session, g["kept_id"], ident)
        absorbed = [await own_row(session, i, ident) for i in g["absorbed_ids"]]
        snapshot.append({"kept": _snap(kept), "absorbed": [_snap(a) for a in absorbed]})
        who = ident.get("username") or ""
        await claim_sheet_row(session, kept, who)              # Sheet 列被併：本體轉手填、被併的留指紋
        for a in absorbed:
            if a.source != "manual":
                await add_tombstone(session, a.row_hash, who, staff_name=a.staff_name, project_name=a.project_name,
                                    work_date=a.work_date, hours=a.hours)
        kept.hours = g["hours"]
        kept.task_note = g["task_note"] or None
        kept.remark = g["remark"] or None
        kept.start_time = None
        kept.end_time = None
        for a in absorbed:
            await session.delete(a)
    log = TimesheetMergeLog(id=uuid.uuid4().hex, staff_id=ident["staff_id"], work_date=day, groups=len(snapshot), snapshot=snapshot)
    session.add(log)
    await session.commit()
    return {"dry_run": False, "log_id": log.id, **plan, "rows": await list_rows(session, ident, day, d1)}


async def undo_merge(session, ident: dict, log_id: str) -> dict:
    """復原一次合併：本體還原成併前的樣子（合併後對它的修改會被蓋掉）、被併掉的列照原 id 放回去。已復原過的 409。"""
    from db.models import Timesheet, TimesheetMergeLog
    log = await session.get(TimesheetMergeLog, log_id)
    if log is None or log.staff_id != ident["staff_id"]:
        raise HTTPException(status_code=404, detail="找不到這筆合併紀錄")
    if log.undone_at is not None:
        raise HTTPException(status_code=409, detail="這筆合併已經復原過了")
    for g in log.snapshot or []:
        k = _unsnap(g["kept"])
        kept = await session.get(Timesheet, k["id"])
        if kept is None:
            session.add(Timesheet(**k))
        else:
            for col, v in k.items():
                if col != "id":
                    setattr(kept, col, v)
        for a in g.get("absorbed") or []:
            d = _unsnap(a)
            if await session.get(Timesheet, d["id"]) is None:
                session.add(Timesheet(**d))
    log.undone_at = datetime.now(timezone.utc)
    await session.commit()
    day = log.work_date
    return {"log_id": log.id, "rows": await list_rows(session, ident, day, day + timedelta(days=1))}


async def last_merge(session, ident: dict, day: datetime):
    """那一天最近一筆還沒復原的合併（給「復原合併」鈕決定要不要出現）；沒有回 None。"""
    from db.models import TimesheetMergeLog
    log = (await session.execute(
        select(TimesheetMergeLog).where(TimesheetMergeLog.staff_id == ident["staff_id"])
        .where(TimesheetMergeLog.work_date >= day).where(TimesheetMergeLog.work_date < day + timedelta(days=1))
        .where(TimesheetMergeLog.undone_at.is_(None))
        .order_by(TimesheetMergeLog.created_at.desc()).limit(1))).scalar_one_or_none()
    if log is None:
        return None
    return {"log_id": log.id, "groups": log.groups, "created_at": log.created_at.isoformat() if log.created_at else None}


# ── 總表（管理員）：任一列都能調，含 Sheet 列；守衛在端點（check_admin）──

async def add_tombstone(session, row_hash: str, who: str, *, staff_name="", project_name="", work_date=None, hours=0) -> None:
    """留 Sheet 列指紋：下次拉取不插回（總表刪列、衝突選「用總表的」都走這）。已有就不重複。"""
    from db.models import TimesheetTombstone
    if not await session.get(TimesheetTombstone, row_hash):
        session.add(TimesheetTombstone(row_hash=row_hash, staff_name=staff_name or "", project_name=project_name or "",
                                       work_date=work_date, hours=float(hours or 0), deleted_by=who or ""))

def _remember_sheet_key(r) -> None:
    """Sheet 列第一次被總表改之前，把（人,日,案）原鍵記下來：之後管理員改了案名／人／日期，
    拉取還認得 Sheet 那一列是它（不然 Sheet 再變就插成第二列、時數加倍）。"""
    if r.source != "manual" and not getattr(r, "sheet_key", None):
        from core.hr_logic import sheet_key_of
        r.sheet_key = sheet_key_of(r.staff_name, r.work_date, r.project_name)   # VARCHAR 欄：存字串，不是 tuple


async def admin_update_row(session, row_id: str, body, who: str = "") -> dict:
    """管理員改任一列（欄位同 apply_update）＋ 管理員備註。改過的 Sheet 列 row_hash 不變，
    下次拉取仍認得它、不會再插一次；但 Sheet 那一格之後若也改了，會以新 hash 另插一列。"""
    r = await get_row(session, row_id)
    _remember_sheet_key(r)
    await apply_update(session, r, body)
    if body.note is not None:
        r.note = body.note.strip() or None
    if r.source != "manual":                 # Sheet 列被總表改過：之後 Sheet 同格再變就記衝突（ingest 看 edited_at）
        r.edited_at, r.edited_by = datetime.now(timezone.utc), who or None
    await session.commit()
    return {**ts_dict(r, with_note=True), "editable": True}


async def admin_batch_update(session, ids: list, patch: dict, who: str = "") -> dict:
    """勾選的列一次改（owner 2026-09-03「批次調整」）：只動 patch 有給的欄。專案給名字就走同一支
    resolve_project（對不到就留 NULL、名字照存），給 id 就用 id；Sheet 列一樣標 edited_at。"""
    from core.hr_logic import norm_work_type, resolve_project
    from db.models import Timesheet
    from services.timesheet_lookup import project_names
    ids = [i for i in (ids or []) if i]
    if not ids:
        raise HTTPException(status_code=422, detail="沒有勾任何列")
    proj = None
    if patch.get("project_id"):
        pid = patch["project_id"]
        proj = (pid, (await project_names(session, [pid])).get(pid, "") or (patch.get("project_name") or "").strip())
    elif (patch.get("project_name") or "").strip():
        pname = patch["project_name"].strip()
        pid, _why = resolve_project(pname, await load_project_lookup(session))
        proj = (pid, pname)
    wt = None
    if "work_type" in patch and patch["work_type"] is not None:
        try:
            wt = norm_work_type(patch["work_type"])
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    rows = (await session.execute(select(Timesheet).where(Timesheet.id.in_(ids)))).scalars().all()
    for r in rows:
        _remember_sheet_key(r)
        if proj:
            r.project_id, r.project_name = proj
        if "work_type" in patch and patch["work_type"] is not None:
            r.work_type = wt
        if patch.get("remark") is not None:
            r.remark = patch["remark"].strip() or None
        if patch.get("note") is not None:
            r.note = patch["note"].strip() or None
        if r.source != "manual":
            r.edited_at, r.edited_by = datetime.now(timezone.utc), who or None
    await session.commit()
    return {"status": "ok", "updated": len(rows)}


async def admin_delete_row(session, row_id: str, who: str = "") -> dict:
    """管理員刪任一列。Sheet 拉進來的列另留指紋（TimesheetTombstone），下次拉取不再插回來 ——
    總表為準（owner 2026-09-03）。手填列沒有 Sheet 對應，不用留。"""
    r = await get_row(session, row_id)
    if r.source != "manual":
        await add_tombstone(session, r.row_hash, who, staff_name=r.staff_name, project_name=r.project_name,
                            work_date=r.work_date, hours=r.hours)
    await session.delete(r)
    await session.commit()
    return {"deleted": row_id, "tombstoned": r.source != "manual"}


async def board_days(session, d0: datetime, days: int) -> list:
    """看板的計算（唯一一份）：[{date, people:[{name, items, hours, planned}]}]。
    /board 與員工頁的 /me/team_week 都吃這個 —— 團隊的一週不抄第二份。"""
    from db.models import Timesheet
    d1 = d0 + timedelta(days=days)
    rows = (await session.execute(
        select(Timesheet).where(Timesheet.work_date >= d0).where(Timesheet.work_date < d1)
        .order_by(Timesheet.work_date, Timesheet.staff_name, Timesheet.created_at)
    )).scalars().all()
    by_day: dict = {}
    for r in rows:
        it = ts_dict(r)
        by_day.setdefault(it["date"], {}).setdefault(it["staff_name"] or "(空白)", []).append(it)
    out_days = []
    for i in range(days):
        k = (d0 + timedelta(days=i)).date().isoformat()
        people = [{"name": n, "items": its, "hours": round(sum(x["hours"] for x in its), 1),
                   "planned": round(sum(x["planned_hours"] or 0 for x in its), 1)}
                  for n, its in sorted(by_day.get(k, {}).items())]
        out_days.append({"date": k, "people": people})
    return out_days
