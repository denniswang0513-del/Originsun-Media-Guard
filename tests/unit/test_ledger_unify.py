# -*- coding: utf-8 -*-
"""兩本帳統一（owner 2026-09-05）：顯示名鏈 ＋ 佔位金額的排除。

規劃正本 docs/LEDGER_UNIFY_PLAN.md。三條 owner 拍板的規則：
  · 有連結的案，顯示層顯示母帳案名（`crm_projects.name` 不改寫）
  · 連多個母帳案（N:1）挑不出誰對 → 退回私帳原名，由 owner 自訂
  · 母帳沒金額時先填私帳的，**標成佔位**，母公司毛利／現金流要排除它
"""
from core.ledger_project import linked_display_name
from tests.unit._srcscan import repo_src


def test_display_name_chain():
    """自訂 → 連到的唯一母帳案 → 私帳原名。副標＝私帳原名（等於顯示名時不給）。"""
    # 沒連 → 就是自己
    assert linked_display_name("開村影片", []) == ("開村影片", "")
    assert linked_display_name("開村影片", None) == ("開村影片", "")
    # 1:1 → 母帳案名，副標留私帳原名
    assert linked_display_name("開村影片", ["蟾蜍山｜煥民新村 館所介紹"]) \
        == ("蟾蜍山｜煥民新村 館所介紹", "開村影片")
    # 兩邊同名 → 沒有副標可給（畫出來會是「A / A」）
    assert linked_display_name("2026 臺北城市形象片", ["2026 臺北城市形象片"]) \
        == ("2026 臺北城市形象片", "")
    # N:1 → 自動規則不猜（生產實例：總統創新獎頒獎影片連著兩個母帳案）
    assert linked_display_name(
        "總統創新獎頒獎影片",
        ["2026 產科會_第七屆總統創新獎", "2026 第七屆總統創新獎 獎盃製作"]) \
        == ("總統創新獎頒獎影片", "")
    # 自訂勝過一切，連 N:1 也解得掉
    assert linked_display_name("總統創新獎頒獎影片", ["A", "B"], "總統創新獎（含獎盃）") \
        == ("總統創新獎（含獎盃）", "總統創新獎頒獎影片")
    assert linked_display_name("開村影片", ["蟾蜍山"], "  ") == ("蟾蜍山", "開村影片")
    # 空母帳名不算數（母帳案沒名字時不要顯示成空白）
    assert linked_display_name("開村影片", ["", None]) == ("開村影片", "")


def test_name_column_is_never_rewritten():
    """🔴 `crm_projects.name` 是 Sheet 工時案名對映的查表鍵 —— 顯示名不准落回它。"""
    api = repo_src("routers/api_finance_projects.py")
    assert "p.display_name = " in api, "顯示名要寫進 display_name"
    assert "p.name = " not in api, "顯示名不能改寫 name（工時對映靠它查）"
    proj = repo_src("routers/crm/projects.py")
    # 補建母帳案是建**新的一列**（name=m.name），不是改私帳那列的名字
    assert "m.name = " not in proj


def test_display_chain_has_one_source():
    """顯示名只有一份規則 —— 前後端都不准各自再推一次。"""
    shared = repo_src("routers/crm/_shared.py")
    assert "linked_display_name(" in shared, "案名共同出口 project_names_map 要走這條"
    assert "async def mine_parent_names" in shared
    api = repo_src("routers/api_finance_projects.py")
    assert api.count("linked_display_name(") == 3, "清單／詳情／PUT 回傳三處"
    js = repo_src("frontend/tabs/finance/subviews/projects.js")
    # 前端只顯示與搜尋，不自己判斷「該用哪個名字」
    assert "parent_names[0]" not in js and "parent_names.length === 1" not in js


def test_search_covers_every_name():
    """顯示名換掉之後，用舊名還是要搜得到（否則功能＝東西不見了）。"""
    js = repo_src("frontend/tabs/finance/subviews/projects.js")
    assert "function _names(p)" in js
    assert "p.orig_name" in js and "p.parent_names" in js
    assert "_names(p).some(" in js, "搜尋要吃三個名字，不是只比顯示名"


