# -*- coding: utf-8 -*-
"""零用金請款（docs/PETTY_CASH_PLAN.md）—— `/api/v1/crm/petty/*`。

掛在 CRM 的 `router` 上，因此自動繼承兩件事，**不另建守衛**：
  1. `_crm_read_guard` —— 要登入
  2. `MoneyRedactRoute` —— 沒 `money_view` 的人金額欄位被抹

🔴 但「自己的錢」不受 `money_view` 管（PETTY_CASH_PLAN §4）。這件事**靠 scope
而不是靠抹鍵**達成：`/petty/me` 這一族的查詢一律 `WHERE staff_id = 我`，
`staff_id` 只從 token 解（`resolve_current_staff`），永不接受 client 傳進來的值。
於是「看得到的都是自己的」是查詢的性質，不需要也不該在抹除層開洞。
（`actual` / `estimated` 目前落在 `core.money._PENDING_OWNER`，本來就沒被抹 ——
所以**別**依賴抹除層來擋別人的錢，擋住它的是 scope。）

財務視角（匯款清冊 / 審核 / 全部零用金帳冊）走另一組端點，
第一層 `money_dep` 403 + `finance_approve` 模組。
"""
from __future__ import annotations

import csv
import io
import uuid

from fastapi import Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func as safunc
from sqlalchemy import select

from core.project_link import PETTY_ITEMS as _PETTY_PROJECT_ITEMS
from ._shared import cash_category_texts
from core.auth import check_admin_or_module
from core.money import can_see_money
from core.identity import resolve_current_staff
from core.cash_taxonomy import SEP as _TAX_SEP
from core.cash_taxonomy import petty_item_for
from core.schemas import (PettyExpensePayload, PettyFromCashPayload,
                          PettySubmitPayload)
from core.ledger import not_mine, not_mine_project as _not_mine_project
from db.models import (Client, CrmCashEntry, CrmPaymentRequest, CrmProject,
                       CrmProjectCostGroup, CrmProjectExpense, CrmReimbursement,
                       CrmStaff, User)

from ._shared import (_assert_month_open, _crm_session, _fmt_day, _now,
                      _parse_day, _username, money_dep,
                      project_names_map, router)

# 送出後就不再是本人能改的東西；只有這兩個狀態算「還在我手上」
EDITABLE = ("草稿", "退回")

# 會計項目下拉的備援 —— 正本是 finance_category_map（source='cash'），
# 這份只在對映表空掉時頂著，免得現場的人連下拉都沒有。
FALLBACK_ITEMS = ("專案雜支", "行政", "設備耗材", "業務推廣", "建構",
                  "軟體網路服務", "後期雜支", "專案外包", "其他")

APPROVE_MODULE = "finance_approve"

# 🔴 只有「專案雜支」可以連結專案（owner 2026-08-17：「只有勾專案雜支時，那筆才
# 需要連結專案；其餘的項目不開放連結」）。行政／設備耗材／業務推廣那些是公司層級
# 支出，掛到專案上會讓專案毛利多算一筆不屬於它的錢。
# 值在 core/project_link.py（三個入口的答案放在一起，差異看得見）；前端經
# `/petty/options` 拿同一份，不各自寫死。
PROJECT_LINK_ITEMS = _PETTY_PROJECT_ITEMS


def _check_approver(request: Request):
    """審核／匯款：金額權限 + 審核模組。兩者都要。"""
    check_admin_or_module(request, APPROVE_MODULE)


async def _my_staff(request: Request):
    ident = await resolve_current_staff(request)
    if ident["staff"] is None:
        raise HTTPException(status_code=409,
                            detail="帳號尚未綁定人員檔案，請聯絡管理員")
    return ident["staff"]


def _enforce_project_link(exp, wants_link: bool = False) -> bool:
    """🔴 不變量：非「專案雜支」的單據不得掛專案（PROJECT_LINK_ITEMS）。

    在**賦值之後**檢查最終狀態，不在賦值前預測 —— 預測式守衛實際漏過兩個洞：
    PUT 整支沒檢查；PATCH `{"item": ""}` 讓連結留在 NULL 項目上（守衛拿舊項目
    判斷、賦值卻寫入 None）。所有會動到 project_id / item 的寫入路徑最後都要
    經過這一支。

    `wants_link=True`（這次請求明確要求連結）→ 409 並說明怎麼修；
    否則解除連結並回傳 True，呼叫端回報給前端 —— 靜默清掉的話，
    專案毛利會自己少一筆而沒有人知道為什麼。
    """
    if not exp.project_id or (exp.item or "") in PROJECT_LINK_ITEMS:
        return False
    if wants_link:
        raise HTTPException(
            status_code=409,
            detail=f"「{exp.item or '未分類'}」不開放連結專案。"
                   "要掛專案請先把項目改成「專案雜支」。")
    exp.project_id = None
    exp.cost_group_id = None
    return True


def _new_expense(body, staff_id: str) -> CrmProjectExpense:
    """單據建構的單一正本（本人登記 / 代為登記共用）。

    會計項目若在「項目 → 歸屬人」對映裡（例如後期雜支→王士源），
    建立時就把費用歸屬填好 —— 那條規則設定一次就好，不必每筆挑。
    專案連結的合法性不在這裡管 —— 呼叫端建完要過 `_enforce_project_link`。
    """
    item = body.item or "其他"
    return CrmProjectExpense(
        owner_staff_id=_item_owners().get(item) or None,
        id=uuid.uuid4().hex[:16],
        project_id=body.project_id or None,
        category=body.category or "其他",
        estimated=0, actual=body.actual,
        sub_item=body.summary or None,
        notes=body.notes or None,
        created_at=_now(),
        # 🔴 空日期就留空 —— Sheet 裡本來就有 37 列沒填日期，塞今天會製造假資料。
        # 手機/工作台的表單自己帶今天（value=today），所以只有刻意留白才會是 NULL。
        expense_date=_parse_day(body.expense_date),
        staff_id=staff_id,
        item=item,
        invoice_no=body.invoice_no or None,
        has_invoice=1 if body.invoice_no else 0,
        status="草稿",
    )


def _expense_dict(e: CrmProjectExpense, project_name: str = "") -> dict:
    return {
        "id": e.id,
        "expense_date": _fmt_day(e.expense_date),
        "actual": e.actual,
        "summary": e.sub_item or "",
        "item": e.item or "",
        "category": e.category or "",
        "staff_id": e.staff_id or "",
        "owner_staff_id": e.owner_staff_id or "",
        "owner_settled": bool(e.owner_settled),
        "project_id": e.project_id or "",
        "project_name": project_name,
        "project_label": e.project_label or "",
        "invoice_no": e.invoice_no or "",
        "has_invoice": bool(e.has_invoice),
        "receipt_url": e.receipt_url or "",
        "notes": e.notes or "",
        "status": e.status or "草稿",
        "claim_id": e.claim_id or "",
    }


def _claim_dict(c: CrmReimbursement) -> dict:
    return {
        "id": c.id,
        "staff_name": c.staff_name,
        "period_start": _fmt_day(c.period_start),
        "period_end": _fmt_day(c.period_end),
        "total_claim": c.total_claim,
        "opening_float": c.opening_float,
        "closing_float": c.closing_float,
        "status": c.status,
        "submitted_at": _fmt_day(c.submitted_at),
        "paid_at": _fmt_day(c.paid_at),
        "notes": c.notes or "",
    }


# ── 下拉選項 ────────────────────────────────────────────────────────
def _project_year(p) -> str:
    """專案的年份 —— 只認**專案自己的**日期，猜不到就留白。

    🔴 刻意不退到 created_at：生產庫 238 個專案裡有 217 個是 2026-05 那次匯入
    建立的，退到建立日等於幫 216 個舊專案全部標上「2026」——那是假資料，而且
    會讓「打 2025 找 2025 的案子」剛好濾不到。寧可空著（專案名常自帶年份，
    62 個是這樣），要顯示就去專案管理把起始日補上。
    """
    for d in (p.start_date, p.shoot_date, p.completion_date):
        if d:
            # timestamptz 面值取年份會踩台北 1/1 → 前一年 12/31 的同型坑，
            # 一律走 _fmt_day 歸一再切前四碼
            return _fmt_day(d)[:4]
    return ""


