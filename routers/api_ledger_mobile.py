"""routers/api_ledger_mobile.py — 士源帳本（私帳手機版 /m/ledger.html）專用端點。

規劃：docs/MY_LEDGER_MOBILE_PLAN.md §3。定位「現場輸入＋快看」；只有兩支：
字彙包（/options）與總覽（/home）。其餘讀寫直接打既有端點（/cash-entries、
/project-ledger*、/assets/*），**不重寫任何金額規則** —— 私帳已收是增量制
（routers/crm/cash._sync_mine_project_received），收支寫入一律走那支。

守衛：每支 `require_entity(request, "mine", level="full")`＝帳號要有 finance_mine
（指名制；Lv3 不 bypass，跟 /my-ledger.html 同口徑）。這頁只有 owner 看，所以
回應不走 MoneyRedactRoute（沒有 mine scope 的人在守衛就 403 了）。

跟 NAS office-api 一起掛（main_office._ROUTER_MODULES）—— master 關機手機照用
（owner 2026-09-12 拍板 2）。這支沒有排程、沒有 socketio、不 import notifier。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request

from core.ledger import require_entity
from core.ledger_project import COST_FIELDS, SELECTABLE_SOURCES, income_items
from core.db_guard import db_factory_or_503 as _factory_or_503
from config import load_settings

try:
    from sqlalchemy import func, select
    from db.models import BankAccount, Client, CrmCashEntry, CrmProject
except ImportError:  # DB 套件不存在的 agent 環境 — 同其他 router 的 try/except
    pass

router = APIRouter(prefix="/api/v1/finance/m", tags=["士源帳本"])

#: 家用那一支子樹的頂層節點名（私帳分類樹：公司／個人／家用／轉匯與定存／信用卡）。
#: 收支明細的 `category` 是「頂層_第二層」的鏡射，所以「是不是家用」看前綴就好。
HOUSEHOLD_TOP = "家用"
PERIODS = ("month", "year", "all")
_TW = ZoneInfo("Asia/Taipei")


def _today_tw() -> date:
    """「今天」以台北為準 —— 這支也跑在 NAS 容器（UTC）：`date.today()` 在台北每月 1 日
    00:00–08:00 還是上個月，「本月」會整段變成上個月。"""
    return datetime.now(_TW).date()


def _period_range(period: str, today: date | None = None) -> tuple:
    """`month`／`year`／`all` → `(from_date|None, to_date|None)`（含頭含尾，本地日）。"""
    t = today or _today_tw()
    if period == "month":
        nxt = (t.replace(day=1) + timedelta(days=32)).replace(day=1)
        return t.replace(day=1), nxt - timedelta(days=1)
    if period == "year":
        return date(t.year, 1, 1), date(t.year, 12, 31)
    return None, None


def _in_period(day, lo, hi) -> bool:
    """結案日在不在區間裡；沒有結案日的案只在 `all` 算進去。"""
    if lo is None:
        return True
    if not day:
        return False
    d = day.date() if isinstance(day, datetime) else day
    return lo <= d <= hi


def taxonomy_options(flat_nodes) -> list:
    """分類樹攤平（core.cash_tree.flatten 的輸出）→ picker 的 items。

    `label`＝完整路徑（「家用／變動支出／外食」）、`top`＝頂層節點名（手機用它分
    「收支」與「家用」兩個分頁的清單）。頂層節點自己不進清單 —— 掛到頂層等於沒分類。
    """
    out = []
    for n in flat_nodes:
        path = list(n.get("path") or [])
        if len(path) < 2:
            continue
        out.append({"id": n["id"], "label": "／".join(path), "top": path[0],
                    "depth": int(n.get("depth") or len(path))})
    return out


@router.get("/options")
async def ledger_mobile_options(request: Request):
    """字彙包：分類樹、案源、專案 picker（私帳案，不帶金額）、費用欄、工項、銀行帳戶。"""
    from core.cash_tree import flatten, load_tree
    from core.auth import _extract_token

    require_entity(request, "mine", level="full")
    payload = _extract_token(request) or {}
    factory = _factory_or_503()
    async with factory() as session:
        tree = await load_tree(session, "mine")
        rows = (await session.execute(
            select(CrmProject.id, CrmProject.name, CrmProject.display_name,
                   CrmProject.completion_date, CrmProject.created_at, Client.short_name)
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .where(CrmProject.entity == "mine")
            .order_by(CrmProject.updated_at.desc()))).all()
        # 停用的也回（帶 active 旗標）：歷史列還掛在上面，編輯時選單要認得它，不然存個摘要
        # 就把帳戶洗成「不指定」；新增時的選單由前端只列 active（同桌機 crm-cashbook-fields）
        accounts = (await session.execute(
            select(BankAccount.id, BankAccount.name, BankAccount.active, BankAccount.is_default)
            .where(BankAccount.entity == "mine")
            .order_by(BankAccount.sort_order, BankAccount.created_at))).all()
    projects = []
    for pid, name, disp, close, created, client in rows:
        year = (close or created)
        year = year.year if year else None
        shown = disp or name or ""
        projects.append({"id": pid, "name": shown, "client": client or "",
                         "year": year, "closed": bool(close),
                         "label": " ".join(str(x) for x in (year, client, shown) if x)})
    return {
        "me": {"username": payload.get("sub") or payload.get("username") or "",
               "can_write": True},
        "taxonomy": taxonomy_options(flatten(tree)),
        "household_top": HOUSEHOLD_TOP,
        "sources": list(SELECTABLE_SOURCES),
        "cost_fields": [{"key": k, "label": lb} for k, lb in COST_FIELDS],
        "income_items": income_items(load_settings()),
        "projects": projects,
        "accounts": [{"id": i, "name": n, "active": bool(a), "is_default": bool(d)}
                     for i, n, a, d in accounts],
    }


@router.get("/home")
async def ledger_mobile_home(request: Request, period: str = Query("month")):
    """總覽一趟：案子的四個數字（結案日落在區間的案）＋現金流（收支明細）＋最近 5 筆＋未收前 5 案。

    案子那半直接沿用 `/project-ledger` 的逐案數字（同一支函式，顯示名／實收／應收都是它算的），
    這裡只做區間篩選與加總；不另寫一套。
    """
    from routers.api_finance_projects import project_ledger

    require_entity(request, "mine", level="full")
    period = period if period in PERIODS else "month"
    lo, hi = _period_range(period)
    ledger = await project_ledger(request, entity="mine")
    items = ledger.get("projects") or []
    picked = [p for p in items if _in_period(_parse_day(p.get("close_date")), lo, hi)]
    proj = {"count": len(picked)}
    for k in ("contract", "received", "net"):
        proj[k] = sum(int(p.get(k) or 0) for p in picked)
    # 「未收」一律走 to_collect（同桌機執行專案那欄）：amount_receivable 是營收−已收，
    # 代開／執行業務所得的源頭代扣永遠不會進帳，收齊的案會剩一個代辦費當「未收」
    proj["receivable"] = sum(int(p.get("to_collect") or 0) for p in picked)
    to_collect = sorted((p for p in items if int(p.get("to_collect") or 0) > 0),
                        key=lambda p: -int(p.get("to_collect") or 0))[:5]

    factory = _factory_or_503()
    async with factory() as session:
        conds = [CrmCashEntry.entity == "mine"]
        if lo is not None:
            conds += [CrmCashEntry.entry_date >= datetime(lo.year, lo.month, lo.day, tzinfo=timezone.utc),
                      CrmCashEntry.entry_date < datetime(hi.year, hi.month, hi.day, tzinfo=timezone.utc) + timedelta(days=1)]
        dep, exp, house = (await session.execute(
            select(func.coalesce(func.sum(CrmCashEntry.deposit), 0),
                   func.coalesce(func.sum(CrmCashEntry.expense), 0),
                   func.coalesce(func.sum(CrmCashEntry.expense).filter(
                       CrmCashEntry.category.like(HOUSEHOLD_TOP + "%")), 0))
            .where(*conds))).one()
        recent = (await session.execute(
            select(CrmCashEntry.id, CrmCashEntry.entry_date, CrmCashEntry.summary,
                   CrmCashEntry.deposit, CrmCashEntry.expense, CrmCashEntry.category,
                   CrmCashEntry.item, CrmCashEntry.project_id)
            .where(CrmCashEntry.entity == "mine")
            .order_by(CrmCashEntry.entry_date.desc(), CrmCashEntry.created_at.desc())
            .limit(5))).all()
    names = {p["id"]: p["name"] for p in items}
    return {
        "period": period,
        "range": {"from": lo.isoformat() if lo else "", "to": hi.isoformat() if hi else ""},
        "projects": proj,
        "cash": {"deposit": int(dep or 0), "expense": int(exp or 0),
                 "net": int(dep or 0) - int(exp or 0), "household_expense": int(house or 0)},
        "recent": [{"id": i, "date": _fmt(d), "summary": s or "",
                    "deposit": int(dp or 0), "expense": int(ex or 0),
                    "category": cat or "", "item": it or "",
                    "project_name": names.get(pid, "") if pid else ""}
                   for i, d, s, dp, ex, cat, it, pid in recent],
        "to_collect": [{"id": p["id"], "name": p["name"], "client": p.get("client", ""),
                        "receivable": int(p.get("to_collect") or 0)} for p in to_collect],
    }


def _parse_day(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _fmt(d) -> str:
    """私帳日期慣例：UTC 午夜＝該日 —— 走 routers.crm._shared._fmt_day（轉台北再取日期），不自己算。"""
    from routers.crm._shared import _fmt_day
    return _fmt_day(d) if d else ""
