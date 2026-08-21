# -*- coding: utf-8 -*-
"""福委會 Tab：真的在瀏覽器按過一輪（建池 → 撥款 → 代登 → 核准 → 匯款 → 送會計）。

為什麼要有這一支：這個 repo 咬過「按鈕點了沒反應」兩次（401 靜默失敗、暫時死區），
而且新 tab 少了 index.html 的 section 殼時 API 全綠也看不出來。只有真的點下去才知道。
"""
import json
import os
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
POOL_NAME = "ZZ_UI 快樂"
fails = []
LEFTOVER = []      # 刪不掉的單據 —— 結尾要出聲，不能靜默略過


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

    走 DB 而不是 API：已付款的登記**照設計就是刪不掉的**（錢真的出去了，
    產品要求走沖銷）。那是對的行為，不該為了讓測試好收尾去開一個後門端點。
    開頭也要跑一次 —— 上一次跑到一半炸掉的殘留會讓斷言互咬（實戰教訓）。
    """
    import asyncio

    async def _do():
        from db.session import init_db, get_session_factory
        from sqlalchemy import select
        from db.models import (CrmCashEntry, CrmPaymentRequest, HrBenefitEntry,
                               HrBenefitFunding, HrBenefitPool)
        await init_db()
        async with get_session_factory()() as s:
            pools = (await s.execute(select(HrBenefitPool).where(
                HrBenefitPool.name.like("ZZ\\_UI%", escape="\\")))).scalars().all()
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

        print("[1] 進得了福委會 tab")
        pg.evaluate("window.switchTab('tab_hr_benefits')")
        pg.wait_for_timeout(3500)
        check(pg.locator("#hb-root").count() > 0, "tab 載入了")
        check("載入失敗" not in pg.inner_text("#hb-content"), "沒有載入失敗訊息")

        print("\n[2] 開池")
        pg.fill("#hb-p-name", POOL_NAME)
        pg.click("button:has-text('新增福利池')")
        pg.wait_for_timeout(2500)
        check(POOL_NAME in pg.inner_text("#hb-content"), "池出現在畫面上")
        st, d = api("GET", "/crm/benefits/pools")
        pool = next((x for x in d["items"] if x["name"] == POOL_NAME), None)
        check(pool is not None, "後端真的建了", pool and pool["id"])

        print("\n[3] 撥款進池")
        pg.fill("#hb-f-year", "2026")
        pg.fill("#hb-f-amount", "50000")
        pg.click("button:has-text('撥款進池')")
        pg.wait_for_timeout(2500)
        check(not errs, "沒有 JS 例外", errs[:2])
        check("$50,000" in pg.inner_text("#hb-content"), "撥款金額顯示")
        _, det = api("GET", "/crm/benefits/pools/" + pool["id"])
        check(det["pool"]["funded"] == 50000, "後端記了 50,000",
              det["pool"]["funded"])

        print("\n[4] 代員工登記一筆")
        pg.fill("#hb-e-title", "ZZ 部門聚餐")
        pg.fill("#hb-e-amount", "3000")
        pg.fill("#hb-e-date", "2026-08-21")
        pg.click("button:has-text('代員工登記')")
        pg.wait_for_timeout(2500)
        check(not errs, "沒有 JS 例外", errs[:2])
        _, det = api("GET", "/crm/benefits/pools/" + pool["id"])
        e = (det.get("entries") or [{}])[0]
        check(e.get("amount") == 3000, "後端收到 3,000", e.get("amount"))
        check(e.get("status") == "待審", "登記即待審", e.get("status"))

        print("\n[5] 待審不扣餘額（畫面上）")
        txt = pg.inner_text("#hb-content")
        check("審核中 $3,000" in txt, "畫面顯示審核中", "審核中" in txt)
        check("餘額 $50,000" in txt, "🔴 餘額沒被待審扣掉",
              [ln for ln in txt.split("\n") if "餘額" in ln][:1])
        check("待審" in txt and "1 筆" in txt, "待審佇列出現在最上面")

        print("\n[6] 核准 → 進公司請款")
        pg.locator("button:has-text('核准')").first.click()
        pg.wait_for_timeout(2800)
        check(not errs, "核准沒有 JS 例外", errs[:2])
        _, det = api("GET", "/crm/benefits/pools/" + pool["id"])
        e = det["entries"][0]
        check(e["status"] == "已核准", "轉已核准", e["status"])
        check(bool(e["payment_request_id"]), "產了應付款", e["payment_request_id"])
        check("餘額 $47,000" in pg.inner_text("#hb-content"), "餘額扣掉 3,000")

        print("\n[7] 登記匯款 → 落帳")
        pg.locator("button:has-text('登記匯款')").first.click()
        pg.wait_for_timeout(2800)
        _, det = api("GET", "/crm/benefits/pools/" + pool["id"])
        check(det["entries"][0]["status"] == "已付款", "轉已付款",
              det["entries"][0]["status"])
        check(not errs, "整輪都沒有 JS 例外", errs[:3])

        print("\n[8] 送會計預覽")
        pg.click("button:has-text('預覽')")
        pg.wait_for_timeout(2500)
        out = pg.inner_text("#hb-k-out")
        check("撥款" in out and "餘額" in out, "彙總畫出來")
        check("$3,000" in out, "已用金額出現")
        print("")
        print("[9] 在**已付款**那列補單據與心得（owner：「需要有地方可以")
        print("    上傳單據寫心得」—— 匯進來的歷史紀錄就是這個狀態）")
        row = pg.locator("#hb-content tr", has_text="ZZ 部門聚餐").first
        check(row.locator("button:has-text('＋單據')").count() == 1,
              "已付款的列上看得到「＋單據」")
        check(row.locator("button:has-text('＋心得')").count() == 1,
              "已付款的列上看得到「＋心得」")

        fix = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "fixtures", "einvoice_full.pdf")   # tests/fixtures/，不在 e2e/ 底下
        assert os.path.isfile(fix), fix
        with pg.expect_file_chooser() as fc:
            row.locator("button:has-text('＋單據')").first.click()
        fc.value.set_files(fix)
        pg.wait_for_timeout(3000)
        check(not errs, "上傳沒有 JS 例外", errs[:2])
        _, det = api("GET", "/crm/benefits/pools/" + pool["id"])
        ent = det["entries"][0]
        check(ent["has_receipt"] is True, "後端真的收到單據了",
              ent.get("receipt_url"))
        uploaded = ent.get("receipt_url") or ""

        row = pg.locator("#hb-content tr", has_text="ZZ 部門聚餐").first
        row.locator("button:has-text('＋心得')").first.click()
        pg.wait_for_selector("#hb-note-text", timeout=5000)
        pg.fill("#hb-note-text", "大家吃得很開心\n下次換一家")
        pg.click("#hb-note-save")
        pg.wait_for_timeout(3000)
        check(not errs, "存心得沒有 JS 例外", errs[:2])
        _, det = api("GET", "/crm/benefits/pools/" + pool["id"])
        ent = det["entries"][0]
        check(ent["has_reflection"] is True, "後端真的收到心得了")
        check("下次換一家" in (ent.get("reflection") or ""),
              "換行沒被吃掉（prompt 會吃掉，所以才改小視窗）",
              repr(ent.get("reflection"))[:60])
        check(ent["amount"] == 3000, "金額沒被順手改掉", ent["amount"])
        check(ent["status"] == "已付款", "狀態沒被動到", ent["status"])

        txt = pg.inner_text("#hb-content")
        check("單據" in txt and "心得" in txt, "畫面上標示成有了")

        if uploaded:
            try:
                os.remove(uploaded)
            except OSError as err:
                LEFTOVER.append(f"{uploaded}（{err.__class__.__name__}）")

        b.close()
finally:
    print("\n[清理]")
    _purge()
    st, d = api("GET", "/crm/benefits/pools")
    check(not [x for x in d.get("items", []) if x["name"].startswith("ZZ_UI")],
          "測試池清光")
    check(not LEFTOVER, "磁碟上的測試單據也清光", LEFTOVER)

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
