# -*- coding: utf-8 -*-
"""福委會的單據與心得：給欄位、但不擋（owner 2026-08-21）。

> 「他們會需要上傳單據 以及心得筆記，才有辦法請款…但這塊並不是一定要上傳才能
>   請款，這樣才符合各種使用情境，有心得跟有單據讓我知道就好。」

所以要證明的是兩件事：
  ① 沒單據沒心得 → 照樣登記、照樣核准、照樣付款（**不擋**）
  ② 有沒有要標示出來（has_receipt / has_reflection）

自己建 ZZ 池與臨時帳號，跑完刪光（連磁碟上的單據）。
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, r"E:\Dev\Originsun-Media-Guard")
from core.auth import create_token  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001") + "/api/v1"
ADMIN = create_token({"sub": "admin", "username": "admin",
                      "access_level": 3, "modules": []})
FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "fixtures", "einvoice_full.pdf")   # 當成單據用，內容不重要
fails = []


def check(ok, label, extra=""):
    print(("  PASS " if ok else "  FAIL ") + label + (f" — {extra}" if extra != "" else ""))
    if not ok:
        fails.append(label)


def call(m, path, body=None, tok=ADMIN):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=d, method=m, headers={
        "Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b or b"{}")
        except Exception:
            return e.code, b.decode("utf-8", "replace")


def upload(entry_id, tok):
    bd = "----zz"
    body = (f"--{bd}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"receipt.pdf\"\r\nContent-Type: application/pdf\r\n\r\n").encode()
    body += open(FIX, "rb").read() + f"\r\n--{bd}--\r\n".encode()
    r = urllib.request.Request(
        BASE + f"/crm/benefits/entries/{entry_id}/receipt", data=body, method="POST",
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": f"multipart/form-data; boundary={bd}"})
    try:
        with urllib.request.urlopen(r, timeout=120) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b or b"{}")
        except Exception:
            return e.code, b.decode("utf-8", "replace")


async def setup():
    from db.session import init_db, get_session_factory
    from db.models import CrmStaff, User
    await init_db()
    async with get_session_factory()() as s:
        out = {}
        for who in ("甲", "乙"):
            st = CrmStaff(id=uuid.uuid4().hex[:16], name=f"ZZ證明{who}")
            s.add(st)
            s.add(User(username=f"zz_pf_{who}", password_hash="x", access_level=1,
                       modules=["me_benefits"], staff_id=st.id))
            out[who] = st.id
        await s.commit()
        return out


async def teardown():
    from db.session import init_db, get_session_factory
    from sqlalchemy import select
    from db.models import (CrmPaymentRequest, CrmStaff, HrBenefitEntry,
                           HrBenefitFunding, HrBenefitPool, User)
    await init_db()
    async with get_session_factory()() as s:
        for pool in (await s.execute(select(HrBenefitPool).where(
                HrBenefitPool.name.like("ZZ證明%")))).scalars().all():
            for e in (await s.execute(select(HrBenefitEntry).where(
                    HrBenefitEntry.pool_id == pool.id))).scalars().all():
                if e.receipt_url and os.path.isfile(e.receipt_url):
                    try:
                        os.remove(e.receipt_url)
                    except OSError:
                        pass
                if e.payment_request_id:
                    ap = await s.get(CrmPaymentRequest, e.payment_request_id)
                    if ap:
                        await s.delete(ap)
                await s.delete(e)
            for f in (await s.execute(select(HrBenefitFunding).where(
                    HrBenefitFunding.pool_id == pool.id))).scalars().all():
                await s.delete(f)
            await s.delete(pool)
        for u in (await s.execute(select(User).where(
                User.username.like("zz_pf_%")))).scalars().all():
            await s.delete(u)
        for st in (await s.execute(select(CrmStaff).where(
                CrmStaff.name.like("ZZ證明%")))).scalars().all():
            await s.delete(st)
        await s.commit()


asyncio.run(teardown())
staff = asyncio.run(setup())
tok = {w: create_token({"sub": f"zz_pf_{w}", "username": f"zz_pf_{w}",
                        "access_level": 1, "modules": ["me_benefits"]})
       for w in staff}
_, r = call("POST", "/crm/benefits/pools", {"name": "ZZ證明池"})
pool_id = r["pool"]["id"]
call("POST", f"/crm/benefits/pools/{pool_id}/fundings",
     {"year": 2026, "amount": 50000})

try:
    print("[1] 🔴 什麼都沒有也登記得了（owner：不是一定要上傳才能請款）")
    st, r = call("POST", "/crm/benefits/me/entries",
                 {"pool_id": pool_id, "title": "ZZ 沒單據沒心得", "amount": 300},
                 tok=tok["甲"])
    check(st == 200, "登記成功", st)
    bare = r["entry"]["id"]
    check(r["entry"]["has_receipt"] is False, "標示為沒單據")
    check(r["entry"]["has_reflection"] is False, "標示為沒心得")

    print("\n[2] 🔴 而且核得准、付得了（整條路都不擋）")
    st, _ = call("POST", f"/crm/benefits/entries/{bare}/approve")
    check(st == 200, "核准成功", st)
    st, _ = call("POST", f"/crm/benefits/entries/{bare}/pay?payment_date=2026-08-21")
    check(st == 200, "登記匯款成功", st)

    print("\n[3] 有心得的：登記時就寫")
    st, r = call("POST", "/crm/benefits/me/entries",
                 {"pool_id": pool_id, "title": "ZZ 有心得", "amount": 400,
                  "reflection": "節奏很好，攝影值得學"}, tok=tok["甲"])
    with_note = r["entry"]["id"]
    check(r["entry"]["has_reflection"] is True, "標示為有心得")
    check(r["entry"]["reflection"] == "節奏很好，攝影值得學", "心得內容帶回來",
          r["entry"]["reflection"])

    print("\n[4] 只有空白不算有心得")
    st, r = call("POST", "/crm/benefits/me/entries",
                 {"pool_id": pool_id, "title": "ZZ 空白心得", "amount": 100,
                  "reflection": "   "}, tok=tok["甲"])
    check(r["entry"]["has_reflection"] is False, "空白不算", r["entry"]["reflection"])
    call("DELETE", f"/crm/benefits/me/entries/{r['entry']['id']}", tok=tok["甲"])

    print("\n[5] 補傳單據")
    st, r = upload(with_note, tok["甲"])
    check(st == 200, "上傳成功", st)
    check(r["entry"]["has_receipt"] is True, "標示為有單據")
    path = r["entry"]["receipt_url"]
    check("_福委會" in path, "存在福委會的收據夾", path)
    check(os.path.isfile(path) if not BASE.endswith("8000/api/v1") else True,
          "檔案真的在磁碟上")

    print("\n[6] 🔴 傳不到別人的登記上")
    st, r = upload(with_note, tok["乙"])
    check(st in (403, 409), "乙傳甲的 → 被擋", (st, r.get("detail") if isinstance(r, dict) else r))

    print("\n[7] 🔴 核准之後不能再換單據（那時帳上已經掛著應付款）")
    call("POST", f"/crm/benefits/entries/{with_note}/approve")
    st, r = upload(with_note, tok["甲"])
    check(st == 409, "已核准 → 409", (st, r.get("detail") if isinstance(r, dict) else r))

    print("\n[8] 待審佇列帶得出兩個旗標（owner 要在這裡看）")
    st, r = call("POST", "/crm/benefits/me/entries",
                 {"pool_id": pool_id, "title": "ZZ 待審用", "amount": 250,
                  "reflection": "有寫"}, tok=tok["甲"])
    st, q = call("GET", "/crm/benefits/entries?status=%E5%BE%85%E5%AF%A9")
    row = next((x for x in q["items"] if x["title"] == "ZZ 待審用"), None)
    check(row is not None, "在待審清單裡")
    check(row and "has_receipt" in row and "has_reflection" in row,
          "兩個旗標都帶出來了")
    check(row and row["has_reflection"] is True and row["has_receipt"] is False,
          "旗標值正確", row and (row["has_receipt"], row["has_reflection"]))
finally:
    print("\n[清理]")
    asyncio.run(teardown())
    st, d = call("GET", "/crm/benefits/pools")
    check(not [x for x in d.get("items", []) if x["name"].startswith("ZZ證明")],
          "測試資料清光")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
