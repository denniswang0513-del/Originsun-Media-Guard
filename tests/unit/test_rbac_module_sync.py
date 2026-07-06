"""RBAC 模組清單三方同步 — 新增模組漏改任何一處，這裡會 fail。

三份權威清單（CLAUDE.md 新模組 checklist 的「3 處同步」）：
  1. core/auth.py ALL_MODULES               — 後端 source of truth
  2. frontend/js/shared/tab-config.js       — PERMISSION_GROUPS（前端分組/可見性）
  3. frontend/js/admin/user-mgmt.js         — MODULE_LABELS（權限編輯器中文標籤）

前端是 vanilla JS 沒有 build step，可以直接用 regex 從原始碼抽 key 集合。
"""
import re
from pathlib import Path

from core.auth import ALL_MODULES

_ROOT = Path(__file__).resolve().parents[2]


def _js(path: str) -> str:
    return (_ROOT / path).read_text(encoding="utf-8")


def _tab_config_modules() -> set:
    """抽 PERMISSION_GROUPS 每個 group 的 modules 陣列內容。"""
    src = _js("frontend/js/shared/tab-config.js")
    m = re.search(r"PERMISSION_GROUPS\s*=\s*\[(.*?)\n\];", src, re.DOTALL)
    assert m, "tab-config.js 找不到 PERMISSION_GROUPS 定義"
    keys = set()
    for arr in re.findall(r"modules:\s*\[([^\]]*)\]", m.group(1)):
        keys.update(re.findall(r"'([a-z_]+)'", arr))
    return keys


def _module_labels_keys() -> set:
    src = _js("frontend/js/admin/user-mgmt.js")
    m = re.search(r"MODULE_LABELS\s*=\s*\{([^}]*)\}", src)
    assert m, "user-mgmt.js 找不到 MODULE_LABELS 定義"
    return set(re.findall(r"([a-z_]+)\s*:", m.group(1)))


def test_tab_config_matches_backend():
    assert _tab_config_modules() == set(ALL_MODULES), (
        "tab-config.js PERMISSION_GROUPS 與 core/auth.py ALL_MODULES 不同步"
    )


def test_module_labels_matches_backend():
    assert _module_labels_keys() == set(ALL_MODULES), (
        "user-mgmt.js MODULE_LABELS 與 core/auth.py ALL_MODULES 不同步 — "
        "缺 key 的模組在權限編輯器會顯示裸 key 而非中文標籤"
    )


def test_no_duplicate_modules_across_groups():
    src = _js("frontend/js/shared/tab-config.js")
    m = re.search(r"PERMISSION_GROUPS\s*=\s*\[(.*?)\n\];", src, re.DOTALL)
    all_keys = []
    for arr in re.findall(r"modules:\s*\[([^\]]*)\]", m.group(1)):
        all_keys.extend(re.findall(r"'([a-z_]+)'", arr))
    assert len(all_keys) == len(set(all_keys)), "同一模組出現在多個權限群組"
