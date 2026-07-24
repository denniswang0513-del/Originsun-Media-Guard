"""api_paste.py — 全站「貼上圖片」通用圖床上傳。

任何登入者（任一模組、Lv1 即可）可用：前端全域貼上層（js/shared/paste-image.js）
攔截 textarea 的圖片貼上 → POST /paste_upload → 轉 WebP（EXIF 方向校正 + 中繼資料
隨轉檔剝除、長邊 1600）→ 寫 settings `paste_assets.dir`（預設 NAS PasteAssets，
Assets_Nginx 容器 24/7 serve，不依賴 master）→ 回 {token, url}。

內容欄位只存 token（`paste:<hex>.webp`，不含網域）——渲染端以 GET /paste_config
的 base_url 組 <img src>，圖床搬家改 settings 一行即可，DB 一筆都不用動。
渲染白名單正則見 journal-core renderRich；後端 token 形狀 = paste: + 32 hex + .webp。
"""
import asyncio
import os
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile  # type: ignore

from config import load_settings
from core.auth import require_access_level
from core.image_utils import save_image_as_webp

router = APIRouter(prefix="/api/v1", tags=["paste"])

_MAX_BYTES = 10 * 1024 * 1024   # 貼圖上限 10MB（截圖/手機照綽綽有餘）
_MAX_SIDE = 1600                # 長邊縮到 1600（與官網 OG 圖同規格）


def _assets_conf() -> tuple:
    pa = load_settings().get("paste_assets", {}) or {}
    return (pa.get("dir") or "", (pa.get("base_url") or "").rstrip("/"))


@router.get("/paste_config")
async def paste_config():
    """渲染端取 <img> 基底（公開資訊，不需登入 — 只是一個對外網址）。"""
    _, base = _assets_conf()
    return {"base_url": base}


def _write_webp(content: bytes, dest_dir: str) -> str:
    """makedirs + 轉檔寫入（同步、可能碰 SMB — 一律在 to_thread 裡跑，
    NAS 慢/斷線只卡工作執行緒，不卡 event loop）。回傳實際寫出的檔案路徑。"""
    os.makedirs(dest_dir, exist_ok=True)
    return save_image_as_webp(content, dest_dir, uuid.uuid4().hex, max_side=_MAX_SIDE)


@router.post("/paste_upload")
async def paste_upload(file: UploadFile = File(...),
                       _user: dict = require_access_level(1)):
    dest, base = _assets_conf()
    if not dest or not base:
        raise HTTPException(status_code=503, detail="貼圖服務未設定（settings paste_assets）")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空檔案")
    if len(content) > _MAX_BYTES:
        raise HTTPException(status_code=400, detail="圖片超過 10MB 上限")

    try:
        saved = await asyncio.to_thread(_write_webp, content, dest)
    except OSError:
        raise HTTPException(status_code=503, detail="圖床目錄不可達（NAS 離線或無權限）")

    # image_utils 對開不動的內容會 fallback 寫 .bin — 貼圖情境=非圖片，拒收
    if saved.endswith(".bin"):
        try:
            os.remove(saved)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="無法辨識的圖片格式")

    fname = os.path.basename(saved)
    return {"token": f"paste:{fname}", "url": f"{base}/{fname}"}
