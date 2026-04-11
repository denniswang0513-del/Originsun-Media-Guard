import os
import asyncio
import uuid
from typing import Any
from datetime import datetime as _dt

from core.socket_mgr import sio  # type: ignore
from core.engine_inst import create_engine  # type: ignore
import core.state as state  # type: ignore
from core.logger import _emit_sync_for_job, _write_log_to_file  # type: ignore

import re

from core.schemas import (  # type: ignore
    BackupRequest, TranscodeRequest, ConcatRequest,
    VerifyRequest, TranscribeRequest, ReportJobRequest,
    TtsRequest, TtsCloneRequest, DroneMetaRequest,
    TimelineExportRequest,
)
from core_engine import ReportManifest  # type: ignore


# ── Public API ───────────────────────────────────────────────

async def enqueue_job(request: Any, project_name: str, task_type: str) -> tuple:
    """
    Register a new job and trigger the dispatcher.
    Returns (job_id, warning_or_none).
    If a duplicate (same project_name + task_type) is already active,
    the job is still accepted but a warning string is returned.
    """
    warning = None
    dup = state.find_duplicate(project_name, task_type)
    if dup:
        warning = f"專案 '{project_name}' 已有進行中的 {task_type} 任務 (job_id={dup.job_id})，新任務已加入排隊"

    job_id = getattr(request, 'job_id', '') or uuid.uuid4().hex[:8]
    request.job_id = job_id

    job = state.JobState(
        job_id=job_id,
        task_type=task_type,
        project_name=project_name,
        request=request,
        created_at=_dt.now().isoformat(),
        client_sid=getattr(request, 'client_sid', ''),
    )
    state.register_job(job)

    # Emit job_queued event
    _emit_sync_for_job(job_id, 'log', {
        'type': 'system',
        'msg': f'任務已加入佇列: {project_name} ({task_type})'
    })

    # emit queued 狀態 + 觸發 dispatch（fire-and-forget，不阻塞 HTTP response）
    await sio.emit('task_status', {
        'status': 'queued', 'job_id': job_id,
        'project_name': project_name, 'task_type': task_type
    })
    asyncio.ensure_future(_try_dispatch(task_type))

    return job_id, warning


# ── Priority Dispatcher ─────────────────────────────────────

def _pick_next_job(task_type: str):
    """Pick the highest-priority QUEUED/WAITING job for this task_type."""
    candidates = [
        j for j in state.get_all_jobs().values()
        if j.task_type == task_type
        and j.status in (state.JobStatus.QUEUED, state.JobStatus.WAITING)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda j: (j.priority, j.created_at))
    return candidates[0]


async def _try_dispatch(task_type: str):
    """Check for free slots and dispatch highest-priority queued jobs."""
    while state.can_dispatch(task_type):
        job = _pick_next_job(task_type)
        if not job:
            break
        state.increment_active(task_type)
        job.status = state.JobStatus.RUNNING
        job.started_at = _dt.now().isoformat()
        await sio.emit('task_status', {
            'status': 'running', 'job_id': job.job_id,
            'project_name': job.project_name, 'task_type': task_type
        })
        asyncio.ensure_future(_run_and_finish(job))


async def _run_and_finish(job: state.JobState):
    """Execute a job, then release the slot and trigger next dispatch."""
    try:
        await _execute_job(job)
    finally:
        state.decrement_active(job.task_type)
        await _try_dispatch(job.task_type)


