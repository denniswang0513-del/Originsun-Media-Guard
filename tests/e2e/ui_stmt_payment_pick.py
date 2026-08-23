# -*- coding: utf-8 -*-
"""對帳單預覽：支出列真的挑得到請款單（owner 2026-08-23）。

owner：「我匯入對帳單時，項目勾請款單時，可以讓我勾是哪一筆請款單的項目來對帳，
像是勾發票那樣」。

api_stmt_payment_pick.py 已經證明資料那條路是通的；這一支證明**畫面上真的做得到**：
  ① 支出列那一格寫「＋ 選請款單」（收入列還是「＋ 選發票」）
  ② 點下去開得出視窗，而且欄位是請款單的（摘要／收款人／未付），不是發票的
  ③ 匯費在這一側是**整列一個**（footer 一格），不是每列一格
  ④ 勾一張、確定 → 那一格顯示出來

🔴 這支不寫任何帳（只到預覽為止，不按匯入），但它會**建請款單**當測試資料，
   所以照樣要過生產防護。
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

refuse_prod_seed(BASE)   # 這支會種資料 —— 絕不可打到生產

T = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                  "modules": ["money_view", "crm_invoices"]})
TAG = "ZZ挑請款單-" + uuid.uuid4().hex[:6]
fails = []

#: 第一銀行「交易明細查詢」版面（見 tests/unit/test_bank_statement.py）——
#: 一列支出、一列存入，好同時看到兩側的格子。
STMT = """
交易日期 交易時間 幣別 支出金額 存入金額 餘額 票據號碼 摘要 備註
2026/08/12 10:00:00 新臺幣 44,000.00 - 156,000.00 跨行轉帳 ZZ測試匯出
2026/08/13 11:00:00 新臺幣 - 12,000.00 168,000.00 跨行轉入 ZZ測試收款
幣別 支出金額總計 存入金額總計
新臺幣 44,000.00 12,000.00
"""

pr_id = None


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


async def seed():
    from db.models import CrmPaymentRequest
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        a = CrmPaymentRequest(
            id=uuid.uuid4().hex[:16], entity="parent", amount=44000,
            summary=f"{TAG}｜攝影師酬勞", payee_name=TAG + "收款人",
            category="專案外包", request_date=datetime(2026, 8, 1),
            payment_status="未付款")
        s.add(a)
        await s.commit()
        return a.id


async def clean(pid):
    from sqlalchemy import delete
    from db.models import CrmPaymentRequest
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        await s.execute(delete(CrmPaymentRequest).where(CrmPaymentRequest.id == pid))
        await s.commit()


try:
    pr_id = asyncio.run(seed())
    print(f"[0] 種了一張 44,000 的未付請款單（{TAG}）")

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
        pg.wait_for_selector("#cash-btn-recon", timeout=20000)
        pg.locator("#cash-btn-recon").click()
        pg.wait_for_timeout(2500)

        print("")
        print("[1] 貼上對帳單 → 預覽")
        # 「上傳對帳單」是面板裡的一顆按鈕，點了才開出貼上用的視窗
        pg.evaluate("window._finRecon.stmtOpen()")
        ta = pg.wait_for_selector("#finbank-stmt-text", timeout=20000)
        check(ta is not None, "找得到貼上對帳單的輸入框")
        pg.locator("#finbank-stmt-text").fill(STMT)
        # 帳戶下拉：選第一個真的有值的選項
        pg.evaluate("""() => {
            const s = document.getElementById('finbank-stmt-acct');
            if (!s) return;
            const o = [...s.options].find(x => x.value);
            if (o) { s.value = o.value; s.dispatchEvent(new Event('change')); }
        }""")
        pg.evaluate("""() => {
            const b = [...document.querySelectorAll('button')]
                .find(x => x.innerText.includes('解析看看'));
            if (b) b.click();
        }""")
        pg.wait_for_selector("#finbank-stmt-tbody tr", timeout=40000)
        rows = pg.locator("#finbank-stmt-tbody tr")
        check(rows.count() >= 2, "解析出兩列", rows.count())

        print("")
        print("[2] 支出列寫「選請款單」，收入列寫「選發票」")
        cells = pg.evaluate("""() => [...document.querySelectorAll('#finbank-stmt-tbody tr')]
            .map(tr => {
                const tds = tr.querySelectorAll('td');
                const amt = (tds[3] || {}).innerText || '';
                const btn = tr.querySelector('td:nth-child(7) button');
                return { amt: amt.trim(), label: btn ? btn.innerText.trim() : '(無按鈕)' };
            })""")
        for c in cells:
            print("   ", c)
        out = next((c for c in cells if "-" in c["amt"] or "−" in c["amt"]), None)
        inc = next((c for c in cells if c is not out), None)
        check(out and "請款單" in out["label"], "支出列 → 選請款單",
              out["label"] if out else "找不到支出列")
        check(inc and "發票" in inc["label"], "收入列 → 選發票",
              inc["label"] if inc else "找不到收入列")

        print("")
        print("[3] 點支出列那一格 → 開得出請款單的視窗")
        idx = cells.index(out)
        pg.evaluate(f"window._finRecon.stmtPickAlloc({idx})")
        pg.wait_for_selector("#finbank-pick-list", timeout=15000)
        head = pg.evaluate(
            "() => document.querySelector('#finbank-pick-modal').innerText")
        for word in ("摘要", "收款人", "未付"):
            check(word in head, f"欄位有「{word}」")
        check("發票號" not in head, "不是發票那一側的欄位")
        check(TAG in head, "剛種的那張請款單出現在清單裡")

        print("")
        print("[4] 🔴 匯費是整列一個（不是每列一格）")
        n_row_fee = pg.evaluate(
            "() => document.querySelectorAll('#finbank-pick-list input[type=number]').length")
        n_foot_fee = pg.evaluate(
            "() => document.querySelectorAll('#finbank-pick-fee').length")
        check(n_foot_fee == 1, "footer 有一格匯費", str(n_foot_fee))
        # 清單裡每列只該有「分配金額」一個數字框（收款側才會有第二個）。
        # 🔴 用勾選框數當列數，不要數 `> div` —— 清單上限 100 列，超過時最後
        #    還有一個「還有 N 張沒顯示」的尾巴 div（實測 100 框 / 101 div）。
        n_rows = pg.evaluate(
            "() => document.querySelectorAll("
            "'#finbank-pick-list input[type=checkbox]').length")
        check(n_rows > 0, "清單真的畫出了列（否則下一條會假通過）", str(n_rows))
        check(n_row_fee == n_rows, "清單每列只有一個數字框（分配金額）",
              f"{n_row_fee} 個框 / {n_rows} 列")

        print("")
        print("[5] 勾一張 → 確定 → 那一格顯示出來")
        pg.evaluate(f"window._finRecon.pickToggle('{pr_id}', true)")
        pg.wait_for_timeout(500)
        foot = pg.evaluate("() => document.getElementById('finbank-pick-foot').innerText")
        check("44,000" in foot, "footer 顯示已分配 44,000", foot[:70])
        check("剛好對上" in foot, "跟這列支出剛好對上", foot[:70])
        pg.evaluate("window._finRecon.pickTake()")
        pg.wait_for_timeout(800)
        label = pg.evaluate(f"""() => {{
            const tr = document.querySelectorAll('#finbank-stmt-tbody tr')[{idx}];
            const b = tr && tr.querySelector('td:nth-child(7) button');
            return b ? b.innerText.trim() : '';
        }}""")
        check(label and "選請款單" not in label, "那一格換成挑好的內容了", label)
        check(not errs, "整輪都沒有 JS 例外", errs[:3])
        b.close()

finally:
    print("")
    print("[清理]")
    if pr_id:
        asyncio.run(clean(pr_id))
        print("  已刪掉測試請款單（這支不寫帳，沒有收支要清）")

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: " + "、".join(fails))
sys.exit(1 if fails else 0)
