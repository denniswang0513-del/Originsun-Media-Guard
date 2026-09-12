"""routers/crm/projects.py — 專案管理 + 結案看板 + CSV 匯入。

自 routers/api_crm.py 原樣搬移（純搬移，行為不變）。
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import shutil
import uuid

from fastapi import HTTPException, Request, UploadFile, File, Query
from sqlalchemy import func as _sa_func

from config import load_settings as _load_settings

from core.finance_logic import MINE_LINK_INVOICE_CATEGORY
from core.ledger import hide_mine_projects, not_mine
from core.ledger_project import LINK_SYNC_FIELDS, parent_receipt_fields
from core.project_link import invoice_project_ids as _inv_pids
from db.models import CrmCashSplit    # 拆項列（母帳收款歸案要看它；_shared 沒 re-export）
from core.schemas import (CrmProjectPayload, CrmProjectPatchPayload, ProjectTypeOpPayload,
                          ProjectLedgerMovePayload, ProjectMirrorPayload)

from ._shared import (router, _check_auth, _check_status_auth, _check_project_write_auth,
                      _check_website_auth, _require_db,
                      _get_factory, _now, _parse_shoot_date,
                      _auto_update_client_status, map_csv_row, _fmt_day)

try:
    from ._shared import (select, or_,
                          Client, CrmCashEntry, CrmCashInvoiceLink, CrmInvoice, CrmPaymentRequest,
                          CrmProject, CrmProjectCostGroup,
                          CrmProjectCostLine, CrmProjectExpense,
                          CrmProjectStaff, CrmProjectShowcase)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

# ── Project Helpers ─────────────────────────────────────────

def is_mirrored(p, legacy_ids=()) -> bool:
    """這一案有沒有在私帳開分身（＝要不要標「已連結私帳」）。**只有這一份判定。**

    🔴 兩種形狀都算數，因為連結換過邊：
      · 新的記在**來源這一側**（`mine_link_id`）—— 一個私帳案要能承接多個
        CRM 案（owner 2026-09-01「可以多筆專案連結到一筆私帳」）。
      · 舊的記在私帳案上（`source_project_id` 指回來源），那一欄只裝得下第一個。

    只查舊的那種，第二個之後連上去的案就沒有標籤 —— owner 2026-09-01 回報的
    正是這個（「南山人壽謝經理」連了卻沒標）。

    這是 `resolve_mine_link()` 的布林版：那支現查目標案（詳情那條路），這支
    吃一份先撈好的 `legacy_ids`（清單那條路 —— 409 案不能逐列查 DB）。
    **兩種形狀的知識就這兩支**，舊形狀要退場時一起改。
    """
    return bool(p.mine_link_id) or p.id in legacy_ids


async def mine_link_map(session, parent_ids) -> dict:
    """批次版：{母帳案 id: 私帳案 id}，兩種形狀都認（新：母帳 mine_link_id；舊：私帳 source_project_id 回指）。
    對照表與清單那條路用它，不各自再抄一次兩種形狀。"""
    ids = {i for i in parent_ids if i}
    if not ids:
        return {}
    out = dict((await session.execute(
        select(CrmProject.id, CrmProject.mine_link_id)
        .where(CrmProject.id.in_(ids), CrmProject.mine_link_id.isnot(None)))).all())
    for mid, src in (await session.execute(
            select(CrmProject.id, CrmProject.source_project_id)
            .where(CrmProject.source_project_id.in_(ids)))).all():
        out.setdefault(src, mid)
    return out


async def resolve_mine_link(session, p):
    """這一案連到私帳的哪一案（沒有就 None）。**「兩種形狀」的知識只有這一份。**

    🔴 連結記在**來源這一側**（`mine_link_id`）—— 一個私帳案要能承接多個 CRM 案
    （owner 2026-09-01「可以多筆專案連結到一筆私帳」）。舊資料的連結記在私帳案上
    （`source_project_id` 指回來），那一欄只裝得下第一個來源，所以查不到時退回
    舊查法，兩種形狀都認得。

    `is_mirrored()` 是它的布林版（清單那條路不能逐列查 DB，改用一次撈成集合的
    `legacy_ids`）—— 兩支要一起改，舊形狀退場時才不會漏。
    """
    if p.mine_link_id:
        t = await session.get(CrmProject, p.mine_link_id)
        if t is not None:
            return t
    return (await session.execute(
        select(CrmProject)
        .where(CrmProject.source_project_id == p.id)
        .limit(1))).scalars().first()


async def linked_receipts_map(session, project_ids) -> dict:
    """母公司案 → (Σ deposit, Σ bank_fee)：掛在本案的收入列（列自己 project_id、或列掛的發票是本案的）。
    一次兩句 SQL，不逐案查。私帳案不走這裡（私帳是 _sync_mine_project_received 的增量制）。"""
    ids = {i for i in project_ids if i}
    if not ids:
        return {}
    # 發票掛好幾個案（內部代開分攤）時一筆錢分不出誰的 → 只認掛單一案的發票
    inv_proj = {iid: pids[0] for iid, pid, pids in (
        (r[0], r[1], _inv_pids(r[1], r[2])) for r in (await session.execute(
            select(CrmInvoice.id, CrmInvoice.project_id, CrmInvoice.project_ids)
            .where(CrmInvoice.project_id.in_(ids)))).all())
        if len(pids) == 1 and pids[0] in ids}
    # 分配表：一筆收款分給好幾張發票（invoice_id 只記最大那張）→ 照分配金額給各案，不把整筆都算給第一案
    links: dict = {}
    if inv_proj:
        for eid, iid, amt in (await session.execute(
                select(CrmCashInvoiceLink.cash_entry_id, CrmCashInvoiceLink.invoice_id, CrmCashInvoiceLink.amount)
                .where(CrmCashInvoiceLink.invoice_id.in_(set(inv_proj))))).all():
            links.setdefault(eid, []).append((iid, int(amt or 0)))
    # 拆項：父列的專案歸屬已讓位給拆項（routers/crm/cash_splits）；拆項 fee＝代開費，專案按毛額（amount＋fee）結清
    split_rows = (await session.execute(
        select(CrmCashSplit.entry_id, CrmCashSplit.project_id, CrmCashSplit.amount, CrmCashSplit.fee)
        .where(CrmCashSplit.project_id.in_(ids)))).all()
    split_entries = {r[0] for r in split_rows}          # 有拆項歸到本案的父列（歸屬在拆項上，父列本身不算）
    conds = [CrmCashEntry.project_id.in_(ids)]
    if inv_proj:
        conds.append(CrmCashEntry.invoice_id.in_(set(inv_proj)))
    if links:
        conds.append(CrmCashEntry.id.in_(set(links)))
    # 🔴 發票代開的收款也算客戶匯進來的錢（owner 2026-09-05 把「發票代開」開放掛專案就是為了讓它進「客戶已匯」；
    # 撥給代開人的那半邊是應付，不影響「客戶付了沒」）
    rows = (await session.execute(
        select(CrmCashEntry.id, CrmCashEntry.project_id, CrmCashEntry.invoice_id, CrmCashEntry.deposit, CrmCashEntry.bank_fee)
        .where(CrmCashEntry.entity == "parent", CrmCashEntry.deposit > 0, or_(*conds)))).all()
    out: dict = {}
    def _add(eff, dep, fee):
        r, f = out.get(eff, (0, 0))
        out[eff] = (r + int(dep or 0), f + int(fee or 0))
    for eid, pid, iid, dep, fee in rows:
        if eid in split_entries:
            continue                                   # 這一列的歸屬在拆項上
        if eid in links:
            fee_taken = False
            for liid, amt in links[eid]:
                eff = inv_proj.get(liid)
                if eff:
                    _add(eff, amt, 0 if fee_taken else fee)   # 匯費只給一次（顯示用）
                    fee_taken = True
            continue
        eff = pid if pid in ids else inv_proj.get(iid)
        if eff:
            _add(eff, dep, fee)
    from core.crm_logic import split_gross
    for _eid, spid, amt, sfee in split_rows:
        _add(spid, split_gross(amt, sfee), sfee)       # 專案按毛額結清：加法只有 split_gross 一份
    return out


#: 備份三根的欄位名 —— 正規化、投影端點、測試共用這一份，別各寫各的
BACKUP_ROOT_FIELDS = ("backup_local_root", "backup_nas_root", "backup_proxy_root")


def normalize_backup_roots(data: dict) -> list:
    """把 data 裡的備份三根就地正規化成 canonical UNC，回傳警告字串清單。

    **寫入端唯一入口**（POST /projects 與 PUT /projects/{id} 都走它）：使用者
    在哪台打的就是哪台的視角（master 打 `T:\\`、NAS 容器打 `/share/…`），存進 DB
    前一律翻成 UNC，別台才讀得到 —— 2026-07-21 煥民新村備份 6 連敗的同一個病。

    語法垃圾（黏路徑那類）只**警告不擋**：設定的人可能就是在一台看不到那個
    share 的機器上編輯，這很正常；擋下來反而逼人去別台才能填。
    """
    from core.drive_map import invalid_path_reason, to_canonical
    warnings = []
    for f in BACKUP_ROOT_FIELDS:
        if f not in data:
            continue
        raw = str(data[f] or "").strip().rstrip("\\/")
        if not raw:
            data[f] = None
            continue
        why = invalid_path_reason(raw)
        if why:
            warnings.append(f"{f}：{why}")
        data[f] = to_canonical(raw)
    return warnings


def _to_project_dict(p, client_short_name: str = "", mirrored: bool = False, mine_link=None,
                     receipts=None) -> dict:
    """`mine_link`＝連到的私帳案 `(id, name)`（沒連／看不到私帳＝None）——
    兩種連結形狀都由呼叫端解好再餵進來（清單走 mine_link_map 批次、單筆走 resolve_mine_link）。"""
    mid, mname = mine_link[:2] if (mirrored and mine_link) else ("", "")
    # 母公司案有掛帳收入列 → 已收／匯費／應收／收款狀態一律推導（core.ledger_project.parent_receipt_fields），
    # 沒有掛的老案維持手填欄位
    received, fee = (receipts or {}).get(p.id, (0, 0))
    # 佔位合約（母私帳對齊從私帳抄來的下限，contract_amount_source='mine'）不是真合約：拿它比收款會誤報全額到帳／溢收
    derived = (p.entity or "parent") != "mine" and (received or fee) and (p.contract_amount_source or "") != "mine"
    if derived:
        receivable, status = parent_receipt_fields(int(p.contract_amount or 0), received, fee)
    return {
        "id": p.id, "name": p.name,
        "client_id": p.client_id, "client_short_name": client_short_name,
        "status": p.status or "洽詢",
        "am_username": p.am_username or "", "pm_usernames": p.pm_usernames or [],
        "shoot_date": p.shoot_date.isoformat() if p.shoot_date else None,
        "start_date": p.start_date.isoformat() if p.start_date else None,
        "completion_date": p.completion_date.isoformat() if p.completion_date else None,
        "project_type": p.project_type or "",
        "folder_path": p.folder_path or "",
        # 備份三根：DB 存 canonical UNC，這裡原樣回。翻成當台視角是**呼叫端**的事
        # （備份頁用 drive_map.to_local_path）—— 在序列化就翻掉的話，master 存的
        # 設定經 NAS 對外容器讀出來會變成 /share/… 再存回去，來回一次就爛掉。
        "backup_local_root": p.backup_local_root or "",
        "backup_nas_root": p.backup_nas_root or "",
        "backup_proxy_root": p.backup_proxy_root or "",
        "description": p.description or "", "notes": p.notes or "",
        # 兩本帳 §8：錢流歸屬。這個鍵同時是 core/money mine-aware 抹除的
        # 判定依據（entity=='mine' 的物件，金額鍵對無 mine scope 者整棵抹掉）
        "entity": p.entity or "parent",
        "crm_pushed": int(p.crm_pushed or 0),
        # 這一案有沒有私帳分身（「連結私帳」建出來的那一列指回它）。
        # 🔴 只在請求者看得到私帳時才會是 True —— 分身是私帳的列，對沒有
        # mine scope 的人連存在都不該露（hide_mine_projects 那條線）。
        "mirrored": bool(mirrored),
        # 連到私帳的哪一案。跟 `mirrored` 同一條可見性線（看不到私帳的人拿到
        # 空字串）—— 露出來前端才畫得出「連到哪」，而不是只有開視窗才知道。
        "mine_link_id": (mid or p.mine_link_id or "") if mirrored else "",
        "mine_link_name": mname or "",
        "contract_amount": p.contract_amount,
        "tax_rate": p.tax_rate, "profit_target_pct": p.profit_target_pct,
        "misc_budget_pct": p.misc_budget_pct,
        "payment_status": status if derived else (p.payment_status or "未到帳"),
        "amount_receivable": receivable if derived else p.amount_receivable,
        "amount_received": received if derived else p.amount_received,
        "transfer_fee": fee if derived else p.transfer_fee,
        "receipts_linked": bool(derived),      # True＝上面四欄是掛帳收入推的，不是手填
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


# 私帳案的可見性規則在 core.ledger.hide_mine_projects（取名字才 grep 得到）。
_hide_mine = hide_mine_projects


# ── Project Endpoints ───────────────────────────────────────

@router.get("/project-types")
async def get_project_types():
    """案型清單**一份**（owner 2026-09-03「這裡的案型跟私帳同步」）：正本＝私帳毛利表的列
    （core.finance_logic.project_type_vocab 再併上 settings／在用的）。CRM 專案表下拉、案型清單、私帳設定頁、
    工時 burn 表、手機版都吃同一份。"""
    return await _project_types_payload()


async def _project_types_payload() -> dict:
    """案型清單＝毛利表 ∪ settings ∪ 專案還掛著的（改名／刪除後下拉仍要列得到它，model_remove_type 的承諾）。
    GET 與 POST 都回這一份。"""
    from core.finance_logic import project_type_vocab
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        in_use = (await session.execute(
            select(CrmProject.project_type).where(CrmProject.project_type.isnot(None), CrmProject.project_type != "")
            .distinct())).scalars().all()
    return {"project_types": project_type_vocab(list(in_use))}


@router.post("/project-types")
async def edit_project_types(payload: ProjectTypeOpPayload, request: Request):
    """CRM「案型清單」的新增／改名／刪除：直接改私帳毛利表（案型的正本），save_margin_model 會鏡射進
    settings.project_types。守衛＝crm_projects（2026-09-08 第二批：案型清單就在專案表單旁邊）。
    改名把舊名記進 aliases，舊案還對得到毛利。"""
    _check_project_write_auth(request)
    from core.finance_logic import (load_margin_model, model_add_type, model_remove_type, model_rename_type,
                                    save_margin_model)
    model = load_margin_model("mine")
    ops = {"add": lambda: model_add_type(model, payload.name),
           "rename": lambda: model_rename_type(model, payload.name, payload.new_name),
           "remove": lambda: model_remove_type(model, payload.name)}
    if payload.op not in ops:
        raise HTTPException(status_code=422, detail="op 必須是 add／rename／remove")
    if not ops[payload.op]():
        raise HTTPException(status_code=409, detail={"add": "這個案型已經有了", "rename": "找不到舊案型，或新名字已存在",
                                                     "remove": "找不到這個案型"}[payload.op])
    save_margin_model("mine", model)
    return await _project_types_payload()


@router.get("/projects")
async def list_projects(
    request: Request,
    q: str = Query(""),
    status: str = Query(""),
    client_id: str = Query(""),
    am: str = Query(""),
    entity: str = Query(""),
    include_pushed: int = Query(0),
):
    """entity 篩選（兩本帳 §8）：''＝兩本都看、'parent'＝母公司、'mine'＝私帳。

    🔴 這是**檢視篩選不是權限**：專案本身共用可見（owner 拍板「只有錢分帳」），
    錢的牆在 core/money（mine-aware 抹除 + money_dep）。前端預設帶 'parent' ——
    私帳 402 案匯入後全帶新的 updated_at，不篩就整片壓在列表最上面、把公司的
    案子埋掉（2026-08-24 owner 回報「沒有看到專案管理」的真正症狀）。
    """
    from core.ledger import ENTITIES
    if entity and entity not in ENTITIES:
        raise HTTPException(status_code=422, detail=f"未知的帳本: {entity}")
    _require_db()
    factory = await _get_factory()

    # 提案=專案合體：列表附掛提案子狀態（草稿/已提案/入圍…），管線「提案」
    # 分頁列上顯示 badge 用。多提案連同一專案（N:1）取最近更新的一筆。
    from db.models import PreprodProposal
    prop_status_sq = (
        select(PreprodProposal.status)
        .where(PreprodProposal.project_id == CrmProject.id)
        .order_by(PreprodProposal.updated_at.desc())
        .limit(1)
        .correlate(CrmProject)
        .scalar_subquery()
    )

    async with factory() as session:
        query = (
            select(CrmProject, Client.short_name.label("client_short_name"),
                   prop_status_sq.label("proposal_status"))
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .order_by(CrmProject.updated_at.desc())
        )
        # 🔴 先擋可見性再談篩選：沒有私帳權限的人，私帳案整列不出現
        # （包含 include_pushed 推進管線的那些 —— 推送只影響「他自己」在
        # 專案管理看不看得到，不是對外開放）。
        if _hide_mine(request):
            query = query.where(not_mine(CrmProject.entity))
        # 🔴 分身不進這份清單（owner 2026-08-30「已經連結的就不用重複出現了，
        # 只出現 crm 的就好」）：連結私帳之後同一個案名會出現兩列 —— 母公司那列
        # 標「已連結私帳」、私帳分身那列標「私帳」，看起來像重複建案。
        # 母公司那一列才是專案管理要管的主體（案子、客戶、派工都在它身上）；
        # 分身只是收入的鏡射，它該出現的地方是財務管理 › 執行專案。
        query = query.where(CrmProject.source_project_id.is_(None))
        if entity == "parent" and include_pushed:
            # 專案管理的管線視圖：母公司案 ∪ 推送過來的私帳案（後期專案）。
            # 🔴 是明確參數不是預設 —— 掛錢用的下拉（收支/器材/現金流…）打的
            # 是純 entity=parent，混進私帳案會讓人選到之後被跨帳本守衛 403。
            from sqlalchemy import and_
            query = query.where(or_(CrmProject.entity == "parent",
                                    and_(CrmProject.entity == "mine",
                                         CrmProject.crm_pushed == 1)))
        elif entity:
            query = query.where(CrmProject.entity == entity)
        if status:
            query = query.where(CrmProject.status == status)
        if client_id:
            query = query.where(CrmProject.client_id == client_id)
        if am:
            query = query.where(CrmProject.am_username == am)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(
                CrmProject.name.ilike(ql),
                CrmProject.description.ilike(ql),
            ))
        rows = (await session.execute(query)).all()
        # 有分身的母公司案（「連結私帳」）—— 一次撈成集合，不逐列查。
        # 看不到私帳的人拿到空集合：那個標籤等於在說「owner 私帳有這一案」。
        mirrored_ids = set()
        link_map: dict = {}
        link_names: dict = {}       # 私帳案 id → 案名（詳情列要畫「已連結私帳 → 案名」）
        show_mine = not _hide_mine(request)      # 一次就好（每列問一次是白費）
        if show_mine:
            mirrored_ids = set((await session.execute(
                select(CrmProject.source_project_id)
                .where(CrmProject.source_project_id.isnot(None)))).scalars())
            # 兩種形狀都解（mine_link_map 是唯一的批次解析器），再一次撈齊案名
            link_map = await mine_link_map(session, [p.id for p, _c, _ps in rows])
            if link_map:
                link_names = dict((await session.execute(
                    select(CrmProject.id, CrmProject.name)
                    .where(CrmProject.id.in_(set(link_map.values()))))).all())
        receipts = await linked_receipts_map(
            session, [p.id for p, _c, _ps in rows if (p.entity or "parent") != "mine"])

    def _link_of(p):
        mid = link_map.get(p.id, "")          # 看不到私帳的人 link_map 是空的
        return (mid, link_names.get(mid, "")) if mid else None

    return {
        "projects": [
            {**_to_project_dict(
                p, cname or "",
                # 看不到私帳的人：mirrored_ids 是空的，`mine_link_id` 這半邊也
                # 要一起關掉，否則標籤照樣洩漏「owner 私帳有這一案」。
                show_mine and is_mirrored(p, mirrored_ids), _link_of(p), receipts),
             "proposal_status": ps or ""}
            for p, cname, ps in rows
        ],
        "total": len(rows),
    }


async def create_project_in_session(session, data: dict):
    """建專案的**單一正本**：客戶檢核（404）、AM 繼承、客戶分級重算、
    第一張成本「主表」+ 預設雜支種子。POST /projects、提案建殼/成案
    （routers/api_proposals.py）都走這裡 —— 繞過它直接 insert 會做出
    結構不完整的專案。data = CrmProject 欄位 dict（日期欄需已 parse）。
    client_id 空 = 尚未定客戶的提案殼專案（合法，跳過客戶檢核與 AM 繼承）；
    手建路徑由 CrmProjectPayload.client_id 必填擋住，到不了這個分支。
    不 commit，caller 統一。回傳 (project, client_short_name)。"""
    now = _now()
    project = CrmProject(id=uuid.uuid4().hex,
                         created_at=now, updated_at=now, **data)
    client = (await session.get(Client, project.client_id)
              if project.client_id else None)
    if project.client_id and not client:
        raise HTTPException(status_code=404, detail="找不到指定的客戶")

    # Inherit AM from client if not explicitly set
    if client and not project.am_username and client.am_username:
        project.am_username = client.am_username

    session.add(project)
    await _auto_update_client_status(session, project.client_id)
    # 同時建立第一張子表「主表」。（$0 佔位雜支列 2026-08-18 起不再種 ——
    # 它們長得跟真資料一樣，生產庫一度積了 193 列；日常登記走就地新增列。）
    session.add(CrmProjectCostGroup(
        id=uuid.uuid4().hex, project_id=project.id, name="主表", sort_order=0,
    ))
    return project, (client.short_name if client else "")


@router.post("/projects")
async def create_project(req: CrmProjectPayload, request: Request):
    _check_project_write_auth(request)
    _require_db()
    factory = await _get_factory()

    date_fields = {"shoot_date", "start_date", "completion_date"}
    dates = {f: _parse_shoot_date(getattr(req, f)) for f in date_fields}
    data = {**req.model_dump(exclude=date_fields), **dates}
    root_warnings = normalize_backup_roots(data)

    async with factory() as session:
        project, client_name = await create_project_in_session(session, data)
        await session.commit()
        await session.refresh(project)

    # Folder template copy (best-effort)
    warning = ""
    if req.folder_path:
        try:
            settings = _load_settings()
            template = settings.get("project_folder_template", "")
            if template and os.path.isdir(template) and not os.path.exists(req.folder_path):
                await asyncio.to_thread(shutil.copytree, template, req.folder_path)
        except Exception as e:
            warning = f"資料夾範本複製失敗: {e}"

    if root_warnings:
        warning = "；".join([warning] + root_warnings) if warning else "；".join(root_warnings)

    result = {"status": "ok", "project": _to_project_dict(project, client_name)}
    if warning:
        result["warning"] = warning
    return result


@router.post("/projects/{project_id}/duplicate")
async def duplicate_project(project_id: str, request: Request):
    """一鍵深拷貝專案 — 連同成本子表 / 估算明細 / 雜支 / 派工一起複製。

    只複製「規劃層」（預算 / 估算 / 派工名單），重置「實際 / 結算層」，
    讓複製品當成一個全新可執行的專案，不污染應收應付與財務三表：
      - 專案：amount_received=0、payment_status='未到帳'、transfer_fee 清空；
              contract_amount / amount_receivable / 各預算% 保留；folder_path 清空
      - cost_lines：estimated_* 保留、actual_* 全清
      - expenses：estimated 保留、actual=0、receipt_url/payee/advance_id 清空
      - staff：days/cost/rate_override 保留、actual_days/actual_cost/payment_* 清空
    不複製報價 / 發票 / 收支 / 官網作品 / 付款節點等交易與文件資料。
    """
    _check_project_write_auth(request)   # 複製＝建專案，同一把 crm_projects（2026-09-08）
    _require_db()
    factory = await _get_factory()

    now = _now()
    async with factory() as session:
        src = await session.get(CrmProject, project_id)
        if not src:
            raise HTTPException(status_code=404, detail="找不到專案")
        client = await session.get(Client, src.client_id)
        client_name = client.short_name if client else ""

        new_id = uuid.uuid4().hex
        dup = CrmProject(
            id=new_id, name=f"{src.name}（複製）",
            client_id=src.client_id, status=src.status,
            am_username=src.am_username, pm_usernames=src.pm_usernames,
            shoot_date=src.shoot_date, start_date=src.start_date,
            completion_date=src.completion_date, project_type=src.project_type,
            folder_path="",  # 不沿用資料夾，避免兩專案指向同一夾
            description=src.description, notes=src.notes,
            contract_amount=src.contract_amount, tax_rate=src.tax_rate,
            profit_target_pct=src.profit_target_pct, misc_budget_pct=src.misc_budget_pct,
            amount_receivable=src.amount_receivable,
            # 重置實際收付款
            payment_status="未到帳", amount_received=0, transfer_fee=None,
            created_at=now, updated_at=now,
        )
        session.add(dup)

        # 成本子表 → 建立 old→new id 對映
        groups = (await session.execute(
            select(CrmProjectCostGroup)
            .where(CrmProjectCostGroup.project_id == project_id)
        )).scalars().all()
        gid_map: dict[str, str] = {}
        for g in groups:
            ng = uuid.uuid4().hex
            gid_map[g.id] = ng
            session.add(CrmProjectCostGroup(
                id=ng, project_id=new_id, name=g.name,
                shoot_date=g.shoot_date, notes=g.notes, sort_order=g.sort_order,
                budget_amount=g.budget_amount, misc_budget_amount=g.misc_budget_amount,
                profit_target_pct=g.profit_target_pct, receipt_path=g.receipt_path,
                created_at=now, updated_at=now,
            ))

        # 沒有任何子表的舊專案 → 補一張主表，保證詳情頁有東西
        if not groups:
            session.add(CrmProjectCostGroup(
                id=uuid.uuid4().hex, project_id=new_id, name="主表", sort_order=0,
            ))

        # 成本估算明細 — 保留預估、清結算
        lines = (await session.execute(
            select(CrmProjectCostLine)
            .where(CrmProjectCostLine.project_id == project_id)
        )).scalars().all()
        for ln in lines:
            session.add(CrmProjectCostLine(
                id=uuid.uuid4().hex, project_id=new_id,
                cost_group_id=gid_map.get(ln.cost_group_id),
                phase=ln.phase, item_name=ln.item_name, sort_order=ln.sort_order,
                estimated_unit_price=ln.estimated_unit_price,
                estimated_quantity=ln.estimated_quantity,
                estimated_unit_type=ln.estimated_unit_type,
                estimated_amount=ln.estimated_amount,
                estimated_staff_id=ln.estimated_staff_id,
                estimated_notes=ln.estimated_notes,
                created_at=now, updated_at=now,
            ))

        # 雜支 — 保留預估、清實際 + 收據 / 請款人 / 預支關聯
        expenses = (await session.execute(
            select(CrmProjectExpense)
            .where(CrmProjectExpense.project_id == project_id)
        )).scalars().all()
        for ex in expenses:
            session.add(CrmProjectExpense(
                id=uuid.uuid4().hex, project_id=new_id,
                cost_group_id=gid_map.get(ex.cost_group_id),
                category=ex.category, estimated=ex.estimated, actual=0,
                sub_item=ex.sub_item, notes=ex.notes,
                receipt_url=None, payee=None, advance_id=None,
                created_at=now,
            ))

        # 派工 — 保留預估、清實際 + 財務
        staff_rows = (await session.execute(
            select(CrmProjectStaff)
            .where(CrmProjectStaff.project_id == project_id)
        )).scalars().all()
        for s in staff_rows:
            session.add(CrmProjectStaff(
                id=uuid.uuid4().hex, project_id=new_id,
                staff_id=s.staff_id, role_in_project=s.role_in_project,
                phase=s.phase, days=s.days, rate_override=s.rate_override,
                cost=s.cost, notes=s.notes,
            ))

        await _auto_update_client_status(session, src.client_id)
        await session.commit()
        await session.refresh(dup)

    return {"status": "ok", "project": _to_project_dict(dup, client_name)}


# NOTE: 必須註冊在 /projects/{project_id} 之前 — 否則 "closing" 會被吃成 project_id。
_WEBSITE_PROD_STAGES = ("待製作", "製作中", "不上官網")
# 進入官網製作收件匣的專案階段（8 階段化後由「結案作業」改為「結案」）。
_CLOSING_STATUS = "結案"


async def _work_items_for_project(session, project, rows=None) -> list[dict]:
    """一個專案的作品清單（1:N）— 結案看板 works 子列 / GET /projects/{id}/works 共用。

    排序：主作品先、再依 sort_order / created_at。
    rows: 已預取的 CrmProjectShowcase list（結案看板批次查詢用，省 N+1）；
          None 才自己查。專案沒有任何 sc row（未進過 showcase 流程）→ 以
          _virtual_work_from_project 合成一筆主作品，看板卡片永遠有東西可畫。
    """
    from core.crm_logic import (effective_prod_stage, is_main_work, work_stage,
                                work_url_slug)
    from services.website.project_service import (
        _virtual_work_from_project, work_completeness_dict,
    )
    if rows is None:
        rows = (await session.execute(
            select(CrmProjectShowcase)
            .where(CrmProjectShowcase.project_id == project.id)
            .order_by((CrmProjectShowcase.id == CrmProjectShowcase.project_id).desc(),
                      CrmProjectShowcase.sort_order.desc(),
                      CrmProjectShowcase.created_at.asc().nullslast())
        )).scalars().all()
    if not rows:
        rows = [_virtual_work_from_project(project)]
    items = []
    for sc in rows:
        main = is_main_work(sc)
        # prod_stage 過渡期 fallback（Phase 4 停寫 public_* 時一併移除）：主作品的
        # sc row 可能由 get-or-create 路徑補水前建立、階段只寫在專案欄
        prod_stage = effective_prod_stage(sc.prod_stage, project.website_prod_stage,
                                          is_main=main)
        items.append({
            "id": sc.id,
            "is_primary": main,
            "title": sc.title or project.name or "",
            "slug": work_url_slug(sc),
            "published": bool(sc.published),
            "stage": work_stage(bool(sc.published), prod_stage),
            "verified": sc.verified_at is not None,
            "featured": bool(sc.featured),
            "completeness": work_completeness_dict(sc),
        })
    return items


@router.get("/projects/closing")
async def list_closing_projects(request: Request):
    """結案看板 — 列出所有 status='結案' 專案 + 官網上線/製作狀態 + 完整度。
    （AM 把專案從「歸檔」推進到「結案」才進此收件匣，避免歷史歸檔全列。）

    stage 推導：public=True → '已上線'；否則看 website_prod_stage（製作中/不上官網），
    其餘（含 None）→ '待製作'。completeness 四項各是 bool（是否已備妥該素材）。
    """
    _check_website_auth(request)
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        _q = (
            select(CrmProject, Client.short_name.label("client_short_name"),
                   Client.full_name.label("client_full_name"))
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .where(CrmProject.status == _CLOSING_STATUS)
            .order_by(CrmProject.completion_date.desc().nullslast())
        )
        # 沒有私帳權限就看不到私帳案（同清單那條）
        if _hide_mine(request):
            _q = _q.where(not_mine(CrmProject.entity))
        rows = (await session.execute(_q)).all()

        # 批次撈全部作品（1 查詢取代逐專案 2 次 — 看板每次操作後全量重打，N+1 很有感）
        works_map: dict[str, list] = {}
        pids = [p.id for p, _s, _f in rows]
        if pids:
            sc_rows = (await session.execute(
                select(CrmProjectShowcase)
                .where(CrmProjectShowcase.project_id.in_(pids))
                .order_by((CrmProjectShowcase.id == CrmProjectShowcase.project_id).desc(),
                          CrmProjectShowcase.sort_order.desc(),
                          CrmProjectShowcase.created_at.asc().nullslast())
            )).scalars().all()
            for sc in sc_rows:
                works_map.setdefault(sc.project_id, []).append(sc)

        from core.crm_logic import project_works_summary
        items = []
        for p, short_name, full_name in rows:
            # 卡片欄位 = 主作品值（works 排序保證主作品第一；沒有 sc row 的專案由
            # helper 以 _virtual_work_from_project 合成，舊 1:1 讀法自動涵蓋）
            works_items = await _work_items_for_project(
                session, p, rows=works_map.get(p.id, []))
            primary = works_items[0]
            items.append({
                "id": p.id,
                "name": p.name,
                "client_name": short_name or full_name or "",
                "completion_date": p.completion_date.isoformat() if p.completion_date else None,
                "public": primary["published"],
                "showcase_published": bool(works_map.get(p.id)) and primary["published"],
                "stage": primary["stage"],
                # N-now 上架驗收：rebuild 後對外頁實測 200 才 True（已上線 ✓ vs 驗證中）
                "verified": primary["verified"],
                "public_featured": primary["featured"],
                "slug": primary["slug"],
                "completeness": primary["completeness"],
                "works": works_items,
                "summary": project_works_summary(works_items),
            })

    return {"items": items}


@router.patch("/projects/{project_id}/website-stage")
async def update_project_website_stage(project_id: str, request: Request):
    """設定結案專案的官網製作階段（待製作/製作中/不上官網）。"""
    _check_website_auth(request)
    _require_db()
    factory = await _get_factory()

    body = await request.json()
    stage = (body.get("stage") or "").strip()
    if stage not in _WEBSITE_PROD_STAGES:
        raise HTTPException(status_code=400, detail="無效的製作階段")

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        project.website_prod_stage = stage
        project.updated_at = _now()
        # 1:N：階段正本移往作品層 — 主作品存在就同步寫（結案看板讀 sc.prod_stage）
        sc = await session.get(CrmProjectShowcase, project_id)
        if sc:
            sc.prod_stage = stage
            sc.updated_at = _now()
        await session.commit()

    return {"ok": True, "stage": stage}


async def _rename_asset_folders(session, project) -> list:
    """專案改名 → 各資產子系統的資料夾跟著改名（2026-08-06 owner 要求）。

    回 warning 清單（改名失敗的原因）。**專案名本身照樣改成功** —— 資料夾
    改不動（被開著、NAS 斷線）不該連帶讓改名失敗，下次改名會再試一次。
    子系統各自負責自己的實體 rename + DB 路徑同步（同一個 session/交易）。
    新增資產子系統時在這裡加一行。"""
    from routers.crm import media_log, proposal_assets
    warnings = []
    created = project.created_at or _now()
    for label, fn in (("影像紀錄", media_log.rename_project_folder),
                      ("提案", proposal_assets.rename_project_folder)):
        try:
            _changed, err = await fn(session, project.id, project.name or "", created)
        except Exception as e:      # 單一子系統壞掉不擋改名，也不擋其他子系統
            err = f"{type(e).__name__}: {e}"
        if err:
            # 狀態尾句（維持舊名／已改回／需人工處理）由 rename_and_remap 給 ——
            # 只有它知道補償結果，呼叫端不該自己斷言
            warnings.append(f"{label}資料夾改名失敗：{err}")
    return warnings


async def _latest_proposal_status(session, project_id: str) -> str:
    """專案的衛星提案子狀態（N:1 取最近更新的一筆；無衛星回 ''）。
    單筆端點與列表回應形狀對齊 —— 前端「未成案要問原因」的守門讀這個欄，
    只在列表附掛的話，任何用單筆回應覆寫 state 的路徑都會讓守門靜默失效。"""
    from db.models import PreprodProposal
    return (await session.execute(
        select(PreprodProposal.status)
        .where(PreprodProposal.project_id == project_id)
        .order_by(PreprodProposal.updated_at.desc())
        .limit(1)
    )).scalar() or ""


async def project_wire(session, project, request=None) -> dict:
    """單筆專案的回應形狀（_to_project_dict ＋ 客戶代稱 ＋ proposal_status）。

    單筆 GET／PUT／PATCH status 與手機版詳情四處共用 —— 各自拼一次的話，
    哪天多一個鍵（例如 proposal_status 當初）就會有一條路徑漏掉。
    例外：create_project／duplicate_project 回的是不含 proposal_status 的單筆形狀
    ——刻意的，它們的回應在 session 外組（剛建的案子也還沒有提案可查）。
    """
    client = await session.get(Client, project.client_id) if project.client_id else None
    receipts = await linked_receipts_map(session, [project.id]) if (project.entity or "parent") != "mine" else {}
    # mirrored：同清單那條可見性線（看不到私帳的人一律 False）。原本這裡不算，PUT 回來的單筆把列表上的
    # 「已連結私帳」標籤洗掉（2026-09-06 review）
    mirrored, mine_link = False, None
    if request is not None and (project.entity or "parent") != "mine" and not _hide_mine(request):
        t = await resolve_mine_link(session, project)
        mirrored, mine_link = t is not None, ((t.id, t.name or "") if t is not None else None)
    return {**_to_project_dict(project, client.short_name if client else "", mirrored=mirrored, receipts=receipts,
                               mine_link=mine_link),
            "proposal_status": await _latest_proposal_status(session, project.id)}


@router.get("/projects/{project_id}")
async def get_project(project_id: str, request: Request):
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        # 私帳案對沒有 mine scope 的人不存在 —— 回 404 不是 403，
        # 403 等於承認「有這個案子只是你不能看」，那本身就是洩漏。
        if (project.entity or "parent") == "mine" and _hide_mine(request):
            raise HTTPException(status_code=404, detail="找不到此專案")
        return await project_wire(session, project, request)


def _apply_status_side_effects(project, old_status: str) -> None:
    """狀態寫入的共用副作用（PUT + PATCH 都呼叫 — 避免兩條路徑漂移）：
    1. 轉入「結案」且未指定官網製作階段 → 預設「待製作」（進上架收件匣）。
    2. 剛轉入「結案」→ 推播官網編輯（fire-and-forget thread，通知失敗/未設
       webhook 都不可影響專案更新）。
    CSV 批次匯入刻意不呼叫此函式（不想大量匯入時洗版通知）。"""
    just_entered_closing = old_status != _CLOSING_STATUS and project.status == _CLOSING_STATUS
    if project.status == _CLOSING_STATUS and project.website_prod_stage is None:
        project.website_prod_stage = "待製作"
    if not just_entered_closing:
        return
    try:
        import threading
        from notifier import notify_tab
        threading.Thread(
            target=notify_tab, args=("project_closing",),
            kwargs={"project_name": project.name or ""}, daemon=True,
        ).start()
    except Exception:
        pass


# 提案=專案合體（2026-08-06）：專案階段轉換是 win/loss 的**單一觸發點**。
# 進 win 階段 = 成案；轉「未成案」= 未成案（強制附 outcome_reason，組織
# 學習欄）。狀態集合正本在 routers/api_proposals.py「提案=專案合體」區。


async def _sync_linked_proposals(session, project, old_status: str,
                                 reason: str | None) -> None:
    """專案狀態變動 → 連結的提案衛星列記 win/loss。PUT 與 PATCH /status
    共用（比照 _apply_status_side_effects，避免兩條路徑漂移）；提案側的
    _sync_project_from_proposal 換完階段也回頭呼叫這裡同步兄弟提案。
    轉「未成案」且提案沒有原因、本次也沒帶 → 422（session 尚未 commit，
    整筆狀態變更一起擋下）。

    🔴 **成案只在「這個專案只掛一筆提案」時自動標**（owner 2026-08-10）。
    一個專案可以並行多筆提案（同一案提了三個 concept），專案進「製作」時
    只有一個會贏 —— 無差別把每一筆都標成「成案」會讓成案率虛胖，還會把同一句
    成案原因灌進沒贏的那幾筆，而 win/loss 原因正是這個模組存在的理由。
    多筆時**誰贏由專案負責人指定**（專案詳情的提案切換列上直接標）。

    反方向不受影響：轉「未成案」照樣全部標 —— 整案沒拿到就是每個 concept
    都沒拿到，那個推論成立。"""
    new_status = project.status or ""
    if new_status == old_status:
        return
    from core.project_flow import wins_proposal
    from db.models import PreprodProposal
    props = (await session.execute(
        select(PreprodProposal).where(PreprodProposal.project_id == project.id)
    )).scalars().all()
    if not props:
        return
    now = _now()
    reason = (reason or "").strip()
    for prop in props:
        # 與「推進對話框問不問成案原因」共用同一份判定（見該函式 docstring）
        if wins_proposal(new_status, len(props), prop.status == "成案"):
            prop.status = "成案"
            if reason:
                prop.outcome_reason = reason
            prop.updated_at = now
        elif new_status == "未成案" and prop.status != "未成案":
            effective = reason or (prop.outcome_reason or "").strip()
            if not effective:
                raise HTTPException(status_code=422, detail={
                    "code": "OUTCOME_REASON_REQUIRED",
                    "message": "此專案來自提案 — 轉「未成案」需附原因（組織學習欄）"})
            prop.status = "未成案"
            prop.outcome_reason = effective
            prop.updated_at = now


@router.put("/projects/{project_id}")
async def update_project(project_id: str, req: CrmProjectPatchPayload, request: Request):
    """Partial update — only fields the client included in the body are
    touched. The cell-by-cell auto-save sends just the dirty field, so a
    full-payload schema would 422 on missing name/client_id.

    守衛（2026-09-08，蘇家弘存「編輯專案」被 403）：專案本體的**改**跟建／刪同一把 `crm_projects`
    （owner 2026-08-15 拍板「專案本體與派工的增刪 —— 模組級」，當時只改了 POST／DELETE，PUT 留在
    管理員限定 —— 有模組的人看得到編輯視窗、按儲存就「權限不足」，又是「權限是空頭支票」那個形狀）。
    例外兩條照舊：**推進階段**仍是 core.project_flow.ADVANCE_MODULES 的政策（目前＝管理員），
    表單整包送上來的 status 只要跟現況不同就再過一次那道門；私帳案的金額欄仍只有帳本主人能寫。"""
    _check_project_write_auth(request)
    _require_db()
    factory = await _get_factory()

    date_fields = {"shoot_date", "start_date", "completion_date"}
    update_data = req.model_dump(exclude_unset=True)
    # 逐格 autosave 只送 dirty 欄 → normalize 只碰有送上來的那幾根（`f in data` 判斷）
    root_warnings = normalize_backup_roots(update_data)

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")

        # 階段變更走 PATCH /status 的同一道門（ADVANCE_MODULES）：編輯視窗整包送 status，
        # 沒改就放行；改了才要求推進階段的權限，不然 PUT 就是繞過那條政策的後門。
        if "status" in update_data and (update_data["status"] or "") != (project.status or ""):
            _check_status_auth(request)

        # 🔴 私帳案的金額欄只有 mine scope 能寫。推送進管線（crm_pushed）後
        # 同事看得到這一列 —— 讀那側金額有抹（redact_mine），寫這側原本是零
        # 守衛：逐格 autosave 平常只送 dirty 欄沒事，但誰都能手動對私帳案送
        # contract_amount，而且他看到的欄位是被抹成空白的。非金額欄（階段/
        # 名稱/派工/描述）照「專案共用」的拍板不擋。
        if (project.entity or "parent") == "mine":
            from core.money import MONEY_FIELDS, viewer_has_mine_scope
            touched = set(update_data) & MONEY_FIELDS
            if touched and not viewer_has_mine_scope(request):
                raise HTTPException(status_code=403,
                                    detail="私帳案的金額欄只有帳本主人能修改")

        # If client_id is changing, validate the new client exists.
        # 空值 = 清空客戶（合體後合法：提案殼專案還沒定客戶）→ 正規化成 None。
        if "client_id" in update_data:
            update_data["client_id"] = (update_data["client_id"] or "").strip() or None
            if update_data["client_id"]:
                new_client = await session.get(Client, update_data["client_id"])
                if not new_client:
                    raise HTTPException(status_code=404, detail="找不到指定的客戶")

        old_status = project.status or ""
        old_client_id = project.client_id
        old_name = project.name or ""
        # outcome_reason 不是專案欄位 — 是轉「未成案/成案」時給提案衛星列的
        outcome_reason = update_data.pop("outcome_reason", None)
        for k, v in update_data.items():
            if k in date_fields:
                setattr(project, k, _parse_shoot_date(v))
            else:
                setattr(project, k, v)
        if "contract_amount" in update_data:
            project.contract_amount_source = None      # 人填了真合約 → 佔位旗標（母私帳對齊抄來的下限）解除
        _apply_status_side_effects(project, old_status)
        await _sync_linked_proposals(session, project, old_status, outcome_reason)
        # 以母帳為準（owner 2026-09-12）：母帳改了識別欄，連著的私帳案同一交易跟上
        # （1:1 才跟，N:1 不猜 —— 判定在 _sync_pair）
        if ((project.entity or "parent") != "mine"
                and any(k in update_data for k in LINK_SYNC_FIELDS)):
            linked_mine = await resolve_mine_link(session, project)
            if linked_mine is not None:
                # 這次親手送上來的欄位不做「母帳空白就拿私帳的補」：清結案日＝重開案，
                # 被私帳補回去等於存不進去而且回應還說改好了
                await _sync_pair(session, project, linked_mine, no_fill=set(update_data))
        project.updated_at = _now()
        # 專案狀態或歸屬客戶變動 → 重算客戶分級（含轉移前的舊客戶）
        await _auto_update_client_status(session, project.client_id)
        if old_client_id and old_client_id != project.client_id:
            await _auto_update_client_status(session, old_client_id)
        # 專案改名 → 資產資料夾跟著改名（同一交易，路徑同步更新）
        warnings = ([] if (project.name or "") == old_name
                    else await _rename_asset_folders(session, project))
        await session.commit()
        await session.refresh(project)
        wire = await project_wire(session, project, request)

    result = {"status": "ok", "project": wire}
    if warnings or root_warnings:
        result["warning"] = "；".join(warnings + root_warnings)
    return result


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, request: Request):
    _check_project_write_auth(request)
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        client_id = project.client_id
        await session.delete(project)
        await _auto_update_client_status(session, client_id)
        await session.commit()
    return {"status": "ok"}


async def apply_project_status(session, project, new_status: str,
                               outcome_reason: str | None = None) -> None:
    """改專案階段的**單一正本**（不 commit，caller 統一）：寫 status →
    結案副作用 → 衛星提案 win/loss → updated_at → 客戶分級重算。

    桌機 PATCH /projects/{id}/status 與手機版「簽回報價順便啟動專案」
    （routers/api_crm_mobile.py）都走這裡。拆出來的理由跟
    `_apply_status_side_effects` 當初一樣：兩條路徑各抄一份，第二條就會漏掉
    其中一個副作用（例如只改了 status 沒標成案，成案率就此虛低）。
    未成案缺原因時由 `_sync_linked_proposals` 丟 422，整筆變更一起擋下。
    """
    old_status = project.status or ""
    project.status = new_status
    _apply_status_side_effects(project, old_status)
    await _sync_linked_proposals(session, project, old_status, outcome_reason)
    project.updated_at = _now()
    # 狀態變動可能跨越『有效專案』門檻（如 洽詢→製作）→ 重算客戶分級
    await _auto_update_client_status(session, project.client_id)


@router.patch("/projects/{project_id}/status")
async def update_project_status(project_id: str, request: Request):
    _check_status_auth(request)          # 政策：core.project_flow.ADVANCE_MODULES
    _require_db()
    factory = await _get_factory()

    body = await request.json()
    new_status = body.get("status", "")
    if new_status not in ("投標", "開發", "洽詢", "提案", "製作", "結案", "歸檔", "未成案"):
        raise HTTPException(status_code=400, detail="無效的狀態值")

    contract_amount = body.get("contract_amount")
    amount_receivable = body.get("amount_receivable")

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        await apply_project_status(session, project, new_status,
                                   body.get("outcome_reason"))
        if contract_amount is not None:
            project.contract_amount = int(contract_amount)
            project.contract_amount_source = None
        if amount_receivable is not None:
            project.amount_receivable = int(amount_receivable)
        await session.commit()
        await session.refresh(project)
        wire = await project_wire(session, project, request)

    return {"status": "ok", "project": wire}


# ── Project CSV Import ──────────────────────────────────────

_PROJECT_COL_MAP = {
    "name":           ["專案名稱", "name", "案名", "專案", "project_name", "project"],
    "client_name":    ["客戶", "客戶代稱", "client", "client_name"],
    "status":         ["狀態", "status"],
    "am_username":    ["am", "業務", "am_username"],
    "shoot_date":     ["拍攝日期", "拍攝日", "shoot_date", "拍攝"],
    "folder_path":    ["資料夾", "folder", "folder_path", "路徑"],
    "description":    ["說明", "描述", "description"],
    "notes":          ["備註", "notes"],
}


def _map_project_row(header_map: dict, row: dict) -> dict:
    return map_csv_row(_PROJECT_COL_MAP, header_map, row)


@router.post("/projects/import_csv")
async def import_projects_csv(request: Request, file: UploadFile = File(...)):
    _check_auth(request)
    _require_db()

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("big5", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    header_map = {h.lower(): h for h in headers}
    imported = updated = skipped = 0

    factory = await _get_factory()

    async with factory() as session:
        clients = (await session.execute(select(Client))).scalars().all()
        client_map = {c.short_name: c.id for c in clients}

        existing_map: dict = {
            p.name: p
            for p in (await session.execute(select(CrmProject))).scalars().all()
        }

        affected: set = set()
        for row in reader:
            data = _map_project_row(header_map, row)
            if not data.get("name"):
                skipped += 1
                continue

            client_name = data.pop("client_name", "")
            client_id = client_map.get(client_name, "")
            shoot_dt = _parse_shoot_date(data.pop("shoot_date", None))

            now = _now()
            existing = existing_map.get(data["name"])
            if existing:
                if existing.client_id:
                    affected.add(existing.client_id)   # 轉移前的舊客戶也要重算
                for k, v in data.items():
                    if k != "name" and v:
                        setattr(existing, k, v)
                if client_id:
                    existing.client_id = client_id
                    affected.add(client_id)
                if shoot_dt:
                    existing.shoot_date = shoot_dt
                existing.updated_at = now
                updated += 1
            else:
                if not client_id:
                    skipped += 1
                    continue
                proj_data = {k: v for k, v in data.items() if k != "name"}
                new_proj = CrmProject(
                    id=uuid.uuid4().hex, name=data["name"],
                    client_id=client_id, shoot_date=shoot_dt,
                    created_at=now, updated_at=now, **proj_data,
                )
                session.add(new_proj)
                existing_map[data["name"]] = new_proj
                affected.add(client_id)
                imported += 1

        # 匯入後重算受影響客戶分級（過去 CSV 匯入沒觸發 → 是舊資料卡在潛在客戶的主因）
        for cid in affected:
            if cid:
                await _auto_update_client_status(session, cid)
        await session.commit()

    return {"status": "ok", "imported": imported, "updated": updated, "skipped": skipped}


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
        passthrough = cur != "mine" and await _has_passthrough_invoice(session, project_id)
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
            d["source"] = source or ("代開發票" if await _has_passthrough_invoice(session, project_id)
                                    else "源日")
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

    contract = _mirror_contract(p, mir, source)
    d = mirror_detail(mir["split"])
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
    from core.ledger_project import (MIRROR_SOURCE, MIRROR_TOTAL_KEY, SELECTABLE_SOURCES,
                                     apply_source_fee, merge_split, norm_detail)
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
            if (mode in ("overwrite", "add") and not mir["total"]
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


async def _sync_pair(session, parent, mine, *, no_fill=()) -> bool:
    """對一對已連結的案套「以母帳為準」。回有沒有真的改到東西。

    純規則在 `sync_from_parent`；這裡只補它做不到的 I/O：N:1 判定（兩種連結形狀都認：
    一句 count 查母帳側的 mine_link_id ＋ 私帳側的 source_project_id）、以及**客戶**那一欄的兩個方向 —— 私帳客戶是 entity=mine 的
    Client，跟母帳客戶是兩筆：私帳那筆已經連到（`crm_link_id`）母帳這筆的話兩邊
    **就是同一個客戶**，不改（否則推送到母帳的當下私帳案的客戶就被換成母帳那筆，
    私帳自己的客戶檔／匯款資訊從此對不上這一案）；母帳空白才反向對到／建出母帳客戶。
    `no_fill`：母帳 PUT 親手送上來的欄位，不做「空白就拿私帳的補」。
    """
    from core.ledger_project import sync_from_parent

    # 除了這一案之外還有沒有別的母帳案連著它（兩種形狀都看）
    other = (await session.execute(
        select(_sa_func.count()).select_from(CrmProject)
        .where(CrmProject.mine_link_id == mine.id, CrmProject.id != parent.id))).scalar() or 0
    if other or (mine.source_project_id and mine.source_project_id != parent.id):
        return False                                   # N:1：不猜
    skip = set()
    if parent.client_id and mine.client_id and parent.client_id != mine.client_id:
        mc = await session.get(Client, mine.client_id)
        if mc is not None and (mc.entity or "parent") == "mine" and mc.crm_link_id == parent.client_id:
            skip.add("client_id")                      # 同一個客戶的兩本帳，不是不一致
    changed, filled = sync_from_parent(parent, mine, no_fill=no_fill, skip=skip)
    if not parent.client_id and mine.client_id and "client_id" not in no_fill:
        cid = await _crm_client_for(session, mine.client_id)
        if cid:
            parent.client_id = cid
            filled.append("client_id")
            await _auto_update_client_status(session, cid)
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
