# -*- coding: utf-8 -*-
"""routers/crm/taxonomy.py — 私帳的收支分類樹（cash_taxonomy_nodes 的 CRUD）。

2026-08-30 從 `finance.py` 切出來。那個檔 2,996 行 —— 超過 AI 單次讀取上限
（2,000 行），而且是這三週改動次數第一名（56 次）：**最常改的檔案正好是
一次讀不完的那個**，改起來等於盲改。

怎麼決定切在哪：不是照註解分隔線（那樣切會看到假的循環依賴），而是
① 函式呼叫圖的強連通分量 —— 實測**零循環**，所以拆得開；
② 每個 helper 歸給「用到它的端點群」，多群共用的留在 finance；
③ 迭代到不動點，任何造成反向邊的名字拉回 finance。
結果本檔**只 import finance，沒有反向依賴**。

URL 一條都沒變，route 一樣掛在 `_shared.router` 上。
🔴 檔內順序不可重排：`/{id}` 這種吃萬用字元的路徑必須排在具名路徑之後
（FastAPI 先註冊先贏）。
"""
from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, Query

from core.cash_taxonomy import SEP as _TAX_SEP
from core.ledger import (require_entity)
from core.schemas import (CashTaxonomyNodePayload, CashTaxonomyNodeUpdate)

from ._shared import (router, money_dep, _require_db,
                      _get_factory, _now)

# 這個檔案唯一用得到發票檔那邊的東西：改發票時要跟著改檔名

try:
    from ._shared import (select, func, CrmCashEntry)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

# ── CSV 匯入共用 ────────────────────────────────────────────


# 正本在 finance.py 的共用 helper（單向依賴：finance 不用本檔）
from .finance import (  # noqa: F401
    _entity_for_write)


# ── 收支分類樹 ────────────────────────────────────────────────────
# 樹的正本是 `cash_taxonomy_nodes`（種子見 db/seed_cash_taxonomy.py）。日常讀取
# 走 `/cash-entries/options` 的 `tree`；這裡是它的 CRUD —— POST 收「＋自訂…」
# 長出來的新節點，GET/PUT/DELETE 服務後台編輯器（finance 設定頁的分類樹卡）。
# 🔴 PUT 的改名／搬家是**資料遷移**：收支三欄鏡射與 finance_category_map 的鍵
# 會一起改（見 update_cash_taxonomy_node），不是改個標籤而已。

@router.post("/cash-taxonomy/nodes", dependencies=[Depends(money_dep)])
async def create_cash_taxonomy_node(req: CashTaxonomyNodePayload, request: Request):
    from db.models import CashTaxonomyNode

    ent = _entity_for_write(request, req.entity)
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="分類名稱必填")
    if _TAX_SEP in name:
        # 名稱含底線會把複合鍵切爛（`轉匯與定存_公司信用卡` 的教訓：只切第一個
        # 底線，所以第二層叫「公司_信用卡」時 split 出來就不是原來那個名字了）
        raise HTTPException(status_code=422, detail=f"分類名稱不能包含「{_TAX_SEP}」")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        parent_id = (req.parent_id or "").strip()
        depth = 1
        if parent_id:
            parent = await session.get(CashTaxonomyNode, parent_id)
            if parent is None or parent.entity != ent:
                raise HTTPException(status_code=404, detail="找不到上層分類")
            depth = (parent.depth or 1) + 1
        exist = (await session.execute(
            select(CashTaxonomyNode).where(
                CashTaxonomyNode.entity == ent,
                CashTaxonomyNode.parent_id == parent_id,
                CashTaxonomyNode.name == name))).scalar_one_or_none()
        if exist is not None:
            # 已經有了就回它 —— 兩個人同時打同一個名字不該噴錯，結果一樣就好。
            # 停用過的順手復活（他正想用它）。
            if not exist.active:
                exist.active = 1
                await session.commit()
            return {"status": "ok", "node": {"id": exist.id, "name": exist.name,
                                             "depth": exist.depth}, "existed": True}
        # sort 排在同層最後（種子給的順序是 owner 的 Sheet 順序，新的接在後面）
        last = (await session.execute(
            select(func.max(CashTaxonomyNode.sort)).where(
                CashTaxonomyNode.entity == ent,
                CashTaxonomyNode.parent_id == parent_id))).scalar()
        node = CashTaxonomyNode(id=uuid.uuid4().hex, entity=ent, parent_id=parent_id,
                                name=name, depth=depth, sort=int(last or 0) + 1, active=1)
        session.add(node)
        await session.commit()
        return {"status": "ok", "node": {"id": node.id, "name": node.name,
                                         "depth": node.depth}, "existed": False}


