# -*- coding: utf-8 -*-
"""福利池（docs/BENEFIT_POOL_PLAN.md）—— `/api/v1/crm/benefits/*`。

掛在 CRM 的 `router` 上，因此自動繼承兩件事，**不另建守衛**：
  1. `_crm_read_guard` —— 要登入
  2. `MoneyRedactRoute` —— 沒 `money_view` 的人金額欄位被抹

為什麼不塞進零用金（routers/crm/petty.py）：零用金的形狀是「員工先墊、憑收據
核銷」，福利有一半是公司**直接發給**員工（生日禮金／三節／婚喪喜慶），沒有單據
可貼；而且福利多兩件零用金不管的事 —— 額度與 `taxable`（要不要併入員工個人所得）。

但**金流管線一步都不自己造**：核准後產 `CrmPaymentRequest`（進應付帳款）、匯款
時結清成一列 `CrmCashEntry`，全部沿用零用金那條路，連冪等鍵（`payment_request_id`）
與「退回時撤掉幽靈負債」的規則都一樣。各寫一份的話，兩條路遲早會對不起來。

純規則（項目清單、狀態字、餘額怎麼算、會計怎麼分區）在 `core/hr_logic.py`，
有單元測試；這裡只留 I/O。
"""
from __future__ import annotations

import csv
import io
import uuid

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select

from core.auth import check_admin_or_module
from core.hr_logic import (BENEFIT_CATEGORIES, BENEFIT_EDITABLE, BENEFIT_KINDS,
                           BENEFIT_LOCKED, benefit_accounting_split,
                           benefit_pool_balance, validate_benefit_grant)
from core.ledger import require_entity
from core.schemas import BenefitGrantPayload, BenefitPoolPayload
from db.models import (CrmCashEntry, CrmPaymentRequest, CrmStaff,
                       HrBenefitGrant, HrBenefitPool)

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


def _pool_dict(p, grants=None) -> dict:
    d = {"id": p.id, "entity": p.entity or "parent", "year": p.year,
         "name": p.name, "budget": int(p.budget or 0),
         "status": p.status or "open", "notes": p.notes or "",
         "created_at": _fmt_day(p.created_at)}
    if grants is not None:
        d.update(benefit_pool_balance(p.budget, grants))
    return d


def _grant_dict(g) -> dict:
    return {"id": g.id, "pool_id": g.pool_id, "staff_id": g.staff_id or "",
            "staff_name": g.staff_name or "", "category": g.category,
            "kind": g.kind, "amount": int(g.amount or 0),
            "grant_date": _fmt_day(g.grant_date), "taxable": int(g.taxable or 0),
            "receipt_url": g.receipt_url or "", "status": g.status,
            "payment_request_id": g.payment_request_id or "",
            "notes": g.notes or ""}


async def _pool_or_404(session, pool_id: str) -> HrBenefitPool:
    p = await session.get(HrBenefitPool, pool_id)
    if p is None:
        raise HTTPException(status_code=404, detail="找不到這個福利池")
    return p


async def _grant_or_404(session, grant_id: str) -> HrBenefitGrant:
    g = await session.get(HrBenefitGrant, grant_id)
    if g is None:
        raise HTTPException(status_code=404, detail="找不到這筆福利動支")
    return g


async def _grants_of(session, pool_id: str) -> list:
    return (await session.execute(
        select(HrBenefitGrant).where(HrBenefitGrant.pool_id == pool_id)
        .order_by(HrBenefitGrant.grant_date.desc().nullslast()))).scalars().all()


# ── 池 ────────────────────────────────────────────────────────────

@router.get("/benefits/options")
async def benefit_options():
    """建立表單的選項。清單正本在 core.hr_logic —— 前端不各自寫死一份。"""
    async with _crm_session() as session:
        staff = (await session.execute(
            select(CrmStaff.id, CrmStaff.name).order_by(CrmStaff.name))).all()
    return {"categories": list(BENEFIT_CATEGORIES), "kinds": list(BENEFIT_KINDS),
            "staff": [{"id": s.id, "name": s.name} for s in staff]}


