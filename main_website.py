"""main_website.py
---
NAS website-api container 入口（精簡版 FastAPI）。

僅載入 `routers/website/*`，連同一個 PostgreSQL（共享 JWT secret、CORS 允許
Windows `192.168.1.107:8000` 與 `originsun-studio.com`）。

啟動（容器內）：
    uvicorn main_website:app --host 0.0.0.0 --port 8001

與 Windows `main.py` 並存：兩者都連 NAS Postgres，但：
- main.py：CRM / Backup / Transcode / 官網管理 Tab 前端（不掛 website routers）
- main_website.py：ONLY website routers
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse, PlainTextResponse

from core.version import read_local_version

logger = logging.getLogger("website-api")


async def _periodic_db_check():
    """每 60s 重探 DB；offline 時「重建連線池」而非只重試,讓 init 失敗 /
    postgres 重啟 / idle timeout / ProactorEventLoop wedge 都能自我修復。
    沒這個迴圈,容器要重啟才會復活,所有 endpoint 卡 503。對齊 main.py:_periodic_db_health。

    關鍵:db_available() 用現有 engine/pool,若 pool wedge 掉會一直失敗,光重試救不回;
    所以 offline 就先 dispose 壞 engine 再 init_db() 重建。全部 wait_for 包住,
    避免恢復檢查本身卡死 event loop。
    """
    import core.state as state
    from db.session import init_db, db_available, close_db

    while True:
        try:
            await asyncio.sleep(60)
            if state.db_online:
                ok = await asyncio.wait_for(db_available(), timeout=8)
                if not ok:
                    print("[website-api] DB 中斷,下一輪重建連線池")
                    state.db_online = False
            else:
                try:
                    await asyncio.wait_for(close_db(), timeout=8)
                except Exception:
                    pass
                ok = await asyncio.wait_for(init_db(), timeout=15)
                state.db_online = bool(ok)
                if ok:
                    print("[website-api] DB 連線恢復（已重建連線池）")
        except asyncio.CancelledError:
            raise
        except Exception:
            state.db_online = False


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        from db.session import init_db, get_session_factory
        from db.migrations_website import run_website_migrations
        from db.seed_website import seed_website_if_empty
        import core.state as state

        # init_db 回傳連線是否成功；必須顯式寫回 state.db_online
        # （與 main.py startup 模式一致 — _common.require_db 靠這個 flag）
        ok = await init_db()
        state.db_online = bool(ok)

        if state.db_online:
            factory = get_session_factory()
            if factory:
                await run_website_migrations(factory)
                await seed_website_if_empty(factory)
            print("[website-api] startup OK (DB online)")
        else:
            print("[website-api] startup WITHOUT DB — 60s 後自動重試")
    except Exception as e:
        print(f"[website-api] startup failed: {e}")

    db_check_task = asyncio.create_task(_periodic_db_check())
    try:
        yield
    finally:
        db_check_task.cancel()
        try:
            await db_check_task
        except (asyncio.CancelledError, Exception):
            pass


# 對外容器更沒有理由公開 API 地圖（core/ 在容器的同步清單裡，main.py 不在）
from core.api_docs import docs_urls  # noqa: E402

app = FastAPI(
    title="Originsun Website API",
    version="1.0",
    lifespan=_lifespan,
    **docs_urls(),
)

_allowed_origins = [
    o.strip() for o in os.environ.get(
        "WEBSITE_CORS_ORIGINS",
        "http://localhost:4321,http://127.0.0.1:4321,"
        "http://localhost:8000,http://127.0.0.1:8000,"
        "http://192.168.1.107:8000,"
        # 對外網站 + admin Tab 都走 cloudflared，三個 hostname 都要白
        "https://originsun-studio.com,https://www.originsun-studio.com,"
        "https://test.originsun-studio.com,"
        "https://preview.originsun-studio.com,"
        "https://foundry.originsun-studio.com",
    ).split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# 版號在**模組載入時**取一次，不是每次 healthz 重讀 —— 這個欄位存在的目的就是
# 抓「檔案同步了但容器沒重啟」，每 request 重讀會回報新版號卻跑著舊碼，剛好把
# 比對的意義抵銷掉。改版必然重啟容器，所以凍結在載入時就是正確語意。
_VERSION = read_local_version(default="")


@app.get("/healthz")
async def healthz():
    """健康 + 自我描述。version 讓發版流程比對 master 與本容器是否同碼；
    media_log 讓後台看得見本容器實際在用的歸檔資料夾與可寫狀態
    —— 兩台機器指到不同資料夾會讓照片安靜散落，只能靠回報才發現。"""
    from routers.crm.media_log import media_log_health
    return {"ok": True, "service": "website-api", "version": _VERSION,
            "media_log": await media_log_health()}


from routers.website import router as _website_router
app.include_router(_website_router)

# 影像紀錄的免登入端點（token 授權）—— master 關機時同仁照樣開得了連結、傳得了照片。
# ⚠ 只掛 public_router（4 條），**絕不可**改成 routers.crm 的主 router：
#    那會把 160+ 個 CRM 端點（客戶/報價/成本/財務）曝在對外服務上。
#    守衛：tests/unit/test_media_log_public_router.py。
try:
    from routers.crm import CRM_PREFIX as _CRM_PREFIX
    from routers.crm import public_router as _crm_public
    app.include_router(_crm_public, prefix=_CRM_PREFIX)
except Exception as _e:  # noqa: BLE001 — 缺 DB 套件的環境照常起，只是少這條路
    logging.getLogger(__name__).warning("[website-api] media_log public router 未掛載: %s", _e)

# 對外 serve 執行期上傳檔（團隊頭像 /uploads/team、文章圖片 /uploads/posts）。
# serve 目錄 = admin_posts._UPLOAD_BASE（上傳就寫這裡），確保「存」與「serve」同一處；
# NAS nginx 的 location /uploads/ 反代到這個 app。main.py（master）另有同樣 mount。
from core.public_assets import MODULE_DIRS as _PUBLIC_MODULE_DIRS
from core.public_assets import PAGES as _PUBLIC_PAGES
from fastapi.staticfiles import StaticFiles as _StaticFiles
from routers.website.admin_posts import _UPLOAD_BASE as _UPLOAD_BASE
import mimetypes as _mt
# 容器精簡 Python 的 mimetypes 可能不認 .webp → StaticFiles 回 text/plain，配 nginx 的
# X-Content-Type-Options: nosniff 會讓瀏覽器拒絕當圖片渲染。明確註冊正確型別。
_mt.add_type("image/webp", ".webp")
_mt.add_type("image/avif", ".avif")
os.makedirs(_UPLOAD_BASE, exist_ok=True)
app.mount("/uploads", _StaticFiles(directory=_UPLOAD_BASE), name="uploads")

# ── 影像紀錄公開頁（master 關機也開得起來）──
# 頁面與其 logo 隨 publish 的 NAS_SYNC_PATHS 同步進 code/frontend/，
# 不走 Astro dist（那是 build 產物，沒有這些檔）。nginx 把 /media-log.html
# 與 /img/ 反代到本 app。
_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")


def _serve_public_page(filename: str):
    """對外公開頁的路由工廠。未同步到本機 → 503 而不是 404 ——「檔案沒推過來」
    與「網址打錯」是完全不同的故障，混在一起查起來很痛苦。"""
    path = os.path.join(_FRONTEND_DIR, filename)

    async def _page():
        if not os.path.isfile(path):
            return PlainTextResponse(f"{filename} 未同步到本機", status_code=503)
        return FileResponse(path, media_type="text/html")

    app.get("/" + filename, include_in_schema=False, name=f"page_{filename}")(_page)


# 頁面與它們 import 的模組目錄，清單正本在 core.public_assets
# （publish 的同步清單、nginx 的 location 都對齊那一份，並有測試釘住相依閉包）。
for _page_file in _PUBLIC_PAGES:
    _serve_public_page(_page_file)


# 電子發票分享的短網址（owner 2026-09-10「master 關機也拿得到」）。
# master 的 main.py 有同一條；兩邊回的是**同一個檔**，頁面自己從 location.pathname
# 取短碼再打 /api/v1/crm/public/invoice-file/{token}/meta（那支掛在 public_router 上）。
# 掛根路徑是為了短 —— 走 router 前綴會讓寄給客戶的網址又長回去。
@app.get("/e/{code}", include_in_schema=False)
async def _short_invoice_file(code: str, request: Request):
    from core.public_access import surface_gate
    await surface_gate(request)          # 公開區「發票影像分享」關閉 → 404
    path = os.path.join(_FRONTEND_DIR, "invoice-file.html")
    if not os.path.isfile(path):
        return PlainTextResponse("invoice-file.html 未同步到本機", status_code=503)
    return FileResponse(path, media_type="text/html",
                        headers={"Cache-Control": "no-store"})

# 匯款通知的短網址（owner 2026-09-11）。🔴 跟 /e/ 一樣**兩邊都要有**：
# master 的 main.py 有一條，這裡也要有，不然從對外網域打開就是 404 ——
# 而「master 關機他照樣打得開」正是做成連結的理由。
# （2026-09-11 發版當下就是漏了這一條才發現：nginx 轉得到 NAS，NAS 卻沒有這條路由。）
@app.get("/p/{code}", include_in_schema=False)
async def _short_payout_note(code: str, request: Request):
    from core.public_access import surface_gate
    await surface_gate(request)          # 公開區「匯款通知」關閉 → 404
    path = os.path.join(_FRONTEND_DIR, "payout.html")
    if not os.path.isfile(path):
        return PlainTextResponse("payout.html 未同步到本機", status_code=503)
    return FileResponse(path, media_type="text/html",
                        headers={"Cache-Control": "no-store"})


for _sub in _PUBLIC_MODULE_DIRS:
    _d = os.path.join(_FRONTEND_DIR, *_sub.split("/"))
    if os.path.isdir(_d):
        app.mount("/" + _sub, _StaticFiles(directory=_d), name=_sub.replace("/", "_"))

# 提案（10 條）與參考片（6 條）的 token 端點，全部吃 {token}。
# ⚠️ **絕不可**改成掛各模組的主 router：那會把提案庫/片庫的內部端點
# （清單/統計/成案/刪除）曝在對外服務上。
# 守衛：tests/unit/test_public_surface.py 對整個 app 列舉斷言。
for _mod_name, _prefix_attr in (("routers.api_proposals", "PROPOSALS_PREFIX"),
                                ("routers.api_references", "REFERENCES_PREFIX")):
    try:
        _mod = __import__(_mod_name, fromlist=["public_router"])
        app.include_router(_mod.public_router, prefix=getattr(_mod, _prefix_attr))
    except Exception as _e:  # noqa: BLE001 — 缺 DB 套件的環境照常起，只是少這條路
        logging.getLogger(__name__).warning(
            "[website-api] %s public router 未掛載: %s", _mod_name, _e)

if os.path.isdir(os.path.join(_FRONTEND_DIR, "img")):
    app.mount("/img", _StaticFiles(directory=os.path.join(_FRONTEND_DIR, "img")),
              name="img")
