# -*- coding: utf-8 -*-
"""專案詳情的「應付／請款單」列要有請款日與付款日（owner 2026-09-04「新增付款與請款日期」）。"""
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src


def test_project_ledger_detail_carries_both_dates_and_the_row_shows_them():
    py = repo_src("routers/api_finance_projects.py")
    i = py.index('"payments": [{'); block = py[i:py.index("} for x in pays]", i)]
    assert '"request_date": _fmt_day(x.request_date)' in block and '"payment_date": _fmt_day(x.payment_date)' in block
    js = js_code_only(repo_src("frontend/tabs/finance/subviews/projects.js"))
    row = js[js.index("(d.payments || []).map((x) =>"):js.index("</tbody></table>", js.index("(d.payments || []).map((x) =>"))]
    assert "x.request_date" in row and "x.payment_date" in row
    assert 'colspan="5"' in row, "多了一欄，空列的 colspan 要跟著"
