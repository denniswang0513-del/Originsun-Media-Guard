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
from tests.unit._srcscan import code_only, func_body, migration_sql, repo_src

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
    ("已開立", False), ("作廢", True), ("", False), (None, False), ("  ", False),
    # 🔴 `issue_status` 是三值的，而「未開立」是 issue_status_for() 在每一次 PUT
    #    重推出來的（沒填發票號碼就是它）。寫成「已開立以外都算作廢」的話，一張完好
    #    的發票會在客戶那頁長出紅底的「已作廢」—— 而錯誤只有客戶看得到。
    ("未開立", False),
    ("開立中", False)])   # 前端改名前的舊用字，同理不是作廢
def test_only_the_void_status_counts_as_voided(status, expect):
    """舊資料沒填、或還沒拿到發票號碼，都不該被說成作廢。"""
    assert IS.is_voided(status) is expect


def test_void_wording_matches_the_one_ruler():
    """`is_voided` 認的字串要跟決定 `issue_status` 的那支是同一個。
    兩邊各寫一份中文字面值，改名時只改到一邊＝作廢從此判不出來（安靜）。"""
    from core.finance_logic import issue_status_for
    assert IS.VOID == issue_status_for("AB-1", "作廢")
    assert IS.ISSUED == issue_status_for("AB-1")
    assert not IS.is_voided(issue_status_for(""))       # 未開立


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
    assert '("crm_invoices", "share_snapshot", "JSONB")' in migration_sql()


# ── polish 階段零：把這批動到、但還沒有測試釘住的行為記下來 ──────────
#
# 特徵測試：**不判斷對錯，只把現在的行為釘住**。之後任何一次改動讓行為變了，
# 這裡會紅 —— 那時再決定「是修好了」還是「不小心改壞了」。

def test_revoking_a_link_clears_the_token_but_keeps_the_snapshot():
    """撤銷分享連結目前**只清 token、留著快照**。

    不是安全問題（`_share_row` 只能用 token 查得到那一列，token 沒了就查不到），
    也不是新鮮度問題（重新分享會整包重寫）—— 就是一坨留在列上的死資料。
    先釘住現況；要不要順手清掉是另一個決定。
    """
    body = code_only(func_body(repo_src(INV), "async def revoke_invoice_share_link("))
    assert "inv.share_token = None" in body
    assert "share_snapshot" not in body, \
        "行為變了：撤銷現在會動到快照 —— 確認那是刻意的，然後更新這條"


def test_nas_sync_restarts_every_container_even_if_one_fails():
    """NAS 上有兩個容器（對外官網、內部同仁面），服務的是不同的人。

    一台重啟失敗不該讓另一台繼續跑舊碼 —— 所以是「記下失敗、繼續跑完」而不是
    early return。回傳值仍然是「全部都成功嗎」。
    """
    body = code_only(func_body(repo_src("publish_update.py"), "def sync_website_to_nas("))
    assert "for name in NAS_CONTAINERS:" in body
    assert "ok = False" in body and "return ok" in body
    assert "return False" not in body.split("for name in NAS_CONTAINERS:")[1], \
        "容器迴圈裡不可以 early return —— 那會讓後面的容器跑舊碼"


def test_users_json_mirror_can_be_switched_off_by_env():
    """NAS 容器沒有 users.json 正本（`MEDIAGUARD_NO_USERS_JSON=1`）。

    **讀不擋、只擋寫**：檔案不在時 `_load_json` 本來就回空清單，擋讀沒有意義；
    擋寫是為了不要在 NAS 的 code 目錄長出一份沒人讀、也不會同步回來的帳號檔。
    """
    src = code_only(repo_src("core/auth.py"))
    assert "_NO_USERS_JSON = os.environ.get('MEDIAGUARD_NO_USERS_JSON'" in src
    for fn in ("def save_users_json(", "def sync_user_to_json(", "def remove_user_from_json("):
        assert "if _NO_USERS_JSON:" in func_body(src, fn), fn
    assert "if _NO_USERS_JSON:" not in func_body(src, "def load_users_json("), \
        "讀取不該被擋 —— 擋了在 master 上會讓 DB 掛掉時連唯一的登入退路都沒有"


# ── 兩台機器、兩種路徑視角 ────────────────────────────────────

