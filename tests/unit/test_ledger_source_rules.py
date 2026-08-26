# -*- coding: utf-8 -*-
"""案源規則（owner 2026-08-25）：源日＝現金收款；代開發票＝營收×服務費率的
代辦費（預設 8%，191 個歷史案實證全部 8.00%；逐案可調）。正本在
core.ledger_project.apply_source_fee —— 前端只是同一條式子的即時預覽。"""
from core.ledger_project import (DEFAULT_FEE_PCT, SOURCES, apply_source_fee,
                                 norm_detail)


def test_norm_detail_keeps_source_and_custom_fee():
    d = norm_detail({"source": "代開發票", "fee_pct": 10, "misc": 5})
    assert d["source"] == "代開發票" and d["fee_pct"] == 10 and d["misc"] == 5


def test_norm_detail_drops_default_fee_and_unknown_source():
    """費率＝預設值不落盤（免得預設哪天要調，402 筆都被舊值釘死）；
    案源走白名單（亂字串不進 meta）。"""
    d = norm_detail({"source": "路邊撿的", "fee_pct": DEFAULT_FEE_PCT})
    assert "source" not in d and "fee_pct" not in d
    assert set(SOURCES) == {"自接", "源日", "代開發票", "執行業務所得"}


def test_agency_fee_is_contract_times_pct():
    d = apply_source_fee(105462, norm_detail({"source": "代開發票"}))
    assert d["invoice_fee"] == 8437          # 思沙龍 EP02 的實際數字
    d = apply_source_fee(80000, norm_detail({"source": "代開發票", "fee_pct": 10}))
    assert d["invoice_fee"] == 8000          # 逐案調率


def test_other_sources_never_touch_the_fee():
    """🔴 自動規則只管代開發票 —— 歷史案（自接＋手填代辦費）不可被回溯改寫。"""
    d = norm_detail({"source": "自接", "invoice_fee": 1512})
    assert apply_source_fee(18900, dict(d))["invoice_fee"] == 1512
    d2 = norm_detail({"source": "源日", "invoice_fee": 0})
    assert apply_source_fee(50000, dict(d2))["invoice_fee"] == 0


