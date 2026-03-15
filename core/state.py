import asyncio
import threading
from queue import Queue

# --- Queue & Worker State ---
task_queue = Queue()
worker_busy = False
_current_progress = None

# --- Conflict Resolution State ---
conflict_events = {}
current_conflict_action = "copy"
conflict_event = threading.Event()
global_conflict_action = None

# --- Logging & Status State ---
_current_task_log_file = None
_STATUS_LOG_MAX = 2000
_status_log_buffer = []

# --- Report Job Control State ---
_current_report_task = None
_report_pause_event = asyncio.Event()
_report_pause_event.set()

_main_loop = None

def get_main_loop():
    global _main_loop
    return _main_loop

def set_main_loop(loop):
    global _main_loop
    _main_loop = loop
