# -*- coding: utf-8 -*-
"""工作追蹤 v2 的特徵測試（polish 安全網，2026-09-12）：把幾支新改的端點／服務**現在的行為**釘住，不判對錯。

用假 session（`execute()` 依呼叫順序回預先排好的結果）跑真的函式；守衛用 monkeypatch 換成「管理員」。
"""
from datetime import datetime
from types import SimpleNamespace as NS

import pytest

import routers.api_timesheets as api
import services.timesheet_lookup as lookup


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalars(self):
        return self


class _Session:
    """`execute()` 第 n 次呼叫回 `results[n]`；記下每次的 statement 給斷言看。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def execute(self, stmt):
        self.calls.append(stmt)
        return _Result(self.results.pop(0))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _factory(session):
    return lambda: session


def _ts(**kw):
    base = dict(id="r1", work_date=datetime(2026, 9, 10, 0, 0), staff_name="王小美", staff_id="s1", project_name="案 A",
                project_id="p1", task_note="做了", remark="", start_time="", end_time="", hours=2.0, planned_hours=None,
                planned_by="", work_type="後製", stage_id="", stage_name="", bulletin_id="", source="manual",
                status="draft", edited_at=None, note="")
    base.update(kw)
    return NS(**base)


def _admin(monkeypatch):
    monkeypatch.setattr(api, "check_admin_or_module", lambda request, *keys: {"access_level": 3, "modules": []})
    monkeypatch.setattr(api, "payload_grants", lambda payload, *keys: True)


# ── /timesheets/rows：date／from＋to_day／staff_id ──

@pytest.mark.asyncio
async def test_rows_with_date_returns_day_label_and_marks_every_row_editable_for_admin(monkeypatch):
    _admin(monkeypatch)
    sess = _Session([[_ts(), _ts(id="r2", staff_name="陳阿宏", staff_id="s2")]])
    monkeypatch.setattr(api, "db_factory_or_503", lambda: _factory(sess))
    out = await api.ledger_rows(request=None, date="2026-09-10", from_="", to_day="", staff_id="s1")
    assert out["date"] == "2026-09-10" and out["to_day"] == "2026-09-10" and "month" not in out
    assert out["editable"] is True and [r["editable"] for r in out["items"]] == [True, True]
    assert [r["staff_name"] for r in out["items"]] == ["王小美", "陳阿宏"]
    assert "timesheets.staff_id = " in str(sess.calls[0]), "staff_id 要進 WHERE"


@pytest.mark.asyncio
async def test_rows_day_range_rejects_reversed_or_too_long(monkeypatch):
    _admin(monkeypatch)
    monkeypatch.setattr(api, "db_factory_or_503", lambda: _factory(_Session([[]])))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        await api.ledger_rows(request=None, date="", from_="2026-09-10", to_day="2026-09-01")
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        await api.ledger_rows(request=None, date="", from_="2026-01-01", to_day="2026-06-30")
    assert "62" in e.value.detail


@pytest.mark.asyncio
async def test_rows_without_date_keeps_the_month_shape(monkeypatch):
    _admin(monkeypatch)
    monkeypatch.setattr(api, "db_factory_or_503", lambda: _factory(_Session([[]])))
    out = await api.ledger_rows(request=None, month="2026-09", date="", from_="", to_day="")   # 直接呼叫要把 Query 預設值換成空字串
    assert out["month"] == "2026-09" and out["to"] == "" and "date" not in out


# ── /timesheets/board：absent ──

@pytest.mark.asyncio
async def test_board_absent_lists_active_staff_without_a_real_row_and_skips_weekends(monkeypatch):
    _admin(monkeypatch)
    # 週四 9/10：小美有實際列、阿宏只有計畫卡、冠宇沒填；在職名單三人＋一個兼職不在名單
    rows = [_ts(), _ts(id="r2", staff_name="陳阿宏", staff_id="s2", status="plan", hours=0)]
    sess = _Session([rows, [("王小美",), ("陳阿宏",), ("林冠宇",)]])
    monkeypatch.setattr(api, "db_factory_or_503", lambda: _factory(sess))
    out = await api.day_board(request=None, date="2026-09-10", days=1)
    day = out["items"][0]
    assert day["absent"] == ["林冠宇", "陳阿宏"], "只有計畫卡不算填了；有實際列的不點名"
    # 週六：不點名
    sess = _Session([[], [("王小美",)]])
    monkeypatch.setattr(api, "db_factory_or_503", lambda: _factory(sess))
    out = await api.day_board(request=None, date="2026-09-12", days=1)
    assert out["items"][0]["absent"] == []


# ── /timesheets/people ──

@pytest.mark.asyncio
async def test_people_keeps_active_and_parttime_sorted_by_rank_then_name(monkeypatch):
    _admin(monkeypatch)
    sess = _Session([[("s3", "王小美", "兼職"), ("s1", "陳阿宏", "在職"), ("s4", "離職者", "離職"), ("s2", "林冠宇", ""), ("s5", "", "在職"), ("s6", "合夥人", "合夥")]])
    monkeypatch.setattr(api, "db_factory_or_503", lambda: _factory(sess))
    out = await api.timesheet_people(request=None)
    # 狀態空白（林冠宇）跟在職同一層（core.hr_logic.staff_rank 2026-09-12 起跟 ACTIVE_STATUSES 一致）；兼職最後
    assert [p["name"] for p in out["people"]] == ["合夥人", "林冠宇", "陳阿宏", "王小美"]
    assert out["people"][-1]["status"] == "兼職" and all(p["id"] for p in out["people"])


# ── burn_rows 的錢三欄 ──

@pytest.mark.asyncio
async def test_burn_rows_money_fields_follow_the_margin_model(monkeypatch):
    monkeypatch.setattr(lookup, "load_margin_model", lambda entity="mine": {
        "daily_cost": 4000, "hours_per_day": 8, "rows": [{"type": "形象片", "margin_pct": 40}], "aliases": {}})
    matched = [("p1", 80.0, 10, datetime(2026, 9, 10)), ("p2", 8.0, 1, datetime(2026, 9, 1))]
    projs = [("p1", "案 A", "製作", 120, 105000, 5, "形象片", "晨光"),      # 合約含稅 105,000、稅 5%
             ("p2", "案 B", "製作", None, 0, None, "沒這型", "")]        # 沒合約、案型不在表上
    sess = _Session([matched, projs])
    items = await lookup.burn_rows(sess)
    a = next(i for i in items if i["project_id"] == "p1")
    assert a["contract_net"] == 100000 and a["margin_pct"] == 40 and a["staff_cost"] == 40000      # 80h ÷ 8 × 4000
    assert a["suggested_hours"] == 120                                                                # 100000 × 0.6 ÷ 4000 × 8
    b = next(i for i in items if i["project_id"] == "p2")
    assert b["contract_net"] is None and b["margin_pct"] is None and b["suggested_hours"] is None
    assert b["staff_cost"] == 4000, "沒合約也算得出人力成本（8h ÷ 8 × 4000）"
