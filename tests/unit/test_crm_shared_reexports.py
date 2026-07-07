"""CRM 領域模組的 `from ._shared import ...` 名稱必須全部存在於 _shared。

為什麼需要這個測試（2026-07-07 生產事故）：領域模組的 DB model re-import 包在
`try: ... except ImportError: pass`（機隊無 sqlalchemy 的容錯設計）。當 _shared
刪掉一個被 re-import 的名字時，**import 錯誤被靜默吞掉、路由照常註冊**，
到 runtime 用到該名字才 NameError —— route parity / ruff F821 / py_compile
全都抓不到（名字「有 import」只是 import 失敗了）。v1.10.210 就是這樣把
生產 CRM 打掛（_shared 清 F401 時誤刪 `update as sa_update`：ruff 回報原名
`update`、clients.py re-import 的是別名 `sa_update`，交集分析對不上）。

本測試把「guarded import 的靜默」還原成大聲失敗。
"""
import importlib
import re
from pathlib import Path

import pytest

_CRM_DIR = Path(__file__).resolve().parents[2] / "routers" / "crm"


def _reimported_names(src: str) -> list:
    """抽出檔案內所有 `from ._shared import ...` 的名稱（含括號多行與 as 別名）。"""
    names = []
    for m in re.finditer(r"from \._shared import (?:\(([^)]+)\)|([^\n(]+))", src):
        raw = (m.group(1) or m.group(2) or "").replace("\n", ",")
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            # `X as Y` → _shared 要有的是 X
            names.append(part.split(" as ")[0].strip())
    return names


def _domain_modules():
    return sorted(p for p in _CRM_DIR.glob("*.py")
                  if p.name not in ("_shared.py", "__init__.py"))


@pytest.mark.parametrize("path", _domain_modules(), ids=lambda p: p.name)
def test_shared_reexports_exist(path):
    shared = importlib.import_module("routers.crm._shared")
    names = _reimported_names(path.read_text(encoding="utf-8"))
    assert names, f"{path.name} 沒有任何 _shared import？（切檔慣例改了要更新本測試）"
    missing = [n for n in names if not hasattr(shared, n)]
    assert not missing, (
        f"{path.name} 從 _shared re-import 的名稱不存在：{missing} — "
        "guarded try/except 會把這種錯吞成 runtime NameError（v1.10.210 生產事故）。"
        "刪 _shared 的 import 前先跑本測試。"
    )
