# -*- coding: utf-8 -*-
"""轉到私帳的專案：CRM 明細列出 + 委外人員一鍵請款（owner 2026-08-28
「轉過來的 crm 明細要列出，委外人員的名單要可以請款（由私帳支付）」）。

一行成本只能請一次款 —— 靠 `crm_payment_requests.cost_line_id` 這條硬連結。
前端請完把按鈕換成「已請款」，但那擋不住雙擊／兩個分頁／重送，所以守衛在後端。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _fn(src: str, name: str) -> str:
    return src.split(f"async def {name}(")[1].split("\n@router")[0]


def test_claim_is_blocked_when_the_cost_line_was_already_claimed():
    """而且要擋在 `_sync_mine_project_outsource` 之前 —— 那支是**增量制**
    （委外費用 += 金額），先累加再拒絕的話，被擋下的那次也會留下痕跡。"""
    src = (ROOT / "routers/crm/finance.py").read_text(encoding="utf-8")
    fn = _fn(src, "create_payment")
    assert "cost_line_id" in fn and "409" in fn
    assert fn.index("cost_line_id") < fn.index("_sync_mine_project_outsource")


def test_people_list_excludes_admin_phase_and_zero_rows():
    """人員名單＝真的付給人的錢：`行政雜支` 那一相走 misc（不是人員），
    金額 0／未填的成本行不列（那是還沒發生的估算）。"""
    src = (ROOT / "routers/api_finance_projects.py").read_text(encoding="utf-8")
    fn = src.split("async def _crm_lines(")[1].split("\nasync def ")[0]
    assert 'CrmProjectCostLine.phase != "行政雜支"' in fn
    assert "actual_amount.isnot(None)" in fn and "actual_amount != 0" in fn
    assert "cost_line_id" in fn          # claimed 靠硬連結算，不靠人名＋金額目測


def test_claim_posts_into_the_private_ledger():
    """由**私帳**支付：entity=mine、專案外包。落到母公司就違反
    「私帳的專案不可能跟公司請款」（owner 2026-08-28）。"""
    src = (ROOT / "frontend/tabs/finance/subviews/projects.js").read_text(encoding="utf-8")
    fn = src.split("_fp.claimLine = async (")[1].split("\n_fp.")[0]
    assert "entity: 'mine'" in fn and "category: '專案外包'" in fn
    assert "cost_line_id: row.id" in fn


def test_one_click_claim_is_not_counted_twice():
    """🔴 甲案（相加制）之下的關鍵不變式：一鍵請款建的那張單**不可以**再累加
    進 outsource —— 它的錢已經由 apply_crm_costs 從 CRM 成本行算過一次。

    分辨方式就是 `cost_line_id`（手動加的委外沒有它，所以照舊累加）。
    2026-08-28 實測：CRM 成本行 12,000 ＋ 手動 5,000 ＝ 17,000，
    對那一行按下一鍵請款之後**仍是** 17,000（不是 29,000）。"""
    src = (ROOT / "routers/crm/finance.py").read_text(encoding="utf-8")
    helper = src.split("async def _sync_mine_project_outsource(")[1]
    sig, body = helper.split('"""')[0], helper.split('"""')[2]
    assert "cost_line_id" in sig, "簽章要收得到它"
    assert 'or cost_line_id:' in body.replace("'", '"'), "早退那一行要認它"
    # 兩個寫入端都要把它帶進去（漏一個，那條路就會補累加）
    for fn_name in ("create_payment", "update_payment"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "cost_line_id" in fn, fn_name
