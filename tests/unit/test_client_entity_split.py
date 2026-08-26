# -*- coding: utf-8 -*-
"""客戶主檔統一（owner 2026-08-26 三句話定案）：

  ① 「我的客戶先不要混到 crm 系統」 → 分家（clients.entity='mine'）
  ② 「把私帳的客戶都整合到 crm 系統裡面」 → 84 家併回 CRM，**主檔只有一份**
  ③ 「日後私帳如果新增客戶，就直接新增進去 crm」 → 建客戶一律落 CRM

釘住併完之後的行為：
  1. 私帳名錄（entity=mine）＝整份主檔（統一後私帳要挑得到任何 CRM 客戶）。
  2. CRM 預設清單仍排除 entity='mine'（防呆：又冒出私帳客戶不會靜靜混進來）。
  3. 建客戶一律建 CRM 客戶；帳本主人（lv1+finance_mine）可建、改/刪仍 Lv3。
  4. 分級＝客戶關係，兩本帳的案子都算（金額合計才排除私帳）。
  5. 併的工具（scripts/merge_my_clients_to_crm）先擋模糊重名才動手。

錢不受影響：金額的門綁在**專案**的 entity，不在客戶。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_client_list_default_excludes_mine_and_mine_sees_whole_master():
    src = _read("routers/crm/clients.py")
    fn = src.split("async def list_clients(")[1].split(chr(10) + "@router")[0]
    assert "not_mine(Client.entity)" in fn, "CRM 預設清單仍要排除私帳客戶（防呆）"
    assert 'require_entity(request, "mine", level="full")' in fn
    # 統一之後 mine 分支不再過濾 —— 回整份主檔
    assert "Client.id.in_(_mine_refs)" not in fn, "統一後不該再只回『私帳用過的』"
    assert 'if entity != "mine":' in fn


def test_create_always_lands_in_crm_master_and_owner_may_create():
    """③「日後私帳新增客戶就直接新增進去 crm」—— 建客戶一律落主檔；
    帳本主人（lv1+finance_mine，刻意不是 Lv3）要建得了，否則私帳建案卡 403。"""
    src = _read("routers/crm/clients.py")
    create = src.split("async def create_client(")[1].split(chr(10) + "@router")[0]
    assert 'ent = "parent"' in create, "一律建 CRM 客戶（再建私帳專屬就又分裂）"
    assert "_check_auth(request)" in create
    assert 'require_entity(request, "mine", level="full")' in create
    assert 'exclude={"entity"}' in create, "entity 不得跟著 model_dump 亂入"


def test_client_write_guard_still_row_scoped():
    src = _read("routers/crm/clients.py")
    for fn_name in ("update_client", "delete_client"):
        fn = src.split("async def " + fn_name + "(")[1].split(chr(10) + "@router")[0]
        assert "check_logged_in(request)" in fn, fn_name + " 要先擋匿名"
        assert "_client_write_guard(request, client)" in fn, fn_name + " 要按列帳本驗"
    upd = src.split("async def update_client(")[1].split(chr(10) + "@router")[0]
    assert 'exclude={"status", "entity"}' in upd, "更新不得換帳本"


def test_client_tier_counts_both_ledgers_via_core_rule():
    """統一之後分級＝客戶關係（兩本帳的案子都算，與清單「案數」欄同口徑）；
    門檻收在 core.crm_logic.client_tier（端點與遷移腳本共用一份）。"""
    from core.crm_logic import client_tier
    assert (client_tier(0), client_tier(1), client_tier(2), client_tier(30)) ==         ("潛在客戶", "新客戶", "舊客戶", "舊客戶")
    src = _read("routers/crm/_shared.py")
    fn = src.split("async def _auto_update_client_status(")[1].split(chr(10) + "async def ")[0]
    assert "not_mine(CrmProject.entity)" not in fn, "統一後不再排除私帳案"
    assert "client_tier(count)" in fn, "門檻要走 core 那份，別在端點再寫一套"
    assert '== "mine":' in fn.replace("'", '"'), "私帳客戶（若有）不套 CRM 分級"


def test_merge_tool_blocks_on_fuzzy_duplicates():
    """併的工具先擋模糊重名 —— 直接併會讓同一家公司在 CRM 出現兩筆。"""
    src = _read("scripts/merge_my_clients_to_crm.py")
    assert "_norm_client_name" in src and "sys.exit(1)" in src
    assert "整批中止" in src
    assert "crm_link_id=NULL" in src, "併完之後對應連結沒有意義，要清掉"


def test_client_crm_link_machinery_kept_for_safety():
    """連結機制留著（併完為空）：真有私帳專屬客戶時看得到也連得起來。
    只有 mine 列能設、目標必須是 parent；名稱建議唯一候選才給。"""
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
    fn = src.split("async def set_client_crm_link(")[1].split(chr(10) + "@router")[0]
    assert "_client_write_guard(request, client)" in fn
    assert "只有私帳客戶能設定" in fn and "必須是 CRM（母公司）客戶" in fn


def test_client_manager_subview_shows_master_usage():
    js = _read("frontend/tabs/finance/subviews/clients.js")
    assert "crmFetch('/clients-mine-links')" in js
    assert "d.shared.map" in js, "主表＝我用到的客戶（都在 CRM 主檔）"
    assert "n_parent" in js, "要看得出哪些客戶也有公司案"
    assert "d.mine.length ?" in js, "私帳專屬客戶那區只在真的有的時候才畫"
    html = _read("frontend/tabs/finance/finance.html")
    btn = [ln for ln in html.splitlines() if 'data-subview="clients"' in ln]
    assert len(btn) == 1 and "fin-nav-mine-only" in btn[0]


def test_private_picker_uses_master_and_creates_crm_client():
    js = _read("frontend/tabs/finance/subviews/projects.js")
    assert "crmFetch('/clients?entity=mine')" in js       # 統一後這就是整份主檔
    seg = js.split("crmFetch('/clients', {")[1][:260]
    assert "entity: 'mine'" not in seg, "快速建客戶要進 CRM 主檔（別再建私帳專屬）"
    assert "short_name: g('fpc-newclient')" in seg
