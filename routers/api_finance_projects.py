# -*- coding: utf-8 -*-
"""api_finance_projects.py — 逐案損益（帳本視角的專案清單／單案明細）。

owner 2026-08-24：「跟我有關的專案我都要看到」。/my-ledger.html 是固定
entity 的帳本頁（刻意沒有 CRM 專案管理），但它缺的正是 owner 最在意的維度 ——
逐案的錢。這支端點提供**帳本視角**的專案彙總：一列一案，帶合約／已收／應收／
掛帳支出／未付應付／淨額。

與 CRM 的 /crm/projects 分工：那支是**專案管理**（階段、派工、客戶、看板），
這支是**逐案的錢**（原 Sheet「結案總表」那張表的系統版），欄位口徑對齊帳本。
兩支都按 entity 過濾，錢的牆一樣在 core/ledger（require_entity）。

口徑（與 Sheet 對照）：
- 營收(含稅) = crm_projects.contract_amount
- 已收 / 應收 = amount_received / amount_receivable
- 掛帳支出   = 掛在該案的收支明細 expense 合計（階段 3 按案碼回掛的 612 筆）
- 未付應付   = 該案 crm_payment_requests 未付款者合計（委外／代開稅款）
- 實收       = 營收 − 委外 − 發票代辦費 − 個人稅款 − 雜支 − 股東往來
- 檢查       = 實收 − Σ工項（應為 0）
  這兩條是 2026-08-24 對 402 案反推驗證出來的 Sheet 算式（實收 395/402、
  檢查 397/402 吻合；不吻合的是 Sheet 自己帳不平的那幾案，照實顯示不修）。
  算式正本在 `core/ledger_project.py` 的 `compute()`（本檔只是 import 它；
  腳本與測試也 import 同一份）—— 前端只顯示，不自己算第二份。

工項與費用欄存在 `crm_projects.ledger_detail`（JSONB），可由 UI 編輯；
工項清單預設十項、settings `my_ledger.income_items` 可覆寫。
"""
from fastapi import APIRouter, HTTPException, Request

from config import load_settings
from core.db_guard import db_factory_or_503 as _factory_or_503
# 欄位定義與算式的正本在 core（腳本與測試也 import 同一份 —— 見該檔頭）
from core.ledger_project import (COST_FIELDS, SUM_KEYS, compute,
                                 income_items, norm_detail)
from core.schemas import LedgerDetailPayload
from routers.crm._shared import _fmt_day

from .api_finance import _guard

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


async def _owned_project(session, project_id: str, ent: str):
    """載入專案 → 404 → 驗它真的屬於這本帳。

    query 的 entity 只驗得了「人有沒有這本帳的權限」，驗不了「這一列是誰的」
    —— 兩支端點都要，抽一份免得第三支忘記。
    """
    from db.models import CrmProject
    p = await session.get(CrmProject, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="找不到此專案")
    if (p.entity or "parent") != ent:
        raise HTTPException(status_code=403, detail="這個專案不屬於目前的帳本")
    return p


async def _rollups(session, ent: str):
    """({project_id: 掛帳支出}, {project_id: 未付應付})。一次聚合，不逐案 N+1。

    🔴 未付應付**排除預支款**（is_advance）—— 對齊 core.finance_logic
    ap_open_payments 與「應付帳款」視圖的口徑。預支不是應付（那是先給出去的
    週轉金，核銷後才變成成本），混進來會讓同一個專案在逐案損益與應付帳款上
    出現兩個數字，那正是這套帳要消滅的東西（/simplify 2026-08-25）。

    entity 條件本身就把範圍圈定了 —— 不再另外帶 id 清單進 IN（那是零選擇性
    的白工，而且清單端本來就整份拉）。
    """
    from sqlalchemy import func as fn
    from sqlalchemy import or_, select

    from db.models import CrmCashEntry, CrmPaymentRequest
    cash_q = (select(CrmCashEntry.project_id,
                     fn.coalesce(fn.sum(CrmCashEntry.expense), 0))
              .where(CrmCashEntry.entity == ent,
                     CrmCashEntry.project_id.isnot(None))
              .group_by(CrmCashEntry.project_id))
    ap_q = (select(CrmPaymentRequest.project_id,
                   fn.coalesce(fn.sum(CrmPaymentRequest.amount), 0))
            .where(CrmPaymentRequest.entity == ent,
                   CrmPaymentRequest.project_id.isnot(None),
                   # 🔴 NULL 也是「不是預支」。生產的 is_advance 可為 NULL，
                   # 而 SQL 的 `NULL = 0` 是 NULL → 那一列會整個消失在未付應付
                   # 裡，卻還留在應付帳款上（core.finance_logic 走 Python
                   # truthiness、crm/finance.py:1971 也是這樣防的）—— 正好是
                   # 這支端點要消滅的「同一案兩個數字」。
                   or_(CrmPaymentRequest.is_advance == 0,
                       CrmPaymentRequest.is_advance.is_(None)),
                   CrmPaymentRequest.payment_status != "已付款")
            .group_by(CrmPaymentRequest.project_id))
    cash = {pid: int(e or 0) for pid, e in (await session.execute(cash_q)).all()}
    ap = {pid: int(a or 0) for pid, a in (await session.execute(ap_q)).all()}
    return cash, ap


