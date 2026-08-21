#!/usr/bin/env python3
"""ComfyUI auth gateway - sits behind Caddy, gates ComfyUI with the company's
existing Media Guard accounts.

Why a separate 150-line service instead of reusing core/auth.py directly:
  * core.auth._extract_token only reads the Authorization / X-API-Key headers.
    A browser gate needs a cookie, because ComfyUI's own XHR/WebSocket calls
    will never carry an Authorization header.
  * Shipping the whole repo onto the GPU box just to verify a signature would
    couple this machine to every Media Guard deploy. The token format is a
    plain HS256 JWT, so ~20 lines reproduces it exactly (see core/auth.py
    create_token / verify_token - keep the two in sync if that ever changes).

Verification is OFFLINE (HMAC + exp only, no DB, no network), so ComfyUI's very
chatty request pattern costs nothing. The master agent is contacted once, at
login, to exchange username/password for a token.

Endpoints (all under /_auth/ so Caddy can route them without touching ComfyUI):
  GET  /_auth/verify   forward_auth target. 200 = allowed, 302 = go log in.
  GET  /_auth/login    login page, styled like frontend/website-admin.html
  POST /_auth/login    form post -> master /api/v1/auth/login -> set cookie
  GET  /_auth/logout   clear cookie
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
SECRET_FILE = os.path.join(BASE, 'jwt.secret')
MASTER = os.environ.get('OG_MASTER', 'http://192.168.1.107:8000')
REQUIRED_MODULE = 'comfyui'
COOKIE = 'og_comfy'
PORT = 8190

with open(SECRET_FILE, 'r', encoding='utf-8') as _f:
    SECRET = _f.read().strip()


# ── token ──────────────────────────────────────────────────────────────────
def _b64url_decode(s):
    pad = 4 - len(s) % 4
    if pad != 4:
        s += '=' * pad
    return base64.urlsafe_b64decode(s)


def _b64url_encode(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def verify_token(token):
    """Mirror of core.auth.verify_token. Returns payload dict or None."""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        header, body, sig = parts
        expected = hmac.new(SECRET.encode(), ('%s.%s' % (header, body)).encode(),
                            hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url_encode(expected), sig):
            return None
        payload = json.loads(_b64url_decode(body))
        if payload.get('exp', 0) < time.time():
            return None
        # tokens carrying a `purpose` (password reset etc.) are signed by the
        # same key but are NOT login credentials - same rule as core.auth.
        if payload.get('purpose'):
            return None
        return payload
    except Exception:
        return None


def allowed(payload):
    if not payload:
        return False
    if (payload.get('access_level') or 0) >= 3:
        return True
    return REQUIRED_MODULE in (payload.get('modules') or [])


# ── page ───────────────────────────────────────────────────────────────────
PAGE = u"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>ComfyUI｜ORIGINSUN</title>
<style>
  :root { --red:#c9372c; --ink:#262626; --sub:#737373; --line:#e5e5e5; }
  * { box-sizing:border-box; }
  html,body { margin:0; background:#fff; color:var(--ink);
    font-family:"Noto Sans TC","Microsoft JhengHei","Segoe UI",sans-serif; }
  header { display:flex; align-items:center; justify-content:space-between;
    padding:12px 24px; border-bottom:1px solid var(--line); }
  .brand-name { font-size:13px; letter-spacing:.25em; font-weight:300; }
  .accent { color:var(--red); }
  .eyebrow { font-size:11px; letter-spacing:.35em; text-transform:uppercase; color:var(--sub); }
  #login-view { max-width:380px; margin:8vh auto 0; padding:0 20px; }
  .login-card { border:1px solid var(--line); padding:40px 36px; }
  .login-card h1 { font-size:22px; font-weight:300; letter-spacing:.1em; margin:18px 0 6px; }
  .login-card .sub { font-size:13px; color:var(--sub); margin-bottom:28px; line-height:1.7; }
  .field { margin-bottom:14px; }
  .field label { display:block; font-size:11px; letter-spacing:.15em; color:var(--sub);
    text-transform:uppercase; margin-bottom:6px; }
  .field input { width:100%; border:1px solid var(--line); border-radius:2px;
    padding:10px 12px; font-size:14px; color:var(--ink); outline:none; }
  .field input:focus { border-color:var(--red); }
  .btn-primary { display:block; width:100%; text-align:center; background:var(--ink);
    color:#fff; border:0; padding:12px; font-size:12px; letter-spacing:.3em;
    text-transform:uppercase; cursor:pointer; transition:background .15s; }
  .btn-primary:hover { background:var(--red); }
  .err { color:var(--red); font-size:12px; margin-top:12px; }
</style></head><body>
<header><span class="brand-name">COMFYUI <span class="accent">GPU</span></span></header>
<div id="login-view"><div class="login-card">
  <div class="eyebrow">Originsun</div>
  <h1>ComfyUI 登入</h1>
  <div class="sub">使用你在內部系統的帳號密碼登入。<br>需要 <b>ComfyUI</b> 模組權限或管理員帳號。</div>
  <form method="POST" action="/_auth/login">
    <input type="hidden" name="next" value="__NEXT__">
    <div class="field"><label>帳號 Account</label><input name="username" autocomplete="username" autofocus></div>
    <div class="field"><label>密碼 Password</label><input name="password" type="password" autocomplete="current-password"></div>
    <button class="btn-primary" type="submit">登入 Sign in</button>
  </form>
  __ERR__
</div></div></body></html>"""


