# -*- coding: utf-8 -*-
"""工時 Sheet 匯入／同步的對映規則（docs/TIMESHEET_IMPORT_PLAN.md §2、§4）。

規則寫成純函式（core/hr_logic），五個入口（ingest／remap／budgets／手填／dry-run）
共用一份查表（services/timesheet_lookup）—— 各寫一份的話「撞案要不要猜」就會有
兩個答案。這裡釘的是**規則**，不是實作的排版。
"""
import re

from core.hr_logic import (INTERNAL_BUCKETS, ProjectLookup, group_by_name,
                           remap_target, resolve_project, sheet_project_client,
                           sheet_project_key, split_sheet_name, suggest_projects,
                           unique_hit)
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src

PROJECTS = [
    ("a", "國民法官劇情短片", "三立"),
    ("b", "媒體顧問 202608", "典藏藝術家庭股份有限公司"),   # 私帳同名兩案、客戶不同
    ("c", "媒體顧問 202608", "工安協會"),
    ("d", "課程影片製作", "政大資訊系"),                    # 同名但客戶不同 → 前綴能解
    ("e", "課程影片製作", "政大AI學程"),
    ("f", "台灣火箭序曲", "先決影像"),
    ("g", "年度影片", "典藏藝術家庭股份有限公司"),         # 兩案客戶都以「典藏」開頭
    ("h", "年度影片", "典藏藝術基金會"),
]


def _lk(project_map=None):
    return ProjectLookup.build(project_map or {}, PROJECTS)


# ── 切名字 ──

def test_the_split_is_one_rule_used_by_both_halves():
    """「客戶_案名」的前綴是 Sheet 為了下拉分組加的 —— 只切第一個底線（案名自己可能
    含底線），空白收成一格。client／key 兩支只是同一份切法的兩半。"""
    assert split_sheet_name("三立電視台_國民法官劇情短片") == ("三立電視台", "國民法官劇情短片")
    assert split_sheet_name("政大AI中心_課程影片製作_陳昭伶") == ("政大AI中心", "課程影片製作_陳昭伶")
    assert sheet_project_key("  典藏藝術家庭_媒體顧問   202608 ") == "媒體顧問 202608"
    assert split_sheet_name("行政庶務") == ("", "行政庶務")
    assert sheet_project_client("行政庶務") == ""


# ── 判定順序 ──

def test_resolution_order_map_bucket_exact_key():
    lk = _lk()
    # 對映表永遠優先 —— 就算規則能對到別案
    assert resolve_project("三立電視台_國民法官劇情短片",
                           _lk({"三立電視台_國民法官劇情短片": "z"})) == ("z", "map")
    for b in INTERNAL_BUCKETS:
        assert resolve_project(b, lk) == (None, "bucket")
    assert resolve_project("台灣火箭序曲", lk) == ("f", "exact")
    assert resolve_project("先決影像_台灣火箭序曲", lk) == ("f", "key")


def test_a_collision_is_never_guessed():
    """🔴 私帳有 28 個名字各對到兩案。撞到就回 ambiguous，留給 owner 指定 ——
    絕不 fuzzy 自動合併（同 import_my_projects 的鐵則）。"""
    lk = _lk()
    assert resolve_project("媒體顧問 202608", lk) == (None, "ambiguous")      # 全名撞
    assert resolve_project("某某_課程影片製作", lk) == (None, "ambiguous")   # 前綴對不到任何客戶


def test_the_client_prefix_breaks_a_tie_only_when_it_narrows_to_one():
    """Sheet 前綴是客戶**簡稱**、clients.short_name 常是全名 —— 認「以前綴開頭」，
    但仍要唯一：兩案客戶都以它開頭就還是撞案。這條把生產 dry-run 的 25 個撞案
    解到剩 1。"""
    lk = _lk()
    assert resolve_project("政大資訊系_課程影片製作", lk) == ("d", "key+client")
    assert resolve_project("典藏藝術家庭_媒體顧問 202608", lk) == ("b", "key+client")
    assert resolve_project("典藏_年度影片", lk) == (None, "ambiguous")


