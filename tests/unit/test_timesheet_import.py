# -*- coding: utf-8 -*-
"""工時 Sheet 匯入／同步的對映規則（docs/TIMESHEET_IMPORT_PLAN.md §2、§4）。

規則寫成純函式（core/hr_logic），四個入口（ingest／remap／budgets／dry-run）
共用一份 —— 各寫一份的話「撞案要不要猜」就會有兩個答案。
"""
from core.hr_logic import (INTERNAL_BUCKETS, project_lookup_tables,
                           resolve_project, sheet_project_client,
                           sheet_project_key, suggest_projects)
from tests.unit._srcscan import code_only, func_body, repo_src

PROJECTS = [
    ("a", "國民法官劇情短片", "三立"),
    ("b", "媒體顧問 202608", "典藏"),          # 私帳同名兩案、案碼不同
    ("c", "媒體顧問 202608", "典藏"),
    ("d", "課程影片製作", "政大資訊系"),        # 同名但客戶不同 → 前綴能解
    ("e", "課程影片製作", "政大AI學程"),
    ("f", "台灣火箭序曲", "先決影像"),
]


def _tables():
    return project_lookup_tables(PROJECTS)


def test_the_key_strips_the_client_prefix_only_once():
    """「客戶_案名」的前綴是 Sheet 為了下拉分組加的，不是案名 —— 只切第一個底線
    （案名自己可能含底線）。空白收成一格。"""
    assert sheet_project_key("三立電視台_國民法官劇情短片") == "國民法官劇情短片"
    assert sheet_project_key("政大AI中心_課程影片製作_陳昭伶") == "課程影片製作_陳昭伶"
    assert sheet_project_key("  典藏藝術家庭_媒體顧問   202608 ") == "媒體顧問 202608"
    assert sheet_project_key("行政庶務") == "行政庶務"
    assert sheet_project_client("三立電視台_國民法官劇情短片") == "三立電視台"
    assert sheet_project_client("行政庶務") == ""


def test_resolution_order_map_bucket_exact_key():
    by_name, by_key = _tables()
    # 對映表永遠優先 —— 就算規則能對到別案
    assert resolve_project("三立電視台_國民法官劇情短片", {"三立電視台_國民法官劇情短片": "z"},
                           by_name, by_key) == ("z", "map")
    # 內部桶不對映
    for b in INTERNAL_BUCKETS:
        assert resolve_project(b, {}, by_name, by_key) == (None, "bucket")
    # 全名精確
    assert resolve_project("台灣火箭序曲", {}, by_name, by_key) == ("f", "exact")
    # 去客戶前綴後唯一
    assert resolve_project("先決影像_台灣火箭序曲", {}, by_name, by_key) == ("f", "key")


def test_a_collision_is_never_guessed():
    """🔴 私帳有 28 個名字各對到兩案（媒體顧問月費系列）。撞到就回 ambiguous，
    留給 owner 指定 —— 絕不 fuzzy 自動合併（同 import_my_projects 的鐵則）。"""
    by_name, by_key = _tables()
    assert resolve_project("典藏藝術家庭_媒體顧問 202608", {}, by_name, by_key) == (None, "ambiguous")
    # 全名撞也一樣
    assert resolve_project("媒體顧問 202608", {}, by_name, by_key) == (None, "ambiguous")


def test_the_client_prefix_breaks_a_tie_when_it_matches_exactly_one():
    by_name, by_key = _tables()
    assert resolve_project("政大資訊系_課程影片製作", {}, by_name, by_key) == ("d", "key+client")
    assert resolve_project("政大AI學程_課程影片製作", {}, by_name, by_key) == ("e", "key+client")
    # 前綴對不到任何一案的客戶 → 仍是撞案
    assert resolve_project("某某_課程影片製作", {}, by_name, by_key) == (None, "ambiguous")


def test_the_client_prefix_is_a_short_name_so_startswith_counts():
    """Sheet 前綴是簡稱（「典藏藝術家庭」），clients.short_name 常是全名
    （「…股份有限公司」）—— 以前綴開頭算命中，但仍要**唯一**：兩案的客戶都以它
    開頭就還是撞案。這條把生產 dry-run 的 25 個媒體顧問撞案解掉大半。"""
    by_name, by_key = project_lookup_tables([
        ("b", "媒體顧問 202608", "典藏藝術家庭股份有限公司"),
        ("c", "媒體顧問 202608", "工安協會"),
        ("d", "年度影片", "典藏藝術家庭股份有限公司"),
        ("e", "年度影片", "典藏藝術基金會"),
    ])
    assert resolve_project("典藏藝術家庭_媒體顧問 202608", {}, by_name, by_key) == ("b", "key+client")
    assert resolve_project("典藏_年度影片", {}, by_name, by_key) == (None, "ambiguous")


def test_unknown_and_empty():
    by_name, by_key = _tables()
    assert resolve_project("源日後期_作品集更新", {}, by_name, by_key) == (None, "none")
    assert resolve_project("", {}, by_name, by_key) == (None, "empty")


def test_lookup_tables_keep_collisions_visible():
    """同名不合併 —— 撞案要在 resolve 被看見，不是在建表時被最後一個蓋掉。"""
    by_name, by_key = _tables()
    assert len(by_name["媒體顧問 202608"]) == 2
    assert len(by_key["課程影片製作"]) == 2


# ── 端點與腳本守的規則（掃原始碼）──

