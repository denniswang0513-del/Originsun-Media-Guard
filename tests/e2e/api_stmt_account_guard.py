# -*- coding: utf-8 -*-
"""對帳單匯入：帳號對不上就擋（owner 2026-08-24 實際踩到）。

他拿**合庫**的對帳單、在下拉選了第一銀行。那次是解析失敗才沒寫進去 —— 純屬僥倖。
匯錯帳戶比解析失敗嚴重得多：解析失敗你看得到，匯錯帳戶會安靜地讓兩個帳戶的
餘額同時錯掉，而且每一列看起來都很正常。

順便釘住同一次修的另一件事：逗號分隔的匯出（一銀網銀的 .txt）要解析得出來。
"""
import asyncio
import json
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"

from tests.e2e._guard import refuse_prod_seed  # noqa: E402

refuse_prod_seed(BASE)   # 這支會建帳戶 —— 絕不可打到生產

T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["money_view", "finance"]})
API = BASE + "/api/v1/finance"
TAG = "ZZ帳號防呆-" + uuid.uuid4().hex[:6]
fails = []
ids = {}

COOP = """歷史交易查詢
類別：TWD_CURRENT 戶名：90371657 源日有限公司 帳號：0070717559515
序號 交易日期 摘要 交易行庫 幣別 提款金額 存款金額 餘額 備註
1 2025/01/08 01:56:40 攤還本息 合庫松山 TWD 4470.00 170538.00 01-08 315614
2 2025/01/08 01:56:40 攤還本息 合庫松山 TWD 25290.00 145248.00 01-08 315611
"""

FIRST_CSV = """交易日期,交易時間,幣別,支出金額,存入金額,餘額,票據號碼,摘要,附註
2025/05/12,15:50:44,新臺幣,-,2000.00,2000.00,,跨行轉帳,0120082120000062728
2025/05/12,16:11:57,新臺幣,1000.00,-,1000.00,,硬體轉出,0070000000000000000
2025/05/14,21:33:51,新臺幣,-,29000.00,30000.00,,跨行轉帳,0120082120000062728
"""


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


def form(path, fields):
    b = uuid.uuid4().hex
    body = ("".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
                    for k, v in fields.items()) + f"--{b}--\r\n").encode("utf-8")
    r = urllib.request.Request(
        API + path, method="POST", data=body,
        headers={"Authorization": "Bearer " + T,
                 "Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=180).read())
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_detail": json.loads(e.read()).get("detail", "")}


async def seed():
    from db.models import BankAccount
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        coop = BankAccount(id=uuid.uuid4().hex[:16], entity="parent",
                           name=TAG + "合庫", acct_kind="bank",
                           account_no="0070717559515", opening_balance=0)
        first = BankAccount(id=uuid.uuid4().hex[:16], entity="parent",
                            name=TAG + "一銀", acct_kind="bank",
                            account_no="13310019211", opening_balance=0)
        nono = BankAccount(id=uuid.uuid4().hex[:16], entity="parent",
                           name=TAG + "沒登記帳號", acct_kind="bank",
                           account_no="", opening_balance=0)
        s.add_all([coop, first, nono])
        await s.commit()
        return {"coop": coop.id, "first": first.id, "nono": nono.id}


async def clean(d):
    from sqlalchemy import delete
    from db.models import BankAccount
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        await s.execute(delete(BankAccount).where(BankAccount.id.in_(list(d.values()))))
        await s.commit()


try:
    ids = asyncio.run(seed())
    print(f"[0] 建了三個帳戶（{TAG}）：合庫 / 一銀 / 沒登記帳號的")

    print("")
    print("[1] 🔴 合庫的對帳單 + 選了一銀 → 擋下來，並說出它其實是誰的")
    r = form("/bank-statement/preview",
             {"bank_account_id": ids["first"], "text": COOP})
    print("    ", str(r.get("_detail", r))[:150])
    check(r.get("_status") == 422, "擋下來了（422）", str(r.get("_status")))
    d = str(r.get("_detail", ""))
    check("0070717559515" in d, "講出對帳單的帳號")
    check(TAG + "合庫" in d, "講出它其實是哪個帳戶的")
    check(TAG + "一銀" in d, "講出你選的是哪個")

    print("")
    print("[2] 選對帳戶就放行")
    r2 = form("/bank-statement/preview",
              {"bank_account_id": ids["coop"], "text": COOP})
    check(not r2.get("_status"), "沒被擋", str(r2.get("_detail", ""))[:80])
    check(r2.get("ok") and len(r2.get("rows") or []) == 2, "照常解析出 2 列",
          str(len(r2.get("rows") or [])))

    print("")
    print("[3] 帳戶沒登記帳號 → 驗不了就放行（不能因為驗不了而擋人做事）")
    r3 = form("/bank-statement/preview",
              {"bank_account_id": ids["nono"], "text": COOP})
    check(not r3.get("_status"), "沒被擋", str(r3.get("_detail", ""))[:80])

    print("")
    print("[4] 對帳單沒印帳號（一銀的 CSV 匯出）→ 驗不了也放行")
    r4 = form("/bank-statement/preview",
              {"bank_account_id": ids["coop"], "text": FIRST_CSV})
    check(not r4.get("_status"), "沒被擋", str(r4.get("_detail", ""))[:80])

    print("")
    print("[5] 🔴 逗號分隔的匯出要解析得出來（原本回「找不到任何交易列」）")
    check(r4.get("ok"), "解析成功", str((r4.get("errors") or [])[:1]))
    rows = r4.get("rows") or []
    check(len(rows) == 3, "解析出 3 列", str(len(rows)))
    check([abs(x["amount"]) for x in rows] == [2000, 1000, 29000],
          "金額對（千分位沒被切壞）", str([x["amount"] for x in rows]))

finally:
    print("")
    print("[清理]")
    if ids:
        asyncio.run(clean(ids))
        print("  已清掉測試帳戶")

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: " + "、".join(fails))
sys.exit(1 if fails else 0)