def test_unknown_and_empty():
    lk = _lk()
    assert resolve_project("源日後期_作品集更新", lk) == (None, "none")
    assert resolve_project("", lk) == (None, "empty")


def test_lookup_keeps_collisions_visible_and_exposes_candidates():
    """同名不合併 —— 撞案要在 resolve 被看見，不是在建表時被最後一個蓋掉。
    候選由 lookup 自帶案名與客戶，讀路徑不必再多打一次 DB。"""
    lk = _lk()
    assert len(lk.by_name["媒體顧問 202608"]) == 2
    assert {c[0] for c in lk.candidates("典藏藝術家庭_媒體顧問 202608")} == {"b", "c"}
    assert unique_hit([]) == (None, "none")
    assert unique_hit([("x",)]) == (("x",), "exact")
    assert unique_hit([("x",), ("y",)]) == (None, "ambiguous")
    assert group_by_name([("1", "王"), ("2", "王"), ("3", "")]) == {"王": [("1", "王"), ("2", "王")]}


# ── remap 的三條規則（真值表）──

def test_remap_target_truth_table():
    """對映表覆蓋／自動只補空的／絕不清空。"""
    assert remap_target("map", "A", "B") == "B"          # owner 指定 B，本來自動對到 A → 改
    assert remap_target("map", "B", "B") is None         # 已經是 B → 不動
    assert remap_target("key", None, "A") == "A"         # 自動規則補空的
    assert remap_target("key", "A", "B") is None         # 已對好 → 自動規則不動它
    assert remap_target("ambiguous", "A", None) is None  # 撞案不會清掉既有的
    assert remap_target("none", "A", None) is None
    assert remap_target("none", None, None) is None


# ── 建議只給人看 ──

def test_suggestions_are_report_only_never_a_write_path():
    lk = ProjectLookup.build({}, [("x", "華南永昌E指沖", "華南"), ("y", "沆涸 剪輯", "大漁")])
    assert suggest_projects("華南銀行_華南永昌E指通", lk)[0][0] == "華南永昌E指沖"
    assert suggest_projects("大漁映畫_沆涸", lk)[0][0] == "沆涸 剪輯"
    assert suggest_projects("完全無關", lk) == []
    src = repo_src("routers/api_timesheets.py")
    for fn in ("async def ingest_rows(", "async def remap_timesheets(",
               "async def set_budgets(", "async def upsert_project_map(",
               "async def insert_manual_rows("):
        assert "suggest_projects" not in code_only(func_body(src, fn)), f"建議函式跑進寫入路徑了：{fn}"


# ── 端點與腳本守的規則（掃原始碼，釘規則不釘排版）──

def test_every_entry_point_shares_one_lookup_and_one_resolver():
    """ingest／remap／budgets／手填／summary／projects 都吃 services 那一份查表；
    名稱→專案的判定只有 resolve_project 一支（手填原本自己維護第二份全名精確對映）。"""
    src = code_only(repo_src("routers/api_timesheets.py"))
    for fn in ("async def ingest_rows(", "async def remap_timesheets(", "async def set_budgets(",
               "async def burn_summary(", "async def insert_manual_rows(", "async def timesheet_projects("):
        assert "load_project_lookup(session)" in func_body(src, fn), fn
    assert "name_to_id" not in src, "第二份名稱對映還在"
    # 唯一回給前端當清單的端點，可見性照私帳規矩（Lv3 不隱含 finance_mine）
    assert 'require_entity(request, "mine")' in func_body(src, "async def timesheet_projects("), \
        "私帳案清單沒套 mine scope"
    svc = code_only(repo_src("services/timesheet_lookup.py"))
    assert 'CrmProject.entity == "mine"' in svc, "自動對映只認私帳（owner 2026-09-02）"
    # remap 的判定走純函式，並且依名字聚合（不是 9,800 列逐列）
    rm = func_body(src, "async def remap_timesheets(")
    assert "remap_target(why, cur_pid, pid)" in rm
    assert ".group_by(Timesheet.project_name, Timesheet.project_id)" in rm


