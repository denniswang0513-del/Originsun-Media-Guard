# -*- coding: utf-8 -*-
"""批次分類（owner 2026-08-28「我想要有一個可以批次處理分類的按鈕功能」）。

信用卡明細一天好幾筆「街口電支－統一超商」，一筆一筆點三格分類要按上千次。

🔴 這支盯的是**互動**那一半（資料那半在 tests/unit/test_batch_taxonomy.py）：
   ① 批次模式下點格子要選這一列，**不能**開行內編輯器（兩種點擊語意疊在同一格）
   ② 「全選目前清單」要選到篩出來的全部，不是畫面上那 200 列（分批繪製）
   ③ 套用後畫面上那幾格要就地變 —— 不重載 4,700 列
"""
import asyncio
import sys
import uuid
from datetime import datetime

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"

from tests.e2e._guard import refuse_prod_seed  # noqa: E402

refuse_prod_seed(BASE)

T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["money_view", "crm_invoices"]})
TAG = "ZZ批次UI-" + uuid.uuid4().hex[:6]
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra else ""))
    if not ok:
        fails.append(label)


async def seed():
    """4 筆同內容的支出 + 兩顆自己的分類節點。

    🔴 節點要自己種：分類樹的種子只種私帳（`db/seed_cash_taxonomy.ENTITY='mine'`），
    主 SPA 的收支明細是**母公司**那本、樹是空的 —— 不種就只測得到平的那條路。
    只刪自己建的（金絲雀鐵則）。"""
    from db.models import CashTaxonomyNode, CrmCashEntry
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        root = CashTaxonomyNode(id=uuid.uuid4().hex, entity="parent", parent_id="",
                                name=TAG + "類", depth=1, sort=900, active=1)
        kid = CashTaxonomyNode(id=uuid.uuid4().hex, entity="parent", parent_id=root.id,
                               name=TAG + "子", depth=2, sort=0, active=1)
        s.add_all([root, kid])
        ids = []
        for i in range(4):
            e = CrmCashEntry(id=uuid.uuid4().hex[:16], entity="parent",
                             entry_date=datetime(2026, 4, 20 + i), expense=30 + i,
                             summary=f"{TAG} 街口電支－統一超商")   # 內容全部一樣
            s.add(e)
            ids.append(e.id)
        await s.commit()
        return ids, [root.id, kid.id]


async def read(ids):
    from sqlalchemy import select

    from db.models import CrmCashEntry
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        rows = (await s.execute(select(CrmCashEntry).where(
            CrmCashEntry.id.in_(ids)))).scalars().all()
        return [(r.category or "", r.item or "", r.sub_item or "",
                 bool(r.taxonomy_node_id)) for r in rows]


async def clean(ids, nodes):
    from sqlalchemy import delete

    from db.models import CashTaxonomyNode, CrmCashEntry
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        await s.execute(delete(CrmCashEntry).where(CrmCashEntry.id.in_(ids)))
        await s.execute(delete(CashTaxonomyNode).where(CashTaxonomyNode.id.in_(nodes)))
        await s.commit()


def in_thread(coro_fn, *a):
    """🔴 Playwright 的同步 API 佔著 event loop —— 在 `with sync_playwright()`
    區塊裡直接 asyncio.run 會炸 "cannot be called from a running event loop"。"""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(lambda: asyncio.run(coro_fn(*a))).result()


