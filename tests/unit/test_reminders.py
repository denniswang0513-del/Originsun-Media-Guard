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
    assert 'week_start: f.dataset.gotoWeek' in z1 and 'f.dataset.gotoWeek = weekStart;' in z1     # 週次記在 iframe 上、不關進閉包
    assert 'e.data.type === "journal-submitted"' in z1
    jr = repo_src("frontend/journal.html")
    assert "d.type !== 'journal-goto-week'" in jr and "type: 'journal-submitted'" in jr
    # 管理視角看別人／全部：沒有「我的」提醒
    assert "reminders: () => (whoIsMe() ? own.reminders() : null)," in repo_src("frontend/js/shared/ts-zone/ctx.js")


# ── 特徵測試（/polish 安全網）：端點整條走一遍（假 session 依 SQL 文字回不同結果）──

class _Res:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    """9/9 有實際列、9/10 只有草稿、9/11 是計畫卡（不算填）；9/7 核准的假；9/8 假日；週記 8/31 草稿、9/7 送出。"""

    def __init__(self):
        from datetime import datetime, timezone
        tz = timezone.utc
        self.ts = [(datetime(2026, 9, 9, 1, tzinfo=tz), "draft"), (datetime(2026, 9, 10, 1, tzinfo=tz), "pending"),
                   (datetime(2026, 9, 10, 2, tzinfo=tz), "pending")]
        self.leaves = [(datetime(2026, 9, 7, 0, tzinfo=tz), datetime(2026, 9, 7, 23, tzinfo=tz))]
        self.holidays = [(date(2026, 9, 8), "國定假日")]
        self.journals = [(date(2026, 8, 31), "draft"), (date(2026, 9, 7), "submitted")]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        t = str(stmt)
        if "timesheets" in t:
            assert "status != " in t                                    # 計畫卡不算
            return _Res(self.ts)
        if "hr_leave_requests" in t:
            return _Res(self.leaves)
        if "hr_holidays" in t:
            return _Res(self.holidays)
        if "work_journals" in t:
            return _Res(self.journals)
        raise AssertionError("unexpected statement: " + t[:80])


async def _run(monkeypatch, status="在職", grants_journal=True, today=TODAY):
    from types import SimpleNamespace
    from routers import api_me
    staff = SimpleNamespace(id="S1", name="小明", status=status, hire_date=date(2026, 9, 1))
    sess = _Session()

    async def _bound(request, *keys):
        return {"username": "ming", "staff_id": "S1", "staff": staff}
    monkeypatch.setattr(api_me, "_me_bound", _bound)
    monkeypatch.setattr(api_me, "db_factory_or_503", lambda: (lambda: sess))
    monkeypatch.setattr(api_me, "check_admin_or_module", lambda request, *k: {"sub": "ming"})
    monkeypatch.setattr(api_me, "payload_grants", lambda payload, *k: grants_journal)

    class _D(date):
        @classmethod
        def today(cls):
            return today
    monkeypatch.setattr(api_me, "date", _D)
    return await api_me.my_reminders(request=None)


async def test_endpoint_end_to_end(monkeypatch):
    out = await _run(monkeypatch)
    # 到職 9/1 起：9/1–9/4 沒填、9/7 請假、9/8 假日、9/9 填了、9/10 只有草稿、9/11 沒填（計畫卡不算）、9/12–13 週末／今天
    assert out["active"] is True and out["date"] == "2026-09-13"
    assert out["log_missing"] == ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-11"]
    assert out["log_pending"] == ["2026-09-10"]
    assert out["journals"] == [{"week_start": "2026-08-31", "status": "draft"}]   # 到職那週起；9/7 送出了；本週不算


async def test_endpoint_is_silent_for_people_who_left_and_skips_journals_without_the_key(monkeypatch):
    out = await _run(monkeypatch, status="離職")
    assert out["active"] is False and out["log_missing"] == [] and out["journals"] == []
    out2 = await _run(monkeypatch, grants_journal=False)
    assert out2["journals"] == [] and out2["log_pending"] == ["2026-09-10"]
