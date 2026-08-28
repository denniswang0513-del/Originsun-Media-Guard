# -*- coding: utf-8 -*-
"""證券投資的成本與損益（owner 2026-08-29「開一個股票證券的側邊來整理證券」）。

券商 App 上看到的是「+17,885,232」，系統原本只記市值 —— 沒有成本欄就生不出
那個數字。成本填的是**該列自己的幣別**（同單價的慣例）。
"""
from pathlib import Path

from routers.api_finance_assets import _holding_cost, _to_twd

ROOT = Path(__file__).resolve().parents[2]


class _H:
    def __init__(self, **kw):
        self.__dict__.update({"currency": "TWD", "cost_total": None,
                              "manual_value": None, "shares": None,
                              "last_price": None, **kw})


def test_cost_converts_with_the_same_rate_as_value():
    """🔴 市值與成本共用 `_to_twd`。兩邊各換各的，損益會變成兩個匯率的差 ——
    沒有任何交易的日子也會浮動。"""
    from routers.api_finance_assets import _holding_value
    h = _H(currency="USD", cost_total=10000, shares=100, last_price=150.0)
    assert _to_twd(10000, "USD", 32.0) == 320000
    assert _to_twd(10000, "TWD", 32.0) == 10000, "台幣不乘匯率"
    assert _holding_cost(h, 32.0) == 320000
    assert _holding_value(h, 32.0) == 480000
    # 同一個匯率 → 損益就是原幣損益乘匯率，不含匯率差
    assert _holding_value(h, 32.0) - _holding_cost(h, 32.0) == (15000 - 10000) * 32


def test_no_cost_is_unknown_not_zero():
    """沒填成本 → 成本 0、損益 None。「還沒填」與「成本剛好等於市值」在畫面上
    必須看得出差別，所以損益是 None 不是 0。"""
    assert _holding_cost(_H(cost_total=None), 32.0) == 0
    assert _to_twd(None, "USD", 32.0) == 0
    src = (ROOT / "routers/api_finance_assets.py").read_text(encoding="utf-8")
    fn = src.split("async def _auto_buckets(")[1].split("\n@router")[0]
    assert '"pnl": (_holding_value' in fn and "if h.cost_total else None" in fn


def test_subview_reuses_the_dashboard_endpoints():
    """持股只有一份資料：證券頁與資產儀表板打同一組端點，不是第二套。"""
    js = (ROOT / "frontend/tabs/finance/subviews/securities.js").read_text(encoding="utf-8")
    for path in ("/assets/overview", "/assets/holdings", "/assets/quotes/refresh"):
        assert path in js, path
    assert "/securities/" not in js, "沒有另開一套證券端點"
