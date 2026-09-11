# -*- coding: utf-8 -*-
"""假勤一期的 API／表／遷移（docs/LEAVE_PLAN.md §7）：掃原始碼的不變式。

規則不是「函式住哪裡」而是「這條流程有沒有做這件事」—— 一律用 tests/unit/_srcscan
（func_body／flow_body／code_only／migration_sql／models_src），不切固定字數。
兩個前端 agent 平行對著 §7 寫，這裡釘的是契約：路徑、守衛、狀態轉換只走專屬端點、
核准要分配時數帳、退回要理由、通知模板存在、欄位與回填在開機路徑上。
"""
import re

from tests.unit._srcscan import (code_only, flow_body, func_body, migration_sql, models_src, repo_src, schemas_src)

HR = repo_src("routers/api_hr.py")
ME = repo_src("routers/api_me.py")
SVC = repo_src("services/leave_service.py")


def _routes(src: str) -> set:
    return set(re.findall(r'@router\.(get|post|put|delete)\("([^"]+)"\)', src))


# ── 路由存在（§7.4／§7.5）────────────────────────────────────────────────

def test_employee_routes_exist_per_contract():
    got = _routes(ME)
    for r in [("get", "/leave/summary"), ("post", "/leave/preview"), ("post", "/leave"),
              ("post", "/leave/{leave_id}/cancel"), ("delete", "/leave/{leave_id}")]:
        assert r in got, f"api_me 少了 {r}"


def test_admin_routes_exist_per_contract():
    got = _routes(HR)
    for r in [("get", "/leave"), ("post", "/leave"), ("get", "/leave/{leave_id}/context"),
              ("post", "/leave/{leave_id}/approve"), ("post", "/leave/{leave_id}/reject"),
              ("post", "/leave/{leave_id}/cancel_decide"), ("put", "/leave/{leave_id}"), ("delete", "/leave/{leave_id}"),
              ("get", "/balances"), ("get", "/credits"), ("post", "/credits"),
              ("post", "/credits/{credit_id}/approve"), ("post", "/credits/{credit_id}/reject"), ("delete", "/credits/{credit_id}"),
              ("get", "/holidays"), ("post", "/holidays"), ("delete", "/holidays/{day}"), ("post", "/holidays/import"),
              # 舊分頁還在打的
              ("get", "/leave/quota"), ("put", "/staff/{staff_id}/annual_leave")]:
        assert r in got, f"api_hr 少了 {r}"


# ── 守衛 ────────────────────────────────────────────────────────────────

def test_every_admin_endpoint_is_guarded_by_hr_leave():
    """每支端點都有守：看清單／登記／改欄位＝hr_leave；核准類＝check_admin（owner 2026-09-08：假勤核准留管理員，
    細節釘在 tests/unit/test_batch2_hr_guards.py）。"""
    body = code_only(HR)
    handlers = re.split(r"\n@router\.", body)[1:]
    for h in handlers:
        name = re.search(r"async def (\w+)\(", h).group(1)
        # 直接守（hr_leave 或管理員），或委派給同檔的 _decide_credit（它自己守）
        assert ('check_admin_or_module(request, "hr_leave")' in h or "check_admin(request)" in h
                or "_decide_credit(" in h), f"{name} 沒守"


def test_employee_leave_endpoints_require_bound_staff_and_me_leave():
    body = code_only(ME)
    for header in ("async def preview_my_leave(", "async def apply_my_leave(", "async def _cancel_my_leave("):
        assert 'require_bound_staff(request, "me_leave")' in func_body(body, header), header
    # summary 只認請假那把（2026-09-08 起：畫面早就只在有 me_leave 時畫卡，API 跟著收）
    assert 'require_bound_staff(request, "me_leave")' in func_body(body, "async def my_leave_summary(")
    # 撤回只能撤自己的：查詢帶 staff_id
    assert 'HrLeaveRequest.staff_id == ident["staff_id"]' in func_body(body, "async def _cancel_my_leave(")


# ── 狀態轉換只走專屬端點 ──────────────────────────────────────────────────

def test_put_rejects_status_and_never_changes_it():
    body = code_only(func_body(HR, "async def update_leave("))
    assert 'data.get("status") is not None' in body and "422" in body
    assert "obj.status =" not in body
    # 全檔只有這幾支能寫 leave／credit 的 status（原本這裡斷言一個從來不存在的識別字，等於沒守）。
    # 多一支就要在這裡具名加進來，順便被人看見「又多了一條改狀態的路」。
    writers = {fn for fn in re.findall(r"^async def (\w+)\(", HR, re.M)
               if "obj.status =" in code_only(func_body(HR, f"async def {fn}("))}
    assert writers == {"reject_leave", "decide_cancel", "_decide_credit"}, writers


def test_update_compares_dates_through_tw_day_not_raw_columns():
    """只改起日或只改迄日時，一邊是 parse_ymd 的 naive、另一邊是 DB 讀回的 aware ——
    直接比會 TypeError 讓整支 PUT 變 500（2026-09-08 在 dev 上實際踩到）。兩邊都要先過 tw_day。"""
    body = code_only(func_body(HR, "async def update_leave("))
    assert "tw_day(obj.end_date) < tw_day(obj.start_date)" in body
    assert "obj.end_date < obj.start_date" not in body
    # 改完之後算時數那支也一樣（它本來就有）
    assert "working_hours(tw_day(obj.start_date), tw_day(obj.end_date)" in code_only(func_body(HR, "def _updated_hours("))


