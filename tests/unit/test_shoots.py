# -*- coding: utf-8 -*-
"""行事曆（拍攝場次＋器材預約＋Google 日曆；docs/SHOOT_CALENDAR_PLAN.md）。

三層：
  (a) core/shoot_logic 純規則：區間重疊、專案拍攝日怎麼算、預約列狀態、Google 事件長相
  (b) services/google_calendar：假的 token／HTTP，insert／patch／404 轉 insert／delete／失敗不 raise
  (c) 掃原始碼釘不變式：router 有註冊、migration 有 shoot_id、機密抹除含服務帳號、
      每個寫入端點都重算專案拍攝日、器材狀態翻轉兩邊共用同一份 helper、手機專案詳情帶 shoots
"""
import json
import re
from datetime import date

import pytest

from core import shoot_logic as L
from tests.unit._srcscan import code_only, func_body, migration_sql, repo_src

API = "routers/api_shoots.py"


# ── (a) 純規則 ──────────────────────────────────────────────

def test_overlaps_inclusive_and_open_end():
    d = date
    assert L.overlaps(d(2026, 9, 3), None, d(2026, 9, 3), None)          # 同一天
    assert L.overlaps(d(2026, 9, 1), d(2026, 9, 3), d(2026, 9, 3), None)  # 尾巴碰到
    assert not L.overlaps(d(2026, 9, 1), d(2026, 9, 2), d(2026, 9, 3), d(2026, 9, 5))
    assert L.overlaps(d(2026, 9, 2), d(2026, 9, 10), d(2026, 9, 4), d(2026, 9, 5))  # 包含
    assert L.span(d(2026, 9, 5), d(2026, 9, 1)) == (d(2026, 9, 5), d(2026, 9, 5))  # 結束早於開始＝當天


def test_derive_project_shoot_date():
    today = date(2026, 9, 3)
    rows = [(date(2026, 8, 1), L.DONE), (date(2026, 9, 10), L.SCHEDULED), (date(2026, 9, 20), L.SCHEDULED)]
    assert L.derive_project_shoot_date(rows, today) == date(2026, 9, 10)     # 最近一場未來
    assert L.derive_project_shoot_date([(date(2026, 8, 1), L.DONE), (date(2026, 8, 20), L.DONE)], today) == date(2026, 8, 20)
    assert L.derive_project_shoot_date([(date(2026, 9, 10), L.CANCELLED)], today) is None
    assert L.derive_project_shoot_date([], today) is None
    assert L.derive_project_shoot_date([(date(2026, 9, 3), L.SCHEDULED)], today) == today  # 含今天


def test_checkout_state():
    assert L.checkout_state(None, None) == L.RESERVED
    assert L.checkout_state("x", None) == L.PICKED_UP
    assert L.checkout_state("x", "y") == L.RETURNED
    assert L.EQUIPMENT_STATES == ("預約", "已領", "已還")
    assert L.SHOOT_STATUSES == ("排定", "完成", "取消")


def _shoot(**kw):
    base = {"id": "s1", "project_name": "案A", "client_short_name": "客戶", "date": "2026-09-10",
            "crew": [{"staff_id": "1", "name": "小明"}], "equipment": [{"name": "A7S3"}], "notes": "帶腳架"}
    base.update(kw)
    return base


def test_event_body_all_day_uses_exclusive_end():
    b = L.event_body(_shoot())
    assert b["summary"] == "拍攝｜案A（客戶）"
    assert b["start"] == {"date": "2026-09-10"} and b["end"] == {"date": "2026-09-11"}
    assert "人員：小明" in b["description"] and "器材：A7S3" in b["description"] and "備註：帶腳架" in b["description"]
    assert b["extendedProperties"]["private"]["originsun_shoot_id"] == "s1"
    b2 = L.event_body(_shoot(end_date="2026-09-12"))
    assert b2["end"] == {"date": "2026-09-13"}


def test_event_body_timed_uses_taipei():
    b = L.event_body(_shoot(start_time="09:00", end_time="18:00", title="外景日", location_name="兩廳院"))
    assert b["summary"] == "拍攝｜外景日（客戶）"
    assert b["start"] == {"dateTime": "2026-09-10T09:00:00", "timeZone": "Asia/Taipei"}
    assert b["end"] == {"dateTime": "2026-09-10T18:00:00", "timeZone": "Asia/Taipei"}
    assert b["location"] == "兩廳院"
    b2 = L.event_body(_shoot(start_time="09:00"))
    assert b2["end"]["dateTime"] == "2026-09-10T09:00:00"     # 沒結束時間＝跟開始一樣


# ── (b) Google 日曆服務（假 HTTP）────────────────────────────

@pytest.fixture
def fake_gc(monkeypatch):
    from services import google_calendar as gc
    calls = []
    monkeypatch.setattr(gc, "get_access_token", lambda sa, scope: "tok")

    def fake_urlopen_json(req, timeout, api_name):
        calls.append((req.get_method(), req.full_url, json.loads(req.data.decode()) if req.data else None))
        method = req.get_method()
        if method == "PATCH" and "/events/gone" in req.full_url:
            raise RuntimeError("Google 日曆 API 404: Not Found")
        if method == "GET":
            return {"summary": "源日拍攝", "items": []}
        return {"id": "evt_new" if method == "POST" else req.full_url.rsplit("/", 1)[-1]}
    monkeypatch.setattr(gc, "urlopen_json", fake_urlopen_json)
    return gc, calls


