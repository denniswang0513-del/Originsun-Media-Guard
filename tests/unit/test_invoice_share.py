# -*- coding: utf-8 -*-
"""發票分享頁 `/e/{短碼}`（owner 2026-09-10）。

兩件事一起做：
  1. **master 關機也拿得到** —— 端點從 master 限定的 token_router 移到 public_router
     （NAS 對外容器掛的就是它）。
  2. **從「點了直接下載」變成一頁**：發票資訊排版 ＋ 一顆下載鈕。

規劃與拍板紀錄：docs/INVOICE_SHARE_PLAN.md（§3.5 欄位白名單、§8 拍板）。
"""
import pytest

from core import invoice_share as IS
from core import share_link as SL
from tests.unit._srcscan import code_only, func_body, repo_src

INV = "routers/crm/invoice_files.py"


# ── 欄位白名單：這頁是寄給外面的人的 ─────────────────────────

def test_only_what_is_printed_on_the_invoice_gets_out():
    """🔴 一條規則：**只顯示那張發票上本來就印著的東西**。

    收件人手上就有那張證明聯，把上面已經有的欄位排版出來不增加他知道的事。
    而系統裡另外那半邊（代開費、催收狀態、母帳私帳、內部案號、內部備註）
    是我們的帳務與流程 —— 那些出去了只有客戶看得到，我們永遠不會知道。
    """
    full = {k: "X" for k in IS.SNAPSHOT_FIELDS}
    full.update({k: "洩漏" for k in IS.NEVER_SHARE})
    out = IS.public_view(full)
    leaked = sorted(k for k in IS.NEVER_SHARE if k in out)
    assert not leaked, f"這些不該出現在客戶那頁上：{leaked}"
    assert sorted(out) == sorted(IS.SNAPSHOT_FIELDS)


def test_the_two_lists_do_not_overlap():
    """白名單與黑名單重疊＝有人把不該給的欄位加進了 SNAPSHOT_FIELDS。"""
    both = set(IS.SNAPSHOT_FIELDS) & set(IS.NEVER_SHARE)
    assert not both, both


@pytest.mark.parametrize("key", ["commission", "payment_status", "entity",
                                 "applicant", "notes", "title", "recipient_address"])
def test_the_ones_that_would_hurt_most(key):
    """個別點名幾個 —— 上面那條是集合斷言，這條讓「是誰」出現在失敗訊息裡。

    代開費＝我們跟開票方之間的事；催收狀態＝我們的內部進度；
    title 可能有人拿它記內部案名；收件地址是對方自己的資料，
    但連結一被轉發就等於外洩了它。
    """
    assert key not in IS.public_view({key: "值"})
    assert key not in IS.meta(IS.make_snapshot({key: "值"}, "a.pdf", 1), voided=False)


def test_empty_becomes_none_so_the_page_can_skip_the_row():
    """空字串正規化成 None：頁面靠「是不是 None」決定整列畫不畫。
    留一個「統一編號　—」的空格比不畫還難看。"""
    out = IS.public_view({"tax_id": "  ", "company_name": "某某公司", "amount_total": 0})
    assert out["tax_id"] is None and out["company_name"] == "某某公司"
    assert out["amount_total"] == 0, "0 是金額不是空值，不可以被吃掉"


def test_dates_come_out_as_plain_days():
    from datetime import datetime, timezone
    out = IS.public_view({"invoice_date": datetime(2026, 9, 8, 23, 30, tzinfo=timezone.utc)})
    assert out["invoice_date"] == "2026-09-08"


# ── 快照：畫面不可以跟他下載的那張對不起來 ────────────────────

def test_snapshot_freezes_what_the_customer_sees():
    """檔案是固定的、DB 那筆是活的。事後改了金額，客戶那頁跟著變、而他手上的 PDF 還是舊的
    —— 畫面與附件自相矛盾，**只有他看得到**。所以按分享的那一刻定稿。"""
    snap = IS.make_snapshot({"amount_total": 189000, "invoice_number": "AB-1"},
                            file_name="x.pdf", file_size=214000)
    assert snap["amount_total"] == 189000
    assert snap["file"] == {"name": "x.pdf", "size": 214000}
    assert snap["at"], "要記下定稿時間"
    # 之後 DB 改了，快照不動 —— meta 只讀快照
    assert IS.meta(snap, voided=False)["amount_total"] == 189000


