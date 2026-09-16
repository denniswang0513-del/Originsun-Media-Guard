# -*- coding: utf-8 -*-
"""私帳月報（docs/MONTHLY_REPORT.md）：owner 2026-09-17「在我更新帳戶後自動提供一份月報」「給我財務建議與財務分析」。

釘的是規矩：規則在 core（純函式）、建議是門檻不是文案、集中度不把指數型基金當一檔股票、登記後自動產生但產不出來不能讓登記失敗、
上月＝上一份月報、桌機 nav 私帳限定、手機 #report 隱藏路由、按鈕純文字沒有 emoji。
"""
import re

from core.monthly_report import (CONCENTRATION_WARN, build_report, concentration, health_block, is_broad_fund)
from tests.unit._srcscan import func_body, js_code_only, models_src, repo_src

_EMOJI = re.compile("[\U0001F300-\U0001FAFF]|[✓✔✗]")


def _fortress(**over):
    base = {
        "runway": {"months": 20.2, "with_l4": 20.2, "tone": "g"},
        "monthly_need": {"auto": 101_267, "override": None, "used": 101_267, "sample_months": 4},
        "earmark_total": 2428,
        "layers": [{"no": 5, "name": "複利資本", "have": 47_924_166, "target": None, "gap": None, "pct": 100},
                   {"no": 4, "name": "機會資金", "have": 0, "target": 303_801, "gap": 303_801, "pct": 0},
                   {"no": 3, "name": "緊急預備", "have": 0, "target": 607_602, "gap": 607_602, "pct": 0},
                   {"no": 2, "name": "預留現金", "have": 0, "target": 2428, "gap": 2428, "pct": 0},
                   {"no": 1, "name": "營運現金", "have": 2_043_813, "target": 101_267, "gap": -1_942_546, "pct": 100}],
        "tests": [{"key": k, "title": k, "state": s, "verdict": ""} for k, s in
                  (("income", "ok"), ("market", "ok"), ("shock", "ok"), ("combined", "ok"), ("war", "bad"), ("care", "ok"))],
        "ladder": {"rung": 4, "name": "旅遊自由", "net_worth": 49_965_551, "to_next": 550_034_449,
                   "liabilities": {"total": 2428, "card": 2428, "loan": 0},
                   "rungs": [{"no": i, "name": n} for i, n in ((1, "a"), (2, "b"), (3, "c"), (4, "旅遊自由"), (5, "住宅自由"))]},
        "fire": {"allowed": 145_740, "spend": 103_267, "ratio33": 1.21, "ratio25": 1.61, "current_rate": 0.0248,
                 "state": "ok", "pretax": True},
        "projection": {"rate": 0.06, "inflation": 0.02, "rows": [{"year": 10, "nominal": 85_824_882, "real": 70_406_296}]},
        "accounts": [
            {"name": "Vanguard FTSE All-World", "kind": "holding", "balance": 19_990_202, "currency": "USD", "flags": {}},
            {"name": "台積電", "kind": "holding", "balance": 14_400_000, "currency": "TWD", "flags": {}},
            {"name": "元大台灣50", "kind": "holding", "balance": 7_450_297, "currency": "TWD", "flags": {}},
            {"name": "富邦-收入戶", "kind": "bank", "balance": 1_520_435, "currency": "TWD", "flags": {}},
        ],
    }
    base.update(over)
    return base


REGISTER = {"accounts": [{"id": "a", "name": "富邦-收入戶", "acct_kind": "bank", "balance": 1_482_235,
                          "anchor_date": "2026-09-17", "unfilled": -38_200},
                         {"id": "b", "name": "台新（個人）", "acct_kind": "bank", "balance": 246_075, "anchor_date": None, "unfilled": None}],
            "brokers": [{"broker": "富邦證券", "total": 21_900_297, "plug": 50_000, "count": 2},
                        {"broker": "盈透證券", "total": 20_010_505, "plug": 0, "count": 2}],
            "card_outstanding": 2428}
BUCKETS = {"銀行現金": 2_043_813, "應收帳款": 3_870_752, "固定資產淨值": 367_971, "證券現值": 47_924_166}
SNAPS = [{"date": "2026-06-23", "total": 54_063_660}, {"date": "2026-08-24", "total": 53_641_816}]


