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


async def test_switch_refuses_when_mirror_is_shared(monkeypatch):
    """BUG-5：私帳案 X 同時承接 A、B → A 改後期代開不能把 X 的收入改成 A 的合約（B 的錢會不見）；同 mirror 那支的 409。"""
    t = _mirror()

    async def _link(session, p):
        return t

    async def _shared(session, ids):
        return {"X": ["母帳案 A", "母帳案 B"]}
    monkeypatch.setattr(pl, "resolve_mine_link", _link)
    monkeypatch.setattr(ledger, "require_entity", lambda request, ent, level="": None)
    import routers.crm._shared as shared
    monkeypatch.setattr(shared, "mine_parent_names", _shared)

    with pytest.raises(HTTPException) as e:
        await pl.apply_billing_mode(None, _parent("passthrough"), request=None, old_mode="company")
    assert e.value.status_code == 409
    assert "承接了 2 個母帳案" in e.value.detail
    assert t.contract_amount == 50000 and t.ledger_detail["source"] == "源日", "409 之前不能先動到分身"

    # 換回也一樣：代開那套不能連別案的一起歸零
    t2 = _mirror("代開發票")

    async def _link2(session, p):
        return t2
    monkeypatch.setattr(pl, "resolve_mine_link", _link2)
    with pytest.raises(HTTPException) as e2:
        await pl.apply_billing_mode(None, _parent("company"), request=None, old_mode="passthrough")
    assert e2.value.status_code == 409
    assert t2.ledger_detail["invoice_fee"] == 4000


