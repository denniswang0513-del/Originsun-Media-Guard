"""Originsun Agent OTA Updater — backup → download → pip → preflight → rollback.

Called by update_agent.bat. Exit codes:
  0 = success (or no update needed)
  1 = update failed and rolled back to previous version
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

# ── Paths ──
INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))
ROLLBACK_DIR = os.path.join(INSTALL_DIR, "_rollback")
STATUS_FILE = os.path.join(INSTALL_DIR, "update_status.json")
VERSION_FILE = os.path.join(INSTALL_DIR, "version.json")
REQUIREMENTS = os.path.join(INSTALL_DIR, "requirements_agent.txt")
PREFLIGHT_SCRIPT = os.path.join(INSTALL_DIR, "preflight.py")
PYTHON = sys.executable

# ── Default master server ──
DEFAULT_MASTER = "http://192.168.1.107:8000"


# ────────────────────────────────────────
# Logging & Status
# ────────────────────────────────────────

LOG_FILE = os.path.join(INSTALL_DIR, "update_agent.log")


def log(msg: str):
    """印到 stdout 之外也寫進 update_agent.log（helper 把 stdout 丟 DEVNULL，不落檔就查不到 OTA 為什麼失敗）。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fp:
            fp.write(line + "\n")
    except OSError:
        pass


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


def _kill_port(port: int):
    """Kill all processes listening on the given port."""
    try:
        out = subprocess.run(
            f'netstat -aon | findstr ":{port} " | findstr "LISTENING"',
            shell=True, capture_output=True, text=True, timeout=10,
        ).stdout
        pids = set()
        for line in out.strip().splitlines():
            parts = line.split()
            if parts:
                pids.add(parts[-1])
        for pid in pids:
            if pid and pid != "0":
                log(f"Killing PID {pid} on port {port}")
                subprocess.run(f"taskkill /F /PID {pid}", shell=True,
                               capture_output=True, timeout=10)
        if pids:
            time.sleep(2)
    except Exception as e:
        log(f"Warning: kill_port failed: {e}")


# ────────────────────────────────────────
# pip
# ────────────────────────────────────────

PIP_TIMEOUT = 600   # 原本 300：慢的機器抓 Pillow 那 7MB 就超過，被殺在一半反而留下半套（見 Phase 5 的自救）


def _drop_transitional_pip_ini():
    """2.5.28 過渡用的 python_embed\pip.ini（no-deps，讓舊 update_agent 不碰半套的 pillow）：這支新的 update_agent 會自救，
    不需要它了，裝套件前先拿掉（只刪帶我們標記的那份，別動人家自己放的 pip.ini）。"""
    path = os.path.join(INSTALL_DIR, "python_embed", "pip.ini")
    try:
        if os.path.isfile(path) and "originsun-ota-transitional" in open(path, encoding="utf-8", errors="replace").read():
            os.remove(path)
            log("Removed transitional python_embed/pip.ini")
    except OSError as e:
        log(f"Warning: could not remove transitional pip.ini: {e}")


def _pip_install(req_file: str):
    return subprocess.run(
        [PYTHON, "-m", "pip", "install", "-q", "-r", req_file, "--no-warn-script-location"],
        capture_output=True, text=True, timeout=PIP_TIMEOUT,
    )


MAX_HEALS = 8   # 一次更新最多救幾個沒 RECORD 的套件（ai_2 重灌後 pillow、requests… 一連好幾個）


def _pip_with_self_heal(req_file: str):
    """`pip install -r`，遇到「Cannot uninstall X None（no RECORD）」就對 X --force-reinstall --no-deps 清單上釘的版本，
    再整份重跑；**一個接一個救**（2026-09-16 ai_2：只救一個的話 pillow 好了換 requests 擋，每推一輪才多好一個）。
    同一個套件救過還是同樣的錯就停（不無限迴圈）；最多 MAX_HEALS 個。"""
    result = _pip_install(req_file)
    healed: set = set()
    while result.returncode != 0:
        pkg = _no_record_pkg(result)
        if not pkg or pkg.lower() in healed or len(healed) >= MAX_HEALS:
            break
        healed.add(pkg.lower())
        pin = _pinned(req_file, pkg) or pkg
        log(f"pip: {pkg} has no RECORD (half-installed) -> force-reinstall {pin} ({len(healed)}/{MAX_HEALS})")
        write_status(5, 62, f"修復半套的套件 {pkg}（第 {len(healed)} 個）...")
        # 🔴 用 --ignore-installed 不用 --force-reinstall：force-reinstall 也會先 uninstall，撞到同一個「no RECORD」
        #    （2026-09-16 ai_2 真機：requests 連救 7 輪都在這裡倒）。ignore-installed 直接把新版檔案蓋上去、寫好 RECORD，舊檔留著無害。
        fix = subprocess.run(
            [PYTHON, "-m", "pip", "install", "-q", "--ignore-installed", "--no-deps", pin, "--no-warn-script-location"],
            capture_output=True, text=True, timeout=PIP_TIMEOUT,
        )
        log(f"force-reinstall exit {fix.returncode}:\n{fix.stdout}\n{fix.stderr}")
        result = _pip_install(req_file)
    return result


