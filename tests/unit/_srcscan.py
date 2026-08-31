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


def flow_body(src: str, header: str) -> str:
    """一條**流程**的程式碼：那支函式，加上它在同一個檔裡呼叫的自家 helper。

    🔴 2026-08-30 加。掃描斷言老是寫成 `func_body(src, "async def 某端點(")`，
    釘的其實是「這段程式住在哪一支函式裡」—— 而規則是「這條流程有沒有做這件事」。
    於是把 285 行的端點抽出兩個階段函式（純搬運、行為零改變），四支測試立刻紅了，
    紅的理由是「找不到 BankStatementLine(」，看起來像功能不見了。

    這種假紅有實質代價：它讓「把長函式切開」變成一件會弄壞測試的事，於是沒有人
    切，於是那個檔繼續長。要守的東西應該是行為，不是行號。

    只跟一層（`_` 開頭的同檔 helper）—— 夠用，而且不會把半個模組拖進來。
    """
    body = func_body(src, header)
    seen = {header}
    for name in sorted(set(re.findall(r"\b(_[A-Za-z0-9_]+)\s*\(", body))):
        for h in (f"async def {name}(", f"def {name}("):
            if h in src and h not in seen:
                seen.add(h)
                body += "\n" + func_body(src, h)
                break
    return body


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


def py_trees(*roots):
    """yield `(repo 相對路徑, ast.Module)` —— 掃描型測試的共用 walker。

    roots 裡的目錄遞迴掃、單一 .py 檔名直接收。走這一支而不是各測試檔自己
    rglob＋parse（2026-08-31 已經長出第二份，跳過 SyntaxError 的行為還跟這裡
    不一致 —— 掃描工具的老規矩：正本只有一份）。
    """
    import ast

    for root in roots:
        base = pathlib.Path(_REPO) / root
        paths = sorted(base.rglob("*.py")) if base.is_dir() else \
            ([base] if base.exists() else [])
        for path in paths:
            yield (path.relative_to(_REPO).as_posix(),
                   ast.parse(path.read_text(encoding="utf-8")))


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
    for rel, tree in py_trees(*roots):
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            out[f"{rel}:{fn.name}"] = {
                getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                for n in ast.walk(fn) if isinstance(n, ast.Call)}
    return out


def migration_sql() -> str:
    """開機時會跑的**所有** migration SQL，串成一大段文字。

    🔴 用它，不要直接讀 `main.py`：那些 SQL 2026-08-30 從 `_on_startup`（895 行、
    占 main.py 58%）搬進了 `db/migrations.py`，七支斷言「這句 SQL 在 main.py 裡」
    的測試當場全紅 —— 它們釘的是**位置**不是規則。這一支兩個檔案都收，所以
    下次再搬也不會紅。
    """
    return repo_src("db/migrations.py") + "\n" + repo_src("main.py")


def finance_src(header: str = "") -> str:
    """原本 `routers/crm/finance.py` 那一整個檔的內容。

    🔴 2026-08-30 那個檔 2,996 行（超過 AI 單次讀取上限、又是三週改動第一名），
    依真實相依方向拆成四個：`finance.py`（共用 helper ＋ 發票）、`cash.py`
    （收支明細／應付／應收／收款分配）、`payments.py`（請款單）、`taxonomy.py`
    （分類樹）。三個新模組只 import finance，沒有反向依賴。

    當時有三十幾支斷言寫著 `repo_src("routers/crm/finance.py")` —— 它們釘的是
    **函式在哪個檔案**，而規則其實是「這支函式做了什麼」。用這一支就不會被拆檔
    弄紅：它把四個檔串起來（同源，沒有同名函式），下次再拆只要改這裡一行。

    給 `header` 就回那支函式的本體（在四個檔裡找）。
    """
    return _split_file_src("routers/crm", ("finance.py", "cash.py",
                                           "payments.py", "taxonomy.py"), header)


def finance_logic_src(header: str = "") -> str:
    """原本 `core/finance_logic.py` 那一整個檔的內容。

    🔴 2026-08-30 同樣的理由拆成套件 `core/finance_logic/`（原本 2,548 行、
    改動第二頻繁的財務檔）：`_core`（月份/折舊/貸款/發票狀態/期末部位）
    ← `_statements`（損益表 + 資產負債表）← `_flows`（現金流量表 + 檢核）。
    純行段切割、零函式搬家，跨檔的邊全部同向。

    用這一支而不是 `repo_src("core/finance_logic.py")` —— 理由同 `finance_src`：
    那些斷言釘的是「這支函式做了什麼」，不是「它住在哪個檔」。
    """
    return _split_file_src("core/finance_logic",
                           ("_core.py", "_statements.py", "_flows.py"), header)


def models_src(header: str = "") -> str:
    """原本 `db/models.py` 那一整個檔的內容。

    🔴 2026-08-31 拆成套件 `db/models/`（同一個理由第三次：2,121 行超過單次
    讀取上限、fan-in 85、三週改 47 次）。純行段切割、零類別搬家，六段照原檔
    的領域分節。斷言釘的是「這張表長什麼樣」，不是「它住哪個檔」。
    """
    return _split_file_src("db/models",
                           ("_base.py", "_system.py", "_crm.py", "_workos.py",
                            "_finance.py", "_workspace.py"), header)


def _split_file_src(rel_dir: str, files: tuple, header: str = "") -> str:
    """一個被拆開的檔：把幾塊串回去，或在其中一塊裡找出某支函式的本體。

    拆檔要對掃描測試無感，靠的就是這一支 —— 下次再拆只要改呼叫端那一行的清單。
    """
    import pathlib

    base = pathlib.Path(_REPO).joinpath(*rel_dir.split("/"))
    parts = [(base / n).read_text(encoding="utf-8")
             for n in files if (base / n).exists()]
    assert parts, f"{rel_dir} 底下一個檔都讀不到（拆檔後改名了？）"
    if not header:
        return "\n".join(parts)
    for src in parts:
        if header in src:
            return func_body(src, header)
    raise AssertionError(f"{rel_dir} 的 {files} 裡找不到 {header!r}")