def test_first_report_compares_assets_to_the_last_snapshot_only():
    r = build_report("2026-09", "2026-09-17", _fortress(), REGISTER, BUCKETS, {"deposit": 0, "expense": 0}, SNAPS)
    assert r["first"] is True and r["totals"]["assets"] == 54_206_702 and r["totals"]["net_worth"] == 54_204_274
    assert r["prev"] == {"label": "快照 2026-08-24", "totals": {"assets": 53_641_816}}
    assert r["delta"]["assets"] == 564_886 and r["delta"]["cash"] is None, "第一份：快照只有總數，分項不比"
    assert r["flow"]["has_entries"] is False and r["flow"]["securities_change"] is None
    assert r["trend"][-1] == {"date": "2026-09-17", "total": 54_206_702, "now": True}


def test_second_report_uses_previous_report_for_every_line():
    first = build_report("2026-09", "2026-09-17", _fortress(), REGISTER, BUCKETS, {"deposit": 0, "expense": 0}, SNAPS)
    reg2 = {**REGISTER, "accounts": [{**REGISTER["accounts"][0], "balance": 1_500_000, "anchor_date": "2026-10-05", "unfilled": 0},
                                     REGISTER["accounts"][1]]}
    b2 = {**BUCKETS, "銀行現金": 2_100_000, "證券現值": 48_500_000}
    r = build_report("2026-10", "2026-10-05", _fortress(), reg2, b2,
                     {"deposit": 300_000, "expense": 120_000, "household_expense": 60_000, "bank_net": 17_987},
                     SNAPS, prev=first)
    assert r["first"] is False and r["prev"]["label"] == "2026-09"
    assert r["delta"]["cash"] == 56_187 and r["delta"]["securities"] == 575_834
    assert r["flow"]["net"] == 180_000 and r["flow"]["savings_rate"] == 0.6 and r["flow"]["securities_change"] == 575_834
    assert r["flow"]["register_gap"] == 56_187 - 17_987 and r["flow"]["through"] == "2026-10-05"
    acc = next(a for a in r["accounts"] if a["name"] == "富邦-收入戶")
    assert acc["prev"] == 1_482_235 and acc["delta"] == 17_765 and acc["registered_this_month"] is True
    assert next(a for a in r["accounts"] if a["name"] == "台新（個人）")["registered_this_month"] is False


def test_concentration_ignores_broad_index_funds():
    """VWRA 佔證券 42% 不是「一檔股票決定四成資產」—— 集中度只盯單一公司的股票（台積電 30%）。"""
    assert is_broad_fund("Vanguard FTSE All-World") and is_broad_fund("元大台灣50") and not is_broad_fund("台積電")
    c = concentration(_fortress())
    assert c["top_name"] == "台積電" and abs(c["top_pct"] - 0.344) < 0.001
    assert abs(c["funds_pct"] - 0.656) < 0.001 and c["biggest_name"] == "Vanguard FTSE All-World"
    r = build_report("2026-09", "2026-09-17", _fortress(), REGISTER, BUCKETS, {}, SNAPS)
    conc_adv = next(a for a in r["advice"] if a["key"] == "concentration")
    assert "台積電" in conc_adv["title"] and conc_adv["level"] == "warn"
    only_funds = _fortress(accounts=[a for a in _fortress()["accounts"] if a["name"] != "台積電"])
    h = next(x for x in build_report("2026-09", "2026-09-17", only_funds, REGISTER, BUCKETS, {}, SNAPS)["health"] if x["key"] == "concentration")
    assert h["state"] == "ok" and "分散的基金" in h["text"]


