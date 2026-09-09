# -*- coding: utf-8 -*-
"""「生成報價單」（owner 2026-09-10）。

改動的本質：報價單從「客戶點連結時即時重算重畫」→「按一顆鈕產出一份定稿文件」。
兩個目的，兩個都要守：

  1. **客戶的連結不受 master 開關影響** —— 送的是生成好的檔，NAS 對外容器只要
     會送檔就夠了（不用 Playwright、不用 jinja2、不用讀 settings）。
  2. **報價單是定稿文件** —— 生成之後你在系統裡改東西，客戶手上那份不會靜默地跟著變；
     畫面上會說「內容已修改，尚未重新生成」，按了才換。

規則正本在 core/quote_snapshot.py（純函式），I/O 在 routers/crm/quotes.py。
"""
import pytest

from core import quote_snapshot as QS
from tests.unit._srcscan import code_only, func_body, repo_src

QUOTES = "routers/crm/quotes.py"


# ── 純規則 ────────────────────────────────────────────────────

def test_asset_names_are_deletable_by_the_image_host():
    """🔴 檔名必須過得了 `core.assets_host._SAFE_NAME`（32 hex ＋ 2-5 碼副檔名）。

    那支是圖床**唯一**的刪除路徑。名字不合它的規矩 → 重新生成時舊快照刪不掉，
    會一張一張永遠躺在 NAS 上，而且那些舊網址還通（客戶可能拿到過期版本）。
    """
    from core.assets_host import _SAFE_NAME
    pdf, html = QS.new_asset_names()
    assert _SAFE_NAME.match(pdf) and _SAFE_NAME.match(html)
    assert pdf.endswith(".pdf") and html.endswith(".html")
    assert pdf[:-4] == html[:-5], "同一次生成的兩個檔共用一個亂數 stem"
    assert QS.new_asset_names()[0] != pdf, "每次生成都要換名字（舊網址立刻失效）"


def test_stale_is_decided_by_source_version_not_by_clock():
    """🔴 過期判定比對的是「生成時讀到的 updated_at」，不是時間先後。

    產 PDF 要好幾秒，中間有人存了一次的話，「生成時間比較晚」永遠成立 ——
    會把舊內容判成新鮮的，而且完全靜默。
    """
    rec = QS.make_record("a" * 32 + ".pdf", "a" * 32 + ".html", "x.pdf", "2026-09-10T10:00:00+08:00")
    assert not QS.is_stale(rec, "2026-09-10T10:00:00+08:00")
    assert QS.is_stale(rec, "2026-09-10T10:00:01+08:00"), "改過一次就算過期"
    # 生成比較晚（正常情況）也不會讓「內容其實變了」被蓋掉
    later = QS.make_record("b" * 32 + ".pdf", "b" * 32 + ".html", "x.pdf",
                           "2026-09-10T10:00:00+08:00", generated_at="2026-09-10T23:59:00+08:00")
    assert QS.is_stale(later, "2026-09-10T10:05:00+08:00")


@pytest.mark.parametrize("rec", [None, {}, {"html": "x.html"}, "不是 dict", []])
def test_no_snapshot_counts_as_stale(rec):
    """沒生成過＝不能送。判不出來的東西一律當過期 —— 寧可多叫使用者按一次，
    也不要靜默送一份舊檔給客戶。"""
    assert QS.is_stale(rec, "2026-09-10T10:00:00+08:00")
    assert QS.state(rec, "2026-09-10T10:00:00+08:00")["label"] == QS.LABEL_NONE


def test_state_says_which_of_the_three_situations_it_is():
    now = "2026-09-10T10:00:00+08:00"
    rec = QS.make_record("c" * 32 + ".pdf", "c" * 32 + ".html", "x.pdf", now,
                         generated_at="2026-09-10T14:30:00+08:00")
    fresh = QS.state(rec, now)
    assert fresh["generated"] and not fresh["stale"]
    assert fresh["label"] == "已生成 09/10 14:30"

    stale = QS.state(rec, "2026-09-10T11:00:00+08:00")
    assert stale["generated"] and stale["stale"] and stale["label"] == QS.LABEL_STALE

    blank = QS.state(None, now)
    assert not blank["generated"] and blank["stale"] and blank["label"] == QS.LABEL_NONE


