"""機隊離線告警 watcher — 只在 master 跑（每 5 分鐘巡一輪）。

第一輪健康檢查後 UI 有紅燈但「沒開網頁就沒人知道」；本模組把離線變成主動推播：
連續 3 輪（~15 分鐘）健康檢查無回應 → agent_offline 告警一次；恢復 → agent_recovered。

Gate 設計（雙重，缺一不跑）：
  1. settings.master_server 的 host 是本機 IP → 這台是 master
     （機隊 agent 的 master_server 指向別台 → 不跑，不會 8 台齊發）
  2. database_url 不是 *_dev → dev checkout（8001，與 master 同一台實體機、
     agents 清單同樣指向真機隊）不跑，避免同機雙發
"""
import asyncio

from core.topology import is_master_machine

_PROBE_TIMEOUT = 4.0
_INTERVAL_SEC = 300
_MISS_THRESHOLD = 3  # 連續 3 輪（~15 分鐘）才告警，容忍單次網路抖動


async def run_agent_watch() -> None:
    if not is_master_machine():
        print("[AgentWatch] 本機非 master（或為 dev checkout），離線告警 watcher 不啟動")
        return
    print(f"[AgentWatch] 啟動：每 {_INTERVAL_SEC}s 巡檢，連續 {_MISS_THRESHOLD} 次無回應告警")

    misses: dict = {}
    alerted: set = set()

    while True:
        await asyncio.sleep(_INTERVAL_SEC)
        try:
            from routers.api_agents import list_agents, _async_get_json
            agents = (await list_agents()).get("agents", [])

            async def _probe(a: dict):
                try:
                    await _async_get_json(f"{a['url']}/api/v1/health", _PROBE_TIMEOUT)
                    return a, True
                except Exception:
                    return a, False

            # _probe 自吞例外恆回 (a, ok)，gather 不需再套 return_exceptions
            for a, ok in await asyncio.gather(*[_probe(a) for a in agents]):
                aid = a.get("id", a.get("url", "?"))
                if ok:
                    if aid in alerted:
                        alerted.discard(aid)
                        await _notify("agent_recovered", name=a.get("name", aid), url=a.get("url", ""))
                    misses[aid] = 0
                else:
                    misses[aid] = misses.get(aid, 0) + 1
                    if misses[aid] >= _MISS_THRESHOLD and aid not in alerted:
                        alerted.add(aid)
                        await _notify("agent_offline", name=a.get("name", aid),
                                      url=a.get("url", ""), misses=misses[aid])

            # 從機隊名單移除的 agent 不留殘鍵（misses/alerted 只增不減的微洩漏）
            current = {a.get("id", a.get("url", "?")) for a in agents}
            misses = {k: v for k, v in misses.items() if k in current}
            alerted &= current
        except Exception as e:
            print(f"[AgentWatch] 巡檢輪失敗（下一輪重試）: {e}")


async def _notify(key: str, **fields) -> None:
    try:
        from notifier import notify_tab_async  # type: ignore
        await notify_tab_async(key, **fields)
    except Exception:
        pass
