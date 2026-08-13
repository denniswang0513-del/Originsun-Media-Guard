# -*- coding: utf-8 -*-
"""後端派發的分散式轉檔 —— 失聯偵測與改派。

互動式派發（`app.js` 的 heartbeat）在 2026-08-13 事故後補上了失聯偵測與改派；
**後端派發沒有**，而後端派發正好都是沒人看著的時候在跑：

  - `core/scheduler.dispatch_distributed_transcode`（排程）
  - `core/worker`：備份完成後鏈接的分散式轉檔

對帳的依據不是「那台還活著嗎」，而是**共享的 proxy root 上它交出了什麼**：

    未完成 = 派給它的來源檔 − 它夾子裡已完成的產出

proxy root 大家都讀得到，那台死透了照樣算得出來。這個角度同時涵蓋另一種
ping 得到卻永遠等不到結果的情況：機器活著、但任務被 OTA／重啟吃掉了 ——
它會顯示閒置，而份額還沒做完。

半成品（`*.part.<ext>`、0 byte）不算產出，判定共用 `core_engine`
的 `is_incomplete_output` —— 分兩邊各寫一份的話，總有一天一邊認得、
另一邊不認得，而不認得的那邊會把壞檔當成品交出去。
"""
import os
import json
import time
import logging
import threading
from typing import Dict, List, Optional, Set

from core_engine import is_junk_file, is_incomplete_output  # type: ignore

_log = logging.getLogger("uvicorn.error")

# 連續幾次連不上才判定失聯（tick 每 60 秒一次 → 3 次約 3 分鐘）。
# 給得寬一點：重開機、Defender 冷掃、網路抖一下都不該被當成掛掉。
DEAD_STRIKES = 3
# 「連得上但閒著、份額卻沒做完」要連續幾次才算 —— 一次可能只是剛好在切檔案
IDLE_STRIKES = 2
# 剛派出去的寬限期：POST 回 200 到 worker 真的開始跑之間有落差
START_GRACE_SEC = 180
# 同一支檔最多被改派幾次。每台都轉掛的檔（來源本身壞了）不該無限輪迴
MAX_ATTEMPTS = 2
POLL_TIMEOUT = 5.0
# 紀錄的存活上限。一台卡在「忙碌」永遠不結束（ffmpeg 掛死）就會被盯到天荒地老，
# 這個上限讓它至少會結案並留下 log。24 小時遠大於任何一次正常的分散式轉檔。
MAX_RECORD_AGE_SEC = 24 * 3600

_ACTIVE: List[dict] = []
_lock = threading.Lock()


# ── 對帳：派了什麼 vs 產出了什麼 ──────────────────────────────────

def proxy_stem(path_or_name: str) -> str:
    """來源檔名 → 比對用 stem（proxy 產出是 `<stem>_proxy.mov`）。

    與前端 `app.js` 的 `_proxyStem` 同一套規則，兩邊算出來的必須一致。

    🔴 取檔名一律自己切，不用 `os.path.basename` —— ntpath 會把 `//host/share`
    當成 UNC 的分享根目錄而回傳空字串，而派工清單裡放的正是 UNC 路徑。
    空 stem 會讓「已產出」永遠對不上，於是每一支都被判定沒做完。
    """
    name = str(path_or_name).replace("\\", "/").rsplit("/", 1)[-1]
    stem = os.path.splitext(name)[0].lower()
    if stem.endswith("_proxy"):
        stem = stem[:-6]
    return stem


def produced_stems(dest_dir: str) -> Set[str]:
    """某台的 dispatch 夾裡「真的產出了什麼」（半成品不算）。"""
    stems: Set[str] = set()
    if not dest_dir or not os.path.isdir(dest_dir):
        return stems
    try:
        for root, _dirs, fnames in os.walk(dest_dir):
            for fname in fnames:
                if is_junk_file(fname):
                    continue
                if is_incomplete_output(os.path.join(root, fname)):
                    continue
                stems.add(proxy_stem(fname))
    except OSError as e:
        # 讀不到就當作「什麼都沒產出」—— 寧可重轉，也不要漏掉該補的
        _log.warning("對帳：讀不到 %s（%s），視為零產出", dest_dir, e)
    return stems


def outstanding(assigned: List[str], dest_dirs: List[str]) -> List[str]:
    """派給某台、但還沒有完整產出的來源檔。"""
    done: Set[str] = set()
    for d in dest_dirs:
        done |= produced_stems(d)
    return [f for f in assigned if proxy_stem(f) not in done]


# ── 遠端查詢與派發（local 走行程內佇列）────────────────────────────

