import os
import zipfile
import shutil

# 定義要打包的檔案清單 (確保發布本機 Agent 所需的最少檔案)
TARGET_ZIP = "Originsun_Agent.zip"
FILES_TO_PACK = [
    "main.py",
    "config.py",
    "report_generator.py",
    "notifier.py",
    "drive_sync.py",
    "transcriber.py",
    "download_model.py",
    "update_agent.bat",
    "start_hidden.vbs",
    "logo.ico",
    "ffprobe.exe",
    "ffmpeg.exe",
]
DIRS_TO_PACK = [
    "frontend",
    "templates",
    "windows_helper",
    "python_embed",
    "core",
    "routers",
]

def create_agent_zip():
    print(f"==================================================")
    print(f"  即將建立本機代理加速器安裝包: {TARGET_ZIP}")
    print(f"==================================================")

    # 如果舊的 ZIP 存在，先刪除，確保乾淨打包
    if os.path.exists(TARGET_ZIP):
        os.remove(TARGET_ZIP)
        print(f"[清除] 找到並刪除舊版 {TARGET_ZIP}")

    print(f"\n[打包中] 開始寫入壓縮檔，請稍候 (根據 .venv 大小可能需要幾十秒)...")
    
    with zipfile.ZipFile(TARGET_ZIP, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # 1. 寫入個別檔案
        for file in FILES_TO_PACK:
            if os.path.exists(file):
                zipf.write(file)
                print(f"  - 寫入檔案: {file}")
            else:
                print(f"  ! 警告: 找不到檔案 {file}，將被忽略。")

        # 2. 寫入資料夾 (包含底下所有內容)
        for d in DIRS_TO_PACK:
            if os.path.exists(d):
                for root, dirs, files in os.walk(d):
                    for file in files:
                        file_path = os.path.join(root, file)
                        # 不要把 pycache 包進去減小體積
                        if "__pycache__" not in file_path:
                            zipf.write(file_path)
                print(f"  - 寫入目錄: {d}/")
            else:
                print(f"  ! 警告: 找不到目錄 {d}，將被忽略。")

    print(f"\n==================================================")
    zip_size_mb = os.path.getsize(TARGET_ZIP) / (1024 * 1024)
    print(f"  [OK] 打包完成！")
    print(f"  檔案名稱: {TARGET_ZIP}")
    print(f"  檔案大小: {zip_size_mb:.2f} MB")
    print(f"==================================================")
    print(f"您可以隨意將此腳本加入環境變數或建立捷徑，一鍵執行！")

if __name__ == "__main__":
    create_agent_zip()
