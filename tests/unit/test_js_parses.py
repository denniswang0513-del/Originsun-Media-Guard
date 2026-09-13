# -*- coding: utf-8 -*-
"""前端 .js 真的 parse 得過。

🔴 為什麼需要這一支（2026-09-02 實際踩到）：清理審查把 `cash-split-editor.js`
的 `render()` 拆成三支時，`.csp-tax` 那塊被留在 `bindRows()` 外面、多出一個
`}` —— 整支 module 從此 import 失敗，收支明細的「拆內容」「改拆項」、對帳單
預覽的拆項入口、`splitBadgeHtml` / `splitGross` 全部不會載入。

而當時的驗證方式 **`node --check <file>` 對它回 exit 0**（那條路把 .js 當
CommonJS script 檢查，遇到 ESM 語法的行為不是我們要的），55 支掃原始碼的測試
也全綠 —— `_srcscan` 是字串切割，對「結構壞掉」零防護。兩層驗證都說沒事，
而那個檔在瀏覽器上一顆按鈕都按不動。

正確的檢查是 `node --input-type=module --check < file`。
"""
import os
import shutil
import subprocess

import pytest

from tests.unit._srcscan import _REPO

# 第三方、產出物、上傳內容 —— 不是我們寫的，壞了也不是這支要管的
_SKIP_DIRS = {".venv", "node_modules", "python_embed", "__pycache__", "dist",
              ".git", "uploads", "windows_helper", "backups"}


def _js_files():
    """repo 裡我們自己寫的 .js（第三方與產出物不掃）。

    root 取整個 repo 而不是只有 `frontend/` —— `_SKIP_DIRS` 才真的派得上用場，
    而且 `website/` 那邊哪天長出自己的 .js 也會被守到。
    """
    import pathlib
    root = pathlib.Path(_REPO)
    return sorted(p for p in root.rglob("*.js")
                  if not _SKIP_DIRS & set(p.parts))


@pytest.mark.skipif(not shutil.which("node"), reason="這台沒有 node")
def test_every_frontend_module_parses():
    """每一支 frontend/**/*.js 都要 parse 得過（當 ES module 檢查）。"""
    broken = []
    for f in _js_files():
        r = subprocess.run(["node", "--input-type=module", "--check"],
                           stdin=f.open("rb"), capture_output=True)
        if r.returncode != 0:
            first = (r.stderr.decode("utf-8", "replace").strip()
                     .splitlines() or [""])
            broken.append(f"{f.relative_to(_REPO)}: "
                          + " / ".join(x.strip() for x in first[:4]))
    assert not broken, "這些檔 parse 不過：\n" + "\n".join(broken)


# ── ES module 裡裸用沒宣告的名字 ─────────────────────────────────────
# eslint.config.mjs 刻意關 no-undef（vanilla JS 跨檔 window._* 慣例開了全是噪音），
# 上面那支只驗 parse —— 於是 module 檔漏一個 import 是零告警，只有使用者按下去才
# ReferenceError（2026-09-11 crm-staff.js 的 copyText；2026-09-13 remote-dispatch.js
# 死碼清理漏一行 `ms`）。這裡只對「真的是 ES module」的檔跑 no-undef，把整個 repo
# `window.X =` 過的名字與頁面級函式庫當全域，噪音就沒了。

_PAGE_LIBS = ("$", "jQuery", "google", "gapi", "io", "Chart", "Sortable", "marked",
              "DOMPurify", "html2canvas", "jspdf", "QRCode")


def _es_module_files():
    import re
    pat = re.compile(r"^\s*(import\s.+\sfrom\s|export\s)", re.M)
    return [f for f in _js_files() if pat.search(f.read_text("utf-8", "replace"))]


def _window_globals():
    """全 repo 裡 `window.X =`／`globalThis.X =` 過的名字 —— 執行期都是全域。"""
    import re
    names = set()
    pat = re.compile(r"\b(?:window|globalThis)\.([A-Za-z_$][\w$]*)\s*=[^=]")
    for f in _js_files():
        names |= set(pat.findall(f.read_text("utf-8", "replace")))
    return sorted(names)


@pytest.mark.skipif(not shutil.which("node"), reason="這台沒有 node")
@pytest.mark.skipif(not os.path.exists(os.path.join(_REPO, "node_modules", "eslint")),
                    reason="沒裝 eslint（npm install）")
def test_no_undefined_names_in_es_modules(tmp_path):
    """每一支 ES module 裡用到的名字都要有來源：宣告、import、或 window.* 過。"""
    import json
    import pathlib
    repo = pathlib.Path(_REPO)
    globals_js = (repo / "node_modules" / "globals" / "index.js").as_uri()
    extra = {n: "readonly" for n in list(_PAGE_LIBS) + _window_globals()}
    cfg = tmp_path / "eslint.noundef.mjs"
    cfg.write_text(
        f'import globals from "{globals_js}";\n'
        "export default [{ files: ['**/*.js'],\n"
        "  languageOptions: { ecmaVersion: 2022, sourceType: 'module',\n"
        f"    globals: {{ ...globals.browser, ...globals.es2021, ...{json.dumps(extra)} }} }},\n"
        "  rules: { 'no-undef': 'error' } }];\n", "utf-8")
    files = _es_module_files()
    assert files, "掃不到任何 ES module？_js_files 或 pattern 壞了"
    r = subprocess.run(
        ["node", str(repo / "node_modules" / "eslint" / "bin" / "eslint.js"),
         "--no-config-lookup", "-c", str(cfg), "--format", "json",
         *[str(f) for f in files]],
        capture_output=True, cwd=str(_REPO))
    try:
        report = json.loads(r.stdout.decode("utf-8", "replace"))
    except ValueError:
        pytest.fail("eslint 沒有回 JSON：" + r.stderr.decode("utf-8", "replace")[:500])
    bad = [f"{pathlib_rel(item['filePath'])}:{m['line']} {m['message']}"
           for item in report for m in item["messages"]]
    assert not bad, "ES module 裡裸用了沒來源的名字：\n" + "\n".join(bad)


def pathlib_rel(p):
    import pathlib
    try:
        return str(pathlib.Path(p).relative_to(_REPO))
    except ValueError:
        return p
