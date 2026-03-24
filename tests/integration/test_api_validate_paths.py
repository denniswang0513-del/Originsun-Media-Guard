"""
指令 15：遠端路徑驗證 API 整合測試
POST /api/v1/validate_paths — 驗證磁碟機與路徑是否存在
"""
import os
import pytest

# 實測參數
CARD_A = r"U:\20260205_寬微廣告_影片中文化\09_Export\01_Check"
CARD_B = r"R:\ProjectYaun\20260304_幾莫_鴻海科技獎短影音\09_Export\01_Check"
DEST = r"D:\Antigravity\OriginsunTranscode\test_zone"


def _find_nonexistent_drive():
    """找一個不存在的磁碟機字母"""
    for code in range(ord("Z"), ord("A") - 1, -1):
        letter = chr(code)
        if not os.path.exists(f"{letter}:\\"):
            return f"{letter}:\\"
    pytest.skip("所有磁碟機字母都存在，無法測試")


@pytest.mark.asyncio
async def test_validate_existing_path(async_client):
    """驗證存在的路徑 — C:\\Windows"""
    r = await async_client.post(
        "/api/v1/validate_paths",
        json={"paths": ["C:\\Windows"]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    info = data["results"]["C:\\Windows"]
    assert info["drive_exists"] is True
    assert info["path_exists"] is True


@pytest.mark.asyncio
async def test_validate_nonexistent_drive(async_client):
    """驗證不存在的磁碟機"""
    bad_path = _find_nonexistent_drive() + "test"
    r = await async_client.post(
        "/api/v1/validate_paths",
        json={"paths": [bad_path]},
    )
    assert r.status_code == 200
    info = r.json()["results"][bad_path]
    assert info["drive_exists"] is False
    assert info["path_exists"] is False


@pytest.mark.asyncio
async def test_validate_existing_drive_missing_path(async_client):
    """磁碟機存在但路徑不存在"""
    bad_path = "C:\\NonExistentFolder_Test_12345"
    r = await async_client.post(
        "/api/v1/validate_paths",
        json={"paths": [bad_path]},
    )
    assert r.status_code == 200
    info = r.json()["results"][bad_path]
    assert info["drive_exists"] is True
    assert info["path_exists"] is False


@pytest.mark.asyncio
async def test_validate_multiple_mixed(async_client):
    """混合驗證：存在 + 不存在磁碟機 + 不存在路徑"""
    bad_drive = _find_nonexistent_drive() + "fake"
    paths = [
        "C:\\Windows",
        "C:\\Users",
        bad_drive,
        "C:\\NonExistentFolder_Test_12345",
    ]
    r = await async_client.post(
        "/api/v1/validate_paths",
        json={"paths": paths},
    )
    assert r.status_code == 200
    results = r.json()["results"]
    # C:\Windows — 存在
    assert results["C:\\Windows"]["drive_exists"] is True
    assert results["C:\\Windows"]["path_exists"] is True
    # C:\Users — 存在
    assert results["C:\\Users"]["drive_exists"] is True
    assert results["C:\\Users"]["path_exists"] is True
    # 不存在磁碟機
    assert results[bad_drive]["drive_exists"] is False
    # 不存在路徑
    assert results["C:\\NonExistentFolder_Test_12345"]["drive_exists"] is True
    assert results["C:\\NonExistentFolder_Test_12345"]["path_exists"] is False


@pytest.mark.asyncio
async def test_validate_empty_paths(async_client):
    """空 paths 陣列 → 200，results 為空 dict"""
    r = await async_client.post(
        "/api/v1/validate_paths",
        json={"paths": []},
    )
    assert r.status_code == 200
    assert r.json()["results"] == {}


@pytest.mark.asyncio
async def test_validate_unc_path(async_client):
    """UNC 路徑 — drive 為 \\\\server\\share 格式"""
    unc = "\\\\192.168.1.132\\Originsun"
    r = await async_client.post(
        "/api/v1/validate_paths",
        json={"paths": [unc]},
    )
    assert r.status_code == 200
    info = r.json()["results"][unc]
    # os.path.splitdrive 對 UNC 回傳 ('\\\\server\\share', '\\rest')
    # drive 不為空，drive_exists 取決於 NAS 是否可連線
    assert "drive" in info
    assert "drive_exists" in info
    assert "path_exists" in info


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.path.exists(os.path.splitdrive(CARD_A)[0] + os.sep),
    reason=f"磁碟機 {os.path.splitdrive(CARD_A)[0]} 不存在（非原始開發機）",
)
async def test_validate_real_sources(async_client):
    """使用實測參數的 CARD_A 和 CARD_B 路徑"""
    r = await async_client.post(
        "/api/v1/validate_paths",
        json={"paths": [CARD_A, CARD_B]},
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[CARD_A]["drive_exists"] is True
    assert results[CARD_A]["path_exists"] is True
    assert results[CARD_B]["drive_exists"] is True
    assert results[CARD_B]["path_exists"] is True


@pytest.mark.asyncio
async def test_validate_real_dest(async_client):
    """使用測試目的地路徑 — 若不存在先建立 → 驗證 → 測試後清理"""
    created = False
    if not os.path.exists(DEST):
        os.makedirs(DEST, exist_ok=True)
        created = True
    try:
        r = await async_client.post(
            "/api/v1/validate_paths",
            json={"paths": [DEST]},
        )
        assert r.status_code == 200
        info = r.json()["results"][DEST]
        assert info["drive_exists"] is True
        assert info["path_exists"] is True
    finally:
        if created:
            try:
                os.rmdir(DEST)
            except OSError:
                pass
