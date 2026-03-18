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

    # 1) Kill existing agent on port 8000
    print("[System] Stopping old Agent on port 8000...")
    try:
        out = subprocess.check_output("netstat -aon", text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if ":8000" in line and "LISTENING" in line:
                parts = line.split()
                pid = int(parts[-1])
                if pid != os.getpid():
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except OSError:
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                       capture_output=True)
                    print(f"  Killed PID {pid}")
    except Exception as e:
        print(f"  Warning: {e}")
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

    # 3) Extract (overwrite)
    print("[System] Extracting update...")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(base_dir)
    except Exception as e:
        print(f"[ERROR] Extraction failed: {e}")
        sys.exit(1)
    finally:
        try:
            os.unlink(zip_path)
        except OSError:
            pass

    # 4) Restart agent
    print("\n[OK] Upgrade complete! Restarting Agent...")
    vbs = os.path.join(base_dir, "start_hidden.vbs")
    if os.path.exists(vbs):
        subprocess.Popen(["wscript.exe", vbs])
    else:
        bat = os.path.join(base_dir, "update_agent.bat")
        subprocess.Popen(bat, shell=True, creationflags=0x00000008)
    print("[OK] Agent started. Please refresh the web page.")
    time.sleep(2)


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
