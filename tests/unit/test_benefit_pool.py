# -*- coding: utf-8 -*-
"""福委會（docs/BENEFIT_POOL_PLAN.md）—— 純規則 + 端點的防回歸釘。

owner 2026-08-21 的原話：「我有幾個福利池，一個是快樂、一個是進修，這兩塊員工
都可以登記，他們登記後我審核通過，就進公司請款。我每年會撥一筆錢進這個池。」

守的是三句話：
  ① **待審不扣餘額**。待審就扣的話，退件之後餘額要回沖 —— 那是一種很容易對不
     起來的帳。畫面另外顯示「審核中」金額就夠了。
  ② **超支要看得見**。餘額可以是負的；夾成 0 等於把超編藏起來。
  ③ **自己的登記靠 scope 擋，不是靠抹鍵**。`/benefits/me` 一族一律
     `WHERE staff_id = 我`，而 staff_id 只從 token 解 —— 少一個條件，
     任何人都能改別人的登記。

外加金流管線的釘：核准要冪等、退回要撤掉幽靈負債、已付款不准撤、匯款不得重複
記帳 —— 這四條都是抄零用金的教訓（docs/PETTY_CASH_PLAN.md），不可以在福委會這條
路上重犯。
"""
import pytest

from core.hr_logic import (BENEFIT_COMMITTED, BENEFIT_EDITABLE,
                           BENEFIT_STATUSES, benefit_can_edit,
                           benefit_pool_balance, validate_benefit_entry)
from tests.unit._srcscan import code_only, func_body, repo_src

SRC = "routers/crm/benefits.py"


def _body(fn):
    return code_only(func_body(repo_src(SRC), fn))


# ── ① 餘額 ────────────────────────────────────────────────────────

def test_balance_is_fundings_minus_committed():
    """餘額 ＝ 歷年撥款 − 已核准 − 已付款。"""
    r = benefit_pool_balance([20000, 20000, 19892],
                             [("已核准", 4130), ("已付款", 890)])
    assert r["funded"] == 59892
    assert r["used"] == 5020
    assert r["balance"] == 54872


def test_pending_is_reported_separately_not_deducted():
    """🔴 審核中的金額要看得到，但不能進餘額 —— 退件後就不用回沖。"""
    r = benefit_pool_balance([20000], [("待審", 3000)])
    assert r["pending"] == 3000
    assert r["used"] == 0 and r["balance"] == 20000


@pytest.mark.parametrize("status", ["草稿", "退回", "待審"])
def test_uncommitted_states_never_eat_the_budget(status):
    r = benefit_pool_balance([1000], [(status, 999)])
    assert r["used"] == 0 and r["balance"] == 1000


def test_overspend_shows_as_negative_not_clamped():
    """🔴 花超了要看得見。夾成 0 等於把問題藏起來。"""
    r = benefit_pool_balance([10000], [("已付款", 15000)])
    assert r["balance"] == -5000
    assert r["over"] is True


def test_empty_pool_is_not_over():
    assert benefit_pool_balance([], []) == {
        "funded": 0, "used": 0, "pending": 0, "balance": 0, "over": False}


def test_none_amounts_do_not_crash():
    """人手輸入的資料會有 None —— 當 0，不要爆。"""
    r = benefit_pool_balance([None, 500], [("已核准", None), ("已付款", 200)])
    assert r["funded"] == 500 and r["used"] == 200 and r["balance"] == 300


def test_many_years_of_funding_accumulate():
    """池是跨年度滾動的 —— 每年撥一筆，累加起來。"""
    r = benefit_pool_balance([20000] * 4, [])
    assert r["funded"] == 80000 and r["balance"] == 80000


# ── ② 欄位檢查與狀態 ──────────────────────────────────────────────

def test_free_text_title_is_accepted():
    """項目是自由文字（電影名／餐廳／課程名）—— 不做枚舉。
    分類這件事由**池**承擔（快樂／進修），再加一層只是逼人每次多選一格。"""
    for title in ("奧德賽", "史密斯華倫斯基牛排館", "ComFy UI課程", "侯布雄"):
        assert validate_benefit_entry(title, 300) == ""


@pytest.mark.parametrize("title", ["", "   ", None])
def test_blank_title_is_rejected(title):
    assert "項目" in validate_benefit_entry(title, 300)


@pytest.mark.parametrize("amount", [0, -1, None])
def test_non_positive_amount_is_rejected(amount):
    """金額 0 或負數不是一筆登記。要沖銷走沖銷，不要用負數硬做。"""
    assert "金額" in validate_benefit_entry("奧德賽", amount)


def test_status_vocabulary():
    """登記即待審 —— owner 的流程就是「登記後我審核通過」，
    中間再插一個草稿階段只是逼人多按一次送出。"""
    assert BENEFIT_STATUSES == ("待審", "已核准", "已付款", "退回")
    assert set(BENEFIT_EDITABLE) == {"待審", "退回"}
    assert set(BENEFIT_COMMITTED) == {"已核准", "已付款"}


@pytest.mark.parametrize("status,editable", [
    ("待審", True), ("退回", True), ("已核准", False), ("已付款", False)])
def test_who_can_still_edit(status, editable):
    assert benefit_can_edit(status) is editable


# ── ③ own-scope：自己的登記靠查詢條件擋 ──────────────────────────

