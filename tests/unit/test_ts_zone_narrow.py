# -*- coding: utf-8 -*-
"""ts-zone 的窄螢幕排版（docs/WORKSPACE_RWD_PLAN.md 第一批，2026-09-17）。

規矩：資料與算式一份、排版兩種——表格不刪只藏，卡片與面板寫回同一列、走同一條自動存；
新邏輯只住在新檔 narrow.js（不動 ctx.js／index.js 的 export；Cloudflare 4 小時快取）；
列 html 與列號格仍只在 ts-sheet.js；沒有 emoji；換天只重畫不重抓。
"""
import re

from tests.unit._srcscan import js_code_only, js_func_body, repo_src

NARROW = "frontend/js/shared/ts-zone/narrow.js"
_EMOJI = re.compile("[\U0001F300-\U0001FAFF]|[✓✔✗]")


def test_narrow_module_is_new_and_self_contained():
    src = repo_src(NARROW)
    code = js_code_only(src)
    for name in ("export function isNarrow(", "export function mountLogCards(", "export function weekNarrowHtml(", "export function ensureNarrowCss("):
        assert name in code, name
    assert '"(max-width: 640px)"' in code and "NARROW_CONTAINER_PX = 560" in code, "視窗 640 以下或容器 560 以下"
    # 只從 ts-sheet.js／dom.js 拿既有 export，不新增 ctx.js／index.js 的 export
    assert 'from "/js/shared/ts-sheet.js"' in code and 'from "/js/shared/dom.js"' in code
    assert "from \"./ctx.js\"" not in code and "from \"./index.js\"" not in code
    assert 'class="ts-mine-row"' not in src and 'class="ts-sheet-num"' not in src, "列 html 只在 ts-sheet.js"
    assert not _EMOJI.search(code), "程式碼（含 html 字串）沒有 emoji；註解裡的紅點是 repo 慣例，不算"
    ctx = repo_src("frontend/js/shared/ts-zone/ctx.js")
    assert "narrow" not in ctx.lower() or "isNarrow" not in ctx, "ctx.js 不動"


def test_log_cards_mirror_the_same_rows_and_save_through_the_sheet():
    code = js_code_only(repo_src(NARROW))
    wb = js_func_body(code, "function writeBack(sheetHost, tr, panel) {")
    assert "saveRowNow(sheetHost, tr)" in wb and 'set("type", g("type").value, "change")' in wb, "寫回同一列、觸發同一套事件、走同一條自動存"
    assert "dispatchEvent(new Event(ev, { bubbles: true }))" in wb
    mount = js_func_body(code, "export function mountLogCards(sheetHost, opts = {}) {")
    assert 'sheetHost.classList.add("tsn-on")' in mount and "new MutationObserver(" in mount, "表格藏起來、列變了卡片跟著重畫"
    assert "[data-ts-action=\"row-remove\"]" in mount, "刪列走原本那條（有 id 先問再 DELETE）"
    assert "appendBlankRows(sheetHost, 1)" in mount
    log = js_code_only(repo_src("frontend/js/shared/ts-zone/log.js"))
    assert 'from "./narrow.js"' in log and "if (isNarrow(host)) mountLogCards(sheet," in log
    assert "renderSheet(sheet," in log and "wireAutosave(sheet," in log, "表格照舊掛，不是換掉"


def test_team_week_narrow_is_one_day_per_page_and_switching_day_does_not_refetch():
    tw = repo_src("frontend/js/shared/ts-zone/team-week.js")
    code = js_code_only(tw)
    assert 'from "./narrow.js"' in code
    load = js_func_body(code, "export async function loadTeamWeek() {")
    assert "s.weekCache = { d: dRes, ms, nConf }" in load and "_renderWeek()" in load
    assert "[data-wk-day]" in load and "data-z1" not in load.split("[data-wk-day]")[1][:200], "日鈕是 data-wk-day，不經 index.js 的委派"
    render = js_func_body(code, "function _renderWeek() {")
    assert "const narrow = isNarrow(host)" in render and "weekNarrowHtml({ cols, names, day: wkDay" in render
    assert "cellHtml: cell" in render, "每一格的 html 跟表格同一份"
    assert "names.length && !narrow ? `<div style=\"overflow-x:auto;\"><table class=\"week\">" in render, "寬螢幕表格照舊"
    assert not _EMOJI.search(code)


def test_workspace_mobile_css_touch_targets():
    html = repo_src("frontend/my.html")
    blk = html[html.index("@media (max-width: 640px) {\n    main { padding-top: 92px; }"):]
    blk = blk[:blk.index("}\n</style>")]
    assert ".zone1 .btn { min-height: 44px; }" in blk and ".view-btn { padding: 10px 14px; min-height: 44px; }" in blk
    assert not _EMOJI.search(blk)


def test_batch2_plan_find_remind_narrow_branches():
    """第二批：我的一週一天一列（今天展開、點標題展開）、專案查詢一案一卡＋篩選收成一顆＋再載、要補填一行摘要。
    都是窄螢幕分支；寬螢幕的板／表／那條一個字不變。"""
    plan = js_code_only(repo_src("frontend/js/shared/ts-zone/plan.js"))
    assert 'from "./narrow.js"' in plan and 'board.classList.add("tsn-plan")' in plan
    assert 'if (d === today || s.planOpen.has(d)) col.classList.add("open")' in plan, "今天與點開過的展開"
    assert "if (!dh || e.target.closest(\"button\")) return;" in plan, "點標題才展開，鈕不算"
    find = js_code_only(repo_src("frontend/js/shared/ts-zone/find.js"))
    assert 'from "./narrow.js"' in find and 'det.className = "tsn-filters"' in find and "bar.replaceWith(det); det.appendChild(bar);" in find
    assert 'classList.add("tsn-on-find")' in find and "findCardsHtml(rows, z.s.findPage || 1)" in find and "[data-tsn-more]" in find
    assert "s.findPage = 1; _redrawFindBody();" in find, "改篩選回第一頁"
    for i in ("z1-find-q", "z1-f-status", "z1-f-type", "z1-f-pct", "z1-f-from", "z1-f-to"):
        assert f'id="{i}"' in find, "篩選欄位的 id 不變（收進 details 而已）"
    narrow = js_code_only(repo_src(NARROW))
    cards = js_func_body(narrow, "export function findCardsHtml(rows, page = 1) {")
    assert 'data-ts-action="open-project"' in cards and "FIND_PAGE" in cards, "點卡走原本的 open-project 委派；一次 30 案"
    assert "export const FIND_PAGE = 30" in narrow
    rm = repo_src("frontend/js/shared/ts-zone/remind.js")
    rmc = js_code_only(rm)
    assert "remindNarrowHtml(r) : remindHtml(r)" in rmc
    nb = js_func_body(rmc, "export function remindNarrowHtml(r) {")
    assert "const full = remindHtml(r);" in nb and 'if (!full) return "";' in nb, "完整那條還是同一份；填完一樣消失"
    assert 'data-z1="day-goto"' in nb and "先補" in nb
    assert not _EMOJI.search(rmc) and not _EMOJI.search(plan) and not _EMOJI.search(find)
