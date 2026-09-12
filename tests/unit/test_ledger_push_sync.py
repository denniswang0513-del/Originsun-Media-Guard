# -*- coding: utf-8 -*-
"""雙向「推送」＋連結後以母帳為準（owner 2026-09-12，docs/LEDGER_UNIFY_PLAN.md §8）。

「推送」＝在對面建一個對應的案並連結，兩案並存、各記各的錢。連上之後識別欄
（客戶／案型／日期／說明）由母帳決定；**金額永遠不同步**（母帳的合約額是公司
跟客戶的、私帳的是「我拿到的那段」，同一個欄位兩種意思）。
"""
from datetime import datetime
from types import SimpleNamespace as NS

from core.ledger_project import (LINK_SYNC_FIELDS, MIRROR_TOTAL_KEY,
                                 mirror_detail, mirror_stale, norm_detail,
                                 sync_from_parent)
from tests.unit._srcscan import code_only, func_body, repo_src

_PROJ = repo_src("routers/crm/projects.py")


def _row(**kw):
    base = dict(client_id=None, project_type="", shoot_date=None, start_date=None,
                completion_date=None, description="", status="製作",
                contract_amount=None, ledger_detail=None, amount_received=0)
    base.update(kw)
    return NS(**base)


# ── 純規則：sync_from_parent ──────────────────────────────────────────

def test_money_is_never_a_sync_field():
    """🔴 兩邊的 contract_amount 意思不同（差 3～17 倍），錢的橋只有成本行→私帳收入那一座。"""
    for bad in ("contract_amount", "amount_received", "amount_receivable",
                "ledger_detail", "payment_status", "name", "status", "entity"):
        assert bad not in LINK_SYNC_FIELDS, bad


def test_parent_value_wins():
    """規則 1：母帳有值 → 私帳跟著。"""
    d = datetime(2026, 9, 1)
    p = _row(client_id="c-crm", project_type="紀實影片", completion_date=d, description="母帳寫的")
    m = _row(client_id="c-mine", project_type="影視服務", completion_date=None, description="私帳寫的")
    changed, filled = sync_from_parent(p, m)
    assert set(changed) == {"client_id", "project_type", "completion_date", "description"}
    assert filled == []
    assert (m.client_id, m.project_type, m.completion_date, m.description) == \
        ("c-crm", "紀實影片", d, "母帳寫的")
    assert m.status == "結案", "結案日同步過來 → 私帳狀態跟著推導"


def test_parent_blank_fills_from_mine_instead_of_wiping():
    """規則 2：母帳空白 → **不清私帳**，反而拿私帳的補進母帳（母帳 216 案沒結案日、私帳全部有）。"""
    d = datetime(2025, 3, 3)
    p = _row(completion_date=None, description="", project_type=None)
    m = _row(completion_date=d, description="私帳備註", project_type="平面攝影")
    changed, filled = sync_from_parent(p, m)
    assert changed == []
    assert set(filled) == {"completion_date", "description", "project_type"}
    assert (p.completion_date, p.description, p.project_type) == (d, "私帳備註", "平面攝影")
    assert m.completion_date == d, "私帳的值被清掉了"


def test_mine_client_is_not_pushed_into_parent_by_the_pure_rule():
    """🔴 私帳客戶（entity=mine 的 Client）不能直接寫進母帳案 —— 反向補客戶是 I/O
    （要先對到／建出母帳客戶），住在 routers 的 _sync_pair，純函式不碰。"""
    p = _row(client_id=None)
    m = _row(client_id="c-mine")
    changed, filled = sync_from_parent(p, m)
    assert p.client_id is None and "client_id" not in filled
    body = code_only(func_body(_PROJ, "async def _sync_pair("))
    assert "_crm_client_for(session, mine.client_id)" in body


def test_no_fill_respects_a_deliberate_blank_on_the_parent():
    """母帳 PUT 親手清空的欄位（重開案）不能在同一交易被私帳的值補回去。"""
    d = datetime(2025, 3, 3)
    p = _row(completion_date=None, description="")
    m = _row(completion_date=d, description="私帳備註")
    changed, filled = sync_from_parent(p, m, no_fill={"completion_date"})
    assert changed == [] and filled == ["description"]
    assert p.completion_date is None, "使用者清掉的結案日被私帳補回去了"
    assert m.completion_date == d, "私帳不清"


