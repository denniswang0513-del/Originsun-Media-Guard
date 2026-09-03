# -*- coding: utf-8 -*-
"""CRM 手機版（routers/api_crm_mobile.py；docs/CRM_MOBILE_PLAN.md §3）。

三層：
  (a) `prepend_note` 純規則（core/crm_logic）
  (b) 掃描釘住守衛與「單一 helper」：讀 check_logged_in、寫 `_check_write(`、
      route_class=MoneyRedactRoute、報價簽回與桌機推階段都走 `apply_project_status(`
  (c) 開整個 app 打守衛：匿名 401、非管理員寫入 403、token 續期沒有那個人 401

單元環境沒有 DB，所以 200 那條在 e2e 驗；這裡只釘「不是被權限擋掉」
（同 tests/unit/test_crm_read_guard 的寫法）。
"""
import pytest

from core.crm_logic import prepend_note

from tests.unit._srcscan import code_only, func_body, repo_src

MOBILE = "routers/api_crm_mobile.py"
M = "/api/v1/crm/m"


# ── (a) 純規則 ──────────────────────────────────────────────

def test_prepend_note_puts_newest_first_with_blank_line():
    assert prepend_note("舊的一行", "新的一行") == "新的一行\n\n舊的一行"


def test_prepend_note_on_empty_notes_is_just_the_line():
    assert prepend_note("", "第一則") == "第一則"
    assert prepend_note(None, "第一則") == "第一則"


def test_prepend_note_strips_both_sides_and_keeps_old_content_intact():
    old = "  第一段\n\n第二段  \n"
    assert prepend_note(old, "  新  ") == "新\n\n第一段\n\n第二段"


def test_prepend_note_ignores_blank_line():
    """空行不是備註：不准把 notes 洗成「\\n\\n舊內容」。"""
    assert prepend_note("舊", "   ") == "舊"
    assert prepend_note(None, "") == ""


# ── (b) 掃描：守衛與單一 helper ─────────────────────────────────

def test_router_has_redact_class_and_logged_in_read_guard():
    src = code_only(repo_src(MOBILE))
    assert "route_class=MoneyRedactRoute" in src
    assert "dependencies=[Depends(check_logged_in)]" in src
    assert 'prefix=f"{CRM_PREFIX}/m"' in src, "路徑要落在 CRM_PREFIX 底下，守衛掃描才管得到"


def test_write_endpoints_call_the_single_write_guard():
    src = repo_src(MOBILE)
    assert "_check_write = check_admin" in code_only(src), "一期只給 Lv3；要開給助理改這一行"
    for header in ("async def mobile_add_note(", "async def mobile_quotation_status("):
        body = code_only(func_body(src, header))
        assert "_check_write(request)" in body, f"{header} 沒過寫入守衛"


def test_read_endpoints_do_not_require_admin():
    """讀是 check_logged_in（router 層），端點本體不准再疊 check_admin ——
    疊了會把只有模組權限的同事整頁打壞。"""
    src = repo_src(MOBILE)
    for header in ("async def mobile_options(", "async def mobile_home(",
                   "async def mobile_projects(", "async def mobile_project_detail("):
        body = code_only(func_body(src, header))
        assert "check_admin(" not in body and "_check_write(" not in body, header


def test_quotation_activate_and_desktop_status_share_one_helper():
    """簽回啟動專案與桌機 PATCH /status 都走 apply_project_status —— 漏一個副作用
    （結案預設官網階段、提案 win/loss、客戶分級）就是兩條路徑漂移。"""
    mobile = code_only(func_body(repo_src(MOBILE), "async def mobile_quotation_status("))
    assert "apply_project_status(" in mobile
    projects = repo_src("routers/crm/projects.py")
    desktop = code_only(func_body(projects, "async def update_project_status("))
    assert "apply_project_status(" in desktop
    for stale in ("_apply_status_side_effects(", "_sync_linked_proposals(",
                  "_auto_update_client_status("):
        assert stale not in desktop, f"桌機端點不該再自己呼叫 {stale}，規則只有 apply_project_status 一份"
    helper = code_only(func_body(projects, "async def apply_project_status("))
    for step in ("_apply_status_side_effects(", "_sync_linked_proposals(",
                 "project.updated_at = _now()", "_auto_update_client_status("):
        assert step in helper, f"apply_project_status 少了 {step}"


