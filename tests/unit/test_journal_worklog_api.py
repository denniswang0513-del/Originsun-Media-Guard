# -*- coding: utf-8 -*-
"""週記 §13–§14（docs/JOURNAL_WORKLOG_PLAN.md）：草稿→送出、自動區、掛案子／求助標記、主管回覆。

釘的規則：PUT 只存草稿且不降級；列表端點只回 submitted；自動區不含 hours；回覆守 timesheets 模組並通知本人；
條目 id 盡量沿用（回覆才跟得住自動存）。
"""
from datetime import date

import pytest

from core.journal_logic import (ENTRY_FLAGS, FLAG_SECTIONS, MAX_ENTRIES_PER_SECTION, clean_rich_entries,
                                flag_counts, group_worklog, shell_status, status_after_put, unanswered_flagged)
from tests.unit._srcscan import code_only, func_body, repo_src


# ── 條目清洗（字串或 dict）────────────────────────────────────────

def test_rich_entries_accept_strings_and_dicts_and_keep_ids():
    cleaned, err = clean_rich_entries([" a ", {"id": "e1", "content": " b ", "project_id": "p1", "flag": "help"},
                                       {"content": "   "}, "", {"content": "c", "project_id": "", "flag": ""}])
    assert err is None
    assert cleaned == [{"id": None, "content": "a", "project_id": None, "flag": None},
                       {"id": "e1", "content": "b", "project_id": "p1", "flag": "help"},
                       {"id": None, "content": "c", "project_id": None, "flag": None}]


def test_flags_only_where_allowed_and_must_be_known():
    ok, err = clean_rich_entries([{"content": "x", "flag": "discuss"}], allow_flag=False)
    assert err is None and ok[0]["flag"] is None            # 順利／學到：標記靜默丟掉
    _, err = clean_rich_entries([{"content": "x", "flag": "urgent"}], allow_flag=True)
    assert err and "標記" in err
    assert set(FLAG_SECTIONS) == {"challenges", "others"} and set(ENTRY_FLAGS) == {"help", "discuss"}


def test_rich_entries_share_the_string_limits():
    _, err = clean_rich_entries(["x"] * (MAX_ENTRIES_PER_SECTION + 1))
    assert err is not None
    assert clean_rich_entries(None) == ([], None)


# ── 狀態機 ───────────────────────────────────────────────────────

def test_put_only_stores_draft_and_never_downgrades():
    assert status_after_put(None, None) == "draft"
    assert status_after_put(None, "draft") == "draft"
    assert status_after_put("draft", "") == "draft"
    assert status_after_put("submitted", "draft") == "submitted"     # 送出後再改不降回草稿
    assert status_after_put("submitted", None) == "submitted"
    for bad in ("submitted", "done", "x"):
        with pytest.raises(ValueError):
            status_after_put("draft", bad)


def test_shell_status_treats_legacy_null_as_submitted():
    class S:
        status = None
    assert shell_status(None) == "none"
    assert shell_status(S()) == "submitted"
    S.status = "draft"
    assert shell_status(S()) == "draft"


# ── 求助判定 ─────────────────────────────────────────────────────

def test_flag_counts_and_unanswered():
    entries = {"wins": [{"id": "w1", "content": "a", "flag": None}],
               "challenges": [{"id": "c1", "content": "b", "flag": "help"}, {"id": "c2", "content": "c", "flag": "help"}],
               "others": [{"id": "o1", "content": "d", "flag": "discuss"}]}
    assert flag_counts(entries) == {"help": 2, "discuss": 1}
    left = unanswered_flagged(entries, {"c1"})
    assert [(x["section"], x["id"]) for x in left] == [("challenges", "c2"), ("others", "o1")]
    assert unanswered_flagged({}, set()) == []


# ── 自動區：按案子分組、不含小時 ───────────────────────────────────

def test_group_worklog_groups_by_project_then_day_without_hours():
    rows = [
        {"date": "2026-09-02", "project_id": "p1", "project_name": "案A", "task_note": "剪初版", "work_type": "剪接",
         "stage_name": "A-copy", "hours": 6},
        {"date": "2026-09-01", "project_id": "p2", "project_name": "案B", "task_note": "勘景", "work_type": "拍攝",
         "stage_name": "", "hours": 3},
        {"date": "2026-09-01", "project_id": "p1", "project_name": "案A", "task_note": "看素材", "work_type": "",
         "stage_name": "", "hours": 2},
        {"date": "2026-09-02", "project_id": "p1", "project_name": "案A", "task_note": "", "work_type": "",
         "stage_name": "", "hours": 8},                                                  # 沒內容：跳過
        {"date": "2026-09-03", "project_id": "", "project_name": "", "task_note": "整理硬碟", "work_type": "行政",
         "stage_name": "庶務", "hours": 1},
    ]
    out = group_worklog(rows)
    assert [p["project_name"] for p in out] == ["案B", "案A", "(未填案名)"]       # 照第一次出現的日期
    a = out[1]
    assert [d["date"] for d in a["days"]] == ["2026-09-01", "2026-09-02"]
    assert a["days"][1]["items"] == [{"note": "剪初版", "work_type": "剪接", "stage_name": "A-copy"}]
    # 不含小時：每個 item 只有這三個鍵，案子／天那層也沒有任何合計
    for p in out:
        assert set(p) == {"project_id", "project_name", "days"}
        for d in p["days"]:
            assert set(d) == {"date", "items"}
            for it in d["items"]:
                assert set(it) == {"note", "work_type", "stage_name"}


