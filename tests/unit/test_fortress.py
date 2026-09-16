"""私帳「堡壘」（docs/FORTRESS_PLAN.md）：純規則用 demo 的範例數字釘；端點契約與掛載掃原始碼。"""
from core.fortress_logic import (DEFAULT_TARGET_MONTHS, DEFAULT_WAR, STATE_BAD, STATE_OK, STATE_WARN, assign_layers, build, layer_sums,
                                 merge_settings, normalize_settings, runway_months, stress_tests, tone)
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src

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
    assert d["monthly_need"] == {"auto": 82300, "override": 80000, "used": 80000, "sample_months": 0}
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
    assert [t["key"] for t in d["tests"]] == ["income", "market", "shock", "combined", "war", "care"]
    assert d["projection"]["rows"] and d["projection"]["base"] == 2100000, "資產預期成長的起點＝第 5 層"
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
    """守衛集中在 _guard（見 test_endpoints_are_pinned_to_the_private_ledger）；每支端點都要走它。"""
    for fn in ("async def get_fortress(", "async def put_settings(", "async def add_earmark(", "async def edit_earmark(", "async def delete_earmark("):
        assert "_guard(request, entity)" in code_only(func_body(_SRC, fn)), fn


def test_router_reuses_existing_money_rules_and_mounts_everywhere():
    code = code_only(_SRC)
    assert "_holding_value" in code and "_card_numbers" in code and "CARD_KIND" in code, "餘額／持股／卡欠款沿用既有算法，不另寫一套"
    assert "monthly_need_from_rows" in code, "生活支出的口徑與分母都在 core（純規則，可測）"
    assert "'api_fortress'" in repo_src("main.py") and '"api_fortress"' in repo_src("main_office.py"), "主控與 NAS 都掛"
    assert '"finance": ("fortress",)' in repo_src("core/office_settings.py"), "分層設定要送到 NAS"
    for bad in ("core.scheduler", "import notifier", "from notifier", "socket_mgr"):
        assert bad not in code, bad
    assert "class FinanceFortressEarmark(Base)" in repo_src("db/models/_finance.py") and "FinanceFortressEarmark" in repo_src("db/models/__init__.py")


# ── 特徵測試（/polish 階段零）：把這次動到、原本沒被直接釘住的行為釘下來 ──────
def test_layer_targets_and_wan_text():
    from core.fortress_logic import DEFAULT_TARGET_MONTHS, _wan, layer_targets
    t = layer_targets(80000, 257000, DEFAULT_TARGET_MONTHS)
    assert t == {1: 80000, 2: 257000, 3: 480000, 4: 240000, 5: None}, "2＝預留合計、5 沒上限，其餘＝必要支出×倍數"
    assert layer_targets(0, 0, DEFAULT_TARGET_MONTHS) == {1: 0, 2: 0, 3: 0, 4: 0, 5: None}
    # 結論句的金額寫法：一位小數、整數不留 .0
    assert (_wan(37000), _wan(1230000), _wan(0), _wan(-5000)) == ("3.7 萬", "123 萬", "0 萬", "-0.5 萬")


def test_router_date_helpers():
    """到期日字串 ↔ DB 欄位。壞字串要 422（不是 500），空字串是「沒有到期日」不是錯。"""
    from datetime import datetime, timezone

    from fastapi import HTTPException

    from routers.api_fortress import _fmt_day, _parse_day, _today_tw
    assert _parse_day("2027-01-20") == datetime(2027, 1, 20, tzinfo=timezone.utc)
    assert _parse_day("2027-01-20T13:00:00") == datetime(2027, 1, 20, tzinfo=timezone.utc), "只看前 10 碼"
    assert _parse_day("") is None and _parse_day(None) is None
    for bad in ("nope", "2027-13", "2027/01/20"):
        try:
            _parse_day(bad)
        except HTTPException as e:
            assert e.status_code == 422, bad
        else:
            raise AssertionError(f"{bad} 應該 422")
    assert _fmt_day(None) == "" and _fmt_day(datetime(2026, 9, 16, tzinfo=timezone.utc)) == "2026-09-16"
    assert _today_tw().isoformat() >= "2026-01-01", "以台北為準（NAS 容器跑 UTC）"