def test_approve_allocates_from_the_ledger_and_notifies():
    approve = code_only(flow_body(HR, "async def approve_leave("))
    assert "leave_service.approve_request(" in approve
    assert '_notify_result("核准"' in approve
    svc = code_only(func_body(SVC, "async def approve_request("))
    assert "allocate(" in svc and "HrLeaveAllocation(" in svc and "InsufficientHours" in svc and "422" in svc
    assert 'obj.status = "已核准"' in svc and "obj.approved_by = actor" in svc
    assert 'obj.status != "待審"' in svc and "409" in svc


def test_reject_requires_a_note_and_records_it():
    body = code_only(func_body(HR, "async def reject_leave("))
    assert "if not note:" in body and "422" in body
    assert 'obj.status = "已退回"' in body and "obj.reject_note = note" in body
    assert '_notify_result("退回"' in body


def test_cancel_decide_releases_allocations_when_approved():
    body = code_only(func_body(HR, "async def decide_cancel("))
    assert 'obj.status != "消假待審"' in body
    assert "release_allocations(" in body and 'obj.status = "已撤回"' in body and 'obj.status = "已核准"' in body


def test_employee_cancel_follows_cancel_mode_and_never_hard_deletes():
    body = code_only(func_body(ME, "async def _cancel_my_leave("))
    assert "cancel_mode(" in body
    for token in ('"locked"', "409", '"free"', "release_allocations(", 'obj.status = "消假待審"', 'obj.status = "已撤回"'):
        assert token in body, token
    assert "session.delete(" not in body
    # 舊 DELETE 是 cancel 的別名
    assert "_cancel_my_leave(" in code_only(func_body(ME, "async def cancel_my_leave_legacy("))


def test_create_paths_refuse_on_errors_and_keep_the_old_days_body():
    for src, header in ((ME, "async def apply_my_leave("), (HR, "async def create_leave(")):
        body = code_only(func_body(src, header))
        assert "leave_service.evaluate(" in body and 'if ev["errors"]:' in body and "422" in body, header
        assert "leave_service.build_request(" in body
    # 員工端事由必填
    assert 'body.reason or ""' in func_body(ME, "async def apply_my_leave(")
    # 舊分頁 {days} → days×8
    hb = code_only(func_body(SVC, "def hours_from_body("))
    assert "body.days) * HOURS_PER_DAY" in hb


def test_evaluate_covers_every_error_and_warning_code_in_the_contract():
    body = code_only(func_body(SVC, "async def evaluate("))
    for code in ('"bad_type"', '"bad_range"', '"overlap"', '"insufficient"', '"notice_short"', '"shoot_conflict"', '"sick_cap"'):
        assert code in body, code
    # 保留時數：可用 − 待審保留 < 這次
    assert 'bal["available"] - bal["reserved"]' in body


def test_overlap_and_reserved_use_the_same_active_status_set():
    assert "ACTIVE_STATUSES" in code_only(func_body(SVC, "async def active_between("))
    assert 'HrLeaveRequest.status == "待審"' in code_only(func_body(SVC, "async def pending_hours_for("))
    from core.leave_logic import ACTIVE_STATUSES
    assert set(ACTIVE_STATUSES) == {"待審", "已核准", "消假待審"}


def test_old_rows_without_hours_are_read_as_days_times_eight():
    assert "func.coalesce(HrLeaveRequest.hours, HrLeaveRequest.days * HOURS_PER_DAY)" in SVC
    from core.hr_logic import leave_to_dict

    class Old:
        id = "x"; staff_id = "s"; staff_name = "n"; leave_type = "特休"; start_date = None; end_date = None
        days = 1.5; reason = None; status = "待審"; approved_by = None; approved_at = None; created_by = None; created_at = None
    d = leave_to_dict(Old())
    assert d["hours"] == 12 and d["part"] == "all" and d["reject_note"] == "" and d["cancel_note"] == ""


# ── 通知 ────────────────────────────────────────────────────────────────

def test_leave_result_template_exists_without_emoji():
    src = repo_src("notifier.py")
    m = re.search(r'"leave_result":\s*"([^"]+)"', src)
    assert m, "notifier 缺 leave_result 模板"
    tpl = m.group(1)
    for k in ("{result}", "{staff_name}", "{leave_type}", "{start}", "{end}", "{hours}", "{note}"):
        assert k in tpl, k
    assert not re.search("[\U0001F300-\U0001FAFF☀-➿⭐✅❌]", tpl), "通知文字無 emoji（owner 鐵則）"
    # 呼叫端每個變數都給（缺的會被印成 '-'）
    call = code_only(func_body(HR, "async def _notify_result("))
    for k in ("result=", "staff_name=", "leave_type=", "start=", "end=", "hours=", "note="):
        assert k in call, k


