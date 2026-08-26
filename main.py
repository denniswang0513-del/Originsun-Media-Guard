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
from fastapi.staticfiles import StaticFiles  # type: ignore
from fastapi.responses import FileResponse, RedirectResponse  # type: ignore
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
    'api_api_keys', 'api_timesheets', 'api_cashflow', 'api_finance', 'api_finance_stmt', 'api_finance_card', 'api_finance_assets', 'api_finance_projects', 'api_locations', 'api_proposals',
    'api_references',
    'api_intel',
    'api_portal',
    'api_equipment',
    'api_footage',
    'api_analytics',
    'api_crm',
    'api_drone_meta',
    'api_drone_watcher',
    'api_bulletin',
    'api_me',
    'api_hr',
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
                if method == "GET":
                    headers.append((b"cache-control", b"no-store, no-cache, must-revalidate, max-age=0"))
                    headers.append((b"pragma", b"no-cache"))
                    headers.append((b"expires", b"0"))
                headers.append((b"access-control-allow-private-network", b"true"))
                message = {**message, "headers": headers}
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
    _loop = asyncio.get_running_loop()
    state.set_main_loop(_loop)
    state.init_concurrency()
    print(f"[LOOP] event loop = {type(_loop).__name__}")  # expect SelectorEventLoop on Windows (asyncpg stability)
    # ── 一次性遷移（2026-07）：把外洩的舊 DB 密碼 originsun2026 移出 settings.json ──
    # 機隊 8 台無法直接觸及（無 SSH / 各自 jwt_secret / master 無推設定機制），這段在
    # 每台 OTA 重啟時自癒：偵測到 settings.json 仍是舊密碼 → 改成 config 新預設。
    # 必須在 init_db() 之前（engine 讀 get_database_url() → settings.json）。
    # guarded（只在含舊密碼時動）+ idempotent + 例外不擋啟動。
    try:
        from config import load_settings, save_settings, _DEFAULT_SETTINGS
        # 只換「密碼」子字串，保留主機/埠/庫名 —— dev 的 mediaguard_dev 不能被改成
        # 預設的 mediaguard（否則 dev 連到生產庫，破壞 §2.1 dev/prod 隔離）。
        _new_pw = _DEFAULT_SETTINGS["database_url"].split("://originsun:", 1)[-1].split("@", 1)[0]
        _cur_url = load_settings().get("database_url", "")
        if "originsun2026" in _cur_url and _new_pw and _new_pw != "originsun2026":
            save_settings({"database_url": _cur_url.replace("originsun2026", _new_pw)})
            print("[migrate] settings.json 的外洩 DB 密碼已輪替為新值（保留庫名）")
    except Exception as _e_mig:
        print(f"[migrate] DB 密碼輪替略過: {_e_mig}")
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
    # ── DB Migration: Google OAuth columns ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from sqlalchemy import text
                async with factory() as session:
                    for col, coltype in [
                        ("google_id", "VARCHAR(255)"),
                        ("email", "VARCHAR(255)"),
                        ("avatar_url", "VARCHAR(512)"),
                        ("modules", "JSONB"),          # RBAC v2: 權限直接綁帳號
                        ("access_level", "INTEGER"),   # RBAC v2: 3=管理員, 1=一般
                        ("staff_id", "VARCHAR(32)"),   # N0: 帳號 ↔ crm_staff（個人工作台）
                    ]:
                        try:
                            await session.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {coltype}"))
                            await session.commit()
                        except Exception:
                            await session.rollback()
                    # Google-only users have no password — allow NULL
                    try:
                        await session.execute(text("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL"))
                        await session.commit()
                    except Exception:
                        await session.rollback()
                    # ── RBAC v2 一次性回填：把角色權限複製到 per-user 欄位 ──
                    # idempotent — 只填還是 NULL 的 row，跑多次無害。確保上線後
                    # 沒有人掉權限；之後角色層即可淘汰（Phase 2）。
                    import json as _json_rbac
                    from core.auth import ALL_MODULES as _all_mods
                    _all_mods_sql = _json_rbac.dumps(list(_all_mods))
                    for _bf_sql in [
                        # 1) 有 role_id 的使用者：複製其角色的 modules + access_level
                        "UPDATE users u SET modules = r.modules, access_level = r.access_level "
                        "FROM roles r WHERE u.role_id = r.id AND u.modules IS NULL",
                        # 2) admin 保險（萬一沒有 role_id）：給全模組 + Lv3
                        f"UPDATE users SET access_level = 3, modules = '{_all_mods_sql}'::jsonb "
                        "WHERE username = 'admin' AND modules IS NULL",
                        # 3) 其餘殘留 NULL → 一般使用者、空模組（管理員可再授權）
                        "UPDATE users SET access_level = 1 WHERE access_level IS NULL",
                        "UPDATE users SET modules = '[]'::jsonb WHERE modules IS NULL",
                    ]:
                        try:
                            await session.execute(text(_bf_sql))
                            await session.commit()
                        except Exception:
                            await session.rollback()
        except Exception:
            pass
    # ── 公布欄欄位 migration（新欄位 create_all 不補到既有表）+ 種子 ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _f_bl = get_session_factory()
            if _f_bl:
                from sqlalchemy import text as _t_bl
                async with _f_bl() as _s_bl:
                    for _c, _ct in [("assignee", "VARCHAR(16) DEFAULT 'me'"),
                                    ("conversation", "JSONB"), ("activity", "TEXT"),
                                    ("assignee_username", "VARCHAR(64)")]:  # N0: 指派到個人
                        try:
                            await _s_bl.execute(_t_bl(
                                f"ALTER TABLE bulletin_items ADD COLUMN IF NOT EXISTS {_c} {_ct}"))
                            await _s_bl.commit()
                        except Exception:
                            await _s_bl.rollback()
                from routers.api_bulletin import seed_if_empty as _seed_bulletin
                async with _f_bl() as _s_bl2:
                    await _seed_bulletin(_s_bl2)
        except Exception:
            pass
    # ── DB Migration: api_keys table ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory2 = get_session_factory()
            if factory2:
                from sqlalchemy import text as _text
                async with factory2() as session:
                    await session.execute(_text("""
                        CREATE TABLE IF NOT EXISTS api_keys (
                            id SERIAL PRIMARY KEY,
                            key_hash VARCHAR(64) NOT NULL UNIQUE,
                            key_prefix VARCHAR(12) NOT NULL,
                            name VARCHAR(64) NOT NULL,
                            username VARCHAR(64) NOT NULL,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            expires_at TIMESTAMPTZ,
                            last_used_at TIMESTAMPTZ,
                            is_active BOOLEAN NOT NULL DEFAULT TRUE
                        )
                    """))
                    await session.execute(_text("CREATE INDEX IF NOT EXISTS idx_ak_username ON api_keys(username)"))
                    await session.execute(_text("CREATE INDEX IF NOT EXISTS idx_ak_active ON api_keys(is_active)"))
                    await session.commit()
        except Exception:
            pass
    # ── DB Migration: CRM new columns ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _f = get_session_factory()
            if _f:
                from sqlalchemy import text as _t
                async with _f() as _s:
                    _crm_cols = [
                        ("crm_projects", "start_date", "TIMESTAMPTZ"),
                        ("crm_projects", "completion_date", "TIMESTAMPTZ"),
                        ("crm_projects", "project_type", "VARCHAR(64) DEFAULT ''"),
                        ("crm_projects", "contract_amount", "INTEGER"),
                        ("crm_projects", "tax_rate", "INTEGER DEFAULT 5"),
                        ("crm_projects", "profit_target_pct", "INTEGER DEFAULT 20"),
                        ("crm_projects", "misc_budget_pct", "INTEGER DEFAULT 5"),
                        ("crm_projects", "payment_status", "VARCHAR(32) DEFAULT '未到帳'"),
                        ("crm_projects", "amount_receivable", "INTEGER"),
                        ("crm_projects", "amount_received", "INTEGER"),
                        ("crm_projects", "transfer_fee", "INTEGER"),
                        ("crm_quotation_items", "internal_cost", "INTEGER DEFAULT 0"),
                        ("crm_project_staff", "phase", "VARCHAR(32) DEFAULT ''"),
                        ("crm_project_staff", "actual_days", "INTEGER"),
                        ("crm_project_staff", "actual_cost", "INTEGER"),
                        ("crm_project_staff", "payment_status", "VARCHAR(32)"),
                        ("crm_project_staff", "payment_date", "TIMESTAMPTZ"),
                        ("crm_staff", "address", "VARCHAR(255)"),
                        ("crm_payment_requests", "planned_month", "VARCHAR(7)"),
                        ("crm_invoices", "recipient", "VARCHAR(128)"),
                        ("crm_invoices", "recipient_phone", "VARCHAR(32)"),
                        ("crm_invoices", "recipient_address", "VARCHAR(255)"),
                        ("crm_cash_entries", "updated_at", "TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP"),
                        ("crm_cash_entries", "invoice_id", "VARCHAR(32)"),
                        ("crm_cash_entries", "bank_fee", "INTEGER"),
                        # N-hr H2 出缺勤 + 工時 staff_id 對映（hr_leave_requests 新表由 create_all 建）
                        ("crm_staff", "annual_leave_days", "INTEGER"),
                        ("timesheets", "staff_id", "VARCHAR(32)"),
                        # 影像紀錄：子資料夾名（首次生成後固定，見 media_log._ensure_folder_name）
                        ("project_media_log", "folder_name", "VARCHAR(255)"),
                        # 提案庫資產夾名（core.project_folders，2026-08-06）
                        ("crm_projects", "proposal_folder_name", "VARCHAR(255)"),
                        # 結案歸檔清單 + 專案回顧 KPTA（core/project_archive.py）
                        ("crm_projects", "archive_checklist", "JSONB"),
                        ("crm_projects", "review_kpta", "JSONB"),
                        # 提案企劃矩陣（docs/PROPOSAL_PLANNER.md）
                        ("preprod_proposals", "plan", "JSONB"),
                        ("preprod_proposals", "notes", "TEXT"),   # 基本資料備註（§9.7）
                        # 現況盤點表（core/proposal_survey.py — 對齊 owner 的 Notion 專案啟動面版）
                        ("preprod_proposals", "survey", "JSONB"),
                        # 重點提案勾選（core/pinned_assets.py — 取代舊的「對外分享」子夾）
                        ("preprod_proposals", "pinned_assets", "JSONB"),
                        ("preprod_proposals", "pins_public", "BOOLEAN DEFAULT FALSE"),
                        # 提案在專案資產夾底下的子夾（一專案多提案時各自分開）
                        ("preprod_proposals", "folder_subpath", "VARCHAR(255)"),
                        # 參考影片庫 v2（docs/REFERENCE_LIBRARY.md）
                        ("preprod_references", "description", "TEXT"),
                        ("preprod_references", "facets", "JSONB"),
                        ("preprod_references", "research", "JSONB"),
                        ("preprod_references", "curated", "BOOLEAN"),
                        ("preprod_references", "provider", "VARCHAR(16)"),
                        ("preprod_references", "video_id", "VARCHAR(64)"),
                        ("preprod_references", "updated_at", "TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP"),
                        ("preprod_reference_shots", "created_key", "VARCHAR(64)"),
                        # 會議記錄：錄音 → 逐字稿 → AI 整理（services/meeting_transcriber）
                        ("preprod_meeting_notes", "audio_rel", "VARCHAR(512)"),
                        ("preprod_meeting_notes", "transcript", "TEXT"),
                        ("preprod_meeting_notes", "ai_summary", "TEXT"),
                        ("preprod_meeting_notes", "status", "VARCHAR(16)"),
                        ("preprod_meeting_notes", "error", "TEXT"),
                        ("preprod_meeting_notes", "phase", "VARCHAR(64)"),
                        # 單篇會議記錄唯讀分享（owner 2026-08-15）
                        ("preprod_meeting_notes", "share_token", "VARCHAR(512)"),
                        # 影片封存（docs/REFERENCE_LIBRARY.md §12）
                        ("preprod_references", "archive_status", "VARCHAR(16)"),
                        ("preprod_references", "archive_path", "VARCHAR(512)"),
                        ("preprod_references", "archive_error", "TEXT"),
                        ("preprod_references", "archived_at", "TIMESTAMPTZ"),
                        ("preprod_references", "archive_tries", "INTEGER"),
                        # 收款↔發票分配的逐張匯費（owner 2026-08-24）。沒有這欄的話
                        # 關聯面板每次載入那格都是空的 → 按一下儲存就送 fee=0，
                        # deposit 退回去、bank_fee 被清掉（靜默回退，畫面看不出來）。
                        ("crm_cash_invoice_links", "fee", "INTEGER NOT NULL DEFAULT 0"),
                    ]
                    for tbl, col, coltype in _crm_cols:
                        try:
                            await _s.execute(_t(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col} {coltype}"))
                            await _s.commit()
                        except Exception:
                            await _s.rollback()
                    # 參考影片引用：舊 preprod_proposal_refs → preprod_reference_links
                    # （階段 3 一次性、冪等；舊表保留不再寫入，見 models.py 該類 docstring）
                    try:
                        await _s.execute(_t("""
                            INSERT INTO preprod_reference_links
                                   (id, reference_id, target_type, target_id, created_at)
                            SELECT r.id, r.reference_id, 'proposal', r.proposal_id, CURRENT_TIMESTAMP
                              FROM preprod_proposal_refs r
                             WHERE NOT EXISTS (
                                   SELECT 1 FROM preprod_reference_links l
                                    WHERE l.reference_id = r.reference_id
                                      AND l.target_type = 'proposal'
                                      AND l.target_id = r.proposal_id)
                        """))
                        await _s.commit()
                    except Exception as _mig_err:
                        # 不能靜默：所有讀取都已改查 links，搬遷失敗 = 每個提案的參考片
                        # 清單憑空變空，而且與「使用者自己移除」無法區分
                        print(f"[WARN] reference link backfill failed: {_mig_err}")
                        await _s.rollback()
        except Exception:
            pass
    # ── DB Migration: CRM 索引 + 冪等 DDL（逐條跑，失敗 rollback 不擋啟動）──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _fi = get_session_factory()
            if _fi:
                from sqlalchemy import text as _ti
                async with _fi() as _si:
                    for idx_sql in [
                        "CREATE INDEX IF NOT EXISTS idx_quote_created ON crm_quotations(created_at)",
                        "CREATE INDEX IF NOT EXISTS idx_invoice_issue_status ON crm_invoices(issue_status)",
                        "CREATE INDEX IF NOT EXISTS idx_invoice_pay_status ON crm_invoices(payment_status)",
                        "CREATE INDEX IF NOT EXISTS idx_payreq_planned_month ON crm_payment_requests(planned_month)",
                        "CREATE INDEX IF NOT EXISTS idx_payreq_payee ON crm_payment_requests(payee_name)",
                        # 提案=專案合體：專案列表附掛提案子狀態的 scalar subquery 用
                        "CREATE INDEX IF NOT EXISTS idx_pprop_project ON preprod_proposals(project_id, updated_at)",
                        # §14 工作流的聚合查詢（EXPLAIN 實測補的四顆）：
                        # ① timesheets 用名稱對映那一臂原本是 Seq Scan，而「查不到」
                        #    才是年輕專案的常態 —— 也就是每次開進度分頁都掃全表
                        "CREATE INDEX IF NOT EXISTS idx_ts_project_name ON timesheets(project_name)",
                        # ② 場景使用履歷只索引了 location_id
                        "CREATE INDEX IF NOT EXISTS idx_ploc_usage_project ON preprod_location_usages(project_id)",
                        # ③④ 單欄索引下 planner 只能挑一個，另一個變 Filter；
                        #    inv_unpaid 是 COUNT 不能短路 → 會走遍全公司的未收款發票
                        "CREATE INDEX IF NOT EXISTS idx_invoice_project_status ON crm_invoices(project_id, payment_status)",
                        "CREATE INDEX IF NOT EXISTS idx_quote_project_status ON crm_quotations(project_id, status)",
                        # 提案=專案合體：前期草稿提案還沒定客戶也要能是專案
                        # （ALTER 冪等 — 已 DROP 過再跑一次不會錯）
                        "ALTER TABLE crm_projects ALTER COLUMN client_id DROP NOT NULL",
                    ]:
                        try:
                            await _si.execute(_ti(idx_sql))
                            await _si.commit()
                        except Exception:
                            await _si.rollback()
        except Exception:
            pass

    # ── DB Migration: 提案=專案合體 — 既有提案補殼專案（冪等，2026-08-06） ──
    if state.db_online:
        try:
            from routers.api_proposals import migrate_unlinked_proposals_to_projects
            await migrate_unlinked_proposals_to_projects()
        except Exception as _e_pp:
            # 不能靜默死：沒遷移的提案不會出現在管線專案列（前端提案帶只剩 legacy 提示）
            print(f"[WARN] 提案殼專案遷移失敗: {_e_pp}")

    # ── settings.json 的 proposals.root → DB settings（一次性，2026-08-07）──
    # NAS 對外容器要讀得到它（客戶的提案分享頁由它 serve）。冪等；DB 已有值
    # 就不動，失敗只是繼續用 settings.json（fallback 仍在）。
    if state.db_online:
        try:
            from routers.crm.proposal_assets import migrate_root_to_db
            await migrate_root_to_db()
        except Exception as _e_pr:
            print(f"[WARN] proposals.root 遷移失敗: {_e_pr}")

    # ── 片庫封存資料夾改名 uuid → {片名}_{品牌}（2026-08-06）──
    # 背景跑：逐支 rename 打 NAS，不擋 startup。閘門全在協程自己身上
    # （master gate、factory 為 None 早退、逐筆容錯），這裡不重複判斷。
    if state.db_online:
        from services.reference_archiver import migrate_folder_names
        asyncio.create_task(migrate_folder_names())

    # ── DB Migration: crm_project_cost_lines table ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _fcl = get_session_factory()
            if _fcl:
                from sqlalchemy import text as _tcl
                async with _fcl() as _scl:
                    await _scl.execute(_tcl("""
                        CREATE TABLE IF NOT EXISTS crm_project_cost_lines (
                            id VARCHAR(32) PRIMARY KEY,
                            project_id VARCHAR(32) NOT NULL,
                            phase VARCHAR(32) NOT NULL,
                            item_name VARCHAR(128) NOT NULL,
                            sort_order INTEGER NOT NULL DEFAULT 0,
                            estimated_amount INTEGER,
                            estimated_staff_id VARCHAR(32),
                            estimated_notes VARCHAR(255),
                            actual_amount INTEGER,
                            actual_staff_id VARCHAR(32),
                            actual_notes VARCHAR(255),
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await _scl.execute(_tcl(
                        "CREATE INDEX IF NOT EXISTS idx_costline_project "
                        "ON crm_project_cost_lines(project_id)"
                    ))
                    await _scl.execute(_tcl(
                        "CREATE INDEX IF NOT EXISTS idx_costline_phase "
                        "ON crm_project_cost_lines(project_id, phase)"
                    ))
                    await _scl.commit()
        except Exception:
            pass

    # ── DB Migration: crm_project_expenses new columns ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _fex = get_session_factory()
            if _fex:
                from sqlalchemy import text as _tex
                async with _fex() as _sex:
                    for col_sql in [
                        # 對帳單匯入：貸款的銀行放款帳號（合庫備註帶 315614 這種號碼，
                        # 靠它認出扣款屬於哪筆貸款 —— 金額配對在銀行月付固定、我們重算
                        # 的情況下會差幾十元，帳號才是可靠的鍵）
                        "ALTER TABLE finance_loans ADD COLUMN IF NOT EXISTS account_no VARCHAR(32)",
                        # 銀行實扣金額（空＝照攤還表）。銀行按實際天數算息會跟我們
                        # 重算的差幾元，記攤還表數字會讓銀行餘額逐期累積偏差、
                        # 對帳工作台也永遠勾不掉那些列。
                        "ALTER TABLE finance_loan_payments ADD COLUMN IF NOT EXISTS paid_amount INTEGER",
                        # 結案製作看板：結案專案的官網製作階段（待製作/製作中/不上官網）
                        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS website_prod_stage VARCHAR(16)",
                        # N2 階段0：專案時數預算池（對齊工時 Sheet 的預算欄，藍圖 §3 現況修正）
                        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS budget_hours DOUBLE PRECISION",
                        # N-now 上架驗收：rebuild 後對外頁 200 驗證通過的時間戳
                        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS website_verified_at TIMESTAMP WITH TIME ZONE",
                        # §14 工作流：五軌進度裡「推不出來」的手動里程碑（開拍/剪輯完成）。
                        # 值在 DB、欄目在 core/project_flow.py —— 與 archive_checklist /
                        # proposal_survey 同一套慣例，所以不另建表。
                        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS flow_checks JSONB",
                        # H1 員工檔案完整化（HR_FIN_PLAN）
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS employment_type VARCHAR(16)",
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS hire_date TIMESTAMP WITH TIME ZONE",
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS leave_date TIMESTAMP WITH TIME ZONE",
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS emergency_contact VARCHAR(128)",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS sub_item VARCHAR(128)",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS payee VARCHAR(64)",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS advance_id VARCHAR(32)",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
                        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS advance_by VARCHAR(64)",
                        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS is_advance INTEGER DEFAULT 0",
                        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS advance_returned INTEGER DEFAULT 0",
                        # 代開自動化的冪等鍵改用發票 id（無號發票也有鍵）
                        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS source_invoice_id VARCHAR(32)",
                        "CREATE INDEX IF NOT EXISTS idx_payreq_src_invoice ON crm_payment_requests(source_invoice_id)",
                        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS advance_payment_id VARCHAR(32)",
                        # 兩本帳：私帳客戶分家（owner 2026-08-26「先不要混到 crm」）
                        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
                        # 收支雙備註（owner 2026-08-26）：銀行原始資訊與手寫附註分欄
                        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS bank_memo TEXT",
                        # 私帳客戶 → CRM 客戶對應連結（owner 2026-08-26「用連結的方式同步」）
                        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS crm_link_id VARCHAR(32)",
                        # ── 零用金請款（docs/PETTY_CASH_PLAN.md）──────────────
                        # 支出單據行擴充：一筆登記同時餵專案成本與個人請款。
                        # project_id 放寬成可空 —— 公司層級支出（行政/業務推廣）沒有
                        # 專案，歷史資料 289/443 列如此。既有 241 列不受影響。
                        "ALTER TABLE crm_project_expenses ALTER COLUMN project_id DROP NOT NULL",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS expense_date TIMESTAMP WITH TIME ZONE",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS staff_id VARCHAR(32)",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS item VARCHAR(32)",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS invoice_no VARCHAR(32)",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS has_invoice INTEGER DEFAULT 0",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS claim_id VARCHAR(32)",
                        # 🔴 這欄**不能**帶 DEFAULT：帶了的話 ADD COLUMN 會把既有列
                        # 全部填成「草稿」，而草稿在零用金的語意是「還沒送出的請款」——
                        # 既有雜支會憑空出現在請款清單裡。留 NULL 再由下一句補終態。
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS status VARCHAR(16)",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS project_label VARCHAR(128)",
                        "CREATE INDEX IF NOT EXISTS idx_expense_staff_claim "
                        "ON crm_project_expenses (staff_id, claim_id)",
                        "CREATE INDEX IF NOT EXISTS idx_expense_date ON crm_project_expenses (expense_date)",
                        # 既有列是「已經在專案裡的雜支」，不是待請款單據 —— 補一個終態。
                        # 條件帶 claim_id IS NULL：日後真正的草稿都有批次或會自己走
                        # ORM 預設值，不會被這句掃到（這句每次 boot 都跑）。
                        "UPDATE crm_project_expenses SET status='已付款' "
                        "WHERE status IS NULL AND claim_id IS NULL",
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS petty_float INTEGER DEFAULT 0",
                        "UPDATE crm_staff SET petty_float=0 WHERE petty_float IS NULL",
                        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS expense_id VARCHAR(32)",
                        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS reimbursement_id VARCHAR(32)",
                        # 費用歸屬人（後期雜支那類「別人墊、帳算你的」）
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS owner_staff_id VARCHAR(32)",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS owner_settled INTEGER DEFAULT 0",
                        "CREATE INDEX IF NOT EXISTS idx_expense_owner ON crm_project_expenses (owner_staff_id, owner_settled)",
                        "CREATE INDEX IF NOT EXISTS idx_payreq_reimb ON crm_payment_requests (reimbursement_id)",
                        "CREATE INDEX IF NOT EXISTS idx_cash_expense ON crm_cash_entries (expense_id)",
                        # receipt_path 從 crm_projects 下放到 crm_project_cost_groups。
                        # 第一行先確保 cost_groups 有此欄位；接著一次性把舊值搬到
                        # 該專案 sort_order 最小的子表（且子表還沒設值時）；最後 DROP
                        # 掉 crm_projects 的舊欄位。三句都 idempotent，跑多次無害。
                        "ALTER TABLE crm_project_cost_groups ADD COLUMN IF NOT EXISTS receipt_path VARCHAR(512)",
                        """
                        UPDATE crm_project_cost_groups cg
                           SET receipt_path = p.receipt_path
                          FROM crm_projects p
                         WHERE cg.project_id = p.id
                           AND p.receipt_path IS NOT NULL
                           AND p.receipt_path <> ''
                           AND (cg.receipt_path IS NULL OR cg.receipt_path = '')
                           AND cg.sort_order = (
                               SELECT MIN(sort_order) FROM crm_project_cost_groups
                                WHERE project_id = p.id
                           )
                        """,
                        "ALTER TABLE crm_projects DROP COLUMN IF EXISTS receipt_path",
                        # freeform sc.tags 廢除，統一走 website_categories（kind=tag）。
                        # 舊資料一併刪除（使用者確認）。
                        "ALTER TABLE crm_project_showcase DROP COLUMN IF EXISTS tags",
                        # B5 素材庫：pg_trgm 加速 transcript ILIKE（extension 沒權限就
                        # 降級純 ILIKE，查詢語法相同——這兩句失敗都無妨，有 try/except 容錯）。
                        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
                        "CREATE INDEX IF NOT EXISTS idx_footage_transcript_trgm "
                        "ON footage_index USING gin (transcript gin_trgm_ops)",
                    ]:
                        try:
                            await _sex.execute(_tex(col_sql))
                            await _sex.commit()
                        except Exception:
                            await _sex.rollback()
        except Exception:
            pass

    # ── DB Migration: 專案狀態 8 階段化 backfill（一次性冪等）──
    # 舊 5 值 taxonomy → 新 8 階段。單句 CASE UPDATE + WHERE IN(舊值)：backfill 完成後
    # 舊值不復存在，重跑 WHERE 命中 0 列（no-op）。一次 round-trip 取代 5 句，每次 boot
    # （含機隊 OTA 重啟）只掃一次；失敗只記錄不中斷 startup。
    if state.db_online:
        try:
            from db.session import get_session_factory
            _fst = get_session_factory()
            if _fst:
                from sqlalchemy import text as _tst
                async with _fst() as _sst:
                    _r_st = await _sst.execute(_tst(
                        "UPDATE crm_projects SET status = CASE status "
                        "WHEN '洽談中' THEN '洽詢' WHEN '報價中' THEN '提案' "
                        "WHEN '進行中' THEN '製作' WHEN '已結案' THEN '歸檔' "
                        "WHEN '結案作業' THEN '結案' END "
                        "WHERE status IN ('洽談中','報價中','進行中','已結案','結案作業')"
                    ))
                    await _sst.commit()
                    if _r_st.rowcount:
                        print(f"[startup] 專案狀態 8 階段化 backfill: {_r_st.rowcount} 筆")
        except Exception as _e_st_all:
            print(f"[startup] 專案狀態 backfill migration failed: {_e_st_all}")

    # ── DB Migration: crm_cost_line_templates table ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _ftpl = get_session_factory()
            if _ftpl:
                from sqlalchemy import text as _ttpl
                async with _ftpl() as _stpl:
                    await _stpl.execute(_ttpl("""
                        CREATE TABLE IF NOT EXISTS crm_cost_line_templates (
                            id VARCHAR(32) PRIMARY KEY,
                            name VARCHAR(128) NOT NULL,
                            items JSONB,
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await _stpl.commit()
        except Exception:
            pass

    # ── DB Migration: crm_project_cost_lines new columns ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _fcln = get_session_factory()
            if _fcln:
                from sqlalchemy import text as _tcln
                async with _fcln() as _scln:
                    for col_sql in [
                        "ALTER TABLE crm_project_cost_lines ADD COLUMN IF NOT EXISTS estimated_unit_price INTEGER",
                        "ALTER TABLE crm_project_cost_lines ADD COLUMN IF NOT EXISTS estimated_quantity INTEGER",
                        "ALTER TABLE crm_project_cost_lines ADD COLUMN IF NOT EXISTS actual_unit_price INTEGER",
                        "ALTER TABLE crm_project_cost_lines ADD COLUMN IF NOT EXISTS actual_quantity INTEGER",
                        "ALTER TABLE crm_project_cost_lines ADD COLUMN IF NOT EXISTS estimated_unit_type VARCHAR(16)",
                        "ALTER TABLE crm_project_cost_lines ADD COLUMN IF NOT EXISTS actual_unit_type VARCHAR(16)",
                    ]:
                        try:
                            await _scln.execute(_tcln(col_sql))
                            await _scln.commit()
                        except Exception:
                            await _scln.rollback()
        except Exception:
            pass

    # ── DB Migration: crm_staff resume columns + crm_staff_portfolio table ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _fres = get_session_factory()
            if _fres:
                from sqlalchemy import text as _tres
                async with _fres() as _sres:
                    for col_sql in [
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS photo_url VARCHAR(512)",
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS bio TEXT",
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS skills JSONB",
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS education JSONB",
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS experience JSONB",
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS awards JSONB",
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS resume_visible BOOLEAN DEFAULT FALSE",
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS edit_token VARCHAR(512)",
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS resume_editable BOOLEAN DEFAULT TRUE",
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS created_via VARCHAR(20) DEFAULT 'admin'",
                        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS created_for_project_id VARCHAR(32)",
                    ]:
                        try:
                            await _sres.execute(_tres(col_sql))
                            await _sres.commit()
                        except Exception:
                            await _sres.rollback()
                    # Create portfolio table
                    await _sres.execute(_tres("""
                        CREATE TABLE IF NOT EXISTS crm_staff_portfolio (
                            id VARCHAR(32) PRIMARY KEY,
                            staff_id VARCHAR(32) NOT NULL,
                            title VARCHAR(256) NOT NULL,
                            url VARCHAR(512) NOT NULL,
                            thumbnail_url VARCHAR(512),
                            role_desc VARCHAR(256),
                            sort_order INTEGER DEFAULT 0,
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await _sres.commit()
                    # Index on staff_id
                    try:
                        await _sres.execute(_tres(
                            "CREATE INDEX IF NOT EXISTS idx_portfolio_staff_id ON crm_staff_portfolio (staff_id)"
                        ))
                        await _sres.commit()
                    except Exception:
                        await _sres.rollback()
                    # Create project showcase table
                    await _sres.execute(_tres("""
                        CREATE TABLE IF NOT EXISTS crm_project_showcase (
                            id VARCHAR(32) PRIMARY KEY,
                            cover_url VARCHAR(512),
                            description TEXT,
                            video_url VARCHAR(512),
                            gallery JSONB,
                            process_mode VARCHAR(16) NOT NULL DEFAULT 'gallery',
                            process_items JSONB,
                            credits JSONB,
                            slug VARCHAR(128) UNIQUE,
                            published BOOLEAN NOT NULL DEFAULT FALSE,
                            published_at TIMESTAMPTZ,
                            edit_token VARCHAR(512),
                            editable BOOLEAN NOT NULL DEFAULT TRUE,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await _sres.commit()
                    try:
                        await _sres.execute(_tres(
                            "CREATE UNIQUE INDEX IF NOT EXISTS idx_showcase_slug ON crm_project_showcase (slug) WHERE slug IS NOT NULL AND slug != ''"
                        ))
                        await _sres.commit()
                    except Exception:
                        await _sres.rollback()
                    # AI 參考資料上傳（製作人上傳文件抽文字 + 補充說明 → 餵 AI）
                    for col_sql in [
                        "ALTER TABLE crm_project_showcase ADD COLUMN IF NOT EXISTS ai_reference_files JSONB",
                        "ALTER TABLE crm_project_showcase ADD COLUMN IF NOT EXISTS ai_reference_notes TEXT",
                    ]:
                        try:
                            await _sres.execute(_tres(col_sql))
                            await _sres.commit()
                        except Exception:
                            await _sres.rollback()
        except Exception:
            pass

    # ── Phase M: Website migrations (ALTER crm_projects/crm_staff + 5 new tables) + seed ──
    if state.db_online:
        try:
            from db.session import get_session_factory
            _f_web = get_session_factory()
            if _f_web:
                from db.migrations_website import run_website_migrations
                from db.seed_website import seed_website_if_empty
                await run_website_migrations(_f_web)
                await seed_website_if_empty(_f_web)
                # AI SEO runner 排程 loop（每 60s 檢查 cron 是否到期）
                from services.website import seo_runner, post_seo_runner, translation_service, backup_service, social_runner
                seo_runner.start_scheduler_task()        # 作品集
                post_seo_runner.start_scheduler_task()   # 文章（影像專欄）
                translation_service.start_scheduler_task()  # 英文翻譯（transcreation）
                backup_service.start_scheduler_task()    # 資料備份 → Google Drive（只在有 NAS key 的 master 跑）
                social_runner.start_scheduler_task()     # 社群文稿（Phase N-soc；只在有 claude 的 master 跑）
                from services import intel_runner
                intel_runner.start_scheduler_task()       # 產業情報（P-c；只在有 claude 的 master 跑）
                from services.website import watchdog_runner
                watchdog_runner.start_scheduler_task()   # 站點守衛（多 UA 篡改探測 + GSC 收錄；只在有 website/ 的 master 跑）
        except Exception as _e_web:
            print(f"[startup] Website migration/seed failed: {_e_web}")

    # ── Phase J-5: crm_project_cost_groups table + backfill 主表 ──
    if state.db_online:
        try:
            import uuid as _uuid_cg
            from db.session import get_session_factory
            _fcg = get_session_factory()
            if _fcg:
                from sqlalchemy import text as _tcg
                async with _fcg() as _scg:
                    # 1. 建新表
                    await _scg.execute(_tcg("""
                        CREATE TABLE IF NOT EXISTS crm_project_cost_groups (
                            id VARCHAR(32) PRIMARY KEY,
                            project_id VARCHAR(32) NOT NULL,
                            name VARCHAR(128) NOT NULL,
                            shoot_date TIMESTAMPTZ,
                            notes TEXT,
                            sort_order INTEGER NOT NULL DEFAULT 0,
                            budget_amount INTEGER,
                            misc_budget_amount INTEGER,
                            profit_target_pct INTEGER,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await _scg.execute(_tcg(
                        "CREATE INDEX IF NOT EXISTS idx_costgroup_project "
                        "ON crm_project_cost_groups(project_id, sort_order)"
                    ))
                    # 2. cost_lines + expenses 加 cost_group_id 欄位
                    for col_sql in [
                        "ALTER TABLE crm_project_cost_lines ADD COLUMN IF NOT EXISTS cost_group_id VARCHAR(32)",
                        "ALTER TABLE crm_project_expenses  ADD COLUMN IF NOT EXISTS cost_group_id VARCHAR(32)",
                        "CREATE INDEX IF NOT EXISTS idx_cl_group  ON crm_project_cost_lines(cost_group_id)",
                        "CREATE INDEX IF NOT EXISTS idx_exp_group ON crm_project_expenses(cost_group_id)",
                    ]:
                        try:
                            await _scg.execute(_tcg(col_sql))
                        except Exception:
                            pass
                    await _scg.commit()
                    # 3. 只迭代尚未有 cost_group 的專案（migration 完成後此 query 通常回 0 筆）
                    pending = (await _scg.execute(_tcg(
                        "SELECT id FROM crm_projects WHERE id NOT IN "
                        "(SELECT DISTINCT project_id FROM crm_project_cost_groups "
                        " WHERE project_id IS NOT NULL)"
                    ))).fetchall()
                    for (pid,) in pending:
                        try:
                            gid = _uuid_cg.uuid4().hex
                            await _scg.execute(_tcg(
                                "INSERT INTO crm_project_cost_groups (id, project_id, name, sort_order) "
                                "VALUES (:id, :pid, '主表', 0)"
                            ), {"id": gid, "pid": pid})
                            await _scg.execute(_tcg(
                                "UPDATE crm_project_cost_lines SET cost_group_id = :gid "
                                "WHERE project_id = :pid AND cost_group_id IS NULL"
                            ), {"gid": gid, "pid": pid})
                            await _scg.execute(_tcg(
                                "UPDATE crm_project_expenses SET cost_group_id = :gid "
                                "WHERE project_id = :pid AND cost_group_id IS NULL"
                            ), {"gid": gid, "pid": pid})
                            await _scg.commit()
                        except Exception as _e_pid:
                            await _scg.rollback()
                            print(f"[startup] cost_groups backfill for project {pid} failed: {_e_pid}")
        except Exception as _e_cg:
            print(f"[startup] cost_groups migration failed: {_e_cg}")

    # ── 財務管理階段二：科目/對映/銀行帳戶/對帳/調整表 ──
    # 新表（finance_accounts / finance_category_map / bank_accounts /
    # bank_reconciliations / finance_adjustments，及階段四的 finance_loans /
    # finance_loan_payments）由 init_db 的 Base.metadata.create_all 建；
    # 這裡補既有表新欄位 + 冪等種子（階段四貸款對映也在種子清單內）。
    if state.db_online:
        try:
            from db.session import get_session_factory
            _ffin = get_session_factory()
            if _ffin:
                from sqlalchemy import text as _tfin
                async with _ffin() as _sfin:
                    for col_sql in [
                        # 收支明細掛銀行帳戶 + AP 硬連結（→ crm_payment_requests）
                        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS bank_account_id VARCHAR(32)",
                        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS payment_request_id VARCHAR(32)",
                        # 貸款繳款硬連結（→ finance_loan_payments；treatment=loan 不進損益，階段四）
                        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS loan_payment_id VARCHAR(32)",
                        "CREATE INDEX IF NOT EXISTS idx_cash_bank_account ON crm_cash_entries(bank_account_id)",
                        "CREATE INDEX IF NOT EXISTS idx_cash_payment_request ON crm_cash_entries(payment_request_id)",
                        # 發票收款日（AR 收現時間戳，收支關聯收款時自動回填）
                        "ALTER TABLE crm_invoices ADD COLUMN IF NOT EXISTS paid_date TIMESTAMPTZ",
                        # 器材除役日（折舊自該月停止）
                        "ALTER TABLE equipment ADD COLUMN IF NOT EXISTS retired_date TIMESTAMPTZ",
                        # 對帳工作台：一筆收支只能被一列對帳單明細認領（既有表補建；
                        # 新環境 create_all 隨 model 建）— 也是 matched_entry_id 查詢的索引
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_stmtline_matched_entry "
                        "ON bank_statement_lines(matched_entry_id) WHERE matched_entry_id IS NOT NULL",
                        # 已開立的電子發票檔（存磁碟絕對路徑；根目錄 settings.invoices_root）
                        "ALTER TABLE crm_invoices ADD COLUMN IF NOT EXISTS file_url VARCHAR(512)",
                        # 客戶下載電子發票的分享連結 token
                        "ALTER TABLE crm_invoices ADD COLUMN IF NOT EXISTS share_token VARCHAR(512)",
                        # 分享碼是免登入端點的查詢條件（索引命中，不讓匿名請求逼出全表掃描），
                        # 同時擋短碼碰撞。partial index：NULL 不算重複
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_share_token "
                        "ON crm_invoices (share_token) WHERE share_token IS NOT NULL",
                        # ── 兩本帳（公司實體）：錢流 7 表加 entity 欄
                        # （parent=母公司（預設）/mine=我的帳，docs/LEDGER_ENTITY_PLAN.md §1.1）
                        # 兩本帳 §8：專案的錢流歸屬（2026-08-24 解凍，專案/客戶共用、錢分帳）
                        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
                        # 兩本帳 §8 延伸：器材折舊歸屬（owner 私帳 123 項器材，階段 4）
                        "ALTER TABLE equipment ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
                        # 逐案損益明細（工項拆分 + 委外/稅費），見 db/models.CrmProject.ledger_detail
                        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS ledger_detail JSONB",
                        # 私帳案推送到專案管理（標「後期專案」），見 db/models.CrmProject.crm_pushed
                        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS crm_pushed INTEGER NOT NULL DEFAULT 0",
                        "ALTER TABLE crm_invoices ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
                        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
                        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
                        "ALTER TABLE bank_accounts ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
                        # 股東往來帳戶綁人員（2026-08-21）：綁了那位股東登入就看得到自己的往來
                        "ALTER TABLE bank_accounts ADD COLUMN IF NOT EXISTS staff_id VARCHAR(32)",
                        # 對帳單匯入規則的方向條件（2026-08-22）：
                        # 「薪資」兩側都有（收＝代收薪資、支＝代發薪資），
                        # 光靠關鍵字分不出來，要能限定方向。
                        "ALTER TABLE bank_import_rules ADD COLUMN IF NOT EXISTS only_direction INTEGER NOT NULL DEFAULT 0",
                        # 福委會登記的心得筆記（2026-08-21，非必填）
                        "ALTER TABLE hr_benefit_entries ADD COLUMN IF NOT EXISTS reflection TEXT",
                        # 福委會：年度活動（每人額度＋期間）與說明／附件
                        # （2026-08-21，docs/BENEFIT_POOL_PLAN.md §9）。
                        # 既有兩個池走預設值 shared ＝ 行為完全不變。
                        "ALTER TABLE hr_benefit_pools ADD COLUMN IF NOT EXISTS quota VARCHAR(16) NOT NULL DEFAULT 'shared'",
                        "ALTER TABLE hr_benefit_pools ADD COLUMN IF NOT EXISTS description TEXT",
                        "ALTER TABLE hr_benefit_pools ADD COLUMN IF NOT EXISTS attachments JSONB",
                        "ALTER TABLE hr_benefit_pools ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ",
                        "ALTER TABLE hr_benefit_pools ADD COLUMN IF NOT EXISTS valid_to TIMESTAMPTZ",
                        "CREATE INDEX IF NOT EXISTS idx_bank_acct_staff ON bank_accounts (staff_id)",
                        "ALTER TABLE finance_adjustments ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
                        "ALTER TABLE finance_loans ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
                        "ALTER TABLE finance_month_close ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
                        # （v1 曾用 'own' 當預設值，只存在於 dev DB，且從未 commit／發版。
                        #  2026-08-19 已確認 dev 七表 own=0、default 全是 'parent'，
                        #  prod 這根欄是本次才建、直接就是 'parent' —— 一次性的
                        #  SET DEFAULT / UPDATE own→parent 已無事可做，不留在啟動路徑上
                        #  每次開機空掃七張錢流表。）
                        # month_close 的 unique 從全域 month 改為 (entity, month) 複合
                        "ALTER TABLE finance_month_close DROP CONSTRAINT IF EXISTS finance_month_close_month_key",
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_month_close_entity_month "
                        "ON finance_month_close (entity, month)",
                    ]:
                        try:
                            await _sfin.execute(_tfin(col_sql))
                            await _sfin.commit()
                        except Exception:
                            await _sfin.rollback()
                # 種子：科目表 + category 對映（冪等 — 查無才 insert，不覆蓋後台調整）
                from db.seed_finance import seed_finance_stage2
                await seed_finance_stage2(_ffin)
        except Exception as _e_fin:
            print(f"[startup] finance stage2 migration/seed failed: {_e_fin}")

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
async def _short_invoice_file(code: str):
    """電子發票短網址：`/e/{12 碼}` → 檔案下載（免登入，憑證就是那串碼）。

    掛在根路徑是為了**短** —— 走 router 前綴會變成 /api/v1/crm/... 又長回去
    （owner 要求：原本整條 287 字元，現在約 49）。實作在
    routers/crm/invoice_files.py，這裡只是把根路徑接過去。

    註冊在 `app.mount("/")` 之前才會贏 —— StaticFiles 掛在根，順序決定誰接。
    """
    from routers.crm.invoice_files import serve_invoice_by_share_token
    return await serve_invoice_by_share_token(code)


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

os.makedirs("uploads", exist_ok=True)
import mimetypes as _mt  # 精簡 Python mimetypes 可能不認 .webp → StaticFiles 回 text/plain
_mt.add_type("image/webp", ".webp")
_mt.add_type("image/avif", ".avif")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
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
