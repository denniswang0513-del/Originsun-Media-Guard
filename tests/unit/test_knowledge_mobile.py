# -*- coding: utf-8 -*-
"""知識庫手機版（owner 2026-09-18：「我想要設計手機版」）。

書頁原本那一段（返回／書名／作者／狀態／標籤／五個分頁／三顆鈕）疊起來要 580px，
一支手機看得到的高度才 844 —— 內容等於被推到螢幕外。這支釘住把它收起來之後不能再長回去：

  1. 頭收成一行，其餘動作進 ⋯ 的抽屜。
  2. 長頁面要能捲：骨架有段落跳轉、章節有前後、有回頂。
  3. 手指按得到：窄螢幕每顆鈕至少 44px。
  4. 「存成結論」先開一格可以改再存（不然結論檔會變成第二份聊天紀錄）。
"""
import re

from tests.unit._srcscan import js_code_only, js_func_body, repo_src

BOOK = "frontend/js/knowledge/book.js"
CHAT = "frontend/js/knowledge/chat.js"
CSS = "frontend/js/knowledge/knowledge.css"
INDEX = "frontend/js/knowledge/index.js"
SHELF = "frontend/js/knowledge/shelf.js"
CTX = "frontend/js/knowledge/ctx.js"
PAGE = "frontend/knowledge.html"


def _media(width):
    """那一段 @media 的內容（媒體區塊的結尾是第一個頂格的 `}`）。"""
    src = repo_src(CSS)
    i = src.index("@media (max-width: %dpx)" % width)
    return src[i:src.index("\n}", i)]


def _narrow_css():
    """窄螢幕兩段合起來：≤899（平板與直式）、≤640（手機）。"""
    return _media(899) + "\n" + _media(640)


# ── 1. 頭收成一行、動作進抽屜 ────────────────────────────────
def test_the_header_is_one_line_on_a_phone():
    css = _narrow_css()
    assert "text-overflow: ellipsis" in css, "書名要截斷，不能換行把分頁推下去"
    for hidden in (".kb .kb-side .a", ".kb .kb-side #kb-tags-row", ".kb .kb-actions"):
        assert hidden in css, hidden + " 要收進抽屜"
    assert ".kb .kb-head .kb-more { display: block" in css, "⋯ 只在手機出現"


def test_the_drawer_holds_every_action_that_was_hidden():
    body = js_func_body(repo_src(BOOK), "export function toggleSheet(")
    for act in ("conclude-from-sheet", "tags-edit", "compile", "rename", "delete"):
        assert act in body, "抽屜少了 " + act
    assert "kb-sheet-dim" in body, "點外面要能關掉"


def test_every_drawer_action_closes_the_drawer():
    """按完抽屜要收掉，不然對話框開在抽屜後面。"""
    src = repo_src(INDEX)
    for act in ("tags-edit", "compile", "rename", "delete", "conclude-from-sheet", "pane"):
        line = next(ln for ln in src.splitlines() if "act === '%s'" % act in ln)
        assert "toggleSheet(false)" in line, act


# ── 2. 長頁面捲得動 ──────────────────────────────────────────
def test_the_skeleton_gets_a_jump_strip_from_real_headings():
    body = js_func_body(repo_src(BOOK), "function _skeletonJumpHtml(")
    assert "startsWith('## ')" in body, "段落標題直接從 markdown 取，不要用正則猜內容"
    assert "heads.length < 2" in body, "只有一段就不用畫這排"


def test_sticky_tabs_have_a_tall_enough_container():
    """sticky 的作用範圍是它的容器 —— 放在只有內容高的 .kb-side 裡等於沒黏。"""
    block = _media(899)
    assert "position: sticky" in block
    assert ".kb .kb-side { display: contents; }" in block


def test_the_back_to_top_button_does_not_collide_with_the_shelf_header():
    """書架的頭本來就叫 .kb-top，回頂那顆不能再用同一個名字。"""
    src = repo_src(BOOK)
    assert "kb-totop" in src
    assert 'class="kb-top"' not in src or "data-kact=\"to-top\"" in src
    assert ".kb .kb-totop" in repo_src(CSS)


# ── 3. 手指按得到 ────────────────────────────────────────────
def test_every_button_on_a_phone_is_at_least_44px():
    """走查實測抓到 43／40／36／32px 各一批；這條擋它們長回去。"""
    css = _narrow_css()
    need = (".kb .kb-tabs button", ".kb .kb-doc .tools .kb-link", ".kb .kb-chapnav .kb-btn",
            ".kb .kb-msg .act button", ".kb .kb-ext .act button", ".kb .kb-ext-head .kb-btn",
            ".kb .kb-conc-edit .act button")
    for sel in need:
        assert re.search(re.escape(sel) + r" \{ min-height: 44px; \}", css), sel + " 沒有補到 44px"


# ── 4. 存成結論先開一格 ─────────────────────────────────────
def test_saving_a_conclusion_opens_an_editor_first():
    src = repo_src(CHAT)
    assert "export function openConclusionEdit(" in src
    assert "export function cancelConclusionEdit(" in src
    line = next(ln for ln in repo_src(INDEX).splitlines() if "act === 'save-conclusion'" in ln)
    assert "openConclusionEdit" in line, "按鈕要開編輯，不是直接存"


