# -*- coding: utf-8 -*-
"""未亮的燈上的「去完成」deep-link（§14.4 階段四）。

資料面（哪盞燈有去處、去處合不合法）由單元測試守。這裡守的是**只有瀏覽器
看得到**的那一段：

  A. payload 的 dest 真的變成一條指向對的 section 的 `<a>`。
  B. 沒有目的地模組的人看得到燈也看得到缺什麼權限，但點不動。
  C. 在 SPA 上點下去是原地換 tab，不是整頁重新載入。
  D. 指回「這個畫面本身」的燈不畫連結（`here`）。

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
    """未亮的自動燈要有一條指向真 section 的連結，一盞都不能漏。

    逐項對照 (標籤, section) 的表刻意不寫：那是把 ITEM_DEST 抄到測試裡再比
    一次，而 TAB_MAP 那一段單元測試對全部 10 個目的地都驗過了。這裡驗的是
    「資料真的變成連結」，外加一條代表性的具名斷言。
    """
    mount_flow(case["project_id"], e2e_admin_token, host_id=HOST, here="")
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
    # （亮著的／手動項沒有連結，是後端不給 dest 的結果 ——
    #   由 test_project_flow.test_only_unlit_auto_rows_carry_a_destination 釘）


def test_here_suppresses_links_back_to_this_workspace(page, mount_flow, case,
                                                      e2e_admin_token):
    """🔴 兩個掛載點都在提案工作區裡 —— 指回提案庫的燈不該畫連結。

    「去完成」把人送到他已經站著的地方，就是一顆點了不會發生任何事的按鈕。
    對照上一支（here=''）：同一盞燈在那裡是有連結的，所以這裡沒連結是
    `here` 造成的，不是漏了 dest。
    """
    mount_flow(case["project_id"], e2e_admin_token, host_id=HOST)
    by = {i["label"]: i for i in _items(page)}

    for label in ("創意發想已開始", "企劃書已產出", "企劃範本已收割"):
        assert by[label]["link"] == "", f"「{label}」不該連回自己所在的工作區"
        assert not by[label]["blocked"], f"「{label}」不是權限問題，不該畫成禁用態"
    # 別處的燈照樣有連結（不是整片關掉）
    assert by["報價單已備"]["link"] != ""


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

    # 🔴 片庫收不只同名模組（core.auth.TAB_ACCESS）—— 這個人進得去，
    # 所以那盞燈要是可點的連結，不是「你沒有權限」
    assert by["參考影片已入庫"]["link"] == "/#tab_references", \
        f"片庫的額外放行沒吃到：{by['參考影片已入庫']}"


def test_click_stays_in_the_same_document(page, mount_flow, case, e2e_admin_token):
    """🔴 在 SPA 自己的網址上，點「去完成」**不能重新載入整頁**。

    這是這個元件唯一擁有的一半（href 同文件、不加 target）。接手換 tab 的是
    app.js 既有的 hashchange 路由 —— 那是它的契約，這裡不重測，也測不到：
    這個 harness 的 SPA 是匿名開機的，CRM 的 section 根本沒載進 DOM。
    """
    mount_flow(case["project_id"], e2e_admin_token, host_id=HOST)
    page.evaluate("() => { window.__samePage = true; }")   # 重新載入就會被清掉
    page.click(f"#{HOST} a[href='/#tab_crm_quotes']")
    page.wait_for_function("() => location.hash === '#tab_crm_quotes'", timeout=5000)
    assert page.evaluate("() => window.__samePage") is True, "整頁重新載入了"