@router.get("/benefits/pools", dependencies=[Depends(money_dep)])
async def list_pools(request: Request, year: int = 0, entity: str = ""):
    ent = require_entity(request, entity, level="full")
    async with _crm_session() as session:
        q = select(HrBenefitPool).where(HrBenefitPool.entity == ent)
        if year:
            q = q.where(HrBenefitPool.year == year)
        pools = (await session.execute(
            q.order_by(HrBenefitPool.year.desc(),
                       HrBenefitPool.name))).scalars().all()
        # 一次撈完所有池的動支再分組 —— 逐池查會是 N+1
        ids = [p.id for p in pools]
        rows = (await session.execute(
            select(HrBenefitGrant.pool_id, HrBenefitGrant.status,
                   HrBenefitGrant.amount)
            .where(HrBenefitGrant.pool_id.in_(ids)))).all() if ids else []
        by_pool: dict = {}
        for pid, status, amount in rows:
            by_pool.setdefault(pid, []).append((status, amount))
    items = [_pool_dict(p, by_pool.get(p.id, [])) for p in pools]
    return {"items": items,
            "total_budget": sum(i["budget"] for i in items),
            "total_used": sum(i["used"] for i in items)}


@router.post("/benefits/pools", dependencies=[Depends(money_dep)])
async def create_pool(body: BenefitPoolPayload, request: Request):
    ent = require_entity(request, body.entity or "", level="full")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="池要有名字")
    year = int(body.year or 0) or _now().year
    async with _crm_session() as session:
        p = HrBenefitPool(id=uuid.uuid4().hex[:16], entity=ent, year=year,
                          name=name, budget=int(body.budget or 0),
                          status=body.status or "open",
                          notes=body.notes or None)
        session.add(p)
        await session.commit()
        return {"status": "ok", "pool": _pool_dict(p, [])}


@router.put("/benefits/pools/{pool_id}", dependencies=[Depends(money_dep)])
async def update_pool(pool_id: str, body: BenefitPoolPayload, request: Request):
    """改池。🔴 一律不得換帳本 —— 換了等於把整池的錢搬到另一本帳
    （docs/LEDGER_ENTITY_PLAN.md 的鐵則，與 finance 各更新端點一致）。"""
    async with _crm_session() as session:
        p = await _pool_or_404(session, pool_id)
        require_entity(request, p.entity or "parent", level="full")
        if body.entity and body.entity != (p.entity or "parent"):
            raise HTTPException(status_code=422, detail="不可變更福利池所屬帳本")
        if body.name:
            p.name = body.name.strip()
        if body.year:
            p.year = int(body.year)
        p.budget = int(body.budget or 0)
        if body.status:
            p.status = body.status
        p.notes = body.notes or None
        p.updated_at = _now()
        grants = [(g.status, g.amount) for g in await _grants_of(session, pool_id)]
        await session.commit()
        return {"status": "ok", "pool": _pool_dict(p, grants)}


@router.delete("/benefits/pools/{pool_id}", dependencies=[Depends(money_dep)])
async def delete_pool(pool_id: str, request: Request):
    """只有空池能刪。有動支的池刪掉＝那些錢的歸屬消失，帳上留下孤兒。"""
    async with _crm_session() as session:
        p = await _pool_or_404(session, pool_id)
        require_entity(request, p.entity or "parent", level="full")
        n = len(await _grants_of(session, pool_id))
        if n:
            raise HTTPException(status_code=409,
                                detail=f"這個池底下還有 {n} 筆動支，不能刪")
        await session.delete(p)
        await session.commit()
    return {"status": "ok"}


@router.get("/benefits/pools/{pool_id}", dependencies=[Depends(money_dep)])
async def pool_detail(pool_id: str, request: Request):
    async with _crm_session() as session:
        p = await _pool_or_404(session, pool_id)
        require_entity(request, p.entity or "parent", level="full")
        grants = await _grants_of(session, pool_id)
        return {"pool": _pool_dict(p, [(g.status, g.amount) for g in grants]),
                "grants": [_grant_dict(g) for g in grants]}


# ── 動支 ──────────────────────────────────────────────────────────

