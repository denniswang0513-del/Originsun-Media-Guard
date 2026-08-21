# -*- coding: utf-8 -*-
"""福委會端到端：撥款進池 → 員工自己登記 → owner 審核 → 進公司請款 → 匯款落帳。

owner 的流程原話：「幾個福利池（快樂／進修），員工登記 → 我審核通過 → 進公司
請款；每年撥一筆錢進池。」這支就照那句話走一遍，並驗到帳上。
全部自己建的資料，跑完刪光（開頭也先清 —— 上一次跑到一半炸掉的殘留會讓斷言互咬）。
"""
import json
import sys
import urllib.error
import urllib.request

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001") + "/api/v1"
IS_DEV = BASE.startswith("http://127.0.0.1:8001")
TOK = create_token({"sub": "admin", "username": "admin",
                    "access_level": 3, "modules": []})
H = {"Authorization": "Bearer " + TOK, "Content-Type": "application/json"}
PREFIX = "ZZ_測試池"
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


def call(m, path, body=None, raw=False):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=d, method=m, headers=H)
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            b = f.read()
            return f.status, (b.decode("utf-8") if raw else json.loads(b or b"{}"))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b or b"{}")
        except Exception:
            return e.code, b.decode("utf-8", "replace")


def purge():
    """只刪自己建的（名字前綴）—— 金絲雀鐵則。已付款的登記走 DB
    （照設計就是刪不掉的，不該為了測試好收尾去開後門端點）。"""
    import asyncio

    async def _do():
        from db.session import init_db, get_session_factory
        from sqlalchemy import select
        from db.models import (CrmCashEntry, CrmPaymentRequest, HrBenefitEntry,
                               HrBenefitFunding, HrBenefitPool)
        await init_db()
        async with get_session_factory()() as s:
            pools = (await s.execute(select(HrBenefitPool).where(
                HrBenefitPool.name.like(PREFIX + "%")))).scalars().all()
            for pool in pools:
                for e in (await s.execute(select(HrBenefitEntry).where(
                        HrBenefitEntry.pool_id == pool.id))).scalars().all():
                    if e.payment_request_id:
                        for c in (await s.execute(select(CrmCashEntry).where(
                                CrmCashEntry.payment_request_id
                                == e.payment_request_id))).scalars().all():
                            await s.delete(c)
                        ap = await s.get(CrmPaymentRequest, e.payment_request_id)
                        if ap:
                            await s.delete(ap)
                    await s.delete(e)
                for f in (await s.execute(select(HrBenefitFunding).where(
                        HrBenefitFunding.pool_id == pool.id))).scalars().all():
                    await s.delete(f)
                await s.delete(pool)
            await s.commit()

    asyncio.run(_do())


if IS_DEV:
    purge()

print("[1] 開兩個池（快樂 / 進修）")
pids = {}
for name in ("快樂", "進修"):
    st, r = call("POST", "/crm/benefits/pools", {"name": f"{PREFIX}{name}"})
    check(st == 200, f"建立「{name}」", st)
    pids[name] = r["pool"]["id"]

print("\n[2] 每年撥一筆錢進池")
for year, amount in ((2025, 20000), (2026, 20000)):
    st, r = call("POST", f"/crm/benefits/pools/{pids['快樂']}/fundings",
                 {"year": year, "amount": amount, "fund_date": f"{year}-07-01"})
    check(st == 200, f"{year} 撥款 {amount:,}", st)
st, r = call("POST", f"/crm/benefits/pools/{pids['快樂']}/fundings",
             {"year": 2026, "amount": -5000})
check(st == 422, "負數撥款被擋", (st, r.get("detail")))

st, r = call("GET", f"/crm/benefits/pools/{pids['快樂']}")
check(r["pool"]["funded"] == 40000, "累計撥款 40,000（跨年度滾動）",
      r["pool"]["funded"])
check(r["pool"]["balance"] == 40000, "還沒花，餘額＝撥款", r["pool"]["balance"])

print("\n[3] 員工自己登記（own-scope，staff_id 從 token 解）")
st, me = call("GET", "/crm/benefits/me")
if st == 409:
    print("  SKIP admin 沒綁人員檔案 —— 改用管理端代登驗流程")
    st, r = call("POST", "/crm/benefits/entries",
                 {"pool_id": pids["快樂"], "title": "ZZ 部門聚餐", "amount": 4130,
                  "spend_date": "2026-08-01"})
    check(st == 200, "代登一筆 4,130", st)
    eid = r["entry"]["id"]
else:
    check(st == 200, "自助端點通", st)
    check(any(p["name"].endswith("快樂") for p in me["pools"]), "看得到開放中的池")
    st, r = call("POST", "/crm/benefits/me/entries",
                 {"pool_id": pids["快樂"], "title": "ZZ 部門聚餐", "amount": 4130,
                  "spend_date": "2026-08-01"})
    check(st == 200, "自己登記一筆 4,130", st)
    eid = r["entry"]["id"]
check(r["entry"]["status"] == "待審", "登記即待審（不用再按送出）",
      r["entry"]["status"])

st, r = call("POST", "/crm/benefits/entries",
             {"pool_id": pids["快樂"], "title": "", "amount": 100})
