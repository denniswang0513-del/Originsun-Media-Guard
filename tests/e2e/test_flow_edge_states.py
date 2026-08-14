# -*- coding: utf-8 -*-
"""進度分頁的邊界態（docs/PROPOSAL_PLANNER.md §14.4「邊界態」「重抓時機」）。

三件事在這裡守，共同點是**它們都只在畫面上存在**：後端照樣收得下勾選、
照樣回得出缺項，所以沒有任何後端測試會發現它們壞掉。

  1. 終態（未成案／歸檔）＝凍結唯讀：不再叫人去做一個已經結束的案子。
  2. 沒有殼專案＝一顆真的按鈕（走 PUT 自癒路），不是一句抱怨。
  3. 切回分頁重抓：切走再切回來，看到的是現在的事實不是十分鐘前的。

自建自刪（dev/test 庫限定）。
"""
import uuid

import pytest

from .conftest import HTTP, project_case

pytestmark = pytest.mark.e2e


def _snap(page, host_id):
    """讀出**結構化**的渲染結果（同 test_project_flow_view 的理由：不比對整包
    innerHTML —— 「歸檔」兩個字也出現在項目名「歸檔清單齊備」裡）。"""
    return page.eval_on_selector(f"#{host_id}", """host => ({
        frozen: !!host.querySelector('.pflow.frozen'),
        lost: !!host.querySelector('.pflow-lost'),
        checks: host.querySelectorAll('[data-check]').length,
        links: host.querySelectorAll('a.pflow-item').length,
        tracks: host.querySelectorAll('.pflow-track').length,
        note: host.querySelector('.pflow-note')?.textContent.trim() || '',
        miss: host.querySelectorAll('.pflow-miss').length,
    })""")


@pytest.fixture(scope="module")
def owned_project(real_server, e2e_admin_token, dev_db_only):
    with project_case(real_server["base_url"], e2e_admin_token, "FLOWEDGE") as c:
        yield c


def _set_status(c, status, **extra):
    r = HTTP.patch(f"{c['base']}/api/v1/crm/projects/{c['pid']}/status",
                   headers=c["h"], json={"status": status, **extra})
    assert r.status_code < 400, f"改狀態到「{status}」失敗 {r.status_code}：{r.text[:200]}"


def test_terminal_states_freeze_the_tracks(page, mount_flow, owned_project,
                                           e2e_admin_token):
    """🔴 進行中 → 歸檔 → 未成案：同一個專案走一遍，前後對比。

    分成三支測試就要建三組客戶+專案，而真正要證明的是**差異** —— 同一份
    渲染在活著的案子上有勾選框與「去完成」，在結束的案子上兩者都沒有。
    """
    c = owned_project
    live = _snap(page, mount_flow(c["pid"], e2e_admin_token, host_id="edge-live"))
    assert not live["frozen"] and not live["lost"], live
    assert live["checks"] >= 2, f"進行中的案子該有手動勾選項：{live}"
    assert live["links"] >= 1, f"進行中的案子該有「去完成」連結：{live}"
    assert live["miss"] == 1, f"提案→製作該列出缺項：{live}"

    _set_status(c, "歸檔")
    arch = _snap(page, mount_flow(c["pid"], e2e_admin_token, host_id="edge-arch"))
    assert arch["tracks"] == 5, f"凍結不是不畫，是畫了不能動：{arch}"
    assert arch["frozen"] and not arch["lost"], arch
    assert arch["checks"] == 0, f"歸檔後還勾得動：{arch}"
    assert arch["links"] == 0, f"歸檔後還在叫人「去完成」：{arch}"
    assert "歸檔" in arch["note"] and "查閱" in arch["note"], arch["note"]

    _set_status(c, "未成案", outcome_reason="e2e：驗紅橫幅")
    lost = _snap(page, mount_flow(c["pid"], e2e_admin_token, host_id="edge-lost"))
    assert lost["lost"] and lost["frozen"], f"未成案要紅橫幅 + 凍結：{lost}"
    assert lost["checks"] == 0 and lost["links"] == 0, lost
    assert "未成案" in lost["note"], lost["note"]

    # 交還給後面兩支測試時是活著的 —— 這組客戶+專案是 module 範圍共用的，
    # 留在終態的話它們驗的其實是凍結路徑（現在照樣會過，但那是巧合）
    _set_status(c, "提案")