def test_ingest_uses_the_shared_resolver_and_only_two_sources():
    src = repo_src("routers/api_timesheets.py")
    fn = code_only(func_body(src, "async def ingest_rows("))
    assert "resolve_project(pname, pmap, by_name, by_key)" in fn
    assert "name_to_id" not in fn, "舊的全名精確對映還在"
    assert "source=req.source" in fn
    assert 'if req.source not in _SOURCES' in fn
    assert '_SOURCES = ("sheet", "import")' in code_only(src)
    # staff_id 回填：同名兩人不猜
    assert "staff_id=sid" in fn


def test_all_four_entry_points_share_one_lookup():
    """ingest／remap／budgets／dry-run 都吃 `_project_lookup`（私帳＋對映表）。"""
    src = code_only(repo_src("routers/api_timesheets.py"))
    for fn_name in ("async def ingest_rows(", "async def remap_timesheets(", "async def set_budgets("):
        assert "await _project_lookup(session)" in func_body(src, fn_name), fn_name
    lk = func_body(src, "async def _project_lookup(")
    assert 'CrmProject.entity == "mine"' in lk, "自動對映只認私帳（owner 2026-09-02）"
    script = code_only(repo_src("scripts/import_timesheets.py"))
    assert "_project_lookup" in script


def test_remap_lets_the_owner_decision_override_but_never_clears():
    fn = code_only(func_body(repo_src("routers/api_timesheets.py"), "async def remap_timesheets("))
    assert '(why == "map" and t.project_id != pid) or (pid and not t.project_id)' in fn


def test_the_import_script_formats_dates_like_apps_script():
    """🔴 hash 用的是日期**字串**：xlsx 給 datetime、Apps Script 給 'yyyy/MM/dd'。
    兩條路格式不同，交接那幾天的列就會重複入庫（規劃 D9）。"""
    import datetime as dt
    from scripts.import_timesheets import _date_str
    assert _date_str(dt.datetime(2026, 9, 1)) == "2026/09/01"
    assert _date_str(dt.date(2026, 1, 5)) == "2026/01/05"
    assert _date_str(" 2026/6/30 ") == "2026/6/30"        # 已是字串就原樣
    src = repo_src("scripts/import_timesheets.py")
    assert '"source": "import"' in src
    assert "--apply" in src and "dry-run" in src


def test_suggestions_are_report_only_never_a_write_path():
    """找不到的給相似建議（打錯字那種），但**只在報告**出現 —— 任何寫入路徑
    （ingest／remap／budgets）都不准碰 suggest_projects。"""
    _, by_key = project_lookup_tables([("x", "華南永昌E指沖", "華南"), ("y", "沆涸 剪輯", "大漁")])
    assert suggest_projects("華南銀行_華南永昌E指通", by_key)[0][0] == "華南永昌E指沖"
    assert suggest_projects("大漁映畫_沆涸", by_key)[0][0] == "沆涸 剪輯"
    assert suggest_projects("完全無關", by_key) == []
    # 讀路徑（burn 表要給 owner 看建議）可以用；三條**寫入**路徑不准碰
    src = repo_src("routers/api_timesheets.py")
    for fn in ("async def ingest_rows(", "async def remap_timesheets(",
               "async def set_budgets(", "async def upsert_project_map("):
        assert "suggest_projects" not in code_only(func_body(src, fn)), f"建議函式跑進寫入路徑了：{fn}"


def test_the_burn_board_tells_the_owner_why_a_name_is_unmatched():
    """未對映的每個名字都帶 reason；撞案附候選、找不到附相似建議 —— owner 在 tab
    上按「指定專案」就能決定，不用回頭翻報告。判定走同一支 resolver。"""
    src = repo_src("routers/api_timesheets.py")
    fn = code_only(func_body(src, "async def burn_summary("))
    assert "await _project_lookup(session)" in fn
    assert '"reason": why' in fn
    assert 'item["candidates"]' in fn and 'item["suggestions"]' in fn
    from tests.unit._srcscan import js_code_only
    js = js_code_only(repo_src("frontend/tabs/timesheets/timesheets.js"))
    # UI：寫對映表再 remap，不自己改列（對映表是每小時同步也要吃的正本）
    assert "'/api/v1/timesheets/project_map'" in js and "'/api/v1/timesheets/remap'" in js
    from tests.unit._srcscan import js_func_body
    body = js_func_body(js, "async function _mapProject(sheetName) {")
    assert "project_id =" not in body and ".project_id" not in body, "UI 自己改了列的 project_id"
    assert 'data-ts-action="map"' in js


def test_the_sync_script_reads_the_two_input_tabs_not_the_desc_master():
    """🔴 總表是 ORDER BY 日期 DESC 的 QUERY，新列在最上面 ——「記到第幾列」的 marker
    在它上面不成立。腳本要讀兩個底部追加的輸入分頁、各自記 marker、列 7 起、A–E。"""
    gs = repo_src("docs/appsscript/timesheet_sync.gs")
    assert "name: '工作紀錄表',     startRow: 7" in gs
    assert "name: '助理工作紀錄表', startRow: 7" in gs
    assert "總表（勿動）" not in gs.split("var CONFIG")[1].split("};")[0], "CONFIG 又去讀總表了"
    assert "COL: { DATE: 1, STAFF: 2, PROJECT: 3, TASK: 4, HOURS: 5 }" in gs
    assert "BUDGET" not in gs.split("var CONFIG")[1].split("};")[0], "預算欄在總表根本不存在"
    # 每個分頁各自的 marker；IMPORTRANGE 失連要 throw 不是靜靜略過
    assert "markerKey_(cfg.name)" in gs
    assert "lastRow < lastSynced" in gs and "throw new Error" in gs
    # 歷史列匯過 → 安裝時把 marker 設到表尾
    assert "function executeSetMarkerToEnd()" in gs
    # 日期格式與匯入腳本一致（hash 交接）
    assert "'yyyy/MM/dd'" in gs

