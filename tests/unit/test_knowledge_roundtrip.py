# -*- coding: utf-8 -*-
"""知識庫 2026-09-18～19 那一批端點的**走一輪**特徵測試（/polish 2026-09-19 階段零安全網）。

既有的 test_knowledge_* 把純規則與原始碼不變式釘得很密，但延伸／分享／報告／圖輯這幾支
端點只被掃過原始碼、沒有真的用 TestClient 打過。這裡不判斷對錯，只把「現在的回應形狀」釘住，
好讓 review 修 bug 時改壞了別的分支會立刻紅。

fixture 沿用 test_knowledge_base 的 `kb`（tmp_path 書架、假 claude、fire() 攔下來）。
"""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import knowledge_logic as kl
from tests.unit.test_knowledge_base import URL, _make_pdf, kb  # noqa: F401 — fixture 由 pytest 依名字注入

_EXTEND_REPLY = ('[{"url":"https://a.tw/one","title_original":"One","title_zh":"第一篇","lang":"en",'
                 '"source":"NBER","published":"2026-09-10","summary_zh":"摘要。","why_it_matters":"有關",'
                 '"chapter_guess":""},'
                 '{"url":"https://a.tw/two","title_original":"Two","title_zh":"第二篇","lang":"zh-TW",'
                 '"source":"報導者","published":"","summary_zh":"摘要二。","why_it_matters":"",'
                 '"chapter_guess":""}]')


def _extend_reply(prompt: str):
    return _EXTEND_REPLY, ""


# ── 延伸：GET → POST（背景跑）→ GET → PUT 評分 ───────────────────────
def test_extend_round_trip_and_rating(kb):
    from services import knowledge_service as ks
    bid = kb.upload(pages=1)["id"]
    r = kb.client.get(f"{URL}/{bid}/extend")
    assert r.status_code == 200 and r.json() == {"items": [], "stage": "", "note": ""}

    kb.claude["reply"] = _extend_reply
    r = kb.client.post(f"{URL}/{bid}/extend", json={})
    assert r.status_code == 200 and r.json() == {"status": "searching"}
    assert kb.client.get(f"{URL}/{bid}/extend").json()["stage"] == "queued", "背景還沒跑：畫面要看得到在排隊"
    assert kb.client.post(f"{URL}/{bid}/extend", json={}).status_code == 409, "同一本同時只跑一輪"
    kb.run_fired()

    d = kb.client.get(f"{URL}/{bid}/extend").json()
    assert d["stage"] == "" and d["note"] == ""
    assert [(i["n"], i["url"], i["rating"]) for i in d["items"]] == [
        (1, "https://a.tw/one", ""), (2, "https://a.tw/two", "")]
    assert os.path.isfile(kb.book_path(bid, ks.EXTEND_FILE))

    r = kb.client.put(f"{URL}/{bid}/extend/2", json={"rating": "useless"})
    assert r.status_code == 200 and r.json()["item"]["rating"] == "useless"
    assert kb.client.put(f"{URL}/{bid}/extend/2", json={"rating": ""}).json()["item"]["rating"] == ""
    assert kb.client.put(f"{URL}/{bid}/extend/2", json={"rating": "meh"}).status_code == 400
    assert kb.client.put(f"{URL}/{bid}/extend/99", json={"rating": "useful"}).status_code == 404

    # 書架卡片的兩個計數跟著檔案走
    card = next(x for x in kb.client.get(URL).json() if x["id"] == bid)
    assert card["has_extend"] is True and card["extend_new"] == 2


def test_extend_with_nothing_found_leaves_a_note(kb):
    bid = kb.upload(pages=1)["id"]
    kb.claude["reply"] = lambda prompt: ("[]", "")
    assert kb.client.post(f"{URL}/{bid}/extend", json={}).status_code == 200
    kb.run_fired()
    d = kb.client.get(f"{URL}/{bid}/extend").json()
    assert d["items"] == [] and d["stage"] == "" and "沒有找到" in d["note"]


# ── 分享：私有開關 ↔ 公開那一面 ──────────────────────────────────
@pytest.fixture
def public_client():
    from routers import api_knowledge_public as pub
    app = FastAPI()
    app.include_router(pub.router)
    with TestClient(app) as c:
        yield c


