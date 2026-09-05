# -*- coding: utf-8 -*-
"""發票寫入的權限面（owner 2026-09-05 拍板 B 案）。

背景：`core.ledger.allowed_entities` 早就認定「crm_invoices ＋ money_view ＝
母帳 full scope（可寫）」，但發票那幾支端點的 `_check_auth` 只認 Lv3 ——
兩條規則互相矛盾，結果是**模組發了、按鈕看得到、按下去 403**
（黃聖鈞 Ryansnap：Lv1 ＋ crm_invoices ＋ money_view）。

這裡釘的是「哪幾支放寬、哪幾支不放寬」，因為放寬的那條線很容易被下一次
重構整片抹平或整片收回。
"""
from tests.unit._srcscan import repo_src


def test_daily_invoice_writes_accept_the_module():
    """新增／修改／刪除／垃圾桶列出與還原 —— 管理員 OR 持有 crm_invoices。"""
    src = repo_src("routers/crm/finance.py")
    assert "_check_invoice_auth = _module_guard('crm_invoices')" in src
    assert src.count("_check_invoice_auth(request)") == 5, \
        "create／update／delete／trash list／restore 五支"
    for fn in ("async def create_invoice(", "async def update_invoice(",
               "async def delete_invoice(", "async def list_invoice_trash("):
        i = src.index(fn)
        assert "_check_invoice_auth(request)" in src[i:i + 400], fn


def test_the_real_chokepoint_is_entity_for_write():
    """🔴 端點自己那行守衛**擋不住這裡**。

    `_entity_for_write` 是所有建立/更新端點的咽喉，它自己也會呼
    `_mine_or_admin_write`（母公司＝Lv3）。2026-09-05 實測：只把
    `create_invoice` 的 `_check_auth` 換成模組守衛之後**仍然 403**，
    因為真正擋人的是咽喉那一層。所以發票的建立／更新要把守衛傳進去。
    """
    src = repo_src("routers/crm/finance.py")
    assert "def _entity_for_write(request: Request, payload_entity, row=None,\n" \
           "                      parent_guard=None) -> str:" in src
    assert "_mine_or_admin_write(request, ent, parent_guard=parent_guard)" in src
    assert "(parent_guard or _check_auth)(request)" in src
    # 只有發票那兩支傳；收支／請款／分類樹維持 Lv3（不傳＝預設）
    assert src.count("parent_guard=_check_invoice_auth") == 2
    for other in ("routers/crm/cash.py", "routers/crm/payments.py", "routers/crm/taxonomy.py"):
        assert "parent_guard" not in repo_src(other), other


def test_irreversible_and_bulk_stay_admin_only():
    """🔴 兩件事不隨模組放寬：**永久清除**垃圾桶（不可逆）與 **CSV 批次匯入**
    （一次寫幾百列、還牽動月結守衛）。批次標記收付款也維持 Lv3。"""
    src = repo_src("routers/crm/finance.py")
    i = src.index("async def purge_invoice_trash(")
    assert "_check_auth(request)" in src[i:i + 200], "永久清除要維持 Lv3"
    assert "_check_invoice_auth" not in src[i:i + 200]
    # 批次（_mine_or_admin_write）的母公司路徑仍是 Lv3
    j = src.index("def _mine_or_admin_write(")
    seg = src[j:j + 1200]
    # 批次那條路不傳 parent_guard → 落到預設的 _check_auth（Lv3）
    assert "(parent_guard or _check_auth)(request)" in seg
    assert "parent_guard=_check_invoice_auth" not in seg


def test_the_ledger_wall_is_untouched():
    """🔴 放寬的只有「要不要是管理員」那一層 —— 每一支自己的帳本守衛照舊，
    私帳（finance_mine 指名制）一步都沒開。"""
    src = repo_src("routers/crm/finance.py")
    for fn, guard in (("async def delete_invoice(", "require_entity(request, inv.entity"),
                      ("async def restore_invoice_from_trash(", "require_entity(request, t.entity")):
        i = src.index(fn)
        assert guard in src[i:i + 2000], fn
    i = src.index("async def create_invoice(")
    assert "_entity_for_write(request, req.entity, parent_guard=_check_invoice_auth)"         in src[i:i + 400]
