"""System core endpoints: health, settings, status, jobs, models, control, version.

OTA/publish/download endpoints moved to api_ota.py.
Utility endpoints moved to api_utils.py.
All endpoint paths remain unchanged.
"""
import json
import os
import sys
import socket
import asyncio
import urllib.request
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore

import core.state as state  # type: ignore
from core.socket_mgr import sio  # type: ignore
from config import load_settings, save_settings  # type: ignore
from core.schemas import ListDirRequest, DownloadModelRequest  # type: ignore
from core_engine import is_junk_file, is_incomplete_output  # type: ignore

router = APIRouter()


def _check_admin(request: Request):
    """Check admin permission. No-op if auth module not available."""
    try:
        from core.auth import check_admin
        check_admin(request)
    except ImportError:
        # fail-closed：auth 模組載不進來是安全事件，不是「跳過檢查」（官網那邊 _common.py 同款回 503）
        raise HTTPException(status_code=503, detail="Auth module unavailable")


def _check_admin_or_module(request: Request, *module_keys: str):
    """管理員 OR 帳號有任一指定模組（同 `_check_admin` 的 fail-closed 姿勢）。"""
    try:
        from core.auth import check_admin_or_module
        return check_admin_or_module(request, *module_keys)
    except ImportError:
        raise HTTPException(status_code=503, detail="Auth module unavailable")


# ── /api/settings/save 的分流表（docs/RBAC_AUDIT.md §3.2「一把 admin 鎖擋住四條正常工作」）──
# 頂層鍵 → 非管理員可以憑哪幾把鑰匙寫它。同一組鑰匙的鍵可以同一包送（concurrency＋nas_paths），
# 表外的鍵（notifications／timesheet／jwt…）或跨組混包一律回到管理員。
_SETTINGS_SAVE_KEYS: dict[str, tuple[str, ...]] = {
    "staff_roles": ("crm_staff",),                  # 員工檔案「編輯職能選項」
    "company": ("crm_quotes", "crm_invoices"),      # 報價頁「公司資訊」
    "finance": ("crm_invoices",),                   # 現金流「固定成本」（finance.monthly_fixed_costs）
    "concurrency": ("projects",),                   # 專案總覽「系統參數」
    "nas_paths": ("projects",),                     # 專案總覽「NAS agents_dir」
}
# 頂層鍵底下只准非管理員碰的子鍵（沒列＝整塊都可以）：nas_paths 還放 ota_dir／web_report_dir／voice_dir，
# 給 projects 模組的只有機器清單那一格。
_SETTINGS_SAVE_SUBKEYS: dict[str, tuple[str, ...]] = {
    "nas_paths": ("agents_dir",),
    "finance": ("monthly_fixed_costs",),            # 其餘（margin_model.mine／baseline_month／bookkeeping_fee）是財務主人的東西
}
# 頂層鍵底下非管理員**不准碰**的子鍵：company 的 logo_path／seal_path 會被報價單渲染成 data URI（任意檔讀取），只有管理員上傳端點可以寫
_SETTINGS_SAVE_DENY_SUBKEYS: dict[str, tuple[str, ...]] = {
    "company": ("logo_path", "seal_path"),
}
# 每個頂層鍵該長什麼樣（沒列＝dict）：staff_roles 是清單；形狀不對就 400，不讓 save_settings 拿字串蓋掉整塊
_SETTINGS_SAVE_SHAPES: dict[str, type] = {"staff_roles": list}


def _settings_save_guard_keys(payload_keys) -> tuple[str, ...] | None:
    """這包設定要用哪幾把鑰匙守：全部頂層鍵都落在分流表**同一組**才回那組；
    空包、表外鍵、跨組混包都回 None（＝管理員）。純函式，方便測。"""
    keys = set(payload_keys)
    if not keys:
        return None
    grants = {_SETTINGS_SAVE_KEYS.get(k) for k in keys}
    if len(grants) != 1 or None in grants:
        return None
    return grants.pop()