async def _execute_job(job: state.JobState):
    """Execute a single job within a semaphore slot."""
    job_id = job.job_id
    task = job.request
    task_type = job.task_type

    # Mark running
    job.status = state.JobStatus.RUNNING
    job.started_at = _dt.now().isoformat()

    # Create per-job engine
    engine = create_engine(job_id)
    job.engine = engine

    # Set up log file
    log_dir = _resolve_log_dir(job)
    if log_dir:
        safe_name = str(job.project_name).replace(".", "_").replace(" ", "_")
        dt_str = _dt.now().strftime("%Y%m%d_%H%M%S")
        job.log_file_path = os.path.join(log_dir, f"{safe_name}_Log_{dt_str}.txt")
        _write_log_to_file(job.log_file_path, f"=== Originsun Media Guard Pro {task_type} 任務開始 ===")

    # Reset per-job conflict state
    job.global_conflict_action = None

    await sio.emit('task_status', {
        'status': 'running', 'job_id': job_id,
        'project_name': job.project_name, 'task_type': task_type
    })
    _emit_sync_for_job(job_id, 'log', {
        'type': 'system',
        'msg': f'開始執行任務: {job.project_name} ({task_type})'
    })

    # ── Per-job callbacks ──
    def _on_progress(data: dict):
        data_with_id = {**data, "job_id": job_id}
        job.progress = data_with_id
        # 累積統計供完成摘要使用
        for key in ('total_files', 'total_bytes', 'done_files', 'done_bytes'):
            if key in data:
                setattr(job, key, data[key])
        for src, dst in (('matched', 'verify_matched'), ('mismatched', 'verify_mismatched')):
            if src in data:
                setattr(job, dst, data[src])
        _emit_sync_for_job(job_id, 'progress', data)

    def _on_conflict(data: dict) -> str:
        if job.global_conflict_action:
            return job.global_conflict_action
        job.conflict_event.clear()
        _emit_sync_for_job(job_id, 'file_conflict', data)
        if not job.conflict_event.wait(timeout=60):
            return 'skip'
        return job.current_conflict_action

    try:
        # ── Dispatch by task type ──
        if isinstance(task, BackupRequest):
            await _run_backup(job, engine, task, _on_progress, _on_conflict)

        elif isinstance(task, TranscodeRequest):
            await _run_transcode(job, engine, task, _on_progress)

        elif isinstance(task, ConcatRequest):
            await _run_concat(job, engine, task, _on_progress)

        elif isinstance(task, TranscribeRequest):
            await _run_transcribe(job, engine, task)

        elif isinstance(task, VerifyRequest):
            await _run_verify(job, engine, task, _on_progress)

        elif isinstance(task, ReportJobRequest):
            await _run_report(job, task)

        elif isinstance(task, TtsCloneRequest):
            await _run_tts_clone(job, task)

        elif isinstance(task, TtsRequest):
            await _run_tts(job, task)

        elif isinstance(task, DroneMetaRequest):
            await _run_drone_meta(job, engine, task, _on_progress)

        elif isinstance(task, TimelineExportRequest):
            await _run_timeline_export(job, engine, task, _on_progress)

        # Mark done
        job.status = state.JobStatus.DONE
        job.finished_at = _dt.now().isoformat()
        # 計算耗時
        _elapsed = 0
        if job.started_at:
            try:
                _elapsed = (_dt.fromisoformat(job.finished_at) - _dt.fromisoformat(job.started_at)).total_seconds()
            except Exception:
                pass
        await sio.emit('task_status', {
            'status': 'done', 'job_id': job_id,
            'summary': {
                'task_type': task_type,
                'total_files': job.total_files,
                'total_bytes': job.total_bytes,
                'done_bytes': job.done_bytes,
                'elapsed_sec': _elapsed,
                'verify_matched': job.verify_matched,
                'verify_mismatched': job.verify_mismatched,
            }
        })

    except asyncio.CancelledError:
        job.status = state.JobStatus.CANCELLED
        job.finished_at = _dt.now().isoformat()
        await sio.emit('task_status', {'status': 'cancelled', 'job_id': job_id})

    except Exception as e:
        job.status = state.JobStatus.ERROR
        job.error_detail = str(e)
        job.finished_at = _dt.now().isoformat()
        _emit_sync_for_job(job_id, 'log', {'type': 'error', 'msg': f'任務執行失敗: {e}'})
        await sio.emit('task_status', {'status': 'error', 'detail': str(e), 'job_id': job_id})

    finally:
        if log_dir and job.engine:
            try:
                await asyncio.to_thread(
                    job.engine.save_job_log, log_dir,
                    f"{job.project_name} 工作日誌"
                )
            except Exception:
                pass
        _persist_job_history(job)


# ── Task-type Dispatch Functions ─────────────────────────────

