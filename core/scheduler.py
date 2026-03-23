"""
任務排程器 — Originsun Media Guard Pro
每 60 秒掃描一次 scheduled_jobs.json，到期的排程自動送入任務佇列。
支援 cron 重複排程與 run_at 單次排程。
"""

import os
import json
import asyncio
import logging
import threading
from datetime import datetime
from typing import List, Optional

try:
    from croniter import croniter  # type: ignore
except ImportError:
    croniter = None  # graceful degradation — cron schedules won't fire

from core.schemas import (  # type: ignore
    BackupRequest, TranscodeRequest, ConcatRequest,
    VerifyRequest, TranscribeRequest, ReportJobRequest,
)
import core.state as state  # type: ignore

_log = logging.getLogger(__name__)

_SCHEDULE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scheduled_jobs.json",
)

# task_type → Pydantic Request class (佇列型任務)
REQUEST_CLASS_MAP = {
    "backup": BackupRequest,
    "transcode": TranscodeRequest,
    "concat": ConcatRequest,
    "verify": VerifyRequest,
    "transcribe": TranscribeRequest,
    "report": ReportJobRequest,
}

# 所有可排程的任務類型（含 TTS 非佇列型）
SCHEDULABLE_TYPES = set(REQUEST_CLASS_MAP) | {"tts", "clone"}

# Serialize lock — prevents TOCTOU race between scheduler tick and API writes
_file_lock = threading.Lock()


# ── 持久化 ────────────────────────────────────────────────────

