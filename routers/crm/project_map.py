"""routers/crm/project_map.py — 母私帳專案對應表：清單（/projects-mine-links）、私帳→母帳建立／連結、母帳→私帳建立。

2026-09-13 從 project_links.py 原樣切出（純搬移）。連結的寫入者 `_write_link`／`_sync_pair` 留在 project_links
（推送與收款方式也走它們），這裡 import 來用；分身那一列的寫法 `_new_mirror_row`、預覽 `_mirror_preview` 也是。
掃原始碼的測試用 `_srcscan.projects_src()`。
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException, Request

from core.ledger import not_mine

from ._shared import router, _require_db, _get_factory, _now, _fmt_day
from .project_links import (_crm_client_for, _me_staff_id_or_blank, _mirror_preview, _new_mirror_row,
                            _write_link)
from .projects import mine_link_map

try:
    from ._shared import select, Client, CrmProject
except ImportError:  # DB 套件不存在的 agent 環境 — 同 projects.py
    pass


# ── 母私帳專案對應表（owner 2026-09-05）──────────────────────────────────
#
# 目標：「母帳的專案私帳都可以對齊」。既有的 mirror-to-mine 是**母帳 → 私帳**
# （公司發包給我，把成本行鏡射成私帳收入）；這裡是**反過來的那半邊** ——
# 私帳先有的案，在母帳補一個對應的專案並連結。
#
# 連結欄位共用同一組（`crm_projects.mine_link_id` 記在母帳那一側、私帳那側的
# `source_project_id` 留第一個來源），所以兩條路連出來的東西一模一樣，
# `is_mirrored` / `resolve_mine_link` / `mine_parent_names` 都認得。


def _norm_name(s: str) -> str:
    """比對用的正規化案名（「2026 臺北城市形象片」vs「臺北城市形象片」）：正本 core.project_match.normalize
    （NFKC 折全形、去年份前綴與分隔符），跟請款單→專案的建議同一條規則。"""
    from core.project_match import normalize
    return normalize(s or "")


async def _mine_link_guard(request):
    """對應表三支端點的共同守衛：要有私帳的完整權限。

    看得到「私帳有哪些案」本身就是私帳資料（見 is_mirrored 的可見性註解），
    而這三支還會寫母帳 —— 所以用跟 mirror-to-mine 同一道門。
    """
    from core.ledger import require_entity
    require_entity(request, "mine", level="full")
    _require_db()
    return await _get_factory()


@router.get("/projects-mine-links")
async def projects_mine_links(request: Request):
    """對應表的資料：私帳每一案的連結狀態 ＋ 可挑的母帳案清單。

    `parent_names` 兩種連結形狀都收（見 mine_parent_names）；`suggest_id`
    只在**唯一**候選時給 —— 多個就不猜（同 clients 那張對照表的規矩）。
    """
    from ._shared import mine_parent_names
    factory = await _mine_link_guard(request)
    async with factory() as session:
        mine_rows = (await session.execute(
            select(CrmProject, Client.short_name)
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .where(CrmProject.entity == "mine")
            .order_by(CrmProject.completion_date.desc().nullsfirst(),
                      CrmProject.id))).all()
        parent_rows = (await session.execute(
            select(CrmProject, Client.short_name)
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .where(not_mine(CrmProject.entity))
            .order_by(CrmProject.name))).all()
        links = await mine_parent_names(session, [p.id for p, _c in mine_rows])
        mine_map = await mine_link_map(session, [p.id for p, _c in parent_rows])
        # 私帳案客戶在母帳的狀態只要 entity／crm_link_id 兩欄，不整表 hydrate
        cli = {cid: (ent, link) for cid, ent, link in (await session.execute(
            select(Client.id, Client.entity, Client.crm_link_id)
            .where(Client.id.in_({p.client_id for p, _c in mine_rows if p.client_id} or {""})))).all()}
    by_name: dict = {}
    for p, _c in parent_rows:
        if p.mine_link_id:
            continue            # 已連到別的私帳案的母帳案不當建議（按「採用」只會 409，而且每次重載都再冒出來）
        by_name.setdefault(_norm_name(p.name), []).append(p)
    # 母帳 → 私帳那個方向要畫的東西（owner 2026-09-05「增加一個切換鈕」）：
    # 連到哪一個私帳案、客戶、結案日、金額是不是佔位、以及同名建議。
    mine_by_id = {p.id: p for p, _c in mine_rows}
    by_mine_name: dict = {}
    for p, _c in mine_rows:
        by_mine_name.setdefault(_norm_name(p.name), []).append(p)
    parents = []
    for p, c in parent_rows:
        mid = mine_map.get(p.id, "")
        sug = [] if mid else by_mine_name.get(_norm_name(p.name), [])
        parents.append({
            "id": p.id, "name": p.name, "client": c or "",
            "contract": int(p.contract_amount or 0),
            "close_date": _fmt_day(p.completion_date),
            # 佔位金額（從私帳帶過來、還沒人確認）—— 畫面要標出來提醒回填
            "placeholder": (p.contract_amount_source or "") == "mine",
            "linked_mine_id": mid,
            "linked_mine_name": (mine_by_id[mid].name if mid in mine_by_id else ""),
            "suggest_id": sug[0].id if len(sug) == 1 else "",
            "suggest_name": sug[0].name if len(sug) == 1 else "",
        })
    mine = []
    for p, cname in mine_rows:
        names = links.get(p.id, [])
        sug = [] if names else by_name.get(_norm_name(p.name), [])
        c = cli.get(p.client_id) if p.client_id else None
        mine.append({
            "id": p.id, "name": p.name, "client": cname or "",
            "display_name": p.display_name or "",
            "contract": int(p.contract_amount or 0),
            "close_date": _fmt_day(p.completion_date),
            "parent_names": names,
            # 客戶在母帳的狀態：ok＝本來就是 CRM 客戶或已連結；none＝連不到
            # （「在母帳建立」會順手把客戶也建起來 —— owner「私帳的客戶母帳
            # 都有包含」，不然補建的專案會掛在一個母帳看不到的客戶上）
            "client_state": ("ok" if c is not None
                             and ((c[0] or "parent") != "mine" or c[1])       # (entity, crm_link_id)
                             else "none"),
            "suggest_id": sug[0].id if len(sug) == 1 else "",
            "suggest_name": sug[0].name if len(sug) == 1 else "",
        })
    return {"mine": mine, "parents": parents}


@router.post("/projects/{mine_id}/parent-create")
async def create_parent_from_mine(mine_id: str, request: Request):
    """私帳案 → 在母帳建一個對應的專案並連結（owner 2026-09-05）。

    帶過去的只有「這是哪一個案」需要的欄位：案名、客戶、結案日、案型；
    金額照 owner 拍板「母帳如果沒有 先填私帳的 後面再改」帶私帳的，並標
    `contract_amount_source='mine'`（＝佔位、待確認）。

    🔴 私帳金額是「我拿到的那段」，公司跟客戶的合約額一定 ≥ 它 —— 這個數字
    是**下限占位不是真值**，所以一定要留標記，母公司專案毛利與現金流預測
    才排除得掉（見 docs/LEDGER_UNIFY_PLAN.md §2.2）。

    成本行、派工、報價一律不帶：那些是公司自己的執行面，私帳沒有那份資料，
    憑空生出來的假數字比空的更難發現。
    """
    factory = await _mine_link_guard(request)
    async with factory() as session:
        m = await session.get(CrmProject, mine_id)
        if m is None:
            raise HTTPException(status_code=404, detail="找不到此私帳專案")
        if (m.entity or "parent") != "mine":
            raise HTTPException(status_code=422, detail="這一案不在私帳")
        already = (await session.execute(
            select(CrmProject.id)
            .where(CrmProject.mine_link_id == mine_id))).first()
        if already or m.source_project_id:
            raise HTTPException(status_code=409, detail="這一案已經連到母帳了")
        amt = int(m.contract_amount or 0)
        p = CrmProject(
            id=uuid.uuid4().hex, entity="parent", name=m.name,
            client_id=await _crm_client_for(session, m.client_id),
            contract_amount=amt or None,
            contract_amount_source="mine" if amt else None,
            completion_date=m.completion_date,
            project_type=m.project_type or "",
            status="結案" if m.completion_date else "製作",
            notes="[私帳對應] 由私帳案「%s」在母帳補建" % m.name,
            created_at=_now(), updated_at=_now())
        session.add(p)
        await _write_link(session, p, m)          # 連結唯一寫入者（兩側都在裡面寫；同 create_mine_from_parent）
        await session.commit()
        pid, pname = p.id, p.name
    return {"status": "ok", "id": pid, "name": pname}


@router.put("/projects/{mine_id}/parent-link")
async def set_parent_link(mine_id: str, request: Request):
    """把一個**既有的**母帳案連到這個私帳案，或解除（body: {parent_id: id|null}）。

    連結一律寫在母帳那一側（`mine_link_id`）—— 一個私帳案可以承接多個母帳案
    （owner 2026-09-01），所以連新的不會動到已經連上來的別案。
    解除：清掉**所有**指向這個私帳案的母帳連結；私帳那側的 `source_project_id`
    一起清（不然「這是分身」的舊判定會留著指向一個已解除的連結）。
    """
    factory = await _mine_link_guard(request)
    body = await request.json()
    target = (body.get("parent_id") or "").strip()
    async with factory() as session:
        m = await session.get(CrmProject, mine_id)
        if m is None or (m.entity or "parent") != "mine":
            raise HTTPException(status_code=404, detail="找不到此私帳專案")
        if target:
            p = await session.get(CrmProject, target)
            if p is None or (p.entity or "parent") == "mine":
                raise HTTPException(status_code=422, detail="要連結的目標必須是母帳專案")
            await _write_link(session, p, m)
        else:
            for p in (await session.execute(
                    select(CrmProject)
                    .where(CrmProject.mine_link_id == mine_id))).scalars().all():
                await _write_link(session, p, None)
            m.source_project_id = None
        m.updated_at = _now()
        await session.commit()
    return {"status": "ok", "parent_id": target}


@router.put("/projects/{parent_id}/mine-link")
async def set_mine_link(parent_id: str, request: Request):
    """對應表**母帳 → 私帳**那個方向：這個母帳案連到哪一個私帳案
    （body: {mine_id: id|null}；null＝解除**這一案**的連結）。

    跟 `parent-link` 是同一件事的兩個入口，寫入都走 `_write_link` ——
    差別只在解除的範圍：這支解**這一個**母帳案，那支解「所有指向該私帳案的」。
    兩張表方向不同，能解的粒度本來就不同（一個私帳案可能連著好幾個母帳案）。
    """
    factory = await _mine_link_guard(request)
    body = await request.json()
    target = (body.get("mine_id") or "").strip()
    async with factory() as session:
        p = await session.get(CrmProject, parent_id)
        if p is None or (p.entity or "parent") == "mine":
            raise HTTPException(status_code=404, detail="找不到此母帳專案")
        m = None
        if target:
            m = await session.get(CrmProject, target)
            if m is None or (m.entity or "parent") != "mine":
                raise HTTPException(status_code=422, detail="要連結的目標必須是私帳專案")
        await _write_link(session, p, m)
        if m is None and p.id:
            # 私帳那側的舊形狀只裝得下第一個來源 —— 解掉的正好是它時要清掉，
            # 不然「這是分身」的舊判定會留著指向一個已解除的連結
            for x in (await session.execute(
                    select(CrmProject)
                    .where(CrmProject.source_project_id == parent_id))).scalars().all():
                x.source_project_id = None
                x.updated_at = _now()
        await session.commit()
    return {"status": "ok", "mine_id": target}


@router.post("/projects/{parent_id}/mine-create")
async def create_mine_from_parent(parent_id: str, request: Request):
    """母帳案 → 在私帳建一個對應的案並連結（對應表反方向的「建立」）。

    跟專案頁的「推送到私帳」（`mirror-to-mine`，不帶 target）是**同一件事**：
    金額＝掛給我的成本行加總（`mirror_lines`），一筆都沒有就開 0；預覽與寫入
    共用 `_mirror_preview`／`_new_mirror_row`，這裡只是對應表那頁的入口。
    """
    factory = await _mine_link_guard(request)
    sid = await _me_staff_id_or_blank(request)
    async with factory() as session:
        p, mir, linked = await _mirror_preview(session, parent_id, sid)
        if linked is not None:      # 兩種連結形狀都認（舊形狀只看 mine_link_id 會再建一個分身）
            raise HTTPException(status_code=409, detail="這一案已經連到私帳了")
        m = _new_mirror_row(p, mir, "[母帳對應] 由母帳案「%s」在私帳補建" % p.name)
        session.add(m)
        await _write_link(session, p, m)
        await session.commit()
        new_id = m.id
        from core.ledger import invalidate_mine_projects
        invalidate_mine_projects()
    return {"status": "ok", "id": new_id, "amount": mir["total"],
            "staff_bound": bool(sid)}
