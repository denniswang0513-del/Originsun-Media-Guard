from core_engine import MediaGuardEngine  # type: ignore
from core.logger import make_job_logger  # type: ignore


def create_engine(job_id: str) -> MediaGuardEngine:
    """Factory: create a fresh engine with per-job log/error callbacks."""
    log_cb, err_cb = make_job_logger(job_id)
    return MediaGuardEngine(logger_cb=log_cb, error_cb=err_cb)
