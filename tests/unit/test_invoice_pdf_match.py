# -*- coding: utf-8 -*-
"""上傳發票 PDF 時比對抬頭／金額，不一致就警示。

需求（Soca 2026-08-21，owner 轉）：
> 「上傳發票 pdf 可以偵測上面的抬頭和金額是否跟表單上一致，如果不一樣可以警示我。
>   因為現在這樣如果一次多張，我有點怕會傳錯張。」

他怕的是**傳錯張**，不是填錯欄。所以：

  🔴 是警示不是閘門 —— 抽不到就安靜。擋下來會變成「明明是對的卻傳不上去」
     （掃描件、字型把字拆開、版面不同都會抽不到），那比偶爾漏警示更糟。
  🔴 金額只認**含稅總計** —— 銷售額（未稅）跟營業稅在同一張紙上，
     認錯標籤就會拿未稅價比含稅價，每張都跳假警示。
"""
import os

import pytest

from core.invoice_pdf import compare_invoice_pdf, parse_invoice_text

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "fixtures")


def _fx(name):
    return os.path.join(FIXTURES, name)


def _text(name):
    from core.doc_text import extract_text
    t, err = extract_text(_fx(name))
    assert not err, err
    return t


# ── 解析 ──────────────────────────────────────────────────────────

def test_parses_all_three_fields_from_a_real_pdf():
    """整條鏈：pypdf 抽字 → CJK 正規化 → 標籤正則。手打字串證明不了
    pypdf 抽出來長怎樣（實測它把「總　計」吐成 '總 計'）。"""
    p = parse_invoice_text(_text("einvoice_full.pdf"))
    assert p["invoice_number"] == "DQ45891570"
    assert p["tax_id"] == "31841680"
    assert p["amount_total"] == 50000


def test_amount_picks_the_tax_inclusive_total():
    """🔴 同一張紙上有 銷售額 47,619／營業稅 2,381／總計 50,000。
    認錯標籤＝拿未稅比含稅，每張都跳假警示。"""
    p = parse_invoice_text(_text("einvoice_full.pdf"))
    assert p["amount_total"] == 50000
    assert p["amount_total"] != 47619


def test_missing_fields_are_none_not_zero():
    """抽不到要回 None —— 回 0 的話呼叫端分不出「沒抽到」與「金額是 0」，
    就會拿 0 去比對，每張都跳警示。"""
    p = parse_invoice_text("電子發票證明聯\n發票號碼: DQ45891570")
    assert p["invoice_number"] == "DQ45891570"
    assert p["tax_id"] is None
    assert p["amount_total"] is None


def test_amount_with_commas_and_currency_mark():
    for raw, want in (("總計: 50,000", 50000), ("總 計 ： $1,234,567", 1234567),
                      ("合計:900", 900), ("總金額： ＄12,000", 12000)):
        assert parse_invoice_text(raw)["amount_total"] == want


def test_spaced_out_labels_still_match():
    """證明聯常把字距拉開 —— 抽出來會是「統 一 編 號」。"""
    p = parse_invoice_text("統 一 編 號 ： 00973926\n總 計 ： 500")
    assert p["tax_id"] == "00973926" and p["amount_total"] == 500


# ── 比對 ──────────────────────────────────────────────────────────

FULL = {"invoice_number": "DQ45891570", "tax_id": "31841680",
        "amount_total": 50000,
        "company_name": "財團法人台北市文化基金會松山文創園區"}


def test_matching_pdf_produces_no_warning():
    text = _text("einvoice_full.pdf")
    assert compare_invoice_pdf(parse_invoice_text(text), FULL, text) == []


def test_wrong_pdf_is_caught():
    """🔴 這條就是需求本身：傳到別張發票的 PDF。"""
    text = _text("einvoice_full.pdf")
    other = {**FULL, "tax_id": "00973926", "amount_total": 12345,
             "invoice_number": "DQ99999999",
             "company_name": "國家表演藝術中心國家兩廳院"}
    warns = compare_invoice_pdf(parse_invoice_text(text), other, text)
    joined = " ".join(warns)
    assert "統編不一致" in joined
    assert "金額不一致" in joined
    assert "發票號碼不一致" in joined
    assert "找不到抬頭" in joined


@pytest.mark.parametrize("field,bad,word", [
    ("tax_id", "00973926", "統編"),
    ("amount_total", 49999, "金額"),
    ("invoice_number", "DQ00000000", "發票號碼"),
])
def test_each_field_warns_on_its_own(field, bad, word):
    """一次只錯一個欄位也要抓到 —— 傳錯張時未必四項全錯
    （同一個客戶的兩張發票，抬頭與統編都一樣、只有金額與號碼不同）。"""
    text = _text("einvoice_full.pdf")
    warns = compare_invoice_pdf(parse_invoice_text(text), {**FULL, field: bad}, text)
    assert any(word in w for w in warns), warns


def test_same_client_different_amount_is_the_realistic_case():
    """最像實際會發生的：同一個客戶開兩張，抬頭統編都對，只有金額不同。"""
    text = _text("einvoice_full.pdf")
    warns = compare_invoice_pdf(parse_invoice_text(text),
                                {**FULL, "amount_total": 30000,
                                 "invoice_number": "DQ45891570"}, text)
    assert len(warns) == 1 and "金額不一致" in warns[0]