@router.get("/petty/options")
async def petty_options():
    """登記表單的兩個軸：專案（可留白＝公司支出）＋ 會計項目。

    專案帶年份與客戶 —— 238 個專案裡同名/近名的不少，光看名字挑不出來
    （owner 2026-08-18）。年份的取法見 `_project_year`（沒填日期的專案不硬猜）。
    """
    async with _crm_session() as session:
        # 🔴 選單不提供私帳案（core.ledger.hide_mine_projects）——
        # 私帳的案子不能跟公司請款（見 _assert_not_mine_project），提供出來
        # 只會讓人挑了才在送出時吃 409。
        _q = (
            select(CrmProject.id, CrmProject.name, CrmProject.status,
                   CrmProject.start_date, CrmProject.shoot_date,
                   CrmProject.completion_date, Client.short_name)
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .order_by(CrmProject.created_at.desc()).limit(400))
        # 私帳的案子不能跟公司請款 —— 這個選單一律不提供（不看 request：
        # 就算是帳本主人自己，也不該從這裡把私帳案掛到零用金單據上）
        _q = _q.where(not_mine(CrmProject.entity))
        projects = (await session.execute(_q)).all()
        items = await cash_category_texts(session)
    return {
        "projects": [{"id": p.id, "name": p.name, "status": p.status or "",
                      "year": _project_year(p), "client": p.short_name or ""}
                     for p in projects],
        "items": items or list(FALLBACK_ITEMS),
        # 前端據此決定專案欄可不可以編（規則的單一真相在後端）
        "project_link_items": list(PROJECT_LINK_ITEMS),
    }


# ── 我的請款（own-scope，不受 money_view 管）──────────────────────────
async def _petty_payload(session, staff) -> dict:
    """某個人的零用金現況。own-scope 與代管視圖共用 —— 兩份會漂。"""
    names = dict((await session.execute(
        select(CrmProject.id, CrmProject.name))).all())
    # 🔴 私帳專案的雜支不出現在零用金（owner 2026-08-28「已經轉到私帳的專案
    # 不能列入母公司的成本」）。登記時就擋掉了（_assert_not_mine_project），但
    # **專案是後來才搬過去的**那條路擋不到 —— 那張單會一直躺在他的可請款清單裡，
    # 而且整批送出時被 409 卡住、其他幾張跟著送不出去。它沒有不見：私帳逐案
    # 損益的「行政雜支明細」會列出來，由私帳自己付。
    rows = (await session.execute(
        select(CrmProjectExpense)
        .where(CrmProjectExpense.staff_id == staff.id,
               CrmProjectExpense.claim_id.is_(None),
               _not_mine_project(CrmProjectExpense))
        .order_by(CrmProjectExpense.expense_date.desc().nullslast())
    )).scalars().all()
    claims = (await session.execute(
        select(CrmReimbursement)
        .where(CrmReimbursement.staff_id == staff.id)
        .order_by(CrmReimbursement.created_at.desc()).limit(50)
    )).scalars().all()
    return {
        "staff": {"id": staff.id, "name": staff.name},
        "petty_float": staff.petty_float or 0,
        "pending_total": sum(r.actual or 0 for r in rows),
        "pending": [_expense_dict(r, names.get(r.project_id, "")) for r in rows],
        "claims": [_claim_dict(c) for c in claims],
    }


@router.get("/petty/me")
async def my_petty(request: Request):
    staff = await _my_staff(request)
    async with _crm_session() as session:
        return await _petty_payload(session, staff)


@router.post("/petty/expenses")
async def add_my_petty_expense(body: PettyExpensePayload, request: Request):
    """登記一筆自己墊的錢。staff_id 只從 token 來 —— body 給了也不看。"""
    staff = await _my_staff(request)
    if not body.actual:
        raise HTTPException(status_code=400, detail="金額不可為 0")
    async with _crm_session() as session:
        exp = _new_expense(body, staff.id)
        _enforce_project_link(exp)          # 非專案雜支 → 不連（表單本來就鎖住）
        await _attach_cost_group(session, exp)
        session.add(exp)
        await session.commit()
        return {"status": "ok", "id": exp.id}


async def _attach_cost_group(session, exp) -> None:
    """有專案就落到該專案的成本子表（沒有就自我修復建主表）。

    不做的話，這筆錢在 CRM 專案詳情的雜支區塊看不到 —— 那一區是照子表分組畫的。
    """
    if not exp.project_id or exp.cost_group_id:
        return
    from .costs import _resolve_target_group
    exp.cost_group_id = await _resolve_target_group(session, exp.project_id, None)


@router.put("/petty/expenses/{expense_id}")
async def update_my_petty_expense(expense_id: str, body: PettyExpensePayload,
                                  request: Request):
    async with _crm_session() as session:
        exp = await _own_editable(session, expense_id, request)
        exp.project_id = body.project_id or None
        exp.category = body.category or "其他"
        exp.actual = body.actual
        exp.sub_item = body.summary or None
        exp.notes = body.notes or None
        exp.expense_date = _parse_day(body.expense_date) or exp.expense_date
        exp.item = body.item or "其他"
        exp.invoice_no = body.invoice_no or None
        exp.has_invoice = 1 if body.invoice_no else 0
        _enforce_project_link(exp, wants_link=bool(body.project_id))
        await session.commit()
    return {"status": "ok", "id": expense_id}


@router.delete("/petty/expenses/{expense_id}")
async def delete_my_petty_expense(expense_id: str, request: Request):
    async with _crm_session() as session:
        exp = await _own_editable(session, expense_id, request)
        await session.delete(exp)
        await session.commit()
    return {"status": "ok", "deleted": expense_id}


def _may_manage_others(request: Request) -> bool:
    """審核者（money_view + finance_approve）改得動**任何人**的草稿。

    會計代打之後常要回頭補一張收據或改個項目 —— 只能改自己的話，代為登記就
    變成單向的。非審核者維持只摸得到自己的。
    """
    if not can_see_money(request):
        return False
    try:
        _check_approver(request)
        return True
    except HTTPException:
        return False


async def _own_editable(session, expense_id: str, request: Request):
    """還沒送出的單據。scope 判斷收在這裡：審核者可代管任何人的草稿
    （代打後常要補收據、改項目），非審核者只摸得到自己的。

    404 不 403 —— 403 等於告訴對方「這筆存在、只是不是你的」，那本身就是洩漏。
    """
    any_owner = _may_manage_others(request)
    staff_id = None if any_owner else (await _my_staff(request)).id
    exp = await session.get(CrmProjectExpense, expense_id)
    if exp is None or (not any_owner and exp.staff_id != staff_id):
        raise HTTPException(status_code=404, detail="找不到這筆支出（或不是你的）")
    if (exp.status or "草稿") not in EDITABLE or exp.claim_id:
        raise HTTPException(status_code=409, detail="已送出請款的單據不能自行修改")
    return exp


# ── 代為登記：財務端管理**所有人**的零用金（owner 2026-08-17）───────────
#
# 為什麼要有這一組：`admin` 是共用帳號、後面沒有人，`/petty/me` 對它必然 409；
# 而真實流程裡本來就有「同事把收據交給會計、會計代打」這條路 —— 只做 own-scope
# 等於逼每個墊過錢的人都要有帳號並自己登記。
#
# 🔴 這一組**明確接受 staff_id**，與 own-scope 那組相反（那邊連 schema 都沒有這個
# 欄位，見 test_payload_has_no_staff_id）。分成兩組端點而不是加一個可選參數：
# 可選參數會讓「沒帶就是自己」變成預設安全性，而漏檢一次就是任何人都能替別人
# 記帳。分開之後守衛寫在路徑上，漏不掉。


