# -*- coding: utf-8 -*-
"""加班申請第二批（docs/PAYROLL_OVERTIME_PLAN.md §2.4）的原始碼契約：守衛、自助端不收時數、核准長補休或加班費、薪資單吃加班費、
通知無 emoji、三個宿主都掛卡、管理端有加班佇列。行為測試在 test_overtime_logic.py。"""
import re

from tests.unit._srcscan import func_body, js_code_only, repo_src

_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def test_router_registered_on_master_and_nas():
    assert "'api_overtime'" in repo_src("main.py")
    assert '"api_overtime"' in repo_src("main_office.py"), "員工手機在 NAS 上也要能報加班"
    src = repo_src("routers/api_overtime.py")
    assert 'me_router = APIRouter(prefix="/api/v1/me/overtime"' in src and 'hr_router = APIRouter(prefix="/api/v1/hr/overtime"' in src
    assert "router.include_router(me_router)" in src and "router.include_router(hr_router)" in src


def test_employee_endpoints_use_bound_staff_and_do_not_accept_hours():
    src = repo_src("routers/api_overtime.py")
    for fn in ("my_overtime", "my_shoots_on", "preview_my_overtime", "apply_my_overtime", "cancel_my_overtime"):
        assert 'await require_bound_staff(request, "me_leave")' in func_body(src, f"async def {fn}("), fn
    from core.schemas import MeOvertimeCreate, MeOvertimePreview
    for cls in (MeOvertimeCreate, MeOvertimePreview):
        assert "hours" not in cls.model_fields and "day_kind" not in cls.model_fields, "自助端不收客戶端算好的數字"
    body = func_body(src, "async def apply_my_overtime(")
    assert 'hours=ev["hours"], day_kind=ev["day_kind"]' in body and 'if ev["errors"]:' in body
    assert "事由必填" in body
    cancel = func_body(src, "async def cancel_my_overtime(")
    assert 'if o.status != "待審":' in cancel and "HrOvertimeRequest.staff_id == ident[\"staff_id\"]" in cancel


def test_manager_guards_and_approval_effects():
    src = repo_src("routers/api_overtime.py")
    assert 'VIEWERS = ("hr_leave", "finance_partner")' in src
    assert "check_admin_or_module(request, *VIEWERS)" in func_body(src, "async def list_overtime(")
    ap = func_body(src, "async def approve_overtime(")
    assert "payload = check_admin(request)" in ap and "payload = check_admin(request)" in func_body(src, "async def reject_overtime(")
    assert 'kind="補休"' in ap and 'source="overtime"' in ap and "expires_on=expires_on_for(o.date)" in ap, "補休 → 時數帳一列、當年 12/31 到期"
    assert "hours = credit_hours_for(o.hours, o.day_kind, rates)" in ap, "補休換算照費率表（法定 1:1，公司給假日 1:2）"
    assert "o.pay_month = pay_month_for(o.date, confirmed)" in ap and "o.pay_amount = overtime_pay(hourly, o.hours, o.day_kind, rates)" in ap
    assert "還沒有薪資主檔" in ap, "沒主檔算不出加班費要擋"
    assert "await refresh_staff_line(session, o.staff_id, o.pay_month)" in ap
    assert "退回要寫理由" in func_body(src, "async def reject_overtime(")


def test_payroll_pulls_approved_overtime_pay():
    src = repo_src("routers/api_payroll.py")
    fill = func_body(src, "async def _fill_lines(")
    assert "ot_pay = await approved_pay_by_staff(session, run.month)" in fill
    assert 'if "overtime_pay" not in manual:\n            extras["overtime_pay"] = ot_pay.get(s.id, 0)' in fill, "手改過的加班費不蓋"
    assert "await mark_lines(session, run.month" in fill
    rf = func_body(src, "async def refresh_staff_line(")
    assert 'run.status != "草稿"' in rf and "return False" in rf


def test_model_and_money_registry():
    from core.money import REGISTRY_EXEMPT
    assert "pay_amount" in REGISTRY_EXEMPT
    from db.models import HrOvertimeRequest
    assert HrOvertimeRequest.__tablename__ == "hr_overtime_requests"


def test_notifier_templates_no_emoji():
    src = repo_src("notifier.py")
    for key in ("overtime_request", "overtime_result"):
        line = next(ln for ln in src.splitlines() if f'"{key}":' in ln)
        assert not _EMOJI.search(line), f"{key} 通知不能有 emoji（owner 鐵則）"


def test_employee_card_on_all_hosts_and_no_emoji():
    card = repo_src("frontend/js/my/cards-ot.js")
    assert not _EMOJI.search(js_code_only(card))
    code = js_code_only(card)
    assert "function cardOvertime(bound)" in code and '/api/v1/me/overtime/preview' in code
    assert '"/api/v1/me/overtime/shoots?date="' in code, "從場次帶入"
    assert "cardOvertime(ws.bound)" in repo_src("frontend/js/my/shell.js")
    assert 'src="./js/my/cards-ot.js"' in repo_src("frontend/my.html")
    lh = repo_src("frontend/leave.html")
    assert 'id="lv-ot-host"' in lh and 'src="./js/my/cards-ot.js"' in lh and "otHost.appendChild(cardOvertime(true))" in lh
    m = repo_src("frontend/m/views/leave.js")
    assert "/api/v1/me/overtime" in m and "ot-form" in m, "手機版也要能報加班"


def test_manager_queue_view():
    html = repo_src("frontend/tabs/hr_leave/hr_leave.html")
    assert 'data-hl-view="overtime">加班佇列' in html
    js = js_code_only(repo_src("frontend/tabs/hr_leave/hr_leave.js"))
    assert "/*" not in js, "hr_leave.js 只能用雙斜線註解"
    assert "async function _loadOvertime()" in js and "function _renderOvertime()" in js
    assert "if (view === 'overtime')" in js
    assert "hpost(`/overtime/${id}/approve`)" in js and "hpost(`/overtime/${id}/reject`, { note: note.trim() })" in js
    assert "!isAdmin() || it.status !== '待審'" in js, "核准／退回只在 Lv3 畫"


def test_queue_hides_pay_amount_without_money_key():
    """BUG-1：加班費金額回推得出月薪；人事（hr_leave）與合夥人（finance_partner）看得到佇列但不該看到金額。"""
    from routers.api_overtime import redact_pay
    items = [{"id": "a", "payout": "加班費", "pay_amount": 2667}, {"id": "b", "payout": "補休", "pay_amount": None}]
    assert redact_pay([dict(x) for x in items], True)[0]["pay_amount"] == 2667, "有金額鑰匙照回"
    out = redact_pay([dict(x) for x in items], False)
    assert "pay_amount" not in out[0] and "pay_amount" not in out[1], "沒鑰匙就刪鍵（不是歸零）"
    assert out[0]["payout"] == "加班費", "其他欄位不動"
    src = repo_src("routers/api_overtime.py")
    assert "redact_pay(items, can_see_money(request))" in func_body(src, "async def list_overtime("), "清單一定要經過它"
