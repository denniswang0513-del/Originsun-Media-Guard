"""/health 2026-09-18 特徵測試：`routers.api_me.leave_proof_dir` 的**執行期**行為。

既有 `test_leave_proof.py` 只掃原始碼（釘「走收據根目錄、過 to_local_path」），
這裡把實際算出來的路徑釘住：年月切片、空日期退成 nodate、根目錄一定先翻成本機視角。
不判斷對錯，只把現在的行為釘住；覺得多餘整檔刪掉即可。
"""
import os

import pytest


@pytest.fixture
def fixed_root(monkeypatch):
    import core.drive_map
    import routers.crm.costs
    monkeypatch.setattr(routers.crm.costs, "_receipts_root", lambda: r"\\NAS\Archive\00_零用金收據")
    monkeypatch.setattr(core.drive_map, "to_local_path", lambda p: p.replace(r"\\NAS\Archive", "T:"))
    return r"T:\00_零用金收據"


def test_leave_proof_dir_uses_year_month_subfolder(fixed_root):
    from routers.api_me import leave_proof_dir
    assert leave_proof_dir("2026-09-15") == os.path.join(fixed_root, "_假勤證明", "2026-09")


def test_leave_proof_dir_keeps_only_year_month_even_for_long_timestamps(fixed_root):
    from routers.api_me import leave_proof_dir
    assert leave_proof_dir("2026-09-15T08:30:00") == os.path.join(fixed_root, "_假勤證明", "2026-09")


@pytest.mark.parametrize("day", ["", None])
def test_leave_proof_dir_falls_back_to_nodate(fixed_root, day):
    from routers.api_me import leave_proof_dir
    assert leave_proof_dir(day) == os.path.join(fixed_root, "_假勤證明", "nodate")


def test_leave_proof_dir_translates_root_to_local_view(fixed_root):
    """DB 存的是 canonical UNC，開檔前一定過 to_local_path（NAS 容器與 master 視角不同）。"""
    from routers.api_me import leave_proof_dir
    out = leave_proof_dir("2026-01-02")
    assert out.startswith(fixed_root)
    assert "NAS" not in out
