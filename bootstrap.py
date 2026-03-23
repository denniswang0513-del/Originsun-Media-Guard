"""
Originsun Agent — Bootstrap & OTA Updater (stdlib only, no external deps)

Two modes:
  python bootstrap.py                  → Initial setup (venv, pip, ffmpeg)
  python bootstrap.py --update [URL]   → OTA update from server
  python bootstrap.py --update         → Uses master_server from settings.json
"""
import os, sys, json, zipfile, tempfile, subprocess, shutil, signal, time
import urllib.request

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _find_install_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _read_master_server(base_dir):
    try:
        with open(os.path.join(base_dir, "settings.json"), "r", encoding="utf-8") as f:
            return json.load(f).get("master_server", "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Mode 1: OTA Update (--update)
# ---------------------------------------------------------------------------

def _kill_port(port):
    """Kill any process listening on the given port. Returns list of killed PIDs."""
    killed = []
    try:
        out = subprocess.check_output("netstat -aon", text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                pid = int(line.split()[-1])
                if pid == os.getpid():
                    continue
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True)
                killed.append(pid)
                print(f"  Killed PID {pid}")
    except Exception as e:
        print(f"  Warning: {e}")
    return killed


def _restart_agent(base_dir):
    """Start the agent using start_hidden.vbs or update_agent.bat."""
    vbs = os.path.join(base_dir, "start_hidden.vbs")
    if os.path.exists(vbs):
        subprocess.Popen(["wscript.exe", vbs])
    else:
        bat = os.path.join(base_dir, "update_agent.bat")
        subprocess.Popen(bat, shell=True, creationflags=0x00000008)


def _rollback(base_dir, backup_dir, reason=""):
    """Restore backed-up files and show a Windows alert dialog."""
    if not os.path.isdir(backup_dir):
        print("[Rollback] No backup found, cannot rollback.")
        return
    print("[Rollback] Restoring previous version...")
    restored = 0
    for root, _dirs, files in os.walk(backup_dir):
        for fname in files:
            bak_path = os.path.join(root, fname)
            rel = os.path.relpath(bak_path, backup_dir)
            dst = os.path.join(base_dir, rel)
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(bak_path, dst)
                restored += 1
            except Exception as e:
                print(f"  Warning: failed to restore {rel}: {e}")
    print(f"  Restored {restored} file(s).")
    try:
        shutil.rmtree(backup_dir)
    except OSError:
        pass
    _show_rollback_alert(reason)


def _show_rollback_alert(reason):
    """Pop up a native Windows message box explaining the rollback."""
    safe_reason = reason.replace('"', "'")
    msg = (
        "OTA 更新失敗，已自動回滾至舊版本。" '" & vbCrLf & vbCrLf & "'
        f"原因：{safe_reason}" '" & vbCrLf & vbCrLf & "'
        "舊版本已重新啟動，可正常使用。" '" & vbCrLf & "'
        "請通知管理員檢查更新檔案。"
    )
    try:
        vbs_path = os.path.join(tempfile.gettempdir(), "originsun_rollback_alert.vbs")
        with open(vbs_path, "w", encoding="utf-8") as f:
            # vbExclamation = 48
            f.write(f'MsgBox "{msg}", 48, "Originsun 更新回滾通知"\n')
        subprocess.Popen(["wscript.exe", vbs_path])
    except Exception:
        pass  # alert is best-effort


def _find_python(base_dir):
    """Find the best Python executable for this installation."""
    for p in [
        os.path.join(base_dir, ".venv", "Scripts", "python.exe"),
        os.path.join(base_dir, "python_embed", "python.exe"),
    ]:
        if os.path.exists(p):
            return p
    return None


def run_update(server_url=""):
    base_dir = _find_install_dir()

    if not server_url:
        server_url = _read_master_server(base_dir)
    if not server_url:
        server_url = "http://192.168.1.11:8000"
    server_url = server_url.rstrip("/")

    print("=" * 52)
    print("  Originsun Media Guard Pro \u2014 OTA Updater")
    print("=" * 52)
    print(f"\n  Install dir : {base_dir}")
    print(f"  Server      : {server_url}\n")

    # 0) Pre-check: reject update if worker is busy
    try:
        req = urllib.request.Request(
            "http://localhost:8000/api/v1/status",
            headers={"User-Agent": "OriginsunBootstrap/1.0"},
        )
        resp_data = json.loads(urllib.request.urlopen(req, timeout=3).read())
        busy = resp_data.get("worker_busy") or resp_data.get("active_job_count", 0) > 0
        if busy:
            print("[BLOCKED] 有任務正在執行中，請等待完成後再更新。")
            _show_rollback_alert("有任務正在執行中，無法更新。請等待任務完成後再試。")
            sys.exit(1)
    except Exception:
        pass  # server might already be stopped, proceed anyway

    # 1) Kill existing agent on port 8000
    print("[System] Stopping old Agent on port 8000...")
    _kill_port(8000)
    time.sleep(2)

    # 2) Download update ZIP
    zip_path = os.path.join(tempfile.gettempdir(), "originsun_update.zip")
    url = f"{server_url}/download_update"
    print(f"[System] Downloading from {url} ...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OriginsunBootstrap/1.0"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(zip_path, "wb") as f:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        print(f"\r  {downloaded // 1024:,} KB / {total // 1024:,} KB ({pct}%)",
                              end="", flush=True)
            print()
    except Exception as e:
        print(f"\n[ERROR] Download failed: {e}")
        sys.exit(1)

    # 3) Backup + 4) Extract in one ZIP open
    backup_dir = os.path.join(base_dir, "_backup")
    print("[System] Backing up current version...")
    try:
        shutil.rmtree(backup_dir, ignore_errors=True)
        os.makedirs(backup_dir)
        with zipfile.ZipFile(zip_path, "r") as zf:
            backed_up = 0
            failed = 0
            for name in zf.namelist():
                if name.endswith("/"):
                    continue  # skip directories
                src = os.path.join(base_dir, name)
                if os.path.isfile(src):
                    dst = os.path.join(backup_dir, name)
                    try:
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                        backed_up += 1
                    except Exception as e:
                        print(f"  Warning: failed to backup {name}: {e}")
                        failed += 1
            print(f"  Backup: {backed_up} file(s) saved, {failed} failed.")
            # Extract inside the same ZipFile context
            print("[System] Extracting update...")
            zf.extractall(base_dir)
    except zipfile.BadZipFile as e:
        print(f"[ERROR] Bad ZIP file: {e}")
        _rollback(base_dir, backup_dir, "更新檔案損毀")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Backup/extraction failed: {e}")
        _rollback(base_dir, backup_dir, f"解壓縮失敗：{e}")
        sys.exit(1)
    finally:
        try:
            os.unlink(zip_path)
        except OSError:
            pass

    # 5) Install new pip dependencies from update_manifest.json
    manifest_path = os.path.join(base_dir, "update_manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as mf:
                manifest = json.load(mf)
            new_pkgs = manifest.get("pip_install", [])
            if new_pkgs:
                print(f"[System] Installing {len(new_pkgs)} new package(s): {', '.join(new_pkgs)}")
                # 找 pip
                pip_exe = None
                for p in [
                    os.path.join(base_dir, ".venv", "Scripts", "pip.exe"),
                    os.path.join(base_dir, "python_embed", "Scripts", "pip.exe"),
                ]:
                    if os.path.exists(p):
                        pip_exe = p
                        break
                if not pip_exe:
                    # fallback: python -m pip
                    python_exe = _find_python(base_dir)
                    if python_exe:
                        pip_exe = [python_exe, "-m", "pip"]
                if pip_exe:
                    cmd = pip_exe if isinstance(pip_exe, list) else [pip_exe]
                    cmd += ["install", "--quiet"] + new_pkgs
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    if result.returncode == 0:
                        print(f"  [OK] Packages installed successfully.")
                    else:
                        print(f"  [WARN] pip install failed (non-fatal): {result.stderr[:200]}")
                else:
                    print("  [WARN] pip not found, skipping package installation.")
            else:
                print("[System] No new packages required.")
        except Exception as e:
            print(f"  [WARN] Manifest processing failed (non-fatal): {e}")

    # 6) Startup test — verify new code can at least import
    print("[System] Testing new code imports...")
    python_exe = _find_python(base_dir)
    if python_exe:
        test_result = subprocess.run(
            [python_exe, "-c", "from routers import api_system; print('OK')"],
            capture_output=True, text=True, timeout=30, cwd=base_dir,
        )
        if test_result.returncode != 0:
            print(f"  [ERROR] Import test failed: {test_result.stderr[:300]}")
            _rollback(base_dir, backup_dir, f"新版程式碼 import 失敗")
            _restart_agent(base_dir)
            sys.exit(1)
        print("  [OK] Import test passed.")

    # 7) Restart agent
    print("\n[OK] Upgrade complete! Restarting Agent...")
    _restart_agent(base_dir)

    # 8) Health check — rollback if new version fails to start
    print("[System] Verifying new version starts correctly...")
    healthy = False
    health_req = urllib.request.Request(
        "http://localhost:8000/api/v1/health",
        headers={"User-Agent": "OriginsunBootstrap/1.0"},
    )
    for i in range(15):
        try:
            urllib.request.urlopen(health_req, timeout=2)
            healthy = True
            break
        except Exception:
            print(f"\r  Waiting for health check... ({i + 1}/15)", end="", flush=True)
            time.sleep(1)
    print()

    if healthy:
        print("[OK] New version is running. Update successful!")
        shutil.rmtree(backup_dir, ignore_errors=True)
    else:
        print("[ERROR] New version failed to start!")
        _kill_port(8000)
        time.sleep(1)
        _rollback(base_dir, backup_dir, "新版啟動後無回應（Health Check 15 秒逾時）")
        _restart_agent(base_dir)
        print("[OK] Rolled back to previous version and restarted.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Mode 2: Initial Setup (default — venv, pip, ffmpeg)
# ---------------------------------------------------------------------------

def in_venv():
    return sys.prefix != sys.base_prefix


def ensure_venv():
    venv_dir = os.path.join(os.getcwd(), ".venv")
    if not in_venv():
        print("[Bootstrap] 未偵測到虛擬環境，正在自動建立 .venv ...")
        if not os.path.exists(venv_dir):
            subprocess.run([sys.executable, "-m", "venv", ".venv"], check=True)
            print("[Bootstrap] .venv 建立完成。")
        else:
            print("[Bootstrap] .venv 目錄已存在。")
        print("[Bootstrap] 正在切換至虛擬環境並重新啟動安裝程序...")
        venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
        os.execv(venv_python, [venv_python] + sys.argv)


def install_requirements():
    req_file = os.path.join(os.getcwd(), "requirements.txt")
    if not os.path.exists(req_file):
        print("[Bootstrap] 找不到 requirements.txt，略過套件安裝。")
        return
    print("[Bootstrap] 正在使用 pip 安裝/更新系統依賴套件...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"], check=True)
    print("[Bootstrap] 依賴套件安裝完成。")


def ensure_ffmpeg():
    if shutil.which("ffmpeg"):
        print("[Bootstrap] 系統已安裝 FFmpeg，路徑關聯正常。")
        return
    bin_dir = os.path.join(os.getcwd(), "bin")
    os.makedirs(bin_dir, exist_ok=True)
    ffmpeg_exe = os.path.join(bin_dir, "ffmpeg.exe")
    if os.path.exists(ffmpeg_exe):
        print("[Bootstrap] 專案本地 bin/ 目錄中已找到 FFmpeg。")
        return
    print("[Bootstrap] 系統未安裝 FFmpeg。正在從官方靜態庫自動下載 (需時數分鐘)...")
    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    zip_path = os.path.join(bin_dir, "ffmpeg_temp.zip")

    def report(count, block_size, total_size):
        if total_size > 0:
            percent = min(100, int(count * block_size * 100 / total_size))
            sys.stdout.write(f"\r[Bootstrap] 下載進度... {percent}%")
            sys.stdout.flush()

    try:
        urllib.request.urlretrieve(url, zip_path, reporthook=report)
        print("\n[Bootstrap] 下載完成，正在解壓縮...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for member in zip_ref.namelist():
                filename = os.path.basename(member)
                if filename in ["ffmpeg.exe", "ffprobe.exe", "ffplay.exe"]:
                    source = zip_ref.open(member)
                    target_path = os.path.join(bin_dir, filename)
                    with source, open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)
        os.remove(zip_path)
        print("[Bootstrap] FFmpeg 本地化安裝完成！解壓於 bin/ 資料夾內。")
    except Exception as e:
        print(f"\n[Bootstrap] FFmpeg 下載或解壓失敗: {e}")
        if os.path.exists(zip_path):
            os.remove(zip_path)


def ensure_version_json():
    version_file = os.path.join(os.getcwd(), "version.json")
    if not os.path.exists(version_file):
        print("[Bootstrap] 未找到 version.json，正在建立預設版號檔案...")
        try:
            with open(version_file, "w", encoding="utf-8") as f:
                f.write('{"version": "1.0.0", "notes": "Initial setup"}')
            print("[Bootstrap] version.json 建立完成。")
        except Exception as e:
            print(f"[Bootstrap] 建立 version.json 失敗: {e}")


def run_setup():
    ensure_venv()
    install_requirements()
    ensure_ffmpeg()
    ensure_version_json()
    print("\n[Bootstrap] 環境自癒與準備程序已經全部執行完畢！")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--update" in sys.argv:
        # python bootstrap.py --update [http://server:port]
        idx = sys.argv.index("--update")
        url = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        run_update(url)
    else:
        run_setup()