@router.post("/benefits/pools/{pool_id}/grants", dependencies=[Depends(money_dep)])
async def create_grant(pool_id: str, body: BenefitGrantPayload, request: Request):
    async with _crm_session() as session:
        p = await _pool_or_404(session, pool_id)
        require_entity(request, p.entity or "parent", level="full")
        if (p.status or "open") != "open":
            raise HTTPException(status_code=409,
                                detail="這個福利池已關閉，不能再動支")
        err = validate_benefit_grant(body.category, body.kind, body.amount,
                                     body.taxable)
        if err:
            raise HTTPException(status_code=422, detail=err)
        staff = await session.get(CrmStaff, body.staff_id) if body.staff_id else None
        if body.staff_id and staff is None:
            raise HTTPException(status_code=404, detail="找不到這位員工")
        g = HrBenefitGrant(
            id=uuid.uuid4().hex[:16], pool_id=pool_id,
            staff_id=staff.id if staff else None,
            # 快照：人員改名不該讓歷史單跟著變
            staff_name=(staff.name if staff else "") or "（未指定）",
            category=body.category, kind=body.kind, amount=int(body.amount or 0),
            grant_date=_parse_day(body.grant_date) or _now(),
            taxable=int(body.taxable or 0), status="草稿",
            notes=body.notes or None)
        session.add(g)
        await session.commit()
        return {"status": "ok", "grant": _grant_dict(g)}


@router.put("/benefits/grants/{grant_id}", dependencies=[Depends(money_dep)])
async def update_grant(grant_id: str, body: BenefitGrantPayload, request: Request):
    """🔴 已核准／已付款不准改 —— 那時應付帳款已經掛著一張對應的單，
    這裡改了金額，帳上那張不會跟著動，兩邊就對不起來。"""
    async with _crm_session() as session:
        g = await _grant_or_404(session, grant_id)
        p = await _pool_or_404(session, g.pool_id)
        require_entity(request, p.entity or "parent", level="full")
        if g.status in BENEFIT_LOCKED:
            raise HTTPException(status_code=409,
                                detail=f"{g.status}的動支不能修改（要改請先退回）")
        err = validate_benefit_grant(body.category, body.kind, body.amount,
                                     body.taxable)
        if err:
            raise HTTPException(status_code=422, detail=err)
        if body.staff_id:
            staff = await session.get(CrmStaff, body.staff_id)
            if staff is None:
                raise HTTPException(status_code=404, detail="找不到這位員工")
            g.staff_id = staff.id
            g.staff_name = staff.name or g.staff_name
        g.category = body.category
        g.kind = body.kind
        g.amount = int(body.amount or 0)
        g.taxable = int(body.taxable or 0)
        if body.grant_date:
            g.grant_date = _parse_day(body.grant_date) or g.grant_date
        g.notes = body.notes or None
        g.updated_at = _now()
        await session.commit()
        return {"status": "ok", "grant": _grant_dict(g)}


@router.delete("/benefits/grants/{grant_id}", dependencies=[Depends(money_dep)])
async def delete_grant(grant_id: str, request: Request):
    async with _crm_session() as session:
        g = await _grant_or_404(session, grant_id)
        p = await _pool_or_404(session, g.pool_id)
        require_entity(request, p.entity or "parent", level="full")
        if g.status in BENEFIT_LOCKED:
            raise HTTPException(status_code=409,
                                detail=f"{g.status}的動支不能刪（要刪請先退回）")
        await session.delete(g)
        await session.commit()
    return {"status": "ok"}


@router.post("/benefits/grants/{grant_id}/submit", dependencies=[Depends(money_dep)])
async def submit_grant(grant_id: str, request: Request):
    async with _crm_session() as session:
        g = await _grant_or_404(session, grant_id)
        p = await _pool_or_404(session, g.pool_id)
        require_entity(request, p.entity or "parent", level="full")
        if g.status not in BENEFIT_EDITABLE:
            raise HTTPException(
                status_code=409,
                detail=f"只有{EDITABLE_TEXT}可以送審（目前：{g.status}）")
        g.status = "待審"
        g.updated_at = _now()
        await session.commit()
        return {"status": "ok", "grant": _grant_dict(g)}


# ── 審核 / 匯款（要 money_view + finance_approve）──────────────────

