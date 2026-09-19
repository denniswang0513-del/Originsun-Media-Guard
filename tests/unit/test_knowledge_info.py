# -*- coding: utf-8 -*-
"""書籍資訊、圖輯、章末相簿與圖的出處（owner 2026-09-19 這一批）。

  「我希望有個 tab 可以填書的基本資訊」
  「標籤可以很多個」
  「圖片擷取很重要，截取出來需要標注頁數；不用一定要插入頁面，可以在文章底部用相簿、圖說方式呈現」
  「能插入的就插入，但是有抽出來的圖片 就放相簿」
  「同時在 tab 處開一個圖輯，把書裡抽出來的圖片全部集合一處」
  「可以製作圖說」
  「要標明出處」
"""
import re

import pytest

from core import knowledge_logic as kl
from tests.unit._srcscan import func_body, js_func_body, repo_src

CTX = "frontend/js/knowledge/ctx.js"
BOOK = "frontend/js/knowledge/book.js"
INFO = "frontend/js/knowledge/info.js"
GALLERY = "frontend/js/knowledge/gallery.js"
SERVICE = "services/knowledge_service.py"


# ── 書籍基本資訊 ────────────────────────────────────────────
def test_only_whitelisted_fields_are_stored():
    got = kl.normalize_info({"publisher": " 聯經 ", "亂塞的": "x", "isbn": "978"})
    assert got == {"publisher": "聯經", "isbn": "978"}


def test_empty_fields_are_not_kept():
    assert kl.normalize_info({"publisher": "  ", "isbn": ""}) == {}
    assert kl.normalize_info(None) == {} and kl.normalize_info("x") == {}


def test_each_field_has_its_own_length_cap():
    long = kl.normalize_info({"why": "字" * 999, "isbn": "9" * 999})
    assert len(long["why"]) == dict(kl.INFO_FIELDS)["why"]
    assert len(long["isbn"]) == dict(kl.INFO_FIELDS)["isbn"]


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:text/html,x", "ftp://a/b", "a.tw/b"])
def test_the_link_field_only_takes_http(bad):
    """那一格會被畫成可以點的連結（同延伸那邊的規矩）。"""
    assert kl.normalize_info({"link": bad}) == {}
    assert kl.normalize_info({"link": "https://a.tw/b"}) == {"link": "https://a.tw/b"}


def test_the_form_matches_the_back_end_field_list():
    """前端的標籤與後端的鍵要對得起來 —— 少一個那一格就永遠存不進去。"""
    js = repo_src(INFO)
    # 只看 INFO_FIELDS 那一段 —— 同一個檔裡還有 SHARE_PARTS，長得一樣
    block = js[js.index("export const INFO_FIELDS"):]
    block = block[:block.index("];")]
    keys = re.findall(r"\['([a-z_]+)',", block)
    assert keys == list(kl.INFO_KEYS), f"前端 {keys} ≠ 後端 {list(kl.INFO_KEYS)}"


def test_the_share_checkboxes_match_the_back_end_list():
    """勾選的鍵也要對得起來 —— 多一個前端勾了沒用，少一個那項永遠分享不出去。"""
    js = repo_src(INFO)
    block = js[js.index("export const SHARE_PARTS"):]
    block = block[:block.index("];")]
    keys = re.findall(r"\['([a-z_]+)',", block)
    assert keys == list(kl.SHARE_PART_KEYS), f"前端 {keys} ≠ 後端 {list(kl.SHARE_PART_KEYS)}"


def test_the_two_kinds_of_tick_are_shown_apart():
    """「讓人看得到什麼」與「讓人拿得走什麼」後果差很多，混在同一串裡看不出來。"""
    js = repo_src(INFO)
    block = js[js.index("export const SHARE_ABILITIES"):]
    keys = re.findall(r"'([a-z_]+)'", block[:block.index("];")])
    assert keys == list(kl.SHARE_ABILITIES), f"前端 {keys} ≠ 後端 {list(kl.SHARE_ABILITIES)}"
    assert "可以帶走的檔案" in js, "那一段要有自己的小標"


def test_info_replaces_the_whole_block_not_key_by_key():
    """清空一格要真的清掉；逐鍵合併的話舊值會留著。"""
    body = func_body(repo_src(SERVICE), "def update_book(")
    assert 'fields["info"] = kl.normalize_info(info)' in body
    assert "if info is not None:" in body, "沒帶 info 就不要動它"


# ── 標籤可以很多個 ──────────────────────────────────────────
def test_many_tags_are_allowed():
    """owner 2026-09-19：「標籤可以很多個」（原本 10）。"""
    assert kl.TAGS_MAX >= 20
    assert len(kl.normalize_tags([f"t{i}" for i in range(50)])) == kl.TAGS_MAX


