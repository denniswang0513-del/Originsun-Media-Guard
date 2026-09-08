# -*- coding: utf-8 -*-
"""權限稽核第二批（docs/RBAC_AUDIT.md §3.2、§3.3）——設定分流與手機三把鑰匙。

① `POST /api/settings/save` 依 payload 頂層鍵分流（`routers.api_system._SETTINGS_SAVE_KEYS`）：
   單獨的 staff_roles→crm_staff、company→crm_quotes／crm_invoices、finance→crm_invoices、
   concurrency／nas_paths→projects；其他鍵或跨組混包＝管理員。非管理員只寫得進那組鍵
   （nas_paths 只留 agents_dir）。純函式 `_settings_save_guard_keys` 各分支 + 端點層（save_settings 換成錄音器，
   **不碰磁碟上的 settings.json**——app_client 是真的 app，不換掉會寫進 dev 的設定檔）。
② 右上角「系統設定」「重新啟動 Agent」只在 Lv3 區塊；`_restartAgent` 看 403。
③ 報表歷史每筆 ✕ 只在 `window._isAdmin` 畫。
④ 手機 `/crm/m/options` 的 me 多 `can_invoice`（crm_invoices＋money_view）、`can_expense`（crm_projects）；
   `/m/home|projects|projects/{id}` 要 crm_projects，`/m/options` 登入即可。
⑤ 手機表單：發票／付款掛 `.wi`（body.no-invoice）、雜支掛 `.we`（body.no-expense）、報價狀態鈕掛 `.w`。
"""
import contextlib

import pytest

from core.auth import create_token
from routers.api_system import _SETTINGS_SAVE_KEYS, _settings_save_guard_keys, _settings_save_restrict
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src

MOBILE = "routers/api_crm_mobile.py"
SAVE = "/api/settings/save"


# ── ① 純函式 ──────────────────────────────────────────────

@pytest.mark.parametrize("keys,expected", [
    (["staff_roles"], ("crm_staff",)),
    (["company"], ("crm_quotes", "crm_invoices")),
    (["finance"], ("crm_invoices",)),
    (["concurrency"], ("projects",)),
    (["nas_paths"], ("projects",)),
    (["concurrency", "nas_paths"], ("projects",)),      # 同一組鑰匙可以同包
    (["notifications"], None),                          # 表外＝管理員
    (["timesheet"], None),
    (["jwt_secret"], None),
    (["staff_roles", "company"], None),                 # 跨組混包＝管理員
    (["company", "notifications"], None),               # 夾帶表外鍵＝管理員
    ([], None),                                         # 空包＝管理員
])
def test_settings_save_guard_keys(keys, expected):
    assert _settings_save_guard_keys(keys) == expected


def test_settings_save_table_matches_the_audit():
    """docs/RBAC_AUDIT.md §3.2 的分流表逐鍵釘住——改表要改文件。"""
    assert _SETTINGS_SAVE_KEYS == {
        "staff_roles": ("crm_staff",),
        "company": ("crm_quotes", "crm_invoices"),
        "finance": ("crm_invoices",),
        "concurrency": ("projects",),
        "nas_paths": ("projects",),
    }


def test_settings_save_restrict_drops_foreign_keys_and_nas_subkeys():
    """非管理員的包：表外鍵丟掉；nas_paths 只留 agents_dir（ota_dir／web_report_dir 是 OTA 與報表出口）。"""
    body = {"nas_paths": {"agents_dir": r"\\nas\agents", "ota_dir": "evil"},
            "concurrency": {"backup": 2}, "jwt_secret": "z"}
    assert _settings_save_restrict(body, ("projects",)) == {
        "nas_paths": {"agents_dir": r"\\nas\agents"}, "concurrency": {"backup": 2}}
    assert _settings_save_restrict({"company": {"name": "x"}}, ("crm_staff",)) == {}


# ── ① 端點 ────────────────────────────────────────────────

@pytest.fixture
def captured_save(monkeypatch):
    """把 save_settings 換成錄音器：驗「寫進去的是什麼」，也保證測試不碰 settings.json。"""
    calls = []
    monkeypatch.setattr("routers.api_system.save_settings", lambda d: calls.append(d))
    return calls