def _esc(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
             .replace('"', '&quot;'))


def render(next_url, err=''):
    html = PAGE.replace('__NEXT__', _esc(next_url or '/'))
    html = html.replace('__ERR__', '<div class="err">%s</div>' % _esc(err) if err else '')
    return html.encode('utf-8')


# ── server ─────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    disable_nagle_algorithm = True
    server_version = 'ogauthgate'

    def log_message(self, fmt, *args):
        pass  # ComfyUI is chatty; every request hits /verify

    def _cookie(self):
        raw = self.headers.get('Cookie', '')
        for part in raw.split(';'):
            k, _, v = part.strip().partition('=')
            if k == COOKIE:
                return v
        return ''

    def _send(self, code, body=b'', ctype='text/html; charset=utf-8', extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _redirect(self, to, extra=None):
        self._send(302, b'', extra=[('Location', to)] + (extra or []))

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if path == '/_auth/verify':
            if allowed(verify_token(self._cookie())):
                return self._send(200, b'ok', 'text/plain')
            # forward_auth hands us the original request line in these headers
            nxt = self.headers.get('X-Forwarded-Uri', '/')
            return self._redirect('/_auth/login?next=' + urllib.parse.quote(nxt, safe=''))

        if path == '/_auth/login':
            return self._send(200, render((qs.get('next') or ['/'])[0]))

        if path == '/_auth/logout':
            return self._redirect('/_auth/login', extra=[
                ('Set-Cookie', '%s=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax' % COOKIE)])

        return self._send(404, b'not found', 'text/plain')

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != '/_auth/login':
            return self._send(404, b'not found', 'text/plain')

        n = int(self.headers.get('Content-Length') or 0)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode('utf-8'))
        user = (form.get('username') or [''])[0].strip()
        pwd = (form.get('password') or [''])[0]
        nxt = (form.get('next') or ['/'])[0] or '/'
        # never bounce back to an absolute URL a caller supplied
        if not nxt.startswith('/') or nxt.startswith('//'):
            nxt = '/'

        try:
            req = urllib.request.Request(
                MASTER + '/api/v1/auth/login',
                data=json.dumps({'username': user, 'password': pwd}).encode(),
                headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode()).get('detail', '')
            except Exception:
                detail = ''
            return self._send(401, render(nxt, detail or u'帳號或密碼錯誤'))
        except Exception:
            return self._send(
                502, render(nxt, u'連不到主控端 ' + MASTER +
                            u'（master 可能關機了）'))

        token = data.get('token', '')
        payload = verify_token(token)
        if not allowed(payload):
            return self._send(403, render(nxt, u'此帳號沒有 ComfyUI 權限，'
                                               u'請管理員在「使用者管理」'
                                               u'勾選 ComfyUI 模組。'))
        # Secure is omitted on purpose: the tailnet path is plain http.
        # Cloudflare terminates TLS for the public path, so that leg is still
        # encrypted end to end from the browser's point of view.
        cookie = ('%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax'
                  % (COOKIE, token, max(0, int(payload.get('exp', 0) - time.time()))))
        return self._redirect(nxt, extra=[('Set-Cookie', cookie)])


class Gate(ThreadingHTTPServer):
    # ComfyUI's frontend pulls ~40 JS chunks at once. Over the tailnet the
    # browser caps itself at ~6 HTTP/1.1 connections and nothing notices, but
    # through Cloudflare the burst arrives as HTTP/2 multiplexed streams and
    # cloudflared fans them out to the origin in parallel. Each one makes Caddy
    # fire a forward_auth subrequest here, so this listener sees the whole burst.
    # socketserver's default request_queue_size of 5 refuses the overflow, Caddy
    # reports the auth upstream as down, and the browser gets a wall of 502s.
    request_queue_size = 256
    daemon_threads = True
    allow_reuse_address = True


if __name__ == '__main__':
    print('authgate on 127.0.0.1:%d  master=%s  module=%s' % (PORT, MASTER, REQUIRED_MODULE))
    Gate(('127.0.0.1', PORT), Handler).serve_forever()
