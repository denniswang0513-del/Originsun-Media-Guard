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
    """寫入守衛與 /options 的 `me.can_write` 問的是同一份 MOBILE_WRITE_MODULES
    （空 tuple＝只有 Lv3；要開給助理填 ('crm_projects',) 一行）。"""
    src = repo_src(MOBILE)
    code = code_only(src)
    assert "MOBILE_WRITE_MODULES: tuple[str, ...] = ()" in code, "一期只給 Lv3；要開給助理改這一行"
    assert "_check_write = _module_guard(*MOBILE_WRITE_MODULES)" in code
    for header in ("async def mobile_add_note(", "async def mobile_quotation_status("):
        body = code_only(func_body(src, header))
        assert "_check_write(request)" in body, f"{header} 沒過寫入守衛"
    options = code_only(func_body(src, "async def mobile_options("))
    assert "payload_grants(payload, *MOBILE_WRITE_MODULES)" in options, \
        "can_write 要跟守衛問同一份清單（payload_grants 零 key＝只有管理員）"


def test_activation_obeys_the_desktop_status_policy():
    """簽回順便啟動專案＝推階段：要過 _check_status_auth（ADVANCE_MODULES），
    跟桌機 PATCH /projects/{id}/status 同一支，而且要在 apply_project_status 之前。"""
    body = code_only(func_body(repo_src(MOBILE), "async def mobile_quotation_status("))
    assert "_check_status_auth(request)" in body
    assert body.index("_check_status_auth(request)") < body.index("apply_project_status(")


def test_quote_vocabulary_has_one_home():
    """報價狀態字彙只住 core.finance_logic；手機 router 與 quotation_stats 都從那裡拿。"""
    code = code_only(repo_src(MOBILE))
    assert "QUOTE_STATUSES = (" not in code and "QUOTE_PENDING = " not in code, "router 裡又長出一份字彙"
    import re
    assert re.search(r"from core\.finance_logic import \([^)]*QUOTE_STATUSES", code)
    from core.finance_logic import QUOTE_PENDING, QUOTE_STATUSES
    assert QUOTE_PENDING in QUOTE_STATUSES
    stats = code_only(func_body(repo_src("routers/crm/quotes.py"), "async def quotation_stats("))
    assert "QUOTE_PENDING" in stats and '"已寄送"' not in stats


def test_project_wire_is_shared_by_mobile_and_desktop():
    """單筆專案的回應形狀只有 routers/crm/projects.project_wire 一份。"""
    mobile = code_only(repo_src(MOBILE))
    assert "project_wire(" in mobile and "_project_wire" not in mobile
    projects = repo_src("routers/crm/projects.py")
    for header in ("async def get_project(", "async def update_project(",
                   "async def update_project_status("):
        body = code_only(func_body(projects, header))
        assert "project_wire(session, project)" in body, f"{header} 沒走 project_wire"
        assert '"proposal_status"' not in body, f"{header} 又自己拼了一次形狀"


def test_recent_invoices_are_sliced_by_the_backend():
    """手機「最近 10 張」交給 list_invoices 的 limit／order，不在瀏覽器整批撈再切。"""
    body = repo_src("routers/crm/finance.py")
    sig = body[body.index("async def list_invoices("):body.index("):", body.index("async def list_invoices("))]
    assert "limit: int = Query(0)" in sig and 'order: str = Query("")' in sig
    js = repo_src("frontend/m/views/invoice.js")
    assert "/api/v1/crm/invoices?limit=10&order=recent" in js
    assert ".slice(0, 10)" not in js


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
                 "_now()", "_auto_update_client_status("):
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
    assert "_issue_token(user" in body
    assert "_find_user_by('username'" in body, "權限要從 DB 重讀，不是把舊 payload 重簽"
    assert "'api_key'" in body, "API key 不能換成 7 天的登入 JWT"


def test_login_paths_issue_tokens_through_one_helper():
    """密碼登入／Google 登入／續期／重設密碼四條路都走 _issue_token —— 各拼一次的話
    payload 多一個 claim 就會有一條漏掉。（login 裡「沒有任何帳號時的 bootstrap admin」
    那段是用字面值拼的，不在此限。）"""
    src = repo_src("routers/api_auth.py")
    assert "def _issue_token(user: dict, **extra) -> dict:" in src
    for header in ("async def login(", "async def google_login(", "async def refresh_token(",
                   "async def reset_password("):
        body = code_only(func_body(src, header))
        assert "_issue_token(" in body, f"{header} 沒走 _issue_token"
        assert body.count("create_token(") <= (1 if header == "async def login(" else 0), \
            f"{header} 還自己簽 token"


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