def test_endpoints_apply_the_rule_on_write():
    """create 與 update 都要在寫入時套（前端的 disabled 欄位擋不住 API 呼叫）。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "routers/api_finance_projects.py").read_text(encoding="utf-8")
    for fn_name in ("create_ledger_project", "update_project_ledger"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "apply_source_fee(" in fn, fn_name


# ── 收款自動同步（owner 2026-08-25「勾選專案，如果金額到齊，就是收款」）──
def test_mine_link_rule_is_prefix_and_parent_is_list():
    from core.project_link import CASH_CATEGORIES, cash_can_link
    assert cash_can_link("mine", "公司_專案")
    assert cash_can_link("mine", "公司_專案支出")
    assert not cash_can_link("mine", "個人_生活")      # 掛上去污染專案毛利
    assert not cash_can_link("mine", "")
    for c in CASH_CATEGORIES:
        assert cash_can_link("parent", c)
    assert not cash_can_link("parent", "公司_專案")     # 兩本詞彙不互通
    assert not cash_can_link(None, "公司_專案")         # 預設＝母公司規則


def test_received_sync_is_incremental_and_mine_only():
    """🔴 增量制（±delta）不是重算 —— 歷史已收是匯入基準，重算會把老案洗掉。
    三個寫入端點都要掛；update 必須在 setattr 前抓舊值。只管 mine（母公司的
    已收走發票/分配那條既有流程，疊上去＝雙重驅動）。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "routers/crm/finance.py").read_text(encoding="utf-8")
    helper = src.split("async def _sync_mine_project_received(")[1].split("\ndef ")[0]
    # mine-only 那半（安全半邊）收在共用前導 _get_mine_project，兩支 sync 都走它
    assert "_get_mine_project(session, project_id)" in helper
    pre = src.split("async def _get_mine_project(")[1].split("\nasync def ")[0]
    assert '!= "mine":' in pre.replace("'", '"') and "return None" in pre
    assert "amount_received or 0) + int(delta)" in helper
    for fn_name in ("create_cash_entry", "update_cash_entry", "delete_cash_entry"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "_sync_mine_project_received(" in fn, fn_name
    upd = src.split("async def update_cash_entry(")[1].split("\n@router")[0]
    assert upd.index("_old_dep, _old_pid") < upd.index("for k, v in data.items()"), \
        "舊值要在 setattr 之前抓"


def test_agency_fee_composition():
    """稅金5% + 買發票 = 代辦費（owner 口述＋Sheet 實證 82,000→3,905/2,655/6,560）。
    三欄是**組成**不是加項 —— compute 只扣 invoice_fee，三個都扣＝同一筆錢
    扣兩次。"""
    from core.ledger_project import compute
    d = apply_source_fee(82000, norm_detail({"source": "代開發票"}))
    assert (d["invoice_fee"], d["tax_fee"], d["buy_invoice"]) == (6560, 3905, 2655)
    assert d["tax_fee"] + d["buy_invoice"] == d["invoice_fee"]
    net, _ = compute(82000, d)
    assert net == 82000 - 6560               # 只扣代辦費一次


def test_professional_income_withholding_auto():
    """執行業務所得（owner 2026-08-26「新增一個執行業務所得的項目自動算」）：
    源頭代扣＝所得扣繳 10%（單次稅額 ≤2,000 免扣）＋二代健保 2.11%
    （單次 <20,000 免扣）。42,000 → 4,200＋886＝5,086（典藏媒體顧問實帳）。
    「自接」自可選清單移除（歷史值仍有效，白名單保留）。"""
    from core.ledger_project import (SELECTABLE_SOURCES, SOURCES,
                                     apply_source_fee, withholding)
    assert withholding(42000) == 5086
    assert withholding(20000) == 422          # 稅 2,000 免扣；健保 422 照收
    assert withholding(19999) == 0            # 兩門檻皆未達
    assert withholding(100000) == 10000 + 2110
    d = apply_source_fee(42000, norm_detail({"source": "執行業務所得"}))
    assert d["personal_tax"] == 5086
    assert "自接" not in SELECTABLE_SOURCES and "自接" in SOURCES
    assert "執行業務所得" in SELECTABLE_SOURCES


def test_owner_can_write_own_book_but_parent_stays_admin_only():
    """🔴 owner＝lv1＋finance_mine（指名制），原本 CRM 寫入全是 Lv3-only ——
    帳本主人在生產連一筆帳都記不進去（真實帳號形狀實測 403 才發現；先前
    測試全用 Lv3 token）。開 mine full 路徑；母公司維持 Lv3（不放寬同事）。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "routers/crm/finance.py").read_text(encoding="utf-8")
    helper = src.split("def _mine_or_admin_write(")[1].split("\ndef ")[0]
    assert 'require_entity(request, "mine", level="full")' in helper
    assert "_check_auth(request)" in helper          # 母公司路徑原樣
    # 建立/更新走 _entity_for_write 咽喉（守衛收在裡面 —— 逐端點明呼會忘）
    throat = src.split("def _entity_for_write(")[1].split("\ndef ")[0]
    assert "_mine_or_admin_write(request, ent)" in throat
    for fn_name in ("create_cash_entry", "update_cash_entry",
                    "create_payment", "update_payment"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "_entity_for_write" in fn, fn_name
    # 刪除沒有 payload、不走咽喉 —— 各自明呼
    for fn_name in ("delete_cash_entry", "delete_payment"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "_mine_or_admin_write" in fn, fn_name
    # 🔴 分配寫入（_write_allocs 兩側共用）與批次掛專案 —— 2026-08-26 生產實測
    # 補上的兩條漏網（owner 存「請款單分配」權限不足 ×3）。不走咽喉的寫入端點
    # 一律：check_logged_in → 載列 → 按列帳本驗 _mine_or_admin_write*
    alloc = src.split("async def _write_allocs(")[1].split("\n@router")[0]
    assert "_check_auth(" not in alloc and "_mine_or_admin_write(request, e.entity)" in alloc
    batch = src.split("async def batch_assign_project(")[1].split("\n@router")[0]
    assert "_check_auth(" not in batch and "_mine_or_admin_write_rows(request, rows)" in batch
    for fn_name in ("batch_pay", "batch_unpay", "batch_update_month"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "_mine_or_admin_write_rows(request, rows)" in fn, fn_name
    # 沒動的端點維持 Lv3（發票/CSV 匯入/批次收款/分配）
    for fn_name in ("create_invoice", "batch_receive"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "_check_auth(request)" in fn, fn_name


def test_outsource_sync_is_incremental_and_scoped():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "routers/crm/finance.py").read_text(encoding="utf-8")
    helper = src.split("async def _sync_mine_project_outsource(")[1].split("\ndef ")[0]
    assert '!= "專案外包":\n        return' in helper.replace("'", '"')
    assert '+ int(delta)' in helper
    for fn_name in ("create_payment", "update_payment", "delete_payment"):
        fn = src.split(f"async def {fn_name}(")[1].split("\n@router")[0]
        assert "_sync_mine_project_outsource" in fn, fn_name
