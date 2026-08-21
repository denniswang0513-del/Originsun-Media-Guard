# -*- coding: utf-8 -*-
"""福委會（docs/BENEFIT_POOL_PLAN.md）—— `/api/v1/crm/benefits/*`。

owner 2026-08-21 的原話：「我有幾個福利池，一個是快樂、一個是進修，這兩塊員工
都可以登記，他們登記後我審核通過，就進公司請款。我每年會撥一筆錢進這個池。」

    每年撥款 ──▶ 池（快樂／進修）
                  ▲
    員工登記 ──▶ 待審 ──owner 核准──▶ 已核准（產應付款，進公司請款）──▶ 已付款
                  └──退回──▶ 退回（本人可改可刪，改完再送）

掛在 CRM 的 `router` 上，因此自動繼承兩件事，**不另建守衛**：
  1. `_crm_read_guard` —— 要登入
  2. `MoneyRedactRoute` —— 沒 `money_view` 的人金額欄位被抹

🔴 但「自己登記的那幾筆」不受 `money_view` 管（同零用金 PETTY_CASH_PLAN §4）。
這件事**靠 scope 而不是靠抹鍵**達成：`/benefits/me` 一族的查詢一律
`WHERE staff_id = 我`，而 `staff_id` 只從 token 解（`resolve_current_staff`），
永不接受 client 傳進來的值。於是「看得到的都是自己的」是查詢的性質。

**金流管線一步都不自己造**：核准後產 `CrmPaymentRequest`（進應付帳款）、匯款時
結清成一列 `CrmCashEntry`，全部沿用零用金那條路，連冪等鍵（`payment_request_id`）
與「退回時撤掉幽靈負債」的規則都一樣。各寫一份的話，兩條路遲早會對不起來。

純規則（狀態字、餘額怎麼算）在 `core/hr_logic.py`，有單元測試；這裡只留 I/O。
"""
from __future__ import annotations

import csv
import io
import uuid

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select

from core.auth import check_admin_or_module
from core.hr_logic import (BENEFIT_COMMITTED, BENEFIT_EDITABLE,
                           benefit_pool_balance, validate_benefit_entry)
from core.identity import resolve_current_staff
from core.ledger import require_entity
from core.schemas import (BenefitEntryPayload, BenefitFundingPayload,
                          BenefitPoolPayload)
from db.models import (CrmCashEntry, CrmPaymentRequest, CrmStaff,
                       HrBenefitEntry, HrBenefitFunding, HrBenefitPool)

from ._shared import (_assert_month_open, _crm_session, _fmt_day, _now,
                      _parse_day, money_dep, router)

# 審核／匯款要的模組（與零用金同一把 —— 審的都是別人的錢，沒理由分兩把鑰匙）
APPROVE_MODULE = "finance_approve"
# 應付款的科目軸：cat_map 查的就是 CrmPaymentRequest.category
AP_CATEGORY = "員工福利"
EDITABLE_TEXT = "／".join(BENEFIT_EDITABLE)


def _check_approver(request: Request):
    """審核／匯款：金額權限（money_dep 在路由層）＋ 審核模組。兩者都要。"""
    check_admin_or_module(request, APPROVE_MODULE)


async def _my_staff(request: Request):
    """本人 —— staff_id 只從 token 解，永不收 client 傳的值。"""
    ident = await resolve_current_staff(request)
    if ident["staff"] is None:
        raise HTTPException(status_code=409,
                            detail="帳號尚未綁定人員檔案，請聯絡管理員")
    return ident["staff"]


def _pool_dict(p, fundings=None, entries=None) -> dict:
    d = {"id": p.id, "entity": p.entity or "parent", "name": p.name,
         "status": p.status or "open", "sort_order": int(p.sort_order or 0),
         "notes": p.notes or ""}
    if fundings is not None:
        d.update(benefit_pool_balance(fundings, entries or []))
    return d