async def _run_backup(job, engine, task: BackupRequest, _on_progress, _on_conflict):
    """Backup + optional chained transcode + concat + report."""
    manifest = ReportManifest(
        project_name=task.project_name,
        local_root=task.local_root,
        nas_root=task.nas_root,
        proxy_root=task.proxy_root,
    ) if getattr(task, 'do_report', False) else None

    await asyncio.to_thread(
        engine.run_backup_job,
        sources=task.cards,
        local_root=task.local_root,
        nas_root=task.nas_root,
        project_name=task.project_name,
        do_hash=task.do_hash,
        on_progress=_on_progress,
        on_conflict=_on_conflict,
        manifest=manifest,
        do_report=getattr(task, 'do_report', False)
    )

    # Chained transcode (isolated — failure won't block concat/report)
    compute_hosts = getattr(task, "compute_hosts", []) or []
    has_remote_hosts = any(h.get("ip") != "local" for h in compute_hosts) if compute_hosts else False

    if getattr(task, "do_transcode", False) and has_remote_hosts:
        # 分散式轉檔：派發到遠端主機
        try:
            from core.scheduler import dispatch_distributed_transcode  # type: ignore
            source_dirs = [
                os.path.join(task.local_root, task.project_name, card_name)
                for card_name, _ in task.cards
                if os.path.exists(os.path.join(task.local_root, task.project_name, card_name))
            ]
            _emit_sync_for_job(job.job_id, 'log', {
                'type': 'system',
                'msg': f'[transcode] 啟動分散式轉檔，派發至 {len(compute_hosts)} 台主機...'
            })
            n = await asyncio.to_thread(
                dispatch_distributed_transcode,
                source_dirs=source_dirs,
                dest_dir=os.path.join(task.proxy_root, task.project_name),
                compute_hosts=compute_hosts,
                project_name=task.project_name,
            )
            _emit_sync_for_job(job.job_id, 'log', {
                'type': 'system',
                'msg': f'[transcode] 分散式轉檔已派發 {n} 台主機'
            })
        except Exception as ex:
            _emit_sync_for_job(job.job_id, 'log', {
                'type': 'error',
                'msg': f'[transcode] 分散式轉檔派發失敗: {ex}'
            })
    elif getattr(task, "do_transcode", False):
        # 本機轉檔
        _tc_card_total = len(task.cards)
        for _tc_card_idx, (card_name, _) in enumerate(task.cards):
            if engine._stop_event.is_set():
                break
            src_dir = os.path.join(task.local_root, task.project_name, card_name)
            dest_dir = os.path.join(task.proxy_root, task.project_name, card_name)
            if not os.path.exists(src_dir):
                _emit_sync_for_job(job.job_id, 'log', {
                    'type': 'error',
                    'msg': f'[transcode] 來源目錄不存在，已跳過: {src_dir}'
                })
                continue
            try:
                await asyncio.to_thread(
                    engine.run_transcode_job,
                    sources=[src_dir], dest_dir=dest_dir,
                    on_progress=_make_card_progress(_tc_card_idx, _tc_card_total, _on_progress)
                )
            except Exception as ex:
                _emit_sync_for_job(job.job_id, 'log', {
                    'type': 'error',
                    'msg': f'[transcode] {card_name} 轉檔失敗（不影響其他卡及串帶/報表）: {ex}'
                })

    # Chained concat (isolated — failure won't block report)
    if getattr(task, "do_concat", False):
        cc_resolution = getattr(task, 'concat_resolution', '720P')
        cc_codec = getattr(task, 'concat_codec', 'H.264 (NVENC)')
        cc_burn_tc = getattr(task, 'concat_burn_tc', True)
        cc_burn_fn = getattr(task, 'concat_burn_fn', False)
        _cc_total = len(task.cards)
        for _cc_idx, (card_name, _) in enumerate(task.cards):
            if engine._stop_event.is_set():
                break
            src_dir = os.path.join(
                task.local_root,
                task.project_name, card_name
            )
            dest_dir = os.path.join(task.proxy_root, task.project_name, card_name)
            if not os.path.exists(src_dir):
                _emit_sync_for_job(job.job_id, 'log', {
                    'type': 'error',
                    'msg': f'[concat] 來源目錄不存在，已跳過: {src_dir}'
                })
                continue
            try:
                await asyncio.to_thread(
                    engine.run_concat_job,
                    sources=[src_dir], dest_dir=dest_dir,
                    custom_name=f"{task.project_name}_{card_name}_reel",
                    resolution=cc_resolution, codec=cc_codec,
                    burn_timecode=cc_burn_tc, burn_filename=cc_burn_fn,
                    on_progress=_make_card_progress(_cc_idx, _cc_total, _on_progress, track_files=True)
                )
            except Exception as ex:
                _emit_sync_for_job(job.job_id, 'log', {
                    'type': 'error',
                    'msg': f'[concat] {card_name} 串帶失敗（不影響其他卡及報表）: {ex}'
                })

    # Post-task report (isolated)
    if isinstance(task, BackupRequest) and getattr(task, 'do_report', False):
        try:
            local_dir = os.path.join(task.local_root, task.project_name)
            rpt_output = getattr(task, 'report_output', '') or task.local_root
            rpt_name = getattr(task, 'report_name', '') or task.project_name
            rpt_filmstrip = getattr(task, 'report_filmstrip', True)
            rpt_techspec = getattr(task, 'report_techspec', True)
            rpt_hash = getattr(task, 'report_hash', False)

            await sio.emit('progress', {
                'phase': 'report', 'status': 'processing',
                'current_file': 'backup done, starting report scan...',
                'total_pct': 0, 'job_id': job.job_id
            })

            from core.report_job import _run_report_job  # type: ignore
            report_req = ReportJobRequest(**{
                "source_dir": local_dir,
                "output_dir": rpt_output,
                "nas_root": task.nas_root,
                "report_name": rpt_name,
                "do_filmstrip": rpt_filmstrip, "do_techspec": rpt_techspec,
                "do_hash": rpt_hash,
                "do_gdrive": False, "do_gchat": False, "do_line": False,
                "exclude_dirs": [os.path.join(task.proxy_root, task.project_name)] if task.proxy_root else [],
            })
            report_job_id = uuid.uuid4().hex[:8]
            report_req.job_id = report_job_id
            asyncio.create_task(_run_report_job(report_req, report_job_id))

            await sio.emit('progress', {
                'phase': 'report', 'status': 'processing',
                'current_file': 'report scanning (check report tab for progress)',
                'total_pct': 50, 'job_id': job.job_id
            })
        except Exception as ex:
            _emit_sync_for_job(job.job_id, 'log', {
                'type': 'error',
                'msg': f'[report] report failed: {ex}'
            })

    elif isinstance(task, BackupRequest) and not getattr(task, 'do_report', False):
        try:
            from notifier import notify_all  # type: ignore
            file_count = 0
            total_size = 0.0
            local_dir = os.path.join(task.local_root, task.project_name)
            if os.path.exists(local_dir):
                for root, _, files in os.walk(local_dir):
                    for f in files:
                        p = os.path.join(str(root), f)
                        if not os.path.islink(p):
                            file_count += 1
                            total_size += os.path.getsize(p)
            await asyncio.to_thread(notify_all, task.project_name, None, file_count, total_size)
        except Exception:
            pass


