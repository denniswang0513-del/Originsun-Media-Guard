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
import re
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

import core.state as state
from core.finance_logic import month_of

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
                           ProjectMediaLog, ProjectMediaFile,
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
    "CrmProjectShowcase", "ProjectMediaLog", "ProjectMediaFile",
    "WEBSITE_TEAM_OVERRIDE_FIELDS",
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


async def _mark_dirty_safe(tag: str) -> None:
    """觸發對外網站 rebuild（debounce 60s）— 失敗不擋主操作（儲存/發布/上傳）。

    Lazy import：services/website/ 不在 OTA AGENT_DIRS，只在 master 存在。
    showcase / works 各寫入端點共用（tag 進 log 辨識觸發點）。"""
    try:
        from services.website import rebuild_service
        await rebuild_service.mark_dirty()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("[%s] mark_dirty 失敗: %s", tag, e)


def _username(request: Request) -> str:
    from core.auth import _extract_token
    payload = _extract_token(request) or {}
    return payload.get("username") or payload.get("sub") or "?"


def _parse_day(raw):
    """YYYY-MM-DD → datetime；空值 → None；格式錯 → 422（與 _parse_shoot_date
    的「看不懂回 None」語意不同 — 這支給嚴格驗證的財務端點用）。"""
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail=f"日期格式錯誤: {raw}（要 YYYY-MM-DD）")


def _validate_month(month: str) -> str:
    if not re.match(r"^\d{4}-\d{2}$", month or ""):
        raise HTTPException(status_code=422, detail="month 格式需 YYYY-MM")
    return month


# ── F1 月結守衛（唯一實作 — routers/crm/finance.py 與 routers/api_finance.py 共用）──

async def _locked_month_set(session) -> set:
    """已鎖（未重開）月份集合 — batch / CSV 匯入要逐筆檢查、彙整違規清單時用
    （_assert_month_open 是單筆語意，逐筆呼叫會在第一筆就斷，報不出全貌）。"""
    from db.models import FinanceMonthClose
    rows = (await session.execute(
        select(FinanceMonthClose.month).where(
            FinanceMonthClose.reopened_at.is_(None)))).scalars().all()
    return set(rows)


def _raise_locked_batch(violations: list):
    """batch / CSV 的整批拒絕：409 + detail 列出前幾筆違規（行號/摘要 + 日期）。"""
    shown = "、".join(violations[:5])
    more = f"…共 {len(violations)} 筆" if len(violations) > 5 else ""
    raise HTTPException(
        status_code=409,
        detail=f"下列項目落在已鎖帳月份，整批拒絕：{shown}{more}"
               "（需修改請先到帳務→現金流重開該月）")


async def _assert_month_open(session, *dates):
    """F1 月結鎖帳：任一日期落在已鎖（未重開）月份 → 409。
    dates 收 str / date / datetime（core.finance_logic.month_of 收斂型別）。
    更新時要同時傳舊/新日期（把紀錄搬進或搬出鎖定月都算改帳）。

    守衛覆蓋範圍（財務階段二擴張）與「看哪個日期」的判準：
    - 收支明細  create/update/delete → entry_date（現金側）
    - 發票      create/update/delete → invoice_date（權責收入認列月）
    - 請款單    create/update/delete → request_date（權責費用認列月）
    - 請款單    batch-pay/batch-unpay → payment_date（付款動作影響的是現金側，
      不改費用認列月 request_date；付款日落鎖定月才 409）
    - 請款單    batch-month（改 planned_month 排程欄）不涉權責日期 → 不掛守衛
    - 調整表    create/update/delete → adj_date（routers/api_finance.py）
    - 設定精靈  → 基準月 1 日（routers/api_finance.py setup-wizard）
    - CSV 匯入三支 → 逐列判月，任一列落鎖定月整批 409（見 _assert_rows_open）
    """
    months = {m for m in (month_of(d) for d in dates) if m}
    if not months:
        return
    locked = sorted(months & await _locked_month_set(session))
    if locked:
        raise HTTPException(
            status_code=409,
            detail=f"月份已鎖帳：{', '.join(locked)}（需修改請先到帳務→現金流重開該月）")


