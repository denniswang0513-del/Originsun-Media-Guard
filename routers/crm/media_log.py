"""routers/crm/media_log.py — 影像紀錄（工作過程劇照/花絮收集）。

每專案一條 token 公開連結（/media-log.html?token=…）：現場同仁/合作夥伴
免登入上傳劇照、花絮影片；原始檔案存進管理員設定的影像紀錄資料夾
（settings.json 的 media_log.root）底下的專案子資料夾，縮圖轉 WebP 存
uploads/projects/{project_id}/media_log/ 供列表快速瀏覽。

token 模式照 showcase 編輯連結（_mint_showcase_edit_token / _verify_token_generic
同款）；影片縮圖/時長走專案根目錄的 ffmpeg.exe / ffprobe.exe
（core.subproc.run_capture — Windows SelectorEventLoop 不能用 asyncio subprocess）。

可測的純邏輯（資料夾名 sanitize / 分類正規化 / 副檔名白名單 / 撞名前綴）
抽成模組層純函式，單元測試在 tests/unit/test_media_log.py。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from datetime import datetime
from typing import Callable, Optional

from fastapi import File, Form, HTTPException, Request, UploadFile

from config import load_settings, save_settings
from core.subproc import run_capture

from ._shared import (router, _check_auth, _require_db, _get_factory, _now,
                      _UPLOAD_BASE, _save_image_as_webp, _verify_token_generic,
                      _mint_token_generic)

try:
    from ._shared import select, CrmProject, ProjectMediaLog, ProjectMediaFile
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同其他領域模組
    pass

# ── 常數 ─────────────────────────────────────────────────────

MEDIA_LOG_SCOPE = "media_log"  # 效期用 _mint_token_generic 預設（core.crm_logic 永久 token 常數）

# settings.json 無 media_log.categories 時的預設分類（單一常數 — 前後端契約）
DEFAULT_MEDIA_LOG_CATEGORIES = ["場勘照", "現場花絮", "劇照", "幕後", "其他"]

# settings.json 無 media_log.root 時的預設原檔資料夾（owner 指定 NAS 路徑，2026-07-20）。
# 搆不到（權限/斷線）時 root_set=False，前後台照常提示、不擋其他功能。
DEFAULT_MEDIA_LOG_ROOT = r"\\192.168.1.132\Archive\10_工作側拍"

# 上傳通知 digest：最後一次上傳後安靜 N 秒才發（拍攝中連傳不轟炸群組）
_NOTIFY_QUIET_SEC = 600

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif",
               ".cr2", ".cr3", ".nef", ".arw", ".dng"}
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mts"}
_MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500MB
_READ_CHUNK = 1024 * 1024  # 1MB — 分塊讀寫，大檔不整包進記憶體
_ILLEGAL_NAME_CHARS = '<>:"/\\|?*'  # Windows 檔名/資料夾名非法字元


# ── 純函式（單元測試對象 — 不碰磁碟/DB，存在性用 callable 注入）──

def _clean_name(s: str) -> str:
    """移除 Windows 非法字元與控制字元 + strip（folder/filename 共用的核心規則）。"""
    return "".join(
        ch for ch in str(s or "")
        if ch not in _ILLEGAL_NAME_CHARS and ord(ch) >= 32
    ).strip()


def _sanitize_folder_name(name: str) -> str:
    """專案名 → 合法 Windows 資料夾名；清完全空 → "project"。"""
    return _clean_name(name) or "project"


def _sanitize_filename(name: str) -> str:
    """上傳原檔名 → 合法檔名：先去路徑成分；清完全空 → "upload"
    （副檔名保留 — "." 不在非法清單）。"""
    base = os.path.basename(str(name or "").replace("\\", "/"))
    return _clean_name(base) or "upload"


def _norm_categories(cats) -> list:
    """分類清單正規化：逐項 strip、去空、去重（保序）。"""
    out: list = []
    for c in (cats or []):
        c = str(c).strip()
        if c and c not in out:
            out.append(c)
    return out


def _classify_ext(filename: str) -> Optional[str]:
    """副檔名白名單判定 → "image" / "video" / None（不允許）。"""
    ext = os.path.splitext(str(filename or ""))[1].lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    return None


def _dedup_filename(filename: str, exists: Callable[[str], bool],
                    now: Optional[datetime] = None) -> str:
    """同名檔已存在 → 前綴 "{YYYYmmdd_HHMMSS}_"。存在性用 callable 注入
    （單元測試不碰磁碟）；連時間戳版本也撞（同秒重傳）再摻短 uuid 保證不覆蓋。"""
    if not exists(filename):
        return filename
    ts = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    candidate = f"{ts}_{filename}"
    if not exists(candidate):
        return candidate
    return f"{ts}_{uuid.uuid4().hex[:8]}_{filename}"


# ── settings / 路徑 helpers ──────────────────────────────────

def _media_log_conf() -> tuple:
    """settings.json 的 media_log 區 → (root, categories)。
    root 空/未設 → DEFAULT_MEDIA_LOG_ROOT；categories 空/未設 → 預設分類。"""
    conf = load_settings().get("media_log") or {}
    root = str(conf.get("root") or "").strip() or DEFAULT_MEDIA_LOG_ROOT
    cats = _norm_categories(conf.get("categories")) or list(DEFAULT_MEDIA_LOG_CATEGORIES)
    return root, cats


def _root_set(root: str) -> bool:
    return bool(root and os.path.isdir(root))


def _make_folder_name(project_name: str, created: datetime,
                      taken: Optional[set] = None) -> str:
    """子資料夾名：{建立日期 YYYYMMDD}_{sanitize(專案名)}（owner 指定命名）。
    同日同名專案撞名 → 尾綴 -2、-3…（taken = 既有 folder_name 集合）。純函式可測。"""
    base = f"{created.strftime('%Y%m%d')}_{_sanitize_folder_name(project_name)}"
    name, i = base, 2
    while name in (taken or set()):
        name = f"{base}-{i}"
        i += 1
    return name


async def _ensure_folder_name(session, row, project_name: str) -> str:
    """取得/首次生成該專案的子資料夾名並固定存 DB（之後上傳都進同一夾，
    不會因日期變動而分家）。不 commit，caller 統一 commit。"""
    if row.folder_name:
        return row.folder_name
    taken = set((await session.execute(
        select(ProjectMediaLog.folder_name)
        .where(ProjectMediaLog.folder_name.isnot(None))
    )).scalars())
    row.folder_name = _make_folder_name(project_name, datetime.now(), taken)
    return row.folder_name


def _project_folder(root: str, folder_name: str) -> str:
    return os.path.join(root, folder_name)


def _thumb_dir_path(project_id: str) -> str:
    return os.path.join(_UPLOAD_BASE, "projects", project_id, "media_log")


def _thumb_dir(project_id: str) -> str:
    d = _thumb_dir_path(project_id)
    os.makedirs(d, exist_ok=True)
    return d


def _thumb_path(project_id: str, file_id: str) -> str:
    return os.path.join(_thumb_dir_path(project_id), f"{file_id}.webp")


def _thumb_url_of(project_id: str, file_id: str) -> str:
    return f"/uploads/projects/{project_id}/media_log/{file_id}.webp"


# 專案根：照全 codebase 慣例從 __file__ 推（routers/crm/ 的上上層）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _tool_path(name: str) -> Optional[str]:
    """ffmpeg.exe / ffprobe.exe 在專案根目錄；找不到 → None
    （跳過縮圖/時長，不擋上傳）。"""
    p = os.path.join(_PROJECT_ROOT, name)
    return p if os.path.isfile(p) else None


def _read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


# ── token helpers（照 showcase._mint_showcase_edit_token 模式）──

def _share_url(token: str) -> str:
    return f"/media-log.html?token={token}"


async def _mint_media_log_token(session, project_id: str, *,
                                reuse_existing: bool = False):
    """取得/產生影像紀錄分享 token（_shared._mint_token_generic 薄包裝，
    含 jwt_secret 輪替自癒）。回傳 (token, row)，不 commit。"""
    return await _mint_token_generic(
        session, ProjectMediaLog, project_id, MEDIA_LOG_SCOPE,
        reuse_existing=reuse_existing, row_defaults={"enabled": True})


async def _verify_media_log_token(session, token: str):
    """token → ProjectMediaLog row；enabled=False → 403（generic 行為）。"""
    return await _verify_token_generic(session, token, MEDIA_LOG_SCOPE,
                                       ProjectMediaLog, "enabled",
                                       require_editable=True)


# ── 上傳通知 digest（最後一傳後安靜 10 分鐘才發一則彙總）─────

_notify_state: dict = {}   # project_id -> {"count", "uploaders", "name", "task"}


def _schedule_upload_notify(project_id: str, project_name: str, uploader: str) -> None:
    """記一筆＋重置安靜計時器。單機記憶體即可（master 單行程；掉了頂多少一則通知）。"""
    st = _notify_state.setdefault(
        project_id, {"count": 0, "uploaders": set(), "name": project_name, "task": None})
    st["count"] += 1
    st["name"] = project_name
    if uploader:
        st["uploaders"].add(uploader)
    if st["task"] and not st["task"].done():
        st["task"].cancel()
    st["task"] = asyncio.create_task(_notify_after_quiet(project_id))


async def _notify_after_quiet(project_id: str) -> None:
    try:
        await asyncio.sleep(_NOTIFY_QUIET_SEC)
    except asyncio.CancelledError:
        return
    st = _notify_state.pop(project_id, None)
    if not st or not st["count"]:
        return
    try:
        from notifier import notify_tab_async
        await notify_tab_async(
            "media_log_upload",
            project_name=st["name"],
            file_count=st["count"],
            uploaders="、".join(sorted(st["uploaders"])) or "未具名")
    except Exception:
        pass  # 通知失敗不影響收檔


# ── serializers / 查詢 ───────────────────────────────────────

def _to_file_dict(f) -> dict:
    return {
        "id": f.id,
        "filename": f.filename or "",
        "thumb_url": f.thumb_url or "",
        "media_type": f.media_type or "image",
        "duration_sec": f.duration_sec,
        "category": f.category or "",
        "uploader_name": f.uploader_name or "",
        "created_at": f.created_at.isoformat() if f.created_at else None,
        "size_bytes": f.size_bytes or 0,
    }


async def _list_files(session, project_id: str) -> list:
    rows = (await session.execute(
        select(ProjectMediaFile)
        .where(ProjectMediaFile.project_id == project_id)
        .order_by(ProjectMediaFile.created_at.desc())
    )).scalars().all()
    return [_to_file_dict(f) for f in rows]


# ── 上傳管線（串流寫檔 + 縮圖）───────────────────────────────

async def _stream_to_disk(file: UploadFile, dest_path: str) -> int:
    """分塊寫原檔（1MB/塊）；累計超過 500MB → 刪半成品 → 413。回傳總 bytes。"""
    size = 0
    ok = False
    fh = await asyncio.to_thread(open, dest_path, "wb")
    try:
        while True:
            chunk = await file.read(_READ_CHUNK)
            if not chunk:
                ok = True
                break
            size += len(chunk)
            if size > _MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"檔案超過 {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限")
            await asyncio.to_thread(fh.write, chunk)
    finally:
        await asyncio.to_thread(fh.close)
        if not ok:
            try:
                await asyncio.to_thread(os.remove, dest_path)
            except OSError:
                pass
    return size


async def _webp_thumb_from_bytes(content: bytes, project_id: str, file_id: str,
                                 max_side: int = 0) -> str:
    """圖 bytes → uploads/.../{file_id}.webp，回縮圖 URL。PIL 失敗（.bin
    fallback）對縮圖沒意義（原檔已另存）→ 清掉、回空字串（image/video 共用尾段）。"""
    tdir = await asyncio.to_thread(_thumb_dir, project_id)
    saved = await asyncio.to_thread(
        _save_image_as_webp, content, tdir, file_id, max_side)
    if saved.endswith(".webp"):
        return _thumb_url_of(project_id, file_id)
    try:
        await asyncio.to_thread(os.remove, saved)
    except OSError:
        pass
    return ""


# PIL 必開不了的 RAW 格式 — 整檔（30-100MB+）讀進記憶體只為餵必敗的 PIL 太虧，
# 直接縮圖從缺。HEIC 不在此列（環境裝了 plugin 就吃得到，值得一試）。
_RAW_EXTS = {".cr2", ".cr3", ".nef", ".arw", ".dng"}


async def _make_image_thumb(stored_path: str, project_id: str, file_id: str) -> str:
    """原檔 → 長邊 1000px 內 WebP 縮圖。PIL 開不了（RAW/HEIC）→ 空字串
    （不擋上傳，僅無縮圖）。"""
    if os.path.splitext(stored_path)[1].lower() in _RAW_EXTS:
        return ""
    try:
        content = await asyncio.to_thread(_read_file_bytes, stored_path)
        return await _webp_thumb_from_bytes(content, project_id, file_id, 1000)
    except Exception:
        return ""


async def _probe_duration(stored_path: str) -> Optional[float]:
    """影片時長（秒）— 複用 services.footage_indexer._probe（同一份 ffprobe
    邏輯，sync 設計、執行緒跑）；讀不出 → None。"""
    try:
        from services.footage_indexer import _probe
        meta = await asyncio.to_thread(_probe, stored_path)
        return meta.get("duration_sec")
    except Exception:
        return None


async def _make_video_thumb(stored_path: str, project_id: str, file_id: str) -> str:
    """ffmpeg 抽第 1 秒一格（失敗 retry 第 0 秒）→ WebP 縮圖；
    ffmpeg 不在 / 全失敗 → 空字串（不擋上傳）。"""
    ffmpeg = _tool_path("ffmpeg.exe")
    if not ffmpeg:
        return ""
    tdir = await asyncio.to_thread(_thumb_dir, project_id)
    tmp_jpg = os.path.join(tdir, f"{file_id}_frame.jpg")
    try:
        for ss in ("1", "0"):
            rc, _out, _err = await run_capture(
                [ffmpeg, "-y", "-ss", ss, "-i", stored_path,
                 "-frames:v", "1", "-vf", "scale=1000:-2", tmp_jpg],
                timeout=120)
            if rc == 0 and os.path.isfile(tmp_jpg) and os.path.getsize(tmp_jpg) > 0:
                content = await asyncio.to_thread(_read_file_bytes, tmp_jpg)
                return await _webp_thumb_from_bytes(content, project_id, file_id)
    except Exception:
        pass
    finally:
        try:
            if os.path.isfile(tmp_jpg):
                os.remove(tmp_jpg)
        except OSError:
            pass
    return ""


# ── Admin Endpoints ─────────────────────────────────────────

@router.get("/projects/{project_id}/media-log")
async def get_project_media_log(project_id: str, request: Request):
    """影像紀錄管理面板 — 分享連結（自動 mint / 失效自癒重發）+ 資料夾設定
    + 檔案清單（created_at DESC）。"""
    _check_auth(request)
    _require_db()
    root, cats = _media_log_conf()
    factory = await _get_factory()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到專案")
        proj_name = proj.name or ""
        token, row = await _mint_media_log_token(
            session, project_id, reuse_existing=True)
        folder_name = await _ensure_folder_name(session, row, proj_name)
        enabled = bool(row.enabled)
        await session.commit()
        files = await _list_files(session, project_id)
    # root 可能是 NAS UNC 路徑 — isdir 在斷線時會卡秒級，不佔 event loop
    rs = await asyncio.to_thread(_root_set, root)
    return {
        "token": token,
        "share_url": _share_url(token),
        "enabled": enabled,
        "root": root,
        "root_set": rs,
        "project_folder": _project_folder(root, folder_name),
        "categories": cats,
        "files": files,
    }


@router.post("/projects/{project_id}/media-log/token")
async def reset_media_log_token(project_id: str, request: Request):
    """重置分享連結 — 強制產新 token（舊連結立即失效）。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到專案")
        token, _row = await _mint_media_log_token(
            session, project_id, reuse_existing=False)
        await session.commit()
    return {"token": token, "share_url": _share_url(token)}


