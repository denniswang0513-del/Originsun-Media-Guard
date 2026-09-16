import gzip
import os
# 確保 VBS/BAT 啟動時 CUDA 環境正確（TEMP relaunch 可能丟失 GPU 存取）
os.environ.setdefault('CUDA_DEVICE_ORDER', 'PCI_BUS_ID')
os.environ.setdefault('CUDA_VISIBLE_DEVICES', '0')

import asyncio
import sys as _sys
if _sys.platform == "win32":
    # The server runs on SelectorEventLoop (asyncpg subprocess work goes through
    # core.subproc; see core/loopsetup.py). The mechanism that actually takes
    # effect is the `--loop core.loopsetup:selector_loop_factory` flag passed on
    # every launch (core/process_spawn.py, main __main__, self-heal relaunch) —
    # uvicorn resolves that import-string itself. This policy line is just a cheap
    # fallback for any loop created WITHOUT that flag (e.g. dev launch / a stray
    # asyncio.run). NOTE: setting the policy alone does NOT redirect uvicorn (it
    # hard-codes ProactorEventLoop on Windows), which is why the flag is required.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
import socketio  # type: ignore
import uvicorn  # type: ignore
from fastapi import FastAPI, Request  # type: ignore
from core.no_store import NO_STORE_B, no_store_file
from fastapi.staticfiles import StaticFiles  # type: ignore
from fastapi.responses import RedirectResponse  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore
# BaseHTTPMiddleware removed — it buffers streaming responses (breaks SSE)

from core.socket_mgr import sio  # type: ignore
import core.state as state  # type: ignore

# ── Router 容錯載入（缺模組時跳過該 router，不 crash）──
_ROUTER_MODULES = [
    'api_auth',
    'api_backup', 'api_verify', 'api_proxy', 'api_concat',
    'api_report', 'api_transcribe', 'api_system', 'api_ota', 'api_utils', 'api_tts',
    'api_job_history', 'api_queue', 'api_schedules', 'api_agents', 'api_bookmarks',
    'api_api_keys', 'api_timesheets', 'api_cashflow', 'api_finance', 'api_finance_stmt', 'api_finance_card', 'api_finance_assets', 'api_balance_register', 'api_monthly_report', 'api_finance_projects', 'api_ledger_mobile', 'api_fortress', 'api_locations', 'api_proposals',
    'api_references',
    'api_intel',
    'api_portal',
    'api_equipment', 'api_shoots',
    'api_footage',
    'api_analytics',
    'api_crm',
    'api_crm_mobile',
    'api_drone_meta',
    'api_drone_watcher',
    'api_bulletin',
    'api_me',
    'api_hr',
    'api_milestones',
    'api_calendar',
    'api_journal',
    'api_paste',
]
_routers = {}
for _mod_name in _ROUTER_MODULES:
    try:
        _mod = __import__(f'routers.{_mod_name}', fromlist=['router'])
        _routers[_mod_name] = _mod
    except Exception as _e:
        print(f'[WARN] Router {_mod_name} 載入失敗，已跳過: {_e}')

from core.api_docs import docs_urls  # noqa: E402

app = FastAPI(title="Originsun Media Guard Web API", **docs_urls())

