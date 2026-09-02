# -*- coding: utf-8 -*-
"""routers/crm/payments.py — 請款單（含預支款、批次付款、CSV 匯入）。

2026-08-30 從 `finance.py` 切出來。那個檔 2,996 行 —— 超過 AI 單次讀取上限
（2,000 行），而且是這三週改動次數第一名（56 次）：**最常改的檔案正好是
一次讀不完的那個**，改起來等於盲改。

怎麼決定切在哪：不是照註解分隔線（那樣切會看到假的循環依賴），而是
① 函式呼叫圖的強連通分量 —— 實測**零循環**，所以拆得開；
② 每個 helper 歸給「用到它的端點群」，多群共用的留在 finance；
③ 迭代到不動點，任何造成反向邊的名字拉回 finance。
結果本檔**只 import finance，沒有反向依賴**。

URL 一條都沒變，route 一樣掛在 `_shared.router` 上。
🔴 檔內順序不可重排：`/{id}` 這種吃萬用字元的路徑必須排在具名路徑之後
（FastAPI 先註冊先贏）。
"""
from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, UploadFile, File, Query

from core.finance_logic import (month_of)
from core.auth import check_logged_in
from core.ledger import (require_entity,
                         not_mine_project as _not_mine_project)
from core.project_match import prepare as prepare_projects
from core.project_match import suggest_project
from core.project_link import PAYMENT_CATEGORIES as _PAYMENT_LINK_CATEGORIES
from core.schemas import (PaymentRequestPayload)

from ._shared import (router, money_dep, _require_db,
                      _get_factory, _fmt_day, _now,
                      _parse_shoot_date, _assert_month_open, _locked_month_set, _raise_locked_batch, map_csv_row)

# 這個檔案唯一用得到發票檔那邊的東西：改發票時要跟著改檔名

try:
    from ._shared import (select, or_, CrmProject, CrmInvoice,
                          CrmPaymentRequest, CrmCashEntry,
                          CrmProjectExpense)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

# ── CSV 匯入共用 ────────────────────────────────────────────


# 正本在 finance.py 的共用 helper（單向依賴：finance 不用本檔）
from .finance import (  # noqa: F401
    _entity_for_write,
    _get_mine_project,
    _import_money_csv,
    _mine_or_admin_write,
    _mine_or_admin_write_rows,
    _parse_money,
    sync_remit_status)



# ── Payment Request Helpers ─────────────────────────────────

async def _invoice_link_for(session, payments, ent) -> dict:
    """請款單 → 它對應的那張發票 {payment_id: (invoice_id, title)}。

    列表顯示抬頭而不是號碼 —— 「MJ00094858」對人沒有意義，「思沙龍精華製作EP 02」才有。

    🔴 不要用 join 去撈。invoice_number **不是唯一鍵**，join 一對多就會讓同一張
    請款單在清單裡分身（owner 2026-08-21 截圖：一張 TEDMA 被畫成四列，total 也
    跟著虛胖）。當時的觸發點是空字串：請款單的號碼是 ''，帳上又有 5 張無號發票
    的號碼也是 '' → '' = '' 成立。
    也不要用 correlated 子查詢：那會變成每列跑一次 SubPlan（815 張 × 掃 400 張
    發票），而且 replace() 是欄位上的函式，走不到索引。
    一次把發票撈回來在 Python 查表 —— 兩個問題一起沒有。

    對得上的兩條路：① source_invoice_id（唯一、無號發票也有）
                    ② 舊資料的發票號碼（**空號不比對** —— 兩張都沒號碼不代表是
                       同一張，那是「不知道」不是「相等」）
    """
    ids = {p.source_invoice_id for p in payments if p.source_invoice_id}
    nos = {p.invoice_number.replace("-", "") for p in payments if p.invoice_number}
    if not ids and not nos:
        return {}
    conds = []
    if ids:
        conds.append(CrmInvoice.id.in_(ids))
    if nos:
        conds.append(CrmInvoice.invoice_number.isnot(None))
    invs = (await session.execute(
        select(CrmInvoice.id, CrmInvoice.invoice_number, CrmInvoice.title)
        .where(CrmInvoice.entity == ent, or_(*conds)))).all()   # 別跨帳本抓標題
    by_id = {i: t for i, _n, t in invs}
    by_no = {}                                   # 號碼 → (id, 抬頭)
    for i, n, t in invs:
        key = (n or "").replace("-", "")
        if key:
            by_no.setdefault(key, (i, t))
    out = {}
    for p in payments:
        if p.source_invoice_id and p.source_invoice_id in by_id:
            out[p.id] = (p.source_invoice_id, by_id[p.source_invoice_id])
        elif p.invoice_number:
            hit = by_no.get(p.invoice_number.replace("-", ""))
            if hit:
                out[p.id] = hit
    return out