def test_share_round_trip_private_switch_and_public_view(kb, public_client):
    bid = kb.upload(pages=1, title="分享的書")["id"]
    kb.client.put(f"{URL}/{bid}", json={"tags": ["財務"], "info": {"publisher": "聯經", "isbn": "123"}})
    r = kb.client.get(f"{URL}/{bid}/share")
    assert r.status_code == 200
    assert r.json() == {"on": False, "id": "", "url": "", "at": "", "parts": list(kl.SHARE_DEFAULT)}

    sh = kb.client.put(f"{URL}/{bid}/share", json={"on": True}).json()
    assert sh["on"] and len(sh["id"]) == 32 and sh["url"].endswith("#" + sh["id"]) and sh["at"]
    assert sh["parts"] == list(kl.SHARE_DEFAULT)

    pub = public_client.get(f"/api/v1/knowledge/public/{sh['id']}")
    assert pub.status_code == 200
    d = pub.json()
    assert d["title"] == "分享的書" and d["tags"] == ["財務"] and d["info"] == {"publisher": "聯經", "isbn": "123"}
    assert d["extend"] == [] and d["parts"] == list(kl.SHARE_DEFAULT) and d["shared_at"] == sh["at"]
    for private in ("conclusion", "notes", "skill", "chapters", "gallery"):
        assert private not in d, private
    # 沒勾「書裡的圖」：圖那一支當檔不存在
    assert public_client.get(f"/api/v1/knowledge/public/{sh['id']}/assets/p001-1.jpg").status_code == 404

    # 改勾選：id 不變
    sh2 = kb.client.put(f"{URL}/{bid}/share", json={"on": True, "parts": ["tags", "bogus"]}).json()
    assert sh2["id"] == sh["id"] and sh2["parts"] == ["tags"]
    d = public_client.get(f"/api/v1/knowledge/public/{sh['id']}").json()
    assert "info" not in d and "extend" not in d and d["tags"] == ["財務"]

    # 關掉：舊連結立刻 404；再開換一組新的
    off = kb.client.put(f"{URL}/{bid}/share", json={"on": False}).json()
    assert off == {"on": False, "id": "", "url": "", "at": "", "parts": list(kl.SHARE_DEFAULT)}
    assert public_client.get(f"/api/v1/knowledge/public/{sh['id']}").status_code == 404
    sh3 = kb.client.put(f"{URL}/{bid}/share", json={"on": True}).json()
    assert sh3["id"] != sh["id"]
    assert public_client.get("/api/v1/knowledge/public/" + "0" * 32).status_code == 404
    assert public_client.get("/api/v1/knowledge/public/not-an-id").status_code == 404


# ── 研究報告：清單與單篇 ──────────────────────────────────────────
def test_reports_list_and_read(kb, monkeypatch, tmp_path):
    from services import knowledge_report as kr
    rdir = tmp_path / "reports"
    rdir.mkdir()
    monkeypatch.setattr(kr, "reports_dir", lambda: str(rdir))
    assert kb.client.get(f"{URL}/reports").json() == []
    (rdir / "weekly-2026-W38.md").write_text("# 研究週報 2026-W38\n", encoding="utf-8")
    (rdir / "monthly-2026-09.md").write_text("# 研究月報 2026-09\n", encoding="utf-8")
    (rdir / "junk.md").write_text("x", encoding="utf-8")
    (rdir / "weekly-2026-W37.txt").write_text("x", encoding="utf-8")
    rows = kb.client.get(f"{URL}/reports").json()
    assert [(r["id"], r["title"], r["kind"]) for r in rows] == [
        ("weekly-2026-W38", "研究週報 2026-W38", "weekly"),
        ("monthly-2026-09", "研究月報 2026-09", "monthly")]
    assert all(r["bytes"] > 0 for r in rows)
    r = kb.client.get(f"{URL}/reports/weekly-2026-W38")
    assert r.status_code == 200
    assert r.json() == {"id": "weekly-2026-W38", "title": "研究週報 2026-W38", "md": "# 研究週報 2026-W38\n"}
    assert kb.client.get(f"{URL}/reports/weekly-2026-W99").status_code == 404
    assert kb.client.get(f"{URL}/reports/..%2Fmeta").status_code == 404


