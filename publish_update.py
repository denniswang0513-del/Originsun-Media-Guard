"""Originsun SaaS — Version Publisher (v2).

Usage:
  python publish_update.py --version 1.11.0 --notes "Fix backup bug"
  python publish_update.py   (interactive mode)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

VERSION_FILE = "version.json"
MANIFEST_FILE = "update_manifest.json"
BUILD_SCRIPT = "build_agent_zip.py"


# ────────────────────────────────────────
# Version Utilities
# ────────────────────────────────────────

def validate_version(v: str) -> bool:
    """Validate semver format: MAJOR.MINOR.PATCH (pure digits)."""
    return bool(re.match(r"^\d+\.\d+\.\d+$", v.strip()))


def parse_semver(v: str):
    return tuple(int(p) for p in v.strip().split("."))


def is_greater(new_v: str, old_v: str) -> bool:
    try:
        return parse_semver(new_v) > parse_semver(old_v)
    except (ValueError, TypeError):
        return False


# ────────────────────────────────────────
# Atomic JSON Write
# ────────────────────────────────────────

def atomic_json_write(path: str, data: dict):
    """Write JSON atomically: .tmp → os.replace()."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ────────────────────────────────────────
# Manifest Generator
# ────────────────────────────────────────

def generate_manifest(version: str):
    """Scan imports, compare with requirements, write update_manifest.json."""
    base = os.path.dirname(os.path.abspath(__file__))

    # Read known packages from requirements files
    known_pkgs = set()
    for req_name in ["0225_requirements.txt", "requirements_agent.txt"]:
        req_file = os.path.join(base, req_name)
        if os.path.exists(req_file):
            for line in open(req_file, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#"):
                    known_pkgs.add(re.split(r"[>=<\[]", line)[0].strip().lower())

    # Scan all .py imports
    imported = set()
    exclude = {".venv", "venv", "node_modules", ".git", "tests", "__pycache__",
               "python_embed", "_rollback", ".claude"}
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in exclude]
        for f in files:
            if not f.endswith(".py"):
                continue
            try:
                content = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
                for m in re.findall(r"^\s*(?:from|import)\s+([\w.]+)", content, re.MULTILINE):
                    imported.add(m.split(".")[0].lower())
            except Exception:
                pass

    # Stdlib + local modules
    stdlib = {
        "os", "sys", "json", "time", "re", "io", "math", "random", "uuid", "datetime",
        "pathlib", "collections", "functools", "itertools", "typing", "enum", "dataclasses",
        "abc", "subprocess", "shutil", "tempfile", "zipfile", "hashlib", "socket", "signal",
        "threading", "asyncio", "concurrent", "http", "urllib", "email", "html", "xml",
        "logging", "warnings", "traceback", "inspect", "importlib", "pkgutil", "copy",
        "struct", "base64", "hmac", "secrets", "contextlib", "textwrap", "string",
        "argparse", "configparser", "csv", "sqlite3", "platform", "ctypes", "glob",
        "fnmatch", "stat", "mimetypes", "webbrowser", "multiprocessing", "filecmp", "gc",
        "locale", "queue", "tkinter", "codecs", "operator", "fractions", "decimal",
        "heapq", "bisect", "array", "weakref", "types", "numbers", "cmath", "pprint",
        "dis", "token", "tokenize", "site", "pip", "distutils", "unittest", "doctest",
        "venv", "ensurepip",
    }
    import_to_pip = {
        "pil": "Pillow", "cv2": "opencv-python", "sklearn": "scikit-learn",
        "socketio": "python-socketio", "google": "google-api-python-client",
        "googleapiclient": "google-api-python-client",
        "google_auth_oauthlib": "google-auth-oauthlib",
        "sqlalchemy": "sqlalchemy[asyncio]",
        "starlette": "",  # bundled with fastapi
        "pydub": "pydub", "huggingface_hub": "huggingface-hub",
        "edge_tts": "edge-tts", "f5_tts": "f5-tts",
        "faster_whisper": "faster-whisper",
        "tkinterdnd2": "",  # optional
    }
    local_modules = {
        "core", "routers", "utils", "db", "frontend", "templates", "windows_helper",
        "core_engine", "tts_engine", "transcriber", "notifier", "config",
        "report_generator", "drive_sync", "download_model", "bootstrap",
        "server", "main", "update_monitor", "build_agent_zip", "publish_update",
        "update_agent", "preflight", "ota_manifest",
    }

    new_deps = []
    for pkg in imported - stdlib - local_modules:
        if pkg in import_to_pip:
            pip_name = import_to_pip[pkg]
            if not pip_name:
                continue
            if pip_name.lower() not in known_pkgs:
                new_deps.append(pip_name)
        elif pkg not in known_pkgs:
            new_deps.append(pkg)

    # Implicit deps
    implicit_deps = {"sqlalchemy[asyncio]": ["asyncpg"]}
    for pkg in list(new_deps):
        for trigger, extras in implicit_deps.items():
            if pkg == trigger:
                new_deps.extend(e for e in extras if e not in new_deps)

    manifest = {
        "version": version,
        "pip_install": sorted(new_deps),
        "note": "Agent 更新時自動 pip install 這些套件",
    }
    atomic_json_write(os.path.join(base, MANIFEST_FILE), manifest)

    if new_deps:
        print(f"\n[*] {MANIFEST_FILE}: 偵測到 {len(new_deps)} 個新套件: {', '.join(sorted(new_deps))}")
    else:
        print(f"\n[*] {MANIFEST_FILE}: 無新增套件")


