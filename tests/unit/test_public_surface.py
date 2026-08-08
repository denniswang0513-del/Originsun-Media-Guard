"""NAS 對外容器的曝露面守衛（app 層，不是 per-module）。

對外容器（website-api）掛的東西有兩類，兩類都必須是**列舉出來的**：

  A. API：各模組自己的 `public_router`（CRM 的影像紀錄、提案的 token 頁）。
     低阻力的寫法是 `include_router(某模組.router)` —— 那一行會把整套內部
     端點（客戶/報價/成本/財務、提案清單/統計/成案）曝到對外服務上，而且
     沒有任何徵兆。
  B. 前端：公開頁與它們 import 的模組目錄（core.public_assets）。
     這裡的失敗模式更陰險 —— master 上一切正常，只有客戶那條路壞掉。

per-module 的守衛（test_media_log_public_router）擋得住「有人往那個
public_router 加端點」，但擋不住「有人在 main_website 多掛了一個 router」——
那是 app 層的事實，所以在這裡對**整個 app** 斷言一次。
"""
import os

import pytest

pytest.importorskip("sqlalchemy", reason="對外 app 需要 DB 套件")

from core.public_assets import (MODULE_DIRS, PAGES, SYNC_PATHS,  # noqa: E402
                                import_closure, is_served)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND = os.path.join(REPO, "frontend")
NGINX_CONF = os.path.join(REPO, "docker", "nginx", "originsun.conf")

# 對外容器允許出現的完整 API 路由集合。新增對外端點要同步改這裡，
# 等於強制經過一次「這真的該被**匿名**打到嗎」的思考。
EXPECTED_API = {
    # 影像紀錄（token 授權；同仁現場收照）
    ("/api/v1/crm/public/media-log/{token}", "GET"),
    ("/api/v1/crm/public/media-log/{token}/upload", "POST"),
    # 分塊上傳（>100MB 的檔會被 Cloudflare 擋在單一請求的 body 上限外）
    ("/api/v1/crm/public/media-log/{token}/upload/begin", "POST"),
    ("/api/v1/crm/public/media-log/{token}/upload/{upload_id}/chunk", "PUT"),
    ("/api/v1/crm/public/media-log/{token}/upload/{upload_id}/finish", "POST"),
    ("/api/v1/crm/public/media-log/{token}/file/{file_id}", "GET"),
    ("/api/v1/crm/public/media-log/{token}/file/{file_id}", "DELETE"),
    # 提案公開共編頁（token 授權；客戶手上的 ?t= 連結）
    ("/api/v1/proposals/shared/{token}", "GET"),
    ("/api/v1/proposals/shared/{token}/deck", "GET"),
    ("/api/v1/proposals/shared/{token}/info", "PATCH"),
    ("/api/v1/proposals/shared/{token}/survey", "PATCH"),
    ("/api/v1/proposals/shared/{token}/cell", "PATCH"),
    ("/api/v1/proposals/shared/{token}/meta", "GET"),
    ("/api/v1/proposals/shared/{token}/refs", "POST"),
    ("/api/v1/proposals/shared/{token}/refs/{rid}", "PATCH"),
    ("/api/v1/proposals/shared/{token}/refs/{rid}", "DELETE"),
    ("/api/v1/proposals/shared/{token}/folder", "GET"),
    ("/api/v1/proposals/shared/{token}/folder/file", "GET"),
    # 參考片研究頁（token 授權；提案公開頁的「研究頁 ↗」連過去）
    ("/api/v1/references/shared/{token}/{rid}", "GET"),
    ("/api/v1/references/shared/{token}/{rid}", "PATCH"),
    ("/api/v1/references/shared/{token}/{rid}/research/rows", "POST"),
    ("/api/v1/references/shared/{token}/{rid}/shots", "POST"),
    ("/api/v1/references/shared/{token}/{rid}/shots/{sid}", "PATCH"),
    ("/api/v1/references/shared/{token}/{rid}/shots/{sid}", "DELETE"),
}

# 對外容器可能出現的 API 命名空間（前綴之外的都不是我們在守的東西）
_WATCHED = ("/api/v1/crm", "/api/v1/proposals", "/api/v1/references")


def _app_api_routes():
    """對外 app 上所有受監視命名空間的路由。"""
    import main_website
    out = set()
    for r in main_website.app.routes:
        path = getattr(r, "path", "") or ""
        if not path.startswith(_WATCHED):
            continue
        for m in (getattr(r, "methods", None) or set()):
            if m not in ("HEAD", "OPTIONS"):
                out.add((path, m))
    return out


# ── A. API 曝露面 ─────────────────────────────────────────

def test_public_app_exposes_exactly_the_whitelist():
    assert _app_api_routes() == EXPECTED_API


def test_public_api_is_all_token_scoped():
    """對外端點一律吃 {token} —— 不帶 token 就是匿名可打，不該在這裡。"""
    for path, _m in EXPECTED_API:
        assert "{token}" in path, f"對外端點不帶 token：{path}"


def test_internal_domains_never_reachable():
    """點名幾個絕不可外洩的領域（讀起來就知道守的是什麼）。"""
    joined = " ".join(p for p, _ in _app_api_routes())
    for leaked in ("/clients", "/quotations", "/invoices", "/cash", "/staff",
                   "/payments", "/cost", "/convert", "/stats", "/plan/share",
                   "/archive", "/suggest"):
        assert leaked not in joined, f"對外 app 出現內部路徑：{leaked}"


def test_share_folder_is_read_only():
    """客戶只能看和下載 —— 分享夾出現寫入端點（上傳/刪除/改名）立刻紅。"""
    for path, method in EXPECTED_API:
        if "/folder" in path:
            assert method == "GET", f"分享資料夾出現寫入端點：{method} {path}"


# ── B. 前端資產 ───────────────────────────────────────────

def test_page_import_closure_stays_inside_served_dirs():
    """🔴 這條擋的是最陰險的那種改動：往公開頁的模組加一個 `../crm/…` import。
    master 上一切正常（整個 frontend/ 都在），NAS 上客戶那頁直接載入失敗。"""
    from core.public_assets import SOFT_DEPS
    for page in PAGES:
        for importer, dep in sorted(import_closure(FRONTEND, page)):
            if (importer, dep) in SOFT_DEPS:
                continue          # 動態 import + .catch，載不到只是少一個 toast
            assert is_served(dep), (
                f"{page} 的 {importer} 相依 {dep}，但對外容器只 serve "
                f"{MODULE_DIRS} —— 客戶那頁會在 NAS 上靜默載入失敗")


def test_every_page_and_module_dir_is_synced():
    """serve 得到但沒同步過去 = 永遠 503。"""
    for page in PAGES:
        assert f"frontend/{page}" in SYNC_PATHS
    for d in MODULE_DIRS:
        assert f"frontend/{d}" in SYNC_PATHS


def test_nginx_has_a_location_for_everything_served():
    """nginx 沒開路徑的話，前面兩條都對也還是到不了容器。"""
    conf = open(NGINX_CONF, encoding="utf-8").read()
    for page in PAGES:
        assert f"location = /{page}" in conf, f"nginx 少了 /{page}"
    for d in MODULE_DIRS:
        assert f"location ^~ /{d}/" in conf, f"nginx 少了 /{d}/"


def test_synced_pages_actually_exist():
    for page in PAGES:
        assert os.path.isfile(os.path.join(FRONTEND, page)), page
    for d in MODULE_DIRS:
        assert os.path.isdir(os.path.join(FRONTEND, *d.split("/"))), d