def _entry_dict(e, pool_name="") -> dict:
    return {"id": e.id, "pool_id": e.pool_id, "pool_name": pool_name,
            "staff_id": e.staff_id or "", "staff_name": e.staff_name or "",
            "title": e.title, "amount": int(e.amount or 0),
            "spend_date": _fmt_day(e.spend_date),
            "receipt_url": e.receipt_url or "", "status": e.status,
            "payment_request_id": e.payment_request_id or "",
            "notes": e.notes or ""}


def _funding_dict(f) -> dict:
    return {"id": f.id, "pool_id": f.pool_id, "year": f.year,
            "amount": int(f.amount or 0), "fund_date": _fmt_day(f.fund_date),
            "notes": f.notes or ""}


async def _pool_or_404(session, pool_id: str) -> HrBenefitPool:
    p = await session.get(HrBenefitPool, pool_id)
    if p is None:
        raise HTTPException(status_code=404, detail="找不到這個福利池")
    return p


async def _entry_or_404(session, entry_id: str) -> HrBenefitEntry:
    e = await session.get(HrBenefitEntry, entry_id)
    if e is None:
        raise HTTPException(status_code=404, detail="找不到這筆登記")
    return e


async def _pool_rollup(session, pools) -> dict:
    """一次撈完所有池的撥款與登記再分組 —— 逐池查會是 N+1。"""
    ids = [p.id for p in pools]
    if not ids:
        return {}
    funds = (await session.execute(
        select(HrBenefitFunding.pool_id, HrBenefitFunding.amount)
        .where(HrBenefitFunding.pool_id.in_(ids)))).all()
    ents = (await session.execute(
        select(HrBenefitEntry.pool_id, HrBenefitEntry.status,
               HrBenefitEntry.amount)
        .where(HrBenefitEntry.pool_id.in_(ids)))).all()
    out: dict = {pid: ([], []) for pid in ids}
    for pid, amount in funds:
        out[pid][0].append(amount)
    for pid, status, amount in ents:
        out[pid][1].append((status, amount))
    return out


# ── 池 ────────────────────────────────────────────────────────────

@router.get("/benefits/pools", dependencies=[Depends(money_dep)])
async def list_pools(request: Request, entity: str = ""):
    ent = require_entity(request, entity, level="full")
    async with _crm_session() as session:
        pools = (await session.execute(
            select(HrBenefitPool).where(HrBenefitPool.entity == ent)
            .order_by(HrBenefitPool.sort_order,
                      HrBenefitPool.name))).scalars().all()
        roll = await _pool_rollup(session, pools)
    items = [_pool_dict(p, *roll.get(p.id, ([], []))) for p in pools]
    return {"items": items,
            "total_funded": sum(i["funded"] for i in items),
            "total_balance": sum(i["balance"] for i in items)}


@router.post("/benefits/pools", dependencies=[Depends(money_dep)])
async def create_pool(body: BenefitPoolPayload, request: Request):
    ent = require_entity(request, body.entity or "", level="full")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="池要有名字")
    async with _crm_session() as session:
        p = HrBenefitPool(id=uuid.uuid4().hex[:16], entity=ent, name=name,
                          status=body.status or "open",
                          sort_order=int(body.sort_order or 0),
                          notes=body.notes or None)
        session.add(p)
        await session.commit()
        return {"status": "ok", "pool": _pool_dict(p, [], [])}