@router.post("/benefits/grants/{grant_id}/approve", dependencies=[Depends(money_dep)])
async def approve_grant(grant_id: str, request: Request):
    """核准 → 產一張應付款（進應付帳款，走既有金流管線）。

    冪等：已經有 `payment_request_id` 的不再重產（重按不會變兩張應付款）。
    """
    _check_approver(request)
    async with _crm_session() as session:
        g = await _grant_or_404(session, grant_id)
        p = await _pool_or_404(session, g.pool_id)
        ent = require_entity(request, p.entity or "parent", level="full")
        if g.status != "待審":
            raise HTTPException(status_code=409,
                                detail=f"只有待審的動支可以核准（目前：{g.status}）")
        if not g.payment_request_id:
            staff = await session.get(CrmStaff, g.staff_id) if g.staff_id else None
            when = g.grant_date or _now()
            note = f"福利池 {p.name}"
            if int(g.taxable or 0):
                note += "（併入個人所得）"
            ap = CrmPaymentRequest(
                id=uuid.uuid4().hex[:16], entity=ent, request_date=when,
                amount=int(g.amount or 0),
                summary=f"員工福利 {g.staff_name}／{g.category}",
                category=AP_CATEGORY, payee_name=g.staff_name,
                # 身分證只在這裡從 crm_staff 帶（PII 單一正本，同零用金 _build_aps）
                payee_id=(staff.id_number if staff else None),
                payee_type="內部人員", payment_status="應付款",
                planned_month=when.strftime("%Y-%m"), notes=note)
            session.add(ap)
            g.payment_request_id = ap.id
        g.status = "已核准"
        g.updated_at = _now()
        await session.commit()
        return {"status": "ok", "grant": _grant_dict(g)}


@router.post("/benefits/grants/{grant_id}/reject", dependencies=[Depends(money_dep)])
async def reject_grant(grant_id: str, request: Request, reason: str = Query("")):
    """退回 → 撤掉還沒付款的那張應付款。

    🔴 不撤的話帳上會留**幽靈負債**：動支已經回到草稿、應付帳款卻還掛著那筆錢，
    月結與現金流預測都會多算（零用金 `_clear_aps` 的教訓，同一條規則）。
    已付款的不准撤 —— 那筆錢真的出去了，要沖銷而不是把歷史抹掉。
    """
    _check_approver(request)
    async with _crm_session() as session:
        g = await _grant_or_404(session, grant_id)
        p = await _pool_or_404(session, g.pool_id)
        require_entity(request, p.entity or "parent", level="full")
        if g.status not in ("待審", "已核准"):
            raise HTTPException(status_code=409,
                                detail=f"這個狀態不能退回（目前：{g.status}）")
        if g.payment_request_id:
            ap = await session.get(CrmPaymentRequest, g.payment_request_id)
            if ap is not None:
                if ap.payment_status == "已付款" or ap.payment_date:
                    raise HTTPException(
                        status_code=409,
                        detail="這筆已經有付款紀錄，不能退回（請改走沖銷）")
                await session.delete(ap)
            g.payment_request_id = None
        g.status = "退回"
        if reason:
            g.notes = ((g.notes or "") + f"\n[退回] {reason}").strip()
        g.updated_at = _now()
        await session.commit()
        return {"status": "ok", "grant": _grant_dict(g)}


@router.post("/benefits/grants/{grant_id}/pay", dependencies=[Depends(money_dep)])
async def pay_grant(grant_id: str, request: Request,
                    payment_date: str = Query("")):
    """登記匯款 → 應付款結清成一列收支明細。

    冪等：已經有對應收支列的不再重複落帳（重按不會重複記帳，這是財務端最不能
    出的錯 —— 同零用金 `pay_claim`）。
    """
    _check_approver(request)
    when = _parse_day(payment_date) or _now()
    async with _crm_session() as session:
        g = await _grant_or_404(session, grant_id)
        p = await _pool_or_404(session, g.pool_id)
        ent = require_entity(request, p.entity or "parent", level="full")
        if g.status != "已核准":
            raise HTTPException(
                status_code=409,
                detail=f"只有已核准的動支可以登記匯款（目前：{g.status}）")
        if not g.payment_request_id:
            raise HTTPException(status_code=409, detail="這筆沒有對應的應付款")
        ap = await session.get(CrmPaymentRequest, g.payment_request_id)
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
                item=AP_CATEGORY, payee=g.staff_name,
                payment_date=when, payment_status="已付款",
                payment_request_id=ap.id, note=f"福利池 {p.name}"))
            made = 1
        g.status = "已付款"
        g.updated_at = _now()
        await session.commit()
        return {"status": "ok", "cash_entries": made, "grant": _grant_dict(g)}


