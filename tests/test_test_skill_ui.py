"""
test_test_skill_ui.py — Phase 4 Playwright UI for /test skill.

開 chromium headless 對 master uvicorn (port 18002) 跑深化版 flow：

- crm-staff（×2）：人力資源 Tab — fresh staff 空狀態 / 外部演員 chip
- showcase-edit（×3）：dropdown 浮出 / debounce 搜尋＋click 連結 / quick_add modal
- works-admin：作品集管理 Tab（NAS-only，預期 hint 文字）

跑：`pytest tests/test_test_skill_ui.py -v`
首次跑前：`.venv/Scripts/python.exe -m playwright install chromium`
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

from tests.fixtures.test_data import (
    cleanup_all_test_data,
    seed_test_staff,
    seed_test_project,
    seed_test_showcase_with_token,
    make_admin_token,
    TEST_PREFIX,
)


# pytest-asyncio asyncio_mode=auto 會把這個 module 包進 event loop，
# _run() 直接呼叫會炸 "cannot be called from a running event loop"。
# 用 ThreadPoolExecutor 把 coroutine 丟進獨立 thread，那邊有自己的 loop。
def _run(coro):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


# ── Playwright 跳過守門（沒裝 chromium 直接 skip） ──

playwright = pytest.importorskip("playwright.sync_api", reason="Playwright not installed")
from playwright.sync_api import Page


# ── /test 專用 server fixture（port 18002 — 跟 HTTP test 共用） ──

@pytest.fixture(scope="module")
def real_server():
    """Reuse Phase 3 fixture pattern — 60s timeout 給 NAS PG migration。"""
    project_root = str(Path(__file__).resolve().parent.parent)
    env = os.environ.copy()
    env["MOCK_FFMPEG"] = "1"
    env["MOCK_NAS"] = "1"
    env["ORIGINSUN_DISABLE_SELFHEAL"] = "1"  # 沒這行 _self_heal_scheduled_task 在 Session 0 會 4s 後 taskkill server
    # DEVNULL: Windows pipe buffer (64KB) fills with uvicorn logs and deadlocks
    # event loop. Match conftest.py:real_server pattern.
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:io_app",
         "--host", "0.0.0.0", "--port", "18002"],
        cwd=project_root, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base_url = "http://localhost:18002"
    deadline = time.time() + 90
    ready = False
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/api/v1/health", timeout=2.0)
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            pass
        time.sleep(1.0)
    if not ready:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        pytest.skip("master server failed to start within 90s — sandbox or PG issue")
    yield {"base_url": base_url, "_proc": proc}
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module", autouse=True)
def _cleanup(real_server):
    _run(cleanup_all_test_data())
    yield
    _run(cleanup_all_test_data())


# ── Playwright fixture ──

# admin RBAC 需要的 modules（直接從 db/session.py:_DEFAULT_ROLES 抄）。
# 沒掛 crm_staff / website_admin → CRM Tab / Website Tab 整個被 RBAC hide。
_ADMIN_MODULES = [
    "backup", "verify", "transcode", "concat", "report", "transcribe", "tts",
    "drone_meta", "projects",
    "crm_clients", "crm_projects", "crm_quotes", "crm_staff", "crm_invoices",
    "website_admin",
]


@pytest.fixture(scope="module")
def admin_token():
    """簽 admin JWT — 用同一個 jwt_secret，前端塞進 localStorage 就過 RBAC。"""
    return _run(make_admin_token())


@pytest.fixture(scope="module")
def browser_context(admin_token):
    """共用 context — admin auth 透過 init script 注入，所有 test 共用 chromium。"""
    from playwright.sync_api import sync_playwright

    cached_user = json.dumps({
        "username": "admin",
        "role": "admin",
        "access_level": 3,
        "modules": _ADMIN_MODULES,
    })
    init_script = f"""
        try {{
            localStorage.setItem('auth_token', {json.dumps(admin_token)});
            localStorage.setItem('auth_user', {json.dumps(cached_user)});
        }} catch (_) {{}}
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
        )
        # Init script 在每個 page 第一個 script 之前跑 → app.js 讀 localStorage 拿到 admin
        ctx.add_init_script(init_script)
        yield ctx
        ctx.close()
        browser.close()


