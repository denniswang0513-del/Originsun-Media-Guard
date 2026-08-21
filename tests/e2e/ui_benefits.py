# -*- coding: utf-8 -*-
"""福利池 Tab：真的在瀏覽器按過一輪（建池 → 動支 → 送審 → 核准 → 匯款）。

為什麼要有這一支：這個 repo 咬過「按鈕點了沒反應」兩次（401 靜默失敗、
暫時死區）。API 全綠不代表那顆按鈕接得上 —— 只有真的點下去才知道。
"""
import json
import sys
import urllib.error
import urllib.request

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
TOK = create_token({"sub": "admin", "username": "admin",
                    "access_level": 3, "modules": []})
H = {"Authorization": "Bearer " + TOK, "Content-Type": "application/json"}
POOL_NAME = "ZZ_UI 福利池測試"
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


def api(m, path, body=None):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + "/api/v1" + path, data=d, method=m, headers=H)
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _purge():
    """只刪自己建的（名字前綴 ZZ_UI）—— 金絲雀鐵則。

    走 DB 而不是 API：已付款的動支**照設計就是刪不掉的**（錢真的出去了，
    產品要求走沖銷）。那是對的行為，不該為了讓測試好收尾去開一個後門端點。
    """
    import asyncio

    async def _do():
        from db.session import init_db, get_session_factory
        from sqlalchemy import select
        from db.models import (CrmCashEntry, CrmPaymentRequest, HrBenefitGrant,
                               HrBenefitPool)
        await init_db()
        async with get_session_factory()() as s:
            pools = (await s.execute(select(HrBenefitPool).where(
                HrBenefitPool.name.like("ZZ\\_UI%", escape="\\")))).scalars().all()
            for pool in pools:
                gs = (await s.execute(select(HrBenefitGrant).where(
                    HrBenefitGrant.pool_id == pool.id))).scalars().all()
                for g in gs:
                    if g.payment_request_id:
                        for c in (await s.execute(select(CrmCashEntry).where(
                                CrmCashEntry.payment_request_id
                                == g.payment_request_id))).scalars().all():
                            await s.delete(c)
                        ap = await s.get(CrmPaymentRequest, g.payment_request_id)
                        if ap:
                            await s.delete(ap)
                    await s.delete(g)
                await s.delete(pool)
            await s.commit()

    asyncio.run(_do())


_purge()
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1600, "height": 1100})
        ctx.add_init_script(f"localStorage.setItem('auth_token', '{TOK}')")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(BASE + "/", wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)

        print("[1] 進得了福利池 tab")
        pg.evaluate("window.switchTab('tab_hr_benefits')")
        pg.wait_for_timeout(3500)
        check(pg.locator("#hb-root").count() > 0, "tab 載入了")
        check("載入失敗" not in pg.inner_text("#hb-content"), "沒有載入失敗訊息")

        print("\n[2] 建池")
        pg.select_option("#hb-year", "2026")
        pg.wait_for_timeout(1500)
        pg.fill("#hb-p-name", POOL_NAME)
        pg.fill("#hb-p-budget", "50000")
        pg.click("button:has-text('新增福利池')")
        pg.wait_for_timeout(2500)
        check(POOL_NAME in pg.inner_text("#hb-content"), "池出現在畫面上")
        check("$50,000" in pg.inner_text("#hb-content"), "編列金額顯示")

        st, d = api("GET", "/crm/benefits/pools?year=2026")
        pool = next((x for x in d["items"] if x["name"] == POOL_NAME), None)
        check(pool is not None, "後端真的建了", pool and pool["id"])

        print("\n[3] 新增一筆動支（併入個人所得）")
        pg.select_option("#hb-g-cat", "生日禮金")
        pg.fill("#hb-g-amount", "3000")
        pg.fill("#hb-g-date", "2026-08-21")
        pg.check("#hb-g-taxable")
        pg.click("button:has-text('新增動支')")
        pg.wait_for_timeout(2500)
        check(not errs, "沒有 JS 例外", errs[:2])
        _, det = api("GET", "/crm/benefits/pools/" + pool["id"])
        g = (det.get("grants") or [{}])[0]
        check(g.get("amount") == 3000, "後端收到 3,000", g.get("amount"))
        check(g.get("taxable") == 1, "🔴 併入個人所得的勾有送出去", g.get("taxable"))
        check(g.get("status") == "草稿", "落在草稿", g.get("status"))

        print("\n[4] 送審 → 待審不扣餘額（畫面上）")
        pg.click("button:has-text('送審')")
        pg.wait_for_timeout(2500)
        txt = pg.inner_text("#hb-content")
        check("審核中 $3,000" in txt, "畫面顯示審核中", "審核中" in txt)
        check("餘額 $50,000" in txt, "🔴 餘額沒被待審扣掉",
              [ln for ln in txt.split("\n") if "餘額" in ln][:1])

        print("\n[5] 核准 → 應付帳款")
        pg.click("button:has-text('核准')")
        pg.wait_for_timeout(2800)
        check(not errs, "核准沒有 JS 例外", errs[:2])
        _, det = api("GET", "/crm/benefits/pools/" + pool["id"])
        g = det["grants"][0]
        check(g["status"] == "已核准", "轉已核准", g["status"])
        check(bool(g["payment_request_id"]), "產了應付款", g["payment_request_id"])
        check("餘額 $47,000" in pg.inner_text("#hb-content"), "餘額扣掉 3,000")

        print("\n[6] 登記匯款 → 落帳")
        pg.click("button:has-text('登記匯款')")
        pg.wait_for_timeout(2800)
        _, det = api("GET", "/crm/benefits/pools/" + pool["id"])
        check(det["grants"][0]["status"] == "已付款", "轉已付款",
              det["grants"][0]["status"])
        check(not errs, "整輪都沒有 JS 例外", errs[:3])

        print("\n[7] 會計交付包預覽")
        pg.click("button:has-text('預覽')")
        pg.wait_for_timeout(2500)
        out = pg.inner_text("#hb-k-out")
        check("併入個人所得" in out and "公司費用" in out, "兩區都畫出來")
        check("$3,000" in out, "金額出現在併入所得區")
        b.close()
finally:
    print("\n[清理]")
    _purge()
    st, d = api("GET", "/crm/benefits/pools?year=2026")
    check(not [x for x in d.get("items", []) if x["name"].startswith("ZZ_UI")],
          "測試池清光")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
