# -*- coding: utf-8 -*-
"""「知識庫」（docs/KNOWLEDGE_BASE_PLAN.md §6、§11 獨立應用）：純規則直接釘、路由用 TestClient 走一輪、掛載與守衛掃原始碼。

三個層次：
  1. `core.knowledge_logic` 純函式（id 驗證、split_files 白名單、chunk_text 邊界、append_section 格式、
     parse_structure 容錯、snapshot_lines 只抽四個數、標籤正規化、提示不夾帶機密、chat_prompt 只在 finance=True 帶數字）。
  2. 路由＋服務：`root()` 指到 tmp_path、claude 假掉、守衛假掉、`fire` 攔下來自己跑（背景工作因此是同步可斷言的）。
  3. 掃原始碼的不變式（_srcscan）：每支端點都過 `_guard`、`_guard`＝`check_admin_or_module(request, "knowledge")`、只掛 master、
     `knowledge.root` 進匿名端點的抹除清單、id 只能透過 `book_dir` 變成路徑。

🔴 測試裡不寫真實路徑（D:\\...），一律 tmp_path；不啟動伺服器、不碰 8000／8001。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import knowledge_logic as kl
from tests.unit._srcscan import between, call_args, code_only, func_body, repo_src

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOOD_ID = "0123456789abcdef"
URL = "/api/v1/knowledge"
#: 提示裡絕不能出現的字（規劃 §3.3：owner 的財務數字只讀 latest.json 的四個欄位）
FORBIDDEN = ("internal_cost", "jwt_secret", "database_url")


# ═══════════════════════════════════════════════════════════════
# 1. 純規則
# ═══════════════════════════════════════════════════════════════
def test_is_valid_id_only_accepts_16_lowercase_hex():
    assert kl.is_valid_id(GOOD_ID)
    assert kl.is_valid_id(kl.new_id()), "自己產的一定合法"
    for bad in ("..", "../0123456789abc", "0123456789ab/def", "0123456789ab\\def", "0123456789ABCDEF",
                "0123456789abcde", "0123456789abcdef0", "", " " + GOOD_ID, None, 123, ["a"]):
        assert not kl.is_valid_id(bad), repr(bad)


def test_slugify_and_chapter_filename_strip_path_characters():
    assert kl.slugify_chapter("第一章 開場") == "第一章-開場"
    assert kl.slugify_chapter("Chapter Two: Cash/Flow\\..\\x") == "Chapter-Two-CashFlow..x".rstrip(".") or \
        "/" not in kl.slugify_chapter("Chapter Two: Cash/Flow\\..\\x")
    s = kl.slugify_chapter("../../etc/passwd")
    assert "/" not in s and "\\" not in s and not s.startswith(".")
    assert kl.slugify_chapter("") == "chapter" and kl.slugify_chapter("///") == "chapter"
    assert kl.chapter_filename(3, "第一章 開場") == "ch03-第一章-開場.md"
    assert kl.chapter_filename(120, "x") == "ch120-x.md"


def test_split_files_keeps_only_the_four_support_files():
    text = """前言廢話
===== FILE: SKILL.md =====
骨架內容
===== FILE: ./glossary.md =====
名詞
===== FILE: sub\\patterns.md =====
做法
===== FILE: ../cheatsheet.md =====
規則
===== FILE: evil.md =====
不該出現
===== FILE: skill.md =====
大小寫不同也不收
===== FILE: SKILL.md.bak =====
也不收
"""
    files = kl.split_files(text)
    assert set(files) == set(kl.SUPPORT_FILES), files.keys()
    assert files["SKILL.md"] == "骨架內容\n" and files["glossary.md"] == "名詞\n"
    assert files["patterns.md"] == "做法\n" and files["cheatsheet.md"] == "規則\n"
    assert "不該出現" not in "".join(files.values()) and "也不收" not in "".join(files.values())
    # 空段不收；沒有分隔行就是空 dict
    assert kl.split_files("===== FILE: SKILL.md =====\n\n\n===== FILE: glossary.md =====\nx\n") == {"glossary.md": "x\n"}
    assert kl.split_files("") == {} and kl.split_files("沒有分隔行") == {}


def test_chunk_text_boundaries():
    assert kl.chunk_text("", 10) == []
    assert kl.chunk_text("a" * 10, 10) == ["a" * 10], "剛好等於上限不切"
    parts = kl.chunk_text("a" * 11, 10)
    assert parts == ["a" * 10, "a"], "超一字就切成兩段、一字不漏"
    # 有段落就在段落切（後半的空行）
    text = "p" * 6 + "\n\n" + "q" * 6
    parts = kl.chunk_text(text, 10)
    assert parts == ["p" * 6, "q" * 6]
    # 空行太前面（不到一半）就找換行；都沒有就硬切；合起來字不漏
    text = "ab\n\n" + "c" * 30 + "\n" + "d" * 30
    parts = kl.chunk_text(text, 40)
    assert all(len(p) <= 40 for p in parts) and "".join(parts).replace("\n", "") == text.replace("\n", "")
    with pytest.raises(ValueError):
        kl.chunk_text("x", 0)


def test_append_section_format_appends_and_never_overwrites():
    first = kl.append_section("", "留六個月生活費", "2026-09-18 10:00")
    assert first == "## 2026-09-18 10:00\n\n留六個月生活費\n"
    second = kl.append_section(first, "  第二條  ", "2026-09-19 08:30")
    assert second == first + "\n## 2026-09-19 08:30\n\n第二條\n", "段與段之間剛好一個空行"
    assert second.startswith(first.rstrip()), "既有內容原樣保留"
    # 既有內容沒換行結尾也補得對；空文字什麼都不做
    assert kl.append_section("舊", "新", "when") == "舊\n\n## when\n\n新\n"
    assert kl.append_section("舊\n", "   ", "when") == "舊\n"
    assert kl.append_section("", "", "when") == ""


def test_parse_structure_is_forgiving():
    text = """好的，以下是章節結構（我先看了 [目錄] 那幾頁）：
