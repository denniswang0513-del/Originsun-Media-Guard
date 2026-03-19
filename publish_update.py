import os
import sys
import json
import subprocess
from datetime import datetime

VERSION_FILE = "version.json"
BUILD_SCRIPT = "build_agent_zip.py"


def main():
    print("=" * 60)
    print("[*] Originsun SaaS - Auto Publisher")
    print("=" * 60)
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

    print("\n[OK] 發布完成！Agent ZIP 已建置。")
    print("[*] 同事的 Agent 會透過 HTTP 偵測到新版本並顯示更新通知。")


if __name__ == "__main__":
    main()
