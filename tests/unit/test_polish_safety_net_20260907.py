# -*- coding: utf-8 -*-
"""polish 2026-09-07 安全網：把 95beb7ac..HEAD 這批動到、但還沒有測試釘住的公開函式／常數的**現況**釘下來。
不判斷對錯，只讓後面的 review／simplify 改壞時會紅。

範圍：core.project_flow.QUOTE_PHASE、routers.crm.quotes._render_quotation_html（含三個呼叫點的 web_pdf_url）、
crm_quotations.share_token（model／startup 加欄／索引三處）、routers.api_journal._staff_status_by_username。
"""
import re

from core.project_flow import CLOSED_STATUSES, PIPELINE, QUOTE_PHASE
from core.quotation_pdf import build_quotation_view
from tests.unit._srcscan import code_only, func_body, repo_src
from tests.unit.test_quotation_pdf import COMPANY, _q


# ── QUOTE_PHASE：報價時順手建的殼專案落在哪一階（桌機／手機一份）──────────

def test_quote_phase_is_a_pipeline_stage_and_mobile_options_emit_it():
    assert QUOTE_PHASE == "提案"
    assert QUOTE_PHASE in PIPELINE and QUOTE_PHASE not in CLOSED_STATUSES
    mobile = code_only(repo_src("routers/api_crm_mobile.py"))
    assert "from core.project_flow import" in mobile and "QUOTE_PHASE" in mobile
    assert '"quote_phase": QUOTE_PHASE' in func_body(mobile, "async def mobile_options(")
    # 桌機報價彈窗的 inline 建案目前寫死同一個字（現況；要改成走 options 再一起改）
    desktop = repo_src("frontend/tabs/crm/crm-quotes.js")
    m = re.search(r"status:\s*'([^']+)'", desktop)
    assert m and m.group(1) == QUOTE_PHASE, "桌機殼專案的階段與 QUOTE_PHASE 漂了"


# ── _render_quotation_html：同一份模板，web_pdf_url 決定要不要「下載 PDF」列 ──

def _view():
    return build_quotation_view(_q(), COMPANY, client_name="陽光食品股份有限公司", project_name="2026 品牌形象短片")


def test_render_quotation_html_print_mode_has_no_webbar():
    import routers.crm.quotes as Q
    html = Q._render_quotation_html(_view(), {})
    assert "<html" in html and "陽光食品股份有限公司" in html and "2026 品牌形象短片" in html
    assert 'class="webbar"' not in html and "下載 PDF" not in html
    assert 'src="data:image/' in html            # 預設 logo（frontend/img/originsun-logo.webp）轉成 data URI


def test_render_quotation_html_web_mode_adds_download_bar_and_missing_seal_degrades():
    import routers.crm.quotes as Q
    html = Q._render_quotation_html(_view(), {"seal_path": "company_assets/does-not-exist.png"},
                                    web_pdf_url="/q/abc123/pdf")
    assert 'class="webbar"' in html and 'href="/q/abc123/pdf">下載 PDF</a>' in html
    assert "does-not-exist" not in html          # 找不到的圖檔＝空 src，不把路徑印出來


def test_render_call_sites_only_public_view_passes_web_pdf_url():
    """印出來／存檔的 PDF 不能帶「下載 PDF」列；線上檢視要帶（/q/{token}/pdf）。"""
    src = code_only(repo_src("routers/crm/quotes.py"))
    for fn in ("async def archive_quotation_pdf_now(", "async def _quotation_pdf_response("):
        body = func_body(src, fn)
        assert "_render_quotation_html(view, company)" in body and "web_pdf_url" not in body, fn
    pub = func_body(src, "async def public_quote_html(")
    assert '_render_quotation_html(view, company, web_pdf_url=f"/q/{token}/pdf")' in pub


# ── crm_quotations.share_token：model／startup 加欄／索引三處要同時存在 ─────

def test_quotation_share_token_column_migration_and_index_all_exist():
    from sqlalchemy import String
    from db.migrations import CRM_INDEXES
    from db.models import CrmQuotation
    col = CrmQuotation.__table__.columns["share_token"]
    assert isinstance(col.type, String) and col.type.length == 64 and col.nullable
    assert '("crm_quotations", "share_token", "VARCHAR(64)")' in repo_src("main.py")
    flat = " ".join(CRM_INDEXES) if isinstance(CRM_INDEXES, (list, tuple)) else str(CRM_INDEXES)
    assert "idx_quote_share_token ON crm_quotations(share_token)" in flat


# ── _staff_status_by_username：username → crm_staff.status（沒綁＝不在 dict）──

def test_staff_status_by_username_joins_staff_and_short_circuits_on_empty():
    src = code_only(repo_src("routers/api_journal.py"))
    body = func_body(src, "async def _staff_status_by_username(")
    assert "us = [u for u in usernames if u]" in body and "if not us:" in body and "return {}" in body
    assert "join(CrmStaff, CrmStaff.id == User.staff_id)" in body and "User.username.in_(us)" in body
    wk = func_body(src, "async def week_journals(")
    assert "_staff_status_by_username(session, submitted | pending)" in wk and "staff_rank(st.get(u))" in wk
