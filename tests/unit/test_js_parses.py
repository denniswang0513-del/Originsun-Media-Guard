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