def test_voided_is_live_not_frozen():
    """🔴 作廢是唯一走即時值的東西：那是收件人**事後**必須知道的事。
    凍進快照的話，發票作廢了客戶那頁還是好好的。"""
    snap = IS.make_snapshot({"invoice_number": "AB-1"}, "x.pdf", 1)
    assert IS.meta(snap, voided=True)["voided"] is True
    assert IS.meta(snap, voided=False)["voided"] is False
    assert "voided" not in snap and "issue_status" not in snap


@pytest.mark.parametrize("status,expect", [
    ("已開立", False), ("作廢", True), ("", False), (None, False), ("  ", False)])
def test_blank_issue_status_is_not_voided(status, expect):
    """舊資料沒填 issue_status 不該被說成作廢。"""
    assert IS.is_voided(status) is expect


def test_has_snapshot_needs_a_file():
    assert not IS.has_snapshot(None) and not IS.has_snapshot({})
    assert not IS.has_snapshot({"file": {"name": ""}})
    assert IS.has_snapshot(IS.make_snapshot({}, "x.pdf", 1))


# ── 對外網址：報價與發票共用一個 ──────────────────────────────

def test_one_setting_for_both_share_links():
    """owner 2026-09-10：報價與發票共用同一個對外網址設定 ——
    分兩個只會有一天其中一個忘了填。"""
    assert SL.public_base({"share_public_base": "https://a.com/"}) == "https://a.com"
    # 舊鍵 fallback：CF 給 .js 四小時快取，那一輪的舊分頁還在送舊鍵
    assert SL.public_base({"quotes_public_base": "https://b.com"}) == "https://b.com"
    assert SL.public_base({"share_public_base": "https://a.com",
                           "quotes_public_base": "https://b.com"}) == "https://a.com", "新鍵優先"
    assert SL.public_base({}) == "", "沒設定＝回相對路徑，前端沿用 location.origin"


def test_share_url_falls_back_to_a_relative_path():
    assert SL.share_url("/e/abc") == "/e/abc"
    assert SL.share_url("/e/abc", "https://x.com/") == "https://x.com/e/abc"
    assert SL.share_url("") == ""


@pytest.mark.parametrize("bad", ["example.com", "https://x.com/e", "javascript:alert(1)", "//x.com"])
def test_a_broken_base_is_rejected_before_it_reaches_a_customer(bad):
    """收垃圾的後果是安靜的：連結是複製給客戶的，錯了不會有 error log，
    只會有人說「你給我的網址打不開」。"""
    value, err = SL.clean_base(bad)
    assert err and not value


def test_quotes_and_invoices_go_through_the_same_helper():
    """兩邊各寫一份「怎麼組網址」，總有一天會分岔。"""
    q = code_only(func_body(repo_src("routers/crm/quotes.py"), "def _public_base("))
    i = code_only(func_body(repo_src(INV), "def _share_public_base("))
    for body in (q, i):
        assert "share_link" in body or "public_base" in body


# ── I/O 那一側 ───────────────────────────────────────────────

def test_path_is_translated_before_the_whitelist_check():
    """🔴 `file_url` 存的是 **master 視角**的路徑（UNC／磁碟代號）。NAS 容器上
    `\\\\192.168.1.132\\Archive\\…` 是 `/share/Archive/…` —— 不翻譯的話那邊一律 404，
    而且是安靜的（客戶看到「檔案不存在」，我們這邊什麼都沒發生）。

    而且白名單比對要在**翻譯之後**：翻譯前比對等於拿兩個不同視角的字串比。
    """
    body = code_only(func_body(repo_src(INV), "def _local_invoice_path("))
    assert "to_local_path(" in body
    assert body.index("to_local_path(") < body.index("startswith("), "先翻譯再比對"
    assert "to_local_path(_invoices_root())" in body, "root 也要翻，不然是兩個視角在比"
    # 純 startswith 會讓 `…/00_電子發票_舊` 通過 `…/00_電子發票` 的檢查
    assert "os.sep" in body, "前綴比對要帶路徑分隔符，不然相鄰同名目錄會被放行"


