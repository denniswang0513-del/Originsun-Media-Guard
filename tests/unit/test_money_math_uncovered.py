# -*- coding: utf-8 -*-
"""錢的算法裡「沒有任何測試真的算過一次」的那批（2026-08-30 AST 盤點補上）。

為什麼要特地補：財務程式改壞了**不會噴錯**。它不丟例外、不出紅字，只是給你
一個看起來很合理的數字 —— 少一筆匯費、多算一個月折舊、逾期門檻從 60 天變成
59 天。人要對到 Excel 才會發現，而那通常是三個月以後。

這一批全是純函式（吃 dict/list、零 DB），一支測試三五行寫得完，所以沒有
「太麻煩所以不測」的藉口。斷言一律寫**意圖**（錯了會怎樣），不抄實作 ——
照實作抄的斷言只會把 bug 釘成規格。
"""
from datetime import datetime, timedelta, timezone

from core.finance_logic import (
    COST_GROUPS, INVOICE_REMITTED, ar_overdue_amount, apply_ledger_project_costs,
    bank_fee_total, depreciation_rows, expense_slot, in_amount, invoice_collected,
    local_day, map_account, map_info, normalize_invoice_status, out_amount,
    today_start,
)


# ── 收支明細的方向：哪些欄算流出、哪些算流入 ──────────────────────
# 錯了的後果：損益兩側同額歪掉，而三表照樣「平」（因為兩邊用同一支）。

def test_the_outflow_side_counts_expense_and_claim_but_never_the_bank_fee():
    """🔴 匯費不進損益流出 —— 它另列「營業費用-管理／銀行手續費」（bank_fee_total）。

    算進來就是重複計：同一筆匯費會在成本側出現一次、在管理費出現一次。
    """
    e = {"expense": 1000, "claim": 250, "bank_fee": 30, "deposit": 0}
    assert out_amount(e) == 1250


def test_the_inflow_side_is_the_deposit_column_only():
    assert in_amount({"deposit": 8000, "expense": 500}) == 8000


def test_both_sides_treat_none_and_empty_string_as_zero():
    """DB 撈出來的空欄是 None，CSV 匯入的空格是 ''。`int(None)` 會炸、
    `int('')` 也會炸 —— 兩者都得吃成 0，不然整份報表 500。"""
    assert out_amount({"expense": None, "claim": ""}) == 0
    assert in_amount({}) == 0


# ── 發票狀態的字：改名前後要判成同一件事 ──────────────────────────

def test_the_old_word_for_remitted_still_reads_as_remitted():
    """2026-08-21 把「已轉撥」改名成「已撥款」。舊備份還原、或還沒更新的
    客戶端送上來的都是舊字 —— 不歸一就變成一個系統不認得的狀態。"""
    assert normalize_invoice_status("已轉撥") == INVOICE_REMITTED
    assert normalize_invoice_status(INVOICE_REMITTED) == INVOICE_REMITTED
    assert normalize_invoice_status("未收款") == "未收款", "不認得的字要原樣放行"


def test_a_paid_date_alone_makes_an_invoice_collected():
    """有收款日 = 錢進來了，狀態欄還沒改也算。漏掉這條的後果：那張發票
    永遠掛在應收，帳齡報表天天叫它去催收一筆已經收到的錢。"""
    assert invoice_collected({"paid_date": datetime(2026, 5, 1), "payment_status": ""})
    assert invoice_collected({"payment_status": INVOICE_REMITTED})
    assert not invoice_collected({"payment_status": "未收款", "paid_date": None})


# ── 科目對映查值：舊格式（字串）與新格式（dict）都要吃 ────────────

def test_the_mapping_lookup_accepts_both_the_old_string_and_the_new_dict():
    """對映值歷史上是純字串 treatment，後來變成 {treatment, account_id}。
    只認 dict 的話，還沒遷移的那批列會整批查無對映 → 全掛「未歸類支出」。"""
    assert map_info({("cash", "交際"): "expense"}, "cash", "交際") == {
        "treatment": "expense", "account_id": None}
    new = {("cash", "交際"): {"treatment": "expense", "account_id": "6100"}}
    assert map_info(new, "cash", "交際")["account_id"] == "6100"


def test_a_missing_mapping_is_none_not_a_crash():
    """查無 = None（呼叫端據此走「未歸類」那條路）。cat_map 本身可能是 None
    （還沒載完 / 那本帳沒有任何對映）。"""
    assert map_info({}, "cash", "沒對映的類別") is None
    assert map_info(None, "cash", "x") is None
    assert map_info({("cash", "x"): "expense"}, "invoice", "x") is None, \
        "source 是鍵的一半 —— 只比類別文字會讓發票的規則套到收支上"