def _to_payment_dict(p, project_name: str = "", invoice_id: str = "",
                     invoice_title: str = "") -> dict:
    return {
        # 代開發票的中文名。列表顯示它而不是發票號碼 —— 「MJ00094858」對人沒有
        # 意義，「思沙龍精華製作EP 02」才有。慣例同 _to_cash_dict 的 invoice_title：
        # 由後端 JOIN 帶出來，前端不再自己抓一份發票清單去 find()（那份清單只在
        # 點選某列時才載入，列表首次渲染時是空的 → 全部退回顯示號碼）。
        "invoice_title": invoice_title,
        "id": p.id, "entity": p.entity or "parent",
        "request_date": p.request_date.isoformat() if p.request_date else None,
        "amount": p.amount, "summary": p.summary or "",
        "category": p.category or "",
        "payee_name": p.payee_name or "", "payee_id": p.payee_id or "",
        "payee_type": p.payee_type or "",
        "needs_invoice": p.needs_invoice, "invoice_number": p.invoice_number or "",
        "invoice_amount": p.invoice_amount,
        # 🔴 這裡回的是**解析後**的發票 id：欄位空的舊資料由後端用發票號碼補上
        # （_invoice_link_for）。前端不必知道還有一條號碼比對的舊路 —— 那條規則
        # 抄到前端就會變成第三、第四份，而且空號比對的坑要在每一份各修一次。
        "source_invoice_id": invoice_id or p.source_invoice_id or "",
        "project_id": p.project_id or "", "project_name": project_name,
        "project_label": p.project_label or "",
        "payment_date": p.payment_date.isoformat() if p.payment_date else None,
        "payment_status": p.payment_status or "未付款",
        "planned_month": p.planned_month or "",
        "advance_by": p.advance_by or "",
        "is_advance": p.is_advance or 0,
        "advance_returned": p.advance_returned or 0,
        "notes": p.notes or "",
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


# ── Payment Request Endpoints ──────────────────────────────

@router.get("/payments", dependencies=[Depends(money_dep)])
async def list_payments(
    request: Request,
    q: str = Query(""), category: str = Query(""),
    payment_status: str = Query(""), project_id: str = Query(""),
    unassigned: str = Query(""), suggest: str = Query(""),
    entity: str = Query(""),
):
    # 兩本帳：money_dep 之上疊第二層 entity scope（plan §2.4）
    # full：CRM 帳務＝原始帳列，合夥人不可及 —— money_dep 已擋一層，刻意雙保險
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        query = (
            select(CrmPaymentRequest, CrmProject.name.label("pn"))
            .outerjoin(CrmProject, CrmProject.id == CrmPaymentRequest.project_id)
            .where(CrmPaymentRequest.entity == ent)
            .order_by(CrmPaymentRequest.request_date.desc())
        )
        if category:
            query = query.where(CrmPaymentRequest.category == category)
        if payment_status:
            if payment_status == "應付款":
                query = query.where(CrmPaymentRequest.payment_status.in_(["應付款", "未付款"]))
            else:
                query = query.where(CrmPaymentRequest.payment_status == payment_status)
        if project_id:
            query = query.where(CrmPaymentRequest.project_id == project_id)
        # 「還沒掛專案的」—— 補歷史時最常用的一刀。只看**該掛而未掛**的
        # （類別在 project_link.PAYMENT_CATEGORIES 裡），行政/薪資那種本來就不該掛，
        # 混進來只會讓待辦清單看起來永遠做不完。
        if unassigned:
            query = query.where(
                or_(CrmPaymentRequest.project_id.is_(None),
                    CrmPaymentRequest.project_id == ""),
                CrmPaymentRequest.category.in_(_PAYMENT_LINK_CATEGORIES))
        if q:
            ql = f"%{q}%"
            query = query.where(or_(
                CrmPaymentRequest.summary.ilike(ql),
                CrmPaymentRequest.payee_name.ilike(ql),
            ))
        rows = (await session.execute(query)).all()
        links = await _invoice_link_for(session, [r[0] for r in rows], ent)
        # 未掛專案的列附一個**建議**（owner 2026-08-23：「手動掛，精準為主」）——
        # 人要做的從「翻 238 個專案找一個」變成「看一眼對不對」。
        # 🔴 只在有人問的時候算（suggest=1）：238 專案 × 800 張的子字串比對不該
        #    每次列清單都跑一次。只撈 id/name 兩欄 —— CrmProject 有八十幾個欄位、
        #    好幾個 TEXT，整包從 NAS 拉回來只為了讀名字太貴。
        projs = []
        if suggest:
            projs = prepare_projects((await session.execute(
                select(CrmProject.id, CrmProject.name)
                .where(CrmProject.name.isnot(None)))).all())
    out = []
    #: 摘要 → 建議。同一個摘要算出來一定是同一個答案（projs 這一輪不變），而
    #: 摘要重複得很兇：實測 406 列只有 189 個相異值（外包請款常常整批同名）。
    #: 省掉一半的比對 —— 量到 222ms → 108ms。
    #: ⚠ 只在這一次請求裡有效，不要升級成模組級快取：專案清單會變。
    memo: dict[str, dict | None] = {}
    for pay, pname in rows:
        d = _to_payment_dict(pay, pname or "", *links.get(pay.id, ("", "")))
        # 只算掛得上的類別 —— 行政／薪資本來就會被 batch-project 擋下來（409），
        # 給了建議只是讓人點下去才發現不行
        if projs and not (pay.project_id or "") \
                and (pay.category or "") in _PAYMENT_LINK_CATEGORIES:
            summary = pay.summary or ""
            if summary not in memo:
                memo[summary] = suggest_project(summary, projs)
            if memo[summary]:
                d["suggested"] = memo[summary]
        out.append(d)
    return {"payments": out, "total": len(out)}


@router.post("/payments")
async def create_payment(req: PaymentRequestPayload, request: Request):
    _require_db()
    ent = _entity_for_write(request, req.entity)
    factory = await _get_factory()
    now = _now()
    date_fields = {"request_date", "payment_date"}
    dates = {f: _parse_shoot_date(getattr(req, f)) for f in date_fields}
    data = req.model_dump(exclude=date_fields | {"entity"})
    p = CrmPaymentRequest(id=uuid.uuid4().hex, **dates, entity=ent,
                          created_at=now, updated_at=now, **data)
    async with factory() as session:
        # F1 月結守衛：請款單的權責費用認列月 = request_date
        await _assert_month_open(session, dates.get("request_date"), entity=ent)
        # CRM 的一行（人員費用或行政雜支）只能請一次款。前端請完就把按鈕換成
        # 「已請款」，但那擋不住雙擊、兩個分頁、或重送 —— 硬連結存在的意義
        # 就是在這裡認得出重複。兩條連結**同一條規則**，各寫一次必漏一個。
        for _col, _val in ((CrmPaymentRequest.cost_line_id, req.cost_line_id),
                           (CrmPaymentRequest.expense_id, req.expense_id)):
            if _val and (await session.execute(
                    select(CrmPaymentRequest.id).where(_col == _val))).first():
                raise HTTPException(409, "這一行已經請過款了")
        if req.expense_id:
            # 零用金流進來的列（staff_id）已經有自己的一條請款路（零用金批次 →
            # 依「會計項目×月份」開應付款，routers/crm/petty._build_aps）。上面的
            # 409 只認 expense_id 重複，擋不到「零用金那側另外開了一張」——
            # 這條規則要在這裡，前端不長按鈕只是禮貌。
            from db.models import CrmProjectExpense
            exp = await session.get(CrmProjectExpense, req.expense_id)
            if exp is not None and exp.staff_id:
                raise HTTPException(409, "零用金的支出走零用金批次請款，不能再從這裡請一次")
        if ent == "mine":
            await _apply_outsource(session, _outsource_key(p), +1)
        session.add(p)
        await session.commit()
    return {"status": "ok", "payment": _to_payment_dict(p)}


@router.get("/payments/options", dependencies=[Depends(money_dep)])
async def payment_options(request: Request):
    """請款單的下拉來源（比照 /cash-entries/options、/petty/options）。

    🔴 core/project_link.PAYMENT_CATEGORIES 一直沒有生產呼叫端 —— 那個模組的
    docstring 說它把三份寫死的清單集中起來，但請款單這一份始終留在前端，改規則
    要發版，而零用金／請款／收支三者**刻意**的差異也從程式碼裡看不出來。
    """
    ent = require_entity(request, "", level="full")
    return {"project_link_categories": list(_PAYMENT_LINK_CATEGORIES),
            "categories": await _payment_categories(ent)}


async def _payment_categories(entity: str) -> list:
    """請款單「項目」下拉的來源 ＝ **有會計對映的** ∪ **帳上已經在用的**。

    🔴 為什麼不能寫死在前端：這份清單決定的不只是選單，還決定那筆錢怎麼入帳
    （finance_category_map 把 category 翻成科目與 treatment）。兩邊各存一份就會漂，
    而且是往兩個方向漂 —— 2026-08-24 實測生產：
      · 有對映卻選不到 6 項（代收代付／代收薪資／代發薪資／勞報／後期雜支／現金代收）
      · **帳上已經有請款單在用、編輯視窗卻選不到自己** 2 項（勞報、後期雜支）
        —— 那些單一打開編輯就會被迫改成別的項目
    同一個病在「專案外包」身上咬過一次（歷史匯入 371/806 筆、46% 的最大宗類別
    當時不在清單裡），在收支明細身上也咬過一次（寫死 27 項少 5 項）。

    排序照帳上使用次數（最常用的在最前面），沒用過的接在後面 —— 使用者天天選的
    那幾個不該被字母序推到下面。
    """
    from sqlalchemy import func as sa_func

    from db.models import FinanceCategoryMap
    factory = await _get_factory()
    async with factory() as session:
        mapped = [r for (r,) in (await session.execute(
            select(FinanceCategoryMap.category_text).where(
                FinanceCategoryMap.source == "payment",
                FinanceCategoryMap.active.is_(True)))).all()]
        used = (await session.execute(
            select(CrmPaymentRequest.category, sa_func.count())
            .where(CrmPaymentRequest.entity == entity)
            .group_by(CrmPaymentRequest.category)
            .order_by(sa_func.count().desc()))).all()
    seen, out = set(), []
    for cat, _n in used:                    # 帳上在用的，照次數排
        if cat and cat not in seen:
            seen.add(cat)
            out.append(cat)
    for cat in sorted(mapped):              # 有對映但還沒用過的接在後面
        if cat and cat not in seen:
            seen.add(cat)
            out.append(cat)
    return out


@router.get("/payments/advances", dependencies=[Depends(money_dep)])
async def list_advance_payments(request: Request, returned: int = -1,
                                project_id: str = Query(""), entity: str = Query("")):
    """列出預支款。returned=-1=全部，0=未結清，1=已結清。project_id 可過濾特定專案。"""
    # 兩本帳：money_dep 之上疊第二層 entity scope（plan §2.4）
    # full：CRM 帳務＝原始帳列，合夥人不可及 —— money_dep 已擋一層，刻意雙保險
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        from sqlalchemy import func as sa_func
        q = (select(CrmPaymentRequest)
             .where(CrmPaymentRequest.is_advance == 1)
             .where(CrmPaymentRequest.entity == ent))
        if project_id:
            q = q.where(CrmPaymentRequest.project_id == project_id)
        rows = (await session.execute(q.order_by(CrmPaymentRequest.created_at.desc()))).scalars().all()

        result = []
        for p in rows:
            # Calculate expenses by this payee in this project
            expense_total = 0
            # 私帳專案的花費不能拿來核銷母公司的預支款（owner 2026-08-28）——
            # 核銷等於「這筆預支變成公司的成本」，述詞正本 core.ledger
            exp_sum = (await session.execute(
                select(sa_func.coalesce(sa_func.sum(CrmProjectExpense.actual), 0))
                .where(CrmProjectExpense.advance_id == p.id,
                       _not_mine_project(CrmProjectExpense))
            )).scalar() or 0
            expense_total = exp_sum

            # Get project name
            project_name = p.project_label or ""
            if p.project_id and not project_name:
                proj = await session.get(CrmProject, p.project_id)
                if proj:
                    project_name = proj.name

            amt = p.amount or 0
            # 發款: 收支明細中關聯此預支的支出合計（判定規則在 core/crm_logic.py）
            cash_pay_total = (await session.execute(
                select(sa_func.coalesce(sa_func.sum(CrmCashEntry.expense), 0))
                .where(CrmCashEntry.advance_payment_id == p.id)
                .where(CrmCashEntry.expense > 0)
            )).scalar() or 0
            # 收款: 收支明細中關聯此預支的收入合計
            cash_return_total = (await session.execute(
                select(sa_func.coalesce(sa_func.sum(CrmCashEntry.deposit), 0))
                .where(CrmCashEntry.advance_payment_id == p.id)
                .where(CrmCashEntry.deposit > 0)
            )).scalar() or 0
            from core.crm_logic import compute_advance_status
            adv = compute_advance_status(amt, expense_total, cash_pay_total, cash_return_total)
            # 關聯的收支明細（發款/收款記錄）
            linked_cash = (await session.execute(
                select(CrmCashEntry)
                .where(CrmCashEntry.advance_payment_id == p.id)
                .order_by(CrmCashEntry.entry_date)
            )).scalars().all()
            cash_entries = [{
                "id": c.id,
                "entry_date": _fmt_day(c.entry_date) or None,
                "summary": c.summary or "",
                "deposit": c.deposit or 0,
                "expense": c.expense or 0,
                "type": "收款" if (c.deposit or 0) > 0 else "發款",
            } for c in linked_cash]
            result.append({
                "id": p.id,
                "entity": p.entity or "parent",
                "payee_name": p.payee_name or "",
                "amount": amt,
                "project_id": p.project_id or "",
                "project_name": project_name,
                "payment_date": p.payment_date.isoformat() if p.payment_date else None,
                "request_date": p.request_date.isoformat() if p.request_date else None,
                "payment_status": p.payment_status or "應付款",
                "is_paid": adv["is_paid"],
                "is_returned": adv["is_returned"],
                "is_settled": adv["is_settled"],
                "expense_total": expense_total,
                "balance": adv["balance"],
                "cash_entries": cash_entries,
            })
    # Post-filter by settled status
    if returned == 0:
        result = [r for r in result if not r["is_settled"]]
    elif returned == 1:
        result = [r for r in result if r["is_settled"]]
    return {"advances": result}


@router.patch("/payments/batch-project")
async def batch_assign_project(request: Request):
    """批次把請款單掛到專案（owner 2026-08-23：「專案我可以手動掛，精準為主」）。

    為什麼要有它：817 張請款單裡「專案外包」371 張／1,101 萬，一張都沒掛專案，
    而那是營收的 73.5%。逐張開視窗掛得掛到天荒地老；但自動比對又不可靠
    （project_label 只有 140 張有填、29 種值，跟專案名精確吻合 0 筆）。
    所以：人來判斷、機器只負責一次寫很多筆。

    F1 月結守衛判準：改的是「這筆錢屬於哪個案子」的歸屬，金額、request_date、
    payment_date 都沒動 —— 帳沒變，不掛守衛（與 batch-month 同一判準）。

    🔴 但**類別守衛照掛**（core.project_link.PAYMENT_CATEGORIES，本檔以 _PAYMENT_LINK_CATEGORIES 引入）：行政／薪資
    那種公司層級支出掛到專案上，專案毛利就會多算一筆不屬於它的錢。批次一次
    幾十張，錯起來比逐張更難發現 —— 所以這裡是整批擋下、把違規的列出來，
    不是默默跳過（默默跳過＝使用者以為掛好了）。
    """
    # 寫入守衛＝列的帳本說了算（同 batch_pay 三兄弟）：check_logged_in 先擋匿名，
    # 列載入後 _mine_or_admin_write_rows 定案 —— 原本 _check_auth 只認 Lv3，
    # 帳本主人（lv1+finance_mine）批次掛自己的專案會 403（2026-08-26 同型清查）。
    check_logged_in(request)
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    ids = body.get("payment_ids") or []
    project_id = (body.get("project_id") or "").strip() or None
    if not ids:
        raise HTTPException(status_code=400, detail="請提供 payment_ids")

    async with factory() as session:
        ent = require_entity(request, body.get("entity") or "", level="full")
        proj = None
        if project_id:
            proj = await session.get(CrmProject, project_id)
            if not proj:
                raise HTTPException(status_code=404, detail="找不到此專案")

        rows = (await session.execute(
            select(CrmPaymentRequest).where(
                CrmPaymentRequest.id.in_(ids)))).scalars().all()
        found = {r.id for r in rows}
        missing = [i for i in ids if i not in found]
        if missing:
            raise HTTPException(status_code=404,
                                detail=f"{len(missing)} 張請款單不存在（可能剛被刪掉）")

        cross = [r for r in rows if (r.entity or "parent") != ent]
        if cross:
            raise HTTPException(status_code=409,
                                detail=f"{len(cross)} 張請款單屬於另一本帳，不可跨帳本掛專案")
        _mine_or_admin_write_rows(request, rows)

        # 掛上去才需要驗類別；解除連結（project_id=None）永遠合法
        if project_id:
            bad = [r for r in rows if (r.category or "") not in _PAYMENT_LINK_CATEGORIES]
            if bad:
                names = "、".join(sorted({r.category or "未分類" for r in bad}))
                raise HTTPException(
                    status_code=409,
                    detail=f"其中 {len(bad)} 張的類別（{names}）不能連結專案 —— "
                           f"可連結的類別：{'、'.join(_PAYMENT_LINK_CATEGORIES)}。"
                           "請先取消勾選那幾張，或改掉它們的類別。")

        for r in rows:
            r.project_id = project_id
            r.updated_at = _now()
        await session.commit()
    return {"status": "ok", "updated": len(rows),
            "project_name": (proj.name if proj else "")}


@router.patch("/payments/batch-month")
async def batch_update_month(request: Request):
    """更新請款單的 planned_month。

    F1 月結守衛判準：planned_month 是「預計付款月」排程欄，不是權責認列日
    （request_date）也不是現金發生日（payment_date）— 改排程不改帳，不掛守衛
    （所以也沒有 per-entity 鎖月檢查；各列的 entity 維持不變）。"""
    check_logged_in(request)
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    ids = body.get("payment_ids", [])
    planned_month = body.get("planned_month", "")
    if not ids:
        raise HTTPException(status_code=400, detail="請提供 payment_ids")

    async with factory() as session:
        rows = [p for pid in ids if (p := await session.get(CrmPaymentRequest, pid))]
        _mine_or_admin_write_rows(request, rows)
        updated = 0
        for p in rows:
            p.planned_month = planned_month
            p.updated_at = _now()
            updated += 1
        await session.commit()
    return {"status": "ok", "updated": updated}


@router.patch("/payments/batch-pay")
async def batch_pay(request: Request):
    """批次標記已付款。

    F1 月結守衛判準：付款動作影響的是現金側（payment_date），不改權責費用
    認列月（request_date）— 所以這裡守 payment_date：新付款日或原付款日
    （搬出鎖定月也算改帳）落鎖定月 → 整批 409 並列出違規筆。"""
    check_logged_in(request)
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    ids = body.get("payment_ids", [])
    pay_date = _parse_shoot_date(body.get("payment_date", "")) or _now()
    if not ids:
        raise HTTPException(status_code=400, detail="請提供 payment_ids")

    async with factory() as session:
        # 先載入會變動的列（違規時一筆都不動）
        rows = []
        for pid in ids:
            p = await session.get(CrmPaymentRequest, pid)
            if not p:
                continue
            will_change = (p.payment_status != "已付款") or (p.payment_date != pay_date)
            if will_change:
                rows.append(p)
        _mine_or_admin_write_rows(request, rows)
        # 兩本帳各自鎖月（plan §3）：涉及的 entity 分組、各查一次鎖定月集合
        locked_by_entity = {
            ent: await _locked_month_set(session, entity=ent)
            for ent in {(p.entity or "parent") for p in rows}
        }
        # month_of：timestamptz 回讀是 UTC 表示，直取 strftime 會把月初歸前月
        pay_month = month_of(pay_date)
        targets, violations = [], []
        for p in rows:
            # 🔴 鎖月要看**這一列自己那本帳**。本來新付款日那條是
            # `any(... for locked in locked_by_entity.values())` —— 我的帳鎖了
            # 某個月，母公司的批次付款就整批被擋，而下面舊付款日那條是對的。
            # 同一支函式兩套規則。
            locked = locked_by_entity[p.entity or "parent"]
            old_month = month_of(p.payment_date)
            if pay_month in locked:
                violations.append(f"{p.summary or p.id}（付款日 {pay_month}）")
                continue
            if old_month and old_month in locked:
                violations.append(f"{p.summary or p.id}（{old_month}）")
                continue
            targets.append(p)
        if violations:
            _raise_locked_batch(violations)
        updated = 0
        for p in targets:
            p.payment_status = "已付款"
            p.payment_date = pay_date
            p.updated_at = _now()
            updated += 1
            # 代開請款單付掉 = 錢匯給代開人了 → 對應發票走到「已撥款」，
            # 整條生命週期（未收款→待撥款→已撥款）收尾。規則見 sync_remit_status。
            await sync_remit_status(session, p)
        await session.commit()
    return {"status": "ok", "updated": updated}


@router.patch("/payments/batch-unpay")
async def batch_unpay(request: Request):
    """將已付款的請款單改回應付款。

    F1 月結守衛判準同 batch-pay：取消付款是把現金事件從原付款月抽走 —
    原 payment_date 落鎖定月 → 整批 409 並列出違規筆。"""
    check_logged_in(request)
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    ids = body.get("payment_ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="請提供 payment_ids")

    async with factory() as session:
        rows = []
        for pid in ids:
            p = await session.get(CrmPaymentRequest, pid)
            if p and p.payment_status == "已付款":
                rows.append(p)
        _mine_or_admin_write_rows(request, rows)
        # 兩本帳各自鎖月（plan §3）：涉及的 entity 分組、各查一次鎖定月集合
        locked_by_entity = {
            ent: await _locked_month_set(session, entity=ent)
            for ent in {(p.entity or "parent") for p in rows}
        }
        targets, violations = [], []
        for p in rows:
            old_month = month_of(p.payment_date)
            if old_month and old_month in locked_by_entity[p.entity or "parent"]:
                violations.append(f"{p.summary or p.id}（{old_month}）")
                continue
            targets.append(p)
        if violations:
            _raise_locked_batch(violations)
        updated = 0
        for p in targets:
            p.payment_status = "應付款"
            p.payment_date = None
            p.updated_at = _now()
            updated += 1
            # batch_pay 的對稱反向 —— 同一份規則（sync_remit_status 兩個方向都走）
            await sync_remit_status(session, p)
        await session.commit()
    return {"status": "ok", "updated": updated}


@router.get("/payments/{payment_id}", dependencies=[Depends(money_dep)])
async def get_payment(payment_id: str, request: Request):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        p = await session.get(CrmPaymentRequest, payment_id)
        if not p:
            raise HTTPException(status_code=404, detail="找不到此請款單")
        require_entity(request, p.entity or "parent", level="full")  # 兩本帳 scope 驗證（full：原始帳列，money_dep 之上刻意雙保險）
        pn = ""
        if p.project_id:
            proj = await session.get(CrmProject, p.project_id)
            pn = proj.name if proj else ""
        # 單張也走同一支解析（詳情面板點開就是打這裡）—— 兩條路回的形狀要一樣，
        # 不然「列表看得到抬頭、點進去只剩號碼」
        link = (await _invoice_link_for(session, [p], p.entity or "parent")).get(p.id)
    return _to_payment_dict(p, pn, *(link or ("", "")))


@router.put("/payments/{payment_id}")
async def update_payment(payment_id: str, req: PaymentRequestPayload, request: Request):
    check_logged_in(request)
    _require_db()
    factory = await _get_factory()
    date_fields = {"request_date", "payment_date"}
    # 🔴 部分更新：**只寫前端真的送來的欄位**（exclude_unset）。
    # 整包 model_dump 會把沒送的欄位洗成 pydantic 預設值，而編輯面板只送 13 個欄位
    # —— needs_invoice→0、invoice_amount→None、is_advance→0、advance_by→""、
    # advance_returned→0、project_label→"" 全部無聲歸零。2026-08-24 量過生產：
    # 824 張請款單裡，需代開與代開金額各 186 張、專案標籤 141 張會被一次存檔清掉，
    # 而畫面上只有使用者改的那一欄看起來變了。這是全 repo 的既定慣例
    # （20+ 個更新端點都用 exclude_unset），這支跟 update_cash_entry 一樣是漏網的。
    # 也因此 source_invoice_id 不再需要單獨挑出來保護 —— 沒送就不會被碰。
    data = req.model_dump(exclude_unset=True, exclude={"entity"})
    dates = {f: _parse_shoot_date(data[f]) for f in date_fields if f in data}
    async with factory() as session:
        p = await session.get(CrmPaymentRequest, payment_id)
        if not p:
            raise HTTPException(status_code=404, detail="找不到此請款單")
        # 委外費用同步要用「改之前」的快照當減項（setattr 之後就沒了）
        _old_key = _outsource_key(p)
        # 兩本帳：payload.entity None＝維持既有值；帶不同值＝想搬帳本 → 422
        # （寫入守衛也在 _entity_for_write 裡定案）
        ent = _entity_for_write(request, req.entity, p)
        # F1 月結守衛：舊/新 request_date 的月份都要開著
        await _assert_month_open(session, p.request_date, dates.get("request_date"),
                                 entity=ent)
        prev_src = p.source_invoice_id
        for k, v in data.items():
            if k not in date_fields:
                setattr(p, k, v)
        for k, v in dates.items():
            setattr(p, k, v)
        p.updated_at = _now()
        # 代開：這支也會改付款狀態（payload 有 payment_status），而且會改「指向哪張
        # 發票」—— 兩者都直接決定發票那側的撥款狀態，所以一樣走 sync_remit_status
        # （改指時原本那張的退回也在它裡面，規則只有一份）。
        await sync_remit_status(session, p, previous_invoice_id=prev_src)
        if (p.entity or "parent") == "mine":
            _new_key = _outsource_key(p)
            if _new_key != _old_key:
                await _apply_outsource(session, _old_key, -1)
                await _apply_outsource(session, _new_key, +1)
        await session.commit()
    return {"status": "ok"}


@router.delete("/payments/{payment_id}")
async def delete_payment(payment_id: str, request: Request):
    check_logged_in(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        p = await session.get(CrmPaymentRequest, payment_id)
        if not p:
            raise HTTPException(status_code=404, detail="找不到此請款單")
        _mine_or_admin_write(request, p.entity)  # 兩本帳寫入守衛：母公司=Lv3、私帳=mine full
        await _assert_month_open(session, p.request_date, entity=p.entity or "parent")
        if (p.entity or "parent") == "mine":
            await _apply_outsource(session, _outsource_key(p), -1)
        await session.delete(p)
        await session.commit()
    return {"status": "ok"}


# ── Payment CSV Import ──────────────────────────────────────

_PAYMENT_COL_MAP = {
    "request_date":   ["日期", "request_date"],
    "amount":         ["請款", "金額", "amount"],
    "summary":        ["摘要", "summary"],
    "category":       ["項目", "類別", "category"],
    "payee_combined": ["收款人", "payee"],
    "payee_type":     ["狀態", "payee_type"],
    "invoice_number": ["發票號碼", "invoice_number"],
    "project_label":  ["專案標籤", "project_label"],
    "payment_date":   ["付款日", "payment_date"],
    "payment_status": ["付款狀態", "payment_status"],
    "notes":          ["附註", "備註", "notes"],
}


def _map_payment_row(header_map: dict, row: dict) -> dict:
    data = map_csv_row(
        _PAYMENT_COL_MAP, header_map, row,
        coerce=lambda f, v: _parse_money(v) if f == "amount" else v)
    # Parse combined payee field: "姓名_身分證" or just "姓名"
    combined = data.pop("payee_combined", "")
    if combined:
        parts = combined.split("_", 1)
        data["payee_name"] = parts[0]
        if len(parts) > 1:
            data["payee_id"] = parts[1]
    # 摘要留白 → 用收款人頂替。🔴 這步必須在「要不要跳過這一列」之前，也就是
    # 在 mapper 裡 —— 否則「只有收款人、沒有摘要」的列會被當空列跳掉，而那是
    # 請款單很常見的填法。
    if not data.get("summary") and data.get("payee_name"):
        data["summary"] = data["payee_name"]
    return data


@router.post("/payments/import_csv")
async def import_payments_csv(request: Request, file: UploadFile = File(...)):
    def build(data, d):
        now = _now()
        return CrmPaymentRequest(
            id=uuid.uuid4().hex, request_date=d["request_date"],
            payment_date=d["payment_date"], entity="parent",
            created_at=now, updated_at=now, **data)
    return await _import_money_csv(
        request, file, map_row=_map_payment_row,
        skip_if=lambda d: not d.get("summary"),   # 摘要在 mapper 裡已用收款人補過
        date_fields=("request_date", "payment_date"), build=build)


def _outsource_key(p) -> tuple:
    """一張請款單身上「會影響私帳委外費用」的那幾個欄位：
    `(專案, 類別, 金額, 硬連結)`。

    🔴 抽這一支的理由是實帳事故：這幾個欄位原本由**三個呼叫端各自拼**，
    helper 後來多收一個 `cost_line_id`，只有兩個呼叫端跟著改 —— 漏掉的
    `delete_payment` 於是會扣掉一筆當初根本沒加進去的錢，而且不會噴錯
    （helper 靜靜 return）。要記得帶第 N 個參數的介面遲早會漏，
    所以改成「哪些欄位算數」只在這裡定義一次。
    """
    return (p.project_id, p.category, int(p.amount or 0),
            p.cost_line_id or p.expense_id)


async def _apply_outsource(session, key: tuple, sign: int):
    """私帳委外規則（owner 2026-08-25「要可以新增委外項目」）：掛在專案上、
    類別＝專案外包 的請款單驅動該案 ledger_detail 的「委外費用」。
    `key` 來自 `_outsource_key`；`sign` +1 記上、−1 抵銷。

    🔴 增量制（同 _sync_mine_project_received 的理由）：歷史委外費用是匯入
    基準值（1,661,183，多數已付、沒有對應請款單列），重算會洗掉老案。

    ⚠️ **這一欄不只有你在寫**：`core.ledger_project.apply_crm_costs` 讀取時會把
    CRM 專案帳目的人員費用**加上**你累加的值（owner 2026-08-28 拍板的甲案）。
    所以這裡累加的必須只有「CRM 上沒有的那幾筆」——
    🔴 帶硬連結的請款單是 CRM 成本行／雜支行的鏡射（逐案損益的一鍵請款建的），
    它的錢已經由 apply_crm_costs 算過一次，再累加就是同一筆算兩次。
    委外費用是**權責**（應付＋已付都算成本），所以只跟金額走、不看付款狀態。
    """
    project_id, category, amount, linked = key
    if not amount or (category or "") != "專案外包" or linked:
        return
    from core.ledger_project import norm_detail
    p = await _get_mine_project(session, project_id)
    if not p:
        return
    d = norm_detail(p.ledger_detail)
    d["outsource"] = int(d.get("outsource") or 0) + sign * amount
    p.ledger_detail = norm_detail(d)
    p.updated_at = _now()
