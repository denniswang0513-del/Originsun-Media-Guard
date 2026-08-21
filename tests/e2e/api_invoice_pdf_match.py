# -*- coding: utf-8 -*-
"""端到端：上傳「別張發票的 PDF」到某張發票上，端點要回警示但照樣存檔。

需求（Soca 2026-08-21）：一次多張時怕傳錯張。
自己建 ZZ 測試發票，跑完刪掉（連磁碟上的檔一起）。
"""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001") + "/api/v1"
TOK = create_token({"sub": "admin", "username": "admin",
                    "access_level": 3, "modules": []})
FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "fixtures", "einvoice_full.pdf")
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


def call(m, path, body=None):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=d, method=m, headers={
        "Authorization": "Bearer " + TOK, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def upload(invoice_id, path):
    bd = "----zz"
    body = (f"--{bd}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{os.path.basename(path)}\"\r\n"
            "Content-Type: application/pdf\r\n\r\n").encode()
    body += open(path, "rb").read() + f"\r\n--{bd}--\r\n".encode()
    r = urllib.request.Request(
        BASE + f"/crm/invoices/{invoice_id}/file", data=body, method="POST",
        headers={"Authorization": "Bearer " + TOK,
                 "Content-Type": f"multipart/form-data; boundary={bd}"})
    try:
        with urllib.request.urlopen(r, timeout=120) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def mk(**kw):
    payload = {"title": "ZZ_pdfmatch " + uuid.uuid4().hex[:6],
               "invoice_date": "2026-08-14", "payment_type": "收款",
               "entity": "parent", **kw}
    st, r = call("POST", "/crm/invoices", payload)
    assert st == 200, (st, r)
    inv = r.get("invoice") or r
    return inv["id"]


made, files = [], []
try:
    print("[1] PDF 與表單一致 → 不該有警示")
    ok_id = mk(company_name="財團法人台北市文化基金會松山文創園區",
               tax_id="31841680", amount_total=50000,
               invoice_number="DQ45891570")
    made.append(ok_id)
    st, r = upload(ok_id, FIXTURE)
    files.append(r.get("file_url"))
    check(st == 200, "上傳成功", st)
    check(r.get("checked") is True, "有讀到 PDF 內容", r.get("checked"))
    check(r.get("warnings") == [], "沒有警示", r.get("warnings"))

    print("\n[2] 🔴 傳錯張（別家公司、別的金額）→ 要警示，但檔案照樣存")
    bad_id = mk(company_name="國家表演藝術中心國家兩廳院",
                tax_id="00973926", amount_total=12345,
                invoice_number="DQ99999999")
    made.append(bad_id)
    st, r = upload(bad_id, FIXTURE)
    files.append(r.get("file_url"))
    check(st == 200, "🔴 仍然回 200（警示不是閘門）", st)
    check(bool(r.get("file_url")), "檔案照樣存好了", r.get("file_name"))
    w = " ".join(r.get("warnings") or [])
    for word in ("統編", "金額", "發票號碼", "抬頭"):
        check(word in w, f"警示提到{word}")
    print("     警示內容：")
    for x in r.get("warnings") or []:
        print("       ⚠", x)
    st, g = call("GET", f"/crm/invoices/{bad_id}")
    inv = g.get("invoice") or g
    check(bool(inv.get("file_url")), "重讀發票，檔案關聯還在")

    print("\n[3] 同客戶只有金額不同（最像實際會發生的）")
    amt_id = mk(company_name="財團法人台北市文化基金會松山文創園區",
                tax_id="31841680", amount_total=30000,
                invoice_number="DQ45891570")
    made.append(amt_id)
    st, r = upload(amt_id, FIXTURE)
    files.append(r.get("file_url"))
    ws = r.get("warnings") or []
    check(len(ws) == 1 and "金額" in ws[0], "只警示金額那一項", ws)

    print("\n[4] 表單還沒填 → 不要對每張新發票都跳警示")
    empty_id = mk()
    made.append(empty_id)
    st, r = upload(empty_id, FIXTURE)
    files.append(r.get("file_url"))
    check(r.get("warnings") == [], "沒有警示", r.get("warnings"))
    check(r.get("detected_invoice_number") == "DQ45891570",
          "號碼照樣偵測到（既有功能沒壞）", r.get("detected_invoice_number"))
finally:
    print("\n[清理]")
    for i in made:
        call("DELETE", f"/crm/invoices/{i}")
    gone = 0
    for f in files:
        if f and os.path.isfile(f):
            try:
                os.remove(f)
                gone += 1
            except OSError:
                pass
    check(all(call("GET", f"/crm/invoices/{i}")[0] == 404 for i in made),
          f"{len(made)} 張測試發票已刪")
    print(f"     磁碟上的測試檔清掉 {gone} 個")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