ids, nodes = [], []
try:
    ids, nodes = asyncio.run(seed())
    print(f"[0] 種了 4 筆同內容的信用卡支出（{TAG}）")

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1700, "height": 1200})
        ctx.add_init_script(f"localStorage.setItem('auth_token', '{T}')")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE + "/", wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)
        pg.evaluate("window.switchTab('tab_crm_invoices')")
        pg.wait_for_timeout(3000)
        pg.locator("[data-inv-view='cashbook']").click()
        pg.wait_for_selector("#cash-btn-batch", timeout=25000)
        pg.wait_for_selector("#cash-list-body .crm-row", timeout=25000)
        pg.fill("#cash-search", TAG)
        pg.wait_for_timeout(2500)
        n = pg.locator("#cash-list-body .crm-row").count()
        check(n == 4, "篩出這 4 筆", f"{n} 列")

        print("")
        print("[1] 按下批次分類")
        pg.click("#cash-btn-batch")
        pg.wait_for_timeout(600)
        check(pg.locator("#cash-batch-bar").is_visible(), "批次列出現")
        check(pg.locator("#cash-batch-tax select").count() >= 1, "分類下拉長出來了")

        print("")
        print("[2] 點『類別』那一格 —— 批次模式要選這一列，不是開編輯器")
        pg.locator("#cash-list-body .crm-row").first.locator(".cash-ed").first.click()
        pg.wait_for_timeout(400)
        first = pg.locator("#cash-list-body .crm-row").first
        check("batch-picked" in (first.get_attribute("class") or ""), "那一列被選起來了")
        check(first.locator("select,input").count() == 0,
              "🔴 沒有跳出行內編輯器（不然想選取卻在改資料）")

        print("")
        print("[3] Shift 點最後一列＝選一整段")
        pg.locator("#cash-list-body .crm-row").last.click(modifiers=["Shift"])
        pg.wait_for_timeout(400)
        picked = pg.locator("#cash-list-body .crm-row.batch-picked").count()
        check(picked == 4, "四列全選起來", f"{picked} 列")

        print("")
        print("[4] 清除選取 → 選相同內容")
        pg.click("#cash-batch-none")
        pg.wait_for_timeout(300)
        check(pg.locator("#cash-list-body .crm-row.batch-picked").count() == 0, "清乾淨")
        pg.locator("#cash-list-body .crm-row").first.click()
        pg.click("#cash-batch-same")
        pg.wait_for_timeout(400)
        picked = pg.locator("#cash-list-body .crm-row.batch-picked").count()
        check(picked == 4, "同內容的四列都選起來", f"{picked} 列")

        print("")
        print("[5] 挑分類（自己種的兩層）後套用")

        def pick(idx, name):
            ok = pg.evaluate("""([i, name]) => {
                const sels = document.querySelectorAll('#cash-batch-tax select');
                const s = sels[i];
                if (!s) return 'no-select-' + i;
                const opt = [...s.options].find(o => o.textContent.trim() === name);
                if (!opt) return 'no-option:' + [...s.options].map(o => o.textContent).join('/');
                s.value = opt.value;
                s.dispatchEvent(new Event('change'));
                return 'ok';
            }""", [idx, name])
            check(ok == "ok", f"選到「{name}」", ok)
            pg.wait_for_timeout(400)

        pick(0, TAG + "類")
        pick(1, TAG + "子")
        pg.click("#cash-batch-apply")
        pg.wait_for_timeout(2500)

        rows = in_thread(read, ids)
        print("    DB:", rows)
        want = f"{TAG}類_{TAG}子"
        check(all(r[0] == want and r[3] for r in rows) and len(rows) == 4,
              "四筆都寫進去了（含 taxonomy_node_id）")

        cells = pg.evaluate("""() => [...document.querySelectorAll('#cash-list-body .crm-row')]
            .map(r => [...r.querySelectorAll('.cash-ed')].slice(0, 2).map(c => c.innerText.trim()))""")
        print("    畫面:", cells)
        check(all(c[0] == TAG + "類" and c[1] == TAG + "子" for c in cells),
              "🔴 畫面上那幾格就地變了（沒有整表重載）")

        print("")
        print("[6] 清掉分類")
        pg.click("#cash-batch-all")
        pg.wait_for_timeout(300)
        pg.click("#cash-batch-clear")
        pg.wait_for_timeout(2000)
        rows = in_thread(read, ids)
        check(all(r == ("", "", "", False) for r in rows), "四筆的分類都清掉了", str(rows))

        print("")
        print("[7] 結束批次 → 點格子要恢復成行內編輯")
        pg.click("#cash-batch-exit")
        pg.wait_for_timeout(600)
        check(not pg.locator("#cash-batch-bar").is_visible(), "批次列收起來")
        pg.locator("#cash-list-body .crm-row").first.locator(".cash-ed").first.click()
        pg.wait_for_timeout(500)
        check(pg.locator("#cash-list-body .crm-row").first.locator("select").count() > 0,
              "行內編輯器回來了")

        check(not errs, "沒有 JS 錯誤", "; ".join(errs[:3]))
        b.close()
finally:
    if ids:
        asyncio.run(clean(ids, nodes))

print("")
print(("FAILED: " + "; ".join(fails)) if fails else "ALL PASS")
sys.exit(1 if fails else 0)
