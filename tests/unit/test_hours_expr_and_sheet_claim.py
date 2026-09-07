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
    assert 'type="text" inputmode="decimal" data-f="hours"' in sheet and 'type="number" data-f="hours"' not in sheet
    js = js_code_only(sheet)
    assert "export function parseHours(raw)" in js
    assert js.count("hours: parseHours(v('hours'))") == 2, "rowValues 與 rowBody 都要走算式"
    assert "const n = parseHours(hr.value); hr.value = n == null ? '' : n;" in js   # 離開格子就變算完的數字
    mob = js_code_only(repo_src("frontend/m/views/worklog.js"))
    assert "parseHours(F('hours').value)" in mob and 'id="wl-hours" type="text" inputmode="decimal"' in mob
    assert "parseHours, projectFromInput } from '../../js/shared/ts-sheet.js'" in mob


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
    claim = func_body(svc, "async def claim_sheet_row(")
    assert 'if getattr(r, "source", "") == "manual":' in claim and "add_tombstone(session, r.row_hash" in claim
    assert 'r.source = "manual"' in claim and 'r.status = "draft"' in claim and 'r.row_hash = "manual_" + uuid.uuid4().hex' in claim
    assert "await claim_sheet_row(session, r, ident" in func_body(svc, "async def update_row(")
    dele = func_body(svc, "async def delete_row(")
    assert 'if r.source != "manual":' in dele and "add_tombstone(" in dele
    merge = func_body(svc, "async def merge_day(")
    assert "await claim_sheet_row(session, kept, who)" in merge and "add_tombstone(session, a.row_hash" in merge
    rule = func_body(code_only(repo_src("core/hr_logic.py")), "def can_edit_timesheet(")
    assert '"not_manual"' not in rule
