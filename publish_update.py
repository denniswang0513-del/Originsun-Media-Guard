import os
import sys
import json
import subprocess
from datetime import datetime
import shutil

VERSION_FILE = "version.json"
BUILD_SCRIPT = "build_agent_zip.py"
NAS_OTA_PATH = r"\\192.168.1.132\Container\AI_Workspace\agents\Originsun Media Guard Pro"

def main():
    print("="*60)
    print("[*] Originsun SaaS - Auto Publisher")
    print("="*60)
    print("")

    if not os.path.exists(VERSION_FILE):
        print(f"錯誤: 找不到 {VERSION_FILE}")
        return

    # 1. 讀取目前版本
    with open(VERSION_FILE, "r", encoding="utf-8") as f:
        v_data = json.load(f)
    
    current_version = v_data.get("version", "未知")
    print(f"[*] 目前系統版本: {current_version}")
    
    # 2. 詢問新版本資訊
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", type=str, default="")
    parser.add_argument("--notes", type=str, default="")
    parser.add_argument("--sync", type=str, default="")
    args, _ = parser.parse_known_args()

    if args.version:
        new_version = args.version
    else:
        try:
            raw_nv = input(f"[?] 請輸入新版號 (直接 Enter 沿用 {current_version}): ").strip()
            new_version = raw_nv.encode(sys.stdin.encoding, 'replace').decode('utf-8', 'ignore') if raw_nv else current_version
        except Exception:
            new_version = current_version
            
    if args.notes:
        notes = args.notes
    else:
        try:
            raw_notes = input("[?] 請輸入本次更新日誌 (例如: 修復 XXX Bug): ").strip()
            notes = raw_notes.encode(sys.stdin.encoding, 'replace').decode('utf-8', 'ignore') if raw_notes else v_data.get("notes", "微幅更新")
        except Exception:
            notes = v_data.get("notes", "微幅更新")

    if not new_version:
        new_version = current_version
        
    if not notes:
        notes = v_data.get("notes", "微幅更新")

    # 3. 寫入新版本檔案
    v_data["version"] = new_version
    v_data["build_date"] = datetime.now().strftime("%Y-%m-%d")
    v_data["notes"] = notes

    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(v_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n[OK] 已更新 {VERSION_FILE} (v{new_version})")

    # 4. 呼叫自動打包腳本
    print("\n[*] 開始編譯並打包 ZIP (供新安裝使用)...")
    if not os.path.exists(BUILD_SCRIPT):
        print(f"錯誤: 找不到打包腳本 {BUILD_SCRIPT}")
        return
    
    result = subprocess.run([sys.executable, BUILD_SCRIPT], shell=False)
    if result.returncode != 0:
        print("\n[ERROR] 打包過程發生錯誤，發布終止。")
        return
        
    # 5. [可選] 同步至 NAS OTA 目錄
    print(f"\n[*] 準備發布 OTA 更新至 NAS: {NAS_OTA_PATH}")
    if args.sync.lower() == "y":
        ans = "y"
    elif args.sync.lower() == "n":
        ans = "n"
    else:
        ans = input("是否要立即同步檔案至 NAS? (y/n) [y]: ").strip().lower()
        
    if ans == "" or ans == "y":
        if not os.path.exists(NAS_OTA_PATH):
            try:
                os.makedirs(NAS_OTA_PATH, exist_ok=True)
            except Exception as e:
                print(f"無法存取或建立 NAS 路徑: {e}")
                print("請手動將檔案複製到 NAS。")
                return

        print("正在同步核心檔案至 NAS...")
        files_to_sync = [
            "main.py",
            "config.py",
            "report_generator.py",
            "notifier.py",
            "drive_sync.py",
            "transcriber.py",
            "download_model.py",
            "version.json",
            "logo.ico",
            "Install_Originsun_Agent.bat",
            "update_agent.bat",
            "0225_requirements.txt",
            "bootstrap.py",
            "Originsun_Agent.zip"
        ]
        
        folders_to_sync = ["frontend", "templates", "windows_helper", "core", "routers"]
        
        for f in files_to_sync:
            if os.path.exists(f):
                shutil.copy2(f, os.path.join(NAS_OTA_PATH, f))
                print(f"  [OK] 同步檔案: {f}")
                
        for folder in folders_to_sync:
            if os.path.exists(folder):
                dest_dir = os.path.join(NAS_OTA_PATH, folder)
                if os.path.exists(dest_dir):
                    shutil.rmtree(dest_dir)
                shutil.copytree(folder, dest_dir)
                print(f"  [OK] 同步資料夾: {folder}\\")
                
        print("\n[OK] 發布成功！NAS 端已擁有最新版。")
        print("[*] 同事將會在他們的網頁上看到「發現新版本」的按鈕。")
    else:
        print("\n跳過 NAS 同步，僅完成在地打包。")

if __name__ == "__main__":
    main()
