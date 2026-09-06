# -*- coding: utf-8 -*-
"""報價單 PDF：檢視模型的金額規則、備註組成、檔名；模板能渲染；端點守衛。

視覺正本是 frontend/demo/quotation-pdf.html，這裡釘的是「印出來的數字與文字」不是版面。
"""
from datetime import date

from core.quotation_pdf import (build_quotation_view, footer_line, nt, pdf_filename,
                                promo_amount, spec_lines)
from tests.unit._srcscan import code_only, func_body, repo_src


def _q(**over):
    base = {
        "version": 2, "status": "已寄送",
        "quote_date": "2025-07-15T00:00:00+08:00", "valid_until": None,
        "subtotal": 160000, "discount": 0, "tax_rate": 5, "tax_amount": 8000, "total": 168000,
        "final_price": 165000,
        "payment_stages": [{"label": "簽約", "pct": 50}, {"label": "交片", "pct": 50}],
        "terms": "拍攝一日（8 小時），超時每小時 3,000。\n修改兩次，第三次起每次 5,000。",
        "spec": "形象短片 90 秒 1 支、含中文字幕",
        "items": [
            {"group_name": "拍攝", "description": "導演與企劃", "quantity": 1, "unit": "式", "unit_price": 40000, "amount": 40000, "note": ""},
            {"group_name": "拍攝", "description": "攝影師", "quantity": 2, "unit": "人次", "unit_price": 12000, "amount": 24000, "note": ""},
            {"group_name": "後期製作", "description": "剪輯", "quantity": 1, "unit": "支", "unit_price": 85000, "amount": 85000, "note": ""},
            {"group_name": "贈項", "description": "逐字稿", "quantity": 1, "unit": "套", "unit_price": 0, "amount": 0, "note": "原價 10,000"},
        ],
    }
    base.update(over)
    return base


COMPANY = {"name": "源日有限公司", "tax_id": "90371657", "bank": "012 台北富邦 中山分行",
           "account_name": "源日有限公司", "account_no": "82120000062728",
           "quote_valid_days": 14, "delivery_terms": "完成檔以雲端儲存連結交檔。"}


def test_promo_is_total_minus_final_only_when_final_is_lower():
    """owner 拍板：優惠只是把含稅價湊到目標金額，差額倒算；最終報價沒填或更高就沒有這列。"""
    assert promo_amount(168000, 165000) == 3000
    assert promo_amount(168000, None) == 0
    assert promo_amount(168000, 170000) == 0


def test_view_money_and_groups():
    v = build_quotation_view(_q(), COMPANY, client_name="陽光食品股份有限公司", project_name="2026 品牌形象短片")
    assert [g["name"] for g in v["groups"]] == ["拍攝", "後期製作", "贈項"]
    assert v["groups"][0]["subtotal_fmt"] == "NT$64,000"
    assert v["groups"][2]["rows"][0]["note"] == "原價 10,000"
    assert (v["subtotal_fmt"], v["tax_fmt"], v["promo"], v["promo_fmt"], v["final_fmt"]) == \
        ("NT$160,000", "NT$8,000", 3000, "NT$3,000", "NT$165,000")
    assert v["discount"] == 0                       # 稅前折扣 0 → 模板不印那列
    assert [s["amount_fmt"] for s in v["payment"]] == ["NT$82,500", "NT$82,500"]   # 付款比例照最終報價算


def test_view_dates_default_validity_from_company_days():
    v = build_quotation_view(_q(), COMPANY, client_name="c", project_name="p")
    assert (v["quote_date"], v["valid_until"], v["valid_days"]) == ("2025.07.15", "2025.07.29", 14)
    v2 = build_quotation_view(_q(valid_until="2025-08-15T00:00:00+08:00"), COMPANY, client_name="c", project_name="p")
    assert (v2["valid_until"], v2["valid_days"]) == ("2025.08.15", 31)
    v3 = build_quotation_view(_q(quote_date=None), COMPANY, client_name="c", project_name="p", today=date(2026, 9, 6))
    assert v3["quote_date"] == "2026.09.06"


