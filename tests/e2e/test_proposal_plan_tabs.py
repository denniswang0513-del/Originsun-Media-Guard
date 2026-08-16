# -*- coding: utf-8 -*-
"""`/project.html` 側欄兩個新分頁（2026-08-14）：

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

import pytest

from .conftest import HTTP, flow_case

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def prop(real_server, e2e_admin_token, dev_db_only):
    """建/跳過/收的契約共用 conftest 的 flow_case。

    原本這裡自己建了一個客戶，理由寫的是「要帶 client_id 才會建殼專案」——
    那句是錯的（api_proposals._create_shell_project：客戶未定也照建），而且
    本檔沒有任何斷言用到那個客戶。
    """
    with flow_case(real_server["base_url"], e2e_admin_token, "分頁回歸") as c:
        yield {"id": c["prop_id"], "project_id": c["project_id"],
               "base": c["base"], "h": c["h"]}


@pytest.fixture(scope="module")
def pg(plan_page, prop):
    # 側欄要等 _showSideTabs() 把 #plan-side 顯示出來（在資料載完之後）
    page = plan_page(f"?pid={prop['id']}")
    _settled(page)
    return page


def _settled(pg):
    """等基本資料那格真的填好 —— 等條件不是等秒數（實測 0.25s，原本睡 1.2s）。"""
    pg.wait_for_function("() => document.getElementById('info-host')?.children.length > 0",
                         timeout=30000)


def _open(pg, ptab):
    pg.click(f'#plan-side .side-tab[data-ptab="{ptab}"]')
    # 分頁是 lazy import 的 → 等那格有東西，不要睡固定秒數
    pg.wait_for_function(
        "id => document.getElementById(id)?.children.length > 0",
        arg=f"{ptab}-host", timeout=30000)


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
    r = HTTP.post(f"{base}/api/v1/proposals/references", headers=h, timeout=60,
                   json={"url": f"https://example.com/{uuid.uuid4().hex[:8]}",
                         "title": "原標題"})
    if r.status_code >= 400:
        pytest.skip(f"加不進片庫（{r.status_code}）：{r.text[:120]}")
    rid = r.json()["reference"]["id"]
    r2 = HTTP.post(f"{base}/api/v1/proposals/{prop['id']}/refs", headers=h,
                    timeout=60, json={"reference_id": rid})
    if r2.status_code >= 400:
        pytest.skip(f"掛不上提案（{r2.status_code}）：{r2.text[:120]}")

    pg.reload(timeout=60000)
    pg.wait_for_selector("#plan-side .side-tab", timeout=30000)
    _settled(pg)
    _open(pg, "refs")
    pg.wait_for_selector('#refs-host .ref-in[data-rfield="note"]', timeout=15000)

    want = "自動儲存驗證_" + uuid.uuid4().hex[:4]
    pg.fill('#refs-host .ref-in[data-rfield="note"]', want)
    pg.eval_on_selector('#refs-host .ref-in[data-rfield="note"]', "e => e.blur()")
    pg.wait_for_timeout(1500)            # debounce 800ms + 一趟 API

    fresh = HTTP.get(f"{base}/api/v1/proposals/{prop['id']}", headers=h, timeout=30).json()
    refs = (fresh.get("proposal") or fresh).get("references") or []
    assert refs and refs[0].get("note") == want, \
        f"備註沒存進 DB（接線斷了）：{[r.get('note') for r in refs]}"


def test_flow_tab_renders_with_light_theme(pg, prop):
    """進度分頁在白底頁上要吃得到主題變數（元件預設是深色）。"""
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

    # 🔴 這頁不是 SPA —— 「去完成」必須開新分頁。同一個 href 在後台是原地換
    # tab（只換 hash，test_flow_links 驗），從這頁點卻是一次跨頁導覽，會把
    # 企劃人員手上正在編的東西整個帶走。判斷在 flow-view 的 NEW_TAB
    # （比對 pathname），**只有在這頁看得到它另一半有沒有做對**。
    links = pg.eval_on_selector_all(
        "#flow-host a.pflow-item, #flow-host a.pflow-golink",
        "els => els.map(e => ({ href: e.getAttribute('href'),"
        " target: e.getAttribute('target') }))")
    assert links, "空專案的未亮燈上一條 deep-link 都沒有"
    for a in links:
        assert a["target"] == "_blank", f"這頁的連結要開新分頁：{a}"
        assert a["href"].startswith("/#tab_"), a
