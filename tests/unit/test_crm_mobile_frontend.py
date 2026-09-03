# -*- coding: utf-8 -*-
"""CRM 手機版（frontend/m/，docs/CRM_MOBILE_PLAN.md）的兩個坑釘住：

1. **殼只有一份**：登入／fetch／日期只准住 shell.js；分頁模組只 import 它。
   五個獨立手機頁各抄一份登入已經漂過兩次。
2. **不寫死字彙**：狀態／案型／款項／發票種類全部從 GET /crm/m/options 拿。
   /invoice.html 寫死 payment_status 的下場：桌機改名後手機送出的票落在沒人認得的狀態。
"""
import pathlib
import re

from core.public_assets import MODULE_DIRS
from tests.unit._srcscan import _REPO, code_only, js_code_only, repo_src

M = pathlib.Path(_REPO) / "frontend" / "m"
SHELL = repo_src("frontend/m/shell.js")
CRM_JS = repo_src("frontend/m/crm.js")
CRM_HTML = repo_src("frontend/m/crm.html")


def _page_modules():
    """crm.js 與它底下的分頁模組（殼本身除外）。"""
    return sorted(p for p in M.rglob("*.js") if p.name != "shell.js")


# ── 1. 殼只有一份 ─────────────────────────────────────────

def test_crm_imports_the_one_shell():
    assert re.search(r"from\s+['\"]\./shell\.js['\"]", CRM_JS), "crm.js 必須 import ./shell.js"


def test_login_form_lives_only_in_shell():
    """`/auth/login` 只准出現在 shell.js —— 第二份登入表單就是第二個殼。"""
    assert "/api/v1/auth/login" in SHELL
    for p in _page_modules():
        src = js_code_only(p.read_text(encoding="utf-8"))
        assert "/auth/login" not in src, f"{p.name} 長出自己的登入路徑"
        assert "auth/google" not in src, f"{p.name} 長出自己的 Google 登入"


def test_shell_uses_local_date_not_utc():
    body = js_code_only(SHELL)
    assert "export function todayLocal" in body
    assert "toISOString().slice(0, 10)" not in body and "toISOString().slice(0,10)" not in body, \
        "toISOString 是 UTC：台北早上 8 點前會寫成昨天"
    for p in _page_modules():
        assert "toISOString" not in js_code_only(p.read_text(encoding="utf-8")), \
            f"{p.name} 自己算日期，改用 shell.todayLocal()"


def test_shell_refreshes_token_silently():
    assert "export async function refreshTokenIfNeeded" in SHELL
    assert "/api/v1/auth/refresh" in SHELL


def test_shell_borrows_shared_leaves_instead_of_copying():
    """esc 只有 dom.js 一份；GIS 登入按鈕只有 js/shared/google-signin.js 一份
    （SPA 的 js/auth/google-oauth.js 與手機殼都只是呼叫端）。"""
    body = js_code_only(SHELL)
    assert "import { esc } from '/js/shared/dom.js'" in body
    assert "export const esc" not in body and "export function esc" not in body
    assert "initGoogleSignIn(" in body and "renderButton" not in body
    oauth = js_code_only(repo_src("frontend/js/auth/google-oauth.js"))
    assert "initGoogleSignIn(" in oauth and "renderButton" not in oauth, "SPA 殼又抄了一份 GIS"
    leaf = js_code_only(repo_src("frontend/js/shared/google-signin.js"))
    assert "renderButton" in leaf and "import" not in leaf, "google-signin.js 必須是零 import 的葉節點"


def test_invoice_amounts_come_from_the_shared_leaf():
    src = js_code_only(repo_src("frontend/m/views/invoice.js"))
    assert "from '/js/shared/invoice-amounts.js'" in src
    assert "1.05" not in src and "taxRate" not in src, "手機頁自己算稅率"


# ── 2. 不寫死字彙 ─────────────────────────────────────────

