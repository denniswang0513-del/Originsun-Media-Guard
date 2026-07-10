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
    "aligner.py",  # align job（forced-align 文字稿到影片音訊）— 用 stable-ts，
                   # agent 端 lazy import 時若沒裝 stable-ts 會優雅 fail
    "download_model.py",
    "taiwan_dict.json",
    "version.json",
    "update_agent.bat",
    "update_agent.py",
    "update_monitor.py",
    "preflight.py",
    "ota_manifest.py",
    "build_agent_zip.py",  # 完整安裝包打包腳本 — 納入 OTA/deploy 才不會在 master 過期
                           # （2026-07-10 踩過：prod 的舊硬寫版打出缺一半檔案的壞安裝包）
    "bootstrap.py",
    "start_hidden.vbs",
    "logo.ico",
    "requirements_agent.txt",
    "update_manifest.json",
    "Install_Originsun_Agent.bat",
    "Install_or_Update.bat",
    "Fix_Task.bat",
    "exiftool.exe",
]

# ── Directories synced to Agent (recursively) ──
# ⚠ 只放「會變動的程式碼」。二進制目錄一律歸 INSTALL_EXTRA_DIRS —— 它們讓 OTA ZIP
# 膨脹，慢機解壓 + Defender 冷掃會撐爆 preflight(30s) 與健康檢查視窗 → 自動回滾。
AGENT_DIRS = [
    "frontend",
    "templates",
    "core",
    "routers",
    "utils",
    "db",
    "services",
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
    # 2026-07-10 從 AGENT_DIRS 移來：506 個檔 / 10.75MB，佔 OTA ZIP 的 86%，
    # 把 ZIP 撐到 12.5MB（超過發布流程的 10MB 上限），機隊自 1.10.181 起升級
    # 一律逾時回滾。exiftool 是靜態二進制、不隨版本變動，OTA 不需要重送；
    # 完整安裝包（build_agent_zip 取 AGENT_DIRS ∪ INSTALL_EXTRA_DIRS）照舊包含。
    "exiftool_files",
]

# ── Directories excluded from auto-discovery and import scanning ──
EXCLUDE_DIRS = {
    '.venv', 'venv', 'node_modules', '.git', '.claude', 'tests',
    'models', 'voice', 'credentials', '__pycache__', 'e2e',
    '_rollback', 'python_embed', 'exiftool_files',
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
    "faulthandler", "smtplib", "imaplib", "poplib", "email",
    "tarfile",  # 標準庫，先前漏列 → preflight 會把它當第三方套件檢查
}

# ── Local project modules (excluded from dependency checks) ──
LOCAL_MODULES = {
    "core", "routers", "utils", "db", "frontend", "templates", "services",
    "core_engine", "tts_engine", "transcriber", "notifier", "config",
    "report_generator", "drive_sync", "download_model", "bootstrap",
    "server", "main", "main_website", "update_monitor", "build_agent_zip",
    "publish_update", "update_agent", "preflight", "ota_manifest",
    "patch_ui", "debug_compare",
    "backup_source", "env_setup", "extract_frames", "remove_all_emojis",
    "aligner",  # root-level WIP module — align job 用，附在 AGENT_FILES
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
    "docx": "python-docx",   # ⚠ PyPI 上的 `docx` 是廢棄套件，正確名稱是 python-docx
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
    # stable-ts (Python module name `stable_whisper`) — align job 用，
    # 只在 master 跑 alignment 時才需要；agent 不需要
    "stable-ts", "stable_whisper",
    "pysrt",  # SRT 解析，align job 用
    # 以下皆為「函式內延遲 import、只在 master 執行」。列在這裡才能阻止 publish 的
    # 依賴掃描把它們自動寫進 requirements_agent.txt —— 一旦寫進去，preflight 就會
    # 把它們視為 agent 必備並在缺少時擋下 OTA（機隊卡在 1.10.181 的同一類病）。
    "soundfile",     # tts_engine
    "cryptography",  # ga_service / gsc_service 簽 JWT
    "python-docx", "pypdf",  # showcase AI 參考文件解析（已 try/except 降級）
    "pytest",        # publish / deploy 的測試 gate
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
    # align job 用的 stable-ts + pysrt；agent 不跑 align，沒裝 OK
    "stable_whisper", "pysrt",
    # httpx：master 端非阻塞 agent 健康輪詢用（lazy import）；agent 不輪詢，沒裝也 OK。
    # ⚠ 它同時列在 requirements_agent.txt，是本集合唯一「有作用」的成員 —— 其餘項目
    # 早已被 SERVER_ONLY_PKGS 擋在 requirements 之外，preflight 本來就不會檢查。
    "httpx",
}

# ── Implicit deps (installing X also requires Y) ──
IMPLICIT_DEPS = {
    "sqlalchemy[asyncio]": ["asyncpg"],
    "google-auth": ["google-auth-httplib2"],
}


def scan_imports(base_dir: str) -> set:
    """Scan all .py files under base_dir and return set of top-level import names.

    Empty strings are filtered: relative imports like `from . import foo`
    match the regex with `m='.'` which splits to `['', '']`, and the empty
    top-level would otherwise leak into resolve_new_deps and end up written
    as a blank line under a new # Auto-detected header in requirements_agent.
    """
    imported = set()
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if not f.endswith(".py"):
                continue
            try:
                with open(os.path.join(root, f), encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
                for m in re.findall(r"^\s*(?:from|import)\s+([\w.]+)", content, re.MULTILINE):
                    name = m.split(".")[0].lower()
                    if name:
                        imported.add(name)
            except Exception:
                pass
    return imported
