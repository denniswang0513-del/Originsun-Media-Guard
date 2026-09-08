# -*- coding: utf-8 -*-
"""請補修一期 —— 桌機前端契約（docs/LEAVE_PLAN.md §7.4／§7.5／§7.6）。

員工頁 `frontend/my.html` 的「我的假勤」卡與管理 tab `frontend/tabs/hr_leave/` 都是照 §7 的
JSON 形狀寫的，後端另一條線平行實作。這裡釘的是**前端跟契約對得上**：打哪幾支端點、
改狀態一律走 approve／reject／cancel_decide（PUT 不再帶 status）、撤回依 cancel_mode 三分、
preview 有去抖、按鈕文字無 emoji。
"""
import re

from tests.unit._srcscan import between, js_code_only, js_func_body, repo_src

MY = "frontend/my.html"
HL_JS = "frontend/tabs/hr_leave/hr_leave.js"
HL_HTML = "frontend/tabs/hr_leave/hr_leave.html"
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✅❌]")


def _my_code() -> str:
    return js_code_only(re.sub(r"<!--.*?-->", "", repo_src(MY), flags=re.S))


# ── 員工頁 /my.html ─────────────────────────────────────────────

def test_me_leave_card_is_back_in_the_zone():
    code = _my_code()
    zone = between(code, "const ME_ZONE_ON = new Set(", ")")
    assert '"me_leave"' in zone and '"me_petty"' in zone, "假勤卡要放回 ME_ZONE_ON（零用金不能掉）"
    assert 'grid.appendChild(cardLeave(' in code


def test_my_card_talks_to_the_three_employee_endpoints():
    code = _my_code()
    assert '"/api/v1/me/leave/summary"' in js_func_body(code, "async function loadLeave(")
    assert '"/api/v1/me/leave/preview"' in js_func_body(code, "async function lvPreview(")
    apply = js_func_body(code, "async function applyLeave(")
    assert '"/api/v1/me/leave"' in apply and 'method: "POST"' in apply
    cancel = js_func_body(code, "async function cancelLeave(")
    assert '"/api/v1/me/leave/" + id + "/cancel"' in cancel and 'method: "POST"' in cancel
    # 舊路（DELETE /me/leave/{id}、workspace bundle 的 ws.leave.quota）退場
    assert 'method: "DELETE" }' not in cancel
    assert "lv.quota" not in code and "lv-days" not in code, "天數不再手填，時數由 preview 算"


def test_preview_is_debounced_and_ignores_stale_responses():
    code = _my_code()
    soon = js_func_body(code, "function lvPreviewSoon(")
    assert "clearTimeout(_lvPreviewTimer)" in soon and "setTimeout(lvPreview" in soon
    pv = js_func_body(code, "async function lvPreview(")
    assert "++_lvPreviewSeq" in pv and "seq !== _lvPreviewSeq" in pv, "舊回應不可蓋掉新回應"
    assert "共 ${_lvH(d.hours)} 小時" in pv
    assert "d.warnings" in pv and "d.errors" in pv
    # 有 error 就鎖送出鈕；warning 只提醒
    assert "btn.disabled = errs.length > 0" in pv


def test_form_fields_follow_the_contract():
    code = _my_code()
    fn = js_func_body(code, "function renderLeave(")
    for fid in ("lv-type", "lv-part", "lv-start", "lv-end", "lv-range", "lv-start-time", "lv-end-time",
                "lv-preview", "lv-reason", "lv-submit", "lv-err"):
        assert f'id="{fid}"' in fn, fid
    # 假別與半天選項來自 vocab，不寫死
    assert "v.leave_types" in fn and "v.parts" in fn
    payload = js_func_body(code, "function _lvPayload(")
    for k in ("leave_type", "start_date", "end_date", "start_time", "end_time"):
        assert f"{k}:" in payload, k
    assert "part," in payload, "part（整天／上午／下午／時段）也要送"
    # 事由必填；422 的 errors 逐條顯示
    apply = js_func_body(code, "async function applyLeave(")
    assert '"請填事由"' in apply and "d.errors" in apply


def test_cancel_follows_cancel_mode():
    code = _my_code()
    row = js_func_body(code, "function _lvRow(")
    assert 'r.status === "待審"' in row and "cancelLeave('${esc(r.id)}', 'free')" in row
    assert 'r.cancel_mode === "free"' in row and 'r.cancel_mode === "apply"' in row and 'r.cancel_mode === "locked"' in row
    assert ">申請消假</button>" in row and "颱風假當日不可消" in row and "disabled" in row
    assert "r.reject_note" in row and "r.cancel_note" in row
    cancel = js_func_body(code, "async function cancelLeave(")
    # schema LeaveCancel 收 note（2026-09-08 修：之前送 cancel_note 必 422）
    assert 'mode === "apply"' in cancel and "body = { note }" in cancel, "申請消假要帶理由"
    assert "cancel_note: note" not in cancel


