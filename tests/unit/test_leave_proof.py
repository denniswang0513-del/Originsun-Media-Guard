# -*- coding: utf-8 -*-
"""病假證明 ＋ 員工自助只開特休／補休／病假（owner 2026-09-15「這裡改特休、補修就好；病假需要上傳文件」）。

- 字彙：SELF_SERVICE_TYPES（自助能送的）／PROOF_REQUIRED_TYPES（要附證明才准核）都從 vocab 給前端，不寫死。
- 員工端 preview／apply 走 evaluate(self_service=True)：其他假別回 bad_type；管理端代登不受限。
- 證明檔：POST /me/leave/{id}/proof（本人、限待審／消假待審／已核准），放收據根目錄 `_假勤證明/{年月}/`，
  副檔名黑名單＋串流上限與零用金收據同一套；DB 存 canonical UNC；看檔走 /me/leave/{id}/proof 或 /hr/leave/{id}/proof。
- 核准：leave_service.approve_request 對 PROOF_REQUIRED_TYPES 沒 proof_path 的單 422。
- 額度生效日：granted_on 還沒到的 credit 不可扣（李宜庭滿半年才給的 3 天）。
"""
from datetime import date

from tests.unit._srcscan import func_body, js_code_only, js_func_body, repo_src


def test_vocab_exposes_self_service_and_proof_types():
    from core.leave_logic import ALL_LEAVE_TYPES, PROOF_REQUIRED_TYPES, SELF_SERVICE_TYPES, vocab as leave_vocab
    assert SELF_SERVICE_TYPES == ("特休", "補休", "病假")
    assert PROOF_REQUIRED_TYPES == ("病假",)
    assert set(SELF_SERVICE_TYPES) <= set(ALL_LEAVE_TYPES) and set(PROOF_REQUIRED_TYPES) <= set(SELF_SERVICE_TYPES)
    v = leave_vocab()
    assert v["self_service_types"] == list(SELF_SERVICE_TYPES) and v["proof_required_types"] == list(PROOF_REQUIRED_TYPES)
    assert v["leave_types"] == list(ALL_LEAVE_TYPES), "管理端代登仍是整份清單"


def test_employee_endpoints_evaluate_as_self_service_but_admin_does_not():
    me = repo_src("routers/api_me.py")
    assert "self_service=True" in func_body(me, "async def preview_my_leave(")
    assert "self_service=True" in func_body(me, "async def apply_my_leave(")
    hr = repo_src("routers/api_hr.py")
    assert "self_service" not in func_body(hr, "async def create_leave(")
    svc = repo_src("services/leave_service.py")
    ev = func_body(svc, "async def evaluate(")
    assert 'if self_service and getattr(body, "leave_type", None) not in SELF_SERVICE_TYPES:' in ev
    assert '_err("bad_type"' in ev


def test_approve_is_blocked_without_proof():
    svc = repo_src("services/leave_service.py")
    body = func_body(svc, "async def approve_request(")
    assert 'if obj.leave_type in PROOF_REQUIRED_TYPES and not (getattr(obj, "proof_path", None) or "").strip():' in body
    assert "status_code=422" in body.split("PROOF_REQUIRED_TYPES")[1][:300]


def test_proof_upload_is_own_only_and_reuses_the_receipt_pipeline():
    me = repo_src("routers/api_me.py")
    up = func_body(me, "async def upload_my_leave_proof(")
    assert 'require_bound_staff(request, "me_leave")' in up
    assert "_my_leave_or_404(session, leave_id, ident[\"staff_id\"])" in up, "只能傳自己的"
    assert "PROOF_UPLOAD_STATUSES" in up and 'PROOF_UPLOAD_STATUSES = ("待審", "消假待審", "已核准")' in me
    assert "BLOCKED_UPLOAD_EXTS" in up and "stream_to_disk" in up and "_MAX_RECEIPT_BYTES" in up, "與零用金收據同一套黑名單／上限"
    assert "to_canonical_path(filepath)" in up, "DB 存 canonical UNC（NAS 容器與 master 都讀得到）"
    d = func_body(me, "def leave_proof_dir(")
    assert '"_假勤證明"' in d and "_receipts_root()" in d and "to_local_path(" in d
    # 看檔：本人（/me）與管理層（/hr，LEAVE_VIEWERS）
    assert "no_store_file(path)" in func_body(me, "async def my_leave_proof(")
    hr = repo_src("routers/api_hr.py")
    pf = func_body(hr, "async def leave_proof(")
    assert "check_admin_or_module(request, *LEAVE_VIEWERS)" in pf and "no_store_file(path)" in pf


