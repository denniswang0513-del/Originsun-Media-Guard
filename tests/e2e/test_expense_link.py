# -*- coding: utf-8 -*-
"""雜支登記分享連結（token）—— 給**沒有帳號**的現場人員用的那條路。

為什麼需要它：2026-08-14 的 `_crm_read_guard` 之後，`/public/projects/…` 那批
要登入才打得到（CRM 是商務資料，master 經 cloudflared 對外）。但現場登記雜支的
是外部場記／臨時人員，他們沒有帳號 —— token 才是「發一條連結給特定一件事」的
憑證，「專案 id 猜不到」不是。

這支測的是**匿名**（完全不帶 Authorization）能不能走完整條路，以及三道邊界：
  ① 亂 token → 401；停用 → 403
  ② 子表連結**綁死**那一天：前端就算在 payload 塞別的 cost_group_id 也要被忽略
  ③ 匿名看不到金額（子表預算）—— 第二層（MoneyRedactRoute）要在 public_router
     上也生效。這條特別重要：那個 route class 不會被 include_router 繼承。

自建自刪（dev/test 庫限定）。
"""
import pytest

from .conftest import HTTP, project_case

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def case(real_server, e2e_admin_token, dev_db_only):
    with project_case(real_server["base_url"], e2e_admin_token, "雜支連結") as c:
        yield c


def _mint(case, kind, target_id, rotate=False):
    r = HTTP.post(f"{case['base']}/api/v1/crm/expense-links", headers=case["h"],
                  json={"kind": kind, "target_id": target_id, "rotate": rotate})
    assert r.status_code == 200, r.text[:200]
    return r.json()["token"]


def test_anonymous_can_register_with_a_link(case):
    """整條路：發連結 → 匿名開 → 匿名登記 → 匿名看得到自己那筆。"""
    base, pid = case["base"], case["project_id"]
    tok = _mint(case, "project", pid)
    anon = {}                                     # 🔴 完全不帶 Authorization

    info = HTTP.get(f"{base}/api/v1/crm/public/expense/{tok}", headers=anon)
    assert info.status_code == 200, info.text[:200]
    assert info.json()["project"]["id"] == pid

    r = HTTP.post(f"{base}/api/v1/crm/public/expense/{tok}/expenses", headers=anon,
                  json={"category": "交通", "actual": 123, "sub_item": "e2e",
                        "payee": "現場", "notes": ""})
    assert r.status_code == 200, r.text[:200]
    eid = r.json()["expense_id"]
    try:
        rows = HTTP.get(f"{base}/api/v1/crm/public/expense/{tok}/expenses",
                        headers=anon).json()["expenses"]
        got = [e for e in rows if e["id"] == eid]
        assert got and got[0]["actual"] == 123, "登記完看不到自己那筆"
    finally:
        HTTP.delete(f"{base}/api/v1/crm/project-expenses/{eid}", headers=case["h"])


def test_minting_twice_keeps_the_same_link(case):
    """冪等：後台重複點「複製連結」不該讓已經發出去的那條失效。"""
    a = _mint(case, "project", case["project_id"])
    b = _mint(case, "project", case["project_id"])
    assert a == b
    c = _mint(case, "project", case["project_id"], rotate=True)
    assert c != a, "rotate 應該換一條新的（＝重置連結）"
    # 換完之後舊的要當場失效，否則「重置」是假的
    assert HTTP.get(f"{case['base']}/api/v1/crm/public/expense/{a}",
                    headers={}).status_code == 401


def test_bad_and_disabled_links_are_refused(case):
    base, pid = case["base"], case["project_id"]
    assert HTTP.get(f"{base}/api/v1/crm/public/expense/zzzz", headers={}).status_code == 401
    tok = _mint(case, "project", pid)
    HTTP.post(f"{base}/api/v1/crm/expense-links/{pid}/enabled?enabled=false",
              headers=case["h"])
    try:
        assert HTTP.get(f"{base}/api/v1/crm/public/expense/{tok}",
                        headers={}).status_code == 403, "停用了還打得到"
    finally:
        HTTP.post(f"{base}/api/v1/crm/expense-links/{pid}/enabled?enabled=true",
                  headers=case["h"])


def test_group_link_is_bound_to_its_shoot_day_and_hides_money(case):
    """🔴 子表連結兩件事：範圍綁死那一天、匿名看不到預算。

    綁死那半：payload 塞別的 `cost_group_id` 要被忽略 —— 連結的意義就是
    「這一天的雜支」，讓前端指定等於一條連結通吃整個專案。
    """
    base, h, pid = case["base"], case["h"], case["project_id"]
    r = HTTP.post(f"{base}/api/v1/crm/projects/{pid}/cost-groups", headers=h,
                  json={"name": "e2e 拍攝日", "misc_budget_amount": 5000})
    if r.status_code >= 400:
        pytest.skip(f"建不出子表（{r.status_code}）：{r.text[:120]}")
    gid = r.json()["cost_group"]["id"]
    tok = _mint(case, "group", gid)
    anon = {}
    try:
        info = HTTP.get(f"{base}/api/v1/crm/public/expense/{tok}", headers=anon).json()
        assert info["kind"] == "group" and info["group"]["id"] == gid
        for money in ("budget_amount", "misc_budget_amount"):
            assert money not in info["group"], f"匿名看到了金額欄 {money}"

        cr = HTTP.post(f"{base}/api/v1/crm/public/expense/{tok}/expenses", headers=anon,
                       json={"category": "飲食", "actual": 456, "sub_item": "e2e",
                             "payee": "現場", "notes": "",
                             "cost_group_id": "somebody-elses-group"})
        assert cr.status_code == 200, cr.text[:200]
        eid = cr.json()["expense_id"]
        full = HTTP.get(f"{base}/api/v1/crm/projects/{pid}/expenses",
                        headers=h).json()["expenses"]
        mine = [e for e in full if e["id"] == eid]
        assert mine and mine[0].get("cost_group_id") == gid, \
            "前端塞的 cost_group_id 沒有被忽略 —— 一條連結變成通吃整個專案"
        HTTP.delete(f"{base}/api/v1/crm/project-expenses/{eid}", headers=h)
    finally:
        HTTP.delete(f"{base}/api/v1/crm/cost-groups/{gid}", headers=h)
