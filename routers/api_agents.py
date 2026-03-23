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
import urllib.request
import urllib.error
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import load_settings
import core.state as state

router = APIRouter(prefix="/api/v1", tags=["Agents"])


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
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import agents_repo
                async with factory() as session:
                    agents = await agents_repo.list_all(session)
                    return {"agents": agents, "nas_configured": True, "source": "db"}
        except Exception:
            pass
    # Fallback to JSON
    nas_dir = _get_nas_agents_dir()
    agents = _load_agents_json(nas_dir)
    return {"agents": agents, "nas_configured": bool(nas_dir), "source": "json"}


@router.post("/agents")
async def add_agent(req: NewAgentRequest):
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
    agent = None

    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import agents_repo
                async with factory() as session:
                    agent = await agents_repo.get(session, agent_id)
        except Exception:
            pass

    if not agent:
        nas_dir = _get_nas_agents_dir()
        agents = _load_agents_json(nas_dir)
        agent = next((a for a in agents if a.get("id") == agent_id), None)

    if not agent:
        raise HTTPException(status_code=404, detail=f"找不到 ID 為 {agent_id} 的機器")

    url = agent.get("url", "").rstrip("/") + "/api/v1/health"

    def _fetch_health():
        try:
            r = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(r, timeout=4) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return {"status": "offline"}

    import asyncio
    return await asyncio.to_thread(_fetch_health)


@router.delete("/agents/{agent_id}")
async def remove_agent(agent_id: str):
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
