"""AI runner 共用小工具 — 零政策分歧的純機械件才放這裡。

刻意不做 runner 基底類：三個 runner（seo/post_seo/social）的 scheduler loop
有註解背書的政策分歧（首跑策略/gate 條件/sleep 位移/post hook），抽平會把
分歧藏進參數迷宮。抽基底的觸發點是「shotgun surgery」——一個橫切改動得同時
改三份 loop 時再抽（例：relay 認證換 scheme、輪詢改事件驅動）。
真正深的共用件（_call_claude/_resolve_claude_exe）在 seo_runner，維持現狀。
"""
from typing import Optional


def validate_cron(cron_str: str) -> None:
    """cron 字串驗證（raises ValueError）。

    收斂自 seo/post_seo/social/backup 四份逐字重複。
    ⚠ NAS 容器沒裝 croniter：ImportError 退化成 5 欄位結構檢查。
    """
    try:
        from croniter import croniter
        croniter(cron_str)
    except ImportError:
        if len(cron_str.split()) != 5:
            raise ValueError(f"cron 需 5 個欄位（分 時 日 月 週）：{cron_str!r}")
    except Exception as e:
        raise ValueError(f"cron 字串不合法：{cron_str!r}（{e}）")


def cron_due(cron_expr: str, last_at: float) -> bool:
    """距離 last_at（epoch 秒）的下一個 cron 時點是否已到。

    純機械件、零政策分歧（不決定「首跑要不要立刻跑」——那是各 runner 自己的取捨）。
    ⚠ 沒裝 croniter（NAS 容器）或 cron 字串壞掉 → 一律回 False，寧可不跑。
    """
    from datetime import datetime
    try:
        from croniter import croniter
    except ImportError:
        return False
    if not cron_expr:
        return False
    try:
        next_due = croniter(cron_expr, datetime.fromtimestamp(last_at)).get_next(datetime)
    except (ValueError, KeyError, TypeError):
        return False
    return datetime.now() >= next_due


async def relay_post(url: str, timeout: float, *, on_error: Optional[dict] = None) -> dict:
    """POST 到 master internal endpoint（X-Internal-Key = JWT secret）。

    200 → 回傳 response json；任何失敗 → 回傳 {**on_error, "error": <原因>}
    （各 runner 的錯誤回傳形狀不同，由 caller 用 on_error 指定基底欄位）。
    """
    import httpx
    from core.auth import _get_secret
    base = dict(on_error or {})
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers={"X-Internal-Key": _get_secret()})
            if r.status_code == 200:
                return r.json()
            return {**base, "error": f"master relay 失敗 (HTTP {r.status_code}): {r.text[:200]}"}
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        return {**base, "error": f"master 離線或超時：{e}"}
    except Exception as e:
        return {**base, "error": f"relay 錯誤：{e}"}
