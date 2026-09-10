# -*- coding: utf-8 -*-
"""機隊 → master 的 🔴 告警轉寄（2026-09-10 備檔電腦備份失敗時挖出來的）。

原本兩邊都拿「自己的 jwt_secret」當共用金鑰 —— 每台機器各自產、不共用，
所以 agent 送的永遠對不上 master 的，**這條路徑對機隊是永遠 403**。
備檔電腦三次 task_failed 告警全被擋掉，email 一封都沒寄出去。
"""
import asyncio
import json

import pytest

from notifier import INTERNAL_ALERT_KEY
from routers.api_system import internal_alert_email


class _Req:
    """夠用的假 Request：認證那段只碰 headers，過關之後才碰 body。"""

    def __init__(self, key=None, body=None, cloudflare=False):
        h = {}
        if key is not None:
            h["X-Internal-Key"] = key
        if cloudflare:
            h["cf-ray"] = "8f2a1b9c0d1e2f34-TPE"
        self.headers = h
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _call(req):
    return asyncio.run(internal_alert_email(req))


def _status(resp):
    return resp.status_code


class TestAuth:
    def test_the_shipped_key_gets_through(self):
        """帶壞掉的 body → 400「invalid json」＝ 認證這關已經過了。"""
        assert _status(_call(_Req(INTERNAL_ALERT_KEY))) == 400

    def test_no_key_is_rejected(self):
        assert _status(_call(_Req())) == 403

    def test_wrong_key_is_rejected(self):
        assert _status(_call(_Req("nope"))) == 403

    def test_an_agents_own_jwt_secret_is_not_a_valid_key(self):
        """就是這個造成半年份的 403：agent 送自己的 jwt_secret。"""
        assert _status(_call(_Req("a" * 64))) == 403

    def test_masters_own_secret_still_works(self, monkeypatch):
        """master/NAS 之間確實共用同一把，那條路要保留。"""
        import core.auth
        monkeypatch.setattr(core.auth, "_get_secret", lambda: "shared-secret")
        assert _status(_call(_Req("shared-secret"))) == 400

    def test_public_traffic_is_closed_even_with_the_right_key(self):
        """金鑰隨 OTA 包公開，所以公網那條路一律關（同 /internal/restart）。"""
        assert _status(_call(_Req(INTERNAL_ALERT_KEY, cloudflare=True))) == 403

    def test_empty_body_is_rejected_after_auth(self):
        assert _status(_call(_Req(INTERNAL_ALERT_KEY, body={"body": "  "}))) == 400


class TestSender:
    def test_relay_sends_the_shared_key_not_the_local_jwt_secret(self, monkeypatch):
        import urllib.request

        import notifier

        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["key"] = req.headers.get("X-internal-key")
            seen["payload"] = json.loads(req.data.decode("utf-8"))

            class _R:
                def close(self):
                    pass
            return _R()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        notifier._relay_alert_email(
            "task_failed", "NAS 認證失敗…",
            {"master_server": "http://192.168.1.107:8000",
             "jwt_secret": "this-machines-own-secret"},
        )
        assert seen["key"] == INTERNAL_ALERT_KEY
        assert seen["key"] != "this-machines-own-secret"
        assert seen["url"].endswith("/api/v1/internal/alert_email")
        assert "task_failed" in seen["payload"]["subject"]

    def test_no_master_configured_is_a_silent_noop(self, monkeypatch):
        import urllib.request

        import notifier

        monkeypatch.delenv("MASTER_SERVER", raising=False)
        monkeypatch.setattr(urllib.request, "urlopen",
                            lambda *a, **k: pytest.fail("沒設 master 不該送"))
        notifier._relay_alert_email("task_failed", "x", {})


class TestBothSidesStayInSync:
    def test_neither_side_falls_back_to_the_jwt_secret_only_check(self):
        """釘住回歸：任一邊改回只認 _get_secret() 就會再壞一次，而且是靜默的。"""
        recv = open("routers/api_system.py", encoding="utf-8").read()
        fn = recv[recv.index('@router.post("/api/v1/internal/alert_email")'):]
        fn = fn[:fn.index("\n@router.")]
        assert "INTERNAL_ALERT_KEY" in fn, "接收端要認共用金鑰"
        assert "via_cloudflare" in fn, "公開金鑰的端點一定要關公網那條路"

        send = open("notifier.py", encoding="utf-8").read()
        body = send[send.index("def _relay_alert_email"):]
        body = body[:body.index("\ndef ")]
        assert "INTERNAL_ALERT_KEY" in body
        assert "jwt_secret" not in body, "發送端不准再拿本機 jwt_secret 當金鑰"
