"""私帳「堡壘」（docs/FORTRESS_PLAN.md）：純規則用 demo 的範例數字釘；端點契約與掛載掃原始碼。"""
from core.fortress_logic import (DEFAULT_WAR, STATE_BAD, STATE_OK, STATE_WARN, assign_layers, build, layer_sums,
                                 merge_settings, normalize_settings, runway_months, stress_tests, tone)
from tests.unit._srcscan import code_only, func_body, repo_src

# 桌機 demo v3 的範例：層 1 = 玉山 18 萬＋實體 8 萬；層 2 = 國泰 22 萬；層 3 = 中信 55 萬；層 4 = 永豐 30 萬＋美元現金 15 萬；
# 層 5 = 台股 120 萬＋美股 90 萬。信用卡 4.5 萬走預留（不再當負餘額，demo 那樣會算兩次）。
ACCOUNTS = [
    {"id": "esun", "name": "玉山 薪轉戶", "kind": "bank", "balance": 180000, "currency": "TWD"},
    {"id": "safe", "name": "實體現金", "kind": "cash", "balance": 80000, "currency": "TWD"},
    {"id": "cathay", "name": "國泰 活存", "kind": "bank", "balance": 220000, "currency": "TWD"},
    {"id": "ctbc", "name": "中信 定存", "kind": "bank", "balance": 550000, "currency": "TWD"},
    {"id": "sino", "name": "永豐 交割戶", "kind": "bank", "balance": 300000, "currency": "TWD"},
    {"id": "usdcash", "name": "美國券商 美元現金", "kind": "bank", "balance": 150000, "currency": "TWD"},
    {"id": "tw", "name": "台股", "kind": "holding", "balance": 1200000, "currency": "TWD"},
    {"id": "us", "name": "美股", "kind": "holding", "balance": 900000, "currency": "USD"},
]
SETTINGS = {"account_layers": {"cathay": 2, "ctbc": 3, "sino": 4, "usdcash": 4},
            "account_flags": {"safe": {"physical": True}, "usdcash": {"offshore": True, "usd": True}, "us": {"offshore": True}},
            "monthly_need_override": 80000}
EARMARKS = [
    {"id": "loan:1", "label": "房貸下一期", "amount": 32000, "due_date": "2026-10-05", "source": "loan", "paid": False},
    {"id": "card:outstanding", "label": "信用卡目前欠款", "amount": 45000, "due_date": "", "source": "card", "paid": False},
    {"id": "e1", "label": "年繳壽險保費", "amount": 60000, "due_date": "2027-03-15", "source": "manual", "paid": False},
    {"id": "e2", "label": "綜所稅", "amount": 120000, "due_date": "2027-05-31", "source": "manual", "paid": False},
    {"id": "e3", "label": "已付的舊項", "amount": 999999, "due_date": "2026-01-01", "source": "manual", "paid": True},
]


def test_settings_normalize_and_merge():
    s = normalize_settings({"account_layers": {"a": "3", "b": 9, "c": "x"}, "targets": {"3": 4, "2": 1, "5": 2},
                            "war": {"fx": "1.5", "months": "6", "bogus": 1}, "monthly_need_override": "0", "holdings_layer": 7})
    assert s["account_layers"] == {"a": 3}, "超出 1..5 或不是數字的丟掉"
    assert s["targets"] == {1: 1, 3: 4, 4: 3}, "只有 1／3／4 層有倍數；2＝預留合計、5 沒上限"
    assert s["war"]["fx"] == 1.5 and s["war"]["months"] == 6 and s["war"]["tw_drop"] == DEFAULT_WAR["tw_drop"]
    assert s["monthly_need_override"] is None and s["holdings_layer"] == 5
    m = merge_settings(s, {"account_layers": {"z": 2}, "monthly_need_override": 75000})
    assert m["account_layers"] == {"z": 2} and m["monthly_need_override"] == 75000 and m["targets"][3] == 4, "各段整份取代、其餘保留"


def test_layers_and_runway_match_the_demo():
    accts = assign_layers(ACCOUNTS, normalize_settings(SETTINGS))
    have = layer_sums(accts)
    assert have == {1: 260000, 2: 220000, 3: 550000, 4: 450000, 5: 2100000}
    assert [a["flags"]["usd"] for a in accts if a["id"] in ("usdcash", "us")] == [True, True], "USD 幣別自動算美元資產"
    assert runway_months(have[1] + have[2] + have[3], 257000, 80000) == 9.7
    assert runway_months(100, 0, 0) is None and tone(None) == "na"
    assert tone(6) == "g" and tone(5.9) == "a" and tone(2.9) == "r"