class NoCacheMiddleware:
    """Pure ASGI middleware — does NOT buffer streaming responses (unlike BaseHTTPMiddleware).
    This is critical for SSE endpoints like /drone_meta/scan_stream."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Chrome Private Network Access (CORS-RFC1918) preflight handling
        headers_raw = dict(scope.get("headers", []))
        method = scope.get("method", "")
        if (method == "OPTIONS"
                and b"access-control-request-private-network" in headers_raw):
            origin = headers_raw.get(b"origin", b"*").decode()
            req_headers = headers_raw.get(b"access-control-request-headers", b"*").decode()
            resp_headers = [
                (b"access-control-allow-origin", origin.encode()),
                (b"access-control-allow-methods", b"GET, POST, PUT, DELETE, OPTIONS"),
                (b"access-control-allow-headers", req_headers.encode()),
                (b"access-control-allow-credentials", b"true"),
                (b"access-control-allow-private-network", b"true"),
                (b"access-control-max-age", b"600"),
            ]
            await send({"type": "http.response.start", "status": 204, "headers": resp_headers})
            await send({"type": "http.response.body", "body": b""})
            return

        # Wrap send to inject headers on response start (no buffering)
        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # 三層：handler 自己宣告了 Cache-Control（index.html 的 no-store、影像紀錄縮圖的 private
                # max-age、財務文件／OTA 包的 core.no_store）就以它為準；否則有驗證器（ETag／Last-Modified）
                # 的靜態檔 no-cache——瀏覽器每次帶 ETag 問一句、主機回 304 不重傳本體，遠端（Cloudflare）
                # 開頁少下載 ~1.5 MB、發版換檔 ETag 就變（owner 2026-09-03「存取都有點慢」）；
                # 沒驗證器的 JSON／串流 no-cache 只會退化成每次全抓 → no-store。middleware 不認得任何路徑。
                if method == "GET" and not any(k.lower() == b"cache-control" for k, _ in headers):
                    if any(k.lower() in (b"etag", b"last-modified") for k, _ in headers):
                        headers.append((b"cache-control", b"no-cache"))
                    else:
                        headers.append((b"cache-control", NO_STORE_B))
                        headers.append((b"pragma", b"no-cache"))
                        headers.append((b"expires", b"0"))
                headers.append((b"access-control-allow-private-network", b"true"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


class GzipJsonMiddleware:
    """只壓 `application/json` 的純 ASGI 壓縮層。

    owner 平常從外面（cloudflared）開帳：收支明細一次回 4,733 列＝3.5 MB，
    在隧道上就是主要成本；JSON 每列重複同一批欄位名，壓縮率極高。

    🔴 **不用 starlette 的 GZipMiddleware**：它不分內容型別，會連
    OTA 的 ~1GB ZIP（純燒 CPU）、看片門戶帶 Range 的影片（壓了 Range 就壞）、
    SSE `text/event-stream`（會被緩衝，即時進度就不即時了）一起處理。
    這裡只認 JSON，其餘原封不動穿過去。

    🔴 只有 JSON 會被扣住等壓縮，所以不影響串流；真有超大 JSON 串流時
    （_CAP）會放棄壓縮、把已收的原樣送出，不會把記憶體吃爆。
    """

    _MIN = 1024                  # 太小的不值得壓（標頭就佔掉了）
    _THREAD_AT = 512 * 1024      # 超過這個就別在事件迴圈上壓
    _CAP = 32 * 1024 * 1024      # 保險：超過就放棄壓縮，原樣送

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        accept = next((v for k, v in scope.get("headers", [])
                       if k == b"accept-encoding"), b"")
        if b"gzip" not in accept.lower():
            await self.app(scope, receive, send)
            return

        held = {"start": None, "chunks": [], "size": 0}

        async def flush_plain():
            """放棄壓縮：把扣住的 start 與已收的 body 原樣送出。"""
            await send(held["start"])
            held["start"] = None
            for c in held["chunks"]:
                await send({"type": "http.response.body", "body": c, "more_body": True})
            held["chunks"] = []

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                hs = {k.lower(): v for k, v in message.get("headers", [])}
                if (hs.get(b"content-type", b"").startswith(b"application/json")
                        and b"content-encoding" not in hs):
                    held["start"] = message      # 先扣著，壓完才連標頭一起送
                    return
                await send(message)
                return
            if message["type"] == "http.response.body" and held["start"] is not None:
                held["chunks"].append(message.get("body", b""))
                held["size"] += len(message.get("body", b""))
                if held["size"] > self._CAP:     # 大到不像話 → 放棄壓縮
                    await flush_plain()
                    await send({"type": "http.response.body", "body": b"",
                                "more_body": bool(message.get("more_body"))})
                    return
                if message.get("more_body"):
                    return
                raw = b"".join(held["chunks"])
                held["chunks"] = []
                if len(raw) < self._MIN:
                    await send(held["start"])
                    await send({"type": "http.response.body", "body": raw})
                    return
                # 🔴 大的丟去執行緒：3.5MB 在 level 6 要幾十毫秒，那段時間整個
                # 事件迴圈停住（這台同時在推 Socket.IO 進度、又有健康輪詢餓死
                # 的前科）。zlib 對大 buffer 會放掉 GIL，所以真的平行得起來；
                # 小的留在原地（to_thread 本身的開銷比壓縮還貴）。
                packed = (gzip.compress(raw, 6) if len(raw) < self._THREAD_AT
                          else await asyncio.to_thread(gzip.compress, raw, 6))
                keep, vary = [], b""
                for k, v in held["start"]["headers"]:
                    lk = k.lower()
                    if lk == b"vary":
                        vary = v
                    elif lk not in (b"content-length", b"content-encoding"):
                        keep.append((k, v))
                keep += [(b"content-encoding", b"gzip"),
                         (b"content-length", str(len(packed)).encode()),
                         (b"vary", (vary + b", " if vary else b"") + b"Accept-Encoding")]
                await send({**held["start"], "headers": keep})
                await send({"type": "http.response.body", "body": packed})
                return
            await send(message)

        await self.app(scope, receive, send_wrapper)

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
# 最外層：壓縮要看得到最終標頭。只壓 JSON —— 下載／影片／SSE 原樣穿過。
app.add_middleware(GzipJsonMiddleware)

io_app = socketio.ASGIApp(sio, app)

for _mod_name, _mod in _routers.items():
    if hasattr(_mod, 'router'):
        app.include_router(_mod.router)

# [永久架構，非暫時] master 同時服務 /api/website/*，讓官網管理 Tab 透過
# Cloudflare Tunnel (foundry.originsun-studio.com) 存取時走同源 fetch。
# ⚠ 2026-07-08 同源化後這已是**正式設計**，不是待移除的過渡：www 的 CF bot 對抗層
#   會間歇擋帶 Authorization 的跨域 admin fetch，且 admin UI 由 master serve、
#   master 關機時頁面本來就開不起來 →「跨域不依賴 master」的假設不成立。
#   拿掉這段 = 官網後台管理整個掛掉。website routers 在 master 與 NAS 對外容器
#   雙掛（同一份碼，見 CLAUDE.md「官網後台同源化」）。
try:
    from routers.website import router as _website_router
    app.include_router(_website_router)

    @app.get("/healthz")
    async def _website_healthz():
        return {"ok": True, "service": "main.py [website admin same-origin]"}

    print("[website] routers/website mounted on main.py (same-origin admin)")
except Exception as _e:
    print(f"[WARN] website router load failed: {_e}")

def _self_heal_scheduled_task():
    """Fix Agents stuck in Session 0 due to the old installer's `/rl highest`.

    Why: b1b931c registered the scheduled task with `/rl highest`, which
    forces Windows to launch the Agent in Session 0 (Services). Native
    pickers (tkinter/WinForms) rendered there are invisible to the user.
    We detect this on startup, re-register the task without elevation, then
    spawn a detached helper that restarts us via `schtasks /run` — the new
    process lands in the user's interactive Session 1 where pickers work.

    Idempotency lock: writes a marker file in TEMP after triggering once.
    If we re-enter SelfHeal within 5 minutes we skip — without this guard,
    Session 0 → kill self → schtasks /run → new process also Session 0 →
    kill self ... infinite loop that strands master/agents in
    "Waiting for application startup" forever (caused the 2026-05-02
    /publish OTA-bricks-all-agents incident).
    """
    try:
        import ctypes, sys, subprocess, tempfile, time
        from ctypes import wintypes

        if sys.platform != "win32":
            return
        if os.environ.get("ORIGINSUN_DISABLE_SELFHEAL") == "1":
            return  # Test fixtures set this to prevent the helper from killing the test server

        kernel32 = ctypes.WinDLL("Kernel32.dll")
        pid = os.getpid()
        ses = wintypes.DWORD()
        if not kernel32.ProcessIdToSessionId(pid, ctypes.byref(ses)):
            return
        if ses.value != 0:
            return  # Already in interactive session — nothing to fix.

        # Idempotency lock — break Session 0 → kill → respawn → Session 0 loops.
        marker = os.path.join(tempfile.gettempdir(), "originsun_selfheal.lock")
        try:
            if os.path.isfile(marker) and (time.time() - os.path.getmtime(marker)) < 300:
                print("[SelfHeal] Recently attempted (<5min ago) — skipping to avoid kill loop")
                return
        except Exception:
            pass

        app_dir = os.path.dirname(os.path.abspath(__file__))
        vbs_path = os.path.join(app_dir, "start_hidden.vbs")
        if not os.path.isfile(vbs_path):
            return

        # Find an existing Originsun boot task (name varies across installs:
        # OriginsunAgent from one-shot installer, OriginsunBoot from older
        # Install_or_Update). If none exist this Agent was launched some
        # other way (manual run, service wrapper) and we shouldn't touch it.
        task_name = None
        for candidate in ("OriginsunAgent", "OriginsunBoot"):
            q = subprocess.run(
                ["schtasks", "/query", "/tn", candidate],
                capture_output=True, text=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if q.returncode == 0:
                task_name = candidate
                break
        if not task_name:
            return

        print(f"[SelfHeal] Agent running in Session 0 — re-registering {task_name} without /rl highest")

        # Re-register the task without /rl highest so it runs in Session 1.
        # NB: NOT adding /it — Interactive-only tasks can't be triggered by
        # `schtasks /run` from Session 0, so the helper's respawn step below
        # would silently fail (task Last Run stays "1999/11/30 placeholder").
        # Letting the task run in any session keeps recovery working; the
        # marker file below is what actually breaks the kill loop.
        subprocess.run(
            ["schtasks", "/delete", "/tn", task_name, "/f"],
            capture_output=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        cr = subprocess.run(
            ["schtasks", "/create", "/tn", task_name,
             "/tr", f'wscript.exe "{vbs_path}"',
             "/sc", "onlogon", "/f"],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if cr.returncode != 0:
            print(f"[SelfHeal] schtasks /create failed: {cr.stderr}")
            return

        # Write marker BEFORE spawning helper so the next process sees it.
        try:
            with open(marker, "w") as f:
                f.write(str(int(time.time())))
        except Exception:
            pass

        # Spawn a detached cmd that waits for us to die, then directly
        # launches uvicorn — bypassing schtasks (Interactive-only blocks
        # /run from Session 0) and start_hidden.vbs (DETACHED_PROCESS +
        # CREATE_NO_WINDOW makes vbs's WshShell.Run hang).
        #
        # Approach: write a temp BAT file with the recovery sequence, then
        # spawn `cmd /c "BAT"`. BAT files sidestep subprocess.list2cmdline's
        # quote-escape (which turns `"path"` into `\"path\"` — cmd doesn't
        # recognize `\"` as an escape, so paths get a literal backslash
        # prefix and `cd`/python invocation fails silently).
        #
        # Marker file ensures the new uvicorn's SelfHeal skips this branch
        # and lets startup complete. update_agent.py / OTA download is
        # intentionally skipped here — recovery from a kill loop ≠ OTA
        # update; OTA already ran in the BAT/vbs that started this chain.
        # Pickers stay broken in Session 0 until next user logout/login
        # (OriginsunBoot's onlogon trigger then lands in Session 1).
        sys_python = sys.executable
        outLog = os.path.join(app_dir, "uvicorn_out.log")
        errLog = os.path.join(app_dir, "uvicorn_err.log")
        bat_path = os.path.join(tempfile.gettempdir(), "originsun_selfheal_recover.bat")
        bat_lines = [
            "@echo off",
            "timeout /t 4 /nobreak >nul",
            f"taskkill /f /pid {pid} >nul 2>&1",
            f'cd /d "{app_dir}"',
            f'"{sys_python}" -m uvicorn main:io_app --host 0.0.0.0 --port 8000 --loop core.loopsetup:selector_loop_factory > "{outLog}" 2> "{errLog}"',
        ]
        try:
            with open(bat_path, "w", encoding="ascii", errors="replace") as f:
                f.write("\r\n".join(bat_lines))
        except Exception as e:
            print(f"[SelfHeal] write recover bat failed: {e}")
            return
        # NB: only CREATE_NO_WINDOW — DETACHED_PROCESS strips the console
        # which makes BAT internals (`timeout`, redirection) silently fail
        # to run. CREATE_NEW_PROCESS_GROUP is also dropped (we don't need
        # to send Ctrl+C to children).
        subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        print("[SelfHeal] Restart helper spawned — Agent will respawn in Session 1 shortly")
    except Exception as e:
        print(f"[SelfHeal] skipped: {e}")


# Run self-heal synchronously at module import, before uvicorn starts
# serving. If we're in Session 0, we re-register and let a helper kill us
# — so we don't want to waste time loading models first.
_self_heal_scheduled_task()


@app.on_event("startup")
async def _on_startup():
    # 開機 migration：SQL 正本在 db/migrations.py（只有資料），「何時、什麼順序套」在
    # db/startup_migrations.py（2026-09-13 從這裡純搬出去；加一段＝寫一支 _mNN_* 掛進 _POST_DB）
    _loop = asyncio.get_running_loop()
    state.set_main_loop(_loop)
    state.init_concurrency()
    print(f"[LOOP] event loop = {type(_loop).__name__}")  # expect SelectorEventLoop on Windows (asyncpg stability)
    from db import startup_migrations as _SM
    await _SM.run_pre_db()   # settings.json 一次性修補（必須在 init_db 之前）
    # ── PostgreSQL 連線 ──
    _db_import_ok = False
    try:
        from db.session import init_db
        _db_import_ok = True
        ok = await init_db()
        state.db_online = ok
        print(f"[DB] PostgreSQL {'連線成功' if ok else '不可用，使用 JSON fallback'}")
    except Exception as e:
        state.db_online = False
        print(f"[DB] 初始化失敗: {e}")
    if _db_import_ok and not state.db_online:
        # 有 DB 套件（master/dev）但啟動時連不上 → 主動告警。
        # 機隊 agent 沒裝 sqlalchemy，import 失敗不告警（那是常態不是故障）。
        from core.maintenance import notify_db_transition
        asyncio.ensure_future(notify_db_transition(False))
    await _SM.run_post_db()  # 開機 migration／種子（21 段，db/startup_migrations.py，各段自己守 db_online）

    asyncio.create_task(_periodic_version_check())
    asyncio.create_task(_periodic_db_health())
    try:
        from core.maintenance import run_local_maintenance
        from core.agent_watch import run_agent_watch
        asyncio.create_task(run_local_maintenance())  # 每日 retention + log 超大告警
        asyncio.create_task(run_agent_watch())        # 機隊離線告警（內建 master gate）
        # 影像紀錄縮圖補算：上傳由 NAS 對外容器收（那裡沒 ffmpeg），影片縮圖/時長
        # 與遷移中的舊縮圖由 master 醒著時補（內建 master gate）
        from services.media_log_catchup import run_media_log_catchup
        asyncio.create_task(run_media_log_catchup())
    except Exception as _e:
        print(f"[WARN] 維運背景工未啟動: {_e}")
    asyncio.create_task(_loop_heartbeat())
    import threading as _wd_threading
    _wd_threading.Thread(target=_wedge_watchdog, daemon=True, name="wedge-watchdog").start()
    from core.scheduler import run_scheduler  # type: ignore
    asyncio.create_task(run_scheduler())
    from services import timesheet_digest, timesheet_puller
    timesheet_puller.start_scheduler_task()   # 工時 Google Sheet 定時拉（內建 master gate）
    timesheet_digest.start_scheduler_task()   # 週一工時 digest → Google Chat（內建 master gate，預設關）


# ── Wedge watchdog: guaranteed recovery ──────────────────────────────────
# Several in-process fixes (pool_pre_ping, non-blocking agent polls,
# SelectorEventLoop, asyncpg command_timeout) each failed to fully stop 8000
# from occasionally wedging — db_online sticks false / the loop stalls (CPU 0%,
# every endpoint slow), recoverable ONLY by a process restart. Since restart
# ALWAYS clears it, a daemon THREAD (off the event loop, so it survives a loop
# stall) clean-restarts the process when the wedge is detected. This is the
# safety net until the true root is found. (2026-06-18)
import time as _wd_time
_WD_LAST_BEAT = [0.0]            # monotonic ts stamped by _loop_heartbeat
_WD_DB_OFFLINE_SINCE = [0.0]     # monotonic ts when db_online first went false
_WD_PROC_START = _wd_time.monotonic()


async def _loop_heartbeat():
    """Stamp a heartbeat every 10s so the watchdog thread can detect a stalled loop."""
    while True:
        _WD_LAST_BEAT[0] = _wd_time.monotonic()
        await asyncio.sleep(10)


def _wedge_watchdog():
    """Daemon thread (NOT on the event loop). Clean-restarts the process if
    db_online is stuck false >120s or the loop heartbeat goes stale >90s. A
    3-min min-uptime guard prevents restart loops."""
    import time
    while True:
        time.sleep(15)
        try:
            now = time.monotonic()
            if now - _WD_PROC_START < 180:
                continue  # let a freshly-started process settle before any restart
            if not state.db_online:
                if _WD_DB_OFFLINE_SINCE[0] == 0.0:
                    _WD_DB_OFFLINE_SINCE[0] = now
            else:
                _WD_DB_OFFLINE_SINCE[0] = 0.0
            db_stuck = _WD_DB_OFFLINE_SINCE[0] and (now - _WD_DB_OFFLINE_SINCE[0] > 120)
            beat_age = (now - _WD_LAST_BEAT[0]) if _WD_LAST_BEAT[0] else 0
            loop_dead = beat_age > 90
            if db_stuck or loop_dead:
                why = (f"db_online offline {int(now - _WD_DB_OFFLINE_SINCE[0])}s"
                       if db_stuck else f"loop heartbeat stale {int(beat_age)}s")
                print(f"[WATCHDOG] {why} — dumping stacks + clean-restarting 8000", flush=True)
                # Dump EVERY thread's stack (incl. the stalled event-loop thread) so the
                # next wedge finally reveals WHERE the loop is stuck — the root we
                # couldn't pin from 4 hypotheses. Appended to wedge_dump.txt.
                try:
                    import faulthandler
                    _dump = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wedge_dump.txt")
                    with open(_dump, "a", encoding="utf-8") as _fd:
                        _fd.write(f"\n===== WEDGE {why} @ uptime {int(now - _WD_PROC_START)}s =====\n")
                        faulthandler.dump_traceback(file=_fd, all_threads=True)
                except Exception as e:
                    print(f"[WATCHDOG] stack dump failed: {e}", flush=True)
                try:
                    from core.process_spawn import trigger_detached_restart
                    trigger_detached_restart(run_ota=False)
                except Exception as e:
                    print(f"[WATCHDOG] restart spawn failed: {e}", flush=True)
                time.sleep(1.5)
                os._exit(1)
        except Exception as e:
            print(f"[WATCHDOG] error: {e}")


async def _periodic_db_health():
    """每 60 秒檢查 DB 連線；斷線時「重建連線池」而非只重試。

    關鍵：db_available() 用的是現有 engine/pool —— 若 pool 在 Windows
    ProactorEventLoop 上 wedge 掉，db_available() 會一直失敗，光重試永遠救不回。
    所以只要目前是 offline，就先 dispose 壞掉的 engine 再 init_db() 重建全新 pool。
    全部包 wait_for，確保「恢復檢查」本身不會卡死 event loop（卡死會讓整站變慢）。
    """
    try:
        from db.session import db_available, init_db, close_db
    except ImportError:
        return  # 代理端沒有 db 模組，直接退出
    while True:
        await asyncio.sleep(60)
        was_online = state.db_online
        try:
            if state.db_online:
                # 還在線：便宜探測。timeout=15 > connect_args.command_timeout(8)+reconnect，
                # 讓 db_available() 的 pre_ping 有時間「砍掉 black-hole 連線→重連」自我恢復，
                # 不會在恢復途中被 wait_for 取消而誤判離線。
                ok = await asyncio.wait_for(db_available(), timeout=15)
                if not ok:
                    print("[DB] PostgreSQL 連線中斷，下一輪重建連線池")
                    state.db_online = False
            else:
                # 離線：重建。先丟掉（可能 wedge 的）舊 engine，避免壞連線殘留
                # 拖累 event loop，再用全新 pool 測試連線。
                try:
                    await asyncio.wait_for(close_db(), timeout=8)
                except Exception:
                    pass
                ok = await asyncio.wait_for(init_db(), timeout=15)
                state.db_online = bool(ok)
                if ok:
                    print("[DB] PostgreSQL 連線恢復（已重建連線池）")
        except asyncio.TimeoutError:
            state.db_online = False
        except Exception:
            state.db_online = False
        if state.db_online != was_online:
            from core.maintenance import notify_db_transition
            await notify_db_transition(state.db_online)


def _read_local_version() -> str:
    """讀取本機 version.json 中的版號（正本在 core.version）。"""
    from core.version import read_local_version
    return read_local_version()


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

@app.get("/proposal-plan.html", include_in_schema=False)
async def _legacy_proposal_plan(request: Request):
    """舊網址 → `/project.html`（2026-08-15 改名，主鍵早已從提案換成專案）。

    🔴 **query string 一定要帶過去**：已經發給客戶的共編連結是
    `/proposal-plan.html?t=<token>`，掉了 token 就是一個空頁。同理 `?pid=`
    （CRM 的「開啟企劃」、我的工作台）與 `?id=`。

    註冊在 `app.mount("/")` 之前才會贏 —— StaticFiles 掛在根，順序決定誰接。
    NAS 那側的同一條規則在 docker/nginx/originsun.conf（對外流量不經過這裡）。
    """
    qs = request.url.query
    return RedirectResponse("/project.html" + (f"?{qs}" if qs else ""),
                            status_code=301)


@app.get("/e/{code}", include_in_schema=False)
async def _short_invoice_file(code: str, request: Request):
    """電子發票短網址：`/e/{12 碼}` → 檔案下載（免登入，憑證就是那串碼）。

    掛在根路徑是為了**短** —— 走 router 前綴會變成 /api/v1/crm/... 又長回去
    （owner 要求：原本整條 287 字元，現在約 49）。實作在
    routers/crm/invoice_files.py，這裡只是把根路徑接過去。

    註冊在 `app.mount("/")` 之前才會贏 —— StaticFiles 掛在根，順序決定誰接。
    """
    from core.public_access import surface_gate
    await surface_gate(request)   # 公開區「發票影像分享」關閉 → 404（寄給客戶的就是這條短網址）
    # owner 2026-09-10：這條從「點了直接下載」改成回一頁（發票資訊 ＋ 下載鈕）。
    # 頁面自己從 location.pathname 取短碼、再打 /api/v1/crm/public/invoice-file/{token}/meta。
    # 短碼**不由這裡驗** —— 驗證在那支 meta 上（那一支對外容器也掛得到，master 關機照樣運作）。
    return no_store_file(os.path.join("frontend", "invoice-file.html"), media_type="text/html")


@app.get("/p/{code}", include_in_schema=False)
async def _short_payout_note(code: str, request: Request):
    """匯款通知短網址：`/p/{12 碼}` → 一頁（免登入，憑證就是那串碼）。

    掛在根路徑是為了短 —— 這條是貼進 LINE 給收款人的。
    短碼**不由這裡驗**：驗證在 `/api/v1/crm/public/payout/{token}` 那支
    （它掛 public_router，NAS 對外容器也吃得到，master 關機他照樣打得開）。

    註冊在 `app.mount("/")` 之前才會贏 —— StaticFiles 掛在根，順序決定誰接。
    """
    from core.public_access import surface_gate
    await surface_gate(request)   # 公開區「匯款通知」關閉 → 404
    return no_store_file(os.path.join("frontend", "payout.html"), media_type="text/html")


@app.get("/q/{code}", include_in_schema=False)
async def _short_quote_view(code: str, request: Request):
    """報價單線上檢視短網址：`/q/{12 碼}` → HTML（免登入，憑證就是那串碼；頁上有「下載 PDF」）。
    同 /e/{code}：掛根路徑是為了短；實作在 routers/crm/quotes.py。"""
    from core.public_access import surface_gate
    await surface_gate(request)   # 公開區「報價單線上檢視」關閉 → 404
    from routers.crm.quotes import public_quote_html
    return await public_quote_html(code, request)


@app.get("/q/{code}/pdf", include_in_schema=False)
async def _short_quote_pdf(code: str, request: Request):
    """免登入：線上檢視頁的「下載 PDF」（憑證＝網址裡的 share_token，逐字比對）。"""
    from core.public_access import surface_gate
    await surface_gate(request)
    from routers.crm.quotes import public_quote_pdf
    return await public_quote_pdf(code, request)


@app.get("/")
async def serve_index():
    """index.html 永遠不留快取（殼層一換版就要拿到新的）。"""
    return no_store_file(os.path.join("frontend", "index.html"), media_type="text/html")

os.makedirs("uploads", exist_ok=True)
import mimetypes as _mt  # 精簡 Python mimetypes 可能不認 .webp → StaticFiles 回 text/plain
_mt.add_type("image/webp", ".webp")
_mt.add_type("image/avif", ".avif")
class _NoStoreStaticFiles(StaticFiles):
    """收據／發票影像：不留任何快取副本（同 core.no_store）。handler 自己宣告 Cache-Control，
    NoCacheMiddleware 的「handler 宣告了就以它為準」規則接手，middleware 不必認路徑。"""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["cache-control"] = NO_STORE_B.decode()
        return resp


app.mount("/uploads", _NoStoreStaticFiles(directory="uploads"), name="uploads")
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
    uvicorn.run(io_app, host="0.0.0.0", port=port, log_level="error",
                loop="core.loopsetup:selector_loop_factory")  # SelectorEventLoop (asyncpg stability)