async def _approver_staff(request: Request, session, staff_id: str):
    """代管對象查找。權限已在各端點頂部擋過（先 403 再碰 DB），這裡不重查。"""
    staff = await session.get(CrmStaff, staff_id)
    if staff is None:
        raise HTTPException(status_code=404, detail="找不到這位人員")
    return staff


@router.get("/petty/staff-options", dependencies=[Depends(money_dep)])
async def petty_staff_options(request: Request):
    """代為登記的人員下拉：在職人員 + 任何已經有零用金紀錄的人（含離職的）。"""
    _check_approver(request)
    async with _crm_session() as session:
        used = {r[0] for r in (await session.execute(
            select(CrmProjectExpense.staff_id)
            .where(CrmProjectExpense.staff_id.isnot(None)).distinct())).all()}
        rows = (await session.execute(
            select(CrmStaff.id, CrmStaff.name, CrmStaff.status,
                   CrmStaff.petty_float))).all()
    out = [{"id": r.id, "name": r.name, "petty_float": r.petty_float or 0}
           for r in rows if (r.status or "在職") == "在職" or r.id in used]
    return {"staff": sorted(out, key=lambda s: s["name"])}


@router.get("/petty/staff/{staff_id}", dependencies=[Depends(money_dep)])
async def petty_of_staff(staff_id: str, request: Request):
    """某個人的零用金現況 —— 形狀與 `/petty/me` 相同，前端共用同一段畫面。"""
    _check_approver(request)     # 先擋權限再碰 DB —— 否則沒 DB 時會回 503 遮住 403
    async with _crm_session() as session:
        staff = await _approver_staff(request, session, staff_id)
        return await _petty_payload(session, staff)


@router.post("/petty/staff/{staff_id}/expenses", dependencies=[Depends(money_dep)])
async def add_petty_expense_for(staff_id: str, body: PettyExpensePayload,
                                request: Request):
    _check_approver(request)     # 先擋權限再碰 DB —— 否則沒 DB 時會回 503 遮住 403
    async with _crm_session() as session:
        staff = await _approver_staff(request, session, staff_id)
        exp = _new_expense(body, staff.id)
        _enforce_project_link(exp)          # 非專案雜支 → 不連（表單本來就鎖住）
        await _attach_cost_group(session, exp)
        session.add(exp)
        await session.commit()
        return {"status": "ok", "id": exp.id}


@router.post("/petty/staff/{staff_id}/submit", dependencies=[Depends(money_dep)])
async def submit_petty_for(staff_id: str, body: PettySubmitPayload,
                           request: Request):
    _check_approver(request)     # 先擋權限再碰 DB —— 否則沒 DB 時會回 503 遮住 403
    async with _crm_session() as session:
        staff = await _approver_staff(request, session, staff_id)
        return await _submit_for(session, staff, body.notes)


@router.post("/petty/expenses/{expense_id}/receipt")
async def upload_my_petty_receipt(expense_id: str, request: Request,
                                  file: UploadFile = File(...)):
    """收據上傳 —— 復用 costs._save_receipt（它已支援沒有專案的支出）。"""
    async with _crm_session() as session:
        exp = await _own_editable(session, expense_id, request)
        project_id = exp.project_id
    from .costs import _save_receipt
    return await _save_receipt(project_id, expense_id, file)


async def _assert_not_mine_project(session, rows) -> None:
    """🔴 **私帳的專案不可能跟公司請款**（owner 2026-08-28 原話）。

    那些案子的錢流歸屬在私帳 —— 自己的案子自己出錢。而且逐案損益的「行政雜支」
    只算沒請過款的，真送出去那筆錢會從私帳成本裡消失、變成公司費用，兩邊都不對。

    源頭也堵了：收支明細推去零用金時**不帶私帳專案**（見 `_petty_project_id`）
    —— 那張單據是要跟公司請款的，公司要的是它自己那個案子。這裡是第二道，
    擋任何其他路徑塞進來的。
    """
    pids = {r.project_id for r in rows if r.project_id}
    if not pids:
        return
    mine = dict((await session.execute(
        select(CrmProject.id, CrmProject.name)
        .where(CrmProject.id.in_(pids), CrmProject.entity == "mine"))).all())
    if not mine:
        return
    blocked = sum(1 for r in rows if r.project_id in mine)
    which = "、".join(sorted(mine.values()))
    raise HTTPException(
        status_code=409,
        detail=f"「{which}」是私帳的案子，不能跟公司請款（{blocked} 筆）——"
               f"自己的案子自己出錢，那些花費已經算在私帳的逐案損益裡了。"
               f"要送的話請先把那幾筆的專案清掉，或把案子搬回公司帳")


async def _petty_project_id(session, project_id: str):
    """零用金單據能不能掛這個專案。私帳的案子 → 回 None（不掛）。

    🔴 收支明細推去零用金那條路（源日請款）預設會把收支列的專案帶過去，而私帳
    收支只能掛私帳專案 —— 帶過去就成了「私帳的案子在跟公司請款」，正是
    `_assert_not_mine_project` 要擋的事。在**源頭**清掉，使用者不會先建好一張
    註定送不出去的單據。資訊沒有掉：收支列那邊的專案連結還在。
    """
    if not project_id:
        return None
    p = await session.get(CrmProject, project_id)
    return None if p is not None and p.entity == "mine" else project_id


async def _submit_for(session, staff, notes: str = "") -> dict:
    """把某人手上的草稿打包成批次並直接成立。本人送出／代為送出共用。"""
    rows = (await session.execute(
        select(CrmProjectExpense)
        .where(CrmProjectExpense.staff_id == staff.id,
               CrmProjectExpense.claim_id.is_(None),
               _not_mine_project(CrmProjectExpense))       # 同 _petty_payload
    )).scalars().all()
    rows = [r for r in rows if (r.status or "草稿") in EDITABLE]
    if not rows:
        raise HTTPException(status_code=400, detail="沒有可送出的單據")
    await _assert_not_mine_project(session, rows)
    dates = [r.expense_date for r in rows if r.expense_date]
    total = sum(r.actual or 0 for r in rows)
    opening = staff.petty_float or 0
    await _assert_month_open(session, *dates)
    now = _now()
    claim = CrmReimbursement(
        id=uuid.uuid4().hex[:16],
        staff_id=staff.id, staff_name=staff.name,
        period_start=min(dates) if dates else None,
        period_end=max(dates) if dates else None,
        opening_float=opening,
        closing_float=opening - total,
        total_claim=total,
        status="已核准", submitted_at=now,
        approved_by="（自動核准）", approved_at=now,
        notes=notes or None,
    )
    session.add(claim)
    for r in rows:
        r.claim_id = claim.id
        r.status = "已核准"
    aps = await _build_aps(session, claim, rows, staff)
    await session.commit()
    return {"status": "ok", "claim_id": claim.id, "count": len(rows),
        "total_claim": total, "payment_requests": aps, "auto_approved": True}


@router.post("/petty/submit")
async def submit_my_petty(body: PettySubmitPayload, request: Request):
    """把手上的草稿打包成一張請款批次 → **直接成立**（owner 2026-08-17）。

    🔴 預設同意、事後可退。理由是流程現實：這批錢是同事已經**自己墊出去**的，
    卡在「待審」不會讓錢回來，只會讓請款變慢。所以送出即核准、即產應付款，
    財務端看到不對再退回（`/claims/{id}/reject`）—— 退回會把應付款收乾淨、
    單據回到本人草稿。已經付款的批次退不了（見 `_clear_aps`）。

    代價是月結鎖帳的檢查提前到這裡：認列月被鎖 → 409。這是對的 ——
    不能讓人往已經結算完的月份塞新費用。
    """
    staff = await _my_staff(request)
    async with _crm_session() as session:
        return await _submit_for(session, staff, body.notes)


