# -*- coding: utf-8 -*-
"""提案詳情「進度」分頁真的畫得出來（docs/PROPOSAL_PLANNER.md §14 階段一）。

只驗 API 不夠 —— 分頁是 lazy import 的，模組載入失敗只會在畫面上留一行
「元件載入失敗」，後端測試永遠看不到。這裡讓瀏覽器真的載入 flow-view.js
並渲染一次。

自建自刪（dev/test 庫限定）。
"""
import uuid

import pytest

pytestmark = pytest.mark.e2e


def _mount_js(project_id: str) -> str:
    """直接掛載元件 —— 不走 SPA 導覽。

    SPA 的分頁點擊在 headless 下 flaky（v2.4.0 的教訓：改成直接 import
    子視圖模組測），而這支要驗的正是「模組載得起來 + 渲染正確」。

    🔴 回傳**結構化**的渲染結果，不是整包 innerHTML：軌道名用子字串比對會
    誤判（「收割」兩個字也出現在項目名「企劃範本已收割」裡 —— 實測把軌道名
    改掉測試照樣過）。
    """
    return """
    (async (pid) => {
        const host = document.createElement('div');
        host.id = 'flowtest-host';
        document.body.appendChild(host);
        const m = await import('/tabs/proposals/flow-view.js');
        await m.renderFlow(host, { projectId: pid });
        const q = s => [...host.querySelectorAll(s)].map(e => e.textContent.trim());
        return {
            html: host.innerHTML,
            tracks: q('.pflow-tname'),
            items: q('.pflow-item'),
            lit: [...host.querySelectorAll('.pflow-item.on')].map(e => e.textContent.trim()),
            stages: q('.pflow-step'),
            current: q('.pflow-step.cur'),
            missing: q('.pflow-miss'),
        };
    })(%r)
    """ % project_id


def test_flow_tab_renders_five_tracks(page, real_server, e2e_admin_token, dev_db_only):
    import requests
    base = real_server["base_url"]
    H = {"Authorization": "Bearer " + e2e_admin_token}

    # 自己建客戶 + 專案（只刪自己建的 —— 金絲雀鐵則）。
    # 手建專案 client_id 必填（只有提案建殼那條路可以空，見 db.models.CrmProject）。
    tag = uuid.uuid4().hex[:6]
    rc = requests.post(f"{base}/api/v1/crm/clients", headers=H,
                       json={"name": f"[FLOWE2E] 客戶 {tag}",
                             "short_name": f"FE2E{tag}"}, timeout=30)
    assert rc.status_code in (200, 201), f"建客戶失敗 {rc.status_code}: {rc.text[:200]}"
    cid = rc.json().get("id") or (rc.json().get("client") or {}).get("id")
    assert cid, f"回應沒有 client id: {rc.text[:200]}"

    r = requests.post(f"{base}/api/v1/crm/projects", headers=H,
                      json={"name": f"[FLOWE2E] {tag}", "status": "提案",
                            "client_id": cid}, timeout=30)
    assert r.status_code in (200, 201), f"建專案失敗 {r.status_code}: {r.text[:200]}"
    pid = r.json().get("id") or (r.json().get("project") or {}).get("id")
    assert pid, f"回應沒有 project id: {r.text[:200]}"

    try:
        page.evaluate("t => localStorage.setItem('auth_token', t)", e2e_admin_token)
        r = page.evaluate(_mount_js(pid))

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
    finally:
        requests.delete(f"{base}/api/v1/crm/projects/{pid}", headers=H, timeout=30)
        requests.delete(f"{base}/api/v1/crm/clients/{cid}", headers=H, timeout=30)
        page.evaluate("() => document.getElementById('flowtest-host')?.remove()")


def test_flow_without_project_says_so(page, real_server, e2e_admin_token):
    """沒有殼專案的提案不該顯示假進度，要講清楚為什麼沒有。"""
    page.evaluate("t => localStorage.setItem('auth_token', t)", e2e_admin_token)
    r = page.evaluate(_mount_js(""))
    assert "還沒有關聯專案" in r["html"]
    assert r["tracks"] == [], "沒有專案卻畫了軌道"
    page.evaluate("() => document.getElementById('flowtest-host')?.remove()")