@router.put("/benefits/pools/{pool_id}", dependencies=[Depends(money_dep)])
async def update_pool(pool_id: str, body: BenefitPoolPayload, request: Request):
    """🔴 一律不得換帳本 —— 換了等於把整池的錢搬到另一本帳
    （docs/LEDGER_ENTITY_PLAN.md 的鐵則，與 finance 各更新端點一致）。"""
    async with _crm_session() as session:
        p = await _pool_or_404(session, pool_id)
        require_entity(request, p.entity or "parent", level="full")
        if body.entity and body.entity != (p.entity or "parent"):
            raise HTTPException(status_code=422, detail="不可變更福利池所屬帳本")
        if body.name:
            p.name = body.name.strip()
        if body.status:
            p.status = body.status
        p.sort_order = int(body.sort_order or 0)
        p.notes = body.notes or None
        p.updated_at = _now()
        roll = await _pool_rollup(session, [p])
        await session.commit()
        return {"status": "ok", "pool": _pool_dict(p, *roll.get(p.id, ([], [])))}


@router.delete("/benefits/pools/{pool_id}", dependencies=[Depends(money_dep)])
async def delete_pool(pool_id: str, request: Request):
    """只有空池能刪。有撥款或登記的池刪掉＝那些錢的歸屬消失，帳上留下孤兒。"""
    async with _crm_session() as session:
        p = await _pool_or_404(session, pool_id)
        require_entity(request, p.entity or "parent", level="full")
        funds, ents = (await _pool_rollup(session, [p])).get(p.id, ([], []))
        if funds or ents:
            raise HTTPException(
                status_code=409,
                detail=f"這個池底下還有 {len(funds)} 筆撥款、{len(ents)} 筆登記，不能刪")
        await session.delete(p)
        await session.commit()
    return {"status": "ok"}


@router.get("/benefits/pools/{pool_id}", dependencies=[Depends(money_dep)])
async def pool_detail(pool_id: str, request: Request):
    async with _crm_session() as session:
        p = await _pool_or_404(session, pool_id)
        require_entity(request, p.entity or "parent", level="full")
        funds = (await session.execute(
            select(HrBenefitFunding).where(HrBenefitFunding.pool_id == pool_id)
            .order_by(HrBenefitFunding.year.desc()))).scalars().all()
        ents = (await session.execute(
            select(HrBenefitEntry).where(HrBenefitEntry.pool_id == pool_id)
            .order_by(HrBenefitEntry.spend_date.desc().nullslast()))).scalars().all()
        return {"pool": _pool_dict(p, [f.amount for f in funds],
                                   [(e.status, e.amount) for e in ents]),
                "fundings": [_funding_dict(f) for f in funds],
                "entries": [_entry_dict(e, p.name) for e in ents]}


# ── 撥款（每年放一筆進池）────────────────────────────────────────

@router.post("/benefits/pools/{pool_id}/fundings", dependencies=[Depends(money_dep)])
async def add_funding(pool_id: str, body: BenefitFundingPayload, request: Request):
    async with _crm_session() as session:
        p = await _pool_or_404(session, pool_id)
        require_entity(request, p.entity or "parent", level="full")
        if int(body.amount or 0) <= 0:
            raise HTTPException(status_code=422, detail="撥款金額要大於 0")
        when = _parse_day(body.fund_date) or _now()
        f = HrBenefitFunding(id=uuid.uuid4().hex[:16], pool_id=pool_id,
                             year=int(body.year or 0) or when.year,
                             amount=int(body.amount), fund_date=when,
                             notes=body.notes or None)
        session.add(f)
        await session.commit()
        return {"status": "ok", "funding": _funding_dict(f)}


@router.delete("/benefits/fundings/{funding_id}", dependencies=[Depends(money_dep)])
async def delete_funding(funding_id: str, request: Request):
    async with _crm_session() as session:
        f = await session.get(HrBenefitFunding, funding_id)
        if f is None:
            raise HTTPException(status_code=404, detail="找不到這筆撥款")
        p = await _pool_or_404(session, f.pool_id)
        require_entity(request, p.entity or "parent", level="full")
        await session.delete(f)
        await session.commit()
    return {"status": "ok"}


# ── 員工自助（own-scope，不受 money_view 管）──────────────────────

