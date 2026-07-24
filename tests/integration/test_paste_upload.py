"""貼圖圖床 API（routers/api_paste.py）整合測試。

真檔案 I/O 走 tmp_path（不碰 NAS）；token 形狀 paste:<32hex>.webp 是前端
renderRich 白名單的契約——守衛測試直接從 paste-image.js 抽出 PASTE_RE 字面、
拿真實後端 token 驗 match（後端改命名 → 前端沒跟上 → 這裡紅，不會靜默退純文字）。
"""
import io
import re
from pathlib import Path

import pytest

from config import load_settings, save_settings
from core.auth import create_token

_TOKEN_RE = re.compile(r"^paste:[0-9a-f]{32}\.webp$")


def _frontend_paste_re():
    """從 paste-image.js 原始碼抽 PASTE_RE（JS 正則字面在此與 Python re 相容）。"""
    src = (Path(__file__).resolve().parents[2]
           / "frontend" / "js" / "shared" / "paste-image.js").read_text(encoding="utf-8")
    m = re.search(r"const PASTE_RE = /(.+)/g;", src)
    assert m, "paste-image.js 找不到 PASTE_RE 定義"
    return re.compile(m.group(1))


def _png_bytes() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def user_headers(tmp_settings):
    tok = create_token({"sub": "employee", "username": "employee",
                        "access_level": 1, "modules": ["journal"]})
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def paste_dir(tmp_settings, tmp_path):
    """把 paste_assets 指到 tmp（預設值是 NAS UNC，測試環境碰不得）。"""
    d = tmp_path / "paste_assets"
    s = load_settings()
    s["paste_assets"] = {"dir": str(d), "base_url": "/uploads/paste/"}
    save_settings(s)
    return d


async def test_upload_requires_auth(async_client, paste_dir):
    r = await async_client.post(
        "/api/v1/paste_upload",
        files={"file": ("x.png", _png_bytes(), "image/png")})
    assert r.status_code == 401


async def test_upload_ok_token_shape_and_webp(async_client, paste_dir, user_headers):
    r = await async_client.post(
        "/api/v1/paste_upload", headers=user_headers,
        files={"file": ("x.png", _png_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert _TOKEN_RE.match(d["token"]), d["token"]
    fname = d["token"].split(":", 1)[1]
    # base_url 尾斜線被正規化，url = base + / + 檔名
    assert d["url"] == f"/uploads/paste/{fname}"
    saved = paste_dir / fname
    assert saved.exists()
    from PIL import Image
    assert Image.open(saved).format == "WEBP"
    # 跨語言契約守衛：真實後端 token 必須被前端 PASTE_RE 白名單吃到（含檔名捕獲）
    fe = _frontend_paste_re()
    m = fe.search(f"![圖片]({d['token']})")
    assert m, f"前端 PASTE_RE 吃不到後端 token：{d['token']}"
    assert m.group(2) == fname


async def test_upload_rejects_non_image(async_client, paste_dir, user_headers):
    r = await async_client.post(
        "/api/v1/paste_upload", headers=user_headers,
        files={"file": ("x.png", b"definitely not an image", "image/png")})
    assert r.status_code == 400
    # fallback .bin 不能留在圖床
    assert not list(paste_dir.glob("*.bin"))


async def test_upload_rejects_oversize(async_client, paste_dir, user_headers):
    r = await async_client.post(
        "/api/v1/paste_upload", headers=user_headers,
        files={"file": ("x.png", b"\x89PNG" + b"0" * (10 * 1024 * 1024), "image/png")})
    assert r.status_code == 400


async def test_upload_503_when_unconfigured(async_client, user_headers, tmp_settings):
    s = load_settings()
    s["paste_assets"] = {"dir": "", "base_url": ""}
    save_settings(s)
    r = await async_client.post(
        "/api/v1/paste_upload", headers=user_headers,
        files={"file": ("x.png", _png_bytes(), "image/png")})
    assert r.status_code == 503


async def test_paste_config_public(async_client, paste_dir):
    r = await async_client.get("/api/v1/paste_config")
    assert r.status_code == 200
    assert r.json()["base_url"] == "/uploads/paste"   # 尾斜線已剝
