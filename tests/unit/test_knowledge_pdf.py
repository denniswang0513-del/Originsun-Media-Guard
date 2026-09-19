# -*- coding: utf-8 -*-
"""下載 PDF、與把一本書掛到案子上（owner 2026-09-19 這一批）。

  「這裡多一個 pdf 下載，讓大家可以下載資料」
  「這裡也要可以連結現有的專案列表」
  「列表的列出來的方式，和工作時數填寫的規則相同」

🔴 公開那一份 PDF 跟公開那一頁是**同一包資料** —— 他沒勾的東西不可以從 PDF 漏出去。
   這支測試大半在問這一件事。
"""
import re

import pytest

from core import knowledge_logic as kl
from services import knowledge_pdf as kpdf
from tests.unit._srcscan import code_only, func_body, js_func_body, repo_src

ROUTER = "routers/api_knowledge.py"
PUBLIC_ROUTER = "routers/api_knowledge_public.py"
SHARE = "services/knowledge_share.py"
PDF = "services/knowledge_pdf.py"
BOOK_JS = "frontend/js/knowledge/book.js"
INFO_JS = "frontend/js/knowledge/info.js"
PAGE = "frontend/share.html"
PICKER = "routers/api_backup.py"


def _full(**kw):
    d = {"title": "台灣菜的文化史", "author": "陳玉箴"}
    d.update(kw)
    return d


# ── 1. PDF 只印這一包裡有的東西 ────────────────────────────
def test_a_part_that_is_not_in_the_data_is_not_in_the_pdf():
    """公開那條已經照勾選篩過了；PDF 不可以自己再去撈。"""
    html = kpdf.build_html(_full(extend=[{"title_zh": "有勾的"}]))
    assert "有勾的" in html
    for word in ("章節重點", "圖輯", "我的筆記", "骨架"):
        assert word not in html, word + " 沒在資料裡卻印出來了"


def test_the_pdf_never_asks_the_disk_for_anything_by_itself():
    """`build_html` 只會讀傳進去的 dict —— 不 import service、不開檔。"""
    body = func_body(repo_src(PDF), "def build_html(")
    for bad in ("knowledge_service", "open(", "read_doc", "list_assets"):
        assert bad not in body, "build_html 不可以自己去拿資料：" + bad


def test_the_public_pdf_prints_exactly_what_the_public_page_shows():
    src = repo_src(PUBLIC_ROUTER)
    body = func_body(src, "async def shared_pdf(")
    assert "kshare.public_pdf" in body, "要走公開頁那一包（已經照勾選篩過、也認過能不能下載）"
    assert "full_view" not in body, "🔴 公開那條絕不可以用整本那一包"
    assert "404" in body or "HTTPException" in body


def test_the_private_pdf_is_the_whole_book():
    body = func_body(repo_src(ROUTER), "async def book_pdf(")
    assert "_guard(request)" in body, "書是私有的"
    assert "kshare.full_view" in body


def test_chat_and_full_text_are_still_not_in_any_view():
    """討論與全文任何情況都不出去 —— 整本那一包也一樣。"""
    body = code_only(func_body(repo_src(SHARE), "def _build("))
    for bad in ("read_chat", "full_text", "chat.json"):
        assert bad not in body, bad


def test_the_whole_book_view_does_include_every_tickable_part():
    body = func_body(repo_src(SHARE), "def full_view(")
    assert "SHARE_PART_KEYS" in body


# ── 2. 版面：表格與圖跟畫面上一樣 ──────────────────────────
def test_tables_survive_into_the_pdf():
    """owner 2026-09-19：「表格都跑掉了」—— 那是分享頁那次；PDF 這邊一開始就要會。"""
    html = kpdf.build_html(_full(notes="| 項目 | 數字 |\n| --- | --- |\n| 桌 | 3 |\n"))
    assert "<table>" in html and "<th>項目</th>" in html and "<td>3</td>" in html


def test_an_inline_figure_is_embedded_not_linked():
    """PDF 是用 file:// 開的，`src="assets/x.jpg"` 在裡面永遠是破圖。"""
    html = kpdf.build_html(_full(notes="![宴席菜單](assets/p012-1.jpg)"),
                           {"p012-1.jpg": "data:image/jpeg;base64,AAAA"})
    assert 'src="data:image/jpeg;base64,AAAA"' in html
    assert "<figcaption>宴席菜單</figcaption>" in html
    assert "assets/p012-1.jpg" not in html


