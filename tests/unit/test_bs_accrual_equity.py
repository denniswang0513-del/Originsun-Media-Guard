# -*- coding: utf-8 -*-
"""BS 的應收與累積損益要同一個口徑（owner 2026-08-29「我也希望資產負債表
可以做到平」）。

私帳的 BS 應收線是**累計權責**（結案 ≤ as_of 的未收），而 `cum_pnl` 是
**現金**（入帳認列）—— 應收長出來的錢在權益那側沒有對應，表就差那麼多。
2026-08-29 生產實測：2026-06 差 1,428,185、2026-08 差 1,836,281，
補上期末應收之後降到 −176,386／−242,928（2023~2025 三期完全沒動）。
"""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[2]
       / "services/finance_statements.py").read_text(encoding="utf-8")


def test_cumulative_equity_adds_the_period_end_receivable():
    fn = SRC.split("async def compute_live(")[1].split("\nasync def ")[0]
    assert '_cum_recv = _mp["receivable"]' in fn
    assert 'cum_pnl["net"]["amount"] + (_cum_recv if _mp is not None else 0)' in fn


def test_it_adds_the_receivable_not_a_full_accrual_restatement():
    """🔴 加的是**應收本身**，不是把 cum_pnl 整份 restate 成權責。

    那支 restate 是為**期間**設計的（對齊 owner 年度表），套在「基準月以來的
    累計」窗口上會連成本（apply_ledger_project_costs）一起換掉 —— 同一份資料
    實測好壞參半：2026 兩期降到 723,003／747,079，但 2025-12 從 90,265
    惡化到 −1,224,185、2024-06 從 22,590 到 −201,841。
    """
    fn = SRC.split("async def compute_live(")[1].split("\nasync def ")[0]
    tail = fn[fn.index('_cum_recv = _mp["receivable"]'):]
    assert "restate_revenue_accrual(cum_pnl" not in tail
    assert "apply_ledger_project_costs(\n                cum_pnl" not in tail


def test_the_precondition_is_written_down():
    """這條式子的前提：基準月之前結案而仍未收的案＝0。哪天有了那種案，
    它的營收落在基準之前、應收卻掛在表上 —— 要另外進期初調整。
    前提沒寫下來，下一個人只會看到一行 `+ _cum_recv`。"""
    assert "基準月(2023-06)之前" in SRC and "期初調整" in SRC


def test_parent_ledger_is_untouched():
    """母公司沒有 _mp（應收走發票）—— 那條加項只對私帳成立。"""
    fn = SRC.split("async def compute_live(")[1].split("\nasync def ")[0]
    assert 'if _mp is not None else 0' in fn
    assert 'if (inputs.get("entity") or entity) == "mine":' in fn
