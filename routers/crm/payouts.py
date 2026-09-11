"""routers/crm/payouts.py — 匯款通知（`/p/{短碼}`）。

owner 2026-09-11：出納匯完款要通知收款人「這次匯了什麼」，現在是看著清單手打。
做成一條連結，跟報價單 `/q/` 與發票 `/e/` 同一套機制。

**一次匯款一張通知**，綁的是出納勾選的「有匯的那幾筆」（owner 原話：「不用按全部
付款，反而是把有匯的列出」）—— 部分匯款、補匯都是常態，綁在「這個月的全部」上撐不住。

## 三件事照發票那份抄，不要自己發明

1. **短碼是可撤銷的憑證**：`share_token` 逐字比對，不驗簽章。撤銷＝清成 NULL。
2. **分享時定稿**：`share_snapshot` 在鑄連結那一刻寫死（規則在 `core/payout_share.py`）。
   DB 那幾筆是活的，他存摺上的數字不會變 —— 不定稿那頁就會跟著飄，而矛盾只有他看得到。
3. **公開那支掛 `public_router`**：NAS 對外容器也吃得到，master 關機他照樣打得開。
   加／改端點要同步兩支白名單測試（`test_media_log_public_router`、`test_public_surface`），
   而且公開面登記表（`core/public_access.py`）要有這一面。

🔴 金額：`public_router` 掛著 `MoneyRedactRoute`，這頁的金額會被抹掉，所以
`core/money.MONEY_EXEMPT_PREFIXES` 加了這支。門檻三條都成立才准加：憑證是逐字比對的
可撤銷連結、回的是白名單投影、數字是收件人本來就會拿到的（他存摺上就有）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Query, Request  # type: ignore
from sqlalchemy import select  # type: ignore

from core import payout_share
from core.public_access import surface_gate
from core.schemas import PayoutCreate

from ._shared import (_fmt_day, _get_factory, _now, _require_db, money_dep,
                      public_router, router)


def _payer_name() -> str:
    """匯款方＝我們的公司名（存摺上的轉入備註就是它，所以那頁要印一致的字）。"""
    from config import load_settings
    return str((load_settings().get("company") or {}).get("name") or "").strip()


@router.post("/payouts", dependencies=[Depends(money_dep)])
async def create_payout(req: PayoutCreate, request: Request):
    """把出納勾的那幾筆綁成一次匯款，鑄一條通知連結。

    守衛同帳務寫入（`money_dep` ＋ 下面的 `require_entity`）—— 這支會讀請款列的金額。

    **不改付款狀態**：標已付款是另一個動作（面板上那顆），出納可能先匯再標、也可能
    先標再產通知。把兩件事綁在一起的話，「只想補一張通知」就得先把狀態退回去。
    """
    from core.auth import new_short_token
    from core.ledger import require_entity
    from db.models import CrmPaymentRequest, CrmPayout

    # 去重：重複勾同一筆時 rows 會比 ids 少，原本會回一句「有幾筆找不到」——
    # 那句話是錯的，它們都在。
    ids = list(dict.fromkeys(
        str(i).strip() for i in (req.payment_ids or []) if str(i).strip()))
    if not ids:
        raise HTTPException(status_code=422, detail="至少要勾一筆")
    ent = require_entity(request, req.entity or "", level="full")
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        rows = (await session.execute(
            select(CrmPaymentRequest).where(CrmPaymentRequest.id.in_(ids)))).scalars().all()
        if len(rows) != len(ids):
            raise HTTPException(status_code=404, detail="有幾筆找不到，請重新整理再試")
        # 一張通知＝一個收款人的一次匯款。混到兩個人身上等於把別人的金額寄給他。
        payees = {(r.payee_name or "").strip() for r in rows}
        if len(payees) > 1:
            raise HTTPException(status_code=422, detail=f"一張通知只能有一個收款人（勾到了 {len(payees)} 位）")
        if any((r.entity or "parent") != ent for r in rows):
            raise HTTPException(status_code=422, detail="勾到的請款單不在同一本帳")
        # 🔴 一筆請款單只屬於**一張還活著的**通知。原本是無條件 `r.payout_id = 新的`：
        # 舊那張的短碼還活著、快照還在（定稿是刻意的），收款人手上兩條連結加起來
        # 是重複金額，而舊那張在 DB 裡變成沒有任何請款單屬於它的孤兒 —— 全程零錯誤。
        #
        # 只擋**還有短碼**的那些：撤銷（DELETE /payouts/{id}/share）就是出路，撤掉之後
        # 那幾筆可以重新綁。擋「所有 payout_id 非空的」會變成死路 —— 撤銷刻意不清
        # payout_id（那是「同一次匯出去」的事實），於是撤了也還是 422，永遠卡住。
        bound_ids = {r.payout_id for r in rows if r.payout_id}
        if bound_ids:
            live = (await session.execute(
                select(CrmPayout.id).where(CrmPayout.id.in_(bound_ids),
                                           CrmPayout.share_token.isnot(None)))).scalars().all()
            if live:
                n = sum(1 for r in rows if r.payout_id in set(live))
                raise HTTPException(
                    status_code=422,
                    detail=f"勾到的請款單裡有 {n} 筆已經在別的匯款通知上（連結還有效），"
                           "要重做請先把那一張的連結撤銷，不然收款人會拿到兩條連結")

        # 一個值兩邊共用。原本 DB 走 `_parse_day`（看不懂就退成今天）、快照走
        # `payout_share._day`（只切前 10 碼、不驗），於是非 ISO 的日期會讓
        # **他那頁印一個日期、我們的清單是另一個** —— 沒有 error，只有他看得到。
        paid = _paid_day(req.paid_date)
        snap = payout_share.build_snapshot(payees.pop(), paid, _payer_name(), rows)

        payout = CrmPayout(id=uuid.uuid4().hex, entity=ent,
                           payee_name=snap["payee_name"], total=snap["total"],
                           paid_date=datetime.fromisoformat(paid).replace(tzinfo=timezone.utc),
                           share_snapshot=snap, created_at=_now())
        # unique index 擋碰撞；撞到就換一個而不是噴 500（同發票）
        for _ in range(5):
            candidate = new_short_token()      # 預設 9 bytes，同發票那支
            dup = (await session.execute(
                select(CrmPayout.id).where(CrmPayout.share_token == candidate))).first()
            if not dup:
                payout.share_token = candidate
                break
        if not payout.share_token:
            raise HTTPException(status_code=500, detail="短碼產生失敗，請再試一次")
        session.add(payout)
        for r in rows:
            r.payout_id = payout.id
        await session.commit()
        token = payout.share_token

    from core.share_link import public_base, share_url
    from config import load_settings
    return {"id": payout.id, "code": token, "total": snap["total"],
            "url": share_url(f"/p/{token}", public_base(load_settings()))}


def _paid_day(v) -> str:
    """匯款日 → `YYYY-MM-DD`；空的當今天，看不懂就 422。

    **不要**加「看不懂就退成今天」的寬容：那是寫進收款人手上那頁的日期，
    悄悄換成別的數字比直接擋下來糟得多（同 `_shared._parse_day` 的取捨）。
    """
    s = str(v or "").strip()
    if not s:
        return _now().date().isoformat()
    try:
        return datetime.fromisoformat(s[:10]).date().isoformat()
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"匯款日期看不懂：{s}") from None


@router.delete("/payouts/{payout_id}/share", dependencies=[Depends(money_dep)])
async def revoke_payout_share(payout_id: str, request: Request):
    """撤銷：短碼清掉，已經寄出去的連結當場失效（同發票的重置語意）。

    請款列上的 `payout_id` **留著** —— 那是「這幾筆是同一次匯出去的」的事實，
    跟「那條連結還能不能打開」是兩件事。
    """
    from core.ledger import require_entity
    from db.models import CrmPayout
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        po = await session.get(CrmPayout, payout_id)
        if not po:
            raise HTTPException(status_code=404, detail="找不到這次匯款")
        # 拿**那一列的**帳本再問一次（同 invoice_files 的 revoke）。原本問的是
        # `require_entity(request, "")`＝呼叫者的預設那本，於是只有母帳權的人
        # 也撤得掉私帳那張 —— 收款人那條當場死掉，而他本來連那本帳都不該碰。
        require_entity(request, po.entity or "parent", level="full")
        po.share_token = None
        await session.commit()
    return {"status": "revoked"}


@public_router.get("/public/payout/{token}")
async def payout_share_page(token: str, request: Request):
    """`/p/{短碼}` 那頁要顯示的東西。**白名單投影**，規則正本 core/payout_share.py。

    掛 public_router ＝ NAS 對外容器也吃得到（master 關機他照樣打得開）。
    憑證就是「網址裡那串字與 DB 存的完全相同」—— 查不到一律 404，不要分辨
    「沒有這個碼」與「碼被撤銷了」（那等於告訴試碼的人哪一個曾經存在）。
    """
    await surface_gate(request)
    code = (token or "").strip()
    if not code:
        raise HTTPException(status_code=404, detail="連結無效")
    _require_db()
    factory = await _get_factory()
    from db.models import CrmPayout
    async with factory() as session:
        po = (await session.execute(
            select(CrmPayout).where(CrmPayout.share_token == code))).scalars().first()
    if not po:
        raise HTTPException(status_code=404, detail="連結無效或已失效")
    if not (po.share_snapshot or {}).get("items"):
        # 沒有定稿就沒有東西好顯示 —— 誠實回報，不要拿 DB 活值硬湊一頁
        raise HTTPException(status_code=503, detail="這個連結還沒有可顯示的內容，請與我們聯繫")
    return payout_share.share_view(po.share_snapshot)


@router.get("/payouts", dependencies=[Depends(money_dep)])
async def list_payouts(request: Request, payee: str = Query(""), limit: int = Query(20),
                       entity: str = Query("")):
    """某個收款人最近的匯款通知（面板上要顯示「這次已經產過連結了」）。

    `entity` 要收：應付面板可以 pin 到私帳（`payables_summary` 就收），不收的話
    對同時有兩本帳的人一律解析成母帳，私帳產過的通知**永遠列不出來** ——
    畫面看起來像「還沒產過」，於是再產一張，收款人收到兩條連結。
    """
    from core.ledger import require_entity
    from db.models import CrmPayout
    ent = require_entity(request, entity or "", level="full")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        stmt = select(CrmPayout).where(CrmPayout.entity == ent)
        if payee.strip():
            stmt = stmt.where(CrmPayout.payee_name == payee.strip())
        rows = (await session.execute(
            stmt.order_by(CrmPayout.created_at.desc()).limit(max(1, min(limit, 100))))).scalars().all()
    from core.share_link import public_base, share_url
    from config import load_settings
    base = public_base(load_settings())
    return {"payouts": [{
        "id": p.id, "payee_name": p.payee_name or "", "total": p.total or 0,
        "paid_date": _fmt_day(p.paid_date),
        "code": p.share_token or "",
        "url": share_url(f"/p/{p.share_token}", base) if p.share_token else "",
    } for p in rows]}
