# -*- coding: utf-8 -*-
"""對帳單匯入的兩道（owner 2026-08-24 各踩了一次）：

① 逗號分隔的匯出要解析得出來 —— 一銀網銀下載的 .txt 是 CSV，
   上傳回「找不到任何交易列」。
② 對帳單的帳號跟選的帳戶對不上 → 擋下來並說出它其實是誰的。
   owner 拿**合庫**的對帳單、下拉選了第一銀行；那次是解析失敗才沒寫進去 ——
   純屬僥倖。匯錯帳戶會安靜地讓兩個帳戶的餘額同時錯掉。
"""
from core.bank_statement import (extract_account_no, normalize_delimited,
                                 parse_statement, same_account_no)

# 一銀網銀匯出（CSV，空欄位印 `-`）—— 連續不跳號，餘額鏈才驗得過
FIRST_BANK_CSV = """交易日期,交易時間,幣別,支出金額,存入金額,餘額,票據號碼,摘要,附註
2025/05/12,15:50:44,新臺幣,-,2000.00,2000.00,,跨行轉帳,0120082120000062728
2025/05/12,16:11:57,新臺幣,1000.00,-,1000.00,,硬體轉出,0070000000000000000
2025/05/14,21:33:51,新臺幣,-,29000.00,30000.00,,跨行轉帳,0120082120000062728
2025/05/15,11:00:38,新臺幣,3075.00,-,26925.00,,信保基金,
"""

# 合庫（空欄直接消失、空白分隔），表頭有帳號
COOP = """歷史交易查詢
類別：TWD_CURRENT 戶名：90371657 源日有限公司 帳號：0070717559515
序號 交易日期 摘要 交易行庫 幣別 提款金額 存款金額 餘額 備註
1 2025/01/08 01:56:40 攤還本息 合庫松山 TWD 4470.00 170538.00 01-08 315614
2 2025/01/08 01:56:40 攤還本息 合庫松山 TWD 25290.00 145248.00 01-08 315611
"""


# ── ① 逗號分隔 ────────────────────────────────────────────────

def test_a_comma_separated_export_parses():
    """🔴 `_AMOUNT` 兩側的看守（不可前接/後接逗號）是為了不切壞千分位，
    但 CSV 裡金額**前後都是逗號**（`,2000.00,`）→ 兩個看守同時失敗 →
    整行一個金額都抓不到 → 那一份「找不到任何交易列」。"""
    r = parse_statement(FIRST_BANK_CSV)
    assert r.ok, r.errors
    assert len(r.rows) == 4, f"只解析出 {len(r.rows)} 列"
    assert [abs(x.amount) for x in r.rows] == [2000, 1000, 29000, 3075]
    # 第一列沒有前一列的餘額可減、這份也沒印總計 → 方向是推的。
    # 系統對這種列的處理是**標記 inferred 且預覽預設不勾選**，等人確認 ——
    # 這是刻意的誠實，不是 bug。後面幾列有餘額鏈可驗，方向就是確定的。
    assert r.rows[0].inferred is True, "推出來的方向沒有被標記"
    assert [x.amount for x in r.rows[1:]] == [-1000, 29000, -3075]
    assert all(not x.inferred for x in r.rows[1:]), "有餘額鏈可驗的列不該標 inferred"


def test_thousand_separators_survive_the_csv_split():
    """🔴 不能用 `replace(",", " ")`：帶千分位的金額在 CSV 裡是被引號包起來的，
    直接取代會把 "35,000.00" 切成兩半，一筆三萬五變成三十五。"""
    csv_txt = ('交易日期,幣別,支出金額,存入金額,餘額,摘要\n'
               '2025/05/12,新臺幣,-,"35,000.00","35,000.00",跨行轉帳\n'
               '2025/05/13,新臺幣,"1,000.00",-,"34,000.00",手續費\n')
    r = parse_statement(csv_txt)
    assert r.ok, r.errors
    # 只看金額大小 —— 第一列的方向本來就是推的（見上一條測試的說明）
    assert [abs(x.amount) for x in r.rows] == [35000, 1000], \
        "千分位被切壞了（35,000 應該是三萬五不是三十五）"
    assert r.rows[1].amount == -1000, "有餘額鏈可驗的那列方向錯了"


def test_a_space_separated_statement_is_left_alone():
    """空白分隔的版面**不可以**被動到 —— 備註裡帶一兩個逗號不算 CSV。"""
    assert normalize_delimited(COOP) == COOP
    r = parse_statement(COOP)
    assert r.ok and len(r.rows) == 2


# ── ② 帳號防呆 ────────────────────────────────────────────────

def test_the_account_number_is_read_from_the_header():
    assert extract_account_no(COOP) == "0070717559515"


def test_a_statement_without_an_account_number_returns_none():
    """一銀的 CSV 匯出沒印帳號 —— 抓不到就是抓不到，不能硬猜一個出來擋人。"""
    assert extract_account_no(FIRST_BANK_CSV) is None


def test_short_digit_runs_are_not_mistaken_for_an_account():
    """分行代號、票號那種短數字不能被當成帳號 —— 擋錯人比不擋更糟。"""
    assert extract_account_no("帳號：12345") is None


def test_separators_do_not_matter():
    """各家印法不同（有的加 - 或空白），數字序列一樣就是同一個帳號。"""
    assert extract_account_no("帳號: 007-071-755-9515") == "0070717559515"
    assert same_account_no("0070717559515", "007-071-755-9515")
    assert same_account_no("13310019211", "13310019211")


def test_different_accounts_do_not_match():
    assert not same_account_no("0070717559515", "13310019211")


def test_a_missing_side_never_counts_as_a_match():
    """🔴 兩邊都要有值才比得了。空字串當成「相同」的話，沒登記帳號的帳戶
    會變成什麼對帳單都收 —— 那就等於沒有這道防呆。"""
    assert not same_account_no("", "13310019211")
    assert not same_account_no("0070717559515", "")
    assert not same_account_no(None, None)


def test_the_guard_only_blocks_when_it_can_actually_verify():
    """釘住那三個放行條件（抓不到帳號／帳戶沒登記／對得上）——
    驗不了就放行，不能因為驗不了而擋人做事。"""
    from tests.unit._srcscan import code_only, func_body, repo_src
    body = code_only(func_body(repo_src("routers/api_finance_stmt.py"),
                               "async def _assert_statement_belongs_to("))
    assert "if not found:" in body, "抓不到帳號時沒放行"
    assert "same_account_no(found, acct.account_no)" in body, "沒有比對"
    assert "if not (acct.account_no or \"\").strip():" in body, \
        "帳戶沒登記帳號時沒放行"
    assert "raise HTTPException" in body, "對不上卻沒擋"


def test_the_block_says_whose_statement_it_actually_is():
    """只說「不對」沒有用 —— 要講出它其實是哪個帳戶的，人才知道要改選什麼。"""
    from tests.unit._srcscan import repo_src
    src = repo_src("routers/api_finance_stmt.py")
    i = src.index("async def _assert_statement_belongs_to(")
    seg = src[i:i + 2600]
    assert "owner_acct" in seg, "沒有去找它其實屬於哪個帳戶"
    assert "這份對帳單的帳號是" in seg and "但你選的是" in seg, "訊息沒講清楚兩邊"
