"""core/payout_share.py — 匯款通知頁（`/p/{短碼}`）上「能出現什麼」的純規則（無 I/O）。

owner 2026-09-11：出納匯完款要通知收款人「這次匯了什麼」，現在是看著清單手打。
做成一條連結，跟報價單 `/q/` 與發票 `/e/` 同一套機制。

## 一條規則

**只顯示「他自己核對得到的東西」。任何只有我們系統裡才知道的，一律不上。**

他要做的事只有一件：拿這頁跟存摺對。所以金額、日期、哪幾筆、誰匯的要在上面；
而我們的帳務與流程（成本分類、報支項目、內部案號、備註、其他收款人）跟他無關。

🔴 **他自己的銀行帳號也不上**。那是他的沒錯，但這條連結是可以被轉傳的 ——
   一頁上同時有「姓名＋帳號＋金額」，轉出去就是一份現成的資料。他不需要從這頁
   知道自己的帳號。身分證同理（而且那是我們為了報稅存的，不是他給這頁看的）。

## 為什麼要快照

DB 那筆是活的：有人事後改了金額、改了案名、把某一列退回應付款，客戶那頁就跟著變，
而他存摺上的數字不會變 —— 畫面與事實自相矛盾，**只有他看得到**。所以按下「產生通知」
的那一刻把要顯示的東西定稿，那頁只讀快照（同 `core.invoice_share` 與報價單快照）。

## 為什麼是「這次匯的那幾筆」而不是「這個月的全部」

owner 2026-09-11：「不用按全部付款，反而是把有匯的列出」。部分匯款、補匯都是常態，
所以通知綁的是**出納勾選的那幾筆**，不是某個月的全部。一次匯款一張通知、一個短碼。
"""
from __future__ import annotations

from datetime import date, datetime

#: 通知頁上會出現的欄位（頂層）。白名單，不是「記得不要加」。
SNAPSHOT_FIELDS = (
    "payee_name",    # 收款人姓名 —— 他要確認這頁是給他的
    "paid_date",     # 匯款日期 —— 對存摺用
    "total",         # 本次匯款金額
    "payer",         # 匯款方（我們的公司名）—— 對得上存摺裡的轉入備註
    "items",         # 明細，每一列只有下面那三格
)

#: 明細每一列上會出現的欄位。
ITEM_FIELDS = (
    "summary",       # 項目（他做的事）
    "project",       # 案名 —— 他做過的案，本來就知道
    "amount",
)

#: 🔴 絕對不能出現在那頁上的欄位。這份清單的用途是給測試**逐欄斷言**
#: 「就算輸入裡有，輸出也不能有」—— 白名單漏寫、或有人手滑把欄位加進
#: SNAPSHOT_FIELDS，都會在這裡被抓到。
NEVER_SHARE = (
    "bank_account",    # 他自己的帳號 —— 連結可被轉傳，見檔頭
    "bank_code",
    "bank_name",
    "payee_id",        # 身分證 —— 我們為了報稅存的
    "category",        # 成本分類 —— 我們的會計科目
    "payee_type",      # 報支項目（勞報／現金／內部人員）—— 我們要附什麼單
    "payment_status",  # 應付款／已付款 —— 我們的流程狀態
    "planned_month",   # 我們排的付款月
    "project_id",      # 內部案號
    "entity",          # 母帳／私帳 —— 內部帳務結構
    "notes",           # 內部備註 —— 什麼都可能寫在裡面
    "advance_by",      # 費用歸屬 —— 內部
    "payout_id",
    "id",              # 內部主鍵
)


def _day(v) -> str:
    """date／datetime／字串 → `YYYY-MM-DD`；空的回空字串。"""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v or "").strip()
    return s[:10] if s else ""


def _amount(v) -> int:
    try:
        return int(round(float(v or 0)))
    except (TypeError, ValueError):
        return 0


def build_snapshot(payee_name: str, paid_date, payer: str, items) -> dict:
    """按下「產生通知」的那一刻定稿。`items` 是請款列（ORM 或同形物件）。

    只取 `ITEM_FIELDS` 那三格 —— **不要**把整列 `__dict__` 丟進來，那等於把
    「以後有人加了新欄位」變成潛在外洩（同 `invoice_share._inv_dict` 的理由）。
    """
    rows = []
    for it in items or []:
        rows.append({
            "summary": str(getattr(it, "summary", "") or "").strip(),
            "project": str(getattr(it, "project_label", "") or "").strip(),
            "amount": _amount(getattr(it, "amount", 0)),
        })
    return {
        "payee_name": str(payee_name or "").strip(),
        "paid_date": _day(paid_date),
        "payer": str(payer or "").strip(),
        "total": sum(r["amount"] for r in rows),
        "items": rows,
    }


def share_view(snapshot: dict) -> dict:
    """快照 → 公開端點真正回出去的東西。**再過一次白名單**。

    快照是我們自己寫的，理論上乾淨；但它存在 DB 裡會被改、會被舊版本寫過，
    而這一層是最後一道。兩道都在，才不會因為某一版的 `build_snapshot` 手滑
    就把東西送出去。
    """
    snap = snapshot or {}
    items = []
    for r in (snap.get("items") or []):
        r = r if isinstance(r, dict) else {}
        items.append({k: (_amount(r.get(k)) if k == "amount" else str(r.get(k) or ""))
                      for k in ITEM_FIELDS})
    return {
        "payee_name": str(snap.get("payee_name") or ""),
        "paid_date": _day(snap.get("paid_date")),
        "payer": str(snap.get("payer") or ""),
        "total": _amount(snap.get("total")),
        "items": items,
    }
