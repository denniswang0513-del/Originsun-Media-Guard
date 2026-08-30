# -*- coding: utf-8 -*-
"""收支分類樹的讀取 —— `cash_taxonomy_nodes` 的唯一查詢入口。

形狀與「為什麼是樹」見 `db.models.CashTaxonomyNode`；路徑 ↔ 三欄鏡射的規則在
`core.cash_taxonomy.mirror_from_path`。本檔只負責「把表讀成樹」。

🔴 一次全撈：整棵樹幾百個節點（實測 2026-08-27 種子 ~237 個），遞迴查詢
（每層一次 round-trip）在這個量級純屬浪費，而且深度不限時次數還不可預測。
同一個請求要用到多個 helper 時，先 `load_nodes` 一次再用 `nodes=` 傳進去 ——
別讓一個 PUT 把節點表整張撈六遍（2026-08-28 /simplify 實測就是這樣）。
"""
from __future__ import annotations

ROOT = ""          # 根節點的 parent_id（不是 NULL — 見 CashTaxonomyNode）


async def load_nodes(session, entity: str, include_inactive: bool = False) -> list:
    """該帳本的所有節點，已依 (depth, sort, name) 排好。"""
    from sqlalchemy import select
    from db.models import CashTaxonomyNode

    q = select(CashTaxonomyNode).where(CashTaxonomyNode.entity == entity)
    if not include_inactive:
        q = q.where(CashTaxonomyNode.active == 1)
    q = q.order_by(CashTaxonomyNode.depth, CashTaxonomyNode.sort,
                   CashTaxonomyNode.name)
    return list((await session.execute(q)).scalars())


def index_paths(rows) -> dict:
    """`(id, parent_id, name)` 列 → `{id: [根, …, 自己]}`。**walk 只有這一份** ——
    ORM 端（本檔）與 asyncpg 端（scripts/_common.load_taxonomy_tree）共用；
    抄第二份的下場已經發生過一次：環保險只長在其中一份上。

    迴圈保險：搬家搬成環的話（後台編輯理論上做得到）不能讓這裡無限遞迴。
    """
    parent, name = {}, {}
    for nid, pid, nm in rows:
        parent[nid] = pid or ROOT
        name[nid] = nm
    out = {}

    def walk(nid, seen):
        if nid in out:
            return out[nid]
        pid = parent.get(nid, ROOT)
        prefix = walk(pid, seen | {nid}) if (pid and pid in name and pid not in seen) else []
        out[nid] = prefix + [name[nid]]
        return out[nid]

    for nid in name:
        walk(nid, set())
    return out


def build_tree(nodes) -> list:
    """扁平節點 → 巢狀 dict 清單（給前端逐層下拉用）。

    每個節點：`{id, name, depth, active, path, cat, children: [...]}`。`path` 是從
    根到自己的名稱陣列 —— 前端要顯示「家用 ▸ 變動支出 ▸ 醫療保健 ▸ 乳癌治療 ▸
    台北馬偕」時直接用它，不必自己往上爬。

    🔴 `cat` ＝這個節點鏡射出來的 category（`mirror_from_path`，規則正本在
    core.cash_taxonomy）。附在**建樹的這一支**而不是某個 router，是因為三個端點
    出的是同一個節點形狀（/cash-entries/options、/cash-taxonomy/nodes、對帳單
    匯入預覽），而前端只有一份快取在收它們 —— 只有其中一支帶 cat 的話，快取
    是哪一支最後寫的就決定欄位在不在，不在的那次會靜靜地把 category 存成空的。
    """
    from core.cash_taxonomy import mirror_from_path
    by_id, kids = {}, {}
    for n in nodes:
        by_id[n.id] = {"id": n.id, "name": n.name, "depth": n.depth,
                       "active": 1 if getattr(n, "active", 1) else 0,
                       "path": [], "children": []}
        kids.setdefault(n.parent_id or ROOT, []).append(n.id)

    def attach(parent_id, prefix):
        out = []
        for nid in kids.get(parent_id, []):
            node = by_id[nid]
            node["path"] = prefix + [node["name"]]
            node["cat"] = mirror_from_path(node["path"])[0]
            node["children"] = attach(nid, node["path"])
            out.append(node)
        return out

    # 孤兒（父節點被停用或刪掉）會整支消失 —— 刻意的：半截路徑顯示出來只會讓人
    # 選到一個爬不回根的節點，鏡射也算不出 category。
    return attach(ROOT, [])


async def load_tree(session, entity: str, include_inactive: bool = False) -> list:
    return build_tree(await load_nodes(session, entity, include_inactive))


def flatten(tree, out=None) -> list:
    """樹 → `{id, name, depth, path}` 的扁平清單（查表／驗證用，不含 children）。"""
    out = [] if out is None else out
    for n in tree:
        out.append({"id": n["id"], "name": n["name"],
                    "depth": n["depth"], "path": n["path"]})
        flatten(n["children"], out)
    return out


