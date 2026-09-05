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
    assert out["e1"] == {"like": 2, "love": 1, "laugh": 0, "mine": ["like", "love"]}
    assert out["e2"] == {"like": 0, "love": 0, "laugh": 1, "mine": []}     # 不認識的 kind 丟掉


def test_model_and_endpoint():
    assert "class JournalReaction(Base)" in repo_src("db/models/_workspace.py")
    assert 'name="uq_journal_reaction"' in repo_src("db/models/_workspace.py"), "一人一條一種只有一筆"
    assert "JournalReaction" in repo_src("db/models/__init__.py")
    src = code_only(repo_src("routers/api_journal.py"))
    body = func_body(src, "async def react_entry(")
    assert 'check_admin_or_module(request, "journal")' in body, "全員（有週記模組）都能按"
    assert "norm_reaction(body.kind)" in body and "status_code=409" in body   # 沒送出的不能按
    assert "await session.delete(existing)" in body and "session.add(JournalReaction(" in body   # toggle
    # 三個列表端點都帶 reactions（規則只有 _reactions_by_journal 一份）
    for fn in ("async def _mine_payload(", "async def week_journals(", "async def person_journals("):
        assert "_reactions_by_journal(" in func_body(src, fn), fn
    assert "reaction_summary(" in func_body(src, "async def _reactions_by_journal(")


def test_frontend_reacts_instead_of_replying():
    core = js_code_only(repo_src("frontend/js/shared/journal-core.js"))
    assert "export function reactionBar(" in core and "export const REACTIONS" in core
    assert "reactionBar((opts.reactions || {})[e.id]" in core, "條目清單畫心情列"
    assert "react: (body) =>" in core and "/api/v1/journal/react" in core
    page = js_code_only(repo_src("frontend/journal.html"))
    assert "data-reply-send" not in page and "canReply" not in page, "回覆框已拿掉"
    assert "button[data-react]" in page and "api.react(" in page
    assert "canReact: true" in page