def test_skip_leaves_a_field_alone_in_both_directions():
    p = _row(client_id="c-crm", project_type="紀實影片")
    m = _row(client_id="c-mine-linked", project_type=None)
    changed, filled = sync_from_parent(p, m, skip={"client_id"})
    assert changed == ["project_type"] and filled == []
    assert m.client_id == "c-mine-linked"


def test_move_back_to_parent_refuses_a_linked_mirror():
    """分身搬回公司帳＝母帳那側的 mine_link_id 指著一個母帳案，「重新同步」會寫進它的合約額。"""
    fn = code_only(func_body(_PROJ, "async def move_project_ledger("))
    assert 'if target == "parent":' in fn and "mine_parent_links(session, [project_id])" in fn


def test_close_date_wall_only_fires_when_the_value_changes():
    """舊分頁 js（CF 4 小時快取）整包送 close_date，沒動也送 —— 看到鍵就 409 等於存不了。"""
    fin = repo_src("routers/api_finance_projects.py")
    put = code_only(func_body(fin, "async def update_project_ledger("))
    assert "raw != _fmt_day(p.completion_date)" in put


def test_nothing_changes_when_both_sides_agree():
    d = datetime(2026, 1, 1)
    p = _row(client_id="c", completion_date=d, project_type="MV")
    m = _row(client_id="c", completion_date=d, project_type="MV")
    assert sync_from_parent(p, m) == ([], [])


# ── 落後判定：mirror_stale ─────────────────────────────────────────────

def test_norm_detail_keeps_the_synced_total():
    """🔴 每一次 PUT 都會 norm_detail 一遍 —— 不保留 mirror_total 的話「私帳落後多少」永遠判不出來。"""
    d = norm_detail({"split": {"剪輯": 100}, MIRROR_TOTAL_KEY: 100})
    assert d[MIRROR_TOTAL_KEY] == 100
    assert MIRROR_TOTAL_KEY not in norm_detail({"split": {}, MIRROR_TOTAL_KEY: 0})
    assert MIRROR_TOTAL_KEY not in norm_detail({MIRROR_TOTAL_KEY: "x"})


def test_mirror_detail_records_the_total():
    assert mirror_detail({"剪輯": 100, "調光": 50})[MIRROR_TOTAL_KEY] == 150


def test_stale_compares_last_synced_total_not_current_split():
    """比的是**上次同步的合計**：私帳那份他可能自己改過（照實際請款填），那不是落後。"""
    assert mirror_stale({MIRROR_TOTAL_KEY: 100, "split": {"剪輯": 80}}, 100) == (False, 0)
    assert mirror_stale({MIRROR_TOTAL_KEY: 100}, 103000) == (True, 102900)
    assert mirror_stale({MIRROR_TOTAL_KEY: 100}, 60) == (True, -40)
    # 舊連結沒記過 → 判不出來，不是 False（False 會讓畫面說「一致」）
    assert mirror_stale({"split": {"剪輯": 100}}, 100) == (None, 0)
    assert mirror_stale(None, 100) == (None, 0)


def test_check_reports_stale_and_the_sync_writes_the_total():
    chk = code_only(func_body(_PROJ, "async def check_project_mirror("))
    assert "mirror_stale(linked.ledger_detail, mir[\"total\"])" in chk
    assert '"stale": stale, "delta": delta' in chk
    # N:1 的合計不屬於任何一案 → 不判
    assert "shared = len(" in chk and "if not shared:" in chk
    post = code_only(func_body(_PROJ, "async def mirror_project_to_mine("))
    assert 'keep[MIRROR_TOTAL_KEY] = mir["total"]' in post


# ── 連結寫入者順手套「以母帳為準」 ──────────────────────────────────────

