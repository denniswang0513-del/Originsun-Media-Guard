# -*- coding: utf-8 -*-
"""每一個寫 `crm_cash_entries` 的地方，分類都要走同一份規則。

🔴 為什麼用「掃全部建構點」而不是「點名幾支函式」：2026-08-30 這一輪把
`apply_bank_statement`／`apply_rules_to_unclassified`／`apply_card_statement`
三條路都接到 `_sync_taxonomy` 上，測試就照著點名那三支 —— 結果第四個寫入端
（`create_entry_from_statement_line`，對帳工作台的「補記入帳」）沒人想到，
它照樣只寫 category。點名式的斷言只擋得住**已經想到**的那幾個。

沒接上的後果是靜默的：那一列有 category、卻沒有 `taxonomy_node_id`，於是
收支明細的樹狀篩選看不到它，`item` 也是空的 —— 畫面上看起來已經分好類了。
私帳尤其嚴重，因為它的類別下拉吃的是分類樹鏡射出來的複合鍵。
"""
from tests.unit._srcscan import py_callers

ROOTS = ("routers", "services", "core")

# 🔴 例外要寫清楚**為什麼**免疫。加新的進來以前先問：這一列真的不可能是私帳的
# 嗎？（私帳才有分類樹；母公司的節點表是空的，掛不掛節點都一樣。）
EXEMPT = {
    # 貸款繳款：category 是固定的 LOAN_PAY_CATEGORY，而貸款全在母公司
    "routers/api_finance.py:_record_loan_payment",
    # 福委會請款登記匯款：category 固定 "請款"（→ ap_settlement，費用已在 AP
    # 認列），福委會是公司的池子
    "routers/crm/benefits.py:pay_entry",
    # 零用金批次匯款：同上，而且它的 `item` 放的是**那張 AP 的類別**，不是
    # category 的第二層鏡射 —— 走 _sync_taxonomy 會把那個值洗掉
    "routers/crm/petty.py:pay_claim",
    # CSV 匯入：建構時就寫死 entity="parent"
    "routers/crm/cash.py:import_cash_csv",
}


def _writers():
    """`{"路徑:函式名": 有沒有呼叫 _sync_taxonomy}`，只收有建 CrmCashEntry 的。"""
    return {k: "_sync_taxonomy" in v
            for k, v in py_callers(*ROOTS).items() if "CrmCashEntry" in v}


def test_every_writer_routes_classification_through_one_rule():
    writers = _writers()
    # 🔴 掃不到東西要當失敗，不是通過 —— 建構式改名（或掃描根改了）之後，
    # 這個測試會安安靜靜地永遠綠燈，而它要守的東西早就沒人看著了。
    assert len(writers) >= 8, f"掃到的建構點太少（{len(writers)}），掃描本身可能壞了"
    missing = sorted(k for k, ok in writers.items() if not ok and k not in EXEMPT)
    assert not missing, (
        "這些地方建了收支列卻沒讓 `_sync_taxonomy` 寫分類三欄＋節點："
        f"{missing}。要嘛接上去，要嘛連同「為什麼它不可能是私帳的」一起加進 EXEMPT。")


def test_the_exemption_list_has_no_dead_names():
    """例外名單裡的函式要真的還在（改名／刪掉之後，那個豁免就悄悄套到別人身上）。"""
    stale = sorted(EXEMPT - set(_writers()))
    assert not stale, f"EXEMPT 裡這些已經不建收支列了，請移除：{stale}"
