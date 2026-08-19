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

from core.auth import check_admin
from core.finance_logic import month_of
from core.ledger import require_entity
from core.schemas import InvoicePayload, PaymentRequestPayload, CashEntryPayload

from ._shared import (router, token_router, _check_auth, money_dep, _require_db,
                      _get_factory, _fmt_day, _now,
                      _parse_shoot_date, _assert_month_open, _assert_rows_open,
                      _locked_month_set, _raise_locked_batch)

try:
    from ._shared import (select, or_,
                          Client, CrmProject, CrmStaff, CrmInvoice,
                          CrmPaymentRequest, CrmCashEntry, CrmProjectExpense)
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
    return {
        "invoices": [_to_invoice_dict(r[0], r[1] or "") for r in rows],
        "total": len(rows),
    }


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


@router.get("/invoices/{invoice_id}", dependencies=[Depends(money_dep)])
async def get_invoice(invoice_id: str, request: Request):
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
    return _to_invoice_dict(inv, pn)


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
        for k, v in req.model_dump(exclude={"invoice_date", "entity"}).items():
            setattr(inv, k, v)
        inv.invoice_date = new_date
        inv.updated_at = _now()
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
        data["payment_status"] = "未付款" if unpaid else "已付款"
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
        os.replace(old, target)
        return target
    except OSError:
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
        if not os.path.isabs(root):
            raise HTTPException(status_code=422, detail=(
                f"請填**完整路徑**：NAS 要用 \\\\192.168.1.132\\Archive\\... 這種"
                f"開頭兩個反斜線的寫法，本機碟要用 D:\\... —— 目前填的「{root}」是"
                f"相對路徑，檔案會被存進主控端資料夾裡而不是你要的位置。"))
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

def _to_payment_dict(p, project_name: str = "") -> dict:
    return {
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
        if q:
            ql = f"%{q}%"
            query = query.where(or_(
                CrmPaymentRequest.summary.ilike(ql),
                CrmPaymentRequest.payee_name.ilike(ql),
            ))
        rows = (await session.execute(query)).all()
    return {
        "payments": [_to_payment_dict(r[0], r[1] or "") for r in rows],
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


@router.get("/cash-entries", dependencies=[Depends(money_dep)])
async def list_cash_entries(
    request: Request,
    q: str = Query(""), category: str = Query(""),
    item: str = Query(""), project_id: str = Query(""),
    entity: str = Query(""),
):
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
        if item:
            query = query.where(CrmCashEntry.item == item)
        if project_id:
            query = query.where(CrmCashEntry.project_id == project_id)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(CrmCashEntry.summary.ilike(ql), CrmCashEntry.payee.ilike(ql)))
        rows = (await session.execute(query)).all()
    return {"entries": [_to_cash_dict(r[0], r[1] or "", r[2] or "") for r in rows], "total": len(rows)}


@router.post("/cash-entries")
async def create_cash_entry(req: CashEntryPayload, request: Request):
    _check_auth(request)
    _require_db()
    ent = _entity_for_write(request, req.entity)
    factory = await _get_factory()
    date_fields = {"entry_date", "payment_date"}
    dates = {f: _parse_shoot_date(getattr(req, f)) for f in date_fields}
    data = req.model_dump(exclude=date_fields | {"entity"})
    e = CrmCashEntry(id=uuid.uuid4().hex, **dates, entity=ent, created_at=_now(), **data)
    async with factory() as session:
        await _assert_month_open(session, dates.get("entry_date"), entity=ent)
        session.add(e)
        if req.invoice_id and req.deposit:
            inv = await session.get(CrmInvoice, req.invoice_id)
            if inv:
                # 收款日 = 收支入帳日（已有值不覆蓋 — 第一次收款為準）
                _mark_invoice_received(inv, dates.get("entry_date") or _now())
        await session.commit()
    return {"status": "ok", "entry_id": e.id, "entry": {"id": e.id}}


@router.put("/cash-entries/{entry_id}")
async def update_cash_entry(entry_id: str, req: CashEntryPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    date_fields = {"entry_date", "payment_date"}
    dates = {f: _parse_shoot_date(getattr(req, f)) for f in date_fields}
    async with factory() as session:
        e = await session.get(CrmCashEntry, entry_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此收支紀錄")
        # 兩本帳：payload.entity None＝維持既有值；帶不同值＝想搬帳本 → 422
        ent = _entity_for_write(request, req.entity, e)
        await _assert_month_open(session, e.entry_date, dates.get("entry_date"),
                                 entity=ent)
        old_invoice_id = e.invoice_id
        for k, v in req.model_dump(exclude=date_fields | {"entity"}).items():
            setattr(e, k, v)
        for k, v in dates.items():
            setattr(e, k, v)
        e.updated_at = _now()
        if req.invoice_id and req.deposit:
            inv = await session.get(CrmInvoice, req.invoice_id)
            if inv:
                # 收款日 = 收支入帳日（已有值不覆蓋 — 第一次收款為準）
                _mark_invoice_received(inv, dates.get("entry_date") or _now())
        if old_invoice_id and old_invoice_id != req.invoice_id:
            old_inv = await session.get(CrmInvoice, old_invoice_id)
            if old_inv and old_inv.payment_status == "已收款":
                _unmark_invoice_received(old_inv)  # 收款關聯解除 → 收款日一併清掉
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
        if e.invoice_id and e.deposit:
            inv = await session.get(CrmInvoice, e.invoice_id)
            if inv and inv.payment_status == "已收款":
                _unmark_invoice_received(inv)  # 收款關聯的收支被刪 → 收款日一併清掉
        # 對帳工作台反向清理：這筆若被對帳單明細認領，解除認領
        # （否則該列永遠顯示已勾銷、指向不存在的收支）
        from sqlalchemy import update as _saupdate
        from db.models import BankStatementLine
        await session.execute(_saupdate(BankStatementLine).where(
            BankStatementLine.matched_entry_id == entry_id).values(matched_entry_id=None))
        await session.delete(e)
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
    `payment_status NOT IN ('已收款','作廢')` 過濾，「已付款」不在那個清單裡就被
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
                updated += 1
        await session.commit()
    return {"status": "ok", "updated": updated}

