import os
import asyncio
from datetime import datetime as _dt
from core.socket_mgr import sio  # type: ignore
import core.state as state  # type: ignore


def _write_log_to_file(log_file_path: str, msg: str):
    """Write a timestamped line to a specific log file."""
    if not log_file_path:
        return
    try:
        ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
        with open(log_file_path, "a", encoding="utf-8-sig") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception as e:
        print(f"Failed to write log to file: {e}")


def _emit_sync(event: str, data: dict) -> None:
    """Thread-safe emit without job_id (broadcast to all clients)."""
    loop = state.get_main_loop()
    if loop:
        asyncio.run_coroutine_threadsafe(sio.emit(event, data), loop)


def _emit_sync_for_job(job_id: str, event: str, data: dict) -> None:
    """Thread-safe emit that tags events with job_id and updates per-job + global buffers."""
    # Inject job_id into emitted data
    data_with_id = {**data, "job_id": job_id}

    # Write to per-job log buffer & file
    job = state.get_job(job_id)
    if job and event == "log" and "msg" in data:
        _write_log_to_file(job.log_file_path or "", data["msg"])
        job.log_buffer.append(data["msg"])
        if len(job.log_buffer) > state._LOG_BUFFER_MAX:
            job.log_buffer = job.log_buffer[-state._LOG_BUFFER_MAX:]

    # Also append to global buffer for backward compat
    if event == "log" and "msg" in data:
        state._global_log_buffer.append(data["msg"])
        if len(state._global_log_buffer) > state._STATUS_LOG_MAX:
            state._global_log_buffer = state._global_log_buffer[-state._STATUS_LOG_MAX:]

    loop = state.get_main_loop()
    if loop:
        asyncio.run_coroutine_threadsafe(sio.emit(event, data_with_id), loop)


def make_job_logger(job_id: str):
    """Return (log_cb, err_cb) closures bound to a specific job."""

    def _log_cb(msg: str):
        is_verbose = (
            msg.startswith("[OK] 本機寫入") or
            msg.startswith("[OK] NAS 寫入") or
            msg.startswith("[OK] 完成:") or
            msg.startswith("[OK] 串帶完成:") or
            "請求已送出" in msg
        )

        if is_verbose:
            _emit_sync_for_job(job_id, 'log', {'type': 'info', 'msg': msg})
            return

        sys_prefixes = ('[OK]', '[X]', '[!]', '[Engine]', '===', '---', '系統')
        if any(msg.startswith(p) for p in sys_prefixes) or '開始' in msg or '完成' in msg or '失敗' in msg:
            _emit_sync_for_job(job_id, 'log', {'type': 'system', 'msg': msg})
        else:
            _emit_sync_for_job(job_id, 'log', {'type': 'info', 'msg': msg})

    def _err_cb(msg: str):
        _emit_sync_for_job(job_id, 'log', {'type': 'error', 'msg': msg})

    return _log_cb, _err_cb
