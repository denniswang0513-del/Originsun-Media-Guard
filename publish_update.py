"""Originsun SaaS — Version Publisher (v2).

Usage:
  python publish_update.py --version 1.11.0 --notes "Fix backup bug"
  python publish_update.py   (interactive mode)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

from ota_manifest import (
    EXCLUDE_DIRS, STDLIB, LOCAL_MODULES, IMPORT_TO_PIP,
    SERVER_ONLY_PKGS, IMPLICIT_DEPS, scan_imports,
)

VERSION_FILE = "version.json"
MANIFEST_FILE = "update_manifest.json"
BUILD_SCRIPT = "build_agent_zip.py"


# ────────────────────────────────────────
# Version Utilities
# ────────────────────────────────────────

def validate_version(v: str) -> bool:
    """Validate semver format: MAJOR.MINOR.PATCH (pure digits)."""
    return bool(re.match(r"^\d+\.\d+\.\d+$", v.strip()))


def parse_semver(v: str):
    return tuple(int(p) for p in v.strip().split("."))


def is_greater(new_v: str, old_v: str) -> bool:
    try:
        return parse_semver(new_v) > parse_semver(old_v)
    except (ValueError, TypeError):
        return False


# ────────────────────────────────────────
# Atomic JSON Write
# ────────────────────────────────────────

def atomic_json_write(path: str, data: dict):
    """Write JSON atomically: .tmp → os.replace()."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ────────────────────────────────────────
# Manifest Generator
# ────────────────────────────────────────


