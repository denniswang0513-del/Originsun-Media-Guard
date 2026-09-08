# -*- coding: utf-8 -*-
"""權限稽核第一批（2026-09-08，docs/RBAC_AUDIT.md §4 第一批）：安全洞，不動任何人的權限。

1 settings/load 對非管理員多抹工時同步 token 與 webhook；無人呼叫、不抹機密的 /api/v1/settings 刪掉
2 內部重啟金鑰隨 OTA 包公開 → 經 cloudflared 進來的一律擋
3 六支「登入即可」雜支端點收成內部路（登入＋crm_projects）；receipt-file 要一把看得到收據的鑰匙
4 /milestones/week、/project 從登入即可改成要鑰匙
5 API key 列表不回 raw_key
6 機器卡 PUT 補 Authorization   7 員工頁請假撤回欄位名   8 drone_watcher／schedules 寫入區網或登入
9 五處 try: check_admin except ImportError: pass 改 fail-closed
"""
import re

from routers.api_system import _ADMIN_ONLY_SUBKEYS, _SECRET_KEYS, _redact_settings
from tests.unit._srcscan import code_only, func_body, my_page_src, repo_src


def test_settings_load_redacts_more_for_non_admin_and_keeps_them_for_admin():
    s = {"jwt_secret": "x", "database_url": "y", "timesheet": {"ingest_token": "T", "sheet_id": "S"},
         "notifications": {"google_chat_webhook": "W", "alert_webhook": "A", "custom_webhook_url": "C", "line_notify_token": "L", "enabled": True},
         "google_oauth": {"client_id": "id", "client_secret": "sec"}}
    anon = _redact_settings(s)
    assert "jwt_secret" not in anon and "database_url" not in anon and "client_secret" not in anon["google_oauth"]
    assert "ingest_token" not in anon["timesheet"] and anon["timesheet"]["sheet_id"] == "S"
    assert anon["notifications"] == {"enabled": True}, "四個 webhook／token 都要抹"
    adm = _redact_settings(s, admin=True)
    assert adm["timesheet"]["ingest_token"] == "T" and adm["notifications"]["google_chat_webhook"] == "W", "管理員的設定視窗要看得到才能編"
    assert "jwt_secret" not in adm and "client_secret" not in adm["google_oauth"], "簽得出 admin 的機密連管理員也不回"
    assert set(_SECRET_KEYS) == {"jwt_secret", "database_url"} and "ingest_token" in _ADMIN_ONLY_SUBKEYS["timesheet"]
    src = repo_src("routers/api_system.py")
    assert '@router.get("/api/v1/settings")' not in src, "無人呼叫、完全不抹機密的相容端點要刪掉"
    assert "admin=_is_admin_request(request)" in code_only(func_body(src, "async def load_settings_api("))


def test_internal_restart_refuses_cloudflare_traffic():
    for f, fn in (("routers/api_system.py", "async def system_restart("), ("routers/api_ota.py", "async def internal_restart(")):
        body = code_only(func_body(repo_src(f), fn))
        assert "via_cloudflare(request)" in body, f
        assert body.index("via_cloudflare(request)") < body.index("X-Internal-Key"), "先擋公網再比金鑰"
    auth = repo_src("core/auth.py")
    b = code_only(func_body(auth, "def via_cloudflare("))
    assert "cf-connecting-ip" in b and "cf-ray" in b


def test_expense_legacy_paths_require_crm_projects_and_receipts_need_a_key():
    src = repo_src("routers/crm/costs.py")
    for fn in ("async def add_advance_expense(", "async def add_public_project_expense(", "async def get_public_cost_group_info(",
               "async def list_public_cost_group_expenses(", "async def add_public_cost_group_expense(", "async def upload_public_cost_group_receipt(",
               "async def get_public_project_info(", "async def list_public_project_expenses(", "async def upload_project_receipt_public("):
        assert "_check_project_write_auth(request)" in code_only(func_body(src, fn)), fn
    rf = code_only(func_body(src, "async def serve_receipt("))
    assert "check_admin_or_module(request, 'money_view', 'crm_projects', 'crm_invoices', 'finance_approve', 'me_petty', 'me_benefits')" in rf, \
        "員工頁自己的零用金／福委收據（me_petty／me_benefits）不能被鎖掉"


def test_milestones_need_a_key_not_just_login():
    src = repo_src("routers/api_milestones.py")
    who = code_only(func_body(src, "def _who("))
    assert 'check_admin_or_module(request, "timesheets", ME_ZONE_MASTER, "me_plan_parttime", *extra)' in who
    assert "check_logged_in" not in code_only(src)
    assert '_who(request, "crm_projects")' in code_only(func_body(src, "async def milestones_of_project("))


def test_api_key_listing_never_returns_raw_key():
    src = repo_src("routers/api_api_keys.py")
    assert "'raw_key'" not in code_only(func_body(src, "def _key_to_safe_dict("))
    assert "'key': raw_key" in src, "建立時仍回一次"
    js = repo_src("frontend/js/admin/api-keys.js")
    assert "k.raw_key||prefix" not in js and "${k.raw_key ? `<button" in js


def test_frontend_one_liners():
    assert "opts.method === 'PUT' || opts.method === 'DELETE'" in repo_src("frontend/js/auth/login-modal.js"), "機器卡 PUT /agents/{id} 要帶 token"
    html = my_page_src()
    assert "body = { note };" in html and "cancel_note: note" not in html


def test_persistent_side_effect_endpoints_require_lan_or_login():
    dw = repo_src("routers/api_drone_watcher.py")
    for fn in ("async def save_watcher_config(", "async def cancel_all_drone_jobs(", "async def run_watcher_now("):
        assert "check_lan_or_logged_in(request)" in code_only(func_body(dw, fn)), fn
    sc = repo_src("routers/api_schedules.py")
    for fn in ("async def create_schedule(", "async def update_schedule(", "async def delete_schedule("):
        assert "check_lan_or_logged_in(request)" in code_only(func_body(sc, fn)), fn
    ota = repo_src("routers/api_ota.py")
    for fn in ("async def publish_status(", "async def suggest_release_notes(", "async def get_publish_history(",
               "async def deploy_to_prod_eligible(", "async def deploy_website_eligible("):
        assert "_check_admin(request)" in code_only(func_body(ota, fn)), fn


def test_admin_guards_fail_closed_when_auth_module_missing():
    for f in ("routers/api_system.py", "routers/api_ota.py", "routers/api_agents.py", "routers/api_job_history.py", "routers/api_report.py"):
        src = code_only(repo_src(f))
        assert not re.search(r"check_admin\(request\)\s*\n\s*except ImportError:\s*\n\s*pass", src), f"{f}: auth 載不進來不能等於全開"