def test_the_customers_filename_is_never_split_with_posix_rules():
    r"""🔴 `file_url` 存的是 Windows 視角的路徑，而這支也跑在 NAS 的 Linux 容器裡。

    那邊 `os.path` 是 posixpath，反斜線不是分隔字元 —— `os.path.basename()` 會把
    **整條內部路徑**原封不動回傳。而那個字串會被寫進 `share_snapshot["file"]["name"]`，
    印在寄給客戶的分享頁上並凍在快照裡。`ntpath.basename` 兩種分隔字元都認。
    """
    import ntpath
    import posixpath
    unc = r"\\192.168.1.132\Archive\00_電子發票\2026\2026-09\x.pdf"
    assert posixpath.basename(unc) == unc, "posix 切不動反斜線 —— 這就是那個 bug"
    assert ntpath.basename(unc) == "x.pdf"
    assert ntpath.basename("/share/Archive/00_電子發票/x.pdf") == "x.pdf", "NAS 視角也要對"

    src = code_only(repo_src(INV))
    assert "os.path.basename" not in src, \
        "這支檔的檔名一律走 ntpath.basename —— os.path 在 Linux 容器上切不動 UNC"


def test_writing_a_file_uses_this_machines_view_and_stores_the_shared_one():
    """寫檔要用**這台看得到的**路徑，存進 DB 要用**大家都翻得回去的**那個。

    🔴 讀那側（`_local_invoice_path`）早就翻譯了，寫這側原本沒有：在 NAS 的
       office-api 上 makedirs 一個 UNC 會長出一個名字裡帶反斜線的資料夾在 `/app`
       底下 —— 寫檔成功、回 200、DB 記下一個**沒有任何一台讀得到**的路徑，
       全程沒有一行 error。
    """
    src = code_only(repo_src(INV))
    assert "def _invoices_write_root(" in src and "to_local_path(_invoices_root())" in src
    assert "def _stored_path(" in src and "to_canonical_path(" in src

    up = code_only(func_body(src, "async def upload_invoice_file("))
    assert "_invoices_write_root()" in up, "寫檔的根目錄要翻譯過"
    assert "_invoices_root()" not in up.replace("_invoices_write_root()", ""), \
        "upload 不該再直接用未翻譯的根目錄"
    assert "inv.file_url = _stored_path(" in up, "存進 DB 的要是 canonical"

    rs = code_only(func_body(src, "def _resync_invoice_file("))
    assert "to_local_path(" in rs and "_invoices_write_root()" in rs
    assert "shutil.move(local_old, target)" in rs, "搬的是翻譯過的來源"


def test_the_internal_download_shares_the_one_whitelist():
    """`/invoice-file?path=` 原本自己寫了一份純 startswith 的白名單比對。

    兩個後果：`…/00_電子發票_舊` 通得過 `…/00_電子發票` 的檢查（少了 os.sep），
    而且沒翻譯 —— 在 NAS 的 office-api 上每一張都 404。
    規則只留 `_local_invoice_path` 一份。
    """
    body = code_only(func_body(repo_src(INV), "async def serve_invoice_file("))
    assert "_local_invoice_path(path)" in body
    assert "startswith" not in body, "白名單比對不要在這裡再寫一份"


def test_the_seller_and_remit_lines_survive_the_trip_to_nas_and_nothing_else_does():
    """分享頁頁尾的賣方（我們是誰）與「匯款資訊」那塊在 NAS 上也要有值。

    那頁由 NAS 的對外容器 serve，而容器裡沒有 settings.json —— 只有 `/publish`
    送過去的那幾個鍵。`company` 不在清單裡的話那兩塊是空的，而那頁是寄給
    客戶與會計師的。

    2026-09-12 起匯款三欄＋存摺影本路徑也送（owner 要分享頁有匯款資訊）；
    `company` 其餘的（地址、電話、章、交檔條款…）這台沒程式讀，子鍵投影仍是白名單 ——
    以後 company 長出新欄位，預設不出去。
    """
    from core.office_settings import EXPORT_SUBKEYS, export_settings
    out, _dropped = export_settings({"company": {
        "name": "源日有限公司", "tax_id": "90371657",
        "bank": "(012)台北富邦中山分行", "account_name": "源日有限公司", "account_no": "82120000062728",
        "bankbook_path": "\\\\192.168.1.132\\Archive\\00_電子發票\\_公司\\存摺影本.pdf",
        "email": "x@y.z", "address": "台北市…", "seal_path": "company_assets/seal.png",
        "delivery_terms": "…"}})
    assert out["company"] == {
        "name": "源日有限公司", "tax_id": "90371657",
        "bank": "(012)台北富邦中山分行", "account_name": "源日有限公司", "account_no": "82120000062728",
        "bankbook_path": "\\\\192.168.1.132\\Archive\\00_電子發票\\_公司\\存摺影本.pdf"}
    assert EXPORT_SUBKEYS["company"] == ("name", "tax_id", "bank", "account_name", "account_no", "bankbook_path")

    # 快照那側讀同一包
    m = IS.meta(IS.make_snapshot({}, "x.pdf", 1), seller=out["company"], bankbook=True)
    assert m["seller"] == {"name": "源日有限公司", "tax_id": "90371657"}
    assert m["remit"] == {"account_name": "源日有限公司", "bank": "(012)台北富邦中山分行",
                          "account_no": "82120000062728", "bankbook": True}


