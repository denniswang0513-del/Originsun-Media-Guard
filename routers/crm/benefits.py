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

import asyncio
import csv
import io
import os
import re
import uuid

from fastapi import Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select

from core.auth import check_admin_or_module
from core.finance_logic import local_day
from core.hr_logic import (BENEFIT_COMMITTED, BENEFIT_EDITABLE,
                           benefit_pool_balance, in_window,
                           pool_allowance_rollup, staff_allowance_balance,
                           validate_against_allowance, validate_benefit_entry)
from core.identity import resolve_current_staff
from core.ledger import require_entity
from core.schemas import (BenefitAllowancePayload, BenefitEntryPayload,
                          BenefitFundingPayload, BenefitPoolPayload)
from db.models import (CrmCashEntry, CrmPaymentRequest, CrmStaff,
                       HrBenefitAllowance, HrBenefitEntry, HrBenefitFunding,
                       HrBenefitPool)

from ._shared import (_assert_month_open, _crm_session, _fmt_day, _now,
                      _parse_day, money_dep, router)

# 審核／匯款要的模組（與零用金同一把 —— 審的都是別人的錢，沒理由分兩把鑰匙）
QUOTA_MODES = ("shared", "per_person")
APPROVE_MODULE = "finance_approve"
# 應付款的科目軸：cat_map 查的就是 CrmPaymentRequest.category
AP_CATEGORY = "員工福利"
EDITABLE_TEXT = "／".join(BENEFIT_EDITABLE)


def _check_approver(request: Request):
    """審核／匯款：金額權限（money_dep 在路由層）＋ 審核模組。兩者都要。"""
    check_admin_or_module(request, APPROVE_MODULE)


def _can_manage(request: Request, pool) -> bool:
    """這個人管得動這個池嗎（帳本 scope + 審核權）。布林版，不丟例外。

    🔴 判定順序很重要：要**先問管理權**再問「是不是自己的」。反過來的話，
    同時是管理者又是當事人的人（owner 自己就是股東兼員工）會落進員工那條
    狀態限制 —— 於是他補不了自己歷史紀錄的單據與心得（owner 2026-08-21 回報）。
    """
    try:
        require_entity(request, pool.entity or "parent", level="full")
        _check_approver(request)
        return True
    except HTTPException:
        return False


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
         "notes": p.notes or "",
         "quota": p.quota or "shared",
         "description": p.description or "",
         "attachments": list(p.attachments or []),
         "valid_from": _fmt_day(p.valid_from), "valid_to": _fmt_day(p.valid_to)}
    if fundings is not None:
        # 每人額度的池也算一份 shared 版的數字 —— 前端要顯示「已用」，
        # 而「已用」在兩種模式下是同一個意思（已核准＋已付款的合計）。
        d.update(benefit_pool_balance(fundings, entries or []))
    return d


def _allowance_dict(a) -> dict:
    return {"id": a.id, "pool_id": a.pool_id, "staff_id": a.staff_id,
            "staff_name": a.staff_name, "amount": int(a.amount or 0),
            "valid_from": _fmt_day(a.valid_from), "valid_to": _fmt_day(a.valid_to),
            "notes": a.notes or ""}


def _window_of(al, pool):
    """這份額度的有效期間 —— 列上沒填就繼承池的（§9.3）。回 (date, date)。"""
    lo = local_day(al.valid_from if al is not None and al.valid_from else pool.valid_from)
    hi = local_day(al.valid_to if al is not None and al.valid_to else pool.valid_to)
    return (lo.date() if lo else None, hi.date() if hi else None)


def _allowance_state(al, pool, entries) -> dict:
    """一份額度目前的樣子（含餘額）。`entries` 是這個人在這個池的所有登記。

    期間判定在這裡做完再交給純規則 —— hr_logic 保持無日期、無時區
    （時區邏輯只該有一份，見 _shared._fmt_day 的註解）。
    """
    lo, hi = _window_of(al, pool)
    rows = []
    for e in entries:
        d = local_day(e.spend_date)
        inside = in_window(d.date() if d else None, lo, hi)
        rows.append((e.status, e.amount, inside))
    st = staff_allowance_balance(al.amount if al is not None else 0, rows)
    st.update({"valid_from": lo, "valid_to": hi})
    return st


