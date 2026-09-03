#!/usr/bin/env python3
"""Queue a ComfyUI workflow template and measure the run.

The UI->API conversion is done by ComfyUI's own frontend (app.graphToPrompt())
driven through a headless browser, rather than reimplemented here. The newer
templates wrap everything in subgraphs - the top level is 4-7 nodes and the real
graph lives in definitions.subgraphs - and a hand-written flattener would also
have to re-handle seed control widgets and bypassed nodes. Using the frontend
means the conversion is by definition the same one a human clicking Run gets.

usage: run_template.py <template_name> [width height frames]
"""
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

sys.path.insert(0, r'e:\Dev\Originsun-Media-Guard')
from core.auth import create_token  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

HOST = '100.125.114.5'
UI = 'http://%s:8188' % HOST          # through the auth gate
API = 'http://%s:8188' % HOST
BOX = 'gpubox'
SSH = r'C:\Program Files\Git\usr\bin\ssh.exe'
TPL_DIR = ('D:/AI/ComfyUI_windows_portable/python_embeded/Lib/site-packages/'
           'comfyui_workflow_templates_json/templates')


def ssh(cmd):
    return subprocess.run([SSH, BOX, cmd], capture_output=True, text=True,
                          timeout=120).stdout.strip()


def api(path, data=None, token=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(data).encode() if data else None,
        headers={'Content-Type': 'application/json', 'Cookie': 'og_comfy=' + token})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