async def _run_transcode(job, engine, task: TranscodeRequest, _on_progress):
    await asyncio.to_thread(
        engine.run_transcode_job,
        sources=task.sources, dest_dir=task.dest_dir,
        on_progress=_on_progress
    )
    try:
        from notifier import notify_tab  # type: ignore
        _fc = sum(
            len(list(os.walk(str(src)))) and sum(len(f) for _, _, f in os.walk(str(src)))
            for src in task.sources if os.path.isdir(str(src))
        )
        _proj = os.path.basename(task.dest_dir)
        await asyncio.to_thread(
            notify_tab, "transcode_success",
            project_name=_proj, dest_dir=task.dest_dir, file_count=_fc
        )
    except Exception:
        pass


async def _run_concat(job, engine, task: ConcatRequest, _on_progress):
    await asyncio.to_thread(
        engine.run_concat_job,
        sources=task.sources, dest_dir=task.dest_dir,
        custom_name=task.custom_name,
        resolution=task.resolution, codec=task.codec,
        burn_timecode=task.burn_timecode, burn_filename=task.burn_filename,
        on_progress=_on_progress
    )


async def _run_transcribe(job, engine, task: TranscribeRequest):
    job_id = job.job_id
    try:
        import transcriber  # type: ignore
        import importlib
        importlib.reload(transcriber)
    except ImportError as ie:
        err_msg = f"無法載入語音辨識模組: {ie}"
        await sio.emit('log', {'type': 'error', 'msg': err_msg, 'job_id': job_id})
        await sio.emit('transcribe_error', {'msg': err_msg, 'job_id': job_id})
        return

    safe_dest = task.dest_dir.rstrip("\\/") if task.dest_dir else ""
    _proj = os.path.basename(safe_dest) or "transcribe"

    _loop = asyncio.get_running_loop()

    _tr_total = len(task.sources)

    def _on_prog(pct, msg):
        if engine._stop_event and engine._stop_event.is_set():
            raise Exception("使用者強制中止任務")
        # 從 msg 解析 [idx/total] 格式
        _m = re.match(r'\[(\d+)/(\d+)\]', msg)
        _done = int(_m.group(1)) if _m else 0
        _tot = int(_m.group(2)) if _m else _tr_total
        asyncio.run_coroutine_threadsafe(
            sio.emit('transcribe_progress', {
                'pct': pct, 'msg': msg, 'job_id': job_id,
                'done_files': _done, 'total_files': _tot,
            }),
            _loop
        )

    res = await asyncio.to_thread(
        transcriber.run_transcribe_job,
        sources=task.sources, dest_dir=task.dest_dir,
        project_name=_proj, model_size=task.model_size,
        output_srt=task.output_srt, output_txt=task.output_txt,
        output_wav=task.output_wav, generate_proxy=task.generate_proxy,
        individual_mode=task.individual_mode,
        progress_callback=_on_prog,
        check_cancel_cb=engine._stop_event.is_set
    )

    if res.get("success"):
        await sio.emit('transcribe_done', {'dest_dir': res.get("dest_dir"), 'job_id': job_id})
        try:
            from notifier import notify_tab  # type: ignore
            await asyncio.to_thread(
                notify_tab, "transcribe_success",
                project_name=_proj, dest_dir=task.dest_dir,
                file_count=res.get("file_count", 0)
            )
        except Exception:
            pass
    else:
        err_msg = res.get("error", "未知錯誤")
        await sio.emit('log', {'type': 'error', 'msg': f'逐字稿發生錯誤: {err_msg}', 'job_id': job_id})
        await asyncio.sleep(0.1)
        await sio.emit('transcribe_error', {'msg': err_msg, 'job_id': job_id})
        raise Exception(err_msg)


async def _run_verify(job, engine, task: VerifyRequest, _on_progress):
    await asyncio.to_thread(
        engine.run_verify_job,
        pairs=task.pairs, mode=task.mode, on_progress=_on_progress
    )
    try:
        from notifier import notify_tab  # type: ignore
        _total = len(task.pairs)
        _fail = sum(1 for line in engine._log_buffer if '[FAIL]' in line or 'FAIL' in line.upper())
        _pass = _total - _fail
        _proj = os.path.basename(task.pairs[0][0]) if task.pairs else "Unknown"
        await asyncio.to_thread(
            notify_tab, "verify_success",
            project_name=_proj, pass_count=_pass,
            fail_count=_fail, total_count=_total
        )
    except Exception:
        pass


def _tts_normalize(text: str, use_taiwan: bool) -> str:
    """Apply Taiwan normalizer if enabled."""
    if use_taiwan:
        try:
            from utils.taiwan_normalizer import normalize_for_taiwan_tts
            return normalize_for_taiwan_tts(text)
        except Exception:
            pass
    return text


def _tts_progress_cb(job_id: str):
    """Create a TTS progress callback for the given job."""
    from core.logger import _emit_sync_for_job  # type: ignore
    def _cb(data: dict):
        _emit_sync_for_job(job_id, 'progress', {
            'phase': 'tts',
            'total_pct': data.get('pct', 0),
            'current_file': data.get('msg', ''),
            'done_files': 1 if data.get('pct', 0) >= 100 else 0,
            'total_files': 1,
        })
    return _cb


def _tts_finalize(job, output_path: str):
    """Set job stats after TTS completion."""
    job.total_files = 1
    job.done_files = 1
    try:
        job.total_bytes = os.path.getsize(output_path)
    except Exception:
        pass


