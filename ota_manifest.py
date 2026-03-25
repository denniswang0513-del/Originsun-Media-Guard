"""OTA Manifest — Single source of truth for agent file lists.

Used by: publish_update.py, build_agent_zip.py, update_agent.py
"""

# ── Individual files synced to Agent ──
AGENT_FILES = [
    "main.py",
    "server.py",
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
]

# ── Directories synced to Agent (recursively) ──
AGENT_DIRS = [
    "frontend",
    "templates",
    "core",
    "routers",
    "utils",
    "db",
    "windows_helper",
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
]

# ── Directories excluded from auto-discovery ──
EXCLUDE_DIRS = {
    '.venv', 'venv', 'node_modules', '.git', '.claude', 'tests',
    'models', 'voice', 'credentials', '__pycache__', 'e2e',
    '_rollback',
}