def test_the_file_is_always_an_attachment():
    """🔴 owner 2026-09-10 拍板不做內嵌預覽，連「在新分頁開啟」都拿掉。

    inline 送出使用者上傳的檔＝在官網網域上執行別人給的內容，而上傳黑名單
    `core.project_folders.BLOCKED_UPLOAD_EXTS` 並沒有擋 .svg／.html。
    不 inline 就完全沒有這個面 —— 別為了「順手可以線上看」把它加回來。
    """
    src = code_only(repo_src(INV))
    body = func_body(src, "async def serve_invoice_by_share_token(")
    assert "no_store_file(" in body and "filename=" in body
    assert "inline" not in src.lower(), "整支檔都不該出現 inline"


def test_public_endpoints_are_mounted_where_the_nas_container_can_see_them():
    """掛 public_router ＝ NAS 對外容器也吃得到（master 關機客戶照樣打得開）。
    每一支都要自己 `await surface_gate` —— master 的 /e/{code} 有擋一次，
    但 NAS 那條沒有經過 main.py。"""
    src = code_only(repo_src(INV))
    for dec in ('@public_router.get("/public/invoice-file/{token}/meta")',
                '@public_router.get("/public/invoice-file/{token}/download")',
                '@public_router.get("/public/invoice-file/{token}")'):
        assert dec in src, dec
    assert "@token_router." not in src, "舊的 master 限定掛法要清掉"
    for fn in ("async def invoice_share_meta(", "async def invoice_share_download(",
               "async def download_invoice_file_public("):
        assert "await surface_gate(request)" in code_only(func_body(src, fn)), fn


def test_old_long_links_still_just_download():
    """那條的存在意義就是「改版前寄出去的連結不要死」。跟著改成回一頁，
    對方（多半是會計師）點了會拿到一個他沒預期的東西。"""
    body = code_only(func_body(repo_src(INV), "async def download_invoice_file_public("))
    assert "serve_invoice_by_share_token(token)" in body


def test_every_share_press_refreshes_what_the_customer_sees():
    """這顆鈕的語意是「我現在要把這條寄出去」—— 那一刻客戶會看到的就該是現在這一版。
    不重新定稿的話，改過金額之後再複製一次連結，客戶看到的還是舊的。
    也因為這樣不需要另外做一顆「更新分享內容」。"""
    body = code_only(func_body(repo_src(INV), "async def create_invoice_share_link("))
    assert "invoice_share.make_snapshot(" in body
    assert "if not inv.share_token:" in body, "短碼仍要冪等（寄兩次信不能讓先寄的失效）"
    assert body.index("inv.share_snapshot =") > body.index("if not inv.share_token:"), \
        "定稿在鑄碼之後，不要只在第一次鑄碼時才做"
    assert '"url":' in body and "share_link.share_url(" in body


def test_snapshot_is_built_from_a_whitelist_not_the_whole_row():
    """整列 `__dict__` 丟進 make_snapshot 等於把「以後有人加了新欄位」變成潛在外洩。"""
    body = code_only(func_body(repo_src(INV), "def _inv_dict("))
    assert "invoice_share.SNAPSHOT_FIELDS" in body


def test_old_links_without_a_snapshot_say_so_instead_of_faking_one():
    """這次改版前鑄的連結沒有快照。拿 DB 活值硬湊一頁，正是這件事要避免的
    「畫面與附件對不起來」。誠實回報，重新按一次分享就會定稿。"""
    body = code_only(func_body(repo_src(INV), "async def invoice_share_meta("))
    assert "invoice_share.has_snapshot(" in body and "status_code=503" in body


