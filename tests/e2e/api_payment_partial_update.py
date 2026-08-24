# -*- coding: utf-8 -*-
"""請款單編輯：空的預計付款月存得了，而且沒送的欄位不會被洗掉。

owner 2026-08-24：「我之前記帳錯誤我要調整 但是不給我調整了」——
畫面只吐 `儲存失敗: Input should be a valid string`，看不出是哪一欄。

這支跑**真實端點**，因為兩個修正都只在 API 那一層看得出來：
  ① 前端對空的 month 送 null（crm-utils 的共用收值），planned_month 要收得下。
  ② PUT 是部分更新 —— 編輯面板只送 13 個欄位，needs_invoice / invoice_amount /
     project_label / advance_by / is_advance / advance_returned 沒送就不能被碰。

🔴 ②比①嚴重：那個 422 其實**擋下了一次資料破壞**。只修①的話，等於把一顆會把
   186 張代開單的「需代開／代開金額」清成 0 的存檔鈕交回給使用者。
"""
import io
import json
import sys
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"

from tests.e2e._guard import refuse_prod_seed  # noqa: E402

refuse_prod_seed(BASE)   # 這支會種資料 —— 絕不可打到生產

T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["crm_invoices", "money_view", "finance"]})
H = {"Authorization": "Bearer " + T, "Content-Type": "application/json"}
API = BASE + "/api/v1/crm"
TAG = "ZZPAYPART"
fails = []


def call(path, method="GET", body=None):
    r = urllib.request.Request(API + path, method=method, headers=H,
                               data=json.dumps(body).encode() if body is not None else None)
    try:
        raw = urllib.request.urlopen(r, timeout=60).read()
        return json.loads(raw) if raw else {}, 200
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", "replace"), e.code


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


# 先清乾淨（前一次跑失敗可能留下）
for p in (call("/payments?q=" + TAG)[0] or {}).get("payments", []):
    call("/payments/" + p["id"], "DELETE")

print("[1] 建一張代開請款單（帶滿那六個「編輯面板不會送」的欄位）")
created, code = call("/payments", "POST", {
    "summary": TAG + " 代開測試", "amount": 14490, "category": "發票代開",
    "request_date": "2026-03-19", "payee_name": "王士源", "payee_id": "R123953698",
    "invoice_number": "XG60957061", "invoice_amount": 15750, "needs_invoice": 1,
    "project_label": TAG + "專案標籤", "advance_by": "someone",
    "is_advance": 0, "advance_returned": 0, "planned_month": "2026-04",
})
check(code == 200, "建立成功", str(code) + " " + str(created)[:120])
PID = None
if isinstance(created, dict):
    PID = (created.get("payment") or {}).get("id") or created.get("id")
if not PID:
    print("ALL FAIL（建不起來，後面不用跑了）")
    sys.exit(1)

before, _ = call("/payments/" + PID)
check(before.get("needs_invoice") == 1 and before.get("invoice_amount") == 15750,
      "六個欄位真的存進去了", f"needs_invoice={before.get('needs_invoice')} "
      f"invoice_amount={before.get('invoice_amount')}")

print("")
print("[2] 🔴 照編輯面板的形狀送出：13 個欄位 + 空的預計付款月送 null")
# 這就是 crm-payments.js 的 onSave 會組出來的 payload（enableInlineEdit 對空的
# month 送 null；那六個欄位根本不在表單裡所以不會出現）
edit = {
    "summary": TAG + " 代開測試（改過摘要）", "amount": 14490, "category": "發票代開",
    "payee_type": "", "request_date": "2026-03-19", "payee_name": "王士源",
    "payee_id": "R123953698", "planned_month": None,
    "invoice_number": "XG60957061", "project_id": None, "notes": "",
    "payment_date": None, "payment_status": "應付款", "source_invoice_id": None,
}
res, code = call("/payments/" + PID, "PUT", edit)
check(code == 200, "🔴 空的預計付款月不再擋住存檔", f"HTTP {code} {str(res)[:160]}")

print("")
print("[3] 🔴 沒送的六個欄位要原封不動")
after, _ = call("/payments/" + PID)
check(after.get("summary") == TAG + " 代開測試（改過摘要）", "有改到的欄位確實改了",
      str(after.get("summary")))
check(after.get("planned_month") in ("", None), "預計付款月清空了",
      repr(after.get("planned_month")))
for field, want in (("needs_invoice", 1), ("invoice_amount", 15750),
                    ("project_label", TAG + "專案標籤"), ("advance_by", "someone")):
    check(after.get(field) == want, f"{field} 沒被洗掉",
          f"{after.get(field)!r}（應為 {want!r}）")

print("")
print("[4] 明確送 null 仍然是「清空」，不是「不要動」")
res2, code2 = call("/payments/" + PID, "PUT", {**edit, "invoice_amount": None})
check(code2 == 200, "送得出去", str(code2))
after2, _ = call("/payments/" + PID)
check(after2.get("invoice_amount") is None, "顯式的 null 有清掉",
      repr(after2.get("invoice_amount")))
check(after2.get("needs_invoice") == 1, "同一次裡沒送的還是沒被碰",
      repr(after2.get("needs_invoice")))

print("")
print("[清理]")
_, dc = call("/payments/" + PID, "DELETE")
check(dc == 200, "測試請款單已刪除", str(dc))

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
