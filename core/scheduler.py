"""
任務排程器 — Originsun Media Guard Pro
每 60 秒掃描一次 scheduled_jobs.json，到期的排程自動送入任務佇列。
支援 cron 重複排程與 run_at 單次排程。
"""

import os
import json
import asyncio
import logging
import threading
from datetime import datetime, timedelta
from typing import List, Optional

try:
    from croniter import croniter  # type: ignore
except ImportError:
    croniter = None  # graceful degradation — cron schedules won't fire

from core.schemas import (  # type: ignore
    BackupRequest, TranscodeRequest, ConcatRequest,
    VerifyRequest, TranscribeRequest, ReportJobRequest,
)
import core.state as state  # type: ignore

_log = logging.getLogger(__name__)

_SCHEDULE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scheduled_jobs.json",
)

# task_type → Pydantic Request class (佇列型任務)
REQUEST_CLASS_MAP = {
    "backup": BackupRequest,
    "transcode": TranscodeRequest,
    "concat": ConcatRequest,
    "verify": VerifyRequest,
    "transcribe": TranscribeRequest,
    "report": ReportJobRequest,
}

# 所有可排程的任務類型（含 TTS 非佇列型）
SCHEDULABLE_TYPES = set(REQUEST_CLASS_MAP) | {"tts", "clone"}

# Serialize lock — prevents TOCTOU race between scheduler tick and API writes
_file_lock = threading.Lock()


# ── 持久化 ────────────────────────────────────────────────────

