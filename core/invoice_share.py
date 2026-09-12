"""core/invoice_share.py — 發票分享頁上「能出現什麼」的純規則（無 I/O）。

owner 2026-09-10：`/e/{短碼}` 從「點了直接下載」改成一頁 —— 發票資訊排版 ＋ 下載鈕。
規劃與拍板紀錄見 docs/INVOICE_SHARE_PLAN.md。

## 一條規則

**只顯示「那張發票上本來就印著的東西」。任何只有我們系統裡才知道的，一律不上。**

收件人手上就有那張證明聯，把上面已經有的欄位排版出來不增加他知道的事；而系統裡另外
那半邊（代開費、催收狀態、母帳私帳、內部案號、內部備註）是我們的帳務與流程，跟他無關。

原本的端點註解寫著「不回發票的其他欄位 —— 順手多給金額／統編／客戶名等於把不必要的
東西一起寄出去」。那個顧慮沒有作廢，只是講得太粗：金額與統編本來就印在他手上那張上面。

🔴 **投影寫成白名單而不是黑名單**，而且 `NEVER_SHARE` 另外列一份給測試逐欄釘住。
   靠「記得不要加」是撐不住的 —— 下一個人往回傳多塞一個欄位不會有任何徵兆，
   而錯誤只有**客戶**看得到，我們永遠不會知道。

## 為什麼要快照

檔案是固定的，DB 那筆是活的。有人事後改了金額或補了統編，客戶那頁就跟著變，而他下載到
的 PDF 還是舊的那張 —— 畫面與附件自相矛盾，只有他看得到。`core.invoice_pdf.compare_invoice_pdf`
存在的理由就是這兩邊會不一致。所以**按分享的那一刻把要顯示的欄位定稿**，那頁只讀快照。

🔴 唯一的例外是**作廢**：那是收件人事後必須知道的事，所以 `voided` 走即時值、不進快照。

## 匯款資訊（owner 2026-09-12）

那頁下半多一塊「匯款資訊」（戶名／銀行／帳號／存摺影本下載）。它**不是發票上印著的東西**，
是我們主動給收件人的 —— 所以它不走發票列、不進快照，跟頁尾賣方一樣從設定即時讀
（`settings.company` 的 bank／account_name／account_no；存摺影本是發票根目錄下固定位置的檔）：帳戶哪天換了，
還沒付款的舊發票要顯示新帳戶。**作廢的發票不給匯款資訊**（已作廢還把帳號擺在旁邊等於請人匯錯錢）。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

# 會出現在分享頁上的欄位（＝發票上本來就印著的）
SNAPSHOT_FIELDS = (
    "invoice_number",   # 發票號碼
    "invoice_date",     # 開立日期
    "company_name",     # 買受人抬頭（就是收件人自己）
    "tax_id",           # 統一編號（同上）
    "item_type",        # 品項（實務上就是發票的品名欄）
    "invoice_kind",     # 紙本／電子
    "amount_ex_tax",    # 未稅
    "tax_amount",       # 稅額
    "amount_total",     # 含稅合計
)

# 🔴 絕對不能出現在那頁上的欄位。這份清單的用途是給 `test_invoice_share` 逐欄斷言
# 「就算輸入裡有，輸出也不能有」—— 白名單漏寫成黑名單、或有人手滑把欄位加進
# SNAPSHOT_FIELDS，都會在這裡被抓到。
NEVER_SHARE = (
    "commission",         # 代開費 —— 我們跟開票方之間的事
    "category",           # 專案／內部代開 —— 「內部代開」四個字直接揭露這是代開的
    "payment_status",     # 未收款／已收款 —— 我們的催收狀態
    "paid_date",          # 收款日
    "payment_type",       # 收款／付款
    "entity",             # 母帳／私帳 —— 內部帳務結構
    "applicant",          # 申請人 —— 我方同事姓名
    "project_id",         # 內部案號
    "project_ids",
    "notes",              # 內部備註 —— 什麼都可能寫在裡面
    "title",              # 名稱（案件／項目）—— owner 2026-09-10：可能有人拿它記內部案名
    "recipient",          # 紙本收件人／電話／地址 —— 是對方自己的資料，
    "recipient_phone",    #   但連結一被轉發就等於外洩了它們，而他不需要看
    "recipient_address",
    "id", "share_token", "file_url", "created_at", "updated_at",
)

# 🔴 `issue_status` 是**三值**的（`core.finance_logic.issue_status_for`）：
#    未開立／已開立／作廢。這裡只認「作廢」—— 見 is_voided。
ISSUED = "已開立"
VOID = "作廢"


def public_view(inv: Optional[dict]) -> dict:
    """發票列（dict）→ 只含白名單欄位的投影。空值一律回 `None`。

    空字串正規化成 `None` 是給畫面用的：頁面靠「是不是 None」決定整列畫不畫，
    留一個「統一編號　—」的空格比不畫還難看。
    """
    src = inv or {}
    out = {}
    for k in SNAPSHOT_FIELDS:
        out[k] = _clean(src.get(k))
    return out


def make_snapshot(inv: Optional[dict], file_name: str = "", file_size: int = 0,
                  at=None) -> dict:
    """按下分享時定稿的那一包。存進 `crm_invoices.share_snapshot`（JSONB）。

    檔名與大小也一起存：客戶按下載之前要知道自己會拿到什麼，而那兩個值在檔案被
    換掉之前不會變 —— 換了檔就該重新分享一次（按同一顆鈕就會重新定稿）。
    """
    snap = public_view(inv)
    snap["file"] = {"name": str(file_name or ""), "size": int(file_size or 0)}
    snap["at"] = _iso(at or datetime.now().astimezone())
    return snap


def meta(snapshot: Optional[dict], *, voided: bool = False,
         seller: Optional[dict] = None, bankbook: bool = False) -> dict:
    """分享頁那支 `/meta` 回什麼。

    🔴 `voided` 是**即時**傳進來的，不從快照拿：作廢是收件人事後必須知道的事，
       凍在快照裡等於客戶永遠看不到那張已經作廢了。
    `bankbook`＝存摺影本檔**這台現在開得到**（端點自己去確認），開不到就不畫那一列 ——
    不要鑄一顆按了 404 的下載鈕。
    """
    out = public_view(snapshot)
    f = (snapshot or {}).get("file") or {}
    out["file"] = {"name": str(f.get("name") or ""), "size": int(f.get("size") or 0)}
    out["voided"] = bool(voided)
    # 頁尾的賣方（我們）。從設定來而不是寫死在頁面上 —— 公司抬頭或統編哪天改了，
    # 一個寫死的字串不會跟著動，而那頁是寄給客戶的。
    sell = seller or {}
    out["seller"] = {"name": str(sell.get("name") or ""),
                     "tax_id": str(sell.get("tax_id") or "")}
    # 匯款資訊：作廢不給（見檔頭）；三欄都空也不給（頁面整塊不畫）。
    out["remit"] = None if voided else remit_view(sell, bankbook=bankbook)
    return out


def remit_view(company: Optional[dict], *, bankbook: bool = False) -> Optional[dict]:
    """`settings.company` → 分享頁「匯款資訊」那一塊；戶名／銀行／帳號全空 → None。

    `bankbook` 只是「有沒有存摺影本可下載」的布林，路徑本身不出去（那是內部檔案系統的路徑）。
    """
    c = company or {}
    out = {"account_name": _clean(c.get("account_name")),
           "bank": _clean(c.get("bank")),
           "account_no": _clean(c.get("account_no"))}
    if not any(out.values()):
        return None
    out["bankbook"] = bool(bankbook)
    return out


def is_voided(issue_status) -> bool:
    """只有「作廢」算作廢。

    🔴 不能寫成「已開立以外都算」：`issue_status` 是三值的，而「未開立」
       是 `issue_status_for()` 在**每一次 PUT** 重推出來的 —— 發票號碼還沒填就是它。
       那算作廢的話，一張完好的發票會在客戶那頁頂上長出紅底的「已作廢」，
       而**錯誤只有客戶看得到**（我們這邊的列表寫的是「未開立」，看不出任何異狀）。
       空值同理不算作廢（舊資料沒填）。
    """
    return str(issue_status or "").strip() == VOID


def has_snapshot(snapshot: Optional[dict]) -> bool:
    """這張定稿過了嗎。只認「有檔名」—— 沒有檔就沒有可以給的東西。"""
    return bool(isinstance(snapshot, dict) and (snapshot.get("file") or {}).get("name"))


# ── 內部 ──────────────────────────────────────────────────────

def _clean(v):
    """空字串→None；日期→`YYYY-MM-DD`；其餘原樣。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return v


def _iso(value) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value or "")
