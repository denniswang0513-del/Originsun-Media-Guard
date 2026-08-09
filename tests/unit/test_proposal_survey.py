# -*- coding: utf-8 -*-
"""core/proposal_survey.py — 現況盤點表的純邏輯。

守的三件事：範本每次讀時對齊（加欄目時舊提案要長出來）、公開路徑看不到也
寫不到預算、自訂列有上限且刪不到範本列。
"""
import pytest

from core import proposal_survey as ps
from core import row_table


def test_empty_gives_full_template():
    rows = ps.rows(None)
    assert [r["key"] for r in rows] == list(ps.TEMPLATE_KEYS)
    assert all(r["content"] == "" and r["note"] == "" for r in rows)
    assert rows[0]["label"] == "客戶"


def test_stored_values_survive_and_new_template_keys_appear():
    # 只存了兩格（模擬「範本後來變長」的舊資料）
    stored = [{"key": "ta", "content": "25-40 女性", "note": "客戶自己說的"}]
    rows = ps.rows(stored)
    assert [r["key"] for r in rows] == list(ps.TEMPLATE_KEYS)   # 其餘欄目照樣長出來
    ta = next(r for r in rows if r["key"] == "ta")
    assert ta["content"] == "25-40 女性" and ta["note"] == "客戶自己說的"


def test_label_comes_from_template_not_db():
    stored = [{"key": "ta", "label": "被改壞的舊 label", "content": "x"}]
    assert next(r for r in ps.rows(stored) if r["key"] == "ta")["label"] == "TA"


def test_public_view_drops_private_rows():
    stored, err = ps.apply_patch(None, "budget", "content", "80 萬")
    assert err == ""
    assert any(r["key"] == "budget" for r in ps.rows(stored))
    pub = ps.rows(stored, public=True)
    assert not any(r["key"] == "budget" for r in pub)
    assert "80 萬" not in str(pub)          # 值也沒有從別的欄位漏出去


def test_public_cannot_write_private_row():
    stored, err = ps.apply_patch(None, "budget", "content", "偷改", public=True)
    assert err and stored is None          # 原樣退回、沒有落任何值


def test_public_can_write_normal_row():
    stored, err = ps.apply_patch(None, "ta", "content", "親子客群", public=True)
    assert err == ""
    assert next(r for r in ps.rows(stored) if r["key"] == "ta")["content"] == "親子客群"


@pytest.mark.parametrize("key,field", [("nope", "content"), ("ta", "answer"), ("", "content")])
def test_patch_rejects_unknown_key_or_field(key, field):
    stored, err = ps.apply_patch(None, key, field, "x")
    assert err and stored is None


def test_patch_rejects_oversized_cell():
    stored, err = ps.apply_patch(None, "ta", "content", "字" * (ps.MAX_TEXT + 1))
    assert err and stored is None


def test_add_remove_custom_row():
    stored, err = ps.add_row(None, "競品", key_seed="abc123def")
    assert err == ""
    extra = [r for r in ps.rows(stored) if r["key"].startswith("x-")]
    assert len(extra) == 1 and extra[0]["label"] == "競品"
    key = extra[0]["key"]

    stored, err = ps.apply_patch(stored, key, "content", "他牌 A")
    assert err == "" and next(r for r in ps.rows(stored) if r["key"] == key)["content"] == "他牌 A"

    # 自訂列排在範本列後面
    assert [r["key"] for r in ps.rows(stored)][:len(ps.TEMPLATE_KEYS)] == list(ps.TEMPLATE_KEYS)

    stored, err = ps.remove_row(stored, key)
    assert err == "" and not any(r["key"].startswith("x-") for r in ps.rows(stored))


def test_cannot_remove_template_row():
    stored, err = ps.remove_row(None, "ta")
    assert err and stored is None


def test_add_row_needs_label_and_has_cap():
    _, err = ps.add_row(None, "   ", key_seed="abcdef")
    assert err
    stored = None
    for i in range(row_table.MAX_EXTRA_ROWS):
        stored, err = ps.add_row(stored, f"欄{i}", key_seed=f"{i:06x}")
        assert err == ""
    _, err = ps.add_row(stored, "滿了", key_seed="ffffff")
    assert err


def test_garbage_stored_does_not_crash():
    for junk in ("not a list", [1, 2], [{"no_key": 1}], [{"key": ""}], [{"key": "ta", "content": 5}]):
        rows = ps.rows(junk)
        assert [r["key"] for r in rows] == list(ps.TEMPLATE_KEYS)


def test_unknown_extra_keys_are_dropped():
    # 認不得格式的 key（舊版/髒資料）不往外送，也不佔自訂列的位子
    assert not any(r["key"] == "weird" for r in ps.rows([{"key": "weird", "content": "x"}]))


def test_public_key_set_is_pinned():
    """🔴 加一列 TEMPLATE 就是一次對外揭露 —— 客戶看得到什麼要在這裡明著改，
    不能靠 review 時有人記得。改這條之前先想清楚那一列會不會出現在客戶眼前。"""
    assert ps.PUBLIC_KEYS == {
        "client", "brand_tone", "product", "product_tone", "ta", "goal",
        "style", "special", "count_length", "media", "deliver_date",
    }
    assert "budget" not in ps.PUBLIC_KEYS


def test_custom_rows_never_public():
    stored, err = ps.add_row(None, "內部毛利估算", key_seed="abc123")
    assert err == ""
    stored, err = ps.apply_patch(stored, [r["key"] for r in ps.rows(stored)][-1],
                                 "content", "內部數字")
    assert err == ""
    assert "內部數字" not in str(ps.rows(stored, public=True))