SA = {"client_email": "bot@x.iam.gserviceaccount.com", "private_key": "k"}


def test_upsert_inserts_then_patches(fake_gc):
    gc, calls = fake_gc
    eid, err = gc.upsert_event(SA, "cal@group.calendar.google.com", None, {"summary": "x"})
    assert (eid, err) == ("evt_new", "") and calls[-1][0] == "POST"
    eid2, err2 = gc.upsert_event(SA, "cal@group.calendar.google.com", "evt_new", {"summary": "y"})
    assert (eid2, err2) == ("evt_new", "") and calls[-1][0] == "PATCH"
    assert "cal%40group.calendar.google.com" in calls[-1][1]      # 日曆 ID 要 URL 編碼


def test_upsert_recreates_when_event_was_deleted_in_calendar(fake_gc):
    gc, calls = fake_gc
    eid, err = gc.upsert_event(SA, "c", "gone", {"summary": "x"})
    assert (eid, err) == ("evt_new", "")
    assert [c[0] for c in calls[-2:]] == ["PATCH", "POST"]


def test_upsert_failure_returns_error_not_raise(monkeypatch):
    from services import google_calendar as gc
    monkeypatch.setattr(gc, "get_access_token", lambda sa, scope: (_ for _ in ()).throw(RuntimeError("Google OAuth API 401: bad key")))
    eid, err = gc.upsert_event(SA, "c", None, {})
    assert eid is None and "401" in err


def test_test_connection_explains_404(fake_gc, monkeypatch):
    gc, calls = fake_gc
    ok, msg, summary = gc.test_connection(SA, "c")
    assert ok and summary == "源日拍攝"
    assert gc.test_connection(SA, "")[0] is False
    monkeypatch.setattr(gc, "urlopen_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Google 日曆 API 404: notFound")))
    ok, msg, _ = gc.test_connection(SA, "c")
    assert not ok and "分享" in msg


def test_calendar_scope_is_events_only():
    assert repo_src("services/google_calendar.py").count("auth/calendar.events") == 1
    assert "asyncio.to_thread" in repo_src(API), "阻塞的 urllib 要在 thread 裡跑"


# ── (c) 掃原始碼 ───────────────────────────────────────────

def test_router_registered_and_migrated():
    assert "'api_shoots'" in repo_src("main.py")
    assert '("equipment_checkouts", "shoot_id"' in migration_sql()
    assert "class CrmShoot(Base)" in repo_src("db/models/_workos.py")
    assert "CrmShoot" in repo_src("db/models/__init__.py")


def test_service_account_json_is_redacted_from_settings_load():
    src = repo_src("routers/api_system.py")
    m = re.search(r"_SECRET_SUBKEYS = \{(.*?)\}", src, re.S)
    assert m and '"google_calendar": ("service_account_json",)' in m.group(1)
    assert '"google_calendar": {' in repo_src("config.py")


def test_every_write_endpoint_recomputes_project_shoot_date_and_syncs():
    src = repo_src(API)
    for header in ("async def create_shoot(", "async def update_shoot(", "async def set_shoot_status("):
        body = code_only(func_body(src, header))
        assert "_recompute_project_shoot_date(" in body, header
        assert "_sync_calendar(" in body, header
        assert "_check_write(request)" in body, header


def test_equipment_state_flip_is_shared_with_equipment_tab():
    eq = repo_src("routers/api_equipment.py")
    assert "def mark_checked_out(" in eq and "def mark_returned(" in eq
    for header in ("async def checkout_equipment(", "async def return_equipment("):
        body = code_only(func_body(eq, header))
        assert "mark_checked_out(" in body or "mark_returned(" in body, header
        assert 'equip.status = "' not in body, f"{header} 不准自己翻器材狀態"
    sh = code_only(func_body(repo_src(API), "async def _pickup_or_return("))
    assert "mark_checked_out(" in sh and "mark_returned(" in sh


def test_mobile_project_detail_carries_shoots():
    body = code_only(func_body(repo_src("routers/api_crm_mobile.py"), "async def mobile_project_detail("))
    assert "shoots_for_project(" in body and 'out["shoots"]' in body


def test_guards():
    src = repo_src(API)
    assert "dependencies=[Depends(check_logged_in)]" in src
    assert 'check_admin_or_module(request, WRITE_MODULE)' in src and 'WRITE_MODULE = "crm_projects"' in src
    for header in ("async def calendar_config(", "async def calendar_test("):
        assert "check_admin(request)" in code_only(func_body(src, header)), header
    vocab = code_only(func_body(src, "async def shoot_options("))
    assert "list(SHOOT_STATUSES)" in vocab and "list(EQUIPMENT_STATES)" in vocab


def test_project_shoot_date_uses_the_repo_date_convention():
    """專案拍攝日（與器材應還日）一律走 routers.crm._shared._parse_shoot_date（該日 UTC 午夜）——
    寫台北午夜會存成前一天 16:00Z，手機取 ISO 前 10 碼就少一天（tests/unit/test_shoot_date_convention）。"""
    src = repo_src(API)
    body = code_only(func_body(src, "async def _recompute_project_shoot_date("))
    assert "_parse_shoot_date(" in body and "datetime.combine(" not in body
    assert "datetime.combine(" not in code_only(src), "api_shoots 不准自己造午夜 datetime"
