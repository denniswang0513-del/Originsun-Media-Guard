"""routers/crm/finance.py — 帳務：發票 / 請款 / 預支款 / 收支明細 /
應付帳款 / 應收帳款（含各 CSV 匯入）。

自 routers/api_crm.py 原樣搬移（純搬移，行為不變）。
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import re
import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, UploadFile, File, Query

from core.crm_logic import normalize_tax_id
from core.finance_logic import (INVOICE_COLLECTED_STATUSES as INVOICE_COLLECTED,
                                INVOICE_PASSTHROUGH_COLLECTED,
                                INVOICE_PENDING_REMIT, INVOICE_REMITTED,
                                issue_status_for,
                                INVOICE_RECEIVED, alloc_verdict,
                                initial_invoice_status,
                                amount_is_settled, invoice_direction,
                                apply_payment_fee, apply_receipt_fee,
                                is_passthrough_category, month_of,
                                normalize_invoice_status, passthrough_commission)
from core.auth import check_logged_in
from sqlalchemy import and_ as _sa_and
from core.cash_taxonomy import SEP as _TAX_SEP, split_category as _tax_split
from core.ledger import (not_mine as _cli_not_mine, require_entity,
                         not_mine_project as _not_mine_project)
from core.project_link import CASH_CATEGORIES as _PROJECT_LINK_CATEGORIES
from core.project_match import prepare as prepare_projects
from core.project_match import suggest_project
from core.project_link import PAYMENT_CATEGORIES as _PAYMENT_LINK_CATEGORIES
from core.schemas import (InvoicePayload, PaymentRequestPayload, CashEntryPayload,
                          CashInvoiceLinksPayload, CashPaymentLinksPayload,
                          CashTaxonomyNodePayload, CashTaxonomyNodeUpdate)

from ._shared import (router, _check_auth, money_dep, _require_db,
                      ledger_categories_and_tree,
                      _get_factory, _fmt_day, _now,
                      _parse_shoot_date, _assert_month_open, _assert_rows_open,
                      _locked_month_set, _raise_locked_batch, map_csv_row)

# 這個檔案唯一用得到發票檔那邊的東西：改發票時要跟著改檔名
from .invoice_files import _resync_invoice_file

try:
    from ._shared import (select, or_, func,
                          Client, CrmProject, CrmStaff, CrmInvoice,
                          CrmPaymentRequest, CrmCashEntry, CrmProjectExpense,
                          CrmCashInvoiceLink, CrmCashPaymentLink)
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

    寫入守衛也在這裡定案（_mine_or_admin_write：母公司=Lv3、私帳=mine full）——
    這支是所有建立/更新端點的咽喉，守衛放咽喉，下一個端點就不會忘了呼叫。
    刪除/批次端點沒有 payload 不走這裡，各自明呼 _mine_or_admin_write。
    """
    if payload_entity is None:
        ent = (row.entity or "parent") if row is not None else "parent"
    else:
        ent = require_entity(request, payload_entity, level="full")
        if row is not None and (row.entity or "parent") != ent:
            raise HTTPException(status_code=422, detail="這筆帳不可跨帳本搬移")
    _mine_or_admin_write(request, ent)
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
    """發票收到錢了：標狀態 + paid_date first-wins（兩欄不變式成對動 — 反向見 _unmark）。

    代開發票標的是**待撥款**不是已收款：錢進來了，但這筆是過路錢，還欠代開人
    一次轉撥（owner 2026-08-21）。同一個動作也會生出那張應付的請款單，見
    _sync_passthrough_request。
    """
    inv.payment_status = (INVOICE_PENDING_REMIT
                          if is_passthrough_category(inv.category)
                          else INVOICE_RECEIVED)
    if not inv.paid_date:
        inv.paid_date = when


def _unmark_invoice_received(inv) -> None:
    inv.payment_status = "未收款"
    inv.paid_date = None


# 自動產生的待請款在 notes 裡帶這個標記 —— 反向清理（發票退回未收款）只敢刪
# 帶標記且還沒付的，人手建的請款單絕不動。
_AUTO_KAI_NOTE = "代開發票已收款自動產生"

# 發票款項狀態的正本在 core/finance_logic —— 這裡只是把名字帶進來用。
# ⚠ 只有發票這一邊用這組字：CrmPaymentRequest 與零用金批次的「已付款」語意
# 正確（我們真的付掉一筆費用），別動。


async def _passthrough_requests_for(session, invs) -> dict:
    """代開發票 → 它那張自動請款單 {invoice_id: [請款單…]}（整批預載）。

    鍵的規則跟 _sync_passthrough_request 同一套：source_invoice_id 為主、
    舊資料退回發票號碼、**空號一律不比**。
    🔴 逐張查的代價不是「多一個往返」而已 —— 條件裡的 replace() 是欄位上的函式，
    索引用不到，等於**每張發票掃一次全部的請款單**（生產 816 筆）。批次重算
    25 張發票就是掃 25 遍。
    """
    invs = [i for i in invs if is_passthrough_category(i.category)]
    if not invs:
        return {}
    from sqlalchemy import func as safunc
    ids = {i.id for i in invs}
    nos = {(i.invoice_number or "").replace("-", "") for i in invs} - {""}
    conds = [CrmPaymentRequest.source_invoice_id.in_(ids)]
    if nos:
        conds.append(
            safunc.replace(CrmPaymentRequest.invoice_number, "-", "").in_(nos))
    rows = (await session.execute(
        select(CrmPaymentRequest)
        .where(CrmPaymentRequest.category == "發票代開", or_(*conds)))).scalars().all()
    by_id, by_no = {}, {}
    for p in rows:
        if p.source_invoice_id:
            by_id.setdefault(p.source_invoice_id, []).append(p)
        k = (p.invoice_number or "").replace("-", "")
        if k:
            by_no.setdefault(k, []).append(p)
    out = {}
    for i in invs:
        hits = list(by_id.get(i.id, []))
        for p in by_no.get((i.invoice_number or "").replace("-", ""), []):
            if p not in hits:
                hits.append(p)
        out[i.id] = hits
    return out


