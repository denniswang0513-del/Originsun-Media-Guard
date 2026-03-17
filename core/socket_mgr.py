import socketio  # type: ignore
import core.state as state  # type: ignore

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')


@sio.on('resolve_conflict')
async def handle_conflict_resolution(sid, data):
    job_id = data.get('job_id', '')
    action = data.get('action', 'copy')

    if job_id:
        job = state.get_job(job_id)
        if job:
            job.current_conflict_action = action
            job.conflict_event.set()
            return

    # Backward compat: if no job_id, resolve the first waiting conflict
    for job in state.get_all_jobs().values():
        if (job.status == state.JobStatus.RUNNING
                and not job.conflict_event.is_set()):
            job.current_conflict_action = action
            job.conflict_event.set()
            break


@sio.on('set_global_conflict')
async def handle_set_global_conflict(sid, data):
    job_id = data.get('job_id', '')
    action = data.get('action')

    if job_id:
        job = state.get_job(job_id)
        if job:
            job.global_conflict_action = action
            job.current_conflict_action = action or 'skip'
            job.conflict_event.set()
            return

    # Backward compat fallback
    for job in state.get_all_jobs().values():
        if job.status == state.JobStatus.RUNNING:
            job.global_conflict_action = action
            job.current_conflict_action = action or 'skip'
            job.conflict_event.set()
