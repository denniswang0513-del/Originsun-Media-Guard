"""Originsun Agent OTA Updater — backup → download → pip → preflight → rollback.

Called by update_agent.bat. Exit codes:
  0 = success (or no update needed)
  1 = update failed and rolled back to previous version
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
import re

# ── Paths ──
INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))
ROLLBACK_DIR = os.path.join(INSTALL_DIR, "_rollback")
STATUS_FILE = os.path.join(INSTALL_DIR, "_update_status.json")
VERSION_FILE = os.path.join(INSTALL_DIR, "version.json")
REQUIREMENTS = os.path.join(INSTALL_DIR, "requirements_agent.txt")
PREFLIGHT_SCRIPT = os.path.join(INSTALL_DIR, "preflight.py")
PYTHON = sys.executable

# ── Default master server ──
DEFAULT_MASTER = "http://192.168.1.11:8000"


# ────────────────────────────────────────
# Logging & Status
# ────────────────────────────────────────

def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def write_status(step: int, pct: int, msg: str):
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump({"step": step, "pct": pct, "msg": msg}, f, ensure_ascii=False)
    except Exception:
        pass


# ────────────────────────────────────────
# Version Utilities
# ────────────────────────────────────────

def parse_semver(v: str):
    """Parse 'X.Y.Z' → (X, Y, Z) tuple of ints, or None if invalid."""
    if not v or not isinstance(v, str):
        return None
    parts = v.strip().split(".")
    try:
        return tuple(int(p) for p in parts)
    except (ValueError, TypeError):
        return None


def is_newer(remote_v: str, local_v: str) -> bool:
    """True if remote version is strictly greater than local."""
    r = parse_semver(remote_v)
    l = parse_semver(local_v)
    if r is None or l is None:
        return False
    return r > l


def read_local_version() -> str:
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


# ────────────────────────────────────────
# Network
# ────────────────────────────────────────

def fetch_remote_version(master_url: str) -> str:
    """Fetch version from master server. Returns version string or empty on failure."""
    url = f"{master_url.rstrip('/')}/api/v1/version"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OriginsunAgent/2.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
            return data.get("version", "")
    except Exception as e:
        log(f"Cannot reach master: {e}")
        return ""


def download_update(master_url: str, dest_zip: str) -> bool:
    """Download /download_update ZIP to dest_zip. Returns True on success."""
    url = f"{master_url.rstrip('/')}/download_update"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OriginsunAgent/2.0"})
        with urllib.request.urlopen(req, timeout=300) as r:
            with open(dest_zip, "wb") as f:
                shutil.copyfileobj(r, f)
        return os.path.exists(dest_zip) and os.path.getsize(dest_zip) > 1000
    except Exception as e:
        log(f"Download failed: {e}")
        return False


# ────────────────────────────────────────
# Backup & Rollback
# ────────────────────────────────────────

def backup_current():
    """Copy current agent files to _rollback/ for safety."""
    if os.path.exists(ROLLBACK_DIR):
        shutil.rmtree(ROLLBACK_DIR, ignore_errors=True)
    os.makedirs(ROLLBACK_DIR, exist_ok=True)

    # Import manifest (may not exist on very old agents)
    try:
        from ota_manifest import AGENT_FILES, AGENT_DIRS
    except ImportError:
        AGENT_FILES = ["main.py", "config.py", "version.json", "requirements_agent.txt"]
        AGENT_DIRS = ["frontend", "core", "routers"]

    for fname in AGENT_FILES:
        src = os.path.join(INSTALL_DIR, fname)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(ROLLBACK_DIR, fname))

    for dname in AGENT_DIRS:
        src = os.path.join(INSTALL_DIR, dname)
        if os.path.isdir(src):
            dst = os.path.join(ROLLBACK_DIR, dname)
            shutil.copytree(src, dst, dirs_exist_ok=True)

    log(f"Backup created: {ROLLBACK_DIR}")


def rollback(reason: str):
    """Restore from _rollback/ and re-install old requirements."""
    log(f"ROLLBACK triggered: {reason}")
    write_status(7, 90, f"更新失敗，正在回滾... ({reason})")

    if not os.path.exists(ROLLBACK_DIR):
        log("No rollback directory found! Cannot restore.")
        return

    try:
        from ota_manifest import AGENT_FILES, AGENT_DIRS
    except ImportError:
        AGENT_FILES = ["main.py", "config.py", "version.json", "requirements_agent.txt"]
        AGENT_DIRS = ["frontend", "core", "routers"]

    # Restore files
    for fname in AGENT_FILES:
        bak = os.path.join(ROLLBACK_DIR, fname)
        if os.path.isfile(bak):
            shutil.copy2(bak, os.path.join(INSTALL_DIR, fname))

    # Restore directories
    for dname in AGENT_DIRS:
        bak = os.path.join(ROLLBACK_DIR, dname)
        if os.path.isdir(bak):
            dst = os.path.join(INSTALL_DIR, dname)
            if os.path.exists(dst):
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(bak, dst)

    # Re-install old requirements
    old_req = os.path.join(ROLLBACK_DIR, "requirements_agent.txt")
    if os.path.isfile(old_req):
        log("Re-installing old requirements...")
        subprocess.run(
            [PYTHON, "-m", "pip", "install", "-q", "-r", old_req, "--no-warn-script-location"],
            capture_output=True, timeout=300,
        )

    # Cleanup
    shutil.rmtree(ROLLBACK_DIR, ignore_errors=True)
    log("Rollback complete. Old version restored.")
    write_status(7, 95, f"已回滾到舊版本 ({reason})")


# ────────────────────────────────────────
# Main Update Flow
# ────────────────────────────────────────

def run_update(master_url: str) -> int:
    """Execute the 7-phase update. Returns 0=success, 1=failed+rolled back."""

    # ── Phase 1: CHECK ──
    log("Phase 1: Checking master server...")
    write_status(1, 5, "正在連線到主控端...")
    remote_ver = fetch_remote_version(master_url)
    if not remote_ver:
        log("Master unreachable. Skipping update.")
        write_status(1, 100, "無法連線到主控端，使用現有版本")
        return 0

    # ── Phase 2: COMPARE ──
    local_ver = read_local_version()
    log(f"Phase 2: Local v{local_ver} vs Remote v{remote_ver}")
    write_status(2, 10, f"本機 v{local_ver} / 主控端 v{remote_ver}")

    if not is_newer(remote_ver, local_ver):
        log("Already up-to-date. Skipping update.")
        write_status(2, 100, "已是最新版本")
        return 0

    log(f"Update available: {local_ver} → {remote_ver}")

    # ── Phase 3: BACKUP ──
    log("Phase 3: Backing up current version...")
    write_status(3, 15, "正在備份現有版本...")
    try:
        backup_current()
    except Exception as e:
        log(f"Backup failed: {e}. Aborting update (nothing was changed).")
        write_status(3, 100, f"備份失敗，取消更新: {e}")
        return 0  # Not an error — just skip update

    # ── Phase 4: DOWNLOAD ──
    log("Phase 4: Downloading update...")
    write_status(4, 25, f"正在下載 v{remote_ver}...")

    zip_path = os.path.join(tempfile.gettempdir(), "originsun_update.zip")
    if not download_update(master_url, zip_path):
        rollback("下載失敗")
        return 1

    # Extract
    write_status(4, 50, "正在解壓更新檔案...")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(INSTALL_DIR)
        os.remove(zip_path)
        log("Update extracted.")
    except Exception as e:
        log(f"Extract failed: {e}")
        if os.path.exists(zip_path):
            os.remove(zip_path)
        rollback(f"解壓失敗: {e}")
        return 1

    # ── Phase 5: PIP ──
    req_file = os.path.join(INSTALL_DIR, "requirements_agent.txt")
    if os.path.isfile(req_file):
        log("Phase 5: Installing requirements...")
        write_status(5, 60, "正在安裝套件...")
        try:
            result = subprocess.run(
                [PYTHON, "-m", "pip", "install", "-q", "-r", req_file, "--no-warn-script-location"],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                err = result.stderr[:300] if result.stderr else "unknown error"
                log(f"pip install failed (exit {result.returncode}): {err}")
                rollback(f"pip 安裝失敗: {err[:100]}")
                return 1
            log("pip install OK.")
        except subprocess.TimeoutExpired:
            log("pip install timed out (300s)")
            rollback("pip 安裝逾時")
            return 1
        except Exception as e:
            log(f"pip error: {e}")
            rollback(f"pip 執行錯誤: {e}")
            return 1
    else:
        log("Phase 5: No requirements_agent.txt found, skipping pip.")

    # ── Phase 6: PREFLIGHT ──
    log("Phase 6: Running preflight check...")
    write_status(6, 80, "正在驗證更新...")
    if os.path.isfile(PREFLIGHT_SCRIPT):
        try:
            result = subprocess.run(
                [PYTHON, PREFLIGHT_SCRIPT],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                log(f"Preflight FAILED:\n{result.stdout}")
                rollback(f"健康檢查失敗")
                return 1
            log("Preflight OK.")
        except Exception as e:
            log(f"Preflight error: {e}")
            rollback(f"健康檢查執行錯誤: {e}")
            return 1
    else:
        log("Phase 6: No preflight.py found, skipping.")

    # ── Phase 7: CLEANUP ──
    log("Phase 7: Update successful! Cleaning up...")
    write_status(7, 95, f"更新成功！v{local_ver} → v{remote_ver}")

    if os.path.exists(ROLLBACK_DIR):
        shutil.rmtree(ROLLBACK_DIR, ignore_errors=True)

    log(f"Update complete: v{local_ver} → v{remote_ver}")
    write_status(7, 100, f"更新完成 v{remote_ver}，正在重新啟動...")
    return 0


# ────────────────────────────────────────
# Entry Point
# ────────────────────────────────────────

def get_master_url() -> str:
    """Read master_server from settings.json, or use CLI arg, or default."""
    # CLI arg (passed by update_agent.bat or api_system.py)
    if len(sys.argv) > 1:
        return sys.argv[1]
    # settings.json
    try:
        settings_path = os.path.join(INSTALL_DIR, "settings.json")
        with open(settings_path, "r", encoding="utf-8") as f:
            return json.load(f).get("master_server", DEFAULT_MASTER)
    except Exception:
        return DEFAULT_MASTER


def main():
    log("=" * 50)
    log("Originsun Agent OTA Updater v2")
    log("=" * 50)

    master_url = get_master_url()
    log(f"Master: {master_url}")

    exit_code = run_update(master_url)

    if exit_code == 0:
        log("Ready to start server.")
    else:
        log("Update failed, rolled back. Starting with previous version.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
