# -*- coding: utf-8 -*-
"""收款方式（39376dab～9383d4cc）/code-review 的四個發現 —— 紅→綠（2026-09-13）。

apply_billing_mode 的三條規則用假 session／假連結打（resolve_mine_link／mine_parent_names／
require_entity 都換掉，只看它自己的分支邏輯）；rounding 那條是純函式。
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import core.ledger as ledger
from core.ledger_project import apply_source_fee, norm_detail
from routers.crm import project_links as pl
from tests.unit._srcscan import code_only, flow_body, projects_src


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


async def test_switch_moves_only_that_parents_share(monkeypatch):
    """BUG-5（owner 2026-09-13 改為「加起來」）：私帳案 X 承接 A、B → A 改後期代開只換 A 的份額，
    X 吃差額，B 的錢與 owner 自己填的都不動；換回也只動 A。"""
    from core.ledger_project import parent_shares, set_parent_share

    d, c = set_parent_share({"source": "源日"}, 0, "A", 40000, {"剪接": 40000}, synced_total=40000)
    d, c = set_parent_share(d, c, "B", 80000, {"剪接": 80000}, synced_total=80000)
    t = SimpleNamespace(id="X", name="私帳案 X", entity="mine", contract_amount=c + 20000,   # owner 自己多填 20,000
                        ledger_detail=d, updated_at=None)

    async def _link(session, p):
        return t
    monkeypatch.setattr(pl, "resolve_mine_link", _link)
    monkeypatch.setattr(ledger, "require_entity", lambda request, ent, level="": None)
    monkeypatch.setattr(pl, "_store_mirror_detail", lambda t, detail: setattr(t, "ledger_detail", detail))

    out = await pl.apply_billing_mode(None, _parent("passthrough", contract=100000), request=None, old_mode="company")
    assert out["action"] == "switched"
    assert t.contract_amount == 140000 + 60000            # A 40,000 → 100,000：只加 60,000
    shares = parent_shares(t.ledger_detail)
    assert shares["A"]["amount"] == 100000 and shares["A"]["split"] == {"剪接": 40000}   # 工項沿用
    assert shares["B"] == {"amount": 80000, "split": {"剪接": 80000}, "synced_total": 80000, "at": ""}
    assert t.ledger_detail["split"]["剪接"] == 120000       # 工項沒動
    assert t.ledger_detail["source"] == "代開發票"


async def test_switch_on_legacy_one_to_one_claims_whole_row_first(monkeypatch):
    """沒分案記錄的舊 1:1 分身：先把整筆認成 A 的份額，再換 —— 結果跟以前一樣（收入＝母帳合約額）。"""
    from core.ledger_project import parent_shares

    t = SimpleNamespace(id="X", name="私帳案 X", entity="mine", contract_amount=50000,
                        ledger_detail={"source": "源日", "split": {"剪接": 50000}}, updated_at=None)

    async def _link(session, p):
        return t
    monkeypatch.setattr(pl, "resolve_mine_link", _link)
    monkeypatch.setattr(ledger, "require_entity", lambda request, ent, level="": None)
    monkeypatch.setattr(pl, "_store_mirror_detail", lambda t, detail: setattr(t, "ledger_detail", detail))
    await pl.apply_billing_mode(None, _parent("passthrough", contract=100000), request=None, old_mode="company")
    assert t.contract_amount == 100000
    assert parent_shares(t.ledger_detail)["A"]["amount"] == 100000


async def test_switch_refuses_pending_legacy_n_to_one(monkeypatch):
    """回填分不出各案份額的舊 N:1（by_parent_pending）：不猜，409 要求先逐案認領。"""
    from core.ledger_project import BY_PARENT_PENDING_KEY

    t = SimpleNamespace(id="X", name="私帳案 X", entity="mine", contract_amount=120000,
                        ledger_detail={"source": "源日", "split": {"剪接": 120000}, BY_PARENT_PENDING_KEY: True},
                        updated_at=None)

    async def _link(session, p):
        return t
    monkeypatch.setattr(pl, "resolve_mine_link", _link)
    monkeypatch.setattr(ledger, "require_entity", lambda request, ent, level="": None)
    with pytest.raises(HTTPException) as e:
        await pl.apply_billing_mode(None, _parent("passthrough"), request=None, old_mode="company")
    assert e.value.status_code == 409 and "認領" in e.value.detail
    assert t.contract_amount == 120000


async def test_unlink_drops_that_parents_share(monkeypatch):
    """決策 ①：解除連結把那案加進私帳的錢與工項拿掉；別案不動。"""
    from core.ledger_project import parent_shares, set_parent_share

    d, c = set_parent_share({"source": "源日"}, 0, "A", 40000, {"剪接": 40000})
    d, c = set_parent_share(d, c, "B", 80000, {"剪接": 80000})
    t = SimpleNamespace(id="X", name="私帳案 X", entity="mine", contract_amount=c, ledger_detail=d, updated_at=None)
    parent = SimpleNamespace(id="A", mine_link_id="X", updated_at=None)

    class _S:
        async def get(self, model, pk):
            return t if pk == "X" else None
    monkeypatch.setattr(pl, "_store_mirror_detail", lambda t, detail: setattr(t, "ledger_detail", detail))
    await pl._write_link(_S(), parent, None)
    assert parent.mine_link_id is None
    assert t.contract_amount == 80000
    assert t.ledger_detail["split"] == {"剪接": 80000}
    assert list(parent_shares(t.ledger_detail)) == ["B"]



def test_half_up_rounding_matches_the_frontend():
    """BUG-6：前端 Math.round 是半進位，後端 round() 是銀行家 —— .5 時差 1 元，代辦費會被誤標成「手動」。"""
    d = norm_detail({"source": "代開發票", "fee_pct": 10, "invoice_fee": 1235})
    out = apply_source_fee(12345, d, keep={"invoice_fee"})      # 12345 × 10% = 1234.5 → 前端送 1235
    assert out["invoice_fee"] == 1235
    assert "invoice_fee" not in (out.get("manual") or []), "跟試算值一樣的送值不是手動"
    # 真的手改還是要被標
    d2 = norm_detail({"source": "代開發票", "fee_pct": 10, "invoice_fee": 1300})
    assert "invoice_fee" in apply_source_fee(12345, d2, keep={"invoice_fee"})["manual"]


def test_create_project_surfaces_skipped():
    """BUG-8：建案時分身沒建（同事沒私帳權限）要跟 update 一樣出聲，不能靜靜回 ok。"""
    create = code_only(flow_body(projects_src(), "async def create_project("))
    update = code_only(flow_body(projects_src(), "async def update_project("))
    msg = "收款方式已存，但你看不到私帳"
    assert msg in update
    assert msg in create and '"skipped"' in create


async def test_switch_marks_only_that_parent_as_agency_and_fee_counts_only_it(monkeypatch):
    """代辦費分案：A 改後期代開 → 只有 A 的份額標代開，代辦費只算 A；B 留源日。換回也只動 A。"""
    from core.ledger_project import fee_bases, parent_shares, set_parent_share

    d, c = set_parent_share({"source": "源日"}, 0, "A", 100000, {"導演": 100000}, source="源日")
    d, c = set_parent_share(d, c, "B", 80000, {"剪接": 80000}, source="源日")
    t = SimpleNamespace(id="X", name="X", entity="mine", contract_amount=c, ledger_detail=d, updated_at=None)

    async def _link(session, p):
        return t
    monkeypatch.setattr(pl, "resolve_mine_link", _link)
    monkeypatch.setattr(ledger, "require_entity", lambda request, ent, level="": None)
    monkeypatch.setattr(pl, "_store_mirror_detail", lambda t, detail: setattr(t, "ledger_detail", detail))

    await pl.apply_billing_mode(None, _parent("passthrough", contract=100000), request=None, old_mode="company")
    sh = parent_shares(t.ledger_detail)
    assert sh["A"]["source"] == "代開發票" and sh["B"]["source"] == "源日"
    assert fee_bases(t.contract_amount, t.ledger_detail) == (100000, 0)
    assert t.ledger_detail["invoice_fee"] == 8000                     # 不是 180,000 × 8%

    out = await pl.apply_billing_mode(None, _parent("company", contract=100000), request=None, old_mode="passthrough")
    assert out["action"] == "reverted"
    sh = parent_shares(t.ledger_detail)
    assert sh["A"]["source"] == "源日" and t.ledger_detail["source"] == "源日"
    assert (t.ledger_detail["invoice_fee"], t.ledger_detail["tax_fee"], t.ledger_detail["buy_invoice"]) == (0, 0, 0)
    assert t.contract_amount == 180000                                # 換回不動錢
