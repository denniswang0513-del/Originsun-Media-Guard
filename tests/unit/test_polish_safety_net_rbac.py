# -*- coding: utf-8 -*-
"""/polish 階段零安全網（2026-09-08 權限重整批）：把三個沒有測試點名的公開函式現在的行為釘住。

record_denial（core.auth）：403 記錄的形狀；payload／request 給 None 也不炸。
plan_for_targets／plan_for_rows（routers/api_timesheets）：兼職排班的兩支讀取端點——守衛、查誰、日期半開區間。
"""
from core.auth import record_denial, recent_denials
from tests.unit._srcscan import code_only, func_body, timesheets_src


class _Req:
    method = "GET"
    def __init__(self, path):
        class _U: pass
        self.url = _U(); self.url.path = path


def test_record_denial_shape_and_null_safety():
    record_denial({"username": "zz_sn", "sub": "zz_sn"}, _Req("/api/v1/x"), ("crm_projects", "unknown_key"))
    d = recent_denials(1)[0]
    assert d["username"] == "zz_sn" and d["method"] == "GET" and d["path"] == "/api/v1/x"
    assert d["missing"] == ["crm_projects", "unknown_key"] and d["labels"] == ["專案管理", "unknown_key"], "沒名字的鍵就印鍵本身"
    assert d["at"].endswith("+00:00")
    n = len(recent_denials(300))
    record_denial(None, None, None)                      # 都是 None 也記一筆（空 username），不能炸
    assert len(recent_denials(300)) == n + 1 and recent_denials(1)[0]["username"] == "" and recent_denials(1)[0]["missing"] == []
    assert isinstance(recent_denials(0), list)


def test_plan_for_read_endpoints_pin():
    src = timesheets_src()
    targets = code_only(func_body(src, "async def plan_for_targets("))
    assert "CrmStaff.status == _PARTTIME" in targets and 'order_by(CrmStaff.name)' in targets
    assert 'check_admin_or_module(request, "timesheets")' in targets or "require_bound_staff" in targets, "工作追蹤整區恆過，其餘要綁定＋鑰匙"
    rows = code_only(func_body(src, "async def plan_for_rows("))
    assert "_day_or_422(from_)" in rows and "_day_or_422(to) + timedelta(days=1)" in rows, "to 是含當天的閉區間，list_rows 吃 [d0, d1)"
    assert "_plan_for_ident(request, session, staff_id)" in rows and 'list_rows(session, ctx["ident"], d0, d1)' in rows
    assert '"staff_name": ctx["target"].name' in rows
