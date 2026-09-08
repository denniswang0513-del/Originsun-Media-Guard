# -*- coding: utf-8 -*-
"""客戶：**兩邊各一筆＋連結**（owner 2026-08-26 定案的最終形狀）。

owner 的話：「crm 有一筆，私帳有一筆，中間做連結」「留下這個連結的清單好做
日後使用」「雙方客戶連結後以 crm 的客戶清單為主要清單」「日後私帳如果新增
客戶，就直接新增進去 crm」。

（過程中曾走過「全部併進 CRM」一版，owner 否決 —— 保留兩份清單才是他要的
工作方式：CRM 是公司的主清單，私帳那份是他自己的帳，中間用 crm_link_id
對照。這批測試釘住最終形狀。）

釘住：
  1. 代稱唯一鍵＝(entity, short_name)：同一家公司兩本帳各一筆、名字一樣。
  2. CRM 清單只回 CRM 客戶；私帳名錄＝CRM 主檔＋**未連結**的私帳客戶
     （已連結的由 CRM 那筆代表 —— 同名兩筆並排沒人選得下去）。
  3. 建客戶一律落 CRM；帳本主人（lv1+finance_mine）可建，改/刪仍 Lv3。
  4. CRM 分級只算公司案；私帳客戶不套 CRM 分級。
  5. 代稱不再全域唯一 → 兩處靠代稱查客戶的地方要指名母公司那筆。
  6. 連結清單是交付物：端點 + 子視圖 + 匯出 CSV。
"""
from pathlib import Path
from tests.unit._srcscan import finance_src

ROOT = Path(__file__).resolve().parents[2]
NL = chr(10)


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_short_name_unique_per_entity():
    """全域唯一會讓「私帳一筆＋CRM 一筆」的第二筆被迫改名 —— 那是替約束
    服務不是替帳服務。改成每本帳各自唯一。"""
    from tests.unit._srcscan import models_src
    m = models_src()
    assert "short_name = Column(String(64), nullable=False)" in m, "不可再是 unique=True"
    assert 'UniqueConstraint("entity", "short_name"' in m
    from tests.unit._srcscan import migration_sql
    mig = migration_sql()
    assert "DROP CONSTRAINT IF EXISTS clients_short_name_key" in mig
    assert "uq_client_entity_short_name" in mig


def test_client_list_scopes():
    src = _read("routers/crm/clients.py")
    fn = src.split("async def list_clients(")[1].split(NL + "@router")[0]
    assert "not_mine(Client.entity)" in fn, "CRM 清單只回 CRM 客戶"
    assert 'require_entity(request, "mine", level="full")' in fn
    # 私帳名錄＝主檔 ＋ 未連結的私帳客戶（已連結的由 CRM 那筆代表）
    assert "Client.crm_link_id.is_(None)" in fn


def test_create_always_lands_in_crm_and_owner_may_create():
    """「日後私帳如果新增客戶，就直接新增進去 crm」——建客戶一律落主檔；
    帳本主人（lv1+finance_mine，刻意不是 Lv3）要建得了，否則私帳建案卡 403。"""
    src = _read("routers/crm/clients.py")
    create = src.split("async def create_client(")[1].split(NL + "@router")[0]
    assert 'ent = "parent"' in create
    # 2026-09-08 第二批：母帳建客戶＝客戶／專案／報價三把鑰匙（_check_client_write），不再是 Lv3
    assert "_check_client_write(request, record=False)" in create and "_check_auth(request)" not in create
    assert 'require_entity(request, "mine", level="full")' in create
    assert 'exclude={"entity"}' in create, "entity 不得跟著 model_dump 亂入"


def test_client_write_guard_is_row_scoped():
    src = _read("routers/crm/clients.py")
    for fn_name in ("update_client", "delete_client"):
        fn = src.split("async def " + fn_name + "(")[1].split(NL + "@router")[0]
        assert "check_logged_in(request)" in fn, fn_name + " 要先擋匿名"
        assert "_client_write_guard(request, client" in fn, fn_name + " 要按列帳本驗"
    # 刪客戶留管理員（owner 2026-09-08 ADMIN_ONLY_ACTIONS）；改客戶走三把鑰匙
    dele = src.split("async def delete_client(")[1].split(NL + "@router")[0]
    assert "_client_write_guard(request, client, admin_only=True)" in dele
    upd = src.split("async def update_client(")[1].split(NL + "@router")[0]
    assert 'exclude={"status", "entity"}' in upd, "更新不得換帳本"