# ── /polish 階段一：私密性與守衛 ────────────────────────────────────────
def test_fortress_settings_never_reach_the_anonymous_settings_endpoint():
    """🔴 `/api/settings/load` 是**匿名**端點（routers/api_system.py，master 走 Cloudflare 對外）。
    堡壘的設定裡有 owner 每月必要支出、實際銀行帳戶 id 與分層 —— 私帳靠 finance_mine 指名制擋住的東西，
    不能從那條路整包流出去。設定視窗不顯示這段，堡壘頁自己的 GET 也帶著它，全抹掉沒有副作用。"""
    from routers.api_system import _redact_settings
    s = {"finance": {"fortress": {"mine": {"monthly_need_override": 80000,
                                           "account_layers": {"acct-abc": 3}}},
                     "baseline_month": "2026-01"}}
    for admin in (False, True):
        fin = _redact_settings(s, admin=admin).get("finance") or {}
        assert "fortress" not in fin, f"admin={admin} 也不該回（堡壘頁不靠這條路拿設定）"
        assert fin.get("baseline_month") == "2026-01", "同一把 key 底下其他子鍵照舊"


def test_endpoints_are_pinned_to_the_private_ledger():
    """entity 是 query 參數、`require_entity` 空字串會落到 parent —— 沒鎖的話，
    有帳務鑰匙但沒有 finance_mine 的管理員可以拿到一個「母公司版堡壘」，還會把 finance.fortress.parent 寫進設定。
    這功能整個是私帳的（docs/FORTRESS_PLAN.md §0.3），所以一律鎖 mine。"""
    src = repo_src("routers/api_fortress.py")
    guard = code_only(func_body(src, "def _guard(request: Request, entity: str = \"\") -> str:"))
    assert "MINE" in guard and "status_code=422" in guard, "entity 不是 mine 就 422，不要靜默落到 parent"
    assert 'require_entity(request, MINE, level="full")' in guard
    for fn in ("async def get_fortress(", "async def put_settings(", "async def add_earmark(",
               "async def edit_earmark(", "async def delete_earmark("):
        assert "_guard(request, entity)" in code_only(func_body(src, fn)), fn


def test_settings_write_is_not_mounted_on_the_nas():
    """NAS 那台的 settings.json 是**唯讀副本**（publish 會整份覆蓋回去）。
    在那邊存分層會靜默消失，所以那支路徑不掛上去；預留清單（寫 DB）照掛。"""
    office = repo_src("main_office.py")
    assert '"/api/v1/finance/fortress/settings"' in office


# ── /polish 階段一：必要支出的口徑 ──────────────────────────────────────
def test_need_category_rule():
    """哪些收支列算進「每月生活支出」。真實私帳（2026-09 實查）分類樹只搬了一半：
    大部分列還掛在 `個人_旅遊`／`個人_生活`／`家用` 這種舊的兩層名字上，
    原本的 `家用%` ＋ `個人_固定支出%` 只撈到家用那 6.2 萬，把個人生活費 33 萬整個漏掉 ——
    可撐月數因此被高估好幾倍（分母太小），而且是往危險的方向錯。"""
    from core.fortress_logic import need_category_ok as ok
    for yes in ("家用", "家用_固定支出", "家用_變動支出", "個人_生活", "個人_旅遊", "個人_固定支出", "貸款繳款"):
        assert ok(yes), yes
    for no in ("公司_薪水", "公司_專案", "轉匯與定存", "轉匯與定存_公司信用卡", "信用卡", "", None,
               "家用_被動收入", "個人_主動收入",          # 收入列
               "家用_投資支出", "個人_投資支出",          # 買股票不是生活支出
               "家用_借款支出", "個人_借款支出",          # 卡費還款／房貸本息：卡債與貸款走「預留」，這裡再算一次就是重複
               "家用_其他支出", "個人_其他支出"):         # 帳務對齊、信用卡款
        assert not ok(no), no


def test_monthly_need_divides_by_the_months_that_have_data():
    """只有 3 個月有資料卻除以 6 → 每月生活支出被低估一半、可撐月數被高估一倍。"""
    from core.fortress_logic import monthly_need_from_rows
    rows = [("家用_固定支出", "2026-07", 30000), ("個人_生活", "2026-07", 10000), ("家用", "2026-08", 20000),
            ("公司_薪水", "2026-08", 900000), ("家用_投資支出", "2026-08", 500000)]
    avg, months = monthly_need_from_rows(rows)
    assert (avg, months) == (30000, 2), "只算生活支出、除以真的有資料的月份數"
    assert monthly_need_from_rows([]) == (0.0, 0), "沒資料不要除以 0"


def test_payload_says_how_many_months_the_average_came_from():
    d = build([], [], 82300, {}, "2026-09-16", need_months=3)
    assert d["monthly_need"]["sample_months"] == 3, "畫面要能誠實寫出「近 3 個月」"


