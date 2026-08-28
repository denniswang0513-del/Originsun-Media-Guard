# -*- coding: utf-8 -*-
"""owner 2026-08-28：「已經轉到私帳的專案 不能列入母公司的成本」。

漏洞的形狀：`crm_project_expenses` / `crm_project_cost_lines` **沒有 entity 欄**，
只有 project_id —— 專案搬進私帳時它們沒跟著搬，於是還留在母公司的認列路徑上。
述詞正本 `core.ledger.not_mine_project`（比 `not_mine` 多一層：帳本由專案說了算）。

2026-08-28 dev 實測那條路：預支款不掛專案（給人的週轉金）→ 專案沒有阻擋列 →
推得動 → 推之前母公司 2026-08 損益表有那筆 9,000（預支核銷）、推之後歸零。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_null_project_id_is_kept():
    """🔴 `project_id IS NULL` 要放行。NOT IN 遇到 NULL 整條述詞是 NULL（不是
    TRUE）—— 少了這一層，「沒掛專案的雜支」會被整批篩掉。生產 496 筆雜支裡有
    419 筆是那一類（零用金的一般花費），全部會從清冊上消失而且沒人看得出來。"""
    src = _read("core/ledger.py")
    fn = src.split("def not_mine_project(")[1].split("\n\n\n")[0]
    assert "project_id.is_(None)" in fn and "or_(" in fn
    assert "notin_(" in fn


def test_parent_pnl_excludes_mine_project_expenses():
    """母公司損益表的「預支核銷」（營業成本-費）—— 兩支查詢都要擋。"""
    src = _read("services/finance_statements.py")
    fn = src.split("async def _advance_state(")[1].split("\ndef _resolve_baseline")[0]
    assert fn.count("not_mine_project(CrmProjectExpense)") == 2, \
        "核銷合計與明細列兩支查詢都要帶（少一支，數字與明細就對不起來）"


def test_advance_settlement_excludes_mine_project_expenses():
    """核銷＝「這筆預支變成公司的成本」，私帳專案的花費不能拿來核銷。"""
    src = _read("routers/crm/finance.py")
    fn = src.split("async def list_advances(")[1].split("\n@router")[0] \
        if "async def list_advances(" in src else src
    assert "_not_mine_project(CrmProjectExpense)" in fn


def test_petty_hides_mine_project_expenses():
    """零用金＝跟公司請款。登記時就擋了（_assert_not_mine_project），但
    **專案是後來才搬過去的**那條路擋不到 —— 那張單會躺在可請款清單裡，
    而且整批送出時被 409 卡住、其他幾張跟著送不出去。"""
    src = _read("routers/crm/petty.py")
    for fn_name in ("_petty_payload", "_submit_for"):
        fn = src.split(f"async def {fn_name}(")[1].split("\nasync def ")[0]
        assert "_not_mine_project(CrmProjectExpense)" in fn, fn_name
    assert src.count("_not_mine_project(CrmProjectExpense)") >= 3, "還有人員清冊那支"