# ── 表／欄位／遷移 ──────────────────────────────────────────────────────────

def test_new_tables_and_columns_are_declared():
    src = models_src()
    for tbl in ("hr_leave_credits", "hr_leave_allocations", "hr_holidays"):
        assert f'__tablename__ = "{tbl}"' in src, tbl
    req = src[src.index("class HrLeaveRequest("):src.index("class HrLeaveCredit(")]
    for col in ("hours", "part", "start_time", "end_time", "reject_note", "cancel_note", "google_event_id", "synced_at", "sync_error"):
        assert re.search(rf"\n    {col} = Column\(", req), f"HrLeaveRequest 缺 {col}"
    alloc = src[src.index("class HrLeaveAllocation("):src.index("class HrHoliday(")]
    assert 'UniqueConstraint("request_id", "credit_id"' in alloc
    assert "date = Column(Date, primary_key=True)" in src[src.index("class HrHoliday("):]
    from db.models import HrHoliday, HrLeaveAllocation, HrLeaveCredit  # noqa: F401  匯出面（漏加會 ImportError）


def test_new_request_columns_are_in_the_boot_migration_with_backfill():
    sql = migration_sql()
    for col in ("hours", "part", "start_time", "end_time", "reject_note", "cancel_note", "google_event_id", "synced_at", "sync_error"):
        assert re.search(rf'\("hr_leave_requests", "{col}", "', sql), f"_crm_cols 缺 hr_leave_requests.{col}"
    assert "UPDATE hr_leave_requests SET hours = days * 8 WHERE hours IS NULL" in sql
    # 回填要跑在加欄之後：main.py 先跑 CRM_COLUMNS 那個迴圈（ADD COLUMN），
    # 再跑 FINANCE_AND_CRM_COLUMNS（含那句 UPDATE）—— 釘的是**執行順序**，不是清單住哪個檔
    main = repo_src("main.py")
    assert main.index("_MIG.CRM_COLUMNS") < main.index("_MIG.FINANCE_AND_CRM_COLUMNS")
    assert "UPDATE hr_leave_requests SET hours" in repo_src("db/migrations.py")


def _schema_body(src: str, cls: str) -> str:
    body = src[src.index(f"class {cls}("):]
    return body[:body.index("\nclass ")]


def test_schemas_keep_new_fields_optional_for_old_clients():
    src = schemas_src()
    for cls in ("LeaveCreate", "LeaveUpdate"):        # 管理端：時數可以手調，欄位要 Optional（舊分頁不帶）
        body = _schema_body(src, cls)
        for f in ("part", "start_time", "end_time", "hours", "days"):
            assert re.search(rf"\n    {f}: Optional\[[^\]]+\] = None", body), f"{cls}.{f} 要 Optional=None（舊分頁不帶）"
    body = _schema_body(src, "MeLeaveCreate")         # 員工自助：時段欄位照樣 Optional
    for f in ("part", "start_time", "end_time"):
        assert re.search(rf"\n    {f}: Optional\[[^\]]+\] = None", body), f"MeLeaveCreate.{f} 要 Optional=None"
    for cls in ("MeLeavePreview", "LeaveCancel", "LeaveReject", "LeaveCancelDecide", "CreditCreate", "HolidayCreate", "HolidayImport"):
        assert f"class {cls}(BaseModel)" in src, cls


def test_employee_leave_never_takes_hours_from_the_client():
    """🔴 員工自助送單**不收 hours／days**（2026-09-08 review）：收了的話可以送「五天特休、hours: 0.5」，
    preview 顯示 40 小時、實際只從特休帳扣 0.5。時數一律由起迄／時段算（hours_from_body 走 working_hours）。
    MeLeavePreview 本來就沒有這兩欄，兩條路要算出同一個答案。"""
    from core.schemas import MeLeaveCreate, MeLeavePreview
    assert "hours" not in MeLeaveCreate.model_fields and "days" not in MeLeaveCreate.model_fields
    assert set(MeLeaveCreate.model_fields) - set(MeLeavePreview.model_fields) == {"reason"}, "送單只比 preview 多一個事由"
    m = MeLeaveCreate(leave_type="特休", start_date="2026-10-05", end_date="2026-10-09", reason="x",
                      hours=0.5, days=0.5)          # 舊分頁還在送 → 被忽略，不是 422
    assert getattr(m, "hours", None) is None and getattr(m, "days", None) is None


def test_rules_live_in_leave_logic_not_in_routers():
    """週末／假日算法只住 core.leave_logic.working_hours；router 與 service 不自己數日子。"""
    for name, src in (("api_hr", HR), ("api_me", ME), ("leave_service", SVC)):
        body = code_only(src)
        assert "weekday() < 5" not in body, f"{name} 自己在算週末"     # `.weekday()` 當顯示欄位可以，拿來數工作日不行
        assert "def working_hours(" not in body, f"{name} 抄了第二份 working_hours"
        assert "hr_logic import" not in body or "is_workday" not in re.search(r"from core\.hr_logic import \(?([^)]*)\)?", body).group(1), \
            f"{name} 拿 hr_logic.is_workday（那是工時用的）"
