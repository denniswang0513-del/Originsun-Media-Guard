# -*- coding: utf-8 -*-
"""現金流勾稽差額要講**真正的**原因（owner 2026-08-22）。

owner 把所有收支都掛上帳戶之後，差額還在 $65,880 —— 因為畫面上那句
「可能有收支未掛帳戶」是寫死的，而後端 `build_cashflow` 早就算出真正的原因
（轉存未完全成對），只是被前端丟掉了。

猜的原因比沒有原因更糟：它讓人往錯的方向修一整輪。
"""
from tests.unit._srcscan import code_only, func_body, repo_src

SRC = "core/finance_logic.py"
JS = "frontend/tabs/finance/subviews/statements.js"


def _cf():
    return code_only(func_body(repo_src(SRC), "def build_cashflow("))


# ── 後端本來就算得出原因 ──────────────────────────────────────────

def test_backend_explains_all_three_causes():
    body = _cf()
    for cause in ("員工預支往來淨流", "帳戶間轉存未完全成對", "未掛帳戶收支未列入"):
        assert cause in body, f"少了「{cause}」這個原因"


def test_diff_is_closing_minus_opening_plus_net():
    """定義要釘住 —— 方向反了整段解讀就顛倒。"""
    body = _cf()
    assert ('diff = int(closing.get("total") or 0) - '
            '(int(opening.get("total") or 0) + net)') in body


def test_unassigned_entries_are_excluded_from_flow():
    """未掛帳戶的收支不影響任何帳戶餘額，計入就會破壞恆等式。"""
    body = _cf()
    assert 'if not e.get("bank_account_id"):' in body
    assert "unassigned += 1" in body and "continue" in body


def test_transfer_principal_is_tracked_for_the_note():
    """轉存本金不列入活動，但差額要能歸因到它。"""
    body = _cf()
    assert "transfer_net += principal" in body


# ── 前端要把原因講出來 ────────────────────────────────────────────

def _cf_html():
    """🔴 框到 _cfHtml 裡面 —— `const checkHtml` 在這個檔案有兩處
    （資產負債表也有一個），全檔 index() 會抓到另一份（實測踩到）。"""
    js = repo_src(JS)
    i = js.index("function _cfHtml(")
    return js[i:js.index(chr(10) + "function ", i + 10)]


def test_ui_shows_the_backend_notes():
    seg = _cf_html()
    assert "chk.notes" in seg, "沒有把後端的 notes 取出來"
    assert "notes.map(" in seg, "notes 沒有畫出來"


def test_ui_no_longer_guesses_a_single_cause():
    """🔴 這一句就是把 owner 帶偏的元凶。"""
    js = repo_src(JS)
    assert "期初＋淨流 ≠ 期末，可能有收支未掛帳戶" not in js, \
        "又把原因寫死成「未掛帳戶」了"


def test_ui_says_so_when_there_is_no_known_cause():
    """notes 空的時候不能沉默 —— 那代表系統自己也不知道，要講出來。"""
    assert "系統找不到已知原因" in _cf_html()