def _settings_save_restrict(payload: dict, grant: tuple[str, ...]) -> dict:
    """非管理員的寫入只留分流表歸這組鑰匙的頂層鍵（防夾帶），有子鍵白名單的再往下過濾一層。"""
    out = {}
    for k, v in payload.items():
        if _SETTINGS_SAVE_KEYS.get(k) != grant:
            continue
        if not isinstance(v, _SETTINGS_SAVE_SHAPES.get(k, dict)):   # 形狀不對會讓 save_settings 整塊覆蓋（nas_paths="x" 打掉 ota_dir…）
            raise HTTPException(status_code=400, detail=f"{k} 的形狀不對")
        if isinstance(v, dict):
            allowed_sub = _SETTINGS_SAVE_SUBKEYS.get(k)
            if allowed_sub is not None:
                v = {sk: sv for sk, sv in v.items() if sk in allowed_sub}
            deny = _SETTINGS_SAVE_DENY_SUBKEYS.get(k, ())
            v = {sk: sv for sk, sv in v.items() if sk not in deny}
        out[k] = v
    return out


# ── Health & Settings ───────────────────────────────────────────────────

@router.get("/api/v1/health")
async def health_check():
    jobs = state.get_all_jobs()
    running = [j for j in jobs.values() if j.status == state.JobStatus.RUNNING]
    busy = len(running) > 0

    cpu_pct = 0.0
    mem_pct = 0.0
    try:
        import psutil  # type: ignore
        cpu_pct = psutil.cpu_percent(interval=None)
        mem_pct = psutil.virtual_memory().percent
    except Exception:
        pass

    from core.version import read_local_version
    ver = read_local_version(default="")

    current_tasks = [
        {"job_id": j.job_id, "project_name": j.project_name, "task_type": j.task_type}
        for j in running
    ]

    return {
        "status": "ok",
        "hostname": socket.gethostname(),
        "cpu_percent": cpu_pct,
        "memory_percent": mem_pct,
        "worker_busy": busy,
        "active_job_count": len(running),
        "current_tasks": current_tasks,
        "version": ver,
    }

# ⚠️ settings.json 內含機密：`jwt_secret` 能簽出 access_level=3 的 admin token（master 與
# NAS website-api 共用同一把，internal endpoint 的 X-Internal-Key 也是它），`database_url`
# 帶 Postgres 密碼。而 `/api/settings/load` 是**未認證**端點（前端 4 處在無 token 狀態下讀
# 它，不能改成要求認證），且 master 8000 經 Cloudflare tunnel 對外 —— 2026-07-10 實測
# `https://foundry.originsun-studio.com/api/settings/load` 從公網可讀到這兩個值。
# 因此輸出前一律抹除。新增機密欄位時務必同步加進這裡。
_SECRET_KEYS = ("jwt_secret", "database_url")
_SECRET_SUBKEYS = {"google_oauth": ("client_secret",), "google_calendar": ("service_account_json",)}
# 2026-09-08 稽核：這些不是「簽得出 admin」等級的機密，但也不該給匿名——工時同步 token 拿到就能對
# /timesheets/ingest 塞假工時（它本身是管理員限定端點）、webhook URL 拿到就能對團隊 Chat 灌訊息。
# 設定視窗（管理員、帶 token）要顯示它們才能編輯，所以只對**非管理員**抹。
_ADMIN_ONLY_SUBKEYS = {"timesheet": ("ingest_token",),
                       "notifications": ("google_chat_webhook", "alert_webhook", "custom_webhook_url", "line_notify_token")}


def _redact_settings(s: dict, *, admin: bool = False) -> dict:
    """回傳去機密的淺拷貝（不改動原 dict —— 它是 load_settings 的快取內容）。admin=False 再抹 _ADMIN_ONLY_SUBKEYS。"""
    out = {k: v for k, v in s.items() if k not in _SECRET_KEYS}
    rules = dict(_SECRET_SUBKEYS)
    if not admin:
        for parent, subs in _ADMIN_ONLY_SUBKEYS.items():
            rules[parent] = tuple(rules.get(parent, ())) + subs
    for parent, subs in rules.items():
        if isinstance(out.get(parent), dict):
            out[parent] = {k: v for k, v in out[parent].items() if k not in subs}
    return out


