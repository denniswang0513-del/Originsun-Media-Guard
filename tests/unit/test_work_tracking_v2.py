# -*- coding: utf-8 -*-
"""工作追蹤 v2（docs/WORK_TRACKING_V2_PLAN.md）：CRM 分頁＝員工四視圖（共用 js/shared/ts-zone）＋管理層。

釘的是「兩邊吃同一份、管理層只在管理視角長出來、員工端一個位元都不多」：
- ts-zone 的四個視圖只有一份，員工頁與 CRM 分頁都 import 它（不是各抄一份）
- 管理端點表（manageApi）跟員工端點表（defaultApi）分開；看誰的＝自己時走 own-scope
- 後端：/rows 收 date／from／to_day／staff_id；/people；/board 的 absent；/manual 走 add_rows；/me/team_week 管理視角不必綁定
- ts-sheet 的自動存／刪列端點可換（替別人填走 /manual、/rows/{id}），預設仍是 own-scope
"""
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src, timesheets_src

CTX = "frontend/js/shared/ts-zone/ctx.js"
IDX = "frontend/js/shared/ts-zone/index.js"
LOG = "frontend/js/shared/ts-zone/log.js"
TW = "frontend/js/shared/ts-zone/team-week.js"
TAB = "frontend/tabs/timesheets/timesheets.js"
API = "routers/api_timesheets.py"
ME = "routers/api_me.py"
SHEET = "frontend/js/shared/ts-sheet.js"


def test_both_hosts_mount_the_same_zone_module():
    assert "import * as TSZ from '/js/shared/ts-zone/index.js';" in repo_src("frontend/my.html")
    assert "TS.mountZone({" in repo_src("frontend/js/my/zone1.js")
    assert "import * as TSZ from '../../js/shared/ts-zone/index.js';" in repo_src(TAB)
    assert "TSZ.mountZone({" in repo_src(TAB)
    # 視圖的 loader 只在 ts-zone 註冊一次
    assert "z.loaders = { log: () => loadLog(), plan: () => loadMyWeek(), week: () => loadTeamWeek(), find: () => loadFind() };" in repo_src(IDX)


def test_manage_api_is_separate_and_self_falls_back_to_own_scope():
    ctx = js_code_only(repo_src(CTX))
    own = js_func_body(ctx, "export function defaultApi() {")
    man = js_func_body(ctx, "export function manageApi() {")
    for url in ("/api/v1/timesheets/rows", "/api/v1/timesheets/manual", "/api/v1/timesheets/summary", "/api/v1/timesheets/people", "/api/v1/timesheets/board"):
        assert url not in own, url
        assert url in man, url
    # 看誰的＝自己：每一支都退回 own（跟員工頁一模一樣）；別人：替他填帶 staff_id
    for k in ("today", "mineDay", "mineIncomplete", "mineRows", "mineRow", "mineCreate", "merge", "mergeUndo", "mergeLast"):
        assert f"{k}: (" in man and "whoIsMe() ?" in man, k
    assert "createBody: (rows) => (whoIsMe() ? { rows } : { staff_id: z.who.id, rows })" in man
    assert "sheetEndpoints: () => (whoIsOther() ?" in man
    assert "z.api = Object.assign(z.manage ? manageApi() : defaultApi(), opts.api || {});" in ctx


def test_manage_only_bits_are_guarded_and_the_employee_shell_never_turns_them_on():
    log = js_code_only(repo_src(LOG))
    assert "if (z.manage && !z.who) return _loadTeamDay();" in js_func_body(log, "export async function loadLog() {")
    assert "readonly: true" in js_func_body(log, "async function _loadTeamDay() {"), "全部＝唯讀"
    tw = js_code_only(repo_src(TW))
    assert "const sumTh = z.manage ?" in tw and "const nagged = (name) => z.manage &&" in tw
    zone1 = repo_src("frontend/js/my/zone1.js")
    assert "manage" not in js_func_body(zone1, "async function buildZone1() {")
    # CRM 分頁：預設管理視角、看誰的預設全部（owner 2026-09-11 拍板）
    tab = repo_src(TAB)
    assert "let _zoneManage = true;" in tab and "let _zoneWho = null;" in tab