def test_the_account_lookup_walks_mapping_then_account_table():
    accounts = {"6100": {"name": "交際費", "pnl_group": "營業費用-管理"}}
    cat_map = {("cash", "交際"): {"treatment": "expense", "account_id": "6100"}}
    assert map_account(cat_map, accounts, "cash", "交際")["name"] == "交際費"
    # 對映指到一個已被刪掉的科目 → None，不是 KeyError
    assert map_account({("cash", "交際"): {"account_id": "9999"}},
                       accounts, "cash", "交際") is None


# ── 費用歸位：對映壞掉時要「誠實掛未歸類」，不是靜靜丟掉 ──────────

def test_an_unmapped_expense_lands_in_a_visible_bucket_not_in_the_void():
    """🔴 這是整套引擎的誠實性支點：沒對映的錢**還是要出現在報表上**
    （營業費用-管理／未歸類支出），並被 statement_warnings 數出來。
    悄悄丟掉的話，損益會憑空好看，而且沒有任何跡象。"""
    group, label = expense_slot(None)
    assert group == "營業費用-管理" and label == "未歸類支出"
    # 誤映到資產科目（pnl_group 是 None）也走同一條路
    assert expense_slot({"name": "銀行存款", "pnl_group": None})[1] == "未歸類支出"


def test_a_properly_mapped_expense_keeps_its_own_group_and_name():
    acct = {"name": "外包費", "pnl_group": COST_GROUPS[1]}
    assert expense_slot(acct) == (COST_GROUPS[1], "外包費")


# ── 匯費合計：只算期間內的 ────────────────────────────────────────

def test_the_bank_fee_total_only_counts_months_inside_the_period():
    """漏掉月份過濾＝把歷年匯費全部塞進這一期的管理費。"""
    rows = [{"entry_date": datetime(2026, 3, 5), "bank_fee": 30},
            {"entry_date": datetime(2026, 4, 5), "bank_fee": 15},
            {"entry_date": datetime(2025, 3, 5), "bank_fee": 999}]
    assert bank_fee_total(rows, {"2026-03", "2026-04"}) == 45


def test_transfers_and_advances_pay_bank_fees_too():
    """轉存與預支不進損益，但它們的匯費是真的付出去的錢。
    用「這一列有沒有進損益」當過濾條件會漏掉這一段。"""
    rows = [{"entry_date": datetime(2026, 3, 5), "bank_fee": 30,
             "category": "轉存", "deposit": 0, "expense": 0}]
    assert bank_fee_total(rows, {"2026-03"}) == 30


# ── 折舊：每月一列，0 的月不出現 ──────────────────────────────────

def test_depreciation_rows_skip_the_months_with_nothing_to_depreciate():
    """出 0 元列的後果不是數字錯，是 drilldown 長出一排 0 元的假明細。"""
    eq = [{"purchase_cost": 36000, "depreciation_months": 3,
           "purchase_date": datetime(2026, 1, 10)}]
    rows = depreciation_rows(eq, ["2025-12", "2026-01", "2026-02", "2026-03", "2026-04"])
    assert [r["month"] for r in rows] == ["2026-01", "2026-02", "2026-03"]
    assert [r["amount"] for r in rows] == [12000, 12000, 12000]


def test_depreciation_rows_add_up_several_pieces_of_equipment_per_month():
    eq = [{"purchase_cost": 12000, "depreciation_months": 12,
           "purchase_date": datetime(2026, 1, 1)},
          {"purchase_cost": 24000, "depreciation_months": 12,
           "purchase_date": datetime(2026, 1, 1)}]
    assert depreciation_rows(eq, ["2026-01"]) == [{"month": "2026-01", "amount": 3000}]


# ── 應收逾期：60 天門檻 ──────────────────────────────────────────

def test_the_overdue_threshold_is_strictly_more_than_the_given_days():
    """🔴 邊界差一天在畫面上看不出來（金額只是變大或變小一點）。
    第 60 天還不算逾期，第 61 天才算。"""
    def inv(days_ago, amount):
        return {"invoice_date": datetime.now() - timedelta(days=days_ago),
                "amount_total": amount, "payment_status": "未收款",
                "payment_type": "收款", "category": "專案"}
    assert ar_overdue_amount([inv(60, 100)]) == 0
    assert ar_overdue_amount([inv(61, 100)]) == 100
    assert ar_overdue_amount([inv(61, 100), inv(90, 50)]) == 150


def test_a_collected_invoice_is_never_overdue_however_old_it_is():
    old = datetime.now() - timedelta(days=900)
    assert ar_overdue_amount([{"invoice_date": old, "amount_total": 999999,
                               "paid_date": old, "payment_type": "收款",
                               "category": "專案"}]) == 0


