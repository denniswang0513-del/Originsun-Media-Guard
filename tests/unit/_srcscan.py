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
import pathlib
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


def call_args(src: str, callee: str) -> list:
    """`callee(...)` 每一次呼叫的引數清單（配對括號，只在頂層逗號切）。

    🔴 第三個坑（2026-08-29 加）：`.split(")")` 會被引數裡的
    `int(p.amount or 0)` 提前切斷 —— 而被切掉的正好是最後一個參數，
    「有沒有把它傳進去」這種斷言於是永遠看不到答案。
    """
    out = []
    for chunk in src.split(f"{callee}(")[1:]:
        depth, buf, args = 1, [], []
        for ch in chunk:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
                if depth == 0:
                    break
            if ch == "," and depth == 1:
                args.append("".join(buf).strip())
                buf = []
                continue
            buf.append(ch)
        args.append("".join(buf).strip())
        out.append(args)
    return out


def js_func_body(src: str, header: str) -> str:
    """JS 版 `func_body` —— 從 `header` 切到**下一個頂層宣告**之前。

    🔴 檔頭那個坑的 JS 變體：這裡本來各測試自己 `split("\nfunction ")[0]`，
    而那只擋得住下一個東西剛好是 `function` 的情形。下一個是 `const`、
    `window.`、`export`、或一段 `/** */` 的時候，切出來的「函式本體」會一路
    吃到檔尾 —— 於是 `assert X in fn` 幾乎必定成立，斷言看起來很嚴格，其實
    整個檔案都算數。

    判準：函式內的每一行都有縮排（或是收尾的 `}` / `});`），所以第一個
    「頂在第 0 欄、又不是收尾符號」的行就是下一個宣告。
    """
    i = src.index(header)
    lines = src[i + len(header):].splitlines(True)
    out = lines[:1]                      # header 之後的殘句一定屬於本體
    for ln in lines[1:]:
        if ln[:1].strip() and not ln.startswith(("}", ")", "]")):
            break
        out.append(ln)
    return header + "".join(out)


def js_code_only(src: str) -> str:
    """JS 版的 code_only —— 剝掉 /* */ 與 // 註解。

    同一個坑：`assert "financeNav" not in js` 會被「註解正好在說為什麼不用它」
    打敗。Python 版切的是 '#'，對 JS 沒用，所以這裡另備一支。
    網址裡的 `//` 會被誤切，故只在行首或前面不是 ':' 時才當註解。
    """
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return "\n".join(re.sub(r"(?<!:)//.*$", "", ln) for ln in src.splitlines())


def py_callers(*roots) -> dict:
    """`{"路徑:函式名": 它內部呼叫過的名字集合}`，掃 `roots` 底下所有 .py。

    「每個做 X 的地方都要呼叫 Y」這種不變式用它寫 —— 點名幾支函式的斷言只擋得住
    **已經想到**的那幾個（2026-08-30 實例：三條寫入路都接上了 `_sync_taxonomy`、
    測試也照著點名那三支，第四個寫入端沒人想到，它照樣只寫一半）。

    🔴 `obj.method(...)` 也算：只看 `ast.Name` 的話 `models.CrmCashEntry(...)`
    這種寫法會整個看不見 —— 而看不見是**靜默通過**，錯的那一邊。
    🔴 鍵帶著檔案路徑：同名函式散在兩個檔時，用純函式名當鍵會後蓋前，其中一個
    沒做也看不出來；順便讓失敗訊息直接指到檔案。
    """
    import ast

    out: dict = {}
    for root in roots:
        base = pathlib.Path(_REPO) / root
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(_REPO).as_posix()
            for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                out[f"{rel}:{fn.name}"] = {
                    getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                    for n in ast.walk(fn) if isinstance(n, ast.Call)}
    return out