@router.get("/cash-taxonomy/nodes", dependencies=[Depends(money_dep)])
async def list_cash_taxonomy_nodes(request: Request, entity: str = Query("")):
    """整棵樹（**含停用**）＋每個節點的使用筆數 —— 後台編輯器用。

    `count` 是直接掛在它上面的、`subtree_count` 是整支的。改名／停用／刪除前
    要看得到「會動到幾筆」，這是危險操作唯一的剎車。
    """
    from core.cash_tree import load_tree

    ent = require_entity(request, entity, level="full")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        # active 隨 build_tree 的節點 dict 出來，不必再查一張表
        tree = await load_tree(session, ent, include_inactive=True)
        used = dict((await session.execute(
            select(CrmCashEntry.taxonomy_node_id, func.count())
            .where(CrmCashEntry.entity == ent,
                   CrmCashEntry.taxonomy_node_id.isnot(None))
            .group_by(CrmCashEntry.taxonomy_node_id))).all())

    def walk(nodes):
        total = 0
        for n in nodes:
            n["count"] = used.get(n["id"], 0)
            n["subtree_count"] = n["count"] + walk(n["children"])
            total += n["subtree_count"]
        return total

    walk(tree)
    return {"tree": tree}


async def _rename_category_map(session, renames, ent: str = "mine") -> list:
    """複合鍵改名 → `finance_category_map` 的鍵跟著改。

    🔴 不改的話那批帳**從三表消失**：那張表帶著每個鍵的科目與 treatment
    （實測 38 個私帳複合鍵都在，各自帶 transfer／direct_expense／direct_income）。
    新鍵已經存在就擋下來 —— 把兩個科目對映併成一個不是「改名」該做的事。
    """
    from db.models import FinanceCategoryMap

    done = []
    for old, new in renames:
        clash = (await session.execute(
            select(FinanceCategoryMap).where(
                FinanceCategoryMap.source == "cash",
                FinanceCategoryMap.entity == ent,
                FinanceCategoryMap.category_text == new))).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(
                status_code=409,
                detail=f"「{new}」已經有科目對映了 —— 改成這個名字會把兩個對映併在一起。"
                       f"請先到「科目與設定」處理那一筆，或換個名字")
        row = (await session.execute(
            select(FinanceCategoryMap).where(
                FinanceCategoryMap.source == "cash",
                FinanceCategoryMap.entity == ent,
                FinanceCategoryMap.category_text == old))).scalar_one_or_none()
        if row is not None:
            row.category_text = new
            done.append([old, new])
    return done


