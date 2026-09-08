# -*- coding: utf-8 -*-
"""公開區（owner 2026-09-08）：對外免登入的面集中一份登記表，owner 在使用者管理「公開區」分頁自己開關。

規則：預設全部＝連結（owner：「正常來說都是連結公開的」）；公開只有履歷與註冊；關閉＝404「此功能未開放」。
守衛只有一處 surface_gate：掛 public_router／token_router／portal router，/q/ 與 /register 手動 await。
設定的正本＝共用 Postgres（website_settings 的 public_access 那一筆）——同一批 public_router 在 master
與 NAS 對外容器各跑一份，設定存本機 settings.json 的話「關掉」只關得掉主機那台。
"""
import re

import pytest
from fastapi import HTTPException

import core.public_access as pa
from core.public_access import (MODE_LINK, MODE_OFF, MODE_OPEN, PUBLIC_SURFACES, SURFACE_KEYS, current_modes,
                                invalidate, mode_of, normalize, surface_for_path, surface_gate)
from tests.unit._srcscan import code_only, func_body, repo_src


@pytest.fixture(autouse=True)
def _clean_cache():
    """模組層快取是跨測試共用的狀態 —— 每支測試前後都清，不然順序一換就飄。"""
    invalidate()
    yield
    invalidate()


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


def _fake_db(value, reachable=True, counter=None):
    """假的 DB 讀取層（正本是 website_settings 那一筆）。counter 用來數「打了幾次 DB」。"""
    async def _read():
        if counter is not None:
            counter.append(1)
        return reachable, value
    return _read


async def test_gate_404s_only_when_closed(monkeypatch):
    monkeypatch.setattr(pa, "_db_modes", _fake_db({"expense": "off"}))
    with pytest.raises(HTTPException) as e:
        await surface_gate(_Req("/api/v1/crm/public/expense/tok/expenses"))
    assert e.value.status_code == 404 and "未開放" in str(e.value.detail)
    assert await surface_gate(_Req("/api/v1/crm/public/media-log/tok")) == "media_log"
    assert await surface_gate(_Req("/api/v1/crm/projects")) is None
    assert pa.MODE_OFF == "off"


async def test_modes_come_from_db_not_the_local_settings_file(monkeypatch):
    """DB 有值就以 DB 為準 —— settings.json 是舊正本，不能再蓋過共用設定。"""
    monkeypatch.setattr("config.load_settings", lambda: {"public_access": {"quote": "off"}})
    monkeypatch.setattr(pa, "_db_modes", _fake_db({"expense": "off"}))
    modes = await current_modes()
    assert modes["expense"] == MODE_OFF and modes["quote"] == MODE_LINK


async def test_ttl_cache_only_hits_db_once(monkeypatch):
    hits = []
    monkeypatch.setattr(pa, "_db_modes", _fake_db({"expense": "off"}, counter=hits))
    assert (await current_modes())["expense"] == MODE_OFF
    for _ in range(5):
        await surface_gate(_Req("/api/v1/crm/public/media-log/tok"))
    assert len(hits) == 1, "每個公開請求打一次 DB＝把對外頁的延遲綁在 DB 上"
    assert pa._CACHE_TTL >= 10, "TTL 太短＝NAS 容器每分鐘打幾十次 DB"


async def test_db_down_falls_back_and_never_blocks(monkeypatch):
    """DB 不可用 → 舊 settings.json → 登記表預設；**不能**因為資料庫離線把對外頁全部 404。"""
    hits = []
    monkeypatch.setattr(pa, "_db_modes", _fake_db(None, reachable=False, counter=hits))
    monkeypatch.setattr("config.load_settings", lambda: {"public_access": {"expense": "off"}})
    assert (await current_modes())["expense"] == MODE_OFF, "DB 讀不到才退 settings.json"
    assert len(hits) == 1
    invalidate()
    monkeypatch.setattr("config.load_settings", lambda: {})
    modes = await current_modes()
    assert all(modes[k] != MODE_OFF for k in SURFACE_KEYS), "兩邊都沒有 → 預設全開（link／open），不擋人"
    assert await surface_gate(_Req("/api/v1/crm/public/expense/tok/expenses")) == "expense"


async def test_db_read_layer_is_silent_when_db_is_offline(monkeypatch):
    """真的 `_db_modes`：DB 離線回 (False, None)，不 raise（公開請求不能被 503 打死）。"""
    import core.state as state
    monkeypatch.setattr(state, "db_online", False, raising=False)
    assert await pa._db_modes() == (False, None)


