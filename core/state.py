import asyncio
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum


# ── Job Status Enum ──────────────────────────────────────────
class JobStatus(str, Enum):
    QUEUED = "queued"
    WAITING = "waiting"       # 等待 dispatch slot
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


# ── Per-Job State ────────────────────────────────────────────
@dataclass
class JobState:
    job_id: str
    task_type: str
    project_name: str
    status: JobStatus = JobStatus.QUEUED
    request: Any = None
    engine: Any = None                  # MediaGuardEngine（run 時建立）
    asyncio_task: Optional[asyncio.Task] = None

    # Per-job 進度 & log
    progress: Optional[dict] = None
    log_buffer: list = field(default_factory=list)
    log_file_path: Optional[str] = None

    # Per-job 衝突解決
    conflict_event: threading.Event = field(default_factory=threading.Event)
    current_conflict_action: str = "copy"
    global_conflict_action: Optional[str] = None

    # Per-job 報表暫停
    report_pause_event: Optional[asyncio.Event] = None

    # 時間戳
    created_at: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error_detail: Optional[str] = None

    # Priority & Urgent
    priority: int = 0           # 越小越優先，0 = 普通
    urgent: bool = False        # 緊急標記

    # Socket.IO client tracking
    client_sid: str = ""        # 發起任務的 Socket.IO client sid


# ── Global Job Registry ─────────────────────────────────────
_jobs: Dict[str, JobState] = {}
_jobs_lock = threading.Lock()


# ── Concurrency Limits ──────────────────────────────────────
CONCURRENCY_LIMITS: Dict[str, int] = {
    "backup": 1,
    "transcode": 2,
    "concat": 2,
    "verify": 2,
    "transcribe": 1,
    "report": 1,
}

_LOG_BUFFER_MAX = 500   # per-job log buffer limit


# ── Active Counts (replaces semaphores) ─────────────────────
_active_counts: Dict[str, int] = {}
_active_lock = threading.Lock()


def init_concurrency():
    """Called once from app startup. Reads concurrency overrides from settings."""
    try:
        from config import load_settings
        settings = load_settings()
        saved = settings.get("concurrency", {})
        for key, val in saved.items():
            if key in CONCURRENCY_LIMITS and isinstance(val, int) and val > 0:
                CONCURRENCY_LIMITS[key] = val
    except Exception:
        pass
    with _active_lock:
        for task_type in CONCURRENCY_LIMITS:
            _active_counts.setdefault(task_type, 0)


def increment_active(task_type: str):
    with _active_lock:
        _active_counts[task_type] = _active_counts.get(task_type, 0) + 1


def decrement_active(task_type: str):
    with _active_lock:
        cur = _active_counts.get(task_type, 0)
        _active_counts[task_type] = max(0, cur - 1)


def get_active_count(task_type: str) -> int:
    return _active_counts.get(task_type, 0)


def can_dispatch(task_type: str) -> bool:
    limit = CONCURRENCY_LIMITS.get(task_type, 1)
    return get_active_count(task_type) < limit


# ── Backward-Compatible Global Log Buffer ────────────────────
_STATUS_LOG_MAX = 2000
_global_log_buffer: list = []


# ── Event Loop Reference (unchanged) ────────────────────────
_main_loop = None

def get_main_loop():
    global _main_loop
    return _main_loop

def set_main_loop(loop):
    global _main_loop
    _main_loop = loop


# ── Job Registry Helper Functions ────────────────────────────
def register_job(job: JobState):
    with _jobs_lock:
        _jobs[job.job_id] = job

def get_job(job_id: str) -> Optional[JobState]:
    return _jobs.get(job_id)

def remove_job(job_id: str):
    with _jobs_lock:
        _jobs.pop(job_id, None)

def get_all_jobs() -> Dict[str, JobState]:
    return dict(_jobs)

def find_duplicate(project_name: str, task_type: str) -> Optional[JobState]:
    """Return an active job with same project+task_type, or None."""
    for job in _jobs.values():
        if (job.project_name == project_name
                and job.task_type == task_type
                and job.status in (
                    JobStatus.QUEUED, JobStatus.WAITING,
                    JobStatus.RUNNING, JobStatus.PAUSED)):
            return job
    return None


# ── Queue / Priority Helpers ─────────────────────────────────
def get_queued_jobs(task_type: str = None) -> List[JobState]:
    """Return QUEUED/WAITING jobs sorted by priority then created_at."""
    jobs = []
    for j in _jobs.values():
        if j.status not in (JobStatus.QUEUED, JobStatus.WAITING):
            continue
        if task_type and j.task_type != task_type:
            continue
        jobs.append(j)
    jobs.sort(key=lambda j: (j.priority, j.created_at))
    return jobs


def get_running_jobs(task_type: str = None) -> List[JobState]:
    """Return RUNNING jobs."""
    jobs = []
    for j in _jobs.values():
        if j.status != JobStatus.RUNNING:
            continue
        if task_type and j.task_type != task_type:
            continue
        jobs.append(j)
    return jobs


def reorder_jobs(ordered_job_ids: List[str]):
    """Set priority based on position in ordered_job_ids.
    Only affects QUEUED/WAITING jobs; others are ignored."""
    with _jobs_lock:
        for idx, jid in enumerate(ordered_job_ids):
            job = _jobs.get(jid)
            if job and job.status in (JobStatus.QUEUED, JobStatus.WAITING):
                job.priority = idx


def set_job_urgent(job_id: str):
    """Mark urgent=True, set priority to min(same-type) - 1 (auto front)."""
    job = _jobs.get(job_id)
    if not job or job.status not in (JobStatus.QUEUED, JobStatus.WAITING):
        return
    # Find min priority among same task_type queued jobs
    min_p = 0
    for j in _jobs.values():
        if (j.task_type == job.task_type
                and j.status in (JobStatus.QUEUED, JobStatus.WAITING)
                and j.job_id != job_id):
            min_p = min(min_p, j.priority)
    job.urgent = True
    job.priority = min_p - 1


def unset_job_urgent(job_id: str):
    """Remove urgent flag, keep priority unchanged."""
    job = _jobs.get(job_id)
    if job:
        job.urgent = False