@router.post("/projects/{project_id}/media-log/enabled")
async def set_media_log_enabled(project_id: str, request: Request):
    """公開連結總開關 — enabled=False 時三個 public 端點一律 403
    （殺青收檔期結束的「關閘」語意；重開即恢復，token 不變）。"""
    _check_auth(request)
    _require_db()
    body = await request.json()
    enabled = bool(body.get("enabled"))
    factory = await _get_factory()
    async with factory() as session:
        row = await session.get(ProjectMediaLog, project_id)
        if not row:
            raise HTTPException(status_code=404, detail="尚未建立影像紀錄（先開一次 tab）")
        row.enabled = enabled
        row.updated_at = _now()
        await session.commit()
    return {"ok": True, "enabled": enabled}


@router.get("/public/media-log/{token}/qr")
async def media_log_qr(token: str, base: str = ""):
    """分享連結 QR PNG — 現場立牌/投影掃了就傳。URL 由 token 伺服端組
    （base 只決定網域），不能拿來產任意內容的 QR。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        await _verify_media_log_token(session, token)
    base = (base or "").strip().rstrip("/")
    if base and not (base.startswith("http://") or base.startswith("https://")):
        raise HTTPException(status_code=400, detail="base 需為 http(s) 網址")
    url = f"{base}{_share_url(token)}"

    def _png() -> bytes:
        import io as _io

        import qrcode
        img = qrcode.make(url, box_size=8, border=2)
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    from starlette.responses import Response
    return Response(await asyncio.to_thread(_png), media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.post("/projects/{project_id}/media-log/settings")
async def update_media_log_settings(project_id: str, request: Request):
    """影像紀錄全域設定（root 資料夾 / 分類清單）— 寫 settings.json 的
    media_log 區。root / categories 是全站共用（不分專案），path 帶
    project_id 只是讓前端從專案面板順手改。缺的欄位不動（partial 語意）。"""
    _check_auth(request)
    body = await request.json()
    patch: dict = {}
    if "root" in body:
        root = str(body.get("root") or "").strip()
        if root and not await asyncio.to_thread(os.path.isdir, root):
            raise HTTPException(status_code=400,
                                detail=f"資料夾不存在或無法存取：{root}")
        patch["root"] = root
    if "categories" in body:
        patch["categories"] = _norm_categories(body.get("categories"))
    settings = load_settings()
    conf = dict(settings.get("media_log") or {})
    if patch:
        conf.update(patch)
        settings["media_log"] = conf
        save_settings(settings)
    return {"ok": True,
            "root": str(conf.get("root") or "").strip() or DEFAULT_MEDIA_LOG_ROOT,
            "categories": _norm_categories(conf.get("categories"))
                          or list(DEFAULT_MEDIA_LOG_CATEGORIES)}


@router.delete("/media-log/files/{file_id}")
async def delete_media_log_file(file_id: str, request: Request):
    """刪除影像紀錄檔案 — 原檔搬進同專案資料夾 _trash/ 子夾（不硬刪，
    誤刪可救；原檔已不在磁碟不報錯）、縮圖檔刪除、DB row 刪。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rec = await session.get(ProjectMediaFile, file_id)
        if not rec:
            raise HTTPException(status_code=404, detail="找不到檔案")
        stored_path = rec.stored_path or ""
        project_id = rec.project_id
        await session.delete(rec)
        await session.commit()

    def _dispose():
        # 原檔在 NAS/UNC 上 — 所有 stat/搬移集中一個執行緒跑，不佔 event loop
        if stored_path and os.path.isfile(stored_path):
            try:
                trash_dir = os.path.join(os.path.dirname(stored_path), "_trash")
                os.makedirs(trash_dir, exist_ok=True)
                dest = _dedup_filename(
                    os.path.basename(stored_path),
                    lambda n: os.path.exists(os.path.join(trash_dir, n)))
                shutil.move(stored_path, os.path.join(trash_dir, dest))
            except OSError:
                pass  # 搬失敗（鎖檔等）不擋刪除 — row 已刪，原檔留原位可人工清
        thumb = _thumb_path(project_id, file_id)
        if os.path.isfile(thumb):
            try:
                os.remove(thumb)
            except OSError:
                pass

    await asyncio.to_thread(_dispose)
    return {"ok": True}


