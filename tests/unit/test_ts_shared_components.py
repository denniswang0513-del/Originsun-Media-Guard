# -*- coding: utf-8 -*-
"""工時的共用元件只有 js/shared/ 那一份（docs/JOURNAL_WORKLOG_PLAN.md §11–§12、BUILD_SPEC §3.1）。

工作追蹤分頁（tabs/timesheets/timesheets.js）與員工工作台（/my.html）都 import：
- ts-sheet.js：Sheet 式格子（列 html／列號／格線／藍框／鍵盤走列／起訖算小時／工作階段跟著分類／逐列自動存）
- ts-projects.js：專案表（burn）＋專案檔案
- stage-editor.js：工作階段設定

🔴 失敗模式＝第二份：以前 my.html 自己畫一套「新增工時」列、tab 自己畫一套格子，欄位一改兩邊就漂
（工作階段欄就是這種會漂的東西）。這裡釘：列 html 的標記只在共用模組出現一次。
"""
import pathlib
import re

from tests.unit._srcscan import js_code_only, my_page_src, repo_src

ROOT = pathlib.Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
TAB = "frontend/tabs/timesheets/timesheets.js"
SHEET = "frontend/js/shared/ts-sheet.js"
PROJ = "frontend/js/shared/ts-projects.js"
STAGE = "frontend/js/shared/stage-editor.js"


def _frontend_sources():
    """frontend 底下所有 .js／.html（示範頁 demo/ 與 node_modules 不算）。"""
    for p in FRONTEND.rglob("*"):
        if p.suffix not in (".js", ".html"):
            continue
        rel = p.relative_to(FRONTEND).as_posix()
        if rel.startswith(("demo/", "node_modules/", "dist/")):
            continue
        yield rel, p.read_text(encoding="utf-8", errors="replace")


def test_both_hosts_import_the_shared_sheet_and_projects_modules():
    tab = js_code_only(repo_src(TAB))
    my = my_page_src()
    assert "from '../../js/shared/ts-sheet.js'" in tab and "from '../../js/shared/ts-projects.js'" in tab
    # 工作階段設定 2026-09-12 起由共用的 ts-zone 開（兩個宿主都掛它），tab 不再自己 import
    assert 'from "/js/shared/stage-editor.js"' in js_code_only(repo_src("frontend/js/shared/ts-zone/index.js"))
    # 員工頁那邊 2026-09-12 起視圖本身就是 ES module（js/shared/ts-zone/），直接 import 共用元件
    assert 'from "/js/shared/ts-sheet.js"' in my and 'from "/js/shared/ts-projects.js"' in my
    assert 'from "/js/shared/stage-editor.js"' in my
    # 兩邊都是真的拿來畫，不是 import 了放著（tab 剩設定頁的快速補登 grid 與專案檔案頁；burn 表在 ts-zone）
    assert "rowBody(" in tab and "projectFileHtml(" in tab
    zl = js_code_only(repo_src("frontend/js/shared/ts-zone/log.js"))
    assert "renderSheet(" in zl and "wireAutosave(" in zl
    assert "burnTbodyHtml(" in js_code_only(repo_src("frontend/js/shared/ts-zone/find.js"))
    assert "renderSheet(sheet," in my and "wireAutosave(sheet," in my and "projectFileHtml(d," in my and "burnTbodyHtml(" in my


def test_the_sheet_row_html_lives_in_exactly_one_file():
    """列的 html（<tr class="ts-mine-row"…> ＋ 列號格）只在 ts-sheet.js。"""
    where_row = sorted(rel for rel, src in _frontend_sources() if 'class="ts-mine-row"' in src)
    where_num = sorted(rel for rel, src in _frontend_sources() if 'class="ts-sheet-num"' in src)
    assert where_row == ["js/shared/ts-sheet.js"], where_row
    assert where_num == ["js/shared/ts-sheet.js"], where_num
    sheet = repo_src(SHEET)
    assert sheet.count('class="ts-mine-row"') == 1, "列 html 在共用模組裡也只能有一份"
    # 樣式（格線／列號／藍框）跟著元件走，tab 的 html 不再放第二份
    assert "counter-reset:sheetrow" in sheet.replace(" ", "") and "focus-within" in sheet
    tab_html = repo_src("frontend/tabs/timesheets/timesheets.html")
    assert "counter-reset: sheetrow" not in tab_html and "ts-sheet-num {" not in tab_html