def _is_admin_request(request: Request) -> bool:
    from core.auth import _extract_token, payload_grants
    return payload_grants(_extract_token(request))   # 沒帶鑰匙＝純管理員判定（單一正本）


@router.get("/api/settings/load")
async def load_settings_api(request: Request):
    return _redact_settings(load_settings(), admin=_is_admin_request(request))

# ── 公司 Logo／印章上傳（報價單 PDF 用）──────────────────────────
# 存 repo 根目錄 company_assets/：不掛靜態（/uploads 是公開的，章不該無登入就抓得到）、gitignore、
# deploy 不 mirror-delete 所以發版不會被清掉。PDF 端讀 settings.company.<kind>_path（相對根目錄）。
_COMPANY_IMAGE_KINDS = ("logo", "seal")
_IMAGE_MAGIC = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpg", b"RIFF": "webp"}
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COMPANY_ASSETS_DIR = os.path.join(_REPO_ROOT, "company_assets")
_COMPANY_IMAGE_MAX_MB = 5


def _company_image_kind_or_404(kind: str) -> str:
    """路徑上的 {kind} 只認 logo／seal（上傳與取回同一個閘）。"""
    if kind not in _COMPANY_IMAGE_KINDS:
        raise HTTPException(status_code=404, detail="只有 logo／seal")
    return kind


def _image_ext(content: bytes) -> str:
    """看檔頭不看副檔名：png／jpg／webp 才收（章要去背，PNG 最常見）。"""
    for magic, ext in _IMAGE_MAGIC.items():
        if content.startswith(magic):
            if ext == "webp" and content[8:12] != b"WEBP":
                continue
            return ext
    raise HTTPException(status_code=400, detail="只收 PNG／JPG／WebP 圖檔")


@router.post("/api/settings/company-image/{kind}")
async def upload_company_image(kind: str, req: Request, file: UploadFile = File(...)):
    """上傳公司 Logo／印章（管理員）：看檔頭驗格式、同 kind 只留一份、路徑直接寫進 settings.company。"""
    _check_admin(req)
    _company_image_kind_or_404(kind)
    content = await file.read()
    if len(content) > _COMPANY_IMAGE_MAX_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"圖檔超過 {_COMPANY_IMAGE_MAX_MB}MB")
    ext = _image_ext(content)
    os.makedirs(_COMPANY_ASSETS_DIR, exist_ok=True)
    for old in os.listdir(_COMPANY_ASSETS_DIR):          # 同 kind 只留一份，換副檔名也不殘留
        if old.rsplit(".", 1)[0] == kind:
            try:
                os.remove(os.path.join(_COMPANY_ASSETS_DIR, old))
            except OSError:
                pass
    with open(os.path.join(_COMPANY_ASSETS_DIR, f"{kind}.{ext}"), "wb") as f:
        f.write(content)
    rel = f"company_assets/{kind}.{ext}"
    settings = load_settings()
    company = dict(settings.get("company") or {})
    company[f"{kind}_path"] = rel
    save_settings({**settings, "company": company})     # 直接寫進設定：上傳完下一張 PDF 就用新圖
    return {"status": "ok", "path": rel}


@router.get("/api/settings/company-image/{kind}")
async def get_company_image(kind: str, req: Request):
    """設定頁預覽用（管理員）；找不到就 404，前端顯示「尚未上傳」。"""
    from core.no_store import no_store_file
    _check_admin(req)
    _company_image_kind_or_404(kind)
    rel = (load_settings().get("company") or {}).get(f"{kind}_path") or ""
    path = rel if os.path.isabs(rel) else os.path.join(_REPO_ROOT, rel)
    if not rel or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="尚未上傳")
    return no_store_file(path)


