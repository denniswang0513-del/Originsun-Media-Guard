# -*- coding: utf-8 -*-
"""routers/crm/invoice_files.py validate_root_dir 的**行為** —— 特徵測試（/health 2026-09-13）。

既有兩支測試只掃原始碼（確認 set_invoices_root 有呼叫它），這裡真的打：
2026-08-19 owner 少打反斜線、檔案默默存進主控端自己資料夾那件事，就靠這支的 422。
"""
import builtins
import os

import pytest
from fastapi import HTTPException

from routers.crm.invoice_files import validate_root_dir


def test_empty_root_is_a_no_op():
    validate_root_dir("")
    validate_root_dir(None)


@pytest.mark.parametrize("bad", [
    "192.168.1.132\\Archive\\inv",     # 少打兩個反斜線
    "\\192.168.1.132\\Archive\\inv",   # 少打一個（isabs 會說 True，這裡不能放行）
    "Archive/inv",
    "inv",
])
def test_relative_or_half_unc_is_422_with_the_full_path_hint(bad):
    with pytest.raises(HTTPException) as e:
        validate_root_dir(bad)
    assert e.value.status_code == 422
    assert "不是完整路徑" in e.value.detail


def test_drive_path_is_created_probed_and_probe_removed(tmp_path):
    root = str(tmp_path / "invoices" / "2026")
    validate_root_dir(root)
    assert os.path.isdir(root)
    assert not os.path.exists(os.path.join(root, ".originsun_write_test"))


def test_unc_prefix_passes_the_shape_check(monkeypatch):
    # 形狀對了才會走到 makedirs；把 makedirs／open／remove 攔住，只驗「兩個反斜線開頭」被當完整路徑
    calls = []
    real_open = builtins.open
    monkeypatch.setattr(os, "makedirs", lambda p, exist_ok=False: calls.append(p))
    monkeypatch.setattr(builtins, "open",
                        lambda p, *a, **k: real_open(os.devnull, "wb")
                        if str(p).endswith(".originsun_write_test") else real_open(p, *a, **k))
    monkeypatch.setattr(os, "remove", lambda p: None)
    validate_root_dir("\\\\192.168.1.132\\Archive\\inv")
    assert calls == ["\\\\192.168.1.132\\Archive\\inv"]


def test_uncreatable_dir_is_422_not_crash(tmp_path):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    with pytest.raises(HTTPException) as e:
        validate_root_dir(str(blocker / "sub"))      # 檔案底下建資料夾 → OSError
    assert e.value.status_code == 422
    assert "資料夾無法使用" in e.value.detail
