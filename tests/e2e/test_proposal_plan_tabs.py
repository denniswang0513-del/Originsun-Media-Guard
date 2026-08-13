# -*- coding: utf-8 -*-
"""`/proposal-plan.html` 側欄兩個新分頁（2026-08-14）：

  - **參考影片**：原本壓在「基本資料」最底下，owner 要求搬成獨立分頁。
    搬家的風險不在版面而在**接線**：標題/備註的自動儲存原本由
    `_wireInfoEditing(info-host)` 接，欄位搬走之後那支就找不到任何 `.ref-in`
    —— 症狀是「改了字，看起來好好的，重整就沒了」。所以這裡不只驗畫得出來，
    還要驗**改一個字真的存進 DB**。
  - **進度**：五軌工作流（§14 階段四把它接上獨立頁）。這頁是官網白底，
    元件預設深色 —— 順帶驗主題變數有生效（不是一片黑貼在白紙上）。

企劃人員主要在這頁工作，兩個分頁壞掉不會有人在後台 SPA 發現。
只在 dev/測試庫跑（自建自刪）。
"""
import uuid

import httpx
import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def prop(real_server, e2e_admin_token, dev_db_only):
    base = real_server["base_url"]
    h = {"Authorization": f"Bearer {e2e_admin_token}"}
    tag = uuid.uuid4().hex[:6]
    # 要帶 client_id 才會建殼專案 —— 沒有殼專案就沒有「進度」分頁可驗
    # （db.models.CrmProject：只有提案建殼這條路允許 client_id 為空）
    rc = httpx.post(f"{base}/api/v1/crm/clients", headers=h, timeout=60,
                    json={"name": f"[分頁回歸] {tag}", "short_name": f"TAB{tag}"})
    if rc.status_code >= 400:
        pytest.skip(f"建不出客戶（{rc.status_code}）：{rc.text[:120]}")
    cid = rc.json().get("id") or (rc.json().get("client") or {}).get("id")

    r = httpx.post(f"{base}/api/v1/proposals", headers=h, timeout=60,
                   json={"title": f"分頁回歸_{tag}", "ptype": "品牌形象",
                         "client_id": cid})
    if r.status_code >= 400:
        httpx.delete(f"{base}/api/v1/crm/clients/{cid}", headers=h, timeout=30)
        pytest.skip(f"建不出提案（{r.status_code}）：{r.text[:120]}")
    p = r.json()["proposal"]
    yield {"id": p["id"], "project_id": p.get("project_id") or "", "base": base, "h": h}
    # 各刪各的 —— 交叉刪（拿提案 id 去打 projects）有機會刪到別人的 dev 資料
    httpx.delete(f"{base}/api/v1/proposals/{p['id']}", headers=h, timeout=30)
    if p.get("project_id"):
        httpx.delete(f"{base}/api/v1/crm/projects/{p['project_id']}", headers=h, timeout=30)
    httpx.delete(f"{base}/api/v1/crm/clients/{cid}", headers=h, timeout=30)


@pytest.fixture(scope="module")
def pg(browser_context, real_server, e2e_admin_token, prop):
    page = browser_context.new_page()
    page.goto(real_server["base_url"] + "/proposal-plan.html", timeout=60000)
    page.evaluate("t => localStorage.setItem('auth_token', t)", e2e_admin_token)
    page.goto(f"{real_server['base_url']}/proposal-plan.html?pid={prop['id']}",
              timeout=60000)
    # 側欄要等 _showSideTabs() 把 #plan-side 顯示出來（在資料載完之後）
    page.wait_for_selector("#plan-side .side-tab", state="visible", timeout=30000)
    page.wait_for_timeout(1200)
    yield page
    page.close()


def _open(pg, ptab):
    pg.click(f'#plan-side .side-tab[data-ptab="{ptab}"]')
    pg.wait_for_timeout(800)


