# -*- coding: utf-8 -*-
"""收支拆項 —— 「帳目一筆、內容拆裂」（owner 2026-08-31 定案，規劃 v3）。

母公司匯來的一筆錢（+350,436）同時裝著專案款、代墊回款、薪資。帳目上維持
一筆（收支明細一列、對帳工作台 1↔1 不動），拆裂放在 `crm_cash_splits`：
每個拆項各自帶分類樹節點與專案連結；代墊拆項另可逐筆連結原代墊流出列
（`crm_cash_split_advance_links`）做結清。

三條鐵則（散開就會出兩本帳那種「看起來對、其實錯」的帳）：
  🔴 Σ(拆項) ＝ 父列金額 —— 差一塊都不能存（`core.crm_logic.split_amount_error`
     是算式正本）。差額不自動補項：補出來的那項沒有人決定分類。
  🔴 父列有拆項後不再自帶分類與專案（正本在拆項）—— 分類鏡射走 cash.py 的
     `_sync_taxonomy` 同一份規則、專案已收走 `_sync_mine_project_received`
     同一條增量制，不另寫第二份。
  🔴 三表引擎在**載入層**展開（services/finance_statements）——引擎純函式
     不知道拆項存在。這個檔只管寫入與查詢，不碰報表。

🔴 每個拆項都**必須有分類**（節點或平面類別擇一）。「未分類」快篩與規則
自動分類都以「拆項父列＝已分好」為前提排除父列 —— 前提要在這個唯一寫入端
成立，不然沒分類的拆項錢會從每一個「找未分類」的工具眼前消失。
"""
from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Query, Request  # type: ignore
from sqlalchemy import delete as sa_delete
from sqlalchemy import exists, func as sa_func, select

from core.auth import check_logged_in
from core.crm_logic import (advance_open_amount, split_amount_error,
                            split_gross, split_side)
from core.ledger import require_entity
from core.project_link import cash_can_link
from db.models import (Client, CrmCashEntry, CrmCashSplit,
                       CrmCashSplitAdvanceLink, CrmProject)

from ._shared import (_assert_month_open, _get_factory, _now, _require_db,
                      money_dep, project_names_map, router)
from .cash import _sync_mine_project_received, _sync_taxonomy
from .finance import _mine_or_admin_write

# 代墊的類別鍵（私帳分類樹的第一層鏡射）。逐筆結清只對這個類別的流出列有意義。
ADVANCE_CATEGORY = "公司_代墊"


def _split_to_dict(s, tax_path=None, advances=None, project_name="") -> dict:
    # 🔴 `advances` 一定要出去：重編輯的 initial 就是這份 dict —— 少了它，
    # 使用者按一次「儲存拆項」代墊結清連結就整組被 replace 掉（靜默）。
    # 🔴 `project_name` 也是：編輯器的名稱查表只有**未收案**清單 —— 案子一
    # 結清就不在裡面，重開編輯器那列會顯示成一串 raw id，owner 看起來像
    # 「連結不完整」（和平行動者案，2026-09-01 實際發生）。
    return {
        "id": s.id, "amount": int(s.amount or 0),
        "fee": int(s.fee or 0),
        "category": s.category or "", "item": s.item or "",
        "sub_item": s.sub_item or "",
        "taxonomy_node_id": s.taxonomy_node_id or "",
        "taxonomy_path": list(tax_path or []),
        "project_id": s.project_id or "",
        "project_name": project_name or "",
        "note": s.note or "",
        "advances": list(advances or []),
    }


async def load_splits_map(session, entry_ids=None, *, entity=None) -> dict:
    """{entry_id: [CrmCashSplit…]}（照 sort）。

    `entity` 給了就整本帳一次撈（拆項表很小、有 entity 條件的 join 便宜）——
    清單頁 4,733 列時，`IN (4,733 個參數)` 的 SQL 本身就要序列化/解析 150KB，
    比掃整張小表貴（/simplify 效率審查）。`entry_ids` 用在單筆路徑。
    """
    q = select(CrmCashSplit).order_by(CrmCashSplit.sort, CrmCashSplit.id)
    if entity is not None:
        q = q.join(CrmCashEntry, CrmCashEntry.id == CrmCashSplit.entry_id) \
             .where(CrmCashEntry.entity == entity)
    else:
        ids = [i for i in (entry_ids or []) if i]
        if not ids:
            return {}
        q = q.where(CrmCashSplit.entry_id.in_(ids))
    out: dict = {}
    for s in (await session.execute(q)).scalars():
        out.setdefault(s.entry_id, []).append(s)
    return out


