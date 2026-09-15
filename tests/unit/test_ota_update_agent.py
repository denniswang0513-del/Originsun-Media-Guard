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


def test_pip_self_heals_half_installed_packages():
    """2026-09-15 五台機器停在 2.5.24 的真因：前一次 pip 裝 Pillow 逾時被殺、剩半套沒 RECORD，之後每次
    `pip install -r` 都死在「Cannot uninstall pillow None」。update_agent 要自己 --force-reinstall --no-deps 那個套件再重跑；
    pip 逾時拉到 600 秒。"""
    import importlib.util, types
    spec = importlib.util.spec_from_file_location("update_agent_mod", "update_agent.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    r = types.SimpleNamespace(returncode=1, stdout="", stderr="error: uninstall-no-record-file\n\n× Cannot uninstall pillow None\n╰─> no RECORD file was found for pillow.")
    assert mod._no_record_pkg(r) == "pillow"
    assert mod._no_record_pkg(types.SimpleNamespace(returncode=1, stdout="", stderr="ERROR: something else")) == ""
    assert mod.PIP_TIMEOUT == 600
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("fastapi==0.134.0\nPillow==12.3.0\npillow-heif==1.4.0\n"); path = f.name
    try:
        assert mod._pinned(path, "pillow") == "Pillow==12.3.0" and mod._pinned(path, "pillow-heif") == "pillow-heif==1.4.0" and mod._pinned(path, "torch") == ""
    finally:
        os.unlink(path)
    from tests.unit._srcscan import repo_src
    src = repo_src("update_agent.py")
    assert '"--force-reinstall", "--no-deps", pin' in src and "result = _pip_install(req_file)" in src


def test_transitional_pip_ini_bridge_is_gone():
    """2.5.30 過渡（OTA 帶 python_embed/pip.ini no-deps、Pillow 不釘）已在 2.5.31 還原：Pillow 釘回、manifest 不帶 pip.ini；
    新 update_agent 仍會刪機器上殘留的過渡檔。"""
    from tests.unit._srcscan import repo_src
    assert "Pillow==12.3.0" in repo_src("requirements_agent.txt").splitlines()
    assert '"python_embed/pip.ini",' not in repo_src("ota_manifest.py")
    assert '"originsun-ota-transitional" in open(path' in repo_src("update_agent.py")


def test_backup_and_rollback_create_parent_dirs():
    """2.5.31 推到 2.5.30 的機器時 backup 在 `_rollback/python_embed/pip.ini` 炸掉（沒那層目錄）→ 整個更新 abort。子路徑要先 makedirs。"""
    from tests.unit._srcscan import func_body, repo_src
    ua = repo_src("update_agent.py")
    assert "os.makedirs(os.path.dirname(dst), exist_ok=True)" in func_body(ua, "def backup_current(")
    assert "os.makedirs(os.path.dirname(dst), exist_ok=True)" in func_body(ua, "def rollback(")