def test_refs_tab_exists_and_renders(pg):
    """參考影片自成一個分頁，而且不再留在基本資料裡（不然就是複製不是搬家）。"""
    tabs = pg.eval_on_selector_all(
        "#plan-side .side-tab", "els => els.map(e => e.textContent.trim())")
    assert "參考影片" in tabs, f"側欄沒有參考影片分頁：{tabs}"

    _open(pg, "refs")
    host = pg.eval_on_selector("#refs-host", "e => e.innerHTML")
    assert "參考影片" in host and "ref-add" in host, host[:300]
    # 搬家 ≠ 複製：基本資料那邊不該還留一份
    info = pg.eval_on_selector("#info-host", "e => e.innerHTML")
    assert "ref-new-url" not in info, "基本資料裡還留著參考影片的控件（重複了）"


def test_ref_note_autosave_survives_reload(pg, prop):
    """🔴 搬家最容易斷的是自動儲存接線 —— 改一個字要真的存進 DB。"""
    base, h = prop["base"], prop["h"]
    # 兩步（同 prop-actions.addRefByUrl）：先進共用片庫，再掛到這個提案
    r = httpx.post(f"{base}/api/v1/proposals/references", headers=h, timeout=60,
                   json={"url": f"https://example.com/{uuid.uuid4().hex[:8]}",
                         "title": "原標題"})
    if r.status_code >= 400:
        pytest.skip(f"加不進片庫（{r.status_code}）：{r.text[:120]}")
    rid = r.json()["reference"]["id"]
    r2 = httpx.post(f"{base}/api/v1/proposals/{prop['id']}/refs", headers=h,
                    timeout=60, json={"reference_id": rid})
    if r2.status_code >= 400:
        pytest.skip(f"掛不上提案（{r2.status_code}）：{r2.text[:120]}")

    pg.reload(timeout=60000)
    pg.wait_for_selector("#plan-side .side-tab", timeout=30000)
    pg.wait_for_timeout(1200)
    _open(pg, "refs")
    pg.wait_for_selector('#refs-host .ref-in[data-rfield="note"]', timeout=15000)

    want = "自動儲存驗證_" + uuid.uuid4().hex[:4]
    pg.fill('#refs-host .ref-in[data-rfield="note"]', want)
    pg.eval_on_selector('#refs-host .ref-in[data-rfield="note"]', "e => e.blur()")
    pg.wait_for_timeout(1500)            # debounce 800ms + 一趟 API

    fresh = httpx.get(f"{base}/api/v1/proposals/{prop['id']}", headers=h, timeout=30).json()
    refs = (fresh.get("proposal") or fresh).get("references") or []
    assert refs and refs[0].get("note") == want, \
        f"備註沒存進 DB（接線斷了）：{[r.get('note') for r in refs]}"


def test_flow_tab_renders_with_light_theme(pg, prop):
    """進度分頁在白底頁上要吃得到主題變數（元件預設是深色）。"""
    if not prop["project_id"]:
        pytest.skip("這筆提案沒有殼專案（無 client_id），進度分頁本來就不建")

    tabs = pg.eval_on_selector_all(
        "#plan-side .side-tab", "els => els.map(e => e.textContent.trim())")
    assert "進度" in tabs, f"側欄沒有進度分頁：{tabs}"

    _open(pg, "flow")
    pg.wait_for_selector("#flow-host .pflow-track", timeout=20000)
    got = pg.eval_on_selector_all("#flow-host .pflow-tname",
                                  "els => els.map(e => e.textContent.trim())")
    assert got == ["商務", "企劃", "製作", "交付", "收割"], got

    # 白底：主題變數要被 html.plan-theme-light 覆寫成淺色（不是深色預設）
    bg = pg.eval_on_selector(
        "#flow-host .pflow",
        "e => getComputedStyle(e).getPropertyValue('--pf-card').trim()")
    assert bg.lower() in ("#fafafa", "rgb(250, 250, 250)"), \
        f"白底頁沒吃到淺色主題（--pf-card={bg!r}）"
