# -*- coding: utf-8 -*-
"""提案企劃頁 `/proposal-plan.html` 的手機版版面回歸（iPhone 14 尺寸）。

企劃人員主要在手機上用這頁，客戶則是用 `?t=` 連結在手機上看。這裡釘住三條
不變式，每一條都對應一個真的壞過的東西：

  A. 不准有橫向捲動 —— 清單列的固定欄寬曾把整頁撐到 649px（螢幕只有 390）。
  B. 對話框必須完整落在視窗內 —— 版面視窗一被撐大，`position:fixed` 就以
     撐大的寬度置中而偏出畫面，右半截按不到。這是 A 的下游症狀，但**分開
     驗**：以後別的東西撐破版面時，這條會直接指出使用者按不到按鈕的後果。
  C. 表單控件字級 ≥16px（iOS Safari 對更小的欄位一 focus 就把整頁放大）、
     可點目標 ≥44px（Apple HIG / WCAG 2.5.5）、勾選框 ≥24×24（WCAG 2.5.8 —
     手指的空間由列提供，把勾選框撐成 44 只會變成一顆巨無霸方塊）。

只在 dev / 測試資料庫上跑（會建一筆提案再刪掉）。生產庫直接 skip。
"""
import uuid

import httpx
import pytest

MOBILE = {"width": 390, "height": 844}
IOS_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


@pytest.fixture(scope="module")
def admin_token():
    from core.auth import create_token
    return create_token({"sub": "admin", "username": "admin",
                         "access_level": 3, "modules": []})


@pytest.fixture(scope="module")
def _dev_db_only():
    """自建自刪的測試會寫真資料庫 —— 只允許 dev / test 庫。"""
    from config import load_settings
    url = (load_settings().get("database_url") or "")
    db = url.rsplit("/", 1)[-1].split("?")[0].lower()
    if not db or not (db.endswith("_dev") or "test" in db):
        pytest.skip(f"只在 dev/test 資料庫上跑（目前 {db or '未設定'}）")


@pytest.fixture(scope="module")
def proposal(real_server, admin_token, _dev_db_only):
    """一筆提案（含殼專案）+ 一條公開共編連結；跑完刪掉。"""
    base = real_server["base_url"]
    h = {"Authorization": f"Bearer {admin_token}"}
    tag = uuid.uuid4().hex[:6]
    r = httpx.post(f"{base}/api/v1/proposals", headers=h, timeout=60,
                   json={"title": f"手機回歸_{tag}", "ptype": "品牌形象",
                         "budget_range": "80-120萬"})
    if r.status_code >= 400:
        pytest.skip(f"建不出提案（{r.status_code}）：{r.text[:120]}")
    p = r.json()["proposal"]
    tok = httpx.post(f"{base}/api/v1/proposals/{p['id']}/plan/share",
                     headers=h, timeout=60).json()["token"]
    yield {"id": p["id"], "project_id": p["project_id"], "share": tok}
    for i in (p["id"], p["project_id"]):
        httpx.delete(f"{base}/api/v1/proposals/{i}", headers=h, timeout=30)
        httpx.delete(f"{base}/api/v1/crm/projects/{i}", headers=h, timeout=30)


