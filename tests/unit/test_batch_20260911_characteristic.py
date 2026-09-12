# -*- coding: utf-8 -*-
"""2026-09-11「都修好」＋手機報價那批的特徵測試（polish 安全網）：把當時沒被測到的幾個公開行為釘住。

- core/version：磁碟版號與行程載入時取的 RUNNING_VERSION 是兩個東西（發版閘門看後者）
- STALE_PATHS：三條部署路都吃同一份清單；bootstrap 用 regex＋exec 從剛解出的 manifest 讀，要能讀得出跟 import 一樣的結果
- showcase-edit 的「重畫不清空未存欄位」：_render 前收、畫完放回；本來就要覆蓋的動作先 _forgetDirty
"""
import re

import ota_manifest
from core import version as ver
from tests.unit._srcscan import repo_src, showcase_edit_src


def test_version_json_and_running_version_are_distinct_but_start_equal():
    disk = ver.read_local_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+", disk)
    assert ver.RUNNING_VERSION == disk, "行程剛載入時兩者相等；發版改了 version.json 之後 RUNNING_VERSION 才會落後"
    assert ver.read_local_version(default="x") == disk and isinstance(ver.read_version_json(), dict)


def test_bootstrap_reads_stale_paths_the_same_way_import_does():
    """bootstrap 是 stdlib-only、不 import ota_manifest：用 regex 抓 `STALE_PATHS = [...]` 再 exec。清單改成多行／加註解都要還讀得到。"""
    src = repo_src("ota_manifest.py")
    m = re.search(r"^STALE_PATHS\s*=\s*\[(.*?)\]", src, re.S | re.M)
    assert m, "bootstrap 那條 regex 要抓得到"
    ns = {}
    exec("STALE_PATHS = [" + m.group(1) + "]", ns)
    assert ns["STALE_PATHS"] == list(ota_manifest.STALE_PATHS)
    assert "core/schemas.py" in ns["STALE_PATHS"]
    # 三條部署路都吃它
    assert "for rel in STALE_PATHS:" in repo_src("routers/api_ota.py")
    assert "STALE_PATHS" in repo_src("publish_update.py").split("def ")[0], "publish_update 頂端 import"
    assert 'r"^STALE_PATHS\\s*=\\s*\\[(.*?)\\]"' in repo_src("bootstrap.py")


def test_showcase_editor_keeps_unsaved_fields_across_rerender():
    code = showcase_edit_src()
    assert "image/*" not in code, "accept 要在 JS 裡用 'image/' + '*' 設：原始碼裡的 image 加斜線星號會讓 js_code_only 整段吃掉"
    assert "const _dirtyEls = new Set();" in code and "function _snapshotDirty()" in code and "function _restoreDirty(keep)" in code
    render = code[code.index("const _keep = _snapshotDirty();"):code.index("_restoreDirty(_keep);")]
    assert "_keep" in render and "innerHTML" in render, "_render 先收再放回"
    # 本來就要覆蓋的動作（套用專案描述／AI SEO）先忘掉那兩格，不然新值進不來
    assert "_forgetDirty('f:description', 'id:inp-seo-desc');" in code
    # 存檔成功整批清（key === false）
    assert "_dirtyKeys.clear(); _dirtyEls.clear(); _catsDraft = null;" in code
