# -*- coding: utf-8 -*-
"""公司案改成後期代開後：它 CRM 帳目裡**別人**的人員費用＝私帳的委外、行政雜支＝私帳的雜支（owner 2026-09-13）。

私帳主人自己那幾行是收入（mirror_lines 鏡射成工項），不算成本。假 session 依 SQL 文字回不同結果。
"""
from types import SimpleNamespace

import pytest

from routers import api_finance_projects as fp
from routers.crm import _shared as shared


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self


class _Session:
    """P（後期代開、連到 X）掛了：小明 30,000、王士源 50,000、沒指定 5,000 的人員費用；雜支 1,200（已請款 800 不算）。
    X 自己在 CRM 沒東西。"""

    def __init__(self):
        self.owner = SimpleNamespace(staff_id="WANG", modules=["finance_mine"])

    async def execute(self, stmt):
        t = str(stmt)
        if "users" in t:
            return _Res([self.owner])
        if "billing_mode" in t and "mine_link_id" in t:          # passthrough_parents 新形狀
            return _Res([("P", "X")])
        if "source_project_id" in t:                               # 舊形狀
            return _Res([])
        if "crm_project_expenses" in t:
            if "JOIN crm_projects" in t:                            # X 自己的
                return _Res([])
            return _Res([("P", 1200)])                              # P 的（claim_id 為空的）
        if "crm_project_cost_lines" in t:
            if "JOIN crm_projects" in t:
                return _Res([])
            assert "WANG" in t or ":coalesce" in t or "!=" in t     # 排除王士源那幾行
            return _Res([("P", 35000)])                             # 小明 30,000 ＋ 沒指定 5,000
        raise AssertionError("unexpected statement: " + t[:80])


async def test_passthrough_parent_costs_land_in_the_mine_project():
    out = await fp._crm_costs(_Session(), "mine", "X")
    assert out["X"] == {"misc": 1200, "outsource": 35000}


async def test_company_parent_costs_do_not(monkeypatch):
    """收款方式不是後期代開的母帳案：什麼都不算（跟以前一樣）。"""
    async def _none(session, mine_id=""):
        return {}
    monkeypatch.setattr(fp, "passthrough_parents", _none)
    out = await fp._crm_costs(_Session(), "mine", "X")
    assert out.get("X") is None


def test_itemised_list_tags_rows_from_the_passthrough_parent_and_skips_the_owners_lines():
    from tests.unit._srcscan import code_only, func_body, repo_src
    fn = code_only(func_body(repo_src("routers/api_finance_projects.py"), "async def _crm_lines("))
    assert "pmap = await passthrough_parents(session, project_id)" in fn
    assert "ids = [project_id] + list(pmap)" in fn
    assert "(l.actual_staff_id or l.estimated_staff_id) == owner" in fn        # 自己那幾行不列
    assert '"from": from_name.get(l.project_id, "")' in fn and '"from": from_name.get(e.project_id, "")' in fn
    js = repo_src("frontend/tabs/finance/subviews/projects.js")
    assert "來自 ' + esc(x.from) + '（後期代開）" in js


async def test_owner_lookup_is_the_single_finance_mine_user():
    class _S:
        async def execute(self, stmt):
            return _Res([SimpleNamespace(staff_id="WANG", modules=["finance_mine"]),
                         SimpleNamespace(staff_id="S9", modules=["crm_projects"])])
    assert await shared.mine_owner_staff_id(_S()) == "WANG"

    class _Two:
        async def execute(self, stmt):
            return _Res([SimpleNamespace(staff_id="A", modules=["finance_mine"]),
                         SimpleNamespace(staff_id="B", modules=["finance_mine"])])
    assert await shared.mine_owner_staff_id(_Two()) == ""


@pytest.mark.parametrize("owner", ["WANG", ""])
async def test_without_a_known_owner_every_line_counts_as_outsource(owner, monkeypatch):
    """認不出主人時不猜：整案人員費用都算委外（少算收入好過把別人的錢當成自己的）。"""
    async def _owner(session):
        return owner
    monkeypatch.setattr(fp, "mine_owner_staff_id", _owner)
    from tests.unit._srcscan import code_only, func_body, repo_src
    fn = code_only(func_body(repo_src("routers/api_finance_projects.py"), "async def _crm_costs("))
    assert '*([fn.coalesce(who, "") != owner] if owner else [])' in fn
