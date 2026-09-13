# -*- coding: utf-8 -*-
"""補填提醒（owner 2026-09-13「新增提醒區塊，有沒寫的工作日誌、哪一天沒填的專案日誌，好讓同事補填，持續出現到填完為止」
＋「可以有個按鈕給他跳到那個地方去填寫」＋「只有在職需要」）。規則 core.reminder_logic；I/O routers/api_me `/me/reminders`。"""
from datetime import date

from core.reminder_logic import missing_journal_weeks, missing_log_days
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src

TODAY = date(2026, 9, 13)     # 週日


def test_missing_days_skip_weekends_holidays_leave_today_and_days_before_hire():
    filled = {date(2026, 9, 9): {"draft"}, date(2026, 9, 10): {"pending"}, date(2026, 9, 11): {"pending", "draft"}}
    out = missing_log_days(TODAY, date(2026, 9, 7), filled, holidays={date(2026, 9, 8): "國定假日"}, leave_days=[date(2026, 9, 7)])
    # 9/7 請假、9/8 假日、9/9 有列、9/10 只有草稿、9/11 有實際、9/12–13 週末／今天
    assert out == {"missing": [], "pending": [date(2026, 9, 10)]}
    # 沒到職日：往回 30 天都看；一列都沒有的工作日全列出來
    out2 = missing_log_days(date(2026, 9, 10), None, {}, holidays=None)
    assert out2["missing"][0] == date(2026, 8, 11) and out2["missing"][-1] == date(2026, 9, 9)
    assert all(d.weekday() < 5 for d in out2["missing"]) and out2["pending"] == []
    # 補班日的週六算工作日
    assert date(2026, 9, 12) in missing_log_days(TODAY, None, {}, holidays={date(2026, 9, 12): "補班日"})["missing"]


def test_missing_journal_weeks_are_the_past_six_weeks_not_submitted_since_hire():
    st = {date(2026, 9, 7): "submitted", date(2026, 8, 31): "draft", date(2026, 8, 24): "submitted"}
    out = missing_journal_weeks(TODAY, None, st)
    assert out[0] == (date(2026, 7, 27), "none") and (date(2026, 8, 31), "draft") in out
    assert date(2026, 9, 7) not in dict(out) and date(2026, 8, 24) not in dict(out)
    assert date(2026, 9, 14) not in dict(out) and len(out) == 5          # 6 週扣掉 8/24、9/7 已送出；本週不算（週記是週一寫上一週）
    # 到職那一週起：8/26 到職 → 8/24 那一週算，之前的不追
    out2 = missing_journal_weeks(TODAY, date(2026, 8, 26), {})
    assert [ws for ws, _s in out2] == [date(2026, 8, 24), date(2026, 8, 31)]


def test_endpoint_only_nags_active_staff_and_excludes_approved_leave():
    fn = code_only(func_body(repo_src("routers/api_me.py"), "async def my_reminders("))
    assert 'ident = await _me_bound(request, "me_worklog")' in fn
    assert 'if not is_active_staff(getattr(staff, "status", None)):' in fn and "return empty" in fn     # 只有在職需要
    assert 'HrLeaveRequest.status == "已核准"' in fn and "holidays = await holidays_map(session)" in fn
    assert '.where(Timesheet.status != "plan")' in fn                                                    # 計畫卡不算填了
    assert '"journal"' in fn and "missing_journal_weeks(today, hire, statuses)" in fn                     # 沒週記鑰匙的人不催週記


def test_zone_draws_the_strip_above_the_tabs_with_goto_buttons():
    idx = js_code_only(repo_src("frontend/js/shared/ts-zone/index.js"))
    assert '<div id="z1-remind"></div>' in idx and "loadReminders();" in idx
    assert 'if (act === "journal-goto") { if (z.hooks.journalGoto) z.hooks.journalGoto(btn.dataset.week || ""); return; }' in idx
    goto = js_code_only(func_body(repo_src("frontend/js/shared/ts-zone/index.js"), 'if (act === "day-goto") {'))
    assert 'switchZ1("log")' in goto and "scrollIntoView" in goto                                        # 從別的視圖點也跳得到
    rm = js_code_only(js_func_body(repo_src("frontend/js/shared/ts-zone/remind.js"), "export function remindHtml(r) {"))
    assert 'if (!r || r.active === false) return "";' in rm and 'if (!parts.length) return "";' in rm  # 填完就不畫
    assert 'data-z1="day-goto"' in rm and 'data-z1="journal-goto"' in rm
    # 員工頁：週記那顆跳到第二區那一週（postMessage 給內嵌的 journal.html）；送出後重抓
    z1 = repo_src("frontend/js/my/zone1.js")
    assert 'type: "journal-goto-week", week_start: weekStart' in z1 and 'e.data.type === "journal-submitted"' in z1
    jr = repo_src("frontend/journal.html")
    assert "d.type !== 'journal-goto-week'" in jr and "type: 'journal-submitted'" in jr
    # 管理視角看別人／全部：沒有「我的」提醒
    assert "reminders: () => (whoIsMe() ? own.reminders() : null)," in repo_src("frontend/js/shared/ts-zone/ctx.js")
