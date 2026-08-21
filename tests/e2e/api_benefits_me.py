# -*- coding: utf-8 -*-
"""員工自助那一端（own-scope）—— 建一個臨時帳號綁到人員檔案，用**他的** token 打。

要驗的重點只有一個：看得到的、改得動的，只有自己的。
跑完把臨時帳號與資料清光。
"""
import asyncio, json, sys, urllib.error, urllib.request
sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token

BASE = "http://127.0.0.1:8001/api/v1"
ADMIN = create_token({"sub": "admin", "username": "admin",
                      "access_level": 3, "modules": []})
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


def call(m, path, body=None, tok=ADMIN):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=d, method=m, headers={
        "Authorization": "Bearer " + tok, "Content-Type": "application/json"})
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
    """建 兩位臨時人員 + 兩個帳號（甲乙），甲綁甲、乙綁乙。"""
    from db.session import init_db, get_session_factory
    from db.models import User, CrmStaff
    import uuid
    await init_db()
    async with get_session_factory()() as s:
        out = {}
        for who in ("甲", "乙"):
            st = CrmStaff(id=uuid.uuid4().hex[:16], name=f"ZZ臨時{who}")
            s.add(st)
            u = User(username=f"zz_tmp_{who}", password_hash="x",
                     access_level=1, modules=["me_benefits"], staff_id=st.id)
            s.add(u)
            out[who] = (st.id, u.username)
        await s.commit()
        return out


async def teardown(pool_id):
    from db.session import init_db, get_session_factory
    from sqlalchemy import select
    from db.models import (User, CrmStaff, HrBenefitEntry, HrBenefitFunding,
                           HrBenefitPool)
    await init_db()
    async with get_session_factory()() as s:
        for e in (await s.execute(select(HrBenefitEntry).where(
                HrBenefitEntry.pool_id == pool_id))).scalars().all():
            await s.delete(e)
        # 🔴 撥款也要刪 —— 漏了它，池刪掉之後撥款會變孤兒留在庫裡
        for f in (await s.execute(select(HrBenefitFunding).where(
                HrBenefitFunding.pool_id == pool_id))).scalars().all():
            await s.delete(f)
        p = await s.get(HrBenefitPool, pool_id)
        if p:
            await s.delete(p)
        for u in (await s.execute(select(User).where(
                User.username.like("zz_tmp_%")))).scalars().all():
            await s.delete(u)
        for st in (await s.execute(select(CrmStaff).where(
                CrmStaff.name.like("ZZ臨時%")))).scalars().all():
            await s.delete(st)
        await s.commit()


people = asyncio.run(setup())
tok = {who: create_token({"sub": u, "username": u, "access_level": 1,
                          "modules": ["me_benefits"]})
       for who, (_sid, u) in people.items()}

st, r = call("POST", "/crm/benefits/pools", {"name": "ZZ_me 快樂"})
pool_id = r["pool"]["id"]
call("POST", f"/crm/benefits/pools/{pool_id}/fundings", {"year": 2026, "amount": 20000})

try:
    print("[1] 甲自己登記")
    st, r = call("POST", "/crm/benefits/me/entries",
                 {"pool_id": pool_id, "title": "ZZ 甲的電影", "amount": 300},
                 tok=tok["甲"])
    check(st == 200, "登記成功", (st, r))
    e_a = r["entry"]["id"]
    check(r["entry"]["staff_name"] == "ZZ臨時甲", "掛在自己名下（從 token 解）",
          r["entry"]["staff_name"])
    check(r["entry"]["status"] == "待審", "登記即待審")

    print("\n[2] 乙自己登記")
    st, r = call("POST", "/crm/benefits/me/entries",
                 {"pool_id": pool_id, "title": "ZZ 乙的課程", "amount": 900},
                 tok=tok["乙"])
    e_b = r["entry"]["id"]
    check(st == 200, "登記成功", st)

    print("\n[3] 🔴 看得到的只有自己的")
    st, mine_a = call("GET", "/crm/benefits/me", tok=tok["甲"])
    ids = {x["id"] for x in mine_a["entries"]}
    check(e_a in ids, "甲看得到自己那筆")
    check(e_b not in ids, "🔴 甲**看不到**乙那筆", [x["title"] for x in mine_a["entries"]])

    print("\n[4] 🔴 改不動別人的")
    st, r = call("PUT", f"/crm/benefits/me/entries/{e_b}",
                 {"title": "被甲改掉", "amount": 1}, tok=tok["甲"])
    check(st == 403, "甲改乙的 → 403", (st, r.get("detail")))
    st, r = call("DELETE", f"/crm/benefits/me/entries/{e_b}", tok=tok["甲"])
    check(st == 403, "甲刪乙的 → 403", st)

    print("\n[5] 自己的改得動")
    st, r = call("PUT", f"/crm/benefits/me/entries/{e_a}",
                 {"title": "ZZ 甲的電影（改）", "amount": 350}, tok=tok["甲"])
    check(st == 200 and r["entry"]["amount"] == 350, "改自己的成功",
          (st, r.get("entry", {}).get("amount")))

    print("\n[6] 核准之後本人就改不動了")
    call("POST", f"/crm/benefits/entries/{e_a}/approve")
    st, r = call("PUT", f"/crm/benefits/me/entries/{e_a}",
                 {"title": "再改", "amount": 99999}, tok=tok["甲"])
    check(st == 409, "已核准 → 409（帳上已經掛著一張應付款）",
          (st, r.get("detail")))

    print("\n[7] 退回後改完自動重新送審")
    call("POST", f"/crm/benefits/entries/{e_a}/reject?reason=ZZ")
    st, r = call("PUT", f"/crm/benefits/me/entries/{e_a}",
                 {"title": "ZZ 改好了", "amount": 350}, tok=tok["甲"])
    check(st == 200 and r["entry"]["status"] == "待審", "改完回到待審",
          r.get("entry", {}).get("status"))

    print("\n[8] 沒綁人員檔案的帳號")
    st, r = call("GET", "/crm/benefits/me")      # admin 沒綁
    check(st == 409 and "綁定" in str(r.get("detail", "")),
          "給得出人看得懂的話", (st, r.get("detail")))
finally:
    print("\n[清理]")
    asyncio.run(teardown(pool_id))
    st, d = call("GET", "/crm/benefits/pools")
    check(not [x for x in d["items"] if x["name"].startswith("ZZ_me")], "清光")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
