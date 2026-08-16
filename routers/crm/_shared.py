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
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

# 模組層 import：CRM 套件本來就在 import 時硬相依 core.auth
# （proposal_assets 模組層就 import 了），函式內 import 的 ImportError 退路是
# 死碼，而守衛掛在 router 層＝每個請求都走一次。
from core.auth import check_admin_or_module, check_logged_in
from core.money import MoneyRedactRoute, check_money
from core.project_flow import ADVANCE_MODULES, CHECK_MODULES

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

CRM_PREFIX = "/api/v1/crm"   # 單一真相：router、NAS 掛載、守衛測試三處共用


async def _crm_read_guard(request: Request):
    """整個 CRM router 的底線守衛：**你得先是我們的人**。

    🔴 2026-08-14 發現：CRM 的讀取端點（清單/詳情）多數根本沒帶 request 參數
    ＝ 零守衛。實測匿名可讀生產 236 筆專案，欄位含 contract_amount /
    amount_receivable / profit_target_pct / 客戶名 / 負責人。8000 經
    cloudflared 對外，等同對網際網路公開整份案件清單與金額。

    為什麼放在 router 層而不是逐支加：漏一支就等於沒修，而 CRM 有 140+ 端點、
    還會繼續長。放這裡，新端點預設就是安全的（**沒有任何路徑例外** —— 例外
    清單會變成下一個被遺忘的洞）。

    用 `check_logged_in` 而**不是** `check_admin`：器材庫/場景庫/看片門戶/
    素材庫/現金流/提案庫六個分頁都在讀 CRM 清單，那些使用者不是管理員
    （模組級授權）—— 用 admin 會把他們的畫面全打壞。也**不是**
    `check_lan_or_logged_in`：那條是後期製作工具的產品前提（owner 2026-08-12
    拍板，剪輯師在本機不登入直接用），CRM 是商務資料，不適用。

    寫入端各自的 `_check_auth`（Lv3）不動 —— 這裡只是補上「至少要登入」的底線。
    """
    check_logged_in(request)


def _module_guard(*modules: str):
    """守衛工廠：`check_admin_or_module(request, *modules)`。

    不帶 modules ＝ 只有管理員（`check_admin_or_module` 零 key 時等同
    `check_admin` —— 兩者都走 `payload_grants`，access_level>=3 或 legacy
    role=='admin' 通過）。CRM 的寫入守衛正在長第三、第四個（模組級鬆綁才
    剛開始），三個各寫一遍就會長出三種拼法。
    """
    def guard(request: Request):
        return check_admin_or_module(request, *modules)
    return guard


router = APIRouter(prefix=CRM_PREFIX, tags=["CRM"],
                   dependencies=[Depends(_crm_read_guard)],
                   route_class=MoneyRedactRoute)

# 對外白名單 —— NAS 對外容器只掛這一個 router（master 在 crm/__init__ 收編回
# 主 router，URL 完全不變）。「這條端點可以對外」是整個 CRM 套件的橫切分類
# （costs/showcase/staff 也有 token 端點），所以 seam 放在這裡而非某個領域模組：
# **本套件**的對外曝露面只有這一個物件。
#
# 2026-08-07 起對外容器不只掛這一個 —— routers/api_proposals.py 有它自己的
# public_router（前綴不同，一個 APIRouter 載不了兩種）。整個 app 的曝露面由
# tests/unit/test_public_surface.py 一次列舉斷言；per-module 的
# tests/unit/test_media_log_public_router.py 仍在，守的是「有人往這個物件加端點」。
# 刻意不帶 prefix —— master 由上面 router 的 CRM_PREFIX 提供，NAS 掛載時自己指定。
# ⚠ 往這裡加端點前先問：它真的該在對外服務上被匿名打到嗎？
public_router = APIRouter(tags=["CRM 公開（token 授權）"])


# ── Helpers ──────────────────────────────────────────────────

# CRM 一般寫入 —— 管理員限定（歷史預設）。
_check_auth = _module_guard()

# 第一層金額守衛：**端點本身就是錢**（成本明細／財務摘要／發票／請款／收支／
# 報價／費率史）。第二層（欄位抹除）由 router 的 MoneyRedactRoute 負責，管的是
# 「夾帶」金額的端點。政策與名單住 core/money.py，這裡只是把它接到 CRM 上。
#
# ⚠️ 這層 async 包裝不是別名，是**成本**：FastAPI 對同步 dependency 會走
# `run_in_threadpool`，實測那一跳 104µs、而它包住的驗證只有 10µs —— 21 支端點
# 每個請求白付 10 倍成本，還各吃掉一個 anyio worker 名額。隔壁的
# `_crm_read_guard` 也是 async，同一個理由。
# （`core.money.check_money` 本體維持同步：`api_finance`/`api_cashflow` 的
#   `_guard` 是在 endpoint body 裡直接呼叫它的。）
async def _check_money(request: Request):
    return check_money(request)

# 官網製作授權 — 管理員 OR 擁有 website_admin 模組即可（不需全域 admin）。
# 給「結案製作」看板 + showcase 編輯端點用：非管理員的官網製作人員只要帳號
# modules 含 'website_admin' 就能操作，跟官網管理 Tab 寫入守衛（website 路由）一致。
_check_website_auth = _module_guard('website_admin')

