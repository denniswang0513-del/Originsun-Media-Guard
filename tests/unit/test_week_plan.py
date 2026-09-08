# -*- coding: utf-8 -*-
"""我的週規劃（owner 2026-09-08；示範 /demo/week-plan.html 定稿）：
一張卡＝一列工時（status=plan、沒時數、planned_hours 不用）；不判有做沒做；執行動作只有填時數與「挪到隔天」。
釘住：row_state 的 plan 旗標、schema 欄位、normalize_row／apply_update 傳遞、格子的「計畫」狀態列、
員工頁的「我的一週」視圖、團隊的一週不再把沒時數的列畫成「0 h」。"""
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src

MY = "frontend/my.html"
SHEET = "frontend/js/shared/ts-sheet.js"


def test_row_state_plan_flag_without_hours():
    """沒時數、沒 planned_hours，但 plan=True → plan（不是 pending）；填了時數 → draft（旗標不擋）。"""
    from core.hr_logic import PENDING_STATUS, row_state
    assert row_state(0, None) == PENDING_STATUS
    assert row_state(0, None, plan=True) == "plan"
    assert row_state(2, None, plan=True) == "draft"
    assert row_state(0, 3) == "plan", "舊的計畫時數路照舊"


def test_schema_and_normalize_carry_the_plan_flag():
    from core.schemas import TimesheetManualRow
    assert TimesheetManualRow.model_fields["plan"].default is None, "舊分頁不帶 plan：Optional=None，不能是 False 預設洗掉狀態"
    body = code_only(func_body(repo_src("services/timesheet_manual.py"), "def normalize_row("))
    assert 'plan=bool(getattr(r, "plan", None))' in body


def test_update_keeps_plan_until_hours_are_filled():
    """格子的 PUT 與挪日期的 PUT 都不帶 plan：列是 plan 就沿用（改內容／挪隔天還是計畫；填時數 row_state 自己變 draft）。"""
    body = code_only(func_body(repo_src("services/timesheet_self.py"), "async def apply_update("))
    assert '"plan" not in sent and r.status == "plan"' in body and 'carry["plan"] = True' in body


def test_incomplete_reminder_ignores_plan_rows():
    """「專案紀錄未完成」只找 pending；未來排的計畫列不能被當成未完成。"""
    body = code_only(func_body(repo_src("routers/api_timesheets.py"), "async def my_incomplete_days("))
    assert "Timesheet.status == PENDING_STATUS" in body and '"plan"' not in body


def test_sheet_marks_plan_rows_and_offers_defer():
    js = js_code_only(repo_src(SHEET))
    assert "export const planStateHtml" in js and 'data-ts-action="row-defer"' in js
    assert "plan: r.status === 'plan' && !(r.hours > 0)" in js, "有時數的 plan 列（舊資料）不當計畫"
    assert "if (tr.dataset.plan) body.plan = true" in js, "計畫列的自動存要帶 plan，不然改個字就變草稿"
    save = js_func_body(js, "export function wireAutosave(host, cfg = {})")
    assert "delete tr.dataset.plan" in save and "planStateHtml()" in save, "填了時數要脫掉計畫；還是計畫要畫回「計畫＋隔天」"
    assert "tr[data-plan] td" in js and "data-plan=\"1\"" in js.replace("'", '"'), "藍底靠 data-plan，列的 class 字串不動（test_ts_shared_components 釘著唯一一份）"


def test_workspace_has_the_my_week_view():
    html = repo_src(MY)
    assert 'btn("plan", "我的一週")' in html      # 2026-09-08 起按鈕依鑰匙動態畫（test_me_zone_keys）
    assert '["log", "plan", "week", "find"].includes(v)' in html
    for act in ("plan-week", "plan-add", "plan-add-ok", "plan-add-cancel", "plan-del", "plan-defer", "plan-from-ms", "plan-copy-last", "row-defer"):
        assert f'act === "{act}"' in html, act
    # 卡＝POST /timesheets/mine/rows 帶 plan:true；挪＝PUT 只帶 work_date；只能排自己的（沒有 staff_id 參數）
    assert "plan: true" in html and "_PUT({ work_date: day })" in html
    assert "staff_id" not in js_func_body(js_code_only(html), "async function _planCreate(rows)")
    # 不判有做沒做：沒有未執行／沒做／對到當天紀錄這些字
    z = html[html.index("視圖 1.5：我的一週"):html.index("視圖 2：團隊的一週")]
    for bad in ("未執行", "沒做了", "對到當天紀錄", "isLate", "matchOf"):
        assert bad not in z, bad
    # 兩邊是同一批列：一邊動了另一邊要標 stale，切過去重抓
    assert "function _z1MarkStale(" in html and "el.dataset.stale" in html


def test_team_week_does_not_draw_zero_hours():
    html = repo_src(MY)
    cell = html[html.index("async function loadTeamWeek()"):html.index("// ── 本週里程碑")]
    assert "計畫 ${i.planned_hours ?? \"\"} h" not in cell, "沒計畫時數的計畫列曾畫成「計畫  h」"
    assert '<span class="hrs plan">草稿</span>' in cell and "i.hours > 0 ?" in cell


def test_timesheets_tab_handles_the_same_defer_button():
    js = repo_src("frontend/tabs/timesheets/timesheets.js")
    assert "act === 'row-defer'" in js and "work_date: shiftDays(_day, 1)" in js


def test_no_new_named_import_from_shared_sheet_module():
    """Cloudflare 給 .js 4 小時快取：my.html 這輪不能從 ts-sheet.js 新增具名 import（舊 js 沒那個 export 整個 module 會掛）。"""
    html = repo_src(MY)
    line = next(l for l in html.splitlines() if "from '/js/shared/ts-sheet.js'" in l)
    assert "planStateHtml" not in line