# ── Public Endpoints（免登入，token 授權）────────────────────

@router.get("/public/media-log/{token}")
async def public_media_log_view(token: str):
    """分享連結頁資料 — 專案名 + 分類 + 檔案清單（enabled=False → 403）。"""
    _require_db()
    root, cats = _media_log_conf()
    factory = await _get_factory()
    async with factory() as session:
        row = await _verify_media_log_token(session, token)
        proj = await session.get(CrmProject, row.id)
        project_name = (proj.name or "") if proj else ""
        files = await _list_files(session, row.id)
    return {
        "project_name": project_name,
        "categories": cats,
        "upload_enabled": await asyncio.to_thread(_root_set, root),
        "max_upload_bytes": _MAX_UPLOAD_BYTES,  # 前端預檢/文案由此推導，免兩邊硬寫
        "files": files,
    }


@router.post("/public/media-log/{token}/upload")
async def public_media_log_upload(token: str, file: UploadFile = File(...),
                                  category: str = Form(""),
                                  uploader_name: str = Form("")):
    """免登入上傳 — 原檔進 {root}/{專案子夾}/、縮圖轉 WebP。回單筆 FILE dict。"""
    _require_db()
    orig_name = _sanitize_filename(file.filename or "")
    media_type = _classify_ext(orig_name)
    if not media_type:
        ext = os.path.splitext(orig_name)[1] or "（無副檔名）"
        raise HTTPException(status_code=400, detail=f"不支援的檔案格式：{ext}")
    root, _cats = _media_log_conf()
    if not await asyncio.to_thread(_root_set, root):
        raise HTTPException(status_code=503, detail="管理員尚未設定影像紀錄資料夾")
    factory = await _get_factory()
    async with factory() as session:
        row = await _verify_media_log_token(session, token)
        project_id = row.id
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到專案")
        proj_name = proj.name or ""
        folder_name = await _ensure_folder_name(session, row, proj_name)
        await session.commit()   # folder_name 首次生成要落庫（之後上傳同夾）
    # 原檔寫入（session 已關 — 大檔串流期間不佔 DB 連線）；
    # 目的夾 makedirs + 撞名探測都打 NAS/UNC — 集中一個執行緒跑
    folder = _project_folder(root, folder_name)

    def _prepare_dest() -> str:
        os.makedirs(folder, exist_ok=True)
        name = _dedup_filename(
            orig_name, lambda n: os.path.exists(os.path.join(folder, n)))
        return os.path.join(folder, name)

    stored_path = await asyncio.to_thread(_prepare_dest)
    final_name = os.path.basename(stored_path)
    size = await _stream_to_disk(file, stored_path)
    # 縮圖 / 時長（失敗不擋上傳）；影片的 ffprobe 與 ffmpeg 抽格彼此獨立 → 並行
    file_id = uuid.uuid4().hex
    duration = None
    if media_type == "video":
        duration, thumb = await asyncio.gather(
            _probe_duration(stored_path),
            _make_video_thumb(stored_path, project_id, file_id))
    else:
        thumb = await _make_image_thumb(stored_path, project_id, file_id)
    async with factory() as session:
        rec = ProjectMediaFile(
            id=file_id, project_id=project_id,
            filename=final_name, stored_path=stored_path,
            thumb_url=thumb, media_type=media_type,
            duration_sec=duration,
            category=str(category or "").strip()[:50],
            uploader_name=str(uploader_name or "").strip()[:100],
            size_bytes=size, created_at=_now(),
        )
        session.add(rec)
        await session.commit()
        _schedule_upload_notify(project_id, proj_name,
                                str(uploader_name or "").strip())
        return _to_file_dict(rec)


@router.get("/public/media-log/{token}/file/{file_id}")
async def public_media_log_file(token: str, file_id: str):
    """免登入下載/檢視原始檔 — file_id 不屬於該 token 專案 → 404。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        row = await _verify_media_log_token(session, token)
        rec = await session.get(ProjectMediaFile, file_id)
        if not rec or rec.project_id != row.id:
            raise HTTPException(status_code=404, detail="找不到檔案")
        stored_path = rec.stored_path or ""
        filename = rec.filename or os.path.basename(stored_path) or "file"
    if not stored_path or not await asyncio.to_thread(os.path.isfile, stored_path):
        raise HTTPException(status_code=404, detail="檔案已不存在")
    from starlette.responses import FileResponse
    return FileResponse(stored_path, filename=filename)