def load_schedules() -> List[dict]:
    """讀取排程列表。檔案不存在或損毀時回傳空 list。"""
    if not os.path.exists(_SCHEDULE_FILE):
        return []
    try:
        with open(_SCHEDULE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_schedules(schedules: List[dict]) -> None:
    """原子寫入排程列表（先寫 .tmp 再 os.replace）。"""
    tmp = _SCHEDULE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(schedules, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _SCHEDULE_FILE)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ── Cron 工具 ─────────────────────────────────────────────────

def is_valid_cron(expr: str) -> bool:
    """檢查 cron 表達式是否合法。"""
    if croniter is None:
        return False
    try:
        croniter(expr)
        return True
    except (ValueError, KeyError, TypeError):
        return False


def compute_next_run(cron_expr: str, after: Optional[datetime] = None) -> str:
    """計算下次執行時間（ISO 字串）。"""
    if croniter is None:
        return ""
    base = after or datetime.now()
    ci = croniter(cron_expr, base)
    return ci.get_next(datetime).isoformat(timespec="seconds")


# ── Request 建構 ──────────────────────────────────────────────

def build_request(task_type: str, data: dict):
    """根據 task_type 建立對應的 Pydantic Request 物件。"""
    cls = REQUEST_CLASS_MAP.get(task_type)
    if not cls:
        raise ValueError(f"不支援的排程任務類型: {task_type}")
    return cls(**data)


# ── TTS 排程派發（透過 HTTP 自呼叫） ──────────────────────────

def _dispatch_tts(task_type: str, req_data: dict) -> bool:
    """透過 HTTP 呼叫本機 TTS API 來派發 TTS/Clone 任務。"""
    import urllib.request
    import urllib.error

    endpoint_map = {
        "tts": "/api/v1/tts_jobs",
        "clone": "/api/v1/tts_jobs/clone",
    }
    path = endpoint_map.get(task_type)
    if not path:
        return False

    url = f"http://127.0.0.1:8000{path}"
    body = json.dumps(req_data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status == 200
    except Exception as e:
        _log.warning("TTS 排程派發失敗 (%s): %s", task_type, e)
        return False


# ── 分散式轉檔派發 ────────────────────────────────────────────

# 影片副檔名（與 ListDirRequest 預設值對齊）
_VIDEO_EXTS = {".mov", ".mp4", ".mkv", ".mxf", ".avi", ".mts", ".m2ts", ".r3d", ".braw"}


def _scan_video_files(source_dirs: list) -> list:
    """掃描來源目錄下所有影片檔案，回傳 (abs_path, rel_subdir) 列表。"""
    files = []
    for src in source_dirs:
        src = src.strip()
        if not os.path.isdir(src):
            continue
        for root, _dirs, filenames in os.walk(src):
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() in _VIDEO_EXTS:
                    abs_path = os.path.join(root, fn)
                    rel = os.path.relpath(root, src)
                    files.append((abs_path, rel))
    return files


def _ping_host(ip: str, timeout: float = 3.0) -> bool:
    """測試遠端主機是否可連線。"""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(f"http://{ip}/api/v1/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def dispatch_distributed_transcode(
    source_dirs: list,
    dest_dir: str,
    compute_hosts: list,
    project_name: str = "",
) -> int:
    """
    將影片檔案分散派發到多個遠端主機進行轉檔。
    compute_hosts: [{name, ip}, ...]，ip='local' 表示本機。
    回傳成功派發的主機數量。
    """
    import urllib.request
    import urllib.error

    # 1. 掃描影片
    all_files = _scan_video_files(source_dirs)
    if not all_files:
        _log.warning("分散式轉檔：無影片檔案")
        return 0

    # 2. 過濾可連線的主機
    reachable = []
    for h in compute_hosts:
        if h.get("ip") == "local":
            reachable.append(h)
        elif _ping_host(h.get("ip", "")):
            reachable.append(h)
        else:
            _log.warning("分散式轉檔：主機 %s (%s) 無法連線", h.get("name"), h.get("ip"))

    if not reachable:
        _log.warning("分散式轉檔：無可用主機")
        return 0

    # 3. Round-robin 分配
    host_buckets = {i: [] for i in range(len(reachable))}
    for idx, (abs_path, rel) in enumerate(all_files):
        host_buckets[idx % len(reachable)].append((abs_path, rel))

    # 4. 派發任務
    dispatched = 0
    for i, host in enumerate(reachable):
        bucket = host_buckets[i]
        if not bucket:
            continue
        sources = [fp for fp, _ in bucket]
        ip = host.get("ip", "local")
        host_name = host.get("name", ip)

        if ip == "local":
            # 本機直接 enqueue
            try:
                from core.worker import enqueue_job  # type: ignore
                host_dest = os.path.join(dest_dir, f"HostDispatch_{host_name}") if len(reachable) > 1 else dest_dir
                req = TranscodeRequest(sources=sources, dest_dir=host_dest, project_name=project_name)
                enqueue_job(req, project_name or "scheduled", "transcode")
                dispatched += 1
            except Exception as e:
                _log.warning("分散式轉檔本機 enqueue 失敗: %s", e)
        else:
            # 遠端 HTTP 派發
            host_dest = os.path.join(dest_dir, f"HostDispatch_{host_name}")
            payload = json.dumps({
                "sources": sources,
                "dest_dir": host_dest,
                "project_name": project_name,
            }).encode("utf-8")
            url = f"http://{ip}/api/v1/jobs/transcode"
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status in (200, 201):
                        dispatched += 1
                    else:
                        _log.warning("分散式轉檔主機 %s 回應 %s", host_name, resp.status)
            except Exception as e:
                _log.warning("分散式轉檔主機 %s 派發失敗: %s", host_name, e)

    _log.info("分散式轉檔派發完成: %d/%d 主機", dispatched, len(reachable))
    return dispatched


# ── 排程檢查與派發 ────────────────────────────────────────────

def _get_machine_id() -> str:
    try:
        from config import load_settings
        return load_settings().get("machine_id", "") or __import__("socket").gethostname()
    except Exception:
        return "unknown"


def _check_and_dispatch() -> int:
    """
    掃描所有排程，到期的送入 enqueue_job() 或透過 HTTP 派發 TTS。
    回傳本次觸發的排程數量。
    DB 優先，JSON fallback。
    """
    from core.worker import enqueue_job  # type: ignore

    due_tasks = []  # list of (schedule_id, task_type, req_data, name)
    used_db = False

    # ─── Phase 1: 收集到期排程並更新狀態 ───────────────────
    if state.db_online:
        try:
            import asyncio
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import schedules_repo

                async def _db_check():
                    nonlocal due_tasks, used_db
                    now = datetime.now()
                    machine_id = _get_machine_id()
                    async with factory() as session:
                        due = await schedules_repo.get_due(session, machine_id, now)
                        for sch in due:
                            sid = sch.get("schedule_id")
                            task_type = sch.get("task_type", "backup")
                            req_data = sch.get("request", {})
                            due_tasks.append((sid, task_type, req_data,
                                              sch.get("name", "scheduled")))
                            # 更新時間
                            cron = sch.get("cron")
                            if cron:
                                next_run_str = compute_next_run(cron, now)
                                next_run_dt = datetime.fromisoformat(next_run_str) if next_run_str else None
                                await schedules_repo.mark_fired(
                                    session, sid, last_run=now,
                                    next_run=next_run_dt, enabled=True,
                                )
                            else:
                                await schedules_repo.mark_fired(
                                    session, sid, last_run=now,
                                    next_run=None, enabled=False,
                                )
                        await session.commit()
                    used_db = True

                # Run async code from sync context
                loop = state.get_main_loop()
                if loop and loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(_db_check(), loop)
                    future.result(timeout=10)
        except Exception as e:
            _log.debug("排程 DB 檢查失敗，回退到 JSON: %s", e)
            due_tasks = []
            used_db = False

    # JSON fallback
    if not used_db:
        with _file_lock:
            schedules = load_schedules()
            now = datetime.now()
            changed = False

            for sch in schedules:
                if not sch.get("enabled", True):
                    continue
                next_run = sch.get("next_run")
                if not next_run:
                    continue
                try:
                    next_dt = datetime.fromisoformat(next_run)
                except (ValueError, TypeError):
                    continue
                if now >= next_dt:
                    task_type = sch.get("task_type", "backup")
                    req_data = sch.get("request", {})
                    due_tasks.append((sch.get("schedule_id"), task_type, req_data,
                                      sch.get("name", "scheduled")))

                    # 更新時間（無論成功與否，避免無限重試）
                    sch["last_run"] = now.isoformat(timespec="seconds")
                    cron = sch.get("cron")
                    if cron:
                        sch["next_run"] = compute_next_run(cron, now)
                    else:
                        sch["enabled"] = False
                        sch["next_run"] = None
                    changed = True

            if changed:
                save_schedules(schedules)

    # ─── Phase 2: 在鎖外派發任務（避免 HTTP 呼叫阻塞鎖） ──
    dispatched = 0
    for schedule_id, task_type, req_data, name in due_tasks:
        if task_type in ("tts", "clone"):
            success = _dispatch_tts(task_type, req_data)
            if success:
                dispatched += 1
            else:
                _log.warning("排程 %s TTS dispatch 失敗", schedule_id)
        elif task_type == "transcode" and req_data.get("compute_hosts"):
            # 分散式轉檔：直接派發到多個主機
            try:
                n = dispatch_distributed_transcode(
                    source_dirs=req_data.get("sources", []),
                    dest_dir=req_data.get("dest_dir", ""),
                    compute_hosts=req_data["compute_hosts"],
                    project_name=req_data.get("project_name", name),
                )
                if n > 0:
                    dispatched += 1
            except Exception as e:
                _log.warning("排程 %s 分散式轉檔 dispatch 失敗: %s", schedule_id, e)
        else:
            try:
                req = build_request(task_type, req_data)
                project_name = getattr(req, "project_name", name)
                enqueue_job(req, project_name, task_type)
                dispatched += 1
            except Exception as e:
                _log.warning("排程 %s dispatch 失敗: %s", schedule_id, e)

    return dispatched


# ── 背景排程迴圈 ──────────────────────────────────────────────

async def run_scheduler():
    """每 60 秒檢查一次到期排程。由 main.py startup 啟動。"""
    await asyncio.sleep(30)  # 讓服務先穩定
    while True:
        try:
            await asyncio.to_thread(_check_and_dispatch)
        except Exception:
            _log.exception("排程檢查迴圈發生異常")
        await asyncio.sleep(60)
