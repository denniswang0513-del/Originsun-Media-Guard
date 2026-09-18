# -*- coding: utf-8 -*-
"""研究助理（知識庫「延伸」）的規則（docs/KNOWLEDGE_BASE_PLAN.md §9）。

釘三件事：
  1. 形狀 —— `延伸.md` 一則一行、來回轉不走樣、壞行不會拖垮整頁。
  2. 流水號 —— 評分認的是**檔內流水號**，不是第幾筆；刪掉中間一則之後不會評到別人身上。
  3. 🔴 安全 —— 這是整個知識庫唯一會讀網頁的一發（網頁＝不可信輸入）：
     只准 WebSearch／WebFetch、cwd 是臨時空目錄、回來的東西只當資料寫檔。
"""
import glob
import os
import re

import pytest

from core import knowledge_logic as kl
from tests.unit._srcscan import func_body, repo_src

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── 形狀 ─────────────────────────────────────────────────────
def _one(**over):
    item = {"n": 1, "date": "2026-09-18", "title_zh": "中文標題", "url": "https://example.com/a",
            "title_original": "Original Title", "lang": "en", "source": "NBER", "published": "2026-09-01",
            "summary_zh": "一句摘要", "chapter_guess": "第 3 章", "why_it_matters": "跟他的現金流規則有關",
            "rating": ""}
    item.update(over)
    return item


def test_one_item_per_line_round_trip():
    items = [_one(), _one(n=2, url="https://example.com/b", rating="useful")]
    md = "\n".join(kl.extend_line(i) for i in items)
    assert md.count("\n") == 1, "一則就是一行"
    assert kl.parse_extend_md(md) == items


def test_separator_inside_a_field_does_not_split_the_row():
    """標題裡本來就有全形直線時，不能把那一行切成兩欄多。"""
    bad = _one(title_original="Money｜Psychology", summary_zh="前\n後")
    back = kl.parse_extend_md(kl.extend_line(bad))
    assert len(back) == 1
    assert back[0]["title_original"] == "Money|Psychology"
    assert back[0]["summary_zh"] == "前 後", "換行要壓掉，不然下一行會被當成新的一則"


def test_a_hand_broken_line_is_skipped_not_fatal():
    md = "# 標題列\n- 欄位不夠\n" + kl.extend_line(_one()) + "\n隨手寫的字\n"
    assert [i["n"] for i in kl.parse_extend_md(md)] == [1]


# ── 流水號 ───────────────────────────────────────────────────
def test_numbers_are_never_reused_after_a_delete():
    items = [_one(n=1), _one(n=2, url="https://b"), _one(n=3, url="https://c")]
    assert kl.next_extend_n(items) == 4
    del items[1]                                     # 中間那則被刪掉
    assert kl.next_extend_n(items) == 4, "號碼不重用，不然新的一則會頂到舊評分"
    assert [i["n"] for i in items] == [1, 3]


def test_new_items_continue_from_start_n():
    raw = '[{"url":"https://x.tw/1","title_zh":"甲"},{"url":"https://x.tw/2","title_zh":"乙"}]'
    assert [i["n"] for i in kl.parse_extend(raw, "2026-09-18", start_n=7)] == [7, 8]


# ── 解析的容錯 ───────────────────────────────────────────────
def test_junk_rows_are_dropped():
    raw = ('前言 [{"url":"https://ok.tw/a","title_zh":"好的"},'
           ' {"url":"not-a-url","title_zh":"沒有協定"},'
           ' {"title_zh":"沒有網址"},'
           ' "整個不是物件"] 後話')
    got = kl.parse_extend(raw, "2026-09-18")
    assert [i["url"] for i in got] == ["https://ok.tw/a"]


def test_already_collected_urls_do_not_come_back():
    raw = '[{"url":"https://a.tw/x/","title_zh":"甲"},{"url":"https://b.tw/y","title_zh":"乙"}]'
    got = kl.parse_extend(raw, "2026-09-18", seen_urls=["https://A.TW/x"])
    assert [i["title_zh"] for i in got] == ["乙"], "大小寫與結尾斜線不同也算同一篇"


def test_the_same_url_twice_in_one_batch_only_counts_once():
    raw = '[{"url":"https://a.tw/x","title_zh":"甲"},{"url":"https://a.tw/x?utm_source=z","title_zh":"甲的追蹤連結"}]'
    assert len(kl.parse_extend(raw, "2026-09-18")) == 1


def test_batch_is_capped():
    raw = "[" + ",".join('{"url":"https://a.tw/%d","title_zh":"t"}' % i for i in range(30)) + "]"
    assert len(kl.parse_extend(raw, "2026-09-18")) == kl.EXTEND_MAX_ITEMS


def test_unreadable_reply_is_empty_not_an_exception():
    assert kl.parse_extend("我找不到東西。", "2026-09-18") == []


def test_manual_one_reports_why_it_failed():
    item, why = kl.parse_extend_one('{"error":"需要訂閱"}', "2026-09-18")
    assert item is None and "訂閱" in why
    item, why = kl.parse_extend_one("不是 JSON", "2026-09-18")
    assert item is None and why
    item, why = kl.parse_extend_one('{"url":"https://a.tw/x","title_original":"Only English"}',
                                    "2026-09-18", n=5)
    assert why == "" and item["n"] == 5
    assert item["title_zh"] == "Only English", "只給一個標題時另一個要補上，畫面不能空一格"


