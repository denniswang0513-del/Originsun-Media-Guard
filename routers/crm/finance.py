"""routers/crm/finance.py — 帳務：發票 / 請款 / 預支款 / 收支明細 /
應付帳款 / 應收帳款（含各 CSV 匯入）。

自 routers/api_crm.py 原樣搬移（純搬移，行為不變）。
"""
from __future__ import annotations

import asyncio
import csv
import io
import ntpath
import os
import re
import shutil
import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, UploadFile, File, Query

from core.auth import check_admin
from core.finance_logic import month_of
from core.ledger import require_entity
from core.project_link import CASH_CATEGORIES as _PROJECT_LINK_CATEGORIES
from core.schemas import (InvoicePayload, PaymentRequestPayload, CashEntryPayload,
                          CashInvoiceLinksPayload)

from ._shared import (router, token_router, _check_auth, money_dep, _require_db,
                      cash_category_texts,
                      _get_factory, _fmt_day, _now,
                      _parse_shoot_date, _assert_month_open, _assert_rows_open,
                      _locked_month_set, _raise_locked_batch)

try:
    from ._shared import (select, or_,
                          Client, CrmProject, CrmStaff, CrmInvoice,
                          CrmPaymentRequest, CrmCashEntry, CrmProjectExpense,
                          CrmCashInvoiceLink)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

# ── CSV 匯入共用 ────────────────────────────────────────────

def _parse_money(val: str) -> int:
    """CSV 金額欄 → int（無法辨識回 0）。三支 import_csv 共用的唯一正本。

    🔴 為什麼要收斂成一份：發票／收支／請款三個 mapper 本來各寫一套，發票那套
    **沒有去逗號** —— Google Sheets 匯出時金額欄只要設了千分位格式就是 "1,234"，
    於是每一筆帶逗號的發票金額都被靜靜寫成 0（不報錯、不跳過）。2026-08-19 匯
    394 筆歷史發票前實測到 14 筆會中招，其中最大一筆 342,857。

    吃得下：千分位逗號、全形/半形空白、NT$／$ 符號、小數點。
    ⚠️ 會計式括號負數 "(1,234)" 沿用舊行為＝去括號後仍為正數（收支表有獨立的
    支出/存入兩欄，不靠括號表達正負）。要改負號語意得先確認三張表都沒在用它。
    """
    s = (val or "").strip().replace(",", "").replace(" ", "").replace("　", "")
    s = s.replace("NT$", "").replace("$", "").replace("(", "").replace(")", "")
    return int(float(s)) if re.fullmatch(r"-?\d+(\.\d+)?", s) else 0


# ── 兩本帳（entity）helpers — docs/LEDGER_ENTITY_PLAN.md §2.4/§3 ──────

def _entity_for_write(request: Request, payload_entity, row=None) -> str:
    """建立/更新端點的 entity 決策 —— 回傳該列最終應落的 entity。

    - payload.entity 為 None → 建立落 'parent'（母公司，預設）、更新維持既有值
      （🔴 絕不可把 None 洗成 'parent'——舊前端整包寫回會把「我的帳」列洗回母公司）。
    - payload.entity 非 None → require_entity 驗 scope＋合法值（非法 422）。
    - 🔴 **更新不得換帳本**（owner 2026-08-19 拍板）：帶了與該列現值不同的
      entity → 422，與 api_finance 的銀行帳戶／調整／貸款同一條規矩，也就是
      plan §2.4「更新維持既有值」的字面意思。一筆帳建在哪本就留在哪本；真要
      換帳本＝刪掉重建，那會留下痕跡，而不是靜靜地把一列錢搬進另一本帳。

    此處與本檔其他 require_entity 一律 level="full"：CRM 帳務端點是原始帳列
    與寫入面，合夥人（finance_partner）不可及 —— money_dep 已擋一層，
    這是刻意的雙保險（plan §2.3/§2.4）。
    """
    if payload_entity is None:
        return (row.entity or "parent") if row is not None else "parent"
    ent = require_entity(request, payload_entity, level="full")
    if row is not None and (row.entity or "parent") != ent:
        raise HTTPException(status_code=422, detail="這筆帳不可跨帳本搬移")
    return ent


# ── Invoice Helpers ─────────────────────────────────────────

def _to_invoice_dict(inv, project_name: str = "") -> dict:
    return {
        "id": inv.id, "entity": inv.entity or "parent",
        "payment_type": inv.payment_type or "收款",
        "payment_status": inv.payment_status or "", "issue_status": inv.issue_status or "",
        "invoice_number": inv.invoice_number or "", "title": inv.title or "",
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "applicant": inv.applicant or "", "category": inv.category or "專案",
        "invoice_kind": inv.invoice_kind or "", "amount_ex_tax": inv.amount_ex_tax,
        "amount_total": inv.amount_total, "tax_amount": inv.tax_amount,
        "commission": inv.commission, "company_name": inv.company_name or "",
        "tax_id": inv.tax_id or "", "item_type": inv.item_type or "",
        "project_id": inv.project_id or "", "project_name": project_name,
        "recipient": getattr(inv, 'recipient', '') or "",
        "recipient_phone": getattr(inv, 'recipient_phone', '') or "",
        "recipient_address": getattr(inv, 'recipient_address', '') or "",
        "paid_date": inv.paid_date.isoformat() if getattr(inv, 'paid_date', None) else None,
        # 已開立的電子發票檔（磁碟絕對路徑）。前端拿它去打 /crm/invoice-file 取檔。
        # 🔴 刻意不進 InvoicePayload —— 進去的話 update_invoice 的整包 model_dump
        # 寫回會在前端沒送這欄時把它洗成 ""（本 repo 記過的那族陷阱）。只有上傳／
        # 刪除端點動得了它。
        "file_url": getattr(inv, 'file_url', '') or "",
        "file_name": os.path.basename(getattr(inv, 'file_url', '') or ""),
        # 只回「有沒有」，不回 token 本身 —— 列表是每個看得到帳務的人都拿得到的
        # 回應，客戶下載連結沒有理由整批躺在那裡（要用時前端再打 /share 取）。
        "has_share": bool(getattr(inv, 'share_token', None)),
        "notes": inv.notes or "",
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
    }


def _mark_invoice_received(inv, when) -> None:
    """收款發票標已收款 + paid_date first-wins（兩欄不變式成對動 — 反向見 _unmark）。"""
    inv.payment_status = "已收款"
    if not inv.paid_date:
        inv.paid_date = when


def _unmark_invoice_received(inv) -> None:
    inv.payment_status = "未收款"
    inv.paid_date = None


# 自動產生的待請款在 notes 裡帶這個標記 —— 反向清理（發票退回未收款）只敢刪
# 帶標記且還沒付的，人手建的請款單絕不動。
_AUTO_KAI_NOTE = "代開發票已收款自動產生"

# 🔴 發票的「錢已經付出去」狀態叫**已轉撥**，不是「已付款」（owner 2026-08-20）。
# 這條線上的錢是過路錢：客戶把款匯進公司 → 公司轉撥給代開人。叫「已付款」會跟
# **請款單**的已付款（我們真的付掉一筆費用）混在一起，那是兩件事、兩張表。
# 實測生產 183 張這個狀態全是 payment_type=付款，其中 181 張是代開。
# ⚠ 只改發票這一邊 —— CrmPaymentRequest 與零用金批次的「已付款」語意正確，別動。
INVOICE_REMITTED = "已轉撥"


async def _sync_passthrough_request(session, inv, when=None) -> None:
    """代開發票的收款 → 待請款 自動化（owner 2026-08-20 拍板）。

    業務流：公司幫人代開發票（category=內部代開/外部代開），客戶把錢匯進公司
    → 發票標「已收款」→ 這筆錢的 92% 要匯回給代開人（8% 是稅與手續費），
    所以要出現一張「應付款」的請款單。金額用發票的 commission 欄（發票匯入時
    就算好的代開應匯 = amount_total × 92%），沒有才臨時算。
    之後在應付帳款把它標成已付款 → 發票進「已轉撥」= 整條走完（見 batch_pay）。

    冪等：同一張發票號碼已有「發票代開」請款單（不論狀態）就不再建 ——
    歷史匯入的 178 筆與重複觸發都靠這條擋。
    反向：發票退回「未收款」（收款被刪/改）→ 只刪**自動產生且還是應付款**的
    那張；請款單已付款代表錢真的匯出去了，退狀態不能把付款紀錄變不見。
    """
    if not (inv.category and "代開" in inv.category):
        return
    no = (inv.invoice_number or "").replace("-", "")
    if not no:
        return                      # 沒有號碼就沒有可靠的冪等鍵，不自動建
    from sqlalchemy import func as safunc
    existing = (await session.execute(
        select(CrmPaymentRequest)
        .where(CrmPaymentRequest.category == "發票代開",
               safunc.replace(CrmPaymentRequest.invoice_number, "-", "") == no)
    )).scalars().all()
    if inv.payment_status == "已收款":
        if existing:
            return
        amount = int(inv.commission or 0) or round(int(inv.amount_total or 0) * 0.92)
        if amount <= 0:
            return
        d = when or _now()
        now = _now()
        session.add(CrmPaymentRequest(
            id=uuid.uuid4().hex, entity=inv.entity or "parent",
            request_date=d, amount=amount,
            summary=inv.title or f"代開 {inv.invoice_number}",
            category="發票代開", payee_name=inv.applicant or None,
            needs_invoice=1, invoice_number=inv.invoice_number,
            invoice_amount=int(inv.amount_total or 0),
            project_id=inv.project_id or None,
            payment_status="應付款", planned_month=month_of(d),
            notes=f"{_AUTO_KAI_NOTE}（發票 {int(inv.amount_total or 0):,} × 92% 應匯 {amount:,}）",
            created_at=now, updated_at=now))
    else:
        for p in existing:
            if p.payment_status == "應付款" and _AUTO_KAI_NOTE in (p.notes or ""):
                await session.delete(p)


