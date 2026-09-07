"""routers/crm/quotes.py — 報價管理 + 報價範本。

自 routers/api_crm.py 原樣搬移（純搬移，行為不變）。
"""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from datetime import datetime

from fastapi import Depends, HTTPException, Request, Query
from fastapi.responses import HTMLResponse

from core.finance_logic import QUOTE_PENDING
from core.no_store import no_store_file
from core.quotation_pdf import PDF_MARGIN, build_quotation_view, footer_line
from core.schemas import QuotationPayload, QuotationTemplatePayload

from ._shared import (router, token_router, _check_auth, money_dep, _require_db, _get_factory,
                      _now, _parse_shoot_date)

try:
    from ._shared import (select, or_, delete, IntegrityError,
                          Client, CrmProject, CrmQuotation, CrmQuotationItem,
                          CrmQuotationTemplate)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

# ── Quotation Helpers ───────────────────────────────────────

def _calc_quotation(items_data: list, discount: int, tax_rate: int):
    """Calculate subtotal/tax/total from items."""
    for it in items_data:
        it["amount"] = it["quantity"] * it["unit_price"]
    subtotal = sum(it["amount"] for it in items_data)
    taxable = max(subtotal - discount, 0)
    tax_amount = int(taxable * tax_rate / 100)
    total = taxable + tax_amount
    return subtotal, tax_amount, total


def _to_quotation_dict(q, items=None, project_name="", client_short_name="") -> dict:
    return {
        "id": q.id, "project_id": q.project_id,
        "project_name": project_name, "client_short_name": client_short_name,
        "version": q.version, "status": q.status or "草稿",
        "quote_date": q.quote_date.isoformat() if q.quote_date else None,
        "valid_until": q.valid_until.isoformat() if q.valid_until else None,
        "subtotal": q.subtotal, "discount": q.discount,
        "tax_rate": q.tax_rate, "tax_amount": q.tax_amount,
        "total": q.total, "final_price": q.final_price,
        "payment_stages": q.payment_stages or [], "terms": q.terms or "",
        "spec": q.spec or "",
        "share_url": f"/q/{q.share_token}" if getattr(q, "share_token", None) else None,
        "items": items or [],
        "created_at": q.created_at.isoformat() if q.created_at else None,
        "updated_at": q.updated_at.isoformat() if q.updated_at else None,
    }


def _item_to_dict(it) -> dict:
    return {
        "id": it.id, "group_name": it.group_name or "",
        "sort_order": it.sort_order, "description": it.description,
        "unit": it.unit, "quantity": it.quantity,
        "unit_price": it.unit_price, "amount": it.amount,
        "internal_cost": it.internal_cost, "note": it.note or "",
    }


async def project_quotation_rows(session, project_id: str) -> list:
    """某專案的全部報價列（version desc）—— 本檔的列表端點與提案的報價分頁
    （proposal_quotes）共用同一條 query。"""
    if not project_id:
        return []
    return (await session.execute(
        select(CrmQuotation).where(CrmQuotation.project_id == project_id)
        .order_by(CrmQuotation.version.desc())
    )).scalars().all()


async def _load_items(session, quotation_id: str) -> list:
    result = await session.execute(
        select(CrmQuotationItem)
        .where(CrmQuotationItem.quotation_id == quotation_id)
        .order_by(CrmQuotationItem.sort_order)
    )
    return [_item_to_dict(it) for it in result.scalars().all()]


async def _save_items(session, quotation_id: str, items: list):
    """Delete existing items, insert new ones. Returns items_data for calc."""
    await session.execute(delete(CrmQuotationItem).where(CrmQuotationItem.quotation_id == quotation_id))

    items_data = []
    for i, it in enumerate(items):
        d = it.model_dump() if hasattr(it, 'model_dump') else dict(it)
        d["amount"] = d["quantity"] * d["unit_price"]
        items_data.append(d)
        session.add(CrmQuotationItem(
            id=uuid.uuid4().hex, quotation_id=quotation_id, sort_order=i,
            group_name=d.get("group_name", ""), description=d["description"],
            unit=d.get("unit", "式"), quantity=d["quantity"],
            unit_price=d["unit_price"], amount=d["amount"],
            internal_cost=d.get("internal_cost", 0), note=d.get("note", ""),
        ))
    return items_data