def test_write_link_is_the_only_place_that_syncs_a_pair_on_link():
    """四條連結路（推送鈕／對應表兩方向／連既有）都經過 _write_link，所以同步掛在它身上
    就一處也不會漏；1:1 才做（N:1 母帳互相矛盾時不猜）。"""
    writer = code_only(func_body(_PROJ, "async def _write_link("))
    assert "await _sync_pair(session, parent, mine)" in writer
    pair = code_only(func_body(_PROJ, "async def _sync_pair("))
    assert "sync_from_parent(parent, mine, no_fill=no_fill, skip=skip)" in pair
    # 私帳客戶已連結到母帳這筆客戶＝同一個客戶的兩本帳，規則 1 不准把私帳案的客戶換掉
    assert 'mc.crm_link_id == parent.client_id' in pair and 'skip.add("client_id")' in pair
    assert "CrmProject.mine_link_id == mine.id, CrmProject.id != parent.id" in pair
    assert "mine.source_project_id != parent.id" in pair, "舊形狀的第二個來源沒認"
    assert "return False" in pair


def test_parent_edits_flow_to_the_linked_mine_case():
    """母帳 PUT 改了識別欄 → 同一交易寫進連著的私帳案（不是等下次連結）。"""
    fn = code_only(func_body(_PROJ, "async def update_project("))
    assert "any(k in update_data for k in LINK_SYNC_FIELDS)" in fn
    # 親手送上來的欄位不做規則 2：清結案日＝重開案，不能被私帳的值補回去
    assert "await _sync_pair(session, project, linked_mine, no_fill=set(update_data))" in fn
    assert fn.index("await _sync_pair(") < fn.index("await session.commit()")


def test_mine_side_locks_the_close_date_when_linked_one_to_one():
    """以母帳為準：私帳詳情的結案日鎖住（畫面畫鎖、後端 409），到母帳改會同步過來。"""
    fin = repo_src("routers/api_finance_projects.py")
    assert 'LOCKED_WHEN_LINKED = ("close_date",)' in fin
    put = code_only(func_body(fin, "async def update_project_ledger("))
    assert "結案日以母帳為準" in put and "409" in put
    det = code_only(func_body(fin, "async def project_ledger_detail("))
    assert '"parent_links": [{"id": pid, "name": n} for pid, n in _pl]' in det
    assert '"locked_fields": list(LOCKED_WHEN_LINKED) if len(_pl) == 1 else []' in det


# ── 推送不擋 0；換帳本放寬給代開 ──────────────────────────────────────

def test_push_does_not_block_when_no_cost_line_is_mine():
    """推送＝補一列；沒有掛給我的成本行就開 0（warning 講清楚），不是 409。"""
    chk = code_only(func_body(_PROJ, "async def check_project_mirror("))
    assert "_me_staff_id_or_blank(request)" in chk, "沒綁人員檔案也不擋"
    post = code_only(func_body(_PROJ, "async def mirror_project_to_mine("))
    assert "_me_staff_id_or_blank(request)" in post
    assert "raise HTTPException(status_code=409, detail=reason)" not in post
    prev = code_only(func_body(_PROJ, "async def _mirror_preview("))
    assert "if staff_id" in prev, "staff_id 空字串拿去比會把 actual_staff_id 空字串的行當成我的"


def test_move_to_mine_lets_passthrough_invoices_through():
    """走發票代開：公司幫我開的「內部代開」發票掛在私帳案上是正常形狀（生產已有），
    它與它自動生的請款單不擋換帳本；「專案」類發票、手動請款、所有收支照擋。"""
    flt = code_only(func_body(_PROJ, "def _blocker_filter("))
    assert "MINE_LINK_INVOICE_CATEGORY" in flt
    assert "CrmInvoice.category != MINE_LINK_INVOICE_CATEGORY" in flt
    assert "CrmPaymentRequest.source_invoice_id.is_(None)" in flt
    assert "CrmCashEntry" not in flt, "收支不能放寬"
    blk = code_only(func_body(_PROJ, "async def _ledger_blockers("))
    assert "_blocker_filter(M)" in blk


