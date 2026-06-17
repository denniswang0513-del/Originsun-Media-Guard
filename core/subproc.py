"""Selector-loop-safe subprocess helpers.

On Windows the server runs on WindowsSelectorEventLoopPolicy (asyncpg is stable
there; the default ProactorEventLoop silently wedged asyncpg connections under
sustained load — see the policy block + _periodic_db_health in main.py). But
SelectorEventLoop on Windows does NOT support asyncio.create_subprocess_exec
(raises NotImplementedError). So every subprocess goes through subprocess.run /
Popen executed in a worker thread via asyncio.to_thread — loop-agnostic, never
touches the event loop's (absent) subprocess machinery, and never blocks it.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Optional, Sequence

# Hide the console window for child console apps (npm.cmd / claude.exe) when the
# server itself runs under pythonw.exe (no console). 0 on POSIX (ignored).
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


async def run_capture(
    args: Sequence[str],
    *,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    input_bytes: Optional[bytes] = None,
    timeout: Optional[float] = None,
    merge_stderr: bool = False,
) -> tuple[int, bytes, bytes]:
    """Run a subprocess in a worker thread; return (returncode, stdout, stderr).

    Selector-loop-safe. On timeout the child is killed and rc=-1 is returned with
    stderr=b"timeout after Ns". On spawn failure (e.g. WinError 2) rc=-1 with
    stderr=b"spawn failed: ...". stdout/stderr are raw bytes (decode at call site).
    With merge_stderr=True, stderr is folded into stdout (returned stderr is b"").
    """
    def _run() -> tuple[int, bytes, bytes]:
        try:
            p = subprocess.run(
                list(args),
                cwd=cwd,
                env=env,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
                timeout=timeout,
                creationflags=_CREATE_NO_WINDOW,
            )
            return p.returncode, p.stdout or b"", p.stderr or b""
        except subprocess.TimeoutExpired as e:
            return -1, (e.stdout or b""), f"timeout after {timeout}s".encode()
        except Exception as e:  # spawn failure (file not found, perms, ...)
            return -1, b"", f"spawn failed: {e}".encode("utf-8", "replace")

    return await asyncio.to_thread(_run)
