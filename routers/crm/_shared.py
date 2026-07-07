"""routers/crm/_shared.py — CRM router 拆分共用層。

原 routers/api_crm.py（單檔 144 端點）已依領域拆分至 routers/crm/。
本檔持有：共用 router 單例（APIRouter 建構參數照抄原檔）、原檔頂部全部
import、以及被多個領域模組共用的 helpers / 常數（自原檔各 section 原樣搬入）。

搬移唯一調整：_UPLOAD_BASE / 各函式內以 __file__ 推專案根目錄的運算多包一層
os.path.dirname —— 檔案從 routers/ 移深一層到 routers/crm/，不加會讓
uploads/templates 路徑整體位移到 routers/ 底下（行為改變）。
"""
# Lazy annotations (PEP 563) — keeps function signatures parsing on agents
# without sqlalchemy / asyncpg installed. db.models classes referenced as
# type hints would otherwise NameError when the optional DB import branch
# fails (per the try/except below), preventing the whole CRM router from
# loading. See bug: agent 192.168.1.5 had this exact failure mode.
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

import core.state as state

try:
    from sqlalchemy import select, or_, delete, update as sa_update
    from sqlalchemy.exc import IntegrityError
    from db.models import (Client, User, CrmProject, CrmQuotation, CrmQuotationItem,
                           CrmQuotationTemplate, CrmStaff, CrmStaffPortfolio,
                           CrmProjectStaff, CrmProjectExpense,
                           CrmInvoice, CrmPaymentRequest, CrmCashEntry,
                           CrmProjectCostLine, CrmCostLineTemplate,
                           CrmProjectCostGroup,
                           CrmProjectShowcase,
                           WEBSITE_TEAM_OVERRIDE_FIELDS)
    _HAS_DB = True
except ImportError:
    _HAS_DB = False

# 本檔自用只有 select / Client / CrmProject / CrmProjectExpense；
# 其餘 db.models 類與 or_ / delete / IntegrityError 是給領域模組
# `from ._shared import ...` 的 re-export（列進 __all__，ruff F401 視為已使用）。
__all__ = [
    "router", "or_", "delete", "sa_update", "IntegrityError", "User",
    "CrmQuotation", "CrmQuotationItem", "CrmQuotationTemplate",
    "CrmStaff", "CrmStaffPortfolio", "CrmProjectStaff",
    "CrmInvoice", "CrmPaymentRequest", "CrmCashEntry",
    "CrmProjectCostLine", "CrmCostLineTemplate", "CrmProjectCostGroup",
    "CrmProjectShowcase", "WEBSITE_TEAM_OVERRIDE_FIELDS",
]

router = APIRouter(prefix="/api/v1/crm", tags=["CRM"])


# ── Helpers ──────────────────────────────────────────────────

def _check_auth(request: Request):
    try:
        from core.auth import check_admin
        check_admin(request)
    except ImportError:
        pass


def _check_website_auth(request: Request):
    """官網製作授權 — 管理員 OR 擁有 website_admin 模組即可（不需全域 admin）。

    給「結案製作」看板 + showcase 編輯端點用：非管理員的官網製作人員只要帳號
    modules 含 'website_admin' 就能操作，跟官網管理 Tab 寫入守衛（website 路由）
    一致。full admin（access_level>=3 / legacy role）永遠通過。
    """
    try:
        from core.auth import check_admin_or_module
        check_admin_or_module(request, 'website_admin')
    except ImportError:
        pass


def _require_db():
    if not state.db_online:
        raise HTTPException(status_code=503, detail="資料庫目前不可用")


async def _get_factory():
    from db.session import get_session_factory
    factory = get_session_factory()
    if not factory:
        raise HTTPException(status_code=503, detail="資料庫未初始化")
    return factory


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_shoot_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        d = date.fromisoformat(date_str)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    except ValueError:
        return None


