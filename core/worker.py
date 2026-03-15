import os
import asyncio
from typing import Any

from core.socket_mgr import sio  # type: ignore
from core.engine_inst import engine  # type: ignore
import core.state as state  # type: ignore
from core.logger import _emit_sync, _write_log_to_file  # type: ignore

from core.schemas import (  # type: ignore
    BackupRequest, TranscodeRequest, ConcatRequest, 
    VerifyRequest, TranscribeRequest, ReportJobRequest
)
from core_engine import ReportManifest  # type: ignore

# ── Socket Events (Conflict Resolving) ──────────────────────────
@sio.on('resolve_conflict')
async def handle_conflict_resolution(sid, data):
    state.current_conflict_action = data.get('action', 'copy')
    state.conflict_event.set()

@sio.on('set_global_conflict')
async def handle_set_global_conflict(sid, data):
    action = data.get('action') 
    state.global_conflict_action = action
    state.current_conflict_action = action or 'skip'
    state.conflict_event.set()


# ── Background Task Runner ──────────────────────────────────────
async def _background_worker():
    while not state.task_queue.empty():
        state.worker_busy = True
        task: Any = state.task_queue.get()
        
        task_name = getattr(task, "project_name", getattr(task, "task_type", "Unknown"))
        engine.clear_logs()
        state._status_log_buffer = [] 
        await sio.emit('log', {'type': 'system', 'msg': f'開始執行新任務: {task_name}'})
        
        log_dir = ""
        if isinstance(task, BackupRequest):
            log_dir = os.path.join(task.local_root, task.project_name)
        elif isinstance(task, TranscodeRequest) or isinstance(task, ConcatRequest):
            import tempfile
            log_dir = tempfile.gettempdir()
        elif isinstance(task, VerifyRequest) and task.pairs:
            log_dir = task.pairs[0][1]
        
        state.global_conflict_action = None
        
        from datetime import datetime as _dt
        _dt_str = _dt.now().strftime("%Y%m%d_%H%M%S")
        if log_dir:
            safe_task_name = str(task_name).replace(".", "_").replace(" ", "_")
            state._current_task_log_file = os.path.join(log_dir, f"{safe_task_name}_Log_{_dt_str}.txt")
            _write_log_to_file(f"=== Originsun Media Guard Pro {getattr(task, 'task_type', 'Job')} 任務開始 ===")
        else:
            state._current_task_log_file = None

        def _on_progress(data: dict):
            state._current_progress = data
            _emit_sync('progress', data)
            
        def _on_conflict(data: dict) -> str:
            if state.global_conflict_action:
                return state.global_conflict_action
            state.conflict_event.clear()
            _emit_sync('file_conflict', data)
            if not state.conflict_event.wait(timeout=60):
                return 'skip'
            return state.current_conflict_action

        try:
            if isinstance(task, BackupRequest):
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
                
                if getattr(task, "do_transcode", False):
                    for card_name, _ in task.cards:
                        if engine._pause_event.is_set() and engine._stop_event.is_set():
                            break
                        src_dir = os.path.join(task.local_root, task.project_name, card_name)
                        dest_dir = os.path.join(task.proxy_root, task.project_name, card_name)
                        if os.path.exists(src_dir):
                            await asyncio.to_thread(
                                engine.run_transcode_job,
                                sources=[src_dir], dest_dir=dest_dir, on_progress=_on_progress
                            )
                            
                if getattr(task, "do_concat", False):
                    for card_name, _ in task.cards:
                        if engine._pause_event.is_set() and engine._stop_event.is_set():
                            break
                        use_proxy = getattr(task, "do_transcode", False)
                        src_dir = os.path.join(task.proxy_root if use_proxy else task.local_root, task.project_name, card_name)
                        dest_dir = os.path.join(task.proxy_root, task.project_name, card_name)
                        if os.path.exists(src_dir):
                            await asyncio.to_thread(
                                engine.run_concat_job,
                                sources=[src_dir], dest_dir=dest_dir,
                                custom_name=f"{task.project_name}_{card_name}_reel",
                                resolution="720P", codec="H.264 (NVENC)", burn_timecode=True, burn_filename=False,
                                on_progress=_on_progress
                            )
                            
            elif isinstance(task, TranscodeRequest):
                await asyncio.to_thread(engine.run_transcode_job, sources=task.sources, dest_dir=task.dest_dir, on_progress=_on_progress)
                try:
                    from notifier import notify_tab  # type: ignore
                    _fc = sum(len(list(os.walk(str(src)))) and sum(len(f) for _, _, f in os.walk(str(src))) for src in task.sources if os.path.isdir(str(src)))
                    _proj = os.path.basename(task.dest_dir)
                    await asyncio.to_thread(notify_tab, "transcode_success", project_name=_proj, dest_dir=task.dest_dir, file_count=_fc)
                except Exception: pass

            elif isinstance(task, ConcatRequest):
                await asyncio.to_thread(
                    engine.run_concat_job, sources=task.sources, dest_dir=task.dest_dir, custom_name=task.custom_name,
                    resolution=task.resolution, codec=task.codec, burn_timecode=task.burn_timecode, burn_filename=task.burn_filename,
                    on_progress=_on_progress
                )
                try:
                    from notifier import notify_tab  # type: ignore
                    c_upper = task.codec.upper()
                    ext = ".mp4" if ("H264" in c_upper or "H.264" in c_upper or "NVENC" in c_upper or "X264" in c_upper) else ".mov"
                    folder_name = task.custom_name or (os.path.basename(task.sources[0]) if task.sources and os.path.isdir(task.sources[0]) else "Standalone_Reel")
                    _out = f"{folder_name}{ext}"
                except Exception: pass

            elif isinstance(task, TranscribeRequest):
                try:
                    import transcriber  # type: ignore
                    import importlib
                    importlib.reload(transcriber)
                except ImportError as ie:
                    err_msg = f"無法載入語音辨識模組: {ie}"
                    await sio.emit('log', {'type': 'error', 'msg': err_msg})
                    await sio.emit('transcribe_error', {'msg': err_msg})
                    continue
                safe_dest = task.dest_dir.rstrip("\\/") if task.dest_dir else ""
                _proj = os.path.basename(safe_dest) or "transcribe"
                
                _loop = asyncio.get_running_loop()
                def _on_prog(pct, msg):
                    if engine._stop_event and engine._stop_event.is_set():
                        raise Exception("使用者強制中止任務")
                    asyncio.run_coroutine_threadsafe(sio.emit('transcribe_progress', {'pct': pct, 'msg': msg}), _loop)
                    
                res = await asyncio.to_thread(
                    transcriber.run_transcribe_job,
                    sources=task.sources, dest_dir=task.dest_dir, project_name=_proj, model_size=task.model_size,
                    output_srt=task.output_srt, output_txt=task.output_txt, output_wav=task.output_wav, generate_proxy=task.generate_proxy,
                    individual_mode=task.individual_mode, progress_callback=_on_prog, check_cancel_cb=engine._stop_event.is_set
                )
                if res.get("success"):
                    await sio.emit('transcribe_done', {'dest_dir': res.get("dest_dir")})
                    try:
                        from notifier import notify_tab  # type: ignore
                        await asyncio.to_thread(notify_tab, "transcribe_success", project_name=_proj, dest_dir=task.dest_dir, file_count=res.get("file_count", 0))
                    except Exception: pass
                else:
                    err_msg = res.get("error", "未知錯誤")
                    await sio.emit('log', {'type': 'error', 'msg': f'逐字稿發生錯誤: {err_msg}'})
                    await asyncio.sleep(0.1)
                    await sio.emit('transcribe_error', {'msg': err_msg})
                    continue

            elif isinstance(task, VerifyRequest):
                await asyncio.to_thread(engine.run_verify_job, pairs=task.pairs, mode=task.mode, on_progress=_on_progress)
                try:
                    from notifier import notify_tab  # type: ignore
                    _total = len(task.pairs)
                    _fail = sum(1 for line in engine._log_buffer if '[FAIL]' in line or 'FAIL' in line.upper())
                    _pass = _total - _fail
                    _proj = os.path.basename(task.pairs[0][0]) if task.pairs else "Unknown"
                    await asyncio.to_thread(notify_tab, "verify_success", project_name=_proj, pass_count=_pass, fail_count=_fail, total_count=_total)
                except Exception: pass
                
            # Post-task HTML Report 
            if isinstance(task, BackupRequest) and getattr(task, 'do_report', False):
                try:
                    local_dir = os.path.join(task.local_root, task.project_name)
                    await sio.emit('progress', {'phase': 'report', 'status': 'processing', 'current_file': '📊 備份完成，正在啟動視覺報表掃描...', 'total_pct': 0})
                    
                    from core.report_job import _run_report_job  # type: ignore
                    report_req = ReportJobRequest(**{
                        "source_dir": local_dir,
                        "output_dir": task.local_root,
                        "nas_root": task.nas_root,
                        "report_name": task.project_name,
                        "do_filmstrip": True, "do_techspec": True, "do_hash": task.do_hash,
                        "do_gdrive": False, "do_gchat": False, "do_line": False,
                        "exclude_dirs": [os.path.join(task.proxy_root, task.project_name)] if task.proxy_root else [],
                    })
                    asyncio.create_task(_run_report_job(report_req))
                    await sio.emit('progress', {'phase': 'report', 'status': 'processing', 'current_file': '📊 視覺報表掃描中（請查看「視覺報表」頁籤追蹤進度）', 'total_pct': 50})
                except Exception as ex:
                    _emit_sync('log', {'type': 'error', 'msg': f'任務 {task_name} 執行發生錯誤: {ex}'})

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
                                    file_count += 1  # type: ignore
                                    total_size += os.path.getsize(p)  # type: ignore
                    await asyncio.to_thread(notify_all, task.project_name, None, file_count, total_size)
                except Exception: pass

            await sio.emit('task_status', {'status': 'done'})
        except Exception as e:
            await sio.emit('log', {'type': 'error', 'msg': f'任務執行失敗: {str(e)}'})
            await sio.emit('task_status', {'status': 'error', 'detail': str(e)})
        finally:
            if log_dir:
                try:
                    await asyncio.to_thread(engine.save_job_log, log_dir, f"{task_name} 工作日誌")
                except Exception as log_err:
                    print(f"Error saving job log: {log_err}")
            
        state.task_queue.task_done()
    
    state.worker_busy = False
    state._current_progress = None