# ── Quotation Endpoints ─────────────────────────────────────

@router.get("/quotations", dependencies=[Depends(money_dep)])
async def list_all_quotations(
    q: str = Query(""), status: str = Query(""),
    client_id: str = Query(""), am: str = Query(""),
):
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        query = (
            select(CrmQuotation, CrmProject.name.label("pn"), Client.short_name.label("cn"))
            .outerjoin(CrmProject, CrmProject.id == CrmQuotation.project_id)
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .order_by(CrmQuotation.updated_at.desc())
        )
        if status:
            query = query.where(CrmQuotation.status == status)
        if client_id:
            query = query.where(CrmProject.client_id == client_id)
        if am:
            query = query.where(CrmProject.am_username == am)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(CrmProject.name.ilike(ql), Client.short_name.ilike(ql)))
        rows = (await session.execute(query)).all()

    return {
        "quotations": [_to_quotation_dict(r[0], project_name=r[1] or "", client_short_name=r[2] or "") for r in rows],
        "total": len(rows),
    }


@router.get("/quotations/stats", dependencies=[Depends(money_dep)])
async def quotation_stats():
    _require_db()
    factory = await _get_factory()
    now = _now()
    month_start = datetime(now.year, now.month, 1, tzinfo=now.tzinfo)

    from sqlalchemy import func as sa_func, case

    async with factory() as session:
        price_col = sa_func.coalesce(CrmQuotation.final_price, CrmQuotation.total, 0)
        row = (await session.execute(
            select(
                sa_func.count().label("total_count"),
                sa_func.sum(case((CrmQuotation.status == QUOTE_PENDING, 1), else_=0)).label("pending"),
                sa_func.sum(case((CrmQuotation.status == "已簽核", 1), else_=0)).label("signed"),
                sa_func.sum(case((CrmQuotation.created_at >= month_start, price_col), else_=0)).label("month_total"),
            )
        )).one()

    total_count = row.total_count or 0
    signed = row.signed or 0
    return {
        "month_total": row.month_total or 0, "pending_count": row.pending or 0,
        "sign_rate": round(signed / total_count * 100) if total_count > 0 else 0,
        "total_count": total_count,
    }


@router.get("/projects/{project_id}/quotations", dependencies=[Depends(money_dep)])
async def list_project_quotations(project_id: str):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = await project_quotation_rows(session, project_id)
    return {"quotations": [_to_quotation_dict(q) for q in rows], "total": len(rows)}