# ── /polish 階段一：壓力測試的誠實度 ────────────────────────────────────
def test_holdings_take_the_drop_wherever_they_sit():
    """🔴 跌幅原本只套在「第 5 層」。把一檔 ETF 標成第 4 層機會資金（下拉就能做），
    它就變成戰爭題裡不會跌的現金 —— 1200 萬股票在台股跌六成的情境下原封不動。
    正解看的是**這筆是不是證券**（kind），不是它被放在第幾層。"""
    from core.fortress_logic import assign_layers, layer_sums, stress_tests
    accts = assign_layers(
        [{"id": "cash", "name": "活存", "kind": "bank", "balance": 500000},
         {"id": "etf", "name": "台股 ETF", "kind": "holding", "balance": 1200000}],
        normalize_settings({"account_layers": {"etf": 4}}))
    t = {x["key"]: x for x in stress_tests(layer_sums(accts), accts, 80000, 0, dict(DEFAULT_WAR))}
    war = t["war"]
    assert war["lines"][2][1] == 500000, "第 1–4 層現金不含證券"
    assert war["lines"][4][1] == 480000, "放在第 4 層的台股照樣跌六成"
    assert t["market"]["lines"][0][1] == 1200000, "股票下跌題看的是全部證券，不是只有第 5 層"


def test_war_verdict_follows_the_configured_months():
    """假設改成 6 個月，結論就不該還寫「撐得過一年」。"""
    from core.fortress_logic import assign_layers, layer_sums, stress_tests
    accts = assign_layers([{"id": "c", "name": "活存", "kind": "cash", "balance": 3000000}],
                          normalize_settings({"account_flags": {"c": {"physical": True}}}))
    war = {**DEFAULT_WAR, "months": 6}
    v = {x["key"]: x for x in stress_tests(layer_sums(accts), accts, 80000, 0, war)}["war"]["verdict"]
    assert "6 個月" in v and "一年" not in v


def test_settings_bounds():
    """戰爭假設沒有上下限的話，months=0 會讓「撐得過」變成必然、fx=0 會讓美元資產歸零。"""
    s = normalize_settings({"war": {"months": 0, "tw_drop": 5, "us_drop": -1, "fx": 0, "bank_freeze_weeks": -3},
                            "monthly_need_override": -50000})
    assert s["war"]["months"] >= 1 and 0 <= s["war"]["tw_drop"] <= 1 and 0 <= s["war"]["us_drop"] <= 1
    assert s["war"]["fx"] >= 0.1 and s["war"]["bank_freeze_weeks"] >= 0
    assert s["monthly_need_override"] is None, "負的必要支出＝沒填"


# ── /polish 階段一：預留的完整性與看得見的警告 ──────────────────────────
def test_payload_warns_when_the_usd_rate_is_missing():
    """🔴 `my_ledger.usd_twd` 沒設或是 0 時，_holding_value 會把每一筆美元持股算成 0
    （routers/api_finance_assets.py 對這個坑有紅字註解）。堡壘不能無聲吞掉：
    第 5 層會少掉整個外幣部位，而畫面上什麼跡象都沒有。"""
    accts = [{"id": "us", "name": "美股", "kind": "holding", "balance": 0, "currency": "USD"}]
    d = build(accts, [], 50000, {}, "2026-09-16", warnings=["美元匯率沒設定，美元資產現在算 0"])
    assert d["warnings"] == ["美元匯率沒設定，美元資產現在算 0"]
    assert build([], [], 50000, {}, "")["warnings"] == [], "沒事就是空的"


def test_auto_earmarks_keep_the_overdue_backlog():
    """只留「每筆貸款最近一期」的話，欠了三期的人看到的預留只有一期 —— 少算的方向是危險的。
    規則：逾期（到期日 ≤ 今天）全部留著，再加未來最近的一期。"""
    from core.fortress_logic import pick_loan_dues
    rows = [("a", "2026-07-05", 32000), ("a", "2026-08-05", 32000), ("a", "2026-09-05", 32000),
            ("a", "2026-10-05", 32000), ("a", "2026-11-05", 32000), ("b", "2026-10-20", 9000)]
    got = [(loan, due) for loan, due, _amt, _overdue in pick_loan_dues(rows, "2026-09-16")]
    assert got == [("a", "2026-07-05"), ("a", "2026-08-05"), ("a", "2026-09-05"), ("a", "2026-10-05"), ("b", "2026-10-20")]
    assert [o for _l, _d, _a, o in pick_loan_dues(rows, "2026-09-16")] == [True, True, True, False, False]
    assert pick_loan_dues([], "2026-09-16") == []


