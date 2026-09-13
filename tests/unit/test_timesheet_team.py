# -*- coding: utf-8 -*-
"""團隊工時互看＋匯總（docs/TIMESHEET_SELF_ENTRY_PLAN.md §5）。

釘的規則：「幫大家算好」是純函式（每人合計／天數／每週／各案、參考工時＝工作日×8）；
團隊端點的閘門＝看得到自己就看得到大家（_team_ident＝me_finance 或 timesheets 任一＋綁定），回的是 Sheet 案名與時數、
沒有金額也沒有 CRM 專案 id；/hours.html 只打 /me/team/*。
"""
import datetime as dt

from core.hr_logic import hours_rollup, month_workdays, week_key
from tests.unit._srcscan import code_only, func_body, js_code_only, my_page_src, repo_src


def test_workdays_and_week_key():
    assert month_workdays(2026, 9) == 22          # 2026-09：週一起 30 天，22 個平日
    assert month_workdays(2026, 2) == 20
    assert week_key(dt.date(2026, 9, 3)) == "2026-W36"
    assert week_key(dt.date(2026, 1, 1)) == "2026-W01"
    assert week_key(dt.date(2027, 1, 1)) == "2026-W53"   # 跨年歸 ISO 年


def test_rollup_computes_everyone_and_every_project():
    rows = [
        ("王", dt.date(2026, 9, 1), "A案", 4), ("王", dt.date(2026, 9, 1), "B案", 4),
        ("王", dt.date(2026, 9, 8), "A案", 8),
        ("李", dt.date(2026, 9, 2), "A案", 2), ("李", None, "行政庶務", 1),
    ]
    out = hours_rollup(rows, 2026, 9)
    assert out["workdays"] == 22 and out["reference_hours"] == 176 and out["total"] == 19.0
    wang, li = out["people"]                        # 合計多的在前
    assert wang == {"name": "王", "total": 16.0, "days_filled": 2, "avg_per_day": 8.0,
                    "weeks": {"2026-W36": 8.0, "2026-W37": 8.0},
                    "projects": [("A案", 12.0), ("B案", 4.0)]}
    assert li["days_filled"] == 1 and li["total"] == 3.0 and li["weeks"] == {"2026-W36": 2.0}
    assert out["projects"][0] == ("A案", 14.0)
    assert hours_rollup([], 2026, 9)["people"] == []


def test_team_endpoints_are_gated_like_my_own_rows_and_carry_no_money():
    src = code_only(repo_src("routers/api_me.py"))
    for fn in ("async def team_hours(", "async def team_projects(", "async def team_project_detail("):
        body = func_body(src, fn)
        assert "await _team_ident(request)" in body, fn      # timesheets／me_finance 任一＋綁定（2026-09-08）
        for bad in ("amount", "contract", "cost", "daily_rate", "hourly_rate"):
            assert bad not in body, (fn, bad)
    assert "hours_rollup(data, m0.year, m0.month)" in func_body(src, "async def team_hours("), "端點自己算了"
    # 專案匯總回 Sheet 案名、預算小時；不回 project_id 給前端當連結
    body = func_body(src, "async def team_projects(")
    assert '"project_name": pname' in body and '"budget_hours": b' in body
    assert '"project_id"' not in body
    # 明細的列走 _team_row＝ts_dict 去掉 project_id（不然 ts_dict 會把私帳案 id 帶出去）
    assert "_team_row(r)" in func_body(src, "async def team_project_detail(")
    assert '.pop("project_id")' in func_body(src, "def _team_row(")


def test_hours_page_only_talks_to_team_endpoints():
    html = repo_src("frontend/hours.html")
    assert "/api/v1/me/team/hours?month=" in html
    assert "/api/v1/me/team/projects?months=" in html
    assert "/api/v1/me/team/project?name=" in html
    assert "/api/v1/timesheets/" not in html, "員工頁不該打管理端 timesheets 端點"
    assert 'href="/hours.html"' in my_page_src(), "我的工時沒有入口到團隊工時"
    # 內部 App 的工作追蹤 tab：說明列留連結到 /hours.html（2026-09-12 起四視圖住 ts-zone，舊人員分頁沒了）；
    # tab 不自己再畫一份 /me/team 的表
    tab_src = repo_src("frontend/tabs/timesheets/timesheets.js")
    assert 'href="/hours.html"' in tab_src
    assert "/api/v1/me/team/" not in js_code_only(tab_src), "tab 自己又畫了一份團隊表"


def test_returned_dicts_never_repeat_a_key():
    """dict 字面值同鍵寫兩次＝後者蓋前者（第二輪把 by_month 換成 hr_logic 函式後舊行沒刪 →
    .items() 炸 500，掃原始碼的測試全綠）。AST 掃工時整組的檔，任何常數鍵重複就紅。"""
    import ast
    for path in ("routers/api_me.py", "routers/api_timesheets.py", "services/timesheet_self.py",
                 "services/timesheet_manual.py", "services/timesheet_lookup.py", "services/timesheet_digest.py",
                 "services/timesheet_puller.py", "services/timesheet_ingest.py", "services/timesheet_conflicts.py", "core/hr_logic.py"):
        for node in ast.walk(ast.parse(repo_src(path))):
            if isinstance(node, ast.Dict):
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
                assert len(keys) == len(set(keys)), (path, keys)


def test_team_burn_merges_the_same_project_across_sheet_name_strings():
    """🔴 同一個案的列會帶不同的 project_name（Sheet 匯入「客戶_案名」vs app 手填「案名」；生產 2026-09-13
    實查 7 案、最大一案 852 h 被切成兩列，各自對同一份預算算消耗率）—— 對到案的按 project_id 合、名字用
    CRM 顯示名；明細用名字反查案 id 把所有列收齊。"""
    src = code_only(repo_src("routers/api_me.py"))
    body = func_body(src, "async def team_projects(")
    assert 'key = pid or ("name:" + (pname or ""))' in body
    assert "names.get(pid) or pname" in body
    hours = func_body(src, "async def team_hours(")
    assert "names.get(pid) or p" in hours, "團隊月表各案小計也要合"
    det = func_body(src, "async def team_project_detail(")
    assert "project_ids_named(session, name)" in det and "Timesheet.project_id.in_(pids)" in det
    helper = func_body(src, "async def _team_project_names(")
    assert "project_names_map" in helper, "顯示名鏈只有那一份（自訂 → 母帳案名 → 私帳原名）"
    look = code_only(repo_src("services/timesheet_lookup.py"))
    assert "CrmProject.display_name == name" in func_body(look, "async def project_ids_named(")
    # 顯示名撞名（兩個客戶各一個「剪輯協助 202601」）→ 退回 crm_projects.name，團隊表分得開、明細只找到自己
    assert "Counter(names.values())" in helper and "await client_prefixed_names(session," in helper
    assert 'Client.short_name + "_" + CrmProject.name == name' in func_body(look, "async def project_ids_named(")
