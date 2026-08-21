# -*- coding: utf-8 -*-
"""年度活動端對端：每人額度、期間、說明附件（docs/BENEFIT_POOL_PLAN.md §9）。

> owner 2026-08-21：「我想要有一個像是活動，然後下面有好幾個人的額度與
>   有效期間，然後有一些活動說明。」
> 「有些是健檢方案⋯⋯我覺得可以就是一個可以打字、附上文件的說明。」

要證明的三件事：
  ① 每人各自一份額度，超額與期間外**擋得住**（共用池不受影響）
  ② 說明打得了字、附件上傳得了、員工在 /my 看得到
  ③ 既有的共用池行為**完全沒變**（這次改動不能波及快樂／進修）

自己建 ZZ 活動與臨時帳號，跑完刪光（連磁碟上的附件）。
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
# DB helper 要跟 API 打同一個庫（生產跑時把生產的 settings 插到最前面）
if ":8000" in BASE:
    sys.path.insert(0, r"C:\OriginsunAgent")
    os.chdir(r"C:\OriginsunAgent")
ADMIN = create_token({"sub": "admin", "username": "admin",
                      "access_level": 3, "modules": []})
FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "fixtures", "einvoice_full.pdf")   # 當健檢方案 PDF 用
fails = []
LEFTOVER = []


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


def upload(pool_id, tok=ADMIN):
    bd = "----zzact"
    body = (f"--{bd}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"健檢方案.pdf\"\r\nContent-Type: application/pdf\r\n\r\n").encode()
    body += open(FIX, "rb").read() + f"\r\n--{bd}--\r\n".encode()
    r = urllib.request.Request(
        BASE + f"/crm/benefits/pools/{pool_id}/files", data=body, method="POST",
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": f"multipart/form-data; boundary={bd}"})
    try:
        with urllib.request.urlopen(r, timeout=120) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


async def setup():
    from db.session import init_db, get_session_factory
    from db.models import CrmStaff, User
    await init_db()
    async with get_session_factory()() as s:
        out = {}
        for who in ("甲", "乙"):
            st = CrmStaff(id=uuid.uuid4().hex[:16], name=f"ZZ活動{who}", status="在職")
            s.add(st)
            s.add(User(username=f"zz_act_{who}", password_hash="x", access_level=1,
                       modules=["me_benefits"], staff_id=st.id))
            out[who] = st.id
        await s.commit()
        return out


async def teardown():
    from db.session import init_db, get_session_factory
    from sqlalchemy import select
    from db.models import (CrmPaymentRequest, CrmStaff, HrBenefitAllowance,
                           HrBenefitEntry, HrBenefitFunding, HrBenefitPool, User)
    await init_db()
    async with get_session_factory()() as s:
        for pool in (await s.execute(select(HrBenefitPool).where(
                HrBenefitPool.name.like("ZZ活動%")))).scalars().all():
            for f in (pool.attachments or []):
                try:
                    os.remove(f.get("path") or "")
                except FileNotFoundError:
                    pass
                except OSError as err:
                    LEFTOVER.append(f"{f.get('path')}（{err.__class__.__name__}）")
            for e in (await s.execute(select(HrBenefitEntry).where(
                    HrBenefitEntry.pool_id == pool.id))).scalars().all():
                if e.payment_request_id:
                    ap = await s.get(CrmPaymentRequest, e.payment_request_id)
                    if ap:
                        await s.delete(ap)
                await s.delete(e)
            for a in (await s.execute(select(HrBenefitAllowance).where(
                    HrBenefitAllowance.pool_id == pool.id))).scalars().all():
                await s.delete(a)
            for f in (await s.execute(select(HrBenefitFunding).where(
                    HrBenefitFunding.pool_id == pool.id))).scalars().all():
                await s.delete(f)
            await s.delete(pool)
        for u in (await s.execute(select(User).where(
                User.username.like("zz_act_%")))).scalars().all():
            await s.delete(u)
        for st in (await s.execute(select(CrmStaff).where(
                CrmStaff.name.like("ZZ活動%")))).scalars().all():
            await s.delete(st)
        await s.commit()


asyncio.run(teardown())
staff = asyncio.run(setup())
tok = {w: create_token({"sub": f"zz_act_{w}", "username": f"zz_act_{w}",
                        "access_level": 1, "modules": ["me_benefits"]})
       for w in staff}

# 先記下共用池現在的樣子 —— 這次改動不准波及它們
_, before = call("GET", "/crm/benefits/pools")
shared_before = {p["name"]: (p["funded"], p["used"], p["balance"])
                 for p in before["items"] if p["quota"] == "shared"}

try:
    print("[1] 開一個年度活動（每人一份）")
    st, r = call("POST", "/crm/benefits/pools",
                 {"name": "ZZ活動 LAZY KIT", "quota": "per_person"})
    check(st == 200, "建立成功", st)
    pool = r["pool"]["id"]
    check(r["pool"]["quota"] == "per_person", "類型是每人一份", r["pool"]["quota"])

    print("")
    print("[2] 說明打字 + 期間")
    desc = "有薪假期間的所有支出 10,000 內\n內容不限單件"
    st, r = call("PUT", f"/crm/benefits/pools/{pool}",
                 {"name": "ZZ活動 LAZY KIT", "quota": "per_person",
                  "description": desc,
                  "valid_from": "2026-01-01", "valid_to": "2026-12-31"})
    check(st == 200, "存得起來", st)
    check(r["pool"]["description"] == desc, "換行沒被吃掉",
          repr(r["pool"]["description"])[:50])
    check(r["pool"]["valid_from"] == "2026-01-01"
          and r["pool"]["valid_to"] == "2026-12-31", "期間正確",
          (r["pool"]["valid_from"], r["pool"]["valid_to"]))

    print("")
    print("[3] 附件（健檢方案就是靠這個）")
    st, r = upload(pool)
    check(st == 200, "上傳成功", st)
    atts = r["pool"]["attachments"]
    check(len(atts) == 1, "清單裡有一個", len(atts))
    check(atts[0]["name"] == "健檢方案.pdf", "檔名保留", atts and atts[0]["name"])
    fid = atts[0]["id"]
    # 🔴 存說明**不可以**把附件清掉（附件不在 payload 裡就是為了這個）
    st, r = call("PUT", f"/crm/benefits/pools/{pool}",
                 {"name": "ZZ活動 LAZY KIT", "quota": "per_person",
                  "description": desc + "（改過）",
                  "valid_from": "2026-01-01", "valid_to": "2026-12-31"})
    check(len(r["pool"]["attachments"]) == 1, "🔴 存說明沒有把附件洗掉",
          len(r["pool"]["attachments"]))

    print("")
    print("[4] 發額度：一人 10,000")
    for w in ("甲", "乙"):
        st, _ = call("POST", f"/crm/benefits/pools/{pool}/allowances",
                     {"staff_id": staff[w], "amount": 10000})
        check(st == 200, f"發給 {w}", st)
    st, r = call("POST", f"/crm/benefits/pools/{pool}/allowances",
                 {"staff_id": staff["甲"], "amount": 5000})
    check(st == 409, "同一個人不能發第二份", st)

    st, d = call("GET", f"/crm/benefits/pools/{pool}")
    ro = d["pool"]["allowance_rollup"]
    check(ro["granted"] == 20000 and ro["people"] == 2, "已配 20,000 / 2 人", ro)
    check(ro["untouched"] == 2, "兩個人都還沒動用", ro["untouched"])

    print("")
    print("[5] 額度內登記得了")
    st, r = call("POST", "/crm/benefits/me/entries",
                 {"pool_id": pool, "title": "ZZ 住宿", "amount": 4000,
                  "spend_date": "2026-05-01"}, tok=tok["甲"])
    check(st == 200, "4,000 過關", st)
    eid = r["entry"]["id"]

    print("")
    print("[6] 🔴 超過自己的額度要擋（共用池只轉紅不擋，這裡不一樣）")
    st, r = call("POST", "/crm/benefits/me/entries",
                 {"pool_id": pool, "title": "ZZ 超額", "amount": 7000,
                  "spend_date": "2026-05-02"}, tok=tok["甲"])
    check(st == 422, "被擋", st)
    check("6,000" in str(r.get("detail", "")), "有講還可以用多少", r.get("detail"))

    print("")
    print("[7] 🔴 期間外要擋")
    st, r = call("POST", "/crm/benefits/me/entries",
                 {"pool_id": pool, "title": "ZZ 過期", "amount": 100,
                  "spend_date": "2027-01-01"}, tok=tok["甲"])
    check(st == 422, "被擋", st)
    check("期間" in str(r.get("detail", "")), "有講期間", r.get("detail"))

    print("")
    print("[8] 🔴 改金額也要重驗（不然登記 1 元再改成 99999 就繞過去了）")
    st, r = call("PUT", f"/crm/benefits/me/entries/{eid}",
                 {"pool_id": pool, "title": "ZZ 住宿", "amount": 99999,
                  "spend_date": "2026-05-01"}, tok=tok["甲"])
    check(st == 422, "被擋", st)
    # 但改成額度內的金額要過（自己那筆不能吃自己）
    st, r = call("PUT", f"/crm/benefits/me/entries/{eid}",
                 {"pool_id": pool, "title": "ZZ 住宿", "amount": 9000,
                  "spend_date": "2026-05-01"}, tok=tok["甲"])
    check(st == 200, "改成 9,000（額度內）過關 —— 自己不吃自己", st)

    print("")
    print("[9] 額度是**各自**的 —— 甲用掉不影響乙")
    st, r = call("POST", "/crm/benefits/me/entries",
                 {"pool_id": pool, "title": "ZZ 乙的", "amount": 9500,
                  "spend_date": "2026-06-01"}, tok=tok["乙"])
    check(st == 200, "乙照樣有自己的 10,000", st)

    print("")
    print("[10] 員工在 /my 看得到說明、附件、自己的額度")
    st, me = call("GET", "/crm/benefits/me", tok=tok["甲"])
    p = next(x for x in me["pools"] if x["id"] == pool)
    check(p["description"].startswith("有薪假期間"), "說明帶得出來")
    check(len(p["attachments"]) == 1, "附件帶得出來")
    a = p["mine_allowance"]
    check(a is not None, "帶了我的額度")
    check(a["quota"] == 10000, "額度 10,000", a and a["quota"])
    check(a["balance"] == 10000, "顯示用的餘額還是 10,000（那筆還在待審）",
          a and a["balance"])
    check(a["available"] == 1000, "可再申請只剩 1,000（待審佔住自己的額度）",
          a and a["available"])
    check(a["valid_from"] == "2026-01-01", "期間帶得出來", a and a["valid_from"])
    # 甲看不到乙的額度（own-scope）
    check(str(staff["乙"]) not in json.dumps(me), "看不到別人的東西")

    print("")
    print("[11] 沒發到額度的人：畫面要講得出來，登記要被擋")
    st, r = call("POST", "/crm/benefits/pools",
                 {"name": "ZZ活動 沒發", "quota": "per_person"})
    p2 = r["pool"]["id"]
    st, r = call("POST", "/crm/benefits/me/entries",
                 {"pool_id": p2, "title": "ZZ x", "amount": 1}, tok=tok["甲"])
    check(st == 422 and "沒有發給你" in str(r.get("detail", "")), "被擋且說明原因",
          (st, r.get("detail")))
    st, me = call("GET", "/crm/benefits/me", tok=tok["甲"])
    check(next(x for x in me["pools"] if x["id"] == p2)["mine_allowance"] is None,
          "帶回 None（畫面才知道要說『沒有發給你』）")

    print("")
    print("[12] 額度用過就不能刪；附件刪得掉")
    st, d = call("GET", f"/crm/benefits/pools/{pool}")
    aid = next(x["id"] for x in d["allowances"] if x["staff_id"] == staff["甲"])
    st, r = call("DELETE", f"/crm/benefits/allowances/{aid}")
    check(st == 409, "用過的額度不能刪", st)
    st, r = call("DELETE", f"/crm/benefits/pools/{pool}/files/{fid}")
    check(st == 200, "附件刪得掉", st)
    check(r["pool"]["attachments"] == [], "清單空了")

    print("")
    print("[13] 🔴 既有的共用池完全沒被波及")
    _, after = call("GET", "/crm/benefits/pools")
    shared_after = {p["name"]: (p["funded"], p["used"], p["balance"])
                    for p in after["items"] if p["quota"] == "shared"}
    check(shared_before == shared_after, "快樂／進修的數字一個都沒動",
          [k for k in shared_before if shared_before.get(k) != shared_after.get(k)])
    names = [p["name"] for p in after["items"] if p["quota"] == "shared"]
    if names:
        sp = next(p["id"] for p in after["items"] if p["quota"] == "shared")
        st, r = call("POST", "/crm/benefits/me/entries",
                     {"pool_id": sp, "title": "ZZ 共用池不擋", "amount": 999999,
                      "spend_date": "2026-05-01"}, tok=tok["甲"])
        check(st == 200, "共用池超支照樣過（只轉紅不擋）", st)
        if st == 200:
            call("DELETE", f"/crm/benefits/me/entries/{r['entry']['id']}", tok=tok["甲"])
finally:
    print("")
    print("[清理]")
    asyncio.run(teardown())
    _, d = call("GET", "/crm/benefits/pools")
    check(not [x for x in d.get("items", []) if x["name"].startswith("ZZ活動")],
          "測試活動清光")
    check(not LEFTOVER, "磁碟上的附件也清光", LEFTOVER)

print("")
print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
