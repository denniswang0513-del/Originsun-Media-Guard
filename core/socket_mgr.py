import socketio # type: ignore
import core.state as state

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

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
