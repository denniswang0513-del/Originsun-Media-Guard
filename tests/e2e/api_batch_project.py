# -*- coding: utf-8 -*-
"""批次把請款單掛到專案，走真的端點、真的 DB（owner 2026-08-23）。

源碼掃描擋得住「守衛被拿掉」，擋不住「守衛寫了但條件反了」。這支證明四件事：
  ① 可連結的類別（專案外包／專案雜支）真的掛得上去
  ② 混進一張不能連結的（行政）→ **整批 409**，一張都沒動
  ③ 解除連結（project_id=null）不受類別守衛限制
  ④ unassigned=1 只列「該掛而未掛」的，不把行政那張算進待辦

跑在 dev 8001 / mediaguard_dev。
"""
import asyncio
import io
import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

TOK = create_token({"sub": "admin", "username": "admin", "access_level": 3,
                    "modules": ["finance", "crm_invoices", "crm_projects"]})
BASE = "http://localhost:8001/api/v1/crm"
TAG = "ZZBP"
fails = []


def call(path, method="GET", body=None):
    r = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Authorization": "Bearer " + TOK, "Content-Type": "application/json"})
    try:
        raw = urllib.request.urlopen(r, timeout=180).read()
        return json.loads(raw) if raw else {}, 200
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8"), e.code


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra else ""))
    if not ok:
        fails.append(label)


async def seed():
    from db.models import CrmPaymentRequest, CrmProject
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        proj = CrmProject(id=uuid.uuid4().hex, name=TAG + " 測試專案", status="製作")
        s.add(proj)
        ids = {}
        for key, cat in (("out", "專案外包"), ("misc", "專案雜支"), ("admin", "行政")):
            p = CrmPaymentRequest(
                id=uuid.uuid4().hex, entity="parent", request_date=datetime(2026, 8, 1),
                amount=1000, summary=f"{TAG} {cat}", category=cat,
                payee_name=TAG + "廠商", payment_status="應付款")
            s.add(p)
            ids[key] = p.id
        await s.commit()
        return proj.id, ids


async def project_of(pid):
    from db.models import CrmPaymentRequest
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        p = await s.get(CrmPaymentRequest, pid)
        return p.project_id if p else "(不存在)"


async def clean():
    from sqlalchemy import select

    from db.models import CrmPaymentRequest, CrmProject
    from db.session import get_session_factory, init_db
    await init_db()
    async with get_session_factory()() as s:
        for m, col in ((CrmPaymentRequest, CrmPaymentRequest.summary),
                       (CrmProject, CrmProject.name)):
            for r in (await s.execute(select(m).where(col.like(TAG + "%")))).scalars().all():
                await s.delete(r)
        await s.commit()


asyncio.run(clean())
proj_id, ids = asyncio.run(seed())
try:
    print("[1] 可連結的兩張掛上去")
    r, code = call("/payments/batch-project", "PATCH",
                   {"payment_ids": [ids["out"], ids["misc"]], "project_id": proj_id})
    check(code == 200, "HTTP 200", str(r)[:120])
    check(isinstance(r, dict) and r.get("updated") == 2, "updated=2", str(r)[:100])
    check(asyncio.run(project_of(ids["out"])) == proj_id, "專案外包那張掛上了")
    check(asyncio.run(project_of(ids["misc"])) == proj_id, "專案雜支那張掛上了")

    print("")
    print("[2] 混一張「行政」進來 → 整批擋下，一張都不能動")
    before = asyncio.run(project_of(ids["admin"]))
    r, code = call("/payments/batch-project", "PATCH",
                   {"payment_ids": [ids["out"], ids["admin"]], "project_id": proj_id})
    check(code == 409, "HTTP 409", str(code))
    check("行政" in str(r), "訊息說出是哪個類別違規", str(r)[:160])
    check(asyncio.run(project_of(ids["admin"])) == before, "行政那張沒被掛上")

    print("")
    print("[3] 解除連結不受類別守衛限制")
    r, code = call("/payments/batch-project", "PATCH",
                   {"payment_ids": [ids["out"], ids["misc"], ids["admin"]],
                    "project_id": None})
    check(code == 200, "HTTP 200（含那張行政也放行）", str(r)[:120])
    check(asyncio.run(project_of(ids["out"])) is None, "專案外包那張已解除")

    print("")
    print("[4] unassigned=1 只列該掛而未掛的")
    r, code = call("/payments?unassigned=1")
    rows = r.get("payments", []) if code == 200 else []
    mine = {p["id"] for p in rows} & set(ids.values())
    check(code == 200, "HTTP 200")
    check(ids["out"] in mine and ids["misc"] in mine, "專案外包／專案雜支有列出來")
    check(ids["admin"] not in mine, "行政那張沒被列進待辦（它本來就不該掛）")

    print("")
    print("[5] 找不到的 id 要講出來，不是默默成功")
    r, code = call("/payments/batch-project", "PATCH",
                   {"payment_ids": [ids["out"], "no-such-id"], "project_id": proj_id})
    check(code == 404, "HTTP 404", str(code) + " " + str(r)[:100])
finally:
    print("")
    print("[清理]")
    asyncio.run(clean())

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