async def load_advance_links_map(session, split_ids) -> dict:
    """{split_id: [{"entry_id", "amount"}…]}（給 _split_to_dict 的 advances）。"""
    ids = [i for i in split_ids if i]
    if not ids:
        return {}
    out: dict = {}
    for lk in (await session.execute(
            select(CrmCashSplitAdvanceLink).where(
                CrmCashSplitAdvanceLink.split_id.in_(ids)))).scalars():
        out.setdefault(lk.split_id, []).append(
            {"entry_id": lk.advance_entry_id, "amount": int(lk.amount or 0)})
    return out


async def entry_has_splits(session, entry_id: str) -> bool:
    """單筆守衛用的存在性判斷（不把拆項列整組撈出來丟掉）。"""
    return bool((await session.execute(
        select(exists().where(CrmCashSplit.entry_id == entry_id)))).scalar())


def entry_has_splits_subq():
    """SQL 述詞用：`CrmCashEntry.id.in_(entry_has_splits_subq())`。"""
    return select(CrmCashSplit.entry_id)


def split_category_pred(*conds):
    """「拆項裡有符合 conds 的分類」→ 父列 id 子查詢。

    分類形狀的篩選（category/item/sub_item/book/節點）每一個都要問拆項 ——
    各自手寫 OR 的話，漏掉的那個篩選會讓拆過的錢人間蒸發（母公司平面篩選
    就是這樣漏掉的，/simplify 層次審查）。
    """
    return select(CrmCashSplit.entry_id).where(*conds)


async def _advance_linked_sums(session, advance_ids, *, exclude_split_ids=()) -> dict:
    """{代墊流出列 id: 已被拆項沖掉的合計}。replace 時排除自己舊拆項的連結。"""
    if not advance_ids:
        return {}
    q = (select(CrmCashSplitAdvanceLink.advance_entry_id,
                sa_func.sum(CrmCashSplitAdvanceLink.amount))
         .where(CrmCashSplitAdvanceLink.advance_entry_id.in_(list(advance_ids)))
         .group_by(CrmCashSplitAdvanceLink.advance_entry_id))
    if exclude_split_ids:
        q = q.where(CrmCashSplitAdvanceLink.split_id.notin_(list(exclude_split_ids)))
    return {aid: int(total or 0) for aid, total in (await session.execute(q)).all()}


def _project_deltas(side: str, parent_deposit, parent_project_id, splits) -> dict:
    """這組狀態對各專案「已收」的貢獻 {project_id: Σ毛額}（只算收入側）。

    父列自己掛的專案也算一份 —— 第一次拆項時父列的連結被拆項取代，
    差額制（新 − 舊）自然會把父列那份沖掉、拆項那份補上，帳永遠平。
    🔴 毛額＝amount＋fee：代開費是源日先扣走的專案款 —— 只記淨額的話
    未收額會永遠留一個費用大小的尾巴清不掉（這正是 fee 欄存在的理由）。
    """
    out: dict = {}
    if side != "deposit":
        return out
    if parent_project_id:
        out[parent_project_id] = out.get(parent_project_id, 0) + int(parent_deposit or 0)
    for s in splits:
        if s.project_id:
            # 毛額規則在 core.crm_logic.split_gross（專案按毛額結清）
            out[s.project_id] = out.get(s.project_id, 0) + split_gross(s.amount, s.fee)
    return out


async def _shift_project_received(session, ent, old_deltas: dict, new_deltas: dict):
    """已收增量 = 新 − 舊，逐案套用（規則正本 cash._sync_mine_project_received）。"""
    if ent != "mine":
        return
    for pid in set(old_deltas) | set(new_deltas):
        diff = new_deltas.get(pid, 0) - old_deltas.get(pid, 0)
        if diff:
            await _sync_mine_project_received(session, pid, diff)


