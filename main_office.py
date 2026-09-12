"""main_office.py
---
NAS `office-api` 容器入口 —— 內部同仁那幾個面的 24/7 版本。

owner 2026-09-10：「我希望報價單、發票、員工的工作頁面 不會被 8000 的生產機開關影響」。

那三個面的資料本來就都在 NAS 的 Postgres，只有「把頁面端出來、把 API 跑起來」綁在
master Windows 上。這支就是把**純資料庫的那半邊**在 NAS 上再跑一份：

    掛：認證（登入／我是誰）、員工工作台、工時／假勤／週記／里程碑、
        整包 CRM（客戶／專案／報價／人力／成本／發票／收支／請款）、手機 BFF、貼圖
    不掛：備份／轉檔／串接／報表／語音／空拍／機隊／OTA／排程／Socket.IO
          —— 那些吃本機硬體與記憶卡，本來就該綁 master

跟另外兩支進入點的分工（三支都連同一顆 Postgres、共用同一把 jwt_secret）：

    main.py           master Windows 8000：完整 SPA ＋ 上面那些吃硬體的 ＋ AI 報價助理 ＋ 產 PDF
    main_website.py   NAS 8001：對外官網 ＋ 匿名公開頁（含客戶的報價單連結）
    main_office.py    NAS 8002：本檔

啟動（容器內）：
    uvicorn main_office:app --host 0.0.0.0 --port 8002

🔴 **這支不准長出排程或背景 runner**。全機隊共用同一顆生產 DB，只該跑一次的東西
（夜間批次、告警探測、對外寄信）在這裡再跑一份就是重複觸發。`core.topology.
is_master_machine()` 擋得住那些自己判斷的，但最可靠的是**不要把它們 import 進來** ——
`tests/unit/test_office_surface.py` 對這個 app 的模組圖直接斷言。
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse, PlainTextResponse

from core.version import read_local_version

logger = logging.getLogger("office-api")

# 要掛的 router 模組。順序照 main.py 的 _ROUTER_MODULES（先註冊先贏，CRM 內部有順序相依）。
_ROUTER_MODULES = (
    "api_auth",
    "api_me", "api_timesheets", "api_hr", "api_journal", "api_milestones",
    "api_crm",          # 薄殼，re-export routers/crm 的 composition root
    "api_crm_mobile",
    # 士源帳本（私帳手機版，owner 2026-09-12「主機關機手機也要能用」）：執行專案／資產／
    # 儀表板那幾支 + 手機 BFF。這四支都沒有排程（test_office_surface 的模組圖釘著）。
    "api_finance", "api_finance_projects", "api_finance_assets", "api_ledger_mobile",
    "api_paste",
)

# 掛上去之後要拿掉的路徑（前綴比對）。
#
# 🔴 帳號管理留在 master：使用者資料是**雙寫**的（Postgres ＋ users.json），而 NAS 這台
#    沒有 users.json 正本。在這裡改權限只會寫進 DB，master 的 JSON 鏡射就跟著漂 ——
#    平常沒事（JSON 只是 DB 掛掉時的退路），但那正是最需要它準的時候。
# 🔴 身份範本存在 settings.json，這台也沒有那個檔（見 docs/OFFLINE_MASTER_PLAN.md §7.1）。
#
# 🔴 `/auth/denials`（最近授權不足）是**行程內**的 300 筆環形緩衝。掛在這台只會給出
#    一份「只有 office-api 這個行程看過的 403」，跟 master 那份是兩份不同的清單而畫面上
#    看不出差別 —— 誤導比沒有更糟。而且看它的 UI 在桌機 SPA（那整套留在 master），
#    拿掉在這裡沒有任何畫面會壞。
#
# ⚠ 這是**路徑前綴**比對，認不出 method。要拿掉「同一條路徑上的某個 POST」得另外做
#   （`_mount` 那邊多帶一份 (path, method) 清單）。目前沒有這種需求，但別以為列一條路徑
#   就等於擋掉了那支寫入 —— 真要擋寫入請確認 GET 也一起沒了是可以接受的。
_DROP_PREFIXES = (
    "/api/v1/auth/users",
    "/api/v1/auth/rbac/templates",
    "/api/v1/auth/denials",
)

# 🔴 **刻意接受的漂移**：`/auth/register`、`/auth/reset`、`/auth/google/login` 有掛，
#    而它們會寫使用者（`_persist_user`）。這台的 JSON 鏡射是關的，所以走這條路建的帳號
#    ／改的密碼**不會進 master 的 users.json**。
#    後果只有一種，而且發生在已經很糟的情境裡：master 的 DB 也掛掉、退回 JSON 登入時，
#    那些人會用不了（或還在用舊密碼）。
#    為什麼還是掛：不掛的話「master 關機時新同事不能註冊、忘記密碼的人不能重設」——
#    那正是這整件事要解決的問題本身。正本（Postgres）永遠是對的，退路可能舊一點。
#    要收掉這個漂移的作法是讓 master 開機時從 DB 重建一次 users.json（還沒做）。


async def _periodic_db_check():
    """每 60s 重探 DB；offline 時**重建連線池**而非只重試。

    照抄 main_website._periodic_db_check 的理由：沒這個迴圈，init 失敗／postgres 重啟／
    idle timeout 都要重啟容器才會復活，期間所有端點卡 503。db_available() 用現有 pool，
    pool wedge 掉光重試救不回來 —— 所以 offline 就先 dispose 再 init_db()。
    """
    import core.state as state
    from db.session import init_db, db_available, close_db

    while True:
        try:
            await asyncio.sleep(60)
            if state.db_online:
                ok = await asyncio.wait_for(db_available(), timeout=8)
                if not ok:
                    print("[office-api] DB 中斷，下一輪重建連線池")
                    state.db_online = False
            else:
                try:
                    await asyncio.wait_for(close_db(), timeout=8)
                except Exception:
                    pass
                ok = await asyncio.wait_for(init_db(), timeout=15)
                state.db_online = bool(ok)
                if ok:
                    print("[office-api] DB 連線恢復（已重建連線池）")
        except asyncio.CancelledError:
            raise
        except Exception:
            state.db_online = False


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        from db.session import init_db
        import core.state as state

        # init_db 回傳連線是否成功；必須顯式寫回 state.db_online（守衛靠這個 flag）
        ok = await init_db()
        state.db_online = bool(ok)
        # 🔴 **不跑 migration**：加欄位／建表是 master startup 的事（main.py 的 _crm_cols）。
        #    兩台同時對同一顆 DB 跑 DDL 只會互相卡，而且這台的碼可能比 master 舊一版。
        print(f"[office-api] startup {'OK (DB online)' if ok else 'WITHOUT DB — 60s 後自動重試'}")
    except Exception as e:
        print(f"[office-api] startup failed: {e}")

    task = asyncio.create_task(_periodic_db_check())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


from core.api_docs import docs_urls  # noqa: E402

app = FastAPI(title="Originsun Office API", version="1.0", lifespan=_lifespan, **docs_urls())

# 這個面是登入後用的，同源打自己。CORS 白名單只留「同一批頁可能被哪個網域載入」，
# 預設是 office 的對外網址；要換網域改 env，不要在程式裡加萬用字元。
_allowed_origins = [
    o.strip() for o in os.environ.get(
        "OFFICE_CORS_ORIGINS",
        "https://office.originsun-studio.com,"
        "http://192.168.1.132:8091,"
        "http://localhost:8002,http://127.0.0.1:8002",
    ).split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)

# 版號在**模組載入時**取一次，不是每次 healthz 重讀 —— 這個欄位的用途就是抓
# 「檔案同步了但容器沒重啟」，每 request 重讀會回報新版號卻跑著舊碼。
_VERSION = read_local_version(default="")


@app.get("/healthz")
async def healthz():
    import core.state as state
    return {"ok": True, "service": "office-api", "version": _VERSION,
            "db": bool(getattr(state, "db_online", False))}


def _mount(router, drop_prefixes=()) -> int:
    """把 router 掛上 app，路徑落在 drop_prefixes 的不掛。回實際掛了幾條。

    FastAPI 的 include_router 沒有「排除某幾條」的參數，所以自己組一個只含要留的
    APIRouter。**不是**掛完再從 app.router.routes 刪 —— 那要靠刪除順序，而且刪錯了
    沒有任何徵兆；這樣寫的話「哪些沒掛」是一份看得見的清單。
    """
    kept = APIRouter()
    for r in router.routes:
        if isinstance(r, APIRoute) and any(r.path.startswith(p) for p in drop_prefixes):
            continue
        kept.routes.append(r)
    app.include_router(kept)
    return len(kept.routes)


for _name in _ROUTER_MODULES:
    try:
        _mod = __import__(f"routers.{_name}", fromlist=["router"])
        _mount(_mod.router, _DROP_PREFIXES)
    except Exception as _e:      # noqa: BLE001 — 少一個 router 也要起得來，才看得到 log
        logger.warning("[office-api] router %s 未掛載: %s", _name, _e)

# ── 前端：只有那幾個獨立入口頁，不是整個 frontend/（清單正本 core/office_assets）──
from core.office_assets import MODULE_DIRS as _MODULE_DIRS  # noqa: E402
from core.office_assets import MODULE_FILES as _MODULE_FILES  # noqa: E402
from core.office_assets import PAGES as _PAGES  # noqa: E402

_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")


def _serve_page(filename: str):
    """頁面路由工廠。檔案沒同步過來 → **503 不是 404**：「檔案沒推過來」與「網址打錯」
    是完全不同的故障，混成同一個回應查起來很痛苦（同 main_website）。"""
    path = os.path.join(_FRONTEND_DIR, *filename.split("/"))

    async def _page():
        if not os.path.isfile(path):
            return PlainTextResponse(f"{filename} 未同步到本機", status_code=503)
        # 殼層一換版就要拿到新的，不留快取（同 main.py 的 index.html）
        return FileResponse(path, media_type="text/html",
                            headers={"Cache-Control": "no-store"})

    app.get("/" + filename, include_in_schema=False, name=f"page_{filename}")(_page)


for _p in _PAGES:
    _serve_page(_p)

for _sub in _MODULE_DIRS:
    _d = os.path.join(_FRONTEND_DIR, *_sub.split("/"))
    if os.path.isdir(_d):
        app.mount("/" + _sub, StaticFiles(directory=_d), name=_sub.replace("/", "_"))
    else:
        logger.warning("[office-api] 前端目錄未同步: %s", _sub)


def _serve_module_file(rel: str):
    """逐檔 serve 的共用庫（住在頁籤資料夾裡，不能把整個目錄開出去 —— 見 office_assets）。
    掛成路由而不是 StaticFiles：那兩支的鄰居是內部 SPA 的全部原始碼。"""
    path = os.path.join(_FRONTEND_DIR, *rel.split("/"))

    async def _file():
        if not os.path.isfile(path):
            return PlainTextResponse(f"{rel} 未同步到本機", status_code=503)
        return FileResponse(path, media_type="text/javascript")

    app.get("/" + rel, include_in_schema=False, name="mod_" + rel.replace("/", "_"))(_file)


for _f in _MODULE_FILES:
    _serve_module_file(_f)