def _no_record_pkg(result) -> str:
    """pip 抱怨「Cannot uninstall X None … no RECORD file」→ 回 X；不是這種錯回空字串。"""
    text = (result.stderr or "") + "\n" + (result.stdout or "")
    if "no RECORD file" not in text and "uninstall-no-record-file" not in text:
        return ""
    m = re.search(r"Cannot uninstall ([A-Za-z0-9_.\-]+)", text)
    return m.group(1) if m else ""


def _pinned(req_file: str, pkg: str) -> str:
    """requirements 裡那個套件釘的那行（`Pillow==12.3.0`），大小寫不分；沒釘回空字串。"""
    try:
        for line in open(req_file, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and re.split(r"[>=<\[ ]", line)[0].lower() == pkg.lower():
                return line
    except OSError:
        pass
    return ""


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
            dst = os.path.join(ROLLBACK_DIR, fname)
            os.makedirs(os.path.dirname(dst), exist_ok=True)   # AGENT_FILES 有子路徑（2.5.30 的 python_embed/pip.ini）時 _rollback 底下沒那層目錄
            shutil.copy2(src, dst)

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
            dst = os.path.join(INSTALL_DIR, fname)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(bak, dst)

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

    # Clear stale status from previous runs
    try:
        if os.path.exists(STATUS_FILE):
            os.remove(STATUS_FILE)
    except Exception:
        pass

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
    _drop_transitional_pip_ini()   # 要在 backup 之前：2.5.30 的 manifest 帶著它，backup 複製到 _rollback/python_embed/ 會因為沒那層目錄而炸
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

    # Kill running server before extracting (avoid locked files on Windows)
    write_status(4, 40, "正在停止服務...")
    _kill_port(8000)

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
            result = _pip_with_self_heal(req_file)
            if result.returncode != 0:
                # 🔴 取**尾段**、stderr 沒東西就看 stdout：pip 的 ERROR 在最後、前面常只有「pip 有新版」那條 notice，
                #    取頭 300 字只會看到 notice（2026-09-15 五台機器回滾時原因全被吃掉）。完整輸出在 update_agent.log。
                raw = (result.stderr or "").strip() or (result.stdout or "").strip() or "unknown error"
                lines = [ln for ln in raw.splitlines() if ln.strip() and not ln.startswith("[notice]")]
                err = "\n".join(lines)[-600:] if lines else raw[-600:]
                log(f"pip install failed (exit {result.returncode}):\n{result.stdout}\n{result.stderr}")
                rollback(f"pip 安裝失敗: {err[-200:]}")
                return 1
            log("pip install OK.")
        except subprocess.TimeoutExpired:
            log(f"pip install timed out ({PIP_TIMEOUT}s)")
            rollback("pip 安裝逾時")
            return 1
        except Exception as e:
            log(f"pip error: {e}")
            rollback(f"pip 執行錯誤: {e}")
            return 1
    else:
        log("Phase 5: No requirements_agent.txt found, skipping pip.")

    # ── Phase 5b: MANIFEST SAFETY NET ──
    manifest_file = os.path.join(INSTALL_DIR, "update_manifest.json")
    if os.path.isfile(manifest_file):
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            extra_pkgs = manifest.get("pip_install", [])
            if extra_pkgs:
                log(f"Phase 5b: Installing {len(extra_pkgs)} manifest packages: {', '.join(extra_pkgs)}")
                write_status(5, 70, f"正在安裝額外套件 ({len(extra_pkgs)})...")
                subprocess.run(
                    [PYTHON, "-m", "pip", "install", "-q", "--no-warn-script-location"] + extra_pkgs,
                    capture_output=True, text=True, timeout=300,
                )
                log("Manifest packages installed.")
        except Exception as e:
            log(f"Manifest install warning (non-fatal): {e}")

    # ── Phase 6: PREFLIGHT ──
    log("Phase 6: Running preflight check...")
    write_status(6, 80, "正在驗證更新...")
    if os.path.isfile(PREFLIGHT_SCRIPT):
        try:
            result = subprocess.run(
                [PYTHON, PREFLIGHT_SCRIPT],
                capture_output=True, text=True, timeout=30,
                cwd=INSTALL_DIR,
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
