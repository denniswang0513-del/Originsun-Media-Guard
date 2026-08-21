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
import urllib.parse
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
LEFTOVER = []      # 清不掉的檔案 —— 收尾要出聲


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
    """清理走共用的 purge —— 七張表一次刪完（含**收支明細**與請款單）。

    🔴 這支本來自己寫了一份，漏了收支明細與請款單：第 9 段會核准＋登記匯款，
    於是每跑一次就在帳上留一筆假的支出與一張孤兒請款單。同樣的漏在
    api_benefit_proof 上直接留進了生產（owner 2026-08-21 自己看到才發現）。
    """
    from _benefit_residue import purge
    LEFTOVER.extend(await purge("ZZ\_my%"))
    LEFTOVER.extend(await purge("ZZ臨時員工"))


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
        print("")
        print("[6] 退回原因看得到 —— 退回是唯一需要員工動手的狀態")
        eid = rows[0]["id"]
        # 🔴 reason 是 **query 參數**不是 body（第一次寫成 body：端點回 200、
        #    原因卻沒被記下來，畫面自然空的 —— 看起來像前端壞了）
        why_text = "ZZ 請補一下發票"
        st, _ = api("POST", f"/crm/benefits/entries/{eid}/reject"
                            f"?reason={urllib.parse.quote(why_text)}")
        check(st == 200, "管理端退回", st)
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(5000)
        txt3 = pg.locator('[data-card="benefits"]').inner_text()
        check("退回" in txt3, "狀態變退回")
        check(why_text in txt3, "🔴 原因真的畫在畫面上", txt3[:200])

        print("")
        print("[7] 我用了多少（池餘額是全公司共用的，回答不了這題）")
        check("池餘額" in txt3, "餘額標示成「池」的")
        check("我在" in txt3 and "已用" in txt3, "看得到自己用了多少")
        _, me2 = api("GET", "/crm/benefits/me", tok=EMP)
        p0 = me2["pools"][0]
        for k in ("mine_used", "mine_pending", "mine_count"):
            check(k in p0, f"後端帶了 {k}")

        print("")
        print("[8] 補心得（退回的可以改，改完自動重新送審）")
        pg.locator('[data-card="benefits"] [data-note]').first.click()
        pg.wait_for_selector('[data-noteedit] textarea', timeout=5000)
        pg.fill('[data-noteedit] textarea', "很好看\n第二行")
        pg.locator('[data-noteedit] [data-save]').click()
        pg.wait_for_timeout(2500)
        check(not errs, "存心得沒有 JS 例外", errs[:2])
        _, me3 = api("GET", "/crm/benefits/me", tok=EMP)
        row = next(e for e in me3["entries"] if e["id"] == eid)
        check(row["has_reflection"] is True, "心得存進去了")
        check("第二行" in (row["reflection"] or ""), "換行沒被吃掉（prompt 會吃掉）",
              repr(row["reflection"])[:50])
        # 🔴 me 那支 PUT 是整筆覆蓋 —— 項目與金額不可以被洗掉
        check(row["title"] == "ZZ 我的電影", "項目沒被洗掉", row["title"])
        check(row["amount"] == 420, "金額沒被洗掉", row["amount"])
        check(row["status"] == "待審", "退回的改完自動重新送審", row["status"])

        print("")
        print("[9] 已付款的不給按鈕（按了才被 409 拒是最差的）")
        api("POST", f"/crm/benefits/entries/{eid}/approve")
        api("POST", f"/crm/benefits/entries/{eid}/pay?payment_date=2026-08-21")
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(5000)
        card2 = pg.locator('[data-card="benefits"]')
        check("已付款" in card2.inner_text(), "狀態是已付款")
        check(card2.locator("[data-up]").count() == 0, "沒有傳單據鈕",
              card2.locator("[data-up]").count())
        check(card2.locator("[data-note]").count() == 0, "沒有寫心得鈕",
              card2.locator("[data-note]").count())

        b.close()
finally:
    print("\n[清理]")
    asyncio.run(_teardown())
    _, d = api("GET", "/crm/benefits/pools")
    check(not [x for x in d.get("items", []) if x["name"].startswith("ZZ_my")],
          "清光")
    check(not LEFTOVER, "磁碟上的檔也清光", LEFTOVER)
    from _benefit_residue import scan
    rest = asyncio.run(scan())
    check(not rest, "🔴 全域殘留掃描（別支漏的也算）", rest)

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