@router.post("/api/settings/save")
async def save_settings_api(req: Request):
    """依 payload 的頂層鍵分流（`_SETTINGS_SAVE_KEYS`）：單一組的鍵給該組模組，
    其他一律管理員。非管理員只寫得進那組鍵（`_settings_save_restrict`）。"""
    try:
        body = await req.json()
    except Exception:
        body = None
    grant = _settings_save_guard_keys(body.keys()) if isinstance(body, dict) else None
    if grant is None:
        _check_admin(req)
    else:
        payload = _check_admin_or_module(req, *grant)
        from core.auth import payload_grants
        if not payload_grants(payload):          # 零 key＝只有管理員過：非管理員只寫得進這組鍵
            body = _settings_save_restrict(body, grant)
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"status": "error", "message": "設定內容需為 JSON 物件"})
    try:
        save_settings(body)
        return {"status": "success", "message": "設定已儲存"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


# ── 磁碟代號 ↔ UNC 對應（core/drive_map；備份進件翻譯用）──────

@router.get("/api/v1/drive_map")
async def get_drive_map():
    """有效對應（程式預設 + settings 覆寫）+ 預設表（前端標示哪些被改過）。"""
    from core.drive_map import DEFAULT_DRIVE_MAP, effective_map
    return {"map": effective_map(), "defaults": DEFAULT_DRIVE_MAP}


@router.post("/api/v1/drive_map")
async def save_drive_map(req: Request):
    """存完整期望表 {字母: UNC}（admin — 全軟體層級設定，入口在右上角選單）。
    與程式預設的差異存進 settings.json 的 drive_map（預設有但期望表沒有的
    字母 → 存空字串墓碑 = 停用），使有效對應恆等於期望表。"""
    _check_admin(req)
    from core.drive_map import DEFAULT_DRIVE_MAP
    body = await req.json()
    desired = body.get("drive_map")
    if not isinstance(desired, dict):
        return JSONResponse(status_code=400, content={"detail": "drive_map 需為 {字母: UNC} 物件"})
    clean: dict = {}
    for k, v in desired.items():
        letter = str(k).strip().rstrip(":").upper()
        unc = str(v or "").strip().rstrip("\\/")
        if not (len(letter) == 1 and letter.isalpha()):
            return JSONResponse(status_code=400, content={"detail": f"磁碟代號需為單一英文字母：{k!r}"})
        if not unc.startswith("\\\\"):
            return JSONResponse(status_code=400, content={"detail": f"{letter}: 的對應需為 \\\\ 開頭的 UNC 路徑"})
        clean[letter] = unc
    # save_settings 是深度合併（merge-on-save）— 單純寫新表清不掉舊 override 的字母。
    # 墓碑要涵蓋「程式預設 ∪ 既有 override」中所有不在期望表的字母，有效表才恆等於期望表。
    current = load_settings().get("drive_map") or {}
    known = set(DEFAULT_DRIVE_MAP)
    for k in current:
        letter = str(k).strip().rstrip(":").upper()
        if len(letter) == 1 and letter.isalpha():
            known.add(letter)
    override = dict(clean)
    for letter in known:
        if letter not in clean:
            override[letter] = ""   # 墓碑：停用該字母
    save_settings({"drive_map": override})
    return {"status": "success", "map": clean}

@router.post("/api/v1/internal/alert_email")
async def internal_alert_email(request: Request):
    """把 🔴 級告警寄成 email。呼叫者＝任一節點的 `notifier._relay_alert_email`。

    認證：`X-Internal-Key` = JWT secret（與 /internal/seo/run 同一套）。
    只有 master 會被打（notifier 送往 settings.master_server）——SMTP 帳密與收件人存在
    `website_settings`（NAS Postgres），機隊 7 台因此不需要持有寄信憑證。
    收件人：`notify.alert_email_to` 優先，沒設才退 `notify.email_to`（聯絡表單那個信箱）。
    """
    from core.auth import _get_secret
    expected = _get_secret()
    if not expected or request.headers.get("X-Internal-Key", "") != expected:
        return JSONResponse(status_code=403, content={"detail": "forbidden"})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "invalid json"})
    subject = (body.get("subject") or "Originsun 系統告警").strip()
    text = (body.get("body") or "").strip()
    if not text:
        return JSONResponse(status_code=400, content={"detail": "empty body"})

    # lazy import：機隊精簡 agent 沒裝 sqlalchemy，這支端點在它們身上不會被呼叫到
    from db.session import get_session_factory
    from services.website.notify_service import _parse_recipients, _smtp_send
    from services.website.settings_service import get_all_settings

    factory = get_session_factory()
    if factory is None:
        return JSONResponse(status_code=503, content={"detail": "db not ready"})
    async with factory() as session:
        settings = await get_all_settings(session)

    raw = settings.get("notify.alert_email_to") or settings.get("notify.email_to") or ""
    to_list = _parse_recipients(raw)
    if not to_list:
        return {"sent": 0, "detail": "no recipients"}
    try:
        await asyncio.to_thread(_smtp_send, settings, to_list, subject, text)
    except Exception as e:  # SMTP 失敗不該把呼叫端的告警路徑一起拖垮
        return JSONResponse(status_code=502, content={"sent": 0, "error": str(e)[:200]})
    return {"sent": len(to_list)}


