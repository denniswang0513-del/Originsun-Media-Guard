# -*- coding: utf-8 -*-
"""同事回饋 2026-09-07（李宜庭）兩條：

1. 時數格要能打「+時數」往上加（2.5 後打 +1.1 → 3.6）。原本是 <input type=number>，瀏覽器本來就不讓打「+」和第二個「.」；
   改成 text＋ts-sheet.parseHours 算式，格子與手機表單同一份。
2. 上週的紀錄改不了：那批列是 Google Sheet 拉進來的（source=sheet／import），原規則「Sheet 列請改試算表」。
   現在本人可改：第一次改就 claim_sheet_row 轉手填列＋留 tombstone 指紋（拉取不插回）；刪、合併也留指紋。
"""
import subprocess

from tests.unit._srcscan import _REPO, code_only, func_body, js_code_only, repo_src


def test_hours_cell_is_text_with_expression_parser_shared_by_mobile():
    sheet = repo_src("frontend/js/shared/ts-sheet.js")
    # inputmode 2026-09-11 改成依裝置輸出（見下面那條測試），這裡釘的是 **type**：
    # type="number" 會擋掉「+」，算式就打不出來
    assert 'type="text"${NUM_IM.dec} data-f="hours"' in sheet
    assert 'type="number" data-f="hours"' not in sheet
    js = js_code_only(sheet)
    assert "export function parseHours(raw)" in js
    assert js.count("hours: parseHours(v('hours'))") == 2, "rowValues 與 rowBody 都要走算式"
    assert "const n = parseHours(hr.value); hr.value = n == null ? '' : n;" in js   # 離開格子就變算完的數字
    mob = js_code_only(repo_src("frontend/m/views/worklog.js"))
    assert "parseHours(F('hours').value)" in mob and 'id="wl-hours" type="text" inputmode="decimal"' in mob
    assert "parseHours, projectFromInput } from '../../js/shared/ts-sheet.js'" in mob


def test_numeric_inputmode_is_touch_only_so_the_ime_survives():
    """同事回饋 2026-09-11：打完「起／訖」回到「做了什麼」「備註」變成英文輸入法。

    Windows 的 Chrome 把 `inputmode` 轉成輸入法 InputScope，numeric/decimal ＝
    「只收數字」→ 微軟輸入法切英數；而中/英模式是**視窗層級**的狀態，離開那一格
    不會自己切回來。`inputmode` 的用途只是手機叫數字鍵盤，桌機加了零好處。

    所以：桌機不輸出、觸控才輸出。改回無條件輸出就會再犯一次，故釘住。
    """
    sheet = repo_src("frontend/js/shared/ts-sheet.js")
    js = js_code_only(sheet)
    # 列模板裡一個寫死的 inputmode 都不准有 —— 三個數字欄都要走同一個開關
    row = func_body(js, "export function rowHtml(")
    assert "inputmode=" not in row, "列模板不可寫死 inputmode，要走 NUM_IM"
    assert "NUM_IM.num" in row and "NUM_IM.dec" in row
    assert js.count("matchMedia('(pointer: coarse)')") == 1, "裝置判斷只有一份"
    # 中文欄位（做了什麼／備註／專案）本來就不該有 inputmode —— 有的話同樣會踢掉輸入法
    for f in ('data-f="note"', 'data-f="remark"'):
        cell = sheet[sheet.index(f) - 120:sheet.index(f) + 40]
        assert "inputmode" not in cell, f"{f} 不可以有 inputmode"
    # 手機版的時數欄維持 inputmode（那裡要的就是數字鍵盤，而且沒有實體鍵盤可切）
    mob = js_code_only(repo_src("frontend/m/views/worklog.js"))
    assert 'id="wl-hours" type="text" inputmode="decimal"' in mob


def test_parse_hours_truth_table_in_node():
    """真的用 node 跑 parseHours：算式、全形加號、單一數字、空、垃圾。"""
    js = js_code_only(repo_src("frontend/js/shared/ts-sheet.js"))
    fn = func_body(js, "export function parseHours(raw)").replace("export function", "function", 1)
    script = fn + """
const out = ['2.5+1.1', '+1.1', '3-0.5', '2.5＋1.1', '1.98', ' 0.33 ', '', 'abc', '1+2+0.25', '.5+.5'].map(parseHours);
console.log(JSON.stringify(out));"""
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, cwd=_REPO)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[3.6,1.1,2.5,3.6,1.98,0.33,null,null,3.25,1]"


def test_sheet_rows_are_editable_by_owner_and_claimed_with_tombstone():
    svc = code_only(repo_src("services/timesheet_self.py"))
    # 「Sheet 列被吃掉之前先留指紋」四處共用 tombstone_if_sheet（手填列回 False、不留）
    helper = func_body(svc, "async def tombstone_if_sheet(")
    assert 'if getattr(row, "source", "") == "manual":' in helper and "add_tombstone(session, row.row_hash" in helper
    assert "work_date=row.work_date" in helper and "hours=row.hours" in helper, "指紋欄位要齊，漏一欄那列下次拉取又長回來"
    claim = func_body(svc, "async def claim_sheet_row(")
    assert "tombstone_if_sheet(session, r, who)" in claim
    assert 'r.source = "manual"' in claim and 'r.status = "draft"' in claim and 'r.row_hash = "manual_" + uuid.uuid4().hex' in claim
    assert "await claim_sheet_row(session, r, ident" in func_body(svc, "async def update_row(")
    assert "tombstone_if_sheet(session, r, ident" in func_body(svc, "async def delete_row(")
    merge = func_body(svc, "async def merge_day(")
    assert "await claim_sheet_row(session, kept, who)" in merge and "tombstone_if_sheet(session, a, who)" in merge
    rule = func_body(code_only(repo_src("core/hr_logic.py")), "def can_edit_timesheet(")
    assert '"not_manual"' not in rule
