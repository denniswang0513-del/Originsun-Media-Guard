"""Tiny update-progress HTTP server. Runs on port 8001 during OTA updates.
Launched as a DETACHED_PROCESS before the main uvicorn dies.
Reads update_status.json and serves it on GET /status.
Auto-shuts down after 30 minutes to avoid zombie processes.
"""
import json
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_status.json")


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/', '/status'):
            try:
                with open(STATUS_FILE, encoding='utf-8') as f:
                    data = f.read()
            except Exception:
                data = json.dumps({"step": 0, "pct": 2, "msg": "正在準備更新..."}, ensure_ascii=False)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data.encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # suppress access logs


def _kill_old_monitor():
    """殺掉佔用 port 8001 的舊 monitor 進程。"""
    import subprocess
    try:
        result = subprocess.run(
            ['netstat', '-ano'], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if ':8001' in line and 'LISTENING' in line:
                parts = line.split()
                pid = parts[-1]
                if pid.isdigit() and int(pid) != os.getpid():
                    subprocess.run(['taskkill', '/F', '/PID', pid],
                                   capture_output=True, timeout=5)
    except Exception:
        pass


if __name__ == '__main__':
    _kill_old_monitor()
    try:
        httpd = HTTPServer(('0.0.0.0', 8001), StatusHandler)
        threading.Timer(1800, httpd.shutdown).start()  # 30 分鐘後自動關閉
        httpd.serve_forever()
    except OSError:
        pass  # port 仍被佔用 → 靜默退出（前端 fallback 會處理）
