# -*- coding: utf-8 -*-
"""`/proposal-plan.html` → `/project.html` 的舊網址 301（2026-08-15 改名）。

🔴 這條守的不是路由表，是**已經發出去的東西**：客戶手上的共編連結、同事
書籤、Google Chat 裡貼過的網址，全都是舊路徑帶 `?t=` / `?pid=` / `?id=`。
掉了 query string，每一條分享連結都會變成一個沒有 token 的空頁 —— 而那種壞法
不會有人來報修，只會有人默默說「那個連結壞了」。

兩側各有一份規則（對外流量走 NAS nginx，不經過 master）：
  - master：main.py 的 `_legacy_proposal_plan`（必須註冊在 mount("/") 之前）
  - NAS   ：docker/nginx/originsun.conf 的 `return 301 …$is_args$args`
所以這裡兩份都驗。
"""
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NGINX_CONF = os.path.join(REPO, "docker", "nginx", "originsun.conf")

CASES = [
    ("", "/project.html"),
    ("?t=abc123", "/project.html?t=abc123"),
    ("?pid=deadbeef", "/project.html?pid=deadbeef"),
    ("?id=proj1&p=prop2", "/project.html?id=proj1&p=prop2"),
]


@pytest.mark.parametrize("qs,expect", CASES)
def test_master_redirects_and_keeps_the_query(app_client, qs, expect):
    r = app_client.get("/proposal-plan.html" + qs, follow_redirects=False)
    assert r.status_code == 301, f"舊網址沒轉址（{r.status_code}）"
    assert r.headers["location"] == expect


def test_nginx_has_the_same_rule_with_args():
    """NAS 那側漏了 `$is_args$args` 的話 master 全綠、客戶連結照樣壞。"""
    with open(NGINX_CONF, encoding="utf-8") as fh:
        conf = fh.read()
    assert "location = /proposal-plan.html" in conf, "nginx 沒有舊網址的 location"
    assert "return 301 /project.html$is_args$args;" in conf, \
        "nginx 的 301 沒帶 query string —— 分享連結的 token 會掉"
    assert "location = /project.html" in conf, "nginx 沒有新網址的 location"