# ── 圖輯：沒圖的書回空清單、亂檔名 404 ───────────────────────────
def test_assets_list_and_get_for_a_book_without_figures(kb):
    bid = kb.upload(pages=1)["id"]
    assert kb.client.get(f"{URL}/{bid}/assets").json() == []
    assert kb.client.get(f"{URL}/{bid}/assets/p001-1.jpg").status_code == 404
    assert kb.client.get(f"{URL}/{bid}/assets/..%2Fmeta.json").status_code == 404


# ── 小工具 ─────────────────────────────────────────────────────
def test_week_span_is_monday_to_sunday_inclusive():
    import datetime as dt
    assert kl.week_span(dt.date(2026, 9, 14)) == (dt.date(2026, 9, 14), dt.date(2026, 9, 20))


def test_discord_webhook_url_prefers_the_environment(monkeypatch):
    import notifier
    monkeypatch.delenv("DISCORD_WEBHOOK", raising=False)
    assert notifier.discord_webhook_url({"notifications": {"discord_webhook": "https://d/x"}}) == "https://d/x"
    assert notifier.discord_webhook_url({"notifications": {}}) == ""
    monkeypatch.setenv("DISCORD_WEBHOOK", "https://env/y")
    assert notifier.discord_webhook_url({"notifications": {"discord_webhook": "https://d/x"}}) == "https://env/y"


# ── /polish 2026-09-19 階段一（每張卡一條紅→綠）────────────────────
def _part_reply(prompt: str):
    """假 claude：章節 pass 每一段都照提示在末尾寫 `## 圖說`；其餘走預設。"""
    import re as _re
    from tests.unit.test_knowledge_base import _default_reply
    if "===== 章文 =====" in prompt:
        m = _re.search(r"這是本章第 (\d+)/(\d+) 段", prompt)
        pi = m.group(1) if m else "1"
        return f"## 內容\n段落{pi}的筆記\n\n## 圖說\n- p001-1.jpg｜第{pi}段寫的圖說\n", ""
    return _default_reply(prompt)


