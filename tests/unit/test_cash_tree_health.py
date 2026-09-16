# -*- coding: utf-8 -*-
"""/health 2026-09-17 特徵測試：釘住 `core/cash_tree.index_paths`（覆蓋 33%；`build_tree`／`flatten`
已有 test_cash_tree.py 釘，`index_paths` 沒有）。它是「節點 id → 根到自己的名稱路徑」的唯一 walk，
ORM 端與 asyncpg 端（scripts/_common.load_taxonomy_tree）共用，環保險只長在這一份上。只釘現況；整檔可刪。
"""
from core.cash_tree import ROOT, index_paths


def test_paths_run_from_root_to_self_and_root_parent_is_empty_string_or_none():
    rows = [("a", None, "家用"), ("b", "a", "變動支出"), ("c", "b", "醫療保健"), ("d", ROOT, "公司")]
    out = index_paths(rows)
    assert out == {"a": ["家用"], "b": ["家用", "變動支出"], "c": ["家用", "變動支出", "醫療保健"], "d": ["公司"]}


def test_orphan_keeps_only_its_own_name_instead_of_disappearing():
    # 跟 build_tree 不同：build_tree 會把孤兒整支丟掉，index_paths 給它一條只剩自己的路徑
    # （歷史列掛在被刪掉父節點下的節點，列表仍要印得出名字）
    out = index_paths([("x", "gone", "孤兒"), ("y", "x", "孤兒的小孩")])
    assert out["x"] == ["孤兒"] and out["y"] == ["孤兒", "孤兒的小孩"]


def test_cycle_does_not_recurse_forever():
    out = index_paths([("a", "b", "A"), ("b", "a", "B")])
    # 兩個都算得出來、都是有限長度；誰先被 walk 決定誰是「根」
    assert set(out) == {"a", "b"}
    assert all(1 <= len(v) <= 2 for v in out.values())
    # 現況：自己指自己的節點會把名字印兩次（walk 起手 seen 是空的，所以允許回到自己一次才停）——
    # 有限、不遞迴，只是路徑多一節；後台編輯做不出這種資料，釘住只為讓人看見
    assert index_paths([("s", "s", "自環")]) == {"s": ["自環", "自環"]}


def test_order_of_rows_does_not_change_paths_and_empty_input_is_empty():
    fwd = [("a", None, "A"), ("b", "a", "B"), ("c", "b", "C")]
    assert index_paths(fwd) == index_paths(list(reversed(fwd)))
    assert index_paths([]) == {}
