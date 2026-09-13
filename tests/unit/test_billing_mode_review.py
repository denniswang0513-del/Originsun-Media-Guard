# -*- coding: utf-8 -*-
"""收款方式（39376dab～9383d4cc）/code-review 的四個發現 —— 紅→綠（2026-09-13）。

apply_billing_mode 的三條規則用假 session／假連結打（resolve_mine_link／mine_parent_names／
require_entity 都換掉，只看它自己的分支邏輯）；rounding 那條是純函式。
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import core.ledger as ledger
from routers.crm import project_links as pl


def _parent(mode, contract=100000):
    return SimpleNamespace(id="A", name="母帳案 A", entity="parent", billing_mode=mode,
                           contract_amount=contract)


def _mirror(source="源日"):
    return SimpleNamespace(id="X", name="私帳案 X", entity="mine", contract_amount=50000,
                           ledger_detail={"source": source, "invoice_fee": 4000, "tax_fee": 200, "buy_invoice": 3800},
                           updated_at=None)


@pytest.fixture
def no_mine_scope(monkeypatch):
    calls = []

    def _deny(request, ent, level=""):
        calls.append(ent)
        raise HTTPException(status_code=403, detail="沒有私帳權限")
    monkeypatch.setattr(ledger, "require_entity", _deny)
    return calls


async def test_no_mirror_work_means_no_skipped(monkeypatch, no_mine_scope):
    """BUG-7：company↔cash 不涉分身 → 對沒私帳權限的同事也要回 none，不是 skipped（那句 warning 每次存檔都跳）。"""
    async def _no_link(session, p):
        return None
    monkeypatch.setattr(pl, "resolve_mine_link", _no_link)

    out = await pl.apply_billing_mode(None, _parent("cash"), request=None, old_mode="company")
    assert out == {"action": "none", "created": False}
    assert no_mine_scope == [], "不需要動分身就不該去問私帳權限"

    out = await pl.apply_billing_mode(None, _parent("company"), request=None, old_mode="cash")
    assert out == {"action": "none", "created": False}


async def test_passthrough_switch_still_asks_for_mine_scope(monkeypatch, no_mine_scope):
    """反向釘：真的要建／改分身時，沒權限還是 skipped（1bf46e4e 那條不能被 BUG-7 的修法弄壞）。"""
    async def _no_link(session, p):
        return None
    monkeypatch.setattr(pl, "resolve_mine_link", _no_link)
    out = await pl.apply_billing_mode(None, _parent("passthrough"), request=None, old_mode="company")
    assert out == {"action": "skipped", "created": False}
    assert no_mine_scope == ["mine"]



