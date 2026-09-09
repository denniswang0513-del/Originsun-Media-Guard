# -*- coding: utf-8 -*-
"""/polish 階段零安全網（2026-09-09 報價助理那批）。

只釘「現在的行為」，不判斷對錯 —— 後面幾個階段要動這些程式碼時，這裡先把行為釘住。

補的是這批新模組裡**只有掃原始碼、沒有行為測試**的三塊：
- `core/subproc.run_stream`（新的串流 subprocess：執行緒、逾時、殺行程都沒被真的跑過）
- `core/assets_host.assets_delete`（原本只測了「不合法檔名要拒絕」，沒測真的刪得掉）
- `frontend/js/shared/quote-delete.js`（全新的純函式，一個測試都沒有）
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core import assets_host
from core.subproc import run_stream

_REPO = Path(__file__).resolve().parents[2]


# ── core/subproc.run_stream ──────────────────────────────────

def _py(code: str) -> list:
    return [sys.executable, "-c", code]


async def _collect(args, **kw):
    seen = []
    rc, out, err = await run_stream(args, on_line=seen.append, **kw)
    return rc, out, err, seen


async def test_run_stream_hands_over_lines_as_they_arrive():
    rc, out, err, seen = await _collect(
        _py("import sys\nfor i in range(5): print(i, flush=True)"))
    assert rc == 0
    assert [b.decode() for b in seen] == ["0", "1", "2", "3", "4"], "逐行、照順序"
    assert out.splitlines() == [b"0", b"1", b"2", b"3", b"4"], "同時也要回完整的 stdout"
    assert err == b""


async def test_run_stream_feeds_stdin_from_its_own_thread():
    """幾 KB 的提示邊寫邊讀會卡死在 pipe buffer —— stdin 是另一條執行緒在寫。"""
    big = ("x" * 200 + "\n") * 400          # ~80KB，遠超 Windows pipe buffer
    rc, out, err, seen = await _collect(
        _py("import sys\nd=sys.stdin.read()\nprint(len(d))"),
        input_bytes=big.encode())
    assert rc == 0
    assert out.strip() == str(len(big)).encode(), out


async def test_run_stream_survives_a_callback_that_raises():
    """壞掉的 callback 不准把整個讀取迴圈殺掉（不然會拿到半截 stdout）。"""
    def boom(_line):
        raise RuntimeError("callback 爆了")

    rc, out, err = await run_stream(
        _py("for i in range(3): print(i, flush=True)"), on_line=boom)
    assert rc == 0
    assert out.splitlines() == [b"0", b"1", b"2"]


async def test_run_stream_kills_the_child_on_timeout():
    rc, out, err, seen = await _collect(
        _py("import time\nprint('start', flush=True)\ntime.sleep(30)"), timeout=2)
    assert rc == -1
    assert b"timeout" in err
    assert seen and seen[0] == b"start", "逾時前已經吐出來的行還是要交出去"


async def test_run_stream_reports_spawn_failure_instead_of_raising():
    rc, out, err, seen = await _collect(["這個執行檔不存在_zzz"])
    assert rc == -1 and b"spawn failed" in err and seen == []


async def test_run_stream_reports_a_nonzero_exit_and_stderr():
    rc, out, err, seen = await _collect(
        _py("import sys\nprint('out', flush=True)\nsys.stderr.write('bad')\nsys.exit(3)"))
    assert rc == 3
    assert seen == [b"out"] and b"bad" in err


# ── core/assets_host.assets_delete ───────────────────────────

HEX = "a" * 32


def test_assets_delete_removes_the_file_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(assets_host, "assets_target", lambda ns: (str(tmp_path), "http://x/" + ns))
    f = tmp_path / f"{HEX}.webp"
    f.write_bytes(b"fake")
    assert assets_delete_ok(f"{HEX}.webp") is True and not f.exists()
    # 再刪一次：檔案已經不在 → False，但不能丟例外（清理是順手做的，不該擋住寄出報價）
    assert assets_delete_ok(f"{HEX}.webp") is False


def assets_delete_ok(name: str) -> bool:
    return assets_host.assets_delete("paste", name)


def test_assets_delete_refuses_when_the_namespace_is_not_configured(monkeypatch):
    monkeypatch.setattr(assets_host, "assets_target", lambda ns: ("", ""))
    assert assets_host.assets_delete("paste", f"{HEX}.webp") is False


def test_assets_delete_stays_inside_its_namespace(tmp_path, monkeypatch):
    """白名單之外的第二道保險：正規化之後必須還在那個目錄底下。"""
    ns_dir = tmp_path / "paste"
    ns_dir.mkdir()
    outside = tmp_path / "settings.json"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(assets_host, "assets_target", lambda ns: (str(ns_dir), "http://x"))
    for evil in (f"../{outside.name}", f"..\\{outside.name}", f"sub/{HEX}.webp"):
        assert assets_host.assets_delete("paste", evil) is False
    assert outside.exists(), "範圍外的檔案一個都不准碰"


# ── frontend/js/shared/quote-delete.js ───────────────────────

_HARNESS = """
import { deleteConfirmSpec, confirmQuoteDelete } from '%s';
const draft  = { project_name: '論壇專案', version: 2, status: '草稿' };
const sent   = { project_name: '論壇專案', version: 3, status: '已寄送' };
const shared = { project_name: '論壇專案', version: 4, status: '已簽核', share_url: '/q/abc' };
const asked = [];
const ask = (typed) => ({ confirm: (m) => { asked.push(['confirm', m]); return true; },
                          prompt:  (m) => { asked.push(['prompt', m]); return typed; } });
