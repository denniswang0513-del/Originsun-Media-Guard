"""Custom asyncio loop factory — forces SelectorEventLoop on Windows.

asyncpg is unstable on Windows' default ProactorEventLoop: under sustained real
load its connections silently wedge → db_available() fails forever → db_online
stuck false → only a full process restart recovers. asyncpg targets the
SelectorEventLoop, so we run the server on it.

uvicorn 0.49 hard-codes ProactorEventLoop on Windows (uvicorn/loops/asyncio.py)
via Config.get_loop_factory(), which overrides any event-loop policy. main.py
points that factory at this function. Trade-off: SelectorEventLoop can't run
asyncio subprocesses on Windows — all subprocess work goes through core.subproc
(subprocess.run / Popen in a worker thread).
"""
import asyncio
import sys


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Return a fresh loop — SelectorEventLoop on Windows, default elsewhere."""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()
