# -*- coding: utf-8 -*-
"""OTA 的依賴掃描只認**真的 import**（2026-09-02 發版被自己擋下來才發現）。

舊版是 regex 掃原始文字：

    re.findall(r"^\s*(?:from|import)\s+([\w.]+)", content)

於是 `core/schemas.py` 描述 `mode` 那四個值的 docstring ——

      overwrite  私帳的工項換成母公司成本行算出來的
      keep       只建立連結，私帳的金額一毛不動
      import     反過來：把私帳的工項寫成母公司的 CRM 成本行

—— 被讀成 `import 反過來`（Python 的 `\w` 是 Unicode-aware，中文照樣命中），
自動寫進 `requirements_agent.txt`，preflight 再 `import 反過來` 炸掉、整個發版回滾。

🔴 真正危險的不是這種明顯的假名字，是**剛好撞到 PyPI 上真有的套件名** ——
那會被安靜地裝進 9 台 agent，而沒有任何人看得出來。
"""
from ota_manifest import _imports_in, scan_imports
from tests.unit._srcscan import _REPO


def test_a_docstring_is_not_an_import():
    """註解與 docstring 裡的 `import` 一律不算 —— 這是那次事故的原文。"""
    src = '''
class ProjectMirrorPayload:
    """
      overwrite  私帳的工項換成母公司成本行算出來的
      keep       只建立連結，私帳的金額一毛不動
      import     反過來：把私帳的工項寫成母公司的 CRM 成本行（掛給我），
    """
    mode: str = "overwrite"

# import 也不是 import
'''
    assert _imports_in(src) == set()


def test_real_imports_still_get_picked_up():
    """真的 import 一個都不能漏 —— 漏了就是 agent 少裝套件、啟動炸掉。"""
    src = ("import os\n"
           "import a.b.c\n"
           "from sqlalchemy import select\n"
           "from . import sibling\n"          # 相對 import ＝ 本地，不是套件
           "from .pkg import thing\n"
           "def f():\n"
           "    import lazy_one\n")           # 函式內的延遲 import 也要算
    assert _imports_in(src) == {"os", "a", "sqlalchemy", "lazy_one"}


def test_a_half_written_file_still_falls_back():
    """parse 不過（有人存到一半）就退回 regex —— 但只收 ASCII 開頭的識別字，
    模組名不會是中文。寧可退化成舊行為，也不要整個掃描空手而回。"""
    broken = "import requests\nclass X(  # 少一個括號\n"
    assert "requests" in _imports_in(broken)
    assert _imports_in("  import   反過來：說明\nclass X(") == set()


def test_the_repo_scan_yields_no_non_ascii_package_names():
    """整包掃一遍：不可以再冒出非 ASCII 的「套件」。"""
    bad = sorted(n for n in scan_imports(_REPO) if not n.isascii())
    assert not bad, f"依賴掃描又把中文當成套件了：{bad}"