VOCAB = ("未收款", "已收款", "已撥款", "草稿", "已寄送", "已簽核", "電子發票", "已付款", "應付款")


def test_page_modules_have_no_hardcoded_vocab():
    """按鈕文字與狀態字都得從 options 推；出現任何一個狀態字＝又抄了一份字彙。"""
    for p in _page_modules():
        src = js_code_only(p.read_text(encoding="utf-8"))
        hit = [v for v in VOCAB if v in src]
        assert not hit, f"{p.relative_to(M)} 寫死了字彙 {hit}"


def test_invoice_is_a_request_to_issue():
    """owner 2026-09-03：手機登記的一定是請款發票、還沒開——方向與狀態從 options 取（第一個方向、
    第二個狀態），不送發票號碼（後端給未開立）；送出後有可複製的通知；專案必選。"""
    src = js_code_only(repo_src("frontend/m/views/invoice.js"))
    assert "payment_type: receivableType()" in src and "payment_status: unpaidStatus()" in src
    assert "invoice_number: ''" in src and "applicant: F('applicant').value" in src
    assert "showNotice(noticeText(body))" in src and "copyText(" in src
    assert "if (!body.project_id) { toast(" in src
    # 紙本發票要收件人／電話／地址（同桌機發票本）；哪一種是紙本由 options.invoice.paper_kind 說
    assert "paper_kind" in src and "recipient_address" in src and "'inv-paper'" in src
    api = code_only(repo_src("routers/api_crm_mobile.py"))
    assert "get_invoice_applicants(request)" in api and "INVOICE_PASSTHROUGH_CATEGORIES" in api


def test_invoice_form_lets_the_server_decide_status():
    """發票狀態的字不在手機端：款項狀態下拉吃 /options.invoice.statuses_by_type（後端規則算的），
    issue_status 只准送空字串（後端看發票號碼）。"""
    src = js_code_only(repo_src("frontend/m/views/invoice.js"))
    assert "/api/v1/crm/invoices" in src
    assert "statuses_by_type" in src
    assert "issue_status: ''" in src, "issue_status 只准送空字串，不准自己決定（後端看發票號碼）"
    api = code_only(repo_src("routers/api_crm_mobile.py"))
    assert "initial_invoice_status(pt, unpaid=True)" in api      # 選項由規則算，不是另一份手抄


def test_long_lists_are_typeable_pickers():
    """owner 2026-09-03「所有的搜尋都要可以打字搜尋」：專案（發票）與客戶（新案子）這兩個上百筆的清單
    走 ui.mountPicker（hidden input＋搜尋框＋結果列），不是原生 <select>。"""
    ui = js_code_only(repo_src("frontend/m/ui.js"))
    assert "export function mountPicker(" in ui and "onpointerdown" in ui     # blur 先於 click，選取要用 pointerdown
    inv = js_code_only(repo_src("frontend/m/views/invoice.js"))
    assert "pickerHtml('inv-project_id')" in inv and "mountPicker('inv-project_id'" in inv
    assert '<select id="inv-project_id"' not in inv
    pj = js_code_only(repo_src("frontend/m/views/projects.js"))
    assert "pickerHtml('np-client')" in pj and "mountPicker('np-client'" in pj
    assert '<select id="np-client"' not in pj


def test_options_is_fetched_once_at_boot():
    assert "/api/v1/crm/m/options" in CRM_JS
    for p in _page_modules():
        if p.name == "crm.js":
            continue
        assert "/crm/m/options" not in js_code_only(p.read_text(encoding="utf-8")), \
            f"{p.name} 自己再打一次 options —— 用 ui.state.options"


# ── 3. 頁面殼與分頁 ───────────────────────────────────────

