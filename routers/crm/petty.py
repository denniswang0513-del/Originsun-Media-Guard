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

財務視角（帳戶總覽 / 待審 / 匯款清冊）走另一組端點，第一層 `money_dep` 403 +
`finance_approve` 模組。
"""
from __future__ import annotations

import csv
import io
import uuid

from fastapi import Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func as safunc
from sqlalchemy import select

from core.auth import check_admin_or_module
from core.money import can_see_money
from core.identity import resolve_current_staff
from core.schemas import PettyExpensePayload, PettySubmitPayload
from db.models import (CrmCashEntry, CrmPaymentRequest, CrmProject,
                       CrmProjectExpense, CrmReimbursement, CrmStaff,
                       FinanceCategoryMap)

from ._shared import (_assert_month_open, _get_factory, _now, _parse_day,
                      _require_db, _username, money_dep, router)

# 送出後就不再是本人能改的東西；只有這兩個狀態算「還在我手上」
EDITABLE = ("草稿", "退回")

# 會計項目下拉的備援 —— 正本是 finance_category_map（source='cash'），
# 這份只在對映表空掉時頂著，免得現場的人連下拉都沒有。
FALLBACK_ITEMS = ("專案雜支", "行政", "設備耗材", "業務推廣", "建構",
                  "軟體網路服務", "後期雜支", "專案外包", "其他")

APPROVE_MODULE = "finance_approve"


def _check_approver(request: Request):
    """審核／匯款：金額權限 + 審核模組。兩者都要。"""
    check_admin_or_module(request, APPROVE_MODULE)


async def _my_staff(request: Request):
    ident = await resolve_current_staff(request)
    if ident["staff"] is None:
        raise HTTPException(status_code=409,
                            detail="帳號尚未綁定人員檔案，請聯絡管理員")
    return ident["staff"]


def _new_expense(body, staff_id: str) -> CrmProjectExpense:
    """單據建構的單一正本（本人登記 / 代為登記共用）。"""
    return CrmProjectExpense(
        id=uuid.uuid4().hex[:16],
        project_id=body.project_id or None,
        category=body.category or "其他",
        estimated=0, actual=body.actual,
        sub_item=body.summary or None,
        notes=body.notes or None,
        created_at=_now(),
        expense_date=_parse_day(body.expense_date) or _now(),
        staff_id=staff_id,
        item=body.item or "其他",
        invoice_no=body.invoice_no or None,
        has_invoice=1 if body.invoice_no else 0,
        status="草稿",
    )


def _expense_dict(e: CrmProjectExpense, project_name: str = "") -> dict:
    return {
        "id": e.id,
        "expense_date": e.expense_date.strftime("%Y-%m-%d") if e.expense_date else "",
        "actual": e.actual,
        "summary": e.sub_item or "",
        "item": e.item or "",
        "category": e.category or "",
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
        "period_start": c.period_start.strftime("%Y-%m-%d") if c.period_start else "",
        "period_end": c.period_end.strftime("%Y-%m-%d") if c.period_end else "",
        "total_claim": c.total_claim,
        "opening_float": c.opening_float,
        "closing_float": c.closing_float,
        "status": c.status,
        "submitted_at": c.submitted_at.strftime("%Y-%m-%d") if c.submitted_at else "",
        "paid_at": c.paid_at.strftime("%Y-%m-%d") if c.paid_at else "",
        "notes": c.notes or "",
    }


# ── 下拉選項 ────────────────────────────────────────────────────────
@router.get("/petty/options")
async def petty_options():
    """登記表單的兩個軸：專案（可留白＝公司支出）＋ 會計項目。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        projects = (await session.execute(
            select(CrmProject.id, CrmProject.name, CrmProject.status)
            .order_by(CrmProject.created_at.desc()).limit(400))).all()
        items = [r[0] for r in (await session.execute(
            select(FinanceCategoryMap.category_text)
            .where(FinanceCategoryMap.source == "cash",
                   FinanceCategoryMap.active.is_(True))
            .order_by(FinanceCategoryMap.category_text))).all()]
    return {
        "projects": [{"id": p.id, "name": p.name, "status": p.status or ""}
                     for p in projects],
        "items": items or list(FALLBACK_ITEMS),
    }


