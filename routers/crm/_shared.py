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
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request

# 模組層 import：CRM 套件本來就在 import 時硬相依 core.auth
# （proposal_assets 模組層就 import 了），函式內 import 的 ImportError 退路是
# 死碼，而守衛掛在 router 層＝每個請求都走一次。
from core.auth import check_admin_or_module, check_logged_in
from core.money import MoneyRedactRoute, money_dep  # money_dep 給領域模組 re-export
from core.project_flow import ADVANCE_MODULES, CHECK_MODULES

import core.state as state
from core.finance_logic import month_of

try:
    from sqlalchemy import select, or_, delete, func, update as sa_update
    from sqlalchemy.exc import IntegrityError
    from db.models import (Client, User, CrmProject, CrmQuotation, CrmQuotationItem,
                           CrmQuotationTemplate, CrmStaff, CrmStaffPortfolio,
                           CrmProjectStaff,
                           CrmProjectExpense,
                           CrmInvoice, CrmPaymentRequest, CrmCashEntry,
                           CrmCashInvoiceLink,
                           CrmCashPaymentLink,
                           CrmProjectCostLine, CrmCostLineTemplate,
                           CrmProjectCostGroup,
                           CrmProjectShowcase,
                           ProjectMediaLog, ProjectMediaFile, CrmExpenseLink,
                           WEBSITE_TEAM_OVERRIDE_FIELDS)
    _HAS_DB = True
except ImportError:
    _HAS_DB = False

