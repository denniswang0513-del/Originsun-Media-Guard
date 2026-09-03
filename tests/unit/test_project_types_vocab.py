# -*- coding: utf-8 -*-
"""案型清單只有一份（owner 2026-09-03「這裡的案型跟私帳同步，然後 CRM 找個地方可以新增案型」）。

正本＝core.finance_logic.project_type_vocab（settings.project_types ∪ 私帳毛利表）。四個吃字彙的地方都要走它：
CRM 專案表（GET /api/v1/crm/project-types）、私帳設定頁（api_finance margin）、工時 burn 表（api_timesheets）、
手機版（api_crm_mobile options）。桌機下拉不准再自己讀 /api/settings/load 湊一份。
"""
import re

from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src


def test_backend_endpoints_share_project_type_vocab():
    proj = repo_src("routers/crm/projects.py")
    assert '@router.get("/project-types")' in proj and '@router.put("/project-types")' in proj
    assert "project_type_vocab()" in code_only(func_body(proj, "async def get_project_types("))
    assert "_check_auth(request)" in code_only(func_body(proj, "async def put_project_types(")), "覆寫清單＝Lv3（同 /api/settings/save）"
    for f in ("routers/api_finance.py", "routers/api_timesheets.py", "routers/api_crm_mobile.py"):
        assert "project_type_vocab(" in repo_src(f), f


def test_desktop_dropdown_and_editor_use_the_one_list():
    js = js_code_only(repo_src("frontend/tabs/crm/crm-projects-core.js"))
    assert "_fetch('/project-types')" in js
    assert "/api/settings/load" not in js, "案型不准再從 settings 自己湊一份"
    assert "_marginTypes" in js and "margin_types" in js, "毛利表來的案型要標記、不可在 CRM 改"
    assert "_fetch('/project-types', { method: 'PUT'" in js
    html = repo_src("frontend/tabs/crm/crm-projects.html")
    assert 'id="proj-btn-types"' in html and "window._projEditTypes()" in html, "工具列要有「案型清單」入口"
    assert "✎" not in html, "UI 無 emoji／符號鐵則：按鈕用文字"
