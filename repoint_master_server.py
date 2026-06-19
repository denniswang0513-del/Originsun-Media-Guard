"""One-off fleet maintenance: repoint every agent's master_server -> .107.

Why: the master moved 192.168.1.11 -> 192.168.1.107, but each agent's
settings.json still points OTA downloads at the old (now offline) .11.
settings.json is NOT shipped in the OTA ZIP, so updating code can't fix it
remotely — this script does, via each agent's merge-on-save settings endpoint.

Usage (dev venv):
    python repoint_master_server.py          # DRY preview (read-only)
    python repoint_master_server.py apply     # actually write
Re-run `apply` later for machines that were offline this round.
NOT shipped in OTA (not in ota_manifest); do not commit.
"""
import sys
import json
import urllib.request

NEW_MASTER = "http://192.168.1.107:8000"
MASTER_API = "http://127.0.0.1:8000"          # the local prod master (this machine)
APPLY = len(sys.argv) > 1 and sys.argv[1] == "apply"


def _post(url, data, token=None, timeout=8):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, method="POST",
                                 data=json.dumps(data).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(url, token=None, timeout=8):
    h = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def login(base):
    return _post(base + "/api/v1/auth/login",
                 {"username": "admin", "password": "admin"}, timeout=6).get("token", "")


def main():
    mtok = login(MASTER_API)
    agents = _get(MASTER_API + "/api/v1/agents", mtok).get("agents", []) or []
    mode = "APPLY 實際寫入" if APPLY else "DRY 預覽（不寫入）"
    print(f"機隊共 {len(agents)} 台。模式：{mode}\n")

    ok = skip = off = err = 0
    for a in agents:
        name = a.get("name")
        url = (a.get("url") or "").rstrip("/")
        try:
            tok = login(url)
            cur = _get(url + "/api/settings/load", tok).get("master_server", "(未設)")
        except Exception as e:
            print(f"  [離線] {name} {url} — {str(e)[:60]}")
            off += 1
            continue
        if cur == NEW_MASTER:
            print(f"  [已是.107] {name} {url}")
            skip += 1
            continue
        if not APPLY:
            print(f"  [待改] {name} {url}: {cur}  →  {NEW_MASTER}")
            continue
        try:
            _post(url + "/api/settings/save", {"master_server": NEW_MASTER}, tok)
            new = _get(url + "/api/settings/load", tok).get("master_server", "?")
            if new == NEW_MASTER:
                print(f"  [✓改好] {name} {url}: {cur} → {new}")
                ok += 1
            else:
                print(f"  [✗驗證失敗] {name} {url}: 仍是 {new}")
                err += 1
        except Exception as e:
            print(f"  [✗寫入失敗] {name} {url} — {str(e)[:60]}")
            err += 1

    print(f"\n總結：改好 {ok}、已是.107 {skip}、離線 {off}、失敗 {err}（共 {len(agents)}）")
    if not APPLY:
        print("（這是預覽。確認無誤後執行：python repoint_master_server.py apply）")


if __name__ == "__main__":
    main()