async def _run_tts(job, task: TtsRequest):
    """Run TTS job within the slot worker."""
    from tts_engine import run_edge_tts  # type: ignore
    from core.logger import _emit_sync_for_job  # type: ignore

    text = _tts_normalize(task.text, task.use_taiwan)
    os.makedirs(task.output_dir, exist_ok=True)
    output_path = os.path.join(task.output_dir, task.output_name + ".mp3")

    await run_edge_tts(
        text, task.voice,
        int(task.rate) if task.rate else 0,
        int(task.pitch) if task.pitch else 0,
        output_path, progress_cb=_tts_progress_cb(job.job_id),
    )

    _emit_sync_for_job(job.job_id, 'log', {'type': 'system', 'msg': f'TTS 完成：{output_path}'})
    _tts_finalize(job, output_path)

    try:
        from notifier import notify_tab  # type: ignore
        await asyncio.to_thread(
            notify_tab, "tts_success",
            project_name=task.output_name, dest_dir=task.output_dir
        )
    except Exception:
        pass


async def _run_tts_clone(job, task: TtsCloneRequest):
    """Run F5-TTS voice clone job within the slot worker."""
    from tts_engine import _f5_clone_sync, _ensure_tts_imports  # type: ignore
    from core.logger import _emit_sync_for_job  # type: ignore
    _ensure_tts_imports()

    text = _tts_normalize(task.text, task.use_taiwan)
    os.makedirs(task.output_dir, exist_ok=True)
    output_path = os.path.join(task.output_dir, task.output_name + ".wav")

    await asyncio.to_thread(
        _f5_clone_sync, text, task.reference_audio, output_path,
        _tts_progress_cb(job.job_id), task.speed, task.pitch, task.ref_text,
    )

    _emit_sync_for_job(job.job_id, 'log', {'type': 'system', 'msg': f'聲音複製完成：{output_path}'})
    _tts_finalize(job, output_path)


async def _run_report(job, task: ReportJobRequest):
    """Run report job within the slot worker (for standalone report submissions)."""
    from core.report_job import _run_report_job  # type: ignore
    job.report_pause_event = asyncio.Event()
    job.report_pause_event.set()  # start unpaused
    await _run_report_job(task, job.job_id)


# ── Progress Scaling ─────────────────────────────────────────

def _make_card_progress(idx, total, on_progress, *, track_files=False):
    """Factory: scale per-card progress (0-100%) into cross-card aggregate."""
    def _cb(data):
        pct = data.get('total_pct', 0)
        scaled = {**data, 'total_pct': (idx / total + pct / 100 / total) * 100}
        if track_files:
            scaled['done_files'] = idx + (1 if pct >= 100 else 0)
            scaled['total_files'] = total
        on_progress(scaled)
    return _cb


# ── Helper Functions ─────────────────────────────────────────

def _resolve_log_dir(job: state.JobState) -> str:
    """Determine the directory for this job's log file.

    Priority: NAS logs_dir (shared, persistent) → local fallback.
    """
    # Try NAS shared logs directory first
    try:
        from config import load_settings
        settings = load_settings()
        nas_logs = settings.get("nas_paths", {}).get("logs_dir", "")
        if nas_logs and os.path.isdir(nas_logs):
            return nas_logs
    except Exception:
        pass

    # Fallback to local directories
    task = job.request
    if isinstance(task, BackupRequest):
        return os.path.join(task.local_root, task.project_name)
    elif isinstance(task, (TranscodeRequest, ConcatRequest)):
        import tempfile
        return tempfile.gettempdir()
    elif isinstance(task, VerifyRequest) and task.pairs:
        return task.pairs[0][1]
    elif isinstance(task, ReportJobRequest):
        return task.output_dir or task.source_dir
    return ""


def _persist_job_history(job: state.JobState):
    """Write completed job summary to job_history.json."""
    try:
        from routers.api_job_history import append_history  # type: ignore
        append_history({
            "job_id": job.job_id,
            "task_type": job.task_type,
            "project_name": job.project_name,
            "status": job.status.value if hasattr(job.status, 'value') else str(job.status),
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "error_detail": job.error_detail,
            "log_file": job.log_file_path or "",
        })
    except Exception as e:
        print(f"Failed to persist job history: {e}")


# ── Drone Metadata Writer ──────────────────────────────────────

async def _run_drone_meta(job, engine, task: DroneMetaRequest, _on_progress):
    await asyncio.to_thread(_drone_meta_sync, job, engine, task, _on_progress)


