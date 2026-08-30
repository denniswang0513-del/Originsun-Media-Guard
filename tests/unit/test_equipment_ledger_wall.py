# -*- coding: utf-8 -*-
"""器材的帳本牆 —— 讀寫兩側（/simplify 第 5 輪）。

🔴 2026-08-25 實測的洞：`GET /equipment/{id}` 會抹掉 mine 器材的
`purchase_cost`，但 `PUT` 的回應是裸的 —— 送一個**空 body**（exclude_unset 讓它
什麼都不改）就把 GET 藏起來的數字讀出來了（實測回 3,744）。同一個 token 也可以
直接 `PUT {"purchase_cost": …}` 改寫 owner 的私人器材：讀那側有牆、寫這側完全
沒有。牆要兩側都有才叫牆。
"""
from pathlib import Path
from types import SimpleNamespace as S

import pytest
from fastapi import HTTPException

from routers.api_equipment import _assert_mine_writable

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "routers/api_equipment.py").read_text(encoding="utf-8")


def _req(mine_scope: bool):
    """只要能被 core.money.viewer_has_mine_scope 讀懂就好。"""
    lvl = 3 if mine_scope else 1
    mods = ["finance_mine"] if mine_scope else ["equipment"]
    return S(scope={"type": "http"},
             headers={}, state=S(_auth_payload={"access_level": lvl, "modules": mods}))


@pytest.mark.parametrize("entity,scope,blocked", [
    ("mine", False, True),      # 私帳器材 + 沒有我的帳 scope → 擋
    ("mine", True, False),      # owner 自己 → 放行
    ("parent", False, False),   # 公司器材 → 一般同事照常編輯（不可誤傷）
    (None, False, False),       # 舊列 entity 為 NULL ＝ 公司
])
def test_write_guard(monkeypatch, entity, scope, blocked):
    monkeypatch.setattr("core.money.viewer_has_mine_scope", lambda _r: scope)
    equip = S(entity=entity)
    if blocked:
        with pytest.raises(HTTPException) as ex:
            _assert_mine_writable(equip, _req(scope))
        assert ex.value.status_code == 403
    else:
        _assert_mine_writable(equip, _req(scope))     # 不該拋


def _fn(name):
    return SRC.split(f"async def {name}(")[1].split("\n@router")[0]


def test_put_response_goes_through_the_read_wall():
    """🔴 這是那個洞本身：回應沒過牆＝一個用 PUT 當 GET 的旁路。"""
    body = _fn("update_equipment")
    assert "_strip_mine_money(_equip_dict(equip)" in body, "PUT 的回應沒過讀的牆"
    assert "_assert_mine_writable(equip, request)" in body, "PUT 沒有寫入側的牆"


def test_delete_has_the_write_guard():
    assert "_assert_mine_writable(equip, request)" in _fn("delete_equipment")


def test_entity_is_settable_on_create_and_frozen_on_update():
    """建立收得進帳本（否則 owner 從 UI 新增的私人器材折舊會進母公司損益表），
    更新則一律不得換帳本（換帳本是搬遷不是編輯）。"""
    assert 'require_entity(request, ent, level="full")' in _fn("create_equipment")
    assert "不能用更新換帳本" in _fn("update_equipment")


def test_list_can_filter_by_ledger():
    body = _fn("list_equipment")
    assert "Equipment.entity == entity" in body
    assert "未知的帳本" in body, "未知 entity 要 422，不要靜靜當成沒篩"


# ── 清冊分家（owner 2026-08-25「crm 如果是我的清冊就不要看到」）────────
def test_crm_equipment_tab_is_pinned_to_parent():
    """CRM 器材庫只看公司器材 —— 兩個取數點（列表＋chips 全量補抓）都要帶
    entity=parent，漏一個就會把 123 件私人器材混回來。"""
    js = (ROOT / "frontend/tabs/equipment/equipment.js").read_text(encoding="utf-8")
    assert "params.set('entity', 'parent')" in js
    assert "'?entity=parent'" in js, "chips 的全量補抓那條路也要釘"


def test_gear_subview_follows_the_current_book():
    """🔴 2026-08-30 反轉：器材清冊不再釘死私帳。owner「器材清單這些清單是
    私帳的，跟母公司沒關係，母公司的器材清單要另外建」—— 改成跟著當前帳本，
    母公司那本從零開始建（生產實查：123 件全在私帳、母公司 0 件）。

    清單走引擎端點（與 BS 的「器材淨值」同口徑）、建立落在**當前帳本**。
    """
    js = (ROOT / "frontend/tabs/finance/subviews/gear.js").read_text(encoding="utf-8")
    assert "finFetchMine" not in js.split("*/", 1)[1], "不再釘死私帳（檔頭註解不算）"
    assert "finFetch('/assets/equipment')" in js
    assert "entity: finEntity()" in js.split("method: 'POST'")[1][:160],         "建立要落在當前帳本"
    html = (ROOT / "frontend/tabs/finance/finance.html").read_text(encoding="utf-8")
    btn = [ln for ln in html.splitlines() if 'data-subview="gear"' in ln][0]
    assert "fin-nav-mine-only" not in btn, "兩本帳都看得到，不再是私帳專屬入口"
    assert "fin-nav-mine-ok" in btn, "但仍要有任一本帳的權"
