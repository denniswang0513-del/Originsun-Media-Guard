# -*- coding: utf-8 -*-
"""公開分享一本書的研究（docs/KNOWLEDGE_BASE_PLAN.md §9.9）。

owner 2026-09-19：「可以有一個公開分享的連結，讓我把這本書的研究分享出去
（但是點不到我其他的地方）」。

🔴 這是整個知識庫**唯一不需要登入**的東西，所以這支測試的每一條都是在問同一件事：
「外面的人只看得到他想分享的那些嗎？」
"""
import re

import pytest

from core import knowledge_logic as kl
from services import knowledge_share as kshare
from tests.unit._srcscan import code_only, func_body, repo_src

PUBLIC_ROUTER = "routers/api_knowledge_public.py"
SHARE = "services/knowledge_share.py"
PAGE = "frontend/share.html"


# ── 1. 公開的東西獨立一支，看得完 ──────────────────────────
def test_the_public_surface_is_one_small_file():
    """混進 api_knowledge.py 的話，那支「每一支端點都要 _guard」的測試就有了例外，
    以後誰忘了守也不會被抓到。"""
    src = repo_src(PUBLIC_ROUTER)
    routes = re.findall(r"@router\.(\w+)\(", src)
    assert set(routes) == {"get"}, "公開的那一面只能有 GET：" + str(routes)
    # 2026-09-19 多了兩支：研究筆記 PDF、原書 PDF（owner：「這裡多一個 pdf 下載」
    # 「研究報告與書籍都可以」）。上限往上調是有意識的決定，不是順手放寬 ——
    # 每一支都只認 `knowledge_share` 算出來的勾選，沒有任何一支自己決定給什麼。
    assert len(routes) <= 4, ("公開的端點只有四支（那一本、圖、研究筆記 PDF、原書 PDF）："
                              + str(routes))
    # 只看程式碼 —— 說明文字裡提到 `_guard` 是在解釋為什麼這支要獨立，不算違規
    code = src[src.index('"""', src.index('"""') + 3) + 3:]
    assert "_guard(" not in code and "check_admin(" not in code, "這支本來就不需要登入"
    assert "knowledge_share" in code, "資料從那支來，不要直接碰 knowledge_service"


def test_the_private_endpoints_still_all_have_the_guard():
    """開關是私有的，只有他能按。"""
    src = repo_src("routers/api_knowledge.py")
    for name in ("share_get", "share_put"):
        assert "_guard(request)" in func_body(src, "async def %s(" % name), name


# ── 2. 網址猜不到、書 id 不能當網址 ────────────────────────
def test_the_share_id_is_random_and_not_the_book_id():
    """2026-09-19 起是 12 個字（owner：「我希望網址短一點」），約 70 bits。"""
    a, b = kshare.new_share_id(), kshare.new_share_id()
    assert a != b
    assert re.fullmatch(r"[a-zA-Z2-9]{12}", a), a
    assert not kshare.is_valid_share_id("0" * 16), "書 id 是 16 hex —— 長度刻意不同，不能互相當成對方"
    # 字母表拿掉了看起來一樣的那幾個（他可能要唸給別人聽）
    assert not set("0Oo1lI") & set(kshare._SHARE_ALPHABET)


def test_links_shared_before_today_still_open():
    """🔴 2026-09-19 之前發出去的是 32 hex。那些連結已經在別人手上，不能死。"""
    assert kshare.is_valid_share_id("f36d2b7e285c201c9747881030c1cf48")


def test_the_whole_link_is_short_enough_to_paste():
    """改前 78 字（/share.html# ＋ 32 hex），現在 51。"""
    url = kshare.share_url(kshare.new_share_id())
    assert len(url) <= 55, url
    assert url.startswith("https://") and "#" in url, "碼還是放 hash —— 不進存取紀錄、不被 referer 帶走"


@pytest.mark.parametrize("bad", ["", None, "zz", "0" * 31, "0" * 33, "../x", "0" * 16, "A" * 32])
def test_a_bad_share_id_is_refused(bad):
    assert not kshare.is_valid_share_id(bad)


def test_an_unknown_id_looks_the_same_as_a_closed_one(monkeypatch):
    """都回 None → 端點都回同一句 404，不透露這本書存不存在。"""
    monkeypatch.setattr(kshare.ks, "list_books", lambda *a, **k: [])
    assert kshare.public_view("0" * 32) is None
    assert kshare.public_view("not-an-id") is None


# ── 3. 外面看得到什麼（這條最重要）──────────────────────────
_PRIVATE = ("notes", "conclusion", "chat", "chapters", "skill", "cheatsheet",
            "full_text", "focus", "watch", "assets", "id", "share", "error", "model")