async def _drop_splits(session, splits):
    """刪一組拆項＋它們的結清連結（唯一的 teardown —— 三個路徑共用）。"""
    ids = [s.id for s in splits]
    if not ids:
        return
    await session.execute(sa_delete(CrmCashSplitAdvanceLink).where(
        CrmCashSplitAdvanceLink.split_id.in_(ids)))
    await session.execute(sa_delete(CrmCashSplit).where(CrmCashSplit.id.in_(ids)))


async def remove_entry_splits(session, e):
    """解除一列的拆項並沖回專案已收（`_apply_splits(空)` 與刪列共用）。"""
    old = (await load_splits_map(session, [e.id])).get(e.id, [])
    if not old:
        return
    side = split_side(e.deposit, e.expense)
    await _shift_project_received(
        session, e.entity or "parent",
        _project_deltas(side, e.deposit, None, old), {})
    await _drop_splits(session, old)


async def _apply_splits(session, e, items: list, *, paths: dict,
                        fresh: bool = False, projects: dict = None) -> list:
    """拆項的唯一寫入端（單筆端點與對帳單匯入共用）。回寫入後的拆項列。

    `items`：core.schemas.CashSplitItem 的 list；空 list ＝ 解除拆項。
    `fresh=True`：呼叫端剛建出 e（對帳單匯入）—— 舊拆項必為空，省掉那趟查詢
    （一張 52 列的對帳單就是 52 趟保證空手而回的 SELECT）。
    呼叫端負責：權限（_mine_or_admin_write）、鎖月（_assert_month_open）、commit。
    """
    ent = e.entity or "parent"
    side = split_side(e.deposit, e.expense)
    old = [] if fresh else (await load_splits_map(session, [e.id])).get(e.id, [])
    old_deltas = _project_deltas(side, e.deposit, e.project_id, old)

    if not items:                        # ── 解除拆項（父列回到未分類）──
        await _shift_project_received(session, ent, old_deltas, {})
        await _drop_splits(session, old)
        return []

    if not side:
        raise HTTPException(status_code=409,
                            detail="這一列同時有收入與支出（或兩側皆空），不開放拆項")
    parent_amount = int((e.deposit if side == "deposit" else e.expense) or 0)
    err = split_amount_error(parent_amount, [it.amount for it in items])
    if err:
        raise HTTPException(status_code=422, detail=err)
    for it in items:
        if not (it.taxonomy_node_id or "").strip() and not (it.category or "").strip():
            raise HTTPException(
                status_code=422,
                detail="每個拆項都要有分類 —— 沒分類的拆項會從"
                       "「未分類」快篩與自動分類規則眼前消失（父列被視為已分好）")
        fee = int(it.fee or 0)
        if fee < 0:
            raise HTTPException(status_code=422, detail="代開費不能是負數")
        if fee and (side != "deposit" or not (it.project_id or "").strip()):
            raise HTTPException(
                status_code=422,
                detail="代開費只對「收入側、掛了專案」的拆項有意義 —— "
                       "它是源日代開發票先扣走的專案款（專案按毛額結清）")

    # ── 代墊連結驗證（一趟迭代收齊；先驗完才動資料，任何一條不合法整批不寫）──
    want: dict = {}
    for it in items:
        lk_sum = 0
        for lk in (it.advances or []):
            if int(lk.amount or 0) <= 0:
                raise HTTPException(status_code=422, detail="沖銷金額要大於 0")
            lk_sum += int(lk.amount)
            want[lk.entry_id] = want.get(lk.entry_id, 0) + int(lk.amount)
        if (it.advances or []) and lk_sum != int(it.amount or 0):
            raise HTTPException(
                status_code=422,
                detail=f"拆項 {int(it.amount or 0):,} 與其沖銷合計 {lk_sum:,} 不符")
    if want:
        if side != "deposit":
            raise HTTPException(status_code=409, detail="只有收入側的拆項能沖代墊")
        adv_rows = {r.id: r for r in (await session.execute(
            select(CrmCashEntry).where(
                CrmCashEntry.id.in_(list(want))))).scalars()}
        linked = await _advance_linked_sums(
            session, want, exclude_split_ids=[s.id for s in old])
        for aid, amt in want.items():
            r = adv_rows.get(aid)
            if not r or (r.entity or "parent") != ent or not int(r.expense or 0):
                raise HTTPException(status_code=404, detail="找不到要沖的代墊支出列")
            open_amt = advance_open_amount(r.expense, linked.get(aid, 0))
            if amt > open_amt:
                raise HTTPException(
                    status_code=409,
                    detail=f"「{(r.summary or '')[:20]}」只剩 {open_amt:,} 未回款，"
                           f"沖 {amt:,} 會超沖")

    # ── 專案一次撈齊（逐項 session.get 是 N 趟循序往返）──
    if projects is None:
        proj_ids = {it.project_id.strip() for it in items
                    if (it.project_id or "").strip()}
        projects = {p.id: p for p in (await session.execute(
            select(CrmProject).where(CrmProject.id.in_(list(proj_ids))))).scalars()} \
            if proj_ids else {}

    # ── 換新（replace-all：拆項沒有部分更新的語義，整組就是一個答案）──
    await _drop_splits(session, old)
    created = []
    for i, it in enumerate(items):
        s = CrmCashSplit(id=uuid.uuid4().hex[:32], entry_id=e.id,
                         amount=int(it.amount), fee=int(it.fee or 0) or None,
                         note=(it.note or "")[:255] or None, sort=i)
        s.entity = ent          # transient — _sync_taxonomy 的 fallback 會讀
        data = ({"taxonomy_node_id": it.taxonomy_node_id}
                if (it.taxonomy_node_id or "").strip()
                else {"category": it.category, "sub_item": it.sub_item})
        await _sync_taxonomy(session, s, data, paths)
        if (it.project_id or "").strip():
            if not cash_can_link(ent, s.category):
                raise HTTPException(
                    status_code=409,
                    detail=f"「{s.category or '未分類'}」的拆項不能掛專案")
            p = projects.get(it.project_id.strip())
            if not p or (p.entity or "parent") != ent:
                raise HTTPException(status_code=403,
                                    detail="不能把拆項掛到另一本帳的專案上")
            s.project_id = p.id
        session.add(s)
        created.append(s)
        for lk in (it.advances or []):
            session.add(CrmCashSplitAdvanceLink(
                id=uuid.uuid4().hex[:32], split_id=s.id,
                advance_entry_id=lk.entry_id, amount=int(lk.amount)))

    # ── 專案「已收」增量同步（差額制：父列的舊連結自然被沖掉）──
    new_deltas = _project_deltas(side, e.deposit, None, created)
    await _shift_project_received(session, ent, old_deltas, new_deltas)

    # ── 父列讓位：分類與專案的正本移到拆項（**唯一**動父列的地方）──
    e.taxonomy_node_id = None
    e.category = e.item = e.sub_item = ""
    e.project_id = None
    e.updated_at = _now()
    return created


