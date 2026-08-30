# -*- coding: utf-8 -*-
"""HTML 逃脫全前端只有一份（`js/shared/dom.js`）。

🔴 2026-08-30 收斂前有**八份**，而且行為各不相同 —— 名字一樣會讓人（和 AI）
假設一樣，但實際上：

    dom.js / fin-utils          & < > " '   ?? ''   ← 正確的那份
    crm-utils                   & < > " '   || ''   ← esc(0) 回空字串
    nas-browser                 & < > "     || ''   ← 不逃脫單引號
    petty.js                    & <   "     || ''   ← 連 > 都不逃脫
    tts.js                      & < > "            ← 不逃脫單引號，且非字串會炸
    references / reference-page  DOM textContent    ← **完全不逃脫引號**

最後兩個最危險：結果會被塞進 `attr="..."`，少一個引號逃脫就是屬性逃逸
（那兩個檔案還各自另外寫了一支 `attr()` 來補這個洞）。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
CANON = "frontend/js/shared/dom.js"
# 「這是一支 HTML 逃脫函式」的特徵：把 & 換成 &amp;
_ESC_BODY = re.compile(r"replace\(\s*/&/g\s*,\s*['\"]&amp;['\"]")
# textContent → innerHTML 那條路（不逃脫引號，塞進屬性會逃逸）
_DOM_ESC = re.compile(r"textContent\s*=[^\n]*;\s*return\s+\w+\.innerHTML")


def _js_files():
    for p in sorted((ROOT / "frontend").rglob("*.js")):
        rel = p.relative_to(ROOT).as_posix()
        if "/node_modules/" in rel:
            continue
        yield rel, p.read_text(encoding="utf-8")


def test_only_dom_js_implements_html_escaping():
    offenders = [rel for rel, src in _js_files()
                 if rel != CANON and _ESC_BODY.search(src)]
    assert not offenders, (
        f"這些檔案自己寫了一份 HTML 逃脫，改成 import {CANON} 的 esc：{offenders}")


def test_nobody_escapes_through_textcontent():
    offenders = [rel for rel, src in _js_files() if _DOM_ESC.search(src)]
    assert not offenders, (
        "`textContent → innerHTML` **不逃脫引號**，塞進 attr=\"…\" 會逃逸；"
        f"改用 dom.js 的 esc：{offenders}")


def test_the_canonical_one_is_still_there_and_complete():
    """正向對照：偵測式在正本身上抓得到，而且它五個字元都逃。"""
    src = (ROOT / CANON).read_text(encoding="utf-8")
    assert _ESC_BODY.search(src) or "'&': '&amp;'" in src, "偵測式失效或正本被改掉了"
    for ch in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert ch in src, f"正本沒逃 {ch}"
    # 🔴 `?? ''` 不是 `|| ''`：對記帳系統來說「0 被渲染成空白」是危險的預設
    assert "?? ''" in src and "|| ''" not in src


def test_re_exporting_esc_also_binds_it_locally():
    """🔴 `export { esc } from '…'` 是**純轉出，不建立區域繫結**。

    收斂那天就這樣炸過：`crm-utils.js` 改成純轉出之後，它自己內部用到 `esc` 的
    地方（`kebabMenuHtml`）當場 ReferenceError，而 eslint、單元測試、後端全綠 ——
    只有真的開瀏覽器點到人力資源那一頁才看得到。

    規則：檔案自己也會用到 `esc` 的話，要 `import` 再 `export`。
    """
    for rel in ("frontend/tabs/crm/crm-utils.js", "frontend/tabs/finance/fin-utils.js"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        if not re.search(r"(?<![\w.$])esc\s*\(", src):
            continue                      # 自己沒用到就沒這個問題
        assert "import { esc }" in src, (
            f"{rel} 自己會用到 esc，卻只寫了 export-from（純轉出）—— "
            "要 `import { esc } from …` 再 `export { esc }`")
