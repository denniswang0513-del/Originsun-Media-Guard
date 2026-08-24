# -*- coding: utf-8 -*-
"""貸款帳戶（一銀／合庫）的固定結構 —— 匯入就該分類完，不用逐列動手。

owner 2026-08-24：「一銀與合庫都是貸款帳號，結構都固定，你統一標準，
之後匯入不要放錯就可以了」「按下確定就直接完成記帳」。

一筆貸款在帳上的完整生命週期：
  撥款日：信保基金／手續費先扣 → 放款貸放進來 → 當天掃到營運帳戶
  之後每月：營運帳戶匯錢進來 → 放款本息／放款利息被扣 → 補貼息入帳

🔴 借款到位**只在放款行的帳戶認列一次**。掃到營運帳戶的那一筆是轉存，不是第二次
   借款 —— 兩邊都記成借款的話，負債會憑空變兩倍，而且兩張報表都看起來很正常。
"""
from core.bank_statement import KEYWORD_RULES, parse_statement

# 一銀網銀匯出（CSV，空欄印 `-`）—— 撥款日的完整順序
FIRST_BANK = """交易日期,交易時間,幣別,支出金額,存入金額,餘額,票據號碼,摘要,附註
2025/05/12,15:50:44,新臺幣,-,2000.00,2000.00,,跨行轉帳,0120082120000062728
2025/05/14,21:33:51,新臺幣,-,28000.00,30000.00,,跨行轉帳,0120082120000062728
2025/05/15,11:00:38,新臺幣,3075.00,-,26925.00,,信保基金,
2025/05/15,12:03:25,新臺幣,-,1000000.00,1026925.00,,放款貸放,0070000013366031755
2025/05/15,12:03:25,新臺幣,20000.00,-,1006925.00,,手續費,0070000013366031755
2025/05/15,12:20:51,新臺幣,1005000.00,-,1925.00,,寄庫寄款,
2025/06/16,01:22:39,新臺幣,278.00,-,1647.00,,放款本息,13366031755IN278
2025/06/16,15:23:29,新臺幣,-,5000.00,6647.00,,跨行轉帳,0120082120000062728
2025/06/16,15:35:22,新臺幣,1850.00,-,4797.00,,放款利息,0070000013366031755
2025/06/21,03:37:49,新臺幣,-,1.00,4798.00,,活存息,
"""

# 合庫（空欄直接消失、空白分隔）
COOP = """序號 交易日期 摘要 交易行庫 幣別 提款金額 存款金額 餘額 備註
1 2025/01/08 01:56:40 攤還本息 合庫松山 TWD 4470.00 170538.00 01-08 315614
2 2025/01/23 01:43:07 中心轉存 合庫營 TWD 3222.00 173760.00 文創補貼息
3 2025/01/28 06:30:52 跨行轉入 B012 TWD 35000.00 208760.00 0082120000062728
4 2025/06/21 01:02:39 利息　　 合庫松山 TWD 615.00 209375.00 $0(稅)
"""


def _cats(text, opening=0):
    r = parse_statement(text, opening_balance=opening)
    assert r.ok, r.errors
    return {x.note.split()[0]: x.category for x in r.rows}


# ── 一銀：撥款日那一串 ────────────────────────────────────────

def test_the_disbursement_day_classifies_end_to_end():
    got = _cats(FIRST_BANK)
    assert got["跨行轉帳"] == "轉存", "營運帳戶匯進來的錢是搬錢"
    assert got["信保基金"] == "其他", "撥款前先扣的保證費"
    assert got["放款貸放"] == "貸款撥款", "🔴 借款到位"
    assert got["手續費"] == "其他"
    assert got["寄庫寄款"] == "轉存", "🔴 掃到營運帳戶是搬錢，不是第二次借款"
    assert got["放款本息"] == "貸款繳款"
    assert got["活存息"] == "銀行利息"


def test_loan_interest_paid_is_not_interest_earned():
    """🔴 `放款利息` 是**繳出去**的貸款利息。被「利息 → 銀行利息」攔截的話會
    變成利息**收入** —— 2026-08-24 實測到，一銀每月 1,850 + 2,498 全被記成收入，
    損益表憑空多出一筆收入、又少了一筆費用（錯兩次）。"""
    assert _cats(FIRST_BANK)["放款利息"] == "貸款繳款"


def test_the_loan_interest_rule_outranks_the_generic_one():
    """規則是**由上而下先命中先贏** —— 順序本身就是規格，要釘住。"""
    order = [kw for kw, _c, *_ in KEYWORD_RULES]
    assert order.index("放款利息") < order.index("利息"), \
        "放款利息 排在 利息 後面 → 會被攔截成利息收入"
    assert order.index("放款貸放") < order.index("轉存")


def test_nothing_in_the_fixed_structure_is_left_unclassified():
    """owner 要的是「按下確定就直接完成記帳」—— 固定結構裡不該有未分類的列。"""
    r = parse_statement(FIRST_BANK, opening_balance=0)
    blank = [x for x in r.rows if not x.category]
    assert not blank, f"還有沒分類的：{[(x.date, x.note[:14]) for x in blank]}"


# ── 合庫：同一套標準 ──────────────────────────────────────────

def test_the_coop_statement_uses_the_same_standard():
    got = _cats(COOP, opening=175008)
    assert got["攤還本息"] == "貸款繳款"
    assert got["中心轉存"] == "貸款補貼", "信保／文創的利息補貼是收入"
    assert got["跨行轉入"] == "轉存"
    assert got["利息"] == "銀行利息", "存款利息才是收入"