def test_advice_is_rules_with_thresholds_not_fixed_copy():
    r = build_report("2026-09", "2026-09-17", _fortress(), REGISTER, BUCKETS, {"deposit": 0, "expense": 0}, SNAPS)
    keys = [a["key"] for a in r["advice"]]
    assert keys[0] == "war_flags", "戰爭題紅＋外幣沒標海外 → 排第一（bad）"
    assert "layers" in keys and "records" in keys and "fire_tax" in keys and "need_sample" in keys and "strengths" in keys
    assert [a["no"] for a in r["advice"]] == list(range(1, len(r["advice"]) + 1))
    levels = [a["level"] for a in r["advice"]]
    assert levels == sorted(levels, key=lambda x: {"bad": 0, "warn": 1, "info": 2, "ok": 3}[x]), "照急迫度排"
    layers = next(a for a in r["advice"] if a["key"] == "layers")
    assert "194.3 萬" in layers["title"] and "91.4 萬" in layers["how"], "第 1 層多 194.3 萬、要分 91.4 萬過去：數字進文案"
    # 門檻：戰爭題轉綠、外幣都標了海外 → 那條消失；第 2–4 層填滿 → 那條消失
    ok = _fortress(tests=[{"key": k, "title": k, "state": "ok", "verdict": ""} for k in ("income", "market", "shock", "combined", "war", "care")],
                   layers=[{"no": n, "name": str(n), "have": 1_000_000, "target": 100, "gap": 0, "pct": 100} for n in (5, 4, 3, 2, 1)])
    r2 = build_report("2026-09", "2026-09-17", ok, REGISTER, BUCKETS, {"deposit": 0, "expense": 0}, SNAPS)
    k2 = [a["key"] for a in r2["advice"]]
    assert "war_flags" not in k2 and "war" not in k2 and "layers" not in k2
    assert CONCENTRATION_WARN == 0.25


def test_health_and_todo_reflect_records():
    r = build_report("2026-09", "2026-09-17", _fortress(), REGISTER, BUCKETS, {"deposit": 0, "expense": 0}, SNAPS)
    h = {x["key"]: x for x in r["health"]}
    assert h["liquidity"]["state"] == "ok" and h["resilience"]["state"] == "bad" and h["records"]["state"] == "warn"
    assert any("補明細" in t and "富邦-收入戶" in t for t in r["todo"])
    assert any("台新" in t and "還沒登記" in t for t in r["todo"])
    assert any("富邦證券 5 萬" in t for t in r["todo"])
    fresh = health_block({"runway": 3, "tests": [{"key": "war", "state": "ok"}]}, {"top_pct": None, "total": 0}, [], [], {"has_entries": True})
    assert fresh[0]["state"] == "bad" and fresh[3]["state"] == "ok"


# ── 接線 ──
def test_table_router_and_trigger_are_wired():
    m = models_src("class FinanceMonthlyReport(Base):")
    assert '__tablename__ = "finance_monthly_reports"' in m and "payload = Column(JSONB" in m
    assert 'UniqueConstraint("entity", "month"' in m
    assert "'api_monthly_report'" in repo_src("main.py") and '"api_monthly_report"' in repo_src("main_office.py")
    router = repo_src("routers/api_monthly_report.py")
    assert "save_settings" not in router, "只寫 DB（NAS 的 settings.json 是唯讀副本）"
    assert '@router.get("/monthly-reports")' in router and '@router.get("/monthly-reports/{month}")' in router
    assert '@router.post("/monthly-reports/generate")' in router
    quiet = func_body(router, "async def generate_quietly(")
    assert "except Exception" in quiet and "log.warning" in quiet, "月報產不出來只記 log"
    reg = func_body(repo_src("routers/api_balance_register.py"), "async def put_balance_register(")
    assert "generate_quietly(ent, _username(request))" in reg and 'out["report_month"]' in reg, "登記儲存後自動產生"
    core = repo_src("core/monthly_report.py")
    assert "import" not in func_body(core, "def build_report(").replace("from typing", ""), "純函式：不做 I/O"


