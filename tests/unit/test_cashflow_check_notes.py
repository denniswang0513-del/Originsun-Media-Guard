# -*- coding: utf-8 -*-
"""現金流勾稽差額要講**真正的**原因（owner 2026-08-22）。

owner 把所有收支都掛上帳戶之後，差額還在 $65,880 —— 因為畫面上那句
「可能有收支未掛帳戶」是寫死的，而後端 `build_cashflow` 早就算出真正的原因
（轉存未完全成對），只是被前端丟掉了。

猜的原因比沒有原因更糟：它讓人往錯的方向修一整輪。
"""
from tests.unit._srcscan import code_only, func_body, repo_src

SRC = "core/finance_logic.py"
JS = "frontend/tabs/finance/subviews/statements.js"


def _cf():
    return code_only(func_body(repo_src(SRC), "def build_cashflow("))


def _cfl():
    """「哪幾列算數、各算多少」自 2026-08-22 起住在 cashflow_lines
    —— build_cashflow 與鑽取共用同一支（見該函式 docstring）。"""
    return code_only(func_body(repo_src(SRC), "def cashflow_lines("))


# ── 後端本來就算得出原因 ──────────────────────────────────────────

def test_backend_explains_all_three_causes():
    body = _cf()
    for cause in ("員工預支往來淨流", "帳戶間轉存未完全成對", "未掛帳戶收支未列入"):
        assert cause in body, f"少了「{cause}」這個原因"


def test_diff_is_closing_minus_opening_plus_net():
    """定義要釘住 —— 方向反了整段解讀就顛倒。"""
    body = _cf()
    assert ('diff = int(closing.get("total") or 0) - '
            '(int(opening.get("total") or 0) + net)') in body


def test_unassigned_entries_are_excluded_from_flow():
    """未掛帳戶的收支不影響任何帳戶餘額，計入就會破壞恆等式。"""
    body = _cfl()
    assert 'if not e.get("bank_account_id"):' in body
    assert 'stats["unassigned"] += 1' in body and "continue" in body


def test_noncash_accounts_are_excluded_from_flow():
    """🔴 期初/期末只算現金類帳戶（split_bank_lines 的 ["cash"]），
    迭代這邊也必須一致 —— 不然股東往來上的每一筆都會製造勾稽差額。
    這是 owner 2026-08-22 正要開始用股東往來記帳前抓到的（還沒爆）。"""
    assert 'cash_ids is not None and e["bank_account_id"] not in cash_ids' in _cfl()
    assert 'stats["noncash"] += 1' in _cfl()
    assert "非現金帳戶（股東往來）" in _cf(), "排除了但沒告訴人"


def test_official_path_passes_the_accounts():
    """算得出來還要真的傳進去 —— 沒傳就退回舊行為（等於沒修）。"""
    from tests.unit._srcscan import code_only, repo_src
    src = repo_src("services/finance_statements.py")
    assert 'bank_accounts=inputs["bank_accounts"]' in src
    # 🔴 要看**程式碼**，不是全檔找字串 —— 我在那裡寫的註解就提到
    #    is_shareholder_kind，全檔比對會被自己的註解餵飽（今天第三次踩）。
    assert "is_shareholder_kind" not in code_only(src),         "呼叫端又自己判了一次什麼算現金"


def test_missing_accounts_falls_back_to_old_behaviour():
    """沒傳清單時只擋未掛帳戶（舊呼叫端與單元測試不一定傳得出帳戶清單）。"""
    from core.finance_logic import build_cashflow
    ents = [{"entry_date": "2026-01-05", "bank_account_id": "X",
             "deposit": 100, "category": "其他收入"}]
    cm = {("cash", "其他收入"): {"treatment": "direct_income"}}
    r = build_cashflow(["2026-01"], opening={"total": 0}, closing={"total": 100},
                       cash_entries=ents, cat_map=cm)
    assert r["net"] == 100, "沒傳 bank_accounts 時不該把它擋掉"
    # 傳了帳戶：股東往來那個帳戶上的收支不進淨流
    r2 = build_cashflow(["2026-01"], opening={"total": 0}, closing={"total": 0},
                        cash_entries=ents, cat_map=cm,
                        bank_accounts=[{"id": "X", "acct_kind": "shareholder_loan"},
                                       {"id": "Y", "acct_kind": "bank"}])
    assert r2["net"] == 0, "股東往來上的收支被算進淨流了"
    assert any("非現金帳戶" in n for n in r2["check"]["notes"])
    # 一般銀行帳戶照算
    r3 = build_cashflow(["2026-01"], opening={"total": 0}, closing={"total": 100},
                        cash_entries=ents, cat_map=cm,
                        bank_accounts=[{"id": "X", "acct_kind": "bank"}])
    assert r3["net"] == 100


