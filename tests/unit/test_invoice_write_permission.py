# -*- coding: utf-8 -*-
"""帳務寫入的權限面（owner 2026-09-05）。

兩件事在同一天定案：
  1. 黃聖鈞（Ryansnap，Lv1 ＋ crm_invoices ＋ money_view）回報「無法開發票」——
     `core.ledger.allowed_entities` 早就認定「crm_invoices ＋ money_view ＝
     母帳 full scope（可寫）」，但端點的 `_check_auth` 只認管理員。
     模組發了、按鈕看得到、按下去 403。
  2. owner：「權限管理也沒有什麼 lv 幾，都在這裡管理就好」—— 使用者管理上
     「管理員」的說明是**使用者 / 設定 / 發版**，它不是業務功能的閘門。

於是整個**母帳帳務**的寫入面改看「財務管理」那個勾（模組鍵 `crm_invoices`）。
"""
from tests.unit._srcscan import repo_src


def test_finance_writes_are_gated_by_the_module_not_by_admin():
    """母帳帳務的寫入 —— 管理員 OR 持有「財務管理」模組。"""
    shared = repo_src("routers/crm/_shared.py")
    assert "_check_finance_auth = _module_guard('crm_invoices')" in shared,         "守衛要放 _shared（invoice_files 也要用，而 finance 已經 import 它）"
    fin = repo_src("routers/crm/finance.py")
    # 咽喉：所有建立／更新都經過它，母帳那半邊看模組
    assert "_check_finance_auth(request)" in fin
    assert "    _check_auth(request)" not in fin, "帳務不該再有管理員限定的寫入"
    files = repo_src("routers/crm/invoice_files.py")
    assert "_check_auth(request)" not in files, "發票檔案端點也是帳務工作"
    assert files.count("_check_finance_auth(request)") == 5


def test_the_real_chokepoint_is_mine_or_admin_write():
    """🔴 端點自己那行守衛**擋不住** —— `_entity_for_write` 這支咽喉自己也會呼
    `_mine_or_admin_write`。2026-09-05 實測：只把 create_invoice 的守衛換掉
    之後仍然 403。要改就改咽喉。"""
    fin = repo_src("routers/crm/finance.py")
    j = fin.index("def _mine_or_admin_write(")
    seg = fin[j:j + 1400]
    assert 'require_entity(request, "mine", level="full")' in seg, "私帳仍是指名制"
    assert "_check_finance_auth(request)" in seg, "母帳看模組"
    # 上一版為了發票加的 parent_guard 管線退場（預設值本身改了就不需要）
    assert "parent_guard" not in fin


def test_the_ledger_wall_is_untouched():
    """🔴 放寬的只有「要不要是管理員」那一層 —— 帳本牆照舊，
    私帳（finance_mine 指名制）一步都沒開。"""
    src = repo_src("routers/crm/finance.py")
    for fn, guard in (("async def delete_invoice(", "require_entity(request, inv.entity"),
                      ("async def restore_invoice_from_trash(", "require_entity(request, t.entity")):
        i = src.index(fn)
        assert guard in src[i:i + 2000], fn
    i = src.index("async def create_invoice(")
    assert "_entity_for_write(request, req.entity)" in src[i:i + 400]


def test_admin_still_means_users_settings_releases():
    """「管理員」保留給系統面：使用者管理／設定／發版 —— 那正是 UI 上的說明。"""
    labels = repo_src("frontend/js/admin/user-mgmt.js")
    assert "管理員（完整權限：使用者 / 設定 / 發版）" in labels
    # 「財務管理」那個勾就是 crm_invoices（後端守衛用的鍵）
    assert "crm_invoices:'財務管理'" in labels
