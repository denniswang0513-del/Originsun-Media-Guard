# -*- coding: utf-8 -*-
"""宣告式裡呼叫自己 ＝ 暫時死區，那顆按鈕從此是死的（2026-08-21 實戰）。

```js
import { today } from './crm-utils.js';
...
const today = today();     // ReferenceError: Cannot access 'today' before initialization
```

`const today` 讓 `today` 這個名字在**整個函式範圍**進入暫時死區，右邊那個 `today()`
呼叫的不是 import 進來的那支，是還沒初始化的自己。

為什麼值得一條專門的釘：
  ① 它**無聲**。應付帳款那兩處都寫在 `try` 之外（`confirm()` 之後、`try` 之前），
     catch 接不到、UI 不出任何錯誤訊息 —— 使用者只看到「按了沒反應」。
  ② 它是機械式改寫的產物。`new Date().toISOString().substring(0,10)` 整批換成既有的
     `today()` 時，變數名剛好就叫 today，兩處一起中。這種批次取代還會再發生。
  ③ eslint 抓得到它的規則是 no-use-before-define，但那條在本專案有 74 處既有噪音
     （模組層 let 被前面定義的函式用到，執行期沒問題），開了 gate 第一天就紅 ——
     違反 eslint.config.mjs 開宗明義的原則。所以在這裡用精準掃描補這一刀。

真實災情：v2.4.112~122 的應付帳款分頁，單筆「應付款」與「全部付款」兩顆按鈕全死，
一筆都標不了已付款。
"""
import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

# const/let X = ...X(...) —— 初始化式裡呼叫同名的自己。
# 🔴 開頭那個 negative lookbehind 不可省：少了它，`const now = Date.now()`、
# `const text = await f.text()`、`const max = Math.max(...)` 這類**方法**呼叫
# 會被誤判（實測誤報 7 支檔案）。
SELF_CALL = re.compile(
    r"^[ \t]*(?:const|let)[ \t]+([A-Za-z_$][\w$]*)[ \t]*=[^;\n]*?"
    r"(?<![.\w$])\1[ \t]*\(", re.M)


def _js_files():
    return sorted(p for p in FRONTEND.rglob("*.js") if "node_modules" not in str(p))


def test_there_are_js_files_to_scan():
    """掃描目標存在 —— 路徑寫錯的話下面那條會空掃通過。"""
    assert len(_js_files()) > 50


def test_the_pattern_actually_catches_the_real_bug():
    """釘子本身要能咬 —— 用當初真的出事的那一行驗，順便釘住不可誤判方法呼叫。"""
    bad = "    const today = today();\n"
    assert SELF_CALL.search(bad), "抓不到真正出事的寫法"
    for ok in ("    const now = Date.now();\n",
               "    const text = await f.text();\n",
               "    const max = Math.max(a, b);\n",
               "    const day = today();\n"):
        assert not SELF_CALL.search(ok), f"誤判：{ok.strip()}"


@pytest.mark.parametrize("path", _js_files(), ids=lambda p: p.name)
def test_no_declaration_calls_itself(path):
    src = path.read_text(encoding="utf-8")
    hits = [(src[:m.start()].count("\n") + 1, m.group(1))
            for m in SELF_CALL.finditer(src)]
    assert not hits, (
        f"{path.name} 有宣告式呼叫自己（暫時死區，執行到就 ReferenceError）："
        + "；".join(f"第 {ln} 行 `{name}`" for ln, name in hits))
