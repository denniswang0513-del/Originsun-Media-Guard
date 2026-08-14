# -*- coding: utf-8 -*-
"""提案詳情「進度」分頁真的畫得出來（docs/PROPOSAL_PLANNER.md §14 階段一）。

只驗 API 不夠 —— 分頁是 lazy import 的，模組載入失敗只會在畫面上留一行
「元件載入失敗」，後端測試永遠看不到。這裡讓瀏覽器真的載入 flow-view.js
並渲染一次。

自建自刪（dev/test 庫限定）。
"""
import pytest

from .conftest import project_case

pytestmark = pytest.mark.e2e


HOST = "flowtest-host"


def _read(page):
    """讀出**結構化**的渲染結果，不是整包 innerHTML。

    🔴 軌道名用子字串比對會誤判（「收割」兩個字也出現在項目名「企劃範本已
    收割」裡 —— 實測把軌道名改掉測試照樣過），所以比對的是節點本身。
    """
    return page.eval_on_selector(f"#{HOST}", """host => {
        const q = s => [...host.querySelectorAll(s)].map(e => e.textContent.trim());
        return {
            html: host.innerHTML,
            tracks: q('.pflow-tname'),
            items: q('.pflow-item'),
            lit: q('.pflow-item.on'),
            stages: q('.pflow-step'),
            current: q('.pflow-step.cur'),
            missing: q('.pflow-miss'),
        };
    }""")


def test_flow_tab_renders_five_tracks(page, mount_flow, real_server,
                                      e2e_admin_token, dev_db_only):
    # 建客戶 + 專案 + 反序刪掉（只刪自己建的 —— 金絲雀鐵則）走 conftest 的
    # 共用契約，與 test_flow_edge_states 同一份
    with project_case(real_server["base_url"], e2e_admin_token, "FLOWE2E") as c:
        mount_flow(c["pid"], e2e_admin_token, host_id=HOST)
        r = _read(page)

        assert "載入失敗" not in r["html"], f"渲染錯誤：{r['html'][:300]}"
        # 五條軌，比對**軌道名節點**本身（不是整包 HTML 的子字串）
        assert r["tracks"] == ["商務", "企劃", "製作", "交付", "收割"], r["tracks"]
        # 階段列：七站、目前在「提案」
        assert r["current"] == ["提案"], r["current"]
        assert len(r["stages"]) == 7, r["stages"]
        # 有客戶 → 商務軌「客戶已建檔」亮；訊號真的從 DB 來
        assert "客戶已建檔" in r["lit"], f"客戶建檔沒亮：{r['lit']}"
        # 剛建的空專案：這些一定還沒完成（不准謊報）
        for never in ("提案已建立", "報價單已備", "開拍", "官網上線"):
            assert never in r["items"], f"缺少項目「{never}」"
            assert never not in r["lit"], f"空專案不該亮「{never}」"
        # 推進建議（提案 → 製作）
        assert r["missing"] and "推進到「製作」" in r["missing"][0]
        # 收割軌四項全部 auto，空專案一盞都不該亮
        assert not any(x in r["lit"] for x in ("企劃範本已收割", "素材庫已索引"))


def test_flow_without_project_says_so(page, mount_flow, e2e_admin_token):
    """沒有殼專案的提案不該顯示假進度，要講清楚為什麼沒有。"""
    # 沒有專案就不會畫軌道 → 沒有 .pflow-track 可等
    mount_flow("", e2e_admin_token, host_id=HOST, wait=None)
    r = _read(page)
    assert "還沒有關聯專案" in r["html"]
    assert r["tracks"] == [], "沒有專案卻畫了軌道"