# ────────────────────────────────────────
# Main
# ────────────────────────────────────────

def main():
    print("=" * 60)
    print("[*] Originsun SaaS - Auto Publisher (v2)")
    print("=" * 60)
    print()

    if not os.path.exists(VERSION_FILE):
        print(f"錯誤: 找不到 {VERSION_FILE}")
        return 1

    # Read current version
    with open(VERSION_FILE, "r", encoding="utf-8") as f:
        v_data = json.load(f)
    current_version = v_data.get("version", "0.0.0")
    print(f"[*] 目前版本: {current_version}")

    # Parse args
    parser = argparse.ArgumentParser(description="Originsun Version Publisher")
    parser.add_argument("--version", type=str, default="", help="New version (e.g. 1.11.0)")
    parser.add_argument("--notes", type=str, default="", help="Release notes")
    args, _ = parser.parse_known_args()

    # Get version (from args or interactive)
    if args.version:
        new_version = args.version.strip()
    else:
        if not sys.stdin.isatty():
            print("錯誤: 非互動模式必須提供 --version 參數")
            return 1
        raw = input(f"[?] 新版號 (Enter 沿用 {current_version}): ").strip()
        new_version = raw if raw else current_version

    # Validate version format
    if not validate_version(new_version):
        print(f"錯誤: 版本號 '{new_version}' 格式不正確（需要 X.Y.Z 純數字）")
        return 1

    # Validate version is greater (unless same = re-publish)
    if new_version != current_version and not is_greater(new_version, current_version):
        print(f"錯誤: 新版本 {new_version} 不大於目前版本 {current_version}")
        return 1

    # Get notes (from args or interactive)
    if args.notes:
        notes = args.notes.strip()
    else:
        if not sys.stdin.isatty():
            notes = v_data.get("notes", "微幅更新")
        else:
            raw = input("[?] 更新日誌: ").strip()
            notes = raw if raw else v_data.get("notes", "微幅更新")

    # Write version.json (atomic)
    v_data["version"] = new_version
    v_data["build_date"] = datetime.now().strftime("%Y-%m-%d")
    v_data["notes"] = notes
    atomic_json_write(VERSION_FILE, v_data)
    print(f"\n[OK] 已更新 {VERSION_FILE} (v{new_version})")

    # Generate manifest
    generate_manifest(new_version)

    # Build ZIP
    print("\n[*] 開始編譯並打包 ZIP...")
    if not os.path.exists(BUILD_SCRIPT):
        print(f"錯誤: 找不到 {BUILD_SCRIPT}")
        return 1

    result = subprocess.run([sys.executable, BUILD_SCRIPT], shell=False)
    if result.returncode != 0:
        print("\n[ERROR] 打包失敗，發布終止。")
        return 1

    print("\n[OK] 發布完成！Agent 會自動偵測到新版本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
