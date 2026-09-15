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
    import importlib.util, subprocess, types
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


def test_transitional_pip_ini_bridge():
    """2.5.28 過渡：ota_manifest 帶 python_embed/pip.ini（no-deps）、Pillow 暫時不在清單、新 update_agent 裝套件前刪掉它。
    下一版要把這三樣還原（Pillow 放回、manifest 拿掉那行、主控的 pip.ini 刪檔）。"""
    from tests.unit._srcscan import repo_src
    ua = repo_src("update_agent.py")
    assert "_drop_transitional_pip_ini()" in ua.split("# ── Phase 5: PIP ──")[1][:120]
    assert '"originsun-ota-transitional" in open(path' in ua
    assert '"python_embed/pip.ini",' in repo_src("ota_manifest.py")
    req = repo_src("requirements_agent.txt")
    assert "\nPillow==" not in req and "# Pillow==12.3.0" in req, "過渡版暫時不釘 Pillow"
    ini = open(r"C:\OriginsunAgent\python_embed\pip.ini", encoding="utf-8").read() if __import__("os").path.isfile(r"C:\OriginsunAgent\python_embed\pip.ini") else ""
    assert (not ini) or ("no-deps = true" in ini and "originsun-ota-transitional" in ini)
