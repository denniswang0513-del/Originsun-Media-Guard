# -*- coding: utf-8 -*-
"""基準月分帳本（owner 2026-08-25「我的私帳的對帳起始點先不用設」）。

settings 的 finance.baseline_month 是**母公司設定精靈**寫的全域單值。修正前
_resolve_baseline 不分帳本直接套 —— 私帳的三表被母公司的 2025-07 攔腰切掉，
拿掉之後又踩到第二個雷：私帳退到「資料最早月」時，Sheet 上兩筆打錯年份的
2014/3/14（實為 2024/3/14，夾在 2024/3 的列中間）把累計區間撐到 148 個月、
month_range 直接 raise、整張三表 500。資料已修，這裡把兩層行為都釘住。
"""
import asyncio

from services.finance_statements import _resolve_baseline, compute_live


def _inputs(entity, cash_months=("2023-06", "2024-01")):
    return {
        "entity": entity,
        "invoices": [], "payments": [],
        "cash_entries": [{"entry_date": f"{m}-15", "deposit": 100, "expense": 0,
                          "category": "", "bank_account_id": None,
                          "status": "", "claim": 0, "bank_fee": 0,
                          "project_id": None, "invoice_id": None,
                          "summary": "x", "is_advance_link": None}
                         for m in cash_months],
        "equipment": [], "adjustments": [], "bank_accounts": [],
        "loans": [], "loan_payments": [], "accounts": {}, "cat_map": {},
    }


def test_parent_reads_the_settings_baseline(monkeypatch):
    import config
    monkeypatch.setattr(config, "load_settings",
                        lambda: {"finance": {"baseline_month": "2025-07"}})
    assert _resolve_baseline(_inputs("parent")) == "2025-07"


def test_mine_ignores_the_settings_baseline(monkeypatch):
    """🔴 私帳不吃母公司的基準月 —— 一律走資料最早月（＝沒有人為起始點）。"""
    import config
    monkeypatch.setattr(config, "load_settings",
                        lambda: {"finance": {"baseline_month": "2025-07"}})
    assert _resolve_baseline(_inputs("mine")) == "2023-06"


def test_missing_entity_defaults_to_parent(monkeypatch):
    """舊呼叫端沒帶 entity 鍵 → 當母公司（既有語意不變）。"""
    import config
    monkeypatch.setattr(config, "load_settings",
                        lambda: {"finance": {"baseline_month": "2025-07"}})
    d = _inputs("parent")
    d.pop("entity")
    assert _resolve_baseline(d) == "2025-07"


def test_ancient_typo_date_warns_instead_of_500(monkeypatch):
    """🔴 這就是那次 500 的本體：一筆打錯年份的日期（1999）讓累計區間超過
    120 個月上限。修正後＝clamp ＋ 一條指路的警語，三表照樣算得出來。"""
    import config
    monkeypatch.setattr(config, "load_settings", lambda: {})
    bad = _inputs("mine", cash_months=("1999-01", "2026-01"))
    adv = {"balance_total": 0, "expenses": []}
    r = asyncio.run(compute_live(None, ["2026-06"], inputs=bad, adv=adv,
                                 entity="mine"))
    assert r["baseline_month"] >= "2016-07"          # 已 clamp 進上限
    assert any("超出累計上限" in w for w in r["warnings"]["messages"] if isinstance(w, str)) \
        or any("超出累計上限" in str(w) for w in r["warnings"]["messages"])


def test_normal_history_is_untouched(monkeypatch):
    """沒踩上限的正常資料：baseline＝資料最早月、零 clamp 警語。"""
    import config
    monkeypatch.setattr(config, "load_settings", lambda: {})
    r = asyncio.run(compute_live(None, ["2026-06"], inputs=_inputs("mine"),
                                 adv={"balance_total": 0, "expenses": []},
                                 entity="mine"))
    assert r["baseline_month"] == "2023-06"
    assert not any("超出累計上限" in str(w) for w in r["warnings"]["messages"])
