"""routers/crm/project_links.py — 母帳 ↔ 私帳：換帳本、推送（分身）、對應表。

2026-09-12 從 projects.py 原樣切出（純搬移；那檔 1,979 行離單次讀取上限 21 行）。
路由仍註冊在 _shared.router 上，URL 不變；由 routers/crm/__init__ 在 projects 之後 import。
連結的判定（is_mirrored／resolve_mine_link／mine_link_map）住在 projects.py，這裡 import 來用；
projects.update_project 要用這裡的 _sync_pair，走函式內 lazy import（兩檔互相依賴）。
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException, Request
from sqlalchemy import func as _sa_func

from core.finance_logic import MINE_LINK_INVOICE_CATEGORY
from core.ledger import not_mine
from core.schemas import ProjectLedgerMovePayload, ProjectMirrorPayload

from ._shared import (router, _require_db, _get_factory, _now, _fmt_day,
                      _auto_update_client_status)
from .projects import _apply_status_side_effects, mine_link_map, resolve_mine_link

try:
    from ._shared import (select, or_,
                          Client, CrmCashEntry, CrmInvoice, CrmPaymentRequest,
                          CrmProject, CrmProjectCostLine)
except ImportError:  # DB 套件不存在的 agent 環境 — 同 projects.py
    pass


# ── 搬帳本（專案管理 ↔ 私帳）─────────────────────────────────────
# owner 2026-08-28：「可以有一個按鈕把專案推送至私帳（只有擁有私帳權限的人能用）」。
#
# 🔴 這是全 repo「更新一律不得換帳本」（器材／福委會／客戶／收支／請款都擋）的
# **唯一例外**，所以給它專屬端點與專屬守衛，而不是放行 PUT /projects/{id} 的
# entity 欄 —— 那條路一開，任何一次普通更新都可能把案子的錢流歸屬帶走。
#
# 擋的只有**自己帶 entity 的那三張**：搬過去它們會留在原本那本帳，
# `_assert_project_same_entity`（收支只能掛同一本帳的專案）當場破功，
# 母公司三表也會少掉那幾筆。
#
# 🔴 **專案帳目（雜支／成本行）刻意不擋**（owner 2026-08-28「這個還是要可以推啊，
# 我已經做相對應的空間了」）：那兩張沒有 entity 欄，本來就跟著專案走，而私帳的
# 逐案損益已經有「行政雜支／人員費用」兩欄收納它們（core.ledger_project.CRM_BACKED）。
# 已經跟公司請過款的那些不會重複計算 —— `_crm_costs` 只算 claim_id 為空的。
# 早一版把雜支也列進來，結果是「有帳目的案子永遠推不動」，正好擋掉他要推的那些。
_LEDGER_BLOCKERS = (
    (CrmCashEntry, "收支明細"),
    (CrmInvoice, "發票"),
    (CrmPaymentRequest, "請款"),
)


def _blocker_filter(M):
    """這張表上「掛在本案、而且搬過去會壞」的列。

    owner 2026-09-12（「有時候走發票代開」）：我的案客戶要發票時，公司幫我開一張
    **內部代開**發票掛到**私帳案**上（`MINE_LINK_INVOICE_CATEGORY`，本來就准），
    客戶匯進公司後自動生一張應付給我的請款單（`source_invoice_id`）。這兩種掛在
    私帳案上是正常形狀（生產已有），所以不擋 —— 擋的是「專案」類發票（公司自己
    開給客戶的）、手動請款、以及所有收支（那是公司帳戶的錢，兩本帳的帳戶與分類樹
    都不同，不能自動搬）。
    """
    if M is CrmInvoice:
        return or_(CrmInvoice.category.is_(None),
                   CrmInvoice.category != MINE_LINK_INVOICE_CATEGORY)
    if M is CrmPaymentRequest:
        return CrmPaymentRequest.source_invoice_id.is_(None)
    return None


async def _ledger_blockers(session, project_id: str) -> list:
    """這個專案身上掛了哪些會擋換帳本的錢。回 [(中文名, 筆數), …]，空＝可以搬。"""
    from .flow import _count_sq

    def _cond(M):
        base = M.project_id == project_id
        extra = _blocker_filter(M)
        return base if extra is None else (base & extra)

    row = (await session.execute(select(*[
        _count_sq(M, _cond(M)).label(f"c{i}")
        for i, (M, _label) in enumerate(_LEDGER_BLOCKERS)]))).one()
    return [(label, int(n)) for n, (_M, label) in zip(row, _LEDGER_BLOCKERS) if n]


async def _has_passthrough_invoice(session, project_id: str) -> bool:
    """本案身上有沒有「內部代開」發票 —— 有的話搬到私帳時案源預設＝代開發票。"""
    return bool((await session.execute(
        select(CrmInvoice.id)
        .where(CrmInvoice.project_id == project_id,
               CrmInvoice.category == MINE_LINK_INVOICE_CATEGORY)
        .limit(1))).first())


async def _is_passthrough(session, p) -> bool:
    """這一案走不走代開：收款方式＝後期代開（owner 2026-09-13 的欄位），或身上已有內部代開發票。"""
    from core.ledger_project import billing_mode_of
    return billing_mode_of(p.billing_mode) == "passthrough" or await _has_passthrough_invoice(session, p.id)


async def _passthrough_invoice_count(session, project_id: str) -> int:
    return int((await session.execute(
        select(_sa_func.count()).select_from(CrmInvoice)
        .where(CrmInvoice.project_id == project_id,
               CrmInvoice.category == MINE_LINK_INVOICE_CATEGORY))).scalar() or 0)


def _today_tw() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()


@router.get("/projects/{project_id}/ledger-move-check")
async def check_project_ledger_move(project_id: str, request: Request):
    """能不能搬、搬過去會變怎樣 —— 按鈕按下去之前先問這支。

    把「會擋住的東西」講出來，而不是讓使用者按了才吃一個 409。
    """
    from core.ledger import require_entity
    from core.ledger_project import SELECTABLE_SOURCES, norm_detail

    require_entity(request, "mine", level="full")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        p = await session.get(CrmProject, project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="找不到此專案")
        blockers = await _ledger_blockers(session, project_id)
        cur, name = p.entity or "parent", p.name
        # 案源只在「搬到私帳」那個方向有意義；搬回公司帳不必多查一次發票
        passthrough = cur != "mine" and await _is_passthrough(session, p)
        cur_source = norm_detail(p.ledger_detail).get("source") or ""
    return {"entity": cur, "target": "parent" if cur == "mine" else "mine",
            "can_move": not blockers,
            "blockers": [{"what": w, "count": n} for w, n in blockers],
            # 擋人的那句話只寫一次 —— 前端直接顯示它，不再自己拼一份
            "reason": _blocked_reason(name, blockers),
            "name": name,
            # 搬到私帳要問案源：身上有內部代開發票就預設「代開發票」
            "has_passthrough_invoice": passthrough,
            "source_options": list(SELECTABLE_SOURCES),
            "source_default": cur_source or ("代開發票" if passthrough else "源日")}


def _blocked_reason(name: str, blockers) -> str:
    """為什麼不能搬。check 端點與 POST 的 409 共用同一句。"""
    if not blockers:
        return ""
    what = "、".join(f"{w} {n} 筆" for w, n in blockers)
    return (f"「{name}」身上已經記了錢（{what}），不能換帳本 —— "
            f"錢會留在原本那本，帳目就對不起來了。"
            "如果錢確實經過公司帳戶，這是公司的案：該用「推送」在私帳開分身，不是換帳本；"
            "如果是代開，先到發票把類別改成「內部代開」再搬")


@router.post("/projects/{project_id}/move-ledger")
async def move_project_ledger(project_id: str, req: ProjectLedgerMovePayload,
                              request: Request):
    """把專案搬到另一本帳。**只有私帳 full scope 的帳號能按**（兩個方向都是）。

    搬到私帳時順手把 `crm_pushed` 設成 1 —— 否則專案管理的預設檢視
    （entity=parent）當場看不到它，使用者會以為案子不見了。搬回母公司時清掉。

    搬到私帳還要把私帳需要的欄位補齊（owner 2026-09-12「記錯帳本」那條路）：
    案源（代開發票→代辦費自動算）、`ledger_detail` 正規化、🔴 `resync_receivable`
    —— 漏了它，那一案會安靜地不進私帳應收帳款（同 2026-08-26 那 349 案 NULL 的病）。
    """
    from core.ledger import ENTITIES, require_entity
    from core.ledger_project import (SELECTABLE_SOURCES, apply_source_fee,
                                     norm_detail)
    from routers.api_finance_projects import resync_receivable

    require_entity(request, "mine", level="full")
    target = (req.entity or "").strip()
    if target not in ENTITIES:
        raise HTTPException(status_code=422, detail=f"未知的帳本: {target or '(空)'}")
    source = (req.source or "").strip()
    if source and source not in SELECTABLE_SOURCES:
        raise HTTPException(status_code=422, detail=f"未知的案源：{source}")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        p = await session.get(CrmProject, project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="找不到此專案")
        cur = p.entity or "parent"
        if cur == target:
            raise HTTPException(status_code=409, detail=f"這個專案本來就在「{target}」")
        blockers = await _ledger_blockers(session, project_id)
        if blockers:
            raise HTTPException(status_code=409,
                                detail=_blocked_reason(p.name, blockers))
        if target == "mine" and await resolve_mine_link(session, p) is not None:
            raise HTTPException(status_code=409,
                                detail="這一案已經在私帳有分身了（「推送」連的那一案）—— "
                                       "整案搬過去會變成兩個案。先解除連結，或直接用那個分身")
        if target == "parent":
            # 反方向同一道牆：連著母帳案的私帳分身搬回公司帳，母帳那側的 mine_link_id 會指著
            # 一個已經在母帳的案，「重新同步」接著把鏡射的工項寫進它的合約額（兩種形狀都認）
            from ._shared import mine_parent_links
            _pl = (await mine_parent_links(session, [project_id])).get(project_id) or []
            if _pl:
                raise HTTPException(status_code=409,
                                    detail="這一案連著母帳的「%s」（是它的分身）—— 整案搬回公司帳會變成"
                                           "兩個母帳案互相連結。先到專案對應表解除連結" % "、".join(n for _i, n in _pl))
        p.entity = target
        # 搬到私帳的案子預設仍要在專案管理看得到（標「後期專案」）
        pushed = 1 if target == "mine" else 0
        p.crm_pushed = pushed
        if target == "mine":
            d = norm_detail(p.ledger_detail)
            d["source"] = source or ("代開發票" if await _is_passthrough(session, p) else "源日")
            d = norm_detail(apply_source_fee(int(p.contract_amount or 0), d))
            p.ledger_detail = d
            resync_receivable(p, d)
        else:
            p.contract_amount_source = None
        p.updated_at = _now()
        await session.commit()
        # 🔴 is_mine_project 有 60 秒 id-set 快取，而這裡是全 repo 唯一的 entity
        # 寫入路徑 —— 不清掉的話，剛搬過去的案子最多一分鐘內還被當成母公司的。
        from core.ledger import invalidate_mine_projects
        invalidate_mine_projects()
    return {"status": "ok", "entity": target, "crm_pushed": pushed}


# ── 連結私帳（母公司專案 → 私帳的收入分身）──────────────────────
# owner 2026-08-29：「費用要給王士源的，直接在私帳建立專案、同步收入」。
#
# 🔴 跟上面的「搬帳本」是**兩件事**，別把它們併成一顆按鈕：
#   搬帳本 = 整案換本（一案只在一本，母公司不再認它）
#   連結   = 分身（母公司留著跟客戶的合約；私帳多一案，收入＝公司要付我的錢）
# 兩本帳各記各的，不是重複計帳：公司那邊是成本、我這邊是收入。
#
# 金額來源＝人員配置的成本行裡掛給**我**的那些（規則正本 core.ledger_project
# .mirror_lines）。「我」＝登入帳號綁的 crm_staff（User.staff_id），不寫死人名。
#: 連結時，要連結的私帳案**已經填過工項**的三種處理方式（owner 2026-08-30
#: 「跳出幾個選擇讓我決定要怎麼做」）。前端的三顆按鈕與這裡是同一組值。
MIRROR_MODES = ("overwrite", "add", "keep", "import")


async def _import_split_to_cost_lines(session, parent, split: dict,
                                      staff_id: str) -> int:
    """私帳的工項 → 母公司的 CRM 成本行（掛給我）。回寫了幾行。

    「從私帳匯入」那條路：私帳是正本（那些數字是他照實際請款填的），公司這邊
    反而還沒把成本行建起來。匯入之後那些成本行在 CRM 照常可以編
    （owner：「但是其他工項 crm 也可以編輯」）—— 它們就是一般的成本行，
    沒有任何鎖。

    🔴 **只碰掛給我自己的那幾行**：同工項名且 `actual_staff_id` 是我 → 更新
    金額；沒有 → 新增一行。掛給別人的同名行一律不動 —— 那是他們的錢，
    「導演」那種工項本來就可能同時有兩個人。
    """
    from db.models import CrmProjectCostLine

    if not split:
        return 0
    rows = (await session.execute(
        select(CrmProjectCostLine)
        .where(CrmProjectCostLine.project_id == parent.id))).scalars().all()
    mine = {(r.item_name or ""): r for r in rows
            if (r.actual_staff_id or "") == staff_id}
    # 子表：走 costs.py 既有的挑選規則（首張＝主表，完全沒有子表時自我修復
    # 建一張）—— 在這裡自己寫 `order_by(sort_order).limit(1)` 就是第三份，
    # 而且少了那個自我修復（「建案時一定會有主表」是假設不是保證）。
    from .costs import _resolve_target_group
    gid = await _resolve_target_group(session, parent.id, None)
    nxt = max([int(r.sort_order or 0) for r in rows], default=0)
    now = _now()
    n = 0
    for item, amount in split.items():
        amt = int(amount or 0)
        if not amt:
            continue
        row = mine.get(item)
        if row is not None:
            if int(row.actual_amount or 0) == amt:
                continue                       # 已經一樣了，不動（也不算一行）
            row.actual_amount = amt
            row.updated_at = now
        else:
            nxt += 1
            session.add(CrmProjectCostLine(
                id=uuid.uuid4().hex, project_id=parent.id, cost_group_id=gid,
                phase="後期製作", item_name=item, sort_order=nxt,
                actual_amount=amt, actual_staff_id=staff_id,
                created_at=now, updated_at=now))
        n += 1
    return n


async def _mirror_preview(session, project_id: str, staff_id: str) -> tuple:
    """這一案能鏡射出什麼 → `(母公司專案, mirror_lines 結果, 已連結的私帳案|None)`。
    預覽與寫入共用，兩邊各算一次就會漂。"""
    from core.ledger_project import mirror_lines

    p = await session.get(CrmProject, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="找不到此專案")
    if (p.entity or "parent") == "mine":
        raise HTTPException(status_code=409,
                            detail="這一案本來就在私帳 —— 連結是給母公司專案用的")
    lines = (await session.execute(
        select(CrmProjectCostLine)
        .where(CrmProjectCostLine.project_id == project_id)
        .order_by(CrmProjectCostLine.sort_order))).scalars().all()
    # 沒綁人員檔案（staff_id 空）→ 認不出哪幾行是我的，一律 0；不能拿空字串去比
    #（actual_staff_id 是空字串的行會被當成「我的」）
    mir = (mirror_lines(lines, staff_id) if staff_id
           else {"lines": [], "split": {}, "total": 0})
    return p, mir, await resolve_mine_link(session, p)


async def _me_staff_id_or_blank(request) -> str:
    """登入帳號綁的 crm_staff；沒綁回空字串**不擋**。

    走 `core.identity.resolve_current_staff`（全 repo 唯一的「我是誰」解析器，
    staff_id 從 DB 現查不是從 JWT —— admin 重綁後免重新登入）。
    不擋是因為推送（分身）的語意是「這個案私帳還沒記，補一列」：認不出哪幾行是我的
    就開 0，由 owner 自己填；回應帶 `staff_bound` 讓畫面說得出為什麼是 0。
    （2026-09-12 前有一支 422 的嚴格版，三個呼叫端全改走這支後只剩它自己的 wrapper
    在 catch 它，收掉。）
    """
    from core.identity import resolve_current_staff

    return ((await resolve_current_staff(request)).get("staff_id") or "").strip()


@router.get("/projects/{project_id}/mirror-check")
async def check_project_mirror(project_id: str, request: Request):
    """按鈕按下去之前的預覽：哪幾行、多少錢、連結了沒、私帳落後了沒、可選的既有私帳案。"""
    from core.ledger import require_entity
    from core.ledger_project import mirror_stale, norm_detail

    require_entity(request, "mine", level="full")
    sid = await _me_staff_id_or_blank(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        p, mir, linked = await _mirror_preview(session, project_id, sid)
        warning = _mirror_blocked_reason(mir["total"])
        # 代開的收入是母帳合約額不是成本行，0 本來就是常態 —— 這句「沒有掛給你的成本行」對它是噪音
        # （crmFetch 會把任何 warning 直接 toast，連結後每次重畫詳情都會跳一次）
        from core.ledger_project import billing_mode_of
        if billing_mode_of(p.billing_mode) == "passthrough" or (
                linked is not None and norm_detail(linked.ledger_detail).get("source") == "代開發票"):
            warning = ""
        client = await session.get(Client, p.client_id) if p.client_id else None
        # 可連結的既有私帳案。🔴 **已經被連結過的照樣列出來**（owner 2026-09-01
        # 「可以多筆專案連結到一筆私帳」）—— 原本排除它們，於是第二個 CRM 案
        # 永遠選不到同一個私帳案。改成把「已承接幾案」帶給前端去標示。
        # **已經連結**的不撈：那條路走的是「重新同步」，候選清單一個都不會用到
        #（crm-projects-core.js 的 relink 分支不插 opts、也不畫衝突表）—— 每按一次
        # 白撈 400 案帶 JSONB。
        options, linked_counts, stale, delta = [], {}, None, 0
        if linked is None:
            options = (await session.execute(
                select(CrmProject.id, CrmProject.name, CrmProject.contract_amount,
                       CrmProject.ledger_detail)
                .where(CrmProject.entity == "mine")
                .order_by(CrmProject.created_at.desc()).limit(400))).all()
            # 每個候選已經承接了幾個 CRM 案（一次 group by，不逐案問）
            linked_counts = dict((await session.execute(
                select(CrmProject.mine_link_id, _sa_func.count())
                .where(CrmProject.mine_link_id.isnot(None))
                .group_by(CrmProject.mine_link_id))).all())
        elif sid:
            # 私帳落後了沒：比「上次同步的合計」（core.ledger_project.mirror_stale）。
            # 一個私帳案承接多個母帳案時那個合計不屬於任何一案 → 判不出來（None）。
            # 沒綁人員檔案時 mir["total"] 是「認不出來」不是 0 —— 拿去比會把每一個
            # 已同步的案都標成「私帳落後 −全部」，所以一樣判不出來（None）
            from ._shared import mine_parent_names
            shared = len((await mine_parent_names(session, [linked.id])).get(linked.id) or []) > 1
            if not shared:
                stale, delta = mirror_stale(linked.ledger_detail, mir["total"])
    return {
        "name": p.name,
        "client": client.short_name if client else "",
        "lines": mir["lines"], "total": mir["total"],
        "staff_bound": bool(sid),
        # 已經連結到哪一案（None＝還沒連）。有值時前端走「重新同步」：鎖定這一案、
        # 不給「建立新專案」—— 那會多出第二個分身，同一筆錢在私帳算兩次。
        # 帶著它現有的工項，才畫得出「私帳現有 vs CRM 現在算出來的」對照。
        "linked": None if linked is None else {
            "id": linked.id, "name": linked.name,
            "amount": int(linked.contract_amount or 0),
            "split": (norm_detail(linked.ledger_detail).get("split") or {})},
        # True＝母帳成本行改了（delta＝現在 − 上次同步）、False＝一致、None＝判不出來
        "stale": stale, "delta": delta,
        # 工項合計＝`lines` 依工項名彙總（同名相加、空名落其他都在 mirror_lines
        # 裡）。它一次就把兩份都算好了，前端直接用 —— 使用者要拿這個數字跟私帳
        # 現有的並排，決定覆蓋／保留／匯入。
        "crm_split": mir["split"],
        # 🔴 沒有掛給我的成本行**不擋**（owner 2026-09-12：推送＝在對面建對應的案，
        # 案子確實存在只是私帳還沒記；金額先開 0 由他自己填）。`warning` 是給
        # 畫面講清楚為什麼是 0，不是擋人的理由。`can_mirror` 留著給舊分頁（CF 給
        # .js 4 小時快取），永遠 True。
        "can_mirror": True, "reason": "", "warning": warning,
        # 每個候選帶著它**現有的工項** —— 選到有工項的那一案時，要在當下就畫出
        # 「私帳現有 vs CRM 成本行」的對照讓人決定怎麼做（owner 2026-08-30）。
        # 為此再打一趟 API 的話，下拉每換一次就閃一下。
        "options": [{"id": i, "name": n, "amount": int(a or 0),
                     "split": (norm_detail(d).get("split") or {}),
                     # 已經承接幾個 CRM 案 —— 前端據此提示「這案已經連了 N 筆，
                     # 覆蓋會洗掉別案的錢」並預設改用「加進去」
                     "linked_count": int(linked_counts.get(i, 0))}
                    for i, n, a, d in options],
    }


def _mirror_blocked_reason(total: int) -> str:
    """成本行一筆都沒掛給我時要跟使用者講的那句話（**警告，不擋**）。

    🔴 「已經連結過」**不是**擋人的理由（owner 2026-09-01「我 crm 有更新費用，
    但是私帳沒有連結過去」）。連結是連結當下的一次性複製 —— CRM 後來新增的
    成本行（那一案是後補的「翻譯(士源代)」3,000）不會自己流過去，而原本這裡
    一擋，就連**手動**再同步一次的路都沒有了：按鈕只會 toast 一句「已經連結
    到 X 了」，私帳永遠停在舊數字。已連結改走「重新同步」（鎖定原本那一案，
    覆蓋／加進去／保留三選一）。

    2026-09-12 起「沒有成本行」也不擋了（推送＝補一列，金額先開 0）；這句留著
    給畫面解釋為什麼是 0。
    """
    if not total:
        return ("這一案的成本行沒有一筆掛給你（或金額還沒填）—— "
                "私帳那案的收入會先開 0；之後到人員配置把該給你的工項填上實際金額，"
                "再按「重新同步」")
    return ""


def _mirror_contract(p, mir: dict, source: str) -> int:
    """分身的收入：一般＝掛給我的成本行合計；案源＝代開發票時＝**母帳合約額**
    （owner 2026-09-12「雖然是代開發票，但是專案公司也留一份帳」—— 那張內部代開
    發票的面額就是他的錢，公司這邊只是過路；掛給他的成本行通常是 0）。
    母帳沒填合約額就退回成本行合計。"""
    if source == "代開發票" and int(p.contract_amount or 0):
        return int(p.contract_amount or 0)
    return int(mir["total"] or 0)


def _new_mirror_row(p, mir: dict, note: str, source: str = ""):
    """母帳案 → 私帳分身那一列（未加入 session）。推送鈕與對應表的「建立」共用 ——
    兩條路各建一份，遲早只有一邊記得補新的必填欄。
    `source` 空＝源日（公司內部轉單）；代開發票→代辦費自動算（apply_source_fee）。"""
    from core.ledger_project import apply_source_fee, mirror_detail, norm_detail
    from routers.api_finance_projects import new_ledger_project

    from core.ledger_project import MIRROR_AT_KEY

    contract = _mirror_contract(p, mir, source)
    d = mirror_detail(mir["split"])
    d[MIRROR_AT_KEY] = _today_tw()
    if source:
        d["source"] = source
    d = norm_detail(apply_source_fee(contract, d))
    return new_ledger_project(
        project_id=uuid.uuid4().hex, name=p.name, client_id=p.client_id,
        entity="mine", contract=contract, detail=d,
        close=p.completion_date, notes=note, source_project_id=p.id)


@router.post("/projects/{project_id}/mirror-to-mine")
async def mirror_project_to_mine(project_id: str, req: ProjectMirrorPayload,
                                 request: Request):
    """建立（或連結既有的）私帳收入分身。**只有私帳 full scope 的帳號能按**。

    寫入只碰私帳那一列：`contract_amount`／`ledger_detail.split`／
    `source_project_id`。母公司那一列的**錢**一個欄位都不動 —— 它跟客戶的合約與
    成本都還在原地（識別欄的「以母帳為準」在 _write_link 裡順手套）。
    """

    from core.ledger import require_entity
    from core.ledger_project import (BILLING_MIRROR_SOURCE, MIRROR_AT_KEY, MIRROR_SOURCE, MIRROR_TOTAL_KEY,
                                     SELECTABLE_SOURCES, apply_source_fee, billing_mode_of, merge_split,
                                     norm_detail)
    from routers.api_finance_projects import resync_receivable

    require_entity(request, "mine", level="full")
    mode = (req.mode or "overwrite").strip()
    if mode not in MIRROR_MODES:
        raise HTTPException(status_code=422,
                            detail=f"未知的處理方式：{mode or '(空)'}")
    source = (req.source or "").strip()
    if source and source not in SELECTABLE_SOURCES:
        raise HTTPException(status_code=422, detail=f"未知的案源：{source}")
    sid = await _me_staff_id_or_blank(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        p, mir, linked = await _mirror_preview(session, project_id, sid)
        # 沒指定案源時看母帳的收款方式（後期代開 → 代開發票；其他不表態，交給下面的鏈）
        source = source or BILLING_MIRROR_SOURCE.get(billing_mode_of(p.billing_mode), "")
        target_id = (req.target_id or "").strip()
        if linked is not None:
            # 🔴 已經連結過的案子，空的 target 要解成**原本那一案**，不是落到
            # 下面的 else 去 `new_ledger_project` —— 那會在私帳多開第二個分身，
            # 同一筆錢算兩次，而畫面上兩案長得一模一樣（同名同客戶）。
            target_id = target_id or linked.id
            if target_id != linked.id:
                raise HTTPException(
                    status_code=409,
                    detail=f"這一案已經連結到私帳的「{linked.name}」了，"
                           "目前不支援改連到別案")
        imported = 0
        if target_id:
            # 連結既有：只覆蓋收入那半邊。他在私帳填的委外／代開費用是**他自己
            # 的成本**，公司管不著 —— 整包寫回去會把那些洗成 0。
            t = await session.get(CrmProject, target_id)
            if t is None:
                raise HTTPException(status_code=404, detail="找不到要連結的私帳專案")
            if (t.entity or "parent") != "mine":
                raise HTTPException(status_code=409, detail="要連結的專案不在私帳")
            keep = norm_detail(t.ledger_detail)
            # 🔴 連到**既有**私帳案而 CRM 這邊算出來是 0（沒綁人員檔案認不出哪幾行是我的、
            # 或成本行一筆都沒掛給我）：overwrite 會把他親手填的合約額與工項洗成 0、add 會
            # 把 mirror_total 清掉。以前這條路被 409 擋著，「不擋 0」只該放行**新建**（開 0
            # 由他填），既有的案退成 keep（只連結、金額不動）。代開發票例外：那案的收入是
            # 母帳合約額不是成本行，0 本來就是常態。
            # 同步過的案（有 mirror_total）成本行歸零就是歸零：那正是「私帳落後 −N」要
            # 讓他按「用 CRM 更新」清掉的狀態，降成 keep 的話那顆提示永遠清不掉。
            if (mode in ("overwrite", "add") and not mir["total"]
                    and (not sid or MIRROR_TOTAL_KEY not in keep)
                    and (source or keep.get("source") or MIRROR_SOURCE) != "代開發票"):
                mode = "keep"
            if mode == "import":
                # 反過來：私帳是正本，把它的工項寫成母公司的成本行。
                # 私帳那一列除了連結之外一個數字都不動。
                if not sid:
                    raise HTTPException(status_code=422,
                                        detail="帳號沒綁人員檔案，寫不出「掛給你」的成本行")
                imported = await _import_split_to_cost_lines(
                    session, p, keep.get("split") or {}, sid)
            elif mode in ("overwrite", "add"):
                # overwrite＝用這個 CRM 案的錢取代；add＝加上去（同名工項相加）。
                # 🔴 一個私帳案承接多個 CRM 案時只有 add 說得通 —— overwrite
                # 會把前一個 CRM 案鏡射進來的錢洗掉，而那筆錢也是他該拿的。
                # 併法（同名工項相加／取代）的正本在 core.ledger_project，
                # 它回 delta 而不是 total —— 私帳案的合約金額未必等於 Σ(split)
                if mode == "overwrite":
                    from routers.crm._shared import mine_parent_names
                    _others = (await mine_parent_names(session, [t.id])).get(t.id) or ()
                    if len(_others) > 1:
                        raise HTTPException(status_code=409, detail="這個私帳案承接了 %d 個母帳案（%s），用「取代」會洗掉別案鏡射進來的錢；請改用「加上去」"
                                            % (len(_others), "、".join(_others)))
                merged, delta = merge_split(keep.get("split") or {},
                                            mir["split"] or {},
                                            add=(mode == "add"))
                # 案源：這次指定的 > 私帳案本來的 > 源日。🔴 不要無條件寫回源日 ——
                # 代開發票的分身重新同步一次就會被翻成源日、代辦費歸零
                keep["split"] = merged
                keep["source"] = source or keep.get("source") or MIRROR_SOURCE
                keep[MIRROR_TOTAL_KEY] = mir["total"]     # 「私帳落後了沒」比的就是它
                keep[MIRROR_AT_KEY] = _today_tw()
                if keep["source"] == "代開發票":
                    # 代開的收入是那張發票的面額（母帳合約額），不是成本行；
                    # 重新同步只更新工項，金額不動（沒填過才用母帳合約額補）
                    if not int(t.contract_amount or 0):
                        t.contract_amount = _mirror_contract(p, mir, "代開發票")
                else:
                    t.contract_amount = (int(t.contract_amount or 0) + delta
                                         if mode == "add" else mir["total"])
                keep = norm_detail(apply_source_fee(int(t.contract_amount or 0), keep))
                t.ledger_detail = keep
                # 營收換了 → 應收跟著重算，否則這一案的應收停在舊數字
                resync_receivable(t, keep)
            # mode == "keep"：只建立連結，金額一毛不動
            await _write_link(session, p, t)            # 連結的唯一寫入者（兩側欄位一起顧）
            new_id = t.id
        else:
            t = _new_mirror_row(p, mir, f"[私帳連結] 來自母公司專案：{p.name}", source)
            new_id = t.id
            session.add(t)
            await _write_link(session, p, t)
        await session.commit()
        # 新建的是私帳案 —— is_mine_project 的 60 秒 id-set 快取要清，
        # 否則這一分鐘內它會被當成母公司的（同 move-ledger 的理由）
        from core.ledger import invalidate_mine_projects
        invalidate_mine_projects()
    # amount＝畫面 toast 要講的數字：add 是加了多少（delta＝mir["total"]），其他是私帳那案
    # 現在的收入（代開發票的分身是母帳合約額，不是成本行合計 —— 回 mir["total"] 會說「同步收入 0」）
    # mode 回的是**實際套用的**（連既有案而 CRM 算出 0 時會從 overwrite/add 降成 keep）
    return {"status": "ok", "id": new_id,
            "amount": mir["total"] if mode == "add" else int(t.contract_amount or 0),
            "mode": mode, "imported": imported, "staff_bound": bool(sid)}


# ── 收款方式（owner 2026-09-13）：欄位變了，私帳分身跟著變 ─────────────────────
#
# 表單上的下拉取代了「推送到私帳」先問是哪一種的彈窗（core.ledger_project.BILLING_MODES）。
#   → passthrough：沒分身就建（案源＝代開發票、收入＝母帳合約額、代辦費自動）；有分身就把
#                  案源改成代開發票＋重跑費用（金額與工項不動）。
#   → company／cash（從 passthrough 換回）：分身**留著**，案源改回源日、代辦費三欄歸零。
#                  cash 是「源日收現金」（owner：這裡是源日的收款狀態），不碰後期。
# 🔴 只有看得到私帳（mine full）的請求會動私帳那一列 —— 別人存這個欄位只存欄位，
#    分身等帳本主人下次存一次（回 `skipped`，回應把它講出來）。


async def apply_billing_mode(session, p, request, old_mode: str) -> dict:
    """母帳案 `billing_mode` 從 `old_mode` 變成現在的值 → 私帳分身的對應動作。不 commit。
    回 `{"action": created|switched|reverted|none|skipped, "created": bool}`。"""
    from core.ledger import require_entity
    from core.ledger_project import (FEE_DEDUCTED_KEY, BILLING_MIRROR_SOURCE, MIRROR_SOURCE,
                                     apply_source_fee, billing_mode_of, manual_fields, norm_detail)

    new = billing_mode_of(p.billing_mode)
    old = billing_mode_of(old_mode)
    want_source = BILLING_MIRROR_SOURCE.get(new, "")
    # 值沒變：只有「後期代開但分身還沒建」（同事先存了欄位、帳本主人再存一次）要補建，其他不動
    if (p.entity or "parent") == "mine" or (new == old and not want_source):
        return {"action": "none", "created": False}
    t = await resolve_mine_link(session, p)
    if want_source and new == old and t is not None:
        # 表單整包送回同一個值而分身早就在 —— 什麼都不用做，也**不要**對沒私帳權限的同事回 skipped
        # （那句 warning 會在他每一次存檔時跳出來）
        return {"action": "none", "created": False}
    try:
        require_entity(request, "mine", level="full")
    except HTTPException:
        return {"action": "skipped", "created": False}
    if want_source:
        if t is None:
            sid = await _me_staff_id_or_blank(request)
            _p, mir, _linked = await _mirror_preview(session, p.id, sid)
            t = _new_mirror_row(p, mir, f"[私帳連結] 來自母公司專案：{p.name}（收款方式：後期代開）",
                                want_source)
            session.add(t)
            await _write_link(session, p, t)
            return {"action": "created", "created": True}
        # 有分身：案源改成代開發票、收入**一律**＝母帳合約額（owner 2026-09-13「一律改成合約」——
        # 代開的錢就是那張發票的面額，之前分身記的成本行合計不是）；工項不動。母帳沒填合約額才留原值。
        keep = norm_detail(t.ledger_detail)
        keep["source"] = want_source
        if int(p.contract_amount or 0):
            t.contract_amount = int(p.contract_amount or 0)
        _store_mirror_detail(t, apply_source_fee(int(t.contract_amount or 0), keep))
        return {"action": "switched", "created": False}
    # 換回源日專案／現金收款：分身留著，只把代開那套拿掉
    if t is not None and BILLING_MIRROR_SOURCE.get(old) and \
            norm_detail(t.ledger_detail).get("source") == BILLING_MIRROR_SOURCE[old]:
        keep = norm_detail(t.ledger_detail)
        keep["source"] = MIRROR_SOURCE
        for k in ("invoice_fee", "tax_fee", "buy_invoice"):
            keep[k] = 0
        keep["manual"] = sorted(manual_fields(keep) - {"invoice_fee"})
        keep.pop(FEE_DEDUCTED_KEY, None)
        _store_mirror_detail(t, keep)
        return {"action": "reverted", "created": False}
    return {"action": "none", "created": False}


def _store_mirror_detail(t, detail: dict) -> None:
    """分身的 ledger_detail 落庫三步（正規化、應收重算、時間戳）—— 換案源與換回兩條路共用。"""
    from core.ledger_project import norm_detail
    from routers.api_finance_projects import resync_receivable
    keep = norm_detail(detail)
    t.ledger_detail = keep
    resync_receivable(t, keep)      # 營收／代扣成本動了 → 應收與收款狀態一律重算
    t.updated_at = _now()


async def link_note_for(session, p, request) -> dict:
    """「後期連結」那一行（core.ledger_project.link_note 的 I/O 半邊）。
    看不到私帳的請求：只講收款方式，不露私帳案（同 `mirrored` 那條可見性線）。"""
    from core.ledger import hide_mine_projects
    from core.ledger_project import link_note, mirror_stale, norm_detail
    from ._shared import mine_parent_names

    mode = p.billing_mode
    if (p.entity or "parent") == "mine" or hide_mine_projects(request):
        return link_note(mode, None, None)
    t = await resolve_mine_link(session, p)
    if t is None:
        return link_note(mode, None, None, passthrough_invoices=await _passthrough_invoice_count(session, p.id))
    detail = norm_detail(t.ledger_detail)
    stale = None
    sid = await _me_staff_id_or_blank(request)
    if sid:
        # 同 mirror-check：一個私帳案承接多個母帳案時合計不屬於任何一案 → 判不出來
        shared = len((await mine_parent_names(session, [t.id])).get(t.id) or []) > 1
        if not shared:
            _p, mir, _l = await _mirror_preview(session, p.id, sid)
            stale, _delta = mirror_stale(detail, mir["total"])
    return link_note(mode, (t.id, t.name or "", t.contract_amount), detail, stale=stale,
                     passthrough_invoices=await _passthrough_invoice_count(session, p.id))


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



async def _write_link(session, parent, mine):
    """連結的**唯一寫入者**：母帳那一列指向哪個私帳案（`mine` 為 None＝解除）。

    連結記在母帳側（一個私帳案可以承接多個母帳案，owner 2026-09-01）；私帳那側
    的 `source_project_id` 只留**第一個**來源，給「這是分身」的舊判定沿用。
    對應表兩個方向、mirror-to-mine 的解除都走這裡 —— 三處各寫一遍就會長出
    「一邊清了、另一邊沒清」的半連結狀態。

    連上之後順手套「以母帳為準」（owner 2026-09-12）：識別欄由母帳決定、母帳空白
    的拿私帳的補（`core.ledger_project.sync_from_parent`）。**只對 1:1 做** ——
    一個私帳案承接多個母帳案時母帳彼此可能矛盾，不猜（同顯示名規則）。
    """
    if mine is None:
        parent.mine_link_id = None
        parent.updated_at = _now()
        return
    if parent.mine_link_id and parent.mine_link_id != mine.id:
        raise HTTPException(status_code=409, detail="這個母帳案已經連到別的私帳案")
    parent.mine_link_id = mine.id
    parent.updated_at = _now()
    if not mine.source_project_id:
        mine.source_project_id = parent.id
    mine.updated_at = _now()
    await _sync_pair(session, parent, mine)


async def _sync_pair(session, parent, mine, *, explicit=()) -> bool:
    """對一對已連結的案套「以母帳為準」。回有沒有真的改到東西。

    純規則在 `sync_from_parent`；這裡只補它做不到的 I/O：N:1 判定（走 mine_parent_links ——
    兩種連結形狀都認、母帳案已刪的舊指標不算，跟私帳詳情鎖欄位的判定同一份）、以及**客戶**那一欄的兩個方向 —— 私帳客戶是 entity=mine 的
    Client，跟母帳客戶是兩筆：私帳那筆已經連到（`crm_link_id`）母帳這筆的話兩邊
    **就是同一個客戶**，不改（否則推送到母帳的當下私帳案的客戶就被換成母帳那筆，
    私帳自己的客戶檔／匯款資訊從此對不上這一案）；母帳空白才反向對到／建出母帳客戶。
    `explicit`：母帳 PUT 親手送上來的欄位（空白＝要清空、私帳跟著清；不反向補）。
    反向補到**結案日**時母帳狀態也要進「結案」並走 `_apply_status_side_effects`
    （待製作階段、結案通知）—— 不然 CRM 會有一個有結案日卻不是結案的案，挑案器照樣把它當進行中。
    """
    from core.ledger_project import sync_from_parent
    from core.project_flow import CLOSED_STATUSES
    from ._shared import mine_parent_links

    # 除了這一案之外還有沒有別的母帳案連著它（session 已 autoflush，這一案自己也在裡面）
    links = (await mine_parent_links(session, [mine.id])).get(mine.id) or []
    if any(pid != parent.id for pid, _n in links):
        return False                                   # N:1：不猜
    skip = set()
    if parent.client_id and mine.client_id and parent.client_id != mine.client_id:
        mc = await session.get(Client, mine.client_id)
        if mc is not None and (mc.entity or "parent") == "mine" and mc.crm_link_id == parent.client_id:
            skip.add("client_id")                      # 同一個客戶的兩本帳，不是不一致
    changed, filled = sync_from_parent(parent, mine, explicit=explicit, skip=skip)
    if not parent.client_id and mine.client_id and "client_id" not in explicit:
        cid = await _crm_client_for(session, mine.client_id)
        if cid:
            parent.client_id = cid
            filled.append("client_id")
            await _auto_update_client_status(session, cid)
    if "completion_date" in filled and (parent.status or "") not in CLOSED_STATUSES:
        old_status = parent.status or ""
        parent.status = "結案"
        _apply_status_side_effects(parent, old_status)
    if changed:
        mine.updated_at = _now()
    if filled:
        parent.updated_at = _now()
    return bool(changed or filled)


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


async def _crm_client_for(session, client_id: str):
    """私帳案的客戶 → 母帳該用哪一筆（沒有就建，owner「私帳的客戶母帳都有包含」）。
    規則只有 clients.link_or_create_crm_client 一份（本來就是 CRM 客戶直接用；私帳客戶已連結用連到的；沒連就建同代稱並連結）。"""
    if not client_id:
        return None
    c = await session.get(Client, client_id)
    if c is None:
        return None
    if (c.entity or "parent") != "mine":
        return c.id
    from .clients import link_or_create_crm_client
    exist, _created = await link_or_create_crm_client(session, c)
    return exist.id


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
