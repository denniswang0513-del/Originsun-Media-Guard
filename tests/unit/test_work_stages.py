# -*- coding: utf-8 -*-
"""工作階段（docs/JOURNAL_WORKLOG_PLAN.md §12／§14）：每個分類自己的階段清單。

釘的規則：種子只覆蓋 WORK_TYPES 九類、只在表空時寫；階段必須屬於該列分類（422）；
timesheets.stage_name 是鏡射、**只有一個寫入點**（set_stage）；停用不刪；待辦帶入的列標成實際＝待辦 done。
"""
import re

import pytest

from core.hr_logic import (STAGE_SEED, WORK_TYPES, resolve_stage, stage_categories, stage_index,
                           stages_by_category)
from tests.unit._srcscan import code_only, func_body, repo_src


def _nodes():
    return [
        {"id": "c_edit", "parent_id": "", "name": "剪接", "depth": 1, "sort": 2, "active": 1},
        {"id": "c_shoot", "parent_id": "", "name": "拍攝", "depth": 1, "sort": 1, "active": 1},
        {"id": "s_b", "parent_id": "c_edit", "name": "B-copy", "depth": 2, "sort": 1, "active": 1},
        {"id": "s_a", "parent_id": "c_edit", "name": "A-copy", "depth": 2, "sort": 0, "active": 1},
        {"id": "s_old", "parent_id": "c_edit", "name": "舊流程", "depth": 2, "sort": 9, "active": 0},
        {"id": "s_scout", "parent_id": "c_shoot", "name": "勘景", "depth": 2, "sort": 0, "active": 1},
    ]


# ── 種子 ────────────────────────────────────────────────────────────

def test_seed_covers_exactly_the_nine_work_types_in_order():
    assert tuple(STAGE_SEED) == WORK_TYPES
    for cat, stages in STAGE_SEED.items():
        assert isinstance(stages, tuple), cat
        assert all(s.strip() and isinstance(s, str) for s in stages), cat
        assert len(set(stages)) == len(stages), f"{cat} 有重複階段"
    assert STAGE_SEED["剪接"] == ("A-copy", "B-copy", "Fine cut", "Fine cut 修改", "定剪", "輸出")
    assert STAGE_SEED["其他"] == ()


def test_seed_only_writes_when_table_is_empty():
    src = code_only(repo_src("routers/crm/work_stages.py"))
    body = func_body(src, "async def seed_if_empty(")
    assert "func.count()" in body
    assert body.index("return 0") < body.index("session.add(")      # 先數、有東西就走人
    assert "STAGE_SEED" in body and "WORK_TYPES" in body
    main = repo_src("main.py")
    assert "seed_if_empty as _seed_stages" in main


# ── 純函式：整理與正規化 ────────────────────────────────────────────

def test_categories_follow_work_types_order_and_stage_sort():
    cats = stage_categories(_nodes())
    assert [c["name"] for c in cats] == ["拍攝", "剪接"]           # 照 WORK_TYPES，不照 sort
    assert [s["name"] for s in cats[1]["stages"]] == ["A-copy", "B-copy", "舊流程"]
    assert cats[1]["stages"][2]["active"] is False


def test_inactive_stage_hidden_from_dropdown_but_kept_for_editor():
    editor = stage_categories(_nodes(), include_inactive=True)
    dropdown = stage_categories(_nodes(), include_inactive=False)
    assert "舊流程" in [s["name"] for s in editor[1]["stages"]]
    assert "舊流程" not in [s["name"] for s in dropdown[1]["stages"]]
    opts = stages_by_category(_nodes())
    assert opts == {"拍攝": [{"id": "s_scout", "name": "勘景"}],
                    "剪接": [{"id": "s_a", "name": "A-copy"}, {"id": "s_b", "name": "B-copy"}]}


def test_resolve_stage_truth_table():
    idx = stage_index(_nodes())
    assert resolve_stage("", "剪接", idx) is None
    assert resolve_stage(None, "剪接", idx) is None
    assert resolve_stage("s_a", "剪接", idx) == {"id": "s_a", "name": "A-copy", "category": "剪接", "active": True}
    with pytest.raises(ValueError):
        resolve_stage("nope", "剪接", idx)                 # 找不到
    with pytest.raises(ValueError):
        resolve_stage("s_a", "拍攝", idx)                  # 不屬於該列分類
    with pytest.raises(ValueError):
        resolve_stage("s_a", "", idx)                      # 沒分類就不能有階段
    assert resolve_stage("s_old", "剪接", idx)["active"] is False   # 停用的舊列重存不擋


# ── 鏡射只有一個寫入點 ─────────────────────────────────────────────

def test_stage_name_is_written_only_by_set_stage():
    files = ("services/timesheet_self.py", "services/timesheet_manual.py", "routers/api_timesheets.py",
             "routers/api_me.py", "routers/crm/work_stages.py", "routers/api_journal.py")
    writes = []
    for f in files:
        for m in re.finditer(r"\.stage_name\s*=[^=]", code_only(repo_src(f))):
            writes.append((f, m.start()))
    assert len(writes) == 1, writes
    svc = code_only(repo_src("services/timesheet_self.py"))
    assert ".stage_name =" in func_body(svc, "def set_stage(")


