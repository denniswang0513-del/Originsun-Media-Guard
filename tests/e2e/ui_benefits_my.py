# -*- coding: utf-8 -*-
"""/my.html 的福委會卡片：員工真的在瀏覽器上登記一筆。

為什麼單獨一支：這張卡是唯一「員工自己會碰」的入口，而 my.html 有自己的
一套 helper 與 class（mfetch / .pf-edit / .inline-row）—— 用錯就是畫面空白
或送不出去，API 全綠完全看不出來。跑完把臨時帳號與資料清光。
"""
import asyncio
import json
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
ADMIN = create_token({"sub": "admin", "username": "admin",
                      "access_level": 3, "modules": []})
POOL_NAME = "ZZ_my 快樂"
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


def api(m, path, body=None, tok=ADMIN):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + "/api/v1" + path, data=d, method=m, headers={
        "Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


async def _setup():
    from db.session import init_db, get_session_factory
    from db.models import CrmStaff, User
    await init_db()
    async with get_session_factory()() as s:
        st = CrmStaff(id=uuid.uuid4().hex[:16], name="ZZ臨時員工")
        s.add(st)
        s.add(User(username="zz_my_tmp", password_hash="x", access_level=1,
                   modules=["me_benefits"], staff_id=st.id))
        await s.commit()
        return st.id


async def _teardown():
    from db.session import init_db, get_session_factory
    from sqlalchemy import select
    from db.models import (CrmStaff, HrBenefitEntry, HrBenefitFunding,
                           HrBenefitPool, User)
    await init_db()
    async with get_session_factory()() as s:
        pools = (await s.execute(select(HrBenefitPool).where(
            HrBenefitPool.name.like("ZZ\\_my%", escape="\\")))).scalars().all()
        for pool in pools:
            for e in (await s.execute(select(HrBenefitEntry).where(
                    HrBenefitEntry.pool_id == pool.id))).scalars().all():
                await s.delete(e)
            for f in (await s.execute(select(HrBenefitFunding).where(
                    HrBenefitFunding.pool_id == pool.id))).scalars().all():
                await s.delete(f)
            await s.delete(pool)
        for u in (await s.execute(select(User).where(
                User.username == "zz_my_tmp"))).scalars().all():
            await s.delete(u)
        for st in (await s.execute(select(CrmStaff).where(
                CrmStaff.name == "ZZ臨時員工"))).scalars().all():
            await s.delete(st)
        await s.commit()


asyncio.run(_teardown())      # 先清上一次的殘留
staff_id = asyncio.run(_setup())
_, r = api("POST", "/crm/benefits/pools", {"name": POOL_NAME})
pool_id = r["pool"]["id"]
api("POST", f"/crm/benefits/pools/{pool_id}/fundings",
    {"year": 2026, "amount": 20000})
EMP = create_token({"sub": "zz_my_tmp", "username": "zz_my_tmp",
                    "access_level": 1, "modules": ["me_benefits"]})

try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1200, "height": 1400})
        ctx.add_init_script(f"localStorage.setItem('auth_token', '{EMP}')")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(BASE + "/my.html", wait_until="domcontentloaded")
        pg.wait_for_timeout(5000)

        print("[1] 卡片出現")
        card = pg.locator('[data-card="benefits"]')
        check(card.count() > 0, "福委會卡在工作台上", card.count())
        txt = card.inner_text() if card.count() else ""
        check("載入失敗" not in txt, "沒有載入失敗", txt[:60])
        check(POOL_NAME.split()[-1] in txt or "餘額" in txt, "看得到池餘額", txt[:80])

        print("\n[2] 表單真的畫得出來（class 用對了才看得到）")
        for sel in ("#mb-pool", "#mb-title", "#mb-amount", "#mb-date", "#mb-add"):
            check(pg.locator(sel).count() > 0, f"{sel} 在", pg.locator(sel).count())
        check(pg.locator("#mb-title").is_visible(), "輸入框看得見（不是空白區塊）")

        print("\n[3] 送出一筆")
        pg.fill("#mb-title", "ZZ 我的電影")
        pg.fill("#mb-amount", "420")
        pg.click("#mb-add")
        pg.wait_for_timeout(2500)
        check(not errs, "沒有 JS 例外", errs[:2])
        check(pg.locator("#mb-err").inner_text().strip() == "", "沒有錯誤訊息",
              pg.locator("#mb-err").inner_text())

        _, mine = api("GET", "/crm/benefits/me", tok=EMP)
        rows = [e for e in mine["entries"] if e["title"] == "ZZ 我的電影"]
        check(len(rows) == 1, "後端收到那一筆", len(rows))
        check(rows[0]["amount"] == 420, "金額 420", rows and rows[0]["amount"])
        check(rows[0]["staff_name"] == "ZZ臨時員工", "掛在自己名下",
              rows and rows[0]["staff_name"])
        check(rows[0]["status"] == "待審", "待審")

        print("\n[4] 畫面跟著更新")
        pg.wait_for_timeout(800)
        txt2 = pg.locator('[data-card="benefits"]').inner_text()
        check("ZZ 我的電影" in txt2, "自己那筆出現在卡片上")
        check("待審" in txt2, "狀態標出來")

        print("\n[5] 空欄位要出聲（不能靜默）")
        pg.fill("#mb-title", "")
        pg.fill("#mb-amount", "")
        pg.click("#mb-add")
        pg.wait_for_timeout(600)
        check(pg.locator("#mb-err").inner_text().strip() != "", "有錯誤提示",
              pg.locator("#mb-err").inner_text())
        b.close()
finally:
    print("\n[清理]")
    asyncio.run(_teardown())
    _, d = api("GET", "/crm/benefits/pools")
    check(not [x for x in d.get("items", []) if x["name"].startswith("ZZ_my")],
          "清光")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
