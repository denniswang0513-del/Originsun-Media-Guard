# -*- coding: utf-8 -*-
"""CRM 商務資料不准匿名讀（2026-08-14 發現的洞的回歸測試）。

當時實測：匿名 `GET /api/v1/crm/projects` → 200，生產讀到 236 筆，欄位含
contract_amount / amount_receivable / profit_target_pct / 客戶名 / 負責人。
根因是那批讀取端點**連 request 參數都沒有** ＝ 零守衛，而 8000 經 cloudflared
對外。修法是 router 層級的 `_crm_read_guard`（check_logged_in）。

三條防線各自要有反面：
  ① 商務資料匿名要 401
  ② 官網那兩支要維持匿名 200（Astro build 在讀，擋了官網會壞）
  ③ 模組級使用者（非管理員）要照樣讀得到 —— 用 check_admin 會把器材庫/
     場景庫/看片門戶/素材庫/現金流/提案庫六個分頁一起打壞
"""
import pytest
from fastapi.testclient import TestClient

from core.auth import create_token


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


BUSINESS_PATHS = [
    "/api/v1/crm/projects",
    "/api/v1/crm/clients",
    "/api/v1/crm/staff",
    "/api/v1/crm/quotations",
    "/api/v1/crm/invoices",
    "/api/v1/crm/payments",
    "/api/v1/crm/cash-entries",
]

PUBLIC_SITE_PATHS = [
    "/api/v1/crm/public/site/works",
    "/api/v1/crm/public/site/team",
]


@pytest.mark.parametrize("path", BUSINESS_PATHS)
def test_business_data_rejects_anonymous(client, path):
    """🔴 沒有 token 就是 401 —— 這批欄位含合約金額與客戶名單。"""
    assert client.get(path).status_code == 401, f"{path} 匿名讀得到"


@pytest.mark.parametrize("path", PUBLIC_SITE_PATHS)
def test_public_site_endpoints_stay_anonymous(client, path):
    """對外官網的 Astro build 在讀這兩支 —— 擋了官網就壞。"""
    assert client.get(path).status_code != 401, f"{path} 被守衛擋住了（官網會壞）"


def test_module_user_can_still_read(client):
    """非管理員（模組級授權）照樣讀得到 —— 守衛是 check_logged_in 不是 check_admin。

    這條反面很重要：改成 check_admin 的話這裡會紅，而畫面上的症狀是
    器材庫/場景庫/看片門戶的專案下拉整個空掉。
    """
    tok = create_token({"sub": "s", "username": "s", "access_level": 1,
                        "modules": ["equipment", "portal", "preprod_proposals"]})
    r = client.get("/api/v1/crm/projects", headers={"Authorization": f"Bearer {tok}"})
    # 單元環境沒有 DB → 503；重點是**沒有被權限擋掉**（401/403 才是回歸）
    assert r.status_code not in (401, 403), f"模組級使用者被權限擋掉：{r.status_code}"


def test_every_crm_get_route_rejects_anonymous(client):
    """🔴 守衛掛在 router 上 → **每一支** CRM 端點預設都受保護。

    逐支加守衛的話漏一支就等於沒修（當初就是這樣漏的），而 CRM 有 140+ 端點
    還在長。所以這條不挑幾支測，而是把註冊在 CRM 前綴下的 GET 路由全部列出來
    逐一打 —— 將來有人在別的 router 上加 `/api/v1/crm/...` 也會被抓到。
    """
    import main
    from routers.crm._shared import CRM_PREFIX, _PUBLIC_PATHS

    checked, leaked = 0, []
    for route in main.app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not path.startswith(CRM_PREFIX) or "GET" not in methods:
            continue
        if path in _PUBLIC_PATHS:
            continue
        # 路徑參數填一個不存在的值 —— 守衛在 handler 之前跑，所以不會真的動到資料
        probe = path
        while "{" in probe:
            head, _, rest = probe.partition("{")
            _, _, tail = rest.partition("}")
            probe = head + "__probe__" + tail
        checked += 1
        if client.get(probe).status_code != 401:
            leaked.append(path)

    assert checked > 50, f"只掃到 {checked} 支 CRM 路由，掃描邏輯可能壞了"
    assert not leaked, f"{len(leaked)} 支 CRM 端點匿名打得到：{leaked[:10]}"
