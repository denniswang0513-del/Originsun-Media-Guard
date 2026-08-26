# -*- coding: utf-8 -*-
"""私帳客戶分家（owner 2026-08-26「我的客戶先不要混到 crm 系統，在個人帳裡
連結好確認客戶對應完再過去」）。

釘住的行為：
  1. CRM 各呼叫端（不帶 entity）永遠看不到 mine 客戶。
  2. 私帳名錄 = mine 客戶 + 已被私帳案引用的 parent 客戶（不逼 owner 建重複）。
  3. 客戶寫入守衛與 CRM 帳務同政策：mine 列開 mine full、parent 維持 Lv3。
  4. 更新不得換帳本；分級只算母公司案、mine 客戶不套分級。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_crm_client_list_hides_mine_by_default():
    src = _read("routers/crm/clients.py")
    fn = src.split("async def list_clients(")[1].split("\n@router")[0]
    # 預設分支＝排除 mine；mine 分支＝指名 scope ＋ 引用聯集
    assert "not_mine(Client.entity)" in fn
    assert 'require_entity(request, "mine", level="full")' in fn
    assert 'CrmProject.entity == "mine"' in fn, "mine 名錄要含被私帳案引用的 parent 客戶"


def test_client_write_guard_matches_finance_policy():
    src = _read("routers/crm/clients.py")
    create = src.split("async def create_client(")[1].split("\n@router")[0]
    assert 'require_entity(request, "mine", level="full")' in create
    assert "_check_auth(request)" in create          # parent 維持 Lv3
    assert 'exclude={"entity"}' in create, "entity 不得跟著 model_dump 亂入"
    for fn_name in ("update_client", "delete_client"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "check_logged_in(request)" in fn, f"{fn_name} 要先擋匿名"
        assert "_client_write_guard(request, client)" in fn, f"{fn_name} 要按列帳本驗"
    upd = src.split("async def update_client(")[1].split("\n@router")[0]
    assert 'exclude={"status", "entity"}' in upd, "更新不得換帳本"


def test_client_tier_counts_parent_projects_only():
    src = _read("routers/crm/_shared.py")
    fn = src.split("async def _auto_update_client_status(")[1].split("\nasync def ")[0]
    assert "not_mine(CrmProject.entity)" in fn, "分級混算私帳案會把共用客戶灌成舊客戶"
    assert '== "mine":\n        return' in fn.replace("'", '"'), "mine 客戶不套 CRM 分級"


def test_client_crm_link_is_mapping_not_merge():
    """私帳客戶管理（owner 2026-08-26「跟 crm 同步，但是用連結的方式」）：
    只記對應不搬資料；只有 mine 列能設、目標必須是 parent；名稱建議唯一
    候選才給（多個不猜）。"""
    import sys
    sys.path.insert(0, str(ROOT))
    from routers.crm.clients import _norm_client_name, _suggest_crm_match
    assert _norm_client_name("寬微廣告有限公司") == _norm_client_name("寬微廣告")
    parents = [{"id": "a", "short_name": "寬微廣告有限公司"},
               {"id": "b", "short_name": "典藏藝術家庭股份有限公司"}]
    assert _suggest_crm_match("寬微廣告", parents)["id"] == "a"
    assert _suggest_crm_match("典藏藝術", parents)["id"] == "b"   # 前綴（≥3 字）
    assert _suggest_crm_match("典藏", parents) is None           # 太短（<3 字）不猜
    two = parents + [{"id": "c", "short_name": "寬微廣告工作室"}]
    assert _suggest_crm_match("寬微廣告", two) is None            # 多候選不猜
    src = _read("routers/crm/clients.py")
    # 🔴 路徑不可是 /clients/mine-links —— 會被 /clients/{client_id} 吃掉
    assert '"/clients-mine-links"' in src and '"/clients/mine-links"' not in src
    fn = src.split("async def set_client_crm_link(")[1].split("\n@router")[0]
    assert "_client_write_guard(request, client)" in fn
    assert "只有私帳客戶能設定" in fn and "必須是 CRM（母公司）客戶" in fn


def test_mine_client_manager_subview_wiring():
    js = _read("frontend/tabs/finance/subviews/clients.js")
    assert "crmFetch('/clients-mine-links')" in js
    assert "searchableSelect(sel" in js          # 連結 picker 可搜尋
    html = _read("frontend/tabs/finance/finance.html")
    btn = [ln for ln in html.splitlines() if 'data-subview="clients"' in ln]
    assert len(btn) == 1 and "fin-nav-mine-only" in btn[0]


def test_private_picker_uses_mine_directory_and_can_create():
    js = _read("frontend/tabs/finance/subviews/projects.js")
    assert "crmFetch('/clients?entity=mine')" in js
    assert "entity: 'mine'" in js.split("crmFetch('/clients', {")[1][:220], \
        "快速建客戶必須落私帳（不然又混回 CRM）"
