# -*- coding: utf-8 -*-
"""OTA 依賴掃描的「模組名 ≠ 套件名」別名表（ota_manifest.IMPORT_TO_PIP）。

publish_update.py 掃原始碼的 import，把沒見過的名字**直接**寫進 requirements_agent.txt，那份會裝到整個機隊。
遇到「模組名跟 PyPI 發行名不同」的套件，寫進去的就是一個錯的、甚至是別人佔走的套件名：
機器上 pip 裝失敗 → update_agent 整包回滾 → 那台永遠停在舊版。

2026-09-18 實際發生：知識庫 `import fitz`（PyPI 的 `fitz` 是另一個壞掉的套件，正確發行名是 `pymupdf`）
→ 2.5.49 推機隊，ai_2／agent_3／agent_5 三台 pip 失敗、全部回滾到 2.5.48。
同族的 docx／pptx 之前已經咬過，所以這裡把整族釘起來。
"""
import re

from ota_manifest import IMPORT_TO_PIP
from tests.unit._srcscan import repo_src

#: 模組名 → 正確的 PyPI 發行名。PyPI 上同名的那個是廢棄或佔位套件，裝下去會失敗或裝到錯的東西。
TRAPS = {"fitz": "pymupdf", "docx": "python-docx", "pptx": "python-pptx",
         "pil": "Pillow", "cv2": "opencv-python", "sklearn": "scikit-learn", "jwt": "PyJWT"}


def test_import_name_traps_are_mapped():
    for mod, pkg in TRAPS.items():
        assert IMPORT_TO_PIP.get(mod) == pkg, f"`import {mod}` 的發行名是 {pkg}，別名表要有，否則機隊 pip 會裝到錯的套件"


def test_requirements_agent_never_lists_a_trap_module_name():
    """requirements_agent.txt 裡不准出現任何陷阱的**模組名**（只能是正確發行名）。"""
    lines = [re.split(r"[>=<\[;\s]", ln.strip())[0].strip().lower()
             for ln in repo_src("requirements_agent.txt").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    for mod, pkg in TRAPS.items():
        assert mod not in lines or mod == pkg.lower(), (
            f"requirements_agent.txt 有 `{mod}` —— 那是模組名不是發行名，機隊 pip 會失敗並整包回滾；應該是 `{pkg}`")
    assert "pymupdf" in lines, "知識庫用得到 pymupdf（主控才真的 import，但清單要有正確名字）"
