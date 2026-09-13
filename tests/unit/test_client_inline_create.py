# -*- coding: utf-8 -*-
"""專案的客戶欄「沒有符合的就直接新增客戶」（owner 2026-09-07，桌機與手機都要）。

手機：mountPicker 多 onCreate——打的字沒有完全符合的項目時清單多一列「新增客戶「…」」，點了建一筆只有代稱的客戶、
推進 options.clients、選起來；專案的新案子與報價的客戶欄都掛 createClientOption（ui.js 一份，不各寫各的）。
桌機：專案表單客戶下拉旁「沒有符合的？新增客戶」→ crm-projects-core.createClientInline（同報價彈窗那條）。
"""
from tests.unit._srcscan import crm_projects_core_src
from tests.unit._srcscan import js_code_only, js_func_body, repo_src


def test_mobile_picker_offers_create_row_only_when_no_exact_match():
    ui = js_code_only(repo_src("frontend/m/ui.js"))
    body = js_func_body(ui, "export function mountPicker(id, { items = [], placeholder = '', value = '', free = false, onPick = null, onCreate = null, createLabel = '新增' } = {})")
    assert "const exact = hits.some(i => i.label.toLowerCase() === s)" in body
    assert "onCreate && raw && !exact" in body and 'data-create="${esc(raw)}"' in body
    assert "items.push({ value: made.value" in body and "set(made.value)" in body, "建好要推進清單並選起來"
    assert "export async function createClientOption(name)" in ui
    co = js_func_body(ui, "export async function createClientOption(name)")
    assert "mfetch('/api/v1/crm/clients', { method: 'POST', body: { short_name: name } })" in co
    assert "return { value: c.id, label: c.short_name || name }" in co
    assert ".m-pick-create" in repo_src("frontend/m/m.css")     # 手機頁樣式 2026-09-13 抽到共用的 /m/m.css


def test_mobile_project_and_quote_client_pickers_use_it():
    for f in ("frontend/m/views/projects.js", "frontend/m/views/quotes.js"):
        src = js_code_only(repo_src(f))
        assert "createClientOption" in src and "onCreate: createClientOption" in src, f
        assert "createLabel: '新增客戶'" in src, f


def test_desktop_project_form_has_new_client_button():
    html = repo_src("frontend/tabs/crm/crm-projects.html")
    assert 'id="proj-btn-new-client"' in html and "新增客戶" in html
    core = js_code_only(crm_projects_core_src())
    body = js_func_body(core, "export async function createClientInline()")
    assert "_fetch('/clients', { method: 'POST'" in body and "crmCacheInvalidate('clients')" in body
    assert "_populateClientDropdown('proj-f-client_id', c.id)" in body, "建完要選起來"
    tab = js_code_only(repo_src("frontend/tabs/crm/crm-projects.js"))
    assert "createClientInline" in tab and "proj-btn-new-client" in tab