def test_proof_path_column_serialized_and_migrated():
    assert "proof_path = Column(Text, nullable=True)" in repo_src("db/models/_workos.py")
    assert '("hr_leave_requests", "proof_path", "TEXT")' in repo_src("db/migrations.py")
    assert '"proof_path": getattr(o, "proof_path", None) or ""' in func_body(repo_src("core/hr_logic.py"), "def leave_to_dict(")


def test_workspace_card_and_mobile_form_take_the_proof_file():
    js = js_code_only(repo_src("frontend/js/my/cards-hr.js"))
    render = js_func_body(js, "function renderLeave(")
    assert 'id="lv-proof"' in render and 'id="lv-proof-wrap"' in render
    apply = js_func_body(js, "async function applyLeave(")
    assert '"這種假要附證明（照片或 PDF）"' in apply and "_lvUploadAppProof(created.id, proofFile)" in apply, "申請單一份證明"
    assert "_lvProofNeeded = !!d.proof_required;" in js_func_body(js, "async function lvPreview("), "要不要附證明由試算（挑到的假別）決定"
    assert 'fetch("/api/v1/me/leave/applications/" + id + "/proof", { method: "POST", headers: _lvAuthHeaders(), body: fd })' in js, "multipart 不走 mfetch"
    assert 'fetch("/api/v1/me/leave/" + id + "/proof", { method: "POST", headers: _lvAuthHeaders(), body: fd })' in js, "舊單的補傳照舊"
    assert "image/*" not in js, "accept 不能寫 image/*（掃描器會把 /* 當註解）"
    m = js_code_only(repo_src("frontend/m/views/leave.js"))
    assert "vocab().self_service_types || vocab().leave_types" in m
    assert "const needsProof = (t) => (vocab().proof_required_types || []).includes(t);" in m
    assert "fd.append('file', proofFile)" in m and "'/api/v1/me/leave/' + created.id + '/proof'" in m
    assert "image/*" not in m


def test_admin_tab_shows_proof_and_missing_marker():
    src = repo_src("frontend/tabs/hr_leave/hr_leave.js")
    assert "const _needsProof = (t) => (_vocab.proof_required_types || []).includes(t);" in src
    assert "缺證明（不能核准）" in src and "data-proof=" in src
    assert src.count("if (t.dataset.proof) return _openProof(t.dataset.proof);") == 2, "待核佇列與請假紀錄兩邊都要接"
    assert "authDownload(API + `/leave/${id}/proof`" in src, "<a href> 帶不了 Authorization，走 authDownload"


def test_credit_not_usable_before_granted_on():
    from core.leave_logic import balance, usable_credits
    c = [{"id": "c1", "kind": "特休", "hours": 24, "status": "可用", "granted_on": "2026-12-01", "expires_on": "2027-05-31", "used": 0}]
    assert usable_credits(c, kind="特休", on=date(2026, 9, 15)) == []
    assert [x["id"] for x in usable_credits(c, kind="特休", on=date(2026, 12, 1))] == ["c1"]
    assert balance(c, kind="特休", on=date(2026, 9, 15))["available"] == 0
    assert balance(c, kind="特休", on=date(2026, 12, 15))["available"] == 24


def test_admin_hard_delete_also_removes_the_calendar_event():
    """2026-09-15 重匯歷史假時發現：DELETE /hr/leave/{id} 硬刪後 Google 日曆那顆事件變孤兒。"""
    body = func_body(repo_src("routers/api_hr.py"), "async def delete_leave(")
    assert "event_id = obj.google_event_id" in body and 'calendar_sync.delete_events([event_id], "leave")' in body
    assert body.index("await session.commit()") < body.index("delete_events("), "先 commit 再刪日曆（同 _calendar_sync_leave 的規矩）"