@router.get("/benefits/me")
async def my_benefits(request: Request):
    """我的登記 + 各池餘額。`money_view` 管不到這裡 —— 看得到的都是自己的。"""
    staff = await _my_staff(request)
    async with _crm_session() as session:
        pools = (await session.execute(
            select(HrBenefitPool).where(HrBenefitPool.status == "open")
            .order_by(HrBenefitPool.sort_order,
                      HrBenefitPool.name))).scalars().all()
        roll = await _pool_rollup(session, pools)
        names = {p.id: p.name for p in pools}
        mine = (await session.execute(
            select(HrBenefitEntry).where(HrBenefitEntry.staff_id == staff.id)
            .order_by(HrBenefitEntry.spend_date.desc().nullslast())
            .limit(200))).scalars().all()
    return {"staff_name": staff.name,
            "pools": [_pool_dict(p, *roll.get(p.id, ([], []))) for p in pools],
            "entries": [_entry_dict(e, names.get(e.pool_id, "")) for e in mine]}


@router.post("/benefits/me/entries")
async def add_my_entry(body: BenefitEntryPayload, request: Request):
    """自己登記一筆。**登記即待審** —— owner 的流程就是「登記後我審核通過」，
    中間再插一個草稿階段只是逼人多按一次送出。"""
    staff = await _my_staff(request)
    err = validate_benefit_entry(body.title, body.amount)
    if err:
        raise HTTPException(status_code=422, detail=err)
    async with _crm_session() as session:
        p = await _pool_or_404(session, body.pool_id)
        if (p.status or "open") != "open":
            raise HTTPException(status_code=409, detail="這個福利池已關閉")
        e = HrBenefitEntry(
            id=uuid.uuid4().hex[:16], pool_id=p.id, staff_id=staff.id,
            staff_name=staff.name or "",           # 快照
            title=body.title.strip(), amount=int(body.amount),
            spend_date=_parse_day(body.spend_date) or _now(),
            status="待審", notes=body.notes or None)
        session.add(e)
        await session.commit()
        return {"status": "ok", "entry": _entry_dict(e, p.name)}


@router.put("/benefits/me/entries/{entry_id}")
async def update_my_entry(entry_id: str, body: BenefitEntryPayload,
                          request: Request):
    """改自己的。🔴 `staff_id` 條件不可省 —— 少了它任何人都能改別人的登記。"""
    staff = await _my_staff(request)
    err = validate_benefit_entry(body.title, body.amount)
    if err:
        raise HTTPException(status_code=422, detail=err)
    async with _crm_session() as session:
        e = await _entry_or_404(session, entry_id)
        if e.staff_id != staff.id:
            raise HTTPException(status_code=403, detail="這不是你的登記")
        if e.status not in BENEFIT_EDITABLE:
            raise HTTPException(
                status_code=409,
                detail=f"只有{EDITABLE_TEXT}可以修改（目前：{e.status}）")
        e.title = body.title.strip()
        e.amount = int(body.amount)
        if body.spend_date:
            e.spend_date = _parse_day(body.spend_date) or e.spend_date
        e.notes = body.notes or None
        e.status = "待審"          # 退回後改完自動重新送審
        e.updated_at = _now()
        await session.commit()
        return {"status": "ok", "entry": _entry_dict(e)}


@router.delete("/benefits/me/entries/{entry_id}")
async def delete_my_entry(entry_id: str, request: Request):
    staff = await _my_staff(request)
    async with _crm_session() as session:
        e = await _entry_or_404(session, entry_id)
        if e.staff_id != staff.id:
            raise HTTPException(status_code=403, detail="這不是你的登記")
        if e.status not in BENEFIT_EDITABLE:
            raise HTTPException(
                status_code=409,
                detail=f"只有{EDITABLE_TEXT}可以刪除（目前：{e.status}）")
        await session.delete(e)
        await session.commit()
    return {"status": "ok"}


# ── 管理端：代登 + 審核 + 匯款 ────────────────────────────────────

