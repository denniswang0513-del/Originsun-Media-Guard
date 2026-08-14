# -*- coding: utf-8 -*-
"""未亮的燈上的「去完成」deep-link（§14.4 階段四）。

單元測試守得住資料面（每個 AUTO 項都有去處、鍵是合法模組鍵、鍵在 TAB_MAP
裡有 tab、只有未亮的燈帶 dest）。這裡守的是**只有瀏覽器看得到**的那一段：

  A. payload 的 dest 真的變成一條指向對的 section 的 `<a>`。
  B. 沒有目的地模組的人看得到燈也看得到缺什麼權限，但點不動（不藏功能）。
  C. 在 SPA 上點下去是**原地換 tab** —— 靠的是 `/#section` 只換 hash，
     由 app.js 既有的 hashchange 路由接手，這個元件不攔 click。

自建自刪（dev/test 庫限定）。
"""
import pytest

from .conftest import flow_case

pytestmark = pytest.mark.e2e

HOST = "flowlink-host"


@pytest.fixture(scope="module")
def case(real_server, e2e_admin_token, dev_db_only):
    """整檔共用一筆空專案 —— 這裡每支測試都只是渲染，不改資料。

    （推進那支不能這樣共用，見 test_flow_advance 的同名 fixture。）
    """
    with flow_case(real_server["base_url"], e2e_admin_token, "連結回歸") as c:
        yield c


def _items(page):
    """每盞燈：標籤、亮沒亮、是不是連結、連去哪。"""
    return page.eval_on_selector_all(f"#{HOST} .pflow-item", """els => els.map(e => ({
        label: e.textContent.trim(),
        on: e.classList.contains('on'),
        skip: e.classList.contains('skip'),
        manual: e.classList.contains('manual'),
        link: e.tagName === 'A' ? (e.getAttribute('href') || '') : '',
        blocked: e.classList.contains('nogo'),
        title: e.getAttribute('title') || '',
    }))""")


def test_unlit_auto_lamps_link_to_their_tab(page, mount_flow, case, e2e_admin_token):
    """未亮的自動燈**全部**要有一條指向真 section 的連結，一盞都不能漏。

    逐項對照 (標籤, section) 的表已經刪掉：那是把 ITEM_DEST 抄到測試裡再比
    一次，而 TAB_MAP 那一段單元測試對全部 10 個目的地都驗過了。這裡只驗
    「資料真的變成連結」，外加一條代表性的具名斷言。
    """
    mount_flow(case["project_id"], e2e_admin_token, host_id=HOST)
    items = _items(page)
    assert items, "一盞燈都沒畫出來"

    unlit = [i for i in items if not i["on"] and not i["skip"] and not i["manual"]]
    assert len(unlit) > 5, f"空專案該有一整排未亮的燈，只有 {len(unlit)} 盞"
    for i in unlit:
        assert i["link"].startswith("/#tab_"), f"「{i['label']}」沒連到任何 tab：{i}"

    by = {i["label"]: i for i in items}
    assert by["報價單已備"]["link"] == "/#tab_crm_quotes", by["報價單已備"]
    # 管理員什麼都進得去 → 一條都不該是禁用態
    assert not [i for i in items if i["blocked"]]
    # 亮著的沒事可做、手動項就在這頁勾 —— 兩者都不該把人送走
    for i in items:
        if i["on"] or i["manual"]:
            assert not i["link"], f"「{i['label']}」不該有連結"


def test_missing_module_disables_the_link_but_still_shows_it(page, mount_flow, case,
                                                             user_token):
    """🔴 不藏功能：沒權限的人看得到燈、看得到缺哪個模組，只是點不動。"""
    mount_flow(case["project_id"],
               user_token(username="企劃", modules=["preprod_proposals"]),
               host_id=HOST)
    by = {i["label"]: i for i in _items(page)}

    quote = by["報價單已備"]
    assert quote["link"] == "", "沒有 crm_quotes 卻給了可點的連結"
    assert quote["blocked"], "應該畫成禁用態（淡）而不是整條藏起來"
    assert "報價管理" in quote["title"] and "權限" in quote["title"], quote["title"]

    # 🔴 片庫的閘門收 preprod_proposals（api_references._ACCESS_MODULES）——
    # 只認同名模組的話，這個人會在自己進得去的 tab 上看到「你沒有權限」
    assert by["參考影片已入庫"]["link"] == "/#tab_references", \
        f"片庫的額外放行沒吃到：{by['參考影片已入庫']}"


def test_click_stays_in_the_same_document(page, mount_flow, case, e2e_admin_token):
    """🔴 在 SPA 自己的網址上，點「去完成」**不能重新載入整頁**。

    這是這個元件唯一擁有的一半：href 寫成同文件的 `/#tab_x`、不加 target，
    於是點下去只換 hash。接手換 tab 的是 app.js 既有的 hashchange 路由
    （`app.js` 的「Deep-link: react to manual hash changes」）—— 那是它的
    契約，這裡不重測（也測不到：這個 harness 的 SPA 是匿名開機的，CRM 的
    section 根本沒載進 DOM）。

    做錯的症狀就是這支會紅：加了 target="_blank" 會開新分頁、指到別的路徑
    會整頁重載，兩者都會把使用者手上的東西弄丟。
    """
    mount_flow(case["project_id"], e2e_admin_token, host_id=HOST)
    was = page.evaluate("() => location.hash")
    page.evaluate("() => { window.__samePage = true; }")   # 重新載入就會被清掉
    try:
        page.click(f"#{HOST} a[href='/#tab_crm_quotes']")
        page.wait_for_function("() => location.hash === '#tab_crm_quotes'", timeout=5000)
        assert page.evaluate("() => window.__samePage") is True, "整頁重新載入了"
    finally:
        # 🔴 page 是 session 範圍的：留下的 hash 會跟著後面每一支測試，而
        # 「已經站在那個 tab 上就不畫連結」的規則會讓別支測試少一條連結。
        page.evaluate("h => { location.hash = h; }", was or "#")