def test_returning_to_the_tab_refetches(page, mount_flow, owned_project,
                                        e2e_admin_token):
    """🔴 切走再切回來要重抓（§14.4；v1 不做輪詢）。

    重抓的觸發點是「這一格重新有版面」，所以這裡真的把 host 切成 display:none
    再切回來 —— 兩次 evaluate 之間隔了一個 task，觀察者才看得到那次變化
    （同一個 evaluate 裡切兩次等於沒切）。

    🔴 這支要**跟別支一起跑**才有意義：host 捲到畫面外時的行為正是它抓到的
    那個 bug（原本用 IntersectionObserver，單跑會過）。

    `at` 先歸零＝跳過「才剛抓過就別再抓」那道門檻（15s）。不是繞過測試對象：
    門檻本身在下一支測。
    """
    # 🔴 這一頁必須是**前景**分頁：背景分頁的 document.hidden 是 true，而
    # 「在背景就先不抓」正是元件刻意的行為。
    page.bring_to_front()
    hid = mount_flow(owned_project["pid"], e2e_admin_token, host_id="edge-refetch")
    want = f"/{owned_project['pid']}/flow"     # 認這個專案的那一支，不是任何含 flow 的網址
    page.eval_on_selector(f"#{hid}", "h => { h.__flow.at = 0; h.style.display = 'none'; }")
    # 🔴 等它**真的看到**藏起來了才顯示回來。ResizeObserver 的回報是合併的：
    # 兩次改動落在同一個 frame 裡，只會收到一次「現在看得見」——那不算轉換，
    # 於是不重抓（對使用者是對的，見 _watchVisible）。少了這行，這支測試會
    # 隨機紅在一個沒壞的東西上。
    page.wait_for_function(
        "id => document.getElementById(id).__flow.visible === false", arg=hid,
        timeout=10000)
    with page.expect_request(lambda r: want in r.url and r.method == "GET",
                             timeout=15000):
        page.eval_on_selector(f"#{hid}", "h => { h.style.display = ''; }")


def test_fresh_data_is_not_refetched(page, mount_flow, owned_project,
                                     e2e_admin_token):
    """剛抓過就切回來不重抓 —— 沒有這道門檻，在兩個分頁間點五下就是五趟
    那支不便宜的聚合查詢。

    用 `__flow.at`（每次**送出**抓取時蓋的時間戳）當觀測點，不去監聽網路：
    `page` 是 session 範圍的，掛在它上面的 request 監聽解不乾淨就會跟著跑進
    後面每一支 e2e。
    """
    hid = mount_flow(owned_project["pid"], e2e_admin_token, host_id="edge-fresh")
    at = page.eval_on_selector(f"#{hid}", "h => h.__flow.at")
    page.eval_on_selector(f"#{hid}", "h => { h.style.display = 'none'; }")
    page.eval_on_selector(f"#{hid}", "h => { h.style.display = ''; }")
    page.wait_for_timeout(800)   # 給 RO 兩三個 frame 的機會真的跑一次
    assert page.eval_on_selector(f"#{hid}", "h => h.__flow.at") == at, \
        "才剛抓過卻又抓了一次"


def test_heal_button_creates_the_shell_project(page, mount_flow, real_server,
                                               e2e_admin_token, dev_db_only):
    """🔴 沒有殼專案 → 按鈕真的把它放進管線（走既有的 PUT 自癒路）。

    建法：建提案（自動帶殼）→ 解除連結 + 刪掉那個殼 → 得到一筆「不在管線裡」
    的提案，也就是遷移前的存量長的樣子。
    """
    base = real_server["base_url"]
    h = {"Authorization": f"Bearer {e2e_admin_token}"}
    r = HTTP.post(f"{base}/api/v1/proposals", headers=h,
                  json={"title": f"自癒_{uuid.uuid4().hex[:6]}", "ptype": "其他"})
    if r.status_code >= 400:
        pytest.skip(f"建不出提案（{r.status_code}）：{r.text[:120]}")
    prop = r.json()["proposal"]
    shell = prop.get("project_id") or ""
    healed = ""
    try:
        HTTP.patch(f"{base}/api/v1/proposals/{prop['id']}/project", headers=h,
                   json={"project_id": ""})
        if shell:
            HTTP.delete(f"{base}/api/v1/crm/projects/{shell}", headers=h)

        hid = mount_flow("", e2e_admin_token, host_id="edge-heal", wait="[data-heal]",
                         proposalId=prop["id"])
        page.click(f"#{hid} [data-heal]")
        page.wait_for_selector(f"#{hid} .pflow-track", timeout=20000)

        # 畫面說補上了 → 後端也真的補上了（不是前端自己畫了五條軌）
        healed = HTTP.get(f"{base}/api/v1/proposals/{prop['id']}",
                          headers=h).json()["proposal"]["project_id"]
        assert healed and healed != shell, f"提案還是沒有專案：{healed!r}"
        assert _snap(page, hid)["tracks"] == 5
    finally:
        HTTP.delete(f"{base}/api/v1/proposals/{prop['id']}", headers=h)
        for p in (shell, healed):
            if p:
                HTTP.delete(f"{base}/api/v1/crm/projects/{p}", headers=h)
