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


def test_pdf_filename_is_one_helper_shared_by_both_pages():
    utils = js_code_only(repo_src(UTILS_JS))
    fn = js_func_body(utils, "export function quotePdfFilename(")
    assert "源日報價單" in fn and ".pdf'" in fn
    assert "quote_date" in fn and "today()" in fn                 # 日期取 quote_date、沒有就今天
    assert re.search(r'replace\(/\[\\\\/:\*\?"<>\|\]/g', fn), "檔名禁字要換掉"
    for rel in (QUOTES_JS, PQ_JS):
        src = js_code_only(repo_src(rel))
        assert "quotePdfFilename(" in src, rel
        assert "源日報價單" not in src, f"{rel} 自己再拼一份檔名？正本在 crm-utils.quotePdfFilename"


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
    save = js_func_body(src, "async function saveQuotation(")
    assert "spec:" in save and "'quote-f-spec'" in save
    open_ = js_func_body(src, "async function openModal(")
    assert "'quote-f-spec'" in open_ and "q.spec" in open_
    fields = js_func_body(src, "const _QUOTE_EDIT_FIELDS = [")
    assert "name:'spec'" in fields
    detail = js_func_body(src, "function renderDetail(")
    assert "q.spec" in detail


# ── 範本「+ 新增範本」不再是死按鈕 ─────────────────────────────

def test_template_add_button_is_bound():
    src = js_code_only(repo_src(QUOTES_JS))
    init = js_func_body(src, "export async function initCrmQuotesTab(")
    assert "getElementById('quote-tpl-btn-add').addEventListener('click'" in init
    add = js_func_body(src, "async function _addTemplate(")
    assert "_saveCurrentAsTemplate(" in add and "items: []" in add   # 表單開著存表單、否則建空範本


# ── 設定頁「公司資訊」 ─────────────────────────────────────────

def test_settings_modal_has_company_tab():
    html = repo_src("frontend/index.html")
    assert 'id="tab_company"' in html
    assert "switchSettingsTab('company', event)" in html
    for k in COMPANY_KEYS:
        assert f'id="company_{k}"' in html, k
    assert 'type="number" id="company_quote_valid_days"' in html


def test_settings_modal_reads_and_writes_all_company_keys():
    src = js_code_only(repo_src(SETTINGS_JS))
    keys = js_func_body(src, "const COMPANY_KEYS = [")
    for k in COMPANY_KEYS:
        assert f"'{k}'" in keys, k
    assert keys.count("'") == 2 * len(COMPANY_KEYS), "COMPANY_KEYS 與 config.py 的 13 個 key 要一樣多"
    assert "fillCompany(data.company)" in src                         # 載入回填
    assert "company: readCompany()" in js_func_body(src, "const settingsData = {")   # 儲存併進整包
    assert "parseInt(v) || 14" in js_func_body(src, "function readCompany(")


def test_company_keys_match_config_defaults():
    cfg = repo_src("config.py")
    block = cfg[cfg.index('"company": {'):]
    block = block[:block.index("\n    },")]
    assert set(re.findall(r'^\s+"(\w+)":', block, re.M)) == set(COMPANY_KEYS)