async def _sync_passthrough_request(session, inv, when=None, existing=None) -> None:
    """代開發票的收款 → 待請款 自動化（owner 2026-08-20 拍板）。

    業務流：公司幫人代開發票（category=內部代開/外部代開），客戶把錢匯進公司
    → 發票標「已收款」→ 這筆錢的 92% 要匯回給代開人（8% 是稅與手續費），
    所以要出現一張「應付款」的請款單。金額用發票的 commission 欄（發票匯入時
    就算好的代開應匯 = amount_total × 92%），沒有才臨時算。
    之後在應付帳款把它標成已付款 → 發票進「已轉撥」= 整條走完（見 batch_pay）。

    冪等鍵是 **source_invoice_id**（發票 id），不是發票號碼。
    🔴 原本拿號碼當鍵，遇到**無號發票**（代開還沒拿到號碼）就 `return` ——
    收款在收支明細掛上去、發票也標了已收款，卻不會出現請款單，而且沒有任何
    跡象（owner 2026-08-21 回報）。實際卡住 3 張無號的內部代開。
    號碼比對留著當**舊資料的**退路：歷史那 183 張自動單建立時還沒有這個欄位，
    不比號碼的話會重建一張。空號一律不比（兩張都沒號碼不代表是同一張）。
    反向：發票退回「未收款」（收款被刪/改）→ 只刪**自動產生且還是應付款**的
    那張；請款單已付款代表錢真的匯出去了，退狀態不能把付款紀錄變不見。
    """
    if not is_passthrough_category(inv.category):
        return
    if existing is None:        # 批次呼叫端會把預載好的結果帶進來
        existing = (await _passthrough_requests_for(session, [inv])).get(inv.id, [])
    if inv.payment_status in INVOICE_PASSTHROUGH_COLLECTED:
        if existing:
            return
        # commission 是開票時算好存下來的；沒有就用同一條規則現算（費率在 core，
        # 不是寫死的 0.92 —— 外部代開是 10%，寫死會少匯給對方 2%）
        from config import load_settings
        amount = int(inv.commission or 0) or passthrough_commission(
            inv.amount_total, inv.category, load_settings().get("invoice_fee_rates"))
        if amount <= 0:
            return
        d = when or _now()
        now = _now()
        session.add(CrmPaymentRequest(
            id=uuid.uuid4().hex, entity=inv.entity or "parent",
            source_invoice_id=inv.id,
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


async def _kai_invoice_of(session, p, want_status):
    """代開請款單 → 它對應的那張發票（狀態要落在 want_status 裡）。

    先認 source_invoice_id（無號發票唯一有效的鍵），舊資料退回號碼比對。
    want_status 收字串或字串序列 —— 改名期間新舊用詞會並存（待撥款／已收款）。
    """
    from sqlalchemy import func as safunc
    want = (want_status,) if isinstance(want_status, str) else tuple(want_status)
    if p.source_invoice_id:
        inv = await session.get(CrmInvoice, p.source_invoice_id)
        return inv if inv and inv.payment_status in want else None
    no = (p.invoice_number or "").replace("-", "")
    if not no:
        return None
    return (await session.execute(
        select(CrmInvoice).where(
            safunc.replace(CrmInvoice.invoice_number, "-", "") == no,
            CrmInvoice.payment_status.in_(want))
    )).scalars().first()


async def sync_remit_status(session, p, *, previous_invoice_id=None) -> None:
    """代開請款單付掉了沒 → 那張發票的撥款狀態（**單一正本**）。

    代開的生命週期是 未收款 → 待撥款 → 已撥款：客戶把錢匯進公司（發票→待撥款、
    同時自動生一張應付的請款單），公司再把 92%／90% 匯給代開人（請款單→已付款、
    發票→已撥款）。所以「請款單付掉沒」與「發票撥款沒」是同一件事的兩個面。

    🔴 為什麼要收成一份：這條規則本來 inline 寫在 batch_pay 與 batch_unpay 裡，
    而**改請款單付款狀態的路徑不只那兩條** —— resettle_payment_requests（依帳上
    實付重算，收支明細把匯款配到請款單時走這條）也會標已付款／退回應付款，卻
    沒有這一段。從那條路結清一張代開單，請款單會說錢匯出去了、發票卻停在待撥款，
    而兩個畫面各自看起來都很正常。實測生產目前 0 張走過那條路（所以還沒咬到），
    但那是「還沒有人這樣操作」，不是「不會發生」。

    ⚠ 兩個方向都要走。只補正向的話，取消付款時發票會留在已撥款 ——
    待請款區有一張沒付的單，發票卻說錢已經匯了。
    """
    if (p.category or "") != "發票代開":
        return
    if (p.payment_status or "") == "已付款":
        todo = [(await _kai_invoice_of(session, p, INVOICE_PASSTHROUGH_COLLECTED),
                 INVOICE_REMITTED)]
    else:
        todo = [(await _kai_invoice_of(session, p, INVOICE_REMITTED),
                 INVOICE_PENDING_REMIT)]
    # 🔴 改指到別張票時，原本那張要退回 —— 不退的話兩張票會同時說「錢已經匯出去
    #    了」，而只有一筆錢，兩邊畫面各自都很正常。2026-08-24 owner 撞到的就是這個
    #    形狀：已付款那張請款單標錯成台科大，真正匯出去的是古典魔力那筆。
    if previous_invoice_id and previous_invoice_id != p.source_invoice_id:
        prev = await session.get(CrmInvoice, previous_invoice_id)
        todo.append((prev if prev and prev.payment_status == INVOICE_REMITTED else None,
                     INVOICE_PENDING_REMIT))
    for inv, state in todo:          # 指派只有這一處 —— 規則不要再長出第二份
        if inv:
            inv.payment_status = state
            inv.updated_at = _now()


async def resettle_invoices(session, invoice_ids, when=None,
                            unmark_if_empty: bool = False) -> None:
    """N 張發票一次重算收款狀態。

    只把**讀**批次化（一次 _invoice_collections），寫還是走 _resettle_invoice
    那一支 —— 標記/退回/代開待請款同步的規則不能有第二個版本。
    一年份對帳單一次匯進來會有上百張發票，逐張讀就是上百個往返，全部關在同一個
    交易裡（那段時間 crm_invoices 上是有寫鎖的）。
    """
    ids = [i for i in dict.fromkeys(invoice_ids) if i]
    if not ids:
        return
    coll = await _invoice_collections(session, ids)
    # 發票本體一次載進 identity map（底下的 session.get 就變成記憶體命中），
    # 代開請款單也一次撈 —— 這兩樣逐張做的話都是 N 個往返，其中請款單那個
    # 還是 N 次全表掃描
    invs = (await session.execute(
        select(CrmInvoice).where(CrmInvoice.id.in_(ids)))).scalars().all()
    kai = await _passthrough_requests_for(session, invs)
    for iid in ids:
        await _resettle_invoice(
            session, iid,
            # when 可以是單一日期，也可以是 {invoice_id: 日期}（批次時每張不同）
            when=(when.get(iid) if isinstance(when, dict) else when),
            unmark_if_empty=unmark_if_empty, coll=coll.get(iid, {}),
            kai=kai.get(iid))


async def _resettle_invoice(session, invoice_id, when=None,
                            unmark_if_empty: bool = False, coll=None,
                            kai=None) -> None:
    """依**帳上實收**重算一張發票的收款狀態。所有寫入路徑的唯一入口。

    🔴 為什麼不是「碰到就標已收款」：那條舊規則讓分期收款靜默消失。實測
    （2026-08-20，394 張歷史發票）有一張面額 144,900 只收 111,050、尚欠 33,850，
    卻因為標了「已收款」而整張不在應收帳款清單裡（`payment_status NOT IN
    ('已收款','作廢')`），同一畫面的發票列表又照實顯示 outstanding 33,850 ——
    兩個數字互相打架，而贏的是錯的那個。

    另一頭同樣要修：合併匯款用「關聯發票」面板掛三張時，舊碼**完全不動**發票
    狀態，三張全留在應收帳款裡 → 錢收了應收卻沒減。

    收齊與否走 core.finance_logic.amount_is_settled（含匯費容差），與分配面板
    的綠燈判讀同一條規則。

    🔴 `unmark_if_empty`：帳上**一筆收款紀錄都沒有**時，預設什麼都不做。因為
    「查無收款」不等於「沒收到」—— 收支明細從 2024-02-19 才開始，早於它的發票
    是人工標的已收款。實測（2026-08-20）若無條件降級，會有 8 張這種發票被打回
    未收款、憑空生出 2,721,385 的假應收。只有在「我們剛剛親手拿掉最後一筆收款
    憑據」的呼叫點（換發票、刪收支、分配面板移除）才傳 True。
    """
    if not invoice_id:
        return
    inv = await session.get(CrmInvoice, invoice_id)
    if not inv:
        return
    if coll is None:                    # 批次呼叫端會把讀好的結果帶進來
        coll = (await _invoice_collections(session, [invoice_id])).get(invoice_id, {})
    got = coll.get("collected", 0)
    if amount_is_settled(got, inv.amount_total):
        # 代開發票的「已轉撥」是比已收款更後面的階段（錢已匯給代開人）——
        # 收款重算不可以把它降回已收款，那會讓匯出去的紀錄看起來像沒發生。
        if not (inv.payment_status == INVOICE_REMITTED
                and is_passthrough_category(inv.category)):
            _mark_invoice_received(inv, when or coll.get("last_paid_date") or _now())
    elif got or unmark_if_empty:
        # 帳上收了但沒收齊（分期）→ 回到未收款，它才會留在應收帳款裡
        _unmark_invoice_received(inv)
    else:
        return                      # 帳上沒話講 → 不動人工標記
    inv.updated_at = _now()
    await _sync_passthrough_request(session, inv, when=when, existing=kai)


# 兩種分配（收款掛發票／匯款掛請款單）驗的是同樣四件事，差別只有查哪張表
# 與訊息裡的名詞。本來各寫一份，於是同一個錯誤在兩邊回不同的 HTTP 碼
#（跨帳本 409 vs 422）、空 id 一邊擋一邊靜靜丟掉。
# `fee` ＝那一側怎麼認列匯費。兩側方向相反（見 core.finance_logic 兩支的
# docstring）：付款守「總流出不變」從 expense 搬出來、收款守「淨流入不變」往
# deposit 補回去。放在這張表裡，_write_allocs 就只有一條路。
_ALLOC_KINDS = {
    "invoice": {"model": CrmInvoice, "noun": "發票", "id_field": "invoice_id",
                "dup": "同一張發票不可重複掛在同一筆收款",
                "label": lambda o: o.invoice_number or o.title,
                "fee": apply_receipt_fee,
                # 逐張匯費：匯出行對每一張發票的匯款各扣一次。付款側沒有這個
                # （跨行手續費是對「那一筆匯出」收一次，涵蓋幾張請款單都一樣）。
                "per_item_fee": True},
    "payment": {"model": CrmPaymentRequest, "noun": "請款單",
                "id_field": "payment_request_id",
                "dup": "同一張請款單重複出現",
                "label": lambda o: o.summary or o.payee_name,
                "fee": apply_payment_fee},
}
# replace / load / verdict 定義在檔案後段 —— 這裡延後綁定（模組載入完才填），
# 免得為了湊順序把三組相關的函式拆散。
def _bind_alloc_ops():
    _ALLOC_KINDS["invoice"].update(
        replace=replace_invoice_allocs, load=_load_allocs, verdict=_alloc_verdict)
    _ALLOC_KINDS["payment"].update(
        replace=replace_payment_allocs, load=_load_payment_allocs,
        verdict=_payment_verdict)


async def resolve_allocs(session, items, ent, kind, by_id=None):
    """把 [(id, 金額)] 驗成 [(物件, 金額)]。收付兩側、四條寫入路徑共用。

    擋的是**一定錯**而不是可能錯的四件事：id 空／單據不存在／不同帳本／
    金額 ≤ 0（0 元的分配列在分配表裡就是一個看不出用途的鬼列）。
    金額對不對得上入帳金額**不擋** —— 客戶少匯、多匯、匯費都會讓它對不齊，
    那是提示（見 alloc_verdict），硬擋只會逼人亂填。

    一次把單據撈回來，不要逐列 session.get：一份月對帳單掛 25 張就是 25 個往返。
    `by_id` 給整批呼叫端用（自己撈一次，這裡不再撈）。
    """
    k = _ALLOC_KINDS[kind]
    model = k["model"]
    pairs = [((i or "").strip(), int(amt or 0)) for i, amt in items]
    if any(not i for i, _a in pairs):
        raise HTTPException(status_code=422,
                            detail=f"{k['id_field']} 不可為空")
    ids = [i for i, _a in pairs]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail=k["dup"])
    if by_id is None:
        by_id = {o.id: o for o in (await session.execute(
            select(model).where(model.id.in_(ids)))).scalars().all()} if ids else {}

    rows = []
    for i, amt in pairs:
        obj = by_id.get(i)
        if obj is None:
            raise HTTPException(status_code=404, detail=f"{k['noun']}不存在：{i}")
        if (obj.entity or "parent") != ent:
            raise HTTPException(status_code=409,
                                detail=f"收支與{k['noun']}分屬不同帳本")
        if amt <= 0:
            raise HTTPException(
                status_code=422,
                detail=f"分配金額要大於 0（{k['label'](obj) or i}）")
        rows.append((obj, amt))
    return rows


async def resolve_invoice_allocs(session, items, ent, by_id=None):
    return await resolve_allocs(session, items, ent, "invoice", by_id)


async def resolve_payment_allocs(session, items, ent):
    return await resolve_allocs(session, items, ent, "payment")


async def _payment_allocated(session, request_ids):
    """每張請款單**跨所有匯款**已經分配到多少（分次支付要看全貌）。"""
    ids = [i for i in dict.fromkeys(request_ids) if i]
    if not ids:
        return {}
    # 加總交給 DB —— 一張單掛十筆匯款就是十列搬到 Python 再自己加。
    return {pid: int(total or 0) for pid, total in (await session.execute(
        select(CrmCashPaymentLink.payment_request_id,
               func.sum(CrmCashPaymentLink.amount))
        .where(CrmCashPaymentLink.payment_request_id.in_(ids))
        .group_by(CrmCashPaymentLink.payment_request_id))).all()}


async def resettle_payment_requests(session, request_ids, when=None) -> None:
    """依**帳上實付**重算請款單的付款狀態。

    🔴 跟發票那側同一條規則：不是「碰到就標已付款」—— 分次支付會被那條規則
    靜默吃掉。付滿（含 FEE_TOLERANCE 容差）才標已付款；不足退回應付款。
    """
    ids = [i for i in dict.fromkeys(request_ids) if i]
    if not ids:
        return
    paid_map = await _payment_allocated(session, ids)
    # 🔴 先整批載進 identity map，底下就變成記憶體命中 —— 逐張 session.get 的話，
    #    被解除連結的那些（prev）每一張都是一個冷往返（同 resettle_invoices）。
    aps = {a.id: a for a in (await session.execute(
        select(CrmPaymentRequest).where(
            CrmPaymentRequest.id.in_(ids)))).scalars().all()}
    for pid in ids:
        ap = aps.get(pid)
        if ap is None:
            continue
        got = paid_map.get(pid, 0)
        # 結清判準走 core 的唯一正本（本來在這裡 inline 寫了第二份）
        full = amount_is_settled(got, ap.amount)
        if full:
            ap.payment_status = "已付款"
            ap.payment_date = when or ap.payment_date or _now()
        elif got:
            ap.payment_status = "應付款"      # 付了一部分，還沒結清
        else:
            # 連結被拿光了 → 退回未付款，並清掉付款日（否則帳上永遠是已付）
            ap.payment_status = "未付款"
            ap.payment_date = None
        ap.updated_at = _now()
        # 🔴 代開單從這條路結清時，發票也要跟著收尾 —— 少了這一行，請款單會說
        #    錢匯出去了、發票卻停在待撥款（batch_pay 有做、這裡本來沒有）。
        await sync_remit_status(session, ap)


async def replace_payment_allocs(session, entry, rows):
    """整組取代這筆匯款的請款單分配 —— **唯一**寫 crm_cash_payment_links 的地方。

    一次動三樣（少做一樣就會有兩個畫面各講各的）：
      1. 分配表（多對多的正本）
      2. 收支列的 payment_request_id（主要請款單 = 金額最大那張）——
         classify_cash_entry 的硬連結優先序讀它，不同步的話分類會漂
      3. 相關請款單的付款狀態（**被移除的那些也要重算**，否則錢退掉了
         卻永遠掛已付款 —— 發票那側踩過同一個坑）
    """
    from sqlalchemy import delete as sa_delete
    prev = {pid for (pid,) in (await session.execute(
        select(CrmCashPaymentLink.payment_request_id)
        .where(CrmCashPaymentLink.cash_entry_id == entry.id))).all()}
    if prev:
        await session.execute(sa_delete(CrmCashPaymentLink).where(
            CrmCashPaymentLink.cash_entry_id == entry.id))
    w = entry.entry_date or _now()
    for ap, amt in rows:
        session.add(CrmCashPaymentLink(
            id=uuid.uuid4().hex, cash_entry_id=entry.id,
            payment_request_id=ap.id, amount=amt))
    entry.payment_request_id = (max(rows, key=lambda x: x[1])[0].id
                                if rows else None)
    entry.updated_at = _now()
    await session.flush()
    await resettle_payment_requests(
        session, list(prev | {ap.id for ap, _a in rows}), when=w)


async def replace_invoice_allocs(session, entry, rows, when=None, set_primary=True,
                                 fees=None):
    """單筆版 —— 規則正本在 replace_invoice_allocs_bulk（一筆也是一批）。"""
    await replace_invoice_allocs_bulk(session, [(entry, rows)], when=when,
                                      set_primary=set_primary, fees=fees)


async def replace_invoice_allocs_bulk(session, pairs, when=None, set_primary=True,
                                      fees=None):
    """整組取代 N 筆收款的發票分配 —— **唯一**寫 crm_cash_invoice_links 的地方。

    一次動三樣東西，少做一樣就會有兩個畫面各講各的：
      1. 分配表（多對多的正本）
      2. 收支列的 invoice_id / invoice_number / has_invoice（列表與舊查詢讀這幾欄）
      3. 相關發票的收款狀態（🔴 本來完全不動 —— 客戶合併匯款 300,000 掛三張，
         三張都還留在應收帳款裡：錢收了、應收沒減，資產負債表虛增。
         被移除的那些也要重算，否則錢退掉了卻永遠掛已收款。）

    為什麼要有批次版：對帳單匯入是一列一筆收款，逐筆呼叫等於每列 4 個往返
    （查舊連結、刪、flush、重算收款）。一年份對帳單約 150 列 = 600 個往返，
    而且整段關在同一個寫交易裡壓著 crm_invoices 的鎖。而那些收支列是這次**剛
    建出來的**，舊連結必定是空的 —— 那 2 個往返純粹是白跑。

    🔴 同一筆收款不可以在 pairs 裡出現兩次：DELETE 只跑一輪，後面幾組連結會
    **疊加**而不是後蓋前（逐筆呼叫是後蓋前），主要發票欄也會跟連結講不同的話。
    當場擋掉，不要留給日後某個呼叫端去踩。

    rows 已經過 resolve_invoice_allocs 驗證。呼叫端負責 commit。

    `fees` ＝ {(收支 id, 發票 id): 匯費}（只有收款側有；付款側的手續費是整筆
    匯出收一次）。key 要帶收支 id —— 同一張發票分兩次收款、兩次各被扣一次匯費時，
    只用 invoice_id 當 key 會互相蓋掉。
    🔴 逐張存下來是為了**關聯面板重開時畫得出來** —— 沒存的話那格永遠是空的，
       使用者按一下儲存就送 fee=0，deposit 退回去、bank_fee 被清掉，而且完全
       看不出來（2026-08-24 實測確認過這個回退）。
       entries.bank_fee 仍是加總（報表讀它），這裡存的是歸屬。
    """
    pairs = list(pairs)
    if not pairs:
        return
    from sqlalchemy import delete as sa_delete
    eids = [e.id for e, _r in pairs]
    if len(set(eids)) != len(eids):
        raise HTTPException(status_code=500,
                            detail="同一筆收款不可重複進同一批發票分配")

    prev: dict = {}
    for eid, iid in (await session.execute(
            select(CrmCashInvoiceLink.cash_entry_id, CrmCashInvoiceLink.invoice_id)
            .where(CrmCashInvoiceLink.cash_entry_id.in_(eids)))).all():
        prev.setdefault(eid, set()).add(iid)
    if prev:            # 全新的收支列（對帳單匯入）一筆舊連結都沒有 → 整個跳過
        await session.execute(sa_delete(CrmCashInvoiceLink).where(
            CrmCashInvoiceLink.cash_entry_id.in_(list(prev))))

    kept_when: dict = {}
    for entry, rows in pairs:
        w = when or entry.entry_date or _now()
        for inv, amt in rows:
            session.add(CrmCashInvoiceLink(
                id=uuid.uuid4().hex, cash_entry_id=entry.id,
                invoice_id=inv.id, amount=amt,
                fee=int((fees or {}).get((entry.id, inv.id)) or 0)))
            cur = kept_when.get(inv.id)
            # 同一張發票被這批的兩列分次收款 → 到款日取**最後**那筆（收齊的那天）。
            # 逐筆呼叫時是「第 1 列先算一次（沒收齊）、第 2 列再算一次（收齊）」，
            # 批次一次看到全部，日期就得自己挑對的那個。
            kept_when[inv.id] = w if (cur is None or w > cur) else cur
        # 主要發票 = 分配金額最大的那張（沒有就清空）。
        # set_primary=False 給編輯視窗那條路用：那邊的主要發票是**輸入**不是輸出，
        # 而且收支列被改成支出時不可以順手把 invoice_id 清掉（代開付出去那側要留著）。
        if set_primary:
            _set_primary_invoice(entry, max(rows, key=lambda x: x[1])[0] if rows else None)
    await session.flush()

    await resettle_invoices(session, list(kept_when), when=kept_when)
    # 被移除的：憑據是我們剛拿掉的
    dropped = (set().union(*prev.values()) - set(kept_when)) if prev else set()
    await resettle_invoices(session, dropped, unmark_if_empty=True)


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
        d.update(collection_fields(r[0].amount_total, c))
        out.append(d)
    return {"invoices": out, "total": len(out)}


@router.post("/invoices")
async def create_invoice(req: InvoicePayload, request: Request):
    _check_auth(request)
    _require_db()
    ent = _entity_for_write(request, req.entity)
    factory = await _get_factory()
    now = _now()
    data = req.model_dump(exclude={"invoice_date", "entity"})
    # 款項狀態在**入口**定案，不留給客戶端：
    #   · 舊客戶端可能送改名前的字（已轉撥）→ 正規化
    #   · 沒送或送空字串 → 依方向推（收款＝未收款、代開＝未付款）
    # 🔴 空字串不會觸發欄位的 default —— SQLAlchemy 的 default 只在「這個 key
    # 根本沒出現」時才跑。手機登記頁改成不自己決定狀態之後，送的是 ''，於是
    # 每一張從手機開的發票都存了一個空白狀態：沒有 badge，也不在
    # `payment_status NOT IN (...)` 這類過濾的任何一邊（2026-08-21 實測）。
    data["payment_status"] = (
        normalize_invoice_status(data.get("payment_status") or "")
        or initial_invoice_status(data.get("payment_type") or ""))
    # 方向也由狀態推 —— 前端本來自己判，而且漏掉「待撥款」（那也是付款方向），
    # 於是待撥款的代開發票會被當成收款、跑進應收帳款
    if not (data.get("payment_type") or "").strip():
        data["payment_type"] = invoice_direction(data["payment_status"])
    # 開立狀態同理：有發票號碼才叫已開立。規則本來只在發票本的前端（而且抄三份），
    # 專案頁那個入口沒有 → 從那裡開一張還沒拿到號碼的票會吃到預設值「已開立」。
    data["issue_status"] = issue_status_for(data.get("invoice_number"),
                                            data.get("issue_status"))
    inv = CrmInvoice(
        id=uuid.uuid4().hex, invoice_date=_parse_shoot_date(req.invoice_date),
        created_at=now, updated_at=now, entity=ent, **data,
    )
    async with factory() as session:
        await _assert_month_open(session, inv.invoice_date, entity=ent)
        session.add(inv)
        await session.commit()
        await session.refresh(inv)
    return {"status": "ok", "invoice": _to_invoice_dict(inv)}


def collection_fields(amount_total, coll: dict | None = None,
                      with_detail: bool = False) -> dict:
    """發票 payload 的收款欄位：collected / outstanding / **settled** / last_paid_date。

    🔴 `settled` 是關鍵那一欄。以前只回 collected 與 outstanding，於是前端三個地方
    各自用 `outstanding > 0` / `<= 0` 判「收齊了沒」—— 但真正的規則是
    amount_is_settled（含 NT$50 匯費容差，394 張歷史發票裡有 42 張靠它）。
    結果同一張被匯費短收 30 元的發票：應收帳款說收齊了，發票列表說「尚欠 $30」。
    容差是後端的事，不該複製到瀏覽器 —— 後端算好一個布林送過去。
    """
    c = coll or {}
    got = int(c.get("collected", 0) or 0)
    total = int(amount_total or 0)
    d = {"collected": got, "outstanding": total - got,
         "settled": amount_is_settled(got, total),
         "last_paid_date": c.get("last_paid_date")}
    if with_detail:
        d["payments"] = c.get("payments", [])
    return d


async def _invoice_collections(session, invoice_ids):
    """發票 id → 實際收到多少（＋每一筆的日期金額）。

    來源是 crm_cash_invoice_links（收款↔發票分配表）JOIN 收支明細 —— 也就是
    **帳上真的進了多少錢**，不是有人手動勾的狀態欄。一張發票分三次到款時，
    這裡會回三筆，加總就是已收金額，跟面額一減就知道還欠多少。

    逐筆 `payments` 一律附上；要不要送到前端由 collection_fields 的 with_detail
    決定（本來這裡也有一個同名旗標，把 payments 建好又 pop 掉，兩個閘門永遠同值）。
    原本清單模式另走一條 GROUP BY，等於「已收金額」有兩個算法可以各自出錯 ——
    實測（303 筆分配、394 張發票）兩者 6.4ms vs 6.3ms，那條捷徑什麼也沒買到。
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
        coll = (await _invoice_collections(session, [inv.id])).get(inv.id)
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
    d.update(collection_fields(inv.amount_total, coll, with_detail=True))
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
        upd = req.model_dump(exclude={"invoice_date", "entity"})
        # 同上：舊字正規化、空的就依方向推（PUT 會整包寫回，空字串照樣會寫進去）
        upd["payment_status"] = (
            normalize_invoice_status(upd.get("payment_status") or "")
            or initial_invoice_status(upd.get("payment_type") or ""))
        # 同 create：號碼被補上/清掉時，開立狀態要跟著走（作廢維持作廢）
        upd["issue_status"] = issue_status_for(upd.get("invoice_number"),
                                               upd.get("issue_status"))
        for k, v in upd.items():
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


async def _import_money_csv(request: Request, file: UploadFile, *,
                            map_row, skip_if, date_fields: tuple, build) -> dict:
    """帳務三支 CSV 匯入的共用骨架（發票／請款／收支）。唯一正本。

    這裡收的不是「省幾行」，是四條規則本來各抄三份：
      · utf-8-sig → big5 的解碼退路（同事的 Excel 匯出兩種都有）
      · 列號從 2 起算（第 1 列是表頭）—— 錯誤訊息要指得到人看得懂的那一列
      · 兩本帳：CSV 匯入 v1 限母公司帳（entity='parent'），我的帳不走匯入
      · **F1 月結守衛**：先 parse 全部、逐列判月，任一列落鎖定月 → 整批 409，
        不做半套。第四支帳務 CSV 一定會漏抄其中一半，而漏掉守衛＝把一個
        已經結完帳的月份重新打開。

    date_fields[0] 是月結守衛看的那一欄（權責認列日），其餘只是要 parse 的日期。
    build(data, dates) 回一個 ORM 物件 —— 三張表的欄位不同，那部分不共用。
    """
    _check_auth(request)
    _require_db()
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("big5", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    header_map = {h.lower(): h for h in (reader.fieldnames or [])}
    guard_field = date_fields[0]
    imported = skipped = 0
    factory = await _get_factory()

    async with factory() as session:
        parsed, dated = [], []
        for line_no, row in enumerate(reader, start=2):   # 第 1 列是表頭
            data = map_row(header_map, row)
            if skip_if(data):
                skipped += 1
                continue
            dates = {f: _parse_shoot_date(data.pop(f, None)) for f in date_fields}
            if dates[guard_field]:
                dated.append((f"第 {line_no} 列"
                              f"（{dates[guard_field].strftime('%Y-%m-%d')}）",
                              dates[guard_field]))
            parsed.append((data, dates))
        await _assert_rows_open(session, dated, entity="parent")
        for data, dates in parsed:
            session.add(build(data, dates))
            imported += 1
        await session.commit()
    return {"status": "ok", "imported": imported, "skipped": skipped}


def _map_invoice_row(header_map: dict, row: dict) -> dict:
    data = map_csv_row(
        _INVOICE_COL_MAP, header_map, row,
        coerce=lambda f, v: _parse_money(v) if f in _INVOICE_INT_FIELDS else v)
    # Sheet 的「款項狀態」一欄同時是方向（收/付）與狀態（已/未）→ 拆成兩個欄位。
    # 🔴「未收款」裡也有一個「收」字：只判 '收' in pt 會把未收的發票標成**已收款**，
    #    應收帳款憑空消失、收入被提前認列。2026-08-19 匯 394 筆歷史發票時實測到
    #    44 張未收款發票中招。判「已/未」必須先於或同時於判「收/付」。
    # 統編補回前導 0 —— 這條路 CrmInvoice(**data) 直接建、繞過 InvoicePayload
    # 的驗證器（規則同一支 core.crm_logic.normalize_tax_id）
    if "tax_id" in data:
        data["tax_id"] = normalize_tax_id(data["tax_id"])
    pt = data.get("payment_type", "")
    unpaid = "未" in pt
    if "收" in pt:
        data["payment_type"] = "收款"
    elif "付" in pt:
        data["payment_type"] = "付款"
    if "收" in pt or "付" in pt or "作廢" in pt:
        data["payment_status"] = initial_invoice_status(pt, unpaid)
    return data


@router.post("/invoices/import_csv")
async def import_invoices_csv(request: Request, file: UploadFile = File(...)):
    def build(data, d):
        now = _now()
        return CrmInvoice(id=uuid.uuid4().hex, invoice_date=d["invoice_date"],
                          entity="parent", created_at=now, updated_at=now, **data)
    return await _import_money_csv(
        request, file, map_row=_map_invoice_row,
        skip_if=lambda d: not d.get("title"),
        date_fields=("invoice_date",), build=build)


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


# ── Cash Entry (收支明細) ───────────────────────────────────

def _set_primary_invoice(e, inv):
    """設（或清除）收支的「主要發票」三欄。

    這三欄是刻意的反正規化 —— 列表與舊查詢都讀它們，所以必須一起動。
    分開寫在兩個端點裡的話，第三條寫入路徑會照抄先看到的那一份。
    """
    e.invoice_id = inv.id if inv else None
    e.invoice_number = (inv.invoice_number or "") if inv else None
    e.has_invoice = 1 if inv else 0


def _to_cash_dict(e, project_name: str = "", invoice_title: str = "",
                  petty_status: str = "", tax_path=None) -> dict:
    return {
        "id": e.id,
        "entity": e.entity or "parent",
        "entry_date": e.entry_date.isoformat() if e.entry_date else None,
        "expense": e.expense, "claim": e.claim, "deposit": e.deposit,
        "summary": e.summary or "", "note": e.note or "",
        "bank_memo": getattr(e, "bank_memo", "") or "",
        "category": e.category or "",
        # 三層（book/item 由複合鍵拆出；item 欄有值時以欄為準 —— 匯入時填的
        # 比拆字串可靠）。sub_item 是第三層。
        "book": _tax_split(e.category)[0],
        "item": (e.item or "") or _tax_split(e.category)[1],
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
        # 零用金推送狀態（routers/crm/petty.petty_from_cash）。expense_id 指向
        # 已刪的單據時 petty_status 會是空的 —— 那就是「沒推過」，跟撤銷後一樣。
        "expense_id": getattr(e, 'expense_id', '') or "",
        "petty_status": petty_status or "",
        # 分類樹：節點 id ＋ **完整**路徑。上面的 book/item/sub_item 只是前三層的
        # 鏡射，第四層以後（家用▸變動支出▸醫療保健▸乳癌治療▸台北馬偕）只有這裡看得到。
        "taxonomy_node_id": getattr(e, 'taxonomy_node_id', '') or "",
        "taxonomy_path": list(tax_path or []),
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


async def _sync_taxonomy(session, e, data: dict, paths: dict | None = None):
    """`taxonomy_node_id` ↔ `category`/`item`/`sub_item` 保持一致 —— **規則只有這一份**。

    兩個方向都要走，因為寫入端不只一個：
    - 前端挑了分類樹的節點 → 送 `taxonomy_node_id`，**節點是正本**，三欄由
      `mirror_from_path` 推導（前端送什麼 category 都不算數 —— 兩邊各算一次
      必漂，而且漂的是會計對映的鍵）。
    - CSV／銀行對帳單匯入、對帳腳本走舊路，只有 category/sub_item →
      反查掛上節點。不掛的話那些列要等下次 boot 的回填才進得了樹狀篩選。

    🔴 `taxonomy_node_id` 明確送空 → 連三欄一起清掉。只清節點會留下一列
    「有類別、但不在樹上」的孤兒，樹狀篩選看不到它。
    """
    from core.cash_taxonomy import mirror_from_path, path_from_columns
    from core.cash_tree import find_node_id, node_id_in, path_map

    ent = e.entity or "parent"
    if "taxonomy_node_id" in data:
        nid = (data.get("taxonomy_node_id") or "").strip()
        if not nid:
            e.taxonomy_node_id = None
            e.category = e.item = e.sub_item = ""
            return
        # `paths` ＝呼叫端已經算好的 id→路徑表（批次分類一次 200 列，各撈一次
        # 樹就是 200 趟）。沒帶就自己撈 —— 單筆更新那條路不必為此改。
        path = (paths if paths is not None else await path_map(session, ent)).get(nid)
        if not path:
            raise HTTPException(status_code=400, detail="找不到這個分類節點")
        e.taxonomy_node_id = nid
        e.category, e.item, e.sub_item = mirror_from_path(path)
        return
    if not ({"category", "item", "sub_item"} & set(data)):
        return                                  # 這次沒動分類，不必重算
    # 送什麼就先落到列上，**沒送的欄一律不動**。
    #
    # 🔴 這裡不推導 `item`。它看起來像 category 第二層的鏡射，但那只在私帳成立
    # （私帳的 category 是 `書_項目` 複合鍵）。母公司那本的 category 全是平的，
    # `item` 放的是**獨立資料**：零用金放那張 AP 的類別、福委會放 AP_CATEGORY、
    # CSV 匯入吃「項目」欄。拿 `item_of(category)`（平類別＝空字串）去覆蓋，等於
    # 每次從編輯視窗按存檔就把那一欄清空 —— 而畫面上不會有任何提示。
    for k in ("category", "item", "sub_item"):
        if k in data:
            setattr(e, k, data.get(k) or "")
    # `paths` 給了就在記憶體裡反查（`find_node_id` 是逐層各一次 SELECT，批次那
    # 條路一次跑幾百列就是上千趟）。兩種查法都住在 core/cash_tree，路徑怎麼算
    # 也只有 `path_from_columns` 一份 —— 這裡沒有第二份規則。
    want = [x for x in path_from_columns(e.category, e.sub_item) if x]
    # 🔴 本來就掛在**第 4 層以後**、而三欄描述的前三層沒變 → 保留原本的節點。
    #
    # 三欄只裝得下 3 層（`mirror_from_path` 的規則：第 4 層以後刻意不進那三欄，
    # 深度活在 `taxonomy_node_id` 裡）。拿三欄回頭反查一定只找得到第 3 層那個，
    # 所以不擋的話，使用者只是來改別的欄位按一次存檔，分類就被降級了 ——
    # 收支明細的編輯視窗每次都會送 category＋sub_item，等於每存一次降一次。
    # 實測：`家用▸變動支出▸醫療保健▸乳癌治療▸台北馬偕` 會掉成
    # `家用▸變動支出▸醫療保健`，而私帳有 60 筆掛在那麼深。
    #
    # 前三層真的被改掉時（`cur[:3] != want`）就不保留 —— 那是使用者確實換了分類。
    if e.taxonomy_node_id and len(want) == 3:
        tbl = paths if paths is not None else await path_map(session, ent)
        cur = tbl.get(e.taxonomy_node_id)
        if cur and len(cur) > 3 and cur[:3] == want:
            return
    nid = ""
    if want:
        nid = (node_id_in(paths, want) if paths is not None
               else await find_node_id(session, ent, want))
    e.taxonomy_node_id = nid or None
    # 🔴 這條路**只寫節點**，三欄維持呼叫端送進來的。想從路徑反推三欄的話要用
    # `mirror_from_path`，但它跟 `path_from_columns` 不是互為反向：平的類別配上
    # 子項目時（母公司的類別全是平的），`path_from_columns('其他','保險費')` 出
    # `['其他','保險費']`，mirror 讀位置 1 當項目 → `('其他_保險費','保險費','')`
    # —— 類別被改寫成一個 finance_category_map 裡沒有的複合鍵（那一列於是掉進
    # 三表的「未歸類」），子項目還不見了。生產有這種列。


async def _assert_project_same_entity(session, e):
    """🔴 不變式：收支只能掛**同一本帳**的專案。

    下拉帶了 entity 之後這條路正常走不到，但守衛不能靠 UI —— 跨帳本的組合會讓
    那筆錢在兩本帳的「掛帳支出」裡**都不出現**（api_finance_projects._rollups
    按 entity 篩收支、再按 entity 迭代專案，兩者對不上就靜靜消失），比多算一筆
    更難發現。比照 api_finance_projects._owned_project 回 403。
    """
    if not e.project_id:
        return
    from db.models import CrmProject
    p = await session.get(CrmProject, e.project_id)
    if p and (p.entity or "parent") != (e.entity or "parent"):
        raise HTTPException(
            status_code=403,
            detail="不能把收支掛到另一本帳的專案上")


async def _get_mine_project(session, project_id):
    """兩支 _sync_mine_* 的共用前導：載入專案、**只認 entity='mine'**。

    🔴 mine-only 是這兩條規則安全的那一半（母公司的已收走發票/收款既有流程，
    再疊一條會雙重驅動）—— 集中在這裡，第三條 sync 規則就不會漏抄。
    """
    if not project_id:
        return None
    from db.models import CrmProject
    p = await session.get(CrmProject, project_id)
    if not p or (p.entity or "parent") != "mine":
        return None
    return p


async def _sync_mine_project_received(session, project_id, delta: int):
    """私帳收款規則（owner 2026-08-25「勾選專案，如果金額到齊，就是收款」）：
    掛在專案上的**收入**驅動該案的 已收/應收/收款狀態。

    🔴 增量制（±delta），不是「從掛帳收入整個重算」—— 歷史已收是匯入的基準值
    （並非每一筆歷史收款都有對應的掛帳收支列），重算會把老案的已收洗掉。
    新增 +deposit、刪除 −deposit、編輯＝先減舊再加新，帳永遠平。

    🔴 只管 entity='mine'（_get_mine_project）。到齊（已收 ≥ 營收且營收 > 0）
    ＝全額到帳；部分＝部分到帳；歸零＝未到帳。應收 = 營收 − 已收（可為負：
    溢收要看得見，Sheet 的泛亞 −3,000 就是這種）。
    """
    if not delta:
        return
    p = await _get_mine_project(session, project_id)
    if not p:
        return
    from core.ledger_project import norm_detail, receivable_fields
    p.amount_received = int(p.amount_received or 0) + int(delta)
    # 應收與收款狀態的算式正本在 core（四個寫入端共用；基準＝實際會進帳的錢，
    # 不是營收 —— 見 receivable_fields）
    p.amount_receivable, p.payment_status = receivable_fields(
        int(p.contract_amount or 0), p.amount_received, norm_detail(p.ledger_detail))
    p.updated_at = _now()


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


def _mine_or_admin_write(request, target_entity):
    """CRM 帳務的寫入守衛：Lv3 照舊全放行；**私帳的列**另開給 mine full scope。

    🔴 為什麼要開：owner 的帳號是 lv1＋finance_mine（指名制，刻意不是管理員），
    但這批端點原本 `_check_auth`＝只限 Lv3 —— 帳本主人在生產上**連一筆帳都記
    不進去**（2026-08-25 用真實帳號形狀實測 403；先前測試全用 Lv3 token 所以
    沒炸）。記自己的帳不需要是管理員。
    🔴 母公司路徑維持 Lv3-only：parent full scope（crm_invoices+money_view）
    是財務同事的「看」，寫仍是管理員 —— 這裡只開私帳，不動母公司的權限面。
    """
    if (target_entity or "parent") == "mine":
        require_entity(request, "mine", level="full")
    else:
        _check_auth(request)


def _mine_or_admin_write_rows(request, rows):
    """批次版：全部是私帳列 → mine full；混到任何母公司列 → Lv3。"""
    ents = {(getattr(r, "entity", None) or "parent") for r in rows}
    _mine_or_admin_write(request, "mine" if ents == {"mine"} else "parent")


def _enforce_cash_project_link(e):
    """🔴 不變式：只有專案類的收支可以掛專案（core/project_link.CASH_CATEGORIES）。

    在**賦值之後**檢查最終狀態 —— 賦值前預測會漏掉「只改 category、不動 project_id」
    那條路（零用金那邊踩過同樣的洞，見 petty._enforce_project_link 的說明）。
    行政／薪資／房租那種公司層級支出掛到專案上，專案毛利就會多算一筆不屬於它的錢。
    """
    from core.project_link import cash_can_link
    _ent = getattr(e, "entity", None)      # 測試替身可能沒有這個欄；預設＝母公司規則
    if e.project_id and not cash_can_link(_ent, e.category):
        if (_ent or "parent") == "mine":
            raise HTTPException(
                status_code=409,
                detail=f"「{e.category or '未分類'}」不能掛專案 —— 私帳只有"
                       "「公司」開頭的類別（公司_專案／公司_專案支出…）能掛，"
                       "個人與家用掛上去會污染專案毛利")
        raise HTTPException(
            status_code=409,
            detail=f"「{e.category or '未分類'}」的收支不能連結專案 —— "
                   f"可連結的類別：{'、'.join(_PROJECT_LINK_CATEGORIES)}")


@router.get("/cash-entries/options", dependencies=[Depends(money_dep)])
async def cash_entry_options(request: Request, entity: str = Query("")):
    """收支明細的下拉選項來源（比照 /petty/options）。

    類別清單與「哪些類別可連結專案」都由後端說了算 —— 前端寫死的下拉會跟
    finance_category_map 脫節：`貸款繳款`／`貸款補貼`／`銀行借款` 這些後來加的
    類別就沒同步進去，結果對帳單匯入自己寫出來的列，使用者在編輯視窗選不到
    它的類別（實測種子 32 個、前端只有 27 個）。
    """
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()
    from core.cash_taxonomy import book_of, taxonomy
    async with factory() as session:
        # 🔴 **這本帳**的值域 —— 不是整份 finance_category_map。那份是兩本共用的，
        # 私帳拿到的會是母公司的平面科目（行政／薪資／交際應酬…），而私帳的報表
        # 根本沒有那些類別：選了存進去就靜靜落到「未歸類」，看起來卻像分好了
        # （owner 2026-08-29「這裡的分類規則不需要和 crm 共用」）。母公司那半仍是
        # 同一份平面科目，所以母公司這條路一個字都沒變。
        cats, tree = await ledger_categories_and_tree(session, ent)
        used = list((await session.execute(
            select(CrmCashEntry.category).where(
                CrmCashEntry.entity == ent,
                CrmCashEntry.category.isnot(None),
                CrmCashEntry.category != "").distinct())).scalars())
        subs = list((await session.execute(
            select(CrmCashEntry.sub_item).where(
                CrmCashEntry.entity == ent,
                CrmCashEntry.sub_item.isnot(None),
                CrmCashEntry.sub_item != "").distinct())).scalars())
    from core.project_link import MINE_CASH_LINK_PREFIX
    linkable = ([c for c in cats if c.startswith(MINE_CASH_LINK_PREFIX)]
                if ent == "mine" else list(_PROJECT_LINK_CATEGORIES))
    # 舊的 taxonomy 扁平欄位先留一版：前端已全面改吃 tree，但發版當下還開著的
    # 舊分頁仍讀它；下一版可以連同 core.cash_taxonomy.taxonomy() 一起收掉。
    # 它的值域＝**這本帳實際用到的類別** ∪ 值域裡同一本底下的（還沒用過的新
    # 類別也要挑得到）。
    books_used = {book_of(c) for c in used}
    tax_cats = used + [c for c in cats if book_of(c) in books_used and c not in used]
    return {"categories": cats,
            "project_link_categories": linkable,
            "taxonomy": taxonomy(tax_cats, subs),
            "tree": tree}


@router.get("/cash-entries", dependencies=[Depends(money_dep)])
async def list_cash_entries(
    request: Request,
    q: str = Query(""), category: str = Query(""),
    project_id: str = Query(""),
    bank_account_id: str = Query(""), direction: str = Query(""),
    date_from: str = Query(""), date_to: str = Query(""),
    sub_item: str = Query(""), book: str = Query(""), item: str = Query(""),
    node_id: str = Query(""),
    status: str = Query(""),
    amount_min: str = Query(""), amount_max: str = Query(""),
    entity: str = Query(""),
):
    """direction：'in'＝只看有收入的、'out'＝只看有支出的、空＝全部。
    date_from/date_to＝含當日；amount_min/max 比的是該列**金額量級**
    （收入或 支出＋匯費，取大者）—— owner 2026-08-26「可以篩選日期區間、
    分類、子項目、金額」。"""
    # 兩本帳：money_dep 之上疊第二層 entity scope（plan §2.4）
    # full：CRM 帳務＝原始帳列，合夥人不可及 —— money_dep 已擋一層，刻意雙保險
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()
    from core.cash_tree import load_nodes, path_map, subtree_ids
    async with factory() as session:
        # 分類樹一請求只撈一次（篩選的子樹與每列的路徑共用同一份；含停用 ——
        # 歷史列掛在停用節點上路徑照樣要印得出來）
        tax_nodes = await load_nodes(session, ent, include_inactive=True)
        query = (
            select(CrmCashEntry, CrmProject.name.label("pn"),
                   CrmInvoice.title.label("inv_title"),
                   CrmProjectExpense.status.label("petty_status"))
            .outerjoin(CrmProject, CrmProject.id == CrmCashEntry.project_id)
            .outerjoin(CrmInvoice, CrmInvoice.id == CrmCashEntry.invoice_id)
            # 推去零用金的那幾列要在清單上看得出狀態（PK join，成本可忽略）
            .outerjoin(CrmProjectExpense,
                       CrmProjectExpense.id == CrmCashEntry.expense_id)
            .where(CrmCashEntry.entity == ent)
            .order_by(CrmCashEntry.entry_date.desc())
        )
        if category == "__none__":
            # 「未分類」快篩：卡單匯入後真的分不出的尾巴（owner 逐筆點完就歸零）
            query = query.where(or_(CrmCashEntry.category.is_(None),
                                    CrmCashEntry.category == ""))
        elif category:
            query = query.where(CrmCashEntry.category == category)
        if sub_item:
            query = query.where(CrmCashEntry.sub_item == sub_item)
        if status:
            # 卡片明細＝status='card'（owner 2026-08-27「切這個按鈕就切換成
            # 信用卡的明細」）；其餘 status 值同義比對
            query = query.where(CrmCashEntry.status == status)
        # 三層分類的前兩層：儲存是複合鍵，篩選在鍵上做前綴/後綴比對
        # （規則正本 core.cash_taxonomy —— 別在這裡自己拼字串）
        # 分類樹：選到哪一層就看**那一支整支**（選「家用」＝家用底下全部）。
        # 這條蓋過上面 book/item/sub_item 三個舊參數 —— 前端切過去之後只送這個。
        if node_id:
            query = query.where(CrmCashEntry.taxonomy_node_id.in_(
                await subtree_ids(session, ent, node_id, nodes=tax_nodes)))
        if book:
            query = query.where(or_(CrmCashEntry.category == book,
                                    CrmCashEntry.category.like(book + _TAX_SEP + "%")))
        if item:
            query = query.where(or_(CrmCashEntry.item == item,
                                    CrmCashEntry.category.like("%" + _TAX_SEP + item)))
        if date_from:
            d = _parse_shoot_date(date_from)
            if d:
                query = query.where(CrmCashEntry.entry_date >= d)
        if date_to:
            d = _parse_shoot_date(date_to)
            if d:
                from datetime import timedelta
                query = query.where(CrmCashEntry.entry_date < d + timedelta(days=1))
        if amount_min or amount_max:
            from sqlalchemy import func as _fn
            _amt = _fn.greatest(
                _fn.coalesce(CrmCashEntry.deposit, 0),
                _fn.coalesce(CrmCashEntry.expense, 0) + _fn.coalesce(CrmCashEntry.bank_fee, 0))
            if (amount_min or "").strip().lstrip("-").isdigit():
                query = query.where(_amt >= int(amount_min))
            if (amount_max or "").strip().lstrip("-").isdigit():
                query = query.where(_amt <= int(amount_max))
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
                                    CrmCashEntry.sub_item.ilike(ql),
                                    CrmCashEntry.payee.ilike(ql),
                                    CrmCashEntry.note.ilike(ql),
                                    CrmCashEntry.bank_memo.ilike(ql),
                                    CrmCashEntry.invoice_number.ilike(ql)))
        rows = (await session.execute(query)).all()
        # 路徑一次撈完（幾百個節點一張 dict），不逐列往上爬
        paths = await path_map(session, ent, nodes=tax_nodes)
    return {"entries": [_to_cash_dict(r[0], r[1] or "", r[2] or "", r[3] or "",
                                      paths.get(r[0].taxonomy_node_id))
                        for r in rows], "total": len(rows)}


@router.post("/cash-entries")
async def create_cash_entry(req: CashEntryPayload, request: Request):
    _require_db()
    # summary 在 schema 是選填（PUT 要能只送幾個欄位做部分更新），建立時必填由這裡驗
    if not (req.summary or "").strip():
        raise HTTPException(status_code=422, detail="內容（摘要）必填")
    ent = _entity_for_write(request, req.entity)
    factory = await _get_factory()
    date_fields = {"entry_date", "payment_date"}
    dates = {f: _parse_shoot_date(getattr(req, f)) for f in date_fields}
    data = req.model_dump(exclude=date_fields | {"entity"})
    # 🔴 分類同步要看「前端**真的送了**哪些欄」，不能看 data —— 建立時 data 是
    # 整包（每個欄位都在），照它判會把「只送了 category」的建立當成「明確送了
    # 空的 taxonomy_node_id」，分類當場被清掉。
    sent = req.model_dump(exclude_unset=True)
    e = CrmCashEntry(id=uuid.uuid4().hex, **dates, entity=ent, created_at=_now(), **data)
    _normalize_cash_fks(e)
    async with factory() as session:
        await _assert_month_open(session, dates.get("entry_date"), entity=ent)
        # 分類樹 → 三欄鏡射要跑在可掛專案判定**之前**（判定吃的是 category）
        await _sync_taxonomy(session, e, sent)
        _enforce_cash_project_link(e)
        await _assert_project_same_entity(session, e)
        if ent == "mine":
            await _sync_mine_project_received(session, e.project_id,
                                              int(e.deposit or 0))
        session.add(e)
        await session.flush()      # 讓 _resettle_invoice 的查詢看得到這筆
        # 🔴 建立也要進分配表，不能只有更新路徑做（2026-08-20 實測：新開一筆掛了
        # 發票的收款，列表那欄看得到，但「關聯發票」面板是空的、發票的已收金額
        # 停在 0 —— 因為那兩處讀的是 crm_cash_invoice_links 不是 invoice_id。
        # 要按一次編輯再存檔才會補上，等於帳目正確與否取決於有沒有人多按一下。）
        await _sync_single_alloc(session, e)   # 收款狀態也由它一起收尾
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
    # deposit 被清成 0（改成支出列）→ 空分配：這筆錢已經不是收款了，
    # 留著舊分配列會讓那張發票永遠算已收（實測會留孤兒）。
    ent = e.entity or "parent"
    rows = (await resolve_invoice_allocs(session, [(e.invoice_id, int(e.deposit or 0))], ent)
            if e.invoice_id and (e.deposit or 0) else [])
    # set_primary=False：這條路的主要發票是使用者在表單挑的，不是從分配表推的
    await replace_invoice_allocs(session, e, rows, when=e.entry_date,
                                 set_primary=False)


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
    check_logged_in(request)           # 先擋匿名（不給 401/404 當存在性預言機）
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
        # （寫入守衛也在 _entity_for_write 裡定案）
        ent = _entity_for_write(request, req.entity, e)
        await _assert_month_open(session, e.entry_date, dates.get("entry_date"),
                                 entity=ent)
        # 私帳收款同步要用「改之前」的 (收入, 專案) 當減項 —— setattr 之後就沒了
        _old_dep, _old_pid = int(e.deposit or 0), e.project_id
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
        if "invoice_id" in data:
            _set_primary_invoice(e, await session.get(CrmInvoice, e.invoice_id)
                                 if e.invoice_id else None)
        if "invoice_id" in data or "deposit" in data:
            # 分配表、主要發票欄、相關發票的收款狀態一次到位；換掉的舊發票也會
            # 被重算（共用寫入者比對分配表的前後差異），不用在這裡再比一次
            await _sync_single_alloc(session, e)
        await _sync_taxonomy(session, e, data)      # 同上：要在可掛專案判定之前
        _normalize_cash_fks(e)
        _enforce_cash_project_link(e)
        await _assert_project_same_entity(session, e)
        if ent == "mine" and ("deposit" in data or "project_id" in data):
            new_dep, new_pid = int(e.deposit or 0), e.project_id
            if (_old_dep, _old_pid) != (new_dep, new_pid):
                await _sync_mine_project_received(session, _old_pid, -_old_dep)
                await _sync_mine_project_received(session, new_pid, new_dep)
        await session.commit()
        # 分類是後端從節點路徑推導的（_sync_taxonomy）—— 把推導結果回給前端，
        # 它就地 patch 那一列就好，不必為了拿三欄鏡射把 4,700 列整表重載。
        # 只在這次真的動到分類時才多算（path_map 是一次 237 列的撈取）。
        entry_patch = {}
        if {"taxonomy_node_id", "category", "item", "sub_item"} & set(data):
            from core.cash_tree import path_map
            paths = await path_map(session, e.entity or "parent")
            entry_patch = {
                "category": e.category or "",
                "book": _tax_split(e.category)[0],
                "item": (e.item or "") or _tax_split(e.category)[1],
                "sub_item": e.sub_item or "",
                "taxonomy_node_id": e.taxonomy_node_id or "",
                "taxonomy_path": list(paths.get(e.taxonomy_node_id) or []),
            }
    return {"status": "ok", "entry": entry_patch}


@router.patch("/cash-entries/batch-taxonomy")
async def batch_set_taxonomy(request: Request):
    """批次分類（owner 2026-08-28「我想要有一個可以批次處理分類的按鈕功能」）。

    信用卡明細一天好幾筆「街口電支－統一超商」，一筆一筆點三格分類要按上千次。
    這支把選起來的列一次掛到同一個節點（`taxonomy_node_id=''` ＝一次清掉分類）。

    規則一條都不另寫：三欄鏡射走 `_sync_taxonomy`（**唯一**那份），可掛專案的
    不變式走 `_enforce_cash_project_link`，鎖月走 `_locked_month_set` ——
    單筆更新有的守衛，批次一個都不能少（批次才是會一次弄壞幾百列的那條路）。

    🔴 有問題就**整批不動**（比照 batch-project）：跳過壞列會讓人以為全部都成功了，
    而畫面上看不出哪幾列沒改到 —— 補歷史時這種靜默漏網最難查。
    """
    check_logged_in(request)           # 先擋匿名（不給 401/404 當存在性預言機）
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    ids = body.get("entry_ids") or []
    # 兩種挑法，因為兩本帳的分類長得不一樣：私帳有分類樹（送節點 id），
    # 母公司是平的一層（送 category 字串 —— 那本沒有樹，種子只種 mine）。
    # 兩邊都只是同一份 `_sync_taxonomy` 的兩個入口，不是兩套規則。
    nid = body.get("taxonomy_node_id")
    cat = body.get("category")
    if not ids:
        raise HTTPException(status_code=400, detail="請提供 entry_ids")
    if nid is None and cat is None:
        raise HTTPException(status_code=400,
                            detail="請提供 taxonomy_node_id 或 category")
    nid = (nid or "").strip() if nid is not None else None
    async with factory() as session:
        rows = (await session.execute(
            select(CrmCashEntry).where(CrmCashEntry.id.in_(ids)))).scalars().all()
        if not rows:
            raise HTTPException(status_code=404, detail="找不到這些收支紀錄")
        _mine_or_admin_write_rows(request, rows)
        ents = {(e.entity or "parent") for e in rows}
        # 節點只屬於一本帳的樹 —— 混選必然有一半找不到節點。與其讓它報
        # 「找不到這個分類節點」（看起來像樹壞了），不如說清楚是選錯了。
        if len(ents) > 1:
            raise HTTPException(status_code=400,
                                detail="選取的列橫跨兩本帳，請分開處理")
        ent = ents.pop()
        locked = await _locked_month_set(session, entity=ent)
        violations = [f"{e.summary or e.id}（{month_of(e.entry_date)}）"
                      for e in rows if month_of(e.entry_date) in locked]
        if violations:
            _raise_locked_batch(violations)
        # id→路徑表算一次給整批用（_sync_taxonomy 的 paths 參數就為了這個）
        from core.cash_tree import path_map
        paths = await path_map(session, ent)
        now = _now()
        for e in rows:
            if nid is not None:
                await _sync_taxonomy(session, e, {"taxonomy_node_id": nid}, paths)
            else:
                # 平的那本：這條路的語意是「這幾列現在就是這個類別」，所以更深的
                # 兩欄要一起清掉 —— 不清的話「清空分類」會留下一列沒有類別、卻
                # 還顯示著項目與子項目的列（正是這一輪在消滅的孤兒形狀）。
                # 三欄自己賦值、節點交給同一份規則反查（母公司沒有樹 → 查不到
                # 就是 None，正常）。`paths` 一樣要帶：不帶的話 `_sync_taxonomy`
                # 逐列打 `find_node_id`，一批 200 列就是 200 趟必定落空的查詢。
                e.category, e.item, e.sub_item = (cat or ""), "", ""
                await _sync_taxonomy(session, e, {"category": e.category}, paths)
            _enforce_cash_project_link(e)
            e.updated_at = now
        await session.commit()
        # 整批同一個節點 → 鏡射結果也只有一份，回一份給前端就地 patch 那幾列
        e0 = rows[0]
        entry_patch = {
            "category": e0.category or "",
            "book": _tax_split(e0.category)[0],
            "item": (e0.item or "") or _tax_split(e0.category)[1],
            "sub_item": e0.sub_item or "",
            "taxonomy_node_id": e0.taxonomy_node_id or "",
            "taxonomy_path": list(paths.get(e0.taxonomy_node_id) or []),
        }
    return {"status": "ok", "updated": len(rows), "entry": entry_patch}


@router.delete("/cash-entries/{entry_id}")
async def delete_cash_entry(entry_id: str, request: Request):
    check_logged_in(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await session.get(CrmCashEntry, entry_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此收支紀錄")
        _mine_or_admin_write(request, e.entity)  # 兩本帳寫入守衛：母公司=Lv3、私帳=mine full
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
        if (e.entity or "parent") == "mine":
            await _sync_mine_project_received(session, e.project_id,
                                              -int(e.deposit or 0))
        await session.delete(e)
        await session.flush()
        # 被這筆收款影響到的發票要重算 —— 舊碼是「無條件打回未收款」，那會把
        # 合併匯款裡另外兩筆收款也收過的發票一起打回去。
        await resettle_invoices(
            session, set(touched) | ({e.invoice_id} if e.invoice_id else set()),
            unmark_if_empty=True)
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
    return map_csv_row(
        _CASH_COL_MAP, header_map, row,
        coerce=lambda f, v: _parse_money(v) if f in _CASH_INT_FIELDS else v)


@router.post("/cash-entries/import_csv")
async def import_cash_csv(request: Request, file: UploadFile = File(...)):
    return await _import_money_csv(
        request, file, map_row=_map_cash_row,
        skip_if=lambda d: not d.get("summary"),
        date_fields=("entry_date", "payment_date"),
        build=lambda data, d: CrmCashEntry(
            id=uuid.uuid4().hex, entry_date=d["entry_date"], entity="parent",
            payment_date=d["payment_date"], created_at=_now(), **data))


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
            # 🔴 代稱不再全域唯一（CRM/私帳各一筆同名）—— 不指名母公司那筆，
            # 這個 join 會讓同一張發票回兩列
            .outerjoin(Client, _sa_and(Client.short_name == CrmInvoice.company_name,
                                       _cli_not_mine(Client.entity)))
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
            query = query.where(
                CrmInvoice.payment_status.notin_([*INVOICE_COLLECTED, "作廢"]))
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
            if inv and inv.payment_status not in INVOICE_COLLECTED:
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

async def _entry_for_alloc(session, entry_id: str, request, *, month_guard=False):
    """取這筆收支 ＋ 驗帳本（＋ 需要時擋已鎖月）。四個分配端點共用這段前言。

    level="full"：CRM 帳務＝原始帳列，合夥人不可及 —— money_dep 已擋一層，
    這裡刻意雙保險（plan §2.4）。
    """
    e = await session.get(CrmCashEntry, entry_id)
    if not e:
        raise HTTPException(status_code=404, detail="收支明細不存在")
    ent = require_entity(request, e.entity or "parent", level="full")
    if month_guard:
        await _assert_month_open(session, e.entry_date, entity=ent)
    return e, ent


def _alloc_verdict(entry, allocated: int) -> dict:
    """分配判讀 —— 規則在 core.finance_logic（純函式、有測試、共用容差）。"""
    return alloc_verdict(int(entry.deposit or 0), allocated, side="receipt")


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
            # 逐張的匯費 —— 面板重開時要能把那格畫回來（見 models 那欄的 docstring）
            "fee": int(getattr(link, "fee", 0) or 0),
            # 這張發票**整體**收了多少（跨所有收款）—— 分期時要看得到全貌，
            # 不能只看眼前這一筆分配了多少
            **collection_fields(inv.amount_total if inv else 0,
                                coll.get(link.invoice_id)),
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
        e, _ent = await _entry_for_alloc(session, entry_id, request)
        items, allocated = await _load_allocs(session, e)
    return {"items": items, "check": _alloc_verdict(e, allocated)}


def _payment_verdict(entry, allocated: int) -> dict:
    """付款側判讀 —— 同一支 alloc_verdict，只是換一側（容差方向與措辭）。"""
    return alloc_verdict(int(entry.expense or 0), allocated, side="payment")


async def _load_payment_allocs(session, entry):
    """回 (連結列 dict 清單, 分配合計)。"""
    rows = (await session.execute(
        select(CrmCashPaymentLink, CrmPaymentRequest)
        .outerjoin(CrmPaymentRequest,
                   CrmPaymentRequest.id == CrmCashPaymentLink.payment_request_id)
        .where(CrmCashPaymentLink.cash_entry_id == entry.id)
        .order_by(CrmCashPaymentLink.created_at))).all()
    paid = await _payment_allocated(session, [x.payment_request_id for x, _a in rows])
    items, allocated = [], 0
    for link, ap in rows:
        allocated += int(link.amount or 0)
        total = int((ap.amount if ap else 0) or 0)
        got = paid.get(link.payment_request_id, 0)
        items.append({
            "payment_request_id": link.payment_request_id,
            "amount": int(link.amount or 0),
            # 這張單**整體**付了多少（跨所有匯款）—— 分次支付要看得到全貌
            # 🔴 settled 是關鍵那一欄。少了它，前端只能用 request_open > 0 判
            #    「尚欠」—— 7,010 的單付了 7,000（那 10 元是跨行手續費），
            #    後端標「已付款」、同一個面板同一列標「尚欠 $10」。發票側
            #    （collection_fields）就是為了這個才把布林算好送過去的。
            "request_total": total, "request_paid": got,
            "request_open": max(0, total - got),
            "request_settled": amount_is_settled(got, total),
            "summary": (ap.summary if ap else "") or "",
            "payee_name": (ap.payee_name if ap else "") or "",
            "request_date": _fmt_day(ap.request_date) if ap else "",
            "payment_status": (ap.payment_status if ap else "") or "",
            "missing": ap is None,      # 請款單被刪了，連結變孤兒
        })
    return items, allocated


@router.get("/cash-entries/{entry_id}/payments", dependencies=[Depends(money_dep)])
async def get_cash_entry_payments(entry_id: str, request: Request):
    """這筆匯款掛了哪些請款單、各分配多少、與實付差多少。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e, _ent = await _entry_for_alloc(session, entry_id, request)
        items, allocated = await _load_payment_allocs(session, e)
    return {"items": items, "check": _payment_verdict(e, allocated)}


async def _write_allocs(request, entry_id: str, kind: str, items, fee=None):
    """整組取代某筆收支的分配。兩側**同一條**寫入流程。

    驗帳本 → 擋鎖月 → 驗每一列 → 整組取代（連帶同步主要單據欄與相關單據的
    收付狀態）→ 有匯費就認列 → 回最新的明細與判讀。

    🔴 兩側本來各寫一遍這十二行，於是「存檔後回什麼」「commit 之前還做了什麼」
    要對兩處看。差別只剩匯費的**形狀**：收款側逐張（存進連結表，面板重開畫得回來），
    付款側整筆一個（跨行手續費對一次匯出收一次）。
    """
    k = _ALLOC_KINDS[kind]
    # 🔴 寫入守衛＝列的帳本說了算（母公司=Lv3、私帳=mine full）——原本開頭
    # _check_auth 只認 Lv3，帳本主人在生產存「請款單分配」直接 權限不足 ×3
    # （2026-08-26 實測；上次開 mine 寫入權時漏了分配這條路，它不走
    # _entity_for_write 咽喉）。先 check_logged_in 再載列，匿名不得拿 404 探 id。
    check_logged_in(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e, ent = await _entry_for_alloc(session, entry_id, request,
                                        month_guard=True)
        _mine_or_admin_write(request, e.entity)
        rows = await resolve_allocs(
            session, [(getattr(it, k["id_field"]), it.amount) for it in (items or [])],
            ent, kind)
        # 逐張匯費只有收款側有（見 _ALLOC_KINDS 的 perItemFee 那段說明）——
        # 存下來，關聯面板重開時才畫得回來
        if k.get("per_item_fee"):
            fees = {(e.id, getattr(it, k["id_field"])): int(getattr(it, "fee", 0) or 0)
                    for it in (items or [])}
            await k["replace"](session, e, rows, fees=fees)
        else:
            await k["replace"](session, e, rows)
        if fee is not None:
            # 不變量與冪等性在 core.finance_logic 的兩支 recognize_*_fee ——
            # 那是錢的規則，要有自己的單元測試，不該住在端點裡。
            # 🔴 依 kind 取，不要寫死付款側：收款側加上 fee 欄位之後（對帳單匯入
            #    那條路先做了），這裡若還只認 recognize_bank_fee，關聯面板重存一次
            #    就會把 deposit 的補回值抹掉 —— 帳戶淨流悄悄變回含匯費的數字，
            #    那一列從此在對帳工作台配不上，而且畫面上完全看不出來。
            try:
                k["fee"](e, fee)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        await session.commit()
        items_out, allocated = await k["load"](session, e)
    return {"ok": True, "items": items_out, "check": k["verdict"](e, allocated)}


@router.put("/cash-entries/{entry_id}/payments")
async def set_cash_entry_payments(entry_id: str, req: CashPaymentLinksPayload,
                                  request: Request):
    """整組取代這筆匯款的請款單分配（owner 2026-08-22）。

    金額對不對得上實付**不擋**（跟發票那側同一個道理：出納合併匯款、跨行
    手續費 10~15 元，硬擋會逼人亂填）。擋的是一定錯的：單不存在／不同帳本／
    重複／金額 ≤ 0。

    副作用（刻意）：
      · `payment_request_id` 同步成金額最大的那張 —— classify_cash_entry 的
        硬連結優先序讀它。
      · 相關請款單的付款狀態重算（付滿才標已付款，分次支付標應付款，
        連結被拿光退回未付款並清掉付款日）。
      · `fee` 有給就寫進 bank_fee —— 那 10 元從此是管理費用，不是對不起來的差額。
    """
    return await _write_allocs(request, entry_id, "payment", req.items,
                               fee=req.fee)


@router.put("/cash-entries/{entry_id}/invoices")
async def set_cash_entry_invoices(entry_id: str, req: CashInvoiceLinksPayload,
                                  request: Request):
    """整組取代這筆收款的發票分配。

    金額檢查不擋（見上），但這幾件會擋 —— 它們是**一定錯**而不是可能錯：
      發票不存在／不同帳本；同一張發票重複出現；分配金額 ≤ 0。

    副作用（刻意）：`invoice_id` / `invoice_number` / `has_invoice` 同步成金額最大
    的那張發票 —— 列表與舊查詢都讀這幾欄，不同步的話畫面會跟明細對不起來。

    🔴 `fee` 從**各列加總**來，不是 payload 上的單一欄：收款側的匯費是逐張發票
    被扣的（客戶匯三張的錢，可能只有其中一張被扣了 30），前端也是那樣填的。
    加總後才是這筆收款要補回 deposit 的金額。
    """
    fee = sum(int(it.fee or 0) for it in (req.items or []))
    return await _write_allocs(request, entry_id, "invoice", req.items,
                               fee=fee if req.items else None)


_bind_alloc_ops()


# ── 收支分類樹 ────────────────────────────────────────────────────
# 樹的正本是 `cash_taxonomy_nodes`（種子見 db/seed_cash_taxonomy.py）。日常讀取
# 走 `/cash-entries/options` 的 `tree`；這裡是它的 CRUD —— POST 收「＋自訂…」
# 長出來的新節點，GET/PUT/DELETE 服務後台編輯器（finance 設定頁的分類樹卡）。
# 🔴 PUT 的改名／搬家是**資料遷移**：收支三欄鏡射與 finance_category_map 的鍵
# 會一起改（見 update_cash_taxonomy_node），不是改個標籤而已。

@router.post("/cash-taxonomy/nodes", dependencies=[Depends(money_dep)])
async def create_cash_taxonomy_node(req: CashTaxonomyNodePayload, request: Request):
    from db.models import CashTaxonomyNode

    ent = _entity_for_write(request, req.entity)
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="分類名稱必填")
    if _TAX_SEP in name:
        # 名稱含底線會把複合鍵切爛（`轉匯與定存_公司信用卡` 的教訓：只切第一個
        # 底線，所以第二層叫「公司_信用卡」時 split 出來就不是原來那個名字了）
        raise HTTPException(status_code=422, detail=f"分類名稱不能包含「{_TAX_SEP}」")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        parent_id = (req.parent_id or "").strip()
        depth = 1
        if parent_id:
            parent = await session.get(CashTaxonomyNode, parent_id)
            if parent is None or parent.entity != ent:
                raise HTTPException(status_code=404, detail="找不到上層分類")
            depth = (parent.depth or 1) + 1
        exist = (await session.execute(
            select(CashTaxonomyNode).where(
                CashTaxonomyNode.entity == ent,
                CashTaxonomyNode.parent_id == parent_id,
                CashTaxonomyNode.name == name))).scalar_one_or_none()
        if exist is not None:
            # 已經有了就回它 —— 兩個人同時打同一個名字不該噴錯，結果一樣就好。
            # 停用過的順手復活（他正想用它）。
            if not exist.active:
                exist.active = 1
                await session.commit()
            return {"status": "ok", "node": {"id": exist.id, "name": exist.name,
                                             "depth": exist.depth}, "existed": True}
        # sort 排在同層最後（種子給的順序是 owner 的 Sheet 順序，新的接在後面）
        last = (await session.execute(
            select(func.max(CashTaxonomyNode.sort)).where(
                CashTaxonomyNode.entity == ent,
                CashTaxonomyNode.parent_id == parent_id))).scalar()
        node = CashTaxonomyNode(id=uuid.uuid4().hex, entity=ent, parent_id=parent_id,
                                name=name, depth=depth, sort=int(last or 0) + 1, active=1)
        session.add(node)
        await session.commit()
        return {"status": "ok", "node": {"id": node.id, "name": node.name,
                                         "depth": node.depth}, "existed": False}


@router.get("/cash-taxonomy/nodes", dependencies=[Depends(money_dep)])
async def list_cash_taxonomy_nodes(request: Request, entity: str = Query("")):
    """整棵樹（**含停用**）＋每個節點的使用筆數 —— 後台編輯器用。

    `count` 是直接掛在它上面的、`subtree_count` 是整支的。改名／停用／刪除前
    要看得到「會動到幾筆」，這是危險操作唯一的剎車。
    """
    from core.cash_tree import load_tree

    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        # active 隨 build_tree 的節點 dict 出來，不必再查一張表
        tree = await load_tree(session, ent, include_inactive=True)
        used = dict((await session.execute(
            select(CrmCashEntry.taxonomy_node_id, func.count())
            .where(CrmCashEntry.entity == ent,
                   CrmCashEntry.taxonomy_node_id.isnot(None))
            .group_by(CrmCashEntry.taxonomy_node_id))).all())

    def walk(nodes):
        total = 0
        for n in nodes:
            n["count"] = used.get(n["id"], 0)
            n["subtree_count"] = n["count"] + walk(n["children"])
            total += n["subtree_count"]
        return total

    walk(tree)
    return {"tree": tree}


async def _rename_category_map(session, renames, ent: str = "mine") -> list:
    """複合鍵改名 → `finance_category_map` 的鍵跟著改。

    🔴 不改的話那批帳**從三表消失**：那張表帶著每個鍵的科目與 treatment
    （實測 38 個私帳複合鍵都在，各自帶 transfer／direct_expense／direct_income）。
    新鍵已經存在就擋下來 —— 把兩個科目對映併成一個不是「改名」該做的事。
    """
    from db.models import FinanceCategoryMap

    done = []
    for old, new in renames:
        clash = (await session.execute(
            select(FinanceCategoryMap).where(
                FinanceCategoryMap.source == "cash",
                FinanceCategoryMap.entity == ent,
                FinanceCategoryMap.category_text == new))).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(
                status_code=409,
                detail=f"「{new}」已經有科目對映了 —— 改成這個名字會把兩個對映併在一起。"
                       f"請先到「科目與設定」處理那一筆，或換個名字")
        row = (await session.execute(
            select(FinanceCategoryMap).where(
                FinanceCategoryMap.source == "cash",
                FinanceCategoryMap.entity == ent,
                FinanceCategoryMap.category_text == old))).scalar_one_or_none()
        if row is not None:
            row.category_text = new
            done.append([old, new])
    return done


@router.put("/cash-taxonomy/nodes/{node_id}", dependencies=[Depends(money_dep)])
async def update_cash_taxonomy_node(node_id: str, req: CashTaxonomyNodeUpdate,
                                    request: Request):
    """改名／搬家／停用。

    改名與搬家是**資料遷移**，一次做完三件事：節點本身 → 掛在這一支底下的收支
    三欄鏡射 → finance_category_map 的鍵。少做任何一件，帳與分類就對不起來。
    """
    from core.cash_tree import (category_keys, load_nodes, remirror_subtree,
                                subtree_ids)
    from db.models import CashTaxonomyNode

    ent = _entity_for_write(request, req.entity)
    data = req.model_dump(exclude_unset=True, exclude={"entity"})
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        node = await session.get(CashTaxonomyNode, node_id)
        if node is None or node.entity != ent:
            raise HTTPException(status_code=404, detail="找不到這個分類節點")
        # 改前撈一次、flush 後撈一次 —— 各 helper 都吃 nodes=，別讓一個 PUT
        # 把節點表整張撈六遍
        nodes_before = await load_nodes(session, ent, include_inactive=True)
        new_name = (data.get("name") or node.name).strip()
        new_parent = data.get("parent_id", node.parent_id) or ""
        if "name" in data:
            if not new_name:
                raise HTTPException(status_code=422, detail="分類名稱必填")
            if _TAX_SEP in new_name:
                raise HTTPException(status_code=422,
                                    detail=f"分類名稱不能包含「{_TAX_SEP}」")
        if new_parent != node.parent_id:
            # 🔴 只准同層搬家。跨層搬會讓深度變，複合鍵跟著憑空出現或消失
            # （第三層搬到第二層＝多一個沒人對映的科目鍵，那批帳當場掉出三表）。
            old_parent = (await session.get(CashTaxonomyNode, node.parent_id)
                          if node.parent_id else None)
            tgt = await session.get(CashTaxonomyNode, new_parent) if new_parent else None
            if new_parent and (tgt is None or tgt.entity != ent):
                raise HTTPException(status_code=404, detail="找不到要搬去的上層分類")
            if (tgt.depth if tgt else 0) != (old_parent.depth if old_parent else 0):
                raise HTTPException(
                    status_code=422,
                    detail="只能搬到同一層的其他分類底下（跨層搬會讓科目對映對不上）")
            if new_parent in await subtree_ids(session, ent, node_id,
                                               nodes=nodes_before):
                raise HTTPException(status_code=422, detail="不能搬到自己底下")
        if new_name != node.name or new_parent != node.parent_id:
            dup = (await session.execute(
                select(CashTaxonomyNode.id).where(
                    CashTaxonomyNode.entity == ent,
                    CashTaxonomyNode.parent_id == new_parent,
                    CashTaxonomyNode.name == new_name,
                    CashTaxonomyNode.id != node_id))).scalar()
            if dup:
                raise HTTPException(status_code=409,
                                    detail=f"同一層底下已經有「{new_name}」了")

        ids = await subtree_ids(session, ent, node_id, nodes=nodes_before)
        before = await category_keys(session, ent, ids, nodes=nodes_before)
        node.name = new_name
        node.parent_id = new_parent
        if "active" in data:
            node.active = 1 if data["active"] else 0
        node.updated_at = _now()
        await session.flush()      # 🔴 先落地，下面重讀才看得到新名字
        nodes_after = await load_nodes(session, ent, include_inactive=True)
        after = await category_keys(session, ent, ids, nodes=nodes_after)
        renames = [(before[k], after[k]) for k in before
                   if k in after and before[k] != after[k]]
        mapped = await _rename_category_map(session, renames, ent)
        rows = await remirror_subtree(session, ent, node_id, nodes=nodes_after)
        await session.commit()
    return {"status": "ok", "rows_remirrored": rows, "category_map_renamed": mapped}


@router.delete("/cash-taxonomy/nodes/{node_id}", dependencies=[Depends(money_dep)])
async def delete_cash_taxonomy_node(node_id: str, request: Request,
                                    entity: str = Query("")):
    """只刪得掉**空的**節點（沒有子分類、整支沒有任何收支列）。

    有資料的請用「停用」—— 刪掉會讓那些列指向一個爬不回根的孤兒 id，而 category
    欄還留著舊值，兩邊從此對不起來。

    🔴 `finance_category_map` 那一筆**刻意不刪**：它是會計對映（科目＋treatment），
    歸「科目與設定」管；分類樹刪一個沒人用的節點不該連帶動到會計設定。
    """
    from core.cash_tree import subtree_ids
    from db.models import CashTaxonomyNode

    ent = _entity_for_write(request, entity or None)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        node = await session.get(CashTaxonomyNode, node_id)
        if node is None or node.entity != ent:
            raise HTTPException(status_code=404, detail="找不到這個分類節點")
        ids = await subtree_ids(session, ent, node_id)
        if len(ids) > 1:
            raise HTTPException(status_code=409,
                                detail=f"底下還有 {len(ids) - 1} 個分類，請先處理它們")
        used = (await session.execute(
            select(func.count()).select_from(CrmCashEntry)
            .where(CrmCashEntry.entity == ent,
                   CrmCashEntry.taxonomy_node_id == node_id))).scalar()
        if used:
            raise HTTPException(status_code=409,
                                detail=f"還有 {used} 筆收支掛在這個分類上 —— 請改用「停用」")
        await session.delete(node)
        await session.commit()
    return {"status": "ok"}
