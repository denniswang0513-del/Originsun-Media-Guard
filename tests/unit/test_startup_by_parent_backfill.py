# -*- coding: utf-8 -*-
"""db/startup_migrations._m22：私帳案收入分案記帳的舊資料回填 —— 用假 session 跑，不碰 DB。

1:1 整筆認成那一案；N:1 用掛給 owner 的成本行逐案認、對不上就標待認領；跑兩次結果一樣。
"""
from types import SimpleNamespace

import pytest

from core import state
from core.ledger_project import BY_PARENT_PENDING_KEY, parent_shares
from db import startup_migrations as sm


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self


class _Session:
    """只回答回填會問的三個問題：私帳案 id 清單、users、session.get。"""

    def __init__(self, projects, users):
        self.projects = projects          # id → SimpleNamespace
        self.users = users
        self.committed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        text = str(stmt)
        if "users" in text:
            return _Result(self.users)
        if "ledger_detail" in text:          # 整列的 select（回填改成一次撈有連結的私帳案）
            return _Result([p for p in self.projects.values() if p.entity == "mine"])
        return _Result([(pid,) for pid, p in self.projects.items() if p.entity == "mine"])

    async def get(self, model, pk):
        return self.projects.get(pk)

    async def commit(self):
        self.committed += 1


@pytest.fixture
def world(monkeypatch):
    projects = {
        "X1": SimpleNamespace(id="X1", entity="mine", contract_amount=50000,
                              ledger_detail={"source": "源日", "split": {"剪接": 50000}, "mirror_total": 50000, "mirror_at": "2026-09-01"}),
        "X2": SimpleNamespace(id="X2", entity="mine", contract_amount=120000,
                              ledger_detail={"source": "源日", "split": {"剪接": 120000}}),
        "X3": SimpleNamespace(id="X3", entity="mine", contract_amount=10000,
                              ledger_detail={"source": "源日", "split": {"剪接": 10000}}),
        # BUG-13：只有 30,000 是鏡射來的，70,000 是 owner 自己加的（非工項的合約金額）→ 只認 30,000
        "X4": SimpleNamespace(id="X4", entity="mine", contract_amount=100000,
                              ledger_detail={"source": "源日", "split": {"剪接": 100000}, "mirror_total": 30000}),
        "A": SimpleNamespace(id="A", entity="parent", contract_amount=0, billing_mode="company"),
        "B": SimpleNamespace(id="B", entity="parent", contract_amount=0, billing_mode="company"),
        "C": SimpleNamespace(id="C", entity="parent", contract_amount=80000, billing_mode="passthrough"),
        "D": SimpleNamespace(id="D", entity="parent", contract_amount=0, billing_mode="company"),
        "E": SimpleNamespace(id="E", entity="parent", contract_amount=0, billing_mode="company"),
    }
    users = [SimpleNamespace(staff_id="S1", modules=["finance_mine"]),
             SimpleNamespace(staff_id="S9", modules=["crm_projects"])]
    session = _Session(projects, users)

    async def _links(sess, ids):
        return {"X1": [("A", "母帳案 A")], "X2": [("B", "B"), ("C", "C")], "X3": [("D", "D"), ("E", "E")],
                "X4": [("F", "F")]}

    totals = {"B": ({"剪接": 40000}, 40000), "C": ({"剪接": 80000}, 80000),
              "D": ({"剪接": 9000}, 9000), "E": ({"剪接": 9000}, 9000)}     # D+E = 18,000 > X3 的 10,000 → 待認領

    async def _preview(sess, pid, sid):
        assert sid == "S1"
        split, total = totals[pid]
        return projects[pid], {"split": split, "total": total, "lines": []}, None

    import routers.crm._shared as shared
    import routers.crm.project_links as pl
    monkeypatch.setattr(shared, "mine_parent_links", _links)
    monkeypatch.setattr(pl, "_mirror_preview", _preview)
    monkeypatch.setattr(sm, "get_session_factory", lambda: (lambda: session))
    monkeypatch.setattr(state, "db_online", True)
    return session


