"""本機維運背景工 — main.py 只負責 create_task 掛載（composition root 不放政策）。

- run_local_maintenance()：每日 job_history retention + log 超大告警（全機隊都跑）
- notify_db_transition() / db_display_name()：DB 上線/斷線「轉換點」推播
  （main.py 的 _periodic_db_health 迴圈與 startup 失敗路徑共用）
"""
import asyncio
import os

import core.state as state

_LOG_CAP_MB = 200
_LOG_FILES = ("uvicorn_out.log", "uvicorn_err.log", "agent_server.log")
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def db_display_name() -> str:
    """從 database_url 取庫名（mediaguard / mediaguard_dev），區分同機的 prod/dev 告警。"""
    try:
        from config import load_settings
        return (load_settings().get("database_url") or "").rsplit("/", 1)[-1] or "?"
    except Exception:
        return "?"


async def notify_db_transition(now_online: bool) -> None:
    """DB 上線/斷線「轉換點」主動推播（best-effort）。

    只有裝了 DB 套件的機器（master / dev）會走到這裡 —— 機隊 agent 沒裝
    sqlalchemy，_periodic_db_health 在 import 就早退，不會十台齊發。
    """
    try:
        from notifier import notify_tab_async, machine_label  # type: ignore
        await notify_tab_async(
            "db_recovered" if now_online else "db_offline",
            db=db_display_name(), hostname=machine_label(),
        )
    except Exception:
        pass


async def run_local_maintenance() -> None:
    """每日本機維護（全機隊都跑）：job_history retention + log 超大告警。

    - retention：job_history 是全機隊共寫、只增不減的 DB 表（健康檢查唯一
      無界增長點）。每日刪除超過保留天數的舊列（settings
      job_history_retention_days，預設見 config._DEFAULT_SETTINGS，設 0 停用）。
      多台同時跑也只是重疊的 DELETE，idempotent。
    - log 告警：uvicorn log 只在重啟時輪替（無 RotatingFileHandler），服務
      久不重啟會無界成長（agent_server.log 曾長到 18MB+）。行程自己的
      stdout 檔在 Windows 上鎖著不能改名，所以只能告警提醒重啟，每檔一次。
    """
    warned_logs: set = set()
    await asyncio.sleep(300)  # 開機後 5 分鐘 first run，避開 startup 高峰
    while True:
        try:
            if state.db_online:
                from config import load_settings
                days = int(load_settings().get("job_history_retention_days") or 0)
                if days > 0:
                    from datetime import datetime, timedelta, timezone
                    from db.session import get_session_factory
                    from db.repos import job_history_repo
                    factory = get_session_factory()
                    if factory:
                        async with factory() as session:
                            n = await job_history_repo.purge_older_than(
                                session, datetime.now(timezone.utc) - timedelta(days=days))
                        if n:
                            print(f"[MAINT] job_history retention: 清除 {n} 筆（>{days} 天）")
        except Exception as e:
            print(f"[MAINT] retention 失敗: {e}")
        try:
            for fn in _LOG_FILES:
                p = os.path.join(_BASE, fn)
                if fn in warned_logs or not os.path.exists(p):
                    continue
                mb = os.path.getsize(p) / 1024 / 1024
                if mb > _LOG_CAP_MB:
                    warned_logs.add(fn)
                    from notifier import notify_tab_async, machine_label  # type: ignore
                    await notify_tab_async(
                        "log_oversize", filename=fn, size_mb=f"{mb:.0f}",
                        hostname=machine_label(),
                    )
        except Exception:
            pass
        await asyncio.sleep(86400)
