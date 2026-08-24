# -*- coding: utf-8 -*-
"""core/card_statement.py — 信用卡帳單解析器單元測試。

樣本取自 owner 私帳實際格式（富邦/台新網銀複製、LINE Pay 商家、外幣消費+
國外交易服務費、退貨負項、繳款列）。
"""
from core.card_statement import merchant_key, parse_card_statement


def test_basic_spend_rows():
    r = parse_card_statement(
        "2026/02/21\t立吉富－雪坊精品優格TAICHU\t270\n"
        "2026/02/22\t連加＊八方雲集雙城店\t75\n")
    assert r.ok
    assert [(x.date, x.amount) for x in r.rows] == [
        ("2026-02-21", 270), ("2026-02-22", 75)]
    assert r.rows[0].note.startswith("立吉富")


def test_two_dates_takes_purchase_date():
    # 消費日 + 入帳日 → 取第一個
    r = parse_card_statement("2026/01/16 2026/01/18 GOOGLE STORAGE 913\n")
    assert r.rows[0].date == "2026-01-16"
    assert r.rows[0].amount == 913


def test_foreign_row_takes_last_amount_as_twd():
    r = parse_card_statement("2023/06/16 SP MTMOGRAPH 76.83 USD 2,427\n")
    assert r.rows[0].amount == 2427


def test_refund_negative():
    r = parse_card_statement(
        "2023/06/19 富邦momo-EC (退貨) 549\n"
        "2023/06/19 誠品書店 1,000\n")
    amts = sorted(x.amount for x in r.rows)
    assert amts == [-549, 1000]


def test_payment_rows_excluded_from_spend():
    r = parse_card_statement(
        "2026/03/05 轉帳繳款 40,801\n"
        "2026/03/07 全聯福利中心 512\n")
    assert len(r.rows) == 1 and r.rows[0].amount == 512
    assert len(r.payments) == 1 and r.payments[0].amount == 40801


def test_payment_deadline_header_is_not_payment():
    # 「繳款截止日」是表頭資訊；有日期有金額也不能變成繳款/消費列被排除錯邊
    r = parse_card_statement(
        "2026/03/20 繳款截止 應繳總額 45,000\n"
        "2026/03/02 家樂福 900\n")
    assert len(r.payments) == 0
    # 繳款截止那列不是繳款 → 會落進 rows（人會在預覽取消勾選）或被總計核對吃掉；
    # 底線是家樂福那筆一定在
    assert any(x.amount == 900 for x in r.rows)


def test_fee_row_kind():
    r = parse_card_statement(
        "2023/06/16 SP MTMOGRAPH 76.83 USD 2,427\n"
        "2023/06/16 國外交易服務費（簽帳 2,427 ) 36\n")
    kinds = [x.kind for x in r.rows]
    assert kinds == ["spend", "fee"]
    assert r.rows[1].amount == 36


def test_minguo_year():
    r = parse_card_statement("115/01/16 藍新－ＳＡＴ 第02/06期 913\n")
    assert r.rows[0].date == "2026-01-16"


def test_printed_total_mismatch_warns():
    r = parse_card_statement(
        "2026/02/21 店家Ａ 100\n"
        "本期新增款項合計 999,999\n")
    assert r.ok
    assert any("對不上" in w for w in r.warnings)


def test_printed_total_match_no_warn():
    r = parse_card_statement(
        "2026/02/21 店家Ａ 100\n"
        "2026/02/22 店家Ｂ 200\n"
        "消費明細總計 300\n")
    assert r.ok and not r.warnings


def test_empty_input_errors():
    r = parse_card_statement("這裡沒有任何交易\n")
    assert not r.ok and r.errors


def test_merchant_key_normalization():
    assert merchant_key("連加＊八方雲集雙城店") == "八方雲集雙城店"
    assert merchant_key("街口電支－全家便利商店") == "全家便利商店"
    assert merchant_key("GOOGLE*GOOGLE STORA020192") == "GOOGLE*GOOGLESTORA"
    assert merchant_key("國外交易服務費（簽帳 2,427 )") == "國外交易服務費"
    assert merchant_key("") == ""


def test_amount_not_confused_with_date_digits():
    # 金額必須取日期之後的 token —— 不能把 21 當金額
    r = parse_card_statement("2026/02/21 UBER EATS 359\n")
    assert r.rows[0].amount == 359