@router.get("/benefits/entries", dependencies=[Depends(money_dep)])
async def list_entries(request: Request, status: str = Query(""),
                       pool_id: str = Query(""), entity: str = ""):
    """待審佇列與全部登記。預設不篩 —— 前端自己決定要看哪一段。"""
    ent = require_entity(request, entity, level="full")
    async with _crm_session() as session:
        pools = (await session.execute(
            select(HrBenefitPool).where(HrBenefitPool.entity == ent))).scalars().all()
        names = {p.id: p.name for p in pools}
        q = select(HrBenefitEntry).where(
            HrBenefitEntry.pool_id.in_([p.id for p in pools] or [""]))
        if status:
            q = q.where(HrBenefitEntry.status == status)
        if pool_id:
            q = q.where(HrBenefitEntry.pool_id == pool_id)
        rows = (await session.execute(
            q.order_by(HrBenefitEntry.spend_date.desc().nullslast())
        )).scalars().all()
    return {"items": [_entry_dict(e, names.get(e.pool_id, "")) for e in rows]}


@router.post("/benefits/entries", dependencies=[Depends(money_dep)])
async def add_entry_for(body: BenefitEntryPayload, request: Request):
    """管理端代登（員工不方便自己登記時）。與自助那條走同一組欄位檢查。"""
    _check_approver(request)
    err = validate_benefit_entry(body.title, body.amount)
    if err:
        raise HTTPException(status_code=422, detail=err)
    async with _crm_session() as session:
        p = await _pool_or_404(session, body.pool_id)
        require_entity(request, p.entity or "parent", level="full")
        staff = await session.get(CrmStaff, body.staff_id) if body.staff_id else None
        if body.staff_id and staff is None:
            raise HTTPException(status_code=404, detail="找不到這位員工")
        e = HrBenefitEntry(
            id=uuid.uuid4().hex[:16], pool_id=p.id,
            staff_id=staff.id if staff else None,
            staff_name=(staff.name if staff else "") or "（未指定）",
            title=body.title.strip(), amount=int(body.amount),
            spend_date=_parse_day(body.spend_date) or _now(),
            status="待審", notes=body.notes or None)
        session.add(e)
        await session.commit()
        return {"status": "ok", "entry": _entry_dict(e, p.name)}


@router.post("/benefits/entries/{entry_id}/approve", dependencies=[Depends(money_dep)])
async def approve_entry(entry_id: str, request: Request):
    """核准 → 產一張應付款（進公司請款／應付帳款，走既有金流管線）。

    冪等：已經有 `payment_request_id` 的不再重產（重按不會變兩張應付款）。
    """
    _check_approver(request)
    async with _crm_session() as session:
        e = await _entry_or_404(session, entry_id)
        p = await _pool_or_404(session, e.pool_id)
        ent = require_entity(request, p.entity or "parent", level="full")
        if e.status != "待審":
            raise HTTPException(status_code=409,
                                detail=f"只有待審的登記可以核准（目前：{e.status}）")
        if not e.payment_request_id:
            staff = await session.get(CrmStaff, e.staff_id) if e.staff_id else None
            when = e.spend_date or _now()
            ap = CrmPaymentRequest(
                id=uuid.uuid4().hex[:16], entity=ent, request_date=when,
                amount=int(e.amount or 0),
                summary=f"福委會 {p.name}／{e.staff_name}／{e.title}"[:255],
                category=AP_CATEGORY, payee_name=e.staff_name,
                # 身分證只在這裡從 crm_staff 帶（PII 單一正本，同零用金 _build_aps）
                payee_id=(staff.id_number if staff else None),
                payee_type="內部人員", payment_status="應付款",
                planned_month=when.strftime("%Y-%m"),
                notes=f"福委會 {p.name}")
            session.add(ap)
            e.payment_request_id = ap.id
        e.status = "已核准"
        e.updated_at = _now()
        await session.commit()
        return {"status": "ok", "entry": _entry_dict(e, p.name)}