async def path_map(session, entity: str, nodes=None) -> dict:
    """`{node_id: [名稱, …]}` —— 列表要在每一列印出完整路徑，一次撈完不逐列查。

    含停用節點：歷史列掛在被停用的節點上，路徑照樣要顯示得出來。
    `nodes` 給了就用（🔴 必須是 include_inactive=True 撈的那份，理由同上一句）。
    """
    if nodes is None:
        nodes = await load_nodes(session, entity, include_inactive=True)
    return index_paths((n.id, n.parent_id, n.name) for n in nodes)


def node_id_in(paths: dict, path) -> str:
    """`find_node_id` 的記憶體版：在已經撈好的 `path_map` 裡反查。對不到回空字串。

    批次寫入（套用規則、批次分類、卡單匯入）一次跑幾百列，逐列打
    `find_node_id` 是逐層各一次 SELECT —— 撈一次表在記憶體裡對就好。
    """
    want = [str(x).strip() for x in (path or []) if str(x).strip()]
    if not want:
        return ""
    return next((nid for nid, p in (paths or {}).items() if p == want), "")


async def find_node_id(session, entity: str, path) -> str:
    """路徑（名稱陣列）→ 節點 id；對不到回空字串。舊路徑寫入時反查用。"""
    from sqlalchemy import select
    from db.models import CashTaxonomyNode

    pid = ROOT
    for name in [str(x).strip() for x in (path or []) if str(x).strip()]:
        row = (await session.execute(
            select(CashTaxonomyNode.id).where(
                CashTaxonomyNode.entity == entity,
                CashTaxonomyNode.parent_id == pid,
                CashTaxonomyNode.name == name))).scalar()
        if not row:
            return ""
        pid = row
    return pid if pid != ROOT else ""


async def subtree_ids(session, entity: str, node_id: str, nodes=None) -> list:
    """節點本身 ＋ 所有子孫的 id —— 篩選「選到哪一層就看那一支」用。"""
    if nodes is None:
        nodes = await load_nodes(session, entity, include_inactive=True)
    kids = {}
    for n in nodes:
        kids.setdefault(n.parent_id or ROOT, []).append(n.id)
    out, stack = [], [node_id]
    while stack:
        nid = stack.pop()
        out.append(nid)
        stack.extend(kids.get(nid, []))
    return out


async def remirror_subtree(session, entity: str, node_id: str, nodes=None) -> int:
    """節點改名／搬家之後，把掛在這一支底下的收支列的三欄鏡射重算。

    🔴 一定要在節點異動 **flush 之後**呼叫，`nodes` 也必須是 flush 之後撈的 ——
    拿到舊名字的話，三欄鏡射保持舊值、樹上卻是新名字，兩邊從此對不起來。
    """
    from sqlalchemy import select
    from core.cash_taxonomy import mirror_from_path
    from db.models import CrmCashEntry

    ids = await subtree_ids(session, entity, node_id, nodes=nodes)
    if not ids:
        return 0
    paths = await path_map(session, entity, nodes=nodes)
    rows = list((await session.execute(
        select(CrmCashEntry).where(CrmCashEntry.entity == entity,
                                   CrmCashEntry.taxonomy_node_id.in_(ids)))).scalars())
    # 🔴 拆項的三欄是**同一種鏡射**（cash_splits 寫入走同一份 _sync_taxonomy）——
    # 只重算 entries 的話，改名之後拆項的 category 對映到一個已不存在的類別，
    # 三表把那筆錢靜靜落進「未歸類」（2026-08-31 /simplify 層次審查抓到的）。
    from db.models import CrmCashSplit
    rows += list((await session.execute(
        select(CrmCashSplit)
        .join(CrmCashEntry, CrmCashEntry.id == CrmCashSplit.entry_id)
        .where(CrmCashEntry.entity == entity,
               CrmCashSplit.taxonomy_node_id.in_(ids)))).scalars())
    n = 0
    for e in rows:
        cat, item, sub = mirror_from_path(paths.get(e.taxonomy_node_id) or [])
        if (e.category or "", e.item or "", e.sub_item or "") != (cat, item, sub):
            e.category, e.item, e.sub_item = cat, item, sub
            n += 1
    return n


async def category_keys(session, entity: str, node_ids, nodes=None) -> dict:
    """`{node_id: 複合鍵}` —— **只有第一、二層**有複合鍵（第三層以後不進 category）。

    改名／搬家時要拿它比對前後，好把 `finance_category_map` 的鍵一起改掉：
    那張表帶著每個鍵的科目與 treatment，鍵改了而對映沒跟上，那批帳會從三表消失。
    """
    from core.cash_taxonomy import join_category

    paths = await path_map(session, entity, nodes=nodes)
    out = {}
    for nid in node_ids:
        p = paths.get(nid) or []
        if 1 <= len(p) <= 2:
            out[nid] = join_category(p[0], p[1] if len(p) > 1 else "")
    return out