async def _resettle_invoice(session, invoice_id, when=None,
                            unmark_if_empty: bool = False) -> None:
    """依**帳上實收**重算一張發票的收款狀態。所有寫入路徑的唯一入口。

    🔴 為什麼不是「碰到就標已收款」：那條舊規則讓分期收款靜默消失。實測
    （2026-08-20，394 張歷史發票）有一張面額 144,900 只收 111,050、尚欠 33,850，
    卻因為標了「已收款」而整張不在應收帳款清單裡（`payment_status NOT IN
    ('已收款','作廢')`），同一畫面的發票列表又照實顯示 outstanding 33,850 ——
    兩個數字互相打架，而贏的是錯的那個。

    另一頭同樣要修：合併匯款用「關聯發票」面板掛三張時，舊碼**完全不動**發票
    狀態，三張全留在應收帳款裡 → 錢收了應收卻沒減。

    收齊與否走 core.finance_logic.invoice_is_settled（含匯費容差），與分配面板
    的綠燈判讀同一條規則。

    🔴 `unmark_if_empty`：帳上**一筆收款紀錄都沒有**時，預設什麼都不做。因為
    「查無收款」不等於「沒收到」—— 收支明細從 2024-02-19 才開始，早於它的發票
    是人工標的已收款。實測（2026-08-20）若無條件降級，會有 8 張這種發票被打回
    未收款、憑空生出 2,721,385 的假應收。只有在「我們剛剛親手拿掉最後一筆收款
    憑據」的呼叫點（換發票、刪收支、分配面板移除）才傳 True。
    """
    if not invoice_id:
        return
    from core.finance_logic import invoice_is_settled
    inv = await session.get(CrmInvoice, invoice_id)
    if not inv:
        return
    coll = (await _invoice_collections(session, [invoice_id])).get(invoice_id, {})
    got = coll.get("collected", 0)
    if got and invoice_is_settled(got, inv.amount_total):
        # 代開發票的「已轉撥」是比已收款更後面的階段（錢已匯給代開人）——
        # 收款重算不可以把它降回已收款，那會讓匯出去的紀錄看起來像沒發生。
        if not (inv.payment_status == INVOICE_REMITTED and inv.category
                and "代開" in inv.category):
            _mark_invoice_received(inv, when or coll.get("last_paid_date") or _now())
    elif got or unmark_if_empty:
        # 帳上收了但沒收齊（分期）→ 回到未收款，它才會留在應收帳款裡
        _unmark_invoice_received(inv)
    else:
        return                      # 帳上沒話講 → 不動人工標記
    inv.updated_at = _now()
    await _sync_passthrough_request(session, inv, when=when)


# ── Invoice Endpoints ───────────────────────────────────────

@router.get("/invoices", dependencies=[Depends(money_dep)])
async def list_invoices(
    request: Request,
    q: str = Query(""), payment_type: str = Query(""),
    category: str = Query(""), project_id: str = Query(""),
    issue_status: str = Query(""), entity: str = Query(""),
):
    # 兩本帳：money_dep 之上疊第二層 entity scope（plan §2.4）
    # full：CRM 帳務＝原始帳列，合夥人不可及 —— money_dep 已擋一層，刻意雙保險
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        query = (
            select(CrmInvoice, CrmProject.name.label("pn"))
            .outerjoin(CrmProject, CrmProject.id == CrmInvoice.project_id)
            .where(CrmInvoice.entity == ent)
            # 🔴 日期後面一定要有 tiebreaker：一天開 5-10 張發票很常見，只用
            # invoice_date 排序時，任一列被 UPDATE 後 Postgres 回傳的相對順序就可能改變
            # —— 畫面上是「改了一筆，同日期的列整組跳動」（2026-08-19 實測：連點三次
            # 種類切換鈕，每次點到的是不同筆）。
            .order_by(CrmInvoice.invoice_date.desc(), CrmInvoice.created_at.desc(), CrmInvoice.id)
        )
        if payment_type:
            query = query.where(CrmInvoice.payment_type == payment_type)
        if issue_status:
            query = query.where(CrmInvoice.issue_status == issue_status)
        if category:
            query = query.where(CrmInvoice.category == category)
        if project_id:
            query = query.where(CrmInvoice.project_id == project_id)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(
                CrmInvoice.title.ilike(ql), CrmInvoice.company_name.ilike(ql),
                CrmInvoice.invoice_number.ilike(ql),
            ))
        rows = (await session.execute(query)).all()
        coll = await _invoice_collections(session, [r[0].id for r in rows])
    out = []
    for r in rows:
        d = _to_invoice_dict(r[0], r[1] or "")
        c = coll.get(r[0].id) or {}
        # 清單只帶合計與最後到款日（逐筆明細在 GET /invoices/{id}）—— 分期收款
        # 要能一眼看出「開了多少、收了多少、還欠多少」。
        d["collected"] = c.get("collected", 0)
        d["last_paid_date"] = c.get("last_paid_date")
        d["outstanding"] = int(r[0].amount_total or 0) - d["collected"]
        out.append(d)
    return {"invoices": out, "total": len(out)}


@router.post("/invoices")
async def create_invoice(req: InvoicePayload, request: Request):
    _check_auth(request)
    _require_db()
    ent = _entity_for_write(request, req.entity)
    factory = await _get_factory()
    now = _now()
    inv = CrmInvoice(
        id=uuid.uuid4().hex, invoice_date=_parse_shoot_date(req.invoice_date),
        created_at=now, updated_at=now, entity=ent,
        **req.model_dump(exclude={"invoice_date", "entity"}),
    )
    async with factory() as session:
        await _assert_month_open(session, inv.invoice_date, entity=ent)
        session.add(inv)
        await session.commit()
        await session.refresh(inv)
    return {"status": "ok", "invoice": _to_invoice_dict(inv)}


async def _invoice_collections(session, invoice_ids, with_detail: bool = False):
    """發票 id → 實際收到多少（＋每一筆的日期金額）。

    來源是 crm_cash_invoice_links（收款↔發票分配表）JOIN 收支明細 —— 也就是
    **帳上真的進了多少錢**，不是有人手動勾的狀態欄。一張發票分三次到款時，
    這裡會回三筆，加總就是已收金額，跟面額一減就知道還欠多少。

    with_detail 只決定要不要附上逐筆 `payments`；`collected` 與 `last_paid_date`
    兩種模式都算，而且**走同一段程式碼**。原本清單模式另走一條 GROUP BY，等於
    「已收金額」有兩個算法可以各自出錯 —— 實測（303 筆分配、394 張發票）兩者
    6.4ms vs 6.3ms，那條捷徑什麼也沒買到。
    """
    ids = [i for i in set(invoice_ids or []) if i]
    if not ids:
        return {}
    from db.models import BankAccount
    rows = (await session.execute(
        select(CrmCashInvoiceLink.invoice_id, CrmCashInvoiceLink.amount,
               CrmCashEntry.id, CrmCashEntry.entry_date, CrmCashEntry.summary,
               BankAccount.name)
        .outerjoin(CrmCashEntry, CrmCashEntry.id == CrmCashInvoiceLink.cash_entry_id)
        # 錢進了哪個銀行是對帳時的第一個問題（同一天多家銀行都有進帳很常見）
        .outerjoin(BankAccount, BankAccount.id == CrmCashEntry.bank_account_id)
        .where(CrmCashInvoiceLink.invoice_id.in_(ids))
        .order_by(CrmCashEntry.entry_date))).all()
    out: dict = {}
    for iid, amt, eid, dt, summary, bank in rows:
        d = out.setdefault(iid, {"collected": 0, "last_paid_date": None, "payments": []})
        d["collected"] += int(amt or 0)
        iso = dt.isoformat() if dt else None
        d["payments"].append({"entry_id": eid, "date": iso,
                              "amount": int(amt or 0), "summary": summary or "",
                              "bank_account": bank or ""})
        if iso and (not d["last_paid_date"] or iso > d["last_paid_date"]):
            d["last_paid_date"] = iso
    if not with_detail:
        for d in out.values():
            d.pop("payments")
    return out


@router.get("/invoices/{invoice_id}", dependencies=[Depends(money_dep)])
async def get_invoice(invoice_id: str, request: Request):
    from sqlalchemy import func as safunc
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        inv = await session.get(CrmInvoice, invoice_id)
        if not inv:
            raise HTTPException(status_code=404, detail="找不到此發票")
        require_entity(request, inv.entity or "parent", level="full")  # 兩本帳 scope 驗證（full：原始帳列，money_dep 之上刻意雙保險）
        pn = ""
        if inv.project_id:
            p = await session.get(CrmProject, inv.project_id)
            pn = p.name if p else ""
        coll = (await _invoice_collections(session, [inv.id], with_detail=True)).get(inv.id)
        # 「標已收款卻查無收款」這條提醒只在**收支明細涵蓋得到這張發票的期間**時
        # 才有意義。原本用「這本帳有沒有任何連結」當開關 —— 實測沒用：backfill
        # 一跑完旗標就永遠是 true，那 8 張早於收支表起始日的發票照樣天天跳警告，
        # 等於把提醒訓練成雜訊。改用真正的判準：這張發票的日期有沒有落在收支
        # 明細的涵蓋範圍內。範圍外＝帳上本來就查不到，不是資料有問題。
        covered_from = (await session.execute(
            select(safunc.min(CrmCashEntry.entry_date))
            .where(CrmCashEntry.entity == (inv.entity or "parent")))).scalar()
    d = _to_invoice_dict(inv, pn)
    d["collection_checkable"] = bool(
        covered_from and inv.invoice_date and inv.invoice_date >= covered_from)
    d["collected"] = (coll or {}).get("collected", 0)
    d["last_paid_date"] = (coll or {}).get("last_paid_date")
    d["payments"] = (coll or {}).get("payments", [])
    d["outstanding"] = int(inv.amount_total or 0) - d["collected"]
    return d


