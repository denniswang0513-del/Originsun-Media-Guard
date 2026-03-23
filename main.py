import os
# 確保 VBS/BAT 啟動時 CUDA 環境正確（TEMP relaunch 可能丟失 GPU 存取）
os.environ.setdefault('CUDA_DEVICE_ORDER', 'PCI_BUS_ID')
os.environ.setdefault('CUDA_VISIBLE_DEVICES', '0')

import asyncio
import socketio  # type: ignore
import uvicorn  # type: ignore
import threading
import webbrowser
from fastapi import FastAPI  # type: ignore
from fastapi.staticfiles import StaticFiles  # type: ignore
from fastapi.responses import FileResponse  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore
from starlette.middleware.base import BaseHTTPMiddleware # type: ignore

from core.socket_mgr import sio  # type: ignore
import core.state as state  # type: ignore

# ── Router 容錯載入（缺模組時跳過該 router，不 crash）──
_ROUTER_MODULES = [
    'api_backup', 'api_verify', 'api_proxy', 'api_concat',
    'api_report', 'api_transcribe', 'api_system', 'api_tts',
    'api_job_history', 'api_queue', 'api_schedules', 'api_agents',
]
_routers = {}
for _mod_name in _ROUTER_MODULES:
    try:
        _mod = __import__(f'routers.{_mod_name}', fromlist=['router'])
        _routers[_mod_name] = _mod
    except Exception as _e:
        print(f'[WARN] Router {_mod_name} 載入失敗，已跳過: {_e}')

app = FastAPI(title="Originsun Media Guard Web API")

class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Chrome Private Network Access (CORS-RFC1918) — required for
        # cross-origin fetches between LAN machines (e.g. agent health polling).
        # Must handle BEFORE CORSMiddleware sees the request, because Starlette's
        # CORSMiddleware rejects unknown preflight headers with 400.
        if (request.method == "OPTIONS"
                and request.headers.get("access-control-request-private-network")):
            from starlette.responses import Response as _Resp
            origin = request.headers.get("origin", "*")
            return _Resp(status_code=204, headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": request.headers.get(
                    "access-control-request-headers", "*"),
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Private-Network": "true",
                "Access-Control-Max-Age": "600",
            })

        response = await call_next(request)
        if request.method == "GET":
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# NoCacheMiddleware must be added AFTER CORSMiddleware so it wraps the
# outside — this lets it inject Access-Control-Allow-Private-Network on
# CORS preflight responses that CORSMiddleware already handled.
app.add_middleware(NoCacheMiddleware)

io_app = socketio.ASGIApp(sio, app)

for _mod_name, _mod in _routers.items():
    if hasattr(_mod, 'router'):
        app.include_router(_mod.router)

@app.on_event("startup")
async def _on_startup():
    state.set_main_loop(asyncio.get_running_loop())
    state.init_concurrency()
    # ── PostgreSQL 連線 ──
    try:
        from db.session import init_db
        ok = await init_db()
        state.db_online = ok
        print(f"[DB] PostgreSQL {'連線成功' if ok else '不可用，使用 JSON fallback'}")
    except Exception as e:
        state.db_online = False
        print(f"[DB] 初始化失敗: {e}")
    asyncio.create_task(_periodic_version_check())
    asyncio.create_task(_periodic_db_health())
    from core.scheduler import run_scheduler  # type: ignore
    asyncio.create_task(run_scheduler())


async def _periodic_db_health():
    """每 60 秒檢查 DB 連線，斷線時自動重連。"""
    try:
        from db.session import db_available, init_db
    except ImportError:
        return  # 代理端沒有 db 模組，直接退出
    while True:
        await asyncio.sleep(60)
        try:
            was_online = state.db_online
            state.db_online = await db_available()
            if not was_online and state.db_online:
                print("[DB] PostgreSQL 連線恢復")
            elif was_online and not state.db_online:
                print("[DB] PostgreSQL 連線中斷，切換至 JSON fallback")
                state.db_online = await init_db()
        except Exception:
            state.db_online = False


def _read_local_version() -> str:
    """讀取本機 version.json 中的版號。"""
    import json as _json
    v_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
    try:
        with open(v_file, "r", encoding="utf-8") as f:
            return _json.load(f).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


def _is_newer(remote: str, local: str) -> bool:
    """比較版號，remote > local 回傳 True。自動移除 v 前綴。"""
    def _strip(v):
        return v.lstrip("v") if v else ""
    r, l = _strip(remote), _strip(local)
    if not r or r == "unknown" or not l:
        return False
    if r == l:
        return False
    try:
        rp = list(map(int, r.split(".")))
        lp = list(map(int, l.split(".")))
        for i in range(max(len(rp), len(lp))):
            rv = rp[i] if i < len(rp) else 0
            lv = lp[i] if i < len(lp) else 0
            if rv > lv:
                return True
            if rv < lv:
                return False
    except (ValueError, IndexError):
        pass
    return False


async def _periodic_version_check():
    """每 10 分鐘檢查主控端版號，有更新時透過 Socket.IO 推播給前端。"""
    import json as _json
    import urllib.request
    await asyncio.sleep(15)  # 讓服務先穩定
    while True:
        try:
            from config import load_settings
            master = load_settings().get("master_server", "")
            if master:
                local_ver = _read_local_version()
                url = f"{master.rstrip('/')}/api/v1/version"
                def _fetch():
                    req = urllib.request.Request(url, headers={"User-Agent": "OriginsunAgent/1.0"})
                    with urllib.request.urlopen(req, timeout=5) as r:
                        return _json.loads(r.read().decode())
                remote = await asyncio.to_thread(_fetch)
                remote_ver = remote.get("version", "")
                if _is_newer(remote_ver, local_ver):
                    await sio.emit("update_available", {
                        "latest_version": remote_ver,
                        "current_version": local_ver
                    })
        except Exception:
            pass
        await asyncio.sleep(600)  # 10 分鐘

@app.get("/download_agent")
async def download_agent():
    file_path = "Originsun_Agent.zip"
    if os.path.exists(file_path):
        return FileResponse(file_path, filename="Originsun_Agent.zip")
    return {"error": "系統尚未打包 Originsun_Agent.zip，請聯絡管理員。"}

@app.get("/download_installer")
async def download_installer():
    file_path = "Install_Originsun_Agent.bat"
    if os.path.exists(file_path):
        return FileResponse(file_path, filename="Install_Originsun_Agent.bat")
    return {"error": "找不到自動安裝腳本。"}

@app.get("/")
async def serve_index():
    """Serve index.html with aggressive no-cache headers."""
    index_path = os.path.join("frontend", "index.html")
    if os.path.exists(index_path):
        resp = FileResponse(index_path, media_type="text/html")
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp
    return FileResponse("frontend/index.html")

if os.path.exists("frontend"):
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
else:
    os.makedirs("frontend", exist_ok=True)
    with open("frontend/index.html", "w", encoding="utf-8") as f:
        f.write("<h1>Originsun Media Guard Web (Frontend Pending)</h1>")
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

if __name__ == "__main__":
    port = 8000
    print(f"[Server] 啟動 FastAPI 服務於 port {port}")
    # threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    uvicorn.run(io_app, host="0.0.0.0", port=port, log_level="error")
