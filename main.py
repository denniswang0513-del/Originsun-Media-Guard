import os
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

from routers import (  # type: ignore
    api_backup, api_verify, api_proxy, api_concat,
    api_report, api_transcribe, api_system
)
try:
    from routers import api_tts  # type: ignore
except ImportError:
    api_tts = None
    print("[WARNING] TTS module not available (missing torch/torchaudio/soundfile). TTS endpoints disabled.")

app = FastAPI(title="Originsun Media Guard Web API")

class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.method == "GET":
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

app.add_middleware(NoCacheMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

io_app = socketio.ASGIApp(sio, app)

app.include_router(api_backup.router)
app.include_router(api_verify.router)
app.include_router(api_proxy.router)
app.include_router(api_concat.router)
app.include_router(api_report.router)
app.include_router(api_transcribe.router)
app.include_router(api_system.router)
if api_tts:
    app.include_router(api_tts.router)

@app.on_event("startup")
async def _on_startup():
    state.set_main_loop(asyncio.get_running_loop())

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
