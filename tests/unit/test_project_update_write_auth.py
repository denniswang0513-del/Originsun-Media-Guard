# -*- coding: utf-8 -*-
"""專案本體的「改」跟建／刪同一把 crm_projects（2026-09-08：蘇家弘存「編輯專案」被 403）。

owner 2026-08-15 拍板「專案本體與派工的增刪 —— 模組級」時只改了 POST／DELETE，PUT 留在管理員限定：
有 crm_projects 的人看得到編輯視窗、按儲存就「權限不足」——「權限是空頭支票」那個形狀又出現一次。

例外兩條不能跟著鬆：推進階段（ADVANCE_MODULES 的政策，PUT 整包送 status 只在真的變了才過那道門）、
私帳案金額欄（帳本主人才能寫）。
"""
from tests.unit._srcscan import code_only, func_body, repo_src

SRC = repo_src("routers/crm/projects.py")


def _body(header: str) -> str:
    return code_only(func_body(SRC, header))


def test_create_update_delete_share_the_project_module_guard():
    for header in ("async def create_project(", "async def update_project(", "async def delete_project("):
        body = _body(header)
        assert "_check_project_write_auth(request)" in body, f"{header} 要跟建／刪同一把 crm_projects"
        assert "_check_auth(request)" not in body, f"{header} 不能再是管理員限定"


def test_status_change_through_put_still_needs_the_advance_policy():
    """編輯視窗整包送 status：沒改就放行（不然存個備註也要管理員）；改了才過 ADVANCE_MODULES 那道門。"""
    body = _body("async def update_project(")
    assert '"status" in update_data and (update_data["status"] or "") != (project.status or "")' in body
    assert "_check_status_auth(request)" in body
    # 判定要在撈到 project 之後、寫欄位之前（不然比的是舊值以外的東西）
    assert body.index("_check_status_auth(request)") > body.index('raise HTTPException(status_code=404, detail="找不到此專案")')
    assert body.index("_check_status_auth(request)") < body.index("for k, v in update_data.items():")
    # 專屬端點照舊
    assert "_check_status_auth(request)" in _body("async def update_project_status(")


def test_mine_money_wall_survives_the_relaxation():
    body = _body("async def update_project(")
    assert "viewer_has_mine_scope(request)" in body and "私帳案的金額欄只有帳本主人能修改" in body


def test_other_crm_writes_stay_admin_only():
    """鬆的只有專案本體；案型字彙、CSV 匯入這些照舊管理員限定（owner 2026-08-15：不把其他 CRM 寫入一起放行）。"""
    for header in ("async def add_project_type(", "async def import_projects_csv("):
        if f"{header}" in SRC:
            assert "_check_auth(request)" in _body(header), header
