# -*- coding: utf-8 -*-
"""預期毛利 × 人力日成本 → 工時預算（owner 2026-09-03：「我的員工成本一天 4000，專案的使用率是用這個
基礎算出來的，這一塊在我的私帳設定」）。規則只有 core.finance_logic 一份；burn 表、專案檔案頁、
財務摘要、設定頁都吃它。"""
from core.finance_logic import DEFAULT_MARGIN_MODEL, canonical_type, margin_for_type, suggested_budget_hours
from tests.unit._srcscan import costs_src
from tests.unit._srcscan import code_only, func_body, repo_src, timesheets_src


def test_default_model_is_owners_table():
    rows = {r["type"]: r["margin_pct"] for r in DEFAULT_MARGIN_MODEL["rows"]}
    assert DEFAULT_MARGIN_MODEL["daily_cost"] == 4000 and DEFAULT_MARGIN_MODEL["hours_per_day"] == 8
    assert rows == {"紀實影片": 40, "商業廣告": 40, "影視服務": 50, "活動紀錄": 50, "媒體顧問": 60, "平面攝影": 20,
                    "劇情片": 40, "客製紀實": 20, "動態設計": 20, "其他": 30, "錄混音": 30}
    assert margin_for_type(DEFAULT_MARGIN_MODEL, "媒體顧問") == 60
    assert margin_for_type(DEFAULT_MARGIN_MODEL, "沒有這種") is None      # 不猜
    # 舊版本的案型（CRM 那套：廣告／活動紀實…）對到你的版本後照右邊算
    m = {**DEFAULT_MARGIN_MODEL, "aliases": {"廣告": "商業廣告", "活動紀實": "活動紀錄"}}
    assert canonical_type(m, "廣告") == "商業廣告" and canonical_type(m, "紀實影片") == "紀實影片"
    assert margin_for_type(m, "廣告") == 40 and margin_for_type(m, "活動紀實") == 50
    assert margin_for_type(m, "MV") is None                                # 沒對應就沒有


def test_budget_hours_formula():
    # 合約 105,000 含稅 5% → 未稅 100,000；紀實 40% → 成本預算 60,000 ÷ 4000 × 8 ＝ 120 h
    assert suggested_budget_hours(105000, 5, 40, 4000, 8) == 120
    assert suggested_budget_hours(105000, 5, 40, 4000, 10) == 150
    # 取到 0.5 小時
    assert suggested_budget_hours(3500, 5, 20, 4000, 8) == 5.5
    # 沒合約／沒毛利／沒日成本 → None，不填 0 假裝有
    assert suggested_budget_hours(0, 5, 40, 4000) is None
    assert suggested_budget_hours(105000, 5, None, 4000) is None
    assert suggested_budget_hours(105000, 5, 40, 0) is None


def test_every_surface_uses_the_one_rule():
    lk = code_only(repo_src("services/timesheet_lookup.py"))
    assert "suggested_hours(model, contract, tax_rate, ptype)" in func_body(lk, "async def burn_rows(")
    assert "suggested_budget_hours(contract, tax_rate, margin_for_type(model, ptype)" in func_body(lk, "def suggested_hours(")
    ts = code_only(timesheets_src())
    assert "suggested_hours(load_margin_model(\"mine\")" in func_body(ts, "def _suggested_for(")   # 走 timesheet_lookup.suggested_hours 那一份
    assert '"suggested_hours": _suggested_for(proj)' in func_body(ts, "async def project_file(")
    # 套用建議：私帳 full；預設只填沒設的（Sheet 灌的預算是 owner 的決定）
    ap = func_body(ts, "async def apply_suggested_budgets(")
    assert '_require_mine_admin(request, level="full")' in ap and 'overwrite or not i["budget_hours"]' in ap
    costs = code_only(costs_src())
    assert '"suggested_budget_hours": _suggested_hours' in func_body(costs, "async def project_financial_summary(")
    fin = code_only(repo_src("routers/api_finance.py"))
    # 統一：full 守衛、對應目標必須在表裡、兩本帳的專案一起改、對應記回模型
    un = func_body(fin, "async def unify_project_types(")
    assert '_guard(request, entity, level="full")' in un and "update(CrmProject).where(CrmProject.project_type == old)" in un
    assert 'model["aliases"]' in un and "save_margin_model(ent, model)" in un
    assert "_guard(request, entity)" in func_body(fin, "async def get_margin_model(")
    assert '_guard(request, entity, level="full")' in func_body(fin, "async def put_margin_model(")
    js = repo_src("frontend/tabs/finance/subviews/settings.js")
    assert "finFetch('/margin-model'" in js and "finset-margin-card" in js
    # 2026-09-12 起「套用建議預算」住在共用的 ts-zone 專案查詢（CRM 分頁的管理視角），不在 tab 本身
    zone = repo_src("frontend/js/shared/ts-zone/find.js")
    assert 'data-z1="suggest"' in zone and "suggested_hours" in zone and "z.api.suggestBudgets()" in zone
