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

from config import load_settings as _load_settings

from core.ledger import hide_mine_projects, not_mine
from core.schemas import (CrmProjectPayload, CrmProjectPatchPayload,
                          ProjectLedgerMovePayload)

from ._shared import (router, _check_auth, _check_status_auth, _check_project_write_auth,
                      _check_website_auth, _require_db,
                      _get_factory, _now, _parse_shoot_date,
                      _auto_update_client_status, map_csv_row)

try:
    from ._shared import (select, or_,
                          Client, CrmCashEntry, CrmInvoice, CrmPaymentRequest,
                          CrmProject, CrmProjectCostGroup,
                          CrmProjectCostLine, CrmProjectExpense,
                          CrmProjectStaff, CrmProjectShowcase)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

# ── Project Helpers ─────────────────────────────────────────

def _to_project_dict(p, client_short_name: str = "") -> dict:
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
        "description": p.description or "", "notes": p.notes or "",
        # 兩本帳 §8：錢流歸屬。這個鍵同時是 core/money mine-aware 抹除的
        # 判定依據（entity=='mine' 的物件，金額鍵對無 mine scope 者整棵抹掉）
        "entity": p.entity or "parent",
        "crm_pushed": int(p.crm_pushed or 0),
        "contract_amount": p.contract_amount,
        "tax_rate": p.tax_rate, "profit_target_pct": p.profit_target_pct,
        "misc_budget_pct": p.misc_budget_pct,
        "payment_status": p.payment_status or "未到帳",
        "amount_receivable": p.amount_receivable,
        "amount_received": p.amount_received,
        "transfer_fee": p.transfer_fee,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


# 私帳案的可見性規則在 core.ledger.hide_mine_projects（取名字才 grep 得到）。
_hide_mine = hide_mine_projects


# ── Project Endpoints ───────────────────────────────────────

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

    return {
        "projects": [
            {**_to_project_dict(p, cname or ""), "proposal_status": ps or ""}
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
    _check_auth(request)
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
        client = await session.get(Client, project.client_id)
        client_name = client.short_name if client else ""
        prop_status = await _latest_proposal_status(session, project_id)

    return {**_to_project_dict(project, client_name),
            "proposal_status": prop_status}


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
    full-payload schema would 422 on missing name/client_id."""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()

    date_fields = {"shoot_date", "start_date", "completion_date"}
    update_data = req.model_dump(exclude_unset=True)

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")

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
        _apply_status_side_effects(project, old_status)
        await _sync_linked_proposals(session, project, old_status, outcome_reason)
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
        client = await session.get(Client, project.client_id)
        client_name = client.short_name if client else ""
        prop_status = await _latest_proposal_status(session, project_id)

    result = {"status": "ok",
              "project": {**_to_project_dict(project, client_name),
                          "proposal_status": prop_status}}
    if warnings:
        result["warning"] = "；".join(warnings)
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
        old_status = project.status or ""
        project.status = new_status
        _apply_status_side_effects(project, old_status)
        await _sync_linked_proposals(session, project, old_status,
                                     body.get("outcome_reason"))
        if contract_amount is not None:
            project.contract_amount = int(contract_amount)
        if amount_receivable is not None:
            project.amount_receivable = int(amount_receivable)
        project.updated_at = _now()
        # 狀態變動可能跨越『有效專案』門檻（如 洽詢→製作）→ 重算客戶分級
        await _auto_update_client_status(session, project.client_id)
        await session.commit()
        await session.refresh(project)
        client = await session.get(Client, project.client_id)
        client_name = client.short_name if client else ""
        prop_status = await _latest_proposal_status(session, project_id)

    return {"status": "ok",
            "project": {**_to_project_dict(project, client_name),
                        "proposal_status": prop_status}}


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


async def _ledger_blockers(session, project_id: str) -> list:
    """這個專案身上掛了哪些錢。回 [(中文名, 筆數), …]，空＝可以搬。"""
    from .flow import _count_sq

    row = (await session.execute(select(*[
        _count_sq(M, M.project_id == project_id).label(f"c{i}")
        for i, (M, _label) in enumerate(_LEDGER_BLOCKERS)]))).one()
    return [(label, int(n)) for n, (_M, label) in zip(row, _LEDGER_BLOCKERS) if n]


@router.get("/projects/{project_id}/ledger-move-check")
async def check_project_ledger_move(project_id: str, request: Request):
    """能不能搬、搬過去會變怎樣 —— 按鈕按下去之前先問這支。

    把「會擋住的東西」講出來，而不是讓使用者按了才吃一個 409。
    """
    from core.ledger import require_entity

    require_entity(request, "mine", level="full")
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        p = await session.get(CrmProject, project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="找不到此專案")
        blockers = await _ledger_blockers(session, project_id)
        cur, name = p.entity or "parent", p.name
    return {"entity": cur, "target": "parent" if cur == "mine" else "mine",
            "can_move": not blockers,
            "blockers": [{"what": w, "count": n} for w, n in blockers],
            # 擋人的那句話只寫一次 —— 前端直接顯示它，不再自己拼一份
            "reason": _blocked_reason(name, blockers),
            "name": name}


def _blocked_reason(name: str, blockers) -> str:
    """為什麼不能搬。check 端點與 POST 的 409 共用同一句。"""
    if not blockers:
        return ""
    what = "、".join(f"{w} {n} 筆" for w, n in blockers)
    return (f"「{name}」身上已經記了錢（{what}），不能換帳本 —— "
            f"錢會留在原本那本，帳目就對不起來了。請先處理那些單據")


@router.post("/projects/{project_id}/move-ledger")
async def move_project_ledger(project_id: str, req: ProjectLedgerMovePayload,
                              request: Request):
    """把專案搬到另一本帳。**只有私帳 full scope 的帳號能按**（兩個方向都是）。

    搬到私帳時順手把 `crm_pushed` 設成 1 —— 否則專案管理的預設檢視
    （entity=parent）當場看不到它，使用者會以為案子不見了。搬回母公司時清掉。
    """
    from core.ledger import ENTITIES, require_entity

    require_entity(request, "mine", level="full")
    target = (req.entity or "").strip()
    if target not in ENTITIES:
        raise HTTPException(status_code=422, detail=f"未知的帳本: {target or '(空)'}")
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
        p.entity = target
        # 搬到私帳的案子預設仍要在專案管理看得到（標「後期專案」）
        pushed = 1 if target == "mine" else 0
        p.crm_pushed = pushed
        p.updated_at = _now()
        await session.commit()
        # 🔴 is_mine_project 有 60 秒 id-set 快取，而這裡是全 repo 唯一的 entity
        # 寫入路徑 —— 不清掉的話，剛搬過去的案子最多一分鐘內還被當成母公司的。
        from core.ledger import invalidate_mine_projects
        invalidate_mine_projects()
    return {"status": "ok", "entity": target, "crm_pushed": pushed}
