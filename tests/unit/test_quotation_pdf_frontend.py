# -*- coding: utf-8 -*-
"""報價單 PDF ＋ 規格欄 ＋ 公司資訊設定頁 —— 前端這一側的契約。

後端契約（routers/crm/quotes.py）：報價 dict 多一個 `spec`、`GET /quotations/{id}/pdf` 回 PDF、
settings.json 多 `company` 區塊（config.py 預設值是正本，13 個 key）。這裡釘前端有沒有對上：

- 兩處下載 PDF（報價分頁詳情、專案頁報價子頁）都走 `authDownload(`（帶 Authorization 的 blob 下載，
  js/shared/utils.js 唯一正本）—— `<a href>`／`window.open`／裸 fetch 送不了 token，會直接 401。
- 規格欄：Modal 有 `quote-f-spec`、存檔 payload 帶 `spec`、開啟編輯時回填、詳情有 inline 編輯。
- 範本管理的「+ 新增範本」不再是死按鈕。
- 設定 Modal 有「公司資訊」分頁，settings-modal.js 讀寫的 key 與 config.py 預設值一致。
"""
import re

import pytest

from tests.unit._srcscan import js_code_only, js_func_body, repo_src

QUOTES_JS = "frontend/tabs/crm/crm-quotes.js"
PQ_JS = "frontend/tabs/crm/crm-projects-quotes.js"
UTILS_JS = "frontend/tabs/crm/crm-utils.js"
QUOTE_FILE_JS = "frontend/js/shared/quote-file.js"
M_QUOTES_JS = "frontend/m/views/quotes.js"
M_SHELL_JS = "frontend/m/shell.js"
SETTINGS_JS = "frontend/js/settings/settings-modal.js"

# 與 config.py DEFAULT_SETTINGS["company"] 同一份 key 清單
COMPANY_KEYS = ["name", "name_en", "tax_id", "address", "phone", "email", "bank",
                "account_name", "account_no", "quote_valid_days", "delivery_terms",
                "logo_path", "seal_path"]


# ── PDF 下載 ─────────────────────────────────────────────────

@pytest.mark.parametrize("rel", [QUOTES_JS, PQ_JS])
def test_pdf_download_goes_through_auth_download(rel):
    src = js_code_only(repo_src(rel))
    assert "/pdf'" in src, f"{rel} 沒有打 /pdf 端點"
    assert "authDownload(" in src, f"{rel} 沒用 authDownload"
    assert "from '../../js/shared/utils.js'" in src, f"{rel} 的 authDownload 要從 js/shared/utils.js import"
    # 下載那一行本身要是 authDownload（不是 window.open / fetch 自己拼）
    pdf_lines = [ln for ln in src.splitlines() if "/pdf'" in ln]
    assert pdf_lines and all("authDownload(" in ln for ln in pdf_lines), pdf_lines
    assert "window.open(" not in src


def test_pdf_filename_is_one_helper_shared_by_all_pages():
    """檔名正本只有 js/shared/quote-file.js 一份：桌機兩頁經 crm-utils re-export、手機經 shell.js re-export。"""
    shared = js_code_only(repo_src(QUOTE_FILE_JS))
    assert "import" not in shared, "quote-file.js 要是零 import 的葉節點（手機 shell 才能吃）"
    fn = js_func_body(shared, "export function quotePdfFilename(")
    assert "源日報價單" in fn and ".pdf'" in fn
    assert "quote_date" in fn and "today()" in fn                 # 日期取 quote_date、沒有就今天
    assert re.search(r'replace\(/\[\\\\/:\*\?"<>\|\]/g', fn), "檔名禁字要換掉"
    assert "export { quotePdfFilename } from '../../js/shared/quote-file.js'" in js_code_only(repo_src(UTILS_JS))
    assert "export { quotePdfFilename } from '/js/shared/quote-file.js'" in js_code_only(repo_src(M_SHELL_JS))
    for rel in (QUOTES_JS, PQ_JS, M_QUOTES_JS, UTILS_JS, M_SHELL_JS):
        src = js_code_only(repo_src(rel))
        assert "quotePdfFilename" in src, rel
        assert "源日報價單" not in src, f"{rel} 自己再拼一份檔名？正本在 js/shared/quote-file.js"


def test_mobile_quotes_download_pdf_through_shell():
    """手機版：卡片有 PDF 鈕，下載走 shell.js 的 mdownload（帶 token、401 導回登入），不自己 fetch。"""
    src = js_code_only(repo_src(M_QUOTES_JS))
    assert 'data-pdf="' in src and "button[data-pdf]" in src
    pdf_lines = [ln for ln in src.splitlines() if "/pdf`" in ln]
    assert pdf_lines and all("mdownload(" in ln and "quotePdfFilename(" in ln for ln in pdf_lines), pdf_lines
    assert "from '../shell.js'" in src and "fetch(" not in src.replace("mfetch(", "")
    shell = js_code_only(repo_src(M_SHELL_JS))
    body = js_func_body(shell, "export async function mdownload(")
    assert "Authorization" in body and "401" in body and "createObjectURL" in body


