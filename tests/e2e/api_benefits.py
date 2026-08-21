# -*- coding: utf-8 -*-
"""福利池端到端：建池 → 動支 → 送審 → 核准（進應付帳款）→ 匯款（落帳）
→ 會計交付包。全部自己建的資料，跑完刪光。"""
import json
import sys
import urllib.error
import urllib.request

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001") + "/api/v1"
TOK = create_token({"sub": "admin", "username": "admin",
                    "access_level": 3, "modules": []})
H = {"Authorization": "Bearer " + TOK, "Content-Type": "application/json"}
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


print("[0] 選項")
st, opts = call("GET", "/crm/benefits/options")
check(st == 200 and opts.get("categories"), "選項端點", st)
staff_id = (opts.get("staff") or [{}])[0].get("id", "")
staff_name = (opts.get("staff") or [{}])[0].get("name", "")
print("     拿第一位員工當受款人:", staff_name or "(沒有員工資料)")

print("\n[1] 建池")
st, r = call("POST", "/crm/benefits/pools",
             {"name": "ZZ測試 2026 員工福利", "budget": 100000, "year": 2026})
check(st == 200, "建立成功", st)
pool = r.get("pool", {})
pid = pool.get("id")
check(pool.get("balance") == 100000, "空池餘額＝編列", pool.get("balance"))

print("\n[2] 三筆動支（併入所得 / 公司費用 / 之後要退回的）")
gids = []
for cat, amount, taxable, kind in (("生日禮金", 2000, 1, "給付"),
                                   ("健康檢查", 8000, 0, "核銷"),
                                   ("員工旅遊", 5000, 0, "核銷")):
    st, r = call("POST", f"/crm/benefits/pools/{pid}/grants",
                 {"staff_id": staff_id, "category": cat, "amount": amount,
                  "taxable": taxable, "kind": kind, "grant_date": "2026-08-21"})
    check(st == 200, f"建立 {cat} {amount}", st)
    gids.append(r.get("grant", {}).get("id"))

st, r = call("GET", f"/crm/benefits/pools/{pid}")
check(r["pool"]["used"] == 0, "草稿不吃預算", r["pool"]["used"])
check(r["pool"]["balance"] == 100000, "餘額還是滿的", r["pool"]["balance"])

print("\n[3] 欄位守衛")
st, r = call("POST", f"/crm/benefits/pools/{pid}/grants",
             {"staff_id": staff_id, "category": "加薪", "amount": 100})
check(st == 422, "沒有的項目被擋", (st, r.get("detail")))
st, r = call("POST", f"/crm/benefits/pools/{pid}/grants",
             {"staff_id": staff_id, "category": "生日禮金", "amount": 0})
check(st == 422, "金額 0 被擋", (st, r.get("detail")))

print("\n[4] 送審 → 待審不扣餘額")
for g in gids:
    call("POST", f"/crm/benefits/grants/{g}/submit")
st, r = call("GET", f"/crm/benefits/pools/{pid}")
check(r["pool"]["pending"] == 15000, "審核中 15,000", r["pool"]["pending"])
check(r["pool"]["used"] == 0, "🔴 待審不扣餘額", r["pool"]["used"])
check(r["pool"]["balance"] == 100000, "餘額不動", r["pool"]["balance"])

print("\n[5] 核准 → 產應付款進應付帳款")
st, r = call("POST", f"/crm/benefits/grants/{gids[0]}/approve")
check(st == 200, "核准成功", st)
ap_id = r["grant"]["payment_request_id"]
check(bool(ap_id), "產了應付款", ap_id)
st, ap = call("GET", f"/crm/payments/{ap_id}")
ap = ap.get("payment") or ap
check(ap.get("payment_status") == "應付款", "應付款狀態", ap.get("payment_status"))
check(ap.get("category") == "員工福利", "科目軸＝員工福利", ap.get("category"))
check("併入個人所得" in (ap.get("notes") or ""), "備註標了併入所得", ap.get("notes"))

st, r2 = call("POST", f"/crm/benefits/grants/{gids[0]}/approve")
check(r2.get("detail") is not None or st == 409, "重按核准被擋（狀態機）", st)

st, r = call("GET", f"/crm/benefits/pools/{pid}")
check(r["pool"]["used"] == 2000, "已核准開始吃預算", r["pool"]["used"])
check(r["pool"]["balance"] == 98000, "餘額 98,000", r["pool"]["balance"])

print("\n[6] 已核准不准改 / 不准刪")
st, r = call("PUT", f"/crm/benefits/grants/{gids[0]}",
             {"category": "生日禮金", "amount": 999999, "kind": "給付", "taxable": 1})
check(st == 409, "已核准不能改金額", (st, r.get("detail")))
st, r = call("DELETE", f"/crm/benefits/grants/{gids[0]}")
check(st == 409, "已核准不能刪", st)