def test_the_coop_statement_is_fully_classified():
    r = parse_statement(COOP, opening_balance=175008)
    assert r.ok and not [x for x in r.rows if not x.category]


# ── 第一列的方向要算出來，不是猜的 ────────────────────────────

def test_the_opening_balance_settles_the_first_row():
    """🔴 第一列沒有前一列的餘額可減，銀行又不一定印總計 → 只好猜方向並標
    inferred（預覽預設不勾，要人確認）。但那個數字系統本來就有：期初餘額 ＋
    那天以前的流水。傳進去就是**算出來**的。

    實測：一銀那份第一列本來被推成支出 −2,000，實際是存入 +2,000。
    """
    guess = parse_statement(FIRST_BANK).rows[0]
    known = parse_statement(FIRST_BANK, opening_balance=0).rows[0]
    assert guess.inferred is True, "沒有期初餘額時本來就該標記為推測"
    assert known.inferred is False, "有期初餘額卻還在猜"
    assert known.amount == 2000, "全新帳戶第一筆 2,000 應該是存入"


def test_the_preview_actually_passes_the_opening_balance():
    """算得出來還要真的傳下去 —— 少接這一步的話上面那條在正式流程裡不生效。"""
    from tests.unit._srcscan import code_only, func_body, repo_src
    src = repo_src("routers/api_finance_stmt.py")
    body = code_only(func_body(src, "async def _build_statement_preview("))
    assert "opening = await _balance_before(" in body, \
        "preview 沒有去算帳戶的期初餘額"
    assert "opening_balance=opening" in body, "算了卻沒傳給解析器"
    helper = code_only(func_body(src, "async def _balance_before("))
    assert "bank_running_balance" in helper, \
        "自己又算了一次餘額 —— 定義只能有一份（core.finance_logic）"
    assert "entry_date < first_day" in helper, \
        "沒有限定「對帳單第一列之前」的流水"


def test_a_wrong_opening_balance_must_not_block_the_import():
    """🔴 期初餘額是**提示不是約束**。

    parse_statement 拿 opening_balance 當硬條件 —— 第一列跟它對不上就整份解析
    失敗。而對不上很常見：帳上那段期間有缺漏、重疊匯入、對帳單不是從帳上資料的
    斷點開始。**擋掉一份有效的對帳單，比讓第一列多打一個勾嚴重得多。**

    2026-08-24 實測：加上這個提示之後，兩支本來全綠的 e2e 直接變紅 ——
    那就是「有效的對帳單被擋下來」長什麼樣子。
    """
    from tests.unit._srcscan import code_only, func_body, repo_src
    body = code_only(func_body(repo_src("routers/api_finance_stmt.py"),
                               "async def _build_statement_preview("))
    # 釘的是**保證**不是某一行字：不帶期初先解一次，帶期初那次只有在成功時才採用。
    assert "res = parse_statement(text, rules=rules)" in body, \
        "沒有先解一次不帶期初的 —— 期初算錯就會整份匯不進來"
    assert "if better.ok:" in body, \
        "帶期初那次不管成不成功都採用了 —— 對不上就會擋掉一份有效的對帳單"
    assert body.count("parse_statement(") == 2, \
        "解析次數不是兩次（一次拿第一筆交易日、一次帶期初）"


def test_the_first_transaction_date_comes_from_the_parser_not_the_header():
    """🔴 第一筆交易日要由**解析器**說，不是從整份文字撈第一個日期。

    合庫的對帳單第二行就是「查詢期間：2025/01/01-2026/01/01」—— 撈第一個日期
    會撈到 2025/01/01，而第一筆交易其實是 2025/01/08，差七天。那七天內只要有
    任何一筆資料，算出來的期初就是錯的，這個優化就默默失效（錯的期初會讓解析
    失敗、退回原行為，所以不危險，但也沒人會發現它根本沒作用）。
    """
    from core.bank_statement import _DATE, parse_statement
    header = ("歷史交易查詢\n查詢期間：2025/01/01-2026/01/01\n"
              "類別：TWD_CURRENT 帳號：0070717559515\n"
              "1 2025/01/08 01:56:40 攤還本息 合庫松山 TWD 4470.00 170538.00 01-08 315614\n"
              "2 2025/01/08 01:56:40 攤還本息 合庫松山 TWD 25290.00 145248.00 01-08 315611\n")
    assert _DATE.search(header).group(0) == "2025/01/01", \
        "這份樣本沒有重現那個陷阱（表頭的日期要早於第一筆交易）"
    assert parse_statement(header).rows[0].date == "2025-01-08", \
        "解析器認定的第一筆交易日"

    from tests.unit._srcscan import code_only, func_body, repo_src
    body = code_only(func_body(repo_src("routers/api_finance_stmt.py"),
                               "async def _build_statement_preview("))
    assert "_balance_before(session, acct, res.rows[0].date)" in body, \
        "期初的基準日不是解析器認定的第一筆交易日"
    guard = code_only(func_body(repo_src("routers/api_finance_stmt.py"),
                                "async def _balance_before("))
    assert "_DATE" not in guard, \
        "_balance_before 又自己去文字裡撈日期了 —— 那會撈到表頭的查詢期間"


def test_the_fallback_really_recovers():
    """釘行為不只釘原始碼：故意給一個錯的期初餘額，第一次會失敗、退回後要成功。"""
    wrong = parse_statement(FIRST_BANK, opening_balance=999999)
    assert not wrong.ok, "給錯的期初餘額竟然沒被發現（那道驗證失效了）"
    back = parse_statement(FIRST_BANK)
    assert back.ok and len(back.rows) == 10, "退回之後還是解析不出來"
