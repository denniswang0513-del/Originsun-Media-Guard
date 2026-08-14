"""
E2E test fixtures using Playwright + real server.
"""
import uuid
from contextlib import contextmanager

import httpx
import pytest
from playwright.sync_api import sync_playwright

# 手機視窗（iPhone 14）—— 幾支測試共用同一組尺寸
MOBILE = {"width": 390, "height": 844}


@pytest.fixture(scope="module")
def e2e_admin_token():
    """E2E 用的 admin token（module 範圍，不吃 tmp_settings）。

    與根 conftest 的 `admin_token` 不同支：那支是 function 範圍、payload 也
    不一樣。名字取得不一樣才不會兩支互相蓋掉。
    """
    from core.auth import create_token
    return create_token({"sub": "admin", "username": "admin",
                         "access_level": 3, "modules": []})


@pytest.fixture(scope="module")
def dev_db_only():
    """🔴 自建自刪的測試會寫真資料庫 —— 只允許 dev / test 庫。

    **這道閘只有這一份。** 之前每支測試各抄一份，而抄壞的後果是把測試資料
    寫進生產庫 —— 這種東西不該有第二個定義。
    """
    from config import load_settings
    url = (load_settings().get("database_url") or "")
    db = url.rsplit("/", 1)[-1].split("?")[0].lower()
    if not db or not (db.endswith("_dev") or "test" in db):
        pytest.skip(f"只在 dev/test 資料庫上跑（目前 {db or '未設定'}）")


@pytest.fixture(scope="session")
def browser():
    """整個 e2e session 只開這一支瀏覽器。

    🔴 `sync_playwright()` 在同一條執行緒裡**只能有一個**：哪個測試檔自己再
    開一支，跟這裡混跑就會整批 error（各自單跑都好好的，所以很容易以為沒事）。
    要別的裝置尺寸就從這支 browser 開新 context（見 test_proposal_plan_mobile
    的 `phone`），不要再 `with sync_playwright()`。
    """
    with sync_playwright() as p:
        # autoplay-policy 是 test_meeting_recorder 的 AudioContext oscillator 要的
        # （它拿真 MediaStream 頂替 getUserMedia —— Windows headless 一個音訊
        # 輸入裝置都沒有）。掛在共用 browser 上讓那支不必自己再開一份；
        # 對其他測試無害（只影響音訊自動播放）。
        br = p.chromium.launch(headless=True, args=[
            "--autoplay-policy=no-user-gesture-required",
        ])
        yield br
        br.close()


@pytest.fixture(scope="session")
def browser_context(browser):
    context = browser.new_context()
    yield context
    context.close()


@pytest.fixture(scope="session")
def page(browser_context, real_server):
    p = browser_context.new_page()
    p.on("console", lambda msg: print(f"[BROWSER {msg.type}] {msg.text}"))
    p.goto(real_server["base_url"] + "/", timeout=60000)
    p.wait_for_load_state("domcontentloaded")
    p.wait_for_timeout(3000)  # Allow dynamic tabs to load
    yield p
    p.close()


@contextmanager
def flow_case(base, token, title):
    """自建自刪一筆提案 + 它的殼專案，回 {prop_id, project_id}。

    寫成 context manager 而不是 fixture：用它的兩支測試檔**範圍不同**
    （推進會改狀態 → function；純渲染 → module），而 fixture 的範圍是宣告
    時綁死的。共用的是「怎麼建、怎麼跳過、怎麼收」這份契約 —— 那才是抄壞
    會把測試資料寫進生產庫的部分。
    """
    h = {"Authorization": f"Bearer {token}"}
    r = httpx.post(f"{base}/api/v1/proposals", headers=h, timeout=60,
                   json={"title": f"{title}_{uuid.uuid4().hex[:6]}", "ptype": "其他"})
    if r.status_code >= 400:
        pytest.skip(f"建不出提案（{r.status_code}）：{r.text[:120]}")
    p = r.json()["proposal"]
    if not p.get("project_id"):
        pytest.skip("這筆提案沒有殼專案")
    try:
        yield {"base": base, "h": h,
               "prop_id": p["id"], "project_id": p["project_id"]}
    finally:
        # 各刪各的 —— 交叉刪（拿提案 id 去打 projects）有機會刪到別人的 dev 資料
        httpx.delete(f"{base}/api/v1/proposals/{p['id']}", headers=h, timeout=30)
        httpx.delete(f"{base}/api/v1/crm/projects/{p['project_id']}",
                     headers=h, timeout=30)


@pytest.fixture
def mount_flow(page):
    """把 flow-view 元件掛到一個臨時 host 上；結束（**含失敗**）一定拆掉。

    不走 SPA 導覽 —— headless 下的分頁點擊 flaky（v2.4.0 的教訓：改成直接
    import 子視圖模組測）。

    🔴 teardown 收在這裡而不是每個測試的最後一行：`page` 是 session 範圍的，
    留下的 host 帶著已綁的事件委派會跑進別支測試檔 —— 而失敗時最需要清理，
    正是「最後一行」跑不到的時候。
    """
    made = []

    def _do(project_id, token, *, host_id="flowhost", wait=".pflow-track"):
        made.append(host_id)
        page.evaluate("t => localStorage.setItem('auth_token', t)", token)
        page.evaluate("""async ([pid, hid]) => {
            const host = document.createElement('div');
            host.id = hid;
            document.body.appendChild(host);
            const m = await import('/tabs/proposals/flow-view.js');
            await m.renderFlow(host, { projectId: pid });
        }""", [project_id, host_id])
        if wait:
            page.wait_for_selector(f"#{host_id} {wait}", timeout=20000)
        return host_id
    yield _do
    # 收自己掛過的那幾個 —— 寫死一份 id 清單的話，新測試檔換個 host_id 就
    # 靜默漏掉（而漏掉的殘留會帶著已綁的事件委派跑進別支測試檔）
    page.evaluate("ids => ids.forEach(i => document.getElementById(i)?.remove())", made)


@pytest.fixture(autouse=True)
def _check_server_alive(real_server):
    """Check test server process is alive before each test."""
    proc = real_server.get("_proc")
    if proc and proc.poll() is not None:
        pytest.skip(f"Test server process died (rc={proc.returncode})")
    yield
