# -*- coding: utf-8 -*-
"""進度分頁的邊界態（docs/PROPOSAL_PLANNER.md §14.4「邊界態」「重抓時機」）。

三件事在這裡守，共同點是**它們都只在畫面上存在**：後端照樣收得下勾選、
照樣回得出缺項，所以沒有任何後端測試會發現它們壞掉。

  1. 終態（未成案／歸檔）＝凍結唯讀：不再叫人去做一個已經結束的案子。
  2. 沒有殼專案＝一顆真的按鈕，不是一句抱怨。
  3. 切回分頁重抓：切走再切回來，看到的是現在的事實不是十分鐘前的。

自建自刪（dev/test 庫限定）。
"""
from contextlib import contextmanager

import pytest

from .conftest import HTTP, flow_case, project_case

pytestmark = pytest.mark.e2e


@contextmanager
def _unpipelined(base, token, tag):
    """一筆「不在管線裡」的提案（＝存量遷移前的樣子）：建提案（自動帶殼）→
    解除連結 → 刪掉那個殼。

    🔴 **自癒建出來的新專案由這裡收**。兩支測試各自手抄那段 try/finally 時，
    只要中間任何一個 assert 或 wait 先炸掉，`healed` 還是空字串，那個專案就
    永久留在 dev 庫裡 —— 而「只刪自己建的」正是這個 repo 咬過的金絲雀鐵則。
    這裡改成回頭問後端「你現在掛在哪個專案」，不依賴測試有沒有跑到那一行。
    """
    with flow_case(base, token, tag) as c:
        HTTP.patch(f"{base}/api/v1/proposals/{c['prop_id']}/project",
                   headers=c["h"], json={"project_id": ""})
        HTTP.delete(f"{base}/api/v1/crm/projects/{c['project_id']}", headers=c["h"])
        try:
            yield c
        finally:
            healed = HTTP.get(f"{base}/api/v1/proposals/{c['prop_id']}",
                              headers=c["h"]).json()["proposal"]["project_id"]
            if healed:
                HTTP.delete(f"{base}/api/v1/crm/projects/{healed}", headers=c["h"])


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
    r = HTTP.patch(f"{c['base']}/api/v1/crm/projects/{c['project_id']}/status",
                   headers=c["h"], json={"status": status, **extra})
    assert r.status_code < 400, f"改狀態到「{status}」失敗 {r.status_code}：{r.text[:200]}"


