"""Build Originsun_Agent.zip — full installation package.

Uses ota_manifest.py as the single source of truth for file lists.
"""

import os
import zipfile

from ota_manifest import (
    AGENT_FILES, AGENT_DIRS,
    INSTALL_EXTRA_FILES, INSTALL_EXTRA_DIRS,
    EXCLUDE_DIRS,
)

TARGET_ZIP = "Originsun_Agent.zip"


def _auto_discover_dirs(known_dirs: set) -> list:
    """Scan project root for Python packages not yet in the list."""
    discovered = set(known_dirs)
    base = os.path.dirname(os.path.abspath(__file__))
    for entry in os.listdir(base):
        if entry.startswith(".") or entry.startswith("_"):
            continue
        if entry in EXCLUDE_DIRS:
            continue
        full = os.path.join(base, entry)
        if os.path.isdir(full) and entry not in discovered:
            has_py = any(f.endswith(".py") for f in os.listdir(full))
            if has_py:
                discovered.add(entry)
                print(f"  [自動偵測] 新增資料夾: {entry}/")
    return sorted(discovered)


def create_agent_zip():
    print("=" * 50)
    print(f"  即將建立本機代理加速器安裝包: {TARGET_ZIP}")
    print("=" * 50)

    if os.path.exists(TARGET_ZIP):
        os.remove(TARGET_ZIP)
        print(f"[清除] 找到並刪除舊版 {TARGET_ZIP}")

    print(f"\n[打包中] 開始寫入壓縮檔，請稍候 (根據 .venv 大小可能需要幾十秒)...")

    all_files = list(AGENT_FILES) + list(INSTALL_EXTRA_FILES)
    all_dirs = _auto_discover_dirs(set(AGENT_DIRS) | set(INSTALL_EXTRA_DIRS))

    with zipfile.ZipFile(TARGET_ZIP, "w", zipfile.ZIP_DEFLATED) as zipf:
        # Individual files
        for fname in all_files:
            if os.path.exists(fname):
                zipf.write(fname)
                print(f"  - 寫入檔案: {fname}")
            else:
                print(f"  ! 警告: 找不到 {fname}，跳過")

        # Directories
        for d in all_dirs:
            if os.path.isdir(d):
                for root, dirs, files in os.walk(d):
                    for f in files:
                        fpath = os.path.join(root, f)
                        if "__pycache__" not in fpath:
                            zipf.write(fpath)
                print(f"  - 寫入目錄: {d}/")
            else:
                print(f"  ! 警告: 找不到目錄 {d}，跳過")

    zip_size_mb = os.path.getsize(TARGET_ZIP) / (1024 * 1024)
    print(f"\n{'=' * 50}")
    print(f"  [OK] 打包完成！")
    print(f"  檔案名稱: {TARGET_ZIP}")
    print(f"  檔案大小: {zip_size_mb:.2f} MB")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    create_agent_zip()
