# -*- coding: utf-8 -*-
"""知識庫的破快取（owner 2026-09-18：手機上「顏色對了但版面被切、分頁列還是白的」）。

那不是配色寫錯，是**新 HTML ＋ 舊 CSS**：`docker/nginx/originsun.conf` 對 js/css 送 `no-cache`，
但 Cloudflare 那層把它改寫成 `max-age=14400`（那個檔第 36 行就寫著這件事）。所以發版後最久四小時，
使用者可能處於混合狀態 —— 而版面規則全在 `knowledge.css` 裡，看起來就像 RWD 壞掉。

解法是讓網址自己帶版號：版本一變就是新網址，快取自然失效。
  CSS：`<link href="...knowledge.css?v=X">`
  JS ：import map 把整張模組圖重寫成帶 `?v=X` 的（`import './ctx.js'` 先解析成
       `/js/knowledge/ctx.js`，再過 import map —— 所以不只進入點，每一支都換掉）。
       🔴 **漏一支那支就是舊的**，所以這支測試從進入點走一次模組圖逐一比對。
"""
import json
import os
import re

import pytest

from tests.unit._srcscan import repo_src

PAGE = "frontend/knowledge.html"
FRONT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend")
_IMPORT_RE = re.compile(r"""(?:from|import)\s*\(?\s*['"]([^'"]+)['"]""")


def _entries() -> list:
    """起點＝knowledge.html **內嵌**的 module script 裡 import 的每一支。

    不要只從 `/js/knowledge/index.js` 走 —— 那段內嵌的 script 自己也 import
    `/js/shared/google-signin.js`，2026-09-18 實測時它就是唯一一支沒帶版號的。
    """
    body = "\n".join(re.findall(r'<script type="module">(.*?)</script>', repo_src(PAGE), re.S))
    return sorted({s for s in _IMPORT_RE.findall(body) if s.startswith("/") and s.endswith(".js")})


def _closure(seeds=None) -> set:
    """從起點走一次模組圖 —— 瀏覽器實際會載到的每一支。"""
    seen, todo = set(), list(seeds if seeds is not None else _entries())
    while todo:
        url = todo.pop()
        if url in seen:
            continue
        path = os.path.join(FRONT, url.lstrip("/"))
        if not os.path.isfile(path):
            continue
        seen.add(url)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        base = os.path.dirname(url)
        for spec in _IMPORT_RE.findall(src):
            if spec.startswith(("http", "data:")) or "${" in spec:
                continue
            nxt = spec if spec.startswith("/") else os.path.normpath(os.path.join(base, spec)).replace("\\", "/")
            if not nxt.startswith("/"):
                nxt = "/" + nxt
            if nxt.endswith(".js"):
                todo.append(nxt)
    return seen


def _import_map() -> dict:
    src = repo_src(PAGE)
    m = re.search(r'<script type="importmap">\s*(\{.*?\})\s*</script>', src, re.S)
    assert m, "knowledge.html 沒有 import map"
    return json.loads(m.group(1))["imports"]


def test_the_entry_points_come_from_the_page_itself():
    """起點不是寫死的 —— 從頁面內嵌的 script 讀，加一支新的 import 就會被涵蓋。"""
    seeds = _entries()
    assert "/js/knowledge/index.js" in seeds
    assert "/js/shared/google-signin.js" in seeds, "內嵌 script 的 import 也要算進去"


def test_the_module_graph_is_fully_listed():
    """漏一支，那一支就吃舊的快取 —— 混一支舊的 js 進來跟混一份舊 css 一樣糟。"""
    missing = _closure() - set(_import_map())
    assert not missing, "import map 漏了這幾支：" + str(sorted(missing))


def test_nothing_extra_is_listed():
    """列了不存在的檔＝那一行永遠沒作用，而且會讓人以為蓋到了。"""
    for key in _import_map():
        assert os.path.isfile(os.path.join(FRONT, key.lstrip("/"))), key + " 這個檔不存在"


def test_every_entry_only_adds_a_version():
    """import map 是重寫網址，不是搬檔案 —— 值必須是同一個路徑加上 `?v=`。"""
    for key, val in _import_map().items():
        assert re.fullmatch(re.escape(key) + r"\?v=[\d.]+", val), f"{key} → {val}"


def test_the_whole_page_is_on_one_version():
    """css 與十三支 js 要同一個版號；混版就等於白做。"""
    vers = set(re.findall(r"\?v=([\d.]+)", repo_src(PAGE)))
    assert len(vers) == 1, "同一頁出現多個版號：" + str(sorted(vers))


def test_the_stylesheet_is_versioned_too():
    assert re.search(r'href="/js/knowledge/knowledge\.css\?v=[\d.]+"', repo_src(PAGE))


def test_the_import_map_comes_before_any_module_script():
    """import map 必須早於第一個 module script，晚了就完全沒作用（規格如此）。"""
    src = repo_src(PAGE)
    assert src.index('<script type="importmap">') < src.index('<script type="module">')


def test_publish_stamps_the_version():
    """版號不能靠人手改 —— 忘了就等於沒做。掛在跟 CLAUDE.md／ROADMAP.md 同一支。"""
    pub = repo_src("publish_update.py")
    assert '"frontend/knowledge.html": [' in pub
    assert r'\?v=[\d.]+' in pub, "要能一次換掉整頁的 ?v="
    assert "count=1" not in pub.split('"frontend/knowledge.html"')[0].split("def sync_docs_version")[-1], (
        "一行有多個 ?v=（import map），不能只換第一個")


@pytest.mark.parametrize("name", ["md-lite.js", "utils.js"])
def test_shared_modules_are_covered_too(name):
    """知識庫 import 的 shared 檔也在模組圖裡 —— 它們也會被 CF 快取四小時。"""
    assert f"/js/shared/{name}" in _import_map()
