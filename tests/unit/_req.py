# -*- coding: utf-8 -*-
"""單元測試用：拿真 token 造一個 starlette Request，餵給守衛函式測拒絕碼。

四個測試各自手刻過同一個 ASGI scope dict —— `core.auth._extract_token` 讀 header
的方式一改就要改四處。這裡只有一份。
"""
from starlette.requests import Request

from core.auth import create_token


def token_request(**claims) -> Request:
    """預設 Lv1、沒模組；用 keyword 蓋掉（access_level=3、modules=[...]）。"""
    tok = create_token({"sub": "u", "username": "u", "access_level": 1, "modules": [], **claims})
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                    "headers": [(b"authorization", ("Bearer " + tok).encode())]})