@router.put("/invoices/{invoice_id}")
async def update_invoice(invoice_id: str, req: InvoicePayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        inv = await session.get(CrmInvoice, invoice_id)
        if not inv:
            raise HTTPException(status_code=404, detail="找不到此發票")
        # 兩本帳：payload.entity None＝維持既有值；帶不同值＝想搬帳本 → 422
        ent = _entity_for_write(request, req.entity, inv)
        # 舊/新 invoice_date 的月份都要開著（搬進或搬出鎖定月都算改帳）
        new_date = _parse_shoot_date(req.invoice_date)
        await _assert_month_open(session, inv.invoice_date, new_date, entity=ent)
        old_status = inv.payment_status
        for k, v in req.model_dump(exclude={"invoice_date", "entity"}).items():
            setattr(inv, k, v)
        inv.invoice_date = new_date
        inv.updated_at = _now()
        # 代開發票手動標「已收款」（或退回）→ 待請款同步。最常見的觸發點就是
        # 這裡：客戶匯款進來，有人在發票列表把狀態改掉。
        if inv.payment_status != old_status:
            await _sync_passthrough_request(session, inv)
        # 檔名是上傳當下組出來的 —— 發票號碼/日期/抬頭/金額改了就重新對齊
        # （最常見：上傳時還沒填號碼，檔名落到 id 前 8 碼，之後號碼才補上）
        if inv.file_url:
            inv.file_url = await asyncio.to_thread(_resync_invoice_file, inv)
        await session.commit()
    return {"status": "ok"}


@router.delete("/invoices/{invoice_id}")
async def delete_invoice(invoice_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        inv = await session.get(CrmInvoice, invoice_id)
        if not inv:
            raise HTTPException(status_code=404, detail="找不到此發票")
        require_entity(request, inv.entity or "parent", level="full")  # 兩本帳 scope 驗證（full：原始帳列，money_dep 之上刻意雙保險）
        await _assert_month_open(session, inv.invoice_date, entity=inv.entity or "parent")
        # 🔴 連結是 soft FK，DB 不會替我們清。留著孤兒列的後果是靜默的：
        # _load_allocs 只把它標 missing，_alloc_verdict 卻照樣把金額算進
        # allocated → 那筆收款判成「分配與實收相符」，實際上那些錢沒對到任何
        # 存在的發票。收支列也會繼續顯示已刪發票的號碼、has_invoice=1。
        from sqlalchemy import delete as _sadel
        affected = (await session.execute(
            select(CrmCashInvoiceLink.cash_entry_id)
            .where(CrmCashInvoiceLink.invoice_id == invoice_id))).scalars().all()
        await session.execute(_sadel(CrmCashInvoiceLink).where(
            CrmCashInvoiceLink.invoice_id == invoice_id))
        for eid in set(affected):
            e = await session.get(CrmCashEntry, eid)
            if not e:
                continue
            # 還有剩的話主要發票改指金額最大那張，否則清空
            rest = (await session.execute(
                select(CrmCashInvoiceLink.invoice_id, CrmCashInvoiceLink.amount)
                .where(CrmCashInvoiceLink.cash_entry_id == eid)
                .order_by(CrmCashInvoiceLink.amount.desc()))).all()
            _set_primary_invoice(
                e, await session.get(CrmInvoice, rest[0][0]) if rest else None)
        # 直接指向這張、但沒進分配表的收支（舊資料）也要清掉懸空的號碼
        for e in (await session.execute(
                select(CrmCashEntry)
                .where(CrmCashEntry.invoice_id == invoice_id))).scalars().all():
            _set_primary_invoice(e, None)
        await session.delete(inv)
        await session.commit()
    return {"status": "ok"}


# ── Invoice CSV Import ──────────────────────────────────────

_INVOICE_COL_MAP = {
    "payment_type":   ["款項狀態", "payment_type"],
    "issue_status":   ["開立狀態", "issue_status"],
    "invoice_number": ["發票編號", "invoice_number"],
    "invoice_date":   ["填表時間", "invoice_date", "日期"],
    "title":          ["名稱", "title", "案件名稱"],
    "applicant":      ["申請人", "applicant"],
    "category":       ["類別", "category"],
    "invoice_kind":   ["發票種類", "invoice_kind"],
    "amount_ex_tax":  ["未稅價", "amount_ex_tax"],
    "amount_total":   ["發票金額", "amount_total"],
    "tax_amount":     ["稅額", "tax_amount"],
    # 「代開應區」是舊的錯字別名（實際表頭是「代開應匯」）—— 兩個都收，舊檔不回頭壞
    "commission":     ["代開應匯", "代開應區", "commission"],
    "company_name":   ["抬頭", "company_name"],
    "tax_id":         ["統編", "tax_id"],
    "item_type":      ["品項", "item_type"],
    # 紙本發票收件資訊與備註：model 一直有這四根欄位，別名漏了就整欄靜靜丟掉
    "recipient":         ["收件人", "recipient"],
    "recipient_phone":   ["收件電話", "電話", "recipient_phone"],
    "recipient_address": ["收件地址", "地址", "recipient_address"],
    "notes":             ["備註", "附註", "notes"],
}

_INVOICE_INT_FIELDS = {"amount_ex_tax", "amount_total", "tax_amount", "commission"}


def _map_invoice_row(header_map: dict, row: dict) -> dict:
    data = {}
    for field, aliases in _INVOICE_COL_MAP.items():
        for alias in aliases:
            orig = header_map.get(alias.lower())
            if orig and row.get(orig, "").strip():
                val = row[orig].strip()
                data[field] = _parse_money(val) if field in _INVOICE_INT_FIELDS else val
                break
    # Sheet 的「款項狀態」一欄同時是方向（收/付）與狀態（已/未）→ 拆成兩個欄位。
    # 🔴「未收款」裡也有一個「收」字：只判 '收' in pt 會把未收的發票標成**已收款**，
    #    應收帳款憑空消失、收入被提前認列。2026-08-19 匯 394 筆歷史發票時實測到
    #    44 張未收款發票中招。判「已/未」必須先於或同時於判「收/付」。
    pt = data.get("payment_type", "")
    unpaid = "未" in pt
    if "收" in pt:
        data["payment_type"] = "收款"
        data["payment_status"] = "未收款" if unpaid else "已收款"
    elif "付" in pt:
        data["payment_type"] = "付款"
        data["payment_status"] = "未付款" if unpaid else INVOICE_REMITTED
    elif "作廢" in pt:
        data["payment_status"] = "作廢"
    return data


@router.post("/invoices/import_csv")
async def import_invoices_csv(request: Request, file: UploadFile = File(...)):
    _check_auth(request)
    _require_db()
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("big5", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    header_map = {h.lower(): h for h in headers}
    imported = skipped = 0
    factory = await _get_factory()

    async with factory() as session:
        # F1 月結守衛：逐列判 invoice_date 的月，任一列落鎖定月 → 整批 409
        parsed, dated = [], []
        for line_no, row in enumerate(reader, start=2):  # 第 1 列是表頭
            data = _map_invoice_row(header_map, row)
            if not data.get("title"):
                skipped += 1
                continue
            inv_date = _parse_shoot_date(data.pop("invoice_date", None))
            if inv_date:
                dated.append((f"第 {line_no} 列（{inv_date.strftime('%Y-%m-%d')}）", inv_date))
            parsed.append((data, inv_date))
        # 兩本帳：CSV 匯入 v1 限母公司帳（entity='parent'），我的帳不走匯入
        await _assert_rows_open(session, dated, entity="parent")
        for data, inv_date in parsed:
            now = _now()
            inv = CrmInvoice(id=uuid.uuid4().hex, invoice_date=inv_date, entity="parent",
                             created_at=now, updated_at=now, **data)
            session.add(inv)
            imported += 1
        await session.commit()
    return {"status": "ok", "imported": imported, "skipped": skipped}


# ── 已開立電子發票檔 ────────────────────────────────────────
#
# 骨架與零用金收據（routers/crm/costs.py）同一套：後台可設根目錄 → 依規則產生
# 資料夾與檔名 → 串流寫入 + 大小上限 + 副檔名黑名單 → 走白名單守衛的下載端點。
# 刻意**不**共用 receipts_root：收據是我們付出去的憑證、發票是我們開出去的，
# 交給會計師的是兩包不同東西，常常指到不同位置。
_MAX_INVOICE_BYTES = 30 * 1024 * 1024   # 電子發票是 PDF 或存證聯圖，比照收據


def _invoices_root() -> str:
    """電子發票檔根目錄的單一正本：settings.invoices_root（後台可設，要指到
    NAS 或會計師的共用資料夾就填那個路徑），留空＝uploads/invoices。"""
    from config import load_settings
    root = (load_settings().get("invoices_root") or "").strip()
    return root or os.path.join(os.getcwd(), "uploads", "invoices")


def _safe_part(s: str, limit: int = 0) -> str:
    """檔名片段：拿掉 Windows 不收的字元，可選截斷。"""
    out = re.sub(r'[\\/:*?"<>|]', "", (s or "").strip())
    return out[:limit] if limit else out


def _invoice_file_name(inv, ext: str) -> str:
    """{日期}_{發票號碼}_{抬頭前20字}_{含稅金額}[_作廢].{ext}

    - 日期開頭 → 資料夾內自然照時間排序，整包交給會計師順序就是對的
    - 發票號碼 → 法定唯一識別，對帳/查調/作廢都用它；沒號碼（開立中）用 id 前 8 碼
    - 抬頭截斷 20 字 → 台灣公司名很長，不截會撞 Windows 260 字元路徑上限
    - 含稅金額 → 交叉核對用，檔名數字跟系統對不上就知道有問題
    - 作廢放**尾端**不放前綴 —— 放前面會破壞日期排序
    """
    day = _fmt_day(inv.invoice_date)          # 🔴 台北歸一，直接 strftime 會差一天
    parts = [day.replace("-", "") or "nodate",
             _safe_part(inv.invoice_number) or inv.id[:8],
             _safe_part(inv.company_name, 20) or "無抬頭",
             str(inv.amount_total or 0)]
    if (inv.issue_status or "") == "作廢" or (inv.payment_status or "") == "作廢":
        parts.append("作廢")
    return "_".join(p for p in parts if p) + ext


# 統一發票號碼＝2 碼英文 + 8 碼數字（財政部格式，例 DQ45891570）
_TW_INVOICE_NO = r"[A-Z]{2}\d{8}"
# 「發票號碼：」的標籤。電子發票證明聯常把字距拉開（買　　方 / 統 一 編 號），
# 所以每個字之間都允許空白；冒號全形半形都收 —— core.doc_text 的 NFKC 會把
# 全形「：」轉成半形，但直接讀別處來的文字時不一定經過那層。
_INVOICE_NO_LABELLED = re.compile(
    r"發\s*票\s*號\s*碼\s*[:：]?\s*(" + _TW_INVOICE_NO + r")")


def _detect_invoice_number(path: str) -> str:
    """從電子發票證明聯（PDF）抽發票號碼。抽不到回 ""。

    **同步**（呼叫端要丟 asyncio.to_thread）—— pypdf 是 CPU-bound 純同步解析。

    兩段式：先找「發票號碼：」標籤後面的號碼；找不到標籤才退而求其次，看全文
    是否**恰好只有一組**符合格式的字串。「恰好一組」是刻意的 —— 證明聯上除了
    發票號碼還可能有隨機碼、載具號碼，多於一組時猜錯的代價（把法定號碼寫錯）
    遠大於讓人自己填。

    圖片檔（JPG/PNG）走到這裡會被 extract_text 判成不支援 → 回 ""，不做 OCR。
    """
    if os.path.splitext(path)[1].lower() != ".pdf":
        return ""
    try:
        from core.doc_text import extract_text
        text, err = extract_text(path)
    except Exception:
        return ""
    if err or not text:
        return ""
    m = _INVOICE_NO_LABELLED.search(text)
    if m:
        return m.group(1)
    hits = set(re.findall(_TW_INVOICE_NO, text))
    return hits.pop() if len(hits) == 1 else ""


def _resync_invoice_file(inv) -> str:
    """把磁碟上的檔名/資料夾重新對齊這張發票現在的欄位；回傳新路徑（沒動就回原值）。

    為什麼需要：檔名是**上傳當下**由發票欄位組出來的。上傳時還沒填發票號碼的話
    檔名會落到 id 前 8 碼（fallback），之後號碼補上了，檔名還停在
    `20260806_42ec03d2_…`（owner 2026-08-19 實際遇到）。日期改了也一樣 ——
    資料夾是 {年}/{年-月}，改日期就該換資料夾。

    只搬不刪：目標已存在同名檔就不動（不覆蓋別人的憑證），搬移失敗也只是維持原狀，
    絕不讓「改個發票號碼」因為檔案系統的問題而整個失敗。
    """
    old = inv.file_url or ""
    if not old or not os.path.isfile(old):
        return old
    ext = os.path.splitext(old)[1]
    day = _fmt_day(inv.invoice_date)
    base = os.path.join(_invoices_root(), day[:4] or "nodate", day[:7] or "nodate")
    target = os.path.join(base, _invoice_file_name(inv, ext))
    if os.path.abspath(target) == os.path.abspath(old) or os.path.exists(target):
        return old
    try:
        os.makedirs(base, exist_ok=True)
        # 🔴 一定要 shutil.move 不能用 os.replace/os.rename：那兩支**不能跨磁碟區**，
        # 本機 C:\ → NAS \\192.168.1.132\... 會直接丟 WinError 17（2026-08-19 把
        # 三個電子發票搬上 NAS 時實際踩到；幸好失敗時是維持原狀而不是弄丟檔）。
        # shutil.move 在跨裝置時會退成「複製再刪來源」。
        shutil.move(old, target)
        return target
    except (OSError, shutil.Error):
        return old


@router.post("/invoices/{invoice_id}/file")
async def upload_invoice_file(invoice_id: str, request: Request, file: UploadFile = File(...)):
    """上傳這張發票開好的電子發票檔。同一張再傳一次＝取代（舊檔留在磁碟不刪，
    避免誤傳覆蓋掉唯一的正本；要清掉走 DELETE）。"""
    _check_auth(request)
    _require_db()
    from core.project_folders import BLOCKED_UPLOAD_EXTS, stream_to_disk

    ext = os.path.splitext(file.filename or "")[1] or ".pdf"
    if ext.lower() in BLOCKED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"不接受的檔案格式：{ext}")

    factory = await _get_factory()
    async with factory() as session:
        inv = await session.get(CrmInvoice, invoice_id)
        if not inv:
            raise HTTPException(status_code=404, detail="找不到此發票")
        require_entity(request, inv.entity or "parent", level="full")

        day = _fmt_day(inv.invoice_date)
        # {root}/{年}/{年-月}/ —— 按月不按「期」（營業稅兩個月一期，要交一期抓兩夾），
        # 也刻意不按專案（發票是照稅務期間歸檔的，收據才按專案）
        base = os.path.join(_invoices_root(), day[:4] or "nodate", day[:7] or "nodate")
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as e:
            raise HTTPException(status_code=422, detail=f"資料夾無法使用：{e}")

        filepath = os.path.join(base, _invoice_file_name(inv, ext))
        written = await asyncio.to_thread(stream_to_disk, file.file, filepath,
                                          _MAX_INVOICE_BYTES)
        if written < 0:
            raise HTTPException(status_code=413,
                                detail=f"檔案超過 {_MAX_INVOICE_BYTES // 1024 // 1024}MB")
        inv.file_url = filepath
        # 有了電子發票證明聯就代表這張已經開出去了（owner 2026-08-19）。
        # 作廢的不動 —— 作廢也會留存證明聯，那不是「開立中」。
        if (inv.issue_status or "") != "作廢":
            inv.issue_status = "已開立"
        inv.updated_at = _now()
        await session.commit()
        current_number = inv.invoice_number or ""
    # 偵測到的號碼只**回報**、不自動寫入 —— 發票號碼是法定識別，套不套用由人決定
    detected = await asyncio.to_thread(_detect_invoice_number, filepath)
    return {"status": "ok", "file_url": filepath, "file_name": os.path.basename(filepath),
            "detected_invoice_number": detected,
            # 與現有值相同就不用麻煩使用者
            "detected_differs": bool(detected and detected != current_number)}


@router.delete("/invoices/{invoice_id}/file")
async def clear_invoice_file(invoice_id: str, request: Request):
    """解除關聯。**不刪磁碟上的檔** —— 那可能是唯一一份正本，且稅務憑證誤刪
    救不回來；要清檔案由人到資料夾裡處理。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        inv = await session.get(CrmInvoice, invoice_id)
        if not inv:
            raise HTTPException(status_code=404, detail="找不到此發票")
        require_entity(request, inv.entity or "parent", level="full")
        inv.file_url = None
        inv.updated_at = _now()
        await session.commit()
    return {"status": "ok"}


@router.get("/invoice-file")
async def serve_invoice_file(path: str = Query(""), request: Request = None):
    """提供電子發票檔下載/檢視。路徑白名單：只放行 invoices_root 底下的檔
    （比照 costs.serve_receipt —— 沒有這道，這支就是任意檔案讀取）。"""
    _check_auth(request)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="檔案不存在")
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(os.path.abspath(_invoices_root())):
        raise HTTPException(status_code=403, detail="無權存取此路徑")
    from starlette.responses import FileResponse
    return FileResponse(abs_path, filename=os.path.basename(abs_path))


# 短碼長度：secrets.token_urlsafe(9) → 12 字元（72 bits）。這串是要寄給客戶、
# 有時會被人工轉貼甚至念出來的，所以要短；72 bits 對「猜不到」而言仍綽綽有餘，
# 而且每次嘗試都要打一次伺服器（share_token 上有 unique index，查詢是索引命中）。
_SHARE_CODE_BYTES = 9


def _new_share_code() -> str:
    import secrets
    return secrets.token_urlsafe(_SHARE_CODE_BYTES)


@router.post("/invoices/{invoice_id}/share")
async def create_invoice_share_link(invoice_id: str, request: Request):
    """產生（或取回）給客戶下載這張電子發票的短連結。

    冪等：已經有一張就原樣回傳 —— 同一張發票寄兩次信不該讓先寄出去的連結失效。
    要作廢走 DELETE（重置語意）。

    🔴 憑證＝「網址裡那串字與 DB 存的完全相同」，不驗簽章。所以 jwt_secret 輪替
    不會連坐殺掉已經寄給客戶的連結，而那些連結本來就偽造不了。
    v1 用的是 220+ 字元的 JWT，整條網址 287 字元 —— owner 要求縮短，改成 12 字元
    短碼掛在根路徑 `/e/{code}`（約 49 字元）。**舊的長網址仍然有效**（見
    download_invoice_file_public），已經寄出去的連結不會因為這次改版失效。
    """
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        inv = await session.get(CrmInvoice, invoice_id)
        if not inv:
            raise HTTPException(status_code=404, detail="找不到此發票")
        require_entity(request, inv.entity or "parent", level="full")
        if not inv.file_url:
            raise HTTPException(status_code=422, detail="這張發票還沒有上傳電子發票檔")
        if not inv.share_token:
            # unique index 擋碰撞；72 bits 幾乎不會撞，但撞到就換一個而不是噴 500
            for _ in range(5):
                candidate = _new_share_code()
                dup = (await session.execute(
                    select(CrmInvoice.id).where(CrmInvoice.share_token == candidate))).first()
                if not dup:
                    inv.share_token = candidate
                    break
            else:
                raise HTTPException(status_code=503, detail="短碼產生失敗，請再試一次")
            inv.updated_at = _now()
            await session.commit()
        token = inv.share_token
    return {"status": "ok", "token": token, "path": f"/e/{token}",
            "file_name": os.path.basename(inv.file_url or "")}


@router.delete("/invoices/{invoice_id}/share")
async def revoke_invoice_share_link(invoice_id: str, request: Request):
    """作廢已發出的下載連結（寄錯人、客戶換窗口時用）。之後可再產一張新的。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        inv = await session.get(CrmInvoice, invoice_id)
        if not inv:
            raise HTTPException(status_code=404, detail="找不到此發票")
        require_entity(request, inv.entity or "parent", level="full")
        inv.share_token = None
        inv.updated_at = _now()
        await session.commit()
    return {"status": "ok"}


async def serve_invoice_by_share_token(token: str):
    """憑分享碼取電子發票檔 —— **免登入**，憑證就是網址裡那串字。

    短碼與舊 JWT 共用這一支：查詢就是「share_token 逐字等於來訪者出示的字串」，
    格式不影響判定。所以改成短碼之後，改版前已經寄出去的長網址照樣有效。

    只回檔案本身，不回發票的其他欄位 —— 客戶要的是那張憑證，順手多給金額/統編/
    客戶名等於把不必要的東西一起寄出去。
    """
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        row = (await session.execute(
            select(CrmInvoice.file_url).where(CrmInvoice.share_token == token))).first()
    if not row:
        raise HTTPException(status_code=401, detail="連結已失效")
    path = row[0] or ""
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="檔案不存在")
    # 免登入端點更不能讓 DB 裡一個被改壞的路徑變成任意檔案讀取
    if not os.path.abspath(path).startswith(os.path.abspath(_invoices_root())):
        raise HTTPException(status_code=403, detail="無權存取此路徑")
    from starlette.responses import FileResponse
    return FileResponse(os.path.abspath(path), filename=os.path.basename(path))


@token_router.get("/public/invoice-file/{token}")
async def download_invoice_file_public(token: str):
    """舊的長網址（v1 的 JWT 版）。改用 /e/{code} 短碼之後仍保留這條 ——
    改版前已經寄給客戶的連結不該因為我們換格式而變成死連結。

    掛 token_router 不掛 public_router：後者是 NAS 對外容器也會掛的那組
    （master 關機仍要能用），電子發票下載沒有 24/7 的必要，曝露面不必為它變大。
    """
    return await serve_invoice_by_share_token(token)


@router.post("/invoices/migrate-files")
async def migrate_invoice_files(request: Request, apply: bool = Query(False)):
    """把已上傳的電子發票檔搬到**目前**的發票根目錄底下（改過根目錄後補搬）。

    預設 dry-run，`?apply=true` 才真的搬 —— 比照匯入腳本的契約。

    🔴 一定要由 agent 自己跑，不能從別的 shell：主控 agent 跑在 Session 1、
    握有 NAS 的 SMB 連線；其他 session（例如排程或維運用的 shell）看不到那些
    對映與 UNC，會一路 Access denied（memory 的 Session 0 陷阱）。

    搬移交給 _resync_invoice_file —— 它算的目標路徑就是「目前根目錄 + 這張發票
    現在的欄位」，所以順便把舊檔名（例如上傳時號碼還空著而落到 id 前 8 碼的）
    一起對齊。不覆蓋同名檔、搬不動就維持原狀並列在回應裡。
    """
    check_admin(request)
    _require_db()
    factory = await _get_factory()
    moved, skipped, missing = [], [], []
    async with factory() as session:
        rows = (await session.execute(
            select(CrmInvoice).where(CrmInvoice.file_url.isnot(None),
                                     CrmInvoice.file_url != ""))).scalars().all()
        for inv in rows:
            old = inv.file_url
            if not os.path.isfile(old):
                missing.append({"title": inv.title, "path": old})
                continue
            if apply:
                # 🔴 _resync_invoice_file 只搬檔、**不會**改 inv.file_url —— DB 這半
                # 一定要在這裡自己寫回去。少了這行＝檔案搬走了、DB 還指著舊路徑，
                # 稅務憑證全變孤兒檔（2026-08-19 dev 實測到，幸好沒先對生產跑）。
                # 搬不動（目標已存在／權限不足）時它回傳原路徑，就是下面的相等判斷。
                new = await asyncio.to_thread(_resync_invoice_file, inv)
            else:
                day = _fmt_day(inv.invoice_date)
                new = os.path.join(_invoices_root(), day[:4] or "nodate", day[:7] or "nodate",
                                   _invoice_file_name(inv, os.path.splitext(old)[1]))
            if os.path.abspath(new) == os.path.abspath(old):
                skipped.append({"title": inv.title, "path": old,
                                **({"why": "目標已存在或搬移失敗"} if apply else {})})
                continue
            if apply:
                inv.file_url = new
                inv.updated_at = _now()
            moved.append({"title": inv.title, "from": old, "to": new})
        if apply:
            await session.commit()
    return {"status": "ok", "applied": apply, "root": _invoices_root(),
            "moved": moved, "skipped": skipped, "missing": missing,
            "summary": f"{'已搬移' if apply else '將搬移'} {len(moved)} 個；"
                       f"原地不動 {len(skipped)} 個；找不到檔案 {len(missing)} 個"}


@router.get("/invoice-applicants", dependencies=[Depends(money_dep)])
async def get_invoice_applicants(request: Request):
    """開發票的申請人名單（誰能被選為這張票的申請人）。

    🔴 存在 settings 而不是瀏覽器的 localStorage —— 那是每台電腦各自一份，
    在辦公室設好、回家開就只剩空白，而且沒有人知道少了誰（2026-08-20 owner 實際
    踩到：下拉只剩「—」）。這是全公司共用的一份名單，本來就該存在伺服器。

    ⚠ 與 get_invoices_root 同樣刻意不走 settings/load 整包：那條回應對機密欄位是
    遮罩過的，前端拿整包改一個鍵再存回會把真密碼洗成遮罩值。

    沒設定過就用發票資料裡實際用過的人（依張數排序）當預設，名單永遠不會是空的。
    """
    _require_db()
    from sqlalchemy import func as _f
    safunc_count = _f.count
    from config import load_settings
    saved = load_settings().get("invoice_applicants")
    if isinstance(saved, list):
        return {"applicants": [str(x) for x in saved if str(x).strip()],
                "source": "settings"}
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmInvoice.applicant, safunc_count(CrmInvoice.id))
            .where(CrmInvoice.applicant.isnot(None), CrmInvoice.applicant != "")
            .group_by(CrmInvoice.applicant)
            .order_by(safunc_count(CrmInvoice.id).desc()))).all()
    return {"applicants": [r[0].strip() for r in rows if (r[0] or "").strip()],
            "source": "data"}


