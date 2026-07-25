"""routers/crm/media_log.py — 影像紀錄（工作過程劇照/花絮收集）。

每專案一條 token 公開連結（/media-log.html?token=…）：現場同仁/合作夥伴
免登入上傳劇照、花絮影片；原始檔案存進管理員設定的影像紀錄資料夾
（單一真相在 DB，見 _media_log_conf）底下的專案子資料夾，縮圖轉 WebP 存
uploads/projects/{project_id}/media_log/ 供列表快速瀏覽。

⚠ 這個模組**兩台機器都在跑**：master（後台管理 + QR + 影片縮圖補算）與 NAS
   對外容器（public_router 那 4 個免登入端點，master 關機也不受影響）。
   改動時記得兩邊都會吃到；只有 master 有 ffmpeg 與 qrcode 套件。

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
import tempfile
import uuid
from datetime import datetime
from typing import Callable, Optional

from fastapi import File, Form, HTTPException, Request, UploadFile

from config import load_settings
from core.assets_host import assets_target
from core.drive_map import to_canonical_path, to_local_path
from core.subproc import run_capture

# public_router = 對外白名單（正本在 _shared，全套件共用一個）。本模組的 4 個
# 免登入端點掛它 → NAS 對外容器只掛這組，其餘 160+ 個 CRM 端點不會對外。
# ⚠ QR 端點雖然也免登入，但**只有後台在用**（公開頁一次都沒呼叫）且需要 qrcode
#   套件（對外容器沒裝）→ 留在 router，刻意不進 public_router。
from ._shared import (router, public_router, _check_auth, _require_db,
                      _get_factory, _now, _UPLOAD_BASE, save_webp_or_none,
                      _verify_token_generic, _mint_token_generic)

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

# 上傳通知 digest：最後一次上傳後安靜 N 秒才發（拍攝中連傳不轟炸群組）。
# 實際發送在 services/media_log_catchup.py（master 掃 DB），常數留這裡當契約。
NOTIFY_QUIET_SEC = 600

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

# 設定的單一真相 = DB（website_settings key-value 表；master 與 NAS 對外容器
# 連的是同一個庫）。這個功能兩台機器都在跑，設定各存各的 = 照片安靜地散在兩個
# 資料夾且不會報錯 —— 比整個功能壞掉更難發現。沿用既有 key-value 表而不另建一張，
# 因為它已經是本系統唯一的「設定存 DB」機制（表名帶 website 是歷史，非領域限定）。
_SETTING_PREFIX = "media_log."
_SETTING_KEY_ROOT = "media_log.root"
_SETTING_KEY_CATS = "media_log.categories"
# 分享連結的公開網域 = 官網既有的 seo.site_url（同一個對外站，不另開第二份真相）。
# ⚠ 一定要是 canonical 的 www：apex 在 nginx 會 301，而 301 打在 POST 上傳會壞。
_SETTING_KEY_SITE_URL = "seo.site_url"
_FALLBACK_SITE_URL = "https://www.originsun-studio.com"


async def _db_settings() -> dict:
    """DB 裡與影像紀錄有關的設定（media_log.* + seo.site_url）。
    DB 不可用 → {}（呼叫端退 settings.json / 常數）。

    ⚠ 一個 request 只該呼叫一次，把結果往下傳 —— 公開頁每個訪客、每張上傳的
      照片都會走到這裡，重複查同一批 key 是白花的 round-trip。
    """
    try:
        from services.website import settings_service
        factory = await _get_factory()
        async with factory() as session:
            out = await settings_service.get_prefixed(session, _SETTING_PREFIX)
            out[_SETTING_KEY_SITE_URL] = (
                await settings_service.get_all_settings(session)
            ).get(_SETTING_KEY_SITE_URL)
        return out
    except Exception:
        return {}


def _conf_from(db: dict) -> tuple:
    """(root, categories) — root 已翻成「執行本機的視角」，可直接拿去開檔。

    存的一律是 master 視角的 UNC（單一真相），本機視角由 core.drive_map.to_local_path
    翻譯 —— 同一個實體資料夾在 Windows 與 NAS 上路徑語法不同，只存一份字串
    而不翻譯，必然有一台指到不存在的路徑。
    DB 缺值才退 settings.json 的舊 media_log 區（既有安裝無痛升級用；後台存檔
    會寫進 DB，之後這條路就不再走 —— 故意懶算，穩態下不碰檔案系統）。
    """
    raw_root = str(db.get(_SETTING_KEY_ROOT) or "").strip()
    raw_cats = db.get(_SETTING_KEY_CATS)
    if not raw_root or raw_cats is None:
        conf = load_settings().get("media_log") or {}
        raw_root = raw_root or str(conf.get("root") or "").strip()
        raw_cats = raw_cats if raw_cats is not None else conf.get("categories")
    cats = _norm_categories(raw_cats) or list(DEFAULT_MEDIA_LOG_CATEGORIES)
    return to_local_path(raw_root or DEFAULT_MEDIA_LOG_ROOT), cats


async def _media_log_conf() -> tuple:
    """(root, categories) — 只需要設定、不需要整份 db dict 的呼叫端用這個。"""
    return _conf_from(await _db_settings())


async def media_log_health() -> dict:
    """{root, writable} 或 {error} — 歸檔區可達性自我回報。

    NAS 對外容器的 /healthz 與後台狀態面板共用：兩台機器指到不同資料夾時，
    照片會安靜散落且不報錯，只能靠「各自回報實際在用的路徑」才看得見。
    """
    try:
        root, _cats = await _media_log_conf()
        return {"root": root, "writable": await asyncio.to_thread(_root_set, root)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


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


# ── 縮圖：共用圖床的 medialog 命名空間（core/assets_host.py）──
# 為什麼不留在各機的 uploads/：這個功能兩台機器都在跑（NAS 收檔、master 補算
# 影片縮圖），縮圖各寫各的會變成「後台看得到、公開頁看不到」。放圖床 = 兩邊寫
# 同一份、兩邊用同一個絕對網址讀，master 關機也還在。
# 順帶斷開一條意外耦合：舊落點 uploads/projects/ 會被官網 rebuild 的 uploads
# 同步順手複製到 NAS（rebuild_service._SYNCED_UPLOAD_DIRS），影像紀錄的縮圖
# 等於寄生在官網發布流程上。
_THUMB_NAMESPACE = "medialog"


def _thumb_base(project_id: str, file_id: str) -> str:
    """圖床內的檔名（不含副檔名 — save_webp_or_none 自己加 .webp）。
    扁平命名空間 → 帶專案前綴才看得出歸屬、也不會撞。"""
    return f"{project_id}_{file_id}"


def _thumb_target(project_id: str, file_id: str) -> tuple:
    """(圖床本機目錄, base_name, 對外絕對網址)；圖床未設定 → ("", "", "")。"""
    d, base = assets_target(_THUMB_NAMESPACE)
    if not d or not base:
        return "", "", ""
    name = _thumb_base(project_id, file_id)
    return d, name, f"{base}/{name}.webp"


def _legacy_thumb_path(project_id: str, file_id: str) -> str:
    """舊落點（各機本地 uploads/）—— 只用於刪除時一併清掉殘留。"""
    return os.path.join(_UPLOAD_BASE, "projects", project_id, "media_log",
                        f"{file_id}.webp")


def _remove_thumbs(project_id: str, file_id: str) -> None:
    """刪縮圖 — 圖床與舊落點都清（同步；呼叫端在 to_thread 內）。
    搬家期間兩處可能各有一份，只清一邊會留孤兒。"""
    d, name, _url = _thumb_target(project_id, file_id)
    host = os.path.join(d, name + ".webp") if d else ""
    for p in (host, _legacy_thumb_path(project_id, file_id)):
        if not p:
            continue
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


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

def _share_url(token: str, base: str) -> str:
    """分享連結。base = 對外站網域（NAS 那份，master 關機也開得起來）。
    **已發出的舊連結一律不受影響** —— 它們指向 master，而 master 的公開端點
    完整保留；只有新發出的連結會指向對外站。"""
    return f"{str(base or '').rstrip('/')}/media-log.html?token={token}"


def _site_url_from(db: dict) -> str:
    """對外站網域：官網既有的 seo.site_url，沒設退 canonical www 常數。"""
    return str(db.get(_SETTING_KEY_SITE_URL) or _FALLBACK_SITE_URL).rstrip("/")


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

# 上傳通知不在收檔端發 —— 收檔現在多半發生在 NAS 對外容器，那裡沒有 notifier
# 模組、也沒有 settings.json 裡的 webhook（原本的行程內計時器在那邊是靜默失效）。
# 改由 master 定期掃 DB 補發（services/media_log_catchup.py），單一路徑不會重複發。


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


# ── 資料夾 → DB 同步（owner 直接在資料夾刪原檔 → 牆上同步消失）──

def _find_missing(entries: list, isdir: Callable[[str], bool],
                  isfile: Callable[[str], bool]) -> list:
    """entries=[(file_id, stored_path)] → 原檔已不存在的 file_id 清單。

    🔴 安全鐵則：所屬資料夾本身搆不到（NAS 斷線/未掛）→ 該資料夾整批跳過，
    絕不因連線問題誤刪記錄；只有「資料夾在、檔案不在」才視為真被刪。
    純函式（isdir/isfile 注入），單元測試對象。"""
    folder_ok: dict = {}
    missing = []
    for file_id, sp in entries:
        if not sp:
            continue
        folder = os.path.dirname(sp)
        if folder not in folder_ok:
            folder_ok[folder] = isdir(folder)
        if folder_ok[folder] and not isfile(sp):
            missing.append(file_id)
    return missing


_reconcile_last: dict = {}          # project_id -> monotonic 時間戳（public 端限流）
_RECONCILE_COOLDOWN_SEC = 30


async def _reconcile_files(factory, project_id: str, *, throttled: bool = False) -> int:
    """比對 DB 記錄與磁碟原檔，移除已被刪的（含縮圖）。回傳移除數。
    throttled=True（public 端每次 GET 都打）→ 每專案最多 30 秒一次，
    admin 端不限流（開 tab 就看到最新真相）。"""
    import time
    if throttled:
        now = time.monotonic()
        if now - _reconcile_last.get(project_id, 0.0) < _RECONCILE_COOLDOWN_SEC:
            return 0
        _reconcile_last[project_id] = now
    async with factory() as session:
        rows = (await session.execute(
            select(ProjectMediaFile.id, ProjectMediaFile.stored_path)
            .where(ProjectMediaFile.project_id == project_id)
        )).all()
    # stored_path 存 canonical UNC → 翻成本機視角再檢查存在性（NAS/master 各自）
    entries = [(r[0], to_local_path(r[1] or "")) for r in rows]
    if not entries:
        return 0
    missing = await asyncio.to_thread(
        _find_missing, entries, os.path.isdir, os.path.isfile)
    if not missing:
        return 0
    async with factory() as session:
        for fid in missing:
            rec = await session.get(ProjectMediaFile, fid)
            if rec:
                await session.delete(rec)
        await session.commit()

    def _rm_thumbs():
        for fid in missing:
            _remove_thumbs(project_id, fid)

    await asyncio.to_thread(_rm_thumbs)
    return len(missing)


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
    """圖 bytes → 圖床的 medialog 命名空間，回縮圖的對外絕對網址。PIL 失敗
    （非圖）對縮圖沒意義（原檔已另存）→ 回空字串（image/video 共用尾段）。"""
    d, name, url = _thumb_target(project_id, file_id)
    if not d:
        return ""                       # 圖床未設定 → 無縮圖，不擋上傳
    saved = await asyncio.to_thread(save_webp_or_none, content, d, name, max_side)
    return url if saved else ""


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
        return ""      # 對外容器沒帶 ffmpeg — 交給 master 的補算工作事後補上
    # 中繼影格寫本機暫存，不寫圖床（那裡只該有成品）
    tmp_jpg = os.path.join(tempfile.gettempdir(),
                           f"medialog_{project_id}_{file_id}.jpg")
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
    db = await _db_settings()          # 一個 request 只查一次（root/cats 與分享網域共用）
    root, cats = _conf_from(db)
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
    # 資料夾→DB 同步（admin 端不限流 — 開 tab 就看到最新真相）
    await _reconcile_files(factory, project_id)
    async with factory() as session:
        files = await _list_files(session, project_id)
    # root 可能是 NAS UNC 路徑 — isdir 在斷線時會卡秒級，不佔 event loop
    rs = await asyncio.to_thread(_root_set, root)
    return {
        "token": token,
        "share_url": _share_url(token, _site_url_from(db)),
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
    return {"token": token,
            "share_url": _share_url(token, _site_url_from(await _db_settings()))}


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
    # QR 印出來要能被現場手機掃 → 一律用對外站網域，不用後台自己的 origin
    # （?base 只在對外站尚未設定時當備援）
    url = _share_url(token, _site_url_from(await _db_settings()) or base)
    try:
        import qrcode  # noqa: F401  僅探測 — 缺套件的 agent 給明確 503 而非 500
    except ImportError:
        raise HTTPException(status_code=503,
                            detail="此主機未安裝 qrcode 套件（連結仍可複製使用）")

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


@router.post("/media-log/catchup_now")
async def media_log_catchup_now(request: Request):
    """管理員手動觸發一輪縮圖補算（影片縮圖/時長 + 舊縮圖遷移）。
    平時由 master 背景每 10 分鐘自動跑；剛上傳一批影片想立刻看到縮圖時可手動戳。
    只在 master 有意義（有 ffmpeg + NAS 憑證）；回 {scanned, fixed}。"""
    _check_auth(request)
    from services.media_log_catchup import run_media_log_catchup_once
    return await run_media_log_catchup_once()


@router.post("/projects/{project_id}/media-log/settings")
async def update_media_log_settings(project_id: str, request: Request):
    """影像紀錄全域設定（root 資料夾 / 分類清單）— 寫 DB（單一真相，master 與
    NAS 對外容器共讀，見 _media_log_conf）。root / categories 是全站共用
    （不分專案），path 帶 project_id 只是讓前端從專案面板順手改。
    缺的欄位不動（partial 語意）。root 存的是 master 視角的 UNC。"""
    _check_auth(request)
    _require_db()
    body = await request.json()
    patch: dict = {}
    if "root" in body:
        root = str(body.get("root") or "").strip()
        # 存在性用「本機視角」驗（master 驗 UNC、NAS 驗掛載點），存的仍是原字串
        if root and not await asyncio.to_thread(os.path.isdir, to_local_path(root)):
            raise HTTPException(status_code=400,
                                detail=f"資料夾不存在或無法存取：{root}")
        patch[_SETTING_KEY_ROOT] = root
    if "categories" in body:
        patch[_SETTING_KEY_CATS] = _norm_categories(body.get("categories"))
    if patch:
        from services.website import settings_service
        factory = await _get_factory()
        async with factory() as session:
            await settings_service.update_settings(session, patch)
    root, cats = await _media_log_conf()
    return {"ok": True, "root": root, "categories": cats}


async def _delete_file_record(session, rec) -> None:
    """刪一筆檔案：row 刪 + 原檔搬 _trash/（軟刪可救）+ 縮圖刪。
    admin 端點與 public 端點共用；caller 已驗過權限/歸屬。"""
    stored_path = to_local_path(rec.stored_path or "")   # DB 存 canonical → 本機視角
    project_id = rec.project_id
    file_id = rec.id
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
        _remove_thumbs(project_id, file_id)

    await asyncio.to_thread(_dispose)


@router.delete("/media-log/files/{file_id}")
async def delete_media_log_file(file_id: str, request: Request):
    """刪除影像紀錄檔案（admin）— 軟刪語意見 _delete_file_record。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rec = await session.get(ProjectMediaFile, file_id)
        if not rec:
            raise HTTPException(status_code=404, detail="找不到檔案")
        await _delete_file_record(session, rec)
    return {"ok": True}


