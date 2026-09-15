# -*- coding: utf-8 -*-
"""請假申請單（owner 2026-09-15 三步：日期算小時 → 自己挑要扣的假 → 一整張單送出、一次核准、核准前可編輯）。

- 純規則 core.leave_logic：fit_items（挑的假依順序填滿需要的小時、最後一筆只扣還需要的、挑不夠回 remain）、
  plan_children（鋪到每一天：同種假一張、換種拆上午／下午或時段）。
- I/O services.leave_application：inventory（每筆 credit＋病假／事假／婚假／喪假；公假還沒規劃，owner 2026-09-15 拿掉）、evaluate、save（子單重建）、
  approve（照 alloc_plan 扣、要附證明沒附 422）、set_status（整張連子單）。
- 端點：/me/leave/inventory、/me/leave/applications[/preview|/{id}|/{id}/cancel|/{id}/proof]（本人）；
  /hr/leave/applications（LEAVE_VIEWERS 看）、/{id}/approve|reject|cancel_decide（管理員）。
- 前端：工作台假勤卡三步表單；人事分頁待核佇列一張申請單一張卡、核准（整張）。
"""

from tests.unit._srcscan import func_body, js_code_only, js_func_body, repo_src


def _inv(**kw):
    base = {"id": "c1", "kind": "特休", "credit_id": "c1", "available": 32.0, "label": "一年特休"}
    base.update(kw)
    return base


def test_fit_items_fills_in_order_and_last_pick_is_partial():
    from core.leave_logic import fit_items
    picks = [_inv(), _inv(id="c2", credit_id="c2", available=88.0, label="二年特休")]
    takes, remain = fit_items(picks, 20)
    assert [(t["id"], t["take"]) for t in takes] == [("c1", 20.0), ("c2", 0.0)] and remain == 0, "第一筆就夠：只扣 20、第二筆不扣"
    takes, remain = fit_items(picks, 40)
    assert [(t["id"], t["take"]) for t in takes] == [("c1", 32.0), ("c2", 8.0)] and remain == 0, "第一筆用完接第二筆，第二筆只扣還需要的 8"
    takes, remain = fit_items([_inv()], 40)
    assert takes[0]["take"] == 32.0 and remain == 8.0, "挑不夠：回還差幾小時"
    takes, remain = fit_items([_inv(id="type:事假", kind="事假", credit_id=None, available=None)], 40)
    assert takes[0]["take"] == 40.0 and remain == 0, "不限的（事假）全吃"
    assert fit_items([], 8) == ([], 8.0)


def test_plan_children_merges_same_kind_and_splits_kind_changes():
    from core.leave_logic import plan_children
    slots = [{"date": "2026-12-01", "hours": 8, "part": "all", "start_time": None, "end_time": None},
             {"date": "2026-12-02", "hours": 8, "part": "all", "start_time": None, "end_time": None}]
    # 兩筆特休接著用：12/01 一張扣 c1 8h；12/02 一張、扣 c1 4h＋c2 4h（同種假不拆）
    takes = [_inv(take=12.0), _inv(id="c2", credit_id="c2", take=4.0)]
    ch = plan_children(slots, takes)
    assert [(c["date"], c["kind"], c["hours"], c["part"], c["allocations"]) for c in ch] == [
        ("2026-12-01", "特休", 8.0, "all", [["c1", 8.0]]),
        ("2026-12-02", "特休", 8.0, "all", [["c1", 4.0], ["c2", 4.0]])]
    # 特休 12h ＋ 事假 4h：12/02 拆成上午特休／下午事假
    takes = [_inv(take=12.0), _inv(id="type:事假", kind="事假", credit_id=None, take=4.0)]
    ch = plan_children(slots, takes)
    assert [(c["date"], c["kind"], c["hours"], c["part"]) for c in ch] == [
        ("2026-12-01", "特休", 8.0, "all"), ("2026-12-02", "特休", 4.0, "am"), ("2026-12-02", "事假", 4.0, "pm")]
    assert ch[2]["allocations"] == [], "事假不走時數帳"
    # 不是 4／4 的拆法：照時段接續（09:00 起）
    takes = [_inv(take=14.0), _inv(id="type:事假", kind="事假", credit_id=None, take=2.0)]
    ch = plan_children(slots, takes)
    assert (ch[1]["part"], ch[1]["start_time"], ch[1]["end_time"]) == ("range", "09:00", "15:00")
    assert (ch[2]["part"], ch[2]["start_time"], ch[2]["end_time"]) == ("range", "15:00", "17:00")
    # 下午的半天拆：從 13:00 起
    ch = plan_children([{"date": "2026-12-03", "hours": 4, "part": "pm", "start_time": None, "end_time": None}],
                       [_inv(take=2.0), _inv(id="type:事假", kind="事假", credit_id=None, take=2.0)])
    assert [(c["start_time"], c["end_time"]) for c in ch] == [("13:00", "15:00"), ("15:00", "17:00")]