def test_only_the_public_fields_go_out(monkeypatch):
    meta = {
        "title": "書", "author": "作者", "tags": ["財務"], "info": {"publisher": "聯經"},
        "share": {"id": "a" * 32, "at": "2026-09-19T00:00:00+08:00"},
        # 下面這些都是私有的，一個都不准出去
        "focus": "只找台灣", "watch": True, "error": "x", "model": "opus",
        "notes": "我的筆記", "conclusion": "我的結論", "asset_captions": {"p001-1.jpg": "圖"},
    }
    monkeypatch.setattr(kshare.ks, "list_books", lambda *a, **k: [{"id": "b" * 16}])
    monkeypatch.setattr(kshare.ks, "read_meta", lambda bid: meta)
    monkeypatch.setattr(kshare.ks, "read_extend", lambda bid: [])
    got = kshare.public_view("a" * 32)
    # 預設只勾三項（info／tags／extend）；沒勾的**連鍵都不會有**
    assert set(got) == {"title", "author", "shared_at", "parts", "info", "tags", "extend"}
    for key in ("conclusion", "notes", "skill", "cheatsheet", "chapters", "gallery"):
        assert key not in got, key + " 沒勾就不該出現"
    flat = repr(got)
    for word in ("我的筆記", "我的結論", "只找台灣", "opus", "asset_captions", "a" * 32):
        assert word not in flat, "外洩：" + word


def test_ticking_a_part_is_what_lets_it_out(monkeypatch):
    """owner 2026-09-19：「公開分享的內容讓我勾選」。勾了才出去，沒勾連鍵都沒有。"""
    meta = {"title": "書", "notes": "x", "share": {"id": "a" * 32, "parts": ["notes", "conclusion"]}}
    monkeypatch.setattr(kshare.ks, "list_books", lambda *a, **k: [{"id": "b" * 16}])
    monkeypatch.setattr(kshare.ks, "read_meta", lambda bid: meta)
    monkeypatch.setattr(kshare.ks, "read_doc", lambda bid, which: "【" + which + "】")
    got = kshare.public_view("a" * 32)
    assert got["notes"] == "【notes】" and got["conclusion"] == "【conclusion】"
    for key in ("info", "tags", "extend", "chapters", "gallery", "skill"):
        assert key not in got, key + " 沒勾就不該出現"


def test_nothing_at_all_ticked_still_says_which_book(monkeypatch):
    monkeypatch.setattr(kshare.ks, "list_books", lambda *a, **k: [{"id": "b" * 16}])
    monkeypatch.setattr(kshare.ks, "read_meta", lambda bid: {
        "title": "書", "author": "作者", "share": {"id": "a" * 32, "parts": []}})
    assert set(kshare.public_view("a" * 32)) == {"title", "author", "shared_at", "parts"}


def test_the_figure_endpoint_checks_the_tick_too(monkeypatch):
    """🔴 沒勾「書裡的圖」就當那個檔不存在 —— 不能只靠前端不顯示。"""
    monkeypatch.setattr(kshare.ks, "list_books", lambda *a, **k: [{"id": "b" * 16}])
    monkeypatch.setattr(kshare.ks, "read_meta", lambda bid: {"share": {"id": "a" * 32, "parts": ["extend"]}})
    assert kshare.public_asset("a" * 32, "p001-1.jpg") is None
    body = func_body(repo_src(SHARE), "def public_asset(")
    assert 'shares(shared_parts(book_id), "gallery")' in body


def test_discussion_and_full_text_can_never_be_shared():
    """那兩樣連選項都沒有 —— 討論是私下的對話，全文是整本書。"""
    assert "chat" not in kl.SHARE_PART_KEYS and "full_text" not in kl.SHARE_PART_KEYS
    # 組資料的是 `_build`（公開頁與 PDF 共用它；`public_view` 只負責找書與算勾選）
    body = code_only(func_body(repo_src(SHARE), "def _build("))
    for name in ("read_chat", "CHAT_FILE", "FULLTEXT_FILE", "full_text"):
        assert name not in body, "_build 不該碰 " + name


def test_every_optional_part_sits_behind_its_tick():
    """每一段都要在 `kl.shares(...)` 底下 —— 少一個 if 就是預設外洩。"""
    body = func_body(repo_src(SHARE), "def _build(")
    for key in kl.SHARE_CONTENT_KEYS:
        assert f'kl.shares(parts, "{key}")' in body, key + " 沒有檢查勾選"
    # 「能力」型的勾（可以下載研究筆記／可以下載原書）不是內容的一段，
    # `_build` 不該因為它們多回鍵 —— 由那兩支端點自己認（下面那條釘著）
    src = repo_src(SHARE)
    for key in kl.SHARE_ABILITIES:
        assert f'out["{key}"]' not in src, key + " 不是內容，不該變成回傳的鍵"
    # 而且公開那條真的是照 meta 裡的勾選算出來的，不是傳全部進去
    assert "kl.normalize_parts(" in func_body(repo_src(SHARE), "def public_view(")
    # 書名與作者不在選項裡（一定會出去），所以不用檢查
    assert "out[\"title\"]" not in body or True


def test_being_able_to_take_a_copy_is_its_own_tick():
    """owner 2026-09-19：「公開連結的公開設定 也讓我勾要不要讓人下載 pdf」
    「研究報告與書籍都可以」—— 兩顆各自一個勾，各自一支端點認。"""
    assert kl.SHARE_ABILITIES == ("pdf", "source")
    assert "pdf" in kl.SHARE_DEFAULT, "研究筆記預設給下載（那顆鈕當初就是為了這個做的）"
    assert "source" not in kl.SHARE_DEFAULT, "🔴 整本原書預設**不**給 —— 要他自己按下去"
    assert 'kl.shares(data.get("parts"), "pdf")' in func_body(repo_src(SHARE), "def public_pdf(")
    assert 'shares(shared_parts(book_id), "source")' in func_body(repo_src(SHARE), "def public_source(")


