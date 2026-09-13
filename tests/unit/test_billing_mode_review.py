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


async def test_switch_on_legacy_one_to_one_claims_whole_row_first(monkeypatch, parents_of):
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


async def test_switch_marks_only_that_parent_as_agency_and_fee_counts_only_it(monkeypatch, parents_of):
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


@pytest.fixture
def parents_of(monkeypatch):
    """_ensure_share 要看 X 有幾個母帳案（BUG-12）：給測試指定。"""
    import routers.crm._shared as shared
    box = {"X": [("A", "母帳案 A")]}

    async def _links(session, ids):
        return {i: box.get(i, []) for i in ids}
    monkeypatch.setattr(shared, "mine_parent_links", _links)
    return box


async def test_legacy_n_to_one_without_shares_is_marked_pending_not_claimed(monkeypatch, parents_of):
    """BUG-12：沒分案記錄、但 X 連著兩個母帳案（對應表連的）→ 不能整筆認給第一個，要標待認領（409）。"""
    parents_of["X"] = [("A", "母帳案 A"), ("B", "母帳案 B")]
    t = SimpleNamespace(id="X", name="X", entity="mine", contract_amount=100000,
                        ledger_detail={"source": "源日", "split": {"剪接": 100000}}, updated_at=None)

    async def _link(session, p):
        return t
    monkeypatch.setattr(pl, "resolve_mine_link", _link)
    monkeypatch.setattr(ledger, "require_entity", lambda request, ent, level="": None)
    monkeypatch.setattr(pl, "_store_mirror_detail", lambda t, detail: setattr(t, "ledger_detail", detail))
    with pytest.raises(HTTPException) as e:
        await pl.apply_billing_mode(None, _parent("passthrough", contract=120000), request=None, old_mode="company")
    assert e.value.status_code == 409 and "認領" in e.value.detail
    assert t.contract_amount == 100000                       # 409 → PUT rollback，什麼都不落庫（旗標下次再算）


async def test_legacy_claim_is_capped_to_what_was_mirrored(monkeypatch, parents_of):
    """BUG-13：X 100,000 裡只有 30,000 是 A 鏡射來的（mirror_total）→ 認 A=30,000、工項不認；
    A 改後期代開 120,000 → X = 70,000（owner 自己的）＋ 120,000。"""
    from core.ledger_project import MIRROR_TOTAL_KEY, parent_shares
    t = SimpleNamespace(id="X", name="X", entity="mine", contract_amount=100000,
                        ledger_detail={"source": "源日", "split": {"剪接": 100000}, MIRROR_TOTAL_KEY: 30000}, updated_at=None)

    async def _link(session, p):
        return t
    monkeypatch.setattr(pl, "resolve_mine_link", _link)
    monkeypatch.setattr(ledger, "require_entity", lambda request, ent, level="": None)
    monkeypatch.setattr(pl, "_store_mirror_detail", lambda t, detail: setattr(t, "ledger_detail", detail))
    await pl.apply_billing_mode(None, _parent("passthrough", contract=120000), request=None, old_mode="company")
    assert t.contract_amount == 190000
    assert parent_shares(t.ledger_detail)["A"]["amount"] == 120000
    assert t.ledger_detail["split"] == {"剪接": 100000}            # 工項沒被認走、也沒被動


async def test_unlink_of_a_legacy_shape_link_drops_the_share_too(monkeypatch):
    """BUG-15：舊形狀（只有 source_project_id）的解除也要扣份額，不然後端基數算它、前端不算。"""
    from core.ledger_project import parent_shares, set_parent_share

    d, c = set_parent_share({"source": "源日"}, 0, "A", 40000, {"剪接": 40000}, source="源日")
    t = SimpleNamespace(id="X", name="X", entity="mine", contract_amount=c, ledger_detail=d, updated_at=None,
                        source_project_id="A")
    parent = SimpleNamespace(id="A", mine_link_id=None, updated_at=None)     # 舊形狀：母帳側沒指標

    async def _resolve(session, p):
        return t if p.id == "A" else None
    monkeypatch.setattr(pl, "resolve_mine_link", _resolve)
    monkeypatch.setattr(pl, "_store_mirror_detail", lambda t, detail: setattr(t, "ledger_detail", detail))

    class _S:
        async def get(self, model, pk):
            return None
    await pl._write_link(_S(), parent, None)
    assert t.contract_amount == 0 and parent_shares(t.ledger_detail) == {}