@router.put("/invoice-applicants")
async def set_invoice_applicants(request: Request):
    """整份取代申請人名單。空陣列＝清空（下次讀會退回用發票資料推導）。"""
    check_admin(request)
    from config import load_settings, save_settings
    body = await request.json()
    names = body.get("applicants")
    if not isinstance(names, list):
        raise HTTPException(status_code=422, detail="applicants 要是陣列")
    clean, seen = [], set()
    for n in names:
        n = str(n or "").strip()
        if n and n not in seen:      # 去重、保留順序（順序＝下拉的顯示順序）
            seen.add(n)
            clean.append(n)
    cfg = load_settings()
    cfg["invoice_applicants"] = clean
    save_settings(cfg)
    return {"status": "ok", "applicants": clean}


@router.get("/invoices-root")
async def get_invoices_root(request: Request):
    """電子發票根目錄設定（admin 專用）。

    ⚠ 與 costs.get_receipts_root 同樣刻意不走 settings/load 整包 —— 那條回應對
    機密欄位是遮罩過的，前端拿整包改一鍵再存回會把真密碼洗成遮罩值。
    """
    check_admin(request)
    from config import load_settings
    return {"invoices_root": (load_settings().get("invoices_root") or ""),
            "default": os.path.join(os.getcwd(), "uploads", "invoices"),
            "effective": _invoices_root()}