def _host_status(ip: str) -> Optional[dict]:
    """{'busy':.., 'queue_length':.., 'active_jobs':{..}}；連不上回 None。"""
    if ip == "local":
        try:
            from core import state  # type: ignore
            return {
                "busy": bool(state.worker_busy),
                "queue_length": state.task_queue.qsize(),
                "active_jobs": {},
            }
        except Exception:
            return None
    import urllib.request
    try:
        # log_offset 給大數字＝只要狀態不要那 2000 條 log（省頻寬，舊版 agent 也吃）
        url = f"http://{ip}/api/v1/status?log_offset=999999999"
        with urllib.request.urlopen(url, timeout=POLL_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def _is_working(status: dict) -> bool:
    return bool(status.get("busy")
                or (status.get("queue_length") or 0) > 0
                or (status.get("active_jobs") or {}))


def post_transcode(ip: str, sources: List[str], dest_dir: str,
                   project_name: str) -> bool:
    """把一批檔案派給某台。成功回 True。"""
    if ip == "local":
        try:
            from core.worker import enqueue_job  # type: ignore
            from core.schemas import TranscodeRequest  # type: ignore
            req = TranscodeRequest(sources=sources, dest_dir=dest_dir,
                                   project_name=project_name)
            enqueue_job(req, project_name or "takeover", "transcode")
            return True
        except Exception as e:
            _log.warning("改派本機 enqueue 失敗: %s", e)
            return False
    import urllib.request
    payload = json.dumps({
        "sources": sources,
        "dest_dir": dest_dir,
        "project_name": project_name,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"http://{ip}/api/v1/jobs/transcode", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        _log.warning("改派主機 %s 失敗: %s", ip, e)
        return False


# ── 派工紀錄 ──────────────────────────────────────────────────────

def register(dest_root: str, project_name: str, assignments: List[dict]) -> None:
    """登記一次派發，交給 `reconcile_tick()` 盯著。

    assignments: [{name, ip, dest_dir, sources: [...], failed: bool}, ...]

    派發當場就失敗的主機也要登記（`failed=True`）—— 它的份額原本是直接被丟掉
    的（log 一行 warning 就沒了），登記進來第一次對帳就會改派給別台。
    """
    hosts = []
    now = time.time()
    for a in assignments:
        sources = list(a.get("sources") or [])
        failed = bool(a.get("failed"))
        # 沒分到檔的主機也留著 —— 它是閒的，正好是最適合接手的那台
        state = "done" if not sources else ("lost" if failed else "running")
        hosts.append({
            "name": a.get("name") or a.get("ip") or "?",
            "ip": a.get("ip") or "local",
            "assigned": sources,
            "dest_dirs": [a["dest_dir"]],
            "state": state,
            "reason": "派發當場就失敗" if failed and sources else "",
            "strikes": 0,
            "idle": 0,
            "dispatched_at": now,
        })
    if not hosts:
        return
    with _lock:
        _ACTIVE.append({
            "dest_root": dest_root,
            "project_name": project_name or "(未命名)",
            "created": now,
            "hosts": hosts,
            "attempts": {},
        })
    _log.info("分散式轉檔已登記對帳：%s（%d 台）", project_name, len(hosts))


def active_records() -> List[dict]:
    """給測試與除錯用的快照。"""
    with _lock:
        return list(_ACTIVE)


def reset() -> None:
    """測試用：清掉所有在盯的紀錄。"""
    with _lock:
        _ACTIVE.clear()


# ── 每 60 秒一次的對帳 ────────────────────────────────────────────

def reconcile_tick() -> None:
    """由 `scheduler.run_scheduler()` 每分鐘呼叫一次。單一 record 出錯不能
    影響其他 record，整個 tick 也絕不能把排程迴圈炸掉。"""
    with _lock:
        records = list(_ACTIVE)
    for rec in records:
        try:
            done = _tick_record(rec)
        except Exception:
            _log.exception("分散式轉檔對帳異常（%s）", rec.get("project_name"))
            continue
        if done:
            with _lock:
                if rec in _ACTIVE:
                    _ACTIVE.remove(rec)


def _tick_record(rec: dict) -> bool:
    """回傳 True 表示這筆已經結案，可以不用再盯。"""
    now = time.time()
    # 派發當場就失敗的（register 標成 lost）也要在第一次對帳時改派出去
    lost = [h for h in rec["hosts"] if h["state"] == "lost" and not h.get("handled")]

    for h in list(rec["hosts"]):
        if h["state"] != "running":
            continue
        status = _host_status(h["ip"])

        if status is None:
            h["strikes"] += 1
            if h["strikes"] >= DEAD_STRIKES:
                h["state"] = "lost"
                h["reason"] = f"連續 {h['strikes']} 次連不上"
                lost.append(h)
            continue

        h["strikes"] = 0
        if _is_working(status):
            h["idle"] = 0
            continue

        # 連得上、也沒在忙 —— 份額做完了嗎？
        if not outstanding(h["assigned"], h["dest_dirs"]):
            h["state"] = "done"
            continue
        if now - h["dispatched_at"] < START_GRACE_SEC:
            continue    # 剛派出去，還沒開跑不算數
        h["idle"] += 1
        if h["idle"] >= IDLE_STRIKES:
            h["state"] = "lost"
            h["reason"] = "閒置中但份額未完成（任務可能被重啟／OTA 吃掉）"
            lost.append(h)

    if lost:
        _redispatch(rec, lost)

    if any(h["state"] == "running" for h in rec["hosts"]):
        if now - rec["created"] < MAX_RECORD_AGE_SEC:
            return False
        _log.error("分散式轉檔（%s）超過 %d 小時仍未結束，停止對帳",
                   rec["project_name"], MAX_RECORD_AGE_SEC // 3600)
    _finish(rec)
    return True


def _redispatch(rec: dict, lost: List[dict]) -> None:
    pending: List[str] = []
    exhausted: List[str] = []
    for h in lost:
        h["handled"] = True
        _log.warning("分散式轉檔：%s 失聯（%s）", h["name"], h.get("reason"))
        for f in outstanding(h["assigned"], h["dest_dirs"]):
            if rec["attempts"].get(f, 0) >= MAX_ATTEMPTS:
                exhausted.append(f)     # 每台都轉掛 → 來源本身有問題，別再輪迴
            elif f not in pending:
                pending.append(f)

    if exhausted:
        _log.error("分散式轉檔：%d 支檔案改派 %d 次仍未產出，放棄：%s",
                   len(exhausted), MAX_ATTEMPTS,
                   ", ".join(os.path.basename(f) for f in exhausted[:5]))
        # 放棄的檔案一樣要出聲 —— 這是「有人得手動處理」的訊號
        _notify_lost(rec, lost, exhausted, recovered=False)
    if not pending:
        return

    # 誰能接手：這筆派工裡的其他主機，且**現在**問得到狀態。
    # 掉過的那幾台不再列入 —— 跟前端 heartbeat 的黑名單同一個道理。
    dead_ips = {h["ip"] for h in rec["hosts"] if h["state"] == "lost"}
    seen, takers = set(), []
    for h in rec["hosts"]:
        if h["ip"] in dead_ips or h["ip"] in seen:
            continue
        seen.add(h["ip"])
        if _host_status(h["ip"]) is not None:
            takers.append(h)

    if not takers:
        _notify_lost(rec, lost, pending, recovered=False)
        _log.error("分散式轉檔：沒有可接手的主機，%d 支檔案沒人做", len(pending))
        return

    now = time.time()
    buckets: Dict[int, List[str]] = {i: [] for i in range(len(takers))}
    for i, f in enumerate(pending):
        buckets[i % len(takers)].append(f)

    handed = 0
    for i, taker in enumerate(takers):
        files = buckets[i]
        if not files:
            continue
        dest = os.path.join(rec["dest_root"], f"HostDispatch_Takeover_{taker['name']}")
        if not post_transcode(taker["ip"], files, dest, rec["project_name"]):
            continue
        for f in files:
            rec["attempts"][f] = rec["attempts"].get(f, 0) + 1
        # 接手的份額同樣要被盯著 —— 接手的那台也可能掛
        rec["hosts"].append({
            "name": taker["name"],
            "ip": taker["ip"],
            "assigned": files,
            "dest_dirs": [dest],
            "state": "running",
            "strikes": 0,
            "idle": 0,
            "dispatched_at": now,
        })
        handed += len(files)
        _log.info("分散式轉檔：%d 支改派給 %s", len(files), taker["name"])

    _notify_lost(rec, lost, pending, recovered=handed > 0)


def _finish(rec: dict) -> None:
    remaining: List[str] = []
    for h in rec["hosts"]:
        remaining += outstanding(h["assigned"], h["dest_dirs"])
    total = sum(len(h["assigned"]) for h in rec["hosts"])
    if remaining:
        _log.error("分散式轉檔結束（%s）：%d 支仍未產出", rec["project_name"], len(remaining))
    else:
        _log.info("分散式轉檔結束（%s）：%d 份額全部產出", rec["project_name"], total)


def _notify_lost(rec: dict, lost: List[dict], pending: List[str],
                 recovered: bool) -> None:
    """機器掉了就要出聲 —— 排程跑的時候沒人在看畫面。Best-effort。"""
    try:
        from notifier import notify_tab
        notify_tab(
            "dispatch_host_lost",
            project_name=rec["project_name"],
            hosts="、".join(h["name"] for h in lost),
            reason=lost[0].get("reason", "?") if lost else "?",
            file_count=len(pending),
            action="已改派給其他主機" if recovered else "🔴 沒有可接手的主機，這些檔案沒人做",
        )
    except Exception as e:
        _log.warning("失聯告警發送失敗: %s", e)