def test_the_sheet_has_the_stage_column_after_type_and_stages_follow_the_category():
    sheet = js_code_only(repo_src(SHEET))
    m = re.search(r"export const SHEET_COLS = \[(.*?)\];", sheet, re.S)
    assert m
    keys = re.findall(r"\['(\w+)',", m.group(1))
    # 2026-09-06 owner：計畫 h 整欄拿掉、實際 h 改名時數 h（planned_hours 後端留著，格子不送）
    # 2026-09-07 owner：時數搬到起訖左邊（起訖只是幫忙算時數的工具）
    assert keys == ["project", "type", "stage", "note", "remark", "hours", "t0", "t1", "state"], keys
    # 分類變了 → 階段下拉換清單；原階段不在裡面就清空並提示
    assert "_syncStage(" in sheet and "階段已清空" in sheet
    assert 'data-f="stage"' in sheet and "stagesFor(" in sheet
    # 送出的 body 帶 stage_id（空字串＝清空）與 bulletin_id（從待辦帶入的列）
    assert "body.stage_id = v('stage') || ''" in sheet and "body.bulletin_id = tr.dataset.bulletin" in sheet
    # 總表改列／代填的列沒有階段欄 → 不送 stage_id（不然會把人家的階段洗掉）
    assert "if (tr.querySelector('[data-f=\"stage\"]')) body.stage_id" in sheet
    # 五空列、最後一列有內容自動再長五列
    assert "BLANK_ROWS = 5" in sheet and ".repeat(BLANK_ROWS)" in sheet


def test_the_burn_table_and_project_file_live_in_ts_projects_only():
    """專案表的欄頭（已投入／預算／剩餘／消耗率／列數／最後填報）與專案檔案只在 ts-projects.js。"""
    hits = sorted(rel for rel, src in _frontend_sources() if "sortableTh('used', '已投入(h)'" in src)
    assert hits == ["js/shared/ts-projects.js"], hits
    file_hits = sorted(rel for rel, src in _frontend_sources() if "類似專案（自動推薦，人再挑）" in src)
    assert file_hits == ["js/shared/ts-projects.js"], file_hits
    proj = js_code_only(repo_src(PROJ))
    for name in ("export function burnTbodyHtml(", "export function projectFileHtml(", "export function createBurnSorter(",
                 "export function dayLogByMonth(", "export function hoursLabel(", "export const projLink"):
        assert name in proj, name
    # 「分類 · 階段」（owner §12）
    assert "i.stage_name" in proj
    # tab 不再自己畫 burn 表身／專案檔案
    tab = js_code_only(repo_src(TAB))
    assert "_burnTypeSelect" not in tab and "_dayLogByMonth" not in tab and "function _renderProject(d, modal = false) {\n    return projectFileHtml(" in tab


def test_stage_editor_is_one_module_opened_from_both_hosts():
    st = js_code_only(repo_src(STAGE))
    assert "export async function openStageEditor(" in st
    assert "'/api/v1/crm/work-stages/nodes'" in st
    for verb in ("method: 'POST'", "method: 'PUT'"):
        assert verb in st, verb
    assert "const on = hit.s.active === false; await put(hit.s.id, { active: on })" in st, "停用：切 active"
    # 2026-09-07 起 DELETE 只給「還沒有人用過」的階段（按鈕以 used===0 為閘；後端對有人用的仍改成停用）
    assert st.count("method: 'DELETE'") == 1 and "act === 'del'" in st and "s.used === 0" in st
    # 兩個宿主都掛共用的 ts-zone，工作階段設定那顆在 ts-zone/index.js 開、onSaved 把新清單餵回格子
    zone = js_code_only(repo_src("frontend/js/shared/ts-zone/index.js"))
    assert 'act === "stages"' in zone and "openStageEditor(" in zone and "setStages(sheet, map)" in zone and "onSaved: (map) =>" in zone
    assert 'data-z1="stages"' in my_page_src()
    assert "TSZ.mountZone({" in repo_src(TAB)