# ── 我的請款（own-scope，不受 money_view 管）──────────────────────────
async def _petty_payload(session, staff) -> dict:
    """某個人的零用金現況。own-scope 與代管視圖共用 —— 兩份會漂。"""
    names = dict((await session.execute(
        select(CrmProject.id, CrmProject.name))).all())
    rows = (await session.execute(
        select(CrmProjectExpense)
        .where(CrmProjectExpense.staff_id == staff.id,
               CrmProjectExpense.claim_id.is_(None))
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
    factory = await _get_factory()
    async with factory() as session:
        return await _petty_payload(session, staff)


@router.post("/petty/expenses")
async def add_my_petty_expense(body: PettyExpensePayload, request: Request):
    """登記一筆自己墊的錢。staff_id 只從 token 來 —— body 給了也不看。"""
    staff = await _my_staff(request)
    if not body.actual:
        raise HTTPException(status_code=400, detail="金額不可為 0")
    factory = await _get_factory()
    async with factory() as session:
        exp = _new_expense(body, staff.id)
        session.add(exp)
        await session.commit()
        return {"status": "ok", "id": exp.id}


@router.put("/petty/expenses/{expense_id}")
async def update_my_petty_expense(expense_id: str, body: PettyExpensePayload,
                                  request: Request):
    any_owner = _may_manage_others(request)
    staff_id = None if any_owner else (await _my_staff(request)).id
    factory = await _get_factory()
    async with factory() as session:
        exp = await _own_editable(session, expense_id, staff_id, any_owner)
        exp.project_id = body.project_id or None
        exp.category = body.category or "其他"
        exp.actual = body.actual
        exp.sub_item = body.summary or None
        exp.notes = body.notes or None
        exp.expense_date = _parse_day(body.expense_date) or exp.expense_date
        exp.item = body.item or "其他"
        exp.invoice_no = body.invoice_no or None
        exp.has_invoice = 1 if body.invoice_no else 0
        await session.commit()
    return {"status": "ok", "id": expense_id}


@router.delete("/petty/expenses/{expense_id}")
async def delete_my_petty_expense(expense_id: str, request: Request):
    any_owner = _may_manage_others(request)
    staff_id = None if any_owner else (await _my_staff(request)).id
    factory = await _get_factory()
    async with factory() as session:
        exp = await _own_editable(session, expense_id, staff_id, any_owner)
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


async def _own_editable(session, expense_id: str, staff_id: str, any_owner: bool = False):
    """還沒送出的單據。`any_owner`＝審核者代管別人的（見下方 §代為登記）。

    非審核者只摸得到自己的，而且 404 不 403 —— 403 等於告訴對方「這筆存在、
    只是不是你的」，那本身就是洩漏。
    """
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
    _check_approver(request)
    staff = await session.get(CrmStaff, staff_id)
    if staff is None:
        raise HTTPException(status_code=404, detail="找不到這位人員")
    return staff


@router.get("/petty/staff-options", dependencies=[Depends(money_dep)])
async def petty_staff_options(request: Request):
    """代為登記的人員下拉：在職人員 + 任何已經有零用金紀錄的人（含離職的）。"""
    _check_approver(request)
    factory = await _get_factory()
    async with factory() as session:
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
    factory = await _get_factory()
    async with factory() as session:
        staff = await _approver_staff(request, session, staff_id)
        return await _petty_payload(session, staff)


@router.post("/petty/staff/{staff_id}/expenses", dependencies=[Depends(money_dep)])
async def add_petty_expense_for(staff_id: str, body: PettyExpensePayload,
                                request: Request):
    _check_approver(request)     # 先擋權限再碰 DB —— 否則沒 DB 時會回 503 遮住 403
    factory = await _get_factory()
    async with factory() as session:
        staff = await _approver_staff(request, session, staff_id)
        exp = _new_expense(body, staff.id)
        session.add(exp)
        await session.commit()
        return {"status": "ok", "id": exp.id}


@router.post("/petty/staff/{staff_id}/submit", dependencies=[Depends(money_dep)])
async def submit_petty_for(staff_id: str, body: PettySubmitPayload,
                           request: Request):
    _check_approver(request)     # 先擋權限再碰 DB —— 否則沒 DB 時會回 503 遮住 403
    factory = await _get_factory()
    async with factory() as session:
        staff = await _approver_staff(request, session, staff_id)
        return await _submit_for(session, staff, body.notes)


@router.post("/petty/expenses/{expense_id}/receipt")
async def upload_my_petty_receipt(expense_id: str, request: Request,
                                  file: UploadFile = File(...)):
    """收據上傳 —— 復用 costs._save_receipt（它已支援沒有專案的支出）。"""
    any_owner = _may_manage_others(request)
    staff_id = None if any_owner else (await _my_staff(request)).id
    factory = await _get_factory()
    async with factory() as session:
        exp = await _own_editable(session, expense_id, staff_id, any_owner)
        project_id = exp.project_id
    from .costs import _save_receipt
    return await _save_receipt(project_id, expense_id, file)


async def _submit_for(session, staff, notes: str = "") -> dict:
    """把某人手上的草稿打包成批次並直接成立。本人送出／代為送出共用。"""
    rows = (await session.execute(
        select(CrmProjectExpense)
        .where(CrmProjectExpense.staff_id == staff.id,
               CrmProjectExpense.claim_id.is_(None))
    )).scalars().all()
    rows = [r for r in rows if (r.status or "草稿") in EDITABLE]
    if not rows:
        raise HTTPException(status_code=400, detail="沒有可送出的單據")
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
    factory = await _get_factory()
    async with factory() as session:
        return await _submit_for(session, staff, body.notes)


# ── 財務視角：帳戶總覽 / 待審 / 匯款清冊 ──────────────────────────────
@router.get("/petty/accounts", dependencies=[Depends(money_dep)])
async def petty_accounts(request: Request):
    """帳戶總覽 —— 人 × 期初／期末／應請款／狀態（＝ Sheet 上半段）。"""
    _check_approver(request)
    factory = await _get_factory()
    async with factory() as session:
        # 未結清＝已送出但還沒付款的批次，加上還沒送出的草稿
        claims = (await session.execute(
            select(CrmReimbursement)
            .where(CrmReimbursement.status.in_(("待審", "已核准")))
        )).scalars().all()
        drafts = dict((await session.execute(
            select(CrmProjectExpense.staff_id,
                   safunc.coalesce(safunc.sum(CrmProjectExpense.actual), 0))
            .where(CrmProjectExpense.claim_id.is_(None),
                   CrmProjectExpense.staff_id.isnot(None))
            .group_by(CrmProjectExpense.staff_id))).all())
        staff_rows = (await session.execute(
            select(CrmStaff.id, CrmStaff.name, CrmStaff.petty_float,
                   CrmStaff.bank_name, CrmStaff.bank_account))).all()

    by_staff: dict[str, dict] = {}
    for s in staff_rows:
        pending = drafts.get(s.id, 0)
        mine = [c for c in claims if c.staff_id == s.id]
        if not pending and not mine and not (s.petty_float or 0):
            continue                      # 149 個人裡只留跟零用金有關的
        submitted = sum(c.total_claim for c in mine)
        acct = s.bank_account or ""
        by_staff[s.id] = {
            "staff_id": s.id, "name": s.name,
            "opening_float": s.petty_float or 0,
            "draft_total": pending,          # 還沒送出的
            "claim_total": submitted,        # 已送出待付的
            "closing_float": (s.petty_float or 0) - pending - submitted,
            "bank": (f"{s.bank_name or ''} {acct[-4:]}" if acct else ""),
            # 🔴 缺帳號要在畫面上擋住，不是靜默出 0
            "bank_missing": not acct,
            "status": ("待請款" if (pending or submitted) else "已請款"),
        }
    return {"accounts": sorted(by_staff.values(),
                               key=lambda a: -(a["claim_total"] + a["draft_total"]))}


@router.get("/petty/claims", dependencies=[Depends(money_dep)])
async def petty_claims(request: Request, status: str = Query("待審")):
    """待審／已核准／已付款批次清單（含逐行明細）。"""
    _check_approver(request)
    factory = await _get_factory()
    async with factory() as session:
        claims = (await session.execute(
            select(CrmReimbursement)
            .where(CrmReimbursement.status == status)
            .order_by(CrmReimbursement.submitted_at.desc().nullslast())
        )).scalars().all()
        ids = [c.id for c in claims]
        rows = (await session.execute(
            select(CrmProjectExpense)
            .where(CrmProjectExpense.claim_id.in_(ids))) ).scalars().all() if ids else []
        names = dict((await session.execute(
            select(CrmProject.id, CrmProject.name))).all())
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
    factory = await _get_factory()
    async with factory() as session:
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
    factory = await _get_factory()
    async with factory() as session:
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
    factory = await _get_factory()
    async with factory() as session:
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
    factory = await _get_factory()
    async with factory() as session:
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
    factory = await _get_factory()
    async with factory() as session:
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
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProjectExpense.project_label,
                   safunc.count(CrmProjectExpense.id),
                   safunc.coalesce(safunc.sum(CrmProjectExpense.actual), 0))
            .where(CrmProjectExpense.project_label.isnot(None),
                   CrmProjectExpense.project_id.is_(None))
            .group_by(CrmProjectExpense.project_label)
            .order_by(safunc.count(CrmProjectExpense.id).desc()))).all()
    return {"labels": [{"label": r[0], "count": r[1], "total": r[2]} for r in rows]}


@router.post("/petty/bind-label", dependencies=[Depends(money_dep)])
async def petty_bind_label(request: Request, label: str = Query(...),
                           project_id: str = Query(...)):
    """把某個標籤底下所有未綁定的列，一次掛到指定專案。"""
    _check_approver(request)
    factory = await _get_factory()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if proj is None:
            raise HTTPException(status_code=404, detail="找不到此專案")
        rows = (await session.execute(
            select(CrmProjectExpense)
            .where(CrmProjectExpense.project_label == label,
                   CrmProjectExpense.project_id.is_(None)))).scalars().all()
        for r in rows:
            r.project_id = project_id
        await session.commit()
    return {"status": "ok", "bound": len(rows), "project": proj.name}


__all__ = ["petty_options", "my_petty"]
