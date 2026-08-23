# -*- coding: utf-8 -*-
"""年度活動的 UI：真的在瀏覽器按一輪（建活動 → 發額度 → 上傳附件 → 員工端看）。

API 全綠但畫面壞掉這件事，這個 repo 咬過兩次（少了 index.html 的 section 殼、
新 tab 的 class 用錯導致整塊空白）。所以每個新區塊都要真的點下去。
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
if ":8000" in BASE:
    sys.path.insert(0, r"C:\OriginsunAgent")

    os.chdir(r"C:\OriginsunAgent")

from tests.e2e._guard import refuse_prod_seed  # noqa: E402

refuse_prod_seed(BASE)   # 這支會種資料 —— 絕不可打到生產

ADMIN = create_token({"sub": "admin", "username": "admin",
                      "access_level": 3, "modules": []})
NAME = "ZZ_UI 年度活動"
FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "fixtures", "einvoice_full.pdf")
fails = []
LEFTOVER = []

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
        st = CrmStaff(id=uuid.uuid4().hex[:16], name="ZZ活動員工", status="在職")
        s.add(st)
        s.add(User(username="zz_uiact", password_hash="x", access_level=1,
                   modules=["me_benefits"], staff_id=st.id))
        await s.commit()
        return st.id

async def _teardown():
    from db.session import init_db, get_session_factory
    from sqlalchemy import select
    from db.models import (CrmStaff, HrBenefitAllowance, HrBenefitEntry,
                           HrBenefitPool, User)
    await init_db()
    async with get_session_factory()() as s:
        for pool in (await s.execute(select(HrBenefitPool).where(
                HrBenefitPool.name.like("ZZ\\_UI%", escape="\\")))).scalars().all():
            for f in (pool.attachments or []):
                try:
                    os.remove(f.get("path") or "")
                except FileNotFoundError:
                    pass
                except OSError as err:
                    LEFTOVER.append(f"{f.get('path')}（{err.__class__.__name__}）")
            for e in (await s.execute(select(HrBenefitEntry).where(
                    HrBenefitEntry.pool_id == pool.id))).scalars().all():
                await s.delete(e)
            for a in (await s.execute(select(HrBenefitAllowance).where(
                    HrBenefitAllowance.pool_id == pool.id))).scalars().all():
                await s.delete(a)
            await s.delete(pool)
        for u in (await s.execute(select(User).where(
                User.username == "zz_uiact"))).scalars().all():
            await s.delete(u)
        for st in (await s.execute(select(CrmStaff).where(
                CrmStaff.name == "ZZ活動員工"))).scalars().all():
            await s.delete(st)
        await s.commit()

asyncio.run(_teardown())
staff_id = asyncio.run(_setup())
EMP = create_token({"sub": "zz_uiact", "username": "zz_uiact",
                    "access_level": 1, "modules": ["me_benefits"]})

try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1500, "height": 1300})
        ctx.add_init_script(f"localStorage.setItem('auth_token', '{ADMIN}')")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(BASE + "/", wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)
        pg.evaluate("window.switchTab('tab_hr_benefits')")
        pg.wait_for_timeout(3500)

        print("[1] 建一個「每人一份」的活動")
        # 等表單真的畫出來再斷言 —— 固定 sleep 撞到池清單還沒回來就會
        # 誤報成「沒有類型選單」（第一次跑就是這樣，不是真的壞掉）
        pg.wait_for_selector("#hb-p-quota", timeout=15000)
        check(pg.locator("#hb-p-quota").count() == 1, "有類型選單")
        pg.fill("#hb-p-name", NAME)
        pg.select_option("#hb-p-quota", "per_person")
        pg.click("button:has-text('新增項目')")
        pg.wait_for_timeout(3000)
        check(not errs, "沒有 JS 例外", errs[:2])
        _, d = api("GET", "/crm/benefits/pools")
        pool = next((x for x in d["items"] if x["name"] == NAME), None)
        check(pool is not None, "後端建了")
        check(pool and pool["quota"] == "per_person", "類型是每人一份",
              pool and pool["quota"])
        txt = pg.inner_text("#hb-content")
        check("每人" in txt, "卡片上標示類型")

        print("")
        print("[2] 每人一份的活動不該出現「撥款進池」")
        check("撥款進池" not in txt, "撥款區沒有出現")
        check("每人額度" in txt, "額度區出現了")

        print("")
        print("[3] 說明 + 期間")
        pg.fill("#hb-p-desc", "健檢方案 A：基礎\n健檢方案 B：進階")
        pg.fill("#hb-p-from", "2026-01-01")
        pg.fill("#hb-p-to", "2026-12-31")
        pg.click("button:has-text('儲存說明與期間')")
        pg.wait_for_timeout(3000)
        check(not errs, "存說明沒有 JS 例外", errs[:2])
        _, d = api("GET", f"/crm/benefits/pools/{pool['id']}")
        check("健檢方案 B" in (d["pool"]["description"] or ""), "說明存進去了")
        check(d["pool"]["valid_from"] == "2026-01-01", "期間存進去了",
              d["pool"]["valid_from"])

        print("")
        print("[4] 上傳附件")
        with pg.expect_file_chooser() as fc:
            pg.click("button:has-text('上傳附件')")
        fc.value.set_files(FIX)
        pg.wait_for_timeout(3500)
        check(not errs, "上傳沒有 JS 例外", errs[:2])
        _, d = api("GET", f"/crm/benefits/pools/{pool['id']}")
        check(len(d["pool"]["attachments"]) == 1, "後端收到附件",
              len(d["pool"]["attachments"]))
        check("einvoice_full.pdf" in pg.inner_text("#hb-content"), "畫面列出來了")

        print("")
        print("[5] 發額度")
        pg.select_option("#hb-a-staff", label="ZZ活動員工")
        pg.fill("#hb-a-amount", "10000")
        pg.click("button:has-text('發給這個人')")
        pg.wait_for_timeout(3000)
        check(not errs, "發額度沒有 JS 例外", errs[:2])
        _, d = api("GET", f"/crm/benefits/pools/{pool['id']}")
        check(len(d["allowances"]) == 1, "後端有一份額度", len(d["allowances"]))
        t = pg.inner_text("#hb-content")
        check("ZZ活動員工" in t, "額度表列出來了")
        check("還沒動用" in t, "有「還沒動用」的統計")
        b.close()

        print("")
        print("[6] 員工端 /my.html 看得到")
    with sync_playwright() as p:
        b2 = p.chromium.launch()
        c2 = b2.new_context(viewport={"width": 1100, "height": 1500})
        c2.add_init_script(f"localStorage.setItem('auth_token', '{EMP}')")
        pg2 = c2.new_page()
        errs2 = []
        pg2.on("pageerror", lambda e: errs2.append(str(e)))
        pg2.goto(BASE + "/my.html", wait_until="domcontentloaded")
        pg2.wait_for_timeout(6000)
        card = pg2.locator('[data-card="benefits"]')
        check(card.count() > 0, "福委會卡在工作台上")
        t2 = card.inner_text() if card.count() else ""
        check("我的額度" in t2, "看得到自己的額度", t2[:120])
        check("我還可以用" in t2, "看得到還可以用多少")
        check("健檢方案 B" in t2, "🔴 說明真的畫出來了（健檢就是靠這個）")
        check("einvoice_full.pdf" in t2, "附件連結出現")
        check("2026-01-01" in t2, "期間出現")
        check(not errs2, "員工端沒有 JS 例外", errs2[:2])
        b2.close()
finally:
    print("")
    print("[清理]")
    asyncio.run(_teardown())
    _, d = api("GET", "/crm/benefits/pools")
    check(not [x for x in d.get("items", []) if x["name"].startswith("ZZ_UI")],
          "測試活動清光")
    check(not LEFTOVER, "磁碟上的附件也清光", LEFTOVER)
    from _benefit_residue import scan
    rest = asyncio.run(scan())
    check(not rest, "🔴 全域殘留掃描（別支漏的也算）", rest)

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