@router.post("/projects/{project_id}/quotations")
async def create_quotation(project_id: str, req: QuotationPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    now = _now()

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")

        from sqlalchemy import func as sa_func
        max_ver = (await session.execute(
            select(sa_func.max(CrmQuotation.version)).where(CrmQuotation.project_id == project_id)
        )).scalar()
        version = (max_ver or 0) + 1

        q_id = uuid.uuid4().hex
        items_data = await _save_items(session, q_id, req.items)
        subtotal, tax_amount, total = _calc_quotation(items_data, req.discount, req.tax_rate)

        q = CrmQuotation(
            id=q_id, project_id=project_id, version=version,
            status=req.status, quote_date=_parse_shoot_date(req.quote_date),
            valid_until=_parse_shoot_date(req.valid_until),
            subtotal=subtotal, discount=req.discount, tax_rate=req.tax_rate,
            tax_amount=tax_amount, total=total, final_price=req.final_price,
            payment_stages=req.payment_stages or None, terms=req.terms,
            spec=req.spec or None,
            created_at=now, updated_at=now,
        )
        session.add(q)
        await session.commit()
        await session.refresh(q)
        loaded_items = await _load_items(session, q.id)

    return {"status": "ok", "quotation": _to_quotation_dict(q, items=loaded_items)}


@router.get("/quotations/{quotation_id}", dependencies=[Depends(money_dep)])
async def get_quotation(quotation_id: str):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        q = await session.get(CrmQuotation, quotation_id)
        if not q:
            raise HTTPException(status_code=404, detail="找不到此報價")
        items = await _load_items(session, q.id)
        project = await session.get(CrmProject, q.project_id)
        pn = project.name if project else ""
        client = await session.get(Client, project.client_id) if project else None
        cn = client.short_name if client else ""
    return _to_quotation_dict(q, items=items, project_name=pn, client_short_name=cn)


async def _quotation_view_of(q) -> tuple[dict, dict]:
    """撈齊報價的項目／專案／客戶 → (檢視模型, company 設定)。PDF 與線上檢視三支端點共用。"""
    from config import load_settings
    factory = await _get_factory()
    async with factory() as session:
        items = await _load_items(session, q.id)
        project = await session.get(CrmProject, q.project_id)
        client = await session.get(Client, project.client_id) if project and project.client_id else None
    company = dict(load_settings().get("company") or {})
    client_name = ((client.full_name or "").strip() or client.short_name) if client else ""
    project_name = project.name if project else ""
    view = build_quotation_view(
        _to_quotation_dict(q, items=items, project_name=project_name,
                           client_short_name=client.short_name if client else ""),
        company, client_name=client_name, project_name=project_name)
    return view, company


def _render_quotation_html(view: dict, company: dict, *, web_pdf_url: str = "") -> str:
    """同一份模板：web_pdf_url 給了＝線上檢視（多一條「下載 PDF」列、頁面不撐 A4），沒給＝印 PDF。"""
    from services.html_pdf import file_data_uri, render_template
    return render_template(
        "quotation_pdf.html", v=view,
        logo_src=file_data_uri(company.get("logo_path") or "frontend/img/originsun-logo.webp"),
        seal_src=file_data_uri(company.get("seal_path") or ""),
        web_pdf_url=web_pdf_url,
    )


# ── 報價單資料夾（owner 2026-09-07「跟發票一樣有個地方指定儲存位置」）────────
# 骨架同電子發票根目錄：settings 可設 → {根}/{年}/{年-月}/{檔名}。差別：發票是使用者上傳、
# 報價單是系統產的 —— 所以「存檔」發生在每次產 PDF，同名覆蓋（同一天同一版重產不堆檔）。
_QUOTES_DEFAULT_ROOT = os.path.join(os.getcwd(), "uploads", "quotations")


def _quotes_root() -> str:
    from config import load_settings
    return (load_settings().get("quotes_root") or "").strip() or _QUOTES_DEFAULT_ROOT


def _archive_quotation_pdf(tmp_pdf: str, view: dict) -> str:
    """產好的 PDF 存一份到報價單資料夾；檔名開頭是 YYYYMMDD，年／年-月從它來。"""
    fn = view["filename"]
    dest_dir = os.path.join(_quotes_root(), fn[:4], f"{fn[:4]}-{fn[4:6]}")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, fn)
    shutil.copyfile(tmp_pdf, dest)
    return dest


@router.get("/quotations-root")
async def get_quotations_root(request: Request):
    """報價單資料夾設定（admin）。刻意不走 settings/load 整包（那條對機密欄位有遮罩規則，這裡只要一個路徑）。"""
    from core.auth import check_admin
    from config import load_settings
    check_admin(request)
    return {"quotes_root": (load_settings().get("quotes_root") or ""),
            "default": _QUOTES_DEFAULT_ROOT, "effective": _quotes_root()}


@router.post("/quotations-root")
async def set_quotations_root(request: Request):
    from core.auth import check_admin
    from config import load_settings, save_settings
    from .invoice_files import validate_root_dir          # 與發票根目錄同一份驗證（完整路徑＋可寫）
    check_admin(request)
    body = await request.json()
    root = (body.get("quotes_root") or "").strip()
    validate_root_dir(root)
    try:
        s = load_settings()
        s["quotes_root"] = root
        save_settings(s)
    except OSError as e:
        raise HTTPException(status_code=503, detail=f"設定檔忙碌中，請再按一次儲存（{e}）")
    return {"status": "ok", "quotes_root": root, "effective": _quotes_root()}


