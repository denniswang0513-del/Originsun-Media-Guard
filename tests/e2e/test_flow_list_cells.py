# -*- coding: utf-8 -*-
"""提案清單的「階段 chip + 五格微型完成條」（§14.4 階段四B）。

清單掃一眼要看得出**哪個案子哪一軌卡住**，所以這裡守三件事：

  A. 批次端點與詳情面板**同一份判定** —— 清單顯示 3/5、點進去 2/5 是這個
     功能最快失去信任的方式。所以直接拿兩支端點的數字對撞。
  B. 沒有殼專案（或摘要沒到）畫「—」，不畫五個空格子 —— 空格子跟「五軌全零」
     長得一樣，那是謊報。
  C. 兩個清單（後台 SPA / 獨立企劃頁）都真的畫得出來。畫面那半是動態 import
     的，載入失敗只會安靜地留下空格。

自建自刪（dev/test 庫限定）。
"""
import pytest

from .conftest import HTTP, flow_case

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def case(real_server, e2e_admin_token, dev_db_only):
    with flow_case(real_server["base_url"], e2e_admin_token, "清單進度") as c:
        yield c


def test_summary_agrees_with_the_detail_panel(case):
    """🔴 A：批次摘要與單筆詳情必須逐軌相等（同一支 _gather_facts）。"""
    base, h, pid = case["base"], case["h"], case["project_id"]
    detail = HTTP.get(f"{base}/api/v1/crm/projects/{pid}/flow", headers=h).json()
    summary = HTTP.get(f"{base}/api/v1/crm/projects/flow/summary?ids={pid}",
                       headers=h).json()["projects"][pid]

    assert summary["status"] == detail["stage"]["status"]
    assert [(t["key"], t["done"], t["total"]) for t in summary["tracks"]] == \
           [(t["key"], t["done"], t["total"]) for t in detail["tracks"]], \
        "清單與詳情的軌道數字不一致 —— 兩邊的判定漂了"


def test_summary_is_lean_and_batched(case):
    """摘要不帶燈號明細（清單用不到），而且一次問得動多個專案。"""
    base, h, pid = case["base"], case["h"], case["project_id"]
    d = HTTP.get(f"{base}/api/v1/crm/projects/flow/summary?ids={pid},{pid},nope",
                 headers=h).json()["projects"]
    assert set(d) == {pid}, f"不存在的 id 不該回東西：{sorted(d)}"
    assert "items" not in d[pid]["tracks"][0], "摘要不該帶燈號明細（那是 4KB/專案）"
    assert HTTP.get(f"{base}/api/v1/crm/projects/flow/summary", headers=h) \
               .json()["projects"] == {}


@pytest.fixture(scope="module")
def pg(browser_context, real_server, e2e_admin_token, case):
    """獨立企劃頁的清單。

    ⚠️ 不驗後台 SPA 的那份清單：SPA 的 `loadTabs` 在 `/auth/me` 回來**之前**
    就依 `shouldShowTab` 決定要不要載入分頁，所以只塞 localStorage 的 token
    是進不去提案庫 section 的（這也是 mount_flow 當初改成直接 import 子視圖
    模組的同一個理由）。兩個清單畫的是同一支 `flowCellsHtml`，接線那半在
    這裡驗得到；後台那半的差異只有「哪個元素放格子」。
    """
    page = browser_context.new_page()
    page.goto(real_server["base_url"] + "/proposal-plan.html", timeout=60000)
    page.evaluate("t => localStorage.setItem('auth_token', t)", e2e_admin_token)
    page.goto(real_server["base_url"] + "/proposal-plan.html", timeout=60000)
    page.wait_for_selector("#list-host .prop-row", timeout=30000)
    yield page
    page.close()


def test_list_paints_cells(pg, case):
    """🔴 C：清單真的畫出 chip + 五格；沒有殼專案的列畫「—」。"""
    # 摘要是清單畫完之後才補上的 → 等那一格真的有東西
    pg.wait_for_function(
        "() => [...document.querySelectorAll('#list-host [data-flow]')]"
        ".some(el => el.children.length > 0)", timeout=30000)

    cells = pg.eval_on_selector_all("#list-host [data-flow]", """els => els.map(e => ({
        pid: e.dataset.flow,
        bars: e.querySelectorAll('.pfb-cell').length,
        stage: e.querySelector('.pfb-stage')?.textContent.trim() || '',
        dash: !!e.querySelector('.pfb-none'),
    }))""")
    assert cells, "清單一列都沒有"
    mine = [c for c in cells if c["pid"] == case["project_id"]]
    assert mine, f"找不到剛建的那筆：{[c['pid'] for c in cells][:5]}"
    assert mine[0]["bars"] == 5, f"應該是五軌五格：{mine[0]}"
    assert mine[0]["stage"] == "提案", mine[0]
    # B：沒有殼專案的列畫「—」，不畫空格子
    for c in cells:
        if not c["pid"]:
            assert c["dash"] and c["bars"] == 0, f"沒有專案卻畫了格子：{c}"


def test_cells_are_not_faked_when_summary_is_missing(pg):
    """🔴 B 的另一半：`flowCellsHtml(null)` 是「—」，不是五個空格子。

    空格子跟「五軌全零」長得一模一樣 —— 那會把「還沒問到」畫成「什麼都沒做」。
    """
    got = pg.evaluate("""async () => {
        const m = await import('/tabs/proposals/flow-badge.js');
        const d = document.createElement('div');
        d.innerHTML = m.flowCellsHtml(null);
        return { cells: d.querySelectorAll('.pfb-cell').length,
                 dash: !!d.querySelector('.pfb-none') };
    }""")
    assert got == {"cells": 0, "dash": True}, got
