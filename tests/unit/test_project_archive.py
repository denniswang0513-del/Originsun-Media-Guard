# -*- coding: utf-8 -*-
"""core/project_archive.py — 結案歸檔清單 + KPTA 的純邏輯。

守的三件事：範本每次讀時對齊、「不適用」要算進齊備度（沒童工的案子不該永遠
卡在 4/5）、掃描只往前推進不倒退人工標記。
"""
import pytest

from core import project_archive as pa
from core import row_table


def test_empty_gives_full_template_with_todo():
    rows = pa.rows(None)
    assert [r["key"] for r in rows] == list(pa.TEMPLATE_KEYS)
    assert all(r["status"] == pa.TODO for r in rows)
    assert rows[0]["label"] == "PPM資料" and rows[0]["folder"] == "01_PPM資料"
    assert "場記表" in rows[0]["hint"]


def test_stored_values_survive_and_labels_come_from_template():
    stored = [{"key": "stills", "label": "被改壞的", "status": pa.DONE, "note": "5 張"}]
    rows = pa.rows(stored)
    assert [r["key"] for r in rows] == list(pa.TEMPLATE_KEYS)
    s = next(r for r in rows if r["key"] == "stills")
    assert s["status"] == pa.DONE and s["note"] == "5 張" and s["label"] == "截圖"


def test_bad_status_falls_back_to_todo():
    assert pa.rows([{"key": "ppm", "status": "亂寫"}])[0]["status"] == pa.TODO


def test_progress_counts_na_as_done():
    stored = None
    for key in pa.TEMPLATE_KEYS:
        stored, err = pa.apply_patch(stored, key, "status", pa.DONE)
        assert err == ""
    assert pa.progress(stored) == {"done": 5, "total": 5, "ready": True}

    stored, _ = pa.apply_patch(stored, "license", "status", pa.NA)   # 沒童工的案子
    p = pa.progress(stored)
    assert p["ready"] is True, "「不適用」要算齊備，否則永遠卡著"

    stored, _ = pa.apply_patch(stored, "ppm", "status", pa.TODO)
    assert pa.progress(stored)["ready"] is False


def test_progress_on_empty_is_not_ready():
    p = pa.progress(None)
    assert p == {"done": 0, "total": 5, "ready": False}


@pytest.mark.parametrize("key,field,value", [
    ("nope", "status", pa.DONE),
    ("ppm", "answer", "x"),
    ("ppm", "status", "已收到"),          # 不在 STATUSES
])
def test_patch_rejects_bad_input(key, field, value):
    stored, err = pa.apply_patch(None, key, field, value)
    assert err and stored is None


def test_scan_marks_todo_rows_only():
    stored, _ = pa.apply_patch(None, "final", "status", pa.NA)      # 人工標「不適用」
    stored, marked = pa.apply_scan(stored, ["01_PPM資料", "02_完成檔", "不認得的夾"])
    by = {r["key"]: r["status"] for r in pa.rows(stored)}
    assert by["ppm"] == pa.DONE and marked == ["ppm"]
    assert by["final"] == pa.NA, "掃描不可以把人工標記倒退回去"
    assert by["stills"] == pa.TODO


def test_scan_with_nothing_changes_nothing():
    stored, marked = pa.apply_scan(None, [])
    assert marked == [] and pa.progress(stored)["done"] == 0


def test_add_remove_custom_row():
    stored, err = pa.add_row(None, "客戶簽收單", key_seed="abc123")
    assert err == ""
    extra = [r for r in pa.rows(stored) if r["key"].startswith("x-")]
    assert len(extra) == 1 and extra[0]["label"] == "客戶簽收單" and extra[0]["folder"] == ""
    assert pa.progress(stored)["total"] == 6

    stored, err = pa.remove_row(stored, extra[0]["key"])
    assert err == "" and pa.progress(stored)["total"] == 5


def test_cannot_remove_template_row():
    stored, err = pa.remove_row(None, "ppm")
    assert err and stored is None


def test_add_row_cap():
    stored = None
    for i in range(row_table.MAX_EXTRA_ROWS):
        stored, err = pa.add_row(stored, f"項目{i}", key_seed=f"{i:06x}")
        assert err == ""
    _, err = pa.add_row(stored, "滿了", key_seed="ffffff")
    assert err


def test_kpta_roundtrip_and_validation():
    assert pa.kpta(None) == {k: "" for k in pa.KPTA_KEYS}
    d, err = pa.apply_kpta(None, "keep", "分鏡先給客戶看，改稿次數少一半")
    assert err == "" and d["keep"].startswith("分鏡")
    assert d["action"] == ""

    _, err = pa.apply_kpta(None, "nope", "x")
    assert err
    _, err = pa.apply_kpta(None, "keep", "字" * (pa.KPTA_MAX + 1))
    assert err


def test_garbage_does_not_crash():
    for junk in ("nope", [1], [{"no_key": 1}], [{"key": ""}], [{"key": "ppm", "note": 5}]):
        assert [r["key"] for r in pa.rows(junk)] == list(pa.TEMPLATE_KEYS)
    assert pa.kpta("not a dict") == {k: "" for k in pa.KPTA_KEYS}