def test_group_worklog_accepts_objects_and_dates():
    class R:
        def __init__(self, **kw):
            self.__dict__.update(kw)
    out = group_worklog([R(work_date=date(2026, 9, 1), project_id="p", project_name="X", task_note="n",
                           work_type="", stage_name="", hours=1)])
    assert out == [{"project_id": "p", "project_name": "X",
                    "days": [{"date": "2026-09-01", "items": [{"note": "n", "work_type": "", "stage_name": ""}]}]}]
    assert group_worklog([]) == []


# ── 端點掃描 ──────────────────────────────────────────────────────

def test_lists_only_return_submitted_and_drafts_stay_private():
    src = code_only(repo_src("routers/api_journal.py"))
    for fn in ("async def learning_library(", "async def journal_people(", "async def person_journals(",
               "async def help_queue("):
        assert "_submitted_only()" in func_body(src, fn), fn
    week = func_body(src, "async def week_journals(")
    assert 'shell_status(s) == "submitted"' in week and '"people_status"' in week
    assert '"draft" if u in drafts else "none"' in week


def test_put_is_draft_only_and_submit_is_its_own_endpoint():
    src = code_only(repo_src("routers/api_journal.py"))
    put = func_body(src, "async def put_my_journal(")
    assert "status_after_put(" in put and '"submitted"' not in put
    assert "editable_window_ok(week)" in put
    sub = func_body(src, "async def submit_my_journal(")
    assert '"/mine/submit"' in src
    assert 'shell.status = "submitted"' in sub and "shell.submitted_at = now" in sub
    assert "editable_window_ok(week)" in sub


def test_entries_keep_their_ids_across_full_replace():
    src = code_only(repo_src("routers/api_journal.py"))
    ups = func_body(src, "async def _upsert_section(")
    assert 'by_id.get(it.get("id")' in ups                       # 帶 id → 同一條
    assert '(r.content or "") == it["content"]' in ups           # 沒帶 id → 內容相同也算同一條
    assert "sa_delete(JournalReply).where(JournalReply.entry_id == r.id)" in ups   # 刪條目連回覆
    assert "_upsert_section(session, model, shell.id, cleaned[key])" in func_body(src, "async def put_my_journal(")


def test_reply_is_manager_only_and_notifies_the_author():
    src = code_only(repo_src("routers/api_journal.py"))
    rep = func_body(src, "async def reply_entry(")
    assert 'check_admin_or_module(request, "timesheets")' in rep
    assert "entry.journal_id != shell.id" in rep                 # 條目必須屬於那份週記
    assert "asyncio.to_thread(_notify_reply" in rep
    # 這段要看原始碼（code_only 會把 f-string 裡的 "#journal" 當註解切掉）
    notif = func_body(repo_src("routers/api_journal.py"), "def _notify_reply(")
    assert "send_google_chat(" in notif and "#journal" in notif and "except Exception" in notif
    assert "有主管回覆" in notif and "_MY_PAGE_URL" in notif
    assert 'check_admin_or_module(request, "timesheets")' in func_body(src, "async def help_queue(")
    assert "unanswered_flagged(" in func_body(src, "async def help_queue(")


def test_mine_response_has_worklog_entries_replies_and_legacy_arrays():
    src = code_only(repo_src("routers/api_journal.py"))
    body = func_body(src, "async def _mine_payload(")
    for k in ('"worklog"', '"replies"', '"entries"', '"status"', '"submitted_at"', '"editable"'):
        assert k in body, k
    assert "**_strings(rich)" in body                          # 舊字串陣列保留（personCard 用）
    wl = func_body(src, "async def _worklog_by_user(")
    assert "group_worklog(" in wl and "Timesheet.staff_id.in_(" in wl and "Timesheet.staff_name.in_(" in wl


def test_migration_models_and_schema():
    main = repo_src("main.py")
    for tup in ('("work_journals", "status", "VARCHAR(16)")', '("work_journals", "submitted_at", "TIMESTAMPTZ")',
                '("journal_wins", "project_id", "VARCHAR(32)")', '("journal_others", "flag", "VARCHAR(16)")'):
        assert tup in main, tup
    assert "UPDATE work_journals SET status='submitted'" in main and "WHERE status IS NULL" in main
    from db.models import JournalReply, WorkJournal
    from routers.api_journal import _SECTION_MODELS
    for _, m in _SECTION_MODELS:
        cols = {c.name for c in m.__table__.columns}
        assert {"project_id", "flag"} <= cols, m.__tablename__
    assert {"status", "submitted_at"} <= {c.name for c in WorkJournal.__table__.columns}
    assert {"journal_id", "entry_table", "entry_id", "username", "content"} <= {c.name for c in JournalReply.__table__.columns}
    from core.schemas import JournalPut, JournalReplyPost
    assert "status" in JournalPut.model_fields
    assert JournalPut(wins=["a", {"content": "b", "flag": "help"}]).wins[1]["flag"] == "help"
    assert set(JournalReplyPost.model_fields) == {"journal_id", "entry_table", "entry_id", "content"}