def test_move_to_mine_fills_in_what_a_private_case_needs():
    """🔴 搬過去只改 entity 的話，那一案安靜地不進私帳應收帳款（同 2026-08-26 那 349 案 NULL 的病）。
    案源（代開發票→代辦費自動算）、正規化、resync_receivable 三件都要做。"""
    mv = code_only(func_body(_PROJ, "async def move_project_ledger("))
    seg = mv.split('if target == "mine":')[1].split("else:")[0]
    assert "apply_source_fee(" in seg and "resync_receivable(p, d)" in seg
    assert '"代開發票" if await _has_passthrough_invoice(' in seg
    # 已經有分身的案整案搬過去會變成兩個案 —— 先擋
    assert "await resolve_mine_link(session, p) is not None" in mv
    # 搬回母帳：佔位金額旗標清掉
    assert "p.contract_amount_source = None" in mv


def test_move_check_tells_the_frontend_which_source_to_default():
    chk = code_only(func_body(_PROJ, "async def check_project_ledger_move("))
    for key in ('"has_passthrough_invoice"', '"source_options"', '"source_default"'):
        assert key in chk, key
    from core.schemas import ProjectLedgerMovePayload
    assert "source" in ProjectLedgerMovePayload.model_fields
    assert ProjectLedgerMovePayload.model_fields["source"].default is None


def test_blocked_reason_points_to_push_or_the_invoice_category():
    from routers.crm.projects import _blocked_reason
    msg = _blocked_reason("X", [("發票", 1)])
    assert "推送" in msg and "內部代開" in msg
    assert _blocked_reason("X", []) == ""


# ── 前端（母帳側）：一顆入口三態、三種情況分流 ────────────────────────

def test_crm_push_button_asks_which_kind_first():
    """「推送到私帳」先問是哪一種（公司付我一部分／走代開／記錯帳本），再分流到分身或換帳本。
    使用者不必知道要按哪一顆。"""
    from tests.unit._srcscan import js_code_only, js_func_body
    js = repo_src("frontend/tabs/crm/crm-projects-core.js")
    fn = js_code_only(js_func_body(js, "window._projPushMine = async function (id, linked) {"))
    for kind in ("'share'", "'passthrough'", "'own'"):
        assert kind in fn, kind
    assert "window._projMirrorMine(id)" in fn                       # 分身
    assert "source: kind === 'passthrough' ? '代開發票' : ''" in fn   # 換帳本＋案源
    assert "if (linked) { return window._projMirrorMine(id); }" in fn   # 已連結直接重新同步


def test_crm_stale_hint_trusts_the_backend_verdict():
    """「私帳落後了沒」的判定正本在後端（mirror_stale）—— 前端只畫三值，不自己比 Σsplit。"""
    from tests.unit._srcscan import js_code_only, js_func_body
    js = repo_src("frontend/tabs/crm/crm-projects-core.js")
    fn = js_code_only(js_func_body(js, "window._projMirrorStaleHint = async function (id, btn) {"))
    assert "chk.stale === true" in fn and "chk.stale === false" in fn
    assert "split" not in fn, "前端又自己算了一份"
    det = js_code_only(repo_src("frontend/tabs/crm/crm-projects-detail.js"))
    assert "window._projMirrorStaleHint?.(project.id" in det


def test_crm_move_to_mine_asks_the_source_and_lists_blockers():
    from tests.unit._srcscan import js_code_only, js_func_body
    js = repo_src("frontend/tabs/crm/crm-projects-core.js")
    fn = js_code_only(js_func_body(js, "window._projMoveLedger = async function (id, opts = {}) {"))
    assert 'id="pml-source"' in fn and "chk.source_default" in fn
    assert "chk.has_passthrough_invoice" in fn
    # 被擋時列出擋住的單據，並給「改用推送（分身）」那條出路
    assert "chk.blockers" in fn and 'id="pml-share"' in fn
    sub = js_code_only(js_func_body(js, "async function _projMoveSubmit("))
    assert "source: source || null" in sub