@router.put("/cash-taxonomy/nodes/{node_id}", dependencies=[Depends(money_dep)])
async def update_cash_taxonomy_node(node_id: str, req: CashTaxonomyNodeUpdate,
                                    request: Request):
    """改名／搬家／停用。

    改名與搬家是**資料遷移**，一次做完三件事：節點本身 → 掛在這一支底下的收支
    三欄鏡射 → finance_category_map 的鍵。少做任何一件，帳與分類就對不起來。
    """
    from core.cash_tree import (category_keys, load_nodes, remirror_subtree,
                                subtree_ids)
    from db.models import CashTaxonomyNode

    ent = _entity_for_write(request, req.entity)
    data = req.model_dump(exclude_unset=True, exclude={"entity"})
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        node = await session.get(CashTaxonomyNode, node_id)
        if node is None or node.entity != ent:
            raise HTTPException(status_code=404, detail="找不到這個分類節點")
        # 改前撈一次、flush 後撈一次 —— 各 helper 都吃 nodes=，別讓一個 PUT
        # 把節點表整張撈六遍
        nodes_before = await load_nodes(session, ent, include_inactive=True)
        new_name = (data.get("name") or node.name).strip()
        new_parent = data.get("parent_id", node.parent_id) or ""
        if "name" in data:
            if not new_name:
                raise HTTPException(status_code=422, detail="分類名稱必填")
            if _TAX_SEP in new_name:
                raise HTTPException(status_code=422,
                                    detail=f"分類名稱不能包含「{_TAX_SEP}」")
        if new_parent != node.parent_id:
            # 🔴 只准同層搬家。跨層搬會讓深度變，複合鍵跟著憑空出現或消失
            # （第三層搬到第二層＝多一個沒人對映的科目鍵，那批帳當場掉出三表）。
            old_parent = (await session.get(CashTaxonomyNode, node.parent_id)
                          if node.parent_id else None)
            tgt = await session.get(CashTaxonomyNode, new_parent) if new_parent else None
            if new_parent and (tgt is None or tgt.entity != ent):
                raise HTTPException(status_code=404, detail="找不到要搬去的上層分類")
            if (tgt.depth if tgt else 0) != (old_parent.depth if old_parent else 0):
                raise HTTPException(
                    status_code=422,
                    detail="只能搬到同一層的其他分類底下（跨層搬會讓科目對映對不上）")
            if new_parent in await subtree_ids(session, ent, node_id,
                                               nodes=nodes_before):
                raise HTTPException(status_code=422, detail="不能搬到自己底下")
        if new_name != node.name or new_parent != node.parent_id:
            dup = (await session.execute(
                select(CashTaxonomyNode.id).where(
                    CashTaxonomyNode.entity == ent,
                    CashTaxonomyNode.parent_id == new_parent,
                    CashTaxonomyNode.name == new_name,
                    CashTaxonomyNode.id != node_id))).scalar()
            if dup:
                raise HTTPException(status_code=409,
                                    detail=f"同一層底下已經有「{new_name}」了")

        ids = await subtree_ids(session, ent, node_id, nodes=nodes_before)
        before = await category_keys(session, ent, ids, nodes=nodes_before)
        node.name = new_name
        node.parent_id = new_parent
        if "active" in data:
            node.active = 1 if data["active"] else 0
        node.updated_at = _now()
        await session.flush()      # 🔴 先落地，下面重讀才看得到新名字
        nodes_after = await load_nodes(session, ent, include_inactive=True)
        after = await category_keys(session, ent, ids, nodes=nodes_after)
        renames = [(before[k], after[k]) for k in before
                   if k in after and before[k] != after[k]]
        mapped = await _rename_category_map(session, renames, ent)
        rows = await remirror_subtree(session, ent, node_id, nodes=nodes_after)
        await session.commit()
    return {"status": "ok", "rows_remirrored": rows, "category_map_renamed": mapped}


@router.delete("/cash-taxonomy/nodes/{node_id}", dependencies=[Depends(money_dep)])
async def delete_cash_taxonomy_node(node_id: str, request: Request,
                                    entity: str = Query("")):
    """只刪得掉**空的**節點（沒有子分類、整支沒有任何收支列）。

    有資料的請用「停用」—— 刪掉會讓那些列指向一個爬不回根的孤兒 id，而 category
    欄還留著舊值，兩邊從此對不起來。

    🔴 `finance_category_map` 那一筆**刻意不刪**：它是會計對映（科目＋treatment），
    歸「科目與設定」管；分類樹刪一個沒人用的節點不該連帶動到會計設定。
    """
    from core.cash_tree import subtree_ids
    from db.models import CashTaxonomyNode

    ent = _entity_for_write(request, entity or None)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        node = await session.get(CashTaxonomyNode, node_id)
        if node is None or node.entity != ent:
            raise HTTPException(status_code=404, detail="找不到這個分類節點")
        ids = await subtree_ids(session, ent, node_id)
        if len(ids) > 1:
            raise HTTPException(status_code=409,
                                detail=f"底下還有 {len(ids) - 1} 個分類，請先處理它們")
        used = (await session.execute(
            select(func.count()).select_from(CrmCashEntry)
            .where(CrmCashEntry.entity == ent,
                   CrmCashEntry.taxonomy_node_id == node_id))).scalar()
        if used:
            raise HTTPException(status_code=409,
                                detail=f"還有 {used} 筆收支掛在這個分類上 —— 請改用「停用」")
        await session.delete(node)
        await session.commit()
    return {"status": "ok"}