def test_my_three_stats():
    fn = js_func_body(_my_code(), "function renderLeave(")
    for lbl in ("特休剩餘", "補休剩餘", "病假已用"):
        assert lbl in fn, lbl
    assert 'bal["特休"]' in fn and 'bal["補休"]' in fn and "LV.sick" in fn


# ── 管理 tab frontend/tabs/hr_leave ─────────────────────────────

def test_hr_tab_has_four_blocks():
    html = repo_src(HL_HTML)
    for v in ("queue", "records", "balances", "holidays"):
        assert f'data-hl-view="{v}"' in html, v
    assert 'id="hl-content"' in html and 'id="hl-nav"' in html
    js = js_code_only(repo_src(HL_JS))
    assert "export async function initHrLeaveTab(" in js
    for v in ("queue", "records", "balances", "holidays"):
        assert f"view === '{v}'" in js, v


def test_hr_tab_calls_the_admin_endpoints():
    js = js_code_only(repo_src(HL_JS))
    assert "const API = '/api/v1/hr'" in js
    assert "/leave/${id}/approve" in js_func_body(js, "async function _approve(")
    rej = js_func_body(js, "async function _reject(")
    assert "/leave/${id}/reject" in js and "{ note }" in rej and "if (!note) return" in rej, "退回理由必填"
    cd = js_func_body(js, "async function _cancelDecide(")
    assert "/leave/${id}/cancel_decide" in cd and "{ approve, note }" in cd
    assert "/leave/${it.id}/context" in js_func_body(js, "async function _loadCtx(")
    assert "'/balances?year='" in js_func_body(js, "async function _loadBalances(")
    assert "'/credits?staff_id='" in js and "hpost('/credits'" in js and "hdel('/credits/'" in js
    assert "'/holidays?year='" in js and "hpost('/holidays'" in js and "hdel('/holidays/'" in js
    assert "hpost('/holidays/import', { csv })" in js
    # 佇列同時收 待審 與 消假待審
    q = js_func_body(js, "async function _loadQueue(")
    assert "status=待審" in q and "status=消假待審" in q


def test_status_changes_never_go_through_put():
    js = js_code_only(repo_src(HL_JS))
    assert "_setStatus" not in js
    assert "method: 'PUT'" not in js, "一期管理端沒有任何 PUT（改狀態走 approve／reject／cancel_decide）"
    assert "body: { status" not in js
    # 代登送 part／時段，不再手填 days
    add = js_func_body(js, "async function _addRecord(")
    assert "part," in add and "start_time:" in add and "days:" not in add
    # 舊的特休額度端點退場（改走時數帳）
    assert "/leave/quota" not in js and "annual_leave" not in js


def test_context_card_shows_balance_same_period_and_shoots():
    ctx = js_func_body(js_code_only(repo_src(HL_JS)), "async function _loadCtx(")
    assert "c.balance" in ctx and "餘額不足" in ctx and "餘額足夠" in ctx
    assert "c.same_period" in ctx and "同期休假" in ctx
    assert "c.shoot_conflicts" in ctx and "撞場次" in ctx
    assert "c.notice_days" in ctx


def test_no_emoji_in_hr_tab_ui_and_buttons_are_plain_text():
    js = js_code_only(repo_src(HL_JS))
    m = _EMOJI.search(js)
    assert not m, f"hr_leave.js 含 emoji：{m.group()!r}"
    html = re.sub(r"<!--.*?-->", "", repo_src(HL_HTML), flags=re.S)
    m = _EMOJI.search(html)
    assert not m, f"hr_leave.html 含 emoji：{m.group()!r}"
    for label in ("核准", "退回", "同意消假", "不同意", "建立", "刪除", "匯入", "新增"):
        assert f">{label}</button>" in js, label


def test_hr_tab_imports_only_whitelisted_or_shared():
    js = repo_src(HL_JS)
    imports = re.findall(r"import\s+[^;]*?from\s+'([^']+)'", js)
    assert imports, "沒有 import？"
    for path in imports:
        assert path.startswith("../../js/shared/") or path == "../crm/crm-utils.js", path
    assert "replace(/&/g" not in js, "HTML 逃脫只准 import dom.js 的 esc"