console.log(JSON.stringify({
  draftSpec: deleteConfirmSpec(draft),
  sentSpec: deleteConfirmSpec(sent),
  sharedSpec: deleteConfirmSpec(shared),
  noName: deleteConfirmSpec({}),
  draftOk: confirmQuoteDelete(draft, ask(null)),
  sentRight: confirmQuoteDelete(sent, ask('論壇專案')),
  sentPadded: confirmQuoteDelete(sent, ask('  論壇專案  ')),
  sentWrong: confirmQuoteDelete(sent, ask('隨便打')),
  sentEmpty: confirmQuoteDelete(sent, ask('')),
  sentCancel: confirmQuoteDelete(sent, ask(null)),
  kinds: asked.map(a => a[0]),
}));
"""


def _node(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("這台沒有 node")
    r = subprocess.run([node, "--input-type=module"], input=script.encode("utf-8"),
                       capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return json.loads(r.stdout.decode("utf-8"))


def test_quote_delete_confirmation_is_harder_once_it_left_the_building():
    """已寄送／已簽核刪掉會讓客戶手上的 /q/{code} 變 404 —— 那些要打字確認案名。"""
    got = _node(_HARNESS % (_REPO / "frontend/js/shared/quote-delete.js").as_uri())

    assert got["draftSpec"]["strict"] is False
    assert got["draftSpec"]["title"] == "Q-論壇專案-v2"
    assert "無法復原" in got["draftSpec"]["message"]

    assert got["sentSpec"]["strict"] is True
    assert got["sentSpec"]["expect"] == "論壇專案"
    assert "已寄送" in got["sentSpec"]["message"]
    assert "要繼續請輸入案名" in got["sentSpec"]["message"]
    # 有分享連結才提連結會失效（沒有的話不要嚇人）
    assert "連線" not in got["sentSpec"]["message"] and "失效" not in got["sentSpec"]["message"]
    assert "失效" in got["sharedSpec"]["message"]

    assert got["noName"]["title"] == "Q-（未連專案）-v1", "沒帶欄位也要有得顯示"
    assert got["noName"]["strict"] is False, "沒有 status 就當草稿"

    assert got["draftOk"] is True, "草稿按 OK 就好"
    assert got["sentRight"] is True and got["sentPadded"] is True, "打對（含前後空白）才准"
    assert got["sentWrong"] is False and got["sentEmpty"] is False and got["sentCancel"] is False
    assert got["kinds"] == ["confirm", "prompt", "prompt", "prompt", "prompt", "prompt"], \
        "草稿用 confirm、其餘一律 prompt（不能退回按一下就過）"
