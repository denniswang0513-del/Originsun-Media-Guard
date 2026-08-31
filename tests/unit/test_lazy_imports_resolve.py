# -*- coding: utf-8 -*-
"""每一個**函式內**的 lazy import 都要解析得到 —— 斷了的第一個症狀是生產 500。

這個 repo 的既定模式是大量函式內 import（拆循環依賴、省啟動時間），代價是
**沒有任何靜態工具守它**：py_compile 不解析名字、ruff 不追跨模組、掃字串的
測試只比字面。實際咬到（2026-08-30 拆檔、08-31 發現）：finance.py 拆檔把
`_sync_taxonomy` 搬去 cash.py，五個函式內 import 還指著舊家 —— **對帳單／
卡單／規則套用三條路 500 了一天半**，單元測試全綠，發現靠匯入路徑的端到端
撞上去。

這支把整類收掉：掃出所有函式層級的 `from <本地模組> import <名字>`，逐一
import 模組、驗名字存在。搬家漏改的那一刻就紅，不用等人按匯入。

🔴 try 包裹的 import **只有 handler 明寫 ImportError/ModuleNotFoundError 才跳過**
（那是刻意的可選依賴，如 agent 沒裝 DB）。包在 `except Exception` 裡的不豁免 ——
那正是「ImportError 被吞成 log 裡一行、端點 500」的病灶，本網存在的理由。
"""
import ast
import functools
import importlib

import pytest

from tests.unit._srcscan import py_trees

# import 目標算「本地」的頂層套件；掃描範圍再加 scripts 與根目錄單檔
# （main.py 一支就有 ~39 條函式內 import；scripts/ 匯入腳本直接吃 routers 的
# helper，上一次 finance 拆檔它們就在射程外 —— /simplify 層次審查補進來的）。
LOCAL_ROOTS = ("core", "routers", "services", "db", "utils")
SCAN_ROOTS = LOCAL_ROOTS + ("scripts", "main.py", "core_engine.py",
                            "tts_engine.py", "transcriber.py", "notifier.py")


def _optional_import_ids(tree) -> set:
    """handler 明寫 ImportError/ModuleNotFoundError 的 try 裡的 ImportFrom（id 集合）。"""
    out = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Try):
            continue
        catches_ie = any(
            isinstance(h.type, ast.Name) and h.type.id in ("ImportError",
                                                           "ModuleNotFoundError")
            or isinstance(h.type, ast.Tuple) and any(
                isinstance(x, ast.Name) and x.id in ("ImportError",
                                                     "ModuleNotFoundError")
                for x in h.type.elts)
            for h in n.handlers if h.type is not None)
        if catches_ie:
            for b in n.body:
                out.update(id(i) for i in ast.walk(b)
                           if isinstance(i, ast.ImportFrom))
    return out


@functools.lru_cache(maxsize=None)
def _lazy_imports() -> tuple:
    """((檔案, 行號, 模組, 名字), …)：所有函式內、非可選依賴的本地 from-import。

    lru_cache：兩支測試共用同一次掃描（整棵樹 parse 一次 ~1.35s，量過）。
    """
    out = set()
    for rel, tree in py_trees(*SCAN_ROOTS):
        optional = _optional_import_ids(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.ImportFrom) and node.level == 0
                        and node.module
                        and node.module.split(".")[0] in LOCAL_ROOTS
                        and id(node) not in optional):
                    for a in node.names:
                        out.add((rel, node.lineno, node.module, a.name))
    return tuple(sorted(out))


def test_the_scan_actually_finds_lazy_imports():
    """🔴 掃不到要當失敗 —— 這張網存在的理由就是接住看不見的斷裂。"""
    found = _lazy_imports()
    assert len(found) >= 150, f"只掃到 {len(found)} 條 lazy import，掃描本身可能壞了"
    assert any(rel == "main.py" for rel, *_ in found), \
        "main.py 沒被掃到（它一支就有幾十條，而 conftest 的 import main 不執行函式體）"


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
        uniq = sorted(set(missing))
        pytest.fail(
            "這些函式內 lazy import 已經斷了（執行到那一行就是生產 500）：\n  "
            + "\n  ".join(uniq[:20]) + ("\n  …" if len(uniq) > 20 else "")
            + "\n多半是搬家/改名漏改呼叫端 —— 把 import 指向名字現在住的模組。")


def test_no_router_is_silently_dropped_at_startup():
    """🔴 main.py 對每個 router 的 import 包著 try/except：模組層斷裂只印一行
    [WARN] 然後**整個 router 的 URL 全部 404**，而 conftest 的 `import main`
    照樣成功、發版 gate（只跑 tests/unit）照樣全綠。這裡逐一真的 import ——
    router 在模組層炸掉的那一刻就紅，不用等有人打它的端點。"""
    import main
    broken = []
    for name in main._ROUTER_MODULES:
        try:
            importlib.import_module(f"routers.{name}")
        except Exception as e:      # noqa: BLE001 — 點名，別讓第一個炸掉遮住其他
            broken.append(f"routers.{name}: {type(e).__name__}: {e}")
    assert not broken, (
        "這些 router 在模組層就 import 失敗 —— 生產上它們會被靜默跳過、"
        "整組 URL 404：\n  " + "\n  ".join(broken))