# ── 收支明細 → 零用金單據（owner 2026-08-27）────────────────────────
#
# 為什麼要這條路：他自己墊出去的錢原本只記在私帳（entity='mine'）裡，公司這邊
# 完全看不到 —— 零用金系統裡他的單據數是 **0**，但私帳中用個人帳戶/信用卡付掉
# 的公司支出有一百多萬。手抄一遍到零用金工作台不只是慢，兩本帳還會各自漂。
# 這條路把收支的那一列直接變成一張草稿單據，並用 `cash_entries.expense_id`
# 把兩邊釘死（那個欄位本來就在 model 裡，之前 0 筆使用）。
#
# 🔴 付款方式不影響能不能推（owner：「不管是匯款或信用卡都可以直接接到 crm 的
# 零用金請款」）。卡費最後從哪個帳戶扣是另一件事，不在這裡判。
# 🔴 一次一列（owner：「每一列都可以推送，不是一次一批」）—— 不留批次入口：
# 批次語意（一個 item 蓋整批異質類別）從來沒被設計過，留著就是陷阱。
# 🔴 只建**草稿**，不自動送出 —— 送出＝即核准即產應付款（見 `/petty/submit`
# 的說明），那一步要人看過項目與專案才按。

def _cash_claim_amount(e) -> int:
    """這一列要跟公司請多少 —— 流出側（expense + claim）再加匯費。

    匯費在損益上另列管理費（`core.finance_logic.out_amount` 刻意不含它），
    但錢確實是從他的帳戶走的，請款要還他。
    """
    return int(e.expense or 0) + int(e.claim or 0) + int(e.bank_fee or 0)


def _cash_push_block(entry, pushed) -> str:
    """不能推的理由；空字串＝可以推。expense_id 指向已被刪掉的單據 → 視同沒推過。"""
    if pushed is not None:
        return f"已經推送過（{pushed.status or '草稿'}）"
    if _cash_claim_amount(entry) <= 0:
        return "這一列沒有支出金額"
    return ""


async def _petty_item_domain(session) -> list:
    """會計項目的值域 —— **下拉選項＝寫入白名單，同一份**。

    source='cash' 裡平鋪的費用類別。兩類要排除：複合鍵（`公司_器材`…）是私帳
    自己的分類詞彙，不是會計項目；`轉存`／`請款單`／`營業稅` 那些 treatment 非
    費用的類別掛到單據上，AP 認列會落到轉帳/稅務分支 —— 所以按
    treatment='direct_expense' 篩，不是只濾底線。
    """
    from db.models import FinanceCategoryMap
    rows = (await session.execute(
        select(FinanceCategoryMap.category_text)
        .where(FinanceCategoryMap.source == "cash",
               # 零用金是母公司的東西（本檔的專案查詢都帶 not_mine）
               FinanceCategoryMap.entity == "parent",
               FinanceCategoryMap.active.is_(True),
               FinanceCategoryMap.treatment == "direct_expense")
        .order_by(FinanceCategoryMap.category_text))).all()
    items = [r[0] for r in rows if _TAX_SEP not in r[0]]
    return items or list(FALLBACK_ITEMS)


async def _push_from_cash(session, staff, body: PettyFromCashPayload) -> dict:
    """本人推送／代為推送共用的正本（可先 `preview=True` 試算）。"""
    if not body.entry_id:
        raise HTTPException(status_code=400, detail="沒有選到收支列")
    entry = await session.get(CrmCashEntry, body.entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="找不到這一列收支")
    pushed = (await session.get(CrmProjectExpense, entry.expense_id)
              if entry.expense_id else None)
    blocked = _cash_push_block(entry, pushed)
    items = await _petty_item_domain(session)
    item = body.item or petty_item_for(entry.category, items)

    if body.preview:
        return {"status": "ok", "preview": True,
                "staff": {"id": staff.id, "name": staff.name},
                "items": items,
                "row": {"entry_id": entry.id,
                        "date": _fmt_day(entry.entry_date),
                        "summary": entry.summary or "",
                        "category": entry.category or "",
                        "amount": _cash_claim_amount(entry),
                        "item": item,     # 空＝對不出會計項目，畫面要人挑
                        "blocked": blocked}}

    if blocked:
        raise HTTPException(status_code=409, detail=blocked)
    # 🔴 空項目就擋下，不落「其他」—— 「對不出來留白讓人挑」是 petty_item_for
    # 的契約，守在這裡（後端）才對所有呼叫端都成立，不能只靠一個對話框的 JS。
    if not item:
        raise HTTPException(status_code=400, detail="請先選會計項目")
    if item not in items:
        raise HTTPException(status_code=400, detail=f"「{item}」不是可用的會計項目")
    exp = _new_expense(PettyExpensePayload(
        actual=_cash_claim_amount(entry),
        summary=entry.summary or "",
        item=item,
        category=entry.sub_item or "其他",
        project_id=await _petty_project_id(
            session, body.project_id or entry.project_id or ""),
        invoice_no=entry.invoice_number or "",
        notes=body.notes or f"由收支明細推送（{_fmt_day(entry.entry_date)}）",
    ), staff.id)
    # 🔴 認列在**消費日**（收支列的日期），不是今天 —— 專案成本的時點規則見
    # CrmProjectExpense 的欄位註解。直接用原 datetime，不走字串來回。
    exp.expense_date = entry.entry_date
    _enforce_project_link(exp)           # 非專案雜支不得掛專案
    await _attach_cost_group(session, exp)
    session.add(exp)
    entry.expense_id = exp.id            # 兩邊釘死 —— 重推會被 blocked 擋
    await session.commit()
    return {"status": "ok", "staff": {"id": staff.id, "name": staff.name},
            "expense_id": exp.id, "amount": exp.actual, "item": exp.item}


@router.post("/petty/from-cash", dependencies=[Depends(money_dep)])
async def petty_from_cash(body: PettyFromCashPayload, request: Request):
    """自己的收支列 → 自己的零用金草稿。"""
    async with _crm_session() as session:
        return await _push_from_cash(session, await _my_staff(request), body)


@router.post("/petty/staff/{staff_id}/from-cash", dependencies=[Depends(money_dep)])
async def petty_from_cash_for(staff_id: str, body: PettyFromCashPayload,
                              request: Request):
    """代為推送 —— 比照代為登記走**路徑參數**，own-scope 的 schema 不長 staff_id。"""
    _check_approver(request)     # 先擋權限再碰 DB
    async with _crm_session() as session:
        staff = await _approver_staff(request, session, staff_id)
        return await _push_from_cash(session, staff, body)


@router.delete("/petty/from-cash/{entry_id}", dependencies=[Depends(money_dep)])
async def undo_petty_from_cash(entry_id: str, request: Request):
    """撤銷推送：刪掉草稿單據並解開連結。送出之後就撤不了（要走退回）。"""
    async with _crm_session() as session:
        entry = await session.get(CrmCashEntry, entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="找不到這一列收支")
        exp = (await session.get(CrmProjectExpense, entry.expense_id)
               if entry.expense_id else None)
        if exp is not None:
            if (exp.status or "草稿") not in EDITABLE or exp.claim_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"單據已經送出（{exp.status}），請到零用金那邊退回")
            # 🔴 身分守衛與建立側對稱：自己的草稿自己撤；撤**別人的**＝代管動作，
            # 走審核權（不然任何有 money_view 的人都能把同事的推送靜靜還原）。
            ident = await resolve_current_staff(request)
            me = ident["staff"]
            if me is None or exp.staff_id != me.id:
                _check_approver(request)
            await session.delete(exp)
        entry.expense_id = None      # 空殼連結（單據已刪）＝自我修復，不需身分
        await session.commit()
    return {"status": "ok", "entry_id": entry_id}


