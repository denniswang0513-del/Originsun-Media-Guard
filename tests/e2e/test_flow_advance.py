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

import httpx
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def case(real_server, e2e_admin_token, dev_db_only):
    """每個測試一筆新提案 —— 推進會改狀態，共用會讓測試互相汙染。"""
    base = real_server["base_url"]
    h = {"Authorization": f"Bearer {e2e_admin_token}"}
    r = httpx.post(f"{base}/api/v1/proposals", headers=h, timeout=60,
                   json={"title": f"推進回歸_{uuid.uuid4().hex[:6]}", "ptype": "其他"})
    if r.status_code >= 400:
        pytest.skip(f"建不出提案（{r.status_code}）：{r.text[:120]}")
    p = r.json()["proposal"]
    if not p.get("project_id"):
        pytest.skip("這筆提案沒有殼專案")
    yield {"base": base, "h": h, "prop_id": p["id"], "project_id": p["project_id"]}
    httpx.delete(f"{base}/api/v1/proposals/{p['id']}", headers=h, timeout=30)
    httpx.delete(f"{base}/api/v1/crm/projects/{p['project_id']}", headers=h, timeout=30)


@pytest.fixture
def mount(page):
    """掛上元件；測試結束（**含失敗**）一定拆掉。

    `page` 是 session 範圍的 —— 留著的 host 帶著已綁的事件委派會跑進別支
    測試檔。收在 fixture 的 teardown 而不是每個測試最後一行：失敗時最需要
    清理，而那正是「最後一行」跑不到的時候。
    """
    def _do(project_id, token):
        page.evaluate("t => localStorage.setItem('auth_token', t)", token)
        page.evaluate("""async (pid) => {
            const host = document.createElement('div');
            host.id = 'advtest';
            document.body.appendChild(host);
            const m = await import('/tabs/proposals/flow-view.js');
            await m.renderFlow(host, { projectId: pid });
        }""", project_id)
        page.wait_for_selector("#advtest .pflow-track", timeout=20000)
    yield _do
    page.evaluate("() => document.getElementById('advtest')?.remove()")


def _btn(page):
    return page.eval_on_selector_all(
        "#advtest [data-advance], #advtest .pflow-adv",
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
    page.wait_for_timeout(600)

    d = httpx.get(f"{case['base']}/api/v1/crm/projects/{case['project_id']}/flow",
                  headers=case["h"], timeout=30).json()
    assert d["stage"]["status"] == "提案", f"取消後階段不該變：{d['stage']['status']}"


def test_confirm_advances_and_runs_side_effects(page, mount, case, e2e_admin_token):
    """🔴 確認後真的推進，而且走的是既有端點的副作用鏈（衛星提案跟著成案）。"""
    mount(case["project_id"], e2e_admin_token)
    page.click("#advtest [data-advance]")
    page.wait_for_selector("#pflow-go", timeout=10000)
    # 進「製作」＝這案子拿到了 → 對話框要收成案原因
    assert page.locator("#pflow-reason").count() == 1, "推進到製作要能填成案原因"
    reason = "測試成案原因_" + uuid.uuid4().hex[:4]
    page.fill("#pflow-reason", reason)
    page.click("#pflow-go")
    page.wait_for_timeout(2500)          # PATCH + renderFlow 重抓

    d = httpx.get(f"{case['base']}/api/v1/crm/projects/{case['project_id']}/flow",
                  headers=case["h"], timeout=30).json()
    assert d["stage"]["status"] == "製作", f"階段沒推進：{d['stage']['status']}"

    # 副作用鏈：衛星提案標成案 + 原因寫進組織學習欄
    gp = httpx.get(f"{case['base']}/api/v1/proposals/{case['prop_id']}",
                   headers=case["h"], timeout=30).json()
    gp = gp.get("proposal") or gp
    assert gp["status"] == "成案", f"衛星提案沒跟著成案：{gp['status']}"
    assert gp.get("outcome_reason") == reason, \
        f"成案原因沒寫進去：{gp.get('outcome_reason')!r}"

    # 畫面也跟上了（重抓不是只改按鈕字）
    assert page.eval_on_selector_all(
        "#advtest .pflow-step.cur", "els => els.map(e => e.textContent.trim())") == ["製作"]
    page.evaluate("() => document.getElementById('advtest')?.remove()")