async def test_backfill_claims_one_to_one_and_resolvable_n_to_one_and_flags_the_rest(world, capsys):
    await sm._m22_ledger_by_parent_backfill()
    x1, x2, x3 = world.projects["X1"], world.projects["X2"], world.projects["X3"]
    assert parent_shares(x1.ledger_detail) == {"A": {"amount": 50000, "split": {"剪接": 50000}, "synced_total": 50000,
                                                     "at": "2026-09-01", "source": "源日"}}   # 案源跟 X 走（代辦費分案）
    assert x1.contract_amount == 50000                              # claim 不動錢
    assert parent_shares(x2.ledger_detail)["B"]["amount"] == 40000
    assert parent_shares(x2.ledger_detail)["C"]["amount"] == 80000
    # 第 7 輪 #2：N:1 每案的案源看母帳的收款方式（C 是後期代開），不抄 X 的
    assert parent_shares(x2.ledger_detail)["B"]["source"] == "源日"
    assert parent_shares(x2.ledger_detail)["C"]["source"] == "代開發票"
    assert x2.ledger_detail["invoice_fee"] == 6400                 # 第 10 輪 #5：回填後代辦費照新規則落庫（80,000 × 8%）
    assert x2.amount_receivable == 120000 - 6400                  # 應收也重算
    assert x2.contract_amount == 120000 and x2.ledger_detail["split"] == {"剪接": 120000}
    assert x3.ledger_detail.get(BY_PARENT_PENDING_KEY) is True and not parent_shares(x3.ledger_detail)
    x4 = world.projects["X4"]
    # 只認鏡射來的 30,000，但工項一律認（分身的工項就是鏡射來的；不認會在下次同步時加倍）
    assert parent_shares(x4.ledger_detail)["F"] == {"amount": 30000, "split": {"剪接": 100000}, "synced_total": 30000, "at": "", "source": "源日"}
    assert x4.contract_amount == 100000 and x4.ledger_detail["split"] == {"剪接": 100000}
    assert world.committed == 1
    assert "1:1 2 案、N:1 認出 1 案、待認領 1 案" in capsys.readouterr().out


async def test_backfill_is_idempotent(world):
    await sm._m22_ledger_by_parent_backfill()
    snap = {k: (p.contract_amount, dict(p.ledger_detail)) for k, p in world.projects.items() if p.entity == "mine"}
    await sm._m22_ledger_by_parent_backfill()
    assert {k: (p.contract_amount, dict(p.ledger_detail)) for k, p in world.projects.items() if p.entity == "mine"} == snap


async def test_without_a_single_mine_owner_n_to_one_is_left_pending(world, monkeypatch):
    world.users.append(SimpleNamespace(staff_id="S2", modules=["finance_mine"]))   # 兩個 owner → 不猜
    await sm._m22_ledger_by_parent_backfill()
    assert world.projects["X2"].ledger_detail.get(BY_PARENT_PENDING_KEY) is True
    assert parent_shares(world.projects["X1"].ledger_detail)                    # 1:1 照認


async def test_one_broken_n_to_one_row_does_not_block_the_rest(world, monkeypatch):
    """收尾 review：任一筆 N:1 的 _mirror_preview 丟例外 → 那一筆標待認領，其他照認、照 commit。"""
    import routers.crm.project_links as pl
    real = pl._mirror_preview

    async def _boom(sess, pid, sid):
        if pid == "B":
            raise RuntimeError("cost lines table missing")
        return await real(sess, pid, sid)
    monkeypatch.setattr(pl, "_mirror_preview", _boom)
    await sm._m22_ledger_by_parent_backfill()
    assert parent_shares(world.projects["X1"].ledger_detail)                          # 1:1 照認
    assert world.projects["X2"].ledger_detail.get(BY_PARENT_PENDING_KEY) is True      # 壞的那筆待認領
    assert world.committed == 1