async def test_ensure_share_counts_the_incoming_parent_and_never_claims_owner_money_for_it(parents_of):
    """BUG-17：_ensure_share 在連結寫入前跑，parents 不含這次推進來的案。
    (a) 從沒連過的 X（owner 自己的 50,000）推 B → 不能把 50,000 認成 B 的；
    (b) X 對應表連著 A（沒份額）、推 B → A 的錢不能認成 B 的 → 待認領；
    (c) X 連著 A、A 自己重推 → 舊 1:1 認領照舊。"""
    from core.ledger_project import BY_PARENT_PENDING_KEY, parent_shares
    t = SimpleNamespace(id="X", contract_amount=50000, ledger_detail={"source": "源日", "split": {"剪接": 50000}})
    parents_of["X"] = []
    keep = await pl._ensure_share(None, t, dict(t.ledger_detail), "B")
    assert parent_shares(keep) == {} and keep.get(BY_PARENT_PENDING_KEY) is not True
    parents_of["X"] = [("A", "A")]
    keep = await pl._ensure_share(None, t, dict(t.ledger_detail), "B")
    assert parent_shares(keep) == {} and keep.get(BY_PARENT_PENDING_KEY) is True
    keep = await pl._ensure_share(None, t, dict(t.ledger_detail), "A")
    assert parent_shares(keep)["A"]["amount"] == 50000


async def test_old_shape_unlink_does_not_touch_a_parent_relinked_elsewhere():
    """BUG-20：M 上殘留舊指標 source_project_id=p0，但 p0 已經連到別的私帳案 Y → 解除 M 不能動到 Y。"""
    from tests.unit._srcscan import code_only, func_body, repo_src
    body = code_only(func_body(repo_src("routers/crm/project_map.py"), "async def set_parent_link("))
    assert "p0.mine_link_id in (None, m.id)" in body


async def test_linking_records_a_zero_share_so_later_claims_do_not_grab_owner_money(monkeypatch, parents_of):
    """BUG-25：對應表連結時就記一筆 0 份額 —— 之後「沒記錄」只剩真正的舊資料，legacy_claim 不會把 owner 的
    整筆合約認給那案；推送時舊份額 0 → 錢正常加進去。"""
    from core.ledger_project import parent_shares

    async def _sync(session, parent, mine, *, explicit=()):
        return False
    monkeypatch.setattr(pl, "_sync_pair", _sync)
    parents_of["X"] = []                                  # 從沒連過的 X
    mine = SimpleNamespace(id="X", contract_amount=50000, ledger_detail={"source": "源日", "split": {"剪接": 50000}},
                           source_project_id=None, updated_at=None)
    parent = SimpleNamespace(id="A", mine_link_id=None, updated_at=None)
    await pl._write_link(None, parent, mine)
    assert parent.mine_link_id == "X"
    assert parent_shares(mine.ledger_detail)["A"] == {"amount": 0, "split": {}, "synced_total": 0, "at": "", "source": "源日"}
    assert mine.contract_amount == 50000
    # 已有記錄（推送先寫了份額再連結）就不動
    mine.ledger_detail["by_parent"]["A"]["amount"] = 20000
    await pl._write_link(None, SimpleNamespace(id="A", mine_link_id=None, updated_at=None), mine)
    assert parent_shares(mine.ledger_detail)["A"]["amount"] == 20000


