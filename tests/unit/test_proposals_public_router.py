"""提案 public_router 白名單守衛（比照 test_media_log_public_router）。

NAS 24/7 容器只掛 routers.api_proposals.public_router，好讓客戶的提案共編頁
在 master 關機時照樣打得開。若哪天有人把整個 api_proposals.router 掛上去、
或把非 token 端點加進 public_router，對外服務就會曝露提案庫的內部端點
（清單/統計/成案/刪除，20+ 條）—— 而且不會有任何徵兆。

授權只有 token 一層，所以「哪些端點可以被匿名打到」寫成可執行的斷言：
  1. public_router 自身路由集合 == 預期 10 條（多一條少一條都紅）
  2. 掛進一個乾淨 app（模擬 main_website.py）後不含任何其他提案路徑
  3. master 側 URL 不變（收編後主 router 仍看得到同樣 10 條）
"""
import pytest
from fastapi import FastAPI

pytest.importorskip("sqlalchemy", reason="提案 router 需要 DB 套件")

from routers.api_proposals import PROPOSALS_PREFIX, public_router  # noqa: E402
from routers.api_proposals import router as proposals_router  # noqa: E402

# 對外容器允許出現的完整路由集合（路徑, 方法）。
# 新增對外端點時要同步改這裡，等於強制經過一次「這真的該對外嗎」的思考。
EXPECTED = {
    ("/api/v1/proposals/shared/{token}", "GET"),
    ("/api/v1/proposals/shared/{token}/deck", "GET"),
    ("/api/v1/proposals/shared/{token}/info", "PATCH"),
    ("/api/v1/proposals/shared/{token}/cell", "PATCH"),
    ("/api/v1/proposals/shared/{token}/meta", "GET"),
    ("/api/v1/proposals/shared/{token}/refs", "POST"),
    ("/api/v1/proposals/shared/{token}/refs/{rid}", "PATCH"),
    ("/api/v1/proposals/shared/{token}/refs/{rid}", "DELETE"),
    ("/api/v1/proposals/shared/{token}/folder", "GET"),
    ("/api/v1/proposals/shared/{token}/folder/file", "GET"),
}


def _route_set(routes, prefix=""):
    out = set()
    for r in routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None) or set()
        if not path:
            continue
        for m in methods:
            if m in ("HEAD", "OPTIONS"):
                continue
            out.add((prefix + path, m))
    return out


def test_public_router_exposes_exactly_the_whitelist():
    assert _route_set(public_router.routes, PROPOSALS_PREFIX) == EXPECTED


def test_mounted_app_has_no_other_proposal_endpoints():
    """模擬 NAS main_website.py 的掛法 — 整個 app 不能有第二種提案路徑。"""
    app = FastAPI()
    app.include_router(public_router, prefix=PROPOSALS_PREFIX)
    paths = {p for p, _ in _route_set(app.routes) if p.startswith("/api/v1/proposals")}
    assert paths == {p for p, _ in EXPECTED}
    # 明確點名幾條絕不可匿名打到的（讀起來就知道守的是什麼）
    joined = " ".join(paths)
    for leaked in ("/convert", "/stats", "/references", "/plan/share", "/deck/download"):
        assert leaked not in joined, f"對外 app 出現非 token 授權的提案路徑：{leaked}"


def test_public_paths_are_all_token_scoped():
    """對外端點一律吃 {token} —— 沒有 token 的路徑等於匿名可打，不該在這裡。"""
    for path, _m in _route_set(public_router.routes, PROPOSALS_PREFIX):
        assert "{token}" in path, f"public_router 出現不帶 token 的端點：{path}"


def test_master_side_urls_unchanged():
    """master 掛的是收編後的主 router — 這 10 條 URL 必須原樣還在。"""
    assert EXPECTED <= _route_set(proposals_router.routes)


def test_folder_endpoints_are_read_only():
    """客戶只能看和下載。分享夾出現寫入端點（上傳/刪除/改名）→ 立刻紅。"""
    for path, method in _route_set(public_router.routes, PROPOSALS_PREFIX):
        if "/folder" in path:
            assert method == "GET", f"分享資料夾出現寫入端點：{method} {path}"
