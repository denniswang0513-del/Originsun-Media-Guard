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

# ── NAS website-api 同步目標 ──
# /publish 跑完後把這些路徑 scp 到 NAS（讓對外 website-api 拿最新 code）。
# 不在這份 list 裡的東西不會傳（避免污染 NAS container）。
NAS_HOST = "admin@192.168.1.132"
NAS_CODE_DIR = "/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/code"
NAS_DOCKER_DIR = "/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/docker"
SSH_KEY_PATH = os.path.join(
    os.environ.get("USERPROFILE", os.path.expanduser("~")),
    ".ssh", "id_originsun_nas",
)
SSH_DOCKER = "/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker"

# Windows OpenSSH（publish_update 走的是它，非 Git Bash ssh）連這台 NAS 必須帶
# IdentitiesOnly=yes——否則會先試 ssh-agent / 預設 key，在用到 -i 指定的 key 前就
# 卡住 → subprocess timeout（2026-07-02 /publish 就是這樣炸的）。BatchMode 讓認證
# 失敗快速結束而非等互動輸入；ConnectTimeout 上限握手時間。
_SSH_COMMON_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "BatchMode=yes",
    "-o", "IdentitiesOnly=yes",
    "-o", "ConnectTimeout=10",
]

# website-api container 需要的 code 路徑（routers/website/ 跨 import 到 api_crm
# 等模組，所以 routers/ 整個傳；core/db/services/ 同理）
NAS_SYNC_PATHS = [
    "main_website.py", "config.py",
    "routers", "services", "core", "db",
]


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
# NAS website-api code sync
# ────────────────────────────────────────