def test_transfer_principal_is_tracked_for_the_note():
    """轉存本金不列入活動，但差額要能歸因到它。"""
    assert 'stats["advance_net" if t == "advance" else "transfer_net"] += principal' in _cfl()


def test_transfer_fee_is_real_money_leaving():
    """🔴 轉存的**本金**是內部移動，**跨行手續費**不是 —— 那筆錢真的出去了。

    2026 年生產有十筆「網路跨轉」各 15 元手續費：表上算進營運（對），
    鑽取整筆跳過（錯）→ 兩邊差 150。差得少所以更難發現。
    """
    from core.finance_logic import build_cashflow, cashflow_lines
    ents = [{"id": "t1", "entry_date": "2026-01-10", "bank_account_id": "A",
             "expense": 5000, "bank_fee": 15, "category": "轉存"}]
    cm = {("cash", "轉存"): {"treatment": "transfer"}}
    accts = [{"id": "A", "acct_kind": "bank"}]
    r = build_cashflow(["2026-01"], opening={"total": 0}, closing={"total": 0},
                       cash_entries=ents, cat_map=cm, bank_accounts=accts)
    assert r["operating"] == -15, "手續費沒算進營運"
    rows, _s = cashflow_lines(ents, ["2026-01"], cat_map=cm, bank_accounts=accts)
    assert [(x["amount"], x["is_fee"]) for x in rows] == [(-15, True)], \
        "明細沒有把手續費列出來（本金則不該出現）"


def test_the_table_and_the_drilldown_cannot_disagree():
    """表上每一格 == 該格明細的合計。這是 cashflow_lines 存在的唯一理由。"""
    from core.finance_logic import build_cashflow, cashflow_lines
    months = ["2026-01"]
    ents = [
        {"id": "a", "entry_date": "2026-01-02", "bank_account_id": "A",
         "deposit": 100000, "category": "設計服務收入"},
        {"id": "b", "entry_date": "2026-01-03", "bank_account_id": "A",
         "expense": 5000, "bank_fee": 15, "category": "轉存"},      # 內部移動＋手續費
        {"id": "c", "entry_date": "2026-01-04", "bank_account_id": "A",
         "expense": 8000, "category": "行政費用"},
        {"id": "d", "entry_date": "2026-01-05", "bank_account_id": "S",
         "deposit": 700000, "category": "設計服務收入"},            # 股東往來 → 不算
        {"id": "e", "entry_date": "2026-01-06", "deposit": 999,
         "category": "設計服務收入"},                               # 未掛帳戶 → 不算
    ]
    cm = {("cash", "設計服務收入"): {"treatment": "direct_income"},
          ("cash", "行政費用"): {"treatment": "direct_expense"},
          ("cash", "轉存"): {"treatment": "transfer"}}
    accts = [{"id": "A", "acct_kind": "bank"},
             {"id": "S", "acct_kind": "shareholder_loan"}]
    table = build_cashflow(months, opening={"total": 0}, closing={"total": 0},
                           cash_entries=ents, cat_map=cm, bank_accounts=accts)
    rows, _s = cashflow_lines(ents, months, cat_map=cm, bank_accounts=accts)
    for act in ("operating", "investing", "financing"):
        drill = sum(r["amount"] for r in rows if r["activity"] == act)
        assert drill == table[act], f"{act}：表 {table[act]} vs 明細 {drill}"
    assert table["operating"] == 100000 - 8000 - 15