@pytest.fixture
def page(browser_context, real_server, request):
    p = browser_context.new_page()
    console_errors: list[str] = []
    network_errors: list[str] = []
    p.on("console", lambda m: console_errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
    p.on("pageerror", lambda exc: console_errors.append(f"pageerror: {exc}"))
    p.on("response", lambda r: network_errors.append(f"{r.status} {r.url}") if r.status >= 400 else None)

    yield p

    # 失敗時截圖 + 印 console / network
    if hasattr(request.node, "rep_call") and request.node.rep_call.failed:
        screenshot_dir = Path("tests/reports/screenshots")
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = screenshot_dir / f"{request.node.name}.png"
        try:
            p.screenshot(path=str(path), full_page=True)
            print(f"\n📸 screenshot: {path}")
        except Exception:
            pass
        if console_errors:
            print(f"console errors: {console_errors[:5]}")
        if network_errors:
            print(f"network errors: {network_errors[:5]}")
    p.close()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


# ── shared helpers ──

def _put_credits(base_url: str, token: str, credits: list) -> None:
    r = httpx.put(
        f"{base_url}/api/v1/crm/public/showcase-edit/{token}",
        json={"credits": credits}, timeout=10,
    )
    assert r.status_code == 200, f"PUT credits failed: {r.status_code} {r.text}"


def _get_credits(base_url: str, token: str) -> list:
    r = httpx.get(
        f"{base_url}/api/v1/crm/public/showcase-edit/{token}", timeout=10,
    )
    assert r.status_code == 200, r.text
    return r.json().get("credits") or []


def _open_showcase_edit(page: Page, base_url: str, token: str) -> None:
    page.goto(f"{base_url}/showcase-edit.html?token={token}", timeout=30_000)
    page.wait_for_load_state("domcontentloaded")
    # 等到至少一個 .credit-name-input 渲染（PUT 完才會有 entry input）
    page.wait_for_selector(".credit-name-input", timeout=10_000)


# ══════════════════════════════════════════════════════════
# Flow 1: CRM 人力資源 Tab — 專案紀錄擴充（admin token 注入後可見）
# ══════════════════════════════════════════════════════════

class TestCrmStaffFlow:
    """注入 admin JWT → /api/v1/crm/staff 拿得到 fixture staff → 渲染 + click."""

    def _open_staff_tab(self, page: Page, base_url: str) -> None:
        page.goto(base_url + "/", timeout=30_000)
        page.wait_for_load_state("domcontentloaded")
        # 等 loadTabs() + RBAC apply → 人力資源 tab button 從 display:none 變可見
        page.wait_for_selector('button#btn_tab_crm_staff', state="visible", timeout=15_000)
        page.locator('button#btn_tab_crm_staff').click()
        # initCrmStaffTab → loadStaff → renderList 完成
        # 預設 #staff-list-body 有「載入中...」.crm-empty placeholder，要等到 fetch 回來重 render
        page.wait_for_function(
            """() => {
                const el = document.getElementById('staff-list-body');
                if (!el) return false;
                if (el.querySelector('.crm-row')) return true;
                const empty = el.querySelector('.crm-empty');
                return empty && !empty.textContent.includes('載入中');
            }""",
            timeout=15_000,
        )

    def test_fresh_staff_shows_empty_record(self, page: Page, real_server):
        """剛 seed 的 staff 點開 → 「專案紀錄」detail tab 應顯示「尚無專案紀錄」。

        驗證 _loadStaffProjects empty-state 路徑（projects=[] + credit_only=[]）。
        """
        s = _run(seed_test_staff(role="演員"))

        # 確認 backend 真的看得到 fixture staff
        tok = _run(make_admin_token())
        verify = httpx.get(f"{real_server['base_url']}/api/v1/crm/staff",
                            headers={"Authorization": f"Bearer {tok}"}, timeout=10)
        names = [x.get("name") for x in verify.json().get("staff", [])]
        assert s["name"] in names, f"backend /staff 沒回 fixture staff，前 20 筆 names={names[:20]}"

        self._open_staff_tab(page, real_server["base_url"])

        # debug: 確認 admin token 真的注入
        ls_token = page.evaluate("() => localStorage.getItem('auth_token') || ''")
        ls_user = page.evaluate("() => localStorage.getItem('auth_user') || ''")
        body_text = page.locator('#staff-list-body').inner_text()
        print(f"\n[DEBUG] localStorage token len={len(ls_token)}, user len={len(ls_user)}")
        print(f"[DEBUG] body length={len(body_text)}, content prefix={body_text[:200]!r}")

        row = page.locator(f'#staff-list-body .crm-row:has-text("{s["name"]}")').first
        if row.count() == 0:
            assert False, (f"fixture staff '{s['name']}' 不在 list；body={body_text[:300]!r}")
        row.dispatch_event("click")
        page.wait_for_timeout(600)

        # 切到「專案紀錄」detail tab — 找 staff detail panel 內的 tab button
        # crm-staff.html: #staff-detail-tabs > button[data-tab="projects"]
        proj_tab = page.locator('#staff-detail-tabs button[data-tab="projects"]').first
        assert proj_tab.count() >= 1, "detail panel 應有「專案紀錄」tab"
        proj_tab.dispatch_event("click")
        page.wait_for_timeout(800)

        # 沒派工 + 沒被掛在 credits → empty state
        empty = page.locator('#staff-detail-projects .crm-empty')
        assert empty.count() >= 1
        assert "尚無專案紀錄" in empty.first.inner_text()

    def test_staff_with_credits_shows_external_actor_chip(self, page: Page, real_server):
        """staff 在某 showcase.credits 出現但無派工 → 「外部演員（無派工）」chip。

        驗證 _loadStaffProjects credit_only_projects 路徑 + crm-stat-chip.info variant。
        """
        s = _run(seed_test_staff(role="演員"))
        proj = _run(seed_test_project())
        sc = _run(seed_test_showcase_with_token(proj["id"]))
        # PUT credits — entry 內含 staff_id
        _put_credits(real_server["base_url"], sc["token"], [{
            "role_id": None, "name_zh": "演員", "name_en": "Cast",
            "entries": [{"duty": "主演", "name": s["name"], "staff_id": s["id"], "resume_url": ""}],
        }])

        self._open_staff_tab(page, real_server["base_url"])

        row = page.locator(f'#staff-list-body .crm-row:has-text("{s["name"]}")').first
        assert row.count() >= 1
        row.dispatch_event("click")
        page.wait_for_timeout(600)

        page.locator('#staff-detail-tabs button[data-tab="projects"]').first.dispatch_event("click")
        page.wait_for_timeout(1000)

        # 看到 chip 區塊（非 empty state）
        chips = page.locator(".crm-stat-chips .crm-stat-chip")
        assert chips.count() >= 2, f"應看到至少 2 個 chip，實際 {chips.count()}"

        # 「外部演員（無派工）」label 應該出現
        external_chip = page.locator('.crm-stat-chip-label:has-text("外部演員")')
        assert external_chip.count() >= 1, "credit_only 不為空時應顯示「外部演員」chip"


# ══════════════════════════════════════════════════════════
# Flow 2: showcase-edit autocomplete combobox（3 個深度層級）
# ══════════════════════════════════════════════════════════

class TestShowcaseEditFlow:
    """token-based 編輯器，不需登入 — 只驗 autocomplete + quick_add 邏輯。"""

    def _setup_token_with_empty_entry(self, base_url: str):
        proj = _run(seed_test_project())
        sc = _run(seed_test_showcase_with_token(proj["id"]))
        # 渲染 entry input 必要 — 需要 block + 至少 1 個 entry
        _put_credits(base_url, sc["token"], [{
            "role_id": None, "name_zh": "演員", "name_en": "Cast",
            "entries": [{"duty": "", "name": "", "resume_url": ""}],
        }])
        return proj, sc["token"]

    def test_dropdown_appears_on_focus(self, page: Page, real_server):
        """基本 — focus name input → dropdown 浮出。"""
        _, token = self._setup_token_with_empty_entry(real_server["base_url"])
        _open_showcase_edit(page, real_server["base_url"], token)

        name_input = page.locator(".credit-name-input").first
        name_input.click()
        page.wait_for_selector(".credit-name-dropdown", timeout=3_000)
        assert page.locator(".credit-name-dropdown").count() >= 1

    def test_dropdown_search_finds_seeded_staff(self, page: Page, real_server):
        """進階 — type → debounce 200ms → dropdown 重新搜尋出 fixture staff item。

        驗證 debounce + searchStaff round-trip + re-render 連動（不含 click pick — 那段
        Playwright dispatch synthetic event 觸不到 inline closure listener，改下一個 test
        用 backend PUT + reload 驗 render 路徑）。
        """
        s = _run(seed_test_staff(role="演員"))
        proj, token = self._setup_token_with_empty_entry(real_server["base_url"])
        base = real_server["base_url"]

        _open_showcase_edit(page, base, token)
        name_input = page.locator(".credit-name-input").first
        name_input.click()
        page.wait_for_selector(".credit-name-dropdown", timeout=3_000)

        # 打 fixture staff name 後綴 — debounce 200ms 後重新搜尋
        q = s["name"][len(TEST_PREFIX):][:5]
        name_input.fill(q)
        page.wait_for_timeout(500)  # debounce 200ms + render + race protection

        # dropdown 應該重新 render，含 fixture staff item
        item = page.locator(f'.credit-staff-item[data-staff-id="{s["id"]}"]').first
        assert item.count() >= 1, f"打字後 dropdown 應該含 fixture staff item（id={s['id']}）"

        # 也驗 dropdown 顯示綠點/白點 + name + role 文字
        item_text = item.inner_text()
        assert s["name"] in item_text, f"dropdown item 應顯示 staff name，實際 {item_text!r}"

    def test_credits_reload_renders_linked_staff(self, page: Page, real_server):
        """以 backend PUT 寫入帶 staff_id 的 credits → 開頁 → 渲染：input 顯示 name + 綠點。

        驗證 _renderBlocks 對於 staff_id linked entry 的 render 路徑（_selectStaff 寫入後的
        後續展示，是 client end 看到的最終狀態）。
        """
        s = _run(seed_test_staff(role="演員"))
        proj = _run(seed_test_project())
        sc = _run(seed_test_showcase_with_token(proj["id"]))
        base = real_server["base_url"]
        token = sc["token"]

        # 直接 PUT 帶 staff_id 的 entry — 模擬已連結狀態
        _put_credits(base, token, [{
            "role_id": None, "name_zh": "演員", "name_en": "Cast",
            "entries": [{"duty": "主演", "name": s["name"], "staff_id": s["id"], "resume_url": ""}],
        }])

        _open_showcase_edit(page, base, token)

        # 驗 1：input value === fixture staff name
        name_input = page.locator(".credit-name-input").first
        assert name_input.input_value() == s["name"], \
            f"linked entry 的 input value 應是 staff name，實際 {name_input.input_value()!r}"

        # 驗 2：綠點 .credit-link-dot 出現（staff_id 已掛上）
        green_dot = page.locator(".credit-link-dot")
        assert green_dot.count() >= 1, "linked entry 應渲染出綠點 .credit-link-dot"

        # 驗 3：backend GET round-trip — 確認 staff_id 真的存進去（不是 PUT 只 echo）
        saved = _get_credits(base, token)
        assert saved[0]["entries"][0]["staff_id"] == s["id"], \
            f"backend credits 應持久化 staff_id={s['id']}，實際 {saved!r}"

    def test_quick_add_modal_creates_and_links(self, page: Page, real_server):
        """quick_add — type 不存在的名字 → 「+ 建立新人員」→ modal → submit → entry 自動掛 staff_id。

        全鏈路：dropdown create item → _openQuickAddModal → /staff_quick_add → _selectStaff。
        """
        proj, token = self._setup_token_with_empty_entry(real_server["base_url"])
        base = real_server["base_url"]
        # 用 unique name 確保 backend search 找不到 → 走 create 路徑
        new_name = f"{TEST_PREFIX}qa_ui_{uuid.uuid4().hex[:6]}"

        _open_showcase_edit(page, base, token)
        name_input = page.locator(".credit-name-input").first
        name_input.click()
        page.wait_for_timeout(300)

        name_input.fill(new_name)
        page.wait_for_timeout(400)  # debounce

        # dropdown 應有 .credit-staff-create item（因為 backend 找不到 → hasQuery 為 true）
        create_item = page.locator(".credit-staff-create").first
        assert create_item.count() >= 1, "陌生名字下 dropdown 應顯示「+ 建立新人員」"

        # mousedown → 開 modal
        create_item.dispatch_event("mousedown")
        page.wait_for_timeout(300)

        modal = page.locator("#staff-quick-add-modal.show")
        assert modal.count() >= 1, "點「+ 建立新人員」應開 modal"

        qa_name = page.locator("#qa-name")
        assert qa_name.input_value() == new_name, "modal 開啟時 #qa-name 應預填使用者輸入的名字"

        # submit — 不選 role（測 default 路徑）
        page.locator("#qa-submit").click()
        page.wait_for_timeout(1500)  # quick_add → _selectStaff → PUT → re-render

        # modal 應已關閉（remove 'show' class）
        assert page.locator("#staff-quick-add-modal.show").count() == 0, \
            "submit 成功後 modal 應自動關閉"

        # input 應該填入新名字（_selectStaff 路徑）
        new_input = page.locator(".credit-name-input").first
        assert new_input.input_value() == new_name

        # backend：credits[0].entries[0].staff_id 應該是新人員的 id（非空）
        saved = _get_credits(base, token)
        sid = (saved or [{}])[0].get("entries", [{}])[0].get("staff_id")
        assert sid, f"quick_add 後 entry 應自動掛上新建 staff_id，實際 {saved!r}"

        # 驗 quick_add 真的有建在 crm_staff（透過搜尋）
        r = httpx.get(
            f"{base}/api/v1/crm/public/showcase-edit/{token}/staff_search",
            params={"q": new_name}, timeout=10,
        )
        assert r.status_code == 200
        ids = [x["id"] for x in r.json().get("items", [])]
        assert sid in ids, f"新建 staff 應出現在 staff_search 結果，items={r.json()}"


# ══════════════════════════════════════════════════════════
# Flow 3: 作品集管理 Tab（NAS-only — 預期 hint）
# ══════════════════════════════════════════════════════════

class TestWorksAdminTab:
    def test_works_admin_shows_load_failure_hint(self, page: Page, real_server):
        """master 沒掛 NAS website-api，作品集管理 Tab 應顯示明確 hint。"""
        page.goto(real_server["base_url"] + "/", timeout=30_000)
        page.wait_for_load_state("domcontentloaded")
        # admin token 已注入 → 官網管理 tab 應該渲染
        page.wait_for_selector('button#btn_tab_website', state="visible", timeout=15_000)
        page.locator('button#btn_tab_website').dispatch_event("click")
        # 等 website tab 載入 + render
        page.wait_for_selector('button[data-subview="works"]', timeout=10_000)
        # subview button 在 .website-nav 裡，可能因 RBAC 還沒展開 → dispatch_event 直接觸發 handler
        page.locator('button[data-subview="works"]').first.dispatch_event("click")
        page.wait_for_timeout(2500)

        # 預期看到 hint — 「無法載入」/「404」/「請在 master」其一
        # （NAS-only endpoint 不在 master 上）
        # 等 fetch error 渲染（_loadWorks 用 await + try-catch → 失敗顯示錯誤）
        page.wait_for_timeout(1500)
        # subview content 在 #website-content + 對應 view 的 div
        body_text = page.locator('#tab_website').inner_text()
        # 至少其中一個 hint pattern 應出現（多檔/多語可能寫法）
        assert any(kw in body_text for kw in ["無法載入", "404", "請在 master", "Failed", "Not Found"]), \
            f"NAS-only endpoint 應顯示不可用 hint，實際 body 前 500 字: {body_text[:500]!r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