# ── Public Endpoints（免登入，token 授權）────────────────────

@public_router.get("/public/media-log/{token}")
async def public_media_log_view(token: str):
    """分享連結頁資料 — 專案名 + 分類 + 檔案清單（enabled=False → 403）。"""
    _require_db()
    root, cats = await _media_log_conf()
    factory = await _get_factory()
    async with factory() as session:
        row = await _verify_media_log_token(session, token)
        pid = row.id
        proj = await session.get(CrmProject, pid)
        project_name = (proj.name or "") if proj else ""
    # 資料夾→DB 同步（public 端每次 GET 都打 → 每專案 30 秒限流一次）
    await _reconcile_files(factory, pid, throttled=True)
    async with factory() as session:
        files = await _list_files(session, pid)
    return {
        "project_name": project_name,
        "categories": cats,
        "upload_enabled": await asyncio.to_thread(_root_set, root),
        "max_upload_bytes": _MAX_UPLOAD_BYTES,  # 前端預檢/文案由此推導，免兩邊硬寫
        "files": files,
    }


@public_router.post("/public/media-log/{token}/upload")
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
    root, _cats = await _media_log_conf()
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
            # 本機寫檔用 local，但 DB 存 canonical UNC → 別台（master 補算）讀得到
            filename=final_name, stored_path=to_canonical_path(stored_path),
            thumb_url=thumb, media_type=media_type,
            duration_sec=duration,
            category=str(category or "").strip()[:50],
            uploader_name=str(uploader_name or "").strip()[:100],
            size_bytes=size, created_at=_now(),
        )
        session.add(rec)
        await session.commit()
        return _to_file_dict(rec)   # 通知由 master 掃 DB 補發，見上方註解


