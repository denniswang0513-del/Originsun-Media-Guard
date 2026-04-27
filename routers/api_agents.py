"""
api_agents.py — 機器管理 API（DB 優先，JSON fallback）
Endpoints:
  GET    /api/v1/agents              — 列出所有機器
  POST   /api/v1/agents              — 新增機器
  DELETE /api/v1/agents/{id}         — 移除機器
  GET    /api/v1/agents/{id}/health  — Proxy 取得遠端機器健康狀態
"""
import os
import json
import re
import asyncio
import urllib.request
import urllib.error
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from config import load_settings
import core.state as state

router = APIRouter(prefix="/api/v1", tags=["Agents"])


def _check_admin_agents(request):
    """Check admin permission. No-op if auth module not available."""
    try:
        from core.auth import check_admin
        check_admin(request)
    except ImportError:
        pass


def _find_agent_sync(agent_id: str) -> dict:
    """Find agent by ID from NAS JSON or local JSON. Raises 404 if not found."""
    nas_dir = _get_nas_agents_dir()
    agents = _load_agents_json(nas_dir)
    agent = next((a for a in agents if a.get("id") == agent_id), None)
    if not agent:
        raise HTTPException(status_code=404, detail=f"找不到 ID 為 {agent_id} 的機器")
    return agent


async def _find_agent(agent_id: str) -> dict:
    """Find agent by ID from DB (preferred) or JSON fallback. Raises 404 if not found."""
    # Try DB first
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import agents_repo
                async with factory() as session:
                    agent = await agents_repo.get(session, agent_id)
                    if agent:
                        return agent
        except Exception:
            pass
    # Fallback to JSON
    return _find_agent_sync(agent_id)


# ─── JSON Fallback Helpers (保留原有邏輯) ─────────────────

def _get_nas_agents_dir() -> str:
    return load_settings().get("nas_paths", {}).get("agents_dir", "")


def _agents_file(nas_dir: str) -> str:
    return os.path.join(nas_dir, "agents.json")


