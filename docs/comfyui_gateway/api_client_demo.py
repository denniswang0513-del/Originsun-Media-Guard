#!/usr/bin/env python3
"""Minimal integration example: drive ComfyUI purely over its REST API.

No browser, no ComfyUI frontend. This is the shape Media Guard would use to
trigger a job from a project page or a background worker.

Auth: the gateway in front of ComfyUI takes a cookie holding a normal Media
Guard JWT, so core.auth.create_token is all that is needed - the same secret
signs both sides.
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, r'e:\Dev\Originsun-Media-Guard')
from core.auth import create_token  # noqa: E402

BASE = 'http://100.125.114.5:8188'
HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH = os.path.join(HERE, 'api_restore_upscale.json')
COOKIE = 'og_comfy=' + create_token(
    {'sub': 'mediaguard', 'access_level': 3, 'modules': ['comfyui']})


def call(path, data=None, method=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type': 'application/json', 'Cookie': COOKIE},
        method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        print('  HTTP %s on %s' % (e.code, path))
        print('     ', e.read().decode('utf-8', 'replace')[:200].replace('\n', ' '))
        return None


print('--- which paths exist ---')
for p in ('/prompt', '/api/prompt', '/queue', '/api/queue'):
    r = call(p) if p.endswith('queue') else None
    if p.endswith('queue'):
        print('  GET %-12s -> %s' % (p, 'ok' if r is not None else 'FAILED'))

print()
print('--- queue the job ---')
graph = json.load(io.open(GRAPH, encoding='utf-8'))
graph['73']['inputs']['file'] = 'wan720p.mp4'      # the only field a caller sets

for endpoint in ('/prompt', '/api/prompt'):
    r = call(endpoint, {'prompt': graph, 'client_id': 'mediaguard-demo'})
    if r is not None:
        print('  POST %s -> prompt_id=%s number=%s errors=%s'
              % (endpoint, r.get('prompt_id'), r.get('number'),
                 r.get('node_errors') or 'none'))
        break
else:
    raise SystemExit('could not queue')

time.sleep(6)
q = call('/queue') or {}
print('  queue: running=%d pending=%d'
      % (len(q.get('queue_running', [])), len(q.get('queue_pending', []))))

print()
print('--- interrupt so this demo does not tie up the GPU ---')
call('/interrupt', {}, method='POST')
time.sleep(3)
q = call('/queue') or {}
print('  queue: running=%d pending=%d'
      % (len(q.get('queue_running', [])), len(q.get('queue_pending', []))))
