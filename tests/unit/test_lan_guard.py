# -*- coding: utf-8 -*-
"""check_lan_or_logged_in —— 「LAN 免登入、對外要登入」的守衛。

這條線的來歷：8/08 對檔案系統端點一律要登入，把「同事在本機 agent 免登入用
後期流程」的產品前提弄壞了四天（挑資料夾按鈕靜默失效）。這裡釘住三件事：
LAN 直連匿名放行、經 cloudflared（CF 標頭）的匿名必擋、CF 流量帶有效 token
放行。CF 標頭偽造不了（edge 會覆寫），socket 對端讀的是 peer 不是 XFF。
"""
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from core.auth import check_lan_or_logged_in, create_token


def _req(client_host="192.168.1.50", headers=None):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode())
                    for k, v in (headers or {}).items()],
        "client": (client_host, 51234),
    }
    return Request(scope)


def test_lan_anonymous_allowed():
    """LAN 直連（私網 socket peer、無 CF 標頭）→ 匿名放行，回 None。"""
    for host in ("192.168.1.50", "127.0.0.1", "10.0.0.7", "172.16.3.9"):
        assert check_lan_or_logged_in(_req(host)) is None


def test_tunnel_anonymous_blocked():
    """經 cloudflared（CF 標頭在，socket 對端是本機的 cloudflared）→ 401。
    「來自 127.0.0.1」不能單獨當信任依據 —— tunnel 流量就是本機打進來的。"""
    for h in ({"cf-connecting-ip": "203.0.113.9"}, {"cf-ray": "8abc-TPE"}):
        with pytest.raises(HTTPException) as e:
            check_lan_or_logged_in(_req("127.0.0.1", headers=h))
        assert e.value.status_code == 401


def test_tunnel_with_token_allowed():
    """對外的合法使用者：CF 標頭 + 有效 token → 放行且拿得到 payload。"""
    tok = create_token({"sub": "u1", "username": "u1", "access_level": 1})
    p = check_lan_or_logged_in(_req("127.0.0.1", headers={
        "cf-ray": "8abc-TPE", "authorization": "Bearer " + tok}))
    assert p and p.get("username") == "u1"


def test_public_peer_anonymous_blocked():
    """socket 對端是公網位址（不該發生，防禦縱深）→ 401。"""
    with pytest.raises(HTTPException) as e:
        check_lan_or_logged_in(_req("203.0.113.9"))
    assert e.value.status_code == 401