# ── 匯款資訊（owner 2026-09-12）──────────────────────────────

def test_remit_is_live_from_settings_and_never_the_bankbook_path():
    """匯款資訊不是發票上印的、是我們主動給的 —— 從設定即時讀、不進快照；
    存摺影本只出一個布林（路徑是內部檔案系統的東西）。"""
    snap = IS.make_snapshot({"invoice_number": "AB-1"}, "x.pdf", 1)
    assert "remit" not in snap and "bank" not in snap
    company = {"bank": " (012)台北富邦中山分行 ", "account_name": "源日有限公司", "account_no": "82120000062728",
               "bankbook_path": "\\\\nas\\Archive\\00_電子發票\\_公司\\存摺影本.pdf"}
    m = IS.meta(snap, seller=company, bankbook=False)
    assert m["remit"] == {"account_name": "源日有限公司", "bank": "(012)台北富邦中山分行",
                          "account_no": "82120000062728", "bankbook": False}
    assert "bankbook_path" not in str(m)
    # 三欄全空＝整塊不畫
    assert IS.meta(snap, seller={"name": "源日"})["remit"] is None
    assert IS.remit_view({"bank": "", "account_no": None}) is None
    # 帳號有、戶名沒有：照給（頁面自己收掉沒值的列）
    assert IS.remit_view({"account_no": "1"}) == {"account_name": None, "bank": None, "account_no": "1", "bankbook": False}


def test_a_voided_invoice_gets_no_remit_block():
    """已作廢還把帳號擺在旁邊等於請人匯錯錢：作廢一律不給匯款資訊 —— meta 那頭不畫、
    存摺影本端點對直接打網址的人也 404。"""
    company = {"bank": "b", "account_name": "n", "account_no": "1"}
    assert IS.meta(IS.make_snapshot({}, "x.pdf", 1), voided=True, seller=company, bankbook=True)["remit"] is None
    body = code_only(func_body(repo_src(INV), "async def invoice_share_bankbook("))
    assert "surface_gate(request)" in body
    assert 'path = "" if voided else _bankbook_local_path(' in body
    assert "no_store_file(path, filename=ntpath.basename(path))" in body, "永遠 attachment，檔名走 ntpath"


def test_bankbook_lives_under_the_invoices_root_and_goes_through_the_same_whitelist():
    """存摺影本放**發票根目錄**底下、不放 master 的 company_assets/：那頁由 NAS 對外容器
    serve，company_assets 它看不到 —— master 關機時「下載發票」好的、「存摺影本」404，
    同一頁兩顆鈕一顆好一顆壞。讀取走 `_local_invoice_path`（同一份白名單），寫入存
    canonical UNC（`_stored_path`），檔名固定（換檔 NAS 立刻吃到新檔，不用再發版）。"""
    src = repo_src(INV)
    read = code_only(func_body(src, "def _bankbook_local_path("))
    assert "_local_invoice_path(" in read
    up = code_only(func_body(src, "async def upload_bankbook("))
    assert "check_admin(request)" in up
    assert "os.path.join(_invoices_write_root(), _BANKBOOK_DIR)" in up
    assert 'company["bankbook_path"] = _stored_path(filepath)' in up
    assert "_bankbook_ext(head)" in up, "看檔頭不看副檔名"
    assert "company_assets" not in up
    assert '_BANKBOOK_STEM = "存摺影本"' in src
    # meta 只在檔真的開得到時才給 bankbook=True（不要鑄一顆按了 404 的下載鈕）
    meta_body = code_only(func_body(src, "async def invoice_share_meta("))
    assert "bankbook=bool(_bankbook_local_path(company))" in meta_body


