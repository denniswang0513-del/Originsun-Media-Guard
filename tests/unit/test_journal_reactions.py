"""週記心情（owner 2026-09-05：不用回覆，大家可以按讚／愛心／笑）。

規則正本在 core.journal_logic（REACTION_KINDS、reaction_summary）；一人一條一種只有一筆（再按＝取消）；
只能對送出的週記按；列表端點（mine／week／person）每份都帶 reactions。前端回覆框已拿掉。"""
from core.journal_logic import REACTION_KINDS, norm_reaction, reaction_summary
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src


def test_kinds_and_normalize():
    assert REACTION_KINDS == ("like", "love", "laugh")
    assert norm_reaction(" Love ") == "love" and norm_reaction("angry") is None and norm_reaction("") is None


def test_reaction_summary_counts_and_mine():
    rows = [("e1", "amy", "like"), ("e1", "bob", "like"), ("e1", "amy", "love"), ("e2", "bob", "laugh"), ("e2", "bob", "nope")]
    out = reaction_summary(rows, "amy")
    assert out["e1"] == {"like": 2, "love": 1, "laugh": 0, "mine": ["like", "love"],
                         "users": {"like": ["amy", "bob"], "love": ["amy"], "laugh": []}}   # users 依按的先後（頭像列用）
    assert out["e2"] == {"like": 0, "love": 0, "laugh": 1, "mine": [], "users": {"like": [], "love": [], "laugh": ["bob"]}}   # 不認識的 kind 丟掉


def test_model_and_endpoint():
    assert "class JournalReaction(Base)" in repo_src("db/models/_workspace.py")
    assert 'name="uq_journal_reaction"' in repo_src("db/models/_workspace.py"), "一人一條一種只有一筆"
    assert "JournalReaction" in repo_src("db/models/__init__.py")
    src = code_only(repo_src("routers/api_journal.py"))
    body = func_body(src, "async def react_entry(")
    assert 'check_admin_or_module(request, "journal")' in body, "全員（有週記模組）都能按"
    assert "norm_reaction(body.kind)" in body and "status_code=409" in body   # 沒送出的不能按
    assert "for r in mine:" in body and "await session.delete(r)" in body and "session.add(JournalReaction(" in body   # 同種＝取消、別種＝換掉
    # 三個列表端點都帶 reactions（規則只有 _reactions_by_journal 一份）
    for fn in ("async def _mine_payload(", "async def week_journals(", "async def person_journals("):
        assert "_reactions_by_journal(" in func_body(src, fn), fn
    assert "reaction_summary(" in func_body(src, "async def _reactions_by_journal(")


def test_frontend_reacts_instead_of_replying():
    core = js_code_only(repo_src("frontend/js/shared/journal-core.js"))
    assert "export function reactionBar(" in core and "export const REACTIONS" in core
    assert "reactionBar((opts.reactions || {})[e.id]" in core, "條目清單畫心情列"
    assert "react: (body) =>" in core and "/api/v1/journal/react" in core
    # owner 2026-09-05 再改：每條下面不放心情列，只有標題列一個低調的愛心（整份週記一個），點一下＝愛心、長按換心情
    assert "export function heartHtml(" in core and "work_journals" in core
    page = js_code_only(repo_src("frontend/journal.html"))
    assert "data-reply-send" not in page and "canReply" not in page, "回覆框已拿掉"
    assert "canReact: true" not in page and "heartHtml(" in page
    assert "button[data-heart]" in page and "api.react(" in page and "heartMenu(" in page
    assert "setTimeout(() => { _pressed = true; heartMenu(b); }, 450)" in page, "長按開選單"
    assert "j.username !== ME" in page, "自己那份不能給自己愛心"
    api = code_only(repo_src("routers/api_journal.py"))
    body = func_body(api, "async def react_entry(")
    assert "WorkJournal.__tablename__" in body and "for r in mine:" in body, "整份週記可按；一人一個目標只留一種"


def test_react_call_passes_an_object_body_like_the_other_calls():
    """authFetch 自己 JSON 化 body；react 曾多包一層 JSON.stringify → 後端收到字串 → 422（前端 alert 出 [object Object]）。"""
    core = js_code_only(repo_src("frontend/js/shared/journal-core.js"))
    line = next(l for l in core.splitlines() if "react: (body)" in l)
    assert "JSON.stringify" not in line and "body })" in line
