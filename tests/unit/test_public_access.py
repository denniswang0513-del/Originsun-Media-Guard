# -*- coding: utf-8 -*-
"""公開區（owner 2026-09-08）：對外免登入的面集中一份登記表，owner 在使用者管理「公開區」分頁自己開關。

規則：預設全部＝連結（owner：「正常來說都是連結公開的」）；公開只有履歷與註冊；關閉＝404「此功能未開放」。
守衛只有一處 surface_gate：掛 public_router／token_router／portal router，/q/ 與 /register 手動呼叫。
"""
import re

import pytest
from fastapi import HTTPException

from core.public_access import (MODE_LINK, MODE_OFF, MODE_OPEN, PUBLIC_SURFACES, SURFACE_KEYS, mode_of, normalize,
                                surface_for_path, surface_gate)
from tests.unit._srcscan import code_only, func_body, repo_src


def test_defaults_all_link_except_resume_and_register():
    d = normalize(None)
    for k in SURFACE_KEYS:
        assert d[k] == (MODE_OPEN if k in ("resume", "register") else MODE_LINK), k
    for s in PUBLIC_SURFACES:
        assert MODE_OFF in s["modes"] and s["default"] in s["modes"]
        assert (MODE_OPEN in s["modes"]) == (s["key"] in ("resume", "register")), "公開只有沒 token 的兩面"


def test_normalize_drops_junk_and_unsupported_modes():
    out = normalize({"expense": "open", "register": "off", "junk": "x", "portal": 3})
    assert out["expense"] == MODE_LINK, "雜支不支援公開 → 退回預設"
    assert out["register"] == MODE_OFF and out["portal"] == MODE_LINK and "junk" not in out
    assert mode_of("quote", {"quote": "off"}) == MODE_OFF and mode_of("quote", None) == MODE_LINK


def test_every_prefix_hits_a_real_route_and_maps_back():
    routes = ""
    for f in ("routers/crm/costs.py", "routers/crm/media_log.py", "routers/api_portal.py", "main.py", "routers/crm/quotes.py",
              "routers/crm/invoice_files.py", "routers/api_proposals.py", "routers/api_references.py", "routers/crm/showcase.py", "routers/crm/staff.py", "routers/api_auth.py"):
        routes += repo_src(f)
    tails = {"/api/v1/crm/public/expense/": '"/public/expense/', "/api/v1/crm/public/media-log/": '"/public/media-log/',
             "/api/v1/portal/public/": '"/public/{token}', "/q/": '"/q/{code}"', "/api/v1/crm/public/quote/": '"/public/quote/',
             "/e/": '"/e/{code}"', "/api/v1/crm/public/invoice-file/": '"/public/invoice-file/',
             "/api/v1/proposals/shared/": '"/shared/{token}', "/api/v1/references/shared/": '"/shared/{token}/',
             "/api/v1/crm/public/showcase-edit/": '"/public/showcase-edit/', "/api/v1/crm/public/staff-edit/": '"/public/staff-edit/',
             "/api/v1/crm/public/staff/": '"/public/staff/{staff_id}/resume"', "/api/v1/auth/register": '"/register"'}
    for s in PUBLIC_SURFACES:
        for pre in s["prefixes"]:
            assert tails[pre] in routes, f"{s['key']} 的前綴 {pre} 對不到任何路由"
            assert surface_for_path(pre + "xyz") == s["key"]
    assert surface_for_path("/api/v1/portal/links") is None and surface_for_path("/api/v1/crm/projects") is None, "內部端點不歸公開區管"


class _Req:
    def __init__(self, path):
        class _U: pass
        self.url = _U(); self.url.path = path


def test_gate_404s_only_when_closed(monkeypatch):
    import core.public_access as pa
    monkeypatch.setattr("config.load_settings", lambda: {"public_access": {"expense": "off"}})
    with pytest.raises(HTTPException) as e:
        surface_gate(_Req("/api/v1/crm/public/expense/tok/expenses"))
    assert e.value.status_code == 404 and "未開放" in str(e.value.detail)
    assert surface_gate(_Req("/api/v1/crm/public/media-log/tok")) == "media_log"
    assert surface_gate(_Req("/api/v1/crm/projects")) is None
    assert pa.MODE_OFF == "off"


def test_gate_is_attached_where_public_traffic_enters():
    sh = repo_src("routers/crm/_shared.py")
    for rname in ("public_router", "token_router"):
        block = sh.split(f"{rname} = APIRouter(")[1][:300]
        assert "dependencies=[Depends(surface_gate)]" in block, rname
    assert "dependencies=[Depends(surface_gate)]" in repo_src("routers/api_portal.py").split("router = APIRouter(")[1].split("\n")[0]
    main = repo_src("main.py")
    for fn in ("async def _short_quote_view(", "async def _short_quote_pdf(", "async def _short_invoice_file("):
        assert "surface_gate(request)" in code_only(func_body(main, fn)), fn
    # 提案分享／片庫分享的公開 router 住在各自的檔（NAS 容器也掛它們），不是 crm/_shared
    for f in ("routers/api_proposals.py", "routers/api_references.py"):
        block = repo_src(f).split("public_router = APIRouter(")[1][:300]
        assert "dependencies=[Depends(_surface_gate)]" in block, f
    auth = repo_src("routers/api_auth.py")
    for fn in ("async def register_config(", "async def register("):
        assert "surface_gate(request)" in code_only(func_body(auth, fn)), fn
    for fn in ("async def get_public_access(", "async def put_public_access("):
        assert "_check_admin(request)" in code_only(func_body(auth, fn)), fn
    assert 'save_settings({"public_access": modes})' in code_only(func_body(auth, "async def put_public_access("))


def test_user_mgmt_has_the_public_tab():
    js = repo_src("frontend/js/admin/user-mgmt.js")
    assert ">公開區</button>" in js and "window._switchMgmtTab('pub')" in js
    assert "'/api/v1/auth/public-access'" in js and "window._savePublicAccess = " in js
    assert re.search(r"pub: 'umgmt-pub'", js)
