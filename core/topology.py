"""core/topology.py — 「這台是不是 master？」的單一權威答案

背景：全機隊共用同一個 `mediaguard` 資料庫，而 dev checkout 又跟生產 agent 同機共存。
只該在 master 跑一次的東西（離線告警 watcher、站點守衛探測、對外寄信…）若在每台
agent 上各跑一份，就會重複探測、重複轟炸信箱。

判準（兩個條件都要成立）：
  1. 資料庫**不是** `*_dev` —— 排除同機的 dev checkout
  2. `master_server` 指向本機 —— 排除機隊 agent（它們的 master_server 指向 .107）

⚠ 不要用「某個檔案/目錄存在」當判準（例如 `website/` 不在 OTA 清單、所以只有 master
有）。那是打包決策的副作用，不是節點身分：dev checkout 也有 `website/`，而任何一次
robocopy 都會讓某台 agent 意外自我升格成 master。
"""
from config import load_settings


def is_master_machine() -> bool:
    """本行程是否跑在生產 master 上。任何例外一律回 False（寧可不跑，不要重複跑）。"""
    try:
        s = load_settings()
        if str(s.get("database_url", "")).rstrip("/").endswith("_dev"):
            return False  # dev checkout 不跑（同機雙發防止）
        url = s.get("master_server", "") or ""
        if not url:
            return False
        # 本機 IP 判定重用 api_auth 的既有實作（lazy import：core 載入期不依賴 routers）
        from routers.api_auth import _master_is_self
        return _master_is_self(url)
    except Exception:
        return False