def read_requirements(base_dir: str) -> set:
    """Read known pip package names from requirements files."""
    known = set()
    for req_name in ["0225_requirements.txt", "requirements_agent.txt"]:
        req_file = os.path.join(base_dir, req_name)
        if os.path.exists(req_file):
            for line in open(req_file, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#"):
                    known.add(re.split(r"[>=<\[]", line)[0].strip().lower())
    return known


def resolve_new_deps(imported: set, known_pkgs: set) -> list:
    """Resolve import names to pip packages, return list of NEW deps not in requirements."""
    new_deps = []
    for pkg in imported - STDLIB - LOCAL_MODULES:
        if pkg in IMPORT_TO_PIP:
            pip_name = IMPORT_TO_PIP[pkg]
            if not pip_name:
                continue
            if pip_name.lower() not in known_pkgs and pip_name.split("[")[0].lower() not in known_pkgs:
                new_deps.append(pip_name)
        elif pkg not in known_pkgs:
            new_deps.append(pkg)

    # Add implicit deps
    for pkg in list(new_deps):
        for trigger, extras in IMPLICIT_DEPS.items():
            if pkg == trigger:
                new_deps.extend(e for e in extras if e not in new_deps)

    # Filter out server-only packages
    new_deps = [d for d in new_deps if d.lower() not in SERVER_ONLY_PKGS]

    return sorted(set(new_deps))


def generate_manifest(version: str) -> list:
    """Scan imports, auto-update requirements_agent.txt, write manifest. Returns new deps."""
    base = os.path.dirname(os.path.abspath(__file__))
    imported = scan_imports(base)
    known_pkgs = read_requirements(base)
    new_deps = resolve_new_deps(imported, known_pkgs)

    # ── Auto-append new deps to requirements_agent.txt ──
    if new_deps:
        req_path = os.path.join(base, "requirements_agent.txt")
        existing_lower = set()
        if os.path.exists(req_path):
            for line in open(req_path, encoding="utf-8"):
                pkg = re.split(r"[>=<\[]", line.strip())[0].strip().lower()
                if pkg:
                    existing_lower.add(pkg)

        actually_new = [d for d in new_deps if d.split("[")[0].lower() not in existing_lower]
        if actually_new:
            with open(req_path, "a", encoding="utf-8") as f:
                f.write("\n# ── Auto-detected by publish ──\n")
                for dep in actually_new:
                    f.write(f"{dep}\n")
                    print(f"  [AUTO] requirements_agent.txt += {dep}")
            # All new deps were just written — manifest should show none remaining
            new_deps = []

    # Write manifest (for update_agent.py safety net)
    manifest = {
        "version": version,
        "pip_install": new_deps,
        "note": "Agent OTA safety net — update_agent.py also installs these",
    }
    atomic_json_write(os.path.join(base, MANIFEST_FILE), manifest)

    if new_deps:
        print(f"\n[*] {MANIFEST_FILE}: {len(new_deps)} extra packages: {', '.join(new_deps)}")
    else:
        print(f"\n[*] {MANIFEST_FILE}: all dependencies covered by requirements_agent.txt")

    return new_deps


# ────────────────────────────────────────
# Main
# ────────────────────────────────────────

def main():
    print("=" * 60)
    print("[*] Originsun SaaS - Auto Publisher (v2)")
    print("=" * 60)
    print()

    if not os.path.exists(VERSION_FILE):
        print(f"錯誤: 找不到 {VERSION_FILE}")
        return 1

    # Read current version
    with open(VERSION_FILE, "r", encoding="utf-8") as f:
        v_data = json.load(f)
    current_version = v_data.get("version", "0.0.0")
    print(f"[*] 目前版本: {current_version}")

    # Parse args
    parser = argparse.ArgumentParser(description="Originsun Version Publisher")
    parser.add_argument("--version", type=str, default="", help="New version (e.g. 1.11.0)")
    parser.add_argument("--notes", type=str, default="", help="Release notes")
    args, _ = parser.parse_known_args()

    # Get version (from args or interactive)
    if args.version:
        new_version = args.version.strip()
    else:
        if not sys.stdin.isatty():
            print("錯誤: 非互動模式必須提供 --version 參數")
            return 1
        raw = input(f"[?] 新版號 (Enter 沿用 {current_version}): ").strip()
        new_version = raw if raw else current_version

    # Validate version format
    if not validate_version(new_version):
        print(f"錯誤: 版本號 '{new_version}' 格式不正確（需要 X.Y.Z 純數字）")
        return 1

    # Validate version is greater (unless same = re-publish)
    if new_version != current_version and not is_greater(new_version, current_version):
        print(f"錯誤: 新版本 {new_version} 不大於目前版本 {current_version}")
        return 1

    # Get notes (from args or interactive)
    if args.notes:
        notes = args.notes.strip()
    else:
        if not sys.stdin.isatty():
            notes = v_data.get("notes", "微幅更新")
        else:
            raw = input("[?] 更新日誌: ").strip()
            notes = raw if raw else v_data.get("notes", "微幅更新")

    # ── Backup current version for rollback ──
    rollback_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_publish_rollback")
    os.makedirs(rollback_dir, exist_ok=True)
    try:
        shutil.copy2(VERSION_FILE, os.path.join(rollback_dir, "version.json"))
        zip_path = "Originsun_Agent.zip"
        if os.path.exists(zip_path):
            shutil.copy2(zip_path, os.path.join(rollback_dir, "Originsun_Agent.zip"))
        print(f"[OK] Rollback backup saved to {rollback_dir}")
    except Exception as e:
        print(f"[WARN] Rollback backup failed: {e}")

    # Write version.json (atomic)
    v_data["version"] = new_version
    v_data["build_date"] = datetime.now().strftime("%Y-%m-%d")
    v_data["notes"] = notes
    atomic_json_write(VERSION_FILE, v_data)
    print(f"\n[OK] 已更新 {VERSION_FILE} (v{new_version})")

    # Generate manifest + auto-update requirements
    generate_manifest(new_version)

    # ── Server-side preflight: catch broken imports BEFORE packaging ──
    print("\n[*] 執行發布前健康檢查 (preflight)...")
    preflight_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preflight.py")
    if os.path.exists(preflight_script):
        pf_result = subprocess.run(
            [sys.executable, preflight_script],
            capture_output=True, text=True, timeout=30,
        )
        if pf_result.returncode != 0:
            print(f"\n[ERROR] Preflight 失敗！不允許發布壞版本：")
            print(pf_result.stdout)
            # Rollback version.json
            v_data["version"] = current_version
            atomic_json_write(VERSION_FILE, v_data)
            print(f"[*] 已回滾 {VERSION_FILE} 至 v{current_version}")
            return 1
        print("[OK] Preflight 通過")
    else:
        print("[WARN] preflight.py 不存在，跳過健康檢查")

    # Build ZIP
    print("\n[*] 開始編譯並打包 ZIP...")
    if not os.path.exists(BUILD_SCRIPT):
        print(f"錯誤: 找不到 {BUILD_SCRIPT}")
        return 1

    result = subprocess.run([sys.executable, BUILD_SCRIPT], shell=False)
    if result.returncode != 0:
        print("\n[ERROR] 打包失敗，發布終止。")
        return 1

    # ── Post-publish: verify OTA ZIP ──
    print("\n[*] 驗證 OTA 更新 ZIP...")
    from ota_manifest import AGENT_FILES, AGENT_DIRS, EXCLUDE_DIRS
    ota_total = 0
    ota_count = 0
    base = os.path.dirname(os.path.abspath(__file__))
    dirs_to_include = list(AGENT_DIRS)
    for entry in os.listdir(base):
        if entry.startswith('.') or entry.startswith('_') or entry in EXCLUDE_DIRS:
            continue
        full = os.path.join(base, entry)
        if os.path.isdir(full) and entry not in dirs_to_include:
            if any(f.endswith('.py') for f in os.listdir(full)):
                dirs_to_include.append(entry)

    for f in AGENT_FILES:
        fp = os.path.join(base, f)
        if os.path.exists(fp):
            ota_total += os.path.getsize(fp)
            ota_count += 1
    for d in dirs_to_include:
        dp = os.path.join(base, d)
        if not os.path.isdir(dp):
            continue
        for root, _, fnames in os.walk(dp):
            if "__pycache__" in root:
                continue
            for fn in fnames:
                fp = os.path.join(root, fn)
                ota_total += os.path.getsize(fp)
                ota_count += 1

    ota_mb = ota_total / 1024 / 1024
    print(f"  OTA 內容: {ota_count} 個檔案, {ota_mb:.1f} MB (未壓縮)")

    if ota_mb > 10:
        print(f"\n[ERROR] OTA ZIP 過大 ({ota_mb:.1f} MB > 10 MB)！")
        print("  可能原因: AGENT_DIRS 包含了二進制檔案 (ffmpeg, python_embed 等)")
        print(f"  目前 AGENT_DIRS: {dirs_to_include}")
        print(f"  自動發現的目錄: {[d for d in dirs_to_include if d not in AGENT_DIRS]}")
        # Rollback version.json
        v_data["version"] = current_version
        atomic_json_write(VERSION_FILE, v_data)
        print(f"[*] 已回滾 {VERSION_FILE} 至 v{current_version}")
        return 1
    print(f"[OK] OTA 大小正常 ({ota_mb:.1f} MB)")

    # ── Post-publish: restart server so it serves the new version ──
    print("\n[*] 重啟主控端以載入新版本...")
    import urllib.request
    try:
        # Try the admin restart endpoint (local only)
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/v1/system/restart",
            method="POST", data=b'{}',
            headers={"Content-Type": "application/json",
                     "X-Internal-Key": "originsun-internal-restart"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8000/api/v1/internal/restart",
                method="POST", data=b'{}',
                headers={"Content-Type": "application/json",
                         "X-Internal-Key": "originsun-internal-restart"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            print("[WARN] 無法自動重啟主控端，請手動重啟:")
            print(f"  wscript.exe start_hidden.vbs")

    print(f"\n{'='*60}")
    print(f"[OK] v{new_version} 發布完成！")
    print(f"  - version.json 已更新")
    print(f"  - update_manifest.json 已更新")
    print(f"  - OTA ZIP: {ota_count} 檔, {ota_mb:.1f} MB")
    print(f"  - 主控端正在重啟，約 5 秒後生效")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
