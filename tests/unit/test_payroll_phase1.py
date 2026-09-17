# -*- coding: utf-8 -*-
"""薪資第一批（docs/PAYROLL_OVERTIME_PLAN.md §6 一）的原始碼契約：鑰匙登記、整支守衛、確認長請款單、三表人事分組、
前端鏡射、NAS 不掛、UI 無 emoji。行為測試在 test_payroll_logic.py。"""
import re

from tests.unit._srcscan import func_body, js_code_only, repo_src

_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def test_module_key_registered_everywhere():
    from core.auth import ALL_MODULES, MODULE_BUNDLES, MODULE_LABELS
    assert "hr_payroll" in ALL_MODULES and ALL_MODULES.index("hr_payroll") > ALL_MODULES.index("me_week_plan"), "新鑰匙一律 append 在尾端（2026-09-18 之後 knowledge 排在它後面）"
    assert MODULE_LABELS["hr_payroll"] == "薪資"
    assert "hr_payroll" not in MODULE_BUNDLES["hr"], "薪資不進 hr 捆（人事助理不該連薪水都看得到）"
    from core.rbac_templates import DEFAULT_TEMPLATES
    assert "hr_payroll" in DEFAULT_TEMPLATES["合夥"], "owner：預設合夥以上"
    assert "hr_payroll" not in DEFAULT_TEMPLATES["在職"] and "hr_payroll" not in DEFAULT_TEMPLATES["兼職"]
    tc = repo_src("frontend/js/shared/tab-config.js")
    assert "hr_payroll: 'tab_hr_payroll'" in tc and "'./tabs/hr_payroll/hr_payroll.js'" in tc and "{ key: 'hr_payroll',   label: '薪資' }" in tc
    assert 'id="tab_hr_payroll"' in repo_src("frontend/index.html")


def test_router_guard_and_registration():
    src = repo_src("routers/api_payroll.py")
    assert 'MODULE_KEY = "hr_payroll"' in src
    assert "def _guard(request: Request)" in src and "check_admin_or_module(request, MODULE_KEY)" in src
    for fn in ("list_profiles", "create_profile", "update_profile", "delete_profile", "get_rates", "put_rates", "list_runs", "create_run",
               "get_run", "refresh_run", "update_line", "confirm_run", "delete_run", "export_run"):
        body = func_body(src, f"async def {fn}(")
        assert "_guard(request)" in body, f"{fn} 沒掛整支守衛"
    assert "money_dep" not in src and "MoneyRedactRoute" not in src, "薪資整支 403，不走抹欄位"
    assert "'api_payroll'" in repo_src("main.py")
    assert "api_payroll" not in repo_src("main_office.py"), "NAS office-api 刻意不掛薪資"


def test_confirm_creates_payment_requests_with_salary_category():
    src = repo_src("routers/api_payroll.py")
    body = func_body(src, "async def confirm_run(")
    assert "CrmPaymentRequest(" in body and "payee_type=PAYEE_TYPE" in body and "payment_status=\"應付款\"" in body
    assert "planned_month=pay_month" in body and "_assert_month_open(session, expense_day, entity=ENTITY)" in body
    assert 'CATEGORY_BY_ENTITY = {"公司": "薪資", "代發": "代發薪資"}' in src, "代發＝過帳不算費用"
    assert "ln.payment_request_id = pr.id" in body
    assert "if ln.payment_request_id or ln.net_pay <= 0:" in body, "同一列不開兩張；實發 0 不開"
    # 只有草稿能改／刪／確認
    for fn in ("refresh_run", "update_line", "confirm_run", "delete_run"):
        assert "draft=True" in func_body(src, f"async def {fn}(")


def test_line_update_only_accepts_editable_fields():
    src = repo_src("routers/api_payroll.py")
    body = func_body(src, "async def update_line(")
    assert "if k in LINE_EDITABLE" in body and "manual.update(k for k in data if k not in (\"note\", \"work_hours\"))" in body
    from core.payroll_logic import LINE_EDITABLE
    from core.schemas import PayrollLineUpdate
    assert set(PayrollLineUpdate.model_fields) == set(LINE_EDITABLE)


def test_models_and_money_registry():
    from core.money import MONEY_FIELDS, REGISTRY_EXEMPT, _PAYROLL_ONLY
    for k in ("base_amount", "gross_pay", "net_pay", "employer_total", "labor_self", "health_employer"):
        assert k in _PAYROLL_ONLY and k in REGISTRY_EXEMPT and k not in MONEY_FIELDS
    from db.models import PayrollLine, PayrollRateTable, PayrollRun, StaffPayProfile
    assert StaffPayProfile.__tablename__ == "staff_pay_profiles" and PayrollRun.__tablename__ == "payroll_runs"
    assert PayrollLine.__tablename__ == "payroll_lines" and PayrollRateTable.__tablename__ == "payroll_rate_tables"


def test_personnel_opex_group_and_migration():
    from core.finance_logic import OPEX_GROUPS
    assert "營業費用-人事" in OPEX_GROUPS
    seed = repo_src("db/seed_finance.py")
    for code in ("6100", "6110", "6120"):
        assert re.search(rf'\("{code}", "[^"]+", None, "expense", "operating", "營業費用-人事"\)', seed), f"{code} 種子要進人事組"
    mig = repo_src("db/startup_migrations.py")
    assert "_m23_personnel_pnl_group" in mig and "_m23_personnel_pnl_group,\n]" in mig
    assert "AND pnl_group = '營業費用-管理'" in func_body(mig, "async def _m23_personnel_pnl_group("), "只搬還在管理組的，owner 改過的不動"


def test_cashflow_fixed_cost_falls_back_to_payroll():
    src = repo_src("routers/api_cashflow.py")
    assert "await _payroll_monthly_cost(factory)" in src
    body = func_body(src, "async def _payroll_monthly_cost(")
    assert 'PayrollRun.status == "已確認"' in body and "PayrollLine.employer_total" in body


def test_frontend_tab_no_emoji_and_uses_auth_helpers():
    js = repo_src("frontend/tabs/hr_payroll/hr_payroll.js")
    html = repo_src("frontend/tabs/hr_payroll/hr_payroll.html")
    assert not _EMOJI.search(js) and not _EMOJI.search(html), "UI 無 emoji（owner 鐵則）"
    code = js_code_only(js)
    assert "export async function initHrPayrollTab()" in code
    assert "authDownload(`${API}/runs/${_run.id}/export`" in code, "匯出要帶 token"
    assert "/*" not in code, "跟 hr_leave.js 一樣只用雙斜線註解"
    assert "confirm(`確認 ${_run.month} 薪資單" in code, "確認前要問一次"


def test_cashflow_fallback_excludes_proxy_payroll():
    """BUG-7：代發薪資是過帳（股東自己的人），不是公司費用 —— 現金流的固定月成本不該把它算進去。"""
    body = func_body(repo_src("routers/api_cashflow.py"), "async def _payroll_monthly_cost(")
    assert 'PayrollLine.payroll_entity == "公司"' in body, "只算公司要付的那幾列"