def test_placeholder_contract_is_excluded_from_reports():
    """🔴 佔位金額（私帳帶過來、待確認）不得混進母公司毛利與現金流。

    那是「我拿到的那段」，收入低估、成本卻是公司全額 → 毛利變負的。
    """
    st = repo_src("services/finance_statements.py")
    assert "CrmProject.contract_amount_source.is_(None)" in st, \
        "母公司專案毛利要排除佔位金額"
    cf = repo_src("routers/api_cashflow.py")
    assert 'contract_amount_source", None) == "mine"' in cf, \
        "付款節點模板要擋佔位金額（照它排出來的節點會進現金流預測）"
    proj = repo_src("routers/crm/projects.py")
    assert 'contract_amount_source="mine" if amt else None' in proj, \
        "補建母帳案時要標記金額來源"


def test_link_endpoints_guard_mine_scope():
    """對應表五支都要私帳完整權限 —— 「私帳有哪些案」本身就是私帳資料。

    五支＝清單 ＋ 兩個方向各自的 連結／建立
    （parent-create／parent-link／mine-create／mine-link）。
    """
    proj = repo_src("routers/crm/projects.py")
    assert proj.count("await _mine_link_guard(request)") == 5
    assert 'require_entity(request, "mine", level="full")' in proj


def test_both_link_shapes_are_read():
    """🔴 連結有新舊兩種形狀，而且**要去重** —— 生產 12 組裡有 6 組兩種都寫了。"""
    shared = repo_src("routers/crm/_shared.py")
    assert "CrmProject.mine_link_id.in_(ids)" in shared, "新形狀"
    assert "CrmProject.source_project_id.isnot(None)" in shared, "舊形狀"
    assert "if (mid, src) not in pairs" in shared, "去重（不然 1:1 會被誤判成 N:1）"


def test_client_backfill_reuses_existing_crm_row():
    """母帳已有同代稱的客戶就連過去 —— `(entity, short_name)` 有唯一約束。"""
    cli = repo_src("routers/crm/clients.py")
    assert "async def create_crm_client_from_mine" in cli
    assert "Client.short_name == c.short_name" in cli
    proj = repo_src("routers/crm/projects.py")
    assert "async def _crm_client_for" in proj, "補建專案時客戶也要一起對齊"


def test_link_has_one_writer():
    """🔴 連結寫入只有 `_write_link` 一份 —— 兩個方向各寫一遍就會長出
    「一邊清了、另一邊沒清」的半連結（母帳側清了、私帳側 source_project_id 還指著）。"""
    proj = repo_src("routers/crm/projects.py")
    assert proj.count("def _write_link(") == 1
    # 直接指派 mine_link_id 的地方**只有 _write_link 自己**（解除一次、連結一次）。
    # 多出來的就是有人繞過了唯一寫入者 —— mirror-to-mine 兩條路一開始就是
    # 各寫一遍，於是「母帳側清了、私帳側 source_project_id 還指著」有機會發生。
    assert proj.count(".mine_link_id = ") == 2, "有人繞過 _write_link 直接寫連結欄"


def test_both_directions_exist():
    """對應表兩個方向都要有（owner 2026-09-05「增加一個切換鈕」）。"""
    proj = repo_src("routers/crm/projects.py")
    for path in ("/projects/{mine_id}/parent-create", "/projects/{mine_id}/parent-link",
                 "/projects/{parent_id}/mine-create", "/projects/{parent_id}/mine-link"):
        assert path in proj, path
    js = repo_src("frontend/tabs/finance/subviews/projlinks.js")
    assert "_p.dir = " in js and "_rowParent" in js
    assert "'母帳 → 私帳'" in js and "'私帳 → 母帳'" in js
    # 下拉浮層那段只留一份（兩個方向共用）
    assert js.count("function _pickCell(") == 1 and js.count("searchableSelect(sel") == 1
