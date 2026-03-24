"""JSON fallback — wraps existing JSON I/O for use when DB is unavailable.

Each function mirrors the corresponding repo's interface but delegates
to the original JSON-based implementations in routers/.
"""

import socket as _socket
import core.state as state
from db.session import get_session_factory


def is_db_available() -> bool:
    """Check if DB is available and session factory exists."""
    return state.db_online and get_session_factory() is not None


def get_machine_id() -> str:
    """Get machine_id from settings or hostname."""
    try:
        from config import load_settings
        return load_settings().get("machine_id", "") or _socket.gethostname()
    except Exception:
        return _socket.gethostname()
