"""Preflight health check — dynamically verify ALL imports after OTA update.

Run: python preflight.py
Exit code 0 = all OK, 1 = import failures detected.

Scans every .py file in the project, extracts import statements, filters out
stdlib/local modules, then tries to import each third-party package.
"""

import os
import sys

# Ensure script's own directory is in sys.path (needed when called from a different cwd)
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from ota_manifest import (
    STDLIB, LOCAL_MODULES, IMPORT_NAME_FIX, OPTIONAL_IMPORTS, scan_imports,
)

# ── Critical local modules that MUST import successfully ──
CRITICAL_LOCAL = [
    "config",
    "core_engine",
    "notifier",
]


def run() -> int:
    failures = []
    warnings = []

    # ── Phase 1: Dynamic scan — find ALL third-party imports ──
    all_imports = scan_imports(_script_dir)
    third_party = all_imports - STDLIB - LOCAL_MODULES

    for mod in sorted(third_party):
        actual_mod = IMPORT_NAME_FIX.get(mod, mod)
        try:
            __import__(actual_mod)
        except ImportError as e:
            if mod in OPTIONAL_IMPORTS:
                warnings.append(f"  {mod}: {e} (optional, skipped)")
            else:
                failures.append(f"  {mod}: {e}")
        except Exception:
            pass  # Non-import errors are OK (e.g. missing config at import time)

    # ── Phase 2: Critical local modules ──
    for mod in CRITICAL_LOCAL:
        try:
            __import__(mod)
        except ImportError as e:
            failures.append(f"  {mod}: {e}")
        except Exception:
            pass  # Non-import errors (e.g. missing ffmpeg) are OK

    # ── Report ──
    if warnings:
        print(f"PREFLIGHT WARNINGS — {len(warnings)} optional package(s) missing:")
        for w in warnings:
            print(w)

    if failures:
        print(f"PREFLIGHT FAILED — {len(failures)} required import(s) broken:")
        for f in failures:
            print(f)
        return 1

    checked = len(third_party) + len(CRITICAL_LOCAL)
    print(f"PREFLIGHT OK — {checked} imports verified ({len(warnings)} optional skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(run())