def test_nas_really_receives_the_fortress_settings():
    """不要只釘字串：export_settings 對「子鍵本身是 dict」的情況要真的送得出去
    （送不到＝手機上每個帳戶都掉回第 1 層，而且不會有任何錯誤）。"""
    from core.office_settings import export_settings
    out, _dropped = export_settings({"finance": {"fortress": {"mine": {"holdings_layer": 4}},
                                                 "margin_model": {"mine": {"rows": []}}},
                                     "jwt_secret": "nope"})
    assert out["finance"] == {"fortress": {"mine": {"holdings_layer": 4}}}, "只送 fortress、其他子鍵不送"
    assert "jwt_secret" not in out


def test_targets_have_an_upper_bound():
    """🔴 收尾 review 抓到：`float('inf') >= 0` 是真的，倍數 Infinity 會通過正規化、被寫進設定檔，
    之後每次開堡壘頁都在 `_m(inf)` 炸 OverflowError → 500，而且**修不回來**（設定已經存下去了，
    要手改 settings.json）。BUG-5 那輪把 war／金額／標題都加了上下限，漏了 targets。"""
    s = normalize_settings({"targets": {"1": float("inf"), "3": -5, "4": 999}})
    assert s["targets"][1] == 120 and s["targets"][4] == 120, "上限 120 個月（10 年）"
    assert s["targets"][3] == DEFAULT_TARGET_MONTHS[3], "負數不收，用預設"
    assert build([], [], 50000, s, "")["layers"], "算得出來，不會 OverflowError"


# ── 長照與資產預期成長（owner 2026-09-16「把長照考慮進去」「還有資產預期成長」）────────
def test_care_test_uses_long_term_capital_and_reacts_to_insurance():
    """長照跟其他五題不一樣：它不是一次衝擊，是把每月支出抬高好幾年 → 動用的是長期資本（第 5 層），
    所以「撐得了幾年」要把複利資本算進來；保險月給付直接扣在照護費上。"""
    accts = [{"id": "c", "name": "活存", "kind": "bank", "balance": 2_000_000},
             {"id": "e", "name": "ETF", "kind": "holding", "balance": 20_000_000}]
    d = build(accts, [], 100000, {"account_layers": {"e": 5}}, "2026-09-16", need_months=6)
    care = {t["key"]: t for t in d["tests"]}["care"]
    assert [x[0] for x in care["lines"]][-2:] == ["撐得了幾年", "只用現金撐得了幾年"]
    assert care["lines"][3] == ["現金＋投資合計", 22_000_000, "twd"], "現金＋投資，不是只有現金"
    assert care["lines"][4][2] == "years" and care["lines"][4][1] == 13.1
    assert care["lines"][5][1] == 1.2, "只用現金撐不了幾年 —— 這句話才是重點"
    # 保險月給付 2 萬 → 照護費剩 2 萬 → 撐更久
    d2 = build(accts, [], 100000, {"account_layers": {"e": 5}, "care": {"insurance_monthly": 20000}}, "", need_months=6)
    c2 = {t["key"]: t for t in d2["tests"]}["care"]
    assert c2["lines"][0][1] == 20000 and c2["lines"][4][1] > care["lines"][4][1]
    # 假設有上下限（years 0 會讓「撐得過」變成必然）
    s = normalize_settings({"care": {"years": 0, "monthly": -5, "insurance_monthly": 10 ** 9}})
    assert s["care"]["years"] >= 1 and s["care"]["monthly"] == 0 and s["care"]["insurance_monthly"] <= 1_000_000


def test_growth_projection_gives_nominal_and_todays_purchasing_power():
    """只看名目會高估未來買得起什麼：30 年後的 1000 萬不是今天的 1000 萬。兩個數字都要給。"""
    from core.fortress_logic import GROWTH_YEARS, project_growth
    rows = project_growth(1_000_000, {"rate": 0.06, "inflation": 0.02, "annual_add": 0})
    assert [r["year"] for r in rows] == list(GROWTH_YEARS)
    ten = [r for r in rows if r["year"] == 10][0]
    assert ten["nominal"] == round(1_000_000 * 1.06 ** 10)
    assert ten["real"] == round(ten["nominal"] / 1.02 ** 10) and ten["real"] < ten["nominal"]
    # 每年再投入：年底投入的年金
    with_add = project_growth(0, {"rate": 0.06, "inflation": 0.0, "annual_add": 100_000})
    assert [r for r in with_add if r["year"] == 5][0]["nominal"] == round(100_000 * ((1.06 ** 5 - 1) / 0.06))
    # 報酬 0% 不能除以零
    flat = project_growth(0, {"rate": 0.0, "inflation": 0.0, "annual_add": 100_000})
    assert [r for r in flat if r["year"] == 10][0]["nominal"] == 1_000_000
    d = build([], [], 50000, {}, "", need_months=6)
    assert [r["year"] for r in d["projection"]["rows"]] == list(GROWTH_YEARS) and d["projection"]["base"] == 0


