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

    # 4. 生成 update_manifest.json（列出 pip 套件供 Agent 自動安裝）
    _generate_manifest(new_version)

    # 5. 呼叫自動打包腳本
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


def _generate_manifest(version):
    """掃描所有 .py 的 import，比對 requirements.txt，生成 update_manifest.json。"""
    import re
    base = os.path.dirname(os.path.abspath(__file__))

    # 收集 requirements.txt 裡的套件名
    req_file = os.path.join(base, "0225_requirements.txt")
    known_pkgs = set()
    if os.path.exists(req_file):
        for line in open(req_file, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                known_pkgs.add(re.split(r'[>=<\[]', line)[0].strip().lower())

    # 掃描所有 .py 的 import
    imported = set()
    exclude = {'.venv', 'venv', 'node_modules', '.git', 'tests', '__pycache__', 'python_embed'}
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in exclude]
        for f in files:
            if not f.endswith('.py'):
                continue
            try:
                content = open(os.path.join(root, f), encoding='utf-8', errors='ignore').read()
                for m in re.findall(r'^\s*(?:from|import)\s+([\w.]+)', content, re.MULTILINE):
                    imported.add(m.split('.')[0].lower())
            except Exception:
                pass

    # 找出 import 了但不在 requirements 裡的第三方套件（排除 stdlib + 本地模組）
    stdlib = {'os','sys','json','time','re','io','math','random','uuid','datetime','pathlib',
              'collections','functools','itertools','typing','enum','dataclasses','abc',
              'subprocess','shutil','tempfile','zipfile','hashlib','socket','signal',
              'threading','asyncio','concurrent','http','urllib','email','html','xml',
              'logging','warnings','traceback','inspect','importlib','pkgutil','copy',
              'struct','base64','hmac','secrets','contextlib','textwrap','string',
              'argparse','configparser','csv','sqlite3','platform','ctypes','glob',
              'fnmatch','stat','mimetypes','webbrowser','multiprocessing',
              'filecmp','gc','locale','queue','tkinter','codecs','operator',
              'fractions','decimal','heapq','bisect','array','weakref','types',
              'numbers','cmath','pprint','dis','token','tokenize','site','pip',
              'distutils','unittest','doctest','venv','ensurepip'}
    # import 名 → pip 套件名 映射（名稱不一致的）
    import_to_pip = {
        'pil': 'Pillow', 'cv2': 'opencv-python', 'sklearn': 'scikit-learn',
        'socketio': 'python-socketio', 'google': 'google-api-python-client',
        'googleapiclient': 'google-api-python-client',
        'google_auth_oauthlib': 'google-auth-oauthlib',
        'sqlalchemy': 'sqlalchemy[asyncio]',  # 需要 asyncio driver
        'starlette': '',  # bundled with fastapi
        'pydub': 'pydub', 'huggingface_hub': 'huggingface-hub',
        'edge_tts': 'edge-tts', 'f5_tts': 'f5-tts',
        'faster_whisper': 'faster-whisper',
        'tkinterdnd2': '',  # optional, skip
    }
    local_modules = {'core','routers','utils','db','frontend','templates','windows_helper',
                     'core_engine','tts_engine','transcriber','notifier','config',
                     'report_generator','drive_sync','download_model','bootstrap',
                     'server','main','update_monitor','build_agent_zip','publish_update'}

    new_deps = []
    for pkg in imported - stdlib - local_modules:
        # 用映射表轉換 import 名 → pip 套件名
        if pkg in import_to_pip:
            pip_name = import_to_pip[pkg]
            if not pip_name:
                continue  # 空字串 = 跳過（bundled 或 optional）
            if pip_name.lower() not in known_pkgs:
                new_deps.append(pip_name)
        elif pkg not in known_pkgs:
            new_deps.append(pkg)

    # 補充隱式依賴（不在 import 裡但必須安裝的）
    implicit_deps = {
        'sqlalchemy[asyncio]': ['asyncpg'],  # PostgreSQL async driver
    }
    for pkg in list(new_deps):
        for trigger, extras in implicit_deps.items():
            if pkg == trigger:
                new_deps.extend(e for e in extras if e not in new_deps)

    # 生成 manifest
    manifest = {
        "version": version,
        "pip_install": sorted(new_deps),
        "note": "Agent 更新時自動 pip install 這些套件"
    }
    manifest_path = os.path.join(base, "update_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    if new_deps:
        print(f"\n[*] update_manifest.json: 偵測到 {len(new_deps)} 個新套件: {', '.join(sorted(new_deps))}")
    else:
        print(f"\n[*] update_manifest.json: 無新增套件")


if __name__ == "__main__":
    main()