def test_shared_modules_do_not_import_tab_page_logic():
    """js/shared/ 只能往下依賴（dom／project-pop／svg-charts／crm-utils 那種共用庫），不能 import 頁籤邏輯。"""
    for rel in (SHEET, PROJ, STAGE):
        for m in re.finditer(r"from\s+'([^']+)'", repo_src(rel)):
            target = m.group(1)
            assert "tabs/" not in target or target.endswith("crm/crm-utils.js"), (rel, target)


def test_project_file_has_a_burn_rate_chip_that_falls_back_to_the_suggested_budget():
    """owner 2026-09-13「新增消耗率，讓同事知道這個專案是否已經超支」：預算沒設就照建議預算（拿得到的人），
    超過 100% 寫「超支 +N h」；都沒有就寫「還沒設預算」，不假裝算得出。"""
    from core.hr_logic import burn_rate
    assert burn_rate(22.8, None, 32) == {"base": "suggested", "base_hours": 32, "remaining": 9.2, "pct": 71.2}
    assert burn_rate(40, 32, 50) == {"base": "budget", "base_hours": 32, "remaining": -8, "pct": 125.0}
    assert burn_rate(40, None, None) == {"base": "", "base_hours": None, "remaining": None, "pct": None}
    from tests.unit._srcscan import js_code_only, js_func_body, repo_src
    js = repo_src("frontend/js/shared/ts-projects.js")
    chip = js_code_only(js_func_body(js, "export function burnChip(d) {"))
    assert "超支 +${Math.abs(b.remaining)} h" in chip and "還沒設預算" in chip
    assert 'title="已用 ${d.total} h ÷ ${b.base_hours} h"' in chip     # 基準是後端挑的，前端不再分「照預算／照建議」
    assert "${burnChip(d)}" in js_func_body(js, "export function projectFileHtml(d, opts = {}) {")
    api = repo_src("routers/timesheets/projects.py")
    assert '"burn": burn_rate(m["total"], budget, suggested),' in api
    assert '    suggested = _suggested_for(proj)' + chr(10) in api   # 公式預期製作時數人人拿得到（owner：直接用公式算的）
    chip = js_code_only(js_func_body(js, "export function budgetChip(d, editable) {"))
    assert "'預算 h（手動）' : (b.base === 'suggested' ? '預期製作時數（公式）'" in chip
    assert 'data-ts-action="budget"' in chip and 'data-ts-action="budget"' not in js_func_body(js, "export function projectFileHtml(d, opts = {}) {")


def test_burn_list_uses_the_same_budget_base_as_the_project_file():
    """owner 2026-09-14「有一些數字沒有更新」：清單的預算／剩餘／消耗率跟專案檔案同一把尺（手動 > 公式 > 未設），
    公式那格標「公式」；base／base_hours 是時數、進 SUMMARY_PUBLIC_KEYS（同事也看得到），suggested_hours 仍抹。"""
    from tests.unit._srcscan import js_code_only, js_func_body, repo_src
    from routers.timesheets._shared import SUMMARY_PUBLIC_KEYS
    assert "base" in SUMMARY_PUBLIC_KEYS and "base_hours" in SUMMARY_PUBLIC_KEYS and "suggested_hours" not in SUMMARY_PUBLIC_KEYS
    assert '**burn_rate(total, budget, suggested),' in repo_src("services/timesheet_lookup.py")
    cell = js_code_only(js_func_body(repo_src("frontend/js/shared/ts-projects.js"), "function _budgetCell(p, editable) {"))
    assert "const formula = p.base_hours ?? p.suggested_hours;" in cell and "公式" in cell and "未設" in cell