def test_zone_stops_propagation_except_for_host_buttons():
    """CRM 分頁外層也掛了 [data-ts-action] 委派：ts-zone 處理完就不再冒泡，只有宿主宣告的鈕（passthrough）放行。"""
    idx = js_code_only(repo_src(IDX))
    mount = js_func_body(idx, "export function mountZone(opts) {")
    assert "if (!b || (z.hooks.passthrough && z.hooks.passthrough(b))) return;" in mount
    assert "e.stopPropagation();" in mount
    tab = repo_src(TAB)
    assert "passthrough: (b) => ['view', 'zone', 'export-project', 'export-month'].includes(b.dataset.tsAction)" in tab
    assert "projectPicker: null" in tab, "整個 tab 已掛一份專案浮層，格子不能再掛（同一個 input 兩個 root ↓↑ 走兩格）"


def test_sheet_endpoints_default_to_own_scope_and_can_be_overridden():
    sheet = js_code_only(repo_src(SHEET))
    assert "export const MINE_ENDPOINTS = {" in sheet
    assert "update: (id) => '/api/v1/timesheets/mine/' + id," in sheet and "create: () => '/api/v1/timesheets/mine/rows'," in sheet
    save = js_func_body(sheet, "export function wireAutosave(host, cfg = {}) {")
    assert "await f(ep.update(tr.dataset.id), { method: 'PUT', body });" in save
    assert "await f(ep.create(), { method: 'POST', body: ep.createBody([body]) });" in save
    rm = js_func_body(sheet, "export async function removeRow(host, tr, cfg = {}) {")
    assert "await f(ep.remove(tr.dataset.id), { method: 'DELETE' });" in rm
    assert "'/api/v1/timesheets/mine/' + tr.dataset.id" not in save + rm, "路徑只在 MINE_ENDPOINTS 一份"


def test_rows_endpoint_takes_day_range_and_staff_and_marks_editable():
    api = timesheets_src()   # 2026-09-12 起是套件 routers/timesheets/
    body = code_only(func_body(api, "async def ledger_rows("))
    assert 'staff_id: str = ""' in api.split("async def ledger_rows(")[1].split("):")[0]
    assert 'alias="from"' in api.split("async def ledger_rows(")[1].split("):")[0]
    win = code_only(func_body(api, "def _rows_window("))
    assert "(d1 - d0).days > ROWS_DAY_SPAN_MAX" in win and "ROWS_DAY_SPAN_MAX = 62" in api, "日期區間要有上限"
    assert "_rows_window(month, to, date, from_, to_day)" in body
    assert "q = q.where(Timesheet.staff_id == staff_id.strip())" in body
    assert '{**ts_dict(r, with_note=is_admin), "editable": is_admin}' in body, "格子照 editable 畫可改／唯讀"


def test_people_board_absent_and_manual_reuse_add_rows():
    api = timesheets_src()   # 2026-09-12 起是套件 routers/timesheets/
    people = code_only(func_body(api, "async def timesheet_people("))
    assert 'check_admin_or_module(request, "timesheets")' in people
    assert "is_active_staff(st)" in people and '"兼職"' in people
    board = code_only(func_body(api, "async def day_board("))
    assert "weekend = datetime.fromisoformat(day[\"date\"]).weekday() >= 5" in board
    assert 'it.get("status") != "plan"' in board, "只有計畫卡不算填了"
    assert 'day["absent"] = [] if weekend else' in board
    manual = code_only(func_body(api, "async def add_manual_rows("))
    assert "return await add_rows(session, ident, body.rows)" in manual and "insert_manual_rows" not in manual


