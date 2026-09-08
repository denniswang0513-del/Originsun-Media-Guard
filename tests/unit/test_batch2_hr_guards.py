# -*- coding: utf-8 -*-
"""權限稽核第二批（docs/RBAC_AUDIT.md §4、docs/RBAC_PLAN.md §5，2026-09-08）——人事／福委會／工作追蹤／手機殼。

釘四件事（每件後端守衛＋前端藏鈕成對）：
  1. 假勤：看清單／登記／改欄位＝hr_leave；核准／退回／消假決定、補休 credits 發放／決定／刪除、假日表寫入、
     特休額度＝check_admin（owner：假勤核准留管理員）。hr_leave.js 那些鈕只在 Lv3 畫。
  2. 福委會：池／撥款／登記／會計包的**讀取**＝money_dep＋（hr_benefits／finance_approve／crm_invoices 任一），
     私帳那本仍要 finance_mine；審核類維持 _can_manage／_check_approver。hr_benefits.js 審核類鈕只在 finance_approve 畫。
  3. 工作追蹤：/summary、/projects、/recent 開給 timesheets 鑰匙，summary 對沒私帳 scope 的人抹 suggested_hours 與
     未對映的候選／建議；project_map／remap／budgets／project_budget 仍守私帳 wall；/me/team/* 加收 timesheets；
     timesheets.js 的私帳寫入鈕（指定專案／建議預算／改預算／案型／同步 token／設定分頁）只在 Lv3 畫。
  4. 手機殼底部「工作紀錄」「假勤」依 me.modules 藏（Lv3 恆畫）。
"""
import re

from fastapi import HTTPException

from tests.unit._req import token_request
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src

HR = code_only(repo_src("routers/api_hr.py"))
BEN = code_only(repo_src("routers/crm/benefits.py"))
TS = code_only(repo_src("routers/api_timesheets.py"))
ME = code_only(repo_src("routers/api_me.py"))
HR_JS = js_code_only(repo_src("frontend/tabs/hr_leave/hr_leave.js"))
BEN_JS = js_code_only(repo_src("frontend/tabs/hr_benefits/hr_benefits.js"))
TS_JS = js_code_only(repo_src("frontend/tabs/timesheets/timesheets.js"))
TSP_JS = js_code_only(repo_src("frontend/js/shared/ts-projects.js"))
UI_JS = js_code_only(repo_src("frontend/m/ui.js"))
MCRM_JS = js_code_only(repo_src("frontend/m/crm.js"))


def _status(fn, *args):
    """守衛的結果：丟 HTTPException 就回狀態碼，否則回它的回傳值。"""
    try:
        return fn(*args)
    except HTTPException as e:
        return e.status_code


async def _astatus(coro):
    try:
        await coro
    except HTTPException as e:
        return e.status_code
    return 200


# ── 1. 假勤 ──────────────────────────────────────────────────────────────

HR_ADMIN_ONLY = ("async def approve_leave(", "async def reject_leave(", "async def decide_cancel(",
                 "async def create_credit(", "async def _decide_credit(", "async def delete_credit(",
                 "async def upsert_holiday(", "async def delete_holiday(", "async def import_holidays(",
                 "async def set_annual_leave(")
HR_MODULE = ("async def list_leave(", "async def create_leave(", "async def leave_quota(", "async def leave_context(",
             "async def update_leave(", "async def delete_leave(", "async def all_balances(", "async def list_credits(",
             "async def list_holidays(")


def test_hr_approval_family_is_admin_only_and_the_rest_stays_hr_leave():
    for fn in HR_ADMIN_ONLY:
        body = func_body(HR, fn)
        assert "check_admin(request)" in body, fn
        assert 'check_admin_or_module(request, "hr_leave")' not in body, fn
    for fn in HR_MODULE:
        body = func_body(HR, fn)
        assert 'check_admin_or_module(request, "hr_leave")' in body, fn
        assert "check_admin(request)" not in body, fn
    # credits 的 approve／reject 走 _decide_credit（它自己 check_admin）
    assert "_decide_credit(" in func_body(HR, "async def approve_credit(")
    assert "_decide_credit(" in func_body(HR, "async def reject_credit(")