def test_crm_mirror_dialog_uses_a_searchable_select():
    """400 筆私帳案塞原生 select 找不到東西（專案對應那頁早就是可搜尋的）。"""
    from tests.unit._srcscan import js_code_only, js_func_body
    js = repo_src("frontend/tabs/crm/crm-projects-core.js")
    fn = js_code_only(js_func_body(js, "window._projMirrorMine = async function (id, mirrorOpts = {}) {"))
    assert "searchableSelect(sel, { placeholder: '搜尋私帳案…' })" in fn
    assert "chk.warning" in fn, "沒有成本行的警告要畫出來"
    assert "chk.can_mirror === false" in fn, "舊後端（發版空窗）擋住時仍要講得出話"


def test_jumps_between_the_two_ledgers_go_through_the_right_doors():
    """🔴 私帳案只住在獨立頁 /my-ledger.html（SPA 的財務分頁釘死母帳，私帳案在那邊只會得到
    「這個專案不屬於目前的帳本」—— 真瀏覽器實測）；那頁每次都要重新登入，所以交棒用 ?project=。
    反向從獨立頁跳母帳沒有 switchTab，開 SPA 新分頁帶 ?project=，crm-projects.js 的 hook 要吃它。"""
    from tests.unit._srcscan import js_code_only, js_func_body
    det = js_code_only(repo_src("frontend/tabs/crm/crm-projects-detail.js"))
    assert "window.open('/my-ledger.html?project=' + encodeURIComponent(id)" in det
    assert "switchTab('tab_crm_invoices')" not in det, "又把私帳案往 SPA 的財務分頁送"
    ml = repo_src("frontend/my-ledger.html")
    assert "new URLSearchParams(location.search).get('project')" in ml
    assert "sessionStorage.setItem('omgJumpLedgerProject', jump)" in ml
    crm = js_code_only(repo_src("frontend/tabs/crm/crm-projects.js"))
    assert "sessionStorage.getItem('omgJumpCrmProject') || qs.get('project')" in crm
    fin = repo_src("frontend/tabs/finance/subviews/projects.js")
    fn = js_code_only(js_func_body(fin, "_fp.gotoParent = () => {"))
    assert "window.open('/?project='" in fn and "#tab_crm_projects" in fn


def test_mine_side_push_button_replaces_the_pipeline_flag_button():
    """「⬆ 推專案管理」（crm_pushed）拿掉：有了母帳分身它是多餘的（旗標保留、既有 6 案不動）。
    未連結→「推送到母帳」；已連結→「母帳：案名 ↗」；1:1 時結案日鎖住並標「母帳」。"""
    from tests.unit._srcscan import js_code_only, js_func_body
    fin = repo_src("frontend/tabs/finance/subviews/projects.js")
    code = js_code_only(fin)
    assert "_fp.push = " not in code and "推專案管理" not in code
    btn = js_code_only(js_func_body(fin, "function _linkBtnHtml(p) {"))
    assert "p.parent_links" in btn and "推送到母帳" in btn and "母帳：" in btn
    assert "const _closeLocked = (p) => ((p && p.locked_fields) || []).includes('close_date');" in fin
    sub = js_code_only(js_func_body(fin, "async function _pushSubmit(btn, mode) {"))
    for ep in ("/parent-link", "/ledger-move-check", "/move-ledger", "/parent-create"):
        assert ep in sub, ep


def test_passthrough_copy_keeps_the_parent_case_and_uses_its_contract():
    """owner 2026-09-12「雖然是代開發票，但是專案公司也留一份帳」：代開那一項底下多一個
    「留一份」—— 分身、案源＝代開發票、收入＝母帳合約額（那張發票的面額就是他的錢，
    掛給他的成本行通常是 0）、代辦費自動算。重新同步不能把案源翻回源日、也不動金額。"""
    from routers.crm.projects import _mirror_contract
    p = NS(contract_amount=82000)
    assert _mirror_contract(p, {"total": 0}, "代開發票") == 82000
    assert _mirror_contract(p, {"total": 3000}, "") == 3000
    assert _mirror_contract(NS(contract_amount=None), {"total": 3000}, "代開發票") == 3000
    row = code_only(func_body(_PROJ, "def _new_mirror_row("))
    assert "apply_source_fee(contract, d)" in row
    post = code_only(func_body(_PROJ, "async def mirror_project_to_mine("))
    assert 'keep["source"] = source or keep.get("source") or MIRROR_SOURCE' in post
    assert 'if keep["source"] == "代開發票":' in post
    from core.schemas import ProjectMirrorPayload
    assert "source" in ProjectMirrorPayload.model_fields
    from tests.unit._srcscan import js_code_only, js_func_body
    js = repo_src("frontend/tabs/crm/crm-projects-core.js")
    fn = js_code_only(js_func_body(js, "window._projPushMine = async function (id, linked) {"))
    assert 'name="ppm-pt"' in fn and "window._projMirrorMine(id, { source: '代開發票' })" in fn


