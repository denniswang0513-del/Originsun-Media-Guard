# -*- coding: utf-8 -*-
"""db/startup_migrations：開機 migration／種子的**順序**（2026-09-13 從 main._on_startup 拆出來時釘的）。

順序不是裝飾：CRM_COLUMNS 的 ADD COLUMN 要在 FINANCE_AND_CRM_COLUMNS 的 UPDATE 之前、
分類樹要種好才能回填 category_map、cost_groups 表建好才能 backfill 主表。
要插一段：加進 `_POST_DB`，然後**在這裡**把它放到正確的位置 —— 這支測試就是要你想清楚放哪。
"""
import inspect

from db import startup_migrations as sm

EXPECTED = [
    "_m01_google_oauth_columns",
    "_m02_me_zone_split_backfill",
    "_m03_me_today_zone_backfill",
    "_m04_bulletin_columns_and_seed",
    "_m05_api_keys_table",
    "_m06_crm_new_columns",
    "_m07_crm_indexes_and_ddl",
    "_m08_proposal_shell_projects",
    "_m09_proposals_root_to_db",
    "_m10_reference_folder_rename",
    "_m11_cost_lines_table",
    "_m12_project_expenses_columns",
    "_m13_project_status_8_stage_backfill",
    "_m14_cost_line_templates_table",
    "_m15_cost_lines_new_columns",
    "_m16_staff_resume_and_portfolio",
    "_m17_website_phase_m",
    "_m18_cost_groups_table_and_backfill",
    "_m19_finance_phase2_tables",
    "_m20_seed_mine_cash_taxonomy",
    "_m21_seed_work_stages",
    "_m22_ledger_by_parent_backfill",
]


def test_post_db_steps_run_in_the_pinned_order():
    assert [f.__name__ for f in sm._POST_DB] == EXPECTED


def test_every_step_is_registered_exactly_once():
    defined = [n for n, f in inspect.getmembers(sm, inspect.iscoroutinefunction) if n.startswith("_m")]
    assert sorted(defined) == sorted(EXPECTED), "寫了 _mNN_* 卻沒掛進 _POST_DB（或反過來）"
    assert len(sm._POST_DB) == len(set(sm._POST_DB))


async def test_run_post_db_awaits_each_step_in_order(monkeypatch):
    seen = []

    def _fake(name):
        async def _f():
            seen.append(name)
        _f.__name__ = name
        return _f

    monkeypatch.setattr(sm, "_POST_DB", [_fake("a"), _fake("b"), _fake("c")])
    await sm.run_post_db()
    assert seen == ["a", "b", "c"]


def test_agent_without_sqlalchemy_can_still_import_this_module():
    """機隊 agent 沒裝 sqlalchemy：模組層只能有 stdlib／core.state／db.migrations（純資料）。"""
    src = inspect.getsource(sm)
    head = src[:src.index("async def run_pre_db")]
    assert "from db.session import get_session_factory" in head and "except ImportError" in head
    assert "sqlalchemy" not in head.replace("沒裝 sqlalchemy", "")
