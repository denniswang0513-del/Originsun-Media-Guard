import os
import asyncio
import json
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
    VerifyRequest, TranscribeRequest, AlignRequest, ReportJobRequest,
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

    # 磁碟代號 → UNC（core/drive_map）：中央咽喉統一翻譯 — HTTP 端點、排程、
    # 書籤重放三條進件路都經過這裡，任務在哪台機器執行都不再依賴磁碟掛載。
    from core.drive_map import translate_job_request
    _path_changes = translate_job_request(request, task_type)

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
    for _c in _path_changes:
        _emit_sync_for_job(job_id, 'log', {
            'type': 'system',
            'msg': f'[UNC] 路徑已依磁碟對應轉換: {_c}'
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

        elif isinstance(task, AlignRequest):
            await _run_align(job, engine, task)

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
        # 失敗主動推播（成功通知在各 _run_* 內；失敗只有開著網頁才看得到 → 補一條主動告警）
        # fire-and-forget：不讓通知的 HTTP round-trip 拖住 finally 的 log 落檔
        try:
            from notifier import notify_tab_async, machine_label  # type: ignore
            asyncio.ensure_future(notify_tab_async(
                "task_failed",
                task_type=task_type, project_name=job.project_name or "-",
                error=str(e)[:300], hostname=machine_label(),
            ))
        except Exception:
            pass

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
                "do_gdrive": False, "do_gchat": False,
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
            from notifier import notify_tab_async  # type: ignore
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
            await notify_tab_async("backup_success", project_name=task.project_name,
                                   file_count=file_count, total_size=total_size)
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
    advanced = [c.model_dump() for c in (task.advanced_clips or [])] or None
    await asyncio.to_thread(
        engine.run_concat_job,
        sources=task.sources, dest_dir=task.dest_dir,
        custom_name=task.custom_name,
        resolution=task.resolution, codec=task.codec,
        burn_timecode=task.burn_timecode, burn_filename=task.burn_filename,
        advanced_clips=advanced,
        xfade_enabled=getattr(task, 'xfade_enabled', False),
        xfade_type=getattr(task, 'xfade_type', 'dissolve'),
        xfade_duration=getattr(task, 'xfade_duration', 1.0),
        on_progress=_on_progress
    )


async def _run_transcribe(job, engine, task: TranscribeRequest):
    job_id = job.job_id
    try:
        import transcriber  # type: ignore
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


async def _run_align(job, engine, task: AlignRequest):
    """Forced alignment: known text + video audio → timed SRT.

    Reuses `transcribe_progress` event for the progress bar (shared UI),
    emits `align_done` with per-file quality info for the summary panel,
    falls back to `transcribe_error` on failure.
    """
    job_id = job.job_id
    try:
        import aligner  # type: ignore
    except ImportError as ie:
        err_msg = f"無法載入對齊模組: {ie}"
        await sio.emit('log', {'type': 'error', 'msg': err_msg, 'job_id': job_id})
        await sio.emit('transcribe_error', {'msg': err_msg, 'job_id': job_id})
        return

    _loop = asyncio.get_running_loop()
    total = len(task.tasks)

    def _on_prog(pct, msg):
        if engine._stop_event and engine._stop_event.is_set():
            raise Exception("使用者強制中止任務")
        m = re.match(r'\[(\d+)/(\d+)\]', msg)
        done = int(m.group(1)) if m else 0
        tot = int(m.group(2)) if m else total
        # Keep job.progress fresh: align can run cross-machine (the 處理主機
        # selector dispatches it to a host with the ML stack), and a remote
        # browser polls /api/v1/status — which surfaces active_jobs[id].progress.
        job.progress = {
            'total_pct': pct, 'pct': pct, 'msg': msg,
            'done_files': done, 'total_files': tot, 'mode': 'align',
        }
        asyncio.run_coroutine_threadsafe(
            sio.emit('transcribe_progress', {
                'pct': pct, 'msg': msg, 'job_id': job_id,
                'done_files': done, 'total_files': tot,
                'mode': 'align',
            }),
            _loop,
        )

    tasks_payload = [{
        "source": t.source,
        "transcript": t.transcript,
        "transcript_format": t.transcript_format,
    } for t in task.tasks]

    res = await asyncio.to_thread(
        aligner.run_align_job,
        tasks=tasks_payload,
        dest_dir=task.dest_dir,
        model_size=task.model_size,
        language=task.language,
        anchor_threshold=task.anchor_threshold,
        subtitle_polish=task.subtitle_polish,
        fps_override=task.fps_override,
        min_duration=task.min_duration,
        max_duration=task.max_duration,
        min_gap_frames=task.min_gap_frames,
        hold_until_next=task.hold_until_next,
        encoding_bom=task.encoding_bom,
        progress_callback=_on_prog,
        check_cancel_cb=engine._stop_event.is_set,
    )

    if res.get("success"):
        await sio.emit('align_done', {
            'job_id': job_id,
            'dest_dir': res.get("dest_dir"),
            'file_count': res.get("file_count"),
            'output_files': res.get("output_files", []),
            'qualities': res.get("qualities", []),
        })
        # Also fire transcribe_done so the shared progress bar UI hides itself
        await sio.emit('transcribe_done', {
            'dest_dir': res.get("dest_dir"), 'job_id': job_id, 'mode': 'align',
        })
        try:
            from notifier import notify_tab  # type: ignore
            _proj = os.path.basename(task.dest_dir.rstrip("\\/")) or "align"
            await asyncio.to_thread(
                notify_tab, "transcribe_success",
                project_name=_proj, dest_dir=task.dest_dir,
                file_count=res.get("file_count", 0),
            )
        except Exception:
            pass
    else:
        err_msg = res.get("error", "未知錯誤")
        await sio.emit('log', {
            'type': 'error', 'msg': f'對齊發生錯誤: {err_msg}', 'job_id': job_id,
        })
        await asyncio.sleep(0.1)
        await sio.emit('transcribe_error', {
            'msg': err_msg, 'job_id': job_id, 'mode': 'align',
        })
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

# Image formats supported by drone_meta. Photos go through exiftool-only path
# (no ffmpeg / DJI parser / Autel SRT). Always normalised to lowercase before
# matching.
DRONE_META_IMAGE_EXTS = {
    ".dng", ".jpg", ".jpeg", ".arw", ".cr2", ".cr3",
    ".nef", ".raf", ".orf", ".rw2", ".tif", ".tiff",
}

# Manifest filename written into each dest sub-folder. Lets watcher
# detect partial completion (worker killed mid-batch by OTA / crash) and
# only re-process the missing files instead of the whole folder.
DRONE_META_MANIFEST = "_drone_meta_manifest.json"


def _load_drone_manifest(dest_dir: str) -> dict:
    """Read the per-folder manifest. Returns {} on any failure."""
    if not dest_dir:
        return {}
    path = os.path.join(dest_dir, DRONE_META_MANIFEST)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_drone_manifest(dest_dir: str, manifest: dict) -> None:
    """Atomic write of manifest (.tmp + replace) so partial writes can't corrupt."""
    if not dest_dir:
        return
    try:
        path = os.path.join(dest_dir, DRONE_META_MANIFEST)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass  # Best-effort; never let manifest write break the actual job


def _record_manifest_success(
    manifest_dir: str,
    manifest: dict,
    manifest_stems: dict,
    src_stem: str,
    current_index: int,
    ext_key: str,
    next_index: int,
) -> None:
    """Mark (src_stem, ext_key) processed and persist manifest atomically."""
    entry = manifest_stems.setdefault(src_stem, {"index": current_index, "exts": []})
    entry["index"] = current_index
    if ext_key not in entry["exts"]:
        entry["exts"].append(ext_key)
    manifest["next_index"] = next_index
    _save_drone_manifest(manifest_dir, manifest)


def _process_image_metadata_sync(
    src_path: str, dst_path: str, file_dt,
    drone_make: str, drone_model: str,
    lens_make: str, lens_model: str,
    exiftool_bin: str,
):
    """Disguise a single image as Autel and rewrite all timestamps.

    Both Level 1 (identity rewrite) and Level 2 (DJI XMP/MakerNotes strip)
    always applied — no toggle.

    JPEG: rebuild path (-all= + tagsfromfile filtered copy-back). Plain
    MakerNotes:All= would leave a 4-byte "DJI" inline residue in the
    MakerNote IFD entry that exiftool can't delete via standard tags;
    rebuild eliminates it cleanly. Cost: DJI MPF secondary embedded
    preview (~1 MB) doesn't survive the rebuild — niche feature most
    viewers don't use.

    DNG / ARW / CR3 / NEF / RAF: single-pass surgical strip. We keep
    ColorMatrix / ProfileHueSatMap / ProfileToneCurve / NoiseProfile /
    OpcodeList intact — Lightroom looks up the camera profile via
    UniqueCameraModel ("XL720"), so embedded matrices are dead data
    for raw rendering and stripping them only saves ~17 KB while making
    the file structurally hollower than a real Autel raw (which has
    these populated). Only the actual smoking guns (DJI MakerNotes 30 KB,
    XMP flight metadata, identifying serial / lens / description fields)
    are wiped.
    """
    import shutil
    import subprocess
    HNO = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)

    # Wrap the source→dest copy. shutil.copy2 can raise on transient disk /
    # network / permission issues — without this, the per-file loop dies
    # mid-batch and the post-loop concat trigger never runs.
    class _CopyFailed:
        def __init__(self, e):
            self.returncode = 1
            self.stdout = ""
            self.stderr = f"copy failed: {type(e).__name__}: {e}"
    try:
        shutil.copy2(src_path, dst_path)
    except (OSError, FileNotFoundError, PermissionError) as e:
        return _CopyFailed(e)

    file_exif_dt = file_dt.strftime("%Y:%m:%d %H:%M:%S")

    # Map "EVO Lite+" → its internal "XL720" codename used in
    # UniqueCameraModel/LocalizedCameraModel; otherwise reuse model name.
    autel_unique = "XL720" if "EVO Lite" in drone_model else drone_model

    ext_lower = os.path.splitext(src_path)[1].lower()
    is_jpeg = ext_lower in ('.jpg', '.jpeg')

    args = [exiftool_bin]

    if is_jpeg:
        # ── JPEG: rebuild approach ──
        # 1. -all=  wipes every metadata block (kills MakerNote IFD entry too)
        # 2. -tagsfromfile @ -EXIF:all -GPS:all  copies back EXIF + GPS only
        # 3. --<excludes>  prevents re-introduction of MakerNotes and the
        #    identity tags we'll overwrite below (also catches the LensInfo
        #    "24mm f/2.8-11" Hasselblad fingerprint).
        args += [
            "-all=",
            "-tagsfromfile", "@",
            "-EXIF:all", "-GPS:all",
            "--MakerNotes",
            "--IFD0:Make", "--IFD0:Model", "--IFD0:Software",
            "--IFD0:UniqueCameraModel",
            "--ExifIFD:UniqueCameraModel",
            "--ExifIFD:SerialNumber", "--ExifIFD:LensInfo",
            "--IFD0:CameraSerialNumber", "--ExifIFD:LensSerialNumber",
            "--IFD0:ImageDescription", "--IFD0:XPComment", "--IFD0:XPKeywords",
        ]
    else:
        # ── DNG / ARW / CR3 / NEF / RAF: single-pass strip ──
        # We keep ColorMatrix / ProfileHueSatMap / ProfileToneCurve /
        # NoiseProfile / OpcodeList intact. Lightroom looks up the camera
        # profile via UniqueCameraModel (which we rewrite to "XL720" below),
        # so embedded matrices are dead data for raw rendering — stripping
        # them only saved ~45 KB while making the file structurally hollower
        # than a real Autel raw (which has these fields populated). Only
        # blow away the actual smoking guns: MakerNotes (DJI debug 30 KB),
        # XMP (DJI flight metadata + Hasselblad-flavoured XMPToolkit),
        # and identifying serial / lens fingerprints.
        args += [
            "-XMP:All=",
            "-MakerNotes:All=",
            "-ProfileName=",
            "-ProfileCopyright=",
            "-SerialNumber=",
            "-CameraSerialNumber=",
            "-LensSerialNumber=",
            "-ImageDescription=",
            "-XPComment=",
            "-XPKeywords=",
        ]

    # ── Level 1: rewrite identity to Autel (both paths) ──
    autel_firmware = "V2.1.8.27"
    args += [
        f"-Make={drone_make}",
        f"-Model={drone_model}",
        f"-UniqueCameraModel={autel_unique}",
        f"-LocalizedCameraModel={autel_unique}",
        f"-LensMake={lens_make}",
        f"-LensModel={lens_model}",
        # Autel reference DNG has no LensInfo; clear it.
        "-LensInfo=",
        f"-Software={autel_firmware}",
        f"-ProfileCopyright={drone_make}",
        # Date stamps — write every variant readers might consult.
        f"-CreateDate={file_exif_dt}",
        f"-ModifyDate={file_exif_dt}",
        f"-DateTimeOriginal={file_exif_dt}",
        f"-FileCreateDate={file_exif_dt}",
        f"-FileModifyDate={file_exif_dt}",
        # XMP block — written to byte-match Autel reference (5 fields, exact
        # same namespaces). Autel uses Adobe's XMP Core library so we
        # override exiftool's auto-stamped "Image::ExifTool 13.42" toolkit
        # signature. CreatorTool + DateCreated are part of Autel's standard
        # output; DateTimeOriginal in XMP is NOT (Autel only writes it in
        # EXIF), so we don't add it here.
        "-XMP-x:XMPToolkit=XMP Core 5.5.0",
        f"-XMP-xmp:CreatorTool={autel_firmware}",
        f"-XMP-xmp:CreateDate={file_exif_dt}",
        f"-XMP-xmp:ModifyDate={file_exif_dt}",
        f"-XMP-photoshop:DateCreated={file_exif_dt}",
    ]

    args += ["-overwrite_original", dst_path]

    return subprocess.run(
        args, capture_output=True, text=True,
        encoding="utf-8", errors="ignore",
        creationflags=HNO,
    )


async def _run_drone_meta(job, engine, task: DroneMetaRequest, _on_progress):
    await asyncio.to_thread(_drone_meta_sync, job, engine, task, _on_progress)


def _drone_meta_sync(job, engine, task: DroneMetaRequest, _on_progress):
    import subprocess
    import datetime as _datetime

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

    total = len(task.files)
    success_count = 0
    fail_list = []
    new_file_paths = []

    # Pre-create output dirs — watcher may target a fresh sub-dir that
    # doesn't exist yet; also protects users who typed a non-existent path.
    if task.output_dir:
        os.makedirs(task.output_dir, exist_ok=True)
    if task.do_concat and task.concat_dest_dir:
        os.makedirs(task.concat_dest_dir, exist_ok=True)

    # DJI cameras shoot DNG + JPG pairs that share a basename — group
    # output indices by source stem so MAX_0001.DNG / MAX_0001.JPG stay
    # paired. On resume, hydrate the index map from a previous run's
    # manifest so already-done (stem, ext) pairs reuse the same MAX_NNNN.
    manifest_dir = task.output_dir
    manifest = _load_drone_manifest(manifest_dir)
    manifest.setdefault("version", 1)
    manifest_stems = manifest.setdefault("stems", {})
    stem_to_index = {
        s: int(v["index"])
        for s, v in manifest_stems.items()
        if isinstance(v, dict) and v.get("index")
    }
    next_index = (
        int(manifest.get("next_index") or task.file_index)
        if stem_to_index else task.file_index
    )
    if manifest_stems:
        _emit_sync_for_job(job_id, 'log', {
            'type': 'info',
            'msg': f'偵測到既有 manifest（{len(manifest_stems)} 個來源已處理），續傳模式',
        })

    for idx, file_setting in enumerate(task.files):
        fpath = file_setting.path
        src_ext = os.path.splitext(fpath)[1].lower()
        is_image = src_ext in DRONE_META_IMAGE_EXTS
        src_stem = os.path.splitext(os.path.basename(fpath))[0]
        if src_stem not in stem_to_index:
            stem_to_index[src_stem] = next_index
            next_index += 1
        current_index = stem_to_index[src_stem]
        # Videos always normalise to MOV (container remux). Images keep the
        # original extension (uppercase) so MAX_0001.DNG vs MAX_0001.JPG is
        # visible at a glance.
        if is_image:
            new_name = f"MAX_{current_index:04d}{os.path.splitext(fpath)[1].upper()}"
        else:
            new_name = f"MAX_{current_index:04d}.MOV"
        out_dir = task.output_dir or os.path.dirname(fpath)
        new_path = os.path.join(out_dir, new_name)

        # Resume: if manifest already covers this (stem, ext), skip re-do.
        # Output file existence is the truth (manifest could be stale if user
        # manually deleted dest files); both need to agree.
        ext_key = src_ext.upper() if is_image else ".MOV"
        manifest_entry = manifest_stems.get(src_stem) or {}
        already_done_exts = manifest_entry.get("exts") or []
        if ext_key in already_done_exts and os.path.isfile(new_path):
            success_count += 1
            if not is_image:
                # Videos already in new_file_paths means concat will pick them up.
                new_file_paths.append(new_path)
            _emit_sync_for_job(job_id, 'log', {
                'type': 'info', 'msg': f'⤿ {new_name} 已存在（manifest 略過）'
            })
            continue

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

        # ── Image fast-path: skip ffmpeg/DJI parser/Autel SRT ──
        if is_image:
            img_result = _process_image_metadata_sync(
                fpath, new_path, file_dt,
                task.drone_make, task.drone_model,
                task.lens_make, task.lens_model,
                exiftool,
            )
            if img_result.returncode != 0:
                err = (img_result.stderr or "")[-500:] or "unknown error"
                fail_list.append(f"{new_name}: 圖片 metadata 寫入失敗 — {err}")
                _emit_sync_for_job(job_id, 'log', {
                    'type': 'error', 'msg': f'圖片寫入失敗: {new_name}\n{err}'
                })
                if os.path.exists(new_path):
                    try:
                        os.remove(new_path)
                    except OSError:
                        pass
                continue
            success_count += 1
            _record_manifest_success(manifest_dir, manifest, manifest_stems,
                                     src_stem, current_index, ext_key, next_index)
            # Images do NOT go into new_file_paths — concat takes videos only.
            _emit_sync_for_job(job_id, 'log', {
                'type': 'info', 'msg': f'✓ {new_name} 完成 (圖片)'
            })
            continue

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

        # Per-file step = pure container remux: rename, write creation_time,
        # normalize DJI → Autel (inject SRT subtitle, set handler names).
        # ABSOLUTELY NO trim / color / re-encode — all advanced edits happen
        # at the concat step via advanced_clips so raw clips stay untouched.
        ff_cmd = [ffmpeg_bin, "-y", "-i", fpath]

        if is_dji and autel_srt_path:
            ff_cmd += ["-i", autel_srt_path]

        if is_dji:
            # Exclude DJI private meta/dbgi tracks AND the mjpeg attached_pic
            # thumbnail Mavic 3+ embeds (without :0 it sneaks in as a second
            # video stream and breaks downstream concat / re-mux steps).
            ff_cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
            if autel_srt_path:
                ff_cmd += ["-map", "1:0"]
        else:
            ff_cmd += ["-map", "0"]

        if is_dji and autel_srt_path:
            # Copy av streams; transcode SRT to mov_text for MOV container.
            # Force 60fps timescale to match Autel (DJI uses 59.94).
            ff_cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
                       "-video_track_timescale", "60000"]
        else:
            ff_cmd += ["-c", "copy"]

        # Handler metadata
        if is_dji:
            ff_cmd += ["-metadata:s:v", "handler_name=Autel.Video"]
            if autel_srt_path:
                ff_cmd += ["-metadata:s:s", "handler_name=Autel.Subtitle"]

        # NO +faststart on intermediate files — these go straight into concat,
        # never streamed via web. faststart forces a 2-pass IO write that
        # roughly doubles wall time for multi-GB 4K HEVC drone clips on NAS.
        # The final concat output (line 1089) keeps faststart since that's
        # the user-facing reel that may be browser-previewed.
        ff_cmd += ["-metadata", f"creation_time={file_iso_dt}", new_path]

        ff_result = subprocess.run(
            ff_cmd, capture_output=True, text=True,
            encoding="utf-8", errors="ignore",
            creationflags=HNO,
        )
        if ff_result.returncode != 0:
            err = ff_result.stderr[-800:] if ff_result.stderr else "unknown error"
            # Compact stderr: drop banner lines, keep anything with Error/Invalid/bitstream.
            err_lines = [ln for ln in err.splitlines() if ln.strip()]
            concise = '\n'.join(err_lines[-8:])  # last 8 non-blank lines usually carry the cause
            fail_list.append(f"{os.path.basename(fpath)}: ffmpeg 失敗 — {err}")
            _emit_sync_for_job(job_id, 'log', {
                'type': 'error', 'msg': f'ffmpeg 失敗: {os.path.basename(fpath)}\n{concise}'
            })
            _emit_sync_for_job(job_id, 'log', {
                'type': 'info', 'msg': f'ffmpeg 指令: {" ".join(ff_cmd)}'
            })
            continue

        # exiftool write metadata (date stamps + drone make/model/lens)
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
            f"-Make={task.drone_make}",
            f"-Model={task.drone_model}",
            f"-QuickTime:Make={task.drone_make}",
            f"-QuickTime:Model={task.drone_model}",
            f"-LensMake={task.lens_make}",
            f"-LensModel={task.lens_model}",
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
        _record_manifest_success(manifest_dir, manifest, manifest_stems,
                                 src_stem, current_index, ext_key, next_index)
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

    # Optional concat — surface explicit skip reasons so users can tell
    # from the log why a reel didn't appear (image-only folder, all videos
    # failed, do_concat unset, etc.).
    if task.do_concat and new_file_paths and task.concat_dest_dir:
        _emit_sync_for_job(job_id, 'log', {
            'type': 'info',
            'msg': f'開始串帶（{len(new_file_paths)} 支影片）...'
        })
        _on_progress({'phase': 'concat', 'total_pct': 0, 'status': '串帶中...'})

        try:
            # Per-file pass did NOT touch trim/color — it only remuxed the
            # container and wrote metadata. All advanced edits (trim, color,
            # curves, xfade) are applied here at concat time via advanced_clips.
            xfade_on = bool(getattr(task, 'concat_xfade_enabled', False))

            def _has_advanced(fs):
                return (fs.trim_in > 0 or (fs.trim_out >= 0)
                        or fs.brightness != 0.0 or fs.contrast != 1.0
                        or fs.saturation != 1.0 or fs.gamma != 1.0
                        or fs.color_temp != 0.0
                        or getattr(fs, 'tint', 0.0) != 0.0
                        or getattr(fs, 'shadows', 0.0) != 0.0
                        or getattr(fs, 'midtones', 0.0) != 0.0
                        or getattr(fs, 'highlights', 0.0) != 0.0
                        or bool(getattr(fs, 'curve_points', None)))

            # Concat operates on videos only — images skipped the video path
            # and never landed in new_file_paths, so we must skip their entries
            # in task.files when zipping advanced edits to outputs.
            video_settings = [fs for fs in task.files
                              if os.path.splitext(fs.path)[1].lower() not in DRONE_META_IMAGE_EXTS]
            any_advanced = any(_has_advanced(fs) for fs in video_settings)
            advanced_clips = None
            if xfade_on or any_advanced:
                advanced_clips = []
                for i, fs in enumerate(video_settings):
                    if i >= len(new_file_paths):
                        break
                    advanced_clips.append({
                        'path': new_file_paths[i],
                        'trim_in': fs.trim_in,
                        'trim_out': fs.trim_out,
                        'brightness': fs.brightness,
                        'contrast': fs.contrast,
                        'saturation': fs.saturation,
                        'gamma': fs.gamma,
                        'color_temp': fs.color_temp,
                        'tint': getattr(fs, 'tint', 0.0),
                        'shadows': getattr(fs, 'shadows', 0.0),
                        'midtones': getattr(fs, 'midtones', 0.0),
                        'highlights': getattr(fs, 'highlights', 0.0),
                        'curve_points': getattr(fs, 'curve_points', None),
                    })
            engine.run_concat_job(
                sources=new_file_paths,
                dest_dir=task.concat_dest_dir,
                custom_name=(getattr(task, 'concat_custom_name', '') or task.project_name or '').strip(),
                resolution=task.concat_resolution,
                codec=task.concat_codec,
                burn_timecode=task.concat_burn_timecode,
                burn_filename=task.concat_burn_filename,
                advanced_clips=advanced_clips,
                xfade_enabled=xfade_on,
                xfade_type=getattr(task, 'concat_xfade_type', 'dissolve'),
                xfade_duration=getattr(task, 'concat_xfade_duration', 1.0),
                on_progress=_on_progress,
            )
            _emit_sync_for_job(job_id, 'log', {
                'type': 'info', 'msg': '✓ 串帶完成'
            })
        except Exception as e:
            _emit_sync_for_job(job_id, 'log', {
                'type': 'error', 'msg': f'串帶失敗: {e}'
            })
    elif task.do_concat:
        # do_concat was requested but skipped. Tell the user why.
        if not new_file_paths:
            video_count = sum(
                1 for fs in task.files
                if os.path.splitext(fs.path)[1].lower() not in DRONE_META_IMAGE_EXTS
            )
            if video_count == 0:
                reason = '無影片可串帶（資料夾內全為圖片）'
            else:
                reason = f'無串帶成果輸出（{video_count} 支影片全處理失敗）'
        elif not task.concat_dest_dir:
            reason = '未設定串帶目的目錄'
        else:
            reason = '條件未滿足'
        _emit_sync_for_job(job_id, 'log', {
            'type': 'info', 'msg': f'略過串帶：{reason}'
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