@router.put("/cash-entries/{entry_id}/splits")
async def replace_cash_entry_splits(entry_id: str, request: Request):
    """整組取代這一列的拆項；`{"splits": []}` ＝ 解除拆項。"""
    from core.schemas import CashSplitsPayload
    check_logged_in(request)
    _require_db()
    payload = CashSplitsPayload(**(await request.json()))
    factory = await _get_factory()
    from core.cash_tree import path_map
    async with factory() as session:
        e = await session.get(CrmCashEntry, entry_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此收支紀錄")
        _mine_or_admin_write(request, e.entity)
        await _assert_month_open(session, e.entry_date, entity=e.entity or "parent")
        paths = await path_map(session, e.entity or "parent")
        created = await _apply_splits(session, e, payload.splits, paths=paths)
        adv = await load_advance_links_map(session, [s.id for s in created])
        pnames = await project_names_map(session, created)
        await session.commit()
        return {"status": "ok",
                "splits": [_split_to_dict(s, paths.get(s.taxonomy_node_id),
                                          adv.get(s.id),
                                          pnames.get(s.project_id, ""))
                           for s in created]}


@router.get("/cash-splits/outstanding", dependencies=[Depends(money_dep)])
async def cash_splits_outstanding(request: Request, entity: str = Query("")):
    """拆項編輯器的「連結私帳內容」選單（owner 定案：對起來的對象是私帳自己）。

    - projects：還在等錢的案（`amount_receivable > 0`，維護算式見
      core.ledger_project.receivable_fields —— 這裡只讀存好的欄）
    - advances：公司_代墊 流出列中，逐筆結清後仍有未回款餘額的
      （🔴 歷史回款沒有逐筆連結 —— 帳齡從啟用起算，總額以 1300 科目為準）
    - node id：兩個建議節點（公司_專案／公司_代墊 的樹節點），讓前端預填分類
    """
    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()
    from core.cash_taxonomy import path_from_columns
    from core.cash_tree import node_id_in, path_map
    async with factory() as session:
        projects = []
        if ent == "mine":
            # 客戶跟著帶（owner 2026-09-01「專案 要看得到客戶」）—— 同名的案
            # 每年一個（「節目置入影帶剪輯2025年」「…2026年」），光看案名選不出
            # 是哪一家的。JOIN 一次，不逐案回頭問。
            rows = (await session.execute(
                select(CrmProject, Client.short_name)
                .outerjoin(Client, Client.id == CrmProject.client_id)
                .where(CrmProject.entity == "mine",
                       CrmProject.amount_receivable > 0)
                .order_by(CrmProject.updated_at.desc().nullslast())
                .limit(80))).all()
            projects = [{"id": p.id, "name": p.name,
                         "client": cname or "",
                         "receivable": int(p.amount_receivable or 0)}
                        for p, cname in rows]

        # 未回款過濾放 SQL（HAVING）：已結清的列不佔 limit 窗 —— 在 Python 裡
        # 篩的話，結清越多、越舊的未回款越容易被擠出選單（/simplify 效率審查）
        linked_sq = (select(CrmCashSplitAdvanceLink.advance_entry_id.label("aid"),
                            sa_func.sum(CrmCashSplitAdvanceLink.amount).label("total"))
                     .group_by(CrmCashSplitAdvanceLink.advance_entry_id).subquery())
        adv_q = (await session.execute(
            select(CrmCashEntry, sa_func.coalesce(linked_sq.c.total, 0))
            .outerjoin(linked_sq, linked_sq.c.aid == CrmCashEntry.id)
            .where(CrmCashEntry.entity == ent,
                   CrmCashEntry.category == ADVANCE_CATEGORY,
                   CrmCashEntry.expense > 0,
                   CrmCashEntry.expense > sa_func.coalesce(linked_sq.c.total, 0))
            .order_by(CrmCashEntry.entry_date.desc())
            .limit(200))).all()
        advances = [{
            "entry_id": r.id,
            "date": r.entry_date.date().isoformat() if r.entry_date else "",
            "summary": r.summary or "", "amount": int(r.expense or 0),
            "open": advance_open_amount(r.expense, linked)}
            for r, linked in adv_q]

        # 建議節點：類別 → 路徑（path_from_columns 唯一那份規則）→ 反查節點。
        # 自己掃 mirror「取第一個」會綁到子節點（公司▸專案▸X 也鏡射同類別）。
        paths = await path_map(session, ent)
        def canon(cat):
            return node_id_in(paths, [x for x in path_from_columns(cat, "") if x]) or ""
    from core.ledger_project import DEFAULT_FEE_PCT
    return {"projects": projects, "advances": advances,
            # 代開費的標準費率（%）。勾「扣代開費」時，如果那一案的未收額推不出
            # 費用（案子已結清、或這一列是手動加的），就用它把實匯淨額還原成毛額
            # ——「預設帶出內容，但可以細調」（owner 2026-09-01）。費率正本在
            # core.ledger_project，前端不另外寫一個 8。
            "fee_pct": DEFAULT_FEE_PCT,
            "project_node_id": canon("公司_專案"),
            "advance_node_id": canon(ADVANCE_CATEGORY)}
