import os
from tests.unit._srcscan import func_body, repo_src  # noqa: F401
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
    assert '"--ignore-installed", "--no-deps", pin' in src and "result = _pip_install(req_file)" in src, "force-reinstall 也會先 uninstall，撞同一個錯"


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


# ── updater-first（owner 2026-09-16「之後可以不發生 要設計一個方法」）──

def test_agent_files_have_no_subpaths():
    """2026-09-15 十台機器卡在 2.5.30 的地雷之一：AGENT_FILES 放了 "python_embed/pip.ini"，舊版更新程式的備份步驟對子路徑不先建目錄，
    每次推都倒在那裡。只准根目錄檔名。"""
    from ota_manifest import AGENT_FILES
    bad = [f for f in AGENT_FILES if "/" in f or "\\" in f]
    assert not bad, bad


def test_master_serves_the_current_updater():
    src = repo_src("routers/api_ota.py")
    assert '@router.get("/download_updater_py")' in src
    body = src.split('@router.get("/download_updater_py")')[1].split("@router.get(")[0]
    assert 'os.path.join(base_dir, "update_agent.py")' in body and '"Cache-Control": "no-store"' in body


def test_restart_helper_prefers_a_fresh_updater_and_falls_back(tmp_path, monkeypatch):
    """更新前先跟主控拿新的 update_agent.py 來跑；拿不到／不像／編譯不過 → None（退回本機那支），絕不跑一支壞的。"""
    import urllib.request
    from core import ota_sign as S, process_spawn as ps

    base = str(tmp_path)
    (tmp_path / "settings.json").write_text('{"master_server": "http://master.test:8000/"}', encoding="utf-8")
    good = b'"""Originsun Agent OTA Updater v2"""\ndef run_update(master_url):\n    return 0\n'
    # 2026-09-16 起要簽章：假主控用臨時私鑰簽，公鑰放在 base_dir（機器上隨 OTA 來的那份）
    S.generate_keypair(str(tmp_path / "k.pem"), os.path.join(base, S.PUBLIC_KEY_FILE))

    class _Resp:
        def __init__(self, data):
            self._d = data
            self.headers = {S.SIGNATURE_HEADER: S.sign(data, str(tmp_path / "k.pem"))}
        def read(self): return self._d
        def __enter__(self): return self
        def __exit__(self, *a): return False

    seen = {}
    def fake_open(url, timeout=0):
        seen["url"] = url
        return _Resp(seen.get("payload", good))
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)

    # 1) 正常：抓到、像更新程式、編譯過 → 回新檔的路徑（放在 base_dir，它的 INSTALL_DIR 才會對）
    p = ps._fresh_updater(base)
    assert p == os.path.join(base, ps.FRESH_UPDATER) and os.path.isfile(p)
    assert seen["url"] == "http://master.test:8000/download_updater_py", "settings.json 的 master_server 要去掉尾巴的 /"
    # 2) 內容不像更新程式（例如主控回了登入頁）→ None
    seen["payload"] = b"<html>login</html>"
    assert ps._fresh_updater(base) is None
    # 3) 像、但編譯不過 → None（不能跑一支壞的）
    seen["payload"] = b'"""OTA Updater"""\ndef run_update(:\n'
    assert ps._fresh_updater(base) is None
    # 4) 主控不在 → None
    def down(url, timeout=0): raise OSError("unreachable")
    monkeypatch.setattr(urllib.request, "urlopen", down)
    assert ps._fresh_updater(base) is None
    log = (tmp_path / "update_agent.log").read_text(encoding="utf-8")
    assert "fetch failed" in log and "compile failed" in log and "does not look like" in log
    # 沒 settings.json → 預設主控
    (tmp_path / "settings.json").unlink()
    assert ps._master_url(base) == ps.DEFAULT_MASTER
    # _restart_main 真的先用 fresh、退回 local
    src = repo_src("core/process_spawn.py")
    assert 'updater = _fresh_updater(base_dir) or os.path.join(base_dir, "update_agent.py")' in src


# ── 簽章（owner 2026-09-16「這樣風險偏高」）──

def test_ota_signature_roundtrip_and_fail_closed(tmp_path):
    from core import ota_sign as S
    priv, pub = str(tmp_path / "k.pem"), str(tmp_path / "p.pem")
    S.generate_keypair(priv, pub)
    data = b'"""Originsun Agent OTA Updater v2"""\ndef run_update(m):\n    return 0\n'
    sig = S.sign(data, priv)
    assert sig and S.verify(data, sig, pub) is True
    assert S.verify(data + b"#", sig, pub) is False, "動過一個 byte 就不算"
    assert S.verify(data, None, pub) is False and S.verify(data, "", pub) is False
    assert S.verify(data, "zz", pub) is False, "壞格式不丟例外、只回 False"
    assert S.verify(data, sig, str(tmp_path / "nope.pem")) is False, "沒公鑰＝不跑"
    assert S.sign(data, str(tmp_path / "nokey.pem")) is None, "主控沒私鑰就不帶簽章"
    # 另一把鑰簽的不算
    S.generate_keypair(str(tmp_path / "k2.pem"), str(tmp_path / "p2.pem"))
    assert S.verify(data, S.sign(data, str(tmp_path / "k2.pem")), pub) is False


