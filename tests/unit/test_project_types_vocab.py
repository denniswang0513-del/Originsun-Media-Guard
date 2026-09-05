# -*- coding: utf-8 -*-
"""案型清單只有一份（owner 2026-09-03「這裡的案型跟私帳同步，然後 CRM 找個地方可以新增案型」）。

正本＝私帳毛利表的列（settings.project_types 是 save_margin_model 的鏡射）；讀走 core.finance_logic.project_type_vocab。四個吃字彙的地方都要走它：
CRM 專案表（GET /api/v1/crm/project-types）、私帳設定頁（api_finance margin）、工時 burn 表（api_timesheets）、
手機版（api_crm_mobile options）。桌機下拉不准再自己讀 /api/settings/load 湊一份。
"""
import re

from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src


def test_backend_endpoints_share_project_type_vocab():
    proj = repo_src("routers/crm/projects.py")
    assert '@router.get("/project-types")' in proj and '@router.post("/project-types")' in proj
    assert "project_type_vocab(extra)" in code_only(func_body(proj, "async def get_project_types("))
    edit = code_only(func_body(proj, "async def edit_project_types("))
    assert "_check_auth(request)" in edit, "改案型＝Lv3（同 /api/settings/save）"
    assert 'load_margin_model("mine")' in edit and 'save_margin_model("mine", model)' in edit, "改的是毛利表那份（正本），不是 settings"
    for f in ("routers/api_finance.py", "routers/api_timesheets.py", "routers/api_crm_mobile.py"):
        assert "project_type_vocab(" in repo_src(f), f


def test_desktop_dropdown_and_editor_use_the_one_list():
    js = js_code_only(repo_src("frontend/tabs/crm/crm-projects-core.js"))
    assert "_fetch('/project-types')" in js
    assert "/api/settings/load" not in js, "案型不准再從 settings 自己湊一份"
    assert "_fetch('/project-types', { method: 'POST'" in js and "saveSettings({ project_types" not in js
    for op in ("'add'", "'rename'", "'remove'"):
        assert f"_typeOp({op}" in js, op
    html = repo_src("frontend/tabs/crm/crm-projects.html")
    assert 'id="proj-btn-types"' in html and "window._projEditTypes()" in html, "工具列要有「案型清單」入口"
    assert "✎" not in html, "UI 無 emoji／符號鐵則：按鈕用文字"


def test_margin_model_type_ops_are_pure_and_mirror_aliases():
    from core.finance_logic import DEFAULT_MARGIN_MODEL, model_add_type, model_remove_type, model_rename_type
    from core.finance_logic._core import _copy_model
    m = _copy_model(DEFAULT_MARGIN_MODEL)
    n = len(m["rows"])
    assert model_add_type(m, "空拍") and len(m["rows"]) == n + 1
    assert m["rows"][-1]["type"] == "空拍" and m["rows"][-1]["margin_pct"] == 30   # 抄「其他」那列
    assert not model_add_type(m, "空拍") and not model_add_type(m, " ")            # 重複／空白
    assert model_rename_type(m, "空拍", "空拍攝影") and m.get("aliases", {}).get("空拍") == "空拍攝影"
    assert not model_rename_type(m, "空拍攝影", "其他")                            # 新名已存在
    assert not model_rename_type(m, "不存在", "x")
    assert model_remove_type(m, "空拍攝影") and len(m["rows"]) == n
    assert not model_remove_type(m, "空拍攝影")
