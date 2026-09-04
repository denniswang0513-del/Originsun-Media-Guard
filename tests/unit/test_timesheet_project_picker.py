# -*- coding: utf-8 -*-
"""打字浮層分兩段（owner 2026-09-03 工作日誌專案「進行中／已結案」、2026-09-04 零用金專案同規則＋項目「常用／其他」）。

一份元件：frontend/js/shared/project-pop.js（原生 datalist／select 分不了組）。分組旗標由後端給：
專案 closed＝core.project_flow.is_closed（結案／歸檔／未成案；工作日誌 project_options 與零用金 /petty/options 同一條），
項目 common＝core.crm_logic.rank_items（過去用量 ≥3 次、最多 10 個）。
浮層開著時 ↓↑ 歸浮層（capture），關著時才輪到宿主（工作日誌的換列）；選了記 data-pid，存檔先認 id。
"""
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src

POP = "frontend/js/shared/project-pop.js"


def test_closed_flag_comes_from_one_backend_rule():
    from core.project_flow import CLOSED_STATUSES, LOST, is_closed
    assert is_closed("結案") and is_closed("歸檔") and is_closed(LOST) and not is_closed("製作") and not is_closed("")
    assert set(CLOSED_STATUSES) == {"結案", "歸檔", LOST}
    ts = code_only(func_body(repo_src("services/timesheet_manual.py"), "async def project_options("))
    assert '"closed": is_closed(st)' in ts and '"closed": False' in ts
    petty = code_only(func_body(repo_src("routers/crm/petty.py"), "async def petty_options("))
    assert '"closed": is_closed(p.status)' in petty
    assert "rank_items(" in petty and "CrmProjectExpense.item" in petty, "項目用量從過去雜支列算"


def test_rank_items_rule():
    from core.crm_logic import rank_items
    items = ["交通", "餐費", "器材", "郵資", "其他"]
    out = rank_items(items, {"餐費": 40, "交通": 12, "郵資": 3, "器材": 2})
    assert [r["name"] for r in out if r["common"]] == ["餐費", "交通", "郵資"]       # ≥3 次、依次數
    assert [r["name"] for r in out if not r["common"]] == ["器材", "其他"]         # 其他照原順序
    assert [r["name"] for r in rank_items(items, {}, top=10)] == items and not any(r["common"] for r in rank_items(items, {}))
    assert len([r for r in rank_items([f"i{n}" for n in range(20)], {f"i{n}": 5 for n in range(20)}) if r["common"]]) == 10


def test_shared_popover_contract():
    js = js_code_only(repo_src(POP))
    assert "export function attachProjectPop(root, cfg = {})" in js and "export function closeProjectPop()" in js
    assert "groups: cfg.groups || ['進行中', '已結案']" in js
    assert "p.closed" in js, "分組只看後端旗標，不認狀態字"
    assert "root.addEventListener('keydown', _keydown, true);" in js, "capture：先於宿主的 keydown"
    assert "ev.stopPropagation();" in code_only(func_body(js, "function _keydown(ev)"))
    pick = code_only(func_body(js, "function _pick(p)"))
    assert "input.dataset.pid = p.id || ''" in pick and "ev._fromPick = true" in pick
    assert "delete inp.dataset.pid" in js, "手打改了字要清掉 id"
    assert "root.dataset[flag]" in js, "同一個 root 依欄位各掛一次（重畫靠委派）"


def test_timesheets_and_petty_use_the_shared_popover_not_datalist():
    ts = js_code_only(repo_src("frontend/tabs/timesheets/timesheets.js"))
    assert "from '../../js/shared/project-pop.js'" in ts and "attachProjectPop(_content," in ts
    init = js_func_body(ts, "export async function initTimesheetsTab() {")
    assert init.index("attachProjectPop(_content,") < init.index("await refresh();"), "浮層要先掛（格子畫出來之前）"
    # 2026-09-05：格子（含 ↓↑ 走列）抽到 js/shared/ts-sheet.js；浮層在 root 用 capture、格子的 keydown 不用 capture → 開著時先歸浮層
    sheet = js_code_only(repo_src("frontend/js/shared/ts-sheet.js"))
    assert "host.addEventListener('keydown', (ev) => _keydown(ev, host));" in sheet
    assert "root.addEventListener('keydown', _keydown, true);" in js_code_only(repo_src("frontend/js/shared/project-pop.js"))
    assert "<datalist" not in ts and 'list="ts-proj-list"' not in ts and "ts-proj-pop" not in ts
    assert ts.count("data-proj-pick") + sheet.count("data-proj-pick") >= 3
    assert "el.dataset.pid" in code_only(func_body(sheet, "export function projectFromInput(text, el = null, projects = [])"))
    assert "projectFromInput(v('project'), tr.querySelector" in sheet

    pt = js_code_only(repo_src("frontend/tabs/petty/petty-view.js"))
    assert 'from "../../js/shared/project-pop.js"' in pt
    assert "<datalist" not in pt and "pc-proj-dl" not in pt and "f-proj-dl" not in pt and "ITEM_OPTS" not in pt
    assert pt.count("data-proj-pick autocomplete") == 3, "專案：我的請款表單、總表每列、新增列"
    assert pt.count("data-item-pick autocomplete") == 3, "項目：我的請款表單、總表每列、新增列"
    assert pt.count("attachProjectPop(host,") == 4 and pt.count("groups: ITEM_GROUPS") == 2 and "closed: !r.common" in pt
    assert "ITEM_SET.has(typed)" in pt, "項目打錯字要擋"


def test_no_leftover_popover_css_in_timesheets_html():
    assert ".ts-proj-pop" not in repo_src("frontend/tabs/timesheets/timesheets.html")