def test_desktop_and_mobile_pages():
    html = repo_src("frontend/tabs/finance/finance.html")
    mm = re.search(r'<button class="([^"]*)" data-subview="report">', html)
    assert mm and {"fin-nav-mine-ok", "fin-nav-mine-only"} <= set(mm.group(1).split())
    desk = js_code_only(repo_src("frontend/tabs/finance/subviews/report.js"))
    assert "finFetchMine('/monthly-reports')" in desk and "`/monthly-reports/${" in desk and "'/monthly-reports/generate'" in desk
    assert "export default async function render(" in desk
    regd = js_code_only(repo_src("frontend/tabs/finance/subviews/register.js"))
    assert "d.report_month" in regd and "_reportBanner(" in regd, "登記存完給一條路到月報"
    ledger = repo_src("frontend/m/ledger.js")
    assert "report: '月報'" in ledger and "report: 'overview'" in ledger and "report: reportView" in ledger
    ov = js_code_only(repo_src("frontend/m/views/ledger-overview.js"))
    assert 'data-go="report"' in ov
    view = repo_src("frontend/m/views/ledger-report.js")
    code = js_code_only(view)
    assert "'/api/v1/finance/monthly-reports'" in code and "?entity=mine" in code
    assert not _EMOJI.search(view), "按鈕純文字，沒有 emoji"
    regm = repo_src("frontend/m/views/ledger-register.js")
    assert "_data.report_month" in regm and 'data-go="report"' in regm
    assert ".mr-big" in repo_src("frontend/m/m.css")


def test_desktop_chart_guards_against_zero_or_negative_max():
    """BUG-2（polish 2026-09-17）：全新帳本前兩次快照都是 0 → max=0 → y 座標全 NaN，SVG 整塊壞掉。"""
    from tests.unit._srcscan import js_func_body
    chart = js_func_body(js_code_only(repo_src("frontend/tabs/finance/subviews/report.js")), "function _chart(trend) {")
    assert "if (!(max > 0)) return" in chart


def test_desktop_register_keeps_report_banner_when_card_is_saved_too():
    """BUG-3（polish 2026-09-17）：帳戶＋信用卡一起存，卡那段把 d 洗成 null 再 GET，GET 沒有 report_month → 入口不見。"""
    from tests.unit._srcscan import js_func_body
    body = js_func_body(js_code_only(repo_src("frontend/tabs/finance/subviews/register.js")), "_rg.saveAll = async (btn) => {")
    assert "const reportMonth = d && d.report_month;" in body and "if (reportMonth) _reportBanner(reportMonth);" in body
    assert body.index("const reportMonth") < body.index("if (card !== null)"), "要在卡那段之前留下來"


def test_month_picker_ignores_stale_responses_on_both_pages():
    """BUG-4（polish 2026-09-17）：快速切月份，慢的那次回應最後到 → 下拉顯示 B、數字是 A。兩邊都要守。"""
    from tests.unit._srcscan import js_func_body
    desk = js_func_body(js_code_only(repo_src("frontend/tabs/finance/subviews/report.js")), "_fr.pick = async (month) => {")
    assert "if (_month !== month) return;" in desk
    mob = js_func_body(js_code_only(repo_src("frontend/m/views/ledger-report.js")), "async function pick(host, month) {")
    assert "if (_month !== month) return;" in mob
    # 順手：桌機那一份抓失敗要說「載入失敗」，不是「還沒有月報」（那句是叫他去登記）
    desk_all = js_code_only(repo_src("frontend/tabs/finance/subviews/report.js"))
    assert "的月報載入失敗：" in desk_all


def test_month_income_and_expense_exclude_cross_account_flows():
    """BUG-7：收支明細不是損益表 —— 轉帳、信用卡還款、買賣股票要從本月收入／支出排掉；bank_net 另算（含匯費、請款）。"""
    from tests.unit._srcscan import func_body
    body = func_body(repo_src("routers/api_monthly_report.py"), "async def generate_report(")
    assert 'cat.like("轉匯與定存%")' in body and 'cat.like("信用卡%")' in body and 'cat.like("%投資%")' in body
    assert ".filter(~cross)" in body and "CrmCashEntry.bank_fee" in body and '"bank_net": int(bank_net or 0)' in body
    desk = js_code_only(repo_src("frontend/tabs/finance/subviews/report.js"))
    assert "register_gap" in desk and "unexplained" not in desk and "證券增減" in desk
    mob = js_code_only(repo_src("frontend/m/views/ledger-report.js"))
    assert "register_gap" in mob and "證券增減" in mob