def test_the_overdue_window_can_be_widened():
    inv = {"invoice_date": datetime.now() - timedelta(days=100),
           "amount_total": 100, "payment_status": "未收款",
           "payment_type": "收款", "category": "專案"}
    assert ar_overdue_amount([inv], days=120) == 0


# ── 時區日界：timestamptz 回讀是 UTC，直接取值會少一天 ────────────

def test_a_timezone_aware_datetime_comes_back_as_a_local_naive_one():
    """🔴 這條錯過一次（2026-07「差一天」）：月初/日界的列直接 .date()
    會歸到前一天/前一個月，於是那筆帳掉進上一期。"""
    utc_midnight = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    got = local_day(utc_midnight)
    assert got.tzinfo is None, "要剝掉時區，不然跟 naive 的日期比較會 TypeError"
    assert got == utc_midnight.astimezone().replace(tzinfo=None)


def test_local_day_leaves_naive_values_and_none_alone():
    naive = datetime(2026, 3, 1, 9, 30)
    assert local_day(naive) is naive
    assert local_day(None) is None


def test_today_start_is_midnight_so_same_day_things_are_not_yet_overdue():
    t = today_start()
    assert (t.hour, t.minute, t.second, t.microsecond) == (0, 0, 0, 0)
    assert t.date() == datetime.now().date()


# ── 私帳逐案改寫：拿掉現金那一面，換成權責的數 ────────────────────

def _pnl(cost_lines):
    return {"revenue": {"total": 1000000, "by_collection": {"cash": 0}},
            "cost": {"groups": [{"label": "工", "lines": list(cost_lines), "total": 0},
                                {"label": "費", "lines": [], "total": 0}],
                     "total": 0},
            "opex": {"total": 100000}, "non_operating": {"total": 0},
            "tax": {"income_tax": 0}}


def test_the_cash_mirror_of_project_spending_is_removed_before_the_accrual_number_lands():
    """🔴 這是 FY2025 多算 1,118,775 的那條規則。收支明細「公司_專案」的支出列
    是匯給外包的**現金那一面**，與逐案權責的委外費用是同一批錢。
    只加不減 = 同額重複計，而損益表看起來完全正常。"""
    out = apply_ledger_project_costs(
        _pnl([{"label": "專案雜支", "amount": 1118775}]), outsource=1118775)
    labels = [ln["label"] for g in out["cost"]["groups"] for ln in g["lines"]]
    assert "專案雜支" not in labels, "現金鏡像那一列沒被拿掉 → 這批錢被算了兩次"
    assert out["cost"]["total"] == 1118775


def test_the_project_tax_replaces_the_cash_side_tax_which_is_always_zero_here():
    """私帳沒有 tax_income 那種對映列，引擎自己算出來永遠是 0 —— 稅要外面餵。"""
    out = apply_ledger_project_costs(_pnl([]), outsource=300000, tax=45000)
    assert out["tax"]["income_tax"] == 45000
    # 淨利 = 稅前 − 稅；稅沒扣進去的話淨利會多出一整筆稅
    assert out["net"]["amount"] == out["pretax"] - 45000


def test_the_margins_are_recomputed_from_the_new_cost_not_carried_over():
    """改了成本卻沿用舊毛利率，是「數字自己對不起來自己」那一類的錯 ——
    毛利額變了、百分比沒變，兩個一起印在同一張表上。"""
    out = apply_ledger_project_costs(_pnl([]), outsource=400000, misc=100000)
    assert out["cost"]["total"] == 500000
    assert out["gross"]["amount"] == 500000          # 1,000,000 − 500,000
    assert out["gross"]["rate"] == 50.0
    assert out["operating"]["amount"] == 400000      # 再減 opex 100,000
    assert out["operating"]["rate"] == 40.0


def test_zero_amounts_do_not_create_empty_cost_lines():
    out = apply_ledger_project_costs(_pnl([]), outsource=0, misc=0)
    assert all(not g["lines"] for g in out["cost"]["groups"])
    assert out["cost"]["total"] == 0


def test_the_original_statement_is_not_mutated():
    """🔴 母公司與私帳在同一次請求裡各算一份時，共用的 dict 被就地改掉
    會讓先算完的那本帳跟著變 —— 而且只有並排看兩本帳時才看得出來。"""
    src = _pnl([{"label": "專案雜支", "amount": 999}])
    apply_ledger_project_costs(src, outsource=1)
    assert src["cost"]["groups"][0]["lines"] == [{"label": "專案雜支", "amount": 999}]
