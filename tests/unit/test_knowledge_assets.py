# -*- coding: utf-8 -*-
"""章節裡的圖與表（docs/KNOWLEDGE_BASE_PLAN.md §9.7）。

owner 2026-09-18：「如果章節有重要圖片 或表格 我希望你也可以截取出來」。

兩者走**不同的路**，這支先把這件事釘住：
  表是文字 → 抽成 markdown 直接塞進全文，切章時跟著走，claude 看得到內容也引用得到。
  圖是檔案 → 存成檔，claude **看不到**，提示只給頁碼與圖旁邊的字，由它決定要不要引用。

另外釘：什麼叫「重要」的圖、路徑安全、以及私有的圖怎麼進到畫面裡。
"""
import re

import pytest

from core import knowledge_logic as kl
from tests.unit._srcscan import func_body, js_func_body, repo_src

SERVICE = "services/knowledge_service.py"
ROUTER = "routers/api_knowledge.py"
BOOK = "frontend/js/knowledge/book.js"
MDLITE = "frontend/js/shared/md-lite.js"


# ── 1. 什麼是「重要」的圖 ───────────────────────────────────
def test_decorations_are_dropped():
    """頁首小圖示、分隔線那種。實測那本書裡有一張 40x40 的。"""
    assert kl.keep_image(564, 800)
    assert not kl.keep_image(40, 40)
    assert not kl.keep_image(600, 100), "只有一邊夠大也不算圖"


def test_covers_and_scanned_pages_are_dropped():
    """實測「台灣菜」503 頁：封面與整頁掃描那幾頁本文 0 字、圖佔 52%～100%；
    真正的插圖都在有本文的頁面上、佔兩成版面。71 張因此收斂成 50 張，每張都有圖說。"""
    assert kl.keep_page_image(280, 0.21), "有本文、佔兩成＝插圖"
    assert not kl.keep_page_image(0, 1.0), "封面"
    assert not kl.keep_page_image(0, 0.52), "整頁掃描"
    assert not kl.keep_page_image(500, 0.95), "整頁的底圖不是插圖"


def test_a_book_cannot_dump_hundreds_of_images():
    assert kl.MAX_ASSETS <= 100 and kl.MAX_ASSETS_PER_PAGE <= 6
    body = func_body(repo_src(SERVICE), "def save_images(")
    assert "kl.MAX_ASSETS" in body


# ── 2. 檔名與路徑 ───────────────────────────────────────────
def test_the_name_says_which_page_it_came_from():
    assert kl.asset_name(42, 1, "jpeg") == "p042-1.jpg"
    assert kl.asset_name(7, 2, "png") == "p007-2.png"
    assert kl.asset_page("p042-1.jpg") == 42


@pytest.mark.parametrize("bad_ext", ["gif", "webp", "svg", "exe", ""])
def test_only_png_and_jpg_are_written(bad_ext):
    """svg 會夾帶 script；其他格式手機不一定開得了。副檔名不在白名單就整張跳過。"""
    assert kl.asset_name(1, 1, bad_ext) == ""


@pytest.mark.parametrize("bad", ["../meta.json", "..\\..\\settings.json", "p042-1.exe",
                                 "p42-1.jpg", "p042-1.jpg/../x", "", "assets/p042-1.jpg"])
def test_a_bad_name_never_becomes_a_path(bad):
    assert not kl.is_valid_asset(bad)
    body = func_body(repo_src(SERVICE), "def asset_path(")
    assert "kl.is_valid_asset(name)" in body, "拼路徑前一定要先過白名單"
    assert "raise" in body


# ── 3. 表走文字那條路 ───────────────────────────────────────
def test_a_table_goes_into_the_full_text_so_chapters_carry_it():
    block = kl.table_block(31, 1, "|a|b|\n|---|---|\n|1|2|")
    assert "[[表 p.31-1]]" in block and "|a|b|" in block
    assert kl.table_block(31, 1, "") == "", "空表格不要留一個空標題在全文裡"
    body = func_body(repo_src(SERVICE), "def extract_all(")
    assert "_page_tables(page, i)" in body


def test_tiny_grids_are_not_tables():
    """排版用的格線會被 find_tables 當成表。"""
    assert kl.keep_table(7, 2)
    assert not kl.keep_table(1, 5) and not kl.keep_table(5, 1)


def test_the_chapter_prompt_says_what_the_table_marks_are():
    p = kl.chapter_prompt({"title": "書"}, {"n": 1, "start_page": 1, "end_page": 9}, "章文")
    assert "[[表 p.N-k]]" in p