```json
[
  {"n": 9, "title": "第三章 賣出", "start_page": 30},
  {"n": 1, "title": "前言", "start_page": 1, "end_page": 4},
  {"n": 2, "title": "第一章 現金", "start_page": 5, "end_page": -1},
  {"title": "沒頁碼的丟掉"},
  {"title": "超出全書的丟掉", "start_page": 80, "end_page": 90},
  "不是物件也丟掉"
]
```
說明：最後一章到書末。"""
    out = kl.parse_structure(text, page_count=50)
    assert [c["n"] for c in out] == [1, 2, 3], "照 start_page 排序後重新編號"
    assert [c["title"] for c in out] == ["前言", "第一章 現金", "第三章 賣出"]
    assert out[0]["end_page"] == 4
    assert out[1]["end_page"] == 29, "end 比 start 小（等於沒給）→ 用下一章的 start−1"
    assert out[2]["end_page"] == 50, "最後一章 end 空 → 全書頁數"
    assert kl.parse_structure('[{"title":"a","start_page":1,"end_page":999}]', 50)[0]["end_page"] == 50, "夾回全書頁數"
    # 沒給 page_count 就不夾；end 空的最後一章用自己的 start
    loose = kl.parse_structure('[{"title":"a","start_page":3}]')
    assert loose == [{"n": 1, "title": "a", "start_page": 3, "end_page": 3}]
    # 空標題補「第 N 章」；start 小於 1 丟掉
    assert kl.parse_structure('[{"start_page": 2, "end_page": 3}, {"title": "z", "start_page": 0}]')[0]["title"] == "第 1 章"
    # 解不出來／不是陣列／一章都沒有 → 空清單（呼叫端走退路）
    for bad in ("", "沒有 JSON", "[", '{"n": 1}', "[1, 2, 3]", "[[目錄]] 然後 [這不是 json]"):
        assert kl.parse_structure(bad, 10) == [], repr(bad)


def test_split_chapters_and_page_index_follow_page_marks():
    full = "[[p.1]]\n第一頁 目錄\n[[p.2]]\n第二頁 內容\n[[p.3]]\n第三頁 結尾\n"
    idx = kl.page_index(full)
    assert idx == [(1, "第一頁 目錄"), (2, "第二頁 內容"), (3, "第三頁 結尾")]
    chs = kl.split_chapters(full, [{"n": 1, "title": "a", "start_page": 1, "end_page": 1},
                                   {"n": 2, "title": "b", "start_page": 2, "end_page": 99}])
    assert "第一頁" in chs[0]["text"] and "第二頁" not in chs[0]["text"]
    assert "第二頁" in chs[1]["text"] and "第三頁" in chs[1]["text"] and chs[1]["end_page"] == 99
    assert kl.page_texts("") == [] and kl.page_texts("沒標記") == [(1, "沒標記")]
    fb = kl.fallback_boundaries(60, per=25)
    assert [(c["start_page"], c["end_page"]) for c in fb] == [(1, 25), (26, 50), (51, 60)]


def _fat_latest() -> dict:
    """塞了很多欄位的假 latest.json：只有四個數可以進提示，其餘都是「不該出現」的哨兵。"""
    return {
        "report": {
            "basis_date": "2026-09-01",
            "report": {
                "totals": {"net_financial": 12345678, "cash_usable": 987000, "liabilities": 55555555,
                           "internal_cost": 424242, "salary_income": 313131},
                "accounts": [{"name": "SENTINEL-BANK-ACCOUNT", "account_no": "SENTINEL-ACCT-NO", "balance": 777777}],
                "advice": ["SENTINEL-ADVICE"],
            },
        },
        "fortress": {"runway": {"months": 9.7, "with_l4": 15.3}, "layers": [{"no": 1, "have": 666666}],
                     "earmarks": [{"label": "SENTINEL-EARMARK", "amount": 32000}]},
        "database_url": "postgresql://SENTINEL-DSN", "jwt_secret": "SENTINEL-JWT",
        "loans": [{"lender": "SENTINEL-LENDER", "balance": 8888888}],
    }


SENTINELS = ("SENTINEL", "55555555", "424242", "313131", "777777", "666666", "8888888", "15.3", "32000")


def test_snapshot_lines_only_takes_the_four_numbers():
    lines = kl.snapshot_lines(_fat_latest())
    assert lines == ["數字日期：2026-09-01", "淨值（現金＋證券−負債）：1234.6 萬", "可動用現金：98.7 萬", "可撐月數：9.7 個月"]
    joined = "\n".join(lines)
    for s in SENTINELS + FORBIDDEN:
        assert s not in joined, s
    # 舊形狀（report.totals 直接在 report 底下）也認
    old = {"report": {"basis_date": "2026-08-01", "totals": {"net_financial": 10000, "cash_usable": 0}},
           "fortress": {"runway": {"months": 3}}}
    assert kl.snapshot_lines(old) == ["數字日期：2026-08-01", "淨值（現金＋證券−負債）：1 萬", "可動用現金：0 萬", "可撐月數：3 個月"]
    # 讀不到／形狀不對 → 空；缺哪個數就少哪一行
    for bad in (None, {}, "x", [], {"report": "x", "fortress": 1}, {"report": {"totals": {"net_financial": "not a number"}}}):
        assert kl.snapshot_lines(bad) == [], repr(bad)
    assert kl.snapshot_lines({"fortress": {"runway": {"months": 2.5}}}) == ["可撐月數：2.5 個月"]


def test_prompts_never_carry_secrets_and_chat_prompt_only_gets_the_four_lines():
    meta = {"title": "測試書", "author": "作者", "pages": 2}
    chapters = [{"n": 1, "title": "現金", "file": "ch01-現金.md"}]
    history = [kl.chat_message("user", "早安", "t1"), kl.chat_message("ai", "早", "t2"), {"role": "system", "text": "不該進"}]
    kw = dict(skill="骨架", cheatsheet="規則", chapters=chapters, notes="筆記", conclusion="結論",
              snapshot=kl.snapshot_lines(_fat_latest()), history=history, text="現金要留多少")
    chat = kl.chat_prompt(meta, finance=True, **kw)
    for s in SENTINELS + FORBIDDEN:
        assert s not in chat, s
    for must in ("測試書", "作者", "骨架", "規則", "chapters/ch01-現金.md", "筆記", "結論", "1234.6 萬", "98.7 萬",
                 "9.7 個月", "2026-09-01", "owner：早安", "你：早", "現金要留多少", "可存成結論：", "財務顧問"):
        assert must in chat, must
    assert "不該進" not in chat, "只有 user／ai 兩種角色進歷史"
    # 沒東西時每段都有佔位字，不會炸
    empty = kl.chat_prompt({}, text="嗨", finance=True)
    assert "（還沒編譯章節）" in empty and "（讀不到顧問快照）" in empty and "（這是第一則）" in empty
    # finance=False（預設）：一般討論夥伴角色，**給了 snapshot 也不帶**任何數字、沒有快照段、沒有財務顧問
    plain = kl.chat_prompt(meta, **kw)
    for must in ("測試書", "骨架", "規則", "chapters/ch01-現金.md", "筆記", "結論", "owner：早安", "現金要留多少", "可存成結論："):
        assert must in plain, must
    for nope in ("1234.6 萬", "98.7 萬", "9.7 個月", "2026-09-01", "顧問快照", "財務顧問", "堡壘") + SENTINELS:
        assert nope not in plain, nope
    assert kl.chat_prompt(meta, finance=False, **kw) == plain
    # 其他四種提示：同樣不夾帶機密、繁體中文規矩都在
    prompts = [
        kl.conclude_prompt(meta, history, "既有"),
        kl.chapter_prompt(meta, {"n": 1, "title": "現金", "start_page": 1, "end_page": 2}, "章文", 2, 3),
        kl.structure_prompt([(1, "目錄")], "開頭", meta),
        kl.support_prompt(meta, [{"n": 1, "title": "現金", "file": "ch01-現金.md", "md": "筆記"}]),
    ]
    for p in prompts:
        for s in FORBIDDEN:
            assert s not in p, s
        assert "繁體中文" in p and "不推薦" in p and "emoji" in p
        assert "財務顧問" not in p and "客戶" not in p, "提示中性（§11）：不假設書是財務書、規則對讀者說話"
    assert "當你" in prompts[3], "決策規則寫成「當你…就…因為…」"
    assert "第 2/3 段" in prompts[1] and "既有" in prompts[0] and "owner：早安" in prompts[0]
    assert "===== FILE: SKILL.md =====" in prompts[3] and "chapters/ch01-現金.md" in prompts[3]
    assert "internal_cost" not in repo_src("core/knowledge_logic.py"), "模組本身就沒有這個字"


def test_normalize_tags_trims_dedupes_and_caps():
    assert kl.FINANCE_TAG == "財務"
    assert kl.normalize_tags(["  財務 ", "財務", "", "  ", "投資\n心理", 123, None, "投資 心理"]) == ["財務", "投資 心理"]
    assert kl.normalize_tags(None) == [] and kl.normalize_tags("財務") == [] and kl.normalize_tags({"a": 1}) == []
    long = "x" * 25
    assert kl.normalize_tags([long]) == ["x" * kl.TAG_MAX_LEN] and kl.TAG_MAX_LEN == 20
    many = [f"t{i}" for i in range(30)]
    # owner 2026-09-19：「標籤可以很多個」—— 從 10 放寬到 24
    assert kl.normalize_tags(many) == many[:kl.TAGS_MAX] and kl.TAGS_MAX == 24
    assert kl.normalize_tags(["a"] * 30 + ["b"]) == ["a", "b"], "去重之後才數上限"
    assert kl.has_tag({"tags": [" 財務 "]}, "財務") and not kl.has_tag({"tags": ["投資"]}, "財務")
    assert not kl.has_tag({}, "財務") and not kl.has_tag({"tags": "財務"}, "財務")
    assert "標籤：財務、投資" in kl.chat_prompt({"title": "書", "tags": ["財務", "投資"]}, text="x")


def test_recent_chat_and_conclusion_lines():
    chat = [kl.chat_message("user", str(i), "t") for i in range(40)] + [{"role": "bogus"}, "junk"]
    assert [m["text"] for m in kl.recent_chat(chat, 3)] == ["37", "38", "39"]
    assert len(kl.recent_chat(chat)) == kl.RECENT_CHAT_N and kl.recent_chat(chat, 0) == []
    reply = "先看數字。\n\n可存成結論：\n- 留六個月生活費（第 3 章）\n* 不借錢買股票（第 5 章）\n\n"
    assert kl.conclusion_lines(reply) == ["留六個月生活費（第 3 章）", "不借錢買股票（第 5 章）"]
    assert kl.conclusion_lines("沒有那一行") == [] and kl.conclusion_lines("") == []


# ═══════════════════════════════════════════════════════════════
# 2. 路由＋服務（root → tmp_path、claude 假掉、守衛假掉、fire 攔下）
# ═══════════════════════════════════════════════════════════════
def _make_pdf(pages: int = 2) -> bytes:
    import pymupdf
    doc = pymupdf.open()
    for i in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"Chapter {i} text on page {i}")
    try:
        return doc.tobytes()
    finally:
        doc.close()


class _Harness:
    def __init__(self, client, books_root, fired, guard_calls, claude, prompts):
        self.client = client
        self.books_root = books_root
        self.fired = fired                # fire() 攔下來的 coroutine
        self.guard_calls = guard_calls    # 每次 check_admin_or_module 被叫到的 module_keys tuple
        self.claude = claude              # {"available": bool, "reply": callable(prompt) -> (text, err)}
        self.prompts = prompts            # 假 call_claude 收到的 (prompt, kwargs)

    def run_fired(self):
        """把 fire() 攔下來的背景工作在這裡同步跑完（背景工作因此可以斷言）。"""
        while self.fired:
            asyncio.run(self.fired.pop(0))

    def upload(self, pages: int = 2, title: str = "測試書", name: str = "book.pdf") -> dict:
        r = self.client.post(URL, files={"file": (name, _make_pdf(pages), "application/pdf")}, data={"title": title})
        assert r.status_code == 200, r.text
        return r.json()

    def book_path(self, book_id: str, *parts: str) -> str:
        return os.path.join(self.books_root, book_id, *parts)

    def read(self, book_id: str, *parts: str) -> str:
        with open(self.book_path(book_id, *parts), encoding="utf-8") as f:
            return f.read()


def _default_reply(prompt: str):
    """假 claude：看提示是哪一種 pass 就回對應形狀。"""
    if "===== 章文 =====" in prompt:
        return "## 核心概念\n章的筆記\n## 重點\n- 一句話（p.1）\n", ""
    if "===== FILE: SKILL.md =====" in prompt:
        return ("===== FILE: SKILL.md =====\n# 骨架\n===== FILE: glossary.md =====\n名詞\n"
                "===== FILE: patterns.md =====\n做法\n===== FILE: cheatsheet.md =====\n- 當 X 就 Y（第 1 章）\n"), ""
    if "只輸出 JSON 陣列" in prompt:
        return ('前言：\n[{"n": 1, "title": "第一章 開場", "start_page": 1, "end_page": 1},'
                ' {"n": 2, "title": "Chapter Two: Cash", "start_page": 2}]\n'), ""
    if "===== 對話 =====" in prompt:
        return "- 留六個月生活費（第一章 開場）\n- 不借錢買股票（Chapter Two）\n", ""
    return "先看數字。\n\n可存成結論：\n- 留六個月生活費（第一章 開場）\n", ""


@pytest.fixture
def kb(tmp_path, monkeypatch):
    from routers import api_knowledge as api
    from services import knowledge_service as ks

    books_root = str(tmp_path / "books")
    monkeypatch.setattr(ks, "root", lambda: books_root)
    monkeypatch.setattr(ks, "pick_model", lambda requested="": "opus")
    for d in (ks._stage, ks._chat_partial, ks._chat_stage):
        d.clear()
    ks._concluding.clear()

    guard_calls: list = []

    def _fake_guard(request, *module_keys):
        guard_calls.append(tuple(module_keys))
        return {"sub": "tester", "modules": list(module_keys)}

    monkeypatch.setattr(api, "check_admin_or_module", _fake_guard)
    monkeypatch.setattr(ks, "ADVISOR_LATEST", str(tmp_path / "latest.json"))

    claude = {"available": True, "reply": _default_reply}
    monkeypatch.setattr(api, "claude_available", lambda: claude["available"])
    prompts: list = []

    async def _fake_call_claude(prompt, **kw):
        prompts.append((prompt, kw))
        if kw.get("on_stage"):
            kw["on_stage"]("running")
        text, err = claude["reply"](prompt)
        if kw.get("on_partial") and text:
            kw["on_partial"](text[:3])
        return text, err

    monkeypatch.setattr(ks, "call_claude", _fake_call_claude)

    fired: list = []
    monkeypatch.setattr(api, "fire", lambda coro, label="": fired.append(coro))

    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield _Harness(client, books_root, fired, guard_calls, claude, prompts)
    for coro in fired:                    # 沒跑到的背景工作關掉，別留 never awaited 警告
        coro.close()
    for d in (ks._stage, ks._chat_partial, ks._chat_stage):
        d.clear()
    ks._concluding.clear()


def test_without_a_token_every_endpoint_is_401():
    """真守衛（沒 monkeypatch）：沒帶 token 一律 401，連書架都不給看。"""
    from routers import api_knowledge as api
    app = FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    assert c.get(URL).status_code == 401
    assert c.get(f"{URL}/{GOOD_ID}").status_code == 401
    assert c.post(f"{URL}/{GOOD_ID}/chat", json={"text": "hi"}).status_code == 401
    assert c.delete(f"{URL}/{GOOD_ID}").status_code == 401


def test_invalid_ids_are_404_and_never_touch_the_disk(kb):
    from services import knowledge_service as ks
    for bad in ("0123456789ABCDEF", "0123456789abcde", "zz", "0123456789abcdef0"):
        assert kb.client.get(f"{URL}/{bad}").status_code == 404, bad
        assert kb.client.put(f"{URL}/{bad}", json={"title": "x"}).status_code == 404, bad
        assert kb.client.delete(f"{URL}/{bad}").status_code == 404, bad
        assert kb.client.get(f"{URL}/{bad}/chat").status_code == 404, bad
        assert kb.client.post(f"{URL}/{bad}/conclusions", json={"text": "x"}).status_code == 404, bad
    assert kb.client.get(f"{URL}/{GOOD_ID}").status_code == 404, "合法 id 但資料夾不存在也是 404"
    for bad in ("..", "../x", "0123456789ab/def", "0123456789ab\\def", GOOD_ID.upper()):
        with pytest.raises(ks.BookNotFound):
            ks.book_dir(bad, must_exist=False)
    assert not os.path.exists(kb.books_root), "上面那些都不該建出任何東西"
    assert kb.guard_calls and all(c == ("knowledge",) for c in kb.guard_calls), "每一支都是同一把「知識庫」鑰匙"


def test_upload_rejects_non_pdf_by_header_and_accepts_a_real_pdf(kb):
    r = kb.client.post(URL, files={"file": ("looks.pdf", b"hello world", "application/pdf")})
    assert r.status_code == 422 and "PDF" in r.json()["detail"]
    r = kb.client.post(URL, files={"file": ("x.bin", b"%PDF-1.7 but broken", "application/octet-stream")})
    assert r.status_code == 422, "檔頭對了但 pymupdf 讀不了 → 也是 422（不是 500）"
    assert kb.client.get(URL).json() == [], "根目錄還沒建：書架是空的"

    meta = kb.upload(pages=2, name=r"C:\Users\me\Desktop\投資書.pdf")
    bid = meta["id"]
    assert kl.is_valid_id(bid) and meta["pages"] == 2 and meta["status"] == "uploaded"
    assert meta["title"] == "測試書" and meta["source_name"] == "投資書.pdf", "原檔名只留 basename"
    full = kb.read(bid, "full_text.txt")
    assert "[[p.1]]" in full and "[[p.2]]" in full and "Chapter 2 text on page 2" in full
    assert os.path.isfile(kb.book_path(bid, "source.pdf")) and os.path.isdir(kb.book_path(bid, "chapters"))
    assert json.loads(kb.read(bid, "meta.json"))["chars"] == len(full)
    # 沒給 title 就用檔名
    assert kb.upload(pages=1, title="", name="沒名字.pdf")["title"] == "沒名字"

    shelf = kb.client.get(URL).json()
    assert [b["id"] for b in shelf].count(bid) == 1 and len(shelf) == 2
    row = next(b for b in shelf if b["id"] == bid)
    assert row["chapters"] == 0 and row["has_conclusion"] is False and row["has_notes"] is False and row["stage"] == ""
    assert row["tags"] == [] and meta["tags"] == []
    detail = kb.client.get(f"{URL}/{bid}").json()
    assert detail["skill"] == "" and detail["chapter_list"] == [] and detail["conclusion"] == "" and "chat" not in detail
    assert detail["tags"] == []


def test_compile_needs_claude_and_runs_three_passes(kb):
    from services import knowledge_service as ks
    bid = kb.upload(pages=2)["id"]
    kb.claude["available"] = False
    r = kb.client.post(f"{URL}/{bid}/compile", json={})
    assert r.status_code == 503 and "主控" in r.json()["detail"]
    assert bid not in ks._stage, "503 的時候不該把它標成在編"

    kb.claude["available"] = True
    r = kb.client.post(f"{URL}/{bid}/compile", json={})
    assert r.status_code == 200 and r.json() == {"status": "compiling", "model": "opus"}
    assert len(kb.fired) == 1 and bid in ks._stage
    assert kb.client.get(f"{URL}/{bid}").json()["status"] == "compiling"
    assert kb.client.post(f"{URL}/{bid}/compile", json={}).status_code == 409, "已在跑 → 409"
    assert kb.client.delete(f"{URL}/{bid}").status_code == 409, "編譯中不准刪"

    kb.run_fired()
    kinds = ["structure" if "只輸出 JSON 陣列" in p else "chapter" if "===== 章文 =====" in p else "support"
             for p, _ in kb.prompts]
    assert kinds == ["structure", "chapter", "chapter", "support"], "三個 pass：結構 → 每章 → 骨架"
    assert all(kw["cwd"] == kb.book_path(bid) and kw["model"] == "opus" for _, kw in kb.prompts)
    files = sorted(os.listdir(kb.book_path(bid, "chapters")))
    assert files == ["ch01-第一章-開場.md", "ch02-Chapter-Two-Cash.md"], "檔名由我們產（路徑字元剝掉）"
    assert kb.read(bid, "chapters", files[0]).startswith("# 第 1 章 第一章 開場（p.1–1）\n\n## 核心概念")
    for name in kl.SUPPORT_FILES:
        assert os.path.isfile(kb.book_path(bid, name)), name
    assert kb.read(bid, "cheatsheet.md") == "- 當 X 就 Y（第 1 章）\n"
    meta = json.loads(kb.read(bid, "meta.json"))
    assert meta["status"] == "compiled" and meta["chapters"] == 2 and meta["compiled_at"] and meta["model"] == "opus"
    assert [c["file"] for c in meta["toc"]] == files and bid not in ks._stage

    detail = kb.client.get(f"{URL}/{bid}").json()
    assert detail["status"] == "compiled" and detail["skill"] == "# 骨架\n"
    assert [(c["n"], c["title"]) for c in detail["chapter_list"]] == [(1, "第一章 開場"), (2, "Chapter Two: Cash")]
    ch = kb.client.get(f"{URL}/{bid}/chapters/2").json()
    assert ch["file"] == files[1] and ch["md"].startswith("# 第 2 章 Chapter Two: Cash（p.2–2）")
    assert kb.client.get(f"{URL}/{bid}/chapters/9").status_code == 404

    # 重編（不 force）只補缺的：刪掉第 2 章再編，只叫一次 claude
    os.remove(kb.book_path(bid, "chapters", files[1]))
    kb.prompts.clear()
    assert kb.client.post(f"{URL}/{bid}/compile").status_code == 200
    kb.run_fired()
    assert len(kb.prompts) == 1 and "===== 章文 =====" in kb.prompts[0][0]
    assert os.path.isfile(kb.book_path(bid, "chapters", files[1]))
    # force：整本重來（四次）
    kb.prompts.clear()
    assert kb.client.post(f"{URL}/{bid}/compile", json={"force": True}).status_code == 200
    kb.run_fired()
    assert len(kb.prompts) == 4 and json.loads(kb.read(bid, "meta.json"))["status"] == "compiled"


def test_compile_failure_is_written_to_meta_and_partial_chapters_survive(kb):
    bid = kb.upload(pages=2)["id"]
    calls = {"n": 0}

    def _flaky(prompt):
        if "===== 章文 =====" in prompt:
            calls["n"] += 1
            if calls["n"] == 2:
                return None, "claude 結束碼=1；boom"
        return _default_reply(prompt)

    kb.claude["reply"] = _flaky
    assert kb.client.post(f"{URL}/{bid}/compile").status_code == 200
    kb.run_fired()
    meta = json.loads(kb.read(bid, "meta.json"))
    assert meta["status"] == "failed" and "第 2 章" in meta["error"] and "boom" in meta["error"]
    assert os.listdir(kb.book_path(bid, "chapters")) == ["ch01-第一章-開場.md"], "已產的章保留"
    row = kb.client.get(f"{URL}/{bid}").json()
    assert row["status"] == "failed" and "boom" in row["error"] and row["chapters"] == 1
    # 骨架回的檔少一個 → 也是 failed，而且四個檔一個都不寫
    kb.claude["reply"] = lambda p: ("===== FILE: SKILL.md =====\nx\n", "") if "===== FILE: SKILL.md =====" in p else _default_reply(p)
    assert kb.client.post(f"{URL}/{bid}/compile").status_code == 200
    kb.run_fired()
    meta = json.loads(kb.read(bid, "meta.json"))
    assert meta["status"] == "failed" and "骨架少了" in meta["error"]
    assert not any(os.path.isfile(kb.book_path(bid, n)) for n in kl.SUPPORT_FILES)
    # 全文是空的：一開始就 failed（不叫 claude）
    with open(kb.book_path(bid, "full_text.txt"), "w", encoding="utf-8") as f:
        f.write("")
    kb.claude["reply"] = _default_reply
    kb.prompts.clear()
    assert kb.client.post(f"{URL}/{bid}/compile", json={"force": True}).status_code == 200
    kb.run_fired()
    assert "抽不出文字" in json.loads(kb.read(bid, "meta.json"))["error"] and kb.prompts == []


def test_status_compiling_without_a_runner_reads_as_failed(kb):
    """行程重啟後 meta 停在 compiling、但記憶體裡沒人在編 → 畫面要看到 failed，不能永遠轉圈。"""
    from services import knowledge_service as ks
    bid = kb.upload(pages=1)["id"]
    ks._update_meta(bid, status="compiling")
    assert kb.client.get(URL).json()[0]["status"] == "failed"
    ks._stage[bid] = "第 1/9 章"
    row = kb.client.get(f"{URL}/{bid}").json()
    assert row["status"] == "compiling" and row["stage"] == "第 1/9 章"


def test_chat_flow_writes_user_then_ai_and_prompt_carries_only_the_four_numbers(kb, tmp_path):
    from services import knowledge_service as ks
    bid = kb.upload(pages=1)["id"]
    # latest.json 固定在 ADVISOR_LATEST（fixture 指到 tmp_path），跟書架根目錄無關
    with open(tmp_path / "latest.json", "w", encoding="utf-8") as f:
        json.dump(_fat_latest(), f)
    assert ks.advisor_snapshot() == kl.snapshot_lines(_fat_latest())
    assert kb.client.put(f"{URL}/{bid}", json={"tags": ["財務"]}).json()["tags"] == ["財務"], "標了「財務」才帶數字"

    kb.claude["available"] = False
    assert kb.client.post(f"{URL}/{bid}/chat", json={"text": "現金要留多少"}).status_code == 503
    assert not os.path.exists(kb.book_path(bid, "chat.json")), "503 之前不寫任何東西"
    kb.claude["available"] = True
    assert kb.client.post(f"{URL}/{bid}/chat", json={"text": "   "}).status_code == 400
    assert kb.client.post(f"{URL}/{bid}/chat", json={"text": ""}).status_code == 422

    r = kb.client.post(f"{URL}/{bid}/chat", json={"text": "現金要留多少"})
    assert r.status_code == 200 and r.json()["status"] == "asking"
    assert r.json()["message"]["role"] == "user" and r.json()["message"]["text"] == "現金要留多少"
    chat = json.loads(kb.read(bid, "chat.json"))
    assert [(m["role"], m["text"]) for m in chat] == [("user", "現金要留多少")] and chat[0]["at"]
    state = kb.client.get(f"{URL}/{bid}/chat").json()
    assert state["stage"] == "queued" and state["partial"] == "" and len(state["chat"]) == 1
    assert kb.client.post(f"{URL}/{bid}/chat", json={"text": "再問"}).status_code == 409, "上一輪沒回完 → 409"
    assert kb.client.get(f"{URL}/{bid}").json()["chatting"] is True
    assert kb.client.delete(f"{URL}/{bid}").status_code == 409

    kb.run_fired()
    prompt, kw = kb.prompts[-1]
    assert kw["cwd"] == kb.book_path(bid) and kw["allowed_tools"] == "Read", "cwd＝這本書、只准 Read"
    assert "現金要留多少" in prompt and "1234.6 萬" in prompt and "9.7 個月" in prompt and "財務顧問" in prompt
    for s in SENTINELS + FORBIDDEN:
        assert s not in prompt, s
    chat = json.loads(kb.read(bid, "chat.json"))
    assert [m["role"] for m in chat] == ["user", "ai"] and chat[1]["text"].startswith("先看數字。")
    state = kb.client.get(f"{URL}/{bid}/chat").json()
    assert state["stage"] == "" and state["partial"] == "" and len(state["chat"]) == 2
    assert bid not in ks._chat_stage and bid not in ks._chat_partial
    # 第二則：歷史只帶前面的、這一則單獨在最後一段
    assert kb.client.post(f"{URL}/{bid}/chat", json={"text": "那借錢呢"}).status_code == 200
    kb.run_fired()
    prompt = kb.prompts[-1][0]
    assert prompt.rstrip().endswith("那借錢呢") and "owner：現金要留多少" in prompt and "你：先看數字。" in prompt
    assert prompt.count("那借錢呢") == 1, "這一則不重複進歷史"
    # claude 叫不動：AI 那則寫錯誤字，而不是整輪消失
    kb.claude["reply"] = lambda p: (None, "找不到 claude CLI")
    assert kb.client.post(f"{URL}/{bid}/chat", json={"text": "第三問"}).status_code == 200
    kb.run_fired()
    assert "叫不動 claude" in json.loads(kb.read(bid, "chat.json"))[-1]["text"]


def test_chat_without_finance_tag_never_carries_the_numbers(kb, tmp_path):
    """§11：只有 meta.tags 含「財務」的書才走財務顧問角色＋帶四個數；其他書 latest.json 在也不讀。"""
    from services import knowledge_service as ks
    with open(tmp_path / "latest.json", "w", encoding="utf-8") as f:
        json.dump(_fat_latest(), f)
    assert ks.advisor_snapshot(), "快照本身讀得到（不是因為讀不到才沒帶）"
    bid = kb.upload(pages=1)["id"]
    assert kb.client.put(f"{URL}/{bid}", json={"tags": ["投資心理", "習慣"]}).json()["tags"] == ["投資心理", "習慣"]
    kb.client.post(f"{URL}/{bid}/chat", json={"text": "這本書怎麼用"})
    kb.run_fired()
    prompt = kb.prompts[-1][0]
    assert "這本書怎麼用" in prompt and "標籤：投資心理、習慣" in prompt
    for nope in ("1234.6 萬", "98.7 萬", "9.7 個月", "2026-09-01", "顧問快照", "財務顧問") + SENTINELS:
        assert nope not in prompt, nope
    # 補上「財務」標籤 → 下一輪就帶
    kb.client.put(f"{URL}/{bid}", json={"tags": ["投資心理", "財務"]})
    kb.client.post(f"{URL}/{bid}/chat", json={"text": "再問"})
    kb.run_fired()
    assert "1234.6 萬" in kb.prompts[-1][0] and "財務顧問" in kb.prompts[-1][0]


def test_conclusions_append_conclude_and_whole_file_puts(kb):
    from services import knowledge_service as ks
    bid = kb.upload(pages=1)["id"]
    assert kb.client.post(f"{URL}/{bid}/conclude").status_code == 400, "沒討論 → 400"
    assert bid not in ks._concluding
    assert kb.client.post(f"{URL}/{bid}/conclusions", json={"text": "  "}).status_code == 400

    r = kb.client.post(f"{URL}/{bid}/conclusions", json={"text": "留六個月生活費"})
    assert r.status_code == 200
    text = kb.read(bid, "結論.md")
    assert re.fullmatch(r"## \d{4}-\d{2}-\d{2} \d{2}:\d{2}\n\n留六個月生活費\n", text), text
    assert r.json()["conclusion"] == text
    kb.client.post(f"{URL}/{bid}/conclusions", json={"text": "第二條"})
    text2 = kb.read(bid, "結論.md")
    assert text2.startswith(text) and text2.count("## ") == 2 and text2.endswith("\n\n第二條\n"), "追加不覆蓋"
    assert kb.client.get(URL).json()[0]["has_conclusion"] is True

    # 整理結論：要有討論；背景跑完追加一段
    kb.client.post(f"{URL}/{bid}/chat", json={"text": "討論一下"})
    kb.run_fired()
    kb.claude["available"] = False
    assert kb.client.post(f"{URL}/{bid}/conclude").status_code == 503
    kb.claude["available"] = True
    r = kb.client.post(f"{URL}/{bid}/conclude", json={"model": "opus"})
    assert r.status_code == 200 and r.json() == {"status": "asking"}
    assert kb.client.post(f"{URL}/{bid}/conclude").status_code == 409, "整理中 → 409"
    assert kb.client.get(f"{URL}/{bid}/chat").json()["concluding"] is True
    kb.run_fired()
    prompt = kb.prompts[-1][0]
    assert "===== 對話 =====" in prompt and "owner：討論一下" in prompt and "留六個月生活費" in prompt, "既有結論也進提示"
    text3 = kb.read(bid, "結論.md")
    assert text3.startswith(text2) and text3.count("## ") == 3 and "- 不借錢買股票（Chapter Two）" in text3
    assert bid not in ks._concluding

    # 整檔覆寫
    assert kb.client.put(f"{URL}/{bid}/conclusion", json={"text": "整份覆寫"}).status_code == 200
    assert kb.read(bid, "結論.md") == "整份覆寫"
    assert kb.client.put(f"{URL}/{bid}/notes", json={"text": "我的筆記"}).status_code == 200
    assert kb.read(bid, "筆記.md") == "我的筆記"
    detail = kb.client.get(f"{URL}/{bid}").json()
    assert detail["conclusion"] == "整份覆寫" and detail["notes"] == "我的筆記" and detail["has_notes"] is True
    assert kb.client.put(f"{URL}/{bid}/notes", json={"text": ""}).status_code == 200
    assert kb.read(bid, "筆記.md") == ""


def test_patch_title_author_and_delete_removes_the_folder(kb):
    bid = kb.upload(pages=1)["id"]
    r = kb.client.put(f"{URL}/{bid}", json={"title": "  新書名 ", "author": " 作者 "})
    assert r.status_code == 200 and r.json()["title"] == "新書名" and r.json()["author"] == "作者"
    r = kb.client.put(f"{URL}/{bid}", json={"title": "   "})
    assert r.status_code == 200 and r.json()["title"] == "新書名", "空白標題不覆蓋"
    assert kb.client.put(f"{URL}/{bid}", json={"title": "x" * 201}).status_code == 422
    assert kb.client.put(f"{URL}/{bid}", json={}).json()["author"] == "作者"
    # 標籤：正規化（去空白／去重／截 20 字／最多 kl.TAGS_MAX 個）；沒帶 tags 就不動；空清單＝清掉
    r = kb.client.put(f"{URL}/{bid}", json={"tags": [" 財務 ", "財務", "", "y" * 30] + [f"t{i}" for i in range(20)]})
    # owner 2026-09-19「標籤可以很多個」把上限放寬到 24；別寫死數字，照 kl.TAGS_MAX 算
    assert r.status_code == 200 and r.json()["tags"] == (["財務", "y" * 20]
                                                        + [f"t{i}" for i in range(20)])[:kl.TAGS_MAX]
    assert kb.client.put(f"{URL}/{bid}", json={"author": "改名"}).json()["tags"][:2] == ["財務", "y" * 20], "沒帶 tags 不洗掉"
    assert json.loads(kb.read(bid, "meta.json"))["tags"][0] == "財務"
    assert kb.client.put(f"{URL}/{bid}", json={"tags": []}).json()["tags"] == []
    assert kb.client.put(f"{URL}/{bid}", json={"tags": "財務"}).status_code == 422, "要 list"

    assert os.path.isdir(kb.book_path(bid))
    assert kb.client.delete(f"{URL}/{bid}").json() == {"status": "ok"}
    assert not os.path.exists(kb.book_path(bid)), "整個資料夾消失"
    assert kb.client.get(f"{URL}/{bid}").status_code == 404
    assert kb.client.get(URL).json() == []
    assert os.path.isdir(kb.books_root), "根目錄本身不動"


def test_shelf_filters_by_tag(kb):
    a = kb.upload(pages=1, title="財務書")["id"]
    b = kb.upload(pages=1, title="心理書")["id"]
    c = kb.upload(pages=1, title="沒標籤")["id"]
    kb.client.put(f"{URL}/{a}", json={"tags": ["財務", "投資"]})
    kb.client.put(f"{URL}/{b}", json={"tags": ["心理"]})
    assert {r["id"] for r in kb.client.get(URL).json()} == {a, b, c}
    assert [r["id"] for r in kb.client.get(URL, params={"tag": "財務"}).json()] == [a]
    assert [r["id"] for r in kb.client.get(URL, params={"tag": " 投資 "}).json()] == [a], "篩選字也去空白"
    assert {r["id"] for r in kb.client.get(URL, params={"tag": ""}).json()} == {a, b, c}
    assert kb.client.get(URL, params={"tag": "沒有這個"}).json() == []
    # 舊 meta 沒有 tags 鍵：當空清單，不炸
    meta = json.loads(kb.read(c, "meta.json"))
    meta.pop("tags")
    with open(kb.book_path(c, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    assert next(r for r in kb.client.get(URL).json() if r["id"] == c)["tags"] == []
    assert kb.client.get(f"{URL}/{c}").json()["tags"] == []


def test_shelf_ignores_junk_folders_and_sorts_newest_first(kb):
    from services import knowledge_service as ks
    a = kb.upload(pages=1, title="早的")["id"]
    b = kb.upload(pages=1, title="晚的")["id"]
    ks._update_meta(a, uploaded_at="2026-01-01T00:00:00+08:00")
    ks._update_meta(b, uploaded_at="2026-02-01T00:00:00+08:00")
    os.makedirs(os.path.join(kb.books_root, "not-an-id"))
    os.makedirs(os.path.join(kb.books_root, "fedcba9876543210"))       # 合法 id 但沒有 meta
    with open(os.path.join(kb.books_root, "README.md"), "w", encoding="utf-8") as f:
        f.write("結構說明")
    assert [r["title"] for r in kb.client.get(URL).json()] == ["晚的", "早的"]


# ═══════════════════════════════════════════════════════════════
# 3. 掃原始碼的不變式
# ═══════════════════════════════════════════════════════════════
_ROUTER_SRC = repo_src("routers/api_knowledge.py")
_SERVICE_SRC = repo_src("services/knowledge_service.py")


def _endpoints(src: str) -> list:
    names = re.findall(r"@router\.(?:get|post|put|delete|patch)\([^\n]*\)\s*\nasync def (\w+)\(", src)
    assert len(names) >= 13, names
    return names


def test_every_endpoint_calls_the_guard_and_the_guard_is_the_knowledge_key():
    for name in _endpoints(_ROUTER_SRC):
        body = code_only(func_body(_ROUTER_SRC, f"async def {name}("))
        assert "_guard(request)" in body, name
    guard = code_only(func_body(_ROUTER_SRC, "def _guard(request: Request) -> dict:"))
    assert "check_admin_or_module(request, MODULE_KEY)" in guard
    assert 'MODULE_KEY = "knowledge"' in _ROUTER_SRC
    assert 'prefix="/api/v1/knowledge"' in _ROUTER_SRC, "§11：不再掛在 /finance 底下"
    code = code_only(_ROUTER_SRC)
    for bad in ("require_entity", "core.ledger", '"mine"', "core.scheduler", "import notifier", "from notifier", "socket_mgr"):
        assert bad not in code, bad
    from core.auth import ALL_MODULES, MODULE_LABELS
    assert "knowledge" in ALL_MODULES and ALL_MODULES[-1] == "knowledge" and MODULE_LABELS["knowledge"] == "知識庫"
    from core.rbac_templates import DEFAULT_TEMPLATES, IDENTITIES
    assert not any("knowledge" in DEFAULT_TEMPLATES[i] for i in IDENTITIES), "範本不預設配，owner 自己勾"
    assert "knowledge:'知識庫'" in repo_src("frontend/js/admin/user-mgmt.js")
    assert "'knowledge'" in between(repo_src("frontend/js/shared/tab-config.js"), "export const PERMISSION_GROUPS", "];")


def test_router_mounts_only_on_master_and_knowledge_root_is_redacted():
    mods = between(repo_src("main.py"), "_ROUTER_MODULES", "]")
    assert "'api_knowledge'" in mods or '"api_knowledge"' in mods, "master 要掛"
    office = repo_src("main_office.py")
    assert "api_knowledge" not in office, "NAS 不掛：D:\\ 與 claude CLI 都只在 master"

    from routers.api_system import _SECRET_SUBKEYS, _redact_settings
    assert "root" in _SECRET_SUBKEYS["knowledge"]
    assert "knowledge_root" not in _SECRET_SUBKEYS["finance"] and "fortress" in _SECRET_SUBKEYS["finance"]
    s = {"knowledge": {"root": "X:\\somewhere\\books", "other": 1}, "finance": {"baseline_month": "2026-01"}}
    for admin in (False, True):
        out = _redact_settings(s, admin=admin)
        assert "root" not in out["knowledge"] and out["knowledge"]["other"] == 1, f"admin={admin}"
        assert out["finance"]["baseline_month"] == "2026-01"
    from config import _DEFAULT_SETTINGS
    assert _DEFAULT_SETTINGS["knowledge"]["root"].startswith("D:\\Originsun-Knowledge") and "knowledge_root" not in _DEFAULT_SETTINGS["finance"]


def test_ids_only_become_paths_through_book_dir():
    """`book_dir` 是 id → 路徑的唯一咽喉：它先 `is_valid_id` 再拼；其他地方拼路徑一律拿 `book_dir(...)` 回的目錄。
    （變異驗證過：把 book_dir 的 is_valid_id 拿掉、或在別處 `os.path.join(root(), book_id)`，這條會紅。）"""
    code = code_only(_SERVICE_SRC)
    guard = code_only(func_body(_SERVICE_SRC, "def book_dir("))
    check = guard.index("kl.is_valid_id(book_id)")
    assert "raise BookNotFound" in guard[check:guard.index("os.path.join(")], "先擋再拼"
    assert "root()" in guard, "book_dir 自己從 root() 拼"

    rest = code.replace(guard, "")
    for args in call_args(rest, "os.path.join"):
        head = args[0]
        # latest.json 在根目錄的上一層、檔名固定，允許 dirname(root())；直接 root()／base 只有 book_dir 能用
        assert not head.startswith("root()") and head != "base", f"book_dir 之外不准從 root() 直接拼：{args}"
        assert not (head.startswith("os.path.dirname(root()") and any("book_id" in a for a in args)), args
        for a in args:
            if "book_id" in a:
                assert a.startswith("book_dir("), f"id 要經 book_dir 才能變路徑：{args}"
    # 書架列目錄：名字先過 is_valid_id 才拿去讀 meta
    shelf = code_only(func_body(_SERVICE_SRC, "def list_books("))
    assert "kl.is_valid_id(name)" in shelf and shelf.index("is_valid_id(name)") < shelf.index("read_meta(name)")
    # 章檔名照 toc 的 file 找，不拼使用者給的 n；支援檔只從白名單寫
    ch = code_only(func_body(_SERVICE_SRC, "def read_chapter("))
    assert 'c["file"]' in ch and "chapter_filename" not in ch
    comp = code_only(func_body(_SERVICE_SRC, "async def _compile("))
    assert "kl.split_files(text)" in comp and "kl.chapter_filename(" in comp
    assert "knowledge_service" not in repo_src("main_office.py")


def test_service_has_its_own_gate_and_never_touches_the_db():
    claude = repo_src("services/knowledge_claude.py")
    assert "_KNOWLEDGE_GATE = asyncio.Semaphore(1)" in claude and "async with _KNOWLEDGE_GATE" in claude
    assert "_QUOTE_CHAT_GATE" not in code_only(claude), "跟報價助理的閘各自獨立"
    assert '"--permission-mode", "plan"' in claude and '"--allowedTools"' in claude
    for src in (code_only(_SERVICE_SRC), code_only(claude), code_only(_ROUTER_SRC)):
        for bad in ("sqlalchemy", "db.session", "db.models", "get_session", "SessionLocal"):
            assert bad not in src, bad
    assert "advisor_snapshot" in _SERVICE_SRC and "kl.snapshot_lines(json.load(f))" in _SERVICE_SRC, "快照只走四個數那支"
    head = code_only(_SERVICE_SRC).split("def book_dir(")[0]
    assert re.search(r'DEFAULT_ROOT = r"D:\\+Originsun-Knowledge\\+books"', head), "預設根目錄只在常數（§11 搬到 D:\\Originsun-Knowledge）"
    assert re.search(r'ADVISOR_LATEST = r"D:\\+Originsun-Advisor\\+latest\.json"', head), "顧問快照固定位置，不從書架根目錄推"
    assert "Originsun-Advisor" not in code_only(_SERVICE_SRC).split("ADVISOR_LATEST = ")[1].split("\n", 1)[1]
    snap = code_only(func_body(_SERVICE_SRC, "def advisor_snapshot("))
    assert "open(ADVISOR_LATEST" in snap and "root()" not in snap
    root_fn = code_only(func_body(_SERVICE_SRC, "def root("))
    assert 'get("knowledge")' in root_fn and 'get("root")' in root_fn and "knowledge_root" not in root_fn
    # 沒標「財務」不帶數字：run_chat 只用 has_tag 決定，而且不帶時連 advisor_snapshot 都不讀
    chat = code_only(func_body(_SERVICE_SRC, "async def run_chat("))
    assert "kl.has_tag(meta, kl.FINANCE_TAG)" in chat and "finance=finance" in chat and "advisor_snapshot() if finance else []" in chat
    # 整本重編連上次骨架失敗留的原文一起清
    assert "SUPPORT_RAW_FILE" in code_only(func_body(_SERVICE_SRC, "def reset_compiled("))


# ═══════════════════════════════════════════════════════════════
# 4. 前端 md-lite（node 跑純函式；輸出只印布林）
# ═══════════════════════════════════════════════════════════════
_MD_LITE = os.path.join(_REPO, "frontend", "js", "shared", "md-lite.js")


@pytest.mark.skipif(not shutil.which("node"), reason="這台沒有 node")
def test_md_lite_escapes_script_and_keeps_markdown():
    if not os.path.isfile(_MD_LITE):
        pytest.skip("frontend/js/shared/md-lite.js 還沒進來")
    src = repo_src("frontend/js/shared/md-lite.js")
    assert "export function mdToHtml" in src
    script = src.replace("export function", "function").replace("export const", "const") + """