@pytest.fixture
def admin_hdr():
    return {"Authorization": "Bearer " + create_token({"sub": "adm", "username": "adm", "access_level": 3, "modules": []})}


def test_save_settings_anonymous_is_401(app_client, captured_save):
    assert app_client.post(SAVE, json={"staff_roles": ["a"]}).status_code == 401
    assert app_client.post(SAVE, json={"notifications": {}}).status_code == 401
    assert captured_save == []


@pytest.mark.parametrize("body,module", [
    ({"staff_roles": ["攝影師"]}, "crm_staff"),
    ({"company": {"name": "x"}}, "crm_quotes"),
    ({"company": {"name": "x"}}, "crm_invoices"),
    ({"finance": {"monthly_fixed_costs": 1}}, "crm_invoices"),
    ({"concurrency": {"backup": 2}}, "projects"),
    ({"nas_paths": {"agents_dir": r"\\nas\agents"}}, "projects"),
    ({"concurrency": {"backup": 2}, "nas_paths": {"agents_dir": "x"}}, "projects"),
])
def test_module_user_can_save_its_own_key(app_client, as_user, captured_save, body, module):
    r = app_client.post(SAVE, json=body, headers=as_user(modules=[module]))
    assert r.status_code == 200, r.text
    assert captured_save == [body]


@pytest.mark.parametrize("body,module", [
    ({"company": {"name": "x"}}, "crm_staff"),          # 拿別組的鑰匙
    ({"staff_roles": ["a"]}, "projects"),
    ({"notifications": {"google_chat_webhook": "h"}}, "crm_staff"),   # 表外鍵
    ({"timesheet": {"ingest_token": "t"}}, "projects"),
    ({"staff_roles": ["a"], "company": {"name": "x"}}, "crm_staff"),  # 混包
])
def test_module_user_cannot_save_other_keys(app_client, as_user, captured_save, body, module):
    assert app_client.post(SAVE, json=body, headers=as_user(modules=[module])).status_code == 403
    assert captured_save == []


def test_mixed_payload_needs_admin_even_with_both_modules(app_client, as_user, admin_hdr, captured_save):
    body = {"staff_roles": ["a"], "company": {"name": "x"}}
    assert app_client.post(SAVE, json=body, headers=as_user(modules=["crm_staff", "crm_quotes"])).status_code == 403
    assert captured_save == []
    assert app_client.post(SAVE, json=body, headers=admin_hdr).status_code == 200
    assert captured_save == [body]


def test_non_admin_nas_paths_is_filtered_to_agents_dir(app_client, as_user, captured_save):
    body = {"nas_paths": {"agents_dir": "a", "ota_dir": "evil", "web_report_dir": "evil"}}
    assert app_client.post(SAVE, json=body, headers=as_user(modules=["projects"])).status_code == 200
    assert captured_save == [{"nas_paths": {"agents_dir": "a"}}]


def test_admin_payload_is_not_filtered(app_client, admin_hdr, captured_save):
    body = {"nas_paths": {"agents_dir": "a", "ota_dir": "o"}, "notifications": {"google_chat_webhook": "h"}}
    assert app_client.post(SAVE, json=body, headers=admin_hdr).status_code == 200
    assert captured_save == [body]


def test_non_object_body_is_400_for_admin_and_401_for_anonymous(app_client, admin_hdr, captured_save):
    assert app_client.post(SAVE, json=[1, 2], headers=admin_hdr).status_code == 400
    assert app_client.post(SAVE, json=[1, 2]).status_code == 401
    assert captured_save == []


# ── ① 前端呼叫端：單一頂層鍵、看回應 ─────────────────────

