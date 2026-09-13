"""routers/crm/projects.py — 專案管理 + 結案看板 + CSV 匯入。

自 routers/api_crm.py 原樣搬移（純搬移，行為不變）。
2026-09-12：換帳本／推送（分身）／母私帳對應表那一段切去 `project_links.py`（對應表 2026-09-13 再切去 `project_map.py`）；
2026-09-13：結案看板／官網階段／工項切去 `project_closing.py`（**要在本檔之前註冊**，見 import 那行）、CSV 匯入切去 `project_import.py`
（本檔 1,979 行離單次讀取上限 21 行）；連結的**判定**（is_mirrored／resolve_mine_link／
mine_link_map）留在這裡，清單與單筆都要用。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import uuid

from fastapi import HTTPException, Request, Query

from config import load_settings as _load_settings

from core.ledger import hide_mine_projects, not_mine
from core.ledger_project import BILLING_MODES, LINK_SYNC_FIELDS, billing_mode_of, parent_receipt_fields
from core.project_link import invoice_project_ids as _inv_pids
from db.models import CrmCashSplit    # 拆項列（母帳收款歸案要看它；_shared 沒 re-export）
from core.schemas import CrmProjectPayload, CrmProjectPatchPayload, ProjectTypeOpPayload

from ._shared import (router, _check_status_auth, _check_project_write_auth,
                      _require_db, _get_factory, _now, _parse_shoot_date,
                      _auto_update_client_status)
# 🔴 要在本檔的 `/projects/{project_id}` 之前 import：那支 `/projects/closing` 的路由靠 import 順序
# 排在前面（不然 "closing" 會被吃成 project_id）。常數的正本也在那裡。
from .project_closing import _CLOSING_STATUS  # noqa: E402

try:
    from ._shared import (select, or_,
                          Client, CrmCashEntry, CrmCashInvoiceLink, CrmInvoice,
                          CrmProject, CrmProjectCostGroup,
                          CrmProjectCostLine, CrmProjectExpense,
                          CrmProjectStaff)
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


def _check_billing_mode(data: dict) -> None:
    """`billing_mode` 有送就要是三值之一（None／沒送＝不動；空字串＝company）。"""
    if "billing_mode" not in data or data["billing_mode"] is None:
        return
    v = (data["billing_mode"] or "").strip() or "company"
    if v not in BILLING_MODES:
        raise HTTPException(status_code=422, detail=f"未知的收款方式：{data['billing_mode']}")
    data["billing_mode"] = v


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
        # 收款方式（owner 2026-09-13）：源日專案／後期代開／現金收款；NULL 回 company
        "billing_mode": billing_mode_of(p.billing_mode),
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
    _check_billing_mode(data)
    root_warnings = normalize_backup_roots(data)

    async with factory() as session:
        project, client_name = await create_project_in_session(session, data)
        # 收款方式＝後期代開 → 儲存即在私帳建對應的案（owner 2026-09-13）
        from .project_links import apply_billing_mode    # 那支頂層 import 本檔 → 只能在這裡 import
        billing = await apply_billing_mode(session, project, request, "company")
        await session.commit()
        await session.refresh(project)
    if billing.get("created"):
        from core.ledger import invalidate_mine_projects
        invalidate_mine_projects()

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

    result = {"status": "ok", "project": _to_project_dict(project, client_name), "billing": billing}
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
    from .project_links import link_note_for
    return {**_to_project_dict(project, client.short_name if client else "", mirrored=mirrored, receipts=receipts,
                               mine_link=mine_link),
            "proposal_status": await _latest_proposal_status(session, project.id),
            # 「後期連結」那一行系統備註（owner 2026-09-13）；看不到私帳的人只拿到收款方式那句
            "link_note": (await link_note_for(session, project, request)) if request is not None
                         else None}


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
    _check_billing_mode(update_data)
    if update_data.get("billing_mode") is None:
        update_data.pop("billing_mode", None)      # None＝沒送（舊分頁）→ 不動
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
        old_billing = project.billing_mode
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
                from .project_links import _sync_pair    # 那支頂層 import 本檔 → 只能在這裡 import
                # 這次親手送上來的欄位不做「母帳空白就拿私帳的補」：清結案日＝重開案，
                # 被私帳補回去等於存不進去而且回應還說改好了
                await _sync_pair(session, project, linked_mine, explicit=set(update_data))
        # 收款方式變了 → 私帳分身跟著（建／改案源／改回源日；規則在 project_links.apply_billing_mode）
        billing = {"action": "none", "created": False}
        # （值沒變也呼叫：同事先存了「後期代開」、帳本主人再存一次要把分身補建起來；沒事做它回 none）
        if "billing_mode" in update_data:
            from .project_links import apply_billing_mode
            billing = await apply_billing_mode(session, project, request, old_billing)
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
    if billing.get("created"):
        from core.ledger import invalidate_mine_projects
        invalidate_mine_projects()
    if billing.get("action") == "skipped":
        warnings.append("收款方式已存，但你看不到私帳 —— 私帳那邊的對應案要由帳本主人再存一次才會建／改")

    result = {"status": "ok", "project": wire, "billing": billing}
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