async def _quotation_pdf_response(q):
    """組資料／渲染／產 PDF 同一個出口：壞在哪一段對使用者都是「PDF 生成失敗」。"""
    import html as _html
    from starlette.background import BackgroundTask
    from services.html_pdf import html_to_pdf, unlink_later
    try:
        view, company = await _quotation_view_of(q)
        footer = (
            '<div style="width:100%;margin:0 16mm;font-family:\'Noto Sans TC\',\'Microsoft JhengHei\',sans-serif;'
            'font-size:7px;color:#767676;display:flex;justify-content:space-between;">'
            f'<span>{_html.escape(footer_line(view))}</span>'
            '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'
        )
        tmp_pdf = await html_to_pdf(_render_quotation_html(view, company), prefix="quotation_",
                                    footer_html=footer, margin=PDF_MARGIN)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF 生成失敗：{exc}")
    try:                                   # 存一份到報價單資料夾：資料夾不通只記 log，不擋下載
        _archive_quotation_pdf(tmp_pdf, view)
    except OSError as exc:
        logging.getLogger(__name__).warning("報價單存檔失敗（不影響下載）：%s", exc)
    return no_store_file(              # 金額文件：不留快取副本，送完就刪
        tmp_pdf, media_type="application/pdf", filename=view["filename"],
        background=BackgroundTask(unlink_later(tmp_pdf)),
    )


