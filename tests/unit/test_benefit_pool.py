# -*- coding: utf-8 -*-
"""福利池（docs/BENEFIT_POOL_PLAN.md）—— 純規則 + 端點的防回歸釘。

守的是三句話：
  ① **待審不扣餘額**。待審就扣的話，退件之後餘額要回沖 —— 那是一種很容易對不
     起來的帳。畫面另外顯示「審核中」金額就夠了。
  ② **超支要看得見**。餘額可以是負的；夾成 0 等於把超編藏起來。
  ③ **會計那兩區的分法只看 taxable**。併入個人所得的按人彙總（年底開扣繳憑單），
     不併的按項目彙總（會計要的是科目）。混成一張總表兩邊都不能用。

外加金流管線的釘：核准要冪等、退回要撤掉幽靈負債、已付款不准撤、匯款不得重複
記帳 —— 這四條都是抄零用金的教訓（docs/PETTY_CASH_PLAN.md），不可以在福利這條
路上重犯。
"""
import pytest

from core.hr_logic import (BENEFIT_CATEGORIES, BENEFIT_COMMITTED,
                           BENEFIT_EDITABLE, BENEFIT_KINDS, BENEFIT_LOCKED,
                           BENEFIT_STATUSES, benefit_accounting_split,
                           benefit_pool_balance, validate_benefit_grant)
from tests.unit._srcscan import code_only, func_body, repo_src

SRC = "routers/crm/benefits.py"


def _body(fn):
    return code_only(func_body(repo_src(SRC), fn))


# ── ① 餘額 ────────────────────────────────────────────────────────

def test_only_committed_states_eat_the_budget():
    """🔴 已核准＋已付款才扣餘額；待審／草稿／退回都不扣。"""
    r = benefit_pool_balance(300000, [
        ("已核准", 50000), ("已付款", 37400),
        ("待審", 12000), ("草稿", 9999), ("退回", 8888)])
    assert r["used"] == 87400
    assert r["balance"] == 212600


def test_pending_is_reported_separately_not_deducted():
    """審核中的金額要看得到，但不能進餘額 —— 退件後就不用回沖。"""
    r = benefit_pool_balance(100000, [("待審", 30000)])
    assert r["pending"] == 30000
    assert r["used"] == 0 and r["balance"] == 100000


def test_overspend_shows_as_negative_not_clamped():
    """🔴 超支要看得見。夾成 0 等於把問題藏起來。"""
    r = benefit_pool_balance(10000, [("已付款", 15000)])
    assert r["balance"] == -5000
    assert r["over"] is True


def test_empty_pool_is_not_over():
    r = benefit_pool_balance(0, [])
    assert r == {"budget": 0, "used": 0, "pending": 0,
                 "balance": 0, "over": False}


def test_none_amounts_do_not_crash():
    """人手輸入的資料會有 None —— 當 0，不要爆。"""
    r = benefit_pool_balance(None, [("已核准", None), ("已付款", 500)])
    assert r["used"] == 500 and r["balance"] == -500


# ── ② 欄位檢查 ────────────────────────────────────────────────────

@pytest.mark.parametrize("cat", BENEFIT_CATEGORIES)
def test_every_listed_category_passes(cat):
    assert validate_benefit_grant(cat, "給付", 1000, 0) == ""


def test_unknown_category_is_rejected():
    assert "福利項目" in validate_benefit_grant("加薪", "給付", 1000, 0)


def test_unknown_kind_is_rejected():
    assert validate_benefit_grant("生日禮金", "空投", 1000, 0) != ""


@pytest.mark.parametrize("amount", [0, -1, None])
def test_non_positive_amount_is_rejected(amount):
    """金額 0 或負數不是福利。要沖銷走沖銷，不要用負數的動支硬做。"""
    assert "金額" in validate_benefit_grant("生日禮金", "給付", amount, 0)


def test_taxable_must_be_zero_or_one():
    assert validate_benefit_grant("生日禮金", "給付", 100, 2) != ""


def test_both_kinds_exist_because_benefits_have_two_shapes():
    """給付＝公司直接發（沒收據）、核銷＝員工先墊。少一個就有一半的福利登記不了。"""
    assert set(BENEFIT_KINDS) == {"給付", "核銷"}


def test_status_vocabulary_matches_petty_cash():
    """狀態字刻意與零用金同一組 —— 同一個心智模型，UI 文案/顏色才能沿用。"""
    assert BENEFIT_STATUSES == ("草稿", "待審", "已核准", "已付款", "退回")
    assert set(BENEFIT_EDITABLE) == {"草稿", "退回"}
    assert set(BENEFIT_COMMITTED) == {"已核准", "已付款"}
    assert set(BENEFIT_LOCKED) == {"已核准", "已付款"}


# ── ③ 會計分區 ────────────────────────────────────────────────────

def _g(taxable, amount, staff_id, name, cat):
    return {"taxable": taxable, "amount": amount, "staff_id": staff_id,
            "staff_name": name, "category": cat}


def test_split_is_driven_only_by_taxable():
    d = benefit_accounting_split([
        _g(1, 2000, "s1", "甲", "生日禮金"),
        _g(1, 6000, "s1", "甲", "三節獎金"),
        _g(0, 8000, "s2", "乙", "健康檢查"),
    ])
    assert d["personal_income"]["total"] == 8000
    assert d["company_expense"]["total"] == 8000


