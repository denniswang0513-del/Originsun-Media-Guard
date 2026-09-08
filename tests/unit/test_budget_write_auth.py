# -*- coding: utf-8 -*-
"""專案頁的預算表（子表／預算列／範本）跟專案本體同一把 crm_projects（2026-09-08，蘇家弘「編修預算表一直儲存失敗」）。

原本整支 costs.py 的寫入都是 _check_auth（管理員限定）——有 crm_projects 的人在專案頁看得到預算表、
每一格自動存都 403。開的只有**預算**那一半；雜支登記、收據、雜支連結是真的錢流，照舊管理員／財務。
"""
import re

from tests.unit._srcscan import code_only, func_body, repo_src

SRC = repo_src("routers/crm/costs.py")


def _handlers():
    """[(路徑, 函式名, 本體)]。"""
    out = []
    for m in re.finditer(r'@router\.(get|post|put|delete)\("([^"]+)"[^\n]*\)\nasync def (\w+)\(', SRC):
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


def test_money_flow_writes_stay_admin_or_finance():
    """雜支／收據／雜支連結：真的錢流，不跟著鬆。"""
    seen = 0
    for path, fn, body in _handlers():
        # /advance/ 與 /public/ 是 expense.html 的內部路（2026-09-08 稽核第一批：登入即可 → 登入＋crm_projects；owner 拍板雜支歸 crm_projects，第二批會把本尊也改過去）
        if any(k in path for k in MONEY_FLOW) and "/public/" not in path and "/petty/" not in path and "/advance/" not in path:
            if "_check_project_write_auth(request)" in body:
                raise AssertionError(f"{fn} {path} 是錢流端點，不能用 crm_projects 放行")
            seen += 1
    assert seen >= 5


def test_import_carries_both_guards():
    head = SRC.split("\n\n")[0] + SRC.split("router = ")[0] if "router = " in SRC else SRC[:3000]
    assert "_check_project_write_auth" in SRC.split("def ")[0], "costs.py 要從 _shared 匯入 _check_project_write_auth"