def test_team_week_manage_view_needs_no_binding_and_only_it_gets_project_id():
    me = repo_src(ME)
    body = code_only(func_body(me, "async def team_week("))
    assert 'manage = payload_grants(check_admin_or_module(request, "timesheets", ME_ZONE_MASTER), "timesheets")' in body
    assert 'ident = None if manage else await _me_bound(request, "me_team_week")' in body
    assert '**({"project_id": it.get("project_id") or ""} if manage else {})' in body, "員工那份照舊不吐私帳案 id"


def test_money_columns_only_reach_private_scope_and_only_in_manage():
    """錢四欄：後端只給私帳 scope（/summary 尾端 _redact_summary 只留 SUMMARY_PUBLIC_KEYS）；前端只在管理視角、而且列上真的有欄位才畫。"""
    lk = code_only(func_body(repo_src("services/timesheet_lookup.py"), "async def burn_rows("))
    for k in ('"contract_net": contract_net', '"margin_pct": margin_pct', '"staff_cost": staff_cost'):
        assert k in lk, k
    pub = timesheets_src().split("SUMMARY_PUBLIC_KEYS = (")[1].split(")")[0]
    for k in ("contract_net", "margin_pct", "staff_cost", "suggested_hours"):
        assert k not in pub, f"{k} 不准進員工那份"
    find = js_code_only(repo_src("frontend/js/shared/ts-zone/find.js"))
    assert 'const _money = () => z.manage && (z.s.findRows || []).some(p => "contract_net" in p);' in find
    tp = js_code_only(repo_src("frontend/js/shared/ts-projects.js"))
    assert "export const BURN_MONEY_THEAD" in tp and "${opts.money ? _moneyCells(p) : ''}" in tp
    assert "colspan=\"${opts.money ? 14 : 10}\"" in tp


def test_manage_write_buttons_need_admin_and_go_through_the_same_endpoints_as_the_old_view():
    find = js_code_only(repo_src("frontend/js/shared/ts-zone/find.js"))
    assert "z.hooks.isAdmin()" in js_func_body(find, "function _unmatchedHtml() {")
    assert 'z.manage && z.hooks.isAdmin() && (s.findRows || []).some(p => p.suggested_hours != null)' in find, "套用建議預算只給管理員"
    m = js_func_body(find, "export async function mapSheetName(sheetName) {")
    assert "z.api.projectMap()" in m and "z.api.remap()" in m and "openProjectPicker({" in m
    assert 'budgetEditable: z.manage && z.hooks.isAdmin()' in find
    ctx = js_func_body(js_code_only(repo_src(CTX)), "export function manageApi() {")
    for k in ("conflicts", "suggestBudgets", "projectBudget", "projectMap", "remap", "mineProjects"):
        assert f"{k}: () =>" in ctx, k
    tw = js_code_only(repo_src(TW))
    assert "z.manage && z.api.conflicts && z.hooks.isAdmin() ? mjson(z.api.conflicts())" in tw
    tab = repo_src(TAB)
    assert "isAdmin: _isAdmin," in tab and "onCompare: (names) => { _compareNames = names; }" in tab


def test_dashboard_burn_top_is_redacted_like_summary_and_view_row_ignores_host_buttons():
    """review round 1（2026-09-12）：/dashboard 的 burn 前五整列直接回，burn_rows 帶了錢欄位之後就是個洞；
    ts-zone 的 .views 只認四個視圖的鈕（總表／儀表板／設定在同一列，是宿主的）。"""
    dash = code_only(func_body(timesheets_src(), "async def dashboard("))
    assert "if not viewer_has_mine_scope(request):" in dash and "{k: it.get(k) for k in SUMMARY_PUBLIC_KEYS} for it in burn" in dash
    mount = js_func_body(js_code_only(repo_src(IDX)), "export function mountZone(opts) {")
    assert "if (b && VIEWS.includes(b.dataset.view)) switchZ1(b.dataset.view);" in mount
    assert 'id="ts-zone-who" data-no-search' in repo_src(TAB), "看誰的用原生 select（searchable 小工具不跟程式改值）"