@router.post("/invoices-root")
async def set_invoices_root(request: Request):
    check_admin(request)
    from config import load_settings, save_settings
    body = await request.json()
    root = (body.get("invoices_root") or "").strip()
    if root:
        # 🔴 一定要擋相對路徑。`os.makedirs("192.168.1.132\\Archive\\…")` 會**成功** ——
        # 它在 agent 的工作目錄底下建出一整串資料夾，於是「存到 NAS」變成靜靜存進
        # C:\OriginsunAgent\192.168.1.132\… 而畫面上一切正常。2026-08-19 owner 少打
        # 開頭的兩個反斜線就中招（三個電子發票檔全落在本機）。
        # 合法只有兩種：磁碟機開頭（D:\...）或真正的 UNC（\\server\share\...）。
        # 🔴 `os.path.isabs()` 不夠：Windows 對「單一個反斜線開頭」也回 True，
        # 但那是「目前磁碟的根目錄」—— 少打一個反斜線的 \\192.168.1.132\Archive
        # 會變成 C:\192.168.1.132\Archive，而且 makedirs 與寫入測試**都會成功**，
        # 完全看不出存錯地方（2026-08-19 少打兩個、又少打一個，各中一次）。
        drive, _ = ntpath.splitdrive(root)
        looks_unc = root.startswith("\\\\") or root.startswith("//")
        if not (looks_unc or (drive and drive.endswith(":"))):
            raise HTTPException(status_code=422, detail=(
                f"「{root}」不是完整路徑。NAS 請用 \\\\192.168.1.132\\Archive\\... "
                f"（**開頭兩個**反斜線），本機碟請用 D:\\... —— 只有一個反斜線或沒有的話，"
                f"檔案會被存進主控端自己的資料夾裡，而且畫面上完全看不出來。"))
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as e:
            raise HTTPException(status_code=422, detail=f"資料夾無法使用：{e}")
        # makedirs 成功不等於寫得進去（NAS 可能給了列目錄權限卻不給寫）——
        # 實際寫一個檔再刪掉才算數，不然錯誤會延到「同事上傳發票」那一刻才爆。
        probe = os.path.join(root, ".originsun_write_test")
        try:
            with open(probe, "wb") as f:
                f.write(b"ok")
            os.remove(probe)
        except OSError as e:
            raise HTTPException(status_code=422, detail=f"資料夾不可寫入：{e}")
    # settings.json 寫入也包起來 —— agent 自己會定期寫 settings，撞到檔案佔用
    # 會炸成裸 500（收據那支踩過，同一個坑）
    try:
        s = load_settings()
        s["invoices_root"] = root
        save_settings(s)
    except OSError as e:
        raise HTTPException(status_code=503, detail=f"設定檔忙碌中，請再按一次儲存（{e}）")
    return {"status": "ok", "invoices_root": root, "effective": _invoices_root()}


# ── Payment Request Helpers ─────────────────────────────────

