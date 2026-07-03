"""services/website/backup_service.py
─────────────────────────────────────
資料備份 runner —— 每日打包 mediaguard DB + 上傳媒體 → 你的 Google Drive（off-site）。

兩層備份：
  • Layer 1（NAS crontab `backup_originsun.sh`）：常駐、獨立、master 關機也照跑，
    產出 NAS 本地 `.../Website/backups/`（每日 02:30）。此檔不管 Layer 1。
  • Layer 2（此檔，master 排程 03:00）：
      ssh NAS `pg_dump -Fc mediaguard` + `tar uploads/` → 拉回 master
      + 本地 `website/public/notion-media` → 打包 `originsun_backup_<ts>.tar.gz`
      → drive_sync.upload_private 到你的 Drive → 本地 + Drive 各自輪替
        （keep_daily 日 + keep_monthly 月）。

gate：只有握有 NAS SSH key 的機器（= master）跑排程；fleet agents 無 key → 早退。
       dev(8001) 也有 key，但 `backup.runner.enabled` 存 mediaguard_dev（另一庫）→
       只有在 prod 後台開啟才會讓 prod 跑（同翻譯/SEO 的隔離模型）。
NAS website-api（main_website.py）不註冊此 scheduler；「立即備份」由 NAS 透過
MASTER_RELAY_URL forward 給 master 執行。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import tarfile
import time
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .settings_service import get_all_settings, update_settings

logger = logging.getLogger(__name__)

_run_lock = asyncio.Lock()

# ── NAS 連線常數（與 publish_update.py 一致；備份 pg_dump 走 docker exec，免密碼）──
NAS_HOST = "admin@192.168.1.132"
NAS_WEB = "/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website"
SSH_DOCKER = "/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker"
SSH_KEY_PATH = os.path.join(
    os.environ.get("USERPROFILE", os.path.expanduser("~")), ".ssh", "id_originsun_nas")
_SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
             "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10"]
DB_NAME = "mediaguard"
DB_USER = "originsun"

_BUNDLE_PREFIX = "originsun_backup_"
_DEFAULT_CRON = "0 3 * * *"


# ══════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════

def _app_root() -> str:
    """app 根目錄（services/website/backup_service.py → 三層上）。"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def is_master() -> bool:
    """只有握有 NAS SSH key 的機器才能備份（= master；fleet agents 無 key）。"""
    return os.path.exists(SSH_KEY_PATH)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _app_version(base: str) -> str:
    try:
        with open(os.path.join(base, "version.json"), encoding="utf-8") as f:
            return json.load(f).get("version", "?")
    except (OSError, ValueError):
        return "?"


def _ssh_to_file(remote_cmd: str, out_path: str, timeout: float = 180.0) -> tuple[int, str]:
    """ssh 到 NAS 跑 remote_cmd，stdout 直接寫進 out_path。回 (returncode, stderr)。"""
    cmd = ["ssh", "-i", SSH_KEY_PATH] + _SSH_OPTS + [NAS_HOST, remote_cmd]
    try:
        with open(out_path, "wb") as f:
            p = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=timeout)
        err = p.stderr.decode(errors="replace") if p.stderr else ""
        return p.returncode, err
    except (subprocess.TimeoutExpired, OSError) as e:
        return 1, f"{type(e).__name__}: {e}"


_TS_RE = re.compile(r"(\d{8})_(\d{6})")


def _parse_ts(name: str) -> Optional[str]:
    """從檔名抽出可排序時間戳 YYYYMMDDHHMMSS（月份 = [:6]）。"""
    m = _TS_RE.search(name)
    return (m.group(1) + m.group(2)) if m else None


