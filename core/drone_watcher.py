"""
空拍排程監控 — 指定每日時間點掃描來源根目錄，自動轉檔 + 串帶。

設計：
- 以「子資料夾」為最小單元：source_root/XXX/*.mov → 整包打進一個 card
- 跳過目的地已有 MAX_* 輸出的子資料夾（自癒、不用 ledger）
- 由 core.scheduler 的 60 秒 tick 呼叫 check_and_fire()
"""

import os
import json
import threading
from datetime import datetime
from typing import List, Tuple, Optional

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_BASE_DIR, "watcher_config.json")
_HISTORY_PATH = os.path.join(_BASE_DIR, "watcher_history.json")
_HISTORY_MAX = 50

_VIDEO_EXTS = {".mov", ".mp4", ".mkv", ".mxf", ".avi", ".mts", ".m2ts"}

_file_lock = threading.Lock()

# Config cache (mtime-keyed) — avoid ~1440 pointless JSON reads/day from scheduler tick
_cfg_cache: Optional[dict] = None
_cfg_cache_mtime: float = 0.0


# Keep in sync with frontend DRONE_MODELS in frontend/tabs/drone_meta/drone_meta.js
_MODEL_PROFILES = {
    "autel_evo_lite_plus": {
        "make": "Autel Robotics", "model": "EVO Lite+",
        "lens_make": "Autel Robotics", "lens_model": "EVO Lite+ Camera",
    },
    "dji_mavic3": {
        "make": "DJI", "model": "Mavic 3",
        "lens_make": "DJI", "lens_model": "Mavic 3 Camera",
    },
    "dji_mini4pro": {
        "make": "DJI", "model": "Mini 4 Pro",
        "lens_make": "DJI", "lens_model": "Mini 4 Pro Camera",
    },
    "dji_air3": {
        "make": "DJI", "model": "Air 3",
        "lens_make": "DJI", "lens_model": "Air 3 Camera",
    },
}


def _resolve_drone_model(snapshot: dict) -> dict:
    key = snapshot.get("drone_model_key", "autel_evo_lite_plus")
    if key == "custom":
        return {
            "drone_make": snapshot.get("custom_make", ""),
            "drone_model": snapshot.get("custom_model", ""),
            "lens_make": snapshot.get("custom_lens_make", ""),
            "lens_model": snapshot.get("custom_lens_model", ""),
        }
    p = _MODEL_PROFILES.get(key, _MODEL_PROFILES["autel_evo_lite_plus"])
    return {
        "drone_make": p["make"],
        "drone_model": p["model"],
        "lens_make": p["lens_make"],
        "lens_model": p["lens_model"],
    }


# ── 持久化 ────────────────────────────────────────────────────

def load_config() -> dict:
    if not os.path.exists(_CONFIG_PATH):
        return {}
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_config_cached() -> dict:
    """mtime-guarded cache for scheduler tick (called once/minute)."""
    global _cfg_cache, _cfg_cache_mtime
    if not os.path.exists(_CONFIG_PATH):
        _cfg_cache = None
        _cfg_cache_mtime = 0.0
        return {}
    mtime = os.path.getmtime(_CONFIG_PATH)
    if _cfg_cache is None or mtime != _cfg_cache_mtime:
        _cfg_cache = load_config()
        _cfg_cache_mtime = mtime
    return _cfg_cache or {}


def save_config(cfg: dict) -> None:
    tmp = _CONFIG_PATH + ".tmp"
    with _file_lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _CONFIG_PATH)


def load_history() -> List[dict]:
    if not os.path.exists(_HISTORY_PATH):
        return []
    try:
        with open(_HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def append_history(entry: dict) -> None:
    with _file_lock:
        items = load_history()
        items.insert(0, entry)
        items = items[:_HISTORY_MAX]
        tmp = _HISTORY_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _HISTORY_PATH)


# ── 掃描邏輯 ──────────────────────────────────────────────────

def _scan_candidates(source_root: str, dest_root: str) -> List[Tuple[str, List[str]]]:
    """
    回傳 [(子資料夾絕對路徑, [影片絕對路徑, ...]), ...]
    跳過目的地已有 MAX_*.MOV 的子資料夾。
    """
    if not os.path.isdir(source_root):
        return []

    candidates = []
    for name in sorted(os.listdir(source_root)):
        sub = os.path.join(source_root, name)
        if not os.path.isdir(sub):
            continue

        # Self-healing skip: presence of MAX_*.MOV in dest means this folder
        # already ran successfully. No ledger file needed.
        dest_sub = os.path.join(dest_root, name)
        if os.path.isdir(dest_sub):
            try:
                if any(f.upper().startswith("MAX_") and f.upper().endswith(".MOV")
                       for f in os.listdir(dest_sub)):
                    continue
            except OSError:
                pass

        videos = []
        for root, _dirs, files in os.walk(sub):
            for fn in files:
                if os.path.splitext(fn)[1].lower() in _VIDEO_EXTS:
                    videos.append(os.path.join(root, fn))
        if videos:
            candidates.append((sub, sorted(videos)))

    return candidates


# ── 執行一次 ──────────────────────────────────────────────────

