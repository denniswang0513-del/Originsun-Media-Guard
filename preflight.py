"""Preflight health check — dynamically verify ALL imports after OTA update.

Run: python preflight.py
Exit code 0 = all OK, 1 = import failures detected.

Scans every .py file in the project, extracts import statements, filters out
stdlib/local modules, then tries to import each third-party package.

**什麼算「失敗」**：只有 `requirements_agent.txt` 承諾要裝的套件缺席才算失敗 ——
那份檔案就是 agent 的依賴契約。專案裡還有很多 master/伺服器端專用的套件
（cryptography、pypdf、soundfile…），全都是「函式內延遲 import」，agent 永遠執行
不到；但本檔是靜態掃描、看不出 lazy，若一律當必要就會在 agent 上判定失敗 →
OTA 整包回滾。機隊自 1.10.181 起升不上去正是卡在這裡（2026-07-10 修）。
"""

import os
import re
import sys

# Ensure script's own directory is in sys.path (needed when called from a different cwd)
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from ota_manifest import (
    STDLIB, LOCAL_MODULES, IMPORT_NAME_FIX, IMPORT_TO_PIP, OPTIONAL_IMPORTS, scan_imports,
)

# ── Critical local modules that MUST import successfully ──
CRITICAL_LOCAL = [
    "config",
    "core_engine",
    "notifier",
]


def _required_pkgs() -> set:
    """agent 必備套件 = requirements_agent.txt 列出的那些（小寫、去掉版本與 extras）。

    讀不到檔案時回空集合 → 全部降級成警告，寧可放行也不要誤擋 OTA。
    """
    path = os.path.join(_script_dir, "requirements_agent.txt")
    pkgs = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.split("#")[0].strip()
                if not line:
                    continue
                name = re.split(r"[<>=!\[;]", line)[0].strip().lower()
                if name:
                    pkgs.add(name)
    except OSError:
        pass
    return pkgs


def run() -> int:
    failures = []
    skipped = []
    required = _required_pkgs()

    # ── Phase 1: Dynamic scan — find ALL third-party imports ──
    all_imports = scan_imports(_script_dir)
    third_party = all_imports - STDLIB - LOCAL_MODULES

    for mod in sorted(third_party):
        pip_name = IMPORT_TO_PIP.get(mod, mod).split("[")[0].strip().lower()
        # 必要 = requirements_agent.txt 有列 且 沒被標成選用
        is_required = bool(pip_name) and pip_name in required and mod not in OPTIONAL_IMPORTS
        if not is_required:
            # 非必要的一律「不 import」——不只是不算失敗，而是連試都不試。
            # 舊版會逐一 __import__ torch / f5_tts / faster_whisper 這些重量級套件，
            # 光 import torch 在慢機上就要十幾秒，整支 preflight 撐爆 update_agent
            # 的 30 秒逾時 → OTA 回滾。（2026-07-10：agent_9 就是死在這裡。）
            skipped.append(mod)
            continue
        actual_mod = IMPORT_NAME_FIX.get(mod, mod)
        try:
            __import__(actual_mod)
        except ImportError as e:
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
    if skipped:
        print(f"PREFLIGHT SKIPPED — {len(skipped)} 個非 agent 依賴未檢查（master 專用）: "
              + ", ".join(sorted(skipped)))

    if failures:
        print(f"PREFLIGHT FAILED — {len(failures)} required import(s) broken:")
        for f in failures:
            print(f)
        return 1

    checked = len(third_party) - len(skipped) + len(CRITICAL_LOCAL)
    print(f"PREFLIGHT OK — {checked} imports verified ({len(skipped)} skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(run())