def _drone_meta_sync(job, engine, task: DroneMetaRequest, _on_progress):
    import subprocess
    import datetime as _datetime
    import json

    job_id = job.job_id
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    exiftool = os.path.join(project_root, "exiftool.exe")
    ffmpeg_bin = os.path.join(project_root, "ffmpeg.exe")
    HNO = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)

    # Validate tools
    if not os.path.exists(exiftool):
        _emit_sync_for_job(job_id, 'log', {'type': 'error', 'msg': f'找不到 exiftool.exe: {exiftool}'})
        raise FileNotFoundError(f"exiftool.exe not found: {exiftool}")
    if not os.path.exists(ffmpeg_bin):
        _emit_sync_for_job(job_id, 'log', {'type': 'error', 'msg': f'找不到 ffmpeg.exe: {ffmpeg_bin}'})
        raise FileNotFoundError(f"ffmpeg.exe not found: {ffmpeg_bin}")

    # Parse datetime
    try:
        dt = _datetime.datetime.fromisoformat(task.date_time)
    except Exception as e:
        _emit_sync_for_job(job_id, 'log', {'type': 'error', 'msg': f'日期格式錯誤: {e}'})
        raise ValueError(f"Invalid date_time: {e}")

    exif_dt_str = dt.strftime("%Y:%m:%d %H:%M:%S")
    iso_dt_str = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    total = len(task.files)
    success_count = 0
    fail_list = []
    new_file_paths = []

    for idx, file_setting in enumerate(task.files):
        fpath = file_setting.path
        current_index = task.file_index + idx
        new_name = f"MAX_{current_index:04d}.MOV"
        out_dir = task.output_dir or os.path.dirname(fpath)
        new_path = os.path.join(out_dir, new_name)

        _on_progress({
            'phase': 'drone_meta',
            'total_files': total,
            'done_files': idx,
            'current_file': os.path.basename(fpath),
            'target_file': new_name,
            'file_pct': 0,
            'total_pct': round(idx / total * 100, 1),
        })
        _emit_sync_for_job(job_id, 'log', {
            'type': 'info',
            'msg': f'[{idx + 1}/{total}] 處理中: {os.path.basename(fpath)} → {new_name}'
        })

        # Per-file time override
        if file_setting.date_time_override:
            file_dt = _datetime.datetime.fromisoformat(file_setting.date_time_override)
        else:
            file_dt = dt  # global datetime
        file_exif_dt = file_dt.strftime("%Y:%m:%d %H:%M:%S")
        file_iso_dt = file_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Get video duration via ffprobe
        _probe_cmd = [ffmpeg_bin.replace('ffmpeg', 'ffprobe'), '-v', 'quiet',
                      '-print_format', 'json', '-show_format', fpath]
        _probe_r = subprocess.run(_probe_cmd, capture_output=True, text=True,
                                  encoding='utf-8', errors='ignore', creationflags=HNO)
        _file_duration = 0
        try:
            _file_duration = int(float(json.loads(_probe_r.stdout).get('format', {}).get('duration', 0)))
        except Exception:
            pass

        # Detect DJI and generate Autel subtitle
        from utils.dji_meta_parser import parse_dji_meta
        dji_data = parse_dji_meta(fpath, ffmpeg_path=ffmpeg_bin)
        is_dji = dji_data.get('is_dji', False) and bool(dji_data.get('frames'))
        autel_srt_path = None

        if is_dji:
            _emit_sync_for_job(job_id, 'log', {
                'type': 'info',
                'msg': f'偵測到 DJI 檔案 ({dji_data.get("device", "unknown")})，產生 Autel 字幕...'
            })
            autel_srt_path = os.path.join(out_dir, f"_autel_{current_index:04d}.srt")
            home = dji_data['frames'][0]
            # Autel format: 1 subtitle entry per second
            duration_sec = _file_duration or int(len(dji_data['frames']) / 60)
            # Sample frames evenly: pick 1 per second
            total_frames = len(dji_data['frames'])
            step = max(1, total_frames // max(duration_sec, 1))
            sampled = [dji_data['frames'][min(i * step, total_frames - 1)] for i in range(duration_sec)]
            with open(autel_srt_path, 'w', encoding='utf-8') as sf:
                for i, frame in enumerate(sampled):
                    frame_dt = file_dt + _datetime.timedelta(seconds=i)
                    dt_str = frame_dt.strftime("%Y-%m-%d %H:%M:%S")
                    sf.write(f"{i+1}\n")
                    t1 = f"{i//3600:02d}:{(i%3600)//60:02d}:{i%60:02d},000"
                    t2 = f"{(i+1)//3600:02d}:{((i+1)%3600)//60:02d}:{(i+1)%60:02d},000"
                    sf.write(f"{t1} --> {t2}\n")
                    sf.write(f"HOME(E: {home['lon']:.6f}, N: {home['lat']:.6f}) {dt_str}")
                    sf.write(f".GPS(E: {frame['lon']:.6f}, N: {frame['lat']:.6f}, {frame['alt']:.2f}m) ")
                    sf.write(f".ISO:{int(frame['iso'])} SHUTTER:{int(frame['shutter'])} EV:{frame['ev']:.1f} F-NUM:{frame['fnum']:.1f} ")
                    f_pry = frame.get('f_pry', (0, 0, 0))
                    g_pry = frame.get('g_pry', (0, 0, 0))
                    sf.write(f".F.PRY ({f_pry[0]:.1f}\u00b0, {f_pry[1]:.1f}\u00b0, {f_pry[2]:.1f}\u00b0), ")
                    sf.write(f"G.PRY ({g_pry[0]:.1f}\u00b0, {g_pry[1]:.1f}\u00b0, {g_pry[2]:.1f}\u00b0)\n\n")

        # Determine if re-encoding is needed
        has_trim = file_setting.trim_in > 0 or file_setting.trim_out >= 0
        has_color = (
            file_setting.brightness != 0.0 or
            file_setting.contrast != 1.0 or
            file_setting.saturation != 1.0 or
            file_setting.gamma != 1.0 or
            file_setting.color_temp != 0.0
        )
        needs_reencode = has_trim or has_color

        # Build ffmpeg command
        ff_cmd = [ffmpeg_bin, "-y"]

        if has_trim and file_setting.trim_in > 0:
            ff_cmd += ["-ss", str(file_setting.trim_in)]

        ff_cmd += ["-i", fpath]

        # Add Autel SRT as second input if DJI
        if is_dji and autel_srt_path:
            ff_cmd += ["-i", autel_srt_path]

        if has_trim and file_setting.trim_out >= 0:
            duration = file_setting.trim_out - max(file_setting.trim_in, 0)
            if duration > 0:
                ff_cmd += ["-t", str(duration)]

        if is_dji:
            # DJI: map only video + audio, exclude DJI meta/dbgi tracks
            ff_cmd += ["-map", "0:v", "-map", "0:a?"]
            if autel_srt_path:
                ff_cmd += ["-map", "1:0"]
        else:
            ff_cmd += ["-map", "0"]

        if needs_reencode:
            # Build video filter
            vf_parts = []
            eq_parts = []
            if file_setting.brightness != 0.0:
                eq_parts.append(f"brightness={file_setting.brightness}")
            if file_setting.contrast != 1.0:
                eq_parts.append(f"contrast={file_setting.contrast}")
            if file_setting.saturation != 1.0:
                eq_parts.append(f"saturation={file_setting.saturation}")
            if file_setting.gamma != 1.0:
                eq_parts.append(f"gamma={file_setting.gamma}")
            if eq_parts:
                vf_parts.append("eq=" + ":".join(eq_parts))
            if file_setting.color_temp != 0.0:
                t = file_setting.color_temp
                vf_parts.append(f"colorbalance=rs={t}:gs=0:bs={-t}")
            if vf_parts:
                ff_cmd += ["-vf", ",".join(vf_parts)]

            ff_cmd += [
                "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                "-c:a", "aac", "-b:a", "192k",
            ]
        else:
            if is_dji and autel_srt_path:
                # DJI: copy video/audio but encode subtitle as mov_text
                # Force 60fps timescale to match Autel (DJI uses 59.94)
                ff_cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
                           "-video_track_timescale", "60000"]
            else:
                ff_cmd += ["-c", "copy"]

        # Handler metadata
        if is_dji:
            ff_cmd += ["-metadata:s:v", "handler_name=Autel.Video"]
            if autel_srt_path:
                ff_cmd += ["-metadata:s:s", "handler_name=Autel.Subtitle"]

        ff_cmd += ["-movflags", "+faststart", "-metadata", f"creation_time={file_iso_dt}", new_path]

        ff_result = subprocess.run(
            ff_cmd, capture_output=True, text=True,
            encoding="utf-8", errors="ignore",
            creationflags=HNO,
        )
        if ff_result.returncode != 0:
            err = ff_result.stderr[-500:] if ff_result.stderr else "unknown error"
            fail_list.append(f"{os.path.basename(fpath)}: ffmpeg 失敗 — {err}")
            _emit_sync_for_job(job_id, 'log', {
                'type': 'error', 'msg': f'ffmpeg 失敗: {os.path.basename(fpath)}'
            })
            continue

        # exiftool write metadata (date stamps only)
        exif_cmd = [
            exiftool,
            f"-CreateDate={file_exif_dt}",
            f"-ModifyDate={file_exif_dt}",
            f"-TrackCreateDate={file_exif_dt}",
            f"-TrackModifyDate={file_exif_dt}",
            f"-MediaCreateDate={file_exif_dt}",
            f"-MediaModifyDate={file_exif_dt}",
            f"-FileCreateDate={file_exif_dt}",
            f"-FileModifyDate={file_exif_dt}",
            "-Software=Lavf58.20.100",
            "-QuickTime:SoftwareVersion=Lavf58.20.100",
            "-overwrite_original",
            new_path,
        ]
        exif_result = subprocess.run(
            exif_cmd, capture_output=True, text=True,
            encoding="utf-8", errors="ignore",
            creationflags=HNO,
        )
        if exif_result.returncode != 0:
            err = exif_result.stderr[-500:] if exif_result.stderr else "unknown error"
            fail_list.append(f"{new_name}: exiftool 失敗 — {err}")
            _emit_sync_for_job(job_id, 'log', {
                'type': 'error', 'msg': f'exiftool 失敗: {new_name}'
            })
            # Clean up temp SRT even on failure
            if autel_srt_path and os.path.exists(autel_srt_path):
                try:
                    os.remove(autel_srt_path)
                except OSError:
                    pass
            continue

        # Clean up temp Autel SRT file
        if autel_srt_path and os.path.exists(autel_srt_path):
            try:
                os.remove(autel_srt_path)
            except OSError:
                pass

        success_count += 1
        new_file_paths.append(new_path)
        _emit_sync_for_job(job_id, 'log', {
            'type': 'info', 'msg': f'✓ {new_name} 完成'
                + (f' (DJI→Autel 轉換)' if is_dji else '')
        })

    # Final progress
    _on_progress({
        'phase': 'drone_meta',
        'total_files': total,
        'done_files': total,
        'total_pct': 100,
    })

    # Summary log
    _emit_sync_for_job(job_id, 'log', {
        'type': 'info',
        'msg': f'Metadata 寫入完成: 成功 {success_count}/{total}'
              + (f'，失敗 {len(fail_list)} 個' if fail_list else '')
    })

    job.total_files = total
    job.done_files = success_count

    # Optional concat
    if task.do_concat and new_file_paths and task.concat_dest_dir:
        _emit_sync_for_job(job_id, 'log', {
            'type': 'info', 'msg': '開始串帶...'
        })
        _on_progress({'phase': 'concat', 'total_pct': 0, 'status': '串帶中...'})

        try:
            engine.run_concat_job(
                sources=new_file_paths,
                dest_dir=task.concat_dest_dir,
                resolution=task.concat_resolution,
                codec=task.concat_codec,
                burn_timecode=task.concat_burn_timecode,
                burn_filename=task.concat_burn_filename,
                on_progress=_on_progress,
            )
            _emit_sync_for_job(job_id, 'log', {
                'type': 'info', 'msg': '✓ 串帶完成'
            })
        except Exception as e:
            _emit_sync_for_job(job_id, 'log', {
                'type': 'error', 'msg': f'串帶失敗: {e}'
            })

    if fail_list and success_count == 0:
        raise RuntimeError(f"全部 {total} 個檔案處理失敗")


