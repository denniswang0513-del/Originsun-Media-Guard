# -*- coding: utf-8 -*-
"""資產儀表板的匯率契約（純函式 + 簽章釘）。

2026-08-25 /simplify 第 2 輪把 `_auto_buckets` 的匯率改成可選參數，
`save_snapshot` 沒傳 —— 每筆美元持股乘以 0，存進 row.auto 的證券現值少掉整個
外幣部位，而且沒有任何跡象。第 3 輪改回必填；這裡把「不准有預設值」釘住。
"""
import inspect
import re
from pathlib import Path
from types import SimpleNamespace as S

from routers.api_finance_assets import _auto_buckets, _holding_value

SRC = (Path(__file__).resolve().parents[2]
       / "routers/api_finance_assets.py").read_text(encoding="utf-8")


def test_holding_value_rules():
    """manual_value 優先；台幣不乘匯率；外幣才乘；沒價或沒股數＝0。"""
    assert _holding_value(S(manual_value=500, last_price=10.0, shares=3, currency="USD"), 31.0) == 500
    assert _holding_value(S(manual_value=None, last_price=10.0, shares=3, currency="TWD"), 31.0) == 30
    assert _holding_value(S(manual_value=None, last_price=10.0, shares=3, currency="USD"), 31.0) == 930
    assert _holding_value(S(manual_value=None, last_price=None, shares=3, currency="TWD"), 31.0) == 0


def test_zero_fx_wipes_a_usd_position():
    """這就是那次回歸的本體：拿不到匯率時整個外幣部位乘以 0、還沒有跡象。"""
    h = S(manual_value=None, last_price=10.0, shares=3, currency="USD")
    assert _holding_value(h, 0) == 0


def test_auto_buckets_fx_has_no_default():
    """🔴 匯率由呼叫端傳、**不給預設值** —— 給了預設就是某個呼叫端忘了傳也不會
    有人知道。"""
    p = inspect.signature(_auto_buckets).parameters["usd_twd"]
    assert p.default is inspect.Parameter.empty


def test_every_auto_buckets_call_passes_fx():
    calls = re.findall(r"_auto_buckets\(\s*\n?\s*session,\s*ent,", SRC)
    assert len(calls) == SRC.count("await _auto_buckets("), "有呼叫點沒傳匯率"
