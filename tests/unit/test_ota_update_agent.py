# -*- coding: utf-8 -*-
"""OTA 更新端（update_agent.py）失敗時的可診斷性。"""


def test_ota_failure_reason_is_recoverable():
    """2026-09-15 五台機器 pip 回滾、原因只剩「pip 有新版」那條 notice：update_agent 的輸出要落 update_agent.log、
    狀態訊息取尾段且濾掉 notice；agent 開 /api/v1/update_log、主控代理 /agents/{id}/update_log（管理員）。"""
    from tests.unit._srcscan import func_body, repo_src
    ua = repo_src("update_agent.py")
    assert 'LOG_FILE = os.path.join(INSTALL_DIR, "update_agent.log")' in ua
    assert 'with open(LOG_FILE, "a", encoding="utf-8") as fp:' in func_body(ua, "def log(")
    assert 'not ln.startswith("[notice]")' in ua and 'rollback(f"pip 安裝失敗: {err[-200:]}")' in ua
    assert '@router.get("/api/v1/update_log")' in repo_src("routers/api_ota.py")
    ag = repo_src("routers/api_agents.py")
    assert "_check_admin_agents(request)" in func_body(ag, "async def get_agent_update_log(")
    assert "update_agent.log" in repo_src(".gitignore")