def test_rows_validate_stage_before_insert_and_mirror_after():
    svc = code_only(repo_src("services/timesheet_self.py"))
    add = func_body(svc, "async def add_rows(")
    assert add.index("_stage_or_422(") < add.index("insert_manual_rows(")    # 422 在插入之前
    assert "set_stage(obj, st)" in add and "_mark_bulletin_done(" in add
    upd = func_body(svc, "async def apply_update(")
    assert "set_stage(r," in upd and "_stage_or_422(" in upd
    assert "st is None or st[\"category\"] != (r.work_type or \"\")" in upd  # 分類換了、階段不屬於 → 清空
    assert "_mark_bulletin_done(" in func_body(svc, "async def update_row(")
    assert "raise HTTPException(status_code=422" in func_body(svc, "def _stage_or_422(")


def test_bulletin_done_is_scoped_to_me_and_never_blocks_the_row():
    svc = code_only(repo_src("services/timesheet_self.py"))
    body = func_body(svc, "async def _mark_bulletin_done(")
    assert "float(row.hours or 0) <= 0" in body           # 只有實際列
    assert "begin_nested()" in body and "except Exception" in body   # savepoint、失敗不擋工時
    assert "mine_filter(" in body                          # 只動「與我有關」的那筆
    assert 'b.status = "done"' in body


def test_ts_dict_carries_stage_and_bulletin_fields():
    svc = code_only(repo_src("services/timesheet_self.py"))
    body = func_body(svc, "def ts_dict(")
    for k in ('"stage_id"', '"stage_name"', '"bulletin_id"'):
        assert k in body, k
    from core.schemas import TimesheetManualRow
    assert {"stage_id", "bulletin_id"} <= set(TimesheetManualRow.model_fields)


# ── 端點與註冊 ─────────────────────────────────────────────────────

def test_work_stage_router_is_registered_and_gated_by_timesheets_module():
    assert "from . import work_stages" in repo_src("routers/crm/__init__.py")
    src = code_only(repo_src("routers/crm/work_stages.py"))
    assert '_guard = _module_guard("timesheets")' in src      # 守衛工廠只有 routers.crm._shared 一份
    for fn in ("async def list_work_stage_nodes(", "async def create_work_stage_node(",
               "async def update_work_stage_node(", "async def delete_work_stage_node("):
        assert "_guard(request)" in func_body(src, fn), fn
    for path in ('"/work-stages/nodes"', '"/work-stages/nodes/{node_id}"'):
        assert path in src, path


def test_delete_deactivates_when_rows_reference_it_and_stages_need_a_parent():
    src = code_only(repo_src("routers/crm/work_stages.py"))
    dele = func_body(src, "async def delete_work_stage_node(")
    assert "Timesheet.stage_id == node_id" in dele and '"deactivated"' in dele
    assert "node.active = 0" in dele
    assert "session.delete(node)" in dele
    post = func_body(src, "async def create_work_stage_node(")
    assert "parent_id 必填" in post and "int(parent.depth or 1) != 1" in post


def test_options_endpoint_returns_active_stages_per_category():
    src = code_only(repo_src("routers/api_timesheets.py"))
    body = func_body(src, "async def timesheet_options(")
    assert '"/options"' in src
    assert "stages_by_category(nodes)" in body and "_ts_or_bound(request)" in body


def test_migration_and_model_exports():
    main = repo_src("main.py")
    for tup in ('("timesheets", "stage_id", "VARCHAR(32)")', '("timesheets", "stage_name", "VARCHAR(64)")',
                '("timesheets", "bulletin_id", "VARCHAR(32)")'):
        assert tup in main, tup
    from db.models import WorkStageNode
    cols = {c.name for c in WorkStageNode.__table__.columns}
    assert {"id", "parent_id", "name", "depth", "sort", "active", "created_at", "updated_at"} <= cols
    uniques = [tuple(c.name for c in u.columns) for u in WorkStageNode.__table__.constraints
               if u.__class__.__name__ == "UniqueConstraint"]
    assert ("parent_id", "name") in uniques


def test_editor_offers_delete_only_for_unused_stages():
    """owner 2026-09-07「如果項目還沒有人使用可以移除」：GET 帶 used 筆數；編輯器只對 used===0 露出刪除；
    DELETE 仍是「有人用→停用」的閘（前端拿到 deactivated 就改畫成停用）。"""
    src = repo_src("routers/crm/work_stages.py")
    body = code_only(func_body(src, "async def list_work_stage_nodes("))
    assert "Timesheet.stage_id, func.count()" in body and 'st["used"] = used.get(st.get("id"), 0)' in body
    js = repo_src("frontend/js/shared/stage-editor.js")
    assert "${s.used === 0 ? `<button" in js and 'data-se="del"' in js
    assert "r.status === 'deactivated'" in js and "method: 'DELETE'" in js


def test_workspace_page_recolors_the_shared_popups_for_its_white_theme():
    """個人工作台是白底：stage-editor／proj-pop 的深色預設變數在這頁要被蓋掉（owner 2026-09-07「調整版面配色」）。"""
    html = repo_src("frontend/my.html")
    assert "body .stage-editor { --se-bg: #fff;" in html
    assert "body .proj-pop { --pp-bg: #fff;" in html