def test_terminal_states_freeze_the_tracks(page, mount_flow, owned_project,
                                           e2e_admin_token):
    """🔴 進行中 → 歸檔 → 未成案：同一個專案走一遍，前後對比。

    分成三支測試就要建三組客戶+專案，而真正要證明的是**差異** —— 同一份
    渲染在活著的案子上有勾選框與「去完成」，在結束的案子上兩者都沒有。
    """
    c = owned_project
    live = _snap(page, mount_flow(c["project_id"], e2e_admin_token, host_id="edge-live"))
    assert not live["frozen"] and not live["lost"], live
    assert live["checks"] >= 2, f"進行中的案子該有手動勾選項：{live}"
    assert live["links"] >= 1, f"進行中的案子該有「去完成」連結：{live}"
    assert live["miss"] == 1, f"提案→製作該列出缺項：{live}"

    _set_status(c, "歸檔")
    arch = _snap(page, mount_flow(c["project_id"], e2e_admin_token, host_id="edge-arch"))
    assert arch["tracks"] == 5, f"凍結不是不畫，是畫了不能動：{arch}"
    assert arch["frozen"] and not arch["lost"], arch
    assert arch["checks"] == 0, f"歸檔後還勾得動：{arch}"
    assert arch["links"] == 0, f"歸檔後還在叫人「去完成」：{arch}"
    assert "歸檔" in arch["note"] and "查閱" in arch["note"], arch["note"]

    _set_status(c, "未成案", outcome_reason="e2e：驗紅橫幅")
    lost = _snap(page, mount_flow(c["project_id"], e2e_admin_token, host_id="edge-lost"))
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
    hid = mount_flow(owned_project["project_id"], e2e_admin_token, host_id="edge-refetch")
    want = f"/{owned_project['project_id']}/flow"     # 認這個專案的那一支，不是任何含 flow 的網址
    page.eval_on_selector(f"#{hid}", "h => { h.__flow.at = 0; h.style.display = 'none'; }")
    # 🔴 等它**真的看到**藏起來了才顯示回來。ResizeObserver 的回報是合併的：
    # 兩次改動落在同一個 frame 裡，只會收到一次「現在看得見」——那不算轉換，
    # 於是不重抓（對使用者是對的，見 _visitVisible）。少了這行，這支測試會
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

    用 `__flow.at`（抓取的時間戳）當觀測點，不去監聽網路：`page` 是 session
    範圍的，掛在它上面的 request 監聽解不乾淨就會跟著跑進後面每一支 e2e。

    🔴 **兩次可見度轉換都要真的等到**（理由見上一支）—— 少了它們就走不到
    節流那一行，而測試照樣綠。
    """
    hid = mount_flow(owned_project["project_id"], e2e_admin_token, host_id="edge-fresh")
    seen = "id => document.getElementById(id).__flow.visible"
    at = page.eval_on_selector(f"#{hid}", "h => h.__flow.at")

    page.eval_on_selector(f"#{hid}", "h => { h.style.display = 'none'; }")
    page.wait_for_function(f"{seen} === false", arg=hid, timeout=10000)
    page.eval_on_selector(f"#{hid}", "h => { h.style.display = ''; }")
    page.wait_for_function(f"{seen} === true", arg=hid, timeout=10000)

    assert page.eval_on_selector(f"#{hid}", "h => h.__flow.at") == at, \
        "才剛抓過卻又抓了一次"


def test_heal_button_creates_the_shell_project(page, mount_flow, real_server,
                                               e2e_admin_token, dev_db_only):
    """🔴 沒有殼專案 → 按鈕真的把它放進管線。"""
    with _unpipelined(real_server["base_url"], e2e_admin_token, "自癒") as c:
        base, h, shell = c["base"], c["h"], c["project_id"]
        hid = mount_flow("", e2e_admin_token, host_id="edge-heal",
                         wait="[data-heal]", proposalId=c["prop_id"])
        page.click(f"#{hid} [data-heal]")
        page.wait_for_selector(f"#{hid} .pflow-track", timeout=20000)

        # 畫面說補上了 → 後端也真的補上了（不是前端自己畫了五條軌）
        healed = HTTP.get(f"{base}/api/v1/proposals/{c['prop_id']}",
                          headers=h).json()["proposal"]["project_id"]
        assert healed and healed != shell, f"提案還是沒有專案：{healed!r}"
        assert _snap(page, hid)["tracks"] == 5


def test_no_refetch_when_the_caller_tears_the_host_down(page, real_server,
                                                        e2e_admin_token, dev_db_only):
    """🔴 onChanged 把 host 拆掉 → 元件不再去抓那份會被丟進垃圾桶的聚合查詢。

    這是第二輪抓到的那個缺陷的回歸測試：守衛（`if (!host.isConnected) return`）
    看起來是對的，但 SPA 的回呼是「同步返回、在自己的 await 之後才換掉
    overlay」，所以不 await 回呼的話，檢查的那一刻 host 永遠還在 —— 那趟
    一列 18 個相關子查詢的 `/flow` 照樣送出，然後整份丟掉。
    所以這裡的假 onChanged 刻意做成同一個形狀：先 await 一下，才拆掉 host。

    觀測點同樣是 `__flow.at`（同上一支的理由）：CTA 那條路上它是 0，只有
    `_load` 會蓋 —— 所以「還是 0」就等於「沒去抓」。
    🔴 等的是**回呼真的跑完**那個訊號，不是一段固定秒數：POST 建殼是
    `create_project_in_session` 的第一擊（約 9 趟 round trip + 10 列 insert），
    冷機器上跑超過任何猜出來的秒數都不奇怪，而那會讓這支測試在「根本還沒走到
    判斷點」的情況下綠掉 —— 拿掉守衛也一樣綠。
    """
    with _unpipelined(real_server["base_url"], e2e_admin_token, "拆除") as c:
        at = page.evaluate("""async ([token, propId]) => {
            localStorage.setItem('auth_token', token);
            const host = document.createElement('div');
            document.body.appendChild(host);
            const m = await import('/tabs/proposals/flow-view.js');
            let torn; const teardown = new Promise(r => { torn = r; });
            try {
                await m.renderFlow(host, {
                    projectId: '', proposalId: propId, here: 'preprod_proposals',
                    // 比照 SPA：回呼自己也有 await，拆除在那之後才發生
                    onChanged: async () => {
                        await new Promise(r => setTimeout(r, 0));
                        host.remove();
                        torn();
                    },
                });
                host.querySelector('[data-heal]').click();
                await teardown;                            // 判斷點已經過了
                await new Promise(r => setTimeout(r, 0));  // 讓漏網的 _load 有機會蓋 at
                return host.__flow.at;
            } finally { host.remove(); }
        }""", [e2e_admin_token, c["prop_id"]])

        assert at == 0, "host 都被拆掉了還去抓 /flow"


def test_custom_items_can_be_added_and_removed(page, mount_flow, owned_project,
                                               e2e_admin_token):
    """自訂項（owner 2026-08-15：「裡頭的項目細節也是要可以新增刪除」）。

    範本那五軌是全公司通用的，所以「這個案子額外要做的事」只能加在專案自己的
    blob 裡。這條守三件事：加得出來、勾得動、刪得掉，而且**範本項不給刪**
    （沒有最後那半，一顆手滑的 ✕ 會把全公司的骨架從這個案子上拿掉）。
    """
    c = owned_project
    label = "補拍空景（e2e）"
    host = mount_flow(c["project_id"], e2e_admin_token, host_id="edge-custom")
    page.on("dialog", lambda d: (d.accept(label) if d.type == "prompt" else d.accept()))
    try:
        # 加：製作軌的「＋」
        page.click(f'#{host} [data-add-track="prod"]')
        page.wait_for_function(
            """([h, t]) => document.getElementById(h)?.textContent.includes(t)""",
            arg=[host, label], timeout=20000)

        # 勾得動（自訂項是手動項）
        item = page.query_selector(
            f'#{host} .pflow-item.manual:not(.on)[data-check^="c_"]')
        assert item, "自訂項沒有變成可勾的手動項"
        key = item.get_attribute("data-check")
        item.click()
        page.wait_for_selector(f'#{host} [data-check="{key}"].on', timeout=20000)

        # 🔴 範本項沒有 ✕（不然一顆手滑就把全公司的骨架拿掉）
        assert page.eval_on_selector_all(
            f'#{host} [data-del-item]',
            "els => els.map(e => e.dataset.delItem)") == [key], "刪除鈕出現在範本項上"

        # 刪
        page.click(f'#{host} [data-del-item="{key}"]')
        page.wait_for_function(
            """([h, t]) => !document.getElementById(h)?.textContent.includes(t)""",
            arg=[host, label], timeout=20000)
    finally:
        # 保險：測試中途炸掉時把殘留的自訂項清掉（只清自己加的）
        fresh = HTTP.get(f"{c['base']}/api/v1/crm/projects/{c['project_id']}/flow",
                         headers=c["h"]).json()
        for t in fresh.get("tracks", []):
            for it in t["items"]:
                if it.get("custom") and it["label"] == label:
                    HTTP.delete(
                        f"{c['base']}/api/v1/crm/projects/{c['project_id']}"
                        f"/flow/items/{it['key']}", headers=c["h"])
