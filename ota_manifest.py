"""OTA Manifest — Single source of truth for agent file lists and import scanning.

Used by: publish_update.py, build_agent_zip.py, update_agent.py, preflight.py
"""

import ast
import os
import re
import sys

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

# 🔴 遞迴 walk（deploy 複製、依賴掃描）用這份，不要用上面那份。
# 上面的 'models' 指的是**根目錄**的 F5-TTS 模型快取（models/f5_tts/，GB 級
# 權重檔）；但按裸名字在每一層剪，會把 db/models/（2026-08-31 拆檔後的 ORM
# 套件）一起剪掉 —— 實際咬過：deploy_to_prod 整包複製完，生產的 db/ 底下
# 就是沒有 models/，而 smoke check 因為舊 models.py 還在所以照樣全綠。
# 根層的 listdir 檢查（OTA zip 探索、publish、build_agent_zip）繼續用
# EXCLUDE_DIRS —— 那裡「models」真的就是根目錄那顆快取。
NESTED_EXCLUDE_DIRS = EXCLUDE_DIRS - {'models'}

#: 🔴 把模組拆成套件之後，**舊的單檔要在每一層部署目標清掉**。三條部署路都是「覆蓋、不刪」
#: （NAS scp、deploy_to_prod 逐檔 copy、機隊 OTA 解壓），所以 `core/schemas.py` 會跟
#: `core/schemas/` 並存。Python 會先找到套件（實測過），功能不會壞 —— 但留一份殭屍檔等於
#: 讓下一個人改到一份根本沒被載入的程式碼。同步／部署／OTA 三處都會把這裡列的刪掉。
#: 路徑相對 repo 根、用斜線；只放**檔案**。
STALE_PATHS = [
    "core/schemas.py",       # 2026-09-11 拆成 core/schemas/ 套件
]

# ── Python stdlib modules (excluded from dependency checks) ──
# 3.10+ 直接問直譯器，不再手維護清單 —— tarfile / unicodedata / zoneinfo
# 各漏過一次，每次都是 preflight/發版現場才發現。master 與機隊都跑 3.11。
# 聯集的殘餘＝不是 stdlib、但一樣不該進 requirements 的名字：
#   pip / certifi 隨環境必在；annotations 是掃描器把
#   `from __future__ import annotations` 當模組名收進來的假陽性。
STDLIB = set(sys.stdlib_module_names) | {"pip", "certifi", "annotations"}

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
    # 🔴 一次性腳本目錄。漏了它 → 發版的依賴掃描把 `scripts` 當成 PyPI 套件寫進
    #    requirements_agent.txt（v2.4.149 真的發生過）。那一行不會讓已在線的機器
    #    出事（OTA 不重跑 pip），但**新機安裝**跑 pip install -r 會裝到不相干的
    #    同名套件或直接失敗。同一類坑之前咬過 zoneinfo 與 pptx。
    "scripts",
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
    "pptx": "python-pptx",   # ⚠ 同型陷阱：模組叫 pptx，發行套件叫 python-pptx
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
    "python-pptx",   # core/doc_text —— 企劃範本抽文字，只在 master 消化時跑
    "openpyxl",      # core/doc_text —— 報價單多為 Excel，只在 master 抽文字時跑
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
        # NESTED_ 版（見上面定義處的理由）；根層那顆 models/ 快取沒有 .py，掃到也無害
        dirs[:] = [d for d in dirs if d not in NESTED_EXCLUDE_DIRS]
        for f in files:
            if not f.endswith(".py"):
                continue
            try:
                with open(os.path.join(root, f), encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
            except Exception:
                continue
            imported |= _imports_in(content)
    return imported


def _imports_in(content: str) -> set:
    r"""一份 .py 原始碼裡的頂層 import 名字。

    🔴 **走 AST 不走 regex**（2026-09-02 發版實際被擋下來才發現）：舊版是
    `re.findall(r"^\s*(?:from|import)\s+([\w.]+)")` 掃**原始文字**，於是
    `core/schemas.py` 描述 `mode` 那四個值的 docstring ——

          import     反過來：把私帳的工項寫成母公司的 CRM 成本行

    被當成 `import 反過來`（Python 的 `\w` 是 Unicode-aware，中文照樣命中），
    自動寫進 `requirements_agent.txt`，preflight 再 `import 反過來` 炸掉。

    真正危險的不是這次這種明顯的假名字，而是**剛好撞到 PyPI 上真有的套件名** ——
    那就會被裝進 9 台 agent 而沒有人發現。AST 只看真的 import 節點，註解與
    docstring 一律不算。

    parse 不過（半寫完的檔）才退回舊的 regex，但要求 ASCII 開頭的識別字 ——
    模組名不會是中文。
    """
    names = set()
    try:
        tree = ast.parse(content)
    except SyntaxError:
        for m in re.findall(r"^[ \t]*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_.]*)",
                            content, re.MULTILINE):
            names.add(m.split(".")[0].lower())
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0].lower() for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            # level>0 ＝相對 import（from . import x）—— 那不是外部套件
            if not node.level and node.module:
                names.add(node.module.split(".")[0].lower())
    return names
