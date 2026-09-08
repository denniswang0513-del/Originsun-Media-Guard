# -*- coding: utf-8 -*-
"""權限改了不用重新登入（2026-09-08，劉禮瑜「今天與這週」消失）。

modules 是登入時簽進 JWT 的快照：管理員改權限、開機回填補鑰匙，已發出的 token 都不知道。
修法：/auth/me 與 /me/workspace 發現 token 裡的授權跟帳號現在的不一樣，順手回一顆新 token（走既有的
/auth/refresh 規則：API key／分享 token 不換、90 天絕對壽命），前端每個讀這兩支的地方都把它換進 localStorage。
"""
from core.auth import claims_drifted
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src


def test_claims_drifted_is_order_insensitive_and_checks_level():
    assert not claims_drifted({"modules": ["a", "b"], "access_level": 1}, {"modules": ["b", "a"], "access_level": 1})
    assert claims_drifted({"modules": ["a"], "access_level": 1}, {"modules": ["a", "me_today_zone"], "access_level": 1})
    assert claims_drifted({"modules": ["a"], "access_level": 1}, {"modules": ["a"], "access_level": 3})
    assert not claims_drifted({"modules": None, "access_level": None}, {"modules": [], "access_level": 0})
    assert not claims_drifted(None, {"modules": ["a"]}) and not claims_drifted({"modules": ["a"]}, None)


def test_refresh_and_me_share_one_renewal_rule():
    src = repo_src("routers/api_auth.py")
    refresh = code_only(func_body(src, "async def refresh_token("))
    assert "_reissue_login_token(payload)" in refresh, "/refresh 走共用的重簽"
    rule = code_only(func_body(src, "def _renewable_login_payload("))
    for guard in ("== 'api_key'", "payload.get('scope') or payload.get('purpose')", "90 * 86400"):
        assert guard in rule, guard
    drift = code_only(func_body(src, "async def refreshed_token_if_drifted("))
    assert "claims_drifted(payload, user)" in drift and "_reissue_login_token(payload, user)" in drift
    assert "except HTTPException" in drift, "換不了（超過 90 天等）就不帶 token，不能讓 /me 變 401"
    me = code_only(func_body(src, "async def get_me("))
    assert "refreshed_token_if_drifted(payload, user)" in me and "out['token'] = fresh" in me
    ws = code_only(func_body(repo_src("routers/api_me.py"), "async def my_workspace("))
    assert "refreshed_token_if_drifted(payload, await _find_user(" in ws and '{"token": fresh}' in ws


def test_every_auth_me_reader_adopts_the_fresh_token():
    # my.html：換掉再抓一次（allowed 是照 token 算的），而且只會多抓這一次
    z = js_code_only(repo_src("frontend/my.html"))
    body = js_func_body(z, "async function loadWorkspace()")
    assert "localStorage.setItem(TOKEN_KEY, WS.token);" in body and "if (!_tokenSwapRetried) { _tokenSwapRetried = true; return loadWorkspace(); }" in body, "換 token 只補抓一次（有上限）"
    assert "WS.token !== localStorage.getItem(TOKEN_KEY)" in body, "沒這個判斷會無限重抓"
    # SPA：單點在 _fetchMe
    a = repo_src("frontend/js/auth/auth-state.js")
    assert "localStorage.setItem(STORAGE_KEYS.TOKEN, d.token); window._authToken = d.token;" in js_func_body(js_code_only(a), "async function _fetchMe()")
    # 手機殼與每個獨立頁
    assert "if (me && me.token) localStorage.setItem(TOKEN_KEY, me.token);" in repo_src("frontend/m/shell.js")
    for f in ("frontend/website-admin.html", "frontend/petty-cash.html", "frontend/media-log-workspace.html",
              "frontend/project.html", "frontend/reference.html"):
        assert "if (me.token) localStorage.setItem(TOKEN_KEY, me.token);" in repo_src(f), f


def test_admin_and_bundle_holders_are_not_permanently_drifted():
    """token 簽的是 expand_modules 過的清單、帳號存的可能只有捆（管理員＝ALL_MODULES 三把捆）：
    兩邊要先展開再比，不然每打一次 /auth/me 就重簽一顆 token，my.html 一直重抓。"""
    from core.auth import ALL_MODULES, claims_drifted, expand_modules, grant_admin_all_modules
    admin_stored = {"modules": grant_admin_all_modules(3, []), "access_level": 3}
    admin_token = {"modules": expand_modules(admin_stored["modules"]), "access_level": 3}
    assert claims_drifted(admin_token, admin_stored) is False
    assert "hr_leave" in grant_admin_all_modules(3, []), "管理員的成員鍵要展開（/me/workspace 的 hr_manager 判定）"
    lv1_stored = {"modules": ["postprod", "me_profile"], "access_level": 1}
    lv1_token = {"modules": expand_modules(lv1_stored["modules"]), "access_level": 1}
    assert claims_drifted(lv1_token, lv1_stored) is False
    assert claims_drifted(lv1_token, {"modules": ["me_profile"], "access_level": 1}) is True, "真的改了權限還是要漂"
    assert all(m in ALL_MODULES for m in ("postprod", "preprod", "hr"))