def sync_website_to_nas() -> bool:
    """scp code paths to NAS + restart website-api container.

    回 True = 全程 OK；False = 任一步失敗（publish 不因此 abort，因為主流程
    cancel 了還會留下尷尬狀態。只記錄 warning 讓使用者手動補）。

    SSH key 不存在直接跳過（dev 環境沒設 NAS access）。
    """
    if not os.path.exists(SSH_KEY_PATH):
        print(f"[NAS sync] SSH key 不存在 ({SSH_KEY_PATH})，跳過同步")
        return False

    base = os.path.dirname(os.path.abspath(__file__))
    print(f"\n[*] 同步 code 到 NAS ({NAS_HOST})...")

    # scp 不認資料夾不存在 — 先 ssh mkdir 確保 NAS 結構存在
    ssh_cmd = ["ssh", "-i", SSH_KEY_PATH] + _SSH_COMMON_OPTS + [NAS_HOST]
    try:
        subprocess.run(
            ssh_cmd + [f"mkdir -p {NAS_CODE_DIR}"],
            check=True, capture_output=True, timeout=25,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        _err = getattr(e, "stderr", b"") or b""
        _err = _err.decode(errors="replace") if isinstance(_err, (bytes, bytearray)) else str(_err)
        print(f"[NAS sync] mkdir 失敗/逾時（NAS 不可達？）: {_err[:200] or type(e).__name__}")
        return False

    # scp 每個路徑（檔案 + 目錄）
    for rel in NAS_SYNC_PATHS:
        src = os.path.join(base, rel)
        if not os.path.exists(src):
            print(f"[NAS sync] 跳過 (不存在): {rel}")
            continue
        scp_args = ["scp", "-i", SSH_KEY_PATH] + _SSH_COMMON_OPTS + ["-q"]
        if os.path.isdir(src):
            scp_args += ["-r", src + "/.", f"{NAS_HOST}:{NAS_CODE_DIR}/{rel}/"]
        else:
            scp_args += [src, f"{NAS_HOST}:{NAS_CODE_DIR}/{rel}"]
        try:
            subprocess.run(scp_args, check=True, capture_output=True, timeout=120)
            print(f"[NAS sync] OK: {rel}")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            _err = getattr(e, "stderr", b"") or b""
            _err = _err.decode(errors="replace") if isinstance(_err, (bytes, bytearray)) else str(_err)
            print(f"[NAS sync] FAIL: {rel} — {_err[:200] or type(e).__name__}")
            return False

    # Restart website-api container 讓新 code 生效
    print(f"[*] 重啟 NAS website-api container...")
    try:
        subprocess.run(
            ssh_cmd + [f"{SSH_DOCKER} restart website-api"],
            check=True, capture_output=True, timeout=30,
        )
        print(f"[OK] NAS website-api 已重啟")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        _err = getattr(e, "stderr", b"") or b""
        _err = _err.decode(errors="replace") if isinstance(_err, (bytes, bytearray)) else str(_err)
        print(f"[NAS sync] container restart 失敗/逾時: {_err[:200] or type(e).__name__}")
        return False


# ────────────────────────────────────────
# NAS nginx redirects sync（硬 301 SEO 權重保留）
# ────────────────────────────────────────

NAS_LAN_API = "http://192.168.1.132:8090"  # Website_Nginx LAN port → website-api
NGINX_SNIPPET_PATH = "/etc/nginx/snippets/redirects.conf"

# nginx 主設定（server block）同步目標 —— 解決 repo↔NAS drift。
# 這份 originsun.conf 以前只手動部署過一次 → 改 repo 版不會上 NAS、兩邊早分岔
# （2026-07-03 上線 polish 時發現 NAS source 竟缺 /uploads/ block）。併進 sync 流程自動化。
NGINX_MAIN_REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docker", "nginx", "originsun.conf")
NGINX_MAIN_NAS = f"{NAS_DOCKER_DIR}/nginx/originsun.conf"   # NAS host source 檔
NGINX_MAIN_DEST = "/etc/nginx/conf.d/default.conf"          # Website_Nginx 容器內


def sync_nginx_conf_to_nas() -> bool:
    """把 repo docker/nginx/originsun.conf 同步到 NAS + docker cp 進 Website_Nginx。

    安全流程（上線 polish 踩過雷後定案）：備份現行 config → docker cp 新版 →
    `nginx -t` 驗證 → **只在通過時 reload；失敗自動還原備份**（壞設定絕不拖垮站）。
    best-effort：任一步失敗只記 warning、回 False，不阻斷 caller。

    只在 master 跑（有 SSH key + repo）。NAS website-api 容器沒 SSH key → skip。
    """
    if not os.path.exists(SSH_KEY_PATH):
        print("[nginx conf sync] SSH key 不存在，跳過")
        return False
    if not os.path.exists(NGINX_MAIN_REPO):
        print(f"[nginx conf sync] repo 無 {NGINX_MAIN_REPO}，跳過")
        return False

    ssh_cmd = ["ssh", "-i", SSH_KEY_PATH] + _SSH_COMMON_OPTS + [NAS_HOST]
    scp_args = ["scp", "-i", SSH_KEY_PATH] + _SSH_COMMON_OPTS + ["-q",
                NGINX_MAIN_REPO, f"{NAS_HOST}:{NGINX_MAIN_NAS}"]
    # 備份→cp 新→驗證→reload / 還原，全部一條 ssh（避免壞 config 落地又沒 reload）
    remote = (
        f"{SSH_DOCKER} exec Website_Nginx cp {NGINX_MAIN_DEST} {NGINX_MAIN_DEST}.bak && "
        f"{SSH_DOCKER} cp {NGINX_MAIN_NAS} Website_Nginx:{NGINX_MAIN_DEST} && "
        f"if {SSH_DOCKER} exec Website_Nginx nginx -t; then "
        f"{SSH_DOCKER} exec Website_Nginx nginx -s reload; "
        f"else {SSH_DOCKER} exec Website_Nginx cp {NGINX_MAIN_DEST}.bak {NGINX_MAIN_DEST}; exit 1; fi"
    )
    try:
        subprocess.run(ssh_cmd + [f"mkdir -p {NAS_DOCKER_DIR}/nginx"],
                       check=True, capture_output=True, timeout=25)
        subprocess.run(scp_args, check=True, capture_output=True, timeout=30)
        subprocess.run(ssh_cmd + [remote], check=True, capture_output=True, timeout=45)
        print("[nginx conf sync] OK — originsun.conf 已同步 + reload")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        _err = getattr(e, "stderr", b"") or b""
        _err = _err.decode(errors="replace") if isinstance(_err, (bytes, bytearray)) else str(_err)
        print(f"[nginx conf sync] FAIL（已還原舊 config、站不受影響）: {_err[:300] or type(e).__name__}")
        return False


def sync_redirects_to_nas() -> bool:
    """從 NAS website-api 拉 redirect map → 生成 nginx snippet → docker cp + reload。

    執行時機：publish 流程末段（在 sync_website_to_nas 之後）+ admin Tab
    「強制重新同步」按鈕。

    雙保險架構：
    - Astro build 已生成軟 301 (meta refresh + canonical) 在 dist/
    - 這個函式生成硬 301 (HTTP 301) 在 nginx
    - 兩條獨立路徑，任一失效另一接手

    SSH key 不存在 / API 無法觸及 → 跳過（軟 301 仍生效，不阻斷 publish）。
    """
    import urllib.request
    import urllib.error
    import tempfile

    if not os.path.exists(SSH_KEY_PATH):
        print(f"[redirects sync] SSH key 不存在 ({SSH_KEY_PATH})，跳過")
        return False

    # 0. 先確保 nginx 主設定（server block，內含 redirects 的 include）是最新 repo 版
    #    → 解決 repo↔NAS drift；best-effort，不影響後續 redirects 同步。
    sync_nginx_conf_to_nas()

    # 1. 從 NAS website-api 拉 redirect map
    try:
        with urllib.request.urlopen(f"{NAS_LAN_API}/api/website/redirects", timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("items") or {}
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"[redirects sync] 拉 redirects 失敗: {e}（軟 301 fallback 仍生效）")
        return False

    # 2. 生成 nginx snippet
    lines = [
        f"# Auto-generated by publish_update.sync_redirects_to_nas() at {datetime.now().isoformat(timespec='seconds')}",
        f"# Source: GET {NAS_LAN_API}/api/website/redirects → {len(items)} redirects",
        "# DO NOT EDIT BY HAND — overwritten next sync",
        "",
    ]
    if not items:
        lines.append("# (no redirects yet)")
    else:
        for from_path, to_path in sorted(items.items()):
            # nginx location 路徑只能含安全字元（後端 _normalize_old_url 已過濾過）
            # 舊站（Yoast）URL 都帶結尾 /，但 redirect map 存無結尾版 → 兩種變體都出規則，
            # 否則 exact-match location 對不到 Google 實際索引的帶 / URL（301 不觸發）。
            lines.append(f"location = {from_path} {{ return 301 {to_path}; }}")
            if from_path != "/" and not from_path.endswith("/"):
                lines.append(f"location = {from_path}/ {{ return 301 {to_path}; }}")
    snippet = "\n".join(lines) + "\n"

    # 3. 寫 local tmp → scp → docker cp → reload
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".conf", text=True)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(snippet)

        ssh_cmd = ["ssh", "-i", SSH_KEY_PATH] + _SSH_COMMON_OPTS + [NAS_HOST]
        scp_args = ["scp", "-i", SSH_KEY_PATH] + _SSH_COMMON_OPTS + ["-q",
                    tmp_path, f"{NAS_HOST}:/tmp/redirects.conf"]

        # scp → /tmp
        subprocess.run(scp_args, check=True, capture_output=True, timeout=30)

        # ssh nas mkdir + docker cp + nginx -t + nginx -s reload
        # 第一次部署時 /etc/nginx/snippets/ 可能不存在，先 mkdir
        subprocess.run(
            ssh_cmd + [
                f"{SSH_DOCKER} exec Website_Nginx mkdir -p /etc/nginx/snippets && "
                f"{SSH_DOCKER} cp /tmp/redirects.conf Website_Nginx:{NGINX_SNIPPET_PATH} && "
                f"{SSH_DOCKER} exec Website_Nginx nginx -t && "
                f"{SSH_DOCKER} exec Website_Nginx nginx -s reload"
            ],
            check=True, capture_output=True, timeout=30,
        )
        print(f"[redirects sync] OK — {len(items)} 條硬 301 已部署到 NAS nginx")
        return True
    except subprocess.CalledProcessError as e:
        # nginx -t 失敗時 reload 會 abort，retain 舊 config（nginx 設計如此）
        print(f"[redirects sync] FAIL: {e.stderr.decode(errors='replace')[:300]}")
        print(f"[redirects sync] 軟 301 fallback 仍生效，網站行為正常")
        return False
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ────────────────────────────────────────
# Main
# ────────────────────────────────────────