def _select_expired(names: list[str], keep_daily: int, keep_monthly: int) -> list[str]:
    """回應該刪掉的檔名：保留最近 keep_daily 份 + 最近 keep_monthly 個月各留最新一份。"""
    dated = sorted(((n, _parse_ts(n)) for n in names if _parse_ts(n)),
                   key=lambda x: x[1], reverse=True)
    keep: set[str] = {n for n, _ in dated[:keep_daily]}
    seen_months: list[str] = []
    for n, ts in dated:
        month = ts[:6]
        if month not in seen_months:
            seen_months.append(month)
            if len(seen_months) <= keep_monthly:
                keep.add(n)   # 該月最新（dated 已 desc）
    return [n for n, _ in dated if n not in keep]


# ══════════════════════════════════════════════════════════
# 核心：打包 + 上傳 + 輪替（blocking，跑在 thread）
# ══════════════════════════════════════════════════════════

def _run_backup_blocking(folder_id: str, keep_daily: int, keep_monthly: int,
                         do_upload: bool) -> dict:
    if not is_master():
        return {"ok": False, "error": "本機無 NAS SSH key（非 master）"}
    base = _app_root()
    local_dir = os.path.join(base, "backups")
    staging = os.path.join(local_dir, "staging")
    os.makedirs(staging, exist_ok=True)
    for fn in os.listdir(staging):
        try:
            os.remove(os.path.join(staging, fn))
        except OSError:
            pass

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    parts: list[tuple[str, str]] = []   # (arcname, path)

    # 1) DB dump（custom format）
    db_path = os.path.join(staging, "mediaguard.pgc")
    rc, err = _ssh_to_file(
        f"{SSH_DOCKER} exec originsun_postgres pg_dump -U {DB_USER} -Fc {DB_NAME}", db_path)
    if rc != 0 or not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return {"ok": False, "error": f"pg_dump 失敗：{err[:200] or 'empty output'}"}
    parts.append(("mediaguard.pgc", db_path))

    # 2) uploads 媒體
    up_path = os.path.join(staging, "uploads.tar.gz")
    rc, err = _ssh_to_file(f"tar czf - -C {NAS_WEB} uploads", up_path)
    if rc != 0 or not os.path.exists(up_path) or os.path.getsize(up_path) == 0:
        return {"ok": False, "error": f"uploads tar 失敗：{err[:200] or 'empty output'}"}
    parts.append(("uploads.tar.gz", up_path))

    # 3) 本地 notion-media（若有；full-sync 圖不在 uploads/）
    nm_src = os.path.join(base, "website", "public", "notion-media")
    if os.path.isdir(nm_src) and os.listdir(nm_src):
        nm_path = os.path.join(staging, "notion-media.tar.gz")
        with tarfile.open(nm_path, "w:gz") as tf:
            tf.add(nm_src, arcname="notion-media")
        parts.append(("notion-media.tar.gz", nm_path))

    # 4) manifest
    manifest = {"ts": ts, "created": datetime.now().isoformat(timespec="seconds"),
                "app_version": _app_version(base), "db": DB_NAME, "files": []}
    sizes = {}
    for name, path in parts:
        b = os.path.getsize(path)
        sizes[name] = b
        manifest["files"].append({"name": name, "bytes": b, "sha256": _sha256(path)})
    man_path = os.path.join(staging, "manifest.json")
    with open(man_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    parts.append(("manifest.json", man_path))

    # 5) bundle
    bundle_name = f"{_BUNDLE_PREFIX}{ts}.tar.gz"
    bundle_path = os.path.join(local_dir, bundle_name)
    with tarfile.open(bundle_path, "w:gz") as tf:
        for name, path in parts:
            tf.add(path, arcname=name)
    bundle_bytes = os.path.getsize(bundle_path)
    for _, path in parts:
        try:
            os.remove(path)
        except OSError:
            pass

    summary = {"ok": True, "ts": ts, "bundle": bundle_name, "bundle_bytes": bundle_bytes,
               "db_bytes": sizes.get("mediaguard.pgc", 0),
               "uploads_bytes": sizes.get("uploads.tar.gz", 0),
               "drive_url": "", "drive_error": ""}

    # 6) 上傳 Drive（私有）
    if do_upload and folder_id:
        try:
            import drive_sync
            if not drive_sync.gdrive_ready():
                summary["drive_error"] = "尚未授權（credentials.json / token.json 未備）"
            else:
                r = drive_sync.upload_private(bundle_path, folder_id, mimetype="application/gzip")
                summary["drive_url"] = r.get("link", "")
                summary["drive_id"] = r.get("id", "")
        except Exception as e:
            logger.warning("[backup] Drive 上傳失敗：%s", e)
            summary["drive_error"] = str(e)[:200]

    # 7) 輪替：本地
    try:
        local_names = [f for f in os.listdir(local_dir) if f.startswith(_BUNDLE_PREFIX)]
        for f in _select_expired(local_names, keep_daily, keep_monthly):
            try:
                os.remove(os.path.join(local_dir, f))
            except OSError:
                pass
    except OSError:
        pass

    # 8) 輪替：Drive（只在有上傳 + 有 folder 時）
    if do_upload and folder_id and not summary.get("drive_error"):
        try:
            import drive_sync
            remote = drive_sync.list_files(folder_id, name_contains=_BUNDLE_PREFIX)
            by_name = {f["name"]: f["id"] for f in remote}
            for name in _select_expired(list(by_name), keep_daily, keep_monthly):
                try:
                    drive_sync.delete_file(by_name[name])
                except Exception:
                    pass
        except Exception as e:
            logger.warning("[backup] Drive 輪替失敗：%s", e)

    return summary


# ══════════════════════════════════════════════════════════
# 設定
# ══════════════════════════════════════════════════════════

async def get_runner_settings(session: AsyncSession) -> dict:
    s = await get_all_settings(session)
    # master_ready 由 master 的排程 loop 每 60s 寫進 DB → prod 後台（打 NAS）也能反映
    # master 的真實授權/上線狀態（NAS 容器本身沒 SSH key、沒 drive_sync、沒憑證）。
    mr = s.get("backup.runner.master_ready") or {}
    try:
        import drive_sync
        local_ready = drive_sync.gdrive_ready()
    except Exception:
        local_ready = False   # NAS website-api 無 drive_sync（不上傳，只 relay master）
    return {
        "enabled": s.get("backup.runner.enabled") is True,
        "cron": str(s.get("backup.runner.cron") or _DEFAULT_CRON),
        "gdrive_folder_id": str(s.get("backup.gdrive_folder_id") or ""),
        "keep_daily": int(s.get("backup.keep_daily") or 14),
        "keep_monthly": int(s.get("backup.keep_monthly") or 6),
        "last_run_at": s.get("backup.runner.last_run_at"),
        "last_run_summary": s.get("backup.runner.last_run_summary"),
        "running": s.get("backup.runner.running") is True,
        "gdrive_ready": bool(local_ready or mr.get("gdrive_ready")),
        "is_master": is_master(),
        "master_seen_at": (time.time() if is_master() else mr.get("at")),
    }


async def update_runner_settings(session: AsyncSession, payload: dict, *,
                                 by: Optional[str] = None) -> dict:
    KEYMAP = {
        "enabled": "backup.runner.enabled", "cron": "backup.runner.cron",
        "gdrive_folder_id": "backup.gdrive_folder_id",
        "keep_daily": "backup.keep_daily", "keep_monthly": "backup.keep_monthly",
    }
    if payload.get("cron"):
        cron_str = str(payload["cron"])
        try:
            from croniter import croniter
            croniter(cron_str)
        except ImportError:
            if len(cron_str.split()) != 5:
                raise ValueError(f"cron 需 5 欄位：{cron_str!r}")
        except Exception as e:
            raise ValueError(f"cron 不合法：{cron_str!r}（{e}）")
    to_write = {KEYMAP[k]: v for k, v in payload.items() if k in KEYMAP}
    if to_write:
        await update_settings(session, to_write, updated_by=by)
    return await get_runner_settings(session)


# ══════════════════════════════════════════════════════════
# 執行（背景）+ relay
# ══════════════════════════════════════════════════════════

_backup_task: Optional[asyncio.Task] = None


def is_backup_running() -> bool:
    return _backup_task is not None and not _backup_task.done()


async def _do_backup_and_record() -> dict:
    from db.session import get_session_factory
    factory = get_session_factory()
    if factory is None:
        return {"ok": False, "error": "no db"}
    async with factory() as s:
        st = await get_runner_settings(s)
        await update_settings(s, {"backup.runner.running": True})
    try:
        summary = await asyncio.to_thread(
            _run_backup_blocking, st["gdrive_folder_id"],
            st["keep_daily"], st["keep_monthly"], do_upload=True)
    except Exception as e:
        logger.exception("[backup] 執行例外")
        summary = {"ok": False, "error": str(e)}
    async with factory() as s:
        await update_settings(s, {
            "backup.runner.running": False,
            "backup.runner.last_run_at": time.time(),
            "backup.runner.last_run_summary": summary,
        })
    return summary


def start_backup_bg() -> bool:
    global _backup_task
    if is_backup_running() or _run_lock.locked():
        return False
    _backup_task = asyncio.create_task(_do_backup_and_record())
    return True


async def trigger_backup(session: AsyncSession) -> dict:
    """立即備份。NAS 上 → relay 給 master；master 上 → 起背景任務。"""
    relay = os.environ.get("MASTER_RELAY_URL", "").strip()
    if relay:
        url = f"{relay.rstrip('/')}/api/website/admin/internal/backup/run"
        r = await _relay(url, timeout=20.0)
        return r if r.get("status") else {"status": "error", "error": r.get("error")}
    if not is_master():
        return {"status": "error", "error": "本機非 master、也無 relay 目標"}
    if not start_backup_bg():
        return {"status": "busy"}
    return {"status": "started"}


async def _relay(url: str, timeout: float) -> dict:
    import httpx
    from core.auth import _get_secret
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers={"X-Internal-Key": _get_secret()})
            if r.status_code == 200:
                return r.json()
            return {"ok": False, "error": f"master relay 失敗 (HTTP {r.status_code}): {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": f"master 離線或超時：{e}"}