@router.get("/project-ledger")
async def project_ledger(request: Request, entity: str = ""):
    """本帳本的逐案損益清單（含合計）。

    刻意不收 q／unpaid_only：前端一次拉完 402 列後在本地篩（每敲一個字重抓
    整份是白工，見 subviews/projects.js）。留著沒人走的分支只會讓下一個要改
    rollup 規則的人去推理一個從未執行過的模式（/simplify 2026-08-25）。
    """
    ent = _guard(request, entity, level="full")
    from sqlalchemy import select

    from db.models import Client, CrmProject
    factory = _factory_or_503()
    async with factory() as session:
        query = (select(CrmProject, Client.short_name)
                 .outerjoin(Client, Client.id == CrmProject.client_id)
                 .where(CrmProject.entity == ent)
                 # 依結案日新→舊；未結案（無日期）排最前 —— 這張表是照結案日
                 # 整理的（owner：先整理專案再記帳），不是照最後編輯時間。
                 .order_by(CrmProject.completion_date.desc().nullsfirst(),
                           CrmProject.updated_at.desc()))
        rows = (await session.execute(query)).all()
        cash, ap = await _rollups(session, ent)

    items, tot = [], {"contract": 0, "received": 0, "receivable": 0,
                      "spent": 0, "ap_open": 0, "net": 0,
                      "outsource": 0, "invoice_fee": 0, "unbalanced": 0}
    for p, cname in rows:
        spent = cash.get(p.id, 0)
        ap_open = ap.get(p.id, 0)
        contract = int(p.contract_amount or 0)
        detail = norm_detail(p.ledger_detail)
        net, check = compute(contract, detail)
        item = {
            "id": p.id, "name": p.name, "client": cname or "",
            "status": p.status or "", "type": p.project_type or "",
            "close_date": _fmt_day(p.completion_date),
            # 排序的次要鍵 —— 前端改完結案日要就地重排，少了它排不出與這裡
            # ORDER BY 相同的順序（同一天結案的案子就會落在不同位置）
            "updated_at": p.updated_at.isoformat() if p.updated_at else "",
            "contract": contract,
            "received": int(p.amount_received or 0),
            "receivable": int(p.amount_receivable or 0),
            "payment_status": p.payment_status or "",
            "spent": spent, "ap_open": ap_open,
            "detail": detail, "net": net, "check": check,
        }
        items.append(item)
        # 加總只從 item 取 —— 上面已經算好的數字不要在這裡再算一次
        for k in SUM_KEYS:
            tot[k] += item[k]
        tot["outsource"] += detail["outsource"]
        tot["invoice_fee"] += detail["invoice_fee"]
        if check:
            tot["unbalanced"] += 1
    return {"projects": items, "totals": tot, "count": len(items)}


