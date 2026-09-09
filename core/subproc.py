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


async def run_stream(
    args: Sequence[str],
    *,
    on_line,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    input_bytes: Optional[bytes] = None,
    timeout: Optional[float] = None,
) -> tuple[int, bytes, bytes]:
    """Same contract as run_capture, but stdout lines are handed to `on_line`
    as they arrive (for streaming UIs). Returns (rc, full_stdout, stderr).

    Selector-loop-safe for the same reason as run_capture: everything runs in
    worker threads, never in the event loop.

    stdin is written from its own thread — writing a multi-KB prompt while the
    child is already producing output would deadlock on the pipe buffer if both
    happened on this thread.

    `on_line` is called in a worker thread with a single line of bytes (no
    newline). It must not raise; exceptions are swallowed so a bad callback
    can't kill the read loop mid-stream.
    """
    def _run() -> tuple[int, bytes, bytes]:
        import threading
        try:
            p = subprocess.Popen(
                list(args), cwd=cwd, env=env,
                stdin=subprocess.PIPE if input_bytes is not None else None,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as e:  # spawn failure (file not found, perms, ...)
            return -1, b"", f"spawn failed: {e}".encode("utf-8", "replace")

        def _feed():
            try:
                if p.stdin:
                    if input_bytes:
                        p.stdin.write(input_bytes)
                    p.stdin.close()
            except OSError:
                pass

        err_buf: list = []

        def _drain_err():
            try:
                err_buf.append(p.stderr.read() or b"")
            except (OSError, ValueError):
                pass

        threads = [threading.Thread(target=_feed, daemon=True),
                   threading.Thread(target=_drain_err, daemon=True)]
        for t in threads:
            t.start()

        out_lines: list = []

        # 🔴 stdout 也在自己的執行緒讀，逾時交給 p.wait() 守。
        #    原本是「主執行緒 for line in p.stdout，收到一行才檢查 deadline」——
        #    子行程吐一行之後就卡住時（正是逾時要防的情況）迴圈會一直卡在 readline，
        #    逾時永遠不會發生，整個呼叫就跟著子行程一起吊死。
        def _pump():
            try:
                for raw in p.stdout:
                    out_lines.append(raw)
                    try:
                        on_line(raw.rstrip(b"\r\n"))
                    except Exception:       # noqa: BLE001 — 壞掉的 callback 不准殺掉讀取
                        pass
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=_pump, daemon=True)
        reader.start()
        threads.append(reader)

        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            reader.join(timeout=2)          # 收完已經吐出來的，再回報
            return -1, b"".join(out_lines), f"timeout after {timeout}s".encode()
        for t in threads:
            t.join(timeout=5)               # 讀完管道剩下的（行程結束了，很快）
        return rc, b"".join(out_lines), b"".join(err_buf)

    return await asyncio.to_thread(_run)