# ══════════════════════════════════════════════════════════
# 排程 loop（只在 master 起）
# ══════════════════════════════════════════════════════════

_scheduler_task: Optional[asyncio.Task] = None


async def _scheduler_loop() -> None:
    try:
        from croniter import croniter
    except ImportError:
        logger.warning("[backup] croniter 未安裝，排程不啟動")
        return
    if not is_master():
        logger.info("[backup] 本機無 NAS SSH key — 備份排程不啟動（只在 master 跑）")
        return
    from db.session import get_session_factory
    await asyncio.sleep(50)
    while True:
        try:
            factory = get_session_factory()
            if factory is None:
                await asyncio.sleep(60)
                continue
            async with factory() as session:
                # 每 60s 公告 master 狀態（授權/上線）→ prod 後台可見
                try:
                    import drive_sync
                    await update_settings(session, {"backup.runner.master_ready":
                                                    {"gdrive_ready": drive_sync.gdrive_ready(), "at": time.time()}})
                except Exception:
                    pass
                st = await get_runner_settings(session)
                if st["enabled"] and st["cron"]:
                    last_at = float(st["last_run_at"] or 0)
                    if last_at <= 0:
                        due = True
                    else:
                        try:
                            next_due = croniter(
                                st["cron"], datetime.fromtimestamp(last_at)).get_next(datetime)
                        except (ValueError, KeyError, TypeError) as e:
                            logger.warning("[backup] cron 錯 %r: %s", st["cron"], e)
                            await asyncio.sleep(60)
                            continue
                        due = datetime.now() >= next_due
                    if due and not is_backup_running():
                        logger.info("[backup] 觸發 — 執行備份")
                        await _do_backup_and_record()
        except Exception:
            logger.exception("[backup] scheduler loop 異常")
        await asyncio.sleep(60)


def start_scheduler_task() -> None:
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("[backup] 備份排程 loop 已啟動")
