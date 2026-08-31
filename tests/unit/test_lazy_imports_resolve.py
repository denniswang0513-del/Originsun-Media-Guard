# -*- coding: utf-8 -*-
"""每一個**函式內**的 lazy import 都要解析得到 —— 斷了的第一個症狀是生產 500。

這個 repo 的既定模式是大量函式內 import（拆循環依賴、省啟動時間），代價是
**沒有任何靜態工具守它**：py_compile 不解析名字、ruff 不追跨模組、掃字串的
測試只比字面。2026-08-31 實際咬到：finance.py 拆檔把 `_enforce_cash_project_link`
搬去 cash.py，`apply_bank_statement` 函式內還向 finance 要 —— **整條「匯入
對帳表→套用」500 了一天半**，單元測試 2,421 支全綠，發現靠的是匯入路徑的
端到端撞上去。

這支測試把整類收掉：AST 掃出所有函式層級的 `from <本地模組> import <名字>`，
逐一 import 模組、assert 名字存在。搬家漏改的那一刻就紅，不用等人按匯入。
"""
import ast
import importlib
import pathlib

import pytest

LOCAL_ROOTS = ("core", "routers", "services", "db")
SCAN_DIRS = ("core", "routers", "services", "db")
SKIP = ("__pycache__",)


def _lazy_imports():
    """[(檔案, 行號, 模組, 名字)]：所有函式內、非 try 包裹的本地 from-import。"""
    repo = pathlib.Path(__file__).resolve().parents[2]
    out = []
    for d in SCAN_DIRS:
        for p in (repo / d).rglob("*.py"):
            s = p.as_posix()
            if any(x in s for x in SKIP):
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            rel = p.relative_to(repo).as_posix()
            # try 區塊裡的 import 是刻意的可選依賴（agent 沒裝 DB 那類）—— 跳過
            trylines = set()
            for n in ast.walk(tree):
                if isinstance(n, ast.Try):
                    for b in n.body:
                        trylines.update(range(b.lineno, (b.end_lineno or b.lineno) + 1))
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(fn):
                    if (isinstance(node, ast.ImportFrom) and node.level == 0
                            and node.module
                            and node.module.split(".")[0] in LOCAL_ROOTS
                            and node.lineno not in trylines):
                        for a in node.names:
                            if a.name != "*":
                                out.append((rel, node.lineno, node.module, a.name))
    return out


def test_the_scan_actually_finds_lazy_imports():
    """🔴 掃不到要當失敗 —— 這張網存在的理由就是接住看不見的斷裂。"""
    found = _lazy_imports()
    assert len(found) >= 150, f"只掃到 {len(found)} 條 lazy import，掃描本身可能壞了"


def test_every_lazy_import_resolves():
    missing = []
    for rel, ln, mod, name in _lazy_imports():
        try:
            m = importlib.import_module(mod)
        except Exception as e:      # noqa: BLE001 — 模組本身炸也要點名
            missing.append(f"{rel}:{ln}  import {mod} 失敗：{type(e).__name__}")
            continue
        if not hasattr(m, name):
            # `from services import intel_runner` 是**子模組**匯入 —— runtime
            # 會退回 import services.intel_runner，hasattr 在那之前看不到它
            try:
                importlib.import_module(f"{mod}.{name}")
            except ImportError:
                missing.append(f"{rel}:{ln}  from {mod} import {name} —— 名字不存在")
    if missing:
        pytest.fail(
            "這些函式內 lazy import 已經斷了（執行到那一行就是生產 500）：\n  "
            + "\n  ".join(sorted(set(missing))[:20])
            + ("\n  …" if len(set(missing)) > 20 else "")
            + "\n多半是搬家/改名漏改呼叫端 —— 把 import 指向名字現在住的模組。")