def test_spec_and_terms_split_into_lines():
    assert spec_lines("前導短片 2 支、講座完整版 60 分 3 支\nFellow 個人影音 45 分 6 支") == \
        ["前導短片 2 支", "講座完整版 60 分 3 支", "Fellow 個人影音 45 分 6 支"]
    assert spec_lines(None) == []
    v = build_quotation_view(_q(), COMPANY, client_name="c", project_name="p")
    assert v["terms_lines"] == ["拍攝一日（8 小時），超時每小時 3,000。", "修改兩次，第三次起每次 5,000。"]
    assert v["delivery_terms"] == "完成檔以雲端儲存連結交檔。"


def test_filename_convention_and_sanitizing():
    assert pdf_filename(date(2025, 7, 15), "龍應台文化基金會", "和平行動者系列影片") == \
        "20250715_龍應台文化基金會_和平行動者系列影片_源日報價單.pdf"
    assert pdf_filename(date(2025, 7, 15), "A/B:C", 'x*y?"z') == "20250715_A-B-C_x-y-z_源日報價單.pdf"
    assert pdf_filename(date(2025, 7, 15), "", "只有專案") == "20250715_只有專案_源日報價單.pdf"


def test_footer_line_skips_empty_company_fields():
    v = build_quotation_view(_q(), {"name": "源日有限公司", "tax_id": "90371657"}, client_name="c", project_name="p")
    assert footer_line(v) == "源日有限公司　統一編號 90371657　2025.07.15　v2"


def test_nt_formats_ints_and_none():
    assert (nt(0), nt(None), nt(802800), nt(1234.6)) == ("NT$0", "NT$0", "NT$802,800", "NT$1,235")


def test_template_renders_all_sections():
    """模板不靠 Playwright 也要能渲染；關鍵字串都要在，沒填的區塊不印。"""
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(loader=FileSystemLoader("templates"), autoescape=select_autoescape(["html"]))
    v = build_quotation_view(_q(), COMPANY, client_name="陽光食品股份有限公司", project_name="2026 品牌形象短片")
    html = env.get_template("quotation_pdf.html").render(v=v, logo_src="", seal_src="")
    for must in ("報價單", "陽光食品股份有限公司", "2026 品牌形象短片", "形象短片 90 秒 1 支",
                 "NT$64,000", "專案優惠", "−NT$3,000", "NT$165,000",
                 "付款方式：", "簽約 50%", "完成檔交檔：", "報價有效期限 14 日（至 2025.07.29）",
                 "82120000062728", "客戶簽章"):
        assert must in html, must
    assert "折扣" not in html and "<img" not in html       # 沒稅前折扣、沒 logo／章就不印
    v2 = build_quotation_view(_q(final_price=None, discount=10000), COMPANY, client_name="c", project_name="p")
    html2 = env.get_template("quotation_pdf.html").render(v=v2, logo_src="", seal_src="")
    assert "折扣" in html2 and "專案優惠" not in html2


def test_pdf_endpoint_is_money_guarded_and_never_caches():
    """報價 PDF 是錢：守衛跟其他報價端點一樣走 money_dep；檔案回應 no_store（不留快取副本）。"""
    src = repo_src("routers/crm/quotes.py")
    assert '@router.get("/quotations/{quotation_id}/pdf", dependencies=[Depends(money_dep)])' in src
    body = code_only(func_body(src, "async def quotation_pdf("))
    assert "no_store_file(" in body and "html_to_pdf(" in body and "build_quotation_view(" in body
    assert "spec" in code_only(func_body(src, "def _to_quotation_dict(")), "序列化要帶 spec，前端與 PDF 都靠它"


def test_spec_absent_from_payload_means_unchanged_not_cleared():
    """🔴 CF 給 .js 4 小時快取：舊分頁的 PUT 不帶 spec，不可以把別人剛填的規格洗掉。
    schema 用 Optional[str] = None、更新端點只有 `is not None` 才寫。"""
    from core.schemas import QuotationPayload
    assert QuotationPayload().spec is None
    assert QuotationPayload(spec="").spec == ""          # 有送空字串＝真的要清掉
    body = code_only(func_body(repo_src("routers/crm/quotes.py"), "async def update_quotation("))
    assert "if req.spec is not None:" in body, "沒送 spec 就不該動它"