def test_short_link_serves_the_page_on_both_entry_points():
    """`/e/{code}` 現在回的是那一頁（不是檔案），而且 master 與 NAS 對外容器都要有
    —— 只做 master 那邊的話，關機時客戶還是打不開。"""
    m = code_only(func_body(repo_src("main.py"), "async def _short_invoice_file("))
    assert "invoice-file.html" in m and "surface_gate(request)" in m
    w = code_only(repo_src("main_website.py"))
    assert '@app.get("/e/{code}", include_in_schema=False)' in w
    assert "invoice-file.html" in w and "surface_gate(request)" in w


def test_backfill_is_dry_run_by_default_and_idempotent():
    """改版前鑄的連結沒有快照 → `/meta` 會 503。這支一次補齊（＝人工去每張按一次
    「複製連結」的效果）。

    契約跟 migrate-files 一樣：**預設 dry-run**，`?apply=true` 才寫。
    冪等 —— 已經有快照的跳過，重跑不會蓋掉已經定稿的內容。
    """
    body = code_only(func_body(repo_src(INV), "async def backfill_share_snapshots("))
    assert "check_admin(request)" in body, "掃全公司的發票、整批寫資料 —— 管理員限定"
    assert "apply: bool = Query(False)" in code_only(repo_src(INV))
    assert "if invoice_share.has_snapshot(inv.share_snapshot):" in body, "冪等"
    assert "if apply:" in body and "if apply and done:" in body, "dry-run 不准 commit"
    assert "inv.updated_at" not in body, \
        "補資料不是使用者改了發票 —— 動 updated_at 會讓「最近更新」整批跳到今天"
    assert "no_file" in body, "有連結卻沒有檔的要列出來，不要靜默寫一個指向空氣的快照"


def test_the_money_redaction_exemption_stays_tiny_and_justified():
    """🔴 `MoneyRedactRoute` 對匿名請求會抹掉金額欄位 —— 那是它存在的理由。

    但這頁的未稅／稅額／合計是**收件人手上那張發票本來就印著的數字**：抹掉不會讓誰
    更安全（他有那張紙），只會讓那頁變成一排空格，然後有人為了「修好」它去把整層關掉。
    所以開一個窄的例外，而不是動那一層的邏輯。

    這條測試守的是**例外不要長大**。門檻在 core/money.py 的註解裡：憑證是逐字比對
    的可撤銷連結、回的是白名單投影、那些數字收件人已經拿在手上 —— 三個都要成立。
    """
    from core.money import MONEY_EXEMPT_PREFIXES
    assert len(MONEY_EXEMPT_PREFIXES) <= 2, \
        f"例外變多了（{MONEY_EXEMPT_PREFIXES}）—— 每加一條都要先過那三個門檻"
    assert all(p.startswith("/api/v1/crm/public/") for p in MONEY_EXEMPT_PREFIXES), \
        "只有匿名分享面有資格；登入後的端點請走 money_view"
    src = code_only(repo_src("core/money.py"))
    body = func_body(src, "        async def handler(request: Request) -> Response:")
    assert "MONEY_EXEMPT_PREFIXES" in body
    assert body.index("MONEY_EXEMPT_PREFIXES") < body.index("can_see_money(request)"), \
        "例外要在判斷權限之前就放行，不然還是會走到抹除那條路"


def test_amounts_actually_survive_for_the_share_page():
    """反面對照：例外沒生效的話上面那條會永遠綠（因為它只看原始碼）。
    這條確認發票的三個金額欄真的在被抹除的名單裡 —— 也就是「沒有例外就會被抹掉」。"""
    from core.money import MONEY_FIELDS
    for f in ("amount_ex_tax", "tax_amount", "amount_total"):
        assert f in MONEY_FIELDS, f
        assert f in IS.SNAPSHOT_FIELDS, f


def test_column_and_startup_migration_both_exist():
    """加欄位要兩處同時有：model 與 main.py startup 的 ADD COLUMN 清單。
    只加 model 的話既有的生產庫永遠不會長出那一欄，而且是靜默的。"""
    from sqlalchemy.dialects.postgresql import JSONB
    from db.models import CrmInvoice
    col = CrmInvoice.__table__.columns["share_snapshot"]
    assert isinstance(col.type, JSONB) and col.nullable
    assert '("crm_invoices", "share_snapshot", "JSONB")' in repo_src("main.py")