def test_the_share_page_only_paints_remit_from_the_response():
    """頁面只畫後端給的 `remit`；沒給整塊不畫（`hidden`）；帳號複製走純數字。"""
    page = repo_src("frontend/invoice-file.html")
    assert '<div class="remit" id="remit" hidden>' in page
    assert "renderRemit(d.remit, bankbookUrl);" in page
    assert 'if (!r) { $("remit").hidden = true; return; }' in page
    assert 'copyText(no.replace(/[^0-9]/g, ""), $("cp-acct-no"))' in page
    assert '$("k-bankbook").hidden = $("f-bankbook").hidden = !r.bankbook;' in page
    assert 'render(d, api + "/download", api + "/bankbook");' in page
    # 客戶看得到的原始碼裡不寫內部路徑／欄位清單
    for word in ("company_assets", "bankbook_path", "invoices_root", "NEVER_SHARE"):
        assert word not in page, word


def test_settings_page_has_the_upload_next_to_the_seal():
    """設定 › 公司資訊：存摺影本上傳在發票章正下方（owner：「發票章那頁可以讓我更新檔案」）。
    路徑鍵進 COMPANY_KEYS（不然存設定時 merge 會把它留住沒錯，但欄位不回填）。"""
    html = repo_src("frontend/index.html")
    seal = html.index('id="company_seal_upload"')
    bank = html.index('id="company_bankbook_upload"')
    assert seal < bank < html.index('id="tab_user_mgmt"')
    js = repo_src("frontend/js/settings/settings-modal.js")
    assert "'bankbook_path'" in js.split("const COMPANY_KEYS = [")[1].split("];")[0]
    assert "fetch(_BANKBOOK_API, { method: 'POST', body: fd, headers: _bh() })" in js, "要自己帶 token（login-modal 的 fetch 白名單不認 /crm/）"
    assert "_bindBankbookUpload();" in js


def test_the_shared_link_domain_has_a_way_in():
    """🔴 `share_public_base` 曾經**完全沒有入口**：後端讀得到、GET 也回，
    但沒有任何畫面送得出去 —— 於是永遠是空字串，整個「共用對外網域」等於不存在。

    這種漏法沒有任何徵兆（不會 error，只會有人說「你給我的網址打不開」），
    所以釘住「那張卡上真的有那個欄位、而且 POST 真的帶著它」。
    """
    from tests.unit._srcscan import js_code_only
    card = js_code_only(repo_src("frontend/tabs/crm/crm-utils.js"))
    assert "opts.extra" in card and "-extra" in card
    assert "if (extraEl) body[extra.key] = extraEl.value.trim();" in card, \
        "🔴 只在欄位真的在 DOM 裡才送 —— CF 給 .js 四小時快取，舊 js 沒有 extra，" \
        "一律帶空字串送會把剛設好的網域洗掉"

    quotes = js_code_only(repo_src("frontend/tabs/crm/crm-quotes.js"))
    assert "share_public_base" in quotes, "報價單那張卡要送得出這個鍵"

    # 後端那支不可以回「剛被 pop 掉的舊鍵」（永遠是空字串）
    body = code_only(func_body(repo_src("routers/crm/quotes.py"), "async def set_quotations_root("))
    assert "public_base(s)" in body
    assert 's.get("quotes_public_base")' not in body.split("return {")[-1]


def test_a_link_is_not_minted_when_the_file_cannot_be_opened():
    """開不到檔就不要鑄連結。

    原本是「開不到就 size=0，照樣回 ok」—— 複製給客戶的連結按下載會 404，
    而**只有他看得到**（我們這邊回的是 ok，按鈕上什麼都沒說）。
    """
    body = code_only(func_body(repo_src(INV), "async def create_invoice_share_link("))
    assert "local = _local_invoice_path(inv.file_url)" in body
    assert "if not local:" in body and "422" in body
    assert body.index("if not local:") < body.index("_new_share_code()"), \
        "先確認開得到檔再鑄短碼 —— 反過來的話失敗那次已經把 token 算出來了"
    assert "size = 0" not in body, "讀不到大小要講出來，不要送一個 0 給客戶看"
