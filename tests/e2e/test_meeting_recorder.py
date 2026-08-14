# -*- coding: utf-8 -*-
"""會議記錄分頁的「現場錄音」UI 回歸（`/proposal-plan.html` → 會議記錄）。

錄音那條路有三件事光看程式碼看不出來、壞了也不會有例外，只會安靜地不見：

  A. **按鈕只在有能力時長出來**。`getUserMedia` 在 http://192.168.1.107:8000
     這種內網 IP 上根本不存在 —— 所以不能無條件畫一顆按了會壞的鈕。有能力
     （localhost）時按鈕要在；**把 `MediaRecorder` 拿掉之後按鈕要消失** ——
     沒有後面這半，一顆寫死的按鈕也會讓測試全過。
  B. **開始/取消錄音的狀態轉換**。驗開始後出現計時器、取消後回到原本那排
     按鈕，且錄音狀態有清乾淨（沒清的話下一筆會被「已經有一筆在錄」擋住）。
  C. **錄音列撐得過整份重畫**。錄音中按「新增會議記錄」會整份重畫；錄音狀態
     是渲染的輸入，所以錄音列要被畫回來。畫不回來的話 recorder 還活著、但
     使用者看不到也停不掉（只能重整整頁）。

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
import pytest

from .conftest import MOBILE, flow_case


@pytest.fixture(scope="module")
def proposal(real_server, e2e_admin_token, dev_db_only):
    """自建自刪的提案 —— 建/跳過/收的契約共用 conftest 那一份。

    （原本這裡抄了一份，而且是抄壞的那份：沒有「沒有殼專案就 skip」，所以
    收尾那行 `p['project_id']` 會直接 KeyError 而不是乾淨跳過。）
    """
    with flow_case(real_server["base_url"], e2e_admin_token, "錄音UI回歸") as c:
        yield {"id": c["prop_id"]}


# 用 AudioContext 生一條真的音訊軌，頂替沒有裝置的 getUserMedia（見檔頭 C）。
# 順手把 stream 留一份，好驗收尾時麥克風軌道真的被關掉。
_FAKE_MIC = """
navigator.mediaDevices.getUserMedia = async () => {
    const ac = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ac.createOscillator();
    const dst = ac.createMediaStreamDestination();
    osc.connect(dst);
    osc.start();
    window.__lastStream = dst.stream;
    return dst.stream;
};
"""
# 沒有 MediaRecorder 的瀏覽器（或非安全內容）—— 用來驗按鈕真的是條件長出來的
_NO_RECORDER = "delete window.MediaRecorder;"


@pytest.fixture(scope="module")
def mic_browser(browser):
    """假麥克風的 context —— 從 conftest 那支 session browser 開。

    假麥克風那組旗標與 microphone 權限都用不到：getUserMedia 整支被換掉了，
    真的裝置/權限路徑一次都不會走到。唯一需要的 `--autoplay-policy`
    （AudioContext 的 oscillator 要它）已經掛在共用 browser 上。

    🔴 不要自己 `sync_playwright()`：同執行緒只能有一支，混跑會整批 error。
    """
    ctx = browser.new_context(viewport=MOBILE, has_touch=True)
    ctx.add_init_script(_FAKE_MIC)
    yield ctx
    ctx.close()


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


def test_record_button_is_capability_gated(real_server, mic_browser,
                                           e2e_admin_token, proposal):
    """有能力 → 按鈕在；把 MediaRecorder 拿掉 → 按鈕消失（上傳那條永遠都在）。"""
    base, pid = real_server["base_url"], proposal["id"]
    pg = mic_browser.new_page()
    try:
        _open_meetings(pg, base, e2e_admin_token, pid)
        assert pg.evaluate("() => !!(window.isSecureContext"
                           " && navigator.mediaDevices?.getUserMedia"
                           " && window.MediaRecorder)"), \
            "測試環境本身就不支援錄音（localhost 應該要支援）"
        # MediaRecorder 至少要吃得下一種我們挑的容器（後端白名單有 webm/mp4）
        assert pg.evaluate("() => ['audio/webm;codecs=opus','audio/webm','audio/mp4']"
                           ".some(t => MediaRecorder.isTypeSupported(t))")
        assert pg.locator("#meeting-host [data-rec]").count() == 1, \
            "有能力時應該要有「現場錄音」按鈕"
        assert pg.locator("#meeting-host [data-aup]").count() == 1

        # 新按鈕一樣守 44px（手機可點目標）
        tiny = pg.evaluate("""() => [...document.querySelectorAll('#meeting-host .mv-btn')]
            .map(el => [el.textContent.trim(), el.getBoundingClientRect().height])
            .filter(([, h]) => h > 0 && h < 44)""")
        assert not tiny, f"會議記錄分頁有 <44px 的按鈕：{tiny}"
    finally:
        pg.close()

    # ── 沒有 MediaRecorder 的瀏覽器：按鈕必須不見（否則就是寫死的）──
    pg2 = mic_browser.new_page()
    pg2.add_init_script(_NO_RECORDER)
    try:
        _open_meetings(pg2, base, e2e_admin_token, pid)
        assert pg2.locator("#meeting-host [data-rec]").count() == 0, \
            "沒有 MediaRecorder 還畫出「現場錄音」＝按鈕是寫死的，按了會壞"
        assert pg2.locator("#meeting-host [data-aup]").count() == 1, \
            "錄不成的環境更需要「上傳」那條路"
    finally:
        pg2.close()


def test_start_then_cancel_recording(real_server, mic_browser, e2e_admin_token, proposal):
    """開始 → 出現計時器；取消 → 回到原本那排，狀態清乾淨、麥克風關掉。"""
    pg = mic_browser.new_page()
    pg.on("dialog", lambda d: d.accept())        # 取消時的 confirm
    try:
        _open_meetings(pg, real_server["base_url"], e2e_admin_token, proposal["id"])
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
        # 麥克風軌道要收掉（不收的話分頁上的紅點一直亮著）
        ended = pg.evaluate("() => (window.__lastStream?.getTracks() || [])"
                            ".every(t => t.readyState === 'ended')")
        assert ended, "取消後麥克風軌道沒有停掉"
    finally:
        pg.close()


def test_recording_survives_list_rerender(real_server, mic_browser,
                                          e2e_admin_token, proposal):
    """錄音中按「新增會議記錄」（整份重畫）→ 錄音列要被畫回來、還停得掉。"""
    pg = mic_browser.new_page()
    pg.on("dialog", lambda d: d.accept())
    try:
        _open_meetings(pg, real_server["base_url"], e2e_admin_token, proposal["id"])
        pg.click("#meeting-host [data-rec]")
        pg.wait_for_selector("#meeting-host .mv-rt", timeout=15000)

        before = pg.locator("#meeting-host .mv-card").count()
        pg.click("#meeting-host [data-add]")     # → _load → _render：整份重畫
        pg.wait_for_function(
            f"() => document.querySelectorAll('#meeting-host .mv-card').length > {before}",
            timeout=30000)

        assert pg.locator("#meeting-host .mv-rt").count() == 1, \
            "整份重畫之後錄音列不見了 —— 錄音還活著但使用者看不到也停不掉"
        assert pg.locator("#meeting-host [data-stop]").count() == 1
        pg.click("#meeting-host [data-cancel]")  # 停得掉才算數
        pg.wait_for_selector("#meeting-host .mv-rt", state="detached", timeout=15000)
        assert not pg.evaluate(
            "() => !!document.querySelector('#meeting-host').__mv?.rec")
    finally:
        pg.close()