def run_watcher_scan(cfg: Optional[dict] = None, trigger: str = "scheduled") -> dict:
    """
    執行一次掃描 + 派發。回傳 {folders, files, job_ids, error?}。
    trigger: 'scheduled' | 'manual'
    """
    cfg = cfg or load_config()
    start_ts = datetime.now()

    source_root = cfg.get("source_root", "").strip()
    dest_root = cfg.get("dest_root", "").strip()
    concat_dest_root = (cfg.get("concat_dest_root", "") or dest_root).strip()
    snapshot = cfg.get("snapshot", {})

    if not source_root or not dest_root:
        return {"error": "來源或目的地根目錄未設定", "folders": 0, "files": 0}

    candidates = _scan_candidates(source_root, dest_root)
    if not candidates:
        entry = {
            "ts": start_ts.isoformat(timespec="seconds"),
            "trigger": trigger,
            "folder_count": 0,
            "file_count": 0,
            "status": "ok",
            "note": "無新資料夾（全部已處理）",
        }
        append_history(entry)
        return {"folders": 0, "files": 0, "job_ids": []}

    model_info = _resolve_drone_model(snapshot)

    # Deferred imports: core.worker → core.engine_inst → core.schemas chain
    # creates a circular import if hoisted. Cached by sys.modules after first call.
    from core.schemas import DroneMetaRequest, DroneMetaFileSetting  # type: ignore
    from core.worker import enqueue_job  # type: ignore
    import asyncio
    import core.state as state  # type: ignore

    job_ids = []
    total_files = 0
    errors = []
    loop = state.get_main_loop()

    for sub_path, videos in candidates:
        folder_name = os.path.basename(sub_path)
        output_dir = os.path.join(dest_root, folder_name)

        files = [DroneMetaFileSetting(path=p) for p in videos]
        total_files += len(files)

        concat_dest = ""
        if snapshot.get("do_concat", True):
            concat_dest = os.path.join(concat_dest_root, folder_name)

        req = DroneMetaRequest(
            project_name=folder_name,
            file_index=int(snapshot.get("file_index", 1) or 1),
            files=files,
            output_dir=output_dir,
            date_time=start_ts.isoformat(timespec="seconds"),
            drone_make=model_info["drone_make"],
            drone_model=model_info["drone_model"],
            lens_make=model_info["lens_make"],
            lens_model=model_info["lens_model"],
            do_concat=bool(snapshot.get("do_concat", True)),
            concat_dest_dir=concat_dest,
            concat_custom_name=snapshot.get("concat_custom_name", ""),
            concat_resolution=snapshot.get("concat_resolution", "1080P"),
            concat_codec=snapshot.get("concat_codec", "H.264 (NVENC)"),
            concat_burn_timecode=bool(snapshot.get("concat_burn_timecode", True)),
            concat_burn_filename=bool(snapshot.get("concat_burn_filename", False)),
            concat_xfade_enabled=bool(snapshot.get("concat_xfade_enabled", False)),
            concat_xfade_type=snapshot.get("concat_xfade_type", "fade"),
            concat_xfade_duration=float(snapshot.get("concat_xfade_duration", 1.0) or 1.0),
        )

        # enqueue_job is async; scheduler tick runs in a thread, so bridge via
        # the main event loop captured at startup.
        try:
            if loop and loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(
                    enqueue_job(req, folder_name, "drone_meta"), loop
                )
                job_id, _warn = fut.result(timeout=30)
                job_ids.append(job_id)
            else:
                errors.append(f"{folder_name}: 主 loop 未啟動")
        except Exception as e:
            errors.append(f"{folder_name}: {e}")

    duration_sec = (datetime.now() - start_ts).total_seconds()
    try:
        from notifier import notify_tab  # type: ignore
        notify_tab(
            "drone_watcher_success",
            folder_count=len(candidates),
            file_count=total_files,
            duration=f"{duration_sec:.1f}s",
            trigger=trigger,
        )
    except Exception:
        pass

    entry = {
        "ts": start_ts.isoformat(timespec="seconds"),
        "trigger": trigger,
        "folder_count": len(candidates),
        "file_count": total_files,
        "job_ids": job_ids,
        "status": "ok" if not errors else "partial",
    }
    if errors:
        entry["errors"] = errors
    append_history(entry)

    return {
        "folders": len(candidates),
        "files": total_files,
        "job_ids": job_ids,
        "errors": errors,
    }


# ── 排程 hook ────────────────────────────────────────────────

_last_fired_date: Optional[str] = None  # 'YYYY-MM-DD' in-process guard


def check_and_fire() -> bool:
    """
    由 core.scheduler 的 tick 呼叫（每 60 秒）。
    若到時間且今日未觸發，執行一次掃描。回傳是否有觸發。
    """
    global _last_fired_date
    cfg = _load_config_cached()
    if not cfg.get("enabled"):
        return False

    run_time = cfg.get("run_time", "02:00")
    now = datetime.now()
    if now.strftime("%H:%M") != run_time:
        return False

    today = now.strftime("%Y-%m-%d")
    if _last_fired_date == today:
        return False

    # Restart-safe: history survives process restart, in-process guard doesn't.
    hist = load_history()
    for h in hist[:5]:
        if h.get("trigger") == "scheduled" and h.get("ts", "").startswith(today):
            _last_fired_date = today
            return False

    try:
        run_watcher_scan(cfg, trigger="scheduled")
        _last_fired_date = today
    except Exception as e:
        # Don't set _last_fired_date — allow retry on the next minute tick.
        # History entry still records the failure for the UI.
        append_history({
            "ts": now.isoformat(timespec="seconds"),
            "trigger": "scheduled",
            "status": "error",
            "error": str(e),
        })
    return True
