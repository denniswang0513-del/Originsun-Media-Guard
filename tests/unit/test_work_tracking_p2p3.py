# -*- coding: utf-8 -*-
"""工作追蹤 P2／P3（docs/WORK_TRACKING_UI_PLAN.md）：專案檔案／類似專案／人員檔案／儀表板／
匯出／週一 digest。規則是純函式；端點只做 I/O；主管層只給管理員。"""
import datetime as dt

from core.hr_logic import (STALE_DAYS, digest_text, hours_rollup, is_stale, missing_fillers,
                           project_metrics, similar_projects, type_composition)
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src

D = dt.date


def test_stale_only_for_active_projects_without_recent_hours():
    today = D(2026, 9, 3)
    assert is_stale("製作", D(2026, 8, 20), today) is True
    assert is_stale("製作", D(2026, 8, 30), today) is False
    assert is_stale("製作", None, today) is True
    assert is_stale("結案", D(2026, 1, 1), today) is False
    assert is_stale("", None, today) is False
    assert STALE_DAYS == 7


def test_type_composition_and_project_metrics():
    assert type_composition([("拍攝", 4), ("", 2), (None, 2), ("拍攝", 4)]) == [("拍攝", 8.0, 67), ("未分類", 4.0, 33)]
    m = project_metrics([(D(2026, 9, 1), "王", "拍攝", 8), (D(2026, 9, 3), "李", "剪接", 4), (None, "王", "剪接", 2)])
    assert m["total"] == 14.0 and m["people"] == 2 and m["by_person"] == [("王", 10.0), ("李", 4.0)]
    assert m["first"] == "2026-09-01" and m["last"] == "2026-09-03" and m["span_days"] == 3
    assert m["composition"][0] == ("拍攝", 8.0, 57)
    assert project_metrics([]) == {"total": 0.0, "people": 0, "by_person": [], "first": None, "last": None,
                                   "span_days": 0, "composition": []}


def test_similar_projects_is_a_suggestion_not_a_merge():
    cands = [
        {"name": "典藏_年度影片", "client": "典藏", "total": 100},
        {"name": "典藏_年度影片 2025", "client": "典藏", "total": 90},       # 同客戶＋像＋量級近
        {"name": "別家_年度影片", "client": "別家", "total": 95},            # 像＋量級近
        {"name": "典藏_媒體顧問", "client": "典藏", "total": 5},             # 同客戶但量級差、名不像
        {"name": "無關_排球", "client": "無關", "total": 100},
        {"name": "典藏_年度影片", "client": "典藏", "total": 100},           # 自己
    ]
    got = similar_projects("典藏_年度影片", "典藏", 100, cands)
    names = [n for n, _ in got]
    assert names[0] == "典藏_年度影片 2025" and "別家_年度影片" in names
    assert "典藏_年度影片" not in names and "無關_排球" not in names
    assert all(0 < s <= 1 for _, s in got)
    assert similar_projects("", "", 0, cands) == []


def test_missing_and_digest_text():
    assert missing_fillers({"王", "李", "陳"}, {"李"}) == ["王", "陳"]
    rollup = hours_rollup([("王", D(2026, 8, 24), "A", 8), ("王", D(2026, 8, 25), "A", 6)], 2026, 8)
    text = digest_text("2026-08-24 ～ 2026-08-30", rollup, {"王": 3})
    assert "【上週工時】" in text and "王：14.0 h（2 天）　漏填 3 天" in text and "人事管理 › 工作追蹤" in text
    assert not any(ch in text for ch in "📣🔴✅")            # owner 鐵則：通知無 emoji


def test_endpoints_are_thin_and_gated():
    src = code_only(repo_src("routers/api_timesheets.py"))
    for fn in ("async def compare_projects(", "async def person_file(",
               "async def dashboard(", "async def export_csv("):
        assert 'check_admin_or_module(request, "timesheets")' in func_body(src, fn), fn
    # 2026-09-05（JOURNAL_WORKLOG_PLAN §10）：專案檔案頁開放給綁定人員唯讀 —— 守衛換成
    # 「timesheets 模組或綁定人員」那一支；那支裡面仍是同一句模組守衛先試
    assert "_ts_or_bound(request)" in func_body(src, "async def project_file(")
    assert 'payload_grants(check_logged_in(request), "timesheets")' in func_body(src, "async def _ts_or_bound(")
    assert "project_metrics(metrics_input(rows))" in func_body(src, "async def project_file(")
    assert "similar_projects(" in func_body(src, "async def project_file(")
    assert "project_metrics(metrics_input(" in func_body(src, "async def compare_projects(")
    # 改預算＝寫私帳案 → full；digest 設定＝管理員
    assert '_require_mine_admin(request, level="full")' in func_body(src, "async def set_project_budget(")
    for fn in ("async def get_digest(", "async def put_digest(", "async def send_digest_now("):
        assert "check_admin(request)" in func_body(src, fn), fn
    # 儀表板：主管層只在 admin 才組（負載排名／漏填不給全員比較）
    dash = func_body(src, "async def dashboard(")
    assert 'out["manager"]' in dash and "if is_admin:" in dash
    assert "missing_fillers(active_fillers(" in dash
    # burn 表的專案那一半只有一份（/summary 與 /dashboard 共用）
    assert "burn_rows(session)" in func_body(src, "async def burn_summary(")
    assert "burn_rows(session)" in dash


def test_digest_has_its_own_master_gated_loop_and_is_off_by_default():
    dg = code_only(repo_src("services/timesheet_digest.py"))
    loop = func_body(dg, "async def _scheduler_loop(")
    assert "send_digest()" in loop and "is_master_machine()" in loop
    assert "timesheet_digest.start_scheduler_task()" in repo_src("main.py")
    cfg = repo_src("config.py")
    assert '"digest": {"enabled": False, "cron": "0 9 * * 1"}' in cfg
    assert "digest_text(" in func_body(dg, "async def build_digest(")
    # 發送走 notifier 那一份 webhook 讀法，不自己撈 settings
    assert "send_google_chat" in dg and "google_chat_webhook" not in dg


def test_tab_wires_the_new_views():
    js = repo_src("frontend/tabs/timesheets/timesheets.js")
    code = js_code_only(js)
    # 2026-09-12 P4：人員 view（/person）拿掉了——人×日在 ts-zone 團隊的一週
    for path in ("/api/v1/timesheets/project?name=", "/api/v1/timesheets/compare?names=",
                 "/api/v1/timesheets/dashboard",
                 "/api/v1/timesheets/project_budget", "/api/v1/timesheets/export.csv?",
                 "/api/v1/timesheets/digest"):
        assert path in code, path
    # 2026-09-05：專案檔案的鈕（加入比較／改預算／匯出）畫在 js/shared/ts-projects.js，動作仍由 tab 的 _onAction 接
    shared = repo_src("frontend/js/shared/ts-projects.js")
    for act in ("open-project", "compare-add", "budget", "export-month", "digest-send"):
        assert f"data-ts-action=\"{act}\"" in js + shared, act
        assert f"act === '{act}'" in code, f"tab 沒接 {act}"
    # 停滯／未對映徽章與可點案名
    assert "ts-badge warn" in js and "class=\"ts-link\"" in js