def test_the_editor_defaults_to_the_conclusion_lines_not_the_whole_reply():
    """AI 一則常常五六百字；提示要求它用「可存成結論：」收尾，預設就帶那幾行。"""
    body = js_func_body(repo_src(CHAT), "export function openConclusionEdit(")
    assert "_pickLines(m.text)" in body
    assert "|| (m.text || '').trim()" in body, "沒有那段時要退回整則，不能變成存不了"
    assert "CONCLUSION_MARK" in js_func_body(repo_src(CHAT), "function _pickLines(")


def test_the_front_end_mark_matches_the_back_end_one():
    from core.knowledge_logic import CONCLUSION_MARK
    assert ("export const CONCLUSION_MARK = '%s';" % CONCLUSION_MARK) in repo_src("frontend/js/knowledge/ctx.js")


def test_typing_survives_a_repaint():
    """討論分頁一秒輪詢一次；重畫前沒先把字收回 S 的話，他打到一半就沒了。"""
    src = repo_src(CHAT)
    assert "_captureConcEdit();" in js_func_body(src, "export function chatHtml(")
    assert "_captureConcEdit();" in js_func_body(src, "export async function saveConclusion(")


def test_no_emoji_reaches_the_screen():
    """註解裡的紅點是全專案的危險標記，留著；畫面上的字不准有 emoji（同其他前端的規矩）。"""
    for f in (BOOK, CHAT, INDEX, "frontend/js/knowledge/extend.js", "frontend/js/knowledge/shelf.js"):
        assert not re.search(r"[\U0001F300-\U0001FAFF]", js_code_only(repo_src(f))), f

# ── 5. 書架是清單，可以一次拖幾本，也可以先建書名 ───────────
def test_the_shelf_is_a_list_not_a_grid_of_cards():
    """owner 2026-09-18：「我想要有一個書的清單，然後我可以匯入」。
    卡片一本 104px＋間距，手機上十本就要捲很久。"""
    css = repo_src(CSS)
    assert "grid-template-columns: repeat(auto-fill" not in css, "書架不再是卡片格子"
    assert ".kb .kb-card:last-child { border-bottom: 0; }" in css, "清單要靠分隔線不是靠間距"
    assert "min-height: 104px" not in css


def test_one_row_shows_status_and_what_the_book_already_has():
    body = js_func_body(repo_src(SHELF), "export function renderShelf(")
    assert "statusPill(b)" in body
    for fact in ("頁", "章", "有結論", "有筆記"):
        assert fact in body, fact
    assert "extend_new" in body, "延伸有幾則沒讀過，書架上就要看得到"


def test_the_file_input_takes_more_than_one_book():
    src = repo_src(SHELF)
    assert 'id="kb-file" hidden multiple' in src
    assert "export async function uploadMany(" in src
    assert "export async function upload(" not in src, "單檔那支已經被佇列取代，不要留兩條路"


def test_uploads_run_a_few_at_a_time_not_all_at_once():
    """每支上傳都把整包讀進記憶體（上限 300MB）；五本厚書同時傳就是 1GB。"""
    assert "export const UPLOAD_PARALLEL = 2;" in repo_src(CTX)
    body = js_func_body(repo_src(SHELF), "export async function uploadMany(")
    assert "Math.min(UPLOAD_PARALLEL, list.length)" in body


def test_one_bad_file_does_not_stop_the_rest():
    src = repo_src(SHELF)
    one = js_func_body(src, "async function _uploadOne(")
    assert "catch (e)" in one and "return null;" in one, "單本失敗要自己吞掉，回 null"
    many = js_func_body(src, "export async function uploadMany(")
    assert "失敗" in many, "總結要講失敗幾本"


def test_the_upload_rows_survive_the_shelf_repaint():
    """傳完會重抓書架重畫；狀態放在 DOM 裡的話，失敗那本的原因會閃一下就不見。"""
    src = repo_src(SHELF)
    assert "S.uploads" in js_func_body(src, "function _uploadsHtml(")
    assert "_uploadsHtml()" in js_func_body(src, "export function renderShelf(")
    assert "S.uploads = list.map(" in js_func_body(src, "export async function uploadMany(")


def test_a_book_can_start_as_a_title_with_no_file():
    src = repo_src(SHELF)
    assert "export async function createPending(" in src
    assert "'/pending'" in js_func_body(src, "export async function createPending(")
    line = next(ln for ln in repo_src(INDEX).splitlines() if "act === 'new-pending'" in ln)
    assert "createPending()" in line


def test_pending_has_its_own_pill_and_cannot_be_compiled():
    assert "pending: '待補'" in repo_src(CTX)
    body = js_func_body(repo_src(BOOK), "export async function compile(")
    assert "S.book.status === 'pending'" in body, "待補的書要在前端就擋下來，不要讓他按了才吃 409"
    assert "先補 PDF" in body