def sync_docs_version(version: str) -> None:
    """發版時同步 CLAUDE.md / ROADMAP.md 的版本標記（文件漂移防治）。

    只改「版本標記行」，不碰其他內容；pattern 找不到就跳過，
    任何失敗只 WARN 不擋發版。回滾 version.json 的兩條路徑也要呼叫，
    讓文件標記永遠和 version.json 同步。
    """
    base = os.path.dirname(os.path.abspath(__file__))
    today = datetime.now().strftime("%Y-%m-%d")
    # 檔案 → [(行首 pattern, 替換字串)] — 只替換匹配到的片段，行尾註解保留
    rules = {
        "CLAUDE.md": [
            (re.compile(r"^> \*\*版本\*\*: v[\d.]+（\d{4}-\d{2}-\d{2}）"),
             f"> **版本**: v{version}（{today}）"),
        ],
        "ROADMAP.md": [
            (re.compile(r"^## 現況 \(v[\d.]+\) 基準線"),
             f"## 現況 (v{version}) 基準線"),
            (re.compile(r"^現在 \(v[\d.]+\) ← 你在這裡"),
             f"現在 (v{version}) ← 你在這裡"),
        ],
    }
    for fname, frules in rules.items():
        path = os.path.join(base, fname)
        if not os.path.exists(path):
            continue
        try:
            # newline="" 讀寫都保留原始換行（CRLF/LF），避免整檔換行被改寫
            with open(path, "r", encoding="utf-8", newline="") as f:
                lines = f.readlines()
            changed = False
            for i, line in enumerate(lines):
                for pattern, repl in frules:
                    new_line, n = pattern.subn(repl, line, count=1)
                    if n:
                        lines[i] = new_line
                        changed = True
            if changed:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.writelines(lines)
                print(f"[OK] {fname} 版本標記 → v{version}")
        except Exception as e:
            print(f"[WARN] {fname} 版本標記同步失敗（不擋發版）: {e}")


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

    # Sync doc version markers (CLAUDE.md / ROADMAP.md) — 文件漂移防治
    sync_docs_version(new_version)

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
            sync_docs_version(current_version)
            print(f"[*] 已回滾 {VERSION_FILE} 至 v{current_version}")
            return 1
        print("[OK] Preflight 通過")
    else:
        print("[WARN] preflight.py 不存在，跳過健康檢查")

    # ── Unit test gate：有 pytest 才跑（python_embed 生產環境沒裝就明講跳過）──
    # 注意：不帶 MOCK_FFMPEG/MOCK_NAS — 那兩個 env 會讓 conftest short-circuit
    # 引擎，transcode mock 測試反而全紅（2026-07-06 驗證）。
    print("\n[*] 執行單元測試 gate (pytest tests/unit)...")
    try:
        import pytest as _pytest  # noqa: F401
        _has_pytest = True
    except ImportError:
        _has_pytest = False
    if _has_pytest:
        _t_env = os.environ.copy()
        _t_env.pop("MOCK_FFMPEG", None)
        _t_env.pop("MOCK_NAS", None)
        t_result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/unit", "-q", "--no-header"],
            capture_output=True, text=True, timeout=300, env=_t_env,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        if t_result.returncode != 0:
            print("\n[ERROR] 單元測試失敗！不允許發布壞版本：")
            print((t_result.stdout or t_result.stderr or "")[-2000:])
            v_data["version"] = current_version
            atomic_json_write(VERSION_FILE, v_data)
            sync_docs_version(current_version)
            print(f"[*] 已回滾 {VERSION_FILE} 至 v{current_version}")
            return 1
        _t_tail = (t_result.stdout or "").strip().splitlines()
        print(f"[OK] 單元測試通過（{_t_tail[-1] if _t_tail else 'ok'}）")
    else:
        print("[WARN] pytest 未安裝，跳過單元測試 gate（建議在發版機補裝）")

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

    if ota_mb > 50:
        print(f"\n[ERROR] OTA ZIP 過大 ({ota_mb:.1f} MB > 50 MB)！")
        print("  可能原因: AGENT_DIRS 包含了二進制檔案 (ffmpeg, python_embed 等)")
        print(f"  目前 AGENT_DIRS: {dirs_to_include}")
        print(f"  自動發現的目錄: {[d for d in dirs_to_include if d not in AGENT_DIRS]}")
        # Rollback version.json
        v_data["version"] = current_version
        atomic_json_write(VERSION_FILE, v_data)
        sync_docs_version(current_version)
        print(f"[*] 已回滾 {VERSION_FILE} 至 v{current_version}")
        return 1
    print(f"[OK] OTA 大小正常 ({ota_mb:.1f} MB)")

    # ── Post-publish: restart server so it serves the new version ──
    # Primary: /internal/restart (api_ota path). /system/restart is the
    # legacy duplicate kept only for in-place upgrades from older agents
    # that still call it via /publish.
    print("\n[*] 重啟主控端以載入新版本...")
    import urllib.request
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/v1/internal/restart",
            method="POST", data=b'{}',
            headers={"Content-Type": "application/json",
                     "X-Internal-Key": "originsun-internal-restart"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8000/api/v1/system/restart",
                method="POST", data=b'{}',
                headers={"Content-Type": "application/json",
                         "X-Internal-Key": "originsun-internal-restart"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            print("[WARN] 無法自動重啟主控端，請手動重啟:")
            print(f"  wscript.exe start_hidden.vbs")

    # ── NAS website-api code sync（讓對外網站 admin endpoint 拿到新 code）──
    nas_ok = sync_website_to_nas()

    # ── NAS nginx 硬 301 redirects sync（從 DB 拉 → 寫 nginx snippet → reload）──
    # website-api 重啟後 DB / endpoint 才穩，再來 query redirects 才不會 race。
    redirects_ok = sync_redirects_to_nas() if nas_ok else False

    print(f"\n{'='*60}")
    print(f"[OK] v{new_version} 發布完成！")
    print(f"  - version.json 已更新")
    print(f"  - update_manifest.json 已更新")
    print(f"  - OTA ZIP: {ota_count} 檔, {ota_mb:.1f} MB")
    print(f"  - NAS website-api: {'已同步並重啟' if nas_ok else '同步失敗（手動跑 NAS sync）'}")
    print(f"  - NAS nginx redirects: {'已同步' if redirects_ok else '跳過（軟 301 fallback 仍生效）'}")
    print(f"  - 主控端正在重啟，約 5 秒後生效")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
