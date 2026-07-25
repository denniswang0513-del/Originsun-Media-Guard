"""core.drive_map.nas_local_path — UNC → NAS 本機視角翻譯（R2 的一半）。

設定只存一份 master 視角的 UNC；在 NAS 容器上執行時翻成掛載點。
master／機隊沒設 env → 必須是完全 no-op（這條若破，全機隊路徑會被亂翻）。
"""
import pytest

from core.drive_map import nas_local_path, to_canonical_path, to_local_path


@pytest.fixture
def on_nas(monkeypatch):
    monkeypatch.setenv("NAS_LOCAL_SHARE_ROOT", "/share")


def test_no_env_is_noop(monkeypatch):
    monkeypatch.delenv("NAS_LOCAL_SHARE_ROOT", raising=False)
    p = r"\\192.168.1.132\Archive\10_工作側拍"
    assert nas_local_path(p) == p


def test_translates_on_nas(on_nas):
    assert (nas_local_path(r"\\192.168.1.132\Archive\10_工作側拍")
            == "/share/Archive/10_工作側拍")


def test_translates_nested_and_flips_separators(on_nas):
    assert (nas_local_path(r"\\192.168.1.132\Archive\a\b\c.jpg")
            == "/share/Archive/a/b/c.jpg")


def test_host_root_only(on_nas):
    assert nas_local_path(r"\\192.168.1.132") == "/share"


def test_other_host_untouched(on_nas):
    """別台主機的 UNC 在容器裡本來就碰不到 — 翻了等於假裝可達。"""
    p = r"\\192.168.1.130\storage 01\x"
    assert nas_local_path(p) == p


def test_non_unc_untouched(on_nas):
    for p in ("/share/already/local", r"C:\local\path", "", "relative/path"):
        assert nas_local_path(p) == p


def test_trailing_slash_in_base_normalized(monkeypatch):
    monkeypatch.setenv("NAS_LOCAL_SHARE_ROOT", "/share/")
    assert nas_local_path(r"\\192.168.1.132\Archive") == "/share/Archive"


# ── to_local_path：兩段翻譯的組合入口（單獨呼叫任一段都會漏另一段）──

def test_to_local_path_composes_both_stages(on_nas):
    """磁碟代號 → UNC → 本機掛載點。後台若輸入 T:\\… 也要在 NAS 上開得到。"""
    assert to_local_path(r"T:\專案\a.jpg") == "/share/Project_Longterm/專案/a.jpg"


def test_to_local_path_on_master_keeps_unc(monkeypatch):
    """master 沒設 NAS_LOCAL_SHARE_ROOT → 只做 stage 1，維持 UNC。"""
    monkeypatch.delenv("NAS_LOCAL_SHARE_ROOT", raising=False)
    assert to_local_path(r"T:\專案") == r"\\192.168.1.132\Project_Longterm\專案"


# ── to_canonical_path：DB 存的絕對路徑（stored_path）反向翻成 canonical UNC ──
# 收檔那台若存自己的本機路徑，別台讀不到（NAS 存 /share，master 讀不了）。

def test_canonical_on_nas_local_to_unc(on_nas):
    assert (to_canonical_path("/share/Archive/10_工作側拍/a/b.mp4")
            == r"\\192.168.1.132\Archive\10_工作側拍\a\b.mp4")


def test_canonical_on_master_is_noop(monkeypatch):
    """master 沒設 env → 本機寫的已是 UNC，原樣回。"""
    monkeypatch.delenv("NAS_LOCAL_SHARE_ROOT", raising=False)
    p = r"\\192.168.1.132\Archive\x\y.mp4"
    assert to_canonical_path(p) == p


def test_canonical_roundtrip_with_local(on_nas):
    """canonical ↔ local 互為反向：存 canonical，讀時 to_local_path 翻回。"""
    local = "/share/Archive/專案/clip.mp4"
    canon = to_canonical_path(local)
    assert canon == r"\\192.168.1.132\Archive\專案\clip.mp4"
    assert to_local_path(canon) == local


def test_canonical_outside_share_untouched(on_nas):
    for p in ("/tmp/x.mp4", r"C:\local\a.jpg", ""):
        assert to_canonical_path(p) == p
