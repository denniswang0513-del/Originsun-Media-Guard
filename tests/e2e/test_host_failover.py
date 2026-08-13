# -*- coding: utf-8 -*-
"""一台主機失聯 → 它沒轉完的份額要改派給活著的主機，而不是宣告全體完成。

2026-08-13 事故的回歸測試：當時 heartbeat 把「逾時」寫成 `info.done = true`，
於是 `hosts.every(h => h.done)` 成立 → 直接觸發合併，那台手上還沒轉的 51 支
就這樣沒有人接手，最後還在交付夾留下一個播不開的半截檔。

用假的 fetch 攔截整條路徑跑一次，不需要真的有兩台機器。
"""
import pytest

pytestmark = pytest.mark.e2e

FAKE_JS = """
(() => {
    window.__calls = { transcode: [], merge: 0, listDir: 0 };

    const HOST_A = '10.99.0.1:8000';   // 失聯的那台
    const HOST_B = '10.99.0.2:8000';   // 活著、要接手的那台

    window._remoteJobType = 'transcode';
    window._activeJobTab = 'transcode';
    window._retryFailedHosts = [];
    window._originalDispatchHosts = [
        { name: 'HostA', ip: HOST_A },
        { name: 'HostB', ip: HOST_B },
    ];
    window._originalSourceDirs = [];
    window._dispatchDestRoot = '//nas/proxy/PROJ';

    // HostA 分到兩支，其中 A001 已經產出、A002 還沒
    window._activeRemoteHosts = {};
    window._activeRemoteHosts[HOST_A] = {
        host: { name: 'HostA', ip: HOST_A },
        assigned: [
            { cardName: 'card1', file: '//nas/src/card1/A001.MP4' },
            { cardName: 'card1', file: '//nas/src/card1/A002.MP4' },
        ],
        destDirs: ['//nas/proxy/PROJ/HostDispatch_HostA'],
        state: 'running',
        pct: 40,
        // 已經超過失聯門檻 —— 不必真的等 90 秒
        lastSeen: Date.now() - 600000,
        startTime: Date.now() - 900000,
    };

    // 🔴 page fixture 是 session 範圍的 —— 攔截與假狀態一定要還得回去，
    // 不然後面每一支 e2e 都跑在被汙染的頁面上。
    window.__restoreFetch = (realFetch => () => {
        window.fetch = realFetch;
        delete window.__calls;
        window._activeRemoteHosts = {};
        window._retryFailedHosts = [];
        window._remoteJobType = null;
        window._dispatchDestRoot = '';
        if (window._heartbeatTimer) { clearInterval(window._heartbeatTimer); window._heartbeatTimer = null; }
    })(window.fetch.bind(window));

    const realFetch = window.fetch.bind(window);
    window.fetch = async (url, opts) => {
        const u = String(url);
        const body = opts && opts.body ? JSON.parse(opts.body) : null;

        if (u.includes(HOST_A)) throw new Error('unreachable');   // A 已死

        if (u.includes('/api/v1/health') && u.includes(HOST_B)) {
            return new Response('{"status":"ok"}', { status: 200 });
        }
        if (u.includes('/api/v1/list_dir')) {
            window.__calls.listDir++;
            // HostA 的夾子裡只有 A001 完成了
            return new Response(JSON.stringify({
                files: ['//nas/proxy/PROJ/HostDispatch_HostA/card1/A001_proxy.mov'],
            }), { status: 200 });
        }
        if (u.includes('/api/v1/jobs/transcode')) {
            window.__calls.transcode.push({ url: u, sources: body.sources, dest: body.dest_dir });
            return new Response('{"job_id":"fake123"}', { status: 200 });
        }
        if (u.includes('/api/v1/merge_host_outputs')) {
            window.__calls.merge++;
            return new Response('{"status":"ok","merged":0,"errors":[]}', { status: 200 });
        }
        if (u.includes('/api/v1/status')) {
            return new Response('{"busy":false,"queue_length":0,"active_jobs":{}}', { status: 200 });
        }
        return realFetch(url, opts);
    };

    window.startHeartbeatMonitor();
})();
"""


def _state(page):
    """一次把要斷言的東西全部抓下來 —— restore 之後這些狀態就被清掉了。"""
    return page.evaluate("() => ({ calls: window.__calls,"
                         " hosts: window._activeRemoteHosts,"
                         " blacklist: window._retryFailedHosts })")


