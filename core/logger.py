import os
import asyncio
from datetime import datetime as _dt
from core.socket_mgr import sio  # type: ignore
import core.state as state  # type: ignore

def _write_log_to_file(msg: str):
    if not state._current_task_log_file:
        return
    try:
        ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(state._current_task_log_file), exist_ok=True)
        with open(state._current_task_log_file, "a", encoding="utf-8-sig") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception as e:
        print(f"Failed to write log to file: {e}")

def _emit_sync(event: str, data: dict) -> None:
    if event == "log" and "msg" in data:
        _write_log_to_file(data["msg"])
        state._status_log_buffer.append(data["msg"])
        if len(state._status_log_buffer) > state._STATUS_LOG_MAX:
            state._status_log_buffer = state._status_log_buffer[-state._STATUS_LOG_MAX:]
            
    loop = state.get_main_loop()
    if loop:
        asyncio.run_coroutine_threadsafe(sio.emit(event, data), loop)

def _engine_log_cb(msg: str):
    is_verbose = (
        msg.startswith("[OK] 本機寫入") or 
        msg.startswith("[OK] NAS 寫入") or
        msg.startswith("[OK] 完成:") or 
        msg.startswith("[OK] 串帶完成:") or
        "請求已送出" in msg
    )
    
    if is_verbose:
        _emit_sync('log', {'type': 'info', 'msg': msg})
        return

    sys_prefixes = ('[OK]', '[X]', '[!]', '[Engine]', '===', '---', '系統')
    if any(msg.startswith(p) for p in sys_prefixes) or '開始' in msg or '完成' in msg or '失敗' in msg:
        _emit_sync('log', {'type': 'system', 'msg': msg})
    else:
        _emit_sync('log', {'type': 'info', 'msg': msg})
