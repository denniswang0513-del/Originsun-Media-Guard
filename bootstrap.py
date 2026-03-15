import os
import sys
import subprocess
import shutil
import urllib.request
import zipfile

def in_venv() -> bool:
    """判斷當前是否運行在虛擬環境中"""
    return sys.prefix != sys.base_prefix

def ensure_venv():
    """確保於 .venv 中執行，若無則建立並重啟自己"""
    venv_dir = os.path.join(os.getcwd(), ".venv")
    if not in_venv():
        print("[Bootstrap] 未偵測到虛擬環境，正在自動建立 .venv ...")
        
        if not os.path.exists(venv_dir):
            subprocess.run([sys.executable, "-m", "venv", ".venv"], check=True)
            print("[Bootstrap] .venv 建立完成。")
        else:
            print("[Bootstrap] .venv 目錄已存在。")
            
        print("[Bootstrap] 正在切換至虛擬環境並重新啟動安裝程序...")
        
        # Windows 虛擬環境內的 python 路徑
        venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
        
        # 替換當前程序，交給虛擬環境的 Python 繼續執行
        os.execv(venv_python, [venv_python] + sys.argv)

def install_requirements():
    """靜默安裝 requirements.txt 內的依賴"""
    req_file = os.path.join(os.getcwd(), "requirements.txt")
    if not os.path.exists(req_file):
        print("[Bootstrap] 找不到 requirements.txt，略過套件安裝。")
        return
        
    print("[Bootstrap] 正在使用 pip 安裝/更新系統依賴套件...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"], check=True)
    print("[Bootstrap] 依賴套件安裝完成。")

def ensure_ffmpeg():
    """檢查系統或本地端是否有 FFmpeg，沒有的話自動下載"""
    # 檢查是否已安裝在系統變數中
    if shutil.which("ffmpeg"):
        print("[Bootstrap] 系統已安裝 FFmpeg，路徑關聯正常。")
        return

    # 若系統沒有，檢查專案內的 bin/ 目錄
    bin_dir = os.path.join(os.getcwd(), "bin")
    os.makedirs(bin_dir, exist_ok=True)
    ffmpeg_exe = os.path.join(bin_dir, "ffmpeg.exe")
    
    if os.path.exists(ffmpeg_exe):
        print("[Bootstrap] 專案本地 bin/ 目錄中已找到 FFmpeg。")
        return

    # 若都沒有，自動從 Gyan 官方源下載 Windows 靜態編譯包
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
            # 尋找並解壓縮出 ffmpeg.exe, ffprobe.exe, ffplay.exe 到 bin_dir
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
    """確保根目錄有 version.json"""
    version_file = os.path.join(os.getcwd(), "version.json")
    if not os.path.exists(version_file):
        print("[Bootstrap] 未找到 version.json，正在建立預設版號檔案...")
        try:
            with open(version_file, "w", encoding="utf-8") as f:
                f.write('{"version": "1.0.0", "notes": "Initial setup"}')
            print("[Bootstrap] version.json 建立完成。")
        except Exception as e:
            print(f"[Bootstrap] 建立 version.json 失敗: {e}")

if __name__ == "__main__":
    ensure_venv()
    install_requirements()
    ensure_ffmpeg()
    ensure_version_json()
    print("\n[Bootstrap] 環境自癒與準備程序已經全部執行完畢！")
