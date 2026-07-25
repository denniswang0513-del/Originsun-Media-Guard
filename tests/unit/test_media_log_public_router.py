"""影像紀錄 public_router 白名單守衛（R1 防線）。

NAS 對外容器只掛 routers.crm.media_log.public_router。若哪天有人把它接回
_shared.router、或把別的端點加進 public_router，對外服務就會曝露整套 CRM
（客戶/報價/成本/財務，160+ 端點）——而且不會有任何徵兆。

本測試把「對外只能出現這 4 條」寫成可執行的斷言：
  1. public_router 自身路由集合 == 預期 4 條（多一條少一條都紅）
  2. 掛進一個乾淨 app（模擬 main_website.py）後不含任何其他 CRM 路徑
  3. master 側 URL 不變（_shared.router 仍看得到同樣的 4 條）
"""
import pytest
from fastapi import FastAPI

pytest.importorskip("sqlalchemy", reason="CRM router 需要 DB 套件")

from routers.crm import CRM_PREFIX, public_router  # noqa: E402
from routers.crm import router as crm_router  # noqa: E402

# 對外容器允許出現的完整路由集合（路徑, 方法）——mount prefix 由呼叫端給。
# 新增對外端點時要同步改這裡，等於強制經過一次「這真的該對外嗎」的思考。
EXPECTED = {
    ("/api/v1/crm/public/media-log/{token}", "GET"),
    ("/api/v1/crm/public/media-log/{token}/upload", "POST"),
    ("/api/v1/crm/public/media-log/{token}/file/{file_id}", "GET"),
    ("/api/v1/crm/public/media-log/{token}/file/{file_id}", "DELETE"),
}
MOUNT_PREFIX = CRM_PREFIX   # 與 _shared / main_website 共用同一個常數


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
    assert _route_set(public_router.routes, MOUNT_PREFIX) == EXPECTED


def test_mounted_app_has_no_other_crm_endpoints():
    """模擬 NAS main_website.py 的掛法 — 整個 app 不能有第二種 CRM 路徑。"""
    app = FastAPI()
    app.include_router(public_router, prefix=MOUNT_PREFIX)
    crm_paths = {p for p, _ in _route_set(app.routes) if p.startswith("/api/v1/crm")}
    assert crm_paths == {p for p, _ in EXPECTED}
    # 明確點名幾個絕不可外洩的領域（讀起來就知道守的是什麼）
    joined = " ".join(crm_paths)
    for leaked in ("/clients", "/quotations", "/invoices", "/cash", "/staff",
                   "/payments", "/cost", "/projects/"):
        assert leaked not in joined, f"對外 app 出現非影像紀錄的 CRM 路徑：{leaked}"


def test_master_side_urls_unchanged():
    """master 掛的是收編後的 crm_router — 這 4 條 URL 必須原樣還在。"""
    assert EXPECTED <= _route_set(crm_router.routes)


def test_qr_stays_off_public_router():
    """QR 只有後台在用、且對外容器沒裝 qrcode 套件 → 不可進 public_router。"""
    paths = {p for p, _ in _route_set(public_router.routes, MOUNT_PREFIX)}
    assert not any(p.endswith("/qr") for p in paths)
    # 但它必須仍在 master 側可用
    assert any(p.endswith("/qr") for p, _ in _route_set(crm_router.routes))