# ── 會計交付 ──────────────────────────────────────────────────────

async def _package(session, ent: str, year: int, month: str) -> dict:
    q = (select(HrBenefitGrant, HrBenefitPool)
         .join(HrBenefitPool, HrBenefitPool.id == HrBenefitGrant.pool_id)
         .where(HrBenefitPool.entity == ent,
                # 只給會計「錢已經確定要出去」的那些 —— 草稿/待審/退回不是帳
                HrBenefitGrant.status.in_(("已核准", "已付款"))))
    if year:
        q = q.where(HrBenefitPool.year == year)
    rows = (await session.execute(q)).all()
    ids = {g.staff_id for g, _ in rows if g.staff_id}
    id_numbers = dict((await session.execute(
        select(CrmStaff.id, CrmStaff.id_number)
        .where(CrmStaff.id.in_(ids)))).all()) if ids else {}

    grants = []
    for g, pool in rows:
        day = _fmt_day(g.grant_date)
        if month and not day.startswith(month):
            continue
        grants.append({**_grant_dict(g), "pool_name": pool.name,
                       "year": pool.year, "date": day,
                       # 身分證只在交付包出現（扣繳憑單要），清單端點不帶
                       "id_number": id_numbers.get(g.staff_id) or ""})
    split = benefit_accounting_split(grants)

    pools_q = select(HrBenefitPool).where(HrBenefitPool.entity == ent)
    if year:
        pools_q = pools_q.where(HrBenefitPool.year == year)
    pools = (await session.execute(pools_q)).scalars().all()
    by_pool: dict = {}
    for g, pool in rows:
        by_pool.setdefault(pool.id, []).append((g.status, g.amount))
    return {"year": year, "month": month, "entity": ent,
            "pools": [_pool_dict(p, by_pool.get(p.id, [])) for p in pools],
            **split}


@router.get("/benefits/accounting-package", dependencies=[Depends(money_dep)])
async def accounting_package(request: Request, year: int = 0,
                             month: str = Query(""), entity: str = ""):
    """送會計的那一份。兩區分開，因為兩區的用途不同：

      · 併入個人所得 → 按**人**彙總，會計年底開扣繳憑單用（附身分證）
      · 公司費用     → 按**項目**彙總，會計要的是科目

    只收「已核准／已付款」—— 草稿與待審還不是帳，送過去只會讓人對不起來。
    """
    _check_approver(request)
    ent = require_entity(request, entity, level="full")
    async with _crm_session() as session:
        return await _package(session, ent, int(year or 0), month or "")


@router.get("/benefits/accounting-package.csv", dependencies=[Depends(money_dep)])
async def accounting_package_csv(request: Request, year: int = 0,
                                 month: str = Query(""), entity: str = ""):
    """同一份的 CSV。會計要的是能丟進 Excel 的東西，不是 JSON。"""
    _check_approver(request)
    ent = require_entity(request, entity, level="full")
    async with _crm_session() as session:
        pkg = await _package(session, ent, int(year or 0), month or "")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["區別", "日期", "員工", "身分證", "項目", "方式", "金額",
                "福利池", "狀態", "備註"])
    for label, key in (("併入個人所得", "personal_income"),
                       ("公司費用", "company_expense")):
        for r in sorted(pkg[key]["rows"], key=lambda x: x["date"]):
            w.writerow([label, r["date"], r["staff_name"], r["id_number"],
                        r["category"], r["kind"], r["amount"], r["pool_name"],
                        r["status"], (r["notes"] or "").replace("\n", " ")])
        w.writerow([f"{label} 合計", "", "", "", "", "", pkg[key]["total"]])
    span = str(year or "全部") + (("-" + month.split("-")[-1]) if month else "")
    # BOM：Excel 沒有它會把中文開成亂碼
    return Response(
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 'attachment; filename="benefits_' + span + '.csv"'})