# ── 財富階梯（owner 2026-09-16 拍板：台灣物價回推，不用匯率換；門檻可調）──────────
def test_ladder_thresholds_follow_the_common_taiwan_version():
    """owner 2026-09-16「參考市場先生」：書裡的美金級距乘 30（台灣最常見的算法，跟文章對得上），
    第 5 階「住宅自由」照市場先生拉到 6 億起跳（台灣買房特別貴）。"""
    from core.fortress_logic import DEFAULT_LADDER, ladder_position
    assert DEFAULT_LADDER["thresholds"] == [300_000, 3_000_000, 30_000_000, 600_000_000, 3_000_000_000]
    p = ladder_position(49_945_220, DEFAULT_LADDER)
    assert (p["rung"], p["name"]) == (4, "旅遊自由"), "4,995 萬是第 4 階（跟商周／市場先生的版本一致）"
    assert p["free_amount"] == 4995 and p["to_next"] == 600_000_000 - 49_945_220
    assert [r["you"] for r in p["rungs"]] == [False, False, False, True, False, False]
    assert p["rungs"][-1]["ceiling"] is None and ladder_position(5_000_000_000, DEFAULT_LADDER)["next"] is None
    assert ladder_position(0, DEFAULT_LADDER)["rung"] == 1 and ladder_position(500_000, DEFAULT_LADDER)["rung"] == 2


def test_ladder_thresholds_are_adjustable_but_must_stay_increasing():
    """owner 要能改門檻；亂序或個數不對就退回預設（不然「在第幾階」會算不出來）。"""
    from core.fortress_logic import DEFAULT_LADDER
    ok = normalize_settings({"ladder": {"thresholds": [300_000, 3_000_000, 30_000_000, 300_000_000, 3_000_000_000],
                                        "free_rate": 0.0002, "median": 9_000_000}})["ladder"]
    assert ok["thresholds"][3] == 300_000_000 and ok["free_rate"] == 0.0002 and ok["median"] == 9_000_000, "第 5 階要不要拉高也是設定"
    assert normalize_settings({"ladder": {"thresholds": [1, 2, 3]}})["ladder"]["thresholds"] == DEFAULT_LADDER["thresholds"], "個數不對"
    assert normalize_settings({"ladder": {"thresholds": [5, 5, 5, 5, 5]}})["ladder"]["thresholds"] == DEFAULT_LADDER["thresholds"], "要嚴格遞增"
    assert normalize_settings({"ladder": {"thresholds": [50, 40, 30, 20, 10]}})["ladder"]["thresholds"] == [10, 20, 30, 40, 50], "會自己排好"
    assert normalize_settings({"ladder": {"free_rate": 99}})["ladder"]["free_rate"] <= 0.01


def test_percentile_line_answers_a_different_question():
    """階梯問「不用想能花多少」，分位數問「贏過多少家庭」—— 並排，不互相取代。"""
    from core.fortress_logic import DEFAULT_LADDER, percentile_note
    assert "前 10%" in percentile_note(49_945_220, DEFAULT_LADDER)
    assert "前 20%" in percentile_note(25_000_000, DEFAULT_LADDER)
    assert "高於" in percentile_note(10_000_000, DEFAULT_LADDER)
    assert "低於" in percentile_note(1_000_000, DEFAULT_LADDER)
    assert "2021" in percentile_note(49_945_220, DEFAULT_LADDER), "要寫出資料年份（會過時）"


def test_plan_fields_say_what_it_would_take():
    """三個規劃欄位接起來：照現在幾年到、要準時到的話每年要投多少／報酬要多少。做不到就說做不到。"""
    from core.fortress_logic import plan_result, years_to_reach
    r = plan_result(47_063_708, 600_000_000, 20, {"rate": 0.06, "inflation": 0.02, "annual_add": 0})
    assert r["years_at_current"] == 44 and r["on_track"] is False, "6% 不再投入，6 億要 44 年"
    assert r["need_annual_add"] > 10_000_000 and 0.13 < r["need_rate"] < 0.14, "20 年內到：每年投一千多萬，或報酬 13.6%"
    ok = plan_result(47_063_708, 100_000_000, 20, {"rate": 0.06, "inflation": 0.02, "annual_add": 0})
    assert ok["on_track"] is True and ok["need_annual_add"] == 0, "已經來得及就不用再投入"
    assert years_to_reach(100, 1_000_000, 0.0, 0) is None, "不長也不投入 → 到不了（不是無限迴圈）"
    assert years_to_reach(100, 50, 0.06, 0) == 0