# 本檔自用只有 select / Client / CrmProject；其餘 db.models 類與
# or_ / delete / IntegrityError 是給領域模組 `from ._shared import ...`
# 的 re-export（列進 __all__，ruff F401 視為已使用）。
__all__ = [
    "router", "token_router", "money_dep",
    "or_", "delete", "sa_update", "IntegrityError", "User", "CrmProjectExpense",
    "CrmQuotation", "CrmQuotationItem", "CrmQuotationTemplate",
    "CrmStaff", "CrmStaffPortfolio", "CrmProjectStaff",
    "CrmInvoice", "CrmPaymentRequest", "CrmCashEntry", "CrmCashInvoiceLink",
    "CrmCashPaymentLink",
    "cash_category_texts",
    "CrmProjectCostLine", "CrmCostLineTemplate", "CrmProjectCostGroup",
    "CrmProjectShowcase", "ProjectMediaLog", "ProjectMediaFile", "CrmExpenseLink",
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
#
# 🔴 `route_class` 要自己再寫一次：`include_router` 用 `route_class_override=
# type(route)` **保留子 router 自己的 class**，所以上面那個 router 的
# MoneyRedactRoute 不會傳下來。少了這行，第二層（金額抹除）對這批端點完全
# 不存在 —— 而 core/money.py 宣稱「掛這裡，新端點預設就是安全的」，那句話就
# 在這個子集上變成假的。公開／手機頁正是最可能夾帶金額的去處。
public_router = APIRouter(tags=["CRM 公開（token 授權）"],
                          route_class=MoneyRedactRoute)

# ── token_router：token 自我驗證的端點（匿名，但**不在 NAS 白名單**）──────
#
# 🔴 為什麼需要第三個 router（2026-08-15，完稿結案 iframe 整片 404 的根因）：
# showcase-edit / staff-edit / resume 這批頁面的憑證**是網址裡的 token**
# （發給外部製作人員、沒有帳號），fetch 一律不帶 Authorization。它們的端點
# 原本掛在主 router 上，8/14 的 `_crm_read_guard`（要登入）把它們一起蓋到 ——
# 於是每一支都回「未登入」，頁面只能顯示「連結已失效」。
#
# 跟 public_router 的差別只有一個：**要不要對 NAS 對外容器曝露**。
#   public_router＝master 關機也要活（媒體紀錄、雜支連結）→ NAS 有掛。
#   token_router ＝只有 master 在 serve 的編輯器（showcase-edit 等）→ NAS 不掛，
#   曝露面不用為它變大。
# 守衛就是端點自己的 `_verify_token_generic`（逐字比對 DB）；MoneyRedactRoute
# 照掛 —— 匿名＝沒有 money_view，人員搜尋回的 daily_rate 會被抹（前端已處理）。
token_router = APIRouter(tags=["CRM token 自驗（master 限定）"],
                         route_class=MoneyRedactRoute)


# ── Helpers ──────────────────────────────────────────────────

# CRM 一般寫入 —— 管理員限定（歷史預設）。
_check_auth = _module_guard()

# 官網製作授權 — 管理員 OR 擁有 website_admin 模組即可（不需全域 admin）。
# 給「結案製作」看板 + showcase 編輯端點用：非管理員的官網製作人員只要帳號
# modules 含 'website_admin' 就能操作，跟官網管理 Tab 寫入守衛（website 路由）一致。
_check_website_auth = _module_guard('website_admin')

# 專案本體與派工的增刪 —— 模組級（owner 2026-08-15 拍板，同工作流勾選那條路）。
#
# 為什麼鬆綁：專案頁把「每個案子的工作面」搬齊了之後，唯獨人員配置與專案本身
# 加不了也刪不了 —— 因為這幾支是 Lv3。而專案頁的閘門收 crm_projects，於是
# 持有那個模組的人看得到畫面、按下去 403。這就是 8/14 那句「權限是空頭支票」
# 的同一個形狀，owner 給了同一個答案。
#
# ⚠️ 這比勾一個里程碑重：建專案／刪專案是會動到整個案子的動作。刻意**不**把
# 其他 CRM 寫入（客戶、報價、帳務、成本）一起放行 —— 那些的使用者是財務。
_check_project_write_auth = _module_guard('crm_projects')

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


_TW_TZ = ZoneInfo("Asia/Taipei")


def _fmt_day(dt) -> str:
    """timestamptz → 'YYYY-MM-DD'（台北），None → ''。

    🔴 aware datetime 不准直接 strftime/取 date：寫入端是 naive（PG 依 session
    時區 Asia/Taipei 解讀 → 存成前一天 16:00Z），asyncpg 讀回是 aware UTC ——
    面值取日期就差一天（2026-08-18 在雜支消費日踩到，hr_logic/api_proposals
    早各修過一次）。naive 視為本地 wallclock 直接取。
    """
    if not dt:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(_TW_TZ)
    return dt.strftime("%Y-%m-%d")


def map_csv_row(col_map: dict, header_map: dict, row: dict, coerce=None) -> dict:
    """CSV 一列 → {欄位: 值}，依 {欄位: [別名…]} 對映。六支匯入端的唯一正本。

    別名採「第一個**有值**的欄勝出」。
    🔴 本來有兩種方言：clients/projects 那兩支是「第一個**存在**的表頭勝出」＋
    break —— CSV 同時有「匯款資訊」與「匯款帳號」兩欄、而前者這一列剛好空白時，
    後者的值會被整格吞掉，而且沒有任何跡象。這裡不留那個方言：六個匯入端沒有
    任何一支會寫入空字串（都有 truthiness 閘），空格從來就不是「刻意留白」。

    coerce(欄位, 值) 讓呼叫端自己決定型別轉換（_parse_money／int），不塞進來。
    """
    data = {}
    for field, aliases in col_map.items():
        for alias in aliases:
            orig = header_map.get(alias.lower())
            if orig and (row.get(orig) or "").strip():
                val = row[orig].strip()
                data[field] = coerce(field, val) if coerce else val
                break
    return data


def _fmt_minute(dt) -> str:
    """timestamptz → 'YYYY-MM-DD HH:MM'（台北），None → ''。

    同 _fmt_day 的理由：asyncpg 讀回來是 aware UTC，面值直接切字串會少 8 小時
    （草稿清單的「最後修改」實測顯示成前一天 17:31）。
    """
    if not dt:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(_TW_TZ)
    return dt.strftime("%Y-%m-%d %H:%M")


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

async def _locked_month_set(session, entity: str = "parent") -> set:
    """已鎖（未重開）月份集合 — batch / CSV 匯入要逐筆檢查、彙整違規清單時用
    （_assert_month_open 是單筆語意，逐筆呼叫會在第一筆就斷，報不出全貌）。
    兩本帳各自鎖月，見 docs/LEDGER_ENTITY_PLAN.md §3。"""
    from db.models import FinanceMonthClose
    rows = (await session.execute(
        select(FinanceMonthClose.month).where(
            FinanceMonthClose.reopened_at.is_(None),
            FinanceMonthClose.entity == entity))).scalars().all()
    return set(rows)


def _raise_locked_batch(violations: list):
    """batch / CSV 的整批拒絕：409 + detail 列出前幾筆違規（行號/摘要 + 日期）。"""
    shown = "、".join(violations[:5])
    more = f"…共 {len(violations)} 筆" if len(violations) > 5 else ""
    raise HTTPException(
        status_code=409,
        detail=f"下列項目落在已鎖帳月份，整批拒絕：{shown}{more}"
               "（需修改請先到帳務→現金流重開該月）")


async def _assert_month_open(session, *dates, entity: str = "parent"):
    """F1 月結鎖帳：任一日期落在已鎖（未重開）月份 → 409。
    dates 收 str / date / datetime（core.finance_logic.month_of 收斂型別）。
    更新時要同時傳舊/新日期（把紀錄搬進或搬出鎖定月都算改帳）。
    兩本帳各自鎖月，見 docs/LEDGER_ENTITY_PLAN.md §3 —— 呼叫端傳**該列的
    entity**（petty/costs 為母公司域，吃預設 'parent' 不用改）。

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
    locked = sorted(months & await _locked_month_set(session, entity=entity))
    if locked:
        raise HTTPException(
            status_code=409,
            detail=f"月份已鎖帳：{', '.join(locked)}（需修改請先到帳務→現金流重開該月）")


async def _assert_rows_open(session, dated_rows, entity: str = "parent"):
    """batch / CSV 的逐筆月結檢查：dated_rows = iterable of (label, date_like)。
    一次撈鎖定月集合 → 逐筆比對 → 收集違規 label → 任一違規整批 409。
    label 由呼叫端組好（如「第 N 列（YYYY-MM-DD）」），這裡只負責比對與彙整。
    兩本帳各自鎖月，見 docs/LEDGER_ENTITY_PLAN.md §3。"""
    locked = await _locked_month_set(session, entity=entity)
    violations = [label for label, d in dated_rows if (month_of(d) or "") in locked]
    if violations:
        _raise_locked_batch(violations)


def _parse_shoot_date(date_str: Optional[str]) -> Optional[datetime]:
    """'YYYY-MM-DD' → 該日的 **UTC 午夜**（本 repo 日期欄的寫入慣例）。

    🔴 為什麼是 UTC 午夜而不是台北午夜：台北是 UTC+8，UTC 午夜換算台北仍是同一天，
    所以 `_fmt_day`（轉台北取日期）與前端（取 ISO 字串前 10 碼）兩個讀取端會得到
    同一個答案。寫成台北午夜的話存進去是前一天 16:00Z，前端就會少一天 —— 2026-08-19
    匯 394 筆歷史發票時踩到（匯入腳本自己造 datetime，沒走這支）。

    也吃**完整 ISO datetime**（例如 `_to_invoice_dict` 吐出來的
    '2024-01-01T16:00:00+00:00'）：任何「GET 回來、改一個欄位、PUT 回去」的呼叫端
    都會把這種字串送回來，原本只認純日期 → 直接回 None → 日期被清空。順帶把舊的
    台北午夜資料在每次編輯時歸一到 UTC 午夜（aware 值先轉台北再取日期，同 _fmt_day）。
    """
    if not date_str:
        return None
    s = str(date_str).strip()
    try:
        return datetime(*date.fromisoformat(s).timetuple()[:3], tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_TW_TZ)
    return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)


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
    client = await session.get(Client, client_id)
    if not client or client.status == "暫停合作":
        return
    # 兩本帳各一筆＋連結（owner 2026-08-26 定案）：CRM 客戶的分級＝**公司**的
    # 客戶關係，只算母公司案 —— 私帳案掛在私帳那一筆上，混算會讓合夥人看到
    # 一個「舊客戶」卻找不到任何公司案。私帳那筆不套 CRM 分級（語彙不同）。
    from core.ledger import not_mine
    if (client.entity or "parent") == "mine":
        return
    count = (await session.execute(
        select(func.count()).where(
            CrmProject.client_id == client_id,
            CrmProject.status.notin_(_CLIENT_TIER_EXCLUDE_STATUSES),
            not_mine(CrmProject.entity),
        )
    )).scalar() or 0
    from core.crm_logic import client_tier
    client.status = client_tier(count)



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

async def cash_category_texts(session, ent: str = "parent") -> list:
    """目前有效的收支明細 category 清單（finance_category_map, source='cash'）。

    **母公司**那一半的唯一正本 —— 兩本帳都要問的是下面的
    `ledger_category_domain`。這份清單本來散在四個地方（零用金 options 端點、
    零用金另一支查詢、對帳單預覽的未對映檢查、匯入腳本），其中兩份是 ORM、
    兩份是手寫 SQL，`active` 的寫法還不一致。前端下拉也不該再寫死：`貸款繳款`／`貸款補貼` 這些後來加的
    類別沒同步進去，結果自家匯入寫出來的列，使用者在編輯視窗裡選不到它的類別。
    """
    from db.models import FinanceCategoryMap
    return [r[0] for r in (await session.execute(
        select(FinanceCategoryMap.category_text)
        .where(FinanceCategoryMap.source == "cash",
               FinanceCategoryMap.entity == ent,
               FinanceCategoryMap.active.is_(True))
        .order_by(FinanceCategoryMap.category_text))).all()]


async def ledger_category_domain(session, ent: str, nodes=None) -> set:
    """**這本帳**認得的 category 值域。上面那支是它母公司的那一半。

    🔴 兩本帳的值域**不重疊**：母公司是 finance_category_map 的平面科目
    （行政／薪資／交際應酬…），私帳是 cash_taxonomy_nodes 那棵樹，存進
    `category` 欄的是路徑前兩層的複合鍵（`公司_專案`、`家用_變動支出`；
    鏡射規則的正本在 core.cash_taxonomy.mirror_from_path）。

    共用同一份白名單的話，私帳可以設出一條「→ 交際應酬」的規則 —— 而私帳的
    報表根本沒有那個類別，那些列會靜靜落到「未歸類」，看起來卻像已經分好了
    （owner 2026-08-29「這裡的分類規則不需要和 crm 共用」）。

    住在 `_shared` 而不是某個 router：問這個問題的地方不只一處（匯入規則的
    驗證、規則面板的下拉、預覽的「科目未對映」提醒），而**規則只有這一份**。

    `nodes` 給了就用（呼叫端已經撈過整棵樹時傳進來 —— core/cash_tree 的檔頭
    就在講這件事：同一個請求別讓樹撈六遍）。
    """
    if ent != "mine":
        # 母公司走平面科目（樹是私帳才有的東西，這條路連查都不用查）
        return set(await cash_category_texts(session, ent))
    from core.cash_taxonomy import mirror_from_path
    from core.cash_tree import path_map
    # 🔴 值域＝樹上每個節點鏡射出來的 category。**不跟科目對映取交集**：樹上還
    # 沒建成科目的分支照樣可以設規則（缺對映另有「科目未對映」的標記在提醒）。
    out = {mirror_from_path(p)[0]
           for p in (await path_map(session, ent, nodes=nodes)).values()}
    out.discard("")
    return out


async def assert_ledger_category(session, cat: str, ent: str) -> None:
    """自由輸入的類別：**私帳**不在值域內就 422。

    值域的判斷只有 `ledger_category_domain` 一份，擋人的訊息也只有這一份 ——
    每個寫入端各寫一句，措辭會漂，而使用者看到的就是那句話。

    🔴 只擋私帳，因為兩本帳的「不在值域內」意思不一樣：
    - 私帳的值域是**分類樹**（封閉的）。樹外的類別＝一列看不見的孤兒：
      樹狀篩選找不到它，畫面上卻像分好了。
    - 母公司的值域是**會計科目對映表**，那份刻意允許沒對映的類別 —— 它們會
      出現在「未歸類佇列」等人去補對映，那是既有設計（見補記入帳的說明），
      不是錯誤。在這裡擋下來等於把那條路堵死。
    """
    from fastapi import HTTPException
    if ent != "mine":
        return
    if cat and cat not in await ledger_category_domain(session, ent):
        raise HTTPException(
            status_code=422,
            detail=f"「{cat}」不是這本帳的類別 —— 請先在分類設定裡建立它")


async def ledger_categories_and_tree(session, ent: str) -> tuple:
    """`(這本帳的類別值域, 畫面用的分類樹)` —— 兩個一起要的地方都問這一支。

    🔴 節點表**整份撈一次**（core/cash_tree 檔頭：「別讓一個請求把節點表撈六遍」）
    再兩邊各自過濾，因為兩者要的深淺不同：

    - 值域收**全份**（含停用節點）—— 歷史列掛在被停用的節點上，它的 category
      照樣是有效值，不然那些列一驗就變成「不認得的類別」。
    - 畫面上的樹只放**啟用中**的 —— 停用＝新列挑不到。

    母公司沒有樹（值域是平面科目），那條路連查都不用查。
    """
    from core.cash_tree import build_tree, load_nodes
    nodes = await load_nodes(session, ent, include_inactive=True) \
        if ent == "mine" else []
    return (sorted(await ledger_category_domain(session, ent, nodes=nodes)),
            build_tree([n for n in nodes if n.active]))

async def project_names_map(session, rows) -> dict:
    """{project_id: name} —— 一次撈齊。

    `rows` 收兩種形狀：帶 `project_id` 屬性的物件，或直接是 id 字串。
    🔴 收字串是必要的，不是方便 —— 呼叫端手上一半是 ORM 列、一半是已經算好的
    id 集合（api_equipment 就是後者）。只收前者的話，後者只能再手刻一份，
    而這支存在的理由正是不要有下一份。

    這個 `select(id, name).where(id.in_(...))` → dict 的慣用法本來散在
    api_cashflow ×2、api_equipment、拆項清單。
    （api_timesheets 那兩處方向相反：name → id，結構上用不到這支。）
    """
    ids = {r if isinstance(r, str) else getattr(r, "project_id", None)
           for r in rows}
    ids.discard(None)
    ids.discard("")
    if not ids:
        return {}
    rows2 = (await session.execute(
        select(CrmProject.id, CrmProject.name)
        .where(CrmProject.id.in_(list(ids))))).all()
    return {pid: (name or "") for pid, name in rows2}