@public_router.get("/public/media-log/{token}/file/{file_id}")
async def public_media_log_file(token: str, file_id: str):
    """免登入下載/檢視原始檔 — file_id 不屬於該 token 專案 → 404。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        row = await _verify_media_log_token(session, token)
        rec = await session.get(ProjectMediaFile, file_id)
        if not rec or rec.project_id != row.id:
            raise HTTPException(status_code=404, detail="找不到檔案")
        stored_path = to_local_path(rec.stored_path or "")   # canonical → 本機視角
        filename = rec.filename or os.path.basename(stored_path) or "file"
    if not stored_path or not await asyncio.to_thread(os.path.isfile, stored_path):
        raise HTTPException(status_code=404, detail="檔案已不存在")
    from starlette.responses import FileResponse
    return FileResponse(stored_path, filename=filename)


@public_router.delete("/public/media-log/{token}/file/{file_id}")
async def public_media_log_delete(token: str, file_id: str):
    """公開頁刪除（token 授權；file_id 不屬於該專案 → 404）。
    軟刪：原檔搬 _trash/ 可救回 — 免登入連結誤刪的保險。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        row = await _verify_media_log_token(session, token)
        rec = await session.get(ProjectMediaFile, file_id)
        if not rec or rec.project_id != row.id:
            raise HTTPException(status_code=404, detail="找不到檔案")
        await _delete_file_record(session, rec)
    return {"ok": True}
