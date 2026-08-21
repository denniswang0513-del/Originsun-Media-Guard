# -*- coding: utf-8 -*-
"""股東往來的 own-scope：綁定之後每位股東只看得到自己的。

owner 2026-08-21：「這三位股東也各有各自的登入帳號 需要綁定」。
綁定＝bank_accounts.staff_id → crm_staff.id。

🔴 這一支要證明的是「甲看不到乙」—— 而且是**查詢的性質**（WHERE staff_id = 我，
staff_id 只從 token 解），不是靠前端少畫幾筆。
自己建 ZZ 帳戶與臨時帳號，跑完刪光。
"""
import asyncio
import json
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001") + "/api/v1"
ADMIN = create_token({"sub": "admin", "username": "admin",
                      "access_level": 3, "modules": []})
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


def call(m, path, body=None, tok=ADMIN):
    d = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(BASE + path, data=d, method=m, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b or b"{}")
        except Exception:
            return e.code, b.decode("utf-8", "replace")


async def setup():
    from db.session import init_db, get_session_factory
    from db.models import CrmStaff, User
    await init_db()
    async with get_session_factory()() as s:
        out = {}
        for who in ("甲", "乙"):
            st = CrmStaff(id=uuid.uuid4().hex[:16], name=f"ZZ股東{who}")
            s.add(st)
            s.add(User(username=f"zz_sh_{who}", password_hash="x", access_level=1,
                       modules=["me_shareholder"], staff_id=st.id))
            out[who] = st.id
        await s.commit()
        return out


async def teardown():
    from db.session import init_db, get_session_factory
    from sqlalchemy import select
    from db.models import BankAccount, CrmCashEntry, CrmStaff, User
    await init_db()
    async with get_session_factory()() as s:
        accts = (await s.execute(select(BankAccount).where(
            BankAccount.name.like("ZZ股東%")))).scalars().all()
        for a in accts:
            for e in (await s.execute(select(CrmCashEntry).where(
                    CrmCashEntry.bank_account_id == a.id))).scalars().all():
                await s.delete(e)
            await s.delete(a)
        for u in (await s.execute(select(User).where(
                User.username.like("zz_sh_%")))).scalars().all():
            await s.delete(u)
        for st in (await s.execute(select(CrmStaff).where(
                CrmStaff.name.like("ZZ股東%")))).scalars().all():
            await s.delete(st)
        await s.commit()


asyncio.run(teardown())          # 先清上次殘留
staff = asyncio.run(setup())
tok = {w: create_token({"sub": f"zz_sh_{w}", "username": f"zz_sh_{w}",
                        "access_level": 1, "modules": ["me_shareholder"]})
       for w in staff}

try:
    print("[1] 建兩個股東往來帳戶並綁人")
    ids = {}
    for w, sid in staff.items():
        st, r = call("POST", "/finance/bank-accounts",
                     {"name": f"ZZ股東{w}", "acct_kind": "shareholder_loan",
                      "opening_balance": 0, "staff_id": sid})
        a = r.get("account") or r
        ids[w] = a.get("id")
        check(st == 200, f"建立 ZZ股東{w}", st)
        check(a.get("staff_id") == sid, "綁定有存進去", a.get("staff_id"))

    print("\n[2] 各記一筆（甲墊付 5,000；乙墊付 900）")
    for w, amt in (("甲", 5000), ("乙", 900)):
        st, r = call("POST", "/crm/cash-entries",
                     {"entry_date": "2026-08-20", "deposit": amt,
                      "summary": f"ZZ {w} 墊付", "category": "轉存",
                      "bank_account_id": ids[w], "entity": "parent"})
        check(st == 200, f"{w} 的往來記了 {amt:,}", st)

    print("\n[3] 🔴 甲只看得到自己的")
    st, mine = call("GET", "/finance/shareholder/me", tok=tok["甲"])
    check(st == 200, "自助端點通", st)
    names = [a["name"] for a in mine.get("accounts", [])]
    check(names == ["ZZ股東甲"], "只有自己那個帳戶", names)
    check(mine["accounts"][0]["owed"] == 5000, "公司欠甲 5,000",
          mine["accounts"][0]["owed"])
    body = json.dumps(mine, ensure_ascii=False)
    check("ZZ股東乙" not in body, "🔴 回應裡完全沒有乙的資料")
    check("900" not in body, "🔴 連乙的金額都沒出現")

    print("\n[4] 乙看到的是乙的")
    st, m2 = call("GET", "/finance/shareholder/me", tok=tok["乙"])
    check([a["name"] for a in m2["accounts"]] == ["ZZ股東乙"], "只有乙")
    check(m2["accounts"][0]["owed"] == 900, "公司欠乙 900", m2["accounts"][0]["owed"])

    print("\n[5] 方向：公司匯還 → 欠款變少")
    call("POST", "/crm/cash-entries",
         {"entry_date": "2026-08-21", "expense": 2000, "summary": "ZZ 匯還甲",
          "category": "轉存", "bank_account_id": ids["甲"], "entity": "parent"})
    st, m3 = call("GET", "/finance/shareholder/me", tok=tok["甲"])
    check(m3["accounts"][0]["owed"] == 3000, "5,000 − 2,000 = 3,000",
          m3["accounts"][0]["owed"])
    deltas = [e["delta"] for e in m3["accounts"][0]["entries"]]
    check(sorted(deltas) == [-2000, 5000], "明細用「公司欠款 +/−」表示", deltas)

    print("\n[6] 沒綁人員的帳號 / 未登入")
    st, r = call("GET", "/finance/shareholder/me")      # admin 沒綁人員
    check(st == 409 and "綁定" in str(r.get("detail", "")), "沒綁 → 409", st)
    st, r = call("GET", "/finance/shareholder/me", tok=None)
    check(st == 401, "未登入 → 401（不是誤導的 409）", st)
finally:
    print("\n[清理]")
    asyncio.run(teardown())
    st, d = call("GET", "/finance/bank-accounts")
    check(not [x for x in d.get("items", []) if x["name"].startswith("ZZ股東")],
          "測試帳戶清光")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