def test_effort_focus_and_net_worth_in_payload():
    """淨值＝五層合計 − 負債；「該把力氣放哪」＝資產自己長的 vs 你存的。"""
    accts = [{"id": "c", "name": "活存", "kind": "bank", "balance": 2_883_940},
             {"id": "e", "name": "證券", "kind": "holding", "balance": 47_063_708}]
    d = build(accts, [], 94206, {"account_layers": {"e": 5}}, "2026-09-16", need_months=6,
              liabilities={"card": 2428, "loan": 0})
    L = d["ladder"]
    assert L["net_worth"] == 2_883_940 + 47_063_708 - 2428 and L["liabilities"]["total"] == 2428
    assert L["rung"] == 4 and L["focus"]["passive"] == round(47_063_708 * 0.06) and L["focus"]["passive_wins"] is True
    d2 = build(accts, [], 94206, {"account_layers": {"e": 5}, "growth": {"annual_add": 5_000_000}}, "", need_months=6)
    assert d2["ladder"]["focus"]["passive_wins"] is False, "存得比長得多 → 力氣放在收入"


def test_property_only_counts_toward_the_ladder():
    """owner 2026-09-16「可以新增房產的選項 但我現在沒有」：手填估值只進階梯的淨值 ——
    不進五層、不進可撐月數、不進長照題（房子不是能拿來付帳的錢）。"""
    accts = [{"id": "c", "name": "活存", "kind": "bank", "balance": 2_883_940},
             {"id": "e", "name": "證券", "kind": "holding", "balance": 47_063_708}]
    base = build(accts, [], 94206, {"account_layers": {"e": 5}}, "", need_months=6, liabilities={"card": 2428})
    withp = build(accts, [], 94206, {"account_layers": {"e": 5}, "property": {"value": 30_000_000, "note": "台北自住"}},
                  "", need_months=6, liabilities={"card": 2428})
    assert withp["ladder"]["net_worth"] == base["ladder"]["net_worth"] + 30_000_000
    assert withp["ladder"]["property"] == {"value": 30_000_000, "note": "台北自住"} and withp["ladder"]["financial"] == base["ladder"]["financial"]
    assert withp["cash"] == base["cash"] and withp["runway"] == base["runway"], "五層與可撐月數不變"
    care_b = {t["key"]: t for t in base["tests"]}["care"]["lines"][3]
    care_w = {t["key"]: t for t in withp["tests"]}["care"]["lines"][3]
    assert care_b == care_w, "長照題也不變"
    assert normalize_settings({"property": {"value": -5}})["property"]["value"] == 0
    assert normalize_settings({})["property"] == {"value": 0, "note": ""}, "預設沒有房產"


# ── 財富自由（owner 2026-09-16「此刻如果我不工作沒收入 我每個月可以花多少錢」；1989 年生；提領率 3.5%）──
def test_fire_block_with_the_owners_numbers():
    """三個提領率都給；判定用 3.5%（要撐 50 年以上、股票比重九成，4% 太樂觀）。
    支出＝必要支出＋不工作後自付的健保、國保。模擬含通膨、算到 90 歲。"""
    accts = [{"id": "c", "name": "活存", "kind": "bank", "balance": 2_883_940},
             {"id": "e", "name": "證券", "kind": "holding", "balance": 47_063_708}]
    d = build(accts, [], 94206, {"account_layers": {"e": 5}}, "2026-09-16", need_months=6)
    f = d["fire"]
    assert f["age"] == 37 and f["until_age"] == 90 and f["horizon_years"] == 53, "1989 年生，2026 年 37 歲"
    assert f["spend"] == 94206 + 2000 and f["self_pay"] == 2000
    assert f["by_rate"] == {"0.03": 124869, "0.035": 145681, "0.04": 166492}
    assert f["allowed"] == 145681 and f["withdrawal_rate"] == 0.035
    assert f["current_rate"] == 0.0231 and f["ratio25"] == 1.73 and f["ratio33"] == 1.3
    assert f["runs_out_year"] is None and f["runs_out_year_zero"] == 32, "6% 用不完；0% 第 32 年用完"
    assert f["cash_years"] == 2.5 and f["state"] == STATE_OK and "已達財富自由" in f["verdict"]
    units = {l[2] for l in f["lines"]}
    assert units == {"twd", "pct", "years", "year_or_never"}


