import subprocess
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import shutil


def install_ffmpeg_via_choco():
    """嘗試透過 Chocolatey 安裝 FFmpeg"""
    choco = shutil.which("choco")
    if choco is None:
        print("[!] 未找到 Chocolatey，嘗試自動安裝 Chocolatey...")
        install_choco_cmd = (
            'Set-ExecutionPolicy Bypass -Scope Process -Force; '
            '[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; '
            "iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))"
        )
        result = subprocess.run(
            ["powershell", "-Command", install_choco_cmd],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print("[X] Chocolatey 安裝失敗，請手動前往 https://chocolatey.org/install 安裝後重試。")
            print(result.stderr)
            return False
        print("[OK] Chocolatey 安裝成功")

    print("⏳ 正在透過 Chocolatey 安裝 FFmpeg...")
    result = subprocess.run(
        ["choco", "install", "ffmpeg", "-y", "--no-progress"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("[OK] FFmpeg 安裝成功！請重新啟動終端後再執行本腳本。")
        return True
    else:
        print("[X] FFmpeg 安裝失敗，請手動下載：https://ffmpeg.org/download.html")
        print(result.stderr)
        return False


def setup_environment():
    print("=" * 40)
    print("  ANENT 環境自檢啟動")
    print("=" * 40)

    # 1. 安裝 Python 依賴
    print("\n[1/2] 安裝 Python 依賴...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-q"])
    print("[OK] Python 依賴安裝完成")

    # 2. 檢查 FFmpeg（若缺少則自動安裝）
    print("\n[2/2] 檢查 FFmpeg...")
    if shutil.which("ffmpeg") is not None:
        print("[OK] FFmpeg 已就緒")
    else:
        print("[!] 找不到 FFmpeg，嘗試自動安裝...")
        install_ffmpeg_via_choco()

    # TODO: SMB/NAS 掛載（待日後設定）
    # mount_smb()

    print("\n" + "=" * 40)
    print("  環境自檢完成")
    print("=" * 40)


if __name__ == "__main__":
    setup_environment()
