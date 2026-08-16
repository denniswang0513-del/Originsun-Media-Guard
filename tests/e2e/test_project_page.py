# -*- coding: utf-8 -*-
"""專案模式（`?id=<專案id>`）—— 提案企劃頁改成專案管理頁的第一階段。

守四件事，都是「主鍵從提案換成專案」才會出現的：

  A. 標頭是**專案名 + 階段**，不是某一份提案的標題；開頁預設停在「進度」
     （全局視圖），而不是某一份提案的創意發想。
  B. 一專案 N 提案：兩筆以上才出現切換器，切換會換到那一份。
  C. 專案沒有提案（直接在 CRM 建的）→ 不是死路，給「建立提案」。
  D. 舊的 `?pid=<提案id>` 深連結**照舊**（CRM 的「開啟企劃」、我的工作台都在用）。

🔴 標頭不准出現金額：這頁的閘門比 CRM 寬（preprod_plan 的企劃人員進得來），
而 `GET /crm/projects/{id}` 是會回 contract_amount 的。

每支測試用完自己關頁 —— 留著的頁會掛著 plan-matrix 的 30s 共編輪詢到模組結束
（fixture 只在模組收尾才統一關）。

自建自刪（dev/test 庫限定）。
"""
import uuid

import pytest

from .conftest import HTTP, flow_case, project_case

pytestmark = pytest.mark.e2e


# ── A + D：專案模式的標頭與預設分頁；提案模式不受影響 ──────
@pytest.fixture(scope="module")
def case(real_server, e2e_admin_token, dev_db_only):
    with flow_case(real_server["base_url"], e2e_admin_token, "專案頁") as c:
        yield c


def test_project_mode_shows_project_and_defaults_to_flow(plan_page, case):
    """A：標頭＝專案名＋階段 chip，預設分頁＝進度，且不漏金額。"""
    pg = plan_page(f"?id={case['project_id']}", wait="#plan-side .side-tab")
    pg.wait_for_selector("#flow-host .pflow-track", timeout=30000)

    # 預設就停在進度（不是創意發想）
    active = pg.eval_on_selector(
        "#plan-side .side-tab.active", "el => el.dataset.ptab")
    assert active == "flow", f"預設分頁應該是進度，實際：{active}"
    # 進度鈕排在最前面（順序＝重要性）
    first = pg.eval_on_selector(
        "#plan-side .side-tab", "el => el.dataset.ptab")
    assert first == "flow", f"進度鈕沒排到最前：{first}"

    # 標頭＝專案名（不是提案標題），階段 chip 有字
    proj = HTTP.get(f"{case['base']}/api/v1/crm/projects/{case['project_id']}",
                    headers=case["h"]).json()
    assert pg.locator("#plan-title").text_content().strip() == proj["name"]
    assert pg.locator("#proj-stage").text_content().strip() == proj["status"]

    # 🔴 標頭區不准漏金額
    head = pg.locator(".plan-head").text_content()
    for money in ("contract_amount", "amount_receivable", "profit_target"):
        assert money not in head, f"標頭出現金額欄位：{money}"
    pg.close()


def test_legacy_proposal_deeplink_still_works(plan_page, case):
    """D：`?pid=` 照舊 —— 標題是提案名、預設仍是創意發想。"""
    pg = plan_page(f"?pid={case['prop_id']}", wait="#plan-side .side-tab")
    active = pg.eval_on_selector(
        "#plan-side .side-tab.active", "el => el.dataset.ptab")
    assert active == "plan", f"提案模式的預設分頁被改掉了：{active}"
    assert pg.locator("#proj-stage").is_visible() is False, "提案模式不該有階段 chip"
    pg.close()


def test_delivery_tab_moved_in(plan_page, case):
    """階段 2：完稿結案（歸檔清單 + 專案回顧 + 上架編輯器）搬進專案頁。

    元件與 CRM 那邊**同一份**（tabs/proposals/delivery-view.js，host 與
    fetcher 注入）—— 所以這裡驗的是「注入接對了」，不是又寫了一份。
    """
    pg = plan_page(f"?id={case['project_id']}", wait="#tab-delivery")
    pg.click("#tab-delivery")
    # 歸檔清單那半打的是 /crm/projects/{id}/archive（走注入的 _crmFetch）。
    # 一個 wait 就夠：述詞本身要求元素存在且已離開載入態
    pg.wait_for_function(
        """() => {
            const el = document.querySelector('#delivery-host .delivery-archive');
            return el && el.textContent && !el.textContent.includes('載入中');
        }""", timeout=30000)
    txt = pg.locator("#delivery-host .delivery-archive").text_content()
    assert "載入失敗" not in txt, f"歸檔清單載入失敗（fetcher 沒接對）：{txt[:120]}"
    pg.close()


# ── 人員配置（階段 B）：人看得到、錢看授權 ──────────────────
@pytest.fixture(scope="module")
def staffed(case):
    """在案子上掛一筆派工（自建自刪）：3 天 × $8,000 = $24,000。"""
    base, h = case["base"], case["h"]
    r = HTTP.post(f"{base}/api/v1/crm/staff", headers=h,
                  json={"name": f"派工測試_{uuid.uuid4().hex[:6]}", "role": "攝影",
                        "daily_rate": 8000, "status": "在職"})
    if r.status_code >= 400:
        pytest.skip(f"建不出人員（{r.status_code}）：{r.text[:120]}")
    sid = r.json().get("staff", r.json()).get("id")
    a = HTTP.post(f"{base}/api/v1/crm/projects/{case['project_id']}/staff", headers=h,
                  json={"staff_id": sid, "role_in_project": "主攝", "days": 3,
                        "rate_override": 8000, "notes": "e2e"})
    if a.status_code >= 400:
        HTTP.delete(f"{base}/api/v1/crm/staff/{sid}", headers=h)
        pytest.skip(f"建不出派工（{a.status_code}）：{a.text[:120]}")
    try:
        yield {"staff_id": sid}
    finally:
        rows = HTTP.get(f"{base}/api/v1/crm/projects/{case['project_id']}/staff",
                        headers=h).json().get("staff", [])
        for x in rows:                       # 只刪自己建的
            if x.get("staff_id") == sid:
                HTTP.delete(f"{base}/api/v1/crm/project-staff/{x['id']}", headers=h)
        HTTP.delete(f"{base}/api/v1/crm/staff/{sid}", headers=h)