def _to_payment_dict(p, project_name: str = "", invoice_title: str = "") -> dict:
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
    entity: str = Query(""),
):
    # 兩本帳：money_dep 之上疊第二層 entity scope（plan §2.4）
    # full：CRM 帳務＝原始帳列，合夥人不可及 —— money_dep 已擋一層，刻意雙保險
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        # 發票號碼在兩邊可能一邊帶連字號一邊不帶 —— 用 replace 歸一再 join，
        # 否則同一張發票只因寫法不同就查不到標題（匯入的 171 筆全是無連字號版）。
        from sqlalchemy import func as safunc
        _no = safunc.replace(CrmPaymentRequest.invoice_number, "-", "")
        query = (
            select(CrmPaymentRequest, CrmProject.name.label("pn"),
                   CrmInvoice.title.label("it"))
            .outerjoin(CrmProject, CrmProject.id == CrmPaymentRequest.project_id)
            .outerjoin(CrmInvoice,
                       safunc.replace(CrmInvoice.invoice_number, "-", "") == _no)
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
        if q:
            ql = f"%{q}%"
            query = query.where(or_(
                CrmPaymentRequest.summary.ilike(ql),
                CrmPaymentRequest.payee_name.ilike(ql),
            ))
        rows = (await session.execute(query)).all()
    return {
        "payments": [_to_payment_dict(r[0], r[1] or "", r[2] or "") for r in rows],
        "total": len(rows),
    }


@router.post("/payments")
async def create_payment(req: PaymentRequestPayload, request: Request):
    _check_auth(request)
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
        session.add(p)
        await session.commit()
    return {"status": "ok", "payment": _to_payment_dict(p)}


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
            exp_sum = (await session.execute(
                select(sa_func.coalesce(sa_func.sum(CrmProjectExpense.actual), 0))
                .where(CrmProjectExpense.advance_id == p.id)
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


@router.patch("/payments/batch-month")
async def batch_update_month(request: Request):
    """更新請款單的 planned_month。

    F1 月結守衛判準：planned_month 是「預計付款月」排程欄，不是權責認列日
    （request_date）也不是現金發生日（payment_date）— 改排程不改帳，不掛守衛
    （所以也沒有 per-entity 鎖月檢查；各列的 entity 維持不變）。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    ids = body.get("payment_ids", [])
    planned_month = body.get("planned_month", "")
    if not ids:
        raise HTTPException(status_code=400, detail="請提供 payment_ids")

    async with factory() as session:
        updated = 0
        for pid in ids:
            p = await session.get(CrmPaymentRequest, pid)
            if p:
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
    _check_auth(request)
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
        # 兩本帳各自鎖月（plan §3）：涉及的 entity 分組、各查一次鎖定月集合
        locked_by_entity = {
            ent: await _locked_month_set(session, entity=ent)
            for ent in {(p.entity or "parent") for p in rows}
        }
        # month_of：timestamptz 回讀是 UTC 表示，直取 strftime 會把月初歸前月
        pay_month = month_of(pay_date)
        if any(pay_month in locked for locked in locked_by_entity.values()):
            raise HTTPException(
                status_code=409,
                detail=f"付款日 {pay_date.strftime('%Y-%m-%d')} 落在已鎖帳月份"
                       "（需修改請先到帳務→現金流重開該月）")
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
            p.payment_status = "已付款"
            p.payment_date = pay_date
            p.updated_at = _now()
            updated += 1
            # 代開請款單付掉 = 錢匯給代開人了 → 對應發票走到「已轉撥」，
            # 整條生命週期（未收款→已收款→已轉撥）收尾。號碼對得上才動。
            if p.category == "發票代開" and p.invoice_number:
                from sqlalchemy import func as safunc
                inv = (await session.execute(
                    select(CrmInvoice).where(
                        safunc.replace(CrmInvoice.invoice_number, "-", "")
                        == p.invoice_number.replace("-", ""),
                        CrmInvoice.payment_status == "已收款")
                )).scalars().first()
                if inv:
                    inv.payment_status = INVOICE_REMITTED
                    inv.updated_at = _now()
        await session.commit()
    return {"status": "ok", "updated": updated}


@router.patch("/payments/batch-unpay")
async def batch_unpay(request: Request):
    """將已付款的請款單改回應付款。

    F1 月結守衛判準同 batch-pay：取消付款是把現金事件從原付款月抽走 —
    原 payment_date 落鎖定月 → 整批 409 並列出違規筆。"""
    _check_auth(request)
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
            # batch_pay 的對稱反向：取消付掉的代開請款單 → 發票從「已轉撥」
            # 退回「已收款」。不退的話會留下「請款單應付款、發票卻已轉撥」
            # 的矛盾 —— 待請款區有一張，發票卻說錢已經匯了。
            if p.category == "發票代開" and p.invoice_number:
                from sqlalchemy import func as safunc
                inv = (await session.execute(
                    select(CrmInvoice).where(
                        safunc.replace(CrmInvoice.invoice_number, "-", "")
                        == p.invoice_number.replace("-", ""),
                        CrmInvoice.payment_status == INVOICE_REMITTED)
                )).scalars().first()
                if inv:
                    inv.payment_status = "已收款"
                    inv.updated_at = _now()
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
    return _to_payment_dict(p, pn)


@router.put("/payments/{payment_id}")
async def update_payment(payment_id: str, req: PaymentRequestPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    date_fields = {"request_date", "payment_date"}
    dates = {f: _parse_shoot_date(getattr(req, f)) for f in date_fields}
    async with factory() as session:
        p = await session.get(CrmPaymentRequest, payment_id)
        if not p:
            raise HTTPException(status_code=404, detail="找不到此請款單")
        # 兩本帳：payload.entity None＝維持既有值；帶不同值＝想搬帳本 → 422
        ent = _entity_for_write(request, req.entity, p)
        # F1 月結守衛：舊/新 request_date 的月份都要開著
        await _assert_month_open(session, p.request_date, dates.get("request_date"),
                                 entity=ent)
        for k, v in req.model_dump(exclude=date_fields | {"entity"}).items():
            setattr(p, k, v)
        for k, v in dates.items():
            setattr(p, k, v)
        p.updated_at = _now()
        await session.commit()
    return {"status": "ok"}


@router.delete("/payments/{payment_id}")
async def delete_payment(payment_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        p = await session.get(CrmPaymentRequest, payment_id)
        if not p:
            raise HTTPException(status_code=404, detail="找不到此請款單")
        require_entity(request, p.entity or "parent", level="full")  # 兩本帳 scope 驗證（full：原始帳列，money_dep 之上刻意雙保險）
        await _assert_month_open(session, p.request_date, entity=p.entity or "parent")
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
    data = {}
    for field, aliases in _PAYMENT_COL_MAP.items():
        for alias in aliases:
            orig = header_map.get(alias.lower())
            if orig and row.get(orig, "").strip():
                val = row[orig].strip()
                data[field] = _parse_money(val) if field == "amount" else val
                break
    # Parse combined payee field: "姓名_身分證" or just "姓名"
    combined = data.pop("payee_combined", "")
    if combined:
        parts = combined.split("_", 1)
        data["payee_name"] = parts[0]
        if len(parts) > 1:
            data["payee_id"] = parts[1]
    return data


@router.post("/payments/import_csv")
async def import_payments_csv(request: Request, file: UploadFile = File(...)):
    _check_auth(request)
    _require_db()
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("big5", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    header_map = {h.lower(): h for h in headers}
    imported = skipped = 0
    factory = await _get_factory()

    async with factory() as session:
        # F1 月結守衛：逐列判 request_date 的月，任一列落鎖定月 → 整批 409
        parsed, dated = [], []
        for line_no, row in enumerate(reader, start=2):  # 第 1 列是表頭
            data = _map_payment_row(header_map, row)
            if not data.get("summary") and not data.get("payee_name"):
                skipped += 1
                continue
            if not data.get("summary"):
                data["summary"] = data.get("payee_name", "")
            req_date = _parse_shoot_date(data.pop("request_date", None))
            pay_date = _parse_shoot_date(data.pop("payment_date", None))
            if req_date:
                dated.append((f"第 {line_no} 列（{req_date.strftime('%Y-%m-%d')}）", req_date))
            parsed.append((data, req_date, pay_date))
        # 兩本帳：CSV 匯入 v1 限母公司帳（entity='parent'），我的帳不走匯入
        await _assert_rows_open(session, dated, entity="parent")
        for data, req_date, pay_date in parsed:
            now = _now()
            p = CrmPaymentRequest(
                id=uuid.uuid4().hex, request_date=req_date, payment_date=pay_date,
                entity="parent", created_at=now, updated_at=now, **data,
            )
            session.add(p)
            imported += 1
        await session.commit()
    return {"status": "ok", "imported": imported, "skipped": skipped}


# ── Cash Entry (收支明細) ───────────────────────────────────

def _set_primary_invoice(e, inv):
    """設（或清除）收支的「主要發票」三欄。

    這三欄是刻意的反正規化 —— 列表與舊查詢都讀它們，所以必須一起動。
    分開寫在兩個端點裡的話，第三條寫入路徑會照抄先看到的那一份。
    """
    e.invoice_id = inv.id if inv else None
    e.invoice_number = (inv.invoice_number or "") if inv else None
    e.has_invoice = 1 if inv else 0


def _to_cash_dict(e, project_name: str = "", invoice_title: str = "") -> dict:
    return {
        "id": e.id,
        "entity": e.entity or "parent",
        "entry_date": e.entry_date.isoformat() if e.entry_date else None,
        "expense": e.expense, "claim": e.claim, "deposit": e.deposit,
        "summary": e.summary or "", "note": e.note or "",
        "category": e.category or "", "item": e.item or "",
        "sub_item": e.sub_item or "", "payee": e.payee or "",
        "status": e.status or "",
        "has_invoice": e.has_invoice, "invoice_number": e.invoice_number or "",
        "project_label": e.project_label or "", "project_id": e.project_id or "",
        "project_name": project_name,
        "payment_date": e.payment_date.isoformat() if e.payment_date else None,
        "payment_status": e.payment_status or "",
        "invoice_id": e.invoice_id or "",
        "bank_fee": e.bank_fee,
        "advance_payment_id": e.advance_payment_id or "",
        "bank_account_id": getattr(e, 'bank_account_id', '') or "",
        "payment_request_id": getattr(e, 'payment_request_id', '') or "",
        "invoice_title": invoice_title,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


# F1 月結守衛（_assert_month_open / _assert_rows_open / _locked_month_set /
# _raise_locked_batch）唯一實作在 routers/crm/_shared.py — 含「守哪個日期欄」判準表。


# 這幾欄是 soft FK：空字串跟 NULL 在 SQL 裡不是同一件事，`WHERE project_id IS NULL`
# 會漏掉存成 '' 的列。寫入前一律把空字串收斂成 None。
_CASH_FK_FIELDS = ("project_id", "invoice_id", "bank_account_id",
                   "advance_payment_id", "payment_request_id")


def _normalize_cash_fks(e):
    for f in _CASH_FK_FIELDS:
        if getattr(e, f, None) == "":
            setattr(e, f, None)


def _enforce_cash_project_link(e):
    """🔴 不變式：只有專案類的收支可以掛專案（core/project_link.CASH_CATEGORIES）。

    在**賦值之後**檢查最終狀態 —— 賦值前預測會漏掉「只改 category、不動 project_id」
    那條路（零用金那邊踩過同樣的洞，見 petty._enforce_project_link 的說明）。
    行政／薪資／房租那種公司層級支出掛到專案上，專案毛利就會多算一筆不屬於它的錢。
    """
    if e.project_id and (e.category or "") not in _PROJECT_LINK_CATEGORIES:
        raise HTTPException(
            status_code=409,
            detail=f"「{e.category or '未分類'}」的收支不能連結專案 —— "
                   f"可連結的類別：{'、'.join(_PROJECT_LINK_CATEGORIES)}")


@router.get("/cash-entries/options", dependencies=[Depends(money_dep)])
async def cash_entry_options(request: Request):
    """收支明細的下拉選項來源（比照 /petty/options）。

    類別清單與「哪些類別可連結專案」都由後端說了算 —— 前端寫死的下拉會跟
    finance_category_map 脫節：`貸款繳款`／`貸款補貼`／`銀行借款` 這些後來加的
    類別就沒同步進去，結果對帳單匯入自己寫出來的列，使用者在編輯視窗選不到
    它的類別（實測種子 32 個、前端只有 27 個）。
    """
    require_entity(request, "", level="full")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        cats = await cash_category_texts(session)
    return {"categories": cats,
            "project_link_categories": list(_PROJECT_LINK_CATEGORIES)}


@router.get("/cash-entries", dependencies=[Depends(money_dep)])
async def list_cash_entries(
    request: Request,
    q: str = Query(""), category: str = Query(""),
    project_id: str = Query(""),
    bank_account_id: str = Query(""), direction: str = Query(""),
    entity: str = Query(""),
):
    """direction：'in'＝只看有收入的、'out'＝只看有支出的、空＝全部。"""
    # 兩本帳：money_dep 之上疊第二層 entity scope（plan §2.4）
    # full：CRM 帳務＝原始帳列，合夥人不可及 —— money_dep 已擋一層，刻意雙保險
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        query = (
            select(CrmCashEntry, CrmProject.name.label("pn"), CrmInvoice.title.label("inv_title"))
            .outerjoin(CrmProject, CrmProject.id == CrmCashEntry.project_id)
            .outerjoin(CrmInvoice, CrmInvoice.id == CrmCashEntry.invoice_id)
            .where(CrmCashEntry.entity == ent)
            .order_by(CrmCashEntry.entry_date.desc())
        )
        if category:
            query = query.where(CrmCashEntry.category == category)
        if project_id:
            query = query.where(CrmCashEntry.project_id == project_id)
        if bank_account_id:
            query = query.where(CrmCashEntry.bank_account_id == bank_account_id)
        if direction == "in":
            query = query.where(CrmCashEntry.deposit > 0)     # NULL > 0 在 SQL 本來就不成立
        elif direction == "out":
            query = query.where(CrmCashEntry.expense > 0)
        if q:
            # 備註也要搜得到 —— 應收發票標籤、匯款客戶、待確認原因都寫在那裡，
            # 只搜摘要的話「找某張發票的那筆收款」永遠找不到。
            ql = f"%{q}%"
            query = query.where(or_(CrmCashEntry.summary.ilike(ql),
                                    CrmCashEntry.payee.ilike(ql),
                                    CrmCashEntry.note.ilike(ql),
                                    CrmCashEntry.invoice_number.ilike(ql)))
        rows = (await session.execute(query)).all()
    return {"entries": [_to_cash_dict(r[0], r[1] or "", r[2] or "") for r in rows], "total": len(rows)}


@router.post("/cash-entries")
async def create_cash_entry(req: CashEntryPayload, request: Request):
    _check_auth(request)
    _require_db()
    # summary 在 schema 是選填（PUT 要能只送幾個欄位做部分更新），建立時必填由這裡驗
    if not (req.summary or "").strip():
        raise HTTPException(status_code=422, detail="內容（摘要）必填")
    ent = _entity_for_write(request, req.entity)
    factory = await _get_factory()
    date_fields = {"entry_date", "payment_date"}
    dates = {f: _parse_shoot_date(getattr(req, f)) for f in date_fields}
    data = req.model_dump(exclude=date_fields | {"entity"})
    e = CrmCashEntry(id=uuid.uuid4().hex, **dates, entity=ent, created_at=_now(), **data)
    _normalize_cash_fks(e)
    _enforce_cash_project_link(e)
    async with factory() as session:
        await _assert_month_open(session, dates.get("entry_date"), entity=ent)
        session.add(e)
        await session.flush()      # 讓 _resettle_invoice 的查詢看得到這筆
        # 🔴 建立也要進分配表，不能只有更新路徑做（2026-08-20 實測：新開一筆掛了
        # 發票的收款，列表那欄看得到，但「關聯發票」面板是空的、發票的已收金額
        # 停在 0 —— 因為那兩處讀的是 crm_cash_invoice_links 不是 invoice_id。
        # 要按一次編輯再存檔才會補上，等於帳目正確與否取決於有沒有人多按一下。）
        await _sync_single_alloc(session, e)
        await session.flush()
        await _resettle_invoice(session, e.invoice_id,
                                when=dates.get("entry_date") or _now())
        await session.commit()
    return {"status": "ok", "entry_id": e.id, "entry": {"id": e.id}}


async def _sync_single_alloc(session, e):
    """編輯表單改了發票 → 分配表跟著換成「這一張、全額」。

    🔴 不同步的話兩邊會講不同的話：`crm_cash_entries.invoice_id`（列表那欄讀它）
    指向 A，`crm_cash_invoice_links`（關聯發票面板與 _invoice_collections 讀它）
    還留著 B —— 於是列表顯示 A、面板顯示 B，而 A 的「已收金額」永遠不會動。
    收入列才有意義（分配表講的是收款）。

    🔴 但**已經掛了多張**的收款不歸這裡管 —— 那是合併匯款/分期，由「關聯發票」
    面板維護。硬同步會把三張壓成一張全額（2026-08-20 實測：180,000 掛 A/B/C
    三張，有人在編輯視窗動一下發票下拉，A 與 C 的已收就憑空歸零、B 變成收了
    180,000 超過面額，全程沒有任何提示）。所以多張時擋下來，請他去面板改。
    """
    from sqlalchemy import delete as sa_del
    existing = (await session.execute(
        select(CrmCashInvoiceLink.invoice_id)
        .where(CrmCashInvoiceLink.cash_entry_id == e.id))).scalars().all()
    if len(existing) > 1:
        if e.invoice_id in existing:
            return          # 只是動了別的欄位，多張分配原封不動
        raise HTTPException(
            status_code=409,
            detail=f"這筆收款已經分配給 {len(existing)} 張發票（合併匯款或分期）—— "
                   f"要改請用「關聯發票」面板，從編輯視窗改會把其他幾張的已收金額清掉")
    await session.execute(sa_del(CrmCashInvoiceLink).where(
        CrmCashInvoiceLink.cash_entry_id == e.id))
    # deposit 被清成 0（改成支出列）→ 只刪不建：這筆錢已經不是收款了，
    # 留著舊分配列會讓那張發票永遠算已收（實測會留孤兒）。
    if e.invoice_id and (e.deposit or 0):
        session.add(CrmCashInvoiceLink(id=uuid.uuid4().hex, cash_entry_id=e.id,
                                       invoice_id=e.invoice_id,
                                       amount=int(e.deposit or 0)))


@router.put("/cash-entries/{entry_id}")
async def update_cash_entry(entry_id: str, req: CashEntryPayload, request: Request):
    """部分更新：**只寫前端真的送來的欄位**（`exclude_unset=True`）。

    🔴 為什麼不是整包 model_dump：收支明細有 20 幾個欄位，但編輯視窗只送 10 個
    （見前端 `_FIELDS`）。整包寫回會把沒送的 status／project_label／invoice_number／
    payee 全部洗成 pydantic 預設值 —— 匯入進來的「待確認」標記、專案標籤、發票號碼
    只要有人用編輯視窗改一下就沒了。這是全 repo 的既定慣例（20+ 個更新端點都用
    exclude_unset，`update_loan` 也是），這支是漏網的。

    也因為是部分更新，詳情面板的快速連結下拉直接打這支送 `{project_id}` 就好，
    不需要另開一支「只改關聯欄」的端點。
    """
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    date_fields = {"entry_date", "payment_date"}
    data = req.model_dump(exclude_unset=True, exclude={"entity"})
    dates = {f: _parse_shoot_date(data[f]) for f in date_fields if f in data}
    async with factory() as session:
        e = await session.get(CrmCashEntry, entry_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此收支紀錄")
        # 兩本帳：payload.entity None＝維持既有值；帶不同值＝想搬帳本 → 422
        ent = _entity_for_write(request, req.entity, e)
        await _assert_month_open(session, e.entry_date, dates.get("entry_date"),
                                 entity=ent)
        old_invoice_id = e.invoice_id
        for k, v in data.items():
            if k not in date_fields:
                setattr(e, k, v)
        for k, v in dates.items():
            setattr(e, k, v)
        e.updated_at = _now()
        # 🔴 發票收款狀態只在「這次真的動到 invoice_id」時才連動 —— 部分更新時
        # req.invoice_id 是 None 只代表「沒送這個欄位」，不代表要解除關聯。
        # 🔴 金額變了也要同步分配表，不是只有換發票才要 —— 收款 180,000 打錯成
        # 18,000，分配列會留在 180,000，那張發票的 outstanding 就少算 162,000
        # （明明還欠錢卻顯示收滿）。2026-08-20 實測。
        if "invoice_id" in data or "deposit" in data:
            await _sync_single_alloc(session, e)
        if "invoice_id" in data:
            if old_invoice_id and old_invoice_id != e.invoice_id:
                await _resettle_invoice(session, old_invoice_id,
                                        unmark_if_empty=True)
            _set_primary_invoice(e, await session.get(CrmInvoice, e.invoice_id)
                                 if e.invoice_id else None)
        if "invoice_id" in data or "deposit" in data:
            await _resettle_invoice(session, e.invoice_id,
                                    when=e.entry_date or _now())
        _normalize_cash_fks(e)
        _enforce_cash_project_link(e)
        await session.commit()
    return {"status": "ok"}


@router.delete("/cash-entries/{entry_id}")
async def delete_cash_entry(entry_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await session.get(CrmCashEntry, entry_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此收支紀錄")
        require_entity(request, e.entity or "parent", level="full")  # 兩本帳 scope 驗證（full：原始帳列，money_dep 之上刻意雙保險）
        await _assert_month_open(session, e.entry_date, entity=e.entity or "parent")
        # 對帳工作台反向清理：這筆若被對帳單明細認領，解除認領
        # （否則該列永遠顯示已勾銷、指向不存在的收支）
        from sqlalchemy import delete as _sadelete, update as _saupdate
        from db.models import BankStatementLine
        await session.execute(_saupdate(BankStatementLine).where(
            BankStatementLine.matched_entry_id == entry_id).values(matched_entry_id=None))
        # 🔴 發票分配也要一起刪：連結是 soft FK，DB 不會自動清。留著的話那張發票
        # 的「已收金額」會一直把這筆刪掉的錢算進去 —— 實測刪掉三筆分期收款後，
        # 發票仍顯示已收滿額、尚欠 0，等於帳面上憑空多收了一次。
        touched = (await session.execute(
            select(CrmCashInvoiceLink.invoice_id)
            .where(CrmCashInvoiceLink.cash_entry_id == entry_id))).scalars().all()
        await session.execute(_sadelete(CrmCashInvoiceLink).where(
            CrmCashInvoiceLink.cash_entry_id == entry_id))
        await session.delete(e)
        await session.flush()
        # 被這筆收款影響到的發票要重算 —— 舊碼是「無條件打回未收款」，那會把
        # 合併匯款裡另外兩筆收款也收過的發票一起打回去。
        for iid in set(touched) | ({e.invoice_id} if e.invoice_id else set()):
            await _resettle_invoice(session, iid, unmark_if_empty=True)
        await session.commit()
    return {"status": "ok"}


_CASH_COL_MAP = {
    "entry_date": ["日期"], "expense": ["支出"], "claim": ["請款"],
    "deposit": ["存入"], "summary": ["摘要"], "note": ["附註"],
    "category": ["類別"], "item": ["項目"], "sub_item": ["子項目"],
    "payee": ["收款人"], "status": ["狀態"],
    "invoice_number": ["發票號碼"], "project_label": ["專案標籤"],
    "payment_date": ["付款日"], "payment_status": ["付款狀態"],
}
_CASH_INT_FIELDS = {"expense", "claim", "deposit"}


def _map_cash_row(header_map: dict, row: dict) -> dict:
    data = {}
    for field, aliases in _CASH_COL_MAP.items():
        for alias in aliases:
            orig = header_map.get(alias.lower())
            if orig and row.get(orig, "").strip():
                val = row[orig].strip()
                data[field] = _parse_money(val) if field in _CASH_INT_FIELDS else val
                break
    return data


@router.post("/cash-entries/import_csv")
async def import_cash_csv(request: Request, file: UploadFile = File(...)):
    _check_auth(request)
    _require_db()
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("big5", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    header_map = {h.lower(): h for h in headers}
    imported = skipped = 0
    factory = await _get_factory()

    async with factory() as session:
        # F1 月結守衛：逐列判 entry_date 的月，任一列落鎖定月 → 整批 409
        parsed, dated = [], []
        for line_no, row in enumerate(reader, start=2):  # 第 1 列是表頭
            data = _map_cash_row(header_map, row)
            if not data.get("summary"):
                skipped += 1
                continue
            entry_date = _parse_shoot_date(data.pop("entry_date", None))
            pay_date = _parse_shoot_date(data.pop("payment_date", None))
            if entry_date:
                dated.append((f"第 {line_no} 列（{entry_date.strftime('%Y-%m-%d')}）", entry_date))
            parsed.append((data, entry_date, pay_date))
        # 兩本帳：CSV 匯入 v1 限母公司帳（entity='parent'），我的帳不走匯入
        await _assert_rows_open(session, dated, entity="parent")
        for data, entry_date, pay_date in parsed:
            e = CrmCashEntry(id=uuid.uuid4().hex, entry_date=entry_date, entity="parent",
                             payment_date=pay_date, created_at=_now(), **data)
            session.add(e)
            imported += 1
        await session.commit()
    return {"status": "ok", "imported": imported, "skipped": skipped}


# ── Accounts Payable (應付帳款) ─────────────────────────────

@router.get("/payables/summary", dependencies=[Depends(money_dep)])
async def payables_summary(request: Request, month: str = Query(""),
                           status: str = Query(""), entity: str = Query("")):
    """請款彙總，按收款人分組。month=all 或空=全部應付款；month=YYYY-MM=該月。"""
    # 兩本帳：money_dep 之上疊第二層 entity scope（plan §2.4）
    # full：CRM 帳務＝原始帳列，合夥人不可及 —— money_dep 已擋一層，刻意雙保險
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()

    filter_all = (not month) or month == "all"

    async with factory() as session:
        query = (
            select(CrmPaymentRequest, CrmStaff.id_number, CrmStaff.bank_name, CrmStaff.bank_account)
            .outerjoin(CrmStaff, CrmStaff.name == CrmPaymentRequest.payee_name)
            .where(or_(CrmPaymentRequest.is_advance == 0, CrmPaymentRequest.is_advance.is_(None)))
            .where(CrmPaymentRequest.entity == ent)
            .order_by(CrmPaymentRequest.request_date)
        )

        if not filter_all:
            parts = month.split("-")
            try:
                year, mon = int(parts[0]), int(parts[1])
            except (IndexError, ValueError):
                raise HTTPException(status_code=400, detail="month 格式無效，請使用 YYYY-MM")
            from calendar import monthrange
            try:
                start = datetime(year, mon, 1, tzinfo=timezone.utc)
            except ValueError:
                raise HTTPException(status_code=400, detail="month 格式無效，請使用 YYYY-MM")
            _, last_day = monthrange(year, mon)
            end = datetime(year, mon, last_day, 23, 59, 59, tzinfo=timezone.utc)
            query = query.where(or_(
                CrmPaymentRequest.planned_month == month,
                CrmPaymentRequest.request_date.between(start, end),
            ))

        if status == "已付款":
            query = query.where(CrmPaymentRequest.payment_status == "已付款")
        elif status == "all":
            pass  # 全部狀態，不加篩選
        elif not status or status == "應付款":
            # 預設：應付款（含舊資料的「未付款」）
            query = query.where(CrmPaymentRequest.payment_status.in_(["應付款", "未付款"]))

        rows = (await session.execute(query)).all()

    # 分組聚合是純邏輯，抽在 core/crm_logic.py（有單元測試）
    from core.crm_logic import group_payables
    return {"month": month or "all", **group_payables(rows)}


@router.get("/receivables/summary", dependencies=[Depends(money_dep)])
async def receivables_summary(request: Request, status: str = Query(""),
                              entity: str = Query("")):
    """應收帳款彙總：已開立的**收款**發票按客戶（company_name）分組。
    status=未收款/已收款/空=全部（空＝尚未收到的）。

    🔴 只算收款方向。發票的 payment_type 有 收款／付款 兩種：「付款」是代開發票
    （我們開給對方、錢是我們要付出去的）。原本只用
    `payment_status NOT IN ('已收款','作廢')` 過濾，「已轉撥」不在那個清單裡就被
    當成應收 —— 2026-08-19 匯 394 筆歷史發票後實測：應收 13,554,350 裡有
    10,656,093（79%、183 張）其實是代開的付款發票，把應收虛增成 4.7 倍。
    以前系統裡 0 筆發票，這個缺陷看不出來。
    """
    # 兩本帳：money_dep 之上疊第二層 entity scope（plan §2.4）
    # full：CRM 帳務＝原始帳列，合夥人不可及 —— money_dep 已擋一層，刻意雙保險
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        query = (
            select(CrmInvoice, CrmProject.name.label("pn"),
                   Client.tax_id.label("c_tax_id"), Client.payment_info, Client.payment_note)
            .outerjoin(CrmProject, CrmProject.id == CrmInvoice.project_id)
            .outerjoin(Client, Client.short_name == CrmInvoice.company_name)
            .where(CrmInvoice.issue_status == "已開立")
            .where(CrmInvoice.entity == ent)
            # 🔴 日期後面一定要有 tiebreaker：一天開 5-10 張發票很常見，只用
            # invoice_date 排序時，任一列被 UPDATE 後 Postgres 回傳的相對順序就可能改變
            # —— 畫面上是「改了一筆，同日期的列整組跳動」（2026-08-19 實測：連點三次
            # 種類切換鈕，每次點到的是不同筆）。
            .order_by(CrmInvoice.invoice_date.desc(), CrmInvoice.created_at.desc(), CrmInvoice.id)
        )
        # 收款方向才是應收（NULL 視為收款 —— 與 _to_invoice_dict 的 `or "收款"` 一致；
        # 「付款」的代開發票與「作廢」都排除）
        query = query.where(or_(CrmInvoice.payment_type == "收款",
                                CrmInvoice.payment_type.is_(None)))
        if status:
            query = query.where(CrmInvoice.payment_status == status)
        else:
            query = query.where(CrmInvoice.payment_status.notin_(["已收款", "作廢"]))
        rows = (await session.execute(query)).all()

    # 分組聚合是純邏輯，抽在 core/crm_logic.py（有單元測試）
    from core.crm_logic import group_receivables
    return group_receivables(rows, _now())


@router.patch("/invoices/batch-receive")
async def batch_receive(request: Request):
    """批次標記發票為已收款。

    F1 月結守衛：本端點一直沒掛守衛（發票的權責認列月 = invoice_date 不變，
    標記收款只動 payment_status/paid_date；現金側鎖月由收支明細把關）——
    entity 化維持原判準，各列的 entity 不變、不做鎖月檢查。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    ids = body.get("invoice_ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="請提供 invoice_ids")

    async with factory() as session:
        updated = 0
        for iid in ids:
            inv = await session.get(CrmInvoice, iid)
            if inv and inv.payment_status != "已收款":
                # 批次標記沒有收支入帳日 → 以標記時間為收款日（已有值不覆蓋）
                _mark_invoice_received(inv, _now())
                inv.updated_at = _now()
                await _sync_passthrough_request(session, inv)
                updated += 1
        await session.commit()
    return {"status": "ok", "updated": updated}



# ── 收款 ↔ 發票 多對多分配（合併匯款 / 分期收款）────────────────
#
# owner 2026-08-19：「有些時候客戶會合併匯款，我希望款項的發票如果需要的話可以
# 讓我掛一張以上的發票，並且做金額的檢查」。
#
# 金額檢查是**提示不是閘門**：實收常比發票少幾十元（收款方扣匯費）、也常見分期
# 只收一半。硬擋會逼人亂填，所以後端算出差額與判讀，由人決定要不要理。

def _alloc_verdict(entry, allocated: int) -> dict:
    """分配金額 vs 這筆收款、vs 發票面額 → 給人看的判讀。"""
    received = int(entry.deposit or 0)
    diff = allocated - received
    if not allocated:
        state, msg = "empty", "還沒分配任何發票"
    elif diff == 0:
        state, msg = "ok", "分配金額與實收金額相符"
    elif 0 < diff <= 50:
        state, msg = "fee", f"分配比實收多 {diff} 元 —— 常見於收款方代扣匯費"
    elif diff > 50:
        state = "over"
        msg = (f"分配金額比實收多 {diff:,} 元 —— 可能是這幾張發票沒有全部收齊"
               f"（分期），或多掛了一張")
    else:
        state = "under"
        msg = (f"分配金額比實收少 {-diff:,} 元 —— 還有沒掛上的發票，"
               f"或這筆收款有一部分不屬於發票")
    return {"received": received, "allocated": allocated, "diff": diff,
            "state": state, "message": msg}


async def _load_allocs(session, entry):
    """回 (連結列 dict 清單, 分配合計)。"""
    rows = (await session.execute(
        select(CrmCashInvoiceLink, CrmInvoice)
        .outerjoin(CrmInvoice, CrmInvoice.id == CrmCashInvoiceLink.invoice_id)
        .where(CrmCashInvoiceLink.cash_entry_id == entry.id)
        .order_by(CrmCashInvoiceLink.created_at))).all()
    coll = await _invoice_collections(session, [l.invoice_id for l, _i in rows])
    items, allocated = [], 0
    for link, inv in rows:
        allocated += int(link.amount or 0)
        items.append({
            "invoice_id": link.invoice_id,
            "amount": int(link.amount or 0),
            # 這張發票**整體**收了多少（跨所有收款）—— 分期時要看得到全貌，
            # 不能只看眼前這一筆分配了多少
            "collected": coll.get(link.invoice_id, {}).get("collected", 0),
            "outstanding": (int((inv.amount_total if inv else 0) or 0)
                            - coll.get(link.invoice_id, {}).get("collected", 0)),
            "invoice_number": (inv.invoice_number if inv else "") or "",
            "title": (inv.title if inv else "") or "",
            "amount_total": int((inv.amount_total if inv else 0) or 0),
            "missing": inv is None,      # 發票被刪了，連結變孤兒
        })
    return items, allocated


@router.get("/cash-entries/{entry_id}/invoices", dependencies=[Depends(money_dep)])
async def list_cash_entry_invoices(entry_id: str, request: Request):
    """這筆收款掛了哪些發票、各分配多少、與實收差多少。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await session.get(CrmCashEntry, entry_id)
        if not e:
            raise HTTPException(status_code=404, detail="收支明細不存在")
        require_entity(request, e.entity or "parent", level="full")
        items, allocated = await _load_allocs(session, e)
    return {"items": items, "check": _alloc_verdict(e, allocated)}


@router.put("/cash-entries/{entry_id}/invoices")
async def set_cash_entry_invoices(entry_id: str, req: CashInvoiceLinksPayload,
                                  request: Request):
    """整組取代這筆收款的發票分配。

    金額檢查不擋（見上），但這幾件會擋 —— 它們是**一定錯**而不是可能錯：
      發票不存在／不同帳本；同一張發票重複出現；分配金額 ≤ 0。

    副作用（刻意）：`invoice_id` / `invoice_number` / `has_invoice` 同步成金額最大
    的那張發票 —— 列表與舊查詢都讀這幾欄，不同步的話畫面會跟明細對不起來。
    """
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await session.get(CrmCashEntry, entry_id)
        if not e:
            raise HTTPException(status_code=404, detail="收支明細不存在")
        ent = require_entity(request, e.entity or "parent", level="full")
        await _assert_month_open(session, e.entry_date, entity=ent)

        seen = set()
        rows = []
        for it in (req.items or []):
            iid = (it.invoice_id or "").strip()
            if not iid:
                raise HTTPException(status_code=422, detail="invoice_id 不可為空")
            if iid in seen:
                raise HTTPException(status_code=422,
                                    detail="同一張發票不可重複掛在同一筆收款")
            seen.add(iid)
            inv = await session.get(CrmInvoice, iid)
            if not inv:
                raise HTTPException(status_code=404, detail=f"發票不存在：{iid}")
            if (inv.entity or "parent") != ent:
                raise HTTPException(status_code=409, detail="收款與發票分屬不同帳本")
            amt = int(it.amount or 0)
            if amt <= 0:
                raise HTTPException(
                    status_code=422,
                    detail=f"分配金額要大於 0（{inv.invoice_number or inv.title}）")
            rows.append((inv, amt))

        from sqlalchemy import delete as sa_delete
        prev = (await session.execute(
            select(CrmCashInvoiceLink.invoice_id)
            .where(CrmCashInvoiceLink.cash_entry_id == entry_id))).scalars().all()
        await session.execute(sa_delete(CrmCashInvoiceLink).where(
            CrmCashInvoiceLink.cash_entry_id == entry_id))
        for inv, amt in rows:
            session.add(CrmCashInvoiceLink(
                id=uuid.uuid4().hex, cash_entry_id=entry_id,
                invoice_id=inv.id, amount=amt))
        # 主要發票 = 分配金額最大的那張（沒有就清空）
        _set_primary_invoice(e, max(rows, key=lambda x: x[1])[0] if rows else None)
        await session.flush()
        # 🔴 這裡本來完全不動發票狀態 —— 客戶合併匯款 300,000 掛三張，三張都還
        # 留在應收帳款裡（錢收了、應收沒減，資產負債表虛增）。移除的那些也要
        # 重算，否則錢退掉了卻永遠掛已收款。
        kept = {inv.id for inv, _a in rows}
        for iid in kept:
            await _resettle_invoice(session, iid, when=e.entry_date or _now())
        for iid in set(prev) - kept:        # 被移除的：憑據是我們剛拿掉的
            await _resettle_invoice(session, iid, unmark_if_empty=True)
        await session.commit()

        items, allocated = await _load_allocs(session, e)
    return {"ok": True, "items": items, "check": _alloc_verdict(e, allocated)}
