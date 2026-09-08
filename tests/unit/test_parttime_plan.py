# -*- coding: utf-8 -*-
"""兼職排班（owner 2026-09-08）＋ 專案頁「里程碑（按週）」。

兼職排班：有 me_plan_parttime 的正職（在職／合夥）幫「兼職」排我的一週的卡。卡＝他的一列工時（plan、時數 0、planned_by）。
只碰計畫列、時數不收；不走 own-scope 的 /mine/*（那邊絕不收 client 給的 staff_id），另開 /timesheets/plan-for/*。
里程碑（按週）：專案檔案（/timesheets/project 帶 milestone_weeks）與 CRM 專案詳情（動態 import 同一支畫法）。
"""
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, my_page_src, repo_src

TS = repo_src("routers/api_timesheets.py")


def test_planned_by_column_is_everywhere_a_timesheet_column_must_be():
    assert "planned_by = Column(String(64), nullable=True)" in repo_src("db/models/_workos.py")
    assert '("timesheets", "planned_by", "VARCHAR(64)")' in repo_src("main.py"), "ADD COLUMN IF NOT EXISTS 清單"
    svc = repo_src("services/timesheet_self.py")
    assert '"planned_by"' in svc.split("_SNAP_COLS = (")[1].split(")")[0], "合併快照要含它（test_merge_snapshot 也會抓）"
    assert '"planned_by": getattr(r, "planned_by", None) or ""' in svc, "ts_dict 要回它，卡上才畫得出「由 X 排」"


def test_plan_for_guard_who_can_plan_for_whom():
    body = code_only(func_body(TS, "async def _plan_for_ident("))
    assert 'full = payload_grants(payload, "timesheets")' in body, "管理員／工作追蹤模組整區恆過（布林探針，不留假紀錄）"
    assert 'require_bound_staff(request, "me_plan_parttime")' in body, "其他人要綁定＋兼職排班鑰匙"
    assert "is_active_staff(getattr(me[\"staff\"], \"status\", None))" in body, "本人要在職／合夥"
    assert '(target.status or "").strip() != _PARTTIME' in body and '_PARTTIME = "兼職"' in TS, "對方要是兼職"
    assert '"ident": {"staff_id": target.id, "staff": target, "username": who}' in body, "own-scope 用對方的身份、記排的人"


def test_plan_for_endpoints_only_touch_plan_rows_and_never_take_hours():
    for path in ('"/plan-for/targets"', '"/plan-for/{staff_id}/rows"', '"/plan-for/{staff_id}/{row_id}"'):
        assert path in TS, path
    assert TS.index('@router.get("/plan-for/targets")') < TS.index('@router.get("/plan-for/{staff_id}/rows")'), "targets 要排在 {staff_id} 前面"
    add = code_only(func_body(TS, "async def plan_for_add("))
    assert "r.plan = True" in add and "r.hours = None" in add and "r.planned_hours = None" in add
    assert 'obj.planned_by = ctx["username"] or None' in add
    assert 'notify_tab_async("plan_parttime"' in add
    upd = code_only(func_body(TS, "async def plan_for_update("))
    assert "_plan_row_of(session, ctx[\"target\"], row_id)" in upd and "body.hours = None" in upd and "body.plan = True" in upd
    dele = code_only(func_body(TS, "async def plan_for_delete("))
    assert "_plan_row_of(session, ctx[\"target\"], row_id)" in dele
    guard = code_only(func_body(TS, "async def _plan_row_of("))
    assert 'r.staff_id != target.id or (r.status or "") != "plan"' in guard, "只有對方的計畫列能改／刪"
    for f in ("notifier.py", "config.py"):
        assert '"plan_parttime":' in repo_src(f), f


def test_workspace_has_the_parttime_window():
    html = my_page_src()
    assert 'WS.allowed.includes("me_plan_parttime")' in html and 'data-z1="pt-open">兼職排班</button>' in html
    for act in ("pt-open", "pt-close", "pt-week", "pt-add", "pt-add-ok", "pt-add-cancel", "pt-del", "pt-defer", "pt-from-ms", "pt-copy-last"):
        assert f'act === "{act}"' in html, act
    assert "/api/v1/timesheets/plan-for/${encodeURIComponent(_pt.sid)}" in html and '"/api/v1/timesheets/plan-for/targets"' in html
    z = js_code_only(html)
    assert "plan: true" in js_func_body(z, "async function _ptSubmitAdd(day)")
    assert "_PUT({ work_date: day })" in js_func_body(z, "async function _ptMove(id, day)")
    # 兼職自己那邊看得到是誰排的
    assert "由 ${esc(i.planned_by)} 排" in js_func_body(z, "function _planCardHtml(i)")
    # 關視窗要讓團隊的一週重抓（卡出現在他那一列）
    assert '_z1MarkStale("z1-week")' in js_func_body(z, "async function _z1Action(btn, ev)")


def test_project_file_and_crm_detail_show_milestones_by_week():
    assert '"milestone_weeks": milestone_weeks' in code_only(func_body(TS, "async def project_file("))
    tsp = repo_src("frontend/js/shared/ts-projects.js")
    assert "export function milestoneWeeksHtml(weeks)" in tsp
    assert "d.milestone_weeks !== undefined ? `<div class=\"ts-card\"" in tsp, "舊後端沒這欄就不畫（不噴 undefined）"
    det = repo_src("frontend/tabs/crm/crm-projects-detail.js")
    assert '<div id="pi-milestones"></div>' in det
    assert "import('../../js/shared/ts-projects.js')" in det and "typeof m.milestoneWeeksHtml !== 'function'" in det, \
        "動態 import＋檢查函式存在：ts-projects.js 被 CF 快取四小時，舊版沒這支時不能讓分頁掛掉"
    assert "authFetch('/api/v1/milestones/project/'" in det
