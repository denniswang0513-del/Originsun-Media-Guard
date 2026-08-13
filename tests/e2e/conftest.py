"""
E2E test fixtures using Playwright + real server.
"""
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


@pytest.fixture(autouse=True)
def _check_server_alive(real_server):
    """Check test server process is alive before each test."""
    proc = real_server.get("_proc")
    if proc and proc.poll() is not None:
        pytest.skip(f"Test server process died (rc={proc.returncode})")
    yield