print("\n[7] 退回 → 撤掉幽靈負債")
call("POST", f"/crm/benefits/grants/{gids[2]}/approve")
st, r = call("GET", f"/crm/benefits/pools/{pid}")
before = r["pool"]["used"]
st, g2 = call("GET", "/crm/benefits/pools/" + pid)
ap2 = next(x["payment_request_id"] for x in g2["grants"] if x["id"] == gids[2])
st, r = call("POST", f"/crm/benefits/grants/{gids[2]}/reject?reason=ZZ%E6%B8%AC%E8%A9%A6%E9%80%80%E5%9B%9E")
check(st == 200, "退回成功", st)
st, gone = call("GET", f"/crm/payments/{ap2}")
check(gone == 404 or st == 404, "🔴 應付款被撤掉（沒留幽靈負債）", st)
st, r = call("GET", f"/crm/benefits/pools/{pid}")
check(r["pool"]["used"] == before - 5000, "餘額跟著回來", r["pool"]["used"])

print("\n[8] 匯款 → 落帳成收支明細（冪等）")
st, r = call("POST", f"/crm/benefits/grants/{gids[0]}/pay?payment_date=2026-08-21")
check(st == 200 and r.get("cash_entries") == 1, "落帳 1 筆", r.get("cash_entries"))
st, r2 = call("POST", f"/crm/benefits/grants/{gids[0]}/pay?payment_date=2026-08-21")
check(st == 409, "重按匯款被狀態機擋", st)

st, ap = call("GET", f"/crm/payments/{ap_id}")
ap = ap.get("payment") or ap
check(ap.get("payment_status") == "已付款", "應付款轉已付款", ap.get("payment_status"))

print("\n[9] 會計交付包")
call("POST", f"/crm/benefits/grants/{gids[1]}/approve")
st, pkg = call("GET", "/crm/benefits/accounting-package?year=2026")
check(st == 200, "交付包端點", st)
pi = pkg["personal_income"]
ce = pkg["company_expense"]
mine_pi = [r for r in pi["rows"] if r["pool_name"].startswith("ZZ測試")]
mine_ce = [r for r in ce["rows"] if r["pool_name"].startswith("ZZ測試")]
check(len(mine_pi) == 1 and mine_pi[0]["amount"] == 2000,
      "併入所得區＝生日禮金 2,000", [r["amount"] for r in mine_pi])
check(len(mine_ce) == 1 and mine_ce[0]["amount"] == 8000,
      "公司費用區＝健檢 8,000", [r["amount"] for r in mine_ce])
check(mine_pi[0].get("id_number") is not None, "併入所得帶身分證欄（扣繳憑單用）")
check("id_number" not in json.dumps(
    [x for x in call("GET", f"/crm/benefits/pools/{pid}")[1]["grants"]]),
    "🔴 一般清單不帶身分證（PII 單一正本）")
check(all(r["status"] in ("已核准", "已付款") for r in mine_pi + mine_ce),
      "只收已核准/已付款")

st, csv_text = call("GET", "/crm/benefits/accounting-package.csv?year=2026", raw=True)
check(st == 200 and "併入個人所得" in csv_text and "公司費用" in csv_text,
      "CSV 兩區都在", st)
check(csv_text.startswith("\ufeff"), "CSV 有 BOM（Excel 中文才不亂碼）")

print("\n[清理]")
st, r = call("DELETE", f"/crm/benefits/pools/{pid}")
check(st == 409, "有動支的池刪不掉（守衛）", st)
# 逐筆退回→刪除；已付款那筆連同它的收支列一起收
st, d = call("GET", f"/crm/benefits/pools/{pid}")
import asyncio  # noqa: E402


async def _purge(pool_id):
    from db.session import init_db, get_session_factory
    from sqlalchemy import select
    from db.models import (CrmCashEntry, CrmPaymentRequest, HrBenefitGrant,
                           HrBenefitPool)
    await init_db()
    async with get_session_factory()() as s:
        gs = (await s.execute(select(HrBenefitGrant).where(
            HrBenefitGrant.pool_id == pool_id))).scalars().all()
        for g in gs:
            if g.payment_request_id:
                ce = (await s.execute(select(CrmCashEntry).where(
                    CrmCashEntry.payment_request_id == g.payment_request_id))).scalars().all()
                for c in ce:
                    await s.delete(c)
                ap = await s.get(CrmPaymentRequest, g.payment_request_id)
                if ap:
                    await s.delete(ap)
            await s.delete(g)
        p = await s.get(HrBenefitPool, pool_id)
        if p:
            await s.delete(p)
        await s.commit()
        left = (await s.execute(select(HrBenefitGrant).where(
            HrBenefitGrant.pool_id == pool_id))).scalars().all()
        return len(left)


if BASE.startswith("http://127.0.0.1:8001"):
    check(asyncio.run(_purge(pid)) == 0, "測試資料清光")
else:
    print("  (生產：清理另外跑)")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