class Vram(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__()
        self.peak = 0
        self.stop = False

    def run(self):
        while not self.stop:
            try:
                v = ssh('nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits')
                self.peak = max(self.peak, int(v.split('\n')[0]))
            except Exception:
                pass
            time.sleep(5)


def main():
    name = sys.argv[1]
    token = create_token({'sub': 'bench', 'access_level': 3, 'modules': ['comfyui']})

    # a local .json path runs that file directly; a bare name is fetched from
    # the box's template dir
    if os.path.exists(name):
        wf = json.load(io.open(name, encoding='utf-8'))
    else:

        # pull the template off the box
        local = os.path.join(os.environ['TEMP'], name + '.json')
        subprocess.run([r'C:\Program Files\Git\usr\bin\scp.exe',
                        '%s:%s/%s.json' % (BOX, TPL_DIR, name), local],
                       capture_output=True, timeout=180)
        wf = json.load(io.open(local, encoding='utf-8'))

    if len(sys.argv) >= 5:
        w, h, f = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
        n = 0
        for sg in (wf.get('definitions') or {}).get('subgraphs', []):
            for node in sg.get('nodes', []):
                if node['type'] in ('EmptyLTXVLatentVideo', 'EmptyLatentVideo',
                                    'Wan22ImageToVideoLatent'):
                    node['widgets_values'] = [w, h, f, 1]
                    n += 1
                if node['type'] == 'LTXVEmptyLatentAudio':
                    node['widgets_values'] = [f, 25, 1]
                    n += 1
        print('resolution override applied to %d node(s): %dx%d %d frames' % (n, w, h, f))

    # LTX drives resolution from a top-level ResolutionSelector expressed in
    # megapixels, not from width/height on the latent node. Per the template's
    # own table (16:9, multiple=32):
    #   0.5 -> 960x544   0.9 -> 1280x736 (default)   1.0 -> 1376x768
    #   1.5 -> 1664x928  2.0 -> 1920x1088
    mp = os.environ.get('LTX_MP')
    if mp:
        for node in wf['nodes']:
            if node['type'] == 'ResolutionSelector':
                node['widgets_values'][1] = float(mp)
                print('ResolutionSelector -> %s megapixels' % mp)

    # SeedVR2's auto chunking predicts "the largest chunk that fits free VRAM"
    # and on this card it predicts wrong - it asked for 28.3 GiB on a 16 GB
    # device and OOM'd. Pin it to an explicit frames_per_chunk instead.
    chunk = os.environ.get('SEEDVR2_CHUNK')
    if chunk:
        for sg in (wf.get('definitions') or {}).get('subgraphs', []):
            for node in sg.get('nodes', []):
                if node['type'] == 'SeedVR2TemporalChunk':
                    node['widgets_values'] = [0, 'manual', int(chunk)]
                    print('SeedVR2TemporalChunk -> manual, %s frames/chunk' % chunk)

    # The template upscales x2 (1280x704 -> 2560x1408). For a 1080p deliverable
    # that is 44% more pixels than needed and it is what actually OOMs - the
    # failure is spatial, not temporal (chunking to 13 frames did not move the
    # reported allocation by a single byte).
    scale = os.environ.get('SEEDVR2_SCALE')
    if scale:
        for sg in (wf.get('definitions') or {}).get('subgraphs', []):
            for node in sg.get('nodes', []):
                if node['type'] == 'ResizeImageMaskNode':
                    node['widgets_values'][1] = float(scale)
                    print('ResizeImageMaskNode -> x%s' % scale)

    # LoadVideo reads from ComfyUI's input dir; point it at our own clip
    src = os.environ.get('LOADVIDEO')
    if src:
        for node in wf['nodes']:
            if node['type'] == 'LoadVideo':
                node['widgets_values'][0] = src
                print('LoadVideo ->', src)

    return _run(name, wf, token)


def _run(name, wf, token):
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context()
        ctx.add_cookies([{'name': 'og_comfy', 'value': token, 'domain': HOST, 'path': '/'}])
        pg = ctx.new_page()
        pg.goto(UI, wait_until='domcontentloaded', timeout=90000)
        pg.wait_for_function('() => window.app && window.app.graphToPrompt', timeout=120000)
        pg.wait_for_timeout(4000)

        prompt = pg.evaluate(
            """async (wf) => {
                 await window.app.loadGraphData(wf, true, false);
                 await new Promise(r => setTimeout(r, 3000));
                 const p = await window.app.graphToPrompt();
                 return p.output;
               }""", wf)
        b.close()

    print('api graph nodes:', len(prompt))
    for nid, node in prompt.items():
        if node['class_type'] in ('EmptyLTXVLatentVideo', 'Wan22ImageToVideoLatent',
                                  'EmptyLatentVideo', 'CreateVideo',
                                  'SeedVR2TemporalChunk', 'ResizeImageMaskNode',
                                  'Video Slice', 'VAEEncodeTiled', 'VAEDecodeTiled',
                                  'PrimitiveBoolean', 'ComfySwitchNode'):
            print('  %-26s %s' % (node['class_type'],
                                  json.dumps({k: v for k, v in node['inputs'].items()
                                   if not (isinstance(v, list) and len(v) == 2
                                           and isinstance(v[1], int))}, ensure_ascii=False)))

    dump = os.environ.get('DUMP_API')
    if dump:
        # A fixed workflow only needs converting once. Save the API-format graph
        # and callers can POST it directly, swapping just the input filename -
        # no browser, no frontend, no subgraph flattening at run time.
        io.open(dump, 'w', encoding='utf-8', newline=chr(10)).write(
            json.dumps(prompt, ensure_ascii=False, indent=1))
        print('api graph written to', dump)
        return

    if os.environ.get('DRY'):
        types = {}
        for node in prompt.values():
            types[node['class_type']] = types.get(node['class_type'], 0) + 1
        print('DRY RUN - not queued. class_type counts:')
        for k in sorted(types):
            print('   %-32s %d' % (k, types[k]))
        return

    v = Vram()
    v.start()
    t0 = time.time()
    pid = api('/prompt', {'prompt': prompt}, token)['prompt_id']
    print('queued', pid, flush=True)

    while True:
        time.sleep(15)
        hist = api('/history/' + pid, token=token)
        if pid in hist:
            break
        el = int(time.time() - t0)
        if el % 60 < 15:
            print('  %ds  vram_peak=%d MiB' % (el, v.peak), flush=True)
        if el > 7200:
            print('  gave up after 2h')
            v.stop = True
            return

    el = time.time() - t0
    v.stop = True
    h = hist[pid]
    st = h.get('status', {})
    print('\n' + '=' * 52)
    print('template    :', name)
    print('status      :', st.get('status_str'))
    print('elapsed     : %.1f s  (%.1f min)' % (el, el / 60))
    print('peak VRAM   : %d MiB of 16311' % v.peak)
    for _, out in (h.get('outputs') or {}).items():
        for _, items in out.items():
            for it in items:
                if isinstance(it, dict) and it.get('filename'):
                    print('output      :', it['filename'])
    if st.get('status_str') != 'success':
        for m in st.get('messages', [])[-8:]:
            print('  ', str(m)[:400])


if __name__ == '__main__':
    main()