def test_record_types_and_meta():
    from core.leave_logic import ALL_LEAVE_TYPES, PICKABLE_RECORD_TYPES, RECORD_META, record_item_id
    assert PICKABLE_RECORD_TYPES == ("病假", "事假", "婚假", "喪假"), "公假還沒規劃（owner 2026-09-15），員工挑不到"
    assert "公假" not in RECORD_META and "公假" not in ALL_LEAVE_TYPES, "公假整個拿掉（owner 2026-09-15「我沒有公假」）"
    assert set(PICKABLE_RECORD_TYPES) <= set(ALL_LEAVE_TYPES) and "其他" not in PICKABLE_RECORD_TYPES
    assert RECORD_META["病假"]["proof"] and RECORD_META["病假"]["cap_days"] == 30
    assert RECORD_META["事假"]["proof"] is False and "不給薪" in RECORD_META["事假"]["paid"]
    assert record_item_id("病假") == "type:病假"


def test_service_saves_only_taken_items_and_rebuilds_children():
    svc = repo_src("services/leave_application.py")
    save = func_body(svc, "async def save(")
    assert 'for t in ev["takes"] if float(t.get("take") or 0) > 0]' in save, "挑了但沒扣到的不存"
    assert "await session.delete(c)" in save and "application_id=app.id" in save, "編輯＝子單全刪重建，子單指回申請單"
    assert 'alloc_plan=json.dumps(ch.get("allocations") or []' in save, "核准時照這個扣"
    inv = func_body(svc, "async def inventory(")
    assert "legacy = await _legacy_pending(session, staff_id)" in inv
    assert "HrLeaveRequest.application_id.is_(None)" in func_body(svc, "async def _legacy_pending("), "申請單的子單不能再算一次（編輯時會扣兩遍）"
    ev = func_body(svc, "async def evaluate(")
    assert "workdays_between(d0, d1, holidays)" in ev, "起迄模式由後端展開工作日"
    assert '_err("no_items", "還沒挑要扣的假")' in ev and '_err("insufficient"' in ev
    assert "_overlaps(session, staff.id, dates, exclude_app_id)" in ev, "編輯自己那張時撞單要排除自己的子單"


def test_service_approve_checks_proof_and_specified_credits():
    svc = repo_src("services/leave_application.py")
    ap = func_body(svc, "async def approve(")
    assert "要先附上證明（員工在假勤頁上傳）才能核准" in ap
    assert "for cid, h in _loads(ch.alloc_plan, [])" in ap and "HrLeaveAllocation(" in ap, "照員工指定的那幾筆扣"
    assert 'round(c["remaining"], 2) + 1e-9 < h' in ap and "status_code=422" in ap, "不夠 422、一筆都不扣"
    assert ap.index("status_code=422") < ap.index("HrLeaveAllocation("), "先驗完再寫"
    st = func_body(svc, "async def set_status(")
    assert "release_allocations(session, ch.id)" in st, "撤回／消假核准要把子單吃掉的時數放回去"


def test_endpoints_are_guarded():
    me = repo_src("routers/api_me.py")
    for fn in ("async def my_leave_inventory(", "async def preview_my_application(", "async def _submit_application(",
               "async def cancel_my_application(", "async def upload_my_application_proof(", "async def my_application_proof("):
        assert 'require_bound_staff(request, "me_leave")' in func_body(me, fn), fn
    sub = func_body(me, "async def _submit_application(")
    assert 'get_or_404(session, app_id, ident["staff_id"])' in sub and 'if app.status != "待審":' in sub, "只能改自己的、只能改待審的"
    assert "/leave/batch" not in me, "批次端點被申請單取代，拿掉了"
    hr = repo_src("routers/api_hr.py")
    assert "check_admin_or_module(request, *LEAVE_VIEWERS)" in func_body(hr, "async def list_applications(")
    for fn in ("async def approve_application(", "async def reject_application(", "async def decide_application_cancel("):
        assert "check_admin(request)" in func_body(hr, fn), fn
    assert "await _calendar_sync_leave(cid)" in func_body(hr, "async def approve_application("), "核准後子單逐張上日曆"