# ── 圖：相簿不靠模型挑 ──────────────────────────────────────
def test_the_album_lists_every_figure_not_just_the_referenced_ones():
    """🔴 原本靠提示叫 claude 自己插 `![](assets/…)`。實測三本書只引用了 3/50、0/10、0/35 ——
    靠模型挑，大部分的圖永遠不會出現。現在由程式一律列出。"""
    body = js_func_body(repo_src(BOOK), "function _galleryHtml(")
    assert "ch.assets" in body
    assert "inline" not in body, (
        "owner 2026-09-19：「能插入的就插入，但是有抽出來的圖片就放相簿」"
        "—— 文中插過的照樣要列，相簿是完整索引不是「剩下的」")


def test_the_chapter_carries_its_own_figures():
    body = func_body(repo_src(SERVICE), "def read_chapter(")
    assert "kl.assets_in_range(" in body and '"assets": assets' in body


def test_the_chapter_list_carries_page_ranges():
    """圖輯要靠頁碼把圖分到各章；沒有頁碼就全部掉進「其他頁」。"""
    body = func_body(repo_src(SERVICE), "def _chapter_rows(")
    assert '"start_page"' in body and '"end_page"' in body


def test_the_prompt_still_lets_it_insert_where_it_fits():
    lines = kl.asset_lines([{"name": "p042-1.jpg", "page": 42, "caption": "圖2.1"}])
    assert "![說明](assets/檔名)" in lines, "能插入的就插入"
    assert "一定" in lines and "最後面" in lines, "要讓它知道漏掉也不要緊（相簿會列）"
    assert "你看不到圖" in lines


# ── 圖說是 AI 寫的 ─────────────────────────────────────────
def test_the_model_writes_a_clean_caption():
    """從 PDF 刮下來的圖說常常黏著正文，例如
    「圖2-1：卑南社與馬蘭社位置圖 資料來源：…163 三」尾巴那個「163 三」是頁碼與下一段開頭。"""
    md = ("## 核心概念\n內容\n\n## 圖說\n"
          "- p076-1.jpg｜南灣海域空間概圖。\n- `assets/p077-1.jpg`｜大板埒捕鯨漁場範圍。\n")
    body, caps = kl.parse_captions(md, ["p076-1.jpg", "p077-1.jpg"])
    assert caps == {"p076-1.jpg": "南灣海域空間概圖。", "p077-1.jpg": "大板埒捕鯨漁場範圍。"}
    assert "## 圖說" not in body, "那一段要取走，不留在文章裡"
    assert "## 核心概念" in body


def test_a_made_up_filename_never_gets_in():
    _, caps = kl.parse_captions("## 圖說\n- ../etc/passwd｜壞的\n- p999-1.jpg｜不在這本書裡\n",
                                ["p001-1.jpg"])
    assert caps == {}


def test_no_caption_section_leaves_the_text_alone():
    md = "## 核心概念\n內容\n"
    assert kl.parse_captions(md) == (md, {})


def test_a_missing_caption_falls_back_to_the_scraped_one():
    body = func_body(repo_src(SERVICE), "async def _compile(")
    assert "kl.parse_captions(body" in body
    assert "**(read_meta(book_id).get(\"asset_captions\") or {})" in body, (
        "要合併不是覆蓋 —— 模型沒寫到的那幾張要留著從 PDF 刮下來的原文")


# ── 出處 ───────────────────────────────────────────────────
def test_every_figure_says_where_it_came_from():
    """owner 2026-09-19：「要標明出處」。圖是從他買的書裡抽出來的。"""
    ctx = repo_src(CTX)
    assert "export function figureSource(" in ctx
    body = js_func_body(ctx, "export function figureSource(")
    assert "出自《" in body and "頁" in body
    for f in (BOOK, GALLERY):
        assert "figureSource(" in repo_src(f), f + " 沒標出處"


def test_the_caption_is_not_printed_twice():
    """相簿每張本來就有 figcaption；`_fillAssets` 再補一行 alt 會變成出現兩次。"""
    body = js_func_body(repo_src(BOOK), "async function _fillAssets(")
    assert "img.closest('figure')" in body


# ── 圖輯分頁 ───────────────────────────────────────────────
def test_the_gallery_is_its_own_tab():
    ctx = repo_src(CTX)
    assert "['gallery', '圖輯']" in ctx and "['info', '資訊']" in ctx
    assert "export function galleryHtml(" in repo_src(GALLERY)


def test_the_gallery_groups_by_chapter():
    body = js_func_body(repo_src(GALLERY), "function _byChapter(")
    assert "chapterList(" in body
    assert "其他頁" in body, "落在任何一章之外的也要看得到，不能默默消失"


def test_switching_books_drops_the_previous_gallery():
    """不清掉的話會看到上一本書的圖。"""
    assert "S.assets = []" in js_func_body(repo_src(BOOK), "export async function openBook(")
