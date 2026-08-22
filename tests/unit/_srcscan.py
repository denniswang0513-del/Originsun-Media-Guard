# -*- coding: utf-8 -*-
"""掃描式測試的共用工具 —— 「這個函式裡有沒有做某件事」這類斷言的地基。

這種測試很好用（比起搭一整套 DB fixture 便宜太多），但有兩個踩過的坑，
所以工具收斂在這裡，不要在各測試檔各寫一份：

🔴 **切固定字數會隨註解漂移**：`src[i:i+1400]` 這種寫法，補幾行說明就讓測試
   莫名其妙變紅 —— 而那不是實作有問題。要切到**函式邊界**。
🔴 **比對前一定要剝註解與 docstring**：`assert "os.replace" not in body` 會被
   「本函式的註解正好在說明不可以用 os.replace」打敗（實際發生過）。
"""
from __future__ import annotations

import io
import os
import re

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def repo_src(rel_path: str) -> str:
    """讀 repo 裡的原始碼（rel_path 用 posix 斜線，如 'routers/crm/finance.py'）。"""
    return io.open(os.path.join(_REPO, rel_path), encoding="utf-8").read()


def func_body(src: str, header: str) -> str:
    """從 `def xxx(` 取到下一個頂層 def/裝飾器之前。"""
    i = src.index(header)
    rest = src[i + len(header):]
    ends = [rest.find(m) for m in ("\n@router", "\n@token_router", "\ndef ", "\nasync def ")]
    ends = [e for e in ends if e != -1]
    return src[i:i + len(header) + (min(ends) if ends else len(rest))]


def code_only(body: str) -> str:
    """剝掉 docstring 與 # 註解，只留程式碼。"""
    body = re.sub(r'"""[\s\S]*?"""', "", body)
    return "\n".join(ln.split("#")[0] for ln in body.splitlines())


def js_code_only(src: str) -> str:
    """JS 版的 code_only —— 剝掉 /* */ 與 // 註解。

    同一個坑：`assert "financeNav" not in js` 會被「註解正好在說為什麼不用它」
    打敗。Python 版切的是 '#'，對 JS 沒用，所以這裡另備一支。
    網址裡的 `//` 會被誤切，故只在行首或前面不是 ':' 時才當註解。
    """
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return "\n".join(re.sub(r"(?<!:)//.*$", "", ln) for ln in src.splitlines())