# ── 財務視角（money_view + finance_approve）：匯款清冊 / 審核 / 帳冊 ────
@router.get("/petty/accounts", dependencies=[Depends(money_dep)])
async def petty_accounts(request: Request):
    """匯款清冊的資料源 —— **所有**跟零用金有關的人，含綁定狀態與歷史累計。

    🔴 「有關」的判準刻意放寬到「曾經有過任何一筆單據」，不是「現在還欠他錢」。
    只列「還欠他錢的人」的話，「這期沒請款」與「不在清單」會長得一樣 ——
    owner 要的是全貌
    （誰用過、誰沒帳號、誰沒綁、歷史付了多少）。149 人裡有紀錄的才進來，
    所以清單仍然不會變成整本人員名冊。

    綁定狀態（`bound_user`）是這頁的重點之一：沒綁帳號的人**自己登不了記**，
    只能靠代為登記。空字串＝沒綁，畫面上要看得出來。
    """
    _check_approver(request)
    async with _crm_session() as session:
        # 逐人 × 狀態的單據合計：一次 group by 拿齊草稿／待付／已付
        sums = (await session.execute(
            select(CrmProjectExpense.staff_id, CrmProjectExpense.status,
                   safunc.coalesce(safunc.sum(CrmProjectExpense.actual), 0),
                   safunc.count(CrmProjectExpense.id))
            .where(CrmProjectExpense.staff_id.isnot(None),
                   _not_mine_project(CrmProjectExpense))   # 同上：不是公司的錢
            .group_by(CrmProjectExpense.staff_id, CrmProjectExpense.status))).all()
        staff_rows = (await session.execute(
            select(CrmStaff.id, CrmStaff.name, CrmStaff.petty_float,
                   CrmStaff.bank_name, CrmStaff.bank_account, CrmStaff.status))).all()
        # 綁定狀態：users.staff_id → 帳號名（沒綁的人自己登不了記）
        bound = dict((await session.execute(
            select(User.staff_id, User.username)
            .where(User.staff_id.isnot(None), User.staff_id != ""))).all())
        # 🔴 費用歸屬：這個人要「還」給公司的部分（別人墊、帳算他的、還沒結）。
        # 不扣掉的話，王士源那種「自己沒有未結請款、但欠公司 647」的人在清冊上
        # 會顯示成 —，看起來像沒事。
        owes = dict((await session.execute(
            select(CrmProjectExpense.owner_staff_id,
                   safunc.coalesce(safunc.sum(CrmProjectExpense.actual), 0))
            .where(CrmProjectExpense.owner_staff_id.isnot(None),
                   CrmProjectExpense.owner_settled == 0)
            .group_by(CrmProjectExpense.owner_staff_id))).all())

    agg: dict[str, dict] = {}
    for sid, status, total, cnt in sums:
        a = agg.setdefault(sid, {"draft": 0, "open": 0, "paid": 0, "rows": 0})
        a["rows"] += cnt
        if status in EDITABLE:
            a["draft"] += total
        elif status == "已付款":
            a["paid"] += total
        else:                      # 待審 / 已核准 ＝ 已送出未付
            a["open"] += total

    out = []
    for s in staff_rows:
        a = agg.get(s.id)
        float_amt = s.petty_float or 0
        owe = owes.get(s.id, 0)
        # 🔴 這個 continue 一定要在 `a = a or {...}` **之前**：補了預設值之後 `a`
        # 永遠是 dict，條件就永遠不成立 —— 149 個人全部湧進匯款清冊（踩過一次）。
        if not a and not float_amt and not owe:
            continue
        a = a or {"draft": 0, "open": 0, "paid": 0, "rows": 0}
        acct = s.bank_account or ""
        out.append({
            "staff_id": s.id, "name": s.name,
            "owed_by_staff": owe,          # 歸屬他、還沒還的（正數＝他欠公司）
            "staff_status": s.status or "",
            "bound_user": bound.get(s.id, ""),      # 空＝沒綁帳號
            "opening_float": float_amt,
            "draft_total": a["draft"],              # 還沒送出
            "claim_total": a["open"],               # 已送出待付
            "paid_total": a["paid"],                # 歷史已付累計
            "rows": a["rows"],
            "closing_float": float_amt - a["draft"] - a["open"],
            "bank": (f"{s.bank_name or ''} {acct[-4:]}".strip() if acct else ""),
            # 🔴 缺帳號要在畫面上擋住，不是靜默出 0
            "bank_missing": not acct,
            "status": ("待請款" if (a["draft"] or a["open"]) else "已請款"),
        })
    # 匯款清冊（唯一消費端）自己算合計、批次明細另打 /petty/claims ——
    # 這裡曾多回一份 totals 與全表 claims，兩者都沒人讀，還隨批次歷史線性長大
    return {"accounts": sorted(
        out, key=lambda x: -(x["claim_total"] + x["draft_total"]))}


# ── 項目 → 費用歸屬人 的對映（owner 2026-08-17：「有個設定按鈕，直接讓項目與
# 人員做連結，不用特別開一欄」）──────────────────────────────────────────
#
# 規則存 settings.json（`petty_item_owners`），不開資料表：它是一份「哪個項目算
# 誰的」的設定，不是業務資料 —— 而且 CRM 只跑在 master，settings 就在那裡。
# 建立單據時套用；逐列的 owner_staff_id 仍然存在（例外照樣改得動），只是不必
# 在帳冊上多開一欄讓人每筆挑。
SETTINGS_KEY = "petty_item_owners"


def _item_owners() -> dict:
    from config import load_settings
    v = load_settings().get(SETTINGS_KEY) or {}
    return v if isinstance(v, dict) else {}


@router.get("/petty/item-owners", dependencies=[Depends(money_dep)])
async def get_item_owners(request: Request):
    """目前的「項目 → 歸屬人」對映 + 可選人員（設定面板一次拿齊）。"""
    _check_approver(request)
    async with _crm_session() as session:
        items = await cash_category_texts(session)
        staff = (await session.execute(
            select(CrmStaff.id, CrmStaff.name)
            .where(CrmStaff.status == "在職").order_by(CrmStaff.name))).all()
    return {"mapping": _item_owners(),
            "items": items or list(FALLBACK_ITEMS),
            "staff": [{"id": r.id, "name": r.name} for r in staff]}


@router.put("/petty/item-owners", dependencies=[Depends(money_dep)])
async def set_item_owners(request: Request, apply_existing: int = Query(0)):
    """存對映。`apply_existing=1` 一併套到**還沒指定歸屬**的既有單據。

    只補 `owner_staff_id IS NULL` 的列 —— 已經指定過的（含手動改過的例外、
    已結清的歷史）不動，所以重複按不會把人家改好的東西洗掉。
    """
    _check_approver(request)
    body = await request.json()
    mapping = {str(k): str(v) for k, v in (body.get("mapping") or {}).items() if v}
    from config import load_settings, save_settings
    cur = load_settings()
    cur[SETTINGS_KEY] = mapping
    save_settings(cur)

    touched = 0
    if apply_existing and mapping:
        async with _crm_session() as session:
            for item, sid in mapping.items():
                rows = (await session.execute(
                    select(CrmProjectExpense)
                    .where(CrmProjectExpense.item == item,
                           CrmProjectExpense.owner_staff_id.is_(None)))).scalars().all()
                for r in rows:
                    r.owner_staff_id = sid
                    touched += 1
            await session.commit()
    return {"status": "ok", "mapping": mapping, "applied": touched}


