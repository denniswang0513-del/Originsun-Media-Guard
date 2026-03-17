"""
Layer 1 — config.py 單元測試。

config.py 使用 _SETTINGS_FILE (str) 作為設定檔路徑。
save_settings() 實作 merge-on-save（讀取現有檔案再覆蓋傳入的 key）。
load_settings() 有 deep merge（dict 類型的 section 會與 defaults 合併）。
"""
import json
import os

import pytest
import config


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_settings):
    """Every test uses a fresh isolated settings.json via tmp_settings fixture."""
    pass


# ── 1. init_settings 建立檔案 ───────────────────────────────
def test_init_creates_file(tmp_path, monkeypatch):
    """init_settings() 在檔案不存在時建立 settings.json。"""
    new_path = str(tmp_path / "new_settings.json")
    monkeypatch.setattr("config._SETTINGS_FILE", new_path)
    assert not os.path.exists(new_path)
    config.init_settings()
    assert os.path.exists(new_path)


# ── 2. init_settings 不覆蓋已有檔案 ─────────────────────────
def test_init_skips_existing(tmp_path, monkeypatch):
    """已有檔案時 init_settings() 不覆蓋。"""
    existing_path = str(tmp_path / "existing.json")
    marker = {"__marker__": True}
    with open(existing_path, "w", encoding="utf-8") as f:
        json.dump(marker, f)
    monkeypatch.setattr("config._SETTINGS_FILE", existing_path)
    config.init_settings()
    with open(existing_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("__marker__") is True


# ── 3. load_settings 回傳 dict ──────────────────────────────
def test_load_returns_dict():
    result = config.load_settings()
    assert isinstance(result, dict)


# ── 4. load_settings 補齊缺少的 key ─────────────────────────
def test_load_merges_missing_keys(tmp_path, monkeypatch):
    """只有部分 key 時，load 回來仍有完整預設 key（deep merge）。"""
    partial_path = str(tmp_path / "partial.json")
    with open(partial_path, "w", encoding="utf-8") as f:
        json.dump({"notifications": {"line_notify_token": "test_token"}}, f)
    monkeypatch.setattr("config._SETTINGS_FILE", partial_path)
    result = config.load_settings()
    # notifications 應有預設值與自訂值合併
    assert result["notifications"]["line_notify_token"] == "test_token"
    assert "google_chat_webhook" in result["notifications"]
    # 其他 section 應存在（從 defaults 來）
    assert "concurrency" in result


# ── 5. save → load 值一致 ───────────────────────────────────
def test_save_and_reload():
    """save → load → 值一致。"""
    settings = config.load_settings()
    settings["notifications"]["line_notify_token"] = "my_token_123"
    config.save_settings(settings)
    reloaded = config.load_settings()
    assert reloaded["notifications"]["line_notify_token"] == "my_token_123"


# ── 6. save partial 保留其他 key ─────────────────────────────
def test_save_partial_preserves_others():
    """save 部分 key 不會覆蓋其他 key（merge-on-save）。"""
    # 先存一個有 agents 的完整設定
    full = config.load_settings()
    full["agents"] = [{"id": "pc1", "name": "PC-1", "url": "http://1.2.3.4:8000"}]
    config.save_settings(full)

    # 再只存 notifications（不含 agents）
    config.save_settings({"notifications": {"line_notify_token": "new_tok"}})

    reloaded = config.load_settings()
    # agents 應該被保留
    assert len(reloaded["agents"]) == 1
    assert reloaded["agents"][0]["id"] == "pc1"
    # notifications 也更新了
    assert reloaded["notifications"]["line_notify_token"] == "new_tok"