def test_the_front_end_knows_the_same_statuses_as_the_back_end():
    from services.knowledge_service import STATUSES
    ctx = repo_src(CTX)
    for st in STATUSES:
        assert ("%s: '" % st) in ctx, st + " 前端沒有對應的字"

def test_a_pending_book_has_somewhere_to_put_the_file():
    """先建了書名卻沒有補檔的入口，那個狀態就卡死了。左欄與 ⋯ 抽屜都要有。"""
    src = repo_src(BOOK)
    assert "export async function attachFile(" in src
    assert "'/file'" in src or "/file`" in src
    book = js_func_body(src, "export function renderBook(")
    sheet = js_func_body(src, "export function toggleSheet(")
    for where, body in (("左欄", book), ("抽屜", sheet)):
        assert 'data-kact="attach"' in body, where + "沒有補檔入口"
        assert "補上 PDF" in body, where
    line = next(ln for ln in repo_src(INDEX).splitlines() if "act === 'attach'" in ln)
    assert "kb-book-file" in line


def test_the_read_button_unlocks_the_moment_the_file_lands():
    """補完檔要馬上能按「讀這本書」—— 等重抓那一拍會讓他以為沒補成功。"""
    body = js_func_body(repo_src(BOOK), "export async function attachFile(")
    assert "S.book.status = meta.status" in body
    assert "renderBook();" in body

# ── 6. 手機深色（owner 2026-09-18：「手機 RWD 希望是深色配色 和 logo 匹配」）──
def _page_style():
    src = repo_src(PAGE)
    return src[src.index("<style>"):src.index("</style>")]


#: 深色那一段的開頭（也是淺色色票的結尾）。第一個 `@media` 是上面那個 640 的 brand-mark，不能用。
_DARK_AT = "@media (max-width: 899px)"


def _dark_block():
    """深色 @media 的**內容**，不含 `@media (max-width: 899px)` 那一行本身 ——
    那行自己就有 `width:`，會誤觸下面「不准出現版面規則」那一條。"""
    css = _page_style()
    i = css.index(_DARK_AT) + len(_DARK_AT)
    return css[i:css.index("\n  }", i)]


def test_the_phone_palette_comes_from_the_logo():
    """色票要跟 logo（frontend/img/knowledge-icon.svg）是同一組，不是隨便挑的深藍。"""
    svg = repo_src("frontend/img/knowledge-icon.svg")
    dark = _dark_block()
    assert "#38BDF8" in svg and "#38BDF8" in dark, "強調色＝浪的天藍"
    assert "#12294D" in svg and "#12294D" in dark, "面板＝logo 底的午夜藍"


def test_the_browser_chrome_matches_the_page():
    """捲動時手機瀏覽器上下那條的顏色要跟頁面一樣，不然會露出一塊白。"""
    src = repo_src(PAGE)
    assert 'name="theme-color" content="#0B1B34"' in src
    assert "--bg: #0B1B34;" in _dark_block()


def test_dark_only_swaps_tokens_never_layout():
    """深色那一段只換色票 —— 版面規則共用同一份，不要養出第二套版面。"""
    block = _dark_block()
    for layout in ("display:", "flex", "grid", "position:", "width:", "margin:", "padding:"):
        assert layout not in block, f"深色那段不該出現 {layout}"


def test_danger_is_not_the_accent():
    """🔴 `--red` 在深色底下變成天藍（強調）；刪除那類要是紅的，所以拆成 `--danger`。
    忘了拆的話「刪除這本書」會變成藍字。"""
    css = repo_src(CSS)
    for rule in (".kb .kb-btn.danger", ".kb .note.bad", ".kb .kb-error", ".kb .pill.bad"):
        line = next(ln for ln in css.splitlines() if ln.startswith(rule + " "))
        assert "var(--danger)" in line, rule + " 要用危險色不是強調色"
    dark = _dark_block()
    assert "--danger: #FCA5A5" in dark and "--red: #38BDF8" in dark


def test_no_colour_is_hard_coded_in_the_stylesheet():
    """寫死的顏色換不了 —— 深色底下就會留一塊白。"""
    css = repo_src(CSS)
    code = "\n".join(ln for ln in css.splitlines() if not ln.lstrip().startswith(("/*", "*", "不要")))
    bad = re.findall(r":[^;{}]*?(#[0-9a-fA-F]{3,6}|rgba?\([^)]*\))", code)
    assert not bad, "這些要改走色票：" + str(sorted(set(bad))[:8])


def test_every_token_exists_in_both_palettes():
    """淺色有、深色沒有的話，手機上那一格會掉回淺色的值（例如白底白字）。"""
    css = _page_style()
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", repo_src(CSS)))
    light = css[css.index(":root {"):css.index(_DARK_AT)]
    dark = _dark_block()
    for token in sorted(used):
        assert token + ":" in light, token + " 淺色沒定義"
        assert token + ":" in dark, token + " 深色沒定義（手機上會掉回淺色的值）"


def test_the_desktop_stays_light():
    """owner 只要手機深色；桌機那份不要跟著變。"""
    css = _page_style()
    assert "--bg: #fff;" in css[:css.index(_DARK_AT)]
    assert css.count(_DARK_AT) == 1, "只有一個深色斷點"