def _entry_dict(e, pool_name="") -> dict:
    """owner 2026-08-21：「有心得跟有單據讓我知道就好」—— 所以清單帶
    has_receipt / has_reflection 兩個旗標讓畫面標示，內容也一起帶
    （心得就幾行字、收據只是路徑，不值得為它多一支詳情端點）。"""
    reflection = (e.reflection or "").strip()
    return {"id": e.id, "pool_id": e.pool_id, "pool_name": pool_name,
            "staff_id": e.staff_id or "", "staff_name": e.staff_name or "",
            "title": e.title, "amount": int(e.amount or 0),
            "spend_date": _fmt_day(e.spend_date),
            "receipt_url": e.receipt_url or "", "status": e.status,
            "has_receipt": bool(e.receipt_url),
            "reflection": reflection, "has_reflection": bool(reflection),
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
        quota = body.quota if body.quota in QUOTA_MODES else "shared"
        p = HrBenefitPool(id=uuid.uuid4().hex[:16], entity=ent, name=name,
                          status=body.status or "open",
                          sort_order=int(body.sort_order or 0),
                          quota=quota,
                          description=body.description or None,
                          valid_from=_parse_day(body.valid_from),
                          valid_to=_parse_day(body.valid_to),
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
        if body.quota in QUOTA_MODES:
            p.quota = body.quota
        p.description = body.description or None
        p.valid_from = _parse_day(body.valid_from)
        p.valid_to = _parse_day(body.valid_to)
        # 🔴 attachments 不在這裡寫 —— 它不在 payload 裡，只能走上傳／刪除
        #    那兩支端點（否則舊表單整包寫回會把附件清空）。
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
        als = (await session.execute(select(HrBenefitAllowance).where(
            HrBenefitAllowance.pool_id == p.id))).scalars().all()
        if funds or ents or als:
            raise HTTPException(
                status_code=409,
                detail=f"這個項目底下還有 {len(funds)} 筆撥款、{len(ents)} 筆登記、"
                       f"{len(als)} 份額度，不能刪")
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
        als = (await session.execute(
            select(HrBenefitAllowance).where(HrBenefitAllowance.pool_id == pool_id)
            .order_by(HrBenefitAllowance.staff_name))).scalars().all()
        by_staff = {}
        for e in ents:
            by_staff.setdefault(e.staff_id or "", []).append(e)
        rows = []
        for a in als:
            st = _allowance_state(a, p, by_staff.get(a.staff_id, []))
            d = _allowance_dict(a)
            d.update({"used": st["used"], "pending": st["pending"],
                      "balance": st["balance"], "over": st["over"]})
            rows.append(d)
        d = _pool_dict(p, [f.amount for f in funds],
                       [(e.status, e.amount) for e in ents])
        d["allowance_rollup"] = pool_allowance_rollup(
            [r["amount"] for r in rows], [r["used"] for r in rows])
        return {"pool": d,
                "fundings": [_funding_dict(f) for f in funds],
                "allowances": rows,
                "entries": [_entry_dict(e, p.name) for e in ents]}


# ── 每人額度（年度活動，docs/BENEFIT_POOL_PLAN.md §9）────────────

async def _guard_allowance(session, p, staff_id, amount, spend_dt,
                           exclude_id=None):
    """每人額度模式下，這筆登記過得了嗎。共用池直接放行。

    🔴 這裡**要擋**（422），跟共用池不同：共用池超支只轉紅（那是公司該知道
    的事實），個人額度超額是「這張券本來就沒這麼多」，放過去只是把問題推到
    請款那一關才爆 —— 那時人已經花掉了。
    """
    if (p.quota or "shared") != "per_person":
        return
    al = (await session.execute(select(HrBenefitAllowance).where(
        HrBenefitAllowance.pool_id == p.id,
        HrBenefitAllowance.staff_id == staff_id))).scalars().first()
    if al is None:
        raise HTTPException(status_code=422,
                            detail="這個活動沒有發給你額度，請找管理員")
    ents = [x for x in (await session.execute(select(HrBenefitEntry).where(
        HrBenefitEntry.pool_id == p.id,
        HrBenefitEntry.staff_id == staff_id))).scalars().all()
        if x.id != exclude_id]     # 改自己那筆時，它不能吃自己的額度
    st = _allowance_state(al, p, ents)
    d = local_day(spend_dt)
    err = validate_against_allowance(
        amount, d.date() if d else None,
        {"amount": st["quota"], "valid_from": st["valid_from"],
         "valid_to": st["valid_to"], "available": st["available"]})
    if err:
        raise HTTPException(status_code=422, detail=err)


async def _allowance_or_404(session, allowance_id: str):
    a = await session.get(HrBenefitAllowance, allowance_id)
    if a is None:
        raise HTTPException(status_code=404, detail="找不到這份額度")
    return a


@router.post("/benefits/pools/{pool_id}/allowances", dependencies=[Depends(money_dep)])
async def add_allowance(pool_id: str, body: BenefitAllowancePayload,
                        request: Request):
    """發一份額度給某個人。🔴 一個人在一個活動裡只有一份 —— 重複發是
    帳對不起來的起點（DB 也有 unique index 兜底）。"""
    async with _crm_session() as session:
        p = await _pool_or_404(session, pool_id)
        require_entity(request, p.entity or "parent", level="full")
        _check_approver(request)
        st = await session.get(CrmStaff, (body.staff_id or "").strip())
        if st is None:
            raise HTTPException(status_code=422, detail="找不到這個人員")
        if int(body.amount or 0) <= 0:
            raise HTTPException(status_code=422, detail="額度要大於 0")
        dup = (await session.execute(select(HrBenefitAllowance).where(
            HrBenefitAllowance.pool_id == pool_id,
            HrBenefitAllowance.staff_id == st.id))).scalars().first()
        if dup is not None:
            raise HTTPException(status_code=409,
                                detail=f"{st.name} 在這個活動裡已經有一份額度了")
        a = HrBenefitAllowance(
            id=uuid.uuid4().hex[:16], pool_id=pool_id, staff_id=st.id,
            staff_name=st.name, amount=int(body.amount),
            valid_from=_parse_day(body.valid_from),
            valid_to=_parse_day(body.valid_to), notes=body.notes or None)
        session.add(a)
        await session.commit()
        return {"status": "ok", "allowance": _allowance_dict(a)}


@router.post("/benefits/pools/{pool_id}/allowances/bulk",
             dependencies=[Depends(money_dep)])
async def add_allowances_bulk(pool_id: str, request: Request,
                              amount: int = Query(0)):
    """全部在職員工各發一份（同額）。

    這顆按鈕就是 §9.3 說的「資格不做成規則，做成名單」的實作 —— 名單一次
    生出來，之後 owner 手動增刪。已經有額度的人**跳過不覆蓋**（改額度請走
    PUT，不要靠重跑這支）。
    """
    async with _crm_session() as session:
        p = await _pool_or_404(session, pool_id)
        require_entity(request, p.entity or "parent", level="full")
        _check_approver(request)
        if int(amount or 0) <= 0:
            raise HTTPException(status_code=422, detail="額度要大於 0")
        have = {a.staff_id for a in (await session.execute(
            select(HrBenefitAllowance).where(
                HrBenefitAllowance.pool_id == pool_id))).scalars().all()}
        staff = (await session.execute(select(CrmStaff).where(
            CrmStaff.status == "在職").order_by(CrmStaff.name))).scalars().all()
        added = 0
        for st in staff:
            if st.id in have:
                continue
            session.add(HrBenefitAllowance(
                id=uuid.uuid4().hex[:16], pool_id=pool_id, staff_id=st.id,
                staff_name=st.name, amount=int(amount)))
            added += 1
        await session.commit()
        return {"status": "ok", "added": added,
                "skipped": len(staff) - added, "total_staff": len(staff)}


@router.put("/benefits/allowances/{allowance_id}", dependencies=[Depends(money_dep)])
async def update_allowance(allowance_id: str, body: BenefitAllowancePayload,
                           request: Request):
    async with _crm_session() as session:
        a = await _allowance_or_404(session, allowance_id)
        p = await _pool_or_404(session, a.pool_id)
        require_entity(request, p.entity or "parent", level="full")
        _check_approver(request)
        if int(body.amount or 0) <= 0:
            raise HTTPException(status_code=422, detail="額度要大於 0")
        # 🔴 不換人 —— 換了等於把已經花掉的錢算到別人頭上。要換請刪了重發。
        a.amount = int(body.amount)
        a.valid_from = _parse_day(body.valid_from)
        a.valid_to = _parse_day(body.valid_to)
        a.notes = body.notes or None
        a.updated_at = _now()
        await session.commit()
        return {"status": "ok", "allowance": _allowance_dict(a)}


@router.delete("/benefits/allowances/{allowance_id}", dependencies=[Depends(money_dep)])
async def delete_allowance(allowance_id: str, request: Request):
    """已經用過的額度不准刪 —— 刪了那些登記就變成沒有額度依據的孤兒
    （同「只有空池能刪」的理由）。"""
    async with _crm_session() as session:
        a = await _allowance_or_404(session, allowance_id)
        p = await _pool_or_404(session, a.pool_id)
        require_entity(request, p.entity or "parent", level="full")
        _check_approver(request)
        used = (await session.execute(select(HrBenefitEntry).where(
            HrBenefitEntry.pool_id == a.pool_id,
            HrBenefitEntry.staff_id == a.staff_id))).scalars().all()
        if used:
            raise HTTPException(
                status_code=409,
                detail=f"{a.staff_name} 在這個活動已經登記了 {len(used)} 筆，不能刪額度")
        await session.delete(a)
        await session.commit()
    return {"status": "ok"}


# ── 說明附件（健檢方案的 PDF、券的圖）────────────────────────────

_MAX_ATTACH_BYTES = 20 * 1024 * 1024


@router.post("/benefits/pools/{pool_id}/files", dependencies=[Depends(money_dep)])
async def upload_pool_file(pool_id: str, request: Request,
                           file: UploadFile = File(...)):
    """項目說明的附件（owner 2026-08-21：健檢方案「可以打字、附上文件」）。

    🔴 存放與取檔沿用既有那一條路，不另造：收據 root 底下 `_福委會/_說明文件/`，
    取檔走既有的 `GET /api/v1/crm/receipt-file?path=`（已有路徑白名單守衛）。
    護欄也照抄零用金那兩道：副檔名黑名單＋串流上限。
    """
    from core.project_folders import BLOCKED_UPLOAD_EXTS, stream_to_disk
    from .costs import _receipts_root

    ext = os.path.splitext(file.filename or "")[1] or ".pdf"
    if ext.lower() in BLOCKED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"不接受的檔案格式：{ext}")

    async with _crm_session() as session:
        p = await _pool_or_404(session, pool_id)
        require_entity(request, p.entity or "parent", level="full")
        _check_approver(request)
        base = os.path.join(_receipts_root(), "_福委會", "_說明文件")
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as err:
            raise HTTPException(status_code=422, detail=f"資料夾無法使用：{err}")
        fid = uuid.uuid4().hex[:8]
        safe = re.sub(r'[\/:*?"<>|]', "", os.path.splitext(file.filename or "")[0])[:60]
        filepath = os.path.join(base, f"{p.name[:20]}_{safe}_{fid}{ext}")
        written = await asyncio.to_thread(stream_to_disk, file.file, filepath,
                                          _MAX_ATTACH_BYTES)
        # 🔴 JSONB 欄要**整個換掉**才會被 SQLAlchemy 偵測到有變（in-place append
        #    不會進 dirty set，commit 之後檔案在磁碟上、清單裡卻沒有它）。
        p.attachments = list(p.attachments or []) + [
            {"id": fid, "name": file.filename or f"{fid}{ext}",
             "path": filepath, "size": int(written or 0)}]
        p.updated_at = _now()
        await session.commit()
        return {"status": "ok", "pool": _pool_dict(p)}


@router.delete("/benefits/pools/{pool_id}/files/{file_id}",
               dependencies=[Depends(money_dep)])
async def delete_pool_file(pool_id: str, file_id: str, request: Request):
    """用 id 刪不用索引 —— 索引會隨著別人刪檔而位移，刪錯的是另一個檔。"""
    async with _crm_session() as session:
        p = await _pool_or_404(session, pool_id)
        require_entity(request, p.entity or "parent", level="full")
        _check_approver(request)
        keep, gone = [], None
        for f in (p.attachments or []):
            (keep.append(f) if f.get("id") != file_id else None)
            if f.get("id") == file_id:
                gone = f
        if gone is None:
            raise HTTPException(status_code=404, detail="找不到這個附件")
        p.attachments = keep
        p.updated_at = _now()
        await session.commit()
        # 磁碟上的檔刪不掉不算失敗（NAS 權限），但要留下訊息不靜默
        left = ""
        try:
            os.remove(gone.get("path") or "")
        except FileNotFoundError:
            pass
        except OSError as err:
            left = f"（磁碟上的檔沒刪掉：{err.__class__.__name__}）"
        return {"status": "ok", "note": left, "pool": _pool_dict(p)}


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
        allow_by_pool = {a.pool_id: a for a in (await session.execute(
            select(HrBenefitAllowance).where(
                HrBenefitAllowance.staff_id == staff.id))).scalars().all()}
    mine_by_pool = {}
    for e in mine:
        mine_by_pool.setdefault(e.pool_id, []).append(e)
    pools_out = []
    for p in pools:
        d = _pool_dict(p, *roll.get(p.id, ([], [])))
        # 「池餘額」是全公司共用的那一份，回答不了「我用了多少」——
        # 用同一支純規則算（fundings 傳空），前端不自己加總。
        ents = mine_by_pool.get(p.id, [])
        m = benefit_pool_balance([], [(x.status, x.amount) for x in ents])
        d["mine_used"] = m["used"]
        d["mine_pending"] = m["pending"]
        d["mine_count"] = len(ents)
        # 年度活動：我這一份額度的樣子（沒發給我就是 None，畫面要講出來）
        if (p.quota or "shared") == "per_person":
            al = allow_by_pool.get(p.id)
            if al is None:
                d["mine_allowance"] = None
            else:
                st = _allowance_state(al, p, ents)
                d["mine_allowance"] = {
                    "quota": st["quota"], "used": st["used"],
                    "pending": st["pending"], "balance": st["balance"],
                    "available": st["available"], "over": st["over"],
                    # 🔴 走 _fmt_day，不自己 strftime —— 時區處理只該有一份
                    #    （repo 的 test_expense_dates_are_formatted_in_taipei
                    #    就是在守這條，我這次真的被它攔下來過）
                    "valid_from": _fmt_day(al.valid_from or p.valid_from),
                    "valid_to": _fmt_day(al.valid_to or p.valid_to)}
        pools_out.append(d)
    return {"staff_name": staff.name, "pools": pools_out,
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
        spend = _parse_day(body.spend_date) or _now()
        await _guard_allowance(session, p, staff.id, int(body.amount), spend)
        e = HrBenefitEntry(
            id=uuid.uuid4().hex[:16], pool_id=p.id, staff_id=staff.id,
            staff_name=staff.name or "",           # 快照
            title=body.title.strip(), amount=int(body.amount),
            spend_date=spend,
            status="待審", reflection=(body.reflection or "").strip() or None,
            notes=body.notes or None)
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
        if body.spend_date:
            e.spend_date = _parse_day(body.spend_date) or e.spend_date
        # 🔴 改金額／改日期也要重驗 —— 只在建立時擋的話，登記 1 元再改成
        #    99999 就整個繞過去了。先把這筆排除再算餘額（它自己不能吃自己）。
        if int(body.amount) != int(e.amount or 0) or body.spend_date:
            p2 = await _pool_or_404(session, e.pool_id)
            await _guard_allowance(session, p2, staff.id, int(body.amount),
                                   e.spend_date, exclude_id=e.id)
        e.amount = int(body.amount)
        e.reflection = (body.reflection or "").strip() or None
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


# ── 單據上傳 ──────────────────────────────────────────────────────

@router.post("/benefits/entries/{entry_id}/receipt")
async def upload_benefit_receipt(entry_id: str, request: Request,
                                 file: UploadFile = File(...)):
    """上傳這筆登記的單據。**非必填**（owner 2026-08-21：「並不是一定要上傳
    才能請款，這樣才符合各種使用情境」）—— 沒傳照樣送得出去、照樣核得了。

    誰能傳：**管理者不限狀態**（要補歷史紀錄的單據 —— 匯進來的舊資料本來就
    沒有，owner 2026-08-21）；本人只能傳自己的、而且限待審／退回
    （已核准之後帳上掛著應付款，本人再換單據等於換憑證）。

    存到收據根目錄底下 `_福委會/{年月}/`，與零用金收據同一個 root
    （settings.receipts_root，要集中到 NAS 就指 NAS）。取檔走既有的
    `GET /api/v1/crm/receipt-file?path=` —— 那支已經有路徑白名單守衛。
    """
    from core.identity import resolve_current_staff
    from core.project_folders import BLOCKED_UPLOAD_EXTS, stream_to_disk
    from .costs import _MAX_RECEIPT_BYTES, _receipts_root

    ext = os.path.splitext(file.filename or "")[1] or ".jpg"
    if ext.lower() in BLOCKED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"不接受的檔案格式：{ext}")

    async with _crm_session() as session:
        e = await _entry_or_404(session, entry_id)
        p = await _pool_or_404(session, e.pool_id)
        if not _can_manage(request, p):
            # 不是管理者 → 只能傳自己的，而且限還在自己手上的狀態
            ident = await resolve_current_staff(request)
            mine = ident["staff"] is not None and e.staff_id == ident["staff"].id
            if not mine:
                raise HTTPException(status_code=403, detail="這不是你的登記")
            if e.status not in BENEFIT_EDITABLE:
                raise HTTPException(
                    status_code=409,
                    detail=f"{e.status}的登記不能換單據（要換請先退回）")

        day = _fmt_day(e.spend_date) or _fmt_day(_now())
        base = os.path.join(_receipts_root(), "_福委會", day[:7] or "nodate")
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as err:
            raise HTTPException(status_code=422, detail=f"資料夾無法使用：{err}")
        safe = re.sub(r'[\/:*?"<>|]', "", f"{p.name}_{e.staff_name}_{e.title}")[:60]
        fname = f"{day.replace('-', '')}_{safe}_{e.id[:8]}{ext}"
        filepath = os.path.join(base, fname)
        written = await asyncio.to_thread(stream_to_disk, file.file, filepath,
                                          _MAX_RECEIPT_BYTES)
        if written < 0:
            raise HTTPException(
                status_code=413,
                detail=f"單據超過 {_MAX_RECEIPT_BYTES // (1024 * 1024)}MB")
        e.receipt_url = filepath
        e.updated_at = _now()
        await session.commit()
        return {"status": "ok", "entry": _entry_dict(e, p.name)}


@router.put("/benefits/entries/{entry_id}/reflection")
async def set_entry_reflection(entry_id: str, body: BenefitEntryPayload,
                               request: Request):
    """管理端補／改心得筆記。**不限狀態** —— 匯進來的歷史紀錄本來就沒有心得，
    要能補上（owner 2026-08-21）。

    刻意只動 `reflection` 一欄：金額與項目在已核准之後改了，帳上那張應付款
    不會跟著動，兩邊就對不起來。要改那些請先退回。
    """
    async with _crm_session() as session:
        e = await _entry_or_404(session, entry_id)
        p = await _pool_or_404(session, e.pool_id)
        if not _can_manage(request, p):
            raise HTTPException(status_code=403, detail="沒有福委會的管理權限")
        e.reflection = (body.reflection or "").strip() or None
        e.updated_at = _now()
        await session.commit()
        return {"status": "ok", "entry": _entry_dict(e, p.name)}


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
        spend = _parse_day(body.spend_date) or _now()
        # 代登也走同一道守衛 —— 管理端繞過去的話，畫面上的餘額會跟實際
        # 對不起來，而且沒有任何跡象。要超額請去把額度調高（那是刻意的動作）。
        if (p.quota or "shared") == "per_person":
            if staff is None:
                raise HTTPException(status_code=422,
                                    detail="這是每人額度的活動，要指定是誰")
            await _guard_allowance(session, p, staff.id, int(body.amount), spend)
        e = HrBenefitEntry(
            id=uuid.uuid4().hex[:16], pool_id=p.id,
            staff_id=staff.id if staff else None,
            staff_name=(staff.name if staff else "") or "（未指定）",
            title=body.title.strip(), amount=int(body.amount),
            spend_date=spend,
            status="待審", reflection=(body.reflection or "").strip() or None,
            notes=body.notes or None)
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