def load_schedules() -> List[dict]:
    """讀取排程列表。檔案不存在或損毀時回傳空 list。"""
    if not os.path.exists(_SCHEDULE_FILE):
        return []
    try:
        with open(_SCHEDULE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_schedules(schedules: List[dict]) -> None:
    """原子寫入排程列表（先寫 .tmp 再 os.replace）。"""
    tmp = _SCHEDULE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(schedules, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _SCHEDULE_FILE)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ── Cron 工具 ─────────────────────────────────────────────────

def is_valid_cron(expr: str) -> bool:
    """檢查 cron 表達式是否合法。"""
    if croniter is None:
        return False
    try:
        croniter(expr)
        return True
    except (ValueError, KeyError, TypeError):
        return False


def compute_next_run(cron_expr: str, after: Optional[datetime] = None) -> str:
    """計算下次執行時間（ISO 字串）。"""
    if croniter is None:
        return ""
    base = after or datetime.now()
    ci = croniter(cron_expr, base)
    return ci.get_next(datetime).isoformat(timespec="seconds")


# ── Request 建構 ──────────────────────────────────────────────

def build_request(task_type: str, data: dict):
    """根據 task_type 建立對應的 Pydantic Request 物件。"""
    cls = REQUEST_CLASS_MAP.get(task_type)
    if not cls:
        raise ValueError(f"不支援的排程任務類型: {task_type}")
    return cls(**data)


# ── TTS 排程派發（透過 HTTP 自呼叫） ──────────────────────────

def _dispatch_tts(task_type: str, req_data: dict) -> bool:
    """透過 HTTP 呼叫本機 TTS API 來派發 TTS/Clone 任務。"""
    import urllib.request
    import urllib.error

    endpoint_map = {
        "tts": "/api/v1/tts_jobs",
        "clone": "/api/v1/tts_jobs/clone",
    }
    path = endpoint_map.get(task_type)
    if not path:
        return False

    url = f"http://127.0.0.1:8000{path}"
    body = json.dumps(req_data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status == 200
    except Exception as e:
        _log.warning("TTS 排程派發失敗 (%s): %s", task_type, e)
        return False


# ── 分散式轉檔派發 ────────────────────────────────────────────

# 影片副檔名（與 ListDirRequest 預設值對齊）
_VIDEO_EXTS = {".mov", ".mp4", ".mkv", ".mxf", ".avi", ".mts", ".m2ts", ".r3d", ".braw"}


def _scan_video_files(source_dirs: list) -> list:
    """掃描來源目錄下所有影片檔案，回傳 (abs_path, rel_subdir) 列表。"""
    files = []
    for src in source_dirs:
        src = src.strip()
        if not os.path.isdir(src):
            continue
        for root, _dirs, filenames in os.walk(src):
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() in _VIDEO_EXTS:
                    abs_path = os.path.join(root, fn)
                    rel = os.path.relpath(root, src)
                    files.append((abs_path, rel))
    return files


def _ping_host(ip: str, timeout: float = 3.0) -> bool:
    """測試遠端主機是否可連線。"""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(f"http://{ip}/api/v1/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def dispatch_distributed_transcode(
    source_dirs: list,
    dest_dir: str,
    compute_hosts: list,
    project_name: str = "",
) -> int:
    """
    將影片檔案分散派發到多個遠端主機進行轉檔。
    compute_hosts: [{name, ip}, ...]，ip='local' 表示本機。
    回傳成功派發的主機數量。
    """
    import urllib.request
    import urllib.error

    # 1. 掃描影片
    all_files = _scan_video_files(source_dirs)
    if not all_files:
        _log.warning("分散式轉檔：無影片檔案")
        return 0

    # 2. 過濾可連線的主機
    reachable = []
    for h in compute_hosts:
        if h.get("ip") == "local":
            reachable.append(h)
        elif _ping_host(h.get("ip", "")):
            reachable.append(h)
        else:
            _log.warning("分散式轉檔：主機 %s (%s) 無法連線", h.get("name"), h.get("ip"))

    if not reachable:
        _log.warning("分散式轉檔：無可用主機")
        return 0

    # 3. Round-robin 分配
    host_buckets = {i: [] for i in range(len(reachable))}
    for idx, (abs_path, rel) in enumerate(all_files):
        host_buckets[idx % len(reachable)].append((abs_path, rel))

    # 4. 派發任務
    dispatched = 0
    for i, host in enumerate(reachable):
        bucket = host_buckets[i]
        if not bucket:
            continue
        sources = [fp for fp, _ in bucket]
        ip = host.get("ip", "local")
        host_name = host.get("name", ip)

        if ip == "local":
            # 本機直接 enqueue
            try:
                from core.worker import enqueue_job  # type: ignore
                host_dest = os.path.join(dest_dir, f"HostDispatch_{host_name}") if len(reachable) > 1 else dest_dir
                req = TranscodeRequest(sources=sources, dest_dir=host_dest, project_name=project_name)
                enqueue_job(req, project_name or "scheduled", "transcode")
                dispatched += 1
            except Exception as e:
                _log.warning("分散式轉檔本機 enqueue 失敗: %s", e)
        else:
            # 遠端 HTTP 派發
            host_dest = os.path.join(dest_dir, f"HostDispatch_{host_name}")
            payload = json.dumps({
                "sources": sources,
                "dest_dir": host_dest,
                "project_name": project_name,
            }).encode("utf-8")
            url = f"http://{ip}/api/v1/jobs/transcode"
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status in (200, 201):
                        dispatched += 1
                    else:
                        _log.warning("分散式轉檔主機 %s 回應 %s", host_name, resp.status)
            except Exception as e:
                _log.warning("分散式轉檔主機 %s 派發失敗: %s", host_name, e)

    _log.info("分散式轉檔派發完成: %d/%d 主機", dispatched, len(reachable))
    return dispatched


# ── 排程檢查與派發 ────────────────────────────────────────────

def _get_machine_id() -> str:
    try:
        from config import load_settings
        return load_settings().get("machine_id", "") or __import__("socket").gethostname()
    except Exception:
        return "unknown"


def _check_and_dispatch() -> int:
    """
    掃描所有排程，到期的送入 enqueue_job() 或透過 HTTP 派發 TTS。
    回傳本次觸發的排程數量。
    DB 優先，JSON fallback。
    """
    from core.worker import enqueue_job  # type: ignore

    due_tasks = []  # list of (schedule_id, task_type, req_data, name)
    used_db = False

    # ─── Phase 1: 收集到期排程並更新狀態 ───────────────────
    if state.db_online:
        try:
            import asyncio
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.repos import schedules_repo

                async def _db_check():
                    nonlocal due_tasks, used_db
                    now = datetime.now()
                    machine_id = _get_machine_id()
                    async with factory() as session:
                        due = await schedules_repo.get_due(session, machine_id, now)
                        for sch in due:
                            sid = sch.get("schedule_id")
                            task_type = sch.get("task_type", "backup")
                            req_data = sch.get("request", {})
                            due_tasks.append((sid, task_type, req_data,
                                              sch.get("name", "scheduled")))
                            # 更新時間
                            cron = sch.get("cron")
                            if cron:
                                next_run_str = compute_next_run(cron, now)
                                next_run_dt = datetime.fromisoformat(next_run_str) if next_run_str else None
                                await schedules_repo.mark_fired(
                                    session, sid, last_run=now,
                                    next_run=next_run_dt, enabled=True,
                                )
                            else:
                                await schedules_repo.mark_fired(
                                    session, sid, last_run=now,
                                    next_run=None, enabled=False,
                                )
                        await session.commit()
                    used_db = True

                # Run async code from sync context
                loop = state.get_main_loop()
                if loop and loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(_db_check(), loop)
                    future.result(timeout=10)
        except Exception as e:
            _log.debug("排程 DB 檢查失敗，回退到 JSON: %s", e)
            due_tasks = []
            used_db = False

    # JSON fallback
    if not used_db:
        with _file_lock:
            schedules = load_schedules()
            now = datetime.now()
            changed = False

            for sch in schedules:
                if not sch.get("enabled", True):
                    continue
                next_run = sch.get("next_run")
                if not next_run:
                    continue
                try:
                    next_dt = datetime.fromisoformat(next_run)
                except (ValueError, TypeError):
                    continue
                if now >= next_dt:
                    task_type = sch.get("task_type", "backup")
                    req_data = sch.get("request", {})
                    due_tasks.append((sch.get("schedule_id"), task_type, req_data,
                                      sch.get("name", "scheduled")))

                    # 更新時間（無論成功與否，避免無限重試）
                    sch["last_run"] = now.isoformat(timespec="seconds")
                    cron = sch.get("cron")
                    if cron:
                        sch["next_run"] = compute_next_run(cron, now)
                    else:
                        sch["enabled"] = False
                        sch["next_run"] = None
                    changed = True

            if changed:
                save_schedules(schedules)

    # ─── Phase 2: 在鎖外派發任務（避免 HTTP 呼叫阻塞鎖） ──
    dispatched = 0
    for schedule_id, task_type, req_data, name in due_tasks:
        if task_type in ("tts", "clone"):
            success = _dispatch_tts(task_type, req_data)
            if success:
                dispatched += 1
            else:
                _log.warning("排程 %s TTS dispatch 失敗", schedule_id)
        elif task_type == "transcode" and req_data.get("compute_hosts"):
            # 分散式轉檔：直接派發到多個主機
            try:
                n = dispatch_distributed_transcode(
                    source_dirs=req_data.get("sources", []),
                    dest_dir=req_data.get("dest_dir", ""),
                    compute_hosts=req_data["compute_hosts"],
                    project_name=req_data.get("project_name", name),
                )
                if n > 0:
                    dispatched += 1
            except Exception as e:
                _log.warning("排程 %s 分散式轉檔 dispatch 失敗: %s", schedule_id, e)
        else:
            try:
                req = build_request(task_type, req_data)
                project_name = getattr(req, "project_name", name)
                enqueue_job(req, project_name, task_type)
                dispatched += 1
            except Exception as e:
                _log.warning("排程 %s dispatch 失敗: %s", schedule_id, e)

    return dispatched


# ── 每日一次、master-only 排程任務底座（財務提醒共用）──────────────

_daily_fired: dict = {}  # {task_key: 'YYYY-MM-DD'} in-process 每日一次去重


async def _run_daily_master_task(task_key: str, hour_key: str, body) -> None:
    """每日一次、只在 master 跑的排程任務底座（_loan_due_check /
    _finance_calendar_check 共用；drone_watcher 另有自己的樣板）。

    gate 次序與各分支的副作用集中於此單一處（手抄易漂的正是這幾個細節）：
    - 當日已成功跑過（_daily_fired[task_key]==today）→ 跳過
    - 🔴 非 master → 標記當日完成並跳過（機隊 9 台共用 mediaguard，只有 master 該發，
      否則同一份提醒天天各機各發 N 份）
    - topology 例外 / DB 未上線 / factory 缺 → **不**標記，下一 tick 重試
    - settings finance.<hour_key>（預設 9）之前 → **不**標記，稍後同日重試
    - 過關 → await body(factory, now)；body 成功回來才標記當日完成
      （body 拋例外 → 不標記、下一 tick 重試）
    body 收 (factory, now)，自理 session 與事件。
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if _daily_fired.get(task_key) == today:
        return
    try:
        from core.topology import is_master_machine
        if not is_master_machine():
            _daily_fired[task_key] = today  # 非 master 今天不用再看
            return
    except Exception:
        return
    if not state.db_online:
        return  # DB 沒上線先不標記，下一 tick 再試
    from db.session import get_session_factory
    factory = get_session_factory()
    if not factory:
        return
    try:
        from config import load_settings
        remind_hour = int((load_settings().get("finance") or {}).get(hour_key) or 9)
    except Exception:
        remind_hour = 9
    if now.hour < remind_hour:  # 上班時間才提醒
        return
    await body(factory, now)
    _daily_fired[task_key] = today


# ── 貸款繳款提醒（財務階段四）─────────────────────────────────

async def _loan_due_check() -> None:
    """每日一次貸款繳款提醒（master-only）— 到期 N 天內 + 逾期未繳期別
    （settings finance.loan_remind_days 預設 7）聚合成單一 Chat 訊息。逾期為即時
    推導（不落庫）。gate/觸發時刻/去重見 _run_daily_master_task。"""
    async def _body(factory, now):
        try:
            from config import load_settings
            remind_days = int((load_settings().get("finance") or {})
                              .get("loan_remind_days") or 7)
        except Exception:
            remind_days = 7
        from core.finance_logic import local_day
        from routers.api_finance import _query_upcoming_payments
        today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        horizon = today0 + timedelta(days=remind_days)
        async with factory() as session:
            rows = await _query_upcoming_payments(session, horizon)
        lines = []
        for p, loan_name in rows:
            d = local_day(p.due_date)
            overdue = bool(d and d < today0)
            amount = (p.principal_due or 0) + (p.interest_due or 0)
            lines.append(f"• {loan_name} 第{p.period_no}期 {amount:,} 元，"
                         f"{d.strftime('%Y-%m-%d') if d else '?'} 到期"
                         f"{'（⚠ 已逾期）' if overdue else ''}")
        if lines:
            from notifier import notify_tab_async
            await notify_tab_async("loan_payment_due",
                                   count=len(lines), lines="\n".join(lines))
    await _run_daily_master_task("loan_due", "loan_remind_hour", _body)


# ── 財務行事曆提醒（財務階段五）─────────────────────────────────

async def _finance_calendar_check() -> None:
    """每日一次財務行事曆提醒（master-only）。所有事件在每月 1 號觸發，依當天
    日期一次把該發的全收進 events 再一併送（事件間不 return，否則月報會消音掉
    同一天的營業稅提醒）；各事件 try/except 隔離。gate/觸發時刻/去重見
    _run_daily_master_task。
      - 每月 1 號 → 上個月三表快報（monthly_finance_report）
      - 單數月(1/3/5/7/9/11) 1 號 → 前兩個月營業稅預估應繳（vat_filing_due）
      - 9 月 1 號 → 營所稅暫繳提醒（income_tax_prepay）
    """
    async def _body(factory, now):
        if now.day != 1:  # 只有每月 1 號有事，其餘日 body 空轉 → 底座標記當日完成
            return
        from core.finance_logic import period_months, shift_month, vat_position
        cur_month = f"{now.year:04d}-{now.month:02d}"
        events: list = []  # [(template_key, kwargs)]，一次收齊當天該發的全部再統一送

        # 月報：每月 1 號 → 上個月三表摘要（statements_for_period 上月）
        prev_month = shift_month(cur_month, -1)
        try:
            from services.finance_statements import statements_for_period
            async with factory() as session:
                st = await statements_for_period(session, period_months(prev_month))
            pnl = st["pnl"]
            meta = st.get("meta") or {}
            warn_parts = []
            if prev_month not in (meta.get("locked_months") or []):
                warn_parts.append("該月尚未鎖帳")
            warn_parts += list(meta.get("warnings") or [])
            warn = ("\n⚠ " + "；".join(warn_parts)) if warn_parts else ""
            events.append(("monthly_finance_report", {
                "month": prev_month,
                "revenue": f"{int(pnl['revenue']['total']):,}",
                "cost": f"{int(pnl['cost']['total']):,}",
                "net": f"{int(pnl['net']['amount']):,}",
                "warn": warn,
            }))
        except Exception as e:
            _log.warning("月報排程計算失敗: %s", e)

        # 營業稅：單數月 1 號 → 前兩個月 vat_position 預估應繳
        if now.month in (1, 3, 5, 7, 9, 11):
            try:
                from services.finance_statements import _load_inputs
                vm = [shift_month(cur_month, -2), shift_month(cur_month, -1)]
                async with factory() as session:
                    inputs = await _load_inputs(session)
                vat = vat_position(inputs["invoices"], inputs["cash_entries"],
                                   inputs["cat_map"], vm)
                net = int(vat["net"])
                estimate = f"{net:,}" if net > 0 else "0（本期留抵）"
                events.append(("vat_filing_due", {
                    "period": f"{vm[0]}~{vm[1]}",
                    "estimate": estimate,
                }))
            except Exception as e:
                _log.warning("營業稅提醒計算失敗: %s", e)

        # 營所稅暫繳：9 月 1 號（無佔位符）
        if now.month == 9:
            events.append(("income_tax_prepay", {}))

        from notifier import notify_tab_async
        for key, kw in events:
            await notify_tab_async(key, **kw)
    await _run_daily_master_task("finance_calendar", "report_remind_hour", _body)


# ── 背景排程迴圈 ──────────────────────────────────────────────

async def run_scheduler():
    """每 60 秒檢查一次到期排程。由 main.py startup 啟動。"""
    await asyncio.sleep(30)  # 讓服務先穩定
    while True:
        try:
            await asyncio.to_thread(_check_and_dispatch)
        except Exception:
            _log.exception("排程檢查迴圈發生異常")
        # 空拍排程監控（每日指定時間觸發）
        try:
            from core import drone_watcher  # type: ignore
            await asyncio.to_thread(drone_watcher.check_and_fire)
        except Exception:
            _log.exception("空拍排程監控檢查異常")
        # 貸款繳款提醒（每日一次；master gate 在函式內）
        try:
            await _loan_due_check()
        except Exception:
            _log.exception("貸款繳款提醒檢查異常")
        # 財務行事曆提醒：月報 / 營業稅 / 營所稅暫繳（每日一次；master gate 在函式內）
        try:
            await _finance_calendar_check()
        except Exception:
            _log.exception("財務行事曆提醒檢查異常")
        await asyncio.sleep(60)
