# -*- coding: utf-8 -*-
"""階段 4 收鑰匙（2026-09-08）：後期 9 把→postprod、前期 5 把→preprod、hr_leave＋hr_benefits→hr（47→34 把可勾選）。

做法＝「捆」：帳號與範本存捆鑰匙；發 token／存帳號／開機回填時 expand_modules 展開成「捆＋成員」，
所以成員級守衛（check_admin_or_module(request,'footage')）、TAB_MAP 的成員級分頁、Cloudflare 快取的舊 js 全部照舊。
"""
from core.auth import ALL_MODULES, LEGACY_MODULE_KEYS, MODULE_BUNDLES, MODULE_LABELS, expand_modules, payload_grants
from core.rbac_templates import DEFAULT_TEMPLATES, normalize
from tests.unit._srcscan import code_only, func_body, repo_src


def test_bundles_replace_their_members_in_the_assignable_list():
    assert {"postprod", "preprod", "hr"} <= set(ALL_MODULES)
    assert not (set(LEGACY_MODULE_KEYS) & set(ALL_MODULES)), "成員鑰匙不再是可勾選的模組"
    assert len(ALL_MODULES) == 34
    for b in MODULE_BUNDLES:
        assert b in MODULE_LABELS
    assert ALL_MODULES[0] == "bulletin", "管理員的 modules[0] 決定 SPA 落地頁，捆不能插到最前面"


def test_expand_is_idempotent_and_symmetric():
    assert expand_modules(["postprod"]) == ["postprod", *MODULE_BUNDLES["postprod"]]
    assert expand_modules(list(MODULE_BUNDLES["hr"])) == ["hr_leave", "hr_benefits", "hr"], "成員齊了補捆（舊帳號整組都有的，權限畫面那格才是勾的）"
    assert expand_modules(["backup"]) == ["backup"], "只有一個成員不補捆（不會憑空多權限）"
    once = expand_modules(["preprod", "me_profile"]); assert expand_modules(once) == once
    assert expand_modules(None) == [] and expand_modules([1, "x"]) == ["x"]


def test_guards_and_tokens_see_members():
    assert payload_grants({"access_level": 1, "modules": ["postprod"]}, "footage")
    assert payload_grants({"access_level": 1, "modules": ["hr"]}, "hr_leave")
    assert not payload_grants({"access_level": 1, "modules": ["preprod"]}, "references"), "片庫另一把"
    auth = repo_src("routers/api_auth.py")
    assert "expand_modules(grant_admin_all_modules(" in code_only(func_body(auth, "def _enrich_user(")), "讀帳號的咽喉展開一次，發 token／get_me 不各自再展"
    assert "'modules': expand_modules(req.modules or [])" in code_only(func_body(auth, "async def create_user("))
    assert "user['modules'] = expand_modules(req.modules)" in code_only(func_body(auth, "async def update_user("))
    main = repo_src("main.py")
    assert 'get("bundles_backfilled")' in main and "_bf3_expand(_mods)" in main, "開機一次性回填（旗標）"


def test_templates_store_bundles_and_absorb_legacy_keys():
    for ident, mods in DEFAULT_TEMPLATES.items():
        assert not (set(mods) & set(LEGACY_MODULE_KEYS)), ident
    assert {"postprod", "preprod"} <= set(DEFAULT_TEMPLATES["在職"]) and "hr" not in DEFAULT_TEMPLATES["在職"]
    t = normalize({"在職": ["backup", "hr_leave", "junk"]})
    assert "postprod" in t["在職"] and "hr" in t["在職"] and "backup" not in t["在職"]


def test_frontend_groups_and_labels_use_bundles():
    tc = repo_src("frontend/js/shared/tab-config.js")
    assert "modules: ['preprod', 'references']" in tc and "modules: ['postprod', 'comfyui']" in tc and "'crm_staff', 'timesheets', 'hr', 'journal'" in tc
    js = repo_src("frontend/js/admin/user-mgmt.js")
    for k in ("postprod:'", "preprod:'", "hr:'"):
        assert k in js
    assert "backup:'" not in js and "hr_leave:'" not in js
