from core_engine import MediaGuardEngine  # type: ignore
from core.logger import _engine_log_cb, _emit_sync

engine = MediaGuardEngine(
    logger_cb=_engine_log_cb,
    error_cb=lambda msg: _emit_sync('log', {'type': 'error', 'msg': msg})
)