# 工作流手動里程碑 —— 模組級（owner 2026-08-14 拍板）。這是 CRM 寫入面第一道
# 模組級鬆綁：生產有 3 個 lv1 帳號被授予 crm_projects 卻打不了任何 CRM 寫入
# 端點（其餘寫入都是 Lv3），權限等於空頭支票。勾一個里程碑跟改專案狀態、
# 動錢流不是同一個量級，所以先從這裡兌現。政策字面值住
# core.project_flow.CHECK_MODULES，與前端「畫不畫 checkbox」的判定共用同一份。
_check_flow_check_auth = _module_guard(*CHECK_MODULES)

# 推進專案階段 —— 從 CRM 泛用寫入預設分家，理由與退場條件見
# core.project_flow.ADVANCE_MODULES
_check_status_auth = _module_guard(*ADVANCE_MODULES)


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


async def _project_or_404(session, project_id: str):
    """專案列或 404 —— archive / flow / proposal_assets 共用（原本各抄一份，
    連 404 訊息都不一樣）。"""
    project = await session.get(CrmProject, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="專案不存在")
    return project


@asynccontextmanager
async def _crm_session():
    """DB 守門 + session 生命週期。`async with _crm_session() as session:`

    🔴 形狀刻意是 context manager 而不是「傳一個 callback 進來」：這個套件裡
    有 **130 處**逐字抄著下面這三行

        _require_db(); factory = await _get_factory(); async with factory() ...

    而它們大多在 `_get_factory()` 與 `async with` 之間還要做事（組子查詢、
    解日期），callback 形狀對那些人是**倒退**（要把函式體包成閉包）。CM 形狀
    是它們可以逐一直接換掉的那一種 —— 抽一個沒人接得上的抽象，等於再多一份。
    （`_with_project` 是例外：它要把 404 夾在中間，所以留著 callback。）
    """
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        yield session


async def _with_project(project_id: str, payload):
    """DB 守門 → session → 專案或 404 → `await payload(session, project)`。

    掛在 `crm_projects` 那幾個 JSONB 欄上的功能（archive_checklist /
    review_kpta / flow_checks）讀取端一模一樣，各自抄一份的話，下一個要加
    的東西（audit 列、updated_by）只會被加在其中一個檔。
    """
    async with _crm_session() as session:
        return await payload(session, await _project_or_404(session, project_id))


async def _patch_project_json(project_id: str, attr: str, call, payload):
    """讀某個 JSONB 欄 → `call(舊值) -> (新值, 錯誤)` → commit → 回完整狀態。

    `call` 的簽名刻意對齊 `core.project_archive` / `core.project_flow` 那批
    純函式，端點就只剩一行 lambda。錯誤字串非空 → 422。
    """
    async def _run(session, project):
        new, err = call(getattr(project, attr))
        if err:
            raise HTTPException(status_code=422, detail=err)
        setattr(project, attr, new)
        project.updated_at = _now()
        await session.commit()
        return await payload(session, project)
    return await _with_project(project_id, _run)


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
    手動設的『暫停合作』不自動覆蓋。
    client_id 可為空（提案建殼專案還沒定客戶）→ 無客戶可算，直接返回。"""
    if not client_id:
        return
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
from core.image_utils import save_webp_or_none  # noqa: F401,E402



# ── 以下自原檔「Staff Self-Edit via Token」section 搬入（staff + showcase 共用）──
async def _verify_token_generic(session, token: str, scope: str, model_cls, editable_attr: str, require_editable: bool = False):
    """Generic token verification for staff resume / showcase edit tokens.

    🔴 **不驗簽章，靠逐字比對 DB**（見 core.auth.decode_unverified）。這類 token
    是我們發出去、同時存進 DB 的；真正的憑證是「這串字與存起來的那串完全相同」，
    光有 jwt_secret 產不出對得上的字串。把簽章也一起要求的話，jwt_secret 輪替
    會連坐殺掉所有已經印出來/寄出去的連結 —— 而那正是輪替**不需要**殺的東西。
    """
    from core.auth import decode_unverified, stored_token_matches
    payload = decode_unverified(token)
    if not payload or payload.get('scope') != scope:
        raise HTTPException(status_code=401, detail="無效的連結")
    obj = await session.get(model_cls, payload.get('sub', ''))
    if not obj or not stored_token_matches(getattr(obj, 'edit_token', ''), token):
        raise HTTPException(status_code=401, detail="連結已失效")
    if require_editable and not getattr(obj, editable_attr, True):
        raise HTTPException(status_code=403, detail="管理員已關閉編輯權限")
    return obj


def _is_valid_scoped_token(token, scope: str) -> bool:
    """存庫 token 是否仍可用（`_mint_token_generic` 的 reuse_existing 依此判斷）。

    🔴 這支**必須**與 `_verify_token_generic` 用同一套判準。只改驗證那半邊的話，
    輪替後管理員下一次開後台，這裡會判定「舊的不能用」而重發一張新 token —— 
    連結字串照樣變掉，等於白改。2026-07-10 輪替實案就是這樣讓 226/230 個庫存
    token 換新、編輯器整片 404。
    """
    if not token:
        return False
    try:
        from core.auth import decode_unverified
        payload = decode_unverified(token)
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
    from core.auth import new_share_token
    from core.crm_logic import PERMANENT_TOKEN_EXPIRES_DAYS
    row = await session.get(model_cls, obj_id)
    if row and reuse_existing and _is_valid_scoped_token(row.edit_token, scope):
        return row.edit_token, row
    token = new_share_token(obj_id, scope,
                            expires_days or PERMANENT_TOKEN_EXPIRES_DAYS)
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