@router.post("/benefits/entries/{entry_id}/reject", dependencies=[Depends(money_dep)])
async def reject_entry(entry_id: str, request: Request, reason: str = Query("")):
    """退回 → 撤掉還沒付款的那張應付款。

    🔴 不撤的話帳上會留**幽靈負債**：登記已經回到本人手上、應付帳款卻還掛著
    那筆錢，月結與現金流預測都會多算（零用金 `_clear_aps` 的教訓，同一條規則）。
    已付款的不准撤 —— 那筆錢真的出去了，要沖銷而不是把歷史抹掉。
    """
    _check_approver(request)
    async with _crm_session() as session:
        e = await _entry_or_404(session, entry_id)
        p = await _pool_or_404(session, e.pool_id)
        require_entity(request, p.entity or "parent", level="full")
        if e.status not in ("待審", "已核准"):
            raise HTTPException(status_code=409,
                                detail=f"這個狀態不能退回（目前：{e.status}）")
        if e.payment_request_id:
            ap = await session.get(CrmPaymentRequest, e.payment_request_id)
            if ap is not None:
                if ap.payment_status == "已付款" or ap.payment_date:
                    raise HTTPException(
                        status_code=409,
                        detail="這筆已經有付款紀錄，不能退回（請改走沖銷）")
                await session.delete(ap)
            e.payment_request_id = None
        e.status = "退回"
        if reason:
            e.notes = ((e.notes or "") + f"\n[退回] {reason}").strip()
        e.updated_at = _now()
        await session.commit()
        return {"status": "ok", "entry": _entry_dict(e, p.name)}


@router.post("/benefits/entries/{entry_id}/pay", dependencies=[Depends(money_dep)])
async def pay_entry(entry_id: str, request: Request,
                    payment_date: str = Query("")):
    """登記匯款 → 應付款結清成一列收支明細。

    冪等：已經有對應收支列的不再重複落帳（重按不會重複記帳，這是財務端最不能
    出的錯 —— 同零用金 `pay_claim`）。
    """
    _check_approver(request)
    when = _parse_day(payment_date) or _now()
    async with _crm_session() as session:
        e = await _entry_or_404(session, entry_id)
        p = await _pool_or_404(session, e.pool_id)
        ent = require_entity(request, p.entity or "parent", level="full")
        if e.status != "已核准":
            raise HTTPException(
                status_code=409,
                detail=f"只有已核准的登記可以登記匯款（目前：{e.status}）")
        if not e.payment_request_id:
            raise HTTPException(status_code=409, detail="這筆沒有對應的應付款")
        ap = await session.get(CrmPaymentRequest, e.payment_request_id)
        if ap is None:
            raise HTTPException(status_code=409, detail="對應的應付款已不存在")
        # 現金側落在匯款日 —— 鎖了就擋
        await _assert_month_open(session, when, entity=ent)
        posted = (await session.execute(
            select(CrmCashEntry.id)
            .where(CrmCashEntry.payment_request_id == ap.id))).first()
        ap.payment_status = "已付款"
        ap.payment_date = when
        made = 0
        if not posted:
            session.add(CrmCashEntry(
                id=uuid.uuid4().hex[:16], entity=ent,
                entry_date=when,                  # 現金側＝錢真的出去那天
                expense=int(ap.amount or 0), summary=ap.summary,
                category="請款",                   # → ap_settlement（費用已在 AP 認列）
                item=AP_CATEGORY, payee=e.staff_name,
                payment_date=when, payment_status="已付款",
                payment_request_id=ap.id, note=f"福委會 {p.name}"))
            made = 1
        e.status = "已付款"
        e.updated_at = _now()
        await session.commit()
        return {"status": "ok", "cash_entries": made,
                "entry": _entry_dict(e, p.name)}


# ── 送會計 ────────────────────────────────────────────────────────