# ── 4. 圖走「它看不到」那條路 ───────────────────────────────
def test_the_prompt_admits_claude_cannot_see_the_image():
    """不講的話它會開始描述一張沒看過的圖。"""
    lines = kl.asset_lines([{"name": "p042-1.jpg", "page": 42, "caption": "圖2.1 菜牌"}])
    assert "你看不到圖" in lines
    assert "不要描述你沒看過的畫面" in lines
    assert "圖2.1 菜牌" in lines and "assets/p042-1.jpg" in lines


def test_no_figures_means_no_section():
    assert kl.asset_lines([]) == ""
    p = kl.chapter_prompt({"title": "書"}, {"n": 1, "start_page": 1, "end_page": 9}, "章文", assets=[])
    assert "這一章有這幾張圖" not in p


def test_a_chapter_only_gets_its_own_figures():
    assets = [{"name": "p010-1.jpg", "page": 10}, {"name": "p050-1.jpg", "page": 50},
              {"name": "p090-1.jpg", "page": 90}]
    got = kl.assets_in_range(assets, 40, 60)
    assert [a["page"] for a in got] == [50]
    p = kl.chapter_prompt({"title": "書"}, {"n": 2, "start_page": 40, "end_page": 60}, "章文", assets=assets)
    assert "p050-1.jpg" in p and "p010-1.jpg" not in p


def test_old_books_get_their_figures_backfilled():
    """2026-09-18 之前上傳的書沒有這一步；編譯前補一次，而且抽不出來不該擋住編譯。"""
    src = repo_src(SERVICE)
    body = func_body(src, "async def _compile(")
    assert "ensure_assets" in body and "except Exception" in body
    ensure = func_body(src, "def ensure_assets(")
    assert "if have:" in ensure, "已經有圖就不要重抽"
    assert "save_images(" in ensure and "extract_all(" not in ensure, (
        "🔴 補抽只抽圖，不准重寫 full_text —— 那會把現在那一份整個蓋掉。"
        "2026-09-18 實測：它把「這本抽不出文字」的狀態也蓋掉了，掃描檔因此變成編譯成功")
    assert "FULLTEXT_FILE" not in ensure


# ── 5. 私有的圖怎麼進畫面 ───────────────────────────────────
def test_md_lite_never_emits_a_live_src():
    """它不知道也不該知道怎麼取那個檔；而且不給 src 就永遠不會自己連外。"""
    src = repo_src(MDLITE)
    assert "data-md-src" in src
    body = js_func_body(src, "function inline(")
    assert "img data-md-src" in body
    assert 'img src=' not in src and "<img src" not in src


def test_external_images_are_refused():
    src = repo_src(MDLITE)
    body = js_func_body(src, "function inline(")
    m = re.search(r"!\\\\\[.*?\)/g", body)
    assert "https?" not in (m.group(0) if m else ""), "圖片只收相對路徑，不收外部網址"
    assert "src.includes('..')" in body, "不准往上跳目錄"


def test_the_page_fetches_images_with_the_token():
    """`<img src>` 送不了 Authorization，所以走 fetch + blob。"""
    body = js_func_body(repo_src(BOOK), "async function _fillAssets(")
    assert "bearerHeader()" in body
    assert "URL.createObjectURL" in body
    assert "^assets\\/[A-Za-z0-9._-]+$" in body, "前端也再擋一次路徑"


def test_the_blobs_are_released():
    """一本書翻幾十章，不收回會一直吃記憶體。"""
    src = repo_src(BOOK)
    assert "URL.revokeObjectURL" in js_func_body(src, "function _revokeAssets(")
    assert "_revokeAssets();" in js_func_body(src, "export function renderPane(")


def test_the_caption_is_shown_not_just_in_alt():
    """書裡的圖沒有說明等於看不懂；alt 只有讀螢幕看得到。"""
    body = js_func_body(repo_src(BOOK), "async function _fillAssets(")
    assert "kb-cap" in body
    assert ".kb .kb-md .kb-cap" in repo_src("frontend/js/knowledge/knowledge.css")


def test_both_asset_endpoints_call_the_guard():
    src = repo_src(ROUTER)
    names = re.findall(r'@router\.get\("/\{book_id\}/assets[^"]*"\)\s*\nasync def (\w+)', src)
    assert len(names) == 2, names
    for name in names:
        assert "_guard(request)" in func_body(src, "async def %s(" % name), name
    assert src.index('@router.get("/{book_id}/assets")') < src.index('@router.get("/{book_id}/assets/{name}")')


def test_the_image_is_not_cached_publicly():
    """書是私有的，圖也是 —— 不能讓中間的代理或瀏覽器留一份在磁碟上。
    全專案的規矩是回檔案的 handler 走 `core.no_store`（test_private_files_never_land_in_disk_cache）。"""
    body = func_body(repo_src(ROUTER), "async def asset_get(")
    assert "no_store_file(" in body
    assert "Cache-Control" not in body, "不要自己寫快取標頭，走共用那支"