# ── Directory listing ───────────────────────────────────────────────────

@router.post("/api/v1/list_dir")
async def list_dir_api(req: ListDirRequest):
    # 磁碟代號→UNC：掃描來源常填 T:\ 等網路磁碟代號，本機不一定有掛
    from core.drive_map import make_translator
    req.path = make_translator()(req.path)
    exts = {e.lower() for e in req.exts}
    if os.path.isfile(req.path):
        if os.path.splitext(req.path)[1].lower() in exts:
            return {"files": [req.path.replace("\\", "/")], "count": 1}
        return {"files": [], "error": f"檔案不支援: {req.path}"}
    if not os.path.isdir(req.path): return {"files": [], "error": f"目錄不存在: {req.path}"}
    # 🔴 垃圾檔與「還沒完成的產出」不該出現在任何影片清單裡 —— 前端拿這份
    # 清單當「已經有什麼」的依據（失聯重派靠它算缺件）。0 byte 的殼若被算成
    # 「已產出」，正好就是我們想補的那支不會被補到。
    files = []
    for root, _, fnames in os.walk(req.path):
        for fname in sorted(fnames):
            # 先過便宜的副檔名，再做要 stat 的完整性檢查（SMB 上每次都是往返）
            if os.path.splitext(fname)[1].lower() not in exts:
                continue
            if is_junk_file(fname):
                continue
            full = os.path.join(root, fname)
            if is_incomplete_output(full):
                continue
            files.append(full.replace("\\", "/"))
    return {"files": files, "count": len(files)}


# ── Model management ────────────────────────────────────────────────────

@router.get("/api/v1/models/status")
async def get_models_status():
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"
    sizes = ["turbo", "large-v3", "medium", "small", "base", "tiny"]
    models_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
    os.makedirs(models_dir, exist_ok=True)
    status = {}
    try:
        from faster_whisper import download_model  # type: ignore
        for size in sizes:
            try:
                path = download_model(size, cache_dir=models_dir, local_files_only=True)
                status[size] = True if path else False
            except Exception: status[size] = False
    except ImportError: pass
    return {"status": status}

