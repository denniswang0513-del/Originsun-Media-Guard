# -*- coding: utf-8 -*-
"""收款方式（owner 2026-09-13）：母帳專案表單一個下拉（源日專案／後期代開／現金收款）取代
「推送到私帳」先問是哪一種的彈窗；「後期連結」那一行是系統算的備註；私帳分身的代辦費
可標「已扣除」也可手改。規則正本 core.ledger_project（BILLING_*／link_note／fee_deducted），
I/O 在 routers/crm/project_links.apply_billing_mode／link_note_for。"""
from core.ledger_project import (BILLING_LABELS, BILLING_MIRROR_SOURCE, BILLING_MODES, FEE_DEDUCTED_KEY,
                                 LINK_SYNC_FIELDS, MIRROR_AT_KEY, billing_mode_of, link_note, norm_detail)
from tests.unit._srcscan import code_only, flow_body, func_body, js_code_only, js_func_body, projects_src, repo_src

_LINKS = repo_src("routers/crm/project_links.py")
_PROJ = repo_src("routers/crm/projects.py")


def test_three_modes_and_null_means_company():
    assert BILLING_MODES == ("company", "passthrough", "cash")
    assert set(BILLING_LABELS) == set(BILLING_MODES)
    for raw in (None, "", "  ", "garbage", 0):
        assert billing_mode_of(raw) == "company"
    assert billing_mode_of(" passthrough ") == "passthrough"
    # 只有後期代開會自動要一個私帳分身；現金收款＝源日收現金（owner：這裡是源日的收款狀態），不碰後期
    assert BILLING_MIRROR_SOURCE == {"passthrough": "代開發票"}
    # 私帳沒有這個欄位 → 永遠不在「以母帳為準」的同步清單裡
    assert "billing_mode" not in LINK_SYNC_FIELDS


def test_link_note_unlinked_texts_follow_the_mode():
    for mode, hint in (("company", "推送到私帳"), ("passthrough", "代開發票"), ("cash", "源日收現金")):
        n = link_note(mode, None, None)
        assert n["linked"] is False and n["mode"] == mode and n["mode_label"] == BILLING_LABELS[mode]
        assert hint in n["text"] and n["mine_id"] == "" and n["mine_name"] == ""


def test_link_note_linked_passthrough_spells_out_the_fee():
    from core.ledger_project import apply_source_fee
    d = apply_source_fee(82000, norm_detail({"source": "代開發票", MIRROR_AT_KEY: "2026-09-12"}))
    n = link_note("passthrough", ("m1", "快樂學游泳動態製作", 82000), d, stale=False, passthrough_invoices=1)
    assert n["linked"] and n["mine_id"] == "m1" and n["source"] == "代開發票"
    assert (n["invoice_fee"], n["tax_fee"], n["buy_invoice"], n["fee_pct"]) == (6560, 3905, 2655, 8.0)
    assert n["fee_deducted"] is True and n["stale"] is False and n["mirror_at"] == "2026-09-12"
    t = n["text"]
    for piece in ("快樂學游泳動態製作", "代開發票", "82,000", "8% ＝ 6,560", "3,905", "2,655", "已扣", "一致",
                  "上次同步 2026-09-12", "內部代開發票 1 張"):
        assert piece in t, piece
    # 沒先扣 → 講清楚全額會匯進來；落後 → 標出來；源日 → 不抽代辦費
    off = link_note("passthrough", ("m1", "x", 82000), dict(d) | {FEE_DEDUCTED_KEY: False}, stale=True)
    assert off["fee_deducted"] is False and "未扣" in off["text"] and "私帳落後" in off["text"]
    plain = link_note("company", ("m2", "y", 24000), norm_detail({"source": "源日"}))
    assert "不抽代辦費" in plain["text"] and "24,000" in plain["text"]


def test_norm_detail_keeps_the_two_new_meta_keys_but_only_when_meaningful():
    d = norm_detail({FEE_DEDUCTED_KEY: False, MIRROR_AT_KEY: "2026-09-13"})
    assert d[FEE_DEDUCTED_KEY] is False and d[MIRROR_AT_KEY] == "2026-09-13"
    d2 = norm_detail({FEE_DEDUCTED_KEY: True, MIRROR_AT_KEY: "not a date"})
    assert FEE_DEDUCTED_KEY not in d2 and MIRROR_AT_KEY not in d2


def test_db_column_schema_and_backfill():
    from db import migrations
    from core.schemas import CrmProjectPatchPayload, CrmProjectPayload, LedgerDetailPayload
    assert ("crm_projects", "billing_mode", "VARCHAR(16)") in migrations.CRM_COLUMNS
    # 舊分頁的 PUT 不帶它 → Optional None，不是預設字串（會把人選好的洗掉）
    for m in (CrmProjectPayload, CrmProjectPatchPayload):
        f = m.model_fields["billing_mode"]
        assert f.default is None
    assert LedgerDetailPayload.model_fields["fee_deducted"].default is None
    # 回填：只標欄位、只碰 NULL、不碰私帳案、只認內部代開發票（冪等）
    bf = [x for x in migrations.CRM_INDEXES if "billing_mode='passthrough'" in x]
    assert len(bf) == 1
    assert "WHERE billing_mode IS NULL" in bf[0] and "<> 'mine'" in bf[0] and "category='內部代開'" in bf[0]
    assert "mine_link_id" not in bf[0] and "INSERT" not in bf[0], "回填只標欄位，不建分身"