def test_models_and_migration():
    m = repo_src("db/models/_workos.py")
    assert 'class HrLeaveApplication(Base):' in m and '__tablename__ = "hr_leave_applications"' in m
    assert "application_id = Column(String(32), nullable=True, index=True)" in m and "alloc_plan = Column(Text, nullable=True)" in m
    mig = repo_src("db/migrations.py")
    assert '("hr_leave_requests", "application_id", "VARCHAR(32)")' in mig and '("hr_leave_requests", "alloc_plan", "TEXT")' in mig
    assert '"application_id": getattr(o, "application_id", None) or ""' in func_body(repo_src("core/hr_logic.py"), "def leave_to_dict(")


def test_card_is_three_steps_with_inventory_and_edit():
    js = js_code_only(repo_src("frontend/js/my/cards-hr.js"))
    render = js_func_body(js, "function renderLeave(")
    assert render.index('id="lv-need"') < render.index('id="lv-inv"') < render.index('id="lv-reason"'), "1 日期 → 2 挑假 → 3 送出"
    assert "_lvListHtml()" in render
    lst = js_func_body(js, "function _lvListHtml(")
    assert "LV.applications" in lst and "filter(r => !r.application_id)" in lst, "申請單一列一張；子單不重複列，沒申請單的舊單照舊"
    row = js_func_body(js, "function _lvAppRow(")
    assert 'lvEditApp(' in row and "cancelLeaveApp(" in row and 'a.status === "待審"' in row, "待審可編輯／撤回"
    assert "cancel_mode" in row, "已核准依 cancel_mode 撤回／申請消假"
    pv = js_func_body(js, "async function lvPreview(")
    assert "還差 ${_lvH(d.remain)} 小時，再挑一筆" in pv and "剛好" in pv and "已經夠了，這筆沒扣到" in pv
    assert "這張單每一天扣的是" in pv
    tg = js_func_body(js, "function lvTogglePick(")
    assert "_lvPicks.push(id)" in tg, "挑的順序＝扣的順序"
    assert "lvSetMode(" in js and 'if (!pick) _lvDates = [];' in js_func_body(js, "function lvSetMode(")


def test_admin_queue_shows_applications_with_one_approve():
    src = repo_src("frontend/tabs/hr_leave/hr_leave.js")
    load = js_func_body(js_code_only(src), "async function _loadQueue(")
    assert "'/leave/applications?status=待審'" in load and "filter(x => !x.application_id)" in load, "申請單一張卡；子單不再各自一卡"
    card = js_func_body(js_code_only(src), "function _appCard(")
    assert "核准（整張）" in card and 'data-approve-app=' in card and 'data-reject-app=' in card
    assert "缺證明（不能核准）" in card and 'data-proof-app=' in card
    assert "/leave/applications/${id}/approve" in src and "/leave/applications/${id}/reject" in src and "/leave/applications/${id}/cancel_decide" in src


def test_no_emoji_in_new_ui_text():
    import re
    for f in ("frontend/js/my/cards-hr.js", "frontend/tabs/hr_leave/hr_leave.js"):
        assert not re.search("[\\U0001F300-\\U0001FAFF☀-➿]", js_code_only(repo_src(f))), f


def test_polish_single_row_endpoints_refuse_application_children():
    """/polish 2026-09-15 BUG-1：單張端點動到申請單的子單會讓申請單與子單狀態對不上 → 409，整張走 applications。"""
    hr = repo_src("routers/api_hr.py")
    assert 'if standalone and getattr(obj, "application_id", None):' in func_body(hr, "async def _get_leave(")
    for fn in ("async def approve_leave(", "async def reject_leave(", "async def decide_cancel(", "async def update_leave(", "async def delete_leave("):
        assert "_get_leave(session, leave_id, standalone=True)" in func_body(hr, fn), fn


def test_polish_approve_only_live_credits_and_rebuild_keeps_proof():
    """BUG-2：核准只能扣現在可用的 credit（到期／未生效同 allocate 的判準）；BUG-3：編輯重建子單要帶著證明。"""
    svc = repo_src("services/leave_application.py")
    ap = func_body(svc, "async def approve(")
    assert "usable_credits(all_credits, on=today or date.today())" in ap and "cid not in live" in ap
    assert "proof_path=app.proof_path" in func_body(svc, "async def save(")


def test_polish_pending_count_counts_applications_once():
    """BUG-4：一張 3 天的申請單在工作台「請假待審 N 件」原本算 3 件。"""
    svc = repo_src("services/leave_service.py")
    body = func_body(svc, "async def staff_leave_summary(")
    assert "HrLeaveRequest.application_id.is_(None)" in body and "func.count(HrLeaveApplication.id)" in body