async def test_migrates_settings_json_into_db_once(monkeypatch):
    """DB 活著但沒那筆、settings.json 有 → 搬進 DB（之後就不必再走檔案那條）。"""
    moved = []
    monkeypatch.setattr(pa, "_db_modes", _fake_db(None, reachable=True))
    monkeypatch.setattr("config.load_settings", lambda: {"public_access": {"expense": "off"}})

    async def _mig(modes):
        moved.append(modes)
    monkeypatch.setattr(pa, "_migrate_file_to_db", _mig)
    assert (await current_modes())["expense"] == MODE_OFF
    assert moved and moved[0]["expense"] == MODE_OFF and moved[0]["register"] == MODE_OPEN, "搬進去的是 normalize 過的完整表"


async def test_save_writes_db_and_takes_effect_immediately(monkeypatch):
    """PUT 之後不必等 TTL：寫入端 invalidate，下一個請求就吃到新模式。"""
    written = {}

    class _Sess:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(pa, "_db_modes", _fake_db({"expense": "link"}))
    assert await surface_gate(_Req("/api/v1/crm/public/expense/tok/expenses")) == "expense"
    monkeypatch.setattr("core.db_guard.db_factory_or_503", lambda: (lambda: _Sess()))

    async def _upd(session, values, updated_by=None):
        written.update(values[pa.SETTING_KEY]); written["_by"] = updated_by
    import services.website.settings_service as ss
    monkeypatch.setattr(ss, "update_settings", _upd)

    out = await pa.save_modes({"expense": "off", "junk": "x"}, updated_by="admin")
    assert out["expense"] == MODE_OFF and "junk" not in out
    assert written["expense"] == MODE_OFF and written["_by"] == "admin"
    monkeypatch.setattr(pa, "_db_modes", _fake_db({"expense": "off"}))
    with pytest.raises(HTTPException):
        await surface_gate(_Req("/api/v1/crm/public/expense/tok/expenses"))


def test_gate_is_attached_where_public_traffic_enters():
    sh = repo_src("routers/crm/_shared.py")
    for rname in ("public_router", "token_router"):
        block = sh.split(f"{rname} = APIRouter(")[1][:300]
        assert "dependencies=[Depends(surface_gate)]" in block, rname
    assert "dependencies=[Depends(surface_gate)]" in repo_src("routers/api_portal.py").split("router = APIRouter(")[1].split("\n")[0]
    main = repo_src("main.py")
    for fn in ("async def _short_quote_view(", "async def _short_quote_pdf(", "async def _short_invoice_file("):
        assert "await surface_gate(request)" in code_only(func_body(main, fn)), fn
    # 提案分享／片庫分享的公開 router 住在各自的檔（NAS 容器也掛它們），不是 crm/_shared
    for f in ("routers/api_proposals.py", "routers/api_references.py"):
        block = repo_src(f).split("public_router = APIRouter(")[1][:300]
        assert "dependencies=[Depends(_surface_gate)]" in block, f
    auth = repo_src("routers/api_auth.py")
    for fn in ("async def register_config(", "async def register("):
        assert "await surface_gate(request)" in code_only(func_body(auth, fn)), fn
    for fn in ("async def get_public_access(", "async def put_public_access("):
        assert "_check_admin(request)" in code_only(func_body(auth, fn)), fn


def test_settings_live_in_the_shared_db_not_the_local_file():
    """正本＝共用 Postgres。NAS 對外容器有自己的 settings.json（publish 不同步它），
    設定寫檔＝owner 在畫面上關掉、對外那台照樣開著（2026-09-08 的 known issue）。"""
    src = repo_src("core/public_access.py")
    assert 'SETTING_KEY = "public_access"' in src and "settings_service" in src
    assert "async def surface_gate(" in src, "讀 DB → 守衛必須是 async"
    put = code_only(func_body(repo_src("routers/api_auth.py"), "async def put_public_access("))
    assert "save_modes(" in put and "save_settings" not in put, "寫入只走 DB，不再留第二份正本"
    get = code_only(func_body(repo_src("routers/api_auth.py"), "async def get_public_access("))
    assert "current_modes()" in get, "畫面與守衛必須是同一條讀取路徑"
    # 對外容器只帶 routers/services/core/db —— 讀取路徑用到的都要在同步清單裡
    sync = repo_src("publish_update.py").split("NAS_SYNC_CODE = ")[1].split("]")[0]
    for pkg in ("services", "core", "db"):
        assert f'"{pkg}"' in sync, pkg


def test_user_mgmt_has_the_public_tab():
    js = repo_src("frontend/js/admin/user-mgmt.js")
    assert ">公開區</button>" in js and "window._switchMgmtTab('pub')" in js
    assert "'/api/v1/auth/public-access'" in js and "window._savePublicAccess = " in js
    assert re.search(r"pub: 'umgmt-pub'", js)
    assert "只對主機生效" not in js, "設定已經共用 DB，舊的橘色警語不成立了"