def test_fire_states_and_extra_income():
    """花太多 → 會用完 → 還沒到；65 歲後有年金 → 撐更久。沒填出生年 → 模擬 50 年、不寫歲數。"""
    from core.fortress_logic import fire_simulate
    accts = [{"id": "c", "name": "活存", "kind": "bank", "balance": 2_883_940},
             {"id": "e", "name": "證券", "kind": "holding", "balance": 47_063_708}]
    bad = build(accts, [], 250_000, {"account_layers": {"e": 5}}, "2026-09-16", need_months=6)["fire"]
    assert bad["state"] == STATE_BAD and bad["runs_out_year"] == 29 and bad["runs_out_age"] == 66 and "還沒到" in bad["verdict"]
    # 年金從 65 歲起只比「用完那年」（66 歲）早一年，推不動；從 50 歲起就看得出差別
    with_pension = build(accts, [], 250_000, {"account_layers": {"e": 5}, "fire": {"extra_monthly": 60_000, "extra_from_age": 50}},
                         "2026-09-16", need_months=6)["fire"]
    assert (with_pension["runs_out_year"] or 999) > bad["runs_out_year"], "50 歲後每月多 6 萬，撐更久"
    noage = build(accts, [], 94206, {"account_layers": {"e": 5}, "fire": {"birth_year": 0}}, "2026-09-16", need_months=6)["fire"]
    assert noage["age"] is None and noage["horizon_years"] == 50 and "歲" not in noage["verdict"]
    # 純函式：不長也不領 → 一年就見底；有其他收入從第 3 年起 → 之後不再減
    assert fire_simulate(100, 100, 0.0, 0.0, 5) == (1, 0)
    assert fire_simulate(1000, 10, 0.0, 0.0, 5, extra_monthly=10, extra_from_year=3)[1] == 1000 - 240
    s = normalize_settings({"fire": {"withdrawal_rate": 0.5, "until_age": 30, "birth_year": "1989"}})["fire"]
    assert s["withdrawal_rate"] == 0.10 and s["until_age"] == 40 and s["birth_year"] == 1989, "有上下限"


def test_line_units_render_on_both_frontends():
    """🔴 長照那題的 'years' 單位上線時兩邊都被當成金額印成「13.1 萬」（沒有測試釘）。
    現在四種單位都要有自己的寫法：months／years／pct／year_or_never（null＝用不完）。"""
    desk = js_func_body(js_code_only(repo_src("frontend/tabs/finance/subviews/fortress.js")), "function fmtLine(l, ctx) {")
    for u in ("'months'", "'years'", "'pct'", "'year_or_never'"):
        assert u in desk, u
    assert "'用不完'" in desk
    mob = js_code_only(repo_src("frontend/m/views/ledger-fortress.js"))
    for u in ("'months'", "'years'", "'pct'", "'year_or_never'"):
        assert u in js_func_body(mob, "const lineVal = (v, unit, ctx) => {"), u
    assert "fireHtml(d)" in mob and "不工作每月可花" in mob, "堡壘頁有卡、總覽頂卡有一行"


# ── 特徵測試（/polish 階段零）：這次動到、但沒有被直接釘住的行為 ────────────────
def test_fire_simulate_characterisation():
    """把 fire_simulate 現在的行為釘下來：先長再提、第 1 年的生活費**不加**通膨（指數是 y-1）、
    其他收入從 extra_from_year 那一年（含）開始扣、資產轉負就回那一年。"""
    from core.fortress_logic import fire_simulate
    # 100 萬、0 報酬 0 通膨、一年花 12 萬 → 第 8 年底剩 4 萬，第 9 年見底
    assert fire_simulate(1_000_000, 10_000, 0.0, 0.0, 8) == (None, 1_000_000 - 8 * 120_000)
    assert fire_simulate(1_000_000, 10_000, 0.0, 0.0, 20) == (9, 0), "撐不住那一年回 (年, 0)，後面不再算"
    # 通膨指數：第 1 年原價、第 2 年 ×1.1
    assert fire_simulate(1_000_000, 10_000, 0.0, 0.1, 2)[1] == 1_000_000 - 120_000 - 132_000
    # 報酬先算：100 萬 ×1.06 − 12 萬
    assert fire_simulate(1_000_000, 10_000, 0.06, 0.0, 1)[1] == round(1_000_000 * 1.06 - 120_000)
    # 其他收入：第 3 年起每月 1 萬 → 那三年不用動本金
    assert fire_simulate(500_000, 10_000, 0.0, 0.0, 5, extra_monthly=10_000, extra_from_year=3)[1] == 500_000 - 2 * 120_000
    assert fire_simulate(100, 100, 0.0, 0.0, 0) == (None, 100), "years=0 不模擬"


