# -*- coding: utf-8 -*-
"""CSV 匯入的金額解析與款項狀態衍生 —— 兩個「靜默毀財務資料」的回歸釘。

兩個 bug 都是 2026-08-19 匯 394 筆歷史發票（Google Sheet「發票」分頁）時實測到的，
共同特徵是**不報錯、不跳過**，就是把錯的數字寫進帳裡：

1. 發票 mapper 沒有去千分位逗號 → Sheet 匯出的 "342,857" 一律變成 0（14 筆中招）。
2. `if "收" in pt` 同時吃下「已收款」與「未收款」→ 44 張未收款發票被標成已收款，
   應收帳款憑空消失、收入被提前認列。
"""
import pytest
from tests.unit._srcscan import finance_src

# 🔴 三支 mapper 2026-08-30 隨著 finance.py 拆檔各自搬家（收支→cash、
# 請款→payments、發票與共用的 _parse_money 留在 finance）。行為沒變。
from routers.crm.cash import _map_cash_row
from routers.crm.payments import _map_payment_row
from routers.crm.finance import _map_invoice_row, _parse_money


# ── _parse_money：三支 import_csv 的單一正本 ──────────────────

@pytest.mark.parametrize("raw,want", [
    ("1234", 1234),
    ("1234.00", 1234),
    ("1,234", 1234),                 # 🔴 Google Sheets 千分位格式 — 原本會變 0
    ("1,234.00", 1234),
    (" 342,857 ", 342857),
    ("NT$ 3,780.00", 3780),          # 「發票代開收入」欄的格式
    ("-1,234", -1234),
    ("", 0),
    ("abc", 0),
    ("這筆8月中開", 0),               # 自由文字誤入金額欄
    (None, 0),
])
def test_parse_money(raw, want):
    assert _parse_money(raw) == want


def test_invoice_and_cash_mappers_agree_on_money():
    """發票與收支兩支 mapper 對同一個金額字串必須解出同一個數。

    它們曾經各寫一套（發票那套漏了去逗號）—— 這條釘住「只有一份正本」。"""
    for raw in ("1,234", "1234", "1,234.00", " 12,345 "):
        inv = _map_invoice_row({"名稱": "名稱", "未稅價": "未稅價"},
                               {"名稱": "x", "未稅價": raw})["amount_ex_tax"]
        cash = _map_cash_row({"摘要": "摘要", "支出": "支出"},
                             {"摘要": "x", "支出": raw})["expense"]
        assert inv == cash, f"{raw!r}: 發票 {inv} vs 收支 {cash}"


# ── 款項狀態：方向（收/付）與狀態（已/未）要拆對 ──────────────

@pytest.mark.parametrize("sheet_value,want_type,want_status", [
    ("已收款", "收款", "已收款"),
    ("未收款", "收款", "未收款"),   # 🔴 「未收款」裡也有「收」— 原本被判成已收款
    # 🔴 CSV 寫「已付款」，系統存的是**已撥款** —— 發票這條線上的錢是過路錢
    # （客戶匯進來、再轉撥給代開人），跟請款單的已付款是兩件事。
    # 2026-08-20 先叫「已轉撥」，08-21 owner 改成「已撥款」（配對「待撥款」）。
    ("已付款", "付款", "已撥款"),
    ("未付款", "付款", "未付款"),
    ("作廢", "作廢", "作廢"),
])
def test_payment_status_split(sheet_value, want_type, want_status):
    got = _map_invoice_row({"名稱": "名稱", "款項狀態": "款項狀態"},
                           {"名稱": "x", "款項狀態": sheet_value})
    assert got.get("payment_type") == want_type
    assert got.get("payment_status") == want_status


def test_blank_payment_status_left_to_model_default():
    """款項狀態空白 → mapper 不表態，交給 CrmInvoice 的欄位預設（收款/未收款）。
    在這裡硬塞「已收款」等於幫沒填的列認列收入。"""
    got = _map_invoice_row({"名稱": "名稱", "款項狀態": "款項狀態"},
                           {"名稱": "x", "款項狀態": ""})
    assert "payment_status" not in got
    assert "payment_type" not in got


# ── 別名覆蓋：model 有欄位就不該整欄丟掉 ──────────────────────

def test_invoice_aliases_cover_recipient_notes_and_commission():
    """收件人／電話／地址／備註／代開應匯 —— CrmInvoice 一直有這五根欄位，
    別名漏了就整欄靜靜丟掉（原本 commission 的別名還是錯字「代開應區」）。"""
    hdr = ["名稱", "代開應匯", "收件人", "電話", "收件地址", "備註"]
    got = _map_invoice_row({h: h for h in hdr},
                           {"名稱": "x", "代開應匯": "331,200", "收件人": "王小明",
                            "電話": "02-89145453", "收件地址": "台北市萬華區",
                            "備註": "電子發票"})
    assert got["commission"] == 331200
    assert got["recipient"] == "王小明"
    assert got["recipient_phone"] == "02-89145453"
    assert got["recipient_address"] == "台北市萬華區"
    assert got["notes"] == "電子發票"