def test_dead_host_work_is_reassigned(page):
    page.evaluate(FAKE_JS)
    try:
        # heartbeat 每 5 秒一輪；留兩輪的餘裕給非同步的重派流程
        page.wait_for_timeout(9000)
        st = _state(page)
    finally:
        page.evaluate("() => window.__restoreFetch && window.__restoreFetch()")

    calls = st["calls"]

    # 1. 失聯那台沒轉完的份額被派出去了
    assert len(calls["transcode"]) == 1, f"應該派出一批補轉，實際 {calls['transcode']}"
    sent = calls["transcode"][0]
    names = [s.split("/")[-1] for s in sent["sources"]]
    assert names == ["A002.MP4"], f"只該重派沒產出的那支，實際 {names}"
    assert "10.99.0.2" in sent["url"], f"應派給活著的 HostB，實際 {sent['url']}"
    assert "HostDispatch_Takeover_HostB" in sent["dest"], sent["dest"]

    # 2. 🔴 不准因為一台失聯就宣告完成去合併
    assert calls["merge"] == 0, "有主機失聯時不該觸發合併"

    # 3. 失聯的標成 timeout（不是 done）、接手的標成 running
    hosts = st["hosts"]
    assert hosts["10.99.0.1:8000"]["state"] == "timeout"
    assert hosts["10.99.0.2:8000"]["state"] == "running"

    # 4. 死掉那台進黑名單，就算醒來也不再派工給它
    assert "10.99.0.1:8000" in st["blacklist"]


NO_TAKEOVER_JS = """
(() => {
    window.__calls = { transcode: [], merge: 0 };
    const HOST_A = '10.99.0.3:8000';

    window.__restoreFetch = (realFetch => () => {
        window.fetch = realFetch;
        delete window.__calls;
        window._activeRemoteHosts = {};
        window._retryFailedHosts = [];
        window._remoteJobType = null;
        if (window._heartbeatTimer) { clearInterval(window._heartbeatTimer); window._heartbeatTimer = null; }
    })(window.fetch.bind(window));

    window._remoteJobType = 'transcode';
    window._activeJobTab = 'transcode';
    window._retryFailedHosts = [];
    window._originalDispatchHosts = [{ name: 'HostA', ip: HOST_A }];   // 沒有別台可接手
    window._originalSourceDirs = [];
    window._dispatchDestRoot = '//nas/proxy/PROJ';
    window._isStandaloneTranscode = true;
    const dest = document.getElementById('tc_dest');
    const proj = document.getElementById('tc_proj_name');
    if (dest) dest.value = '//nas/proxy';
    if (proj) proj.value = 'PROJ';

    window._activeRemoteHosts = {};
    window._activeRemoteHosts[HOST_A] = {
        host: { name: 'HostA', ip: HOST_A },
        assigned: [{ cardName: 'card1', file: '//nas/src/card1/A001.MP4' }],
        destDirs: ['//nas/proxy/PROJ/HostDispatch_HostA'],
        state: 'running', pct: 10,
        lastSeen: Date.now() - 600000, startTime: Date.now() - 900000,
    };

    const realFetch = window.fetch.bind(window);
    window.fetch = async (url, opts) => {
        const u = String(url);
        if (u.includes(HOST_A)) throw new Error('unreachable');
        if (u.includes('/api/v1/list_dir')) return new Response('{"files":[]}', { status: 200 });
        if (u.includes('/api/v1/jobs/transcode')) {
            window.__calls.transcode.push(u);
            return new Response('{"job_id":"x"}', { status: 200 });
        }
        if (u.includes('/api/v1/merge_host_outputs')) {
            window.__calls.merge++;
            return new Response('{"status":"ok","merged":0,"errors":[]}', { status: 200 });
        }
        return realFetch(url, opts);
    };

    window.startHeartbeatMonitor();
})();
"""


def test_no_takeover_available_still_reaches_merge(page):
    """沒有別台可以接手時，流程要走完（合併 + 補轉驗證），不是斷在半路。

    這條路徑一度是壞的：失聯台數的計算被放在 `if (hosts.length > 0)` 區塊內，
    卻在區塊外被讀取 —— ReferenceError 直接把 heartbeat 的這一輪炸掉，
    停在 stopHeartbeatMonitor() 之後、觸發合併之前，畫面就這樣不動了。
    """
    page.evaluate(NO_TAKEOVER_JS)
    try:
        page.wait_for_timeout(11000)   # 一輪偵測 + 2 秒緩衝後才觸發合併
        calls = page.evaluate("() => window.__calls")
    finally:
        page.evaluate("() => window.__restoreFetch && window.__restoreFetch()")

    assert calls["transcode"] == [], "沒有可接手的主機，不該派出任何補轉"
    assert calls["merge"] == 1, "沒人接手時流程仍要走到合併（後續驗證會列出缺件）"
