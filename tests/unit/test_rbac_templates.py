# -*- coding: utf-8 -*-
"""身份範本（owner 2026-09-08）：合夥／在職／兼職各一組「預設就有」的鑰匙，在使用者管理的「身份範本」分頁勾。

不是角色層：範本存 settings.json rbac.templates，套用＝寫回帳號自己那份 modules；守衛與 token 不看範本。
合夥＝owner 拍板「除了私帳以外全部」。
"""
from core.auth import ALL_MODULES, ME_ZONE_MASTER
from core.rbac_templates import DEFAULT_TEMPLATES, IDENTITIES, diff, normalize, template_for
from tests.unit._srcscan import code_only, func_body, repo_src


def test_partner_default_is_everything_but_private_ledger():
    p = set(DEFAULT_TEMPLATES["合夥"])
    assert "finance_mine" not in p, "私帳指名才有，合夥也不給（owner 2026-09-08）"
    assert p == set(ALL_MODULES) - {"finance_mine", "me_todos", "me_finance"}, "其餘全部（畫面未開的兩張卡除外）"
    assert "website_admin" in set(DEFAULT_TEMPLATES["在職"]) and "website_admin" in set(DEFAULT_TEMPLATES["兼職"]), "官網管理三個身份都有"
    for ident in IDENTITIES:
        assert set(DEFAULT_TEMPLATES[ident]) <= set(ALL_MODULES)
        assert DEFAULT_TEMPLATES[ident] == [m for m in ALL_MODULES if m in DEFAULT_TEMPLATES[ident]], "照 ALL_MODULES 排"
    assert set(DEFAULT_TEMPLATES["兼職"]) < set(DEFAULT_TEMPLATES["在職"]) < set(DEFAULT_TEMPLATES["合夥"])


def test_normalize_filters_junk_adds_master_and_fills_missing():
    out = normalize({"在職": ["me_worklog", "not_a_key", 42], "兼職": "nope"})
    assert out["在職"] == [ME_ZONE_MASTER, "me_worklog"] or set(out["在職"]) == {ME_ZONE_MASTER, "me_worklog"}
    assert out["在職"] == [m for m in ALL_MODULES if m in {ME_ZONE_MASTER, "me_worklog"}], "子鑰匙自動補總開關、照 ALL_MODULES 排"
    assert out["兼職"] == DEFAULT_TEMPLATES["兼職"] and out["合夥"] == DEFAULT_TEMPLATES["合夥"], "缺的／壞的身份用預設值"
    assert normalize(None) == DEFAULT_TEMPLATES and normalize("x") == DEFAULT_TEMPLATES


def test_template_for_and_diff():
    t = normalize(None)
    assert template_for("", t) == t["在職"] and template_for(None, t) == t["在職"], "空白視同在職"
    assert template_for(" 兼職 ", t) == t["兼職"]
    assert template_for("專案", t) is None and template_for("單位", t) is None
    d = diff(["me_profile", "finance_mine"], ["me_profile", "me_today_zone"])
    assert d == {"missing": ["me_today_zone"], "extra": ["finance_mine"]}


def test_endpoints_are_admin_only_and_store_normalized():
    src = repo_src("routers/api_auth.py")
    for fn in ("async def get_rbac_templates(", "async def put_rbac_templates("):
        body = code_only(func_body(src, fn))
        assert "_check_admin(request)" in body, fn
    put = code_only(func_body(src, "async def put_rbac_templates("))
    assert "normalize(body.templates)" in put and 'save_settings({"rbac": rbac})' in put
    assert "_persist_user" not in put and "_get_all_users" not in put, "存範本不動任何帳號"


def test_user_mgmt_has_the_template_tab_and_apply_button():
    js = repo_src("frontend/js/admin/user-mgmt.js")
    assert "window._switchMgmtTab('tpl')" in js and ">身份範本</button>" in js
    assert "'/api/v1/auth/rbac/templates'" in js
    for fn in ("_saveRbacTemplates", "_resetRbacTemplates", "_tplBulk", "_applyTemplateToUser"):
        assert f"window.{fn} = " in js, fn
    assert "套${staffStatus}範本" in js, "使用者列上的套範本鈕"
    assert "TPL_HIDDEN = new Set(['me_todos', 'me_finance'])" in js
