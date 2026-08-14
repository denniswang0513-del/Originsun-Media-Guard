# -*- coding: utf-8 -*-
"""進度分頁的「推進階段」按鈕（§14 階段三）。

推進是這個功能唯一會**動到商務主軸**的動作：客戶分級、錢流口徑、衛星提案的
win/loss 都掛在專案狀態上。所以三件事要釘住：

  A. 權限分層 —— 看得到 ≠ 勾得動 ≠ 推得動。非管理員的按鈕要是 disabled，
     而不是按下去才吃 403（按鈕看起來能按卻永遠失敗最傷信任）。
  B. 軟擋（owner 決策點 2）—— 缺項只列出來，確認後仍然推得動；取消就不推。
  C. 推進真的走既有端點的副作用鏈 —— 不是前端自己改個字。

只在 dev/測試庫跑（自建自刪）。
"""
import uuid

import pytest

from .conftest import HTTP, flow_case

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def case(real_server, e2e_admin_token, dev_db_only):
    """唯讀那幾支共用一筆 —— 它們只是渲染或按了取消，什麼都沒改。"""
    with flow_case(real_server["base_url"], e2e_admin_token, "推進回歸") as c:
        yield c


@pytest.fixture
def mount(mount_flow):
    """掛在固定的 #advtest 上（本檔的選擇器都寫死這個 id）。"""
    return lambda project_id, token: mount_flow(project_id, token, host_id="advtest")


def _btn(page):
    # 兩種按鈕都有 .pflow-adv（啟用的那顆多一個 data-advance）—— 用單一
    # 選擇器，「應該只有一顆」才不是靠 querySelectorAll 去重湊出來的
    return page.eval_on_selector_all(
        "#advtest .pflow-adv",
        "els => els.map(e => ({ label: e.textContent.trim(),"
        " disabled: e.disabled, next: e.dataset.advance || '' }))")


def test_admin_sees_enabled_button(page, mount, case, e2e_admin_token):
    mount(case["project_id"], e2e_admin_token)
    btns = _btn(page)
    assert len(btns) == 1, f"應該只有一顆推進鈕：{btns}"
    assert btns[0]["next"] == "製作", btns[0]
    assert btns[0]["disabled"] is False, "管理員的推進鈕不該是 disabled"


def test_non_admin_button_is_disabled_not_403(page, mount, case, user_token):
    """🔴 非管理員看得到按鈕但按不下去 —— 不是按了才吃 403。"""
    mount(case["project_id"],
          user_token(username="製片", modules=["preprod_proposals", "crm_projects"]))
    btns = _btn(page)
    assert len(btns) == 1 and btns[0]["disabled"] is True, \
        f"非管理員的推進鈕應該是 disabled：{btns}"
    # 勾選權還在（模組級鬆綁）—— 兩個權限是分開的
    assert page.eval_on_selector_all("#advtest [data-check]", "e => e.length") > 0, \
        "有 crm_projects 的人還是要勾得動里程碑"


def test_cancel_does_not_advance(page, mount, case, e2e_admin_token):
    """取消＝什麼都不做（軟擋的另一半：確認框不是裝飾）。"""
    mount(case["project_id"], e2e_admin_token)
    page.click("#advtest [data-advance]")
    page.wait_for_selector("#pflow-cancel", timeout=10000)
    page.click("#pflow-cancel")
    page.wait_for_selector("#pflow-cancel", state="detached", timeout=10000)

    d = HTTP.get(f"{case['base']}/api/v1/crm/projects/{case['project_id']}/flow",
                  headers=case["h"], timeout=30).json()
    assert d["stage"]["status"] == "提案", f"取消後階段不該變：{d['stage']['status']}"


def test_confirm_advances_and_runs_side_effects(page, mount, real_server,
                                                e2e_admin_token, dev_db_only):
    """🔴 確認後真的推進，而且走的是既有端點的副作用鏈（衛星提案跟著成案）。

    自己開一筆（不用上面那個 module 範圍的）—— 這支會真的改狀態，留給別人
    重用的話，唯讀那幾支就變成依賴檔案順序才會過。
    """
    with flow_case(real_server["base_url"], e2e_admin_token, "推進回歸") as fresh_case:
        _confirm_and_check(page, mount, fresh_case, e2e_admin_token)


def _confirm_and_check(page, mount, fresh_case, e2e_admin_token):
    mount(fresh_case["project_id"], e2e_admin_token)
    page.click("#advtest [data-advance]")
    page.wait_for_selector("#pflow-go", timeout=10000)
    # 進「製作」＝這案子拿到了 → 對話框要收成案原因
    assert page.locator("#pflow-reason").count() == 1, "推進到製作要能填成案原因"
    reason = "測試成案原因_" + uuid.uuid4().hex[:4]
    page.fill("#pflow-reason", reason)
    page.click("#pflow-go")
    # 🔴 等**條件**：PATCH + renderFlow 重畫完，階段列的目前站變成「製作」。
    # 原本固定睡 2.5 秒 —— 實測條件在 50ms 就成立，而睡秒數的版本在機器變快
    # 或變慢時都不對：它等於沒有等待條件，只是賭時鐘。
    page.wait_for_function(
        "() => [...document.querySelectorAll('#advtest .pflow-step.cur')]"
        ".some(e => e.textContent.trim() === '製作')", timeout=20000)

    d = HTTP.get(f"{fresh_case['base']}/api/v1/crm/projects/{fresh_case['project_id']}/flow",
                  headers=fresh_case["h"], timeout=30).json()
    assert d["stage"]["status"] == "製作", f"階段沒推進：{d['stage']['status']}"

    # 副作用鏈：衛星提案標成案 + 原因寫進組織學習欄
    gp = HTTP.get(f"{fresh_case['base']}/api/v1/proposals/{fresh_case['prop_id']}",
                   headers=fresh_case["h"], timeout=30).json()
    gp = gp.get("proposal") or gp
    assert gp["status"] == "成案", f"衛星提案沒跟著成案：{gp['status']}"
    assert gp.get("outcome_reason") == reason, \
        f"成案原因沒寫進去：{gp.get('outcome_reason')!r}"

    # 畫面也跟上了（重抓不是只改按鈕字）
    assert page.eval_on_selector_all(
        "#advtest .pflow-step.cur", "els => els.map(e => e.textContent.trim())") == ["製作"]
