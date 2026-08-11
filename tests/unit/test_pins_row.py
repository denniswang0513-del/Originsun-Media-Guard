# -*- coding: utf-8 -*-
"""proposal_assets.pins_row — 勾選/簡報掛在**哪一筆**提案上。

2026-08-11 之前這支只有「該專案最近更新的那筆」一條退路，於是一個專案並行
多筆提案時：後台「在看 B 卻標到 A」，而客戶那條更糟 —— 拿 B 的分享連結卻讀
到 A 的 `pins_public` 與勾選（B 自己設的「不對外」被忽略）。

這裡測的是那條挑列規則本身：給了 pid 就必須用那一筆、且必須驗證它真的屬於
這個專案（否則 pid 變成跨專案讀別人資料的入口）。
"""
import pytest

pytest.importorskip("sqlalchemy")

from fastapi import HTTPException                        # noqa: E402

from routers.crm.proposal_assets import pins_row         # noqa: E402


class _Prop:
    def __init__(self, pid, project_id, updated_at):
        self.id, self.project_id, self.updated_at = pid, project_id, updated_at


class _FakeSession:
    """只實作 pins_row 用到的兩件事：get(pk) 與「最近更新那筆」的查詢。"""

    def __init__(self, rows):
        self._rows = {r.id: r for r in rows}
        self.executed = 0

    async def get(self, _model, pk):
        return self._rows.get(pk)

    async def execute(self, _stmt):
        self.executed += 1
        newest = sorted(self._rows.values(), key=lambda r: r.updated_at, reverse=True)

        class _R:
            def scalars(_self):
                class _S:
                    def first(_s):
                        return newest[0] if newest else None
                return _S()
        return _R()


A = _Prop("aaa", "prj1", 1)
B = _Prop("bbb", "prj1", 2)          # 較晚更新 → 退路會挑到它
OTHER = _Prop("ccc", "prj2", 9)


async def test_pid_wins_over_recency():
    """給了 pid 就用那一筆 —— 不是「最近更新」。這條就是漂移的修正本身。"""
    ses = _FakeSession([A, B])
    got = await pins_row(ses, "prj1", "aaa")
    assert got is A
    assert ses.executed == 0          # 指名了就不該再去查最近更新那筆


async def test_no_pid_falls_back_to_newest():
    """沒 pid → 退回最近更新（單提案專案的既有行為，不能被這次改動打破）。"""
    ses = _FakeSession([A, B])
    assert await pins_row(ses, "prj1") is B


async def test_pid_from_another_project_is_404():
    """🔴 pid 必須驗證歸屬 —— 否則它就是跨專案讀別人勾選的入口。"""
    ses = _FakeSession([A, B, OTHER])
    with pytest.raises(HTTPException) as e:
        await pins_row(ses, "prj1", "ccc")
    assert e.value.status_code == 404


async def test_unknown_pid_is_404():
    ses = _FakeSession([A, B])
    with pytest.raises(HTTPException) as e:
        await pins_row(ses, "prj1", "nope")
    assert e.value.status_code == 404


async def test_project_without_proposals_returns_none():
    """沒有提案列 → None（呼叫端各自決定要不要 404）。"""
    assert await pins_row(_FakeSession([]), "prj1") is None
