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


# ─── Schemas ──────────────────────────────────────────────

class NewAgentRequest(BaseModel):
    name: str
    url: str


# ─── Endpoints ────────────────────────────────────────────

@router.get("/agents")
async def list_agents():
    """List agents. NAS agents.json is the single source of truth.
    DB agents are merged in (for any that were added via web UI but not yet in NAS)."""
    nas_dir = _get_nas_agents_dir()
    nas_agents = _load_agents_json(nas_dir)

    # If NAS has agents, use NAS as primary source
    if nas_agents:
        # Also check DB for any agents not in NAS (added via web UI)
        db_agents = []
        if state.db_online:
            try:
                from db.session import get_session_factory
                factory = get_session_factory()
                if factory:
                    from db.repos import agents_repo
                    async with factory() as session:
                        db_agents = await agents_repo.list_all(session)
            except Exception:
                pass

        # Merge: NAS is primary, add DB-only agents
        nas_ids = {a["id"] for a in nas_agents}
        merged = list(nas_agents)
        for dba in db_agents:
            if dba["id"] not in nas_ids:
                merged.append(dba)

        # Sync merged list back to NAS so new agents persist
        if len(merged) > len(nas_agents):
            _save_agents_json(nas_dir, merged)

        return {"agents": merged, "nas_configured": True, "source": "nas+db"}

    # No NAS config — fall back to DB only
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import agents_repo
                async with factory() as session:
                    agents = await agents_repo.list_all(session)
                    return {"agents": agents, "nas_configured": False, "source": "db"}
        except Exception:
            pass

    return {"agents": [], "nas_configured": False, "source": "none"}


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
async def trigger_agent_update(agent_id: str, request: Request):
    """Trigger OTA update on a remote Agent.
    Uses /api/admin/restart which calls start_hidden.vbs → update_agent.py → uvicorn.
    This works on ALL agent versions because start_hidden.vbs triggers the full update cycle."""
    _check_admin_agents(request)
    agent = await _find_agent(agent_id)
    base_url = agent.get("url", "").rstrip("/")

    def _trigger():
        # Try internal restart endpoint (works without JWT auth)
        try:
            url = base_url + "/api/v1/internal/restart"
            req = urllib.request.Request(url, method="POST", data=b'{}',
                                         headers={"Content-Type": "application/json",
                                                  "X-Internal-Key": "originsun-internal-restart"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                pass  # Old agent without /internal/restart — try fallback
            else:
                return {"status": "updating", "method": "internal_restart"}
        except Exception:
            return {"status": "updating", "method": "internal_restart"}

        # Fallback for old agents: call /api/v1/control/update without auth
        # (will fail with 401 on auth-required agents, but might work on some)
        try:
            url = base_url + "/api/v1/control/update"
            req = urllib.request.Request(url, method="POST", data=b'{}',
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass

        return {"status": "error", "detail": "Agent does not support remote restart. Please update manually."}

    return await asyncio.to_thread(_trigger)


@router.get("/agents/{agent_id}/update_status")
async def get_agent_update_status(agent_id: str, since: float = 0):
    """Poll remote Agent's update status.

    Logic:
    1. First 20 seconds: only check monitor (port 8001), agent is restarting
    2. After 20 seconds: check health (port 8000) — if responds, update is done
    3. If neither responds: show "updating" with increasing progress
    """
    import time
    agent = await _find_agent(agent_id)
    base_url = agent.get("url", "").rstrip("/")
    monitor_url = base_url.replace(":8000", ":8001") + "/status"
    health_url = base_url + "/api/v1/health"
    elapsed = time.time() - since if since > 0 else 999

    def _poll():
        # ── Check monitor on port 8001 (available during entire update) ──
        monitor_data = None
        try:
            req = urllib.request.Request(monitor_url, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                monitor_data = json.loads(resp.read().decode("utf-8"))
                if "step" in monitor_data and "phase" not in monitor_data:
                    phases = {1: "downloading", 2: "installing", 3: "restarting"}
                    monitor_data["phase"] = phases.get(monitor_data["step"], "updating")
                monitor_data["source"] = "monitor"
        except Exception:
            pass

        # ── After 20 seconds, also check health ──
        if elapsed > 20:
            try:
                req = urllib.request.Request(health_url, method="GET")
                with urllib.request.urlopen(req, timeout=2) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return {"phase": "done", "pct": 100, "source": "health",
                            "version": data.get("version", "")}
            except Exception:
                pass

        # ── Return monitor data if available ──
        if monitor_data:
            return monitor_data

        # ── Neither responded ──
        if elapsed < 20:
            # Still early — server is shutting down, normal
            return {"phase": "restarting", "pct": 30, "source": "none",
                    "detail": "Server is restarting..."}
        elif elapsed < 120:
            # 20-120 seconds — update in progress
            pct = min(80, 30 + int(elapsed / 2))
            return {"phase": "updating", "pct": pct, "source": "none",
                    "detail": "Updating... please wait"}
        else:
            # Over 2 minutes — likely failed
            return {"phase": "failed", "pct": 0, "source": "none",
                    "detail": "Update may have failed. Agent not responding after 2 minutes."}

    return await asyncio.to_thread(_poll)


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
