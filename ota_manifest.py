"""OTA Manifest — Single source of truth for agent file lists and import scanning.

Used by: publish_update.py, build_agent_zip.py, update_agent.py, preflight.py
"""

import os
import re

# ── Individual files synced to Agent ──
AGENT_FILES = [
    "main.py",
    "config.py",
    "core_engine.py",
    "tts_engine.py",
    "report_generator.py",
    "notifier.py",
    "drive_sync.py",
    "transcriber.py",
    "download_model.py",
    "taiwan_dict.json",
    "version.json",
    "update_agent.bat",
    "update_agent.py",
    "update_monitor.py",
    "preflight.py",
    "ota_manifest.py",
    "bootstrap.py",
    "start_hidden.vbs",
    "logo.ico",
    "requirements_agent.txt",
    "update_manifest.json",
    "Install_Originsun_Agent.bat",
    "Install_or_Update.bat",
    "Fix_Task.bat",
]

# ── Directories synced to Agent (recursively) ──
AGENT_DIRS = [
    "frontend",
    "templates",
    "core",
    "routers",
    "utils",
    "db",
]

# ── Extra files only in full install ZIP (not in OTA update) ──
INSTALL_EXTRA_FILES = [
    "ffmpeg.exe",
    "ffprobe.exe",
    "0225_requirements.txt",
]

# ── Extra dirs only in full install ZIP ──
INSTALL_EXTRA_DIRS = [
    "python_embed",
    "windows_helper",
]

# ── Directories excluded from auto-discovery and import scanning ──
EXCLUDE_DIRS = {
    '.venv', 'venv', 'node_modules', '.git', '.claude', 'tests',
    'models', 'voice', 'credentials', '__pycache__', 'e2e',
    '_rollback', 'python_embed',
}

# ── Python stdlib modules (excluded from dependency checks) ──
STDLIB = {
    "os", "sys", "json", "time", "re", "io", "math", "random", "uuid", "datetime", "calendar",
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
    "venv", "ensurepip", "getpass", "atexit", "selectors", "ssl", "certifi",
    "winreg", "msvcrt", "winsound", "nt", "posixpath", "ntpath", "_thread", "builtins",
    "__future__", "annotations", "sysconfig", "zipimport", "runpy",
}

# ── Local project modules (excluded from dependency checks) ──
LOCAL_MODULES = {
    "core", "routers", "utils", "db", "frontend", "templates",
    "core_engine", "tts_engine", "transcriber", "notifier", "config",
    "report_generator", "drive_sync", "download_model", "bootstrap",
    "server", "main", "update_monitor", "build_agent_zip", "publish_update",
    "update_agent", "preflight", "ota_manifest", "patch_ui", "debug_compare",
    "backup_source", "env_setup", "extract_frames", "remove_all_emojis",
}

# ── Import name → pip package name mapping ──
IMPORT_TO_PIP = {
    "pil": "Pillow", "cv2": "opencv-python", "sklearn": "scikit-learn",
    "socketio": "python-socketio", "google": "google-auth",
    "googleapiclient": "google-api-python-client",
    "google_auth_oauthlib": "google-auth-oauthlib",
    "google_auth_httplib2": "google-auth-httplib2",
    "sqlalchemy": "sqlalchemy[asyncio]",
    "asyncpg": "asyncpg",
    "starlette": "",  # bundled with fastapi
    "pydub": "pydub", "huggingface_hub": "huggingface-hub",
    "edge_tts": "edge-tts", "f5_tts": "f5-tts",
    "faster_whisper": "faster-whisper",
    "tkinterdnd2": "",  # optional desktop-only
    "croniter": "croniter",
    "jwt": "PyJWT",
    "jose": "python-jose",
}

# ── Server-only packages — should NOT be installed on Agent machines ──
SERVER_ONLY_PKGS = {
    "torch", "torchaudio", "torchvision", "tts", "coqui-tts",
    "faster-whisper", "ctranslate2", "f5-tts", "edge-tts",
    "pydub", "playwright", "weasyprint",
    "huggingface-hub",
    # DB packages — optional, agents use JSON fallback when not installed
    "sqlalchemy[asyncio]", "sqlalchemy", "asyncpg",
}

# ── Import name corrections (scan finds lowercase but Python needs exact case) ──
IMPORT_NAME_FIX = {
    "pil": "PIL",
}

# ── Optional imports: failure is OK on agent machines ──
OPTIONAL_IMPORTS = {
    "torch", "torchaudio", "torchvision", "tts",
    "faster_whisper", "ctranslate2", "edge_tts", "f5_tts",
    "pydub", "playwright", "weasyprint", "tkinterdnd2",
    "cv2", "sklearn", "asyncpg", "sqlalchemy",
}

# ── Implicit deps (installing X also requires Y) ──
IMPLICIT_DEPS = {
    "sqlalchemy[asyncio]": ["asyncpg"],
    "google-auth": ["google-auth-httplib2"],
}


def scan_imports(base_dir: str) -> set:
    """Scan all .py files under base_dir and return set of top-level import names."""
    imported = set()
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if not f.endswith(".py"):
                continue
            try:
                content = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
                for m in re.findall(r"^\s*(?:from|import)\s+([\w.]+)", content, re.MULTILINE):
                    imported.add(m.split(".")[0].lower())
            except Exception:
                pass
    return imported