def test_crm_tier_counts_company_projects_only():
    """CRM 客戶的分級＝**公司**的客戶關係（私帳案掛在私帳那一筆上）；
    門檻收在 core.crm_logic.client_tier（端點與遷移腳本共用一份）。"""
    from core.crm_logic import client_tier
    assert (client_tier(0), client_tier(1), client_tier(2), client_tier(30)) == \
        ("潛在客戶", "新客戶", "舊客戶", "舊客戶")
    src = _read("routers/crm/_shared.py")
    fn = src.split("async def _auto_update_client_status(")[1].split(NL + "async def ")[0]
    assert "not_mine(CrmProject.entity)" in fn, "CRM 分級不算私帳案"
    assert "client_tier(count)" in fn, "門檻要走 core 那份"
    assert '== "mine":' in fn.replace("'", '"'), "私帳客戶不套 CRM 分級"


def test_short_name_lookups_pin_the_company_row():
    """代稱不再全域唯一 —— 靠代稱查客戶的地方不指名母公司那筆就會撈到兩列
    （發票 join 會讓同一張發票回兩次）。"""
    inv = finance_src()
    seg = inv.split("Client.short_name == CrmInvoice.company_name")[1][:140]
    assert "_cli_not_mine(Client.entity)" in seg
    ws = _read("services/website/project_service.py")
    seg2 = ws.split("Client.short_name == new_client_text")[1][:140]
    assert 'Client.entity != "mine"' in seg2


def test_link_list_is_the_deliverable():
    """連結清單要留著日後使用：端點回對應狀態＋建議，子視圖能連/解/匯出。"""
    src = _read("routers/crm/clients.py")
    # 🔴 路徑不可是 /clients/mine-links —— 會被 /clients/{client_id} 吃掉
    assert '"/clients-mine-links"' in src and '"/clients/mine-links"' not in src
    fn = src.split("async def set_client_crm_link(")[1].split(NL + "@router")[0]
    assert "_client_write_guard(request, client)" in fn
    assert "只有私帳客戶能設定" in fn and "必須是 CRM（母公司）客戶" in fn
    js = _read("frontend/tabs/finance/subviews/clients.js")
    assert "crmFetch('/clients-mine-links')" in js
    assert "_finCli.link(" in js and "解除" in js
    assert "exportCsv" in js and "連結清單" in js, "連結清單要能匯出留存"
    assert "searchableSelect(sel" in js, "挑 CRM 客戶要能搜尋"


def test_name_suggestion_only_when_unique():
    from routers.crm.clients import _norm_client_name, _suggest_crm_match
    assert _norm_client_name("寬微廣告有限公司") == _norm_client_name("寬微廣告")
    parents = [{"id": "a", "short_name": "寬微廣告有限公司"},
               {"id": "b", "short_name": "典藏藝術家庭股份有限公司"}]
    assert _suggest_crm_match("寬微廣告", parents)["id"] == "a"
    assert _suggest_crm_match("典藏藝術", parents)["id"] == "b"   # 前綴（≥3 字）
    assert _suggest_crm_match("典藏", parents) is None           # 太短（<3 字）不猜
    two = parents + [{"id": "c", "short_name": "寬微廣告工作室"}]
    assert _suggest_crm_match("寬微廣告", two) is None            # 多候選不猜


def test_twin_link_tool_shape():
    """建分身＋連結的工具：只對**在公司客戶清單裡**的私帳客戶動作
    （其餘不建不連），已連結的跳過（冪等）。"""
    src = _read("scripts/twin_link_my_clients.py")
    assert "不在公司客戶清單" in src and "不建不連" in src
    assert 'if m["crm_link_id"]:' in src and "skip += 1" in src
    assert "uq_client_entity_short_name" in src, "要把唯一鍵換成每本帳唯一"
    assert "連結指向非 CRM 客戶" in src, "寫完要驗連結都指向 CRM 客戶"


def test_private_picker_uses_master_and_creates_crm_client():
    js = _read("frontend/tabs/finance/subviews/projects.js")
    assert "crmFetch('/clients?entity=mine')" in js
    seg = js.split("crmFetch('/clients', {")[1][:260]
    assert "entity: 'mine'" not in seg, "快速建客戶要進 CRM 主檔"
    assert "short_name: g('fpc-newclient')" in seg
