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
# BaseHTTPMiddleware removed — it buffers streaming responses (breaks SSE)

from core.socket_mgr import sio  # type: ignore
import core.state as state  # type: ignore

# ── Router 容錯載入（缺模組時跳過該 router，不 crash）──
_ROUTER_MODULES = [
    'api_auth', 'api_roles',
    'api_backup', 'api_verify', 'api_proxy', 'api_concat',
    'api_report', 'api_transcribe', 'api_system', 'api_ota', 'api_utils', 'api_tts',
    'api_job_history', 'api_queue', 'api_schedules', 'api_agents',
    'api_api_keys',
    'api_crm',
    'api_drone_meta',
    'api_drone_watcher',
]
_routers = {}
for _mod_name in _ROUTER_MODULES:
    try:
        _mod = __import__(f'routers.{_mod_name}', fromlist=['router'])
        _routers[_mod_name] = _mod
    except Exception as _e:
        print(f'[WARN] Router {_mod_name} 載入失敗，已跳過: {_e}')

app = FastAPI(title="Originsun Media Guard Web API")

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

# [DEV BRIDGE — Phase M] 讓 Windows main.py 同時服務 /api/website/*，使官網管理
# Tab 在透過 Cloudflare Tunnel (foundry.originsun-studio.com) 存取時也能走同源
# fetch。瀏覽器從外部 origin 連不到 main_website.py:8001（localhost 指向用戶端
# 機器；HTTPS 頁面也無法 fetch HTTP 資源）。M-F NAS 部署完成後移除此區塊，
# website routers 應只跑在 NAS website-api container。
try:
    from routers.website import router as _website_router
    app.include_router(_website_router)

    @app.get("/healthz")
    async def _website_healthz():
        return {"ok": True, "service": "main.py [dev bridge]"}

    print("[DEV BRIDGE] routers/website mounted on main.py (remove after M-F)")
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
            f'"{sys_python}" -m uvicorn main:io_app --host 0.0.0.0 --port 8000 > "{outLog}" 2> "{errLog}"',
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
                    _all_mods_sql = _json_rbac.dumps([
                        "backup", "verify", "transcode", "concat", "report",
                        "transcribe", "tts", "drone_meta", "projects",
                        "crm_clients", "crm_projects", "crm_quotes",
                        "crm_staff", "crm_invoices", "website_admin",
                    ])
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
                    ]
                    for tbl, col, coltype in _crm_cols:
                        try:
                            await _s.execute(_t(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col} {coltype}"))
                            await _s.commit()
                        except Exception:
                            await _s.rollback()
        except Exception:
            pass
    # ── DB Migration: CRM performance indexes ──
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
                    ]:
                        try:
                            await _si.execute(_ti(idx_sql))
                            await _si.commit()
                        except Exception:
                            await _si.rollback()
        except Exception:
            pass

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
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS sub_item VARCHAR(128)",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS payee VARCHAR(64)",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS advance_id VARCHAR(32)",
                        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
                        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS advance_by VARCHAR(64)",
                        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS is_advance INTEGER DEFAULT 0",
                        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS advance_returned INTEGER DEFAULT 0",
                        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS advance_payment_id VARCHAR(32)",
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
                    ]:
                        try:
                            await _sex.execute(_tex(col_sql))
                            await _sex.commit()
                        except Exception:
                            await _sex.rollback()
        except Exception:
            pass

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
                from services.website import seo_runner
                seo_runner.start_scheduler_task()
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

os.makedirs("uploads", exist_ok=True)
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
    uvicorn.run(io_app, host="0.0.0.0", port=port, log_level="error")