@router.get("/project-ledger/{project_id}")
async def project_ledger_detail(project_id: str, request: Request,
                                entity: str = ""):
    """單案明細：掛帳的收支逐筆 + 未付應付逐張 + 匯入時保留的原始備註。"""
    ent = _guard(request, entity, level="full")
    from sqlalchemy import select

    from db.models import Client, CrmCashEntry, CrmPaymentRequest
    factory = _factory_or_503()
    async with factory() as session:
        p = await _owned_project(session, project_id, ent)
        client = await session.get(Client, p.client_id) if p.client_id else None
        entries = (await session.execute(
            select(CrmCashEntry)
            .where(CrmCashEntry.project_id == project_id,
                   CrmCashEntry.entity == ent)
            .order_by(CrmCashEntry.entry_date))).scalars().all()
        pays = (await session.execute(
            select(CrmPaymentRequest)
            .where(CrmPaymentRequest.project_id == project_id,
                   CrmPaymentRequest.entity == ent)
            .order_by(CrmPaymentRequest.created_at))).scalars().all()
    _d = norm_detail(p.ledger_detail)
    _net, _check = compute(int(p.contract_amount or 0), _d)
    return {
        "project": {
            "id": p.id, "name": p.name, "client": client.short_name if client else "",
            "status": p.status or "", "type": p.project_type or "",
            "close_date": _fmt_day(p.completion_date),
            "contract": int(p.contract_amount or 0),
            "received": int(p.amount_received or 0),
            "receivable": int(p.amount_receivable or 0),
            "payment_status": p.payment_status or "",
            "detail": _d, "net": _net, "check": _check,
            # 匯入時把案碼/案源/税別等收在這裡（純文字）—— 逐案對照 Sheet 用
            "notes": p.notes or "",
        },
        "income_items": income_items(load_settings()),
        "cost_fields": [{"key": k, "label": lb} for k, lb in COST_FIELDS],
        "entries": [{
            "id": e.id, "date": _fmt_day(e.entry_date), "summary": e.summary,
            "category": e.category or "", "deposit": int(e.deposit or 0),
            "expense": int(e.expense or 0), "status": e.status or "",
        } for e in entries],
        "payments": [{
            "id": x.id, "summary": x.summary, "amount": int(x.amount or 0),
            "category": x.category or "", "payee": x.payee_name or "",
            "payment_status": x.payment_status or "",
            "payment_date": _fmt_day(x.payment_date),
        } for x in pays],
    }


@router.put("/project-ledger/{project_id}")
async def update_project_ledger(project_id: str, payload: LedgerDetailPayload,
                                request: Request, entity: str = ""):
    """編輯單案的工項與費用欄。回存後的明細與重算的實收/檢查。

    🔴 只寫有送的欄（exclude_unset）—— 整包寫回會把沒送的欄洗成 0。
    split 有送＝整份取代（工項是一組值）；0 值在 norm_detail 會被清掉，
    等於「把這個工項刪掉」，那正是使用者把格子清空的意思。
    """
    ent = _guard(request, entity, level="full")
    from routers.crm._shared import _now, _parse_shoot_date
    factory = _factory_or_503()
    async with factory() as session:
        p = await _owned_project(session, project_id, ent)
        data = payload.model_dump(exclude_unset=True)
        contract = data.pop("contract_amount", None)
        if contract is not None:
            p.contract_amount = int(contract)
        # 結案日：owner 的流程是「先整理專案再記帳」，日期在這張表就要能改。
        # 空字串＝清空（未結案）。日期慣例走 _parse_shoot_date（UTC 午夜）。
        if "close_date" in data:
            raw = (data.pop("close_date") or "").strip()
            if raw:
                d = _parse_shoot_date(raw)
                if not d:
                    raise HTTPException(status_code=422, detail=f"結案日無法解析：{raw}")
                p.completion_date = d
            else:
                p.completion_date = None
        d = norm_detail(p.ledger_detail)
        for k, v in data.items():
            if v is None:
                continue
            d[k] = ({str(x): int(y) for x, y in v.items()
                     if isinstance(y, (int, float))} if k == "split" else int(v))
        d = norm_detail(d)          # 再正規化一次（清掉 0 值工項）
        p.ledger_detail = d
        p.updated_at = _now()
        await session.commit()
        net, check = compute(int(p.contract_amount or 0), d)
    return {"status": "ok", "detail": d, "net": net, "check": check,
            "contract": int(contract) if contract is not None else None,
            "close_date": _fmt_day(p.completion_date),
            "updated_at": p.updated_at.isoformat() if p.updated_at else ""}