def test_ingest_only_accepts_the_two_sources_and_fills_staff_id():
    import routers.api_timesheets as m
    assert set(m._SOURCES) == {"sheet", "import"}
    fn = code_only(func_body(repo_src("routers/api_timesheets.py"), "async def ingest_rows("))
    assert "source=req.source" in fn
    assert "staff_id=sid" in fn
    # 人員同名兩人不猜，而且分開回報（不是壓成「找不到」）
    assert "staff_ambiguous" in fn
    # 逐列 budget 鏡射那條死路已經拿掉：預算只從 PUT /budgets 進
    assert "budget" not in fn
    assert "budget:" not in code_only(func_body(repo_src("core/schemas.py"), "class TimesheetRow("))


def test_the_import_script_formats_dates_like_apps_script_and_never_inits_db():
    """🔴 hash 用的是日期**字串**：xlsx 給 datetime、Apps Script 給 'yyyy/MM/dd'。
    兩條路格式不同，交接那幾天的列就會重複入庫（規劃 D9）。
    🔴 dry-run 不經 init_db：那支會 create_all 到目標庫，--prod 的「只讀」就不只讀了。"""
    import datetime as dt
    from scripts.import_timesheets import _date_str
    assert _date_str(dt.datetime(2026, 9, 1)) == "2026/09/01"
    assert _date_str(dt.date(2026, 1, 5)) == "2026/01/05"
    assert _date_str(" 2026/6/30 ") == "2026/6/30"
    src = code_only(repo_src("scripts/import_timesheets.py"))
    assert '"source": "import"' in src
    assert "init_db" not in src
    assert "routers.api_timesheets" not in src, "腳本為了查表拉進整個 router"
    assert "load_project_lookup" in src and "resolve_db_url" in src


def test_the_burn_board_tells_the_owner_why_a_name_is_unmatched():
    src = repo_src("routers/api_timesheets.py")
    fn = code_only(func_body(src, "async def burn_summary("))
    assert '"reason": why' in fn
    assert 'item["candidates"]' in fn and 'item["suggestions"]' in fn
    assert fn.count("resolve_project(") == 1, "同一個名字 resolve 了兩次"
    js = js_code_only(repo_src("frontend/tabs/timesheets/timesheets.js"))
    # UI：寫對映表再 remap，不自己改列（對映表是每小時同步也要吃的正本）
    assert "'/api/v1/timesheets/project_map'" in js and "'/api/v1/timesheets/remap'" in js
    body = js_func_body(js, "async function _mapProject(sheetName) {")
    assert "project_id =" not in body and ".project_id" not in body
    # 挑選視窗的清單來自工時自己的薄端點（同守衛、同查表、沒有錢），不是 /crm/projects
    assert "'/api/v1/timesheets/projects'" in body
    assert "/crm/projects" not in body
    assert 'data-ts-action="map"' in js


def test_the_sync_script_reads_the_two_input_tabs_not_the_desc_master():
    """🔴 總表是 ORDER BY 日期 DESC 的 QUERY，新列在最上面 ——「記到第幾列」的 marker
    在它上面不成立。腳本要讀兩個底部追加的輸入分頁、各自記 marker、列 7 起、A–E。"""
    gs = repo_src("docs/appsscript/timesheet_sync.gs")
    cfg = gs.split("var CONFIG")[1].split("};")[0]
    assert re.search(r"name:\s*'工作紀錄表',\s*startRow:\s*7", cfg)
    assert re.search(r"name:\s*'助理工作紀錄表',\s*startRow:\s*7", cfg)
    assert "總表" not in cfg, "CONFIG 又去讀總表了"
    for col, n in (("DATE", 1), ("STAFF", 2), ("PROJECT", 3), ("TASK", 4), ("HOURS", 5)):
        assert re.search(rf"{col}:\s*{n}\b", cfg), col
    assert "BUDGET" not in cfg, "預算欄在總表根本不存在"
    assert "markerKey_(cfg.name)" in gs                       # 每個分頁各自的 marker
    assert "lastRow < lastSynced" in gs and "throw new Error" in gs   # IMPORTRANGE 失連要 throw
    assert "function executeSetMarkerToEnd()" in gs           # 歷史列匯過 → marker 設到表尾
    assert "'yyyy/MM/dd'" in gs                               # 與匯入腳本同 hash