@router.get("/petty/entries", dependencies=[Depends(money_dep)])
async def petty_entries(request: Request, q: str = Query(""),
                        staff_id: str = Query(""), item: str = Query(""),
                        month: str = Query(""), status: str = Query(""),
                        unbound: int = Query(0),
                        limit: int = Query(1000), offset: int = Query(0)):
    """**逐筆**的零用金帳冊 —— 欄位對齊 owner 的 Google Sheet（日期／請款／摘要／
    附註／項目／收款人／專案標籤）。

    這是「全部零用金」那頁的主體。做成逐筆而不是每人彙總的理由：owner 原本就是
    對著一張 443 列的表在工作，彙總表回答不了「那筆 4,309 的影印是誰、哪個案子」。
    """
    _check_approver(request)
    async with _crm_session() as session:
        w = []
        if staff_id:
            w.append(CrmProjectExpense.staff_id == staff_id)
        if item:
            w.append(CrmProjectExpense.item == item)
        if status:
            w.append(CrmProjectExpense.status == status)
        if unbound:                       # 只看沒對到專案、但有標籤的
            w.append(CrmProjectExpense.project_label.isnot(None))
            w.append(CrmProjectExpense.project_id.is_(None))
        if month:                         # YYYY-MM
            w.append(safunc.to_char(CrmProjectExpense.expense_date, "YYYY-MM") == month)
        if q:
            like = f"%{q}%"
            w.append(safunc.coalesce(CrmProjectExpense.sub_item, "").ilike(like)
                     | safunc.coalesce(CrmProjectExpense.notes, "").ilike(like)
                     | safunc.coalesce(CrmProjectExpense.invoice_no, "").ilike(like)
                     | safunc.coalesce(CrmProjectExpense.project_label, "").ilike(like))
        # 只看零用金的單據 —— 舊的專案雜支（CRM 成本頁建的）沒有 item，混進來
        # 會讓帳冊變髒。
        # 🔴 判準是「有 item」不是「有 claim_id」：剛在這一頁新增的草稿還沒送出、
        # 沒有 claim_id，用 claim_id 當判準的話新增完會看不見 ——「按了沒反應」。
        w.append(CrmProjectExpense.item.isnot(None))

        base = select(CrmProjectExpense).where(*w)
        total, amount = (await session.execute(
            select(safunc.count(CrmProjectExpense.id),
                   safunc.coalesce(safunc.sum(CrmProjectExpense.actual), 0))
            .where(*w))).first()
        rows = (await session.execute(
            # 🔴 沒有日期的排**最前面**，不是最後面。兩個理由：
            #   1. 剛在這一頁新增的列還沒填日期，排最後＝在第 300 列之後，
            #      使用者按完「新增」什麼都沒看到
            #   2. 匯入的 37 列待補日期本來就是該優先整理的東西
            base.order_by(CrmProjectExpense.expense_date.desc().nullsfirst(),
                          CrmProjectExpense.created_at.desc())
            .limit(min(limit, 2000)).offset(offset))).scalars().all()
        # 有界的 IN（只問這批列用到的案）—— 原本是無條件撈全部專案
        names = await project_names_map(session, rows)
        staff = dict((await session.execute(
            select(CrmStaff.id, CrmStaff.name))).all())
        # 哪些批次已經產過應付款 → 那些列的分類不能再改（見 PATCH）
        booked = {r[0] for r in (await session.execute(
            select(CrmPaymentRequest.reimbursement_id)
            .where(CrmPaymentRequest.reimbursement_id.isnot(None)))).all()}
        # 掛到哪張成本子表 —— 帳冊靠這個標出「綁了專案但沒落子表」的列
        # （那種列在專案頁的雜支完全看不到，2026-08-17 踩過一次）
        gids = {e.cost_group_id for e in rows if e.cost_group_id}
        cost_groups = {}
        if gids:
            cost_groups = dict((await session.execute(
                select(CrmProjectCostGroup.id, CrmProjectCostGroup.name)
                .where(CrmProjectCostGroup.id.in_(gids)))).all())

    out = []
    for e in rows:
        d = _expense_dict(e, names.get(e.project_id, ""))
        d["staff_name"] = staff.get(e.staff_id, e.payee or "")
        d["note"] = e.notes or ""
        d["locked"] = e.claim_id in booked     # 已產應付款＝分類已入帳，不准改
        d["cost_group_name"] = cost_groups.get(e.cost_group_id, "")
        out.append(d)
    return {"entries": out, "total": total, "amount": amount,
            "returned": len(out), "offset": offset}


@router.get("/petty/project-groups/{project_id}", dependencies=[Depends(money_dep)])
async def petty_project_groups(project_id: str, request: Request):
    """某專案的成本子表清單 —— 帳冊在綁專案時，若不只一張就問使用者掛哪張。

    只有一張（或沒有）就不必問，後端會自動落主表。
    """
    _check_approver(request)
    async with _crm_session() as session:
        rows = (await session.execute(
            select(CrmProjectCostGroup.id, CrmProjectCostGroup.name,
                   CrmProjectCostGroup.shoot_date)
            .where(CrmProjectCostGroup.project_id == project_id)
            .order_by(CrmProjectCostGroup.sort_order,
                      CrmProjectCostGroup.created_at))).all()
    return {"groups": [{"id": r.id, "name": r.name,
                        "shoot_date": _fmt_day(r.shoot_date)} for r in rows]}


@router.patch("/petty/entries/{expense_id}", dependencies=[Depends(money_dep)])
async def patch_petty_entry(expense_id: str, request: Request):
    """就地修改一列（帳冊頁的下拉／欄位）。

    🔴 **已經產過應付款的列不准改分類**：那筆錢的科目與認列月份已經寫進
    `crm_payment_requests`，改了單據卻不改 AP，帳面上兩邊會對不起來 ——
    而畫面上完全看不出來。要改就先退回批次（AP 會被撤掉），改完再送出。
    匯入的歷史批次沒有 AP，所以那 443 列照樣改得動（那正是要整理的東西）。
    """
    _check_approver(request)
    body = await request.json()
    allowed = {"expense_date", "actual", "summary", "note", "item",
               "staff_id", "project_id", "project_label", "invoice_no",
               "owner_staff_id", "owner_settled", "cost_group_id"}
    unknown = set(body) - allowed
    if unknown:
        raise HTTPException(status_code=400, detail=f"不支援的欄位：{sorted(unknown)}")
    async with _crm_session() as session:
        exp = await session.get(CrmProjectExpense, expense_id)
        if exp is None:
            raise HTTPException(status_code=404, detail="找不到這筆支出")
        if exp.claim_id:
            booked = (await session.execute(
                select(safunc.count(CrmPaymentRequest.id))
                .where(CrmPaymentRequest.reimbursement_id == exp.claim_id))).scalar_one()
            if booked:
                raise HTTPException(
                    status_code=409,
                    detail="這筆已經產生應付款（科目與認列月份已入帳）。"
                           "要修改請先退回該批次，改完再送出。")
        if "expense_date" in body:
            exp.expense_date = _parse_day(body["expense_date"]) or exp.expense_date
        if "actual" in body:
            exp.actual = int(body["actual"] or 0)
        if "summary" in body:
            exp.sub_item = body["summary"] or None
        if "note" in body:
            exp.notes = body["note"] or None
        if "item" in body:
            exp.item = body["item"] or None
        if "staff_id" in body:
            exp.staff_id = body["staff_id"] or None
        if "invoice_no" in body:
            exp.invoice_no = body["invoice_no"] or None
            exp.has_invoice = 1 if exp.invoice_no else 0
        if "project_id" in body:
            exp.project_id = body["project_id"] or None
            # 綁到專案了就把原始標籤收掉 —— 留著會讓「未歸戶」永遠清不完
            if exp.project_id:
                exp.project_label = None
                # 🔴 一併落到成本子表：CRM 專案詳情的雜支是**照子表分組**顯示的，
                # cost_group_id 是 NULL 的列只存在於扁平清單裡 —— 綁了專案卻在
                # 專案頁上看不到那筆錢（owner 2026-08-17 回報）。沿用 CRM 建立
                # 雜支時的同一支解析器，落到主表。
                # 呼叫端可以指定要掛哪一張子表（帳冊在專案有多張時會問）；
                # 沒指定就落主表
                from .costs import _resolve_target_group
                exp.cost_group_id = await _resolve_target_group(
                    session, exp.project_id, body.get("cost_group_id"))
            else:
                exp.cost_group_id = None
        if "project_label" in body:
            exp.project_label = body["project_label"] or None
        if "cost_group_id" in body and "project_id" not in body:
            exp.cost_group_id = body["cost_group_id"] or None
        if "owner_staff_id" in body:
            exp.owner_staff_id = body["owner_staff_id"] or None
        if "owner_settled" in body:
            exp.owner_settled = 1 if body["owner_settled"] else 0
        # 最終狀態檢查（409 會讓 session 不 commit 而整筆回滾，含子表的自我修復）
        unlinked = _enforce_project_link(exp, wants_link=bool(body.get("project_id")))
        await session.commit()
    return {"status": "ok", "id": expense_id, "unlinked": unlinked}