def test_create_and_update_route_the_mode_change_through_apply_billing_mode():
    create = code_only(func_body(_PROJ, "async def create_project("))
    assert "_check_billing_mode(data)" in create
    assert 'await apply_billing_mode(session, project, request, "company")' in create
    update = code_only(func_body(_PROJ, "async def update_project("))
    assert "_check_billing_mode(update_data)" in update
    assert 'update_data.pop("billing_mode", None)' in update, "None＝沒送（舊分頁）→ 不動"
    assert "old_billing = project.billing_mode" in update
    assert 'if "billing_mode" in update_data:' in update, "值沒變也要呼叫（帳本主人再存一次補建分身）"
    assert "await apply_billing_mode(session, project, request, old_billing)" in update
    # 看不到私帳的人存了只存欄位 → 回應要講出來，不能安靜地什麼都沒發生
    assert '"skipped"' in update and "warnings.append(" in update
    # 序列化與單筆 wire
    assert '"billing_mode": billing_mode_of(p.billing_mode)' in func_body(_PROJ, "def _to_project_dict(")
    assert "link_note_for(session, project, request)" in func_body(_PROJ, "async def project_wire(")
    chk = code_only(func_body(_PROJ, "def _check_billing_mode("))
    assert "v not in BILLING_MODES" in chk and "422" in chk


def test_apply_billing_mode_rules():
    fn = code_only(flow_body(_LINKS, "async def apply_billing_mode("))
    # 只有看得到私帳的請求動私帳；不然回 skipped（不 raise —— 欄位本身誰都能存）
    assert 'require_entity(request, "mine", level="full")' in fn and '"skipped"' in fn
    # 值沒變：只補「後期代開但分身還沒建」，其他 none
    assert "new == old and not want_source" in fn and "if want_source and new == old and t is not None:" in fn
    # 同事整包表單送回同一個值而分身早就在 → none（不能每次存檔都對他 skipped＋warning）：查連結要在守衛之前
    assert fn.index("t = await resolve_mine_link(session, p)") < fn.index('require_entity(request, "mine", level="full")')
    # 私帳案自己沒有收款方式
    assert '(p.entity or "parent") == "mine"' in fn
    # 後期代開：沒分身就用 _new_mirror_row（建分身那一列的唯一寫法）＋ _write_link（連結的唯一寫入者）
    assert "_new_mirror_row(p, mir," in fn and "await _write_link(session, p, t)" in fn
    # 有分身：只改案源＋重跑費用，金額與工項不動；收入沒填過才用母帳合約額補
    assert 'keep["source"] = want_source' in fn and "if not int(t.contract_amount or 0):" in fn
    assert "resync_receivable(t, keep)" in fn
    # 換回源日專案／現金收款：分身留著、案源改回源日、代辦費三欄歸零、已扣旗標拿掉
    assert 'keep["source"] = MIRROR_SOURCE' in fn
    assert 'for k in ("invoice_fee", "tax_fee", "buy_invoice"):' in fn and "keep.pop(FEE_DEDUCTED_KEY, None)" in fn
    assert "session.delete" not in fn, "分身不刪（要拿掉走解除連結）"
    # 沒指定案源時 mirror-to-mine 看收款方式；「走不走代開」一份判定
    mm = code_only(func_body(_LINKS, "async def mirror_project_to_mine("))
    assert "BILLING_MIRROR_SOURCE.get(billing_mode_of(p.billing_mode)" in mm
    ip = code_only(func_body(_LINKS, "async def _is_passthrough("))
    assert 'billing_mode_of(p.billing_mode) == "passthrough"' in ip and "_has_passthrough_invoice(session, p.id)" in ip
    assert code_only(projects_src()).count("await _is_passthrough(session, p)") >= 2
    # 分身建立／重新同步都記 mirror_at
    assert "d[MIRROR_AT_KEY] = _today_tw()" in code_only(func_body(_LINKS, "def _new_mirror_row("))
    assert "keep[MIRROR_AT_KEY] = _today_tw()" in mm


def test_link_note_for_respects_the_mine_visibility_line():
    fn = code_only(flow_body(_LINKS, "async def link_note_for("))
    assert "hide_mine_projects(request)" in fn and "return link_note(mode, None, None)" in fn
    # 落後判定同 mirror-check：沒綁人員檔案 None；N:1 不判
    assert "mine_parent_names(session, [t.id])" in fn and "mirror_stale(detail, mir[\"total\"])" in fn