def _load_agents_json(nas_dir: str) -> list:
    if not nas_dir:
        return []
    f = _agents_file(nas_dir)
    if not os.path.exists(f):
        return []
    try:
        with open(f, encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return []


def _save_agents_json(nas_dir: str, agents: list):
    os.makedirs(nas_dir, exist_ok=True)
    with open(_agents_file(nas_dir), "w", encoding="utf-8") as fp:
        json.dump(agents, fp, ensure_ascii=False, indent=2)


def _make_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "agent"


async def _sync_db_to_nas(session, repo):
    """Best-effort: dump DB agents to NAS JSON as backup."""
    try:
        all_agents = await repo.list_all(session)
        nas_dir = _get_nas_agents_dir()
        if nas_dir:
            _save_agents_json(nas_dir, all_agents)
    except Exception:
        pass  # Non-critical — NAS JSON is just a backup


# ─── Schemas ──────────────────────────────────────────────

class NewAgentRequest(BaseModel):
    name: str
    url: str


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    url: str | None = None


# ─── Endpoints ────────────────────────────────────────────

@router.get("/agents")
async def list_agents():
    """DB is the single source of truth. JSON fallback when DB offline."""
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import agents_repo
                async with factory() as session:
                    db_agents = await agents_repo.list_all(session)
                    # Auto-migrate: if DB empty but NAS JSON has data, import once
                    if not db_agents:
                        nas_dir = _get_nas_agents_dir()
                        nas_agents = _load_agents_json(nas_dir)
                        if nas_agents:
                            for a in nas_agents:
                                await agents_repo.add(session, a["id"], a["name"], a["url"])
                            await session.commit()
                            db_agents = await agents_repo.list_all(session)
                    return {"agents": db_agents, "nas_configured": True, "source": "db"}
        except Exception:
            pass
    # Fallback to JSON when DB offline
    nas_dir = _get_nas_agents_dir()
    agents = _load_agents_json(nas_dir)
    return {"agents": agents, "nas_configured": bool(nas_dir), "source": "json"}


@router.post("/agents")
async def add_agent(req: NewAgentRequest, request: Request):
    _check_admin_agents(request)
    url_clean = req.url.rstrip("/")

    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import agents_repo
                async with factory() as session:
                    if await agents_repo.url_exists(session, url_clean):
                        raise HTTPException(status_code=409, detail=f"已存在相同 URL 的機器：{url_clean}")
                    agent_id = _make_id(req.name)
                    # 檢查 ID 唯一性
                    existing = await agents_repo.list_all(session)
                    existing_ids = {a["id"] for a in existing}
                    counter = 1
                    base_id = agent_id
                    while agent_id in existing_ids:
                        counter += 1
                        agent_id = f"{base_id}_{counter}"
                    await agents_repo.add(session, agent_id, req.name, url_clean)
                    await session.commit()
                    new_agent = {"id": agent_id, "name": req.name, "url": url_clean}
                    # Sync to NAS JSON as backup
                    await _sync_db_to_nas(session, agents_repo)
                    return {"status": "ok", "agent": new_agent}
        except HTTPException:
            raise
        except Exception:
            pass

    # Fallback to JSON
    nas_dir = _get_nas_agents_dir()
    if not nas_dir:
        raise HTTPException(status_code=400, detail="尚未設定 nas_paths.agents_dir，請先在系統參數設定 NAS 路徑。")
    agents = _load_agents_json(nas_dir)
    if any(a.get("url", "").rstrip("/") == url_clean for a in agents):
        raise HTTPException(status_code=409, detail=f"已存在相同 URL 的機器：{url_clean}")
    base_id = _make_id(req.name)
    agent_id = base_id
    existing_ids = {a.get("id", "") for a in agents}
    counter = 1
    while agent_id in existing_ids:
        counter += 1
        agent_id = f"{base_id}_{counter}"
    new_agent = {"id": agent_id, "name": req.name, "url": url_clean}
    agents.append(new_agent)
    _save_agents_json(nas_dir, agents)
    return {"status": "ok", "agent": new_agent}


@router.get("/agents/{agent_id}/health")
async def proxy_agent_health(agent_id: str):
    """Proxy health check — avoids browser CORS/Private Network issues."""
    agent = await _find_agent(agent_id)
    url = agent.get("url", "").rstrip("/") + "/api/v1/health"

    def _fetch_health():
        try:
            r = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(r, timeout=4) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return {"status": "offline"}

    return await asyncio.to_thread(_fetch_health)


@router.post("/agents/{agent_id}/update")
async def trigger_agent_update(agent_id: str, request: Request, force: bool = False):
    """Trigger OTA update on a remote Agent.
    Uses /api/admin/restart which calls start_hidden.vbs → update_agent.py → uvicorn.
    This works on ALL agent versions because start_hidden.vbs triggers the full update cycle.

    Pre-flight busy check: refuses to push if the agent has an active job
    (drone transcode of 100+ files takes hours; OTA mid-job kills work
    and watcher's MAX_* skip logic then marks the folder as 'done' even
    though it's incomplete). Pass `?force=true` to override.
    """
    _check_admin_agents(request)
    agent = await _find_agent(agent_id)
    base_url = agent.get("url", "").rstrip("/")

    if not force:
        def _fetch_status():
            try:
                with urllib.request.urlopen(base_url + "/api/v1/status", timeout=5) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception:
                return None  # unreachable / parse error → don't gate
        status = await asyncio.to_thread(_fetch_status)
        if status is not None:
            busy = bool(status.get("busy")) or int(status.get("queue_length", 0) or 0) > 0
            active = status.get("active_jobs") or {}
            if busy or active:
                return JSONResponse(status_code=409, content={
                    "status": "busy",
                    "agent_id": agent_id,
                    "agent_name": agent.get("name", agent_id),
                    "busy": bool(status.get("busy")),
                    "queue_length": int(status.get("queue_length", 0) or 0),
                    "active_jobs": [
                        {"task_type": v.get("task_type"),
                         "project_name": v.get("project_name")}
                        for v in active.values() if isinstance(v, dict)
                    ],
                    "hint": "加 ?force=true 強制推送（會中斷正在跑的任務）",
                })

    def _trigger():
        # Try multiple restart endpoints (newest first, then fallbacks for old agents)
        for endpoint, headers in [
            ("/api/v1/internal/restart", {"Content-Type": "application/json",
                                           "X-Internal-Key": "originsun-internal-restart"}),
            ("/api/v1/system/restart", {"Content-Type": "application/json",
                                         "X-Internal-Key": "originsun-internal-restart"}),
        ]:
            try:
                url = base_url + endpoint
                req = urllib.request.Request(url, method="POST", data=b'{}', headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (404, 405):
                    continue  # Endpoint not available, try next
                return {"status": "updating"}
            except urllib.error.URLError:
                return {"status": "restarting"}
            except Exception:
                continue

        # Fallback: control/update with JWT (for agents that have auth but no internal/restart)
        try:
            login_url = base_url + "/api/v1/auth/login"
            login_data = json.dumps({"username": "admin", "password": "admin"}).encode()
            login_req = urllib.request.Request(login_url, method="POST", data=login_data,
                                               headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(login_req, timeout=5) as resp:
                token = json.loads(resp.read().decode("utf-8")).get("token", "")

            url = base_url + "/api/v1/control/update"
            req = urllib.request.Request(url, method="POST", data=b'{}',
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass

        return {"status": "error", "detail": "Agent does not support remote update."}

    return await asyncio.to_thread(_trigger)


@router.get("/agents/{agent_id}/update_status")
async def get_agent_update_status(agent_id: str, since: float = 0):
    """Poll remote Agent's update status (simplified — no port 8001 monitor).

    Logic:
    1. First 10 seconds: agent is restarting, don't bother connecting
    2. After 10 seconds: try health check on port 8000
       - Responds → read /api/v1/update_status for result → done or failed
       - No response → estimate progress based on elapsed time
    3. Over 3 minutes: declare failed
    """
    import time
    agent = await _find_agent(agent_id)
    base_url = agent.get("url", "").rstrip("/")
    elapsed = time.time() - since if since > 0 else 999

    def _poll():
        # First 10 seconds: agent is shutting down + restarting, don't connect
        if elapsed < 10:
            return {"phase": "restarting", "pct": 20, "detail": "正在重啟..."}

        # Try health check
        health = None
        try:
            req = urllib.request.Request(base_url + "/api/v1/health", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                health = json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass

        if not health or health.get("status") == "offline":
            # Agent still down — estimate progress
            if elapsed < 180:
                pct = min(80, 20 + int(elapsed / 3))
                return {"phase": "updating", "pct": pct, "detail": "更新中，請稍候..."}
            return {"phase": "failed", "pct": 0, "detail": "Agent 超過 3 分鐘未回應"}

        # Agent is back online — read update_status.json for the real result
        version = health.get("version", "")
        detail = ""
        try:
            req2 = urllib.request.Request(base_url + "/api/v1/update_status", method="GET")
            with urllib.request.urlopen(req2, timeout=3) as resp2:
                status = json.loads(resp2.read().decode("utf-8"))
                detail = status.get("msg", "")
        except Exception:
            pass

        return {"phase": "done", "pct": 100, "version": version, "detail": detail}

    return await asyncio.to_thread(_poll)


@router.put("/agents/{agent_id}")
async def update_agent(agent_id: str, req: UpdateAgentRequest, request: Request):
    _check_admin_agents(request)
    if not req.name and not req.url:
        raise HTTPException(status_code=400, detail="請提供至少一個要更新的欄位（name 或 url）")

    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import agents_repo
                async with factory() as session:
                    existing = await agents_repo.get(session, agent_id)
                    if not existing:
                        raise HTTPException(status_code=404, detail=f"找不到 ID 為 {agent_id} 的機器")
                    new_name = req.name or existing["name"]
                    new_url = (req.url.rstrip("/") if req.url else existing["url"])
                    # Check URL uniqueness (exclude self)
                    if new_url != existing["url"] and await agents_repo.url_exists(session, new_url):
                        raise HTTPException(status_code=409, detail=f"已存在相同 URL 的機器：{new_url}")
                    await agents_repo.update(session, agent_id, new_name, new_url)
                    await session.commit()
                    await _sync_db_to_nas(session, agents_repo)
                    return {"status": "ok", "agent": {"id": agent_id, "name": new_name, "url": new_url}}
        except HTTPException:
            raise
        except Exception:
            pass

    # Fallback to JSON
    nas_dir = _get_nas_agents_dir()
    if not nas_dir:
        raise HTTPException(status_code=400, detail="尚未設定 nas_paths.agents_dir")
    agents = _load_agents_json(nas_dir)
    target = next((a for a in agents if a.get("id") == agent_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"找不到 ID 為 {agent_id} 的機器")
    new_url = req.url.rstrip("/") if req.url else target["url"]
    if new_url != target["url"] and any(a.get("url", "").rstrip("/") == new_url for a in agents if a.get("id") != agent_id):
        raise HTTPException(status_code=409, detail=f"已存在相同 URL 的機器：{new_url}")
    if req.name:
        target["name"] = req.name
    if req.url:
        target["url"] = new_url
    _save_agents_json(nas_dir, agents)
    return {"status": "ok", "agent": target}


@router.delete("/agents/{agent_id}")
async def remove_agent(agent_id: str, request: Request):
    _check_admin_agents(request)
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import agents_repo
                async with factory() as session:
                    removed = await agents_repo.remove(session, agent_id)
                    await session.commit()
                    if not removed:
                        raise HTTPException(status_code=404, detail=f"找不到 ID 為 {agent_id} 的機器")
                    # Sync to NAS JSON as backup
                    await _sync_db_to_nas(session, agents_repo)
                    return {"status": "ok"}
        except HTTPException:
            raise
        except Exception:
            pass

    # Fallback to JSON
    nas_dir = _get_nas_agents_dir()
    if not nas_dir:
        raise HTTPException(status_code=400, detail="尚未設定 nas_paths.agents_dir")
    agents = _load_agents_json(nas_dir)
    before = len(agents)
    agents = [a for a in agents if a.get("id") != agent_id]
    if len(agents) == before:
        raise HTTPException(status_code=404, detail=f"找不到 ID 為 {agent_id} 的機器")
    _save_agents_json(nas_dir, agents)
    return {"status": "ok"}
