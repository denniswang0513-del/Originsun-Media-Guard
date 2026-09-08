# -*- coding: utf-8 -*-
"""權限稽核第二批「看得到做得到」—— CRM 後端逐支守衛釘住（2026-09-08，docs/RBAC_AUDIT.md §3.4／§4）。

規則（owner 拍板，docs/RBAC_PLAN.md §5）：分頁鑰匙開什麼分頁，就能做那個分頁上畫出來的動作；
就算有分頁鑰匙也要管理員的只有 ADMIN_ONLY_ACTIONS —— 刪除（客戶／員工／報價／雜支）、三支 CSV 匯入、
全站根目錄與設定、歸檔 folders／scan。

這支只釘「哪一支端點用哪一把守衛」（原始碼掃描）；守衛本身的行為（管理員恆過、模組級放行、其餘 403）
在 test_crm_read_guard／test_money_visibility 已有真請求測試。
"""
import pytest

from tests.unit._srcscan import code_only, func_body, repo_src


def _body(rel: str, header: str) -> str:
    return code_only(func_body(repo_src(rel), header))


# ── 政策常數 ────────────────────────────────────────────────────

def test_advance_modules_opened_to_crm_projects():
    """推進專案階段開給 crm_projects（owner 2026-09-08 §5 第 2 點）。
    PATCH /status、PUT 內的 status 門、手機簽回啟動三處都讀這一份。"""
    from core.project_flow import ADVANCE_MODULES
    assert ADVANCE_MODULES == ("crm_projects",)
    shared = repo_src("routers/crm/_shared.py")
    assert "_check_status_auth = _module_guard(*ADVANCE_MODULES)" in shared
    proj = repo_src("routers/crm/projects.py")
    assert "_check_status_auth(request)" in _body("routers/crm/projects.py", "async def update_project_status(")
    assert "_check_status_auth(request)" in _body("routers/crm/projects.py", "async def update_project(")
    assert "_check_auth(request)" not in code_only(func_body(proj, "async def update_project_status("))


def test_shared_has_the_two_new_module_guards():
    shared = repo_src("routers/crm/_shared.py")
    assert "_check_staff_auth = _module_guard('crm_staff')" in shared
    assert "_check_quotes_auth = _module_guard('crm_quotes')" in shared


# ── 員工檔案（crm_staff）────────────────────────────────────────

STAFF_OPEN = ("create_staff", "update_staff", "update_staff_resume", "upload_staff_photo",
              "list_staff_portfolio", "add_staff_portfolio", "update_staff_portfolio",
              "delete_staff_portfolio", "generate_staff_edit_token")
STAFF_ADMIN = ("delete_staff", "import_staff_csv")


@pytest.mark.parametrize("fn", STAFF_OPEN)
def test_staff_writes_take_crm_staff(fn):
    body = _body("routers/crm/staff.py", f"async def {fn}(")
    assert "_check_staff_auth(request)" in body, f"{fn} 要用 crm_staff"
    assert "_check_auth(request)" not in body, f"{fn} 不能再是管理員限定"


@pytest.mark.parametrize("fn", STAFF_ADMIN)
def test_staff_delete_and_import_stay_admin(fn):
    body = _body("routers/crm/staff.py", f"async def {fn}(")
    assert "_check_auth(request)" in body, f"{fn} 是 ADMIN_ONLY_ACTIONS"
    assert "_check_staff_auth(request)" not in body


def test_rate_history_is_money_dep_only():
    """費率史：路由層 money_dep 就是全部的守衛，handler 內不再另疊管理員。"""
    src = repo_src("routers/crm/staff.py")
    assert '@router.get("/staff/{staff_id}/rate-history", dependencies=[Depends(money_dep)])' in src
    body = _body("routers/crm/staff.py", "async def staff_rate_history(")
    assert "_check_auth(request)" not in body and "_check_staff_auth(request)" not in body


# ── 客戶（crm_clients ＋ 會順手建客戶的 crm_projects／crm_quotes）────────

def test_client_create_and_update_take_three_keys_delete_stays_admin():
    src = repo_src("routers/crm/clients.py")
    assert 'CLIENT_WRITE_MODULES = ("crm_clients", "crm_projects", "crm_quotes")' in src
    guard = code_only(func_body(src, "def _client_write_guard("))
    assert "_check_client_write(request)" in guard and "_check_auth(request)" in guard, \
        "母帳列：一般寫入走三把鑰匙、admin_only 走管理員"
    create = _body("routers/crm/clients.py", "async def create_client(")
    assert "_check_client_write(request)" in create and "_check_auth(request)" not in create
    upd = _body("routers/crm/clients.py", "async def update_client(")
    assert "_client_write_guard(request, client)" in upd and "admin_only" not in upd
    dele = _body("routers/crm/clients.py", "async def delete_client(")
    assert "_client_write_guard(request, client, admin_only=True)" in dele
    imp = _body("routers/crm/clients.py", "async def import_csv(")
    assert "_check_auth(request)" in imp


# ── 報價（crm_quotes）──────────────────────────────────────────

QUOTE_OPEN = ("create_quotation", "update_quotation", "share_quotation",
              "create_template", "update_template", "delete_template")
QUOTE_ADMIN_CHECK_AUTH = ("delete_quotation",)
QUOTE_ADMIN_CHECK_ADMIN = ("get_quotations_root", "set_quotations_root")


@pytest.mark.parametrize("fn", QUOTE_OPEN)
def test_quote_writes_take_crm_quotes(fn):
    body = _body("routers/crm/quotes.py", f"async def {fn}(")
    assert "_check_quotes_auth(request)" in body, f"{fn} 要用 crm_quotes"
    assert "_check_auth(request)" not in body, f"{fn} 不能再是管理員限定"


