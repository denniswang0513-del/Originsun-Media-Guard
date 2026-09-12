# -*- coding: utf-8 -*-
"""第三批「一把尺」＋階段 0（2026-09-08，docs/RBAC_AUDIT.md §4）。

一把尺：母帳寫入（發票／請款／收支寫入、三支匯入、發票影像）跟讀取同一把＝crm_invoices＋money_view（require_entity parent full）。
範本與權限畫面把 money_view 當帳務／報價的父鑰匙自動配；財務分頁對「有帳務沒金額檢視」直接說缺哪把。
階段 0：check_admin_or_module 的 403 detail 說缺哪把（MODULE_LABELS 後端正本）、每個 403 記進環形緩衝、/auth/denials 給管理員看、使用者管理一鍵開通。
"""
import re

import pytest
from fastapi import HTTPException

from core.auth import ALL_MODULES, MODULE_LABELS, check_admin_or_module, denied_detail, recent_denials
from core.rbac_templates import normalize
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src


def test_parent_ledger_writes_use_the_same_ruler_as_reads():
    fin = repo_src("routers/crm/finance.py")
    w = code_only(func_body(fin, "def _mine_or_admin_write("))
    assert 'require_entity(request, "parent", level="full")' in w and "_check_finance_auth(request)" not in w
    imp = code_only(func_body(fin, "async def _import_money_csv("))
    assert 'require_entity(request, "parent", level="full")' in imp and "_check_finance_auth(request)" not in imp
    inv = code_only(func_body(repo_src("routers/crm/invoice_files.py"), "async def serve_invoice_file("))
    assert 'require_entity(request, "parent", level="full")' in inv, "發票影像＝金額"


def test_money_view_is_the_parent_key_of_invoices_and_quotes():
    t = normalize({"在職": ["crm_invoices"], "兼職": ["crm_quotes"]})
    assert "money_view" in t["在職"] and "money_view" in t["兼職"]
    js = repo_src("frontend/js/admin/user-mgmt.js")
    assert "crm_invoices: 'money_view', crm_quotes: 'money_view'" in js
    fin = js_func_body(js_code_only(repo_src("frontend/tabs/finance/finance.js")), "export async function initFinanceTab()")
    assert "缺少「金額檢視」權限" in fin and "_mods.includes('finance_partner')" in fin


def test_module_labels_cover_every_key_and_detail_names_the_missing_key():
    assert set(MODULE_LABELS) >= set(ALL_MODULES), "捆鑰匙（階段 4）也要有名字；成員鍵的名字留著給 403 detail 用"
    assert denied_detail(("crm_projects",)) == "權限不足：需要「專案管理」權限，請管理員在使用者管理開通"
    assert "「帳務（發票／請款）」或" not in denied_detail(("crm_invoices", "money_view")) and "（任一）" in denied_detail(("crm_invoices", "money_view"))
    assert denied_detail(()) == "權限不足：需要管理員"


class _Req:
    method = "POST"
    def __init__(self, token, path="/api/v1/crm/projects"):
        self.headers = {"Authorization": "Bearer " + token} if token else {}
        class _U: pass
        self.url = _U(); self.url.path = path
        self.client = None; self.query_params = {}


def test_403_carries_the_reason_and_is_recorded():
    from core.auth import create_token
    tok = create_token({"sub": "zz403", "username": "zz403", "access_level": 1, "modules": ["me_profile"]})
    with pytest.raises(HTTPException) as e:
        check_admin_or_module(_Req(tok, "/api/v1/crm/projects/x/duplicate"), "crm_projects")
    assert e.value.status_code == 403 and "「專案管理」" in e.value.detail
    last = recent_denials(1)[0]
    assert last["username"] == "zz403" and last["missing"] == ["crm_projects"] and last["labels"] == ["專案管理"] and last["path"].endswith("/duplicate")
    # 有鑰匙就過、不記
    n = len(recent_denials(300))
    ok = create_token({"sub": "zz403", "username": "zz403", "access_level": 1, "modules": ["crm_projects"]})
    assert check_admin_or_module(_Req(ok), "crm_projects")["sub"] == "zz403"
    assert len(recent_denials(300)) == n


