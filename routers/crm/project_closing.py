"""routers/crm/project_closing.py — 完稿結案：結案看板清單（/projects/closing）、官網製作階段、專案的作品清單。

2026-09-13 從 projects.py 原樣切出（純搬移）。
🔴 `/projects/closing` 必須註冊在 projects.py 的 `/projects/{project_id}` 之前 —— 所以是 **projects.py 在檔頭 import 本檔**
（本檔的 decorator 先跑），不是 __init__ 排在它後面；本檔因此**不准** import projects（會成環）。
`_WEBSITE_PROD_STAGES`／`_CLOSING_STATUS` 的正本在這裡（projects／works 都從這裡拿）。
掃原始碼的測試用 `_srcscan.projects_src()`。
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from core.ledger import hide_mine_projects, not_mine

# 私帳案的可見性規則在 core.ledger.hide_mine_projects（同 projects.py 的別名）
_hide_mine = hide_mine_projects

from ._shared import router, _check_website_auth, _require_db, _get_factory, _now

try:
    from ._shared import select, Client, CrmProject, CrmProjectShowcase
except ImportError:  # DB 套件不存在的 agent 環境 — 同 projects.py
    pass


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