check(st == 422, "空項目被擋", (st, r.get("detail")))
st, r = call("POST", "/crm/benefits/entries",
             {"pool_id": pids["快樂"], "title": "X", "amount": 0})
check(st == 422, "金額 0 被擋", (st, r.get("detail")))

print("\n[4] 待審不扣餘額")
st, r = call("GET", f"/crm/benefits/pools/{pids['快樂']}")
check(r["pool"]["pending"] == 4130, "審核中 4,130", r["pool"]["pending"])
check(r["pool"]["used"] == 0, "🔴 待審不扣餘額", r["pool"]["used"])
check(r["pool"]["balance"] == 40000, "餘額不動", r["pool"]["balance"])

print("\n[5] 待審佇列（owner 每天要看的）")
st, q = call("GET", "/crm/benefits/entries?status=" + "%E5%BE%85%E5%AF%A9")
check(any(x["id"] == eid for x in q["items"]), "那一筆在待審清單裡")

print("\n[6] 核准 → 進公司請款")
st, r = call("POST", f"/crm/benefits/entries/{eid}/approve")
check(st == 200, "核准成功", st)
ap_id = r["entry"]["payment_request_id"]
check(bool(ap_id), "產了應付款", ap_id)
st, ap = call("GET", f"/crm/payments/{ap_id}")
ap = ap.get("payment") or ap
check(ap.get("payment_status") == "應付款", "進了應付帳款", ap.get("payment_status"))
check(ap.get("category") == "員工福利", "科目軸＝員工福利", ap.get("category"))
check("福委會" in (ap.get("summary") or ""), "摘要看得出是福委會", ap.get("summary"))

st, r2 = call("POST", f"/crm/benefits/entries/{eid}/approve")
check(r2.get("detail") is not None or st == 409, "重按核准被擋", st)

st, r = call("GET", f"/crm/benefits/pools/{pids['快樂']}")
check(r["pool"]["used"] == 4130, "核准後才扣餘額", r["pool"]["used"])
check(r["pool"]["balance"] == 35870, "餘額 35,870", r["pool"]["balance"])

print("\n[7] 退回 → 撤掉幽靈負債")
st, r = call("POST", "/crm/benefits/entries",
             {"pool_id": pids["進修"], "title": "ZZ 奧德賽", "amount": 380})
eid2 = r["entry"]["id"]
call("POST", f"/crm/benefits/entries/{eid2}/approve")
st, d = call("GET", f"/crm/benefits/pools/{pids['進修']}")
ap2 = next(x["payment_request_id"] for x in d["entries"] if x["id"] == eid2)
st, r = call("POST", f"/crm/benefits/entries/{eid2}/reject?reason=ZZ")
check(st == 200, "退回成功", st)
st, gone = call("GET", f"/crm/payments/{ap2}")
check(st == 404, "🔴 應付款被撤掉（沒留幽靈負債）", st)
st, d = call("GET", f"/crm/benefits/pools/{pids['進修']}")
check(d["pool"]["used"] == 0, "餘額跟著回來", d["pool"]["used"])

print("\n[8] 匯款 → 落帳成收支明細（冪等）")
st, r = call("POST", f"/crm/benefits/entries/{eid}/pay?payment_date=2026-08-21")
check(st == 200 and r.get("cash_entries") == 1, "落帳 1 筆", r.get("cash_entries"))
st, r2 = call("POST", f"/crm/benefits/entries/{eid}/pay?payment_date=2026-08-21")
check(st == 409, "重按匯款被狀態機擋", st)
st, ap = call("GET", f"/crm/payments/{ap_id}")
ap = ap.get("payment") or ap
check(ap.get("payment_status") == "已付款", "應付款轉已付款", ap.get("payment_status"))

print("\n[9] 送會計")
st, pkg = call("GET", "/crm/benefits/accounting-package?year=2026")
check(st == 200, "交付包端點", st)
mine = [s for s in pkg["summary"] if s["pool"].startswith(PREFIX)]
happy = next(s for s in mine if s["pool"].endswith("快樂"))
check(happy["funded"] == 20000, "2026 撥款 20,000（只算該年度）", happy["funded"])
check(happy["used"] == 4130, "2026 已用 4,130", happy["used"])
check(happy["balance"] == 15870, "2026 餘額 15,870", happy["balance"])
check(all(e["status"] in ("已核准", "已付款")
          for e in pkg["entries"] if e["pool_name"].startswith(PREFIX)),
      "只收已核准/已付款")

st, csv_text = call("GET", "/crm/benefits/accounting-package.csv?year=2026", raw=True)
check(st == 200 and "撥款" in csv_text and "支出" in csv_text, "CSV 兩區都在", st)
check(csv_text.startswith("\ufeff"), "CSV 有 BOM（Excel 中文才不亂碼）")

print("\n[清理]")
st, r = call("DELETE", f"/crm/benefits/pools/{pids['快樂']}")
check(st == 409, "有錢的池刪不掉（守衛）", st)
if IS_DEV:
    purge()
    st, d = call("GET", "/crm/benefits/pools")
    check(not [x for x in d["items"] if x["name"].startswith(PREFIX)], "測試資料清光")
else:
    print("  (生產：清理另外跑)")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