def test_invoice_keeps_legacy_commission_alias():
    """舊的錯字別名「代開應區」還收 —— 手上可能有照舊表頭做的檔案。"""
    got = _map_invoice_row({"名稱": "名稱", "代開應區": "代開應區"},
                           {"名稱": "x", "代開應區": "1,000"})
    assert got["commission"] == 1000


def test_alias_resolution_has_one_definition_and_no_empty_column_trap():
    """CSV 別名對映：六支匯入端共用 map_csv_row，「第一個**有值**的欄勝出」。

    🔴 本來有兩種方言。clients/projects 那兩支是「第一個**存在**的表頭勝出」＋
    break：CSV 同時有「匯款資訊」與「匯款帳號」兩欄、而前者這一列剛好空白時，
    後者的值會被整格吞掉 —— 沒有錯誤、沒有跳過計數，那格就是空的。
    """
    from routers.crm._shared import map_csv_row
    col_map = {"info": ["匯款資訊", "匯款帳號"]}
    header_map = {"匯款資訊": "匯款資訊", "匯款帳號": "匯款帳號"}
    # 第一個別名空白 → 要落到第二個，不是整格丟掉
    got = map_csv_row(col_map, header_map, {"匯款資訊": "  ", "匯款帳號": "彰銀 12345"})
    assert got == {"info": "彰銀 12345"}
    # 第一個有值 → 第一個贏
    assert map_csv_row(col_map, header_map,
                       {"匯款資訊": "台銀 999", "匯款帳號": "彰銀 12345"}) == {"info": "台銀 999"}
    # 都空 → 不寫入這個 key（呼叫端才不會把空字串當成「刻意留白」寫進去）
    assert map_csv_row(col_map, header_map, {"匯款資訊": "", "匯款帳號": ""}) == {}
    # coerce 只作用在指定欄位
    assert map_csv_row({"n": ["數"]}, {"數": "數"}, {"數": "1,234"},
                       coerce=lambda f, v: int(v.replace(",", ""))) == {"n": 1234}


def test_all_six_importers_use_the_shared_mapper():
    import re

    from tests.unit._srcscan import repo_src
    for path in ('routers/crm/finance.py', 'routers/crm/clients.py',
                 'routers/crm/project_import.py', 'routers/crm/staff.py'):   # 2026-09-13 專案 CSV 匯入拆出
        src = repo_src(path)
        assert 'map_csv_row(' in src, f'{path} 沒用共用的別名對映'
        # 不准再出現自己寫的那個迴圈
        assert not re.search(r'for alias in aliases:', src), f'{path} 又自己寫了一份'


def test_the_three_money_importers_share_one_skeleton():
    """🔴 F1 月結守衛不能靠「記得抄」。

    發票／請款／收支三支 CSV 匯入的外框本來一字不差抄了三份：解碼退路、列號從 2
    起算、entity='parent'、以及**先 parse 全部再一次判月、任一列落鎖定月整批 409**。
    第四支帳務 CSV 一定會漏抄其中一半，而漏掉守衛＝把一個已經結完帳的月份重新打開。
    """
    from tests.unit._srcscan import code_only, func_body
    src = finance_src()
    skel = code_only(func_body(src, 'async def _import_money_csv('))
    for must in ('_assert_rows_open(', 'entity="parent"', 'big5', 'start=2'):
        assert must in skel, f'共用骨架少了 {must}'
    for fn in ('async def import_invoices_csv(', 'async def import_payments_csv(',
               'async def import_cash_csv('):
        body = code_only(func_body(src, fn))
        assert '_import_money_csv(' in body, f'{fn} 沒走共用骨架'
        assert '_assert_rows_open' not in body, f'{fn} 自己又寫了一份守衛'
        assert 'big5' not in body


def test_payment_summary_falls_back_to_payee_before_the_skip_check():
    """請款單常見填法是「只填收款人、不填摘要」。

    摘要補收款人這步必須在 mapper 裡（也就是在「要不要跳過這列」之前）——
    放在 skip 之後的話，那些列會被當成空列直接跳掉。
    """
    got = _map_payment_row({"收款人": "收款人"}, {"收款人": "王小明"})
    assert got.get("summary") == "王小明"
    # 兩個都空才是真的空列
    assert _map_payment_row({"收款人": "收款人"}, {"收款人": ""}) == {}