def test_hr_leave_js_draws_approval_buttons_only_for_lv3():
    assert "const isAdmin = () => (window._accessLevel || 0) >= 3;" in HR_JS
    q = js_func_body(HR_JS, "function _queueCard(it) {")
    assert "!isAdmin() ? ''" in q and "data-approve-req" in q and "data-cancel-decide" in q
    c = js_func_body(HR_JS, "function _creditsHtml(staffId, name) {")
    assert "used > 0 || !isAdmin() ? ''" in c, "刪 credit 鈕沒依 Lv3 藏"
    assert "${!isAdmin() ? '' : `<div class=\"hl-form\" data-credit-form=" in c, "手開 credit 表單沒依 Lv3 藏"
    h = js_func_body(HR_JS, "function _renderHolidays() {")
    assert "${!isAdmin() ? '' : `<input type=\"date\" id=\"hl-h-date\">" in h, "假日新增列沒依 Lv3 藏"
    assert "${!isAdmin() ? '' : `<button class=\"hl-btn danger\" data-del-holiday=" in h
    assert "${!isAdmin() ? '' : `<div style=\"margin-top:14px;padding-top:12px;border-top:1px solid #333;\">" in h, "CSV 匯入區沒依 Lv3 藏"
    # 看清單與代登（hr_leave）不藏：篩選列與「建立」鈕不看 isAdmin
    r = js_func_body(HR_JS, "function _renderRecords() {")
    assert "isAdmin()" not in r
    # 這個檔只准雙斜線註解（檔頭第 4 行帶了 /*，見檔內說明）
    raw = repo_src("frontend/tabs/hr_leave/hr_leave.js")
    assert "*/" not in raw, "hr_leave.js 出現了星號斜線——js_code_only 會把中間整段當註解剝掉"


# ── 2. 福委會 ───────────────────────────────────────────────────────────

BEN_READ = ("async def list_pools(", "async def pool_detail(", "async def list_entries(",
            "async def accounting_package(", "async def accounting_package_csv(")
BEN_MANAGE = ("async def create_pool(", "async def update_pool(", "async def delete_pool(", "async def add_allowance(",
              "async def add_allowances_bulk(", "async def update_allowance(", "async def delete_allowance(",
              "async def add_funding(", "async def delete_funding(", "async def add_entry_for(",
              "async def approve_entry(", "async def reject_entry(", "async def pay_entry(")


def test_benefit_reads_take_the_tab_key_and_manage_endpoints_keep_the_full_wall():
    assert 'READ_MODULES = ("hr_benefits", APPROVE_MODULE, "crm_invoices")' in BEN
    rd = func_body(BEN, "def _read_entity(")
    assert "check_admin_or_module(request, *READ_MODULES)" in rd
    assert 'require_entity(request, "mine", level="full")' in rd, "私帳那本仍要 finance_mine"
    for fn in BEN_READ:
        body = func_body(BEN, fn)
        assert "_read_entity(request" in body, fn
        assert 'require_entity(' not in body, fn
        if fn not in ("async def accounting_package(", "async def accounting_package_csv("):   # 會計包：收尾 review 補回審核者（全員福委金額）
            assert "_check_approver(request)" not in body, fn
    for fn in BEN_MANAGE:
        body = func_body(BEN, fn)
        assert 'level="full")' in body or "_can_manage(request" in body, fn
        assert "_read_entity(" not in body, fn
    # 讀端點仍掛 money_dep（金額牆不放）
    raw = repo_src("routers/crm/benefits.py")
    for path in ('"/benefits/pools"', '"/benefits/pools/{pool_id}"', '"/benefits/entries"',
                 '"/benefits/accounting-package"', '"/benefits/accounting-package.csv"'):
        assert re.search(r'@router\.get\(' + re.escape(path) + r', dependencies=\[Depends\(money_dep\)\]\)', raw), path


def test_read_entity_behaviour():
    from routers.crm.benefits import _read_entity
    assert _status(_read_entity, token_request(modules=["hr_benefits"]), "") == "parent"
    assert _status(_read_entity, token_request(modules=["crm_invoices"]), "parent") == "parent"
    assert _status(_read_entity, token_request(modules=["finance_approve"]), "") == "parent"
    assert _status(_read_entity, token_request(access_level=3), "") == "parent"
    assert _status(_read_entity, token_request(modules=["me_benefits"]), "") == 403       # 自助鑰匙不是管理端
    assert _status(_read_entity, token_request(modules=[]), "") == 403
    assert _status(_read_entity, token_request(modules=["hr_benefits"]), "mine") == 403   # 私帳仍指名制
    assert _status(_read_entity, token_request(access_level=3), "mine") == 403             # Lv3 不隱含 finance_mine
    assert _status(_read_entity, token_request(modules=["hr_benefits", "finance_mine"]), "mine") == "mine"
    assert _status(_read_entity, token_request(modules=["hr_benefits"]), "other") == 422


