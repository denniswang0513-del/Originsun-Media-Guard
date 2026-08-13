# -*- coding: utf-8 -*-
"""未亮的燈上的「去完成」deep-link（§14.4 階段四）。

這條連結串起三個各自獨立的鍵空間：item_key → 模組鍵 → SPA section id。
單元測試守得住前兩段（`TestDeepLinks`），但**最後一段只有瀏覽器看得到**：
`TAB_MAP[模組]` 換不到 section 的話，畫面不會報錯，只是安靜地少一條連結。

三件事釘在這裡：
  A. 只有未亮的自動燈有連結 —— 亮著的沒事做、手動項就在這頁勾。
  B. 沒有目的地模組的人看得到燈也看得到缺什麼權限，但點不動（不藏功能）。
  C. SPA 裡點下去是**原地換 tab**，不是把使用者丟去重新載入整個 SPA。

自建自刪（dev/test 庫限定）。
"""
import uuid

import httpx
import pytest

pytestmark = pytest.mark.e2e

HOST = "flowlink-host"


@pytest.fixture
def case(real_server, e2e_admin_token, dev_db_only):
    """一筆空專案 —— 空的才有一整排未亮的燈可看。"""
    base = real_server["base_url"]
    h = {"Authorization": f"Bearer {e2e_admin_token}"}
    r = httpx.post(f"{base}/api/v1/proposals", headers=h, timeout=60,
                   json={"title": f"連結回歸_{uuid.uuid4().hex[:6]}", "ptype": "其他"})
    if r.status_code >= 400:
        pytest.skip(f"建不出提案（{r.status_code}）：{r.text[:120]}")
    p = r.json()["proposal"]
    if not p.get("project_id"):
        pytest.skip("這筆提案沒有殼專案")
    yield {"base": base, "h": h, "project_id": p["project_id"]}
    httpx.delete(f"{base}/api/v1/proposals/{p['id']}", headers=h, timeout=30)
    httpx.delete(f"{base}/api/v1/crm/projects/{p['project_id']}", headers=h, timeout=30)


@pytest.fixture
def mount(mount_flow):
    return lambda project_id, token: mount_flow(project_id, token, host_id=HOST)


def _items(page):
    """每盞燈：標籤、亮沒亮、是不是連結、連去哪。"""
    return page.eval_on_selector_all(f"#{HOST} .pflow-item", """els => els.map(e => ({
        label: e.textContent.trim(),
        on: e.classList.contains('on'),
        skip: e.classList.contains('skip'),
        manual: e.classList.contains('manual'),
        link: e.tagName === 'A' ? (e.dataset.go || '') : '',
        marked: e.classList.contains('go') || e.classList.contains('nogo'),
        blocked: e.classList.contains('nogo'),
        title: e.getAttribute('title') || '',
    }))""")


def test_unlit_auto_lamps_link_to_the_right_tab(page, mount, case, e2e_admin_token):
    mount(case["project_id"], e2e_admin_token)
    items = {i["label"]: i for i in _items(page)}

    # 空專案 → 這些都還沒完成，而且各自有去處（section id 由 TAB_MAP 換出來，
    # 這一段只有真瀏覽器驗得到）
    for label, section in (("報價單已備", "tab_crm_quotes"),
                           ("已請款／開發票", "tab_crm_invoices"),
                           ("人員已配置", "tab_crm_projects"),
                           ("素材已進影像紀錄", "tab_media_log"),
                           ("客戶審批通過", "tab_portal"),
                           ("素材庫已索引", "tab_footage"),
                           ("場景已建檔", "tab_preprod_locations"),
                           ("參考影片已入庫", "tab_references")):
        it = items.get(label)
        assert it, f"找不到燈「{label}」：{sorted(items)}"
        assert not it["on"], f"空專案不該亮「{label}」"
        assert it["link"] == section, f"「{label}」連到 {it['link']!r}，應該是 {section}"
        assert it["marked"], f"「{label}」沒有「去完成」記號，使用者看不出來能點"

    # 管理員什麼都進得去 → 一條都不該是禁用態
    assert not [i for i in _items(page) if i["blocked"]]


def test_lit_and_manual_lamps_have_no_link(page, mount, case, e2e_admin_token):
    """亮著的沒事可做、手動項就在這頁勾 —— 兩者都不該把人送走。

    （提案一建立就有殼專案與衛星提案，所以「提案已建立」一定是亮的。）
    """
    mount(case["project_id"], e2e_admin_token)
    items = _items(page)
    lit = [i for i in items if i["on"]]
    assert lit, "至少要有一盞亮燈才驗得到這件事"
    for i in lit:
        assert not i["link"] and not i["marked"], f"亮著的「{i['label']}」不該有連結"
    for i in [x for x in items if x["manual"]]:
        assert not i["link"] and not i["marked"], f"手動項「{i['label']}」不該有連結"


def test_missing_module_disables_the_link_but_still_shows_it(page, mount, case,
                                                             user_token):
    """🔴 不藏功能：沒權限的人看得到燈、看得到缺哪個模組，只是點不動。"""
    mount(case["project_id"],
          user_token(username="企劃", modules=["preprod_proposals"]))
    items = {i["label"]: i for i in _items(page)}

    quote = items["報價單已備"]
    assert quote["link"] == "", "沒有 crm_quotes 卻給了可點的連結"
    assert quote["blocked"], "應該畫成禁用態（淡）而不是整條藏起來"
    assert "報價管理" in quote["title"] and "權限" in quote["title"], quote["title"]

    # 有的那個模組照樣可點 —— 是逐目的地判斷，不是一刀全關
    assert items["創意發想已開始"]["link"] == "tab_preprod_proposals"


def test_click_switches_tab_in_place(page, mount, case, e2e_admin_token):
    """SPA 裡點「去完成」＝原地換 tab（不是重新載入整個 SPA）。"""
    page.evaluate("""() => {
        window.__realSwitchTab = window.switchTab;
        window.__switched = [];
        window.switchTab = (t) => window.__switched.push(t);
    }""")
    try:
        mount(case["project_id"], e2e_admin_token)
        page.click(f"#{HOST} a[data-go='tab_crm_quotes']")
        page.wait_for_timeout(300)
        assert page.evaluate("() => window.__switched") == ["tab_crm_quotes"]
        # 沒有真的導航走 —— host 還在，網址也還在原頁
        assert page.eval_on_selector_all(f"#{HOST}", "e => e.length") == 1
    finally:
        page.evaluate("() => { window.switchTab = window.__realSwitchTab; }")
