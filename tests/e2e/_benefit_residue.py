# -*- coding: utf-8 -*-
"""福委會 e2e 的清理與殘留掃描（共用）。

🔴 為什麼要有這支：2026-08-21 我在生產跑 e2e，teardown 刪了登記、應付款、
池、臨時帳號 —— 就是**漏了收支明細**。「登記匯款」那一步會落一列帳，
於是生產的收支明細上留了三筆「福委會 ZZ證明池／ZZ證明甲」$300 的假帳，
是 owner 自己看到才發現的（而且它們指向的請款單早被刪掉，是孤兒列）。

逐支測試各寫一份 teardown 就是這樣漏的 —— 有人記得刪收支明細（ui_benefits）、
有人忘了（api_benefit_proof、ui_benefits_my）。所以：

  `purge(...)`  刪光某個前綴的測試資料，**七張表一次刪完**
  `scan()`      掃**任何** ZZ 殘留（前綴無關）—— 別支測試漏的也照樣抓得到

每支福委會 e2e 收尾都要 `check(not scan(), "測試資料清光", ...)`。
"""
import os

# 這些表都可能被福委會 e2e 碰到。順序＝刪除順序（子先於父）。
# 🔴 CrmCashEntry 在第一個 —— 它就是被漏掉的那張。
_TABLES = ("CrmCashEntry", "CrmPaymentRequest", "HrBenefitEntry",
           "HrBenefitAllowance", "HrBenefitFunding", "HrBenefitPool",
           "User", "CrmStaff")


async def _models():
    from db.session import init_db, get_session_factory
    from db import models
    await init_db()
    return get_session_factory(), models


async def purge(name_like: str, username_like: str = "zz\\_%") -> list:
    """刪掉符合前綴的測試資料。回傳「刪不掉的檔案」描述（磁碟上的收據／附件）。

    `name_like` 是 SQL LIKE（例如 "ZZ證明%"）。跟人名／池名有關的表用它，
    收支明細與請款單走**收款人／備註**比對（它們沒有池名欄）。
    """
    from sqlalchemy import or_, select
    factory, m = await _models()
    leftover = []
    async with factory() as s:
        pools = (await s.execute(select(m.HrBenefitPool).where(
            m.HrBenefitPool.name.like(name_like)))).scalars().all()
        for p in pools:
            for f in (p.attachments or []):
                _rm(f.get("path"), leftover)
            for e in (await s.execute(select(m.HrBenefitEntry).where(
                    m.HrBenefitEntry.pool_id == p.id))).scalars().all():
                _rm(e.receipt_url, leftover)
                # 🔴 收支明細與請款單都要跟著走 —— 只刪登記的話，
                #    帳上留下的是「指向不存在請款單」的孤兒列。
                for ce in (await s.execute(select(m.CrmCashEntry).where(
                        m.CrmCashEntry.payment_request_id
                        == (e.payment_request_id or "-")))).scalars().all():
                    await s.delete(ce)
                if e.payment_request_id:
                    ap = await s.get(m.CrmPaymentRequest, e.payment_request_id)
                    if ap is not None:
                        await s.delete(ap)
                await s.delete(e)
            for a in (await s.execute(select(m.HrBenefitAllowance).where(
                    m.HrBenefitAllowance.pool_id == p.id))).scalars().all():
                await s.delete(a)
            for f in (await s.execute(select(m.HrBenefitFunding).where(
                    m.HrBenefitFunding.pool_id == p.id))).scalars().all():
                await s.delete(f)
            await s.delete(p)
        # 就算池已經被刪掉，殘留的收支明細／請款單也要清（孤兒列的來源）
        for ce in (await s.execute(select(m.CrmCashEntry).where(or_(
                m.CrmCashEntry.payee.like(name_like),
                m.CrmCashEntry.note.like(name_like))))).scalars().all():
            await s.delete(ce)
        for ap in (await s.execute(select(m.CrmPaymentRequest).where(or_(
                m.CrmPaymentRequest.payee_name.like(name_like),
                m.CrmPaymentRequest.summary.like(name_like))))).scalars().all():
            await s.delete(ap)
        for u in (await s.execute(select(m.User).where(
                m.User.username.like(username_like, escape="\\")))).scalars().all():
            await s.delete(u)
        for st in (await s.execute(select(m.CrmStaff).where(
                m.CrmStaff.name.like(name_like)))).scalars().all():
            await s.delete(st)
        await s.commit()
    return leftover


def _rm(path, leftover):
    if not path:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as err:
        # NAS 的 UNC 路徑本機沒憑證 —— 刪不掉要**出聲**，不能靜默略過
        leftover.append(f"{path}（{err.__class__.__name__}）")


async def scan() -> list:
    """掃**任何** ZZ 殘留，回傳描述清單（空 ＝ 乾淨）。

    刻意跟前綴無關：別支測試漏掉的東西，這支也抓得到。
    """
    from sqlalchemy import or_, select
    factory, m = await _models()
    out = []

    def zz(col):
        return or_(col.like("%ZZ%"), col.like("zz\\_%", escape="\\"))

    async with factory() as s:
        checks = (
            ("收支明細", m.CrmCashEntry, lambda t: zz(t.payee), "summary"),
            ("收支明細", m.CrmCashEntry, lambda t: zz(t.note), "summary"),
            ("請款單", m.CrmPaymentRequest, lambda t: zz(t.payee_name), "summary"),
            ("請款單", m.CrmPaymentRequest, lambda t: zz(t.summary), "summary"),
            ("福利池", m.HrBenefitPool, lambda t: zz(t.name), "name"),
            ("登記", m.HrBenefitEntry, lambda t: zz(t.staff_name), "title"),
            ("額度", m.HrBenefitAllowance, lambda t: zz(t.staff_name), "staff_name"),
            ("人員", m.CrmStaff, lambda t: zz(t.name), "name"),
            ("帳號", m.User, lambda t: zz(t.username), "username"),
        )
        seen = set()
        for label, model, cond, show in checks:
            for r in (await s.execute(select(model).where(cond(model)))).scalars().all():
                key = (label, getattr(r, "id", None) or getattr(r, "username", ""))
                if key in seen:
                    continue
                seen.add(key)
                out.append(f"{label}：{getattr(r, show, '')}")
    return out