def test_a_chapter_split_into_parts_keeps_every_part(kb, monkeypatch):
    """BUG-1：章太長切成兩段時，每段末尾都有 `## 圖說`；取走圖說那一段不能把第二段整個吃掉。"""
    from services import knowledge_service as ks
    bid = kb.upload(pages=1)["id"]
    monkeypatch.setattr(ks.kl, "chunk_text", lambda text, max_chars=0: [text[: len(text) // 2], text[len(text) // 2:]])
    kb.claude["reply"] = _part_reply
    assert kb.client.post(f"{URL}/{bid}/compile", json={}).status_code == 200
    kb.run_fired()
    meta = ks.read_meta(bid)
    assert meta["status"] == "compiled", meta.get("error")
    md = kb.read(bid, ks.CHAPTERS_DIR, meta["toc"][0]["file"])
    assert "段落1的筆記" in md and "段落2的筆記" in md, md
    assert "## 圖說" not in md, "圖說那一段要被取走，不留在文章裡"
    assert meta.get("asset_captions", {}).get("p001-1.jpg", "").startswith("第"), meta.get("asset_captions")


def test_parse_captions_takes_every_caption_section_not_just_the_first():
    """BUG-1 的純函式面：兩段各有一段 `## 圖說`，兩段的章文都要留、兩段的圖說都要收。"""
    md = ("## 內容\n第一段\n\n## 圖說\n- p001-1.jpg｜甲\n\n---\n\n"
          "## 內容\n第二段\n\n## 圖說\n- p002-1.jpg｜乙\n")
    body, caps = kl.parse_captions(md, ["p001-1.jpg", "p002-1.jpg"])
    assert caps == {"p001-1.jpg": "甲", "p002-1.jpg": "乙"}
    assert "第一段" in body and "第二段" in body and "---" in body
    assert "## 圖說" not in body and "甲" not in body


def test_a_rating_made_while_searching_survives_the_run(kb):
    """BUG-3：找資料要跑幾分鐘，畫面上的「有用／沒用」沒鎖；那一輪跑完寫檔時不能把期間按的評分洗掉。"""
    from services import knowledge_service as ks
    bid = kb.upload(pages=1)["id"]
    kb.claude["reply"] = _extend_reply
    kb.client.post(f"{URL}/{bid}/extend", json={})
    kb.run_fired()
    assert [i["n"] for i in ks.read_extend(bid)] == [1, 2]

    def _rate_then_reply(prompt: str):
        ks.rate_extend(bid, 1, "useful")            # claude 還在跑的時候他按了「有用」
        return ('[{"url":"https://a.tw/three","title_zh":"第三篇","title_original":"Three",'
                '"lang":"en","source":"x","published":"","summary_zh":"s","why_it_matters":"","chapter_guess":""}]', "")

    kb.claude["reply"] = _rate_then_reply
    kb.client.post(f"{URL}/{bid}/extend", json={})
    kb.run_fired()
    items = ks.read_extend(bid)
    assert [(i["n"], i["rating"]) for i in items] == [(1, "useful"), (2, ""), (3, "")], items


def test_toggling_the_share_is_serialized_with_other_meta_writes(kb):
    """BUG-6：update_meta_share 的 docstring 說走那把鎖，實作卻是裸的讀-改-寫。
    跟編譯那條的 _update_meta 對撞（同 test_update_meta_is_serialized_per_book 的 barrier 手法），
    最後 share 塊與另一個鍵都要在。"""
    import threading
    from services import knowledge_service as ks
    bid = kb.upload(pages=1)["id"]
    orig_read = ks.read_meta
    gate = threading.Barrier(2, timeout=1)   # 有鎖時第二個等不到 barrier，逾時就放行

    def slow_read(book_id):
        m = orig_read(book_id)
        try:
            gate.wait()
        except threading.BrokenBarrierError:
            pass
        return m

    ks.read_meta = slow_read
    try:
        t1 = threading.Thread(target=ks.update_meta_share, args=(bid, {"id": "a" * 32, "at": "x", "parts": ["tags"]}))
        t2 = threading.Thread(target=ks._update_meta, args=(bid,), kwargs={"asset_captions": {"p001-1.jpg": "圖"}})
        t1.start(); t2.start(); t1.join(5); t2.join(5)
    finally:
        ks.read_meta = orig_read
    m = ks.read_meta(bid)
    assert m.get("share", {}).get("id") == "a" * 32 and m.get("asset_captions") == {"p001-1.jpg": "圖"}, m


def test_opening_a_book_resets_pane_state_and_loads_the_pane():
    """BUG-4：S.pane 跨書持久，但 openBook 只清 chat 那組狀態、也只在 chat 分頁才抓資料。
    站在 A 的延伸／圖輯／資訊分頁回書架開 B：畫 A 的延伸清單（評分打到 B 的 n）、
    A 在找資料時 extendStage 卡住 → B 永遠不 loadExtend、圖輯與分享區也不抓。
    要求：openBook 把每本書自己的分頁狀態歸零，而且用**跟 switchPane 同一支** loader 抓當前分頁。"""
    from tests.unit._srcscan import js_code_only, js_func_body
    js = js_code_only(repo_src_js("frontend/js/knowledge/book.js"))
    body = js_func_body(js, "export async function openBook(")
    for key in ("S.extend = []", "S.extendStage = ''", "S.extendNote = ''", "S.concEdit = null",
                "S.focusEdit = false", "S.infoEdit = false"):
        assert key in body, "openBook 要歸零：" + key
    sw = js_func_body(js, "export function switchPane(")
    loader = next((name for name in ("_loadPaneData(",) if name in body and name in sw), None)
    assert loader, "openBook 與 switchPane 要共用同一支「這個分頁要抓什麼」的函式"
    loader_body = js_func_body(js, "function " + loader)
    for call in ("loadChat(", "loadExtend(", "loadGallery(", "loadShare("):
        assert call in loader_body, call
    for call in ("loadExtend(", "loadGallery(", "loadShare("):
        assert call not in sw, "switchPane 不該自己再抓一份：" + call


def repo_src_js(rel: str) -> str:
    from tests.unit._srcscan import repo_src
    return repo_src(rel)