def _to_dict(c) -> dict:
    return {
        "id": c.id,
        "short_name": c.short_name,
        "full_name": c.full_name or "",
        "tax_id": c.tax_id or "",
        "am_username": c.am_username or "",
        "source_channel": c.source_channel or "",
        "contact_person": c.contact_person or "",
        "contact_method": c.contact_method or "",
        "status": c.status or "潛在客戶",
        "cooperation_note": c.cooperation_note or "",
        "payment_info": c.payment_info or "",
        "payment_note": c.payment_note or "",
        "notes": c.notes or "",
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


async def _auto_update_client_status(session, client_id: str):
    """根據專案數量自動更新客戶狀態：0=潛在客戶, 1=新客戶, 2+=舊客戶"""
    from sqlalchemy import func as _fn
    count = (await session.execute(
        select(_fn.count()).where(CrmProject.client_id == client_id)
    )).scalar() or 0
    client = await session.get(Client, client_id)
    if not client:
        return
    if count == 0:
        client.status = "潛在客戶"
    elif count == 1:
        client.status = "新客戶"
    else:
        client.status = "舊客戶"



# ── 以下自原檔「Staff Helpers」section 搬入（staff + showcase 共用）──────
# crm_staff.created_via 列舉值（避免 magic string 散落）
STAFF_CREATED_VIA_ADMIN = "admin"
STAFF_CREATED_VIA_SHOWCASE_EDIT = "showcase_edit"


def _to_staff_public_dict(s) -> dict:
    """精簡版（無敏感欄位），給 token endpoint / autocomplete chip 用。"""
    return {
        "id": s.id, "name": s.name, "role": s.role or "",
        "photo_url": s.photo_url or "",
        "resume_visible": bool(s.resume_visible),
        "daily_rate": s.daily_rate,
    }



# ── 以下自原檔「Staff Resume / Portfolio」section 搬入（staff + showcase 共用）──
_UPLOAD_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "uploads")
_ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}



# ── 以下自原檔「Staff Self-Edit via Token」section 搬入（staff + showcase 共用）──
async def _verify_token_generic(session, token: str, scope: str, model_cls, editable_attr: str, require_editable: bool = False):
    """Generic token verification for staff resume / showcase edit tokens."""
    from core.auth import verify_token
    payload = verify_token(token)
    if not payload or payload.get('scope') != scope:
        raise HTTPException(status_code=401, detail="無效的連結")
    obj = await session.get(model_cls, payload.get('sub', ''))
    if not obj or obj.edit_token != token:
        raise HTTPException(status_code=401, detail="連結已失效")
    if require_editable and not getattr(obj, editable_attr, True):
        raise HTTPException(status_code=403, detail="管理員已關閉編輯權限")
    return obj



# ── 以下自原檔「Cost Line Default Templates」section 搬入（projects + costs 共用）──
# Default 行政雜支 categories — auto-seeded as $0 rows on each new
# (project, cost-group) pair so users can just fill amounts in instead
# of clicking "+ 新增雜支" first. Kept aligned with EXPENSE_CATEGORIES
# in frontend/tabs/crm/crm-projects-state.js — the inline-edit dropdown
# must offer every seeded category.
_EXPENSE_CATEGORY_DEFAULTS = [
    "交通", "住宿", "飲食", "提案", "器材", "其他",
]


async def _seed_default_expenses(session, project_id: str, cost_group_id: str) -> int:
    """Insert one $0 expense row per default category not yet present
    on (project_id, cost_group_id). Caller commits. Idempotent."""
    rows = (await session.execute(
        select(CrmProjectExpense.category).where(
            CrmProjectExpense.project_id == project_id,
            CrmProjectExpense.cost_group_id == cost_group_id,
        )
    )).scalars().all()
    existing = set(rows)
    now = _now()
    added = 0
    for cat in _EXPENSE_CATEGORY_DEFAULTS:
        if cat in existing:
            continue
        session.add(CrmProjectExpense(
            id=uuid.uuid4().hex,
            project_id=project_id, cost_group_id=cost_group_id,
            category=cat, estimated=0, actual=0,
            created_at=now,
        ))
        added += 1
    return added
