"""
api_agents.py — NAS 共享機器管理 API
Endpoints:
  GET    /api/v1/agents              — 列出所有機器（從 NAS agents.json 讀取）
  POST   /api/v1/agents              — 新增機器
  DELETE /api/v1/agents/{id}         — 移除機器
  GET    /api/v1/agents/{id}/health  — Proxy 取得遠端機器健康狀態（避開瀏覽器 CORS）
"""
import os
import json
import re
import urllib.request
import urllib.error
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import load_settings

router = APIRouter(prefix="/api/v1", tags=["Agents"])


# ─── Helpers ──────────────────────────────────────────────

def _get_nas_agents_dir() -> str:
    """從 settings 取得 NAS agents 目錄路徑。"""
    return load_settings().get("nas_paths", {}).get("agents_dir", "")


def _agents_file(nas_dir: str) -> str:
    return os.path.join(nas_dir, "agents.json")


def _load_agents(nas_dir: str) -> list:
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


def _save_agents(nas_dir: str, agents: list):
    os.makedirs(nas_dir, exist_ok=True)
    with open(_agents_file(nas_dir), "w", encoding="utf-8") as fp:
        json.dump(agents, fp, ensure_ascii=False, indent=2)


def _make_id(name: str) -> str:
    """從名稱生成 ID（小寫英數 + 連字號）。"""
    # 用底線取代非英數字元，去頭尾
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "agent"


# ─── Schemas ──────────────────────────────────────────────

class NewAgentRequest(BaseModel):
    name: str
    url: str  # e.g. "http://192.168.1.120:8000"


# ─── Endpoints ────────────────────────────────────────────

@router.get("/agents")
def list_agents():
    nas_dir = _get_nas_agents_dir()
    agents = _load_agents(nas_dir)
    return {"agents": agents, "nas_configured": bool(nas_dir)}


@router.post("/agents")
def add_agent(req: NewAgentRequest):
    nas_dir = _get_nas_agents_dir()
    if not nas_dir:
        raise HTTPException(status_code=400, detail="尚未設定 nas_paths.agents_dir，請先在系統參數設定 NAS 路徑。")

    agents = _load_agents(nas_dir)

    # 檢查重複 URL
    url_clean = req.url.rstrip("/")
    if any(a.get("url", "").rstrip("/") == url_clean for a in agents):
        raise HTTPException(status_code=409, detail=f"已存在相同 URL 的機器：{url_clean}")

    # 生成唯一 ID
    base_id = _make_id(req.name)
    agent_id = base_id
    existing_ids = {a.get("id", "") for a in agents}
    counter = 1
    while agent_id in existing_ids:
        counter += 1
        agent_id = f"{base_id}_{counter}"

    new_agent = {"id": agent_id, "name": req.name, "url": url_clean}
    agents.append(new_agent)
    _save_agents(nas_dir, agents)
    return {"status": "ok", "agent": new_agent}


@router.get("/agents/{agent_id}/health")
def proxy_agent_health(agent_id: str):
    """Proxy health check for a remote agent — avoids browser CORS/Private Network issues."""
    nas_dir = _get_nas_agents_dir()
    agents = _load_agents(nas_dir)
    agent = next((a for a in agents if a.get("id") == agent_id), None)
    if not agent:
        raise HTTPException(status_code=404, detail=f"找不到 ID 為 {agent_id} 的機器")

    url = agent.get("url", "").rstrip("/") + "/api/v1/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except Exception:
        return {"status": "offline"}


@router.delete("/agents/{agent_id}")
def remove_agent(agent_id: str):
    nas_dir = _get_nas_agents_dir()
    if not nas_dir:
        raise HTTPException(status_code=400, detail="尚未設定 nas_paths.agents_dir")

    agents = _load_agents(nas_dir)
    before = len(agents)
    agents = [a for a in agents if a.get("id") != agent_id]
    if len(agents) == before:
        raise HTTPException(status_code=404, detail=f"找不到 ID 為 {agent_id} 的機器")

    _save_agents(nas_dir, agents)
    return {"status": "ok"}