def test_settings_callers_send_one_top_level_key_and_report_403():
    staff = js_code_only(repo_src("frontend/tabs/crm/crm-staff.js"))
    fn = js_func_body(staff, "async function _saveRoles(next) {")
    assert "saveSettings({ staff_roles: next })" in fn
    assert "r.status === 403" in fn and "crm_staff" in fn
    assert "_roles = next;" in fn, "存成功才換掉 _roles（失敗不能留下已改的清單）"

    cash = js_code_only(repo_src("frontend/tabs/crm/crm-cashflow.js"))
    assert "JSON.stringify({ finance: { monthly_fixed_costs: v } })" in cash
    assert "r.status === 403" in cash and "crm_invoices" in cash

    pj = js_code_only(repo_src("frontend/tabs/projects/projects.js"))
    assert "JSON.stringify({ concurrency: { [key]: numVal } })" in pj
    assert "JSON.stringify({ nas_paths: { agents_dir: dir } })" in pj
    assert "_settingsSaved(res, '系統參數')" in pj and "_settingsSaved(res, 'NAS 機器清單路徑')" in pj
    helper = js_func_body(pj, "function _settingsSaved(res, what) {")
    assert "res.status === 403" in helper and "projects" in helper
    assert "/* silent */" not in js_func_body(pj, "async function saveLimits(key, val) {")

    sm = js_code_only(repo_src("frontend/js/settings/settings-modal.js"))
    assert "const companyOnly = modal.classList.contains('company-only');" in sm
    assert "companyOnly ? { company: readCompany() } :" in sm, "從報價頁開的公司資訊只送 company 一個頂層鍵"
    assert "response.status === 401 || response.status === 403" in sm
    assert "請檢查伺服器連線" not in sm

    quotes = js_code_only(repo_src("frontend/tabs/crm/crm-quotes.js"))
    assert "hasModule('crm_quotes') || hasModule('crm_invoices')" in quotes, \
        "公司資訊入口跟後端分流一致：管理員或 crm_quotes／crm_invoices"


# ── ② 右上角選單 ──────────────────────────────────────────

def test_settings_and_restart_menu_items_only_in_admin_block():
    js = js_code_only(repo_src("frontend/js/auth/auth-state.js"))
    fn = js_func_body(js, "window._authToggle = function() {")
    for label in ("'系統設定'", "'重新啟動 Agent'"):
        assert fn.count(label) == 1, f"{label} 只能畫一次（管理員區塊）"
        assert fn.index("if (window._accessLevel >= 3) {") < fn.index(label) < fn.index("'使用者管理'")
    anon = fn[fn.index("} else {"):]
    assert "'系統設定'" not in anon and "'重新啟動 Agent'" not in anon, "未登入的選單不畫這兩項"


def test_restart_agent_reports_admin_required():
    js = js_code_only(repo_src("frontend/js/auth/auth-state.js"))
    fn = js_func_body(js, "window._restartAgent = async function() {")
    assert "res = await fetch('/api/admin/restart', { method: 'POST' });" in fn
    assert "res.status === 401 || res.status === 403" in fn and "需要管理員" in fn
    assert fn.index("需要管理員") < fn.index("正在重新啟動中"), "被擋要先說，不能讓人白等 15 秒"


# ── ③ 報表歷史 ✕ ──────────────────────────────────────────

def test_report_delete_button_only_drawn_for_admins():
    js = js_code_only(repo_src("frontend/js/shared/report-history.js"))
    fn = js_func_body(js, "export async function loadReportHistory() {")
    assert fn.count("deleteReport(") == 1
    assert "${window._isAdmin ? `<button onclick=\"deleteReport(" in fn


# ── ④ 手機 /options 旗標與讀取守衛 ───────────────────────

class _FakeResult:
    def all(self):
        return []

    def first(self):
        return None

    def scalar(self):
        return 0

    def scalars(self):
        return self


class _FakeSession:
    async def execute(self, *_a, **_k):
        return _FakeResult()


@pytest.fixture
def fake_mobile_db(monkeypatch):
    """/options 會查客戶與品項；單元環境沒有 DB → 換成空結果，讓 me 那段真的算出來。"""
    @contextlib.asynccontextmanager
    async def _sess():
        yield _FakeSession()

    async def _applicants(_request):
        return {"applicants": []}
    monkeypatch.setattr("routers.api_crm_mobile._crm_session", _sess)
    monkeypatch.setattr("routers.api_crm_mobile.get_invoice_applicants", _applicants)


@pytest.mark.parametrize("claims,expect", [
    ({"access_level": 3}, (True, True, True)),                        # Lv3 三把恆 true
    ({"modules": ["crm_invoices"]}, (False, False, False)),           # 少 money_view 不能開發票
    ({"modules": ["money_view"]}, (False, False, False)),
    ({"modules": ["crm_invoices", "money_view"]}, (False, True, False)),
    ({"modules": ["crm_projects"]}, (False, False, True)),
    ({"modules": ["crm_projects", "crm_invoices", "money_view"]}, (False, True, True)),   # 加備註仍 Lv3
    ({"modules": ["me_profile"]}, (False, False, False)),
])
def test_mobile_options_write_flags(app_client, as_user, fake_mobile_db, claims, expect):
    r = app_client.get("/api/v1/crm/m/options", headers=as_user(**claims))
    assert r.status_code == 200, r.text
    me = r.json()["me"]
    assert (me["can_write"], me["can_invoice"], me["can_expense"]) == expect