def test_a_figure_we_could_not_read_is_skipped_rather_than_broken():
    html = kpdf.build_html(_full(notes="![沒有的圖](assets/p999-1.jpg)"), {})
    assert "<img" not in html and "沒有的圖" not in html


def test_the_gallery_says_where_each_picture_came_from():
    """owner 2026-09-19：「要標明出處」（畫面上有，PDF 也要有）。"""
    html = kpdf.build_html(
        _full(gallery=[{"name": "p076-1.jpg", "page": 76, "caption": "南灣海域概圖"}]),
        {"p076-1.jpg": "data:image/jpeg;base64,BBBB"})
    assert "南灣海域概圖" in html
    assert "出自《台灣菜的文化史》" in html and "第 76 頁" in html


def test_a_gallery_picture_without_a_caption_still_says_so():
    html = kpdf.build_html(_full(gallery=[{"name": "p001-1.jpg", "page": 1, "caption": ""}]),
                           {"p001-1.jpg": "data:image/png;base64,CCCC"})
    assert "（原書沒有圖說）" in html


def test_the_notes_say_the_summaries_are_not_the_original_text():
    """章節與骨架是**針對原書整理的筆記**。PDF 會被轉寄出去，那句話要在紙上。"""
    html = kpdf.build_html(_full(chapters=[{"n": 1, "title": "開場", "md": "重點"}]))
    assert "不是原文" in html
    assert "支持原作者與出版社" in kpdf.build_html(_full())


def test_every_chapter_gets_its_own_heading():
    html = kpdf.build_html(_full(chapters=[{"n": 1, "title": "甲", "md": "一"},
                                           {"n": 2, "title": "乙", "md": "二"}]))
    assert "第 1 章 甲" in html and "第 2 章 乙" in html


def test_the_chapter_title_is_not_printed_twice():
    """每章的 md 第一行本來就是 `# 第 N 章 …`（2026-09-19 在 dev 的 PDF 上看到重複）。"""
    html = kpdf.build_html(_full(chapters=[{"n": 1, "title": "圖與表",
                                            "md": "# 第 1 章 圖與表\n\n內容"}]))
    assert html.count("第 1 章 圖與表") == 1


def test_the_title_is_escaped():
    html = kpdf.build_html(_full(title="<script>x</script>"))
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html


# ── 3. 要內嵌哪幾張 ────────────────────────────────────────
def test_the_gallery_and_the_inline_ones_are_both_collected():
    names = kpdf.wanted_images(_full(
        gallery=[{"name": "p001-1.jpg"}],
        notes="![a](assets/p002-1.jpg)",
        chapters=[{"n": 1, "md": "![b](assets/p003-1.jpg)"}]))
    assert names == ["p001-1.jpg", "p002-1.jpg", "p003-1.jpg"]


def test_the_same_picture_is_only_embedded_once():
    """圖輯列的那張也可能插在文章裡；base64 塞兩次檔案就大一倍。"""
    names = kpdf.wanted_images(_full(gallery=[{"name": "p001-1.jpg"}],
                                     notes="![a](assets/p001-1.jpg)"))
    assert names == ["p001-1.jpg"]


def test_there_is_a_ceiling_on_how_many_pictures_go_in():
    assert 10 <= kpdf.MAX_IMAGES <= 200
    body = func_body(repo_src(PDF), "def images_for(")
    assert "MAX_IMAGES" in body


def test_a_made_up_filename_never_becomes_a_path():
    body = func_body(repo_src(PDF), "def images_for(")
    assert "kl.is_valid_asset(" in body, "檔名要過白名單才拼路徑"
    assert "ks.asset_path(" in body, "拼路徑只走那一支（它自己用 book_dir）"


@pytest.mark.parametrize("bad", ["../../etc/passwd", "a/b.jpg", "", "x.exe"])
def test_the_whitelist_actually_refuses_those(bad):
    assert not kl.is_valid_asset(bad)


