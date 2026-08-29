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


def test_misc_rows_are_claimable_one_by_one():
    """行政雜支也能逐項請款（owner 2026-08-29）。來源是另一張表
    （crm_project_expenses），所以硬連結另開一欄 `expense_id` ——
    兩條連結**同一條重複守衛**，各寫一次必漏一個。"""
    src = (ROOT / "routers/crm/finance.py").read_text(encoding="utf-8")
    fn = _fn(src, "create_payment")
    assert "CrmPaymentRequest.expense_id, req.expense_id" in fn
    assert "for _col, _val in (" in fn, "兩條連結走同一個迴圈，不是各寫一份"
    api = (ROOT / "routers/api_finance_projects.py").read_text(encoding="utf-8")
    lines = api.split("async def _crm_lines(")[1].split("async def _rollups")[0]
    assert '"claimed": e.id in claimed' in lines
    assert "CrmPaymentRequest.expense_id" in lines, "已請款要一起撈 expense_id"


def test_company_billed_rows_cannot_be_claimed_again():
    """🔴 已經跟公司請過款的雜支**不給按** —— 那筆錢公司出了，
    私帳再請一次就是同一筆錢請兩次（畫面顯示「公司出」）。"""
    js = (ROOT / "frontend/tabs/finance/subviews/projects.js").read_text(encoding="utf-8")
    seg = js.split("const miscRows =")[1].split("').join('')")[0]
    assert "x.billed_to_company" in seg and "公司出" in seg
    assert seg.index("x.billed_to_company") < seg.index("claimMisc"),         "公司出的判斷要在按鈕之前 —— 否則會先畫出按鈕"


def test_misc_claim_uses_a_category_that_does_not_double_count():
    """🔴 類別用「專案雜支」：它對映的科目正好是 `apply_ledger_project_costs`
    會拿掉的那條現金鏡射（_PROJECT_CASH_MIRROR_LABEL），所以不會跟逐案的
    雜支重複計。換成別的類別（例如「行政」）就會兩邊都算。"""
    js = (ROOT / "frontend/tabs/finance/subviews/projects.js").read_text(encoding="utf-8")
    fn = js.split("_fp.claimMisc = async (")[1].split("_fp.outsourceForm")[0]
    assert "category: '專案雜支'" in fn and "expense_id: row.id" in fn
    from core.finance_logic import _PROJECT_CASH_MIRROR_LABEL
    assert _PROJECT_CASH_MIRROR_LABEL == "專案雜支"