# ── 前端要把原因講出來 ────────────────────────────────────────────

def _cf_html():
    """🔴 框到 _cfHtml 裡面 —— `const checkHtml` 在這個檔案有兩處
    （資產負債表也有一個），全檔 index() 會抓到另一份（實測踩到）。"""
    js = repo_src(JS)
    i = js.index("function _cfHtml(")
    return js[i:js.index(chr(10) + "function ", i + 10)]


def test_ui_shows_the_backend_notes():
    seg = _cf_html()
    assert "chk.notes" in seg, "沒有把後端的 notes 取出來"
    assert "notes.map(" in seg, "notes 沒有畫出來"


def test_ui_no_longer_guesses_a_single_cause():
    """🔴 這一句就是把 owner 帶偏的元凶。"""
    js = repo_src(JS)
    assert "期初＋淨流 ≠ 期末，可能有收支未掛帳戶" not in js, \
        "又把原因寫死成「未掛帳戶」了"


def test_ui_says_so_when_there_is_no_known_cause():
    """notes 空的時候不能沉默 —— 那代表系統自己也不知道，要講出來。"""
    assert "系統找不到已知原因" in _cf_html()


def test_drilldown_uses_the_same_eligibility_as_the_number_it_drills_into():
    """🔴 鑽取的合計要等於它鑽的那個數字 —— 判準必須是同一組。

    兩次都栽在「各自判一遍」：第一次漏了非現金帳戶（差 777,000），第二次漏了
    轉存的跨行手續費（差 150）。所以現在鑽取**不准自己判** —— 只能走
    cashflow_lines，判準沒有第二份可以漂。
    """
    from tests.unit._srcscan import code_only, func_body, repo_src
    src = repo_src("services/finance_statements.py")
    body = code_only(func_body(src, "async def drilldown("))
    # 只框 cash.* 那一段 —— 同一支函式的 revenue/cost 分支本來就要自己 classify
    seg = body[body.index('act = kind.split(".", 1)[1]'):]
    assert "cashflow_lines(" in seg, "鑽取沒有走共用的那支迭代器"
    for own in ("cash_account_ids(", "classify_cash_entry(", "cash_entry_activity("):
        assert own not in seg, f"鑽取又自己判了一次（{own}）"
    # 兩邊都用具名的那一份，不准再出現字面
    cf = code_only(repo_src("core/finance_logic.py"))
    assert '("advance", "transfer")' not in cf, "core 裡還有字面的內部移動清單"


def test_cash_account_rule_has_one_name():
    """「什麼算現金帳戶」跟 split_bank_lines 是同一條規則。"""
    from core.finance_logic import cash_account_ids
    accts = [{"id": "A", "acct_kind": "bank"},
             {"id": "B", "acct_kind": "shareholder_loan"},
             {"id": "C", "acct_kind": "shareholder_capital"},
             {"id": "D", "acct_kind": None}]
    assert cash_account_ids(accts) == {"A", "D"}, "股東往來被算成現金了"
    assert cash_account_ids(None) is None, "沒給帳戶時不該擋"
    # 認不得的性質落到現金 —— 跟 split_bank_lines 的預設一致
    assert "E" in cash_account_ids(accts + [{"id": "E", "acct_kind": "新桶"}])


def test_the_two_cash_rules_agree():
    """cash_account_ids 與 split_bank_lines 對同一份帳戶必須給同一個答案。"""
    from core.finance_logic import cash_account_ids, split_bank_lines
    accts = [{"id": "A", "acct_kind": "bank"},
             {"id": "B", "acct_kind": "shareholder_loan"},
             {"id": "C", "acct_kind": "shareholder_capital"},
             {"id": "D", "acct_kind": None},
             {"id": "E", "acct_kind": "新桶"}]
    lines = [{"id": b["id"]} for b in accts]
    by_split = {ln["id"] for ln in split_bank_lines(accts, lines)["cash"]}
    assert by_split == cash_account_ids(accts), "兩條規則講出不同的話"

