# -*- coding: utf-8 -*-
"""專案頁的預算表（子表／預算列／範本）跟專案本體同一把 crm_projects（2026-09-08，蘇家弘「編修預算表一直儲存失敗」）。

原本整支 costs.py 的寫入都是 _check_auth（管理員限定）——有 crm_projects 的人在專案頁看得到預算表、
每一格自動存都 403。先開的是**預算**那一半；雜支登記、收據、雜支連結在 2026-09-08 權限稽核第二批
跟著開（owner 拍板「內部雜支寫入歸 crm_projects」，docs/RBAC_PLAN.md §5 第 4 點）。
留管理員的只有刪雜支與收據根目錄（ADMIN_ONLY_ACTIONS）。
"""
import re

from tests.unit._srcscan import costs_src
from tests.unit._srcscan import code_only, func_body

SRC = costs_src()


def _handlers(methods=("get", "post", "put", "patch", "delete")):
    """[(路徑, 函式名, 本體)]，只收 `methods` 裡的動詞。"""
    out = []
    for m in re.finditer(r'@router\.(get|post|put|patch|delete)\("([^"]+)"[^\n]*\)\nasync def (\w+)\(', SRC):
        if m.group(1) in methods:
            out.append((m.group(2), m.group(3), code_only(func_body(SRC, f"async def {m.group(3)}("))))
    return out


BUDGET = ("cost-lines", "cost-line-templates", "cost-groups")
MONEY_FLOW = ("expense", "receipt")


def test_budget_writes_take_the_project_module_key():
    seen = 0
    for path, fn, body in _handlers():
        if any(k in path for k in MONEY_FLOW):      # /cost-groups/{id}/receipts 是收據（錢流），不在預算表這一半
            continue
        if any(k in path for k in BUDGET) and "/public/" not in path and ("_check_auth(request)" in body or "_check_project_write_auth(request)" in body):
            assert "_check_project_write_auth(request)" in body, f"{fn} {path} 預算表要用 crm_projects，不是管理員限定"
            assert "_check_auth(request)" not in body, f"{fn} {path}"
            seen += 1
    assert seen >= 12, f"預算表寫入端點只掃到 {seen} 支，掃描可能壞了"


# 雜支那一半仍留管理員的（owner 2026-09-08 ADMIN_ONLY_ACTIONS）：刪雜支、收據根目錄。
# 讀取端（GET /projects/{id}/expenses＝money_dep；兩支收據清單 GET＝管理員）第二批沒動，這裡只掃寫入動詞。
EXPENSE_ADMIN_ONLY = {"delete_project_expense", "set_receipts_root"}


def test_expense_writes_take_the_project_module_key_except_delete_and_root():
    """雜支／收據／雜支連結（內部路）：owner 拍板歸 crm_projects；只有刪除與收據根目錄留管理員。"""
    seen = 0
    for path, fn, body in _handlers(methods=("post", "put", "patch", "delete")):
        if "/public/" in path or "/advance/" in path:
            continue
        if path.startswith("/expense-links"):      # Depends(...) 形狀，守衛在路由列上（下一支測試釘）
            continue
        if any(k in path for k in MONEY_FLOW):
            if fn in EXPENSE_ADMIN_ONLY:
                assert ("_check_auth(request)" in body or "check_admin(request)" in body), \
                    f"{fn} {path} 是 ADMIN_ONLY_ACTIONS，要留管理員"
                assert "_check_project_write_auth(request)" not in body, f"{fn} {path}"
            else:
                assert "_check_project_write_auth(request)" in body, \
                    f"{fn} {path} 雜支寫入要用 crm_projects（owner 2026-09-08）"
                assert "_check_auth(request)" not in body, f"{fn} {path}"
            seen += 1
    assert seen >= 7, f"雜支寫入端點只掃到 {seen} 支，掃描可能壞了"


def test_receipts_root_get_opens_to_approvers_set_stays_admin():
    """零用金收據根目錄：GET 給審核者（財務分頁零用金子視圖第一支就打它），設定仍限管理員（第二批）。"""
    assert "check_admin_or_module(request, 'finance_approve')" in code_only(func_body(SRC, "async def get_receipts_root("))
    assert "check_admin(request)" in code_only(func_body(SRC, "async def set_receipts_root("))


def test_expense_link_dependencies_take_the_project_module_key():
    """兩支 Depends(...) 形狀的雜支連結端點（掃描器抓不到 handler 本體，路由列上釘）。"""
    assert '@router.post("/expense-links", dependencies=[Depends(_check_project_write_auth)])' in SRC
    assert '@router.post("/expense-links/{target_id}/enabled", dependencies=[Depends(_check_project_write_auth)])' in SRC


def test_import_carries_both_guards():
    assert "_check_project_write_auth" in SRC.split("def ")[0], "costs.py 要從 _shared 匯入 _check_project_write_auth"