def test_fire_settings_bounds_each_key():
    """六格都有上下限；亂填不會讓整頁炸掉或給出假答案。"""
    from core.fortress_logic import DEFAULT_FIRE
    lo = normalize_settings({"fire": {"withdrawal_rate": 0, "birth_year": -5, "until_age": 10,
                                      "extra_monthly": -1, "extra_from_age": -3, "self_pay_monthly": -9}})["fire"]
    assert lo == {"withdrawal_rate": 0.01, "birth_year": 0, "until_age": 40,
                  "extra_monthly": 0, "extra_from_age": 0, "self_pay_monthly": 0}
    hi = normalize_settings({"fire": {"withdrawal_rate": 9, "birth_year": 9999, "until_age": 999,
                                      "extra_monthly": 10 ** 9, "extra_from_age": 999, "self_pay_monthly": 10 ** 9}})["fire"]
    assert hi == {"withdrawal_rate": 0.10, "birth_year": 2100, "until_age": 120,
                  "extra_monthly": 10_000_000, "extra_from_age": 120, "self_pay_monthly": 1_000_000}
    assert normalize_settings({"fire": {"withdrawal_rate": "x", "until_age": None}})["fire"] == DEFAULT_FIRE, "不是數字就用預設"
    assert normalize_settings({})["fire"] == DEFAULT_FIRE and DEFAULT_FIRE["birth_year"] == 1989


def test_fire_without_a_birth_year_or_date():
    """沒有出生年（或 build 沒給日期）→ 不寫歲數、模擬 FIRE_DEFAULT_HORIZON 年。"""
    from core.fortress_logic import FIRE_DEFAULT_HORIZON
    accts = [{"id": "c", "name": "活存", "kind": "bank", "balance": 30_000_000}]
    for today, cfg in (("", {}), ("2026-09-16", {"fire": {"birth_year": 0}}), ("不是日期", {})):
        f = build(accts, [], 50_000, cfg, today, need_months=6)["fire"]
        assert f["age"] is None and f["horizon_years"] == FIRE_DEFAULT_HORIZON, (today, cfg)
        assert f["runs_out_age"] is None and "歲" not in f["verdict"]


# ── /polish 階段一 ────────────────────────────────────────────────────────
def test_fire_needs_real_spending_data_before_declaring_freedom():
    """🔴 帳本還沒有生活支出資料時，不能拿「不工作後自付的健保、國保」那 2,000 元當生活費去宣告財富自由。
    原本的守衛是 `spend <= 0`，而 spend＝必要支出＋自付，自付預設 2,000 → 永遠 > 0 →
    新帳本會看到「已達財富自由。照現在的花法（0.2 萬／月）只用到資產的 0.05%」。
    可撐月數那邊在同樣情況會回 None（算不出來），兩邊要一致。"""
    accts = [{"id": "c", "name": "活存", "kind": "bank", "balance": 2_883_940},
             {"id": "e", "name": "證券", "kind": "holding", "balance": 47_063_708}]
    d = build(accts, [], 0, {"account_layers": {"e": 5}}, "2026-09-16", need_months=0)
    f = d["fire"]
    assert f["state"] == "na" and f["lines"] == [] and "先設定每月必要支出" in f["verdict"]
    assert d["runway"]["months"] is None, "同一頁的可撐月數也是算不出來"
    # 「你可以花多少」不依賴現在花多少，所以照樣給；但不能拿來跟現況比較
    assert f["allowed"] > 0 and f["by_rate"], "提領率能算的部分照給"
    assert "current_rate" not in f and "ratio25" not in f and "cash_years" not in f, "比較用的數字不要給"


def test_mobile_overview_line_is_hidden_when_data_is_missing():
    """總覽頂卡那行只看 by_rate 的話，資料不足時照樣印出金額 —— 那是最常看的畫面。"""
    js = js_code_only(repo_src("frontend/m/views/ledger-fortress.js"))
    card = js_func_body(js, "export function fortressCardHtml(d) {")
    assert "d.fire.state !== 'na'" in card, "資料不足就不要印那一行"
