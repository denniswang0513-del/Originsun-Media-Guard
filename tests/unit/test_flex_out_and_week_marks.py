"""彈性外出＋週標示＋行事曆浮動視窗（owner 2026-09-15）。

- 「每天兩小時的彈性外出 幫我規劃進去，也放進休假規章」→ 自己登記不用核准、一筆最多 2 小時、同一天合計 2 小時、不累積。
- 「如果有人收假 團隊的一週與他的一週要標示」→ team_week 帶假別／半天；我的一週有 /me/week_marks。
- 「行事曆用彈出的 跟零用金一樣」→ 工作台第三顆鈕開 /calendar.html（同一份 calendar 元件、白底）。
"""
from core import leave_logic as L
from tests.unit._srcscan import func_body, js_code_only, js_func_body, repo_src


def test_flex_out_rule_two_hours_per_day_not_accumulated():
    assert L.FLEX_OUT_MAX_MINUTES == 120 and L.FLEX_OUT_RULE == "每日可彈性外出兩小時"
    assert L.flex_out_check("10:00", "12:00") == (120, "")
    assert L.flex_out_check("10:00", "11:30") == (90, "")
    mins, err = L.flex_out_check("13:00", "16:00")
    assert mins == 180 and "最多 2 小時" in err
    mins, err = L.flex_out_check("14:00", "15:00", used_today=120)
    assert mins == 60 and "剩 0 分鐘" in err                       # 同一天合計也不能超過
    assert L.flex_out_check("14:00", "15:00", used_today=60) == (60, "")
    assert L.flex_out_check("12:00", "10:00")[1] == "結束要晚於開始"
    assert L.flex_out_check("ab", "10:00")[1] == "時間格式要是 HH:MM"
    assert L.hm_minutes("25:00") is None and L.hm_minutes("09:05") == 545


def test_flex_out_endpoints_are_own_scope_and_check_the_rule():
    src = repo_src("routers/api_me.py")
    for ep in ('@router.get("/flex_out")', '@router.post("/flex_out")', '@router.delete("/flex_out/{item_id}")', '@router.get("/week_marks")'):
        assert ep in src, ep
    create = func_body(src, "async def create_flex_out(")
    assert 'require_bound_staff(request, "me_leave")' in create
    assert "flex_out_check(body.start_time, body.end_time, used)" in create and "HTTPException(status_code=422, detail=err)" in create
    assert "HrFlexOuting.date == day" in create, "同一天已登記的分鐘要加進去算"
    delete = func_body(src, "async def delete_flex_out(")
    assert 'HrFlexOuting.staff_id == ident["staff_id"]' in delete, "只能刪自己的"
    # 表：不走請假單（不進時數帳、不進休假總表）
    model = repo_src("db/models/_workos.py")
    assert 'class HrFlexOuting(Base):' in model and '__tablename__ = "hr_flex_outings"' in model
    assert "HrFlexOuting" in repo_src("db/models/__init__.py")


def test_team_week_and_week_marks_carry_kind_and_half_day():
    src = repo_src("routers/api_me.py")
    tw = func_body(src, "async def team_week(")
    assert '"leave_detail": leave_detail' in tw and '"flex_out": flex' in tw
    assert "leave_detail[d].append(_leave_mark(l))" in tw
    mark = func_body(src, "def _leave_mark(")
    assert '"kind": l.leave_type or "請假", "part": part' in mark
    wm = func_body(src, "async def week_marks(")
    assert "HrLeaveRequest.staff_id == sid" in wm and "HrFlexOuting.staff_id == sid" in wm, "只有自己的"
    assert 'off_days += 0.5' in wm and '"leave_days": off_days' in wm
    # 行事曆事件流多一種 flex_out（不進 Google 日曆）
    cal = repo_src("routers/api_calendar.py")
    assert "events += await _flex_out_events(session, d0, d1, me)" in cal
    assert '"kind": "flex_out"' in func_body(cal, "async def _flex_out_events(")


def test_week_views_mark_leave_and_flex_out():
    tw = js_code_only(repo_src("frontend/js/shared/ts-zone/team-week.js"))
    assert "export function _partWord(m)" in tw
    cell = js_func_body(tw, "const cell = (name, iso) =>") if "const cell = (name, iso) =>" in tw else tw
    assert '<div class="c off${m.part !== "all" ? " half" : ""}">${_partWord(m)}休假<i>${esc(m.kind)}</i></div>' in cell
    assert '<div class="c fo">外出 ${esc(f.start_time)}–${esc(f.end_time)}</div>' in cell
    assert "else if (leaveOf(iso).includes(name)) parts.push('<div class=\"c off\">休假</div>');" in cell, "舊後端只給名字也還能標"
    plan = js_code_only(repo_src("frontend/js/shared/ts-zone/plan.js"))
    assert "z.api.weekMarks" in plan and "s.planMarks = marks" in plan
    assert '<span class="addbtn off">休假日不排</span>' in plan, "整天休假那欄不能加項"
    assert "只能排${otherHalf(d)}" in plan, "半天只鎖那半天"
    assert '<span class="k">休假</span>${marks.leave_days} 天' in plan
    ctx = js_code_only(repo_src("frontend/js/shared/ts-zone/ctx.js"))
    assert 'weekMarks: (start) => "/api/v1/me/week_marks?start=" + start' in ctx
    assert "weekMarks: (start) => (whoIsMe() ? own.weekMarks(start) : null)" in ctx, "管理視角看別人的板沒有這支"
    for css in ("frontend/my.html", "frontend/tabs/timesheets/ts-zone.css"):
        c = repo_src(css)
        assert "table.week .c.off.half" in c and "table.week .c.fo" in c and ".pcol .addbtn.off" in c, css


def test_leave_card_has_the_flex_out_box_and_rules_card_has_the_rule():
    js = js_code_only(repo_src("frontend/js/my/cards-hr.js"))
    assert "const FO_MAX = 120;" in js
    render = js_func_body(js, "function renderLeave(")
    assert 'id="fo-box"' in render and '每日可彈性外出兩小時' in render and 'onclick="foSubmit()"' in render
    check = js_func_body(js, "function foCheck(")
    assert "一次最多 2 小時，超過的請另外請假" in check and "used + m > FO_MAX" in check
    assert "_foLoad();" in js_func_body(js, "async function loadLeave(")
    lv = repo_src("frontend/leave.html")
    assert "<b>彈性外出</b>" in lv and "每日可彈性外出兩小時，不扣假、不扣薪，不用核准。" in lv
    assert lv.index("<b>彈性外出</b>") < lv.index("<b>事假</b>"), "排在事假前面"


def test_calendar_page_is_the_shared_component_in_a_light_host():
    page = repo_src("frontend/calendar.html")
    assert 'import { mountCalendar } from "/js/shared/calendar/index.js";' in page
    assert "light: true" in page and 'view: window.innerWidth < 640 ? "day" : "month"' in page
    assert 'const CAL_KEYS = ["me_today_zone", "me_team_week", "bulletin", "crm_projects", "timesheets"];' in page
    assert 'document.documentElement.classList.add("embed");' in page
    ctx = js_code_only(repo_src("frontend/js/shared/calendar/ctx.js"))
    assert "flex_out: '外出'" in ctx and "if (ev.kind === 'flex_out') return hex.out" in ctx