def test_record_keeps_the_filename_it_was_generated_with():
    """檔名存起來、不重算：之後改了案名或報價日期，客戶手上那份不該跟著改名。"""
    rec = QS.make_record("d" * 32 + ".pdf", "d" * 32 + ".html",
                         "20260910_客戶_專案_源日報價單.pdf", "2026-09-10T10:00:00+08:00")
    assert QS.download_filename(rec) == "20260910_客戶_專案_源日報價單.pdf"
    assert QS.asset_name(rec, "pdf") == "d" * 32 + ".pdf"
    assert QS.asset_name(rec, "html") == "d" * 32 + ".html"
    assert QS.asset_name(rec, "exe") == "", "只認 pdf／html 兩種"
    assert sorted(QS.assets_of(rec)) == sorted(["d" * 32 + ".pdf", "d" * 32 + ".html"])
    assert QS.assets_of(None) == []


def test_share_url_falls_back_to_a_relative_path():
    """設定沒設＝回相對路徑，前端沿用 location.origin（＝改動前的行為，不會突然變）。"""
    assert QS.share_url("abc123") == "/q/abc123"
    assert QS.share_url("abc123", "https://www.originsun-studio.com/") == \
        "https://www.originsun-studio.com/q/abc123"
    assert QS.share_url("") == "" and QS.share_url(None) == ""


# ── I/O 那一側的不變式（掃原始碼）────────────────────────────

def test_generate_records_the_version_it_rendered_from():
    """🔴 `src` 要在**渲染之前**讀，而且寫回時不准動 updated_at。

    動了 updated_at 就等於「生成完當下立刻過期」—— 畫面永遠顯示「內容已修改」，
    而且每次寄出都會再排一次生成。
    """
    gen = code_only(func_body(repo_src(QUOTES), "async def generate_quotation_snapshot("))
    assert "token, src, old = q.share_token, q.updated_at, q.pdf_snapshot" in gen
    assert gen.index("q.updated_at") < gen.index("html_to_pdf("), "要先記下來源版本再開始渲染"
    assert "q.pdf_snapshot = record" in gen
    assert "q.updated_at =" not in gen, "寫回快照不准碰 updated_at（碰了就立刻自我過期）"
    assert "quote_snapshot.make_record(names[0], names[1], view[\"filename\"], src)" in gen


def test_generate_cleans_up_the_previous_snapshot():
    """舊快照留著是佔 NAS 空間，而且那個網址還通得到 —— 客戶可能拿到過期的版本。"""
    gen = code_only(func_body(repo_src(QUOTES), "async def generate_quotation_snapshot("))
    assert "for name in quote_snapshot.assets_of(old):" in gen
    assert "assets_delete(quote_snapshot.NAMESPACE, name)" in gen
    assert gen.index("q.pdf_snapshot = record") < gen.index("assets_of(old)"), \
        "先把新的寫進 DB 再刪舊的：反過來的話中間掛掉就兩份都沒有"


def test_background_generate_does_not_stampede():
    """舊連結的退路會在客戶每次開頁時補送一發生成。客戶按兩下重整＝兩顆 Chromium。"""
    src = repo_src(QUOTES)
    quiet = code_only(func_body(src, "async def generate_quotation_snapshot_quietly("))
    assert "_SNAPSHOT_INFLIGHT" in quiet and "return None" in quiet
    assert "_SNAPSHOT_INFLIGHT.discard(quotation_id)" in quiet and "finally:" in quiet


def test_public_endpoints_serve_the_snapshot_before_anything_else():
    """對外那兩支的重點：**先看有沒有檔**。有檔就送檔 —— 那條路不需要 Playwright、
    不需要 jinja2、不需要讀 settings，所以 NAS 對外容器做得到。"""
    src = code_only(repo_src(QUOTES))
    html = func_body(src, "async def public_quote_html(")
    assert '_snapshot_file(getattr(q, "pdf_snapshot", None), "html")' in html
    assert html.index("_snapshot_file(") < html.index("_live_quote_fallback("), \
        "快照優先；即時渲染只是舊連結的退路"

    pdf = func_body(src, "async def public_quote_pdf(")
    assert '_snapshot_file(snap, "pdf")' in pdf
    assert "quote_snapshot.download_filename(snap)" in pdf, "下載檔名用生成當下存的那個"
    assert pdf.index("_snapshot_file(") < pdf.index("_quotation_pdf_response("), "同上"