@pytest.mark.parametrize("fn", ["async def my_benefits(",
                                "async def add_my_entry(",
                                "async def update_my_entry(",
                                "async def delete_my_entry("])
def test_self_service_resolves_staff_from_token(fn):
    """🔴 staff_id 只從 token 解，永不收 client 傳的值。"""
    body = _body(fn)
    assert "_my_staff(request)" in body, f"{fn} 沒有從 token 解身分"
    assert "body.staff_id" not in body, f"{fn} 竟然吃 client 傳的 staff_id"


@pytest.mark.parametrize("fn", ["async def update_my_entry(",
                                "async def delete_my_entry("])
def test_self_service_checks_ownership(fn):
    """🔴 少了這個條件，任何人都能改／刪別人的登記。"""
    assert "e.staff_id != staff.id" in _body(fn), f"{fn} 沒檢查是不是自己的"


def test_self_service_only_touches_editable_rows():
    """已核准的登記進了公司請款，本人不該還能改金額。"""
    for fn in ("async def update_my_entry(", "async def delete_my_entry("):
        assert "BENEFIT_EDITABLE" in _body(fn), f"{fn} 沒擋已核准/已付款"


def test_my_list_filters_by_my_staff_id():
    """看得到的都是自己的 —— 這是查詢的性質，不靠抹除層。"""
    body = _body("async def my_benefits(")
    assert "HrBenefitEntry.staff_id == staff.id" in body


def test_rejected_entry_returns_to_pending_after_edit():
    """退回後改完自動重新送審 —— 不然員工改了還要再找一顆送出鈕。"""
    assert 'e.status = "待審"' in _body("async def update_my_entry(")


# ── ④ 金流管線的釘（抄零用金的教訓，不可重犯）────────────────────

def test_approve_is_idempotent():
    """🔴 重按核准不可以變兩張應付款。"""
    assert "if not e.payment_request_id:" in _body("async def approve_entry("), \
        "核准沒有冪等保護"


def test_approve_reuses_the_existing_money_pipeline():
    """核准＝進公司請款：產 CrmPaymentRequest 進應付帳款，不自己造一條金流。"""
    body = _body("async def approve_entry(")
    assert "CrmPaymentRequest(" in body
    assert 'payment_status="應付款"' in body


def test_reject_clears_the_unpaid_payable():
    """🔴 幽靈負債：退回不撤應付款的話，登記回到本人手上、帳上還掛著那筆錢，
    月結與現金流預測都會多算（零用金 _clear_aps 的同一條教訓）。"""
    assert "session.delete(ap)" in _body("async def reject_entry("), \
        "退回沒有撤掉應付款"


def test_reject_refuses_when_already_paid():
    """已付款的不准撤 —— 那筆錢真的出去了，要沖銷不是抹歷史。"""
    body = _body("async def reject_entry(")
    assert 'ap.payment_status == "已付款"' in body and "409" in body


def test_pay_does_not_double_post():
    """🔴 重按匯款不可以重複記帳（財務端最不能出的錯）。"""
    body = _body("async def pay_entry(")
    assert "payment_request_id == ap.id" in body and "if not posted:" in body


def test_pay_respects_the_month_lock():
    """現金側落在匯款日 —— 那個月結了就要擋（F1 月結鎖帳）。"""
    assert "_assert_month_open" in _body("async def pay_entry(")


def test_pool_update_cannot_switch_ledger():
    """🔴 換帳本＝把整池的錢搬到另一本帳（LEDGER_ENTITY_PLAN 鐵則）。"""
    body = _body("async def update_pool(")
    assert "不可變更福利池所屬帳本" in body and "422" in body


def test_pool_with_money_cannot_be_deleted():
    """有撥款或登記的池刪掉＝那些錢的歸屬消失，帳上留下孤兒。"""
    assert "409" in _body("async def delete_pool(")


def test_funding_must_be_positive():
    """撥款是放錢進池 —— 負數撥款只會讓餘額變成一筆看不懂的帳。"""
    assert "422" in _body("async def add_funding(")


def test_accounting_package_only_ships_real_bookings():
    """待審與退回還不是帳，送去會計只會讓人對不起來。"""
    assert "BENEFIT_COMMITTED" in _body("async def _package(")


def test_every_admin_endpoint_is_full_level():
    """福委會的管理端是記帳寫入面，不是報表 —— 合夥人（唯讀）不該摸得到。"""
    src = repo_src(SRC)
    assert src.count('level="full"') >= 10
    assert 'level="view"' not in src


def test_approve_reject_pay_need_the_approver_module():
    """審的是別人的錢 —— money_view 之外還要 finance_approve（同零用金）。"""
    for fn in ("async def approve_entry(", "async def reject_entry(",
               "async def pay_entry("):
        assert "_check_approver(request)" in _body(fn), f"{fn} 少了審核守衛"


def test_self_service_is_not_behind_money_dep():
    """🔴 自己的錢不受 money_view 管（PETTY_CASH_PLAN §4 的同一條）。
    給 /benefits/me 掛 money_dep ＝ 沒有金額權的員工連自己登記了什麼都看不到。"""
    src = repo_src(SRC)
    i = src.index('@router.get("/benefits/me")')
    j = src.index("# ── 管理端", i)
    assert "money_dep" not in src[i:j], "自助端點被掛上 money_dep 了"