def test_the_download_gets_a_readable_filename():
    assert kpdf.filename_for({"title": "台灣菜的文化史"}) == "台灣菜的文化史-研究筆記.pdf"
    assert "/" not in kpdf.filename_for({"title": "a/b:c"})
    assert kpdf.filename_for({}).endswith(".pdf")


def test_the_pdf_file_is_not_left_on_disk_and_not_cached():
    body = func_body(repo_src(PDF), "async def pdf_response(")
    assert "no_store_file(" in body, "🔴 私有／原書內容：不要讓中間的代理留一份"
    assert "unlink_later(" in body, "暫存檔送完要刪"


def test_a_machine_without_playwright_says_so_instead_of_crashing():
    """NAS 的容器刻意沒裝 Playwright（同報價單那支）。"""
    body = func_body(repo_src(PDF), "async def pdf_response(")
    assert "503" in body and "ImportError" in body


def test_making_the_pdf_does_not_block_everyone_else():
    """🔴 最多 60 張圖各讀一次檔再轉 base64 —— 留在事件迴圈上，一個人按下載
    就讓整台主控停住好幾秒（2026-09-19 /polish BUG-9 的另一半）。"""
    body = func_body(repo_src(PDF), "async def pdf_response(")
    assert "asyncio.to_thread" in body
    assert "build_html(" in body[body.index("to_thread"):], "組版面那一段要在執行緒裡"


def test_a_page_full_of_decorations_does_not_swallow_the_real_figure():
    """編號要用「留下來的第幾張」。用「內嵌的第幾張」的話，一頁上百張小圖之後
    那張真的圖會拿到 `p001-137`，白名單不認 → 整張靜默消失。"""
    body = func_body(repo_src("services/knowledge_service.py"), "def _page_images(")
    assert "kl.asset_name(n, len(out) + 1," in body
    assert "enumerate(imgs" not in body, "不要再用內嵌序號"
    # 留下來的最多就是每頁上限，所以名字一定在白名單裡
    assert kl.is_valid_asset(kl.asset_name(1, kl.MAX_ASSETS_PER_PAGE, "jpeg"))


def test_a_late_answer_never_paints_the_wrong_book():
    """開 B 書時 A 書遲到的延伸資料不可以蓋上來（loadGallery／loadShare 早就有這道）。"""
    body = js_func_body(repo_src("frontend/js/knowledge/extend.js"), "export async function loadExtend(")
    assert "const bookId = S.book.id" in body
    assert body.count("S.book.id !== bookId") >= 2, "兩次 await 之後都要判一次"


# ── 3.5 原書的 PDF 原檔 ────────────────────────────────────
# owner 2026-09-19：「我的 pdf 希望放上書的 pdf」→ 他選「兩顆鈕分開下載」。
def test_the_private_side_always_has_the_original():
    """他自己那一面不用勾 —— 那本書是他上傳的。"""
    assert "_guard(request)" in func_body(repo_src(ROUTER), "async def book_source(")


def test_the_public_side_needs_a_tick_for_the_original():
    """owner 2026-09-19：「研究報告與書籍都可以」—— 所以公開那一面也有這支，
    但 🔴 **預設是關的**（`SHARE_PARTS` 的 source＝False），要他自己勾。"""
    body = func_body(repo_src(PUBLIC_ROUTER), "async def shared_source(")
    assert "kshare.public_source" in body, "要不要給由 knowledge_share 決定"
    assert "no_store_file(" in body
    assert "source" not in kl.SHARE_DEFAULT


def test_the_original_download_is_not_cached_anywhere():
    """整本原書：不要讓中間的代理或瀏覽器在磁碟上留一份。"""
    body = func_body(repo_src(ROUTER), "async def book_source(")
    assert "no_store_file(" in body


def test_a_book_with_no_file_yet_says_so_instead_of_500():
    body = func_body(repo_src("services/knowledge_service.py"), "def source_path(")
    assert "BookHasNoFile" in body, "待補的書要回 409，不是爆掉"
    assert "book_dir(" in body, "id 只能從 book_dir 變成路徑"


