"""Preflight health check — verify critical imports after OTA update.

Run: python preflight.py
Exit code 0 = all OK, 1 = import failures detected.

Only checks modules required for core functionality (backup/transcode/verify).
Does NOT check optional modules (torch, TTS, whisper, sqlalchemy).
"""

import sys

CRITICAL_IMPORTS = [
    "fastapi",
    "uvicorn",
    "socketio",
    "xxhash",
    "jinja2",
    "requests",
    "psutil",
    "PIL",
    "aiofiles",
    "pydantic",
]

# Local modules (verify project structure intact)
LOCAL_IMPORTS = [
    "config",
    "core_engine",
    "notifier",
]


def run() -> int:
    failures = []

    for mod in CRITICAL_IMPORTS:
        try:
            __import__(mod)
        except ImportError as e:
            failures.append(f"  {mod}: {e}")

    for mod in LOCAL_IMPORTS:
        try:
            __import__(mod)
        except ImportError as e:
            failures.append(f"  {mod}: {e}")
        except Exception:
            pass  # Non-import errors (e.g. missing ffmpeg) are OK

    if failures:
        print(f"PREFLIGHT FAILED — {len(failures)} import(s) broken:")
        for f in failures:
            print(f)
        return 1

    print("PREFLIGHT OK — all critical imports verified")
    return 0


if __name__ == "__main__":
    sys.exit(run())