def test_activation_only_moves_presale_projects():
    """簽回不能把結案／歸檔的案子拉回「製作」。"""
    src = code_only(func_body(repo_src(MOBILE), "async def mobile_quotation_status("))
    assert "in PRESALE" in src


def test_router_is_registered_in_main():
    import main
    assert "api_crm_mobile" in main._ROUTER_MODULES
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert f"{M}/options" in paths and f"{M}/projects/{{project_id}}/note" in paths


def test_refresh_endpoint_reissues_from_db_not_from_old_payload():
    src = repo_src("routers/api_auth.py")
    body = code_only(func_body(src, "async def refresh_token("))
    assert '@router.post("/refresh")' in src
    assert "create_token(" in body
    assert "_find_user_by('username'" in body, "權限要從 DB 重讀，不是把舊 payload 重簽"
    assert "'api_key'" in body, "API key 不能換成 7 天的登入 JWT"


def test_project_lists_decide_about_mine_and_skip_mirrors():
    """手機清單只看母公司案且不列私帳分身 —— 跟桌機清單預設同口徑。"""
    src = code_only(repo_src(MOBILE))
    assert "not_mine(CrmProject.entity)" in src
    assert "CrmProject.source_project_id.is_(None)" in src
    assert "hide_mine_projects(request)" in src


def test_detail_only_packs_money_sections_when_visible():
    """報價／請款／發票帶著 MONEY_FIELDS 名單外的錢（total/final_price/amount），
    抹除層抹不到 —— 只能靠不夾帶。"""
    body = code_only(func_body(repo_src(MOBILE), "async def mobile_project_detail("))
    idx = body.index("if show_money:")
    for key in ('out["quotes"]', 'out["payments"]', 'out["invoices"]', 'out["summary"]'):
        assert key in body and body.index(key) > idx, f"{key} 沒有守在 show_money 底下"


# ── (c) 開 app 打守衛（無 DB：只驗「不是權限擋的」）────────────────

@pytest.mark.parametrize("path", [f"{M}/options", f"{M}/home", f"{M}/projects",
                                  f"{M}/projects/__probe__"])
def test_reads_reject_anonymous(app_client, path):
    assert app_client.get(path).status_code == 401


def test_reads_pass_module_user(app_client, as_user):
    r = app_client.get(f"{M}/options", headers=as_user(modules=["crm_projects"]))
    assert r.status_code not in (401, 403), r.status_code


def test_options_admin_is_not_blocked(app_client, admin_headers):
    r = app_client.get(f"{M}/options", headers=admin_headers)
    assert r.status_code not in (401, 403), r.status_code


@pytest.mark.parametrize("path,body", [
    (f"{M}/projects/__probe__/note", {"text": "x"}),
    (f"{M}/quotations/__probe__/status", {"status": "已簽核"}),
])
def test_writes_are_lv3_only(app_client, as_user, path, body):
    assert app_client.post(path, json=body).status_code == 401
    r = app_client.post(path, json=body, headers=as_user(modules=["crm_projects"]))
    assert r.status_code == 403, f"一期寫入只給 Lv3：{r.status_code}"


def test_note_rejects_empty_and_overlong_before_touching_db(app_client, admin_headers):
    r = app_client.post(f"{M}/projects/__probe__/note", json={"text": "   "}, headers=admin_headers)
    assert r.status_code == 422
    r = app_client.post(f"{M}/projects/__probe__/note", json={"text": "x" * 501}, headers=admin_headers)
    assert r.status_code == 422


def test_quotation_status_must_be_in_vocab(app_client, admin_headers):
    r = app_client.post(f"{M}/quotations/__probe__/status", json={"status": "隨便"},
                        headers=admin_headers)
    assert r.status_code == 422


def test_refresh_rejects_anonymous_and_unknown_user(app_client, user_token):
    assert app_client.post("/api/v1/auth/refresh").status_code == 401
    # token 簽得過、但那個帳號不存在（被刪＝停用）→ 401，不准憑舊 payload 重簽
    tok = user_token(sub="__nobody_crm_mobile__", username="__nobody_crm_mobile__")
    r = app_client.post("/api/v1/auth/refresh", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401