def test_build_returns_the_whole_page():
    d = build(ACCOUNTS, EARMARKS, 82300, SETTINGS, "2026-09-16")
    assert d["monthly_need"] == {"auto": 82300, "override": 80000, "used": 80000}
    assert d["earmark_total"] == 257000, "已付的不算"
    assert d["cash"] == {"l1_3": 1030000, "l1_4": 1480000}
    assert d["runway"]["months"] == 9.7 and d["runway"]["with_l4"] == 15.3 and d["runway"]["tone"] == "g"
    by = {x["no"]: x for x in d["layers"]}
    assert [x["no"] for x in d["layers"]] == [5, 4, 3, 2, 1], "畫的順序：5 在上"
    assert by[2]["target"] == 257000 and by[2]["gap"] == 37000 and by[2]["pct"] == 86
    assert by[3]["target"] == 480000 and by[3]["gap"] == -70000 and by[3]["pct"] == 100
    assert by[5]["target"] is None and by[5]["pct"] == 100 and by[5]["target_rule"] == "沒有上限"
    assert [a["id"] for a in by[1]["accounts"]] == ["esun", "safe"]
    assert [e["id"] for e in d["earmarks"]][-1] == "e3", "已付的排最後"
    assert [t["key"] for t in d["tests"]] == ["income", "market", "shock", "combined", "war"]
    # 沒有必要支出：算不出來，不是無限
    z = build(ACCOUNTS, EARMARKS, 0, {}, "")
    assert z["runway"]["months"] is None and all(t["state"] == "na" for t in z["tests"])


def test_stress_tests_states_and_war_assumptions():
    accts = assign_layers(ACCOUNTS, normalize_settings(SETTINGS))
    have = layer_sums(accts)
    tests = {t["key"]: t for t in stress_tests(have, accts, 80000, 257000, dict(DEFAULT_WAR))}
    assert tests["income"]["state"] == STATE_OK and tests["income"]["lines"][2] == ["撐完還剩", 1030000 - 480000 - 257000, "twd"]
    assert tests["market"]["state"] == STATE_OK and tests["market"]["lines"][1] == ["跌完剩", 1260000, "twd"]
    assert tests["shock"]["lines"][0] == ["先從第 4 層機會資金扣", 400000, "twd"] and tests["shock"]["lines"][1][1] == 0
    assert tests["combined"]["state"] == STATE_OK
    war = tests["war"]
    assert war["state"] == STATE_OK and "12 個月" in war["assume"] and "30%" in war["assume"]
    # 頭一個月：實體 8 萬＋美元現金 15 萬×1.3；第 1–4 層：148 萬＝(103+30)萬 + 15×1.3 萬
    assert war["lines"][0][1] == 80000 + 195000 and war["lines"][2][1] == 1330000 + 195000
    assert war["lines"][4][1] == 480000 and war["lines"][5][1] == 936000
    # 把實體現金拿掉、美元現金也不標海外 → 頭一個月拿不到錢 → 會被迫
    s2 = normalize_settings({**SETTINGS, "account_flags": {"usdcash": {"usd": True}}})
    a2 = assign_layers([a for a in ACCOUNTS if a["id"] != "safe"], s2)
    w2 = {t["key"]: t for t in stress_tests(layer_sums(a2), a2, 80000, 257000, dict(DEFAULT_WAR))}["war"]
    assert w2["state"] == STATE_BAD and "頭一個月" in w2["verdict"]
    # 收入斷 6 個月但第 1–3 層不夠、機會資金補得上 → 很緊
    small = assign_layers([{"id": "x", "name": "x", "kind": "bank", "balance": 400000}, {"id": "y", "name": "y", "kind": "bank", "balance": 400000}],
                          normalize_settings({"account_layers": {"y": 4}}))
    t3 = {t["key"]: t for t in stress_tests(layer_sums(small), small, 80000, 0, dict(DEFAULT_WAR))}
    assert t3["income"]["state"] == STATE_WARN


# ── 端點契約與掛載（掃原始碼）──────────────────────────────────────
_SRC = repo_src("routers/api_fortress.py")


def test_every_endpoint_requires_the_ledger_in_full():
    for fn in ("async def get_fortress(", "async def put_settings(", "async def add_earmark(", "async def edit_earmark(", "async def delete_earmark("):
        assert 'require_entity(request, entity, level="full")' in code_only(func_body(_SRC, fn)), fn


def test_router_reuses_existing_money_rules_and_mounts_everywhere():
    code = code_only(_SRC)
    assert "_holding_value" in code and "_card_numbers" in code and "CARD_KIND" in code, "餘額／持股／卡欠款沿用既有算法，不另寫一套"
    assert "NEED_CATEGORY_PREFIXES" in code, "必要支出＝家用＋個人固定支出（core 定義）"
    assert "'api_fortress'" in repo_src("main.py") and '"api_fortress"' in repo_src("main_office.py"), "主控與 NAS 都掛"
    assert '"finance": ("fortress",)' in repo_src("core/office_settings.py"), "分層設定要送到 NAS"
    for bad in ("core.scheduler", "import notifier", "from notifier", "socket_mgr"):
        assert bad not in code, bad
    assert "class FinanceFortressEarmark(Base)" in repo_src("db/models/_finance.py") and "FinanceFortressEarmark" in repo_src("db/models/__init__.py")