@pytest.mark.parametrize("fn", QUOTE_ADMIN_CHECK_AUTH)
def test_quote_delete_stays_admin(fn):
    body = _body("routers/crm/quotes.py", f"async def {fn}(")
    assert "_check_auth(request)" in body and "_check_quotes_auth(request)" not in body


def test_quotations_root_stays_admin():
    src = repo_src("routers/crm/quotes.py")
    for fn in QUOTE_ADMIN_CHECK_ADMIN:
        assert "check_admin(request)" in code_only(func_body(src, f"async def {fn}(")), fn


# ── 專案（crm_projects）────────────────────────────────────────

def test_duplicate_and_project_types_take_crm_projects_import_stays_admin():
    for fn in ("duplicate_project", "edit_project_types"):
        body = _body("routers/crm/projects.py", f"async def {fn}(")
        assert "_check_project_write_auth(request)" in body, fn
        assert "_check_auth(request)" not in body, fn
    imp = _body("routers/crm/projects.py", "async def import_projects_csv(")
    assert "_check_auth(request)" in imp


# ── 雜支（owner：內部寫入歸 crm_projects）——本體在 test_budget_write_auth；這裡釘留管理員的那幾支 ──

def test_expense_delete_and_receipts_root_stay_admin():
    assert "_check_auth(request)" in _body("routers/crm/costs.py", "async def delete_project_expense(")
    src = repo_src("routers/crm/costs.py")
    assert "check_admin(request)" in code_only(func_body(src, "async def set_receipts_root("))
    assert "check_admin_or_module(request, 'finance_approve')" in code_only(func_body(src, "async def get_receipts_root(")), "GET 給審核者（零用金子視圖第一支）"


# ── 完稿結案（crm_projects）───────────────────────────────────

ARCHIVE_OPEN = ("get_project_archive", "patch_project_archive", "add_archive_row", "delete_archive_row")
ARCHIVE_ADMIN = ("build_archive_folders", "scan_archive_folders", "patch_project_review")


@pytest.mark.parametrize("fn", ARCHIVE_OPEN)
def test_archive_checklist_takes_crm_projects(fn):
    body = _body("routers/crm/archive.py", f"async def {fn}(")
    assert "_check_project_write_auth(request)" in body and "_check_auth(request)" not in body, fn


@pytest.mark.parametrize("fn", ARCHIVE_ADMIN)
def test_archive_folders_scan_review_stay_admin(fn):
    body = _body("routers/crm/archive.py", f"async def {fn}(")
    assert "_check_auth(request)" in body and "_check_project_write_auth(request)" not in body, fn


# ── 官網作品／showcase：讀開給專案頁，寫仍 website_admin ─────────────

def test_showcase_and_works_read_open_to_projects_writes_stay_website_admin():
    sc = _body("routers/crm/showcase.py", "async def get_project_showcase(")
    assert "check_admin_or_module(request, 'website_admin', 'crm_projects')" in sc
    assert "_check_website_auth(request)" not in sc
    for fn in ("update_project_showcase", "toggle_showcase_publish"):
        assert "_check_website_auth(request)" in _body("routers/crm/showcase.py", f"async def {fn}("), fn
    wk = _body("routers/crm/works.py", "async def list_project_works(")
    assert "check_admin_or_module(request, 'website_admin', 'crm_projects')" in wk
    assert "_check_website_auth(request)" not in wk
    for fn in ("create_project_work", "toggle_work_publish", "update_work_stage", "delete_work"):
        assert "_check_website_auth(request)" in _body("routers/crm/works.py", f"async def {fn}("), fn


# ── 影像紀錄：專案內讀取多收 crm_projects；設定與整棵重掃留管理員 ───────

def test_media_log_project_read_opens_to_projects_settings_and_catchup_admin():
    src = repo_src("routers/crm/media_log.py")
    rd = code_only(func_body(src, "def _check_media_log_read_auth("))
    assert "check_admin_or_module(request, 'media_log', 'crm_projects')" in rd
    assert "_check_media_log_read_auth(request)" in _body("routers/crm/media_log.py", "async def get_project_media_log(")
    for fn in ("reset_media_log_token", "set_media_log_enabled", "delete_media_log_file"):
        assert "_check_media_log_auth(request)" in _body("routers/crm/media_log.py", f"async def {fn}("), fn
    for fn in ("update_media_log_settings", "media_log_catchup_now"):
        body = _body("routers/crm/media_log.py", f"async def {fn}(")
        assert "_check_auth(request)" in body and "_check_media_log_auth(request)" not in body, fn


# ── 提案資產：跟 api_proposals.proposal_auth 同一份名單 ──────────────

def test_proposal_assets_auth_aligns_with_proposal_auth():
    body = _body("routers/crm/proposal_assets.py", "def assets_auth(")
    assert 'check_admin_or_module(request, *tab_modules("preprod_proposals"))' in body
    assert 'check_admin_or_module(request, *tab_modules("preprod_proposals"))' in \
        _body("routers/api_proposals.py", "def proposal_auth(")
    settings = _body("routers/crm/proposal_assets.py", "async def set_proposal_assets_root(")
    assert "_check_auth(request)" in settings and "assets_auth(request)" not in settings, "改根目錄留管理員"


# ── 片庫：解除引用改看家族鑰匙，不再看目標型別 ────────────────────

def test_reference_link_delete_uses_family_key_not_target_type():
    body = _body("routers/api_references.py", "async def delete_link(")
    assert "_check_auth(request)" in body and "_check_target_auth(" not in body
    src = repo_src("routers/api_references.py")
    assert '_ACCESS_MODULES = tab_modules("references")' in src
    assert "check_admin_or_module(request, *_ACCESS_MODULES)" in code_only(func_body(src, "def _check_auth("))