# ── 特徵測試（/polish 安全網）：把現在的行為釘住 ─────────────────────────

def test_project_dict_carries_the_linked_mine_case_only_when_mirrored():
    """`_to_project_dict(mine_link=(id, name))`：mirrored 才露 id／name；沒連或看不到私帳兩欄都空。"""
    from routers.crm.projects import _to_project_dict
    from db.models import CrmProject
    p = CrmProject(id="p1", name="案", entity="parent", mine_link_id="m1", crm_pushed=0)
    out = _to_project_dict(p, "客", mirrored=True, mine_link=("m1", "私帳案"))
    assert (out["mirrored"], out["mine_link_id"], out["mine_link_name"]) == (True, "m1", "私帳案")
    out = _to_project_dict(p, "客", mirrored=False, mine_link=("m1", "私帳案"))
    assert (out["mirrored"], out["mine_link_id"], out["mine_link_name"]) == (False, "", "")
    # 舊形狀（母帳列沒有 mine_link_id）由呼叫端解出 id 餵進來，序列化照露
    p.mine_link_id = None
    out = _to_project_dict(p, "客", mirrored=True, mine_link=("m9", "舊分身"))
    assert (out["mine_link_id"], out["mine_link_name"]) == ("m9", "舊分身")


def test_blocker_filter_shape():
    """收支：無額外條件（全擋）；發票／請款：帶放寬條件（SQL 表達式）。"""
    from routers.crm.projects import _blocker_filter
    from db.models import CrmCashEntry, CrmInvoice, CrmPaymentRequest
    assert _blocker_filter(CrmCashEntry) is None
    inv = _blocker_filter(CrmInvoice)
    assert inv is not None and "內部代開" in str(inv.compile(compile_kwargs={"literal_binds": True}))
    pr = _blocker_filter(CrmPaymentRequest)
    assert pr is not None and "source_invoice_id IS NULL" in str(pr)


# ── code review round 2：0 不准洗掉既有私帳案；沒綁人員檔案不判落後 ───────────

def test_zero_total_never_overwrites_an_existing_mine_case():
    """🔴 「不擋 0」只該放行**新建**（開 0 由他填）。連到既有私帳案時 CRM 算出 0
    （沒綁人員檔案、或成本行一筆都沒掛給我）→ overwrite 會把他親手填的合約額與工項
    洗成 0、add 會把 mirror_total 清掉；以前這條路被 409 擋著。退成 keep（只連結）。"""
    post = code_only(func_body(_PROJ, "async def mirror_project_to_mine("))
    seg = post.split("if target_id:")[1]
    assert 'mode in ("overwrite", "add") and not mir["total"]' in seg
    assert 'mode = "keep"' in seg
    assert seg.index('mode = "keep"') < seg.index('if mode == "import":')
    # 代開發票的分身收入是母帳合約額，0 成本行是常態 —— 不退
    assert '!= "代開發票"' in seg.split('mode = "keep"')[0]


def test_stale_is_unknown_when_the_account_has_no_staff_binding():
    """沒綁人員檔案時 mir["total"] 是「認不出來」不是 0 —— 拿去比會把每一個已同步的案
    都標成「私帳落後 −全部」。判不出來就回 None（畫面維持原字）。"""
    chk = code_only(func_body(_PROJ, "async def check_project_mirror("))
    assert "elif sid:" in chk
    assert chk.index("elif sid:") < chk.index("mirror_stale(linked.ledger_detail")
