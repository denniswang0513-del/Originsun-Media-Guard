# -*- coding: utf-8 -*-
"""員工請假一期 — 手機分頁「假勤」（frontend/m/views/leave.js；docs/LEAVE_PLAN.md §7.6）的釘子。

釘的是契約與手機版的三條鐵則，不是行號：
1. 只 import ../shell.js 與 ../ui.js（殼只有一份）；不直接 fetch（一律 mfetch，401 才會導回登入）。
2. 走 §7.4 的三支員工端點（summary／preview／{id}/cancel）＋送單 POST /me/leave；
   已核准的單依 cancel_mode 三分（free 撤回／apply 申請消假帶 note／locked 不給按）。
3. 分頁列有 leave；按鈕字沒有 emoji（feedback_ui_no_emoji）。
"""
import re

from tests.unit._srcscan import js_code_only, js_func_body, repo_src

LEAVE = repo_src("frontend/m/views/leave.js")
SRC = js_code_only(LEAVE)


def test_leave_view_imports_only_the_shell_and_ui():
    imports = re.findall(r"^import\s.*?from\s+['\"]([^'\"]+)['\"]", SRC, flags=re.M)
    assert imports, "leave.js 沒有 import"
    for spec in imports:
        assert spec in ("../shell.js", "../ui.js") or spec.startswith("/js/shared/"), f"leave.js 不准 import {spec}"
    assert "from '../shell.js'" in SRC and "from '../ui.js'" in SRC


def test_leave_view_never_fetches_directly():
    assert not re.search(r"(?<![a-zA-Z_])fetch\(", SRC), "手機頁只准走 shell.mfetch（401 導回登入、detail 統一成 Error）"
    assert "mfetch(" in SRC


def test_leave_view_calls_the_phase1_employee_endpoints():
    assert "'/api/v1/me/leave/summary'" in SRC
    assert "'/api/v1/me/leave/preview', { method: 'POST'" in SRC
    assert "'/api/v1/me/leave', { method: 'POST'" in SRC
    assert "/api/v1/me/leave/${encodeURIComponent(id)}/cancel`, { method: 'POST'" in SRC
    # preview 的 body 形狀＝契約 §7.4
    body = js_func_body(SRC, "function formBody()")
    for k in ("leave_type:", "start_date:", "end_date:", "part,", "start_time:", "end_time:"):
        assert k in body, k
    # 送單多 reason（必填，前端先擋空字串）
    assert "{ ...formBody(), reason }" in SRC and "if (!reason)" in SRC


def test_preview_is_debounced_and_blocks_submit_on_errors():
    assert "PREVIEW_DEBOUNCE_MS = 300" in SRC and "setTimeout(runPreview, PREVIEW_DEBOUNCE_MS)" in SRC
    run = js_func_body(SRC, "async function runPreview()")
    assert "setBlocked(errors.length > 0)" in run
    assert "共 ${fmtH(r.hours)} 小時（${fmtH(r.days)} 天）" in run
    assert "++_previewSeq" in run and "seq !== _previewSeq" in run, "打字太快時要只認最後一次回應"
    assert 'id="lv-submit" disabled' in SRC, "送出鈕一開始鎖著，preview 過了才開"


def test_cancel_mode_has_three_branches():
    acts = js_func_body(SRC, "function actionsHtml(r)")
    assert "r.cancel_mode === 'free'" in acts and 'data-mode="free"' in acts and "撤回" in acts
    assert "r.cancel_mode === 'apply'" in acts and 'data-mode="apply"' in acts and "申請消假" in acts
    assert "r.cancel_mode === 'locked'" in acts and "颱風假當日不可消" in acts and "data-locked=" in acts
    assert "<button" not in acts.split("'locked'", 1)[1].split("\n", 1)[0], "locked 是文字不是按鈕"
    cancel = js_func_body(SRC, "async function cancelRequest(btn, host)")
    assert "window.prompt(" in cancel and "body.note = note" in cancel and "if (!note) return" in cancel, "apply 要帶必填 note"
    assert "window.confirm(" in cancel, "撤回前要 confirm"


def test_error_shapes_are_tolerated():
    fn = js_func_body(SRC, "function errorMessages(e)")
    assert "Array.isArray(d)" in fn and "Array.isArray(d.errors)" in fn and "typeof d === 'string'" in fn


def test_vocab_comes_from_summary_not_hardcoded():
    assert "vocab().leave_types" in SRC and "vocab().parts" in SRC and "hours_per_day" in SRC
    assert "'特休', '補休'" not in SRC, "假別清單不准寫死（後端 vocab.leave_types 才是正本）"
    assert "renderPaged(" in SRC and "shouldLoad('leave'" in SRC and "markStale('leave')" in SRC


def test_leave_is_a_mobile_tab_after_worklog():
    ui = repo_src("frontend/m/ui.js")
    tabs = re.findall(r"'(\w+)'", re.search(r"export const TABS = \[(.*?)\]", ui).group(1))
    assert "leave" in tabs and tabs.index("leave") == tabs.index("worklog") + 1
    html = repo_src("frontend/m/crm.html")
    assert re.search(r'data-tab="leave"[^>]*>(<i[^>]*></i>)?假勤</button>', html), "分頁列要有「假勤」"
    crm = js_code_only(repo_src("frontend/m/crm.js"))
    assert "from './views/leave.js'" in crm and "leave: leaveView" in crm


def test_no_emoji_in_leave_button_labels():
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✅❌]")
    labels = re.findall(r"<button[^>]*>(.*?)</button>", SRC, flags=re.S)
    assert labels
    for lab in labels:
        assert not emoji.search(lab), f"按鈕字含 emoji：{lab!r}"
    assert not emoji.search(SRC)
