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

from fastapi import Depends, HTTPException, Request, UploadFile, File, Query

from core.crm_logic import normalize_tax_id, payment_label
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
from core.ledger import (require_entity)
from core.schemas import (InvoicePayload)

from ._shared import (router, _check_auth, money_dep, _require_db,
                      _get_factory, _fmt_day, _now,
                      _parse_shoot_date, _assert_month_open, _assert_rows_open,
                      map_csv_row)

# 這個檔案唯一用得到發票檔那邊的東西：改發票時要跟著改檔名
from .invoice_files import _resync_invoice_file

try:
    from ._shared import (select, or_, func, CrmProject, CrmInvoice,
                          CrmPaymentRequest, CrmCashEntry,
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
    "invoice": {"model": CrmInvoice, "link": CrmCashInvoiceLink,
                "noun": "發票", "id_field": "invoice_id",
                "dup": "同一張發票不可重複掛在同一筆收款",
                "label": lambda o: o.invoice_number or o.title,
                "fee": apply_receipt_fee,
                # 逐張匯費：匯出行對每一張發票的匯款各扣一次。付款側沒有這個
                # （跨行手續費是對「那一筆匯出」收一次，涵蓋幾張請款單都一樣）。
                "per_item_fee": True},
    "payment": {"model": CrmPaymentRequest, "link": CrmCashPaymentLink,
                "noun": "請款單", "id_field": "payment_request_id",
                "dup": "同一張請款單重複出現",
                # 顯示名走 core.crm_logic 那份正本 —— 這裡本來是
                # `summary or payee_name`（相反的順序），害挑選視窗
                # 顯示收款人、退回的 422 卻用摘要稱呼同一張單。
                "label": lambda o: payment_label(o.payee_name, o.summary),
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
    limit: int = Query(0), order: str = Query(""), offset: int = Query(0),
):
    """`limit`（0＝全部）／`offset`（只在帶 limit 時生效）與 `order="recent"`（建立時間新→舊）
    給手機版「最近登記＋載入更多」用；不帶就是桌機發票本原本的整批＋日期排序。帶 limit 時 total＝本頁筆數。"""
    # 兩本帳：money_dep 之上疊第二層 entity scope（plan §2.4）
    # full：CRM 帳務＝原始帳列，合夥人不可及 —— money_dep 已擋一層，刻意雙保險
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        query = (
            select(CrmInvoice, CrmProject.name.label("pn"), CrmProject.entity.label("pent"))
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
        if order == "recent":
            # 「最近登記的」看建立時間不看發票日期（補登舊票也要浮到最上面）；id 當 tiebreaker
            query = query.order_by(None).order_by(CrmInvoice.created_at.desc(), CrmInvoice.id.desc())
        if limit > 0:
            query = query.limit(limit).offset(max(offset, 0))
        rows = (await session.execute(query)).all()
        coll = await _invoice_collections(session, [r[0].id for r in rows])
    from core.ledger import hide_mine_projects
    _hide = hide_mine_projects(request)
    out = []
    for r in rows:
        # 內部代開可以掛私帳案：案名對沒私帳權限的人不顯示（列的可見性規則同 list_projects）
        pn = "" if (_hide and (r[2] or "parent") == "mine") else (r[1] or "")
        d = _to_invoice_dict(r[0], pn)
        c = coll.get(r[0].id) or {}
        # 清單只帶合計與最後到款日（逐筆明細在 GET /invoices/{id}）—— 分期收款
        # 要能一眼看出「開了多少、收了多少、還欠多少」。
        d.update(collection_fields(r[0].amount_total, c))
        out.append(d)
    return {"invoices": out, "total": len(out)}



async def _assert_project_link(session, request: Request, project_id, category) -> None:
    """發票掛專案的規則（owner 2026-09-04）：三種類別都可以掛；私帳案只能掛在「內部代開」、且請求者要有私帳權限。
    沒這條的話，沒有 finance_mine 的人挑不到私帳案（下拉不給），但直接送 id 還是掛得上。"""
    pid = (project_id or "").strip() if isinstance(project_id, str) else project_id
    if not pid:
        return
    from core.finance_logic import MINE_LINK_INVOICE_CATEGORY
    from core.ledger import hide_mine_projects
    p = await session.get(CrmProject, pid)
    if not p:
        raise HTTPException(status_code=404, detail="找不到要關聯的專案")
    if (p.entity or "parent") == "mine":
        if (category or "") != MINE_LINK_INVOICE_CATEGORY:
            raise HTTPException(status_code=422, detail=f"私帳案只能掛在「{MINE_LINK_INVOICE_CATEGORY}」發票")
        if hide_mine_projects(request):
            raise HTTPException(status_code=403, detail="沒有私帳權限，不能把發票掛到私帳案")


@router.post("/invoices")
async def create_invoice(req: InvoicePayload, request: Request):
    _check_auth(request)
    _require_db()
    ent = _entity_for_write(request, req.entity)
    factory = await _get_factory()
    now = _now()
    data = req.model_dump(exclude={"invoice_date", "entity"})
    async with factory() as _s:
        await _assert_project_link(_s, request, data.get("project_id"), data.get("category"))
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
        await _assert_project_link(session, request, upd.get("project_id"), upd.get("category"))
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


# ── Cash Entry (收支明細) ───────────────────────────────────

def _set_primary_invoice(e, inv):
    """設（或清除）收支的「主要發票」三欄。

    這三欄是刻意的反正規化 —— 列表與舊查詢都讀它們，所以必須一起動。
    分開寫在兩個端點裡的話，第三條寫入路徑會照抄先看到的那一份。
    """
    e.invoice_id = inv.id if inv else None
    e.invoice_number = (inv.invoice_number or "") if inv else None
    e.has_invoice = 1 if inv else 0


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
    """批次版：**這批列涉及哪幾本帳，就要有哪幾本的寫入權**（各本各驗一次）。

    🔴 2026-08-30 改：原本是「全私帳 → mine full；混到母公司列 → Lv3」。
    混帳本那條會漏 —— Lv3 但**沒有** finance_mine 的帳號（生產上除了 owner
    全都是這種）只要在 ids 裡混一張母公司的進來，整批就走 `_check_auth` 放行，
    連帶把私帳那幾列一起改掉。finance_mine 是指名制、Lv3 刻意不隱含它
    （私帳的列對沒這個模組的人是「整個看不到」），繞過去就等於白設。

    改成取聯集後沒有任何合法用法受影響：混帳本原本就要 Lv3，現在是 Lv3 ＋
    finance_mine；純母公司仍是 Lv3、純私帳仍是 mine full，兩條都沒動。
    """
    for ent in {(getattr(r, "entity", None) or "parent") for r in rows}:
        _mine_or_admin_write(request, ent)


@router.patch("/invoices/batch-receive")
async def batch_receive(request: Request):
    """批次標記發票為已收款。

    F1 月結守衛：本端點一直沒掛守衛（發票的權責認列月 = invoice_date 不變，
    標記收款只動 payment_status/paid_date；現金側鎖月由收支明細把關）——
    entity 化維持原判準，各列的 entity 不變、不做鎖月檢查。

    🔴 兩本帳守衛（2026-08-30 補）：原本只有 `_check_auth`，逐張
    `session.get(CrmInvoice, iid)` 不看 entity —— 帶著任意 id 打進來就能改到
    另一本帳的發票，而且**不會噴錯**（只回一個 updated 數字）。今天沒出事純粹
    是私帳還沒開過發票。照 batch_pay 的作法：先載會變動的列 → 按列的帳本驗。"""
    check_logged_in(request)
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    ids = body.get("invoice_ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="請提供 invoice_ids")

    async with factory() as session:
        # 先載入會變動的列（權限不足時一筆都不動）
        rows = []
        for iid in ids:
            inv = await session.get(CrmInvoice, iid)
            if inv and inv.payment_status not in INVOICE_COLLECTED:
                rows.append(inv)
        _mine_or_admin_write_rows(request, rows)
        updated = 0
        for inv in rows:
            # 批次標記沒有收支入帳日 → 以標記時間為收款日（已有值不覆蓋）
            _mark_invoice_received(inv, _now())
            inv.updated_at = _now()
            await _sync_passthrough_request(session, inv)
            updated += 1
        await session.commit()
    return {"status": "ok", "updated": updated}


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


# 分配運算的延後綁定：`_ALLOC_KINDS` 要填的六支都定義在本檔後段，所以等模組
# 載入完才填（見 _bind_alloc_ops 上方的說明）。
#
# 🔴 2026-08-30 拆檔時這一行差點掉進 taxonomy.py —— 它是**裸的頂層敘述**，不屬於
# 任何函式，機械式切檔會把它掃進下一個區塊。ruff 的 F821 抓到了（那個檔沒有這個
# 名字），但如果它剛好掉進一個看得到這個名字的檔，就會靜靜地在錯的時機執行。
_bind_alloc_ops()