def test_personal_income_is_summed_per_person():
    """🔴 併入所得那區按**人**彙總 —— 扣繳憑單是一人一張。"""
    d = benefit_accounting_split([
        _g(1, 2000, "s1", "甲", "生日禮金"),
        _g(1, 6000, "s1", "甲", "三節獎金"),
        _g(1, 1000, "s2", "乙", "生日禮金"),
    ])
    rows = {r["staff_name"]: r for r in d["personal_income"]["summary"]}
    assert rows["甲"]["total"] == 8000 and rows["甲"]["count"] == 2
    assert rows["乙"]["total"] == 1000


def test_company_expense_is_summed_per_category():
    """公司費用那區按**項目**彙總 —— 會計要的是科目，不是誰花的。"""
    d = benefit_accounting_split([
        _g(0, 8000, "s1", "甲", "健康檢查"),
        _g(0, 5000, "s2", "乙", "健康檢查"),
        _g(0, 3000, "s3", "丙", "員工旅遊"),
    ])
    rows = {r["category"]: r for r in d["company_expense"]["summary"]}
    assert rows["健康檢查"]["total"] == 13000 and rows["健康檢查"]["count"] == 2
    assert rows["員工旅遊"]["total"] == 3000


def test_summaries_are_sorted_by_amount_desc():
    """金額大的在上面 —— 會計先看大的。"""
    d = benefit_accounting_split([
        _g(1, 100, "s1", "小", "生日禮金"), _g(1, 9000, "s2", "大", "三節獎金")])
    assert [r["staff_name"] for r in d["personal_income"]["summary"]] == ["大", "小"]


def test_rows_are_carried_through_not_just_totals():
    """彙總之外一定要有逐筆 —— 會計對不上時要能追到那一筆。"""
    d = benefit_accounting_split([_g(1, 2000, "s1", "甲", "生日禮金")])
    assert len(d["personal_income"]["rows"]) == 1
    assert d["personal_income"]["rows"][0]["amount"] == 2000


def test_split_of_nothing_is_empty_not_an_error():
    d = benefit_accounting_split([])
    assert d["personal_income"]["total"] == 0
    assert d["company_expense"]["summary"] == []


# ── ④ 金流管線的釘（抄零用金的教訓，不可重犯）────────────────────

def test_approve_is_idempotent():
    """🔴 重按核准不可以變兩張應付款。"""
    body = _body("async def approve_grant(")
    assert "if not g.payment_request_id:" in body, "核准沒有冪等保護"


def test_approve_reuses_the_existing_money_pipeline():
    """核准要產 CrmPaymentRequest 進應付帳款 —— 不自己造一條金流。"""
    body = _body("async def approve_grant(")
    assert "CrmPaymentRequest(" in body
    assert 'payment_status="應付款"' in body


def test_reject_clears_the_unpaid_payable():
    """🔴 幽靈負債：退回不撤應付款的話，動支回到草稿、帳上還掛著那筆錢，
    月結與現金流預測都會多算（零用金 _clear_aps 的同一條教訓）。"""
    body = _body("async def reject_grant(")
    assert "session.delete(ap)" in body, "退回沒有撤掉應付款"


def test_reject_refuses_when_already_paid():
    """已付款的不准撤 —— 那筆錢真的出去了，要沖銷不是抹歷史。"""
    body = _body("async def reject_grant(")
    assert 'ap.payment_status == "已付款"' in body and "409" in body


def test_pay_does_not_double_post():
    """🔴 重按匯款不可以重複記帳（財務端最不能出的錯）。"""
    body = _body("async def pay_grant(")
    assert "payment_request_id == ap.id" in body and "if not posted:" in body


def test_pay_respects_the_month_lock():
    """現金側落在匯款日 —— 那個月結了就要擋（F1 月結鎖帳）。"""
    body = _body("async def pay_grant(")
    assert "_assert_month_open" in body


def test_locked_grants_cannot_be_edited_or_deleted():
    """已核准／已付款時應付帳款已經掛著一張單，這裡改金額兩邊會對不起來。"""
    for fn in ("async def update_grant(", "async def delete_grant("):
        assert "BENEFIT_LOCKED" in _body(fn), f"{fn} 沒擋鎖定狀態"


def test_pool_update_cannot_switch_ledger():
    """🔴 換帳本＝把整池的錢搬到另一本帳（LEDGER_ENTITY_PLAN 鐵則）。"""
    body = _body("async def update_pool(")
    assert "不可變更福利池所屬帳本" in body and "422" in body


def test_pool_with_grants_cannot_be_deleted():
    """有動支的池刪掉＝那些錢的歸屬消失，帳上留下孤兒。"""
    body = _body("async def delete_pool(")
    assert "409" in body


def test_accounting_package_only_ships_real_bookings():
    """草稿與待審還不是帳，送去會計只會讓人對不起來。"""
    body = _body("async def _package(")
    assert '"已核准", "已付款"' in body


def test_id_number_only_appears_in_the_accounting_package():
    """PII 單一正本：身分證只在交付包（扣繳憑單要）從 crm_staff 帶，
    清單端點不帶。_grant_dict 出現 id_number ＝ 每個列表都在外洩身分證。"""
    assert "id_number" not in _body("def _grant_dict(")
    assert "id_number" in _body("async def _package(")


def test_every_write_endpoint_is_full_level():
    """福利是記帳寫入面，不是報表 —— 合夥人（唯讀）不該摸得到。"""
    src = repo_src(SRC)
    assert src.count('level="full"') >= 10
    assert 'level="view"' not in src


def test_approve_and_pay_need_the_approver_module():
    """審的是別人的錢 —— money_view 之外還要 finance_approve（同零用金）。"""
    for fn in ("async def approve_grant(", "async def reject_grant(",
               "async def pay_grant("):
        assert "_check_approver(request)" in _body(fn), f"{fn} 少了審核守衛"