@router.post("/api/v1/models/download")
async def download_model_endpoint(req: DownloadModelRequest, background_tasks: BackgroundTasks):
    async def _download_task(size: str):
        import subprocess
        await sio.emit('log', {'type': 'system', 'msg': f'⏳ 開始在背景為您下載模型: {size} ... (需時數分鐘，請勿關機)'})
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        downloader = os.path.join(base_dir, "download_model.py")
        loop = asyncio.get_running_loop()
        # Selector-loop-safe: Popen in a worker thread + stream progress lines
        # back onto the loop (asyncio subprocess is unavailable on Windows
        # SelectorEventLoop). stderr merged into stdout so either stream shows.
        def _run_blocking() -> int:
            CNW = 0x08000000 if sys.platform == "win32" else 0
            p = subprocess.Popen(
                [sys.executable, downloader, size],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace',
                creationflags=CNW,
            )
            for line in p.stdout:
                text = line.strip()
                if text and ("MB/" in text or "%" in text or "Downloading" in text or text.startswith("[")):
                    asyncio.run_coroutine_threadsafe(
                        sio.emit('log', {'type': 'system', 'msg': text}), loop)
            p.wait()
            return p.returncode
        rc = await asyncio.to_thread(_run_blocking)
        if rc == 0:
            await sio.emit('log', {'type': 'system', 'msg': f'🎉 模型 {size} 下載並部署完成！'})
            await sio.emit('model_download_done', {'model_size': size})
        else:
            await sio.emit('log', {'type': 'error', 'msg': f'❌ 模型 {size} 下載發生錯誤，這可能是網路異常。'})
            await sio.emit('model_download_error', {'model_size': size})
    background_tasks.add_task(_download_task, req.model_size)
    return {"status": "started", "model_size": req.model_size}


# ── Job control ─────────────────────────────────────────────────────────

@router.post("/api/v1/control/pause")
async def pause_job(job_id: str = ""):
    if job_id:
        job = state.get_job(job_id)
        if not job:
            return JSONResponse(status_code=404, content={"error": "Job not found"})
        if job.engine:
            job.engine.request_pause()
        if job.report_pause_event:
            job.report_pause_event.clear()
        job.status = state.JobStatus.PAUSED
        return {"status": "paused", "job_id": job_id}
    # No job_id: pause ALL running jobs (backward compat)
    for j in state.get_all_jobs().values():
        if j.status == state.JobStatus.RUNNING:
            if j.engine:
                j.engine.request_pause()
            if j.report_pause_event:
                j.report_pause_event.clear()
            j.status = state.JobStatus.PAUSED
    return {"status": "paused"}

@router.post("/api/v1/control/resume")
async def resume_job(job_id: str = ""):
    if job_id:
        job = state.get_job(job_id)
        if not job:
            return JSONResponse(status_code=404, content={"error": "Job not found"})
        if job.engine:
            job.engine.request_resume()
        if job.report_pause_event:
            job.report_pause_event.set()
        job.status = state.JobStatus.RUNNING
        return {"status": "resumed", "job_id": job_id}
    # No job_id: resume ALL paused jobs (backward compat)
    for j in state.get_all_jobs().values():
        if j.status == state.JobStatus.PAUSED:
            if j.engine:
                j.engine.request_resume()
            if j.report_pause_event:
                j.report_pause_event.set()
            j.status = state.JobStatus.RUNNING
    return {"status": "resumed"}

@router.post("/api/v1/control/stop")
async def stop_job(job_id: str = ""):
    if job_id:
        job = state.get_job(job_id)
        if not job:
            return JSONResponse(status_code=404, content={"error": "Job not found"})
        if job.engine:
            job.engine.request_stop()
        if job.asyncio_task and not job.asyncio_task.done():
            job.asyncio_task.cancel()
        job.status = state.JobStatus.CANCELLED
        return {"status": "stopped", "job_id": job_id}
    # No job_id: stop ALL jobs (running + paused + queued)
    for j in list(state.get_all_jobs().values()):
        if j.status in (state.JobStatus.RUNNING, state.JobStatus.PAUSED):
            if j.engine:
                j.engine.request_stop()
            if j.asyncio_task and not j.asyncio_task.done():
                j.asyncio_task.cancel()
        if j.status in (state.JobStatus.RUNNING, state.JobStatus.PAUSED,
                         state.JobStatus.QUEUED, state.JobStatus.WAITING):
            j.status = state.JobStatus.CANCELLED
    # 重置所有活躍 slot 計數，防止 slot 被永久佔用
    state._active_counts.clear()
    return {"status": "stopped"}


