# -*- coding: utf-8 -*-
"""/polish 收尾 review（2026-09-09）抓到的安全洞與退化，修完釘住。

- settings/save 分流：company 的 logo_path／seal_path 非管理員不准碰（報價單會把它渲染成 data URI＝任意檔讀取）；finance 只准固定成本；非 dict 400
- showcase GET：edit_token（公開編輯頁的寫入憑證）只回給 website_admin
- rate-history 要 crm_staff；福委會會計包要 finance_approve；解除 CRM 專案的引用要 crm_projects
- 管理員開關不夾帶指名制鑰匙（finance_mine）
- 探針（petty／benefits）不留假的授權不足紀錄；工作階段守衛認合夥；里程碑讀取放 me_plan_parttime；plan-for targets 本人要在職
- 合夥範本不含 finance_partner（與 money_view 互斥）；守衛在 docstring 之後；死的收鑰匙回填拿掉
"""
import pytest
from fastapi import HTTPException

from core.rbac_templates import DEFAULT_TEMPLATES, normalize
from routers.api_system import _SETTINGS_SAVE_DENY_SUBKEYS, _settings_save_restrict
from tests.unit._srcscan import code_only, func_body, repo_src, timesheets_src


def test_settings_restrict_blocks_path_keys_and_non_dict():
    # bankbook_path（2026-09-12）：免登入分享頁的下載來源，非管理員指到別張發票＝把它公開送出去
    assert _SETTINGS_SAVE_DENY_SUBKEYS["company"] == ("logo_path", "seal_path", "bankbook_path")
    out = _settings_save_restrict({"company": {"name": "O", "logo_path": "settings.json", "seal_path": "x",
                                               "bankbook_path": "2026/2026-08/別人的發票.pdf"}}, ("crm_quotes", "crm_invoices"))
    assert out == {"company": {"name": "O"}}
    assert _settings_save_restrict({"finance": {"monthly_fixed_costs": 1, "margin_model": {}, "baseline_month": "2020-01"}}, ("crm_invoices",)) == {"finance": {"monthly_fixed_costs": 1}}
    with pytest.raises(HTTPException) as e:
        _settings_save_restrict({"nas_paths": "x"}, ("projects",))
    assert e.value.status_code == 400


def test_guards_restored_or_tightened():
    sc = code_only(func_body(repo_src("routers/crm/showcase.py"), "async def get_project_showcase("))
    assert "if not payload_grants(payload, 'website_admin'):" in sc and "data.pop('edit_token', None)" in sc
    st = code_only(func_body(repo_src("routers/crm/staff.py"), "async def staff_rate_history("))
    assert "check_admin_or_module(request, 'crm_staff')" in st
    bf = repo_src("routers/crm/benefits.py")
    for fn in ("async def accounting_package(", "async def accounting_package_csv("):
        assert "_check_approver(request)" in code_only(func_body(bf, fn)), fn
    rf = code_only(func_body(repo_src("routers/api_references.py"), "async def delete_link("))
    assert 'if getattr(link, "target_type", "") == "crm_project":' in rf and 'check_admin_or_module(request, "crm_projects")' in rf


def test_admin_toggle_never_smuggles_explicit_only_keys():
    auth = repo_src("routers/api_auth.py")
    upd = code_only(func_body(auth, "async def update_user("))
    assert "held_before = list(user.get('modules') or [])" in upd and "m not in EXPLICIT_ONLY_MODULES or m in held_before" in upd
    cre = code_only(func_body(auth, "async def create_user("))
    assert "req.access_level >= 3 and m in EXPLICIT_ONLY_MODULES" in cre


def test_probes_do_not_record_and_staff_gates_accept_partners():
    pe = code_only(func_body(repo_src("routers/crm/petty.py"), "def _may_manage_others("))
    assert 'payload_grants(_extract_token(request) or {}, "finance_approve")' in pe and "_check_approver(request)" not in pe
    be = code_only(func_body(repo_src("routers/crm/benefits.py"), "def _can_manage("))
    assert 'payload_grants(_extract_token(request) or {}, "finance_approve")' in be and "_check_approver(request)" not in be
    ws = code_only(func_body(repo_src("routers/crm/work_stages.py"), "async def _stage_guard("))
    assert "is_active_staff(" in ws and "!= STAFF_ACTIVE" not in ws
    ms = code_only(func_body(repo_src("routers/api_milestones.py"), "def _who("))
    assert ms.count('"me_plan_parttime"') == 2
    tg = code_only(func_body(timesheets_src(), "async def plan_for_targets("))
    assert "is_active_staff(" in tg and "只有在職／合夥可以幫兼職排班" in tg


def test_partner_template_and_docstring_order_and_dead_backfill():
    assert "finance_partner" not in DEFAULT_TEMPLATES["合夥"]
    assert "finance_partner" not in normalize({"在職": ["finance_partner", "crm_invoices"]})["在職"], "有 money_view 就丟唯讀鍵"
    ota = repo_src("routers/api_ota.py")
    for fn in ("async def suggest_release_notes(", "async def deploy_to_prod_eligible(", "async def deploy_website_eligible("):
        body = func_body(ota, fn)
        assert body.index('"""') < body.index("_check_admin(request)"), f"{fn} 守衛要在 docstring 之後"
    cs = func_body(repo_src("routers/crm/costs.py"), "async def serve_receipt(")
    assert cs.index('"""') < cs.index("check_admin_or_module(")
    assert "bundles_backfilled" not in repo_src("main.py")