def test_fresh_updater_refuses_unsigned_or_badly_signed_code(tmp_path, monkeypatch):
    import urllib.request
    from core import ota_sign as S, process_spawn as ps
    base = str(tmp_path)
    (tmp_path / "settings.json").write_text('{"master_server": "http://m.test:8000"}', encoding="utf-8")
    S.generate_keypair(str(tmp_path / "k.pem"), os.path.join(base, S.PUBLIC_KEY_FILE))
    good = b'"""Originsun Agent OTA Updater v2"""\ndef run_update(m):\n    return 0\n'
    state = {"body": good, "sig": S.sign(good, str(tmp_path / "k.pem"))}

    class _Resp:
        def __init__(self): self.headers = {S.SIGNATURE_HEADER: state["sig"]} if state["sig"] is not None else {}
        def read(self): return state["body"]
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: _Resp())

    assert ps._fresh_updater(base) is not None, "簽對了才跑"
    state["sig"] = None
    assert ps._fresh_updater(base) is None, "沒簽章不跑"
    state["sig"] = "00" * 64
    assert ps._fresh_updater(base) is None, "簽章不符不跑"
    state["sig"] = S.sign(good + b"# tampered", str(tmp_path / "k.pem"))
    assert ps._fresh_updater(base) is None, "簽的不是這份內容不跑"
    log = (tmp_path / "update_agent.log").read_text(encoding="utf-8")
    assert "refusing unsigned code" in log
    # 沒公鑰（舊機器還沒拿到）→ 不跑
    os.remove(os.path.join(base, S.PUBLIC_KEY_FILE)); state["sig"] = S.sign(good, str(tmp_path / "k.pem"))
    assert ps._fresh_updater(base) is None


def test_signing_key_never_ships_but_public_key_does():
    from ota_manifest import AGENT_FILES
    assert "ota_signing_pub.pem" in AGENT_FILES
    assert "ota_signing_key.pem" not in AGENT_FILES
    gi = repo_src(".gitignore")
    assert "ota_signing_key.pem" in gi
    assert os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "ota_signing_pub.pem")), "公鑰要在 repo 裡"
    src = repo_src("routers/api_ota.py")
    assert "sign(raw, os.path.join(base_dir, PRIVATE_KEY_FILE))" in src


def test_pip_self_heal_loops_over_every_half_installed_package(tmp_path, monkeypatch):
    """2026-09-16 ai_2 重灌後 python_embed 裡 pillow、requests… 好幾個套件都沒 RECORD：只救一個的話每推一輪才多好一個。
    要一個接一個救到 pip 過為止；同一個套件救過還擋就停、最多 MAX_HEALS 個。"""
    import importlib.util, types
    spec = importlib.util.spec_from_file_location("update_agent_mod2", "update_agent.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    req = tmp_path / "req.txt"; req.write_text("Pillow==12.3.0\nrequests==2.33.0\nxxhash==4.0.1\n", encoding="utf-8")
    fails = ["pillow", "requests", "xxhash"]          # 依序擋三個，之後過
    healed = []
    def fake_pip(_req):
        if fails:
            return types.SimpleNamespace(returncode=1, stdout="", stderr=f"error: uninstall-no-record-file\nCannot uninstall {fails[0]} None\nno RECORD file")
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
    def fake_run(cmd, **kw):
        healed.append(cmd[cmd.index("--no-deps") + 1]); fails.pop(0)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(mod, "_pip_install", fake_pip)
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "log", lambda *a, **k: None)
    monkeypatch.setattr(mod, "write_status", lambda *a, **k: None)
    r = mod._pip_with_self_heal(str(req))
    assert r.returncode == 0 and healed == ["Pillow==12.3.0", "requests==2.33.0", "xxhash==4.0.1"], healed
    # 同一個救過還擋 → 停，不無限迴圈
    fails[:] = ["pillow"]; healed.clear()
    def stuck_run(cmd, **kw):
        healed.append(cmd[cmd.index("--no-deps") + 1]); return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(mod.subprocess, "run", stuck_run)
    r = mod._pip_with_self_heal(str(req))
    assert r.returncode == 1 and healed == ["Pillow==12.3.0"]
    assert "result = _pip_with_self_heal(req_file)" in repo_src("update_agent.py")