# ── Status & Jobs ───────────────────────────────────────────────────────

@router.get("/api/v1/status")
async def get_status(log_offset: int = 0):
    jobs = state.get_all_jobs()
    active_statuses = (
        state.JobStatus.QUEUED, state.JobStatus.WAITING,
        state.JobStatus.RUNNING, state.JobStatus.PAUSED,
    )
    active_jobs = {}
    for jid, j in jobs.items():
        if j.status in active_statuses:
            active_jobs[jid] = {
                "job_id": j.job_id,
                "project_name": j.project_name,
                "task_type": j.task_type,
                "status": j.status.value,
                "progress": j.progress,
                "created_at": j.created_at,
                "started_at": j.started_at,
            }

    busy = any(j.status == state.JobStatus.RUNNING for j in jobs.values())
    paused = any(j.status == state.JobStatus.PAUSED for j in jobs.values())
    queue_len = sum(1 for j in jobs.values() if j.status in (state.JobStatus.QUEUED, state.JobStatus.WAITING))

    logs = list(state._global_log_buffer)[log_offset:]
    return {
        "status": "online",
        "busy": busy,
        "queue_length": queue_len,
        "paused": paused,
        "logs": logs,
        "new_log_offset": log_offset + len(logs),
        "progress": None,  # deprecated; use active_jobs[id].progress
        "active_jobs": active_jobs,
    }

@router.get("/api/v1/jobs/{job_id}")
async def get_job_detail(job_id: str):
    job = state.get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    return {
        "job_id": job.job_id,
        "project_name": job.project_name,
        "task_type": job.task_type,
        "status": job.status.value,
        "progress": job.progress,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error_detail": job.error_detail,
    }

@router.get("/api/v1/jobs/{job_id}/logs")
async def get_job_logs(job_id: str, offset: int = 0):
    job = state.get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})
    logs = job.log_buffer[offset:]
    return {"logs": logs, "new_offset": offset + len(logs)}


# ── Version ─────────────────────────────────────────────────────────────

@router.get("/api/v1/version")
async def get_version():
    from core.version import read_version_json
    return read_version_json() or {"version": "0.0.0", "build_date": "Unknown",
                                   "error": "version.json not found"}

@router.get("/api/v1/nas_version")
async def get_nas_version():
    """Fetch the latest version from the master server over HTTP."""
    master = load_settings().get("master_server", "")
    if not master:
        # 未設定 master_server = 本機就是主控端，直接回傳自己的版號
        return await get_version()
    try:
        url = f"{master.rstrip('/')}/api/v1/version"
        def _fetch():
            req = urllib.request.Request(url, headers={"User-Agent": "OriginsunAgent/1.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read().decode())
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        return {"version": "unknown", "error": str(e)}


# ── Restart endpoint (delegates spawn to core.process_spawn) ──────

@router.post("/api/v1/system/restart")
async def system_restart(request: Request):
    """Restart endpoint — delegates to core.process_spawn helper which
    spawns a detached restart sequence (kill port + OTA + rotate + uvicorn)."""
    # 內部金鑰跟著 OTA 包發到每台機器，不能當成只有機隊知道：經 cloudflared 進來的一律擋（2026-09-08 稽核）
    from core.auth import via_cloudflare
    if via_cloudflare(request):
        return JSONResponse({"detail": "Unauthorized"}, 403)
    key = request.headers.get("X-Internal-Key", "")
    if key != "originsun-internal-restart":
        client_ip = request.client.host if request.client else ""
        if client_ip not in ("127.0.0.1", "::1", "localhost"):
            return JSONResponse({"detail": "Unauthorized"}, 403)

    from core.process_spawn import trigger_detached_restart
    trigger_detached_restart(run_ota=True)
    asyncio.get_running_loop().call_later(1.0, os._exit, 0)
    return {"status": "updating"}