def test_project_page_pdf_button_is_wired():
    src = js_code_only(repo_src(PQ_JS))
    assert "window._pqPdf(" in src                     # onclick
    assert "window._pqPdf = " in src                   # 定義


def test_quotes_page_pdf_button_has_no_emoji_and_is_plain_text():
    src = repo_src(QUOTES_JS)
    assert ">下載 PDF</button>" in src


# ── 規格欄 ───────────────────────────────────────────────────

def test_spec_field_round_trips():
    html = repo_src("frontend/tabs/crm/crm-quotes.html")
    assert 'id="quote-f-spec"' in html
    src = js_code_only(repo_src(QUOTES_JS))
    save = js_func_body(src, "function _buildPayload(")   # 按儲存與自動存草稿共用的 payload
    assert "spec:" in save and "'quote-f-spec'" in save
    open_ = js_func_body(src, "async function openModal(")
    assert "'quote-f-spec'" in open_ and "q.spec" in open_
    detail = js_func_body(src, "function renderDetail(")
    assert "q.spec" in detail


# ── 範本「+ 新增範本」不再是死按鈕 ─────────────────────────────

def test_template_add_button_is_bound():
    src = js_code_only(repo_src(QUOTES_JS))
    init = js_func_body(src, "export async function initCrmQuotesTab(")
    assert "getElementById('quote-tpl-btn-add').addEventListener('click'" in init
    assert "getElementById('quote-btn-as-template').addEventListener('click'" in init
    # 兩個入口分開：範本彈窗建空範本、報價彈窗存目前表單（別再用「表單開著嗎」判斷——那條走不到）
    assert "_addTemplate = () => _promptTemplate(" in src and "items: []" in src
    assert "_addTemplateFromForm = () => _promptTemplate(_saveCurrentAsTemplate)" in src
    assert "style.display === 'flex'" not in src


# ── 設定頁「公司資訊」 ─────────────────────────────────────────

def test_settings_modal_has_company_tab():
    html = repo_src("frontend/index.html")
    assert 'id="tab_company"' in html
    assert "switchSettingsTab('company', event)" in html
    for k in COMPANY_KEYS:
        assert f'id="company_{k}"' in html, k
    assert 'type="number" id="company_quote_valid_days"' in html
    assert 'id="quote-btn-as-template"' in repo_src("frontend/tabs/crm/crm-quotes.html")


def test_settings_modal_reads_and_writes_all_company_keys():
    src = js_code_only(repo_src(SETTINGS_JS))
    keys = js_func_body(src, "const COMPANY_KEYS = [")
    for k in COMPANY_KEYS:
        assert f"'{k}'" in keys, k
    assert keys.count("'") == 2 * len(COMPANY_KEYS), "COMPANY_KEYS 與 config.py 的 13 個 key 要一樣多"
    assert "fillCompany(data.company)" in src                         # 載入回填
    # 儲存：從報價頁開的 company-only 只送 company（後端 /api/settings/save 依頂層鍵分流給 crm_quotes／crm_invoices）；
    # 從頭像開的完整設定把 company 併進整包（2026-09-08 權限稽核第二批）
    save = js_func_body(src, "const settingsData = companyOnly ?")
    assert "companyOnly ? { company: readCompany() } :" in save
    assert "...(readCompany() ? { company: readCompany() } : {})" in save
    assert "parseInt(v) || 14" in js_func_body(src, "function readCompany(")


def test_company_keys_match_config_defaults():
    cfg = repo_src("config.py")
    block = cfg[cfg.index('"company": {'):]
    block = block[:block.index("\n    },")]
    assert set(re.findall(r'^\s+"(\w+)":', block, re.M)) == set(COMPANY_KEYS)


def test_desktop_detail_has_share_link_button():
    src = js_code_only(repo_src(QUOTES_JS))
    assert 'id="quote-btn-share"' in src and "'/quotations/' + q.id + '/share', { method: 'POST' }" in src
    assert "location.origin + q.share_url" in src


def test_pdf_and_share_buttons_appear_only_after_sending():
    """owner 2026-09-07「送出再產生連結與 pdf 按鈕」：草稿（狀態清單第一個）不給 PDF／連結，寄出後才出現；桌機與手機同一條規則。"""
    from tests.unit._srcscan import js_code_only, js_func_body
    m = js_code_only(js_func_body(repo_src("frontend/m/views/quotes.js"), "function cardHtml(q)"))
    assert "const sent = q.status !== list('quote_statuses')[0];" in m
    assert "(sent ? `<button type=\"button\" class=\"m-btn sm\" style=\"${BTN}\" data-pdf=" in m
    assert "(sent && (q.share_url || isAdmin())" in m
    d = js_code_only(repo_src("frontend/tabs/crm/crm-quotes.js"))
    assert "const sent = q.status !== _QUOTE_STATUSES[0];" in d
    assert "(sent ? `<button class=\"crm-btn crm-btn-secondary crm-btn-sm\" id=\"quote-btn-pdf\">" in d
    assert "actions.querySelector('#quote-btn-pdf')?.addEventListener" in d and "actions.querySelector('#quote-btn-share')?.addEventListener" in d