@router.get("/quotations/{quotation_id}/pdf", dependencies=[Depends(money_dep)])
async def quotation_pdf(quotation_id: str):
    """報價單 PDF。版面正本 frontend/demo/quotation-pdf.html（2026-09-06 定稿）→ templates/quotation_pdf.html；
    金額／備註／檔名規則在 core/quotation_pdf.py（純函式，有測試）。抬頭／匯款／章讀 settings.company。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        q = await session.get(CrmQuotation, quotation_id)
        if not q:
            raise HTTPException(status_code=404, detail="找不到此報價")
    return await _quotation_pdf_response(q)


@router.post("/quotations/{quotation_id}/share")
async def share_quotation(quotation_id: str, request: Request):
    """取得／鑄造線上檢視連結（冪等：已有就回同一條，寄出去的連結不會因為再按一次就失效）。
    短碼走 new_short_token（純亂數 12 字，網址短；驗證是逐字比對 DB，同電子發票 /e/{code}）。"""
    from core.auth import new_short_token
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        q = await session.get(CrmQuotation, quotation_id)
        if not q:
            raise HTTPException(status_code=404, detail="找不到此報價")
        if not q.share_token:
            q.share_token = new_short_token()
            q.updated_at = _now()
            await session.commit()
        token = q.share_token
    return {"status": "ok", "token": token, "share_url": f"/q/{token}"}


async def _quotation_by_share_token(token: str):
    """免登入端點的憑證就是網址裡那串字：share_token 逐字等於來訪者出示的字串才給。"""
    _require_db()
    if not token:
        raise HTTPException(status_code=401, detail="連結已失效")
    factory = await _get_factory()
    async with factory() as session:
        q = (await session.execute(
            select(CrmQuotation).where(CrmQuotation.share_token == token))).scalars().first()
    if not q:
        raise HTTPException(status_code=401, detail="連結已失效")
    return q


@token_router.get("/public/quote/{token}", response_class=HTMLResponse)
async def public_quote_html(token: str):
    """線上檢視（owner 2026-09-07）：同一份報價單版面直接當網頁看，頁上有「下載 PDF」。
    掛 token_router（master 限定、不對 NAS 曝露）；回的是 HTML 不是 JSON，MoneyRedactRoute 抹不到、
    也不該抹 —— 這頁就是要給客戶看金額的。"""
    q = await _quotation_by_share_token(token)
    view, company = await _quotation_view_of(q)
    html_doc = _render_quotation_html(view, company, web_pdf_url=f"/q/{token}/pdf")
    return HTMLResponse(html_doc, headers={"Cache-Control": "no-store"})


@token_router.get("/public/quote/{token}/pdf")
async def public_quote_pdf(token: str):
    """線上檢視頁的「下載 PDF」：一般導覽下載（不是 blob），手機也能直接存。"""
    q = await _quotation_by_share_token(token)
    return await _quotation_pdf_response(q)


@router.put("/quotations/{quotation_id}")
async def update_quotation(quotation_id: str, req: QuotationPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        q = await session.get(CrmQuotation, quotation_id)
        if not q:
            raise HTTPException(status_code=404, detail="找不到此報價")

        items_data = await _save_items(session, q.id, req.items)
        subtotal, tax_amount, total = _calc_quotation(items_data, req.discount, req.tax_rate)

        q.status = req.status
        q.quote_date = _parse_shoot_date(req.quote_date)
        q.valid_until = _parse_shoot_date(req.valid_until)
        q.subtotal = subtotal
        q.discount = req.discount
        q.tax_rate = req.tax_rate
        q.tax_amount = tax_amount
        q.total = total
        q.final_price = req.final_price
        q.payment_stages = req.payment_stages or None
        q.terms = req.terms
        if req.spec is not None:                 # 沒送＝不動（舊分頁的 PUT 不該洗掉規格）
            q.spec = req.spec or None
        q.updated_at = _now()
        await session.commit()
        await session.refresh(q)
        loaded_items = await _load_items(session, q.id)

    return {"status": "ok", "quotation": _to_quotation_dict(q, items=loaded_items)}


@router.delete("/quotations/{quotation_id}")
async def delete_quotation(quotation_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        q = await session.get(CrmQuotation, quotation_id)
        if not q:
            raise HTTPException(status_code=404, detail="找不到此報價")
        await session.execute(delete(CrmQuotationItem).where(CrmQuotationItem.quotation_id == q.id))
        await session.delete(q)
        await session.commit()
    return {"status": "ok"}


# ── Quotation Template Endpoints ────────────────────────────

@router.get("/quotation-templates", dependencies=[Depends(money_dep)])
async def list_templates():
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmQuotationTemplate).order_by(CrmQuotationTemplate.updated_at.desc())
        )).scalars().all()
    return {"templates": [{
        "id": t.id, "name": t.name, "description": t.description or "",
        "tax_rate": t.tax_rate, "terms": t.terms or "",
        "payment_stages": t.payment_stages or [],
        "items": t.items or [],
        "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in rows]}


@router.post("/quotation-templates")
async def create_template(req: QuotationTemplatePayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    now = _now()
    items = [it.model_dump() for it in req.items]
    t = CrmQuotationTemplate(
        id=uuid.uuid4().hex, name=req.name, description=req.description,
        tax_rate=req.tax_rate, terms=req.terms,
        payment_stages=req.payment_stages or None,
        items=items, created_at=now, updated_at=now,
    )
    async with factory() as session:
        session.add(t)
        try:
            await session.commit()
        except IntegrityError:
            raise HTTPException(status_code=409, detail=f"範本「{req.name}」已存在")
    return {"status": "ok", "template": {"id": t.id, "name": t.name}}


@router.put("/quotation-templates/{template_id}")
async def update_template(template_id: str, req: QuotationTemplatePayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        t = await session.get(CrmQuotationTemplate, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="找不到此範本")
        t.name = req.name
        t.description = req.description
        t.tax_rate = req.tax_rate
        t.terms = req.terms
        t.payment_stages = req.payment_stages or None
        t.items = [it.model_dump() for it in req.items]
        t.updated_at = _now()
        await session.commit()
    return {"status": "ok"}


@router.delete("/quotation-templates/{template_id}")
async def delete_template(template_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        t = await session.get(CrmQuotationTemplate, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="找不到此範本")
        await session.delete(t)
        await session.commit()
    return {"status": "ok"}