# ── 提示 ─────────────────────────────────────────────────────
def test_prompt_asks_for_translation_and_does_not_restrict_language():
    """owner 2026-09-18：不限制中文，英文或其他語言也可以，要附出處、翻譯與摘要。"""
    p = kl.extend_prompt({"title": "金錢心理學"}, conclusion="先付自己")
    assert "語言不限" in p
    assert "繁體中文" in p and "照翻" in p
    assert "source" in p and "published" in p
    assert "先付自己" in p, "他認同過的原則要帶進去，研究才貼著他走"


def test_prompt_lists_what_was_already_collected():
    p = kl.extend_prompt({"title": "書"}, seen_urls=["https://a.tw/x/"])
    assert "https://a.tw/x" in p


def test_prompts_carry_no_emoji():
    for p in (kl.extend_prompt({"title": "書"}), kl.extend_one_prompt("https://a.tw/x", {"title": "書"})):
        assert "不用 emoji" in p
        assert not re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27bf]", p)


def test_digest_drops_what_he_marked_useless():
    items = [_one(n=1, title_zh="留著", rating="useful"),
             _one(n=2, title_zh="還沒評", rating=""),
             _one(n=3, title_zh="沒用的", rating="useless")]
    d = kl.extend_digest(items)
    assert "留著" in d and "還沒評" in d and "沒用的" not in d
    assert "不是作者說的" in d, "討論時要分得出哪些是書、哪些是網路上的"
    assert kl.extend_digest([]) == ""


def test_chat_prompt_carries_the_digest():
    p = kl.chat_prompt({"title": "書"}, extend="## 延伸\n- 某篇研究")
    assert "某篇研究" in p
    assert "某篇研究" not in kl.chat_prompt({"title": "書"})


# ── 🔴 安全：唯一會讀網頁的一發 ──────────────────────────────
def test_research_pass_gets_only_the_web_tools():
    src = repo_src("services/knowledge_service.py")
    m = re.search(r'^_EXTEND_TOOLS = "(.*)"$', src, re.M)
    assert m, "找不到 _EXTEND_TOOLS"
    assert sorted(m.group(1).split(",")) == ["WebFetch", "WebSearch"], (
        "網頁是不可信輸入：這一發只准搜與讀網頁，給 Read／Write／Bash 就等於把注入的字變成命令")
    body = func_body(src, "async def run_extend(")
    assert "allowed_tools=_EXTEND_TOOLS" in body


def test_research_pass_runs_in_a_throwaway_directory():
    body = func_body(repo_src("services/knowledge_service.py"), "async def run_extend(")
    assert "tempfile.mkdtemp" in body, "cwd 要是臨時空目錄，讓它連這本書的檔案都看不到"
    assert "cwd=tmp" in body
    assert "shutil.rmtree" in body, "跑完要刪掉"
    assert "cwd=d" not in body


def test_every_extend_endpoint_calls_the_guard():
    src = repo_src("routers/api_knowledge.py")
    names = re.findall(r'@router\.\w+\("/\{book_id\}/extend[^"]*"\)\s*\nasync def (\w+)', src)
    assert len(names) == 3, names
    for name in names:
        assert "_guard(request)" in func_body(src, "async def %s(" % name), name


def test_the_extend_file_never_leaves_the_book_folder():
    """跟章節同一條規矩：路徑一律走 `book_dir()`，不自己拼 id。"""
    src = repo_src("services/knowledge_service.py")
    for fn in ("read_extend", "_write_extend"):
        assert "book_dir(book_id)" in func_body(src, "def %s(" % fn), fn
    assert "book_id" not in func_body(src, "def _extend_counts("), (
        "_extend_counts 吃資料夾路徑，不吃 id —— 書架列表每本叫一次，不該各自拼一次路徑")


@pytest.mark.parametrize("bad", ["ftp://a.tw/x", "file:///c:/x", "javascript:alert(1)", "a.tw/x"])
def test_manual_collect_rejects_non_http_urls(bad):
    from services import knowledge_service as ks
    with pytest.raises(ValueError):
        ks.start_extend("0" * 16, bad)


def test_the_extend_file_lives_with_the_book_not_in_the_repo():
    """書不進 git（KNOWLEDGE_BASE_PLAN §11）；延伸也是書的一部分，跟 meta 與章節同一個資料夾。"""
    src = repo_src("services/knowledge_service.py")
    assert 'EXTEND_FILE = "延伸.md"' in src
    assert not glob.glob(os.path.join(REPO, "**", "延伸.md"), recursive=True), (
        "延伸.md 跟書一起放在 D 槽的書架下，不該出現在 repo 裡")

def test_collecting_the_same_url_twice_is_refused_before_it_costs_a_call():
    """實測：重複貼同一篇，原本會照樣叫一次 claude 跑兩分鐘，然後靜靜丟掉。"""
    src = repo_src("services/knowledge_service.py")
    body = func_body(src, "def start_extend(")
    assert "read_extend(book_id)" in body and "norm_url" in body, (
        "收之前要先比對已經收過的網址")


def test_a_missing_item_does_not_borrow_the_chapter_message():
    src = repo_src("services/knowledge_service.py")
    assert "ExtendNotFound" in func_body(src, "def rate_extend(")
    router = repo_src("routers/api_knowledge.py")
    assert "except ks.ExtendNotFound:" in router
    assert "延伸裡沒有這一則" in router


def test_the_pane_never_prints_a_raw_stage_token():
    """畫面上出現「正在找資料…（running）」那種英文狀態字就是漏出來了。"""
    js = repo_src("frontend/js/knowledge/extend.js")
    assert "esc(stage)" not in js
    assert "正在找資料" in js