def test_fallback_degrades_honestly_when_this_machine_cannot_render():
    """NAS 對外容器沒有 jinja2／Playwright。那裡不該回 500（客戶看到白畫面不知道發生什麼事），
    要回「尚未生成，請與我們聯絡」。"""
    src = code_only(repo_src(QUOTES))
    fb = func_body(src, "async def _live_quote_fallback(")
    assert "status_code=503" in fb and "_SNAPSHOT_NOT_READY" in fb
    assert "fire(generate_quotation_snapshot_quietly(q.id)" in fb, "順手補生成，下次就走快照"
    pdf = func_body(src, "async def public_quote_pdf(")
    assert "status_code=503" in pdf and "_SNAPSHOT_NOT_READY" in pdf
    assert "except HTTPException:" in pdf and "raise" in pdf, \
        "404／401（連結失效）要照原樣傳出去，不可以被統一成 503"


def test_generate_endpoint_is_a_write_and_mints_the_link():
    gen = code_only(func_body(repo_src(QUOTES), "async def generate_quotation("))
    assert "_check_quotes_auth(request)" in gen, "生成是寫入（會改 DB、會佔 Chromium）"
    assert "status_code=404" in gen
    assert "quote_snapshot.share_url(q.share_token, _public_base())" in gen


def test_share_regenerates_when_the_link_would_show_something_stale():
    """把連結拿去發之前，草稿仍然擋（422），而且該生成的先排下去 ——
    不然客戶點開看到的是舊版或「尚未生成」。"""
    share = code_only(func_body(repo_src(QUOTES), "async def share_quotation("))
    assert "q.status == QUOTE_STATUSES[0]" in share and "status_code=422" in share
    assert "quote_snapshot.is_stale(snap, q.updated_at)" in share
    assert "fire(generate_quotation_snapshot_quietly(quotation_id)" in share


def test_serialised_quotation_carries_the_state_for_both_frontends():
    """桌機與手機都直接顯示後端給的 label —— 兩邊各寫一份中文，改字一定漏掉一邊。"""
    body = code_only(func_body(repo_src(QUOTES), "def _to_quotation_dict("))
    assert '"pdf_state": quote_snapshot.state(getattr(q, "pdf_snapshot", None), q.updated_at)' in body


def test_public_base_setting_rejects_things_that_would_break_silently():
    """客戶連結網域收垃圾的後果是安靜的 —— 連結是複製給客戶的，錯了不會有 error log，
    只會有人說「你給我的網址打不開」。所以在寫進設定前就擋。"""
    from fastapi import HTTPException
    from routers.crm.quotes import _clean_public_base
    assert _clean_public_base("https://www.originsun-studio.com/") == "https://www.originsun-studio.com"
    assert _clean_public_base("") == "" and _clean_public_base(None) == "", "空＝回到 location.origin"
    for bad in ("originsun-studio.com", "https://x.com/q", "javascript:alert(1)", "//x.com"):
        with pytest.raises(HTTPException):
            _clean_public_base(bad)


def test_settings_post_only_writes_the_fields_it_was_given():
    """🔴 舊分頁的 POST 只帶 quotes_root（CF 給 .js 四小時快取，舊分頁一定存在）。
    整包寫回會把剛設好的對外網址清成空字串 —— 同 reference_cloudflare_js_cache 那類坑。

    2026-09-10 對外網址改成報價與發票**共用**的 `share_public_base`（core/share_link.py）。
    舊鍵 `quotes_public_base` 仍然收得下（舊分頁送的就是那個名字），但一律寫進新鍵
    並把舊鍵清掉 —— 兩個鍵同時有值的話「哪個生效」就要翻程式碼才知道。
    """
    body = code_only(func_body(repo_src(QUOTES), "async def set_quotations_root("))
    assert 'if "quotes_root" in body:' in body, "沒送的欄位不准寫"
    assert 'for _k in ("share_public_base", "quotes_public_base"):' in body
    assert 's["share_public_base"] = _clean_public_base(' in body
    assert 's.pop("quotes_public_base", None)' in body, "寫新鍵時要把舊鍵清掉，不要兩個並存"


