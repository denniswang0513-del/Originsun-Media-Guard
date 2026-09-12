# -*- coding: utf-8 -*-
"""合併同案（owner 2026-09-07：員工一天分好幾段記同一個案，「合併同案」把同案同分類同階段的列併成一列；可復原）。

規則：core.hr_logic.merge_plan 純函式；I/O 在 services.timesheet_self（merge_day／undo_merge／last_merge），
端點 /timesheets/mine/merge（dry_run 預覽）、/mine/merge/undo、/mine/merge/last；桌機員工頁與手機工作紀錄都有鈕。
"""
from core.hr_logic import merge_plan
from tests.unit._srcscan import code_only, func_body, js_code_only, my_page_src, repo_src, timesheets_src


def _r(i, **kw):
    base = {"id": f"r{i}", "project_id": "p1", "project_name": "聚焦", "work_type": "剪接", "stage_id": "s1", "stage_name": "A-copy",
            "hours": 1.0, "status": "draft", "editable": True, "task_note": "字卡、剪輯", "remark": "", "start_time": "", "end_time": ""}
    base.update(kw)
    return base


def test_merge_plan_groups_same_project_type_stage_and_sums_hours():
    rows = [_r(1, hours=1.98, start_time="09:23"), _r(2, hours=1.42, start_time="15:24"), _r(3, hours=0.1, start_time="19:44"),
            _r(4, project_id="", project_name="行政庶務", work_type="行政", stage_id="s9", stage_name="庶務", hours=1.38, task_note="工作安排"),
            _r(5, project_id="", project_name="行政庶務", work_type="行政", stage_id="s9", stage_name="庶務", hours=0.08, task_note="工作安排"),
            _r(6, project_id="p2", project_name="狄浣家人追思影片", hours=0.42, task_note="剪輯討論")]
    plan = merge_plan(rows)
    assert plan["skipped"] == 1                                           # 狄浣只有一列不併
    by = {g["label"]: g for g in plan["groups"]}
    g = by["聚焦／剪接／A-copy"]
    assert g["kept_id"] == "r1" and g["absorbed_ids"] == ["r2", "r3"] and g["hours"] == 3.5 and g["count"] == 3
    assert g["task_note"] == "字卡、剪輯", "同一句只留一句"
    h = by["行政庶務／行政／庶務"]
    assert h["hours"] == 1.46 and h["task_note"] == "工作安排" and h["kept_id"] == "r4"


def test_merge_plan_joins_different_notes_and_skips_pending_plan_readonly():
    rows = [_r(1, task_note="A"), _r(2, task_note="B", remark="晚上"), _r(3, task_note="A", remark="下午"),
            _r(4, hours=0, status="pending"), _r(5, status="plan", hours=2), _r(6, editable=False, hours=3)]
    plan = merge_plan(rows)
    assert len(plan["groups"]) == 1 and plan["skipped"] == 3
    g = plan["groups"][0]
    assert g["task_note"] == "A、B" and g["remark"] == "晚上、下午" and g["count"] == 3


def test_merge_plan_keeps_earliest_start_and_falls_back_to_order():
    rows = [_r(1, start_time="13:00"), _r(2, start_time="09:00"), _r(3)]
    g = merge_plan(rows)["groups"][0]
    assert g["kept_id"] == "r2" and g["absorbed_ids"] == ["r1", "r3"]


def test_merge_key_uses_project_name_when_there_is_no_id():
    """沒有 project_id 時用案名當鍵（Sheet 進來的列常常只有名字）。"""
    rows = [_r(1, project_id="", project_name="ZZ 案"), _r(2, project_id="", project_name="ZZ 案")]
    g = merge_plan(rows)["groups"][0]
    assert g["label"].startswith("ZZ 案") and g["absorbed_ids"] == ["r2"]


def test_rows_without_any_project_are_never_merged():
    """🔴 2026-09-08 review：沒對到案、也沒打案名的列**不併**。

    它們的鍵會全部塌成 ("", 分類, 階段)，於是「行政庶務 2h」和「會議 3h」這兩件不相干的事
    被併成一列 5h、第二列被刪掉，標籤還只寫「（未填專案）」，人根本看不出併掉了什麼。"""
    rows = [_r(1, project_id="", project_name="", task_note="行政庶務"),
            _r(2, project_id="", project_name="", task_note="會議")]
    plan = merge_plan(rows)
    assert plan["groups"] == [] and plan["skipped"] == 2


def test_service_merges_in_one_transaction_and_snapshots_for_undo():
    svc = code_only(repo_src("services/timesheet_self.py"))
    m = func_body(svc, "async def merge_day(")
    assert "merge_plan(await list_rows(session, ident, day, d1))" in m
    assert "kept.start_time = None" in m and "kept.end_time = None" in m and "await session.delete(a)" in m
    assert "TimesheetMergeLog(" in m and m.count("await session.commit()") == 1
    u = func_body(svc, "async def undo_merge(")
    assert "status_code=409" in u and "session.add(Timesheet(**d))" in u and "log.undone_at = datetime.now(timezone.utc)" in u
    from db.models import TimesheetMergeLog
    assert {"id", "staff_id", "work_date", "groups", "snapshot", "undone_at"} <= {c.name for c in TimesheetMergeLog.__table__.columns}


def test_endpoints_and_both_frontends():
    src = code_only(timesheets_src())
    for path in ('"/mine/merge"', '"/mine/merge/undo"', '"/mine/merge/last"'):
        assert path in src, path
    assert src.index('@router.post("/mine/merge")') < src.index('@router.put("/mine/{row_id}")')   # 不能被當 row_id
    for fn in ("async def my_merge_day(", "async def my_merge_undo(", "async def my_last_merge("):
        assert "_mine_ident(request)" in func_body(src, fn), fn
    html = my_page_src()
    assert 'data-z1="merge"' in html and 'id="z1-unmerge"' in html
    assert "/api/v1/timesheets/mine/merge/last?date=" in html and 'dry_run: true' in html and "/api/v1/timesheets/mine/merge/undo" in html
    mob = js_code_only(repo_src("frontend/m/views/worklog.js"))
    assert "data-merge" in mob and "data-unmerge" in mob and "'/api/v1/timesheets/mine/merge/undo'" in mob
    assert "dry_run: true" in mob and "window.confirm(" in mob
