# -*- coding: utf-8 -*-
"""階段 3 權限畫面（2026-09-08）：每把鑰匙一行相依說明、沒綁人員紅字、「以他的角度看」預覽。"""
import re

from core.auth import ALL_MODULES
from tests.unit._srcscan import js_code_only, js_func_body, repo_src

JS = repo_src("frontend/js/admin/user-mgmt.js")


def _js_obj_keys(name):
    m = re.search(rf"const {name} = \{{([\s\S]*?)\n\}};", JS)
    assert m, name
    return re.findall(r"^\s*([a-z_]+):", m.group(1), re.M) + re.findall(r",\s*([a-z_]+):", m.group(1))


def test_hints_only_name_real_keys_and_cover_the_dependencies():
    keys = set(_js_obj_keys("MODULE_HINTS"))
    assert keys <= set(ALL_MODULES), keys - set(ALL_MODULES)
    for k in ("money_view", "crm_invoices", "crm_quotes", "finance_mine", "finance_partner", "me_today_zone", "me_plan_parttime", "me_petty", "hr_leave"):
        assert k in keys, k
    assert "hint ? ` title=\"${hint}\"`" in JS and "${hint ? `<span style=\"color:#555" in JS, "說明要同時是 title 與灰字"


def test_unbound_staff_warning_and_preview_button():
    assert "const unboundWarn = (!boundStaff && !isAdminUser && modules.some(k => STAFF_BOUND_KEYS.includes(k)))" in JS
    assert "未綁定人員檔案：員工工作台的區塊會是空的" in JS
    assert "onclick=\"window._previewAs('${u.username}')\"" in JS and ">以他的角度看</button>" in JS
    keys = set(_js_obj_keys("MODULE_HINTS"))
    m = re.search(r"const STAFF_BOUND_KEYS = \[([^\]]+)\]", JS); bound = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    assert bound <= set(ALL_MODULES) and "me_profile" in bound and "me_petty" in bound


def test_preview_uses_the_real_tab_gate_and_current_checkbox_state():
    body = js_func_body(js_code_only(JS), "window._previewAs = function(username)")
    assert "shouldShowTab(k, authUser, mods)" in body, "頂層分頁要用 tab-config 同一個閘門算，不能自己再寫一份"
    assert "input[data-umod-user=\"${username}\"]:checked" in body, "照目前勾的（未儲存也算）"
    assert "缺「金額檢視」" in body and "沒綁人員檔案" in body and "手機版 /m/crm.html" in body
    assert "import { groupModules, ALL_MODULES, TAB_GROUPS, shouldShowTab, tabLabel } from '../shared/tab-config.js';" in JS
    assert "Extended_Pictographic" in body, "群組標籤的圖示要剝掉（卡片裡不畫 emoji）"
