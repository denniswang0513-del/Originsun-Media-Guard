"""JSON fallback — wraps existing JSON I/O for use when DB is unavailable.

Each function mirrors the corresponding repo's interface but delegates
to the original JSON-based implementations in routers/.
"""

import core.state as state
from db.session import get_session_factory


def is_db_available() -> bool:
    """Check if DB is available and session factory exists."""
    return state.db_online and get_session_factory() is not None