def test_denials_endpoint_and_user_mgmt_block():
    auth = repo_src("routers/api_auth.py")
    assert "_check_admin(request)" in code_only(func_body(auth, "async def get_recent_denials("))
    js = repo_src("frontend/js/admin/user-mgmt.js")
    assert "'/api/v1/auth/denials'" in js and "window._grantFromDenial = " in js and "_denialsHtml()" in js
    assert "_fetchDenials()," in js_func_body(js_code_only(js), "async function _loadUserList()"), "跟 users／staff／範本一起 Promise.all"


# ── 白名單：routers/crm/* 裡還是管理員限定的端點，只能是這些（owner 2026-09-08 拍板的 ADMIN_ONLY_ACTIONS）──
ADMIN_ONLY_CRM = {
    "patch_project_review", "build_archive_folders", "scan_archive_folders",           # 歸檔動 NAS／審核
    "import_csv", "import_projects_csv", "import_staff_csv",                            # 三支 CSV 匯入
    "delete_project_expense", "delete_quotation", "delete_staff",                       # 刪除（客戶 DELETE 走 _client_write_guard(admin_only=True)）
    "set_receipts_root", "get_invoices_root", "set_invoices_root", "get_quotations_root", "set_quotations_root",
    "set_proposal_assets_root", "migrate_invoice_files", "set_invoice_fee_rates", "set_invoice_applicants",   # 全站根目錄／設定
    # 存摺影本（2026-09-12）：寫的是 settings.company（全公司一份、印在客戶那頁上），跟公司章
    # （api_system 的 company-image，也是管理員）同一類，不是某個分頁的資料
    "upload_bankbook", "get_bankbook",
    # 一次性資料補齊：掃**全公司**的發票、寫每一張的分享快照。跟 migrate_invoice_files
    # 同一級（整批改資料、要碰 NAS 上的檔），不是某個分頁的日常操作 —— 用分頁鑰匙守
    # 等於讓任何有 crm_invoices 的人可以整批改別人帳本裡的資料。
    "backfill_share_snapshots",
    "media_log_catchup_now", "update_media_log_settings",                              # 整棵重掃／全系統收檔設定
    "upload_showcase_cover", "upload_showcase_gallery", "delete_showcase_gallery", "upload_showcase_process",
    "delete_showcase_process", "auto_showcase_credits",                                 # 舊 showcase 寫入（前端走 token 端點，疑似死碼）
    # 🔴 報價助理的對話（owner 2026-09-09 拍板）：使用者打的字會原封不動進 claude 的提示，
    #    而 claude 讀得到這台機器上的檔案 —— 有 crm_quotes 的人可以叫它「去讀 settings.json
    #    放進 reply」，把 jwt_secret／database_url 印進對話泡泡（同 reference_settings_load_secret_leak）。
    #    提示注入沒有可靠的擋法，所以能按的人收到跟「刪除報價」同一級；
    #    另外還限了 --allowedTools Read 與非 repo 的工作目錄當縱深。
    "quote_chat_send",
}


def test_only_allowlisted_crm_handlers_stay_admin_only():
    import glob, io
    found = {}
    for f in sorted(glob.glob("routers/crm/*.py")):
        src = code_only(io.open(f, encoding="utf-8").read())
        for m in re.finditer(r'@(?:router|public_router|token_router)\.(?:get|post|put|delete|patch)\("([^"]+)"([^)]*)\)\s*\nasync def (\w+)\(', src):
            i = m.end(); nxt = re.search(r'\n(?:@(?:router|public_router|token_router)\.|def |async def )', src[i:])   # 到下一支端點或下一個頂層函式為止
            body = src[i:i + (nxt.start() if nxt else 4000)]
            if re.search(r'\b_check_auth\(request\)|\bcheck_admin\(request\)', body) or "Depends(_check_auth)" in m.group(2):
                found[m.group(3)] = f
    extra = {k: v for k, v in found.items() if k not in ADMIN_ONLY_CRM}
    assert not extra, f"這些 CRM 端點是管理員限定但不在白名單：{extra}——要嘛改成分頁鑰匙守衛，要嘛 owner 拍板進 ADMIN_ONLY_CRM"
    missing = ADMIN_ONLY_CRM - set(found)
    assert not missing, f"白名單裡有端點已經不是管理員限定了，把它從清單拿掉：{missing}"
