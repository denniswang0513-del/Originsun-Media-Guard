# -*- coding: utf-8 -*-
"""CRM 商務資料不准匿名讀（2026-08-14 發現的洞的回歸測試）。

當時實測：匿名 `GET /api/v1/crm/projects` → 200，生產讀到 236 筆，欄位含
contract_amount / amount_receivable / profit_target_pct / 客戶名 / 負責人。
根因是那批讀取端點**連 request 參數都沒有** ＝ 零守衛，而 8000 經 cloudflared
對外。修法是 router 層級的 `_crm_read_guard`（check_logged_in）。

兩條防線各自要有反面：
  ① 商務資料匿名要 401（而且是**每一支** CRM GET 路由，不是挑幾支）
  ② 模組級使用者（非管理員）要照樣讀得到 —— 用 check_admin 會把器材庫/
     場景庫/看片門戶/素材庫/現金流/提案庫六個分頁一起打壞
"""
import pytest

BUSINESS_PATHS = [
    "/api/v1/crm/projects",
    "/api/v1/crm/clients",
    "/api/v1/crm/staff",
    "/api/v1/crm/quotations",
    "/api/v1/crm/invoices",
    "/api/v1/crm/payments",
    "/api/v1/crm/cash-entries",
]

@pytest.mark.parametrize("path", BUSINESS_PATHS)
def test_business_data_rejects_anonymous(app_client, path):
    """🔴 沒有 token 就是 401 —— 這批欄位含合約金額與客戶名單。"""
    assert app_client.get(path).status_code == 401, f"{path} 匿名讀得到"


def test_module_user_can_still_read(app_client, user_headers):
    """非管理員（模組級授權）照樣讀得到 —— 守衛是 check_logged_in 不是 check_admin。

    這條反面很重要：改成 check_admin 的話這裡會紅，而畫面上的症狀是
    器材庫/場景庫/看片門戶的專案下拉整個空掉。
    """
    r = app_client.get("/api/v1/crm/projects",
                       headers=user_headers(modules=["equipment", "portal"]))
    # 單元環境沒有 DB → 503；重點是**沒有被權限擋掉**（401/403 才是回歸）
    assert r.status_code not in (401, 403), f"模組級使用者被權限擋掉：{r.status_code}"


def test_every_crm_get_route_rejects_anonymous(app_client):
    """🔴 守衛掛在 router 上 → **每一支** CRM 端點預設都受保護，**零例外**。

    逐支加守衛的話漏一支就等於沒修（當初就是這樣漏的），而 CRM 有 140+ 端點
    還在長。所以這條不挑幾支測，而是把註冊在 CRM 前綴下的 GET 路由全部列出來
    逐一打 —— 將來有人在別的 router 上加 `/api/v1/crm/...` 也會被抓到。

    註：`/crm/public/site/{works,team}` 曾被列為例外，理由寫「對外官網 Astro
    build 在讀」—— 實際查證後那是錯的（官網讀的是 `/api/website/*`，見
    website/src/lib/crm-client.ts），那兩支零消費者。例外已整條移除。
    """
    import main
    from routers.crm._shared import CRM_PREFIX

    checked, leaked = 0, []
    for route in main.app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not path.startswith(CRM_PREFIX) or "GET" not in methods:
            continue
        # 路徑參數填一個不存在的值 —— 守衛在 handler 之前跑，所以不會真的動到資料
        probe = path
        while "{" in probe:
            head, _, rest = probe.partition("{")
            _, _, tail = rest.partition("}")
            probe = head + "__probe__" + tail
        checked += 1
        if app_client.get(probe).status_code != 401:
            leaked.append(path)

    assert checked > 50, f"只掃到 {checked} 支 CRM 路由，掃描邏輯可能壞了"
    assert not leaked, f"{len(leaked)} 支 CRM 端點匿名打得到：{leaked[:10]}"


# ── 工作流的權限矩陣（owner 2026-08-14 拍板：勾選走模組級）────────────
# 住這裡而不是 test_project_flow.py：那支是 core/project_flow.py 的**純邏輯**
# 測試（無 DB、無 IO），開整個 app 的測試放進去會破壞它的定位，而這個檔本來
# 就是在測守衛、fixture 也現成。
#
# 守衛在 `_require_db` 之前跑，所以 401/403 在沒有 DB 的單元環境也測得到；
# 200 那條在 e2e 驗（這裡只釘「不是被權限擋掉」）。

FLOW = "/api/v1/crm/projects/__probe__/flow"


def test_flow_rejects_anonymous(app_client):
    assert app_client.get(FLOW).status_code == 401
    assert app_client.post(FLOW + "/check",
                           json={"item_key": "shooting", "checked": True}
                           ).status_code == 401


def test_planner_can_read_but_not_tick(app_client, user_headers):
    """🔴 只有提案庫權限的人：看得到全部進度、動不了。"""
    h = user_headers(modules=["preprod_proposals"])
    assert app_client.get(FLOW, headers=h).status_code != 403
    assert app_client.post(FLOW + "/check", headers=h,
                           json={"item_key": "shooting", "checked": True}
                           ).status_code == 403


def test_crm_projects_module_can_tick(app_client, user_headers):
    """🔴 這條就是這次鬆綁：lv1 + crm_projects 不必是管理員也勾得動。

    生產有 3 個 lv1 帳號被授予 crm_projects 卻打不了任何 CRM 寫入
    （其餘寫入都是 Lv3）—— 權限是空頭支票。這裡兌現它。
    """
    r = app_client.post(FLOW + "/check", headers=user_headers(modules=["crm_projects"]),
                        json={"item_key": "shooting", "checked": True})
    assert r.status_code not in (401, 403), f"被權限擋掉：{r.status_code}"


def test_unrelated_module_cannot_read_flow(app_client, user_headers):
    assert app_client.get(FLOW, headers=user_headers(modules=["backup"])
                          ).status_code == 403
