# -*- coding: utf-8 -*-
"""剛註冊的帳號只看得到基本資料，其餘依管理員授權開放（owner 2026-09-08）。

四個地方要一起守，漏一個就破：
1. 註冊與 Google 首登建帳的預設 modules 只有 me_profile。
2. me_profile 只開個人資料卡：「今天與這週」那一區與它後面的端點各看自己的鑰匙（core.auth.ME_ZONE1_KEYS，不含 me_profile）。
3. /my.html 真的畫得出個人資料卡（不然新帳號登入是一整頁空白）。
4. 主 SPA 對「登入了但沒有任何模組」只給免登入的後期流程頁，不再顯示全部 tab。
"""
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src


def test_new_accounts_get_only_the_profile_key():
    from routers.api_auth import _REGISTER_DEFAULT_MODULES
    assert list(_REGISTER_DEFAULT_MODULES) == ["me_profile"]
    src = repo_src("routers/api_auth.py")
    reg = code_only(func_body(src, "async def register("))
    assert "modules = list(_REGISTER_DEFAULT_MODULES)" in reg
    # Google 第一次登入自動建帳也是同一份預設（settings 有給 default_modules 才覆蓋）
    assert "g.get('default_modules') or _REGISTER_DEFAULT_MODULES" in src


def test_profile_key_is_not_a_zone_key():
    from core.auth import ME_MODULE_KEYS, ME_ZONE1_KEYS
    assert "me_profile" not in ME_ZONE1_KEYS and set(ME_ZONE1_KEYS) <= set(ME_MODULE_KEYS)
    assert "me_profile" in ME_MODULE_KEYS, "me_profile 仍是 workspace 的鑰匙（allowed 要帶它，卡片才畫）"


def test_today_zone_endpoints_require_their_own_key():
    """/me/today、/me/team_week、/me/projects_burn、/timesheets/mine*：綁了人員檔案、只有 me_profile 的帳號也進不去
    （2026-09-08 起一顆功能一把，細節在 test_me_zone_keys.py）。"""
    me = code_only(repo_src("routers/api_me.py"))
    assert 'require_bound_staff(request, "timesheets", *keys)' in func_body(me, "async def _me_bound(")
    ts = code_only(repo_src("routers/api_timesheets.py"))
    assert 'require_bound_staff(request, "timesheets", "me_worklog")' in func_body(ts, "async def _mine_ident(")
    assert "ME_MODULE_KEYS" not in ts.split("from core.auth import")[1].split("\n")[0]


def test_workspace_draws_the_profile_card_and_hides_the_today_zone_for_profile_only():
    html = repo_src("frontend/my.html")
    zone = html[html.index("const ME_ZONE_ON = new Set("):]
    zone = zone[:zone.index(")")]
    assert '"me_profile"' in zone, "個人資料卡要在 ME_ZONE_ON，不然只有 me_profile 的新帳號整頁空白"
    assert "ws.allowed.some(k => Z1_KEYS.includes(k))" in html and '"me_profile"' not in html[html.index("const Z1_KEYS"):html.index("const Z1_KEYS") + 120], \
        "「今天與這週」不能因為 me_profile 就出現（後端 ME_ZONE1_KEYS 同一條）"
    # 沒綁人員檔案的提醒仍然靠 staffSections（含 me_profile）：新帳號要看得到「請管理員綁定」
    assert "!ws.bound && ws.allowed.some(k => staffSections.includes(k))" in html


def test_spa_shows_only_media_tabs_when_a_login_has_no_modules():
    js = js_code_only(repo_src("frontend/js/shared/tab-config.js"))
    body = func_body(js, "export function shouldShowTab(")
    assert "if (!hasModules) return MEDIA_TABS.includes(key);" in body
    assert "loggedIn ? true" not in body, "「已登入＋空 modules＝全部 tab」的向下相容行為已拿掉"
