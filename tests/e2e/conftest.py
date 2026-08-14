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

# 🔴 **不要用 httpx 的模組層 `httpx.get/post/delete`**：那些每次都重建一個
# Client，實測 175ms/次，其中 158ms 純粹是建 Client（憑證/環境/transport）——
# 對 127.0.0.1 的純 HTTP 也一樣。共用一個降到 4ms/次。
# retries=1：共用連線偶爾會撞到伺服器端關掉的 keep-alive。
HTTP = httpx.Client(transport=httpx.HTTPTransport(retries=1), timeout=60)


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
    # 等 loadTabs() 把各 section 填好 —— 等**條件**不是等秒數（實測 0.29s 就
    # 到位，原本固定睡 3 秒）。逾時放寬到 30s：慢機只是慢，不該因此變紅。
    p.wait_for_function(
        "() => [...document.querySelectorAll('.tab-content')]"
        ".some(s => s.children.length > 0)", timeout=30000)
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
    r = HTTP.post(f"{base}/api/v1/proposals", headers=h,
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
        HTTP.delete(f"{base}/api/v1/proposals/{p['id']}", headers=h)
        HTTP.delete(f"{base}/api/v1/crm/projects/{p['project_id']}", headers=h)


@contextmanager
def project_case(base, token, tag):
    """自建自刪一組客戶 + CRM 專案（狀態＝提案），回 {base, h, project_id, client_id}。

    鍵名與 `flow_case` 對齊（都用 `project_id`）—— 同一個檔案同時用到兩支時，
    「這支叫 pid 那支叫 project_id」是純粹自找的記憶負擔。

    與 `flow_case` 是姊妹：那支從**提案**側建（順帶拿到殼專案），這支直接建
    專案 —— 要改專案狀態驗終態時得用這支，改到提案的殼會連動衛星提案的
    win/loss，等於順手測了別人的東西。

    手建專案 client_id 必填（只有提案建殼那條路可以空，見 db.models.CrmProject），
    所以客戶那半不能省。共用的同樣是「怎麼建、怎麼跳過、怎麼收」這份契約 ——
    抄壞的後果是把測試資料留在庫裡，而反序刪除（先專案後客戶）正是最容易
    抄漏的一段。
    """
    h = {"Authorization": f"Bearer {token}"}
    uniq = uuid.uuid4().hex[:6]
    rc = HTTP.post(f"{base}/api/v1/crm/clients", headers=h,
                   json={"name": f"[{tag}] 客戶 {uniq}", "short_name": f"{tag[:4]}{uniq}"})
    if rc.status_code >= 400:
        pytest.skip(f"建不出客戶（{rc.status_code}）：{rc.text[:120]}")
    # 直接索引，不給 `or` 退路：那兩支端點只回這一種形狀（clients.py /
    # projects.py 的 return），退路永遠走不到，而形狀真的變了的時候它會把
    # None 靜靜塞進下面的網址
    cid = rc.json()["client"]["id"]
    rp = HTTP.post(f"{base}/api/v1/crm/projects", headers=h,
                   json={"name": f"[{tag}] {uniq}", "status": "提案", "client_id": cid})
    if rp.status_code >= 400:
        HTTP.delete(f"{base}/api/v1/crm/clients/{cid}", headers=h)
        pytest.skip(f"建不出專案（{rp.status_code}）：{rp.text[:120]}")
    pid = rp.json()["project"]["id"]
    try:
        yield {"base": base, "h": h, "project_id": pid, "client_id": cid}
    finally:
        # 反序：專案先走，客戶才刪得掉
        HTTP.delete(f"{base}/api/v1/crm/projects/{pid}", headers=h)
        HTTP.delete(f"{base}/api/v1/crm/clients/{cid}", headers=h)


@pytest.fixture(scope="module")
def plan_page(browser_context, real_server, e2e_admin_token):
    """開一頁 `/proposal-plan.html`（已登入）。用完自己關。

    🔴 為什麼要 goto 兩次：`localStorage` 是綁 origin 的，沒有先落地在那個
    origin 上就寫不進去。這個非顯而易見的開場白原本在四個測試檔各抄一份 ——
    哪天改成 `context.add_init_script`，只會有一個被改到。
    """
    made = []

    def _open(query="", *, wait="#plan-side .side-tab"):
        page = browser_context.new_page()
        made.append(page)
        base = real_server["base_url"] + "/proposal-plan.html"
        page.goto(base, timeout=60000)
        page.evaluate("t => localStorage.setItem('auth_token', t)", e2e_admin_token)
        page.goto(base + query, timeout=60000)
        if wait:
            page.wait_for_selector(wait, timeout=30000)
        return page
    yield _open
    for p in made:
        p.close()


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

    # host_id 沒有預設：每支測試的選擇器都寫死自己那個 id，給預設只會讓
    # 「made 收得齊不齊」變成要去看有沒有人漏傳
    def _do(project_id, token, *, host_id, wait=".pflow-track",
            here="preprod_proposals", **opts):
        # here 的預設值＝兩個真實掛載點都傳的那個（提案工作區）。測試要驗
        # 「沒有 here 時那幾盞燈就有連結」才明確傳 ''。
        # **opts：其餘 renderFlow 選項（proposalId…）原樣帶過去 —— 這裡不列
        # 白名單，不然元件每加一個選項就要來改一次這支 fixture。
        made.append(host_id)
        page.evaluate("t => localStorage.setItem('auth_token', t)", token)
        page.evaluate("""async ([pid, hid, here, extra]) => {
            const host = document.createElement('div');
            host.id = hid;
            document.body.appendChild(host);
            const m = await import('/tabs/proposals/flow-view.js');
            await m.renderFlow(host, { projectId: pid, here, ...extra });
        }""", [project_id, host_id, here, opts])
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