@router.get("/petty/claims", dependencies=[Depends(money_dep)])
async def petty_claims(request: Request, status: str = Query("待審")):
    """待審／已核准／已付款批次清單（含逐行明細）。"""
    _check_approver(request)
    async with _crm_session() as session:
        claims = (await session.execute(
            select(CrmReimbursement)
            .where(CrmReimbursement.status == status)
            .order_by(CrmReimbursement.submitted_at.desc().nullslast())
        )).scalars().all()
        ids = [c.id for c in claims]
        rows = (await session.execute(
            select(CrmProjectExpense)
            .where(CrmProjectExpense.claim_id.in_(ids))) ).scalars().all() if ids else []
        # 有界的 IN（只問這批列用到的案）—— 原本是無條件撈全部專案
        names = await project_names_map(session, rows)
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r.claim_id, []).append(
            _expense_dict(r, names.get(r.project_id, "")))
    return {"claims": [dict(_claim_dict(c), lines=grouped.get(c.id, []))
                       for c in claims]}


# ── 審核 → 應付款 → 匯款（P2）────────────────────────────────────────
#
# 🔴 **費用認列在請款單上，不在收支明細上。** `core.finance_logic
# .classify_cash_entry` 的優先序把帶 `payment_request_id` 的收支列歸成
# `ap_settlement`，而 `iter_expense_items` 只把「請款單」與 treatment 為
# direct_expense/unmapped 的收支列算進損益 —— 這是為了避免同一筆錢被認兩次。
#
# 由此決定了兩件跟原規劃不同的事（docs/PETTY_CASH_PLAN.md §3 已更正）：
#   1. AP 依 **（會計項目 × 認列月份）** 拆，不是一張批次一張 —— 一張批次
#      混著行政／專案雜支／設備耗材，合成一張就把科目軸壓平成「零用金」，
#      而且跨月的批次會把七月的錢認到八月。
#   2. 收支明細 **一張 AP 一列**，不是一張單據一列。逐行落 443 列的話每一列
#      都是 ap_settlement（對損益毫無作用），而專案毛利讀的是
#      `crm_project_expenses`（見 costs.project_financial_summary），本來就不
#      靠收支明細 —— 逐行落帳只是噪音。


def _ap_buckets(rows) -> dict:
    """把單據行分成 (會計項目, 認列月份) 桶 —— 一桶一張應付款。"""
    buckets: dict = {}
    for r in rows:
        when = r.expense_date
        key = (r.item or "其他", when.strftime("%Y-%m") if when else "")
        buckets.setdefault(key, []).append(r)
    return buckets


async def _build_aps(session, claim, rows, staff) -> list:
    """依 (項目 × 月份) 產應付款。呼叫端負責先 `_clear_aps`（避免重複）。"""
    made = []
    for (item, month), bucket in sorted(_ap_buckets(rows).items()):
        dates = [r.expense_date for r in bucket if r.expense_date]
        ap = CrmPaymentRequest(
            id=uuid.uuid4().hex[:16],
            request_date=max(dates) if dates else _now(),
            amount=sum(r.actual or 0 for r in bucket),
            summary=f"零用金請款 {claim.staff_name} {month or '待補日期'}／{item}",
            category=item,          # 🔴 科目軸：cat_map 查的就是這一欄
            payee_name=claim.staff_name,
            # 身分證只在這裡從 crm_staff 帶，不從單據行來（PII 只有一份正本）
            payee_id=(staff.id_number if staff else None),
            payee_type="內部人員",
            payment_status="應付款",
            planned_month=month or None,
            reimbursement_id=claim.id,
            notes=f"零用金批次 {claim.id}（{len(bucket)} 筆單據）",
        )
        session.add(ap)
        made.append(ap.id)
    return made


async def _clear_aps(session, claim_id: str) -> int:
    """撤掉這張批次還沒付款的應付款。

    🔴 退回若不收掉 AP，帳上會留下**幽靈負債**：單據已經回到本人草稿、應付帳款
    卻還掛著那筆錢，月結與現金流預測都會多算。已付款的 AP 不准撤（那筆錢真的
    出去了）—— 撞到就 409，讓人去走沖銷而不是把歷史抹掉。
    """
    aps = (await session.execute(
        select(CrmPaymentRequest)
        .where(CrmPaymentRequest.reimbursement_id == claim_id))).scalars().all()
    paid = [a for a in aps if a.payment_status == "已付款" or a.payment_date]
    if paid:
        raise HTTPException(status_code=409,
                            detail="這張批次已經有付款紀錄，不能退回（請改走沖銷）")
    for a in aps:
        await session.delete(a)
    return len(aps)


@router.post("/petty/claims/{claim_id}/approve", dependencies=[Depends(money_dep)])
async def approve_claim(claim_id: str, request: Request):
    """核准 → 產應付款（項目 × 月份），批次與單據轉「已核准」。"""
    _check_approver(request)
    async with _crm_session() as session:
        claim = await session.get(CrmReimbursement, claim_id)
        if claim is None:
            raise HTTPException(status_code=404, detail="找不到這張請款批次")
        if claim.status != "待審":
            raise HTTPException(status_code=409,
                                detail=f"只有待審的批次可以核准（目前：{claim.status}）")
        rows = (await session.execute(
            select(CrmProjectExpense)
            .where(CrmProjectExpense.claim_id == claim_id))).scalars().all()
        if not rows:
            raise HTTPException(status_code=409, detail="這張批次沒有單據")

        staff = await session.get(CrmStaff, claim.staff_id) if claim.staff_id else None
        # F1 月結鎖帳：認列月被鎖就整張擋下（部分寫入比擋下來更糟）
        await _assert_month_open(session, *[r.expense_date for r in rows if r.expense_date])
        made = await _build_aps(session, claim, rows, staff)
        claim.status = "已核准"
        claim.approved_by = _username(request)
        claim.approved_at = _now()
        for r in rows:
            r.status = "已核准"
        await session.commit()
    return {"status": "ok", "claim_id": claim_id, "payment_requests": made}


@router.post("/petty/claims/{claim_id}/reject", dependencies=[Depends(money_dep)])
async def reject_claim(claim_id: str, request: Request, reason: str = Query("")):
    """整批退回 —— 單據回到本人的草稿（claim_id 清掉），批次留著當紀錄。"""
    _check_approver(request)
    async with _crm_session() as session:
        claim = await session.get(CrmReimbursement, claim_id)
        if claim is None:
            raise HTTPException(status_code=404, detail="找不到這張請款批次")
        if claim.status not in ("待審", "已核准"):
            raise HTTPException(status_code=409, detail="已付款的批次不能退回")
        # 先收應付款（已付款會在這裡 409），再放單據 —— 順序反過來的話
        # 撞到已付款時單據已經被放掉，批次就成了半退回的狀態
        killed = await _clear_aps(session, claim_id)
        rows = (await session.execute(
            select(CrmProjectExpense)
            .where(CrmProjectExpense.claim_id == claim_id))).scalars().all()
        for r in rows:
            r.claim_id = None
            r.status = "退回"
        claim.status = "退回"
        claim.notes = ((claim.notes or "") + f"｜退回：{reason}").strip("｜")
        await session.commit()
    return {"status": "ok", "returned": len(rows), "payment_requests_removed": killed}


@router.post("/petty/claims/{claim_id}/lines/{expense_id}/reject",
             dependencies=[Depends(money_dep)])