# ── 抽不到就安靜（這是產品決定，不是漏掉）──────────────────────

def test_unparseable_pdf_warns_about_nothing():
    """掃描件／圖片檔抽不到字 → 不猜、不警示。擋下來會變成「明明是對的
    卻傳不上去」，比偶爾漏警示更糟。"""
    assert compare_invoice_pdf({}, FULL, "") == []
    assert compare_invoice_pdf(None, FULL, "") == []


def test_fields_the_form_left_blank_are_not_compared():
    """表單自己沒填的欄位不比 —— 拿空值去比會對每張新發票都跳警示。"""
    text = _text("einvoice_full.pdf")
    blank = {"invoice_number": "", "tax_id": "", "amount_total": None,
             "company_name": ""}
    assert compare_invoice_pdf(parse_invoice_text(text), blank, text) == []


def test_field_the_pdf_lacks_is_not_compared():
    """PDF 上沒有金額（有些版面只有明細）→ 不要因此說金額不一致。"""
    parsed = parse_invoice_text("發票號碼: DQ45891570\n統一編號: 31841680")
    warns = compare_invoice_pdf(parsed, {**FULL, "company_name": ""},
                                "發票號碼: DQ45891570\n統一編號: 31841680")
    assert not any("金額" in w for w in warns), warns


def test_tax_id_comparison_ignores_formatting():
    """統編比對只看數字 —— 表單存 '31841680'、PDF 抽到 '31841680' 之外，
    也可能有人打成 '3184-1680'。"""
    text = _text("einvoice_full.pdf")
    warns = compare_invoice_pdf(parse_invoice_text(text),
                                {**FULL, "tax_id": "3184-1680"}, text)
    assert not any("統編" in w for w in warns), warns


def test_company_check_ignores_whitespace():
    """PDF 抽出來的中文常夾空格（「買 方: 財團法人…」）—— 直接比一定不相等。"""
    text = "買 方: 財團法人 台北市文化基金會 松山文創園區\n統一編號: 31841680"
    warns = compare_invoice_pdf(parse_invoice_text(text),
                                {**FULL, "amount_total": None,
                                 "invoice_number": ""}, text)
    assert not any("抬頭" in w for w in warns), warns


def test_very_short_company_name_is_not_containment_checked():
    """兩個字以內的抬頭不做包含檢查 —— 太容易在版面裡誤中，警示會變雜訊。"""
    text = _text("einvoice_full.pdf")
    warns = compare_invoice_pdf(parse_invoice_text(text),
                                {**FULL, "company_name": "台北"}, text)
    assert not any("抬頭" in w for w in warns), warns


# ── 端點契約 ──────────────────────────────────────────────────────

def test_upload_returns_warnings_and_does_not_block():
    """端點回 warnings，但**照樣把檔案存好、照樣回 ok** —— 警示不是閘門。"""
    from tests.unit._srcscan import code_only, func_body, repo_src
    body = code_only(func_body(repo_src("routers/crm/invoice_files.py"),
                               "async def upload_invoice_file("))
    assert "compare_invoice_pdf" in body
    assert '"warnings": warnings' in body
    assert '"status": "ok"' in body
    # 🔴 算出警示之後不准再拋例外 —— 只檢查 "status": "ok" 還在是不夠的：
    #    在它前面插一個 `if warnings: raise 422` 那句話照樣在（破壞測試實測沒咬到）。
    tail = body[body.index("warnings = compare_invoice_pdf"):]
    assert "raise" not in tail, "警示變成閘門了：檔案已經存好卻回錯誤"
    # 只讀一次檔（讀兩次等於把 pypdf 跑兩遍）
    assert body.count("_read_invoice_pdf") == 1


def test_frontend_shows_the_match_result():
    """後端算出來沒人看得到等於沒做（這個 repo 咬過『按了沒反應』兩次）。

    owner 2026-08-21 指定位置：結果掛在「電子發票」標題旁邊的徽章。
    🔴 **相符也要顯示** —— 只在不符時出聲的話，「檢查過沒問題」與「根本沒檢查」
    在畫面上長得一樣，使用者無從知道這個防呆有沒有在運作。"""
    from tests.unit._srcscan import crm_css_src, repo_src
    js = repo_src("frontend/tabs/crm/crm-invoices.js")
    assert "up.warnings" in js, "前端沒接比對結果"
    assert "up.checked" in js, "沒接 checked —— 分不出「相符」與「讀不到」"
    assert "_matchBadge()" in js and "電子發票${_matchBadge()}" in js,         "徽章沒掛在標題旁（owner 指定的位置）"
    assert "inv-match ok" in js and "inv-match bad" in js and "inv-match none" in js,         "三態沒有分別呈現"
    css = crm_css_src()
    for cls in (".inv-match.ok", ".inv-match.bad", ".inv-match.none",
                ".inv-file-warn"):
        assert cls in css, f"{cls} 沒有樣式（會變成看不見的純文字）"


def test_match_result_is_cleared_when_switching_invoice():
    """🔴 切到別張發票要清掉 —— 不然 A 的比對結果會掛在 B 上，比沒有更糟。"""
    from tests.unit._srcscan import repo_src
    js = repo_src("frontend/tabs/crm/crm-invoices.js")
    assert "_fileMatch = null" in js and "_matchOpen = false" in js