def _staff_text(pg):
    pg.click("#tab-staff")
    pg.wait_for_function(
        """() => {
            const el = document.querySelector('#staff-host');
            return el && el.textContent && !el.textContent.includes('載入中');
        }""", timeout=30000)
    return pg.locator("#staff-host").text_content()


def test_staff_tab_shows_people_and_money_for_admin(plan_page, case, staffed):
    """管理員：人 + 檔期 + 日費 + 小計 + 合計都在。"""
    txt = _staff_text(plan_page(f"?id={case['project_id']}", wait="#tab-staff"))
    assert "主攝" in txt and "3 天" in txt, txt[:200]
    assert "8,000" in txt and "24,000" in txt, f"管理員看不到金額：{txt[:200]}"


def test_staff_tab_hides_money_without_grant(plan_page, case, staffed, user_token):
    """🔴 沒有 money_view：人與檔期照給，日費／小計／合計整欄不見。

    這條守的是**後端**（core/money.py 把鍵從回應裡刪掉）—— 前端就算照畫，
    畫出來的也會是空的而不是 0。所以斷言的是「畫面上沒有錢」而不是「有沒有藏」。
    """
    tok = user_token(modules=["preprod_plan", "crm_projects"])
    txt = _staff_text(plan_page(f"?id={case['project_id']}",
                                wait="#tab-staff", token=tok))
    assert "主攝" in txt and "3 天" in txt, f"人與檔期不該被藏：{txt[:200]}"
    for money in ("8,000", "24,000", "內部成本合計"):
        assert money not in txt, f"沒有金額權卻看到「{money}」：{txt[:200]}"


# ── B：一專案 N 提案 ────────────────────────────────────────
def test_switcher_appears_only_with_multiple_proposals(plan_page, case):
    """單筆時不顯示切換器；掛第二筆上去就出現，且兩筆都在選項裡。"""
    base, h, projid = case["base"], case["h"], case["project_id"]
    pg = plan_page(f"?id={projid}", wait="#plan-side .side-tab")
    assert pg.locator("#prop-switch").is_visible() is False, \
        "只有一筆提案卻畫了切換器（永遠只有一個選項的下拉是噪音）"

    r = HTTP.post(f"{base}/api/v1/proposals", headers=h,
                  json={"title": "第二個 concept", "project_id": projid})
    assert r.status_code == 200, r.text[:200]
    second = r.json()["proposal"]["id"]
    try:
        pg.close()                       # 被下一頁取代
        pg2 = plan_page(f"?id={projid}", wait="#prop-switch-sel")
        opts = pg2.eval_on_selector_all(
            "#prop-switch-sel option", "els => els.map(e => e.value)")
        assert set(opts) == {case["prop_id"], second}, opts
        # 指定哪一份就開哪一份
        pg2.close()
        pg3 = plan_page(f"?id={projid}&p={second}", wait="#prop-switch-sel")
        assert pg3.eval_on_selector("#prop-switch-sel", "el => el.value") == second
        pg3.close()
    finally:
        HTTP.delete(f"{base}/api/v1/proposals/{second}", headers=h)


# ── C：沒有提案的專案 ──────────────────────────────────────
def test_project_without_proposals_offers_to_create_one(plan_page, real_server,
                                                        e2e_admin_token, dev_db_only):
    """直接在 CRM 建的專案（沒有提案）不是死路：進度照給 + 一顆建立提案。"""
    with project_case(real_server["base_url"], e2e_admin_token, "無提案") as c:
        pg = plan_page(f"?id={c['project_id']}", wait="#mk-prop")
        # 進度照樣掛得起來（它是專案範疇的）
        pg.wait_for_selector("#flow-host .pflow-track", timeout=30000)
        # 提案範疇的分頁不亮（點了確實沒東西可看）
        for t in ("info", "plan", "refs"):
            assert pg.locator(f'#plan-side [data-ptab="{t}"]').is_visible() is False, \
                f"沒有提案卻亮著「{t}」分頁"

        pg.click("#mk-prop")
        # 建完會換網址重載 → 這次有提案了。等「無提案狀態」真的結束，
        # 不要用逗號選擇器等「兩者之一」—— Playwright 取第一個相符的元素
        # 然後在它身上等可見，而那顆正好是隱藏的切換器（單筆不顯示）。
        pg.wait_for_selector("#no-prop-cta", state="detached", timeout=30000)
        pg.wait_for_selector("#plan-side [data-ptab='plan']", state="visible",
                             timeout=30000)
        props = HTTP.get(f"{c['base']}/api/v1/proposals?project_id={c['project_id']}",
                         headers=c["h"]).json()["proposals"]
        assert len(props) == 1, f"建立提案沒生效：{props}"
        pg.close()   # 先關頁再刪提案 —— 留著的話它的共編輪詢會對已刪的提案 404
        HTTP.delete(f"{c['base']}/api/v1/proposals/{props[0]['id']}", headers=c["h"])
