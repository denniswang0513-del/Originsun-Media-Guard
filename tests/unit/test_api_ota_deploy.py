"""deploy_to_prod 安全網（routers/api_ota.py）— 會覆蓋生產檔案並重啟 8000 的
程式碼，rollback / smoke check / 部署守衛必須有單元測試。

原則：全部 monkeypatch 模組常數與探測函式，絕不碰真實 C:\\OriginsunAgent、
絕不發真 HTTP、絕不真 sleep。
"""
import asyncio
import json
import os

import pytest

import routers.api_ota as ota


# ── _rollback_prod_sync ──────────────────────────────────────

def _setup_rollback_scene(tmp_path):
    """備份含 v1 舊檔 + meta 記一個部署新增檔；prod 目前是 v2。"""
    prod = tmp_path / "prod"
    backup = prod / "_deploy_backup"
    (prod / "core").mkdir(parents=True)
    (backup / "core").mkdir(parents=True)
    (prod / "main.py").write_text("v2")
    (prod / "core" / "worker.py").write_text("v2")
    (prod / "core" / "new_module.py").write_text("v2-new")
    (backup / "main.py").write_text("v1")
    (backup / "core" / "worker.py").write_text("v1")
    (backup / "_backup_meta.json").write_text(json.dumps({
        "prod_version": "1.0.0", "deployed_version": "2.0.0",
        "new_files": [os.path.join("core", "new_module.py")],
    }), encoding="utf-8")
    return prod, backup


def test_rollback_restores_files_and_removes_new(tmp_path, monkeypatch):
    prod, backup = _setup_rollback_scene(tmp_path)
    monkeypatch.setattr(ota, "_PROD_DIR", str(prod))
    monkeypatch.setattr(ota, "_DEPLOY_BACKUP_DIR", str(backup))

    r = ota._rollback_prod_sync()

    assert r["ok"] and r["prev_version"] == "1.0.0"
    assert (prod / "main.py").read_text() == "v1"
    assert (prod / "core" / "worker.py").read_text() == "v1"
    assert not (prod / "core" / "new_module.py").exists(), "部署新增檔應被移除"
    assert not (prod / "_backup_meta.json").exists(), "meta 不應洩漏成 prod 檔案"


def test_rollback_without_backup_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(ota, "_PROD_DIR", str(tmp_path / "prod"))
    monkeypatch.setattr(ota, "_DEPLOY_BACKUP_DIR", str(tmp_path / "nonexistent"))
    r = ota._rollback_prod_sync()
    assert not r["ok"] and "沒有可用備份" in r["log"]


# ── _smoke_check_prod ────────────────────────────────────────
# 探測序列用 closure list 驅動；asyncio.sleep 換成 no-op 讓 30s/90s 迴圈瞬跑。

@pytest.fixture
def fast_sleep(monkeypatch):
    async def _noop(_secs):
        return None
    monkeypatch.setattr(asyncio, "sleep", _noop)


def _replay(seq):
    """依序回放 seq，耗盡後永遠重複最後一項。"""
    items = list(seq)

    def _next(*_args, **_kwargs):
        return items.pop(0) if len(items) > 1 else items[0]
    return _next


def _drive_smoke(monkeypatch, alive_seq, version_responses, expect_version):
    """alive_seq: _prod_alive 依序回傳值（耗盡後重複最後一個）。
    version_responses: /version 依序的行為（dict=回傳、Exception=raise）。"""
    next_ver = _replay(version_responses)

    def fake_get(url, timeout=3.0):
        item = next_ver()
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(ota, "_prod_alive", _replay(alive_seq))
    monkeypatch.setattr(ota, "_http_get_json", fake_get)
    return asyncio.run(ota._smoke_check_prod(expect_version))


def test_smoke_port_never_drops_means_no_restart(fast_sleep, monkeypatch):
    r = _drive_smoke(monkeypatch, [True], [{"version": "9.9.9"}], "9.9.9")
    assert not r["ok"] and not r["restarted"]
    assert "重啟沒有發生" in r["detail"]


def test_smoke_drop_then_correct_version(fast_sleep, monkeypatch):
    r = _drive_smoke(monkeypatch, [True, False],
                     [ConnectionError("booting"), {"version": "9.9.9"}], "9.9.9")
    assert r["ok"] and r["restarted"]


def test_smoke_drop_then_wrong_version(fast_sleep, monkeypatch):
    r = _drive_smoke(monkeypatch, [True, False], [{"version": "1.0.0"}], "9.9.9")
    assert not r["ok"] and r["restarted"]
    assert "版本不符" in r["detail"]


def test_smoke_drop_never_recovers(fast_sleep, monkeypatch):
    r = _drive_smoke(monkeypatch, [False], [ConnectionError("dead")], "9.9.9")
    assert not r["ok"] and r["restarted"]
    assert "未恢復" in r["detail"]


def test_smoke_empty_expect_accepts_any_version(fast_sleep, monkeypatch):
    """rollback 後舊版號未知（""）→ 只驗健康、不比對版本。"""
    r = _drive_smoke(monkeypatch, [False], [{"version": "whatever"}], "")
    assert r["ok"]


# ── _deploy_to_prod_sync 守衛（不進入複製流程）──────────────

def test_deploy_refuses_same_dir(monkeypatch):
    src = os.path.dirname(os.path.dirname(os.path.abspath(ota.__file__)))
    monkeypatch.setattr(ota, "_PROD_DIR", src)
    r = ota._deploy_to_prod_sync("9.9.9", "test")
    assert not r["ok"] and "拒絕部署" in r["log"]


def test_deploy_refuses_missing_prod_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ota, "_PROD_DIR", str(tmp_path / "no_such_agent"))
    r = ota._deploy_to_prod_sync("9.9.9", "test")
    assert not r["ok"] and "生產目錄不存在" in r["log"]