def test_the_download_keeps_the_name_he_uploaded_it_with():
    body = func_body(repo_src("services/knowledge_service.py"), "def source_filename(")
    assert "source_name" in body and "title" in body, "沒有原檔名就退回書名"
    assert "kl.safe_filename(" in body


def test_one_place_cleans_up_download_names():
    """兩份 PDF 共用同一支 —— 兩邊各寫一次正則，哪天漏了反斜線就變成檔名裡帶路徑。"""
    assert "kl.safe_filename(" in func_body(repo_src(PDF), "def filename_for(")


@pytest.mark.parametrize("bad, worry", [
    ("../../etc/passwd", "/"),
    (r"C:\\Users\\x\\book.pdf", "\\"),
    ("a:b*c?.pdf", ":"),
    ("線上讀\r\n.pdf", "\n"),
])
def test_a_nasty_filename_never_survives(bad, worry):
    got = kl.safe_filename(bad)
    assert worry not in got, got
    assert got and not got.startswith(".")


def test_an_empty_name_still_gets_something():
    assert kl.safe_filename("") == "book.pdf"
    assert kl.safe_filename("   ...   ") == "book.pdf"


def test_a_very_long_name_is_trimmed():
    assert len(kl.safe_filename("書" * 300)) <= 80


def test_the_header_offers_both_files_separately():
    """owner 2026-09-19 選的是「兩顆鈕分開下載」，不是合成一份。"""
    body = js_func_body(repo_src(BOOK_JS), "function _headLinksHtml(")
    assert "下載研究筆記" in body and "下載原書" in body


def test_a_book_with_no_file_does_not_offer_the_original():
    """待補的書按下去只會拿到 409 —— 那顆鈕不要出現。"""
    body = js_func_body(repo_src(BOOK_JS), "function _headLinksHtml(")
    assert "'pending'" in body


def test_the_original_download_also_carries_the_token():
    body = js_func_body(repo_src(BOOK_JS), "export function downloadSource(")
    assert "authDownload(" in body and "/source" in body


# ── 4. 掛在哪個案子 ────────────────────────────────────────
def test_the_project_link_keeps_the_label_it_was_picked_with():
    got = kl.normalize_project({"id": "abc12345", "label": "  2026  好客戶  某某案 "})
    assert got == {"id": "abc12345", "label": "2026 好客戶 某某案"}


@pytest.mark.parametrize("bad", [None, "x", {}, {"id": ""}, {"id": "../x"}, {"id": "ab"},
                                 {"id": "a" * 65}, {"label": "沒有 id"}])
def test_anything_that_is_not_a_real_project_id_is_dropped(bad):
    """這個值會被拼進網址（/project.html?id=…）。"""
    assert kl.normalize_project(bad) == {}


def test_a_missing_label_falls_back_to_the_id():
    assert kl.normalize_project({"id": "abc12345"})["label"] == "abc12345"


def test_clearing_the_project_really_clears_it():
    """送 `{}` 就是不掛案子 —— 逐鍵合併的話舊的會留著。"""
    body = func_body(repo_src("services/knowledge_service.py"), "def update_book(")
    assert 'fields["project"] = kl.normalize_project(project)' in body
    assert "if project is not None:" in body, "沒帶 project 就不要動它"


def test_the_book_carries_its_project_back_to_the_screen():
    body = func_body(repo_src("services/knowledge_service.py"), "def _summary(")
    assert '"project": kl.normalize_project(' in body


# ── 5. 清單的列法＝工時那一份 ──────────────────────────────
def test_the_list_is_the_same_one_the_timesheet_uses():
    """owner 2026-09-19：「列表的列出來的方式，和工作時數填寫的規則相同」。

    自己再寫一份排序／去重的話，兩邊會慢慢長歪 —— 規則住在 project_picker。
    """
    body = code_only(func_body(repo_src(PICKER), "async def list_project_options("))
    assert "from services.project_picker import list_options" in body
    assert "list_options(session" in body
    for bad in ("order_by", "sorted(", "CrmProject"):
        assert bad not in body, "列法不要在這裡重寫：" + bad