const a = mdToHtml('<script>alert(1)</script>**x**');
const b = mdToHtml('[c](javascript:alert(1)) [ok](https://example.com/x) <img src=x onerror=alert(1)>');
const c = mdToHtml('# T\\n\\n- one\\n- two\\n\\n`co<de>`');
const out = [
  !a.includes('<script') && a.includes('&lt;script&gt;'),
  a.includes('<strong>x</strong>'),
  !b.includes('javascript:') || !/href="javascript/.test(b),
  b.includes('<a href="https://example.com/x"'),
  !b.includes('<img') && !/<[^>]*onerror/.test(b),
  c.includes('<h1>') && c.includes('<li>') && c.includes('<code>co&lt;de&gt;</code>'),
  mdToHtml('') === '' || typeof mdToHtml('') === 'string',
];
console.log(JSON.stringify(out));"""
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False, dir=tempfile.gettempdir()) as tf:
        tf.write(script)
        path = tf.name
    try:
        r = subprocess.run(["node", path], capture_output=True, text=True, cwd=_REPO)
    finally:
        os.unlink(path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[true,true,true,true,true,true,true]", r.stdout


# ── 收尾 review 抓到的五顆（2026-09-18；先紅後綠）──────────────────────

def test_is_valid_id_rejects_trailing_newline():
    """`$` 在 `\n` 前也算結尾 —— URL `%0A` 解碼後就是它；只認剛好 16 hex。"""
    assert kl.is_valid_id("0123456789abcdef")
    assert not kl.is_valid_id("0123456789abcdef\n")
    assert not kl.is_valid_id("\n0123456789abcdef")


def test_parse_structure_clamps_end_to_next_chapter_start():
    """claude 回的 end_page 跨到下一章時要夾回下一章 start−1，不然同一頁的文字餵進兩章。"""
    text = ('[{"n":1,"title":"A","start_page":1,"end_page":10},'
            ' {"n":2,"title":"B","start_page":5,"end_page":999},'
            ' {"n":3,"title":"C","start_page":30}]')
    rows = kl.parse_structure(text, 50)
    assert [(r["start_page"], r["end_page"]) for r in rows] == [(1, 4), (5, 29), (30, 50)]


def test_scanned_pdf_with_no_text_fails_before_calling_claude(kb):
    """每頁都有 `[[p.N]]` 標記，所以「抽不出文字」要看標記後面有沒有字，不然掃描檔會編出一本空書。"""
    from services import knowledge_service as ks
    meta = kb.upload(pages=2)
    with open(kb.book_path(meta["id"], ks.FULLTEXT_FILE), "w", encoding="utf-8") as f:
        f.write("[[p.1]]\n\n[[p.2]]\n   \n")
    r = kb.client.post(f"{URL}/{meta['id']}/compile", json={})
    assert r.status_code == 200
    kb.run_fired()
    d = kb.client.get(f"{URL}/{meta['id']}").json()
    assert d["status"] == "failed" and "抽不出文字" in d["error"]
    assert kb.prompts == []


def test_compile_on_a_vanished_book_does_not_leak_stage(kb):
    """合法 id 但資料夾不在：404 之後 `_stage` 不能留著那個 id（留著＝之後永遠 409）。"""
    from services import knowledge_service as ks
    gone = "0123456789abcdef"
    r = kb.client.post(f"{URL}/{gone}/compile", json={})
    assert r.status_code == 404
    assert gone not in ks._stage


def test_missing_chapter_is_404_about_the_chapter_not_the_book(kb):
    meta = kb.upload(pages=1)
    r = kb.client.get(f"{URL}/{meta['id']}/chapters/7")
    assert r.status_code == 404
    assert "章" in r.json()["detail"] and "找不到這本書" not in r.json()["detail"]


# ── 編譯三個 pass 不給 claude 任何工具（2026-09-18 實測：plan mode＋Read 會讓它寫計畫、問要不要建檔，四個檔一個都不出）──

def test_compile_passes_call_claude_without_tools_and_chat_with_read_only(kb):
    """行為層：結構／章／骨架三個 pass 的 allowed_tools 都是空（用預設）；討論才是 Read。"""
    from services import knowledge_service as ks
    bid = kb.upload(pages=2)["id"]
    kb.client.post(f"{URL}/{bid}/compile", json={})
    kb.run_fired()
    assert kb.client.get(f"{URL}/{bid}").json()["status"] == "compiled"
    compile_calls = list(kb.prompts)
    assert len(compile_calls) >= 3
    for _prompt, kw in compile_calls:
        assert not kw.get("allowed_tools"), "編譯 pass 不准帶工具（帶了 claude 會寫計畫不輸出）"
    kb.prompts.clear()
    kb.client.post(f"{URL}/{bid}/chat", json={"text": "hi"})
    kb.run_fired()
    assert kb.prompts and kb.prompts[-1][1].get("allowed_tools") == "Read"
    assert bid not in ks._stage


def test_call_claude_default_is_no_tools_and_empty_means_tools_flag_off():
    """原始碼層：`call_claude` 預設 allowed_tools=""；空字串走 `--tools ""`，只有非空才進 plan mode＋allowedTools。"""
    import inspect
    from services import knowledge_claude as kc
    assert inspect.signature(kc.call_claude).parameters["allowed_tools"].default == ""
    src = code_only(repo_src("services/knowledge_claude.py"))
    body = func_body(src, "call_claude")
    assert 'if allowed_tools:' in body
    assert '"--tools", ""' in body, "沒工具那條路要明確 --tools \"\"（不是只省略旗標：預設工具集會整包打開）"
    plan_branch = body.split("if allowed_tools:", 1)[1].split("else:", 1)[0]
    assert '"--permission-mode", "plan"' in plan_branch and '"--allowedTools"' in plan_branch
    no_tools_branch = body.split("else:", 1)[1]
    assert "--permission-mode" not in no_tools_branch


# ── 「待補」：先建書名、之後補 PDF（owner 2026-09-18 手機版第二批）──────────────

def test_pending_book_is_created_by_title_and_completed_by_file(kb):
    from services import knowledge_service as ks
    r = kb.client.post(f"{URL}/pending", json={"title": "  還沒買的書 ", "author": "某人", "tags": ["財務", "財務", " 經營 "]})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == "pending" and b["title"] == "還沒買的書" and b["pages"] == 0 and b["tags"] == ["財務", "經營"]
    assert "has_extend" in b and b["extend_new"] == 0
    bid = b["id"]
    assert not os.path.exists(kb.book_path(bid, ks.SOURCE_FILE))
    # 書架看得到、pill 是 pending；筆記／結論照常
    assert any(x["id"] == bid and x["status"] == "pending" for x in kb.client.get(URL).json())
    assert kb.client.put(f"{URL}/{bid}/notes", json={"text": "先記一筆"}).status_code == 200
    # 沒檔不能編：409，而且不留 _stage 的鬼
    r = kb.client.post(f"{URL}/{bid}/compile", json={})
    assert r.status_code == 409 and "PDF" in r.json()["detail"]
    assert bid not in ks._stage and kb.fired == []
    # 補檔：檔頭檢查同上傳
    r = kb.client.post(f"{URL}/{bid}/file", files={"file": ("x.pdf", b"nope", "application/pdf")})
    assert r.status_code == 422
    r = kb.client.post(f"{URL}/{bid}/file", files={"file": ("real.pdf", _make_pdf(3), "application/pdf")})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == "uploaded" and b["pages"] == 3 and b["id"] == bid and b["title"] == "還沒買的書"
    assert os.path.isfile(kb.book_path(bid, ks.SOURCE_FILE))
    assert "[[p.1]]" in kb.read(bid, ks.FULLTEXT_FILE)
    assert kb.read(bid, ks.NOTES_FILE).strip() == "先記一筆", "補檔不能洗掉他先寫的筆記"
    # 已有檔的書再補 → 409
    r = kb.client.post(f"{URL}/{bid}/file", files={"file": ("again.pdf", _make_pdf(1), "application/pdf")})
    assert r.status_code == 409
    # 補完就能編
    assert kb.client.post(f"{URL}/{bid}/compile", json={}).status_code == 200


def test_pending_requires_a_title_and_bad_id_is_404(kb):
    assert kb.client.post(f"{URL}/pending", json={"title": "   "}).status_code == 422
    r = kb.client.post(f"{URL}/zzzz/file", files={"file": ("x.pdf", _make_pdf(1), "application/pdf")})
    assert r.status_code == 404


# ── meta.json 的讀-改-寫要有每本書一把鎖（2026-09-18：dev 上補圖時正式站正在編譯，兩個寫入者對撞會互相蓋掉）──

def test_update_meta_is_serialized_per_book(kb):
    """兩個執行緒同時對同一本書 _update_meta 不同的鍵，最後兩個鍵都要在（沒鎖＝後寫的把先寫的蓋掉）。"""
    import threading
    from services import knowledge_service as ks
    bid = kb.upload(pages=1)["id"]
    orig_read = ks.read_meta
    gate = threading.Barrier(2, timeout=1)   # 有鎖時第二個讀不到 barrier，逾時就放行

    def slow_read(book_id):
        m = orig_read(book_id)
        try:
            gate.wait()          # 兩邊都讀完舊值才繼續 → 沒鎖的話一定互相蓋掉
        except threading.BrokenBarrierError:
            pass
        return m

    ks.read_meta = slow_read
    try:
        t1 = threading.Thread(target=ks._update_meta, args=(bid,), kwargs={"author": "甲"})
        t2 = threading.Thread(target=ks._update_meta, args=(bid,), kwargs={"tags": ["財務"]})
        t1.start(); t2.start(); t1.join(5); t2.join(5)
    finally:
        ks.read_meta = orig_read
    m = ks.read_meta(bid)
    assert m.get("author") == "甲" and m.get("tags") == ["財務"], m


# ── 開機自動接回被重啟打斷的編譯（2026-09-18 發 2.5.52 時三本編到一半被砍，人工一本一本 POST /compile 接回）──

def test_resume_interrupted_compiles_picks_up_only_orphaned_compiling_books(kb, monkeypatch):
    from services import knowledge_service as ks
    a = kb.upload(pages=2)["id"]           # 上一個行程編到一半（meta 停 compiling、沒人在跑）
    b = kb.upload(pages=2)["id"]           # 編好的
    c = kb.upload(pages=2)["id"]           # 正在跑（在 _stage 裡）
    ks._update_meta(a, status="compiling", model="sonnet")
    ks._update_meta(b, status="compiled")
    ks._update_meta(c, status="compiling"); ks._stage[c] = "第 1/3 章"
    started: list = []
    monkeypatch.setattr(ks, "fire", lambda coro, label="": (started.append(label), coro.close()))
    resumed = ks.resume_interrupted()
    assert resumed == [a], resumed
    assert a in ks._stage and started and a in started[0]
    ks._stage.pop(a, None); ks._stage.pop(c, None)
    # 沒 claude CLI → 什麼都不接，也不動狀態（下次開機再看）
    ks._update_meta(a, status="compiling"); ks._stage.pop(a, None)
    monkeypatch.setattr(ks, "claude_available", lambda: False)
    assert ks.resume_interrupted() == [] and a not in ks._stage


def test_main_startup_resumes_interrupted_compiles_on_master_only():
    src = code_only(repo_src("main.py"))
    body = func_body(src, "_on_startup")
    assert "resume_interrupted" in body, "開機要把被重啟打斷的編譯接回去（只在 master：書架與 claude 都在那台）"
    seg = body.split("resume_interrupted", 1)[0][-600:]
    assert "is_master_machine" in seg