def test_html_meta_for_phone():
    vp = re.search(r'<meta name="viewport" content="([^"]+)"', CRM_HTML).group(1)
    assert "maximum-scale=1" in vp and "width=device-width" in vp
    assert 'name="robots" content="noindex,nofollow"' in CRM_HTML
    assert 'rel="manifest" href="/m/manifest.webmanifest"' in CRM_HTML
    assert 'rel="apple-touch-icon" href="/m/icon-180.png"' in CRM_HTML
    assert 'name="theme-color"' in CRM_HTML
    for f in ("manifest.webmanifest", "icon-180.png", "icon-192.png", "icon-512.png"):
        assert (M / f).is_file(), f"少了 {f}"


def test_five_tabs_and_invoice_is_the_landing_tab():
    """owner 2026-09-03：發票與零用金最常用，排最前面；發票是預設落地分頁。"""
    ui = repo_src("frontend/m/ui.js")
    tabs = re.search(r"export const TABS = \[(.*?)\]", ui).group(1)
    ids = re.findall(r"'(\w+)'", tabs)
    assert ids == ["invoice", "petty", "projects", "quotes", "payments"], ids
    assert "export const DEFAULT_TAB = 'invoice'" in ui
    html_tabs = re.findall(r'data-tab="(\w+)"', CRM_HTML)
    assert html_tabs == ids, "tab bar 的順序要跟 TABS 一致"
    assert "new" not in ids, "新增案子改成專案分頁頂端的抽屜，不是分頁"


def test_recent_invoices_are_sliced_by_the_backend():
    """手機「最近 10 張」交給 list_invoices 的 limit／order，不在瀏覽器整批撈再切
    （後端簽章那半邊在 test_crm_mobile.py）。"""
    js = repo_src("frontend/m/views/invoice.js")
    assert "/api/v1/crm/invoices?limit=10&order=recent" in js
    assert ".slice(0, 10)" not in js


def test_petty_tab_mounts_the_existing_module():
    """零用金不另做表單：掛 tabs/petty/petty-view.js 的 renderMine（同 /petty-cash.html）。"""
    src = repo_src("frontend/m/views/petty.js")
    assert "/tabs/petty/petty-view.js" in src and "renderMine" in src
    for p in _page_modules():
        assert "/api/v1/crm/petty/" not in p.read_text(encoding="utf-8"), \
            f"{p.name} 自己打零用金端點 —— 那是 petty-view.js 的事"
    assert "#petty {" in CRM_HTML and "--ink:" in CRM_HTML, "petty-view 的 CSS 變數要給深色值"


def test_m_is_not_on_the_public_surface():
    """手機 CRM 只掛 master / foundry；NAS 對外容器沒有 CRM 後端。"""
    assert not any(d.startswith("m") for d in MODULE_DIRS)
    assert "m" not in MODULE_DIRS and "frontend/m" not in MODULE_DIRS


def test_no_emoji_in_ui_text():
    """UI 無 emoji 鐵則（feedback_ui_no_emoji）：按鈕／卡片／pill／通知一律純文字。"""
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✅❌]")
    for p in M.rglob("*.js"):
        assert not emoji.search(js_code_only(p.read_text(encoding="utf-8"))), f"{p.name} 的程式碼含 emoji"
    html = re.sub(r"<!--.*?-->", "", CRM_HTML, flags=re.S)
    assert not emoji.search(html), "crm.html 含 emoji"


# ── 4. 舊頁退場 ───────────────────────────────────────────

def test_old_invoice_page_redirects_first():
    """/invoice.html 只剩轉址殼（meta refresh ＋ location.replace ＋ 純連結後備），沒有表單。"""
    src = repo_src("frontend/invoice.html")
    script = src.split("<script>", 1)[1]
    first = next(l for l in script.splitlines() if l.strip())
    assert first.strip().startswith("location.replace('/m/crm.html#invoice')"), first
    assert 'content="0;url=/m/crm.html#invoice"' in src
    assert 'href="/m/crm.html#invoice"' in src
    assert "<form" not in src and "fetch(" not in src, "舊表單要整個退場，不是留一半"