def test_hr_benefits_js_hides_approver_buttons_without_finance_approve():
    assert "import { hasModule } from '../crm/crm-utils.js';" in BEN_JS
    assert "const canApprove = () => hasModule('finance_approve');" in BEN_JS
    for fn in ("function poolFormHtml() {", "function packageHtml() {"):
        assert "if (!canApprove()) return '';" in js_func_body(BEN_JS, fn), fn
    assert "if (!canApprove()) {" in js_func_body(BEN_JS, "function _proof(e) {")
    assert "if (!canApprove()) {" in js_func_body(BEN_JS, "function aboutHtml(p) {")
    pend = js_func_body(BEN_JS, "function pendingHtml() {")
    assert "${canApprove() ? `" in pend and "_hbApprove" in pend
    al = js_func_body(BEN_JS, "function allowanceHtml(p) {")
    assert al.count("canApprove() ?") >= 2, "改額度／刪除 與 發額度表單 都要看 finance_approve"
    det = js_func_body(BEN_JS, "function detailHtml() {")
    assert "if (!canApprove()) {" in det, "登記明細的核准／匯款動作沒藏"
    assert det.count("${canApprove() ? `") >= 3, "撥款刪除、撥款進池、代員工登記 都要看 finance_approve"
    # 每一顆會打審核端點的鈕都在 canApprove 之下：沒有一個 window._hbXxx 的 onclick 落在守衛外
    for fn in ("function pendingHtml() {", "function allowanceHtml(p) {", "function detailHtml() {"):
        body = js_func_body(BEN_JS, fn)
        for m in re.finditer(r"onclick=\"window\._hb(\w+)\(", body):
            before = body[:m.start()]
            assert "canApprove()" in before, (fn, m.group(1))


# ── 3. 工作追蹤 ─────────────────────────────────────────────────────────

def test_timesheet_reads_open_to_tab_key_and_mine_writes_stay_walled():
    for fn in ("async def timesheet_projects(", "async def burn_summary(", "async def recent_rows("):
        body = func_body(TS, fn)
        assert 'check_admin_or_module(request, "timesheets")' in body, fn
        assert "_require_mine_admin(" not in body and "check_admin(request)" not in body, fn
    for fn, level in (("async def upsert_project_map(", "full"), ("async def remap_timesheets(", "full"),
                      ("async def set_budgets(", "full"), ("async def apply_suggested_budgets(", "full"),
                      ("async def set_project_budget(", "full")):
        body = func_body(TS, fn)
        assert f'_require_mine_admin(request, level="{level}")' in body, fn
    # summary：沒私帳 scope 就抹
    bs = func_body(TS, "async def burn_summary(")
    assert "viewer_has_mine_scope(request)" in bs and "_redact_summary(out)" in bs


def test_redact_summary_strips_suggested_hours_and_unmatched_diagnostics():
    from routers.api_timesheets import SUMMARY_PUBLIC_KEYS, UNMATCHED_PUBLIC_KEYS, _redact_summary
    assert "suggested_hours" not in SUMMARY_PUBLIC_KEYS
    assert "candidates" not in UNMATCHED_PUBLIC_KEYS and "suggestions" not in UNMATCHED_PUBLIC_KEYS
    out = {"projects": [{"project_id": "p1", "project_name": "A", "client": "C", "status": "進行中", "project_type": "廣告",
                         "hours_used": 12.0, "budget_hours": 40, "remaining": 28.0, "pct": 30, "rows": 3,
                         "last_entry": "2026-09-01", "stale": False, "suggested_hours": 55}],
           "unmatched": [{"project_name": "X", "hours_used": 2.0, "rows": 1, "reason": "ambiguous",
                          "candidates": [{"id": "g", "name": "私帳案", "client": "客"}], "suggestions": ["像這個"]}],
           "project_types": ["廣告"], "total_rows": 4}
    r = _redact_summary(dict(out))
    assert r["projects"] == [{k: out["projects"][0][k] for k in SUMMARY_PUBLIC_KEYS}]
    assert "suggested_hours" not in r["projects"][0]
    assert r["unmatched"] == [{"project_name": "X", "hours_used": 2.0, "rows": 1, "reason": "ambiguous"}]
    assert r["project_types"] == ["廣告"] and r["total_rows"] == 4
    # /me/projects_burn 用同一份鍵（兩邊漂開＝員工頁與工作追蹤看到的不是同一張表）
    assert "SUMMARY_PUBLIC_KEYS" in func_body(ME, "async def my_projects_burn(")