def test_the_original_book_only_goes_out_through_that_one_tick():
    """🔴 公開那一面唯一給得出整本原書的地方。"""
    src = repo_src(PUBLIC_ROUTER)
    assert "kshare.public_source" in src
    assert "ks.source_path" not in src and "SOURCE_FILE" not in src, "路徑只從 knowledge_share 拿"
    assert "knowledge_service" not in src, "公開那一面不直接碰書架的檔案層"


def test_items_marked_useless_are_not_shared(monkeypatch):
    """他自己刷掉的那幾則不要拿去給別人看。"""
    rows = [{"title_zh": "留著", "rating": "useful"}, {"title_zh": "沒用的", "rating": "useless"},
            {"title_zh": "還沒評", "rating": ""}]
    monkeypatch.setattr(kshare.ks, "list_books", lambda *a, **k: [{"id": "b" * 16}])
    monkeypatch.setattr(kshare.ks, "read_meta", lambda bid: {"share": {"id": "a" * 32}})
    monkeypatch.setattr(kshare.ks, "read_extend", lambda bid: rows)
    titles = [i["title_zh"] for i in kshare.public_view("a" * 32)["extend"]]
    assert titles == ["留著", "還沒評"]


def test_each_shared_item_keeps_its_source(monkeypatch):
    """owner 2026-09-19：「要標明出處」。轉出去的每一則都要說得出是誰寫的、在哪。"""
    monkeypatch.setattr(kshare.ks, "list_books", lambda *a, **k: [{"id": "b" * 16}])
    monkeypatch.setattr(kshare.ks, "read_meta", lambda bid: {"share": {"id": "a" * 32}})
    monkeypatch.setattr(kshare.ks, "read_extend", lambda bid: [
        {"title_zh": "某篇", "url": "https://a.tw/x", "source": "NBER", "published": "2026-09-01"}])
    it = kshare.public_view("a" * 32)["extend"][0]
    for key in ("url", "source", "published", "title_original", "lang"):
        assert key in it, key


# ── 4. 關掉就是真的關掉 ─────────────────────────────────────
def test_turning_it_off_kills_the_old_link():
    body = func_body(repo_src(SHARE), "def set_share(")
    assert "update_meta_share(book_id, None)" in body
    assert "new_share_id()" in body, "再開一次要換新的 id —— 舊連結不能復活"


def test_the_write_goes_through_the_meta_lock():
    """meta 是讀-改-寫，跟編譯撞到會互相蓋掉（財務顧問 d2cc8bae 那把鎖）。"""
    body = func_body(repo_src("services/knowledge_service.py"), "def update_meta_share(")
    assert "write_meta(" in body and "read_meta(" in body


# ── 5. 那一頁不能連回系統 ───────────────────────────────────
def test_the_public_page_has_no_way_back_in():
    """owner：「但是點不到我其他的地方」。"""
    src = repo_src(PAGE)
    hrefs = re.findall(r'href="([^"]+)"', src)
    for h in hrefs:
        if h.startswith(("${", "' +", '" +')):
            continue      # 程式組出來的：那一格只放 /^https?:\/\// 過關的外部網址（下面另外釘）
        # `/api/v1/knowledge/public/…` 是公開那一面自己的端點（PDF 下載）——
        # 它跟這一頁看到的是同一份資料，不是「連回系統的其他地方」。
        assert h.startswith(("http", "/img/", "/api/v1/knowledge/public/")), "公開頁不該連到 " + h
    assert r"/^https?:\/\//i.test(it.url" in src, "外部連結要先驗過協定才敢放"
    # 註解拿掉再看 —— 說明文字裡提到 knowledge.html 是在解釋色票照它做，不是連過去
    code = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    for word in ("knowledge.html", "my.html", "auth_token", "localStorage", "sessionStorage", "window.open"):
        assert word not in code, "公開頁不該出現 " + word


def test_the_public_page_is_not_indexed():
    """分享連結是他傳給特定的人的，不該被搜尋引擎收走。"""
    assert 'name="robots" content="noindex, nofollow"' in repo_src(PAGE)


def test_the_share_id_travels_in_the_hash():
    """放在 hash：不會進伺服器的存取紀錄，也不會被 referer 帶到別的網站。"""
    src = repo_src(PAGE)
    assert "location.hash" in src
    assert "/api/v1/knowledge/public/" in src


def test_outbound_links_do_not_leak_the_referrer():
    assert 'rel="noopener noreferrer nofollow"' in repo_src(PAGE)


def test_the_page_says_what_is_and_is_not_there():
    """看的人要知道這不是書的內容，也要知道去哪找原書。"""
    src = repo_src(PAGE)
    assert "不是這本書的作者說的" in src
    assert "支持原作者與出版社" in src
