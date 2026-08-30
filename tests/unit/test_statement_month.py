# -*- coding: utf-8 -*-
"""對帳單明細的月份與去重不變式（掃描式，不碰 DB）。

owner 2026-08-20：一份跨月的對帳單不該逼人切成三次傳。
"""
from tests.unit._srcscan import code_only, flow_body, repo_src  # noqa: E402

# 🔴 跨兩個檔案：對帳工作台（add_statement_lines）留在 api_finance，
# 對帳單匯入（apply_bank_statement）2026-08-21 搬去 api_finance_stmt。
SRC = 'routers/api_finance.py'
STMT = 'routers/api_finance_stmt.py'


def _body(fn, rel=SRC):
    return code_only(flow_body(repo_src(rel), fn))


def test_month_is_derived_per_row_not_taken_from_payload():
    """每一列自己的 line_date 決定它屬於哪個月。

    payload.month 只剩「該列沒有日期」時的後備 —— 整批一個月的舊寫法會把
    12 月的交易記進 10 月，而工作台是按月看的，那筆就從此消失在視野外。
    """
    body = _body('async def add_statement_lines(')
    assert 'local_day(d).strftime("%Y-%m")' in body, '月份不是逐列由日期算的'
    assert 'fallback' in body, '沒有保留「沒日期才用 payload.month」的後備'


def test_replace_only_clears_months_actually_covered():
    """🔴 replace 用 month.in_(months)，不可以改成日期區間。

    按區間清的話，檔案裡剛好沒有交易的那個月會被連坐清空 —— 那個月已經
    勾銷好的紀錄就沒了（2026-08-20 實測：10 月與 12 月的檔案不可以動到 11 月）。
    """
    body = _body('async def add_statement_lines(')
    assert 'BankStatementLine.month.in_(months)' in body, \
        'replace 的範圍不是「這批涵蓋到的月份」'
    assert 'line_date >=' not in body and 'line_date <=' not in body, \
        'replace 改用日期區間了 —— 會連坐清掉中間沒交易的月份'


def test_default_is_append_with_dedup_not_overwrite():
    """預設補進來、重複跳過 —— 這支的情境是「每個月固定丟一次」，區間重疊很正常。

    每次都新增一份重複的話，工作台會愈長愈髒，而且已經勾銷好的那些會多出一個
    未配對的分身。
    """
    body = _body('async def add_statement_lines(')
    assert 'skipped' in body and 'existing' in body, '沒有去重'
    assert 'if payload.replace:' in body, '去重與覆蓋沒有分開'


def test_replace_reports_how_many_matched_rows_it_destroyed():
    """覆蓋會清掉已勾銷的紀錄 —— 幾筆一定要講出來，不能靜靜發生。"""
    body = _body('async def add_statement_lines(')
    assert 'dropped_matched' in body, '覆蓋掉已配對的列沒有回報'


def test_apply_path_also_writes_per_row_month():
    """/bank-statement/apply 那條也是逐列算月（兩條寫入路徑不能一邊對一邊錯）。"""
    body = _body('async def apply_bank_statement(', STMT)
    assert 'month=r.date[:7]' in body, 'apply 寫 BankStatementLine 時月份不是逐列的'
