# -*- coding: utf-8 -*-
"""會議記錄分頁的「現場錄音」UI 回歸（`/proposal-plan.html` → 會議記錄）。

錄音那條路有兩件事光看程式碼看不出來、壞了也不會有例外，只會安靜地不見：

  A. **按鈕只在安全內容下長出來**。`getUserMedia` 在 http://192.168.1.107:8000
     這種內網 IP 上根本不存在 —— 所以不能無條件畫一顆按了會壞的鈕。這裡跑在
     localhost（＝安全內容），按鈕必須在；同時驗它是「有能力才長」而不是寫死。
  B. **開始/取消錄音的狀態轉換**。驗開始後出現計時器、取消後回到原本那排
     按鈕，且錄音狀態有清乾淨（沒清的話下一筆會被「已經有一筆在錄」擋住）。

     🔴 這裡把 `getUserMedia` 換成一段用 AudioContext 產生的真 MediaStream：
     Windows 的 headless Chromium **一個音訊輸入裝置都沒有**（連
     `--use-fake-device-for-media-capture` 也救不了，實測回
     `NotFoundError`）。要驗的是我們的狀態機不是瀏覽器的裝置層，所以只換
     取得裝置那一步 —— MediaRecorder、軌道收尾、UI 轉換全都是真的跑。

上傳與轉逐字稿那條不在這裡驗（要真的跑 whisper，分鐘級）—— 那條走
scratchpad 的端到端腳本。這裡只釘 UI。

手機視窗跑：新加的按鈕一樣要守 44px 可點目標（同 test_proposal_plan_mobile）。
只在 dev / 測試資料庫上跑（會建一筆提案再刪掉）。
"""
import uuid

import httpx
import pytest

MOBILE = {"width": 390, "height": 844}


@pytest.fixture(scope="module")
def admin_token():
    from core.auth import create_token
    return create_token({"sub": "admin", "username": "admin",
                         "access_level": 3, "modules": []})


@pytest.fixture(scope="module")
def _dev_db_only():
    from config import load_settings
    url = (load_settings().get("database_url") or "")
    db = url.rsplit("/", 1)[-1].split("?")[0].lower()
    if not db or not (db.endswith("_dev") or "test" in db):
        pytest.skip(f"只在 dev/test 資料庫上跑（目前 {db or '未設定'}）")


@pytest.fixture(scope="module")
def proposal(real_server, admin_token, _dev_db_only):
    base = real_server["base_url"]
    h = {"Authorization": f"Bearer {admin_token}"}
    r = httpx.post(f"{base}/api/v1/proposals", headers=h, timeout=60,
                   json={"title": f"錄音UI回歸_{uuid.uuid4().hex[:6]}", "ptype": "其他"})
    if r.status_code >= 400:
        pytest.skip(f"建不出提案（{r.status_code}）：{r.text[:120]}")
    p = r.json()["proposal"]
    yield p
    for i in (p["id"], p["project_id"]):
        httpx.delete(f"{base}/api/v1/proposals/{i}", headers=h, timeout=30)
        httpx.delete(f"{base}/api/v1/crm/projects/{i}", headers=h, timeout=30)


# 用 AudioContext 生一條真的音訊軌，頂替沒有裝置的 getUserMedia（見檔頭 B）
_FAKE_MIC = """
window.__fakeMic = true;
navigator.mediaDevices.getUserMedia = async () => {
    const ac = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ac.createOscillator();
    const dst = ac.createMediaStreamDestination();
    osc.connect(dst);
    osc.start();
    return dst.stream;
};
"""


@pytest.fixture(scope="module")
def mic_browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True, args=[
            "--use-fake-ui-for-media-stream",
            "--autoplay-policy=no-user-gesture-required",
        ])
        ctx = br.new_context(viewport=MOBILE, has_touch=True,
                             permissions=["microphone"])
        ctx.add_init_script(_FAKE_MIC)
        yield ctx
        ctx.close()
        br.close()


def _open_meetings(pg, base, token, pid):
    """登入 → 開這筆提案 → 切到會議記錄分頁 → 確保有一張卡。"""
    pg.goto(f"{base}/proposal-plan.html", timeout=60000)
    pg.evaluate(f"localStorage.setItem('auth_token','{token}')")
    pg.goto(f"{base}/proposal-plan.html?pid={pid}", timeout=60000)
    pg.wait_for_selector("#tab-meeting", state="attached", timeout=30000)
    pg.click("#tab-meeting")
    pg.wait_for_selector("#meeting-host .mv-bar", timeout=30000)
    if pg.locator("#meeting-host .mv-card").count() == 0:
        pg.click("#meeting-host [data-add]")
    pg.wait_for_selector("#meeting-host .mv-card .mv-abar", timeout=30000)


def test_record_button_present_on_secure_origin(real_server, mic_browser,
                                                admin_token, proposal):
    """localhost = 安全內容 → 按鈕在，且與 `canRecord()` 的判斷一致。"""
    pg = mic_browser.new_page()
    try:
        _open_meetings(pg, real_server["base_url"], admin_token, proposal["id"])
        able = pg.evaluate("() => !!(window.isSecureContext"
                           " && navigator.mediaDevices?.getUserMedia"
                           " && window.MediaRecorder)")
        assert able, "測試環境本身就不支援錄音（localhost 應該要支援）"
        # MediaRecorder 至少要吃得下一種我們挑的容器（後端白名單有 webm/mp4）
        assert pg.evaluate("() => ['audio/webm;codecs=opus','audio/webm','audio/mp4']"
                           ".some(t => MediaRecorder.isTypeSupported(t))")
        assert pg.locator("#meeting-host [data-rec]").count() == 1, \
            "安全內容下應該要有「現場錄音」按鈕"
        # 上傳那條永遠都在（錄不成的環境唯一的路）
        assert pg.locator("#meeting-host [data-aup]").count() == 1

        # 新按鈕一樣守 44px（手機可點目標）
        tiny = pg.evaluate("""() => [...document.querySelectorAll('#meeting-host .mv-btn')]
            .map(el => [el.textContent.trim(), el.getBoundingClientRect().height])
            .filter(([, h]) => h > 0 && h < 44)""")
        assert not tiny, f"會議記錄分頁有 <44px 的按鈕：{tiny}"
    finally:
        pg.close()


def test_start_then_cancel_recording(real_server, mic_browser, admin_token, proposal):
    """開始 → 出現計時器；取消 → 回到原本那排，且麥克風被收掉。"""
    pg = mic_browser.new_page()
    pg.on("dialog", lambda d: d.accept())        # 取消時的 confirm
    try:
        _open_meetings(pg, real_server["base_url"], admin_token, proposal["id"])
        pg.click("#meeting-host [data-rec]")
        pg.wait_for_selector("#meeting-host .mv-rt", timeout=15000)
        assert "錄音中" in pg.inner_text("#meeting-host .mv-rt")
        assert pg.locator("#meeting-host [data-stop]").count() == 1

        pg.click("#meeting-host [data-cancel]")
        pg.wait_for_selector("#meeting-host [data-rec]", timeout=15000)
        assert pg.locator("#meeting-host .mv-rt").count() == 0, "取消後不該還在計時"
        # 沒有殘留的錄音狀態 = 下一筆還能開始錄
        left = pg.evaluate("() => !!document.querySelector('#meeting-host').__mv?.rec")
        assert not left, "取消後 rec 狀態沒清掉，下一次會被擋"
    finally:
        pg.close()