async def _package(session, ent: str, year: int) -> dict:
    pools = (await session.execute(
        select(HrBenefitPool).where(HrBenefitPool.entity == ent)
        .order_by(HrBenefitPool.sort_order, HrBenefitPool.name))).scalars().all()
    names = {p.id: p.name for p in pools}
    ids = list(names) or [""]

    funds = (await session.execute(
        select(HrBenefitFunding).where(
            HrBenefitFunding.pool_id.in_(ids)))).scalars().all()
    ents = (await session.execute(
        select(HrBenefitEntry).where(
            HrBenefitEntry.pool_id.in_(ids),
            # 只給會計「錢已經確定要出去」的那些 —— 待審/退回不是帳
            HrBenefitEntry.status.in_(BENEFIT_COMMITTED)))).scalars().all()

    if year:
        funds = [f for f in funds if f.year == year]
        ents = [e for e in ents if _fmt_day(e.spend_date)[:4] == str(year)]

    by_pool: dict = {}
    for p in pools:
        by_pool[p.id] = {"pool": p.name, "funded": 0, "used": 0, "count": 0}
    for f in funds:
        by_pool[f.pool_id]["funded"] += int(f.amount or 0)
    for e in ents:
        by_pool[e.pool_id]["used"] += int(e.amount or 0)
        by_pool[e.pool_id]["count"] += 1
    for row in by_pool.values():
        row["balance"] = row["funded"] - row["used"]

    return {"year": year, "entity": ent,
            "summary": list(by_pool.values()),
            "fundings": [{**_funding_dict(f), "pool_name": names.get(f.pool_id, "")}
                         for f in sorted(funds, key=lambda x: x.year)],
            "entries": [_entry_dict(e, names.get(e.pool_id, ""))
                        for e in sorted(ents, key=lambda x: _fmt_day(x.spend_date))],
            "total_funded": sum(int(f.amount or 0) for f in funds),
            "total_used": sum(int(e.amount or 0) for e in ents)}


@router.get("/benefits/accounting-package", dependencies=[Depends(money_dep)])
async def accounting_package(request: Request, year: int = 0, entity: str = ""):
    """送會計的那一份：每個池的撥款／已用／餘額 + 撥款逐筆 + 支出逐筆。

    只收「已核准／已付款」—— 待審與退回還不是帳，送過去只會讓人對不起來。
    """
    _check_approver(request)
    ent = require_entity(request, entity, level="full")
    async with _crm_session() as session:
        return await _package(session, ent, int(year or 0))


@router.get("/benefits/accounting-package.csv", dependencies=[Depends(money_dep)])
async def accounting_package_csv(request: Request, year: int = 0,
                                 entity: str = ""):
    """同一份的 CSV。會計要的是能丟進 Excel 的東西，不是 JSON。"""
    _check_approver(request)
    ent = require_entity(request, entity, level="full")
    async with _crm_session() as session:
        pkg = await _package(session, ent, int(year or 0))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["區別", "福利池", "年度/日期", "項目", "員工", "金額", "狀態", "備註"])
    for f in pkg["fundings"]:
        w.writerow(["撥款", f["pool_name"], f["year"], "公司撥款進池", "",
                    f["amount"], "", (f["notes"] or "").replace("\n", " ")])
    for e in pkg["entries"]:
        w.writerow(["支出", e["pool_name"], e["spend_date"], e["title"],
                    e["staff_name"], -e["amount"], e["status"],
                    (e["notes"] or "").replace("\n", " ")])
    w.writerow([])
    for s in pkg["summary"]:
        w.writerow(["小計", s["pool"], "", "撥款/已用/餘額", "",
                    f"{s['funded']}/{s['used']}/{s['balance']}", "", ""])
    span = str(year or "全部")
    # BOM：Excel 沒有它會把中文開成亂碼
    return Response(
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 'attachment; filename="welfare_' + span + '.csv"'})