def test_private_put_takes_fee_deducted_as_meta_not_money():
    fn = code_only(func_body(repo_src("routers/api_finance_projects.py"), "async def update_project_ledger("))
    assert 'if "fee_deducted" in data:' in fn and 'fd = data.pop("fee_deducted")' in fn
    assert fn.index('data.pop("fee_deducted")') < fn.index("for k, v in data.items():"), "要在 int() 迴圈之前 pop"


def test_financial_summary_only_adds_a_sentence():
    fn = code_only(func_body(repo_src("routers/crm/costs.py"), "async def project_financial_summary("))
    assert '"company_income": _company_income' in fn and '"billing_mode_label"' in fn
    assert 'if _bm == "passthrough" and contract:' in fn
    # 毛利算式不動（owner 第 4 點）
    assert "project_margin(contract, tax_rate, expense_actual, staff_actual)" in fn


def test_frontend_wiring():
    html = repo_src("frontend/tabs/crm/crm-projects.html")
    assert 'id="proj-f-billing_mode"' in html and 'id="proj-f-link-note"' in html
    for v in ("company", "passthrough", "cash"):
        assert f'value="{v}"' in html
    core = js_code_only(repo_src("frontend/tabs/crm/crm-projects-core.js"))
    assert "'billing_mode'" in js_code_only(core), "_FIELDS 沒有它 → 表單存不進去"
    # 後期代開且未連結 → 不問、直接建代開分身；其他照舊彈窗
    push = code_only(core[core.index("window._projPushMine = "):])
    assert "p.billing_mode === 'passthrough'" in push and "{ source: '代開發票' }" in push
    # 前端只畫後端的 text，不自己拼句子
    assert "note.text" in core or "note && note.text" in core
    # 私帳桌機／手機：已扣旗標「缺鍵＝True」一律用 !== false 讀；送出的鍵是 fee_deducted
    for path in ("frontend/tabs/finance/subviews/projects.js", "frontend/m/views/ledger-projects.js"):
        js = js_code_only(repo_src(path))
        assert "det.fee_deducted !== false" in js, path
        assert "fee_deducted" in js and "fee_deducted ===" not in js, path


def test_mirror_check_does_not_nag_passthrough_cases_about_cost_lines():
    """代開的收入是母帳合約額不是成本行，「沒有掛給你的成本行」對它是噪音 —— crmFetch 會把任何
    warning 直接 toast，連結後每次重畫詳情都跳一次。"""
    fn = code_only(func_body(_LINKS, "async def check_project_mirror("))
    assert 'billing_mode_of(p.billing_mode) == "passthrough"' in fn and 'get("source") == "代開發票"' in fn
    assert 'warning = ""' in fn


def test_link_note_html_and_billing_tag_in_node():
    """特徵測試（polish 階段零）：linkNoteHtml 只畫後端的 text、把 mine_name 換成 /my-ledger.html 連結、
    stale===true 才掛「私帳落後」；billingTagHtml 對 company 回空字串。用 node 跑，輸出只印 ASCII。"""
    import subprocess
    from tests.unit._srcscan import _REPO as REPO
    src = js_code_only(repo_src("frontend/tabs/crm/crm-projects-core.js"))
    body = "\n".join(js_func_body(src, f"export function {fn}(").replace("export ", "", 1)
                     for fn in ("billingTagHtml", "linkNoteHtml"))
    consts = src[src.index("export const BILLING_LABELS"):src.index("export function billingTagHtml(")].replace("export ", "")
    script = "const _esc = (s) => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/\"/g,'&quot;');\n" + consts + body + """
const linked = {linked:true, mine_id:'m 1', mine_name:'A<b', stale:true, text:'A<b link → A<b . src . 82,000'};
const h = linkNoteHtml(linked);
const out = [
  billingTagHtml({billing_mode:'company'}) === '',
  billingTagHtml({billing_mode:'passthrough'}).includes(BILLING_LABELS.passthrough),
  billingTagHtml(null) === '',
  h.includes('href="/my-ledger.html?project=m%201"'),
  h.includes('>A&lt;b') && !h.includes('A<b') && h.startsWith('A&lt;b link ') && (h.match(/<a /g) || []).length === 1,
  h.includes('title=') && h.endsWith('</span>'),
  !linkNoteHtml({linked:true, mine_id:'m', mine_name:'x', stale:false, text:'x'}).includes('</span>'),
  !linkNoteHtml({linked:false, text:'no link'}).includes('href'),
  linkNoteHtml(null).includes('&#8212;') || linkNoteHtml(null).length > 0,
];
console.log(JSON.stringify(out));"""
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as tf:
        tf.write(script); path = tf.name
    try:
        r = subprocess.run(["node", path], capture_output=True, text=True, cwd=str(REPO))
    finally:
        os.unlink(path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[true,true,true,true,true,true,true,true,true]", r.stdout