def test_all_three_quote_surfaces_show_the_state():
    """報價有三個入口（報價分頁／專案頁的報價子頁／手機卡片），三個都編得動報價。

    🔴 編得動卻看不到「客戶那份已經過期了」＝改完沒有任何提示，要切到別的分頁才發現。
    同 quote-delete 那條「三個入口都要走同一支」的理由 —— 漏掉的那個入口不會報錯，
    只會安靜地讓客戶拿到舊版。狀態句一律用後端的 `pdf_state.label`，前端不拼中文。
    """
    from tests.unit._srcscan import js_code_only
    surfaces = {
        "frontend/tabs/crm/crm-quotes.js": "quote-gen-note",
        "frontend/tabs/crm/crm-projects-quotes.js": "pq-gen-note",
        "frontend/m/views/quotes.js": "q.pdf_state",
    }
    for path, marker in surfaces.items():
        js = js_code_only(repo_src(path))
        assert marker in js, path
        assert "pdf_state" in js and "generate'" in js.replace('"', "'").replace("`", "'"), path
        for hardcoded in (QS.LABEL_NONE, QS.LABEL_STALE):
            assert hardcoded not in js, f"{path}：中文正本在 core/quote_snapshot.state，前端不准自己拼"


def test_office_settings_never_carry_a_path_only_that_windows_box_can_see():
    """🔴 送去 NAS 的設定裡不准有磁碟代號路徑（`E:\\Dev\\…`）。

    dev checkout 的 `assets_host.dir` 是本機資料夾（那台沒有 NAS 的 SMB 憑證，
    見 reference_paste_assets_cdn）。推上去 → NAS 容器去找一個它根本看不到的 Windows
    路徑 → 貼圖、影像紀錄縮圖、**客戶的報價單連結**全部靜默壞掉，而 healthz 還是綠的。

    🔴 擋的條件是「**這個值**在 NAS 上有意義嗎」，不是「這台是不是 master」——
    `/publish` 本來就從 dev checkout 跑，用機器身分當條件會每次都成立、
    設定永遠送不出去（第一版就是這樣寫的，等於白做）。
    """
    from core.office_settings import export_settings
    good = {"invoices_root": r"\\192.168.1.132\Originsun\發票",
            "assets_host": {"dir": r"\\192.168.1.132\Container\PasteAssets", "base_url": "https://x"}}
    out, dropped = export_settings(good)
    assert sorted(out) == ["assets_host", "invoices_root"] and not dropped

    bad = dict(good, assets_host={"dir": r"E:\Dev\Originsun-Media-Guard\uploads", "base_url": "/uploads"})
    out, dropped = export_settings(bad)
    assert "assets_host" not in out and len(dropped) == 1, (out, dropped)
    assert "invoices_root" in out, "只丟掉有問題的那個鍵，不是整包不送"

    # drive_map 的**鍵**是磁碟代號、值才是 UNC —— 不可以因為鍵長得像路徑就整個丟掉
    out, dropped = export_settings({"drive_map": {"T": r"\\192.168.1.132\Project_Longterm"}})
    assert "drive_map" in out and not dropped


def test_office_settings_come_from_the_production_agent_not_this_checkout():
    """`/publish` 從 e:\\Dev 跑，但要送去 NAS 的是**生產 agent** 那份設定
    （deploy_to_prod 只覆蓋程式碼，settings.json 留在原地＝生產值）。"""
    src = code_only(repo_src("publish_update.py"))
    assert 'PROD_AGENT_DIR = r"C:\\OriginsunAgent"' in src
    body = code_only(func_body(src, "def _office_settings_source("))
    assert "PROD_AGENT_DIR" in body and "settings.json" in body
    assert "load_settings()" in body, "生產 agent 不在（真的跑在 master 上）才退回本行程"
    push = code_only(func_body(src, "def _push_office_settings("))
    assert "_office_settings_source()" in push and "load_settings()" not in push, \
        "推送那支不准自己讀本機設定，要走同一個來源決定點"


def test_column_and_startup_migration_both_exist():
    """加欄位要兩處同時有：model 與 main.py startup 的 ADD COLUMN 清單。
    只加 model 的話，既有的生產資料庫永遠不會長出那一欄（而且是靜默的）。"""
    from sqlalchemy.dialects.postgresql import JSONB
    from db.models import CrmQuotation
    col = CrmQuotation.__table__.columns["pdf_snapshot"]
    assert isinstance(col.type, JSONB) and col.nullable
    assert '("crm_quotations", "pdf_snapshot", "JSONB")' in repo_src("main.py")
