# -*- coding: utf-8 -*-
"""routers/timesheets/_shared.py —— 工作追蹤 API 共用：router 單例、守衛（_require_mine_admin／_ts_or_bound／_mine_ident）、
小工具（_day_or_422）、類似專案候選快取（_CANDS_CACHE）、/summary 抹私帳後的鍵（SUMMARY_PUBLIC_KEYS）。

2026-09-12 從 routers/api_timesheets.py 拆出來（同 routers/crm/_shared.py 的做法）。
"""
import secrets
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from typing import Optional
from config import load_settings, save_settings
from core.auth import _extract_token, check_admin, check_logged_in, payload_grants
from core.hr_logic import midnight_of
from core.identity import resolve_current_staff
from services.timesheet_ingest import parse_date as _parse_date

router = APIRouter(prefix="/api/v1/timesheets", tags=["timesheets"])

_MAX_ROWS_PER_CALL = 1000


def _get_or_create_ingest_token() -> str:
    s = load_settings()
    tok = (s.get("timesheet") or {}).get("ingest_token") or ""
    if not tok:
        tok = "tsk_" + secrets.token_hex(24)
        s.setdefault("timesheet", {})["ingest_token"] = tok
        save_settings(s)
    return tok


def _require_mine_admin(request: Request, level: str = "view") -> None:
    """對映相關端點（列私帳案／指定對映／回填／灌預算）的守衛。

    🔴 私帳**專案**對沒有 mine scope 的人整列不存在（core.ledger.hide_mine_projects；
    Lv3 不隱含 finance_mine）。讀清單守 view；把時數改掛到私帳案、往私帳案寫預算
    是寫私帳，守 full（同 routers/crm 其他寫私帳的端點）—— 一支守衛，規則只有這一份。
    """
    check_admin(request)
    from core.ledger import MINE, require_entity
    require_entity(request, MINE, level=level)


def _day_or_422(day: str) -> datetime:
    d = _parse_date(day) if day else datetime.now()
    if d is None:
        raise HTTPException(status_code=422, detail=f"日期格式錯誤：{day}")
    return midnight_of(d)


def _has_ts_module(request: Request) -> bool:
    """有工作追蹤模組（或管理員）才看得到會洩露私帳金額的衍生值（建議預算）。"""
    return payload_grants(_extract_token(request) or {}, "timesheets")


async def _ts_or_bound(request: Request) -> Optional[str]:
    """唯讀端點放寬（docs/JOURNAL_WORKLOG_PLAN.md §10／§11）：timesheets 模組 **或** 登入＋綁定人員檔案
    （員工頁的專案查詢、工作階段下拉、專案下拉）。只給讀；寫入端點（改預算等）守衛不變。
    回傳：有 timesheets 模組 → None（整份）；靠綁定人員進來的 → 該員姓名（端點要縮到「本人」時用）。
    🔴 這支不碰私帳 wall：_require_mine_admin 守的端點（/projects 私帳案清單、/summary）不走這裡。"""
    if payload_grants(check_logged_in(request), "timesheets"):   # 有工作追蹤（或管理員）→ 整份；匿名在這裡 401
        return None
    ident = await resolve_current_staff(request)
    if ident["staff"] is None:
        raise HTTPException(status_code=403, detail="權限不足（需要工作追蹤模組，或帳號綁定人員檔案）")
    return ident["staff"].name


# 員工頁（/my.html）的鑰匙是 me_*，工作追蹤分頁是 timesheets；「我的一天」兩邊都要能用，
# 綁定人員檔案的 409 原句仍只在 core.identity.require_bound_staff。


async def _mine_ident(request: Request) -> dict:
    """「我的一天／今天的專案紀錄／我的一週」的列：總開關（me_today_zone）＋「今天的專案紀錄」或「我的一週」任一把
    （我的一週的卡就是格子的列，兩邊都要能讀寫）＋綁定人員檔案；timesheets 模組恆過。
    （owner 2026-09-08：一顆功能一把、整塊一個總開關；me_profile 只開基本資料卡）"""
    from core.identity import require_zone_staff
    return await require_zone_staff(request, "me_worklog", "me_week_plan")



_CANDS_CACHE: dict = {"at": 0.0, "val": None}


#: /summary 對沒有私帳 scope 的人只回這些鍵（＝ /me/projects_burn 的唯讀面）：沒有 suggested_hours
#: （從私帳預期毛利倒算的，等於間接揭露錢）。
SUMMARY_PUBLIC_KEYS = ("project_id", "project_name", "client", "status", "project_type", "hours_used",
                       "budget_hours", "remaining", "pct", "rows", "last_entry", "stale")
#: 未對映列留給非私帳讀者的鍵：Sheet 案名與時數（團隊頁本來就看得到），不帶 candidates／suggestions。
UNMATCHED_PUBLIC_KEYS = ("project_name", "hours_used", "rows", "reason")


def _redact_summary(out: dict) -> dict:
    """抹掉 /summary 裡跟私帳有關的欄位（純函式，給 burn_summary 用）。"""
    out["projects"] = [{k: it.get(k) for k in SUMMARY_PUBLIC_KEYS} for it in out["projects"]]
    out["unmatched"] = [{k: u.get(k) for k in UNMATCHED_PUBLIC_KEYS} for u in out["unmatched"]]
    return out