async def _assert_rows_open(session, dated_rows):
    """batch / CSV 的逐筆月結檢查：dated_rows = iterable of (label, date_like)。
    一次撈鎖定月集合 → 逐筆比對 → 收集違規 label → 任一違規整批 409。
    label 由呼叫端組好（如「第 N 列（YYYY-MM-DD）」），這裡只負責比對與彙整。"""
    locked = await _locked_month_set(session)
    violations = [label for label, d in dated_rows if (month_of(d) or "") in locked]
    if violations:
        _raise_locked_batch(violations)


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


# 分級不計入的專案狀態（尚未拿下的案子＝投標/開發/洽詢/提案/未成案，不算真正案件）
# ——與 clients.py 列表「案件數」欄同口徑，確保客戶狀態與畫面上看到的案件數字一致。
# 「已拿下」＝製作/結案/歸檔。
_CLIENT_TIER_EXCLUDE_STATUSES = ("投標", "開發", "洽詢", "提案", "未成案")


async def _auto_update_client_status(session, client_id: str):
    """依「有效專案數」自動更新客戶分級：0=潛在客戶, 1=新客戶, 2+=舊客戶。
    有效專案＝排除 投標/開發/洽詢/提案/未成案（與客戶列表『案件數』欄同口徑）。
    手動設的『暫停合作』不自動覆蓋。"""
    from sqlalchemy import func as _fn
    count = (await session.execute(
        select(_fn.count()).where(
            CrmProject.client_id == client_id,
            CrmProject.status.notin_(_CLIENT_TIER_EXCLUDE_STATUSES),
        )
    )).scalar() or 0
    client = await session.get(Client, client_id)
    if not client or client.status == "暫停合作":
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


# 單一正本在 core/image_utils.py（2026-07-20 與 routers/website/admin_posts.py
# 的雙胞合併；showcase 因此順帶獲得 EXIF 方向校正 — 手機直拍不再躺著）。
from core.image_utils import save_image_as_webp as _save_image_as_webp  # noqa: F401,E402



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


def _is_valid_scoped_token(token, scope: str) -> bool:
    """存庫 token 是否仍可驗。jwt_secret 輪替後舊 token 驗不過 — 2026-07-10
    輪替實案：生產 226/230 個庫存 token 失效、編輯器整片 404。重用前必驗，
    驗不過就重發（自癒；_mint_token_generic 的 reuse_existing 依此判斷）。"""
    if not token:
        return False
    try:
        from core.auth import verify_token
        payload = verify_token(token)
        return bool(payload) and payload.get("scope") == scope
    except Exception:
        return False


async def _mint_token_generic(session, model_cls, obj_id: str, scope: str, *,
                              reuse_existing: bool = False,
                              expires_days: int | None = None,
                              row_defaults: dict | None = None,
                              on_rotate=None):
    """取得/產生 scope token，upsert 對應 row（verify 半邊的 _verify_token_generic
    孿生；showcase / media_log 的 mint 是本函式的薄包裝）。

    reuse_existing=True：既有 token 仍有效就重用（冪等，不動 updated_at）；
    無效或 False → 產新 token 覆寫（「重置連結」語意）。
    row_defaults：建新 row 時附帶的欄位；on_rotate(row)：覆寫既有 row 前的
    領域鉤子（如 showcase 補 project_id）。回傳 (token, row)，不 commit。
    """
    from core.auth import create_token
    from core.crm_logic import PERMANENT_TOKEN_EXPIRES_DAYS
    row = await session.get(model_cls, obj_id)
    if row and reuse_existing and _is_valid_scoped_token(row.edit_token, scope):
        return row.edit_token, row
    token = create_token({"sub": obj_id, "scope": scope},
                         expires_days=expires_days or PERMANENT_TOKEN_EXPIRES_DAYS)
    if not row:
        row = model_cls(id=obj_id, edit_token=token, **(row_defaults or {}))
        session.add(row)
    else:
        if on_rotate:
            on_rotate(row)
        row.edit_token = token
        row.updated_at = _now()
    return token, row



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