# ── Timeline Export ─────────────────────────────────────────

async def _run_timeline_export(job, engine, task: TimelineExportRequest, _on_progress):
    await asyncio.to_thread(_timeline_export_sync, job, engine, task, _on_progress)


def _timeline_export_sync(job, engine, task: TimelineExportRequest, _on_progress):
    """Export timeline clips as a single concatenated video with per-clip color grading."""
    import subprocess

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ffmpeg_bin = os.path.join(project_root, "ffmpeg.exe")
    HNO = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
    job_id = job.job_id

    clips = task.clips
    if not clips:
        raise ValueError("No clips to export")

    # Build ffmpeg command with filter_complex
    ff_cmd = [ffmpeg_bin, "-y"]

    # Add inputs with trim
    for clip in clips:
        if clip.trim_in > 0:
            ff_cmd += ["-ss", str(clip.trim_in)]
        ff_cmd += ["-i", clip.path]
        if clip.trim_out >= 0:
            duration = clip.trim_out - max(clip.trim_in, 0)
            if duration > 0:
                ff_cmd += ["-t", str(duration)]

    # Build filter_complex
    resolution_map = {"1080P": "1920:1080", "720P": "1280:720", "4K": "3840:2160"}
    res = resolution_map.get(task.resolution, "1920:1080")

    filter_parts = []
    concat_inputs = []
    for i, clip in enumerate(clips):
        vf = []
        # Color grading
        eq_parts = []
        if clip.brightness != 0.0:
            eq_parts.append(f"brightness={clip.brightness}")
        if clip.contrast != 1.0:
            eq_parts.append(f"contrast={clip.contrast}")
        if clip.saturation != 1.0:
            eq_parts.append(f"saturation={clip.saturation}")
        if eq_parts:
            vf.append("eq=" + ":".join(eq_parts))
        if clip.color_temp != 0.0:
            t = clip.color_temp
            vf.append(f"colorbalance=rs={t}:gs=0:bs={-t}")
        # Scale + pad to target resolution
        vf.append(
            f"scale={res}:force_original_aspect_ratio=decrease,"
            f"pad={res}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )

        filter_parts.append(f"[{i}:v]{','.join(vf)}[v{i}]")
        filter_parts.append(
            f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]"
        )
        concat_inputs.append(f"[v{i}][a{i}]")

    concat_str = "".join(concat_inputs) + f"concat=n={len(clips)}:v=1:a=1[vout][aout]"
    filter_parts.append(concat_str)

    filter_complex = ";\n".join(filter_parts)

    out_path = os.path.join(task.output_dir, task.output_name)
    os.makedirs(task.output_dir, exist_ok=True)

    ff_cmd += ["-filter_complex", filter_complex, "-map", "[vout]", "-map", "[aout]"]

    # Codec
    if "NVENC" in task.codec:
        ff_cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "18"]
    else:
        ff_cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "fast"]
    ff_cmd += ["-c:a", "aac", "-b:a", "192k"]
    ff_cmd += ["-movflags", "+faststart", out_path]

    _emit_sync_for_job(job_id, 'log', {
        'type': 'info', 'msg': f'開始匯出 {len(clips)} 個素材...'
    })

    result = subprocess.run(
        ff_cmd, capture_output=True, text=True,
        encoding='utf-8', errors='ignore', creationflags=HNO,
    )

    if result.returncode != 0:
        err = result.stderr[-500:] if result.stderr else "unknown"
        _emit_sync_for_job(job_id, 'log', {'type': 'error', 'msg': f'匯出失敗: {err}'})
        raise RuntimeError(f"FFmpeg export failed: {err}")

    _emit_sync_for_job(job_id, 'log', {
        'type': 'info', 'msg': f'匯出完成: {task.output_name}'
    })
