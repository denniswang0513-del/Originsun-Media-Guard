"""官網 public API 合約 — 對外 Astro 站（website/src）打的每一條路徑必須存在。

這是「前端依賴鄰接表」：rename routers/website/public.py 的任何 URL 前，
先想到 website/src/lib/crm-client.ts / posts.ts / ContactForm.astro 都在打這些
路徑 — build 期 _safeGet 會把 404 吞成空陣列，對外網站變成「假性成功的空白頁」
（v1.10.132 就發生過）。清單來源：grep '/api/website' website/src（2026-07-06）。

新增 public 端點不用改這裡；改名/刪除既有端點會 fail — 那正是本測試的目的：
逼你同步改 Astro client 再更新這份清單。
"""
import importlib

import pytest

# (method, path) — Astro 前端實際依賴的路徑（router path 格式，prefix=/api/website）
FRONTEND_DEPENDENCIES = [
    ("POST", "/api/website/contact"),        # ContactForm.astro 聯絡表單
    ("GET",  "/api/website/works"),          # 作品集列表
    ("GET",  "/api/website/works/{slug}"),   # 作品詳情頁（path-based build）
    ("GET",  "/api/website/featured"),       # 首頁精選
    ("GET",  "/api/website/featured-all"),   # 首頁精選網格（不限數量）
    ("GET",  "/api/website/hero"),           # 首頁 Hero 輪播
    ("GET",  "/api/website/categories"),     # 作品分類
    ("GET",  "/api/website/services"),       # 服務項目
    ("GET",  "/api/website/team"),           # 團隊（about.astro 也直接打）
    ("GET",  "/api/website/faqs"),           # FAQ schema
    ("GET",  "/api/website/testimonials"),   # 見證
    ("GET",  "/api/website/quick_facts"),    # QuickFact
    ("GET",  "/api/website/awards"),         # 獎項（/portfolio）
    ("GET",  "/api/website/posts"),          # 影像專欄列表
    ("GET",  "/api/website/posts/{slug}"),   # 專欄文章頁
    ("GET",  "/api/website/post_categories"),# 專欄分類
    ("GET",  "/api/website/initiatives"),    # 公益/創作（/impact /lab）
    ("GET",  "/api/website/redirects"),      # 301 轉址（llms-full.txt + nginx 生成）
    ("GET",  "/api/website/meta"),           # 站級 meta（portfolio_pdf_url 等）
]


@pytest.fixture(scope="module")
def public_routes():
    m = importlib.import_module("routers.website.public")
    return {(method, r.path) for r in m.router.routes for method in r.methods}


@pytest.mark.parametrize("method,path", FRONTEND_DEPENDENCIES)
def test_frontend_dependency_exists(public_routes, method, path):
    assert (method, path) in public_routes, (
        f"{method} {path} 從 public router 消失了 — 對外 Astro 站還在打這條路徑！"
        "改名/刪除前必須同步改 website/src 並更新本測試的 FRONTEND_DEPENDENCIES。"
    )