@pytest.fixture(scope="module")
def phone(real_server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        ctx = br.new_context(viewport=MOBILE, device_scale_factor=2,
                             is_mobile=True, has_touch=True, user_agent=IOS_UA)
        yield ctx
        ctx.close()
        br.close()


# ── 三組量測 ────────────────────────────────────────────────
def _overflow(pg):
    return pg.evaluate("""() => {
        const vw = document.documentElement.clientWidth;
        const bad = [];
        for (const el of document.querySelectorAll('body *')) {
            const r = el.getBoundingClientRect();
            if (r.width > 0 && (r.right > vw + 1 || r.left < -1))
                bad.push(`${el.tagName}.${String(el.className||'').slice(0,32)} w=${Math.round(r.width)}`);
        }
        return { vw, sw: document.documentElement.scrollWidth, bad: [...new Set(bad)].slice(0, 6) };
    }""")


def _touch(pg):
    return pg.evaluate("""() => {
        const fields = [], tiny = [], box = [];
        for (const el of document.querySelectorAll('input, select, textarea')) {
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) continue;
            if (parseFloat(getComputedStyle(el).fontSize) < 16)
                fields.push(`${el.tagName}.${String(el.className||'').slice(0,24)}`);
        }
        const sel = 'button, input:not([type=checkbox]):not([type=radio]), select, textarea,'
                  + ' .fv-row, .prop-row, .side-tab, .pf-row, a.plc-tb-btn';
        for (const el of document.querySelectorAll(sel)) {
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) continue;
            if (r.height < 44)
                tiny.push(`${el.tagName}.${String(el.className||'').slice(0,24)} h=${Math.round(r.height)}`);
        }
        for (const el of document.querySelectorAll('input[type=checkbox], input[type=radio]')) {
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) continue;
            if (r.width < 24 || r.height < 24)
                box.push(`${String(el.className||'')} ${Math.round(r.width)}x${Math.round(r.height)}`);
        }
        return { fields: [...new Set(fields)], tiny: [...new Set(tiny)], box: [...new Set(box)] };
    }""")


def _assert_screen(pg, label):
    o = _overflow(pg)
    assert o["sw"] <= o["vw"] + 1, \
        f"{label} 被撐出橫向捲動（scrollWidth={o['sw']} vw={o['vw']}）溢出：{o['bad']}"
    t = _touch(pg)
    assert not t["fields"], f"{label} 有 <16px 的輸入框（iOS 會自動放大）：{t['fields']}"
    assert not t["tiny"], f"{label} 有 <44px 的可點目標：{t['tiny']}"
    assert not t["box"], f"{label} 有 <24×24 的勾選框：{t['box']}"


def _login(pg, base, token):
    pg.goto(f"{base}/proposal-plan.html", timeout=60000)
    pg.evaluate(f"localStorage.setItem('auth_token','{token}')")


# ── 測試 ────────────────────────────────────────────────────
def test_login_screen(real_server, phone):
    pg = phone.new_page()
    try:
        pg.goto(real_server["base_url"] + "/proposal-plan.html", timeout=60000)
        pg.wait_for_timeout(1500)
        _assert_screen(pg, "登入畫面")
    finally:
        pg.close()


def test_proposal_list(real_server, phone, admin_token, proposal):
    pg = phone.new_page()
    try:
        _login(pg, real_server["base_url"], admin_token)
        pg.goto(real_server["base_url"] + "/proposal-plan.html", timeout=60000)
        pg.wait_for_selector(".prop-row", timeout=30000)
        pg.wait_for_timeout(600)
        _assert_screen(pg, "提案清單")
    finally:
        pg.close()


def test_plan_matrix(real_server, phone, admin_token, proposal):
    """矩陣堆疊後每一格要自己說「我在回答哪一個視角」——欄標題只在最上面
    出現一次，捲到第 8 格時早就看不到了。"""
    pg = phone.new_page()
    try:
        _login(pg, real_server["base_url"], admin_token)
        pg.goto(f"{real_server['base_url']}/proposal-plan.html?pid={proposal['id']}",
                timeout=60000)
        pg.wait_for_timeout(3000)
        if pg.eval_on_selector_all(".plc-start [data-tid]", "e=>e.length"):
            pg.eval_on_selector(".plc-start [data-tid]", "e=>e.click()")
            pg.wait_for_selector(".plc-grid", timeout=30000)
            pg.wait_for_timeout(1200)
        cols = pg.eval_on_selector(
            ".plc-grid", "e=>getComputedStyle(e).gridTemplateColumns.split(' ').length")
        assert cols == 1, f"手機上矩陣應該堆疊成單欄（實際 {cols} 欄）"
        assert pg.eval_on_selector(".plc-cell-lens",
                                   "e=>getComputedStyle(e).display !== 'none'"), \
            "堆疊後每格的視角標籤要顯示出來"

        # 還沒填的格子收成一行（顯示提問本身，不是「（空）」），點下去展開。
        # 12 格全空的新企劃原本要捲過一千多 px 才看得到下一段。
        heights = pg.eval_on_selector_all(
            ".plc-cell textarea", "e=>e.map(x=>Math.round(x.getBoundingClientRect().height))")
        assert heights and all(h == 44 for h in heights), \
            f"空格應該收成一行 44px（實際 {heights}）"
        pg.eval_on_selector(".plc-cell textarea", "e=>e.focus()")
        pg.wait_for_timeout(400)
        opened = pg.eval_on_selector(".plc-cell textarea",
                                     "e=>Math.round(e.getBoundingClientRect().height)")
        assert opened > 44, f"點下去要展開回完整高度，看得到整段提問（實際 {opened}px）"

        _assert_screen(pg, "企劃矩陣")
    finally:
        pg.close()


def test_dialogs_stay_in_viewport(real_server, phone, admin_token, proposal):
    """對話框偏出畫面是「版面被撐寬」的下游症狀 —— 分開驗，才看得出後果。"""
    pg = phone.new_page()
    try:
        _login(pg, real_server["base_url"], admin_token)
        pg.goto(real_server["base_url"] + "/proposal-plan.html", timeout=60000)
        pg.wait_for_selector(".prop-row", timeout=30000)
        for btn, sel, label in [("#btn-folders", ".pf, .pdlg", "資產資料夾"),
                                ("#btn-new", ".pdlg", "新提案")]:
            pg.eval_on_selector(btn, "e=>e.click()")
            pg.wait_for_selector(sel, timeout=30000)
            pg.wait_for_timeout(800)
            r = pg.eval_on_selector(".pdlg", """e => {
                const b = e.getBoundingClientRect();
                return { left: Math.round(b.left), right: Math.round(b.right),
                         vw: document.documentElement.clientWidth };
            }""")
            assert r["left"] >= -1 and r["right"] <= r["vw"] + 1, \
                f"{label} 對話框沒有落在畫面內：{r}"
            _assert_screen(pg, f"{label}對話框")
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(400)
    finally:
        pg.close()


def test_guest_share_page(real_server, phone, proposal):
    """客戶最可能就是用手機點開這條連結 —— 而且它沒有登入、沒有工具列，
    版面跟員工那條不完全一樣。"""
    pg = phone.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    try:
        pg.goto(f"{real_server['base_url']}/proposal-plan.html?t={proposal['share']}",
                timeout=60000)
        pg.wait_for_timeout(3500)
        _assert_screen(pg, "客戶公開共編頁")
        assert not errs, f"客戶頁有 JS 例外：{errs[:2]}"
    finally:
        pg.close()
