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


def test_module_user_can_still_read(app_client, as_user):
    """非管理員（模組級授權）照樣讀得到 —— 守衛是 check_logged_in 不是 check_admin。

    這條反面很重要：改成 check_admin 的話這裡會紅，而畫面上的症狀是
    器材庫/場景庫/看片門戶的專案下拉整個空掉。
    """
    r = app_client.get("/api/v1/crm/projects",
                       headers=as_user(modules=["equipment", "portal"]))
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

    🔴 唯一不受這條管的是 **token 端點**（影像紀錄、雜支登記）：它們的憑證就是
    token，本來就發給沒有帳號的人。排除方式刻意**不是**寫一張路徑例外清單
    （那正是這支測試在防的東西），而是問「這條路由在不在 `public_router` 上」
    —— 那個物件的內容由 test_media_log_public_router 與 test_public_surface
    逐條列舉釘住，往裡面加一條會在那兩支紅。
    """
    import main
    from routers.crm import public_router, token_router
    from routers.crm._shared import CRM_PREFIX

    token_paths = {CRM_PREFIX + r.path
                   for router_ in (public_router, token_router)
                   for r in router_.routes if getattr(r, "path", None)}
    checked, leaked = 0, []
    for route in main.app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not path.startswith(CRM_PREFIX) or "GET" not in methods:
            continue
        if path in token_paths:
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


def test_planner_can_read_but_not_tick(app_client, as_user):
    """🔴 只有提案庫權限的人：看得到全部進度、動不了。"""
    h = as_user(modules=["preprod_proposals"])
    assert app_client.get(FLOW, headers=h).status_code != 403
    assert app_client.post(FLOW + "/check", headers=h,
                           json={"item_key": "shooting", "checked": True}
                           ).status_code == 403


def test_crm_projects_module_can_tick(app_client, as_user):
    """🔴 這條就是這次鬆綁：lv1 + crm_projects 不必是管理員也勾得動。

    生產有 3 個 lv1 帳號被授予 crm_projects 卻打不了任何 CRM 寫入
    （其餘寫入都是 Lv3）—— 權限是空頭支票。這裡兌現它。
    """
    r = app_client.post(FLOW + "/check", headers=as_user(modules=["crm_projects"]),
                        json={"item_key": "shooting", "checked": True})
    assert r.status_code not in (401, 403), f"被權限擋掉：{r.status_code}"


def test_unrelated_module_cannot_read_flow(app_client, as_user):
    assert app_client.get(FLOW, headers=as_user(modules=["backup"])
                          ).status_code == 403


# ── token 自驗端點：匿名要走得到 token 驗證，不准被登入守衛先擋 ─────────
# 🔴 2026-08-15 事故的回歸測試：showcase-edit / staff-edit / resume 的憑證是
# 網址裡的 token（頁面裸 fetch），但端點掛在主 router 上被 8/14 的
# `_crm_read_guard` 蓋到 —— 每一支都回「未登入」，完稿結案 iframe 與外部編輯
# 連結整片「已失效」。兩種失敗都是 401，**分辨靠 detail**：
#   守衛的 401 = 「未登入或 token 已過期」（請求沒走到 token 驗證）
#   自驗的 401 = 「無效的連結」（走到了，token 不對 —— 這才是對的路）

TOKEN_PAGES = [
    "/api/v1/crm/public/showcase-edit/__badtoken__",
    "/api/v1/crm/public/staff-edit/__badtoken__",
]


@pytest.mark.parametrize("path", TOKEN_PAGES)
def test_token_pages_reach_token_verification_anonymously(app_client, path):
    r = app_client.get(path)
    assert r.status_code in (401, 503), r.status_code
    detail = (r.json() or {}).get("detail", "")
    assert "未登入" not in detail, \
        f"{path} 被登入守衛先擋掉了（token 頁的憑證是 token，頁面不帶 Authorization）"


def test_public_resume_is_anonymous(app_client):
    """對外履歷分享頁（resume.html）—— 設計上就是公開唯讀，錢由第二層抹。"""
    r = app_client.get("/api/v1/crm/public/staff/__probe__/resume")
    assert r.status_code != 401, "履歷分享連結被登入守衛擋掉"


# ── 專案本體與派工的增刪（owner 2026-08-15 拍板，第二道模組級鬆綁）──────
# 專案頁把「每個案子的工作面」搬齊之後，唯獨人員配置與專案本身加不了也刪不了
# ——那幾支是 Lv3，而這頁的閘門收 crm_projects：看得到畫面、按下去 403。
# 同 8/14 那句「權限是空頭支票」。

PROJECT_WRITES = [
    # body 要能過 pydantic —— 驗證發生在 handler 之前，少一個必填欄位會回 422，
    # 那條路上守衛根本沒跑到，反面測試就變成在測 schema 而不是測權限
    ("post", "/api/v1/crm/projects", {"name": "x", "client_id": "c"}),
    ("delete", "/api/v1/crm/projects/__probe__", None),
    ("post", "/api/v1/crm/projects/__probe__/staff",
     {"staff_id": "s", "role_in_project": "攝影", "days": 1}),
    ("delete", "/api/v1/crm/project-staff/__probe__", None),
]


@pytest.mark.parametrize("method,path,body", PROJECT_WRITES)
def test_crm_projects_module_can_write_projects_and_staff(app_client, as_user,
                                                          method, path, body):
    """🔴 lv1 + crm_projects 要動得了專案與派工（不是被權限擋掉）。

    單元環境沒有 DB → 503/404 都算過；這裡釘的是**不是 401/403**。
    """
    kw = {"headers": as_user(modules=["crm_projects"])}
    if body is not None:
        kw["json"] = body
    r = getattr(app_client, method)(path, **kw)
    assert r.status_code not in (401, 403), f"{method} {path} 被權限擋掉：{r.status_code}"


@pytest.mark.parametrize("method,path,body", PROJECT_WRITES)
def test_other_modules_still_cannot_write_projects(app_client, as_user,
                                                   method, path, body):
    """反面：鬆綁只給 crm_projects —— 拿別的模組來的照樣 403。

    沒有這條，上面那條在「守衛整個被拿掉」時也會綠。
    """
    kw = {"headers": as_user(modules=["preprod_proposals", "backup"])}
    if body is not None:
        kw["json"] = body
    assert getattr(app_client, method)(path, **kw).status_code == 403