def test_the_picker_does_not_live_in_the_knowledge_router():
    """🔴 知識庫那支 router 不碰 DB、不碰 core.ledger（書住在 D:\，跟 Postgres 沒關係）——
    tests/unit/test_knowledge_base.py 釘著那兩條。專案清單對外的口本來就在 api_backup。"""
    src = code_only(repo_src(ROUTER))
    for bad in ("project_picker", "db.session", "core.ledger"):
        assert bad not in src, "知識庫那支不該有：" + bad


def test_the_project_list_carries_no_money():
    """這支只是「選一個案子」。帶了金額欄，私帳那條例外就不成立了。"""
    body = func_body(repo_src(PICKER), "async def list_project_options(")
    fields = re.search(r'for k in \(([^)]*)\)', body).group(1)
    for word in ("price", "amount", "budget", "cost", "fee", "total", "profit", "root"):
        assert word not in fields.lower(), fields


def test_private_ledger_projects_follow_the_same_visibility_rule_as_everywhere():
    """owner 2026-08-28「連專案都看不到」—— 沒有 mine scope 的人不該從這裡看到私帳案名。

    備份頁那支的例外是為了備份**實體資料夾**才成立的，不會自動延伸到這支。
    """
    body = func_body(repo_src(PICKER), "async def list_project_options(")
    assert "hide_mine_projects(request)" in body
    assert "MINE" in body


def test_the_picker_endpoint_is_guarded_and_survives_a_dead_database():
    body = func_body(repo_src(PICKER), "async def list_project_options(")
    assert "check_lan_or_logged_in(request)" in body
    assert "db_offline" in body, "DB 不通不該讓整個資訊頁壞掉"


# ── 6. 畫面 ────────────────────────────────────────────────
def test_the_book_header_offers_both_the_pdf_and_the_project():
    body = js_func_body(repo_src(BOOK_JS), "function _headLinksHtml(")
    assert "下載研究筆記" in body
    assert "/project.html?id=" in body


def test_the_private_pdf_goes_through_an_authorised_download():
    """`<a href>` 送不了 Authorization —— 點下去會是 401。"""
    body = js_func_body(repo_src(BOOK_JS), "export function downloadPdf(")
    assert "authDownload(" in body
    assert "<a" not in body


def test_the_info_tab_picks_a_project_with_the_shared_popup():
    js = repo_src(INFO_JS)
    assert "attachProjectPop" in js, "挑法也要跟工時那一格同一支"
    assert "data-proj-pick" in js
    body = js_func_body(js, "async function _projectOptions(")
    assert "'/api/v1/projects/picker'" in body


def test_the_popup_gets_this_pages_palette_and_wins_the_cascade():
    """🔴 `js/shared/project-pop.js` 用 `ensureStyle` 在**執行時**把自己的 <style> 附到
    head 最後面（預設是內部系統的深色皮）。這一頁的覆寫如果只寫 `.proj-pop`，分數一樣、
    它後到就贏 —— 桌機白底上會冒出一塊黑的（2026-09-19 截圖才看到）。"""
    css = repo_src("frontend/js/knowledge/knowledge.css")
    assert "body .proj-pop {" in css, "分數要比元件自己那份高"
    block = css[css.index("body .proj-pop {"):]
    block = block[:block.index("}") + 1]
    for token in ("--pp-bg", "--pp-ink", "--pp-head", "--pp-on"):
        assert token in block, token + " 沒重塗"
    assert "#" not in block, "色票走 var()，不要寫死（手機是深藍、桌機是白）"


def test_the_popup_module_is_in_the_import_map():
    """import map 漏了它 → 那個檔不會被版本號打斷快取，改版後使用者拿到舊的。"""
    html = repo_src("frontend/knowledge.html")
    assert "/js/shared/project-pop.js" in html


def test_emptying_the_box_unhooks_the_project():
    body = js_func_body(repo_src(INFO_JS), "export async function saveInfo(")
    assert "dataset.pid" in body
    assert "project" in body


def test_the_public_page_offers_the_download_too():
    """owner 2026-09-19：「讓大家可以下載資料」—— 重點在「大家」。"""
    html = repo_src(PAGE)
    assert "/api/v1/knowledge/public/" in html and "/pdf" in html
    assert "download>" in html


def test_the_public_page_does_not_start_sending_a_token():
    html = repo_src(PAGE)
    assert "Authorization" not in html and "auth_token" not in html