async def test_link_zero_share_takes_the_parents_billing_mode_not_x_source(monkeypatch, parents_of):
    """BUG-29：連結時的 0 份額案源要看**母帳的收款方式**，不能抄 X 的 —— X 已是代開時 company 母帳連上來
    份額不能被標成代開（之後推送會把整張客戶合約額加進 X、算進代辦費）。"""
    from core.ledger_project import parent_shares

    async def _sync(session, parent, mine, *, explicit=()):
        return False
    monkeypatch.setattr(pl, "_sync_pair", _sync)
    parents_of["X"] = []
    mine = SimpleNamespace(id="X", contract_amount=100000, ledger_detail={"source": "代開發票", "split": {}},
                           source_project_id=None, updated_at=None)
    await pl._write_link(None, SimpleNamespace(id="A", mine_link_id=None, billing_mode="company", updated_at=None), mine)
    assert parent_shares(mine.ledger_detail)["A"]["source"] == "源日"
    await pl._write_link(None, SimpleNamespace(id="B", mine_link_id=None, billing_mode="passthrough", updated_at=None), mine)
    assert parent_shares(mine.ledger_detail)["B"]["source"] == "代開發票"


async def test_revert_only_resets_x_source_when_x_was_agency(monkeypatch, parents_of):
    """BUG-34：X 本身是執行業務所得（owner 自己的案），對應表連了代開的 P 再換回 → X 的案源不能被洗成源日、
    個人稅款不能歸零。"""
    from core.ledger_project import parent_shares, set_parent_share, withholding

    from core.ledger_project import apply_source_fee
    d, c = set_parent_share({"source": "執行業務所得"}, 100000, "A", 0, {}, source="代開發票", claim=True)
    d = apply_source_fee(c, d)
    assert d["personal_tax"] == withholding(100000)
    t = SimpleNamespace(id="X", name="X", entity="mine", contract_amount=c, ledger_detail=d, updated_at=None)

    async def _link(session, p):
        return t
    monkeypatch.setattr(pl, "resolve_mine_link", _link)
    monkeypatch.setattr(ledger, "require_entity", lambda request, ent, level="": None)
    monkeypatch.setattr(pl, "_store_mirror_detail", lambda t, detail: setattr(t, "ledger_detail", detail))
    out = await pl.apply_billing_mode(None, _parent("company", contract=50000), request=None, old_mode="passthrough")
    assert out["action"] == "reverted"
    assert t.ledger_detail["source"] == "執行業務所得"
    assert t.ledger_detail["personal_tax"] == withholding(100000)
    assert parent_shares(t.ledger_detail)["A"]["source"] == "源日"


async def test_unlinking_the_last_agency_share_resets_x_source(monkeypatch):
    """BUG-35：X 被 B 翻成代開；解除 B 後沒有代開份額了 → X 的案源要跟換回一樣退回源日，owner 自己的錢不再被抽代辦費。"""
    from core.ledger_project import apply_source_fee, set_parent_share

    d, c = set_parent_share({"source": "代開發票"}, 20000, "A", 30000, {}, source="源日")
    d, c = set_parent_share(d, c, "B", 100000, {}, source="代開發票")
    d = apply_source_fee(c, d)
    assert d["invoice_fee"] == 9600                                  # (100,000 ＋ owner 20,000) × 8%
    t = SimpleNamespace(id="X", name="X", entity="mine", contract_amount=c, ledger_detail=d, updated_at=None)
    parent = SimpleNamespace(id="B", mine_link_id="X", updated_at=None)

    async def _resolve(session, p):
        return t
    monkeypatch.setattr(pl, "resolve_mine_link", _resolve)
    monkeypatch.setattr(pl, "_store_mirror_detail", lambda t, detail: setattr(t, "ledger_detail", detail))
    await pl._write_link(None, parent, None)
    assert t.contract_amount == 50000
    assert t.ledger_detail["source"] == "源日"
    assert (t.ledger_detail["invoice_fee"], t.ledger_detail["tax_fee"]) == (0, 0)
