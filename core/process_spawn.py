"""Process spawn helpers — single source of truth for restarting uvicorn.

Replaces the historical mess of BAT files + schtasks /it + vbs WshShell.Run
+ `start "" /B` patterns that interacted badly with Windows console takedown,
DETACHED_PROCESS, Session 0/1 elevation, and Intel Fortran runtime
CTRL_CLOSE_EVENT. Each layer fixed one symptom and introduced another.

Approach: spawn uvicorn directly via subprocess.Popen with DETACHED_PROCESS
+ CREATE_NEW_PROCESS_GROUP + DEVNULL handles. uvicorn is a server (no GUI,
no stdin), so detaching from console is safe. New process inherits caller's
Windows session — production master in Session 1 spawns Session 1; test
master in Session 0 spawns Session 0. No schtasks /it gymnastics, no BAT
internals, no console-handle inheritance.

Also runnable as a CLI helper:
    python -m core.process_spawn --restart [--ota] [--wait 2] [--port 8000]
This is what FastAPI restart endpoints invoke: spawn this module detached,
return HTTP response, then os._exit(). The detached helper waits for the
parent to exit, kills any leftover port binder, optionally runs OTA, rotates
logs, then spawns a new uvicorn (also detached).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from typing import Optional

# Windows process creation flags (zero on POSIX so the OR is harmless)
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
DETACH_FLAGS = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW


def _base_dir() -> str:
    """Project root (parent of this file's directory)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_python(gui: bool = True) -> str:
    """Resolve Python interpreter. Default gui=True returns pythonw.exe (no
    console) so spawned uvicorn doesn't trigger Windows Terminal window
    on Windows 11 (default terminal app intercepts console-exe launches
    and shows itself even when STARTUPINFO requests SW_HIDE)."""
    base = _base_dir()
    name = "pythonw.exe" if gui else "python.exe"
    for c in (
        os.path.join(base, ".venv", "Scripts", name),
        os.path.join(base, "python_embed", name),
    ):
        if os.path.isfile(c):
            return c
    # Fallback: try the alternate variant before falling back to sys.executable
    alt = "python.exe" if gui else "pythonw.exe"
    for c in (
        os.path.join(base, ".venv", "Scripts", alt),
        os.path.join(base, "python_embed", alt),
    ):
        if os.path.isfile(c):
            return c
    return sys.executable


def kill_port(port: int = 8000) -> int:
    """Kill all processes listening on port. Returns count killed.

    Sleeps 2s after kills so the OS releases the socket before the next
    bind attempt (avoids TIME_WAIT collisions on rapid restarts).
    """
    if sys.platform != "win32":
        return 0
    try:
        out = subprocess.run(
            f'netstat -aon | findstr ":{port} " | findstr "LISTENING"',
            shell=True, capture_output=True, text=True, timeout=10,
        ).stdout or ""
    except Exception:
        return 0
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[-1].isdigit() and parts[-1] != "0":
            pids.add(parts[-1])
    killed = 0
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", pid],
                capture_output=True, timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
            killed += 1
        except Exception:
            pass
    if killed:
        time.sleep(2)
    return killed


def rotate_log(path: str) -> None:
    """Move path → path.bak (overwriting any existing .bak)."""
    bak = path + ".bak"
    try:
        if os.path.isfile(bak):
            os.remove(bak)
        if os.path.isfile(path):
            os.replace(path, bak)
    except OSError:
        pass


def spawn_uvicorn_detached(base_dir: Optional[str] = None, port: int = 8000) -> int:
    """Spawn uvicorn fully detached from caller's console. Returns child PID.

    The handful of flags here are load-bearing:
    - DETACHED_PROCESS: no console attachment → console takedown can't send
      CTRL_CLOSE_EVENT or trigger Intel Fortran runtime forrtl(200) abort.
    - CREATE_NEW_PROCESS_GROUP: Ctrl+C / Ctrl+Break to caller stays put.
    - CREATE_NO_WINDOW: no flicker if a console somehow gets created.
    - stdin=DEVNULL: no input stream that could close on caller exit.
    - close_fds=False: we explicitly hand stdout/stderr file objects in;
      close_fds=True would close them before the child inherits.

    The child inherits the caller's Windows session — no /it elevation —
    so production master in Session 1 spawns Session 1, test master in
    Session 0 spawns Session 0. Picker dialogs only work in Session 1
    but HTTP serving works in either.
    """
    if base_dir is None:
        base_dir = _base_dir()
    py = find_python()
    out_log = os.path.join(base_dir, "uvicorn_out.log")
    err_log = os.path.join(base_dir, "uvicorn_err.log")

    out_fh = open(out_log, "w", buffering=1)
    err_fh = open(err_log, "w", buffering=1)
    try:
        proc = subprocess.Popen(
            [py, "-m", "uvicorn", "main:io_app",
             "--host", "0.0.0.0", "--port", str(port)],
            cwd=base_dir,
            stdin=subprocess.DEVNULL,
            stdout=out_fh,
            stderr=err_fh,
            creationflags=DETACH_FLAGS,
            close_fds=False,
        )
    finally:
        out_fh.close()
        err_fh.close()
    return proc.pid


def trigger_detached_restart(base_dir: Optional[str] = None, run_ota: bool = False) -> None:
    """Spawn this module as a detached CLI helper that will restart uvicorn.

    Endpoint usage:
        trigger_detached_restart(run_ota=True)
        asyncio.get_running_loop().call_later(1.0, os._exit, 0)
        return {"status": "updating"}

    The helper waits ~2s for the parent endpoint to os._exit, kills any
    leftover port-binder, optionally runs update_agent.py, rotates logs,
    spawns a new uvicorn (detached), then exits.
    """
    if base_dir is None:
        base_dir = _base_dir()
    py = find_python()
    args = [py, "-m", "core.process_spawn", "--restart"]
    if run_ota:
        args.append("--ota")
    subprocess.Popen(
        args,
        cwd=base_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACH_FLAGS,
        close_fds=True,
    )


def _restart_main(args: argparse.Namespace) -> None:
    """CLI entry: kill port → optional OTA → rotate logs → spawn uvicorn."""
    base_dir = _base_dir()
    time.sleep(args.wait)
    kill_port(args.port)
    if args.ota:
        updater = os.path.join(base_dir, "update_agent.py")
        if os.path.isfile(updater):
            try:
                subprocess.run(
                    [find_python(), updater],
                    cwd=base_dir, timeout=300,
                    creationflags=CREATE_NO_WINDOW,
                )
            except Exception:
                pass
    rotate_log(os.path.join(base_dir, "uvicorn_out.log"))
    rotate_log(os.path.join(base_dir, "uvicorn_err.log"))
    spawn_uvicorn_detached(base_dir, args.port)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Originsun process spawn helper (detached uvicorn restart)."
    )
    parser.add_argument("--restart", action="store_true",
                        help="Run the kill+OTA+spawn sequence")
    parser.add_argument("--ota", action="store_true",
                        help="Run update_agent.py before spawning")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--wait", type=float, default=2.0,
                        help="Seconds to wait before killing port (let parent exit)")
    args = parser.parse_args()
    if args.restart:
        _restart_main(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