def test_mobile_options_flags_ask_the_same_rule_as_the_guards():
    body = code_only(func_body(repo_src(MOBILE), "async def mobile_options("))
    assert '"can_invoice": payload_grants(payload, "crm_invoices") and payload_grants(payload, "money_view")' in body
    assert '"can_expense": payload_grants(payload, "crm_projects")' in body
    assert "_check_read(" not in body, "/options 登入即可（殼靠它判斷自己能做什麼）"


def test_mobile_options_stays_login_only(app_client, as_user, fake_mobile_db):
    assert app_client.get("/api/v1/crm/m/options").status_code == 401
    assert app_client.get("/api/v1/crm/m/options", headers=as_user(modules=["me_profile"])).status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/crm/m/home", "/api/v1/crm/m/projects",
                                  "/api/v1/crm/m/projects/__probe__"])
def test_mobile_reads_need_crm_projects(app_client, as_user, path):
    """讀取端點：匿名 401、只有別的模組 403、crm_projects／Lv3 不被權限擋（單元環境沒 DB → 503/404 都算過）。"""
    assert app_client.get(path).status_code == 401
    assert app_client.get(path, headers=as_user(modules=["me_profile", "backup"])).status_code == 403
    for hdr in (as_user(modules=["crm_projects"]), as_user(access_level=3)):
        r = app_client.get(path, headers=hdr)
        assert r.status_code not in (401, 403), f"{path} 被權限擋掉：{r.status_code}"


def test_mobile_read_guard_is_one_helper():
    src = repo_src(MOBILE)
    code = code_only(src)
    assert 'MOBILE_READ_MODULES: tuple[str, ...] = ("crm_projects",)' in code
    assert "_check_read = _module_guard(*MOBILE_READ_MODULES)" in code
    for header in ("async def mobile_home(", "async def mobile_projects(", "async def mobile_project_detail("):
        assert "_check_read(request)" in code_only(func_body(src, header)), header


# ── ⑤ 手機表單三把鑰匙的 class ────────────────────────────

def test_mobile_forms_use_per_key_write_classes():
    html = repo_src("frontend/m/crm.html")
    assert "body.no-write .w { display:none !important; }" in html
    assert "body.no-invoice .wi { display:none !important; }" in html
    assert "body.no-expense .we { display:none !important; }" in html

    crm = js_code_only(repo_src("frontend/m/crm.js"))
    for line in ("state.canInvoice = !!meOpt.can_invoice;", "state.canExpense = !!meOpt.can_expense;",
                 "document.body.classList.toggle('no-invoice', !state.canInvoice);",
                 "document.body.classList.toggle('no-expense', !state.canExpense);"):
        assert line in crm, line

    inv = js_code_only(repo_src("frontend/m/views/invoice.js"))
    assert 'class="m-form m-card wi" id="inv-form"' in inv
    assert "state.canWrite" not in inv, "發票頁只看 canInvoice"
    assert 'class="m-btn wi" data-edit=' in inv and 'class="m-btn wi" data-restore=' in inv

    pay = js_code_only(repo_src("frontend/m/views/payments.js"))
    assert 'class="m-btn sm danger wi" data-unpay=' in pay and 'class="m-btn sm pri wi" data-pay=' in pay

    exp = js_code_only(repo_src("frontend/m/views/expense.js"))
    assert 'class="m-form m-card we" id="exp-form"' in exp and 'class="m-btn pri we" id="exp-submit"' in exp
    assert "state.canExpense" in exp

    q = js_code_only(repo_src("frontend/m/views/quotes.js"))
    assert 'class="m-btn sm w ${t.danger' in q, "建立／成案／拒絕（_check_write）要掛 .w"
    assert 'class="m-actions w"' not in q, "整列不藏：預覽／PDF／複製連結是讀，只能看的人也要看得到"