async def reject_claim_line(claim_id: str, expense_id: str, request: Request,
                            reason: str = Query("")):
    """逐行退回 —— 只把有問題的那一筆退掉，其餘照走。

    整批打回是最省事的寫法，但一張 30 筆的批次因為一張收據沒拍好被整個退回，
    人就得重送 30 筆 —— 於是大家開始不用系統。
    """
    _check_approver(request)
    async with _crm_session() as session:
        claim = await session.get(CrmReimbursement, claim_id)
        exp = await session.get(CrmProjectExpense, expense_id)
        if claim is None or exp is None or exp.claim_id != claim_id:
            raise HTTPException(status_code=404, detail="找不到這筆單據")
        if claim.status not in ("待審", "已核准"):
            raise HTTPException(status_code=409, detail="已付款的批次不能退單")
        # 已核准的批次已經產了應付款 → 抽掉一行就得重算它們（金額變了、
        # 甚至整個 (項目×月份) 桶可能消失）。撤掉重建比就地改安全：
        # 重建走的是與核准時同一段程式碼，不會有第二套「怎麼調整 AP」的邏輯。
        await _clear_aps(session, claim_id)
        exp.claim_id = None
        exp.status = "退回"
        exp.notes = ((exp.notes or "") + f"｜退回：{reason}").strip("｜")
        rows = (await session.execute(
            select(CrmProjectExpense)
            .where(CrmProjectExpense.claim_id == claim_id))).scalars().all()
        rest = sum(r.actual or 0 for r in rows)
        claim.total_claim = rest
        claim.closing_float = (claim.opening_float or 0) - rest
        aps = []
        if rows:
            staff = (await session.get(CrmStaff, claim.staff_id)
                     if claim.staff_id else None)
            aps = await _build_aps(session, claim, rows, staff)
        else:
            # 一行都不剩：批次不該以「已核准 0 元」的樣子留在匯款清冊上
            claim.status = "退回"
        await session.commit()
    return {"status": "ok", "claim_total": rest, "payment_requests": aps}


@router.post("/petty/claims/{claim_id}/pay", dependencies=[Depends(money_dep)])
async def pay_claim(claim_id: str, request: Request, payment_date: str = Query("")):
    """登記匯款 → 批次/單據轉已付款，並把每張 AP 結清成一列收支明細。

    冪等：已經有 `payment_request_id` 對應收支列的 AP 不再重複落帳（重按不會
    重複記帳，這是財務端最不能出的錯）。
    """
    _check_approver(request)
    when = _parse_day(payment_date) or _now()
    async with _crm_session() as session:
        claim = await session.get(CrmReimbursement, claim_id)
        if claim is None:
            raise HTTPException(status_code=404, detail="找不到這張請款批次")
        if claim.status != "已核准":
            raise HTTPException(status_code=409,
                                detail=f"只有已核准的批次可以登記匯款（目前：{claim.status}）")
        aps = (await session.execute(
            select(CrmPaymentRequest)
            .where(CrmPaymentRequest.reimbursement_id == claim_id))).scalars().all()
        # 現金側落在匯款日 —— 鎖了就擋
        await _assert_month_open(session, when)
        posted = {r for (r,) in (await session.execute(
            select(CrmCashEntry.payment_request_id)
            .where(CrmCashEntry.payment_request_id.in_([a.id for a in aps]))
        )).all()} if aps else set()

        made = 0
        for ap in aps:
            ap.payment_status = "已付款"
            ap.payment_date = when
            if ap.id in posted:
                continue
            session.add(CrmCashEntry(
                id=uuid.uuid4().hex[:16],
                entry_date=when,                 # 現金側＝錢真的出去那天
                expense=ap.amount,
                summary=ap.summary,
                category="請款",                  # → ap_settlement（費用已在 AP 認列）
                item=ap.category,
                payee=claim.staff_name,
                payment_date=when,
                payment_status="已付款",
                payment_request_id=ap.id,
                note=f"零用金批次 {claim.id}",
            ))
            made += 1
        claim.status = "已付款"
        claim.paid_at = when
        rows = (await session.execute(
            select(CrmProjectExpense)
            .where(CrmProjectExpense.claim_id == claim_id))).scalars().all()
        for r in rows:
            r.status = "已付款"
        await session.commit()
    return {"status": "ok", "cash_entries": made, "payment_requests": len(aps)}


@router.get("/petty/payout.csv", dependencies=[Depends(money_dep)])
async def payout_csv(request: Request):
    """匯款清冊 CSV（已核准未付款）—— 貼進銀行的批次轉帳。

    缺銀行帳號的人**照樣出現在檔案裡**，帳號欄寫「未填」：靜默略過會讓人
    以為已經匯完了。
    """
    _check_approver(request)
    async with _crm_session() as session:
        claims = (await session.execute(
            select(CrmReimbursement)
            .where(CrmReimbursement.status == "已核准")
            .order_by(CrmReimbursement.staff_name))).scalars().all()
        sids = [c.staff_id for c in claims if c.staff_id]
        banks = {r.id: r for r in (await session.execute(
            select(CrmStaff.id, CrmStaff.name, CrmStaff.bank_name,
                   CrmStaff.bank_account).where(CrmStaff.id.in_(sids)))).all()} \
            if sids else {}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["收款人", "銀行", "帳號", "金額", "期間", "批次ID"])
    for c in claims:
        b = banks.get(c.staff_id)
        w.writerow([c.staff_name,
                    (b.bank_name if b and b.bank_name else ""),
                    (b.bank_account if b and b.bank_account else "未填"),
                    c.total_claim,
                    f"{c.period_start:%Y-%m-%d}~{c.period_end:%Y-%m-%d}"
                    if c.period_start and c.period_end else "",
                    c.id])
    return Response(
        # utf-8-sig：Excel 開 UTF-8 CSV 沒有 BOM 就是一排亂碼
        content=buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="payout.csv"'})


@router.get("/petty/unbound-labels", dependencies=[Depends(money_dep)])
async def petty_unbound_labels(request: Request):
    """匯入時沒對到專案的標籤 —— 一次綁一個標籤就帶走底下所有列。

    實測歷史資料 154 列只用了 18 種標籤，而且六成的標籤在 CRM 裡根本沒有
    對應專案（工研院／王道／婦女…）。所以做成「標籤→專案」的一次性對映，
    不是逐列去挑。
    """
    _check_approver(request)
    async with _crm_session() as session:
        rows = (await session.execute(
            select(CrmProjectExpense.project_label,
                   safunc.count(CrmProjectExpense.id),
                   safunc.coalesce(safunc.sum(CrmProjectExpense.actual), 0))
            .where(CrmProjectExpense.project_label.isnot(None),
                   CrmProjectExpense.project_id.is_(None),
                   # 綁不了的項目列在這裡只是死路（實測 153 列裡有 26 列是
                   # 「專案」「轉存」—— 標籤文字留著，但不開放連結）
                   CrmProjectExpense.item.in_(PROJECT_LINK_ITEMS))
            .group_by(CrmProjectExpense.project_label)
            .order_by(safunc.count(CrmProjectExpense.id).desc()))).all()
    return {"labels": [{"label": r[0], "count": r[1], "total": r[2]} for r in rows]}


@router.post("/petty/bind-label", dependencies=[Depends(money_dep)])
async def petty_bind_label(request: Request, label: str = Query(...),
                           project_id: str = Query(...)):
    """把某個標籤底下所有未綁定的列，一次掛到指定專案。"""
    _check_approver(request)
    async with _crm_session() as session:
        proj = await session.get(CrmProject, project_id)
        if proj is None:
            raise HTTPException(status_code=404, detail="找不到此專案")
        rows = (await session.execute(
            select(CrmProjectExpense)
            .where(CrmProjectExpense.project_label == label,
                   CrmProjectExpense.project_id.is_(None),
                   CrmProjectExpense.item.in_(PROJECT_LINK_ITEMS)))).scalars().all()
        from .costs import _resolve_target_group
        gid = await _resolve_target_group(session, project_id, None)
        for r in rows:
            r.project_id = project_id
            r.cost_group_id = r.cost_group_id or gid
            r.project_label = None
        await session.commit()
    return {"status": "ok", "bound": len(rows), "project": proj.name}

