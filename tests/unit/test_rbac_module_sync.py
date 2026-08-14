"""RBAC 模組清單跨語言同步 — 前端是 vanilla JS 沒有 build step，抄不掉，
所以正本在 Python、前端各留一份鏡射，由這裡用 regex 從原始碼比對。漏改任何
一處，這裡會 fail。

  1. core/auth.py ALL_MODULES               — 後端 source of truth
  2. frontend/js/shared/tab-config.js       — PERMISSION_GROUPS（前端分組/可見性）
  3. frontend/js/admin/user-mgmt.js         — MODULE_LABELS（權限編輯器中文標籤）
  4. core/auth.py TAB_ACCESS                — 誰進得去某個 tab（閘門本身的參數）
     ↔ tab-config.js TAB_EXTRA_ACCESS       — 同一件事的前端鏡射
"""
import re
from pathlib import Path

from core.auth import ALL_MODULES, TAB_ACCESS

_ROOT = Path(__file__).resolve().parents[2]
_TAB_CONFIG = "frontend/js/shared/tab-config.js"


def _js(path: str) -> str:
    return (_ROOT / path).read_text(encoding="utf-8")


def _js_body(const: str, path: str = _TAB_CONFIG) -> str:
    """JS 物件/陣列常數的內容（`X = { … }` 或 `X = [ … ]` 之間那段）。"""
    m = re.search(rf"{const}\s*=\s*[{{[](.*?)[}}\]];", _js(path), re.DOTALL)
    assert m, f"{path} 找不到 {const} 定義（寫法改了？）"
    return m.group(1)


def _js_keys(const: str, path: str = _TAB_CONFIG) -> set:
    """JS 物件常數的鍵集合。"""
    return set(re.findall(r"(\w+)\s*:", _js_body(const, path)))


def _tab_config_modules() -> set:
    """抽 PERMISSION_GROUPS 每個 group 的 modules 陣列內容。"""
    keys = set()
    for arr in re.findall(r"modules:\s*\[([^\]]*)\]", _js_body("PERMISSION_GROUPS")):
        keys.update(re.findall(r"'([a-z_]+)'", arr))
    return keys


def _module_labels_keys() -> set:
    return _js_keys("MODULE_LABELS", "frontend/js/admin/user-mgmt.js")


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
    all_keys = []
    for arr in re.findall(r"modules:\s*\[([^\]]*)\]", _js_body("PERMISSION_GROUPS")):
        all_keys.extend(re.findall(r"'([a-z_]+)'", arr))
    assert len(all_keys) == len(set(all_keys)), "同一模組出現在多個權限群組"


def test_tab_extra_access_matches_backend():
    """🔴 `TAB_ACCESS` 是 router 閘門本身的參數；前端拿它決定 tab 看不看得見。

    漂掉的症狀不對稱、而且兩邊都難發現：前端**少**一個模組 → 那個人打 API
    進得去、畫面上卻沒有那個 tab（2026-08-14 實例：提案庫漏了 preprod_plan）；
    前端**多**一個 → 側欄畫得出來，點進去每支請求 403。
    """
    front = {k: tuple(re.findall(r"'([^']+)'", v))
             for k, v in re.findall(r"(\w+)\s*:\s*\[([^\]]*)\]",
                                    _js_body("TAB_EXTRA_ACCESS"))}
    # 後端列完整名單（含同名模組），前端只列「額外」的 —— 比對前先對齊形狀
    back = {k: tuple(m for m in mods if m != k) for k, mods in TAB_ACCESS.items()}
    assert front == back, f"TAB_EXTRA_ACCESS 與 core/auth.TAB_ACCESS 不同步：{front} vs {back}"