async def test_timesheet_read_guards_by_status():
    from routers import api_timesheets as m
    for coro in (m.timesheet_projects(token_request(modules=["me_finance"])),
                 m.burn_summary(token_request(modules=[])),
                 m.recent_rows(token_request(modules=["hr_leave"]))):
        assert await _astatus(coro) == 403


def test_me_team_takes_timesheets_or_me_finance_and_my_rows_stay_me_finance():
    ti = func_body(ME, "def _team_ident(")
    assert 'require_bound_staff(request, "timesheets", "me_finance")' in ti
    assert 'require_bound_staff(request, "me_finance")' in func_body(ME, "def _me_ident(")
    for fn in ("async def team_hours(", "async def team_projects(", "async def team_project_detail("):
        assert "await _team_ident(request)" in func_body(ME, fn), fn
    for fn in ("async def my_timesheets(", "async def add_my_timesheets(", "async def update_my_timesheet(",
               "async def delete_my_timesheet("):
        body = func_body(ME, fn)
        assert "await _me_ident(request)" in body and "_team_ident" not in body, fn


async def test_me_team_guard_refuses_without_either_key_before_binding():
    from routers.api_me import _team_ident
    assert await _astatus(_team_ident(token_request(modules=["me_leave"]))) == 403
    assert await _astatus(_team_ident(token_request(modules=[]))) == 403


def test_timesheets_js_draws_mine_write_buttons_only_for_lv3():
    assert "const _isAdmin = () => (window._accessLevel || 0) >= 3;" in TS_JS
    assert "{ editable: _isAdmin(), emptyText:" in js_func_body(TS_JS, "function _burnTbodyHtml() {")
    # 專案檔案頁保留 editable:true（加入比較／匯出 CSV 是唯讀動作），改預算單獨走 budgetEditable
    assert TS_JS.count("editable: true") == 1
    assert "editable: true, budgetEditable: _isAdmin()" in js_func_body(TS_JS, "function _renderProject(d, modal = false) {")
    assert "_isAdmin() ? `<button class=\"ts-btn ghost\" data-ts-action=\"map\"" in js_func_body(TS_JS, "function _unmatchedProjRowsHtml() {")
    assert "|| !_isAdmin() ? '' : `" in js_func_body(TS_JS, "function _unmatchedTbodyHtml() {")
    rp = js_func_body(TS_JS, "function _renderProjects(s) {")
    assert "const n = _isAdmin() ? s.projects.filter(" in rp, "套用建議預算 沒依 Lv3 藏"
    assert "${_isAdmin() ? b('settings', '設定') : ''}" in js_func_body(TS_JS, "function _viewBtns() {")
    assert "_isAdmin() ? `<button class=\"ts-btn ghost\" data-ts-action=\"token\"" in js_func_body(TS_JS, "function _renderSettings(s) {")
    # 專案檔案：改預算單獨看 budgetEditable；比較／匯出照 editable
    pf = js_func_body(TSP_JS, "export function projectFileHtml(d, opts = {}) {")
    assert "d.mapped && (opts.budgetEditable ?? true) ?" in pf


# ── 4. 手機殼 ───────────────────────────────────────────────────────────

def test_mobile_shell_hides_worklog_and_leave_tabs_by_modules():
    m = re.search(r"export const TAB_KEYS = (\{.*?\});", UI_JS)
    assert m, "ui.js 少了 TAB_KEYS"
    keys = m.group(1)
    assert "worklog: ['me_today_zone', ['me_worklog', 'me_week_plan']]" in keys   # 總開關＋子鑰匙任一（鏡射 core.auth）
    assert "leave: ['me_leave']" in keys
    csee = js_func_body(UI_JS, "export function canSeeTab(tab, me = state.me) {")
    assert "if (isAdmin(me)) return true;" in csee
    assert "need.every(k => (Array.isArray(k) ? k.some(x => mods.includes(x)) : mods.includes(k)))" in csee
    apply = js_func_body(UI_JS, "export function applyTabVisibility(me = state.me) {")
    assert "#m-tabbar button[data-tab]" in apply and "b.hidden = !canSeeTab(b.dataset.tab, me)" in apply
    # 殼在 state.me 設好後呼叫一次
    main = js_func_body(MCRM_JS, "async function main() {")
    assert "state.me = me;" in main and "applyTabVisibility(me);" in main
    assert "applyTabVisibility" in MCRM_JS.split("from './ui.js'")[0]
    # 鑰匙名要真的存在於後端清單
    auth = repo_src("core/auth.py")
    for k in ("me_today_zone", "me_worklog", "me_week_plan", "me_leave"):
        assert f'"{k}"' in auth or f"'{k}'" in auth, k
