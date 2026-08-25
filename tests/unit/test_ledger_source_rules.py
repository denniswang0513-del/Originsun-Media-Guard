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
    assert set(SOURCES) == {"自接", "源日", "代開發票"}


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
