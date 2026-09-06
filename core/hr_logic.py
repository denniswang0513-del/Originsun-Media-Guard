"""core/hr_logic.py — 人事（請假/工時）純函式：判定邏輯 + 解析/序列化。

照 CLAUDE.md 慣例：純函式、無 I/O。請假／額度那半給 api_hr 與 api_me
（tests/unit/test_hr_logic.py）；工時 Sheet 對映那半給 api_timesheets、
services/timesheet_lookup、scripts/import_timesheets（tests/unit/test_timesheet_import.py）。
router 端只留 I/O。
"""
import calendar
import difflib
import re
from datetime import date, datetime, timedelta
from typing import NamedTuple, Optional
from zoneinfo import ZoneInfo

LEAVE_TYPES = ("特休", "病假", "事假", "公假", "婚假", "喪假", "其他")
LEAVE_STATUSES = ("待審", "已核准", "已退回")
ANNUAL_TYPE = "特休"

# ── 日期歸一（工時整組共用；routers/crm/_shared._fmt_day 委派到這裡）────────────
#
# ⚠ 叫 tw_day 不叫 local_day：core.finance_logic 已有一支 local_day（系統時區、回 naive datetime），
# 兩支同名擺在 core/ 相鄰 router 各 import 一支，回傳型別不同會踩。
#
# 🔴 timestamptz 寫入端是 naive（PG 依 session 時區解讀）、asyncpg 讀回是 aware UTC ——
# 面值取 .date() 在 +08 會差一天。這裡是唯一一份：aware 轉台北，naive 視為本地 wallclock。
_TW = ZoneInfo("Asia/Taipei")


def tw_day(dt) -> Optional[date]:
    """timestamptz／datetime → 台北的日期；None → None。"""
    if not dt:
        return None
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt
    return (dt.astimezone(_TW) if dt.tzinfo else dt).date()


def iso_ts(dt) -> Optional[str]:
    """時間戳的 API 字串（None → None）；週記／手機／場次三個 router 原本各寫一份。"""
    return dt.isoformat() if dt else None


def midnight_of(d) -> datetime:
    """date → 那天 00:00 的 naive datetime（DB 範圍查詢的下界；週一起算的四個地方原本各拼一次）。"""
    return datetime(d.year, d.month, d.day)


def day_iso(dt) -> Optional[str]:
    """tw_day 的字串版 'YYYY-MM-DD'；None → None（API 回日期一律走這裡，不 strftime 面值）。"""
    d = tw_day(dt)
    return d.isoformat() if d else None


def bucket_hours(pairs) -> dict:
    """(key, hours) → {key: 小時和（一位小數）}；只算 hours>0（計畫列不進）、key None 跳過。
    專案各月／人員熱圖與案別／團隊各月都是這一支。"""
    acc: dict = {}
    for k, h in pairs:
        if k is not None and (h or 0) > 0:
            acc[k] = round(acc.get(k, 0.0) + float(h), 1)
    return acc


def month_key(d) -> str:
    """date／datetime → 'YYYY-MM'（API 的 month 欄與各月加總同一個寫法）。"""
    return d.strftime("%Y-%m")


def by_month(day_hours) -> list:
    """(day, hours) → [('YYYY-MM', 小時), …] 依月排序（day None 跳過）。"""
    return sorted(bucket_hours((month_key(d), h) for d, h in day_hours if d).items())


def month_span(month: str) -> tuple:
    """'YYYY-MM'（空＝本月）→ (月初 00:00, 下月初 00:00) naive datetime；格式錯 → ValueError。"""
    try:
        base = datetime.strptime(month, "%Y-%m") if month else datetime.now()
    except ValueError:
        raise ValueError("month 格式需 YYYY-MM")
    m0 = base.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    m1 = m0.replace(year=m0.year + 1, month=1) if m0.month == 12 else m0.replace(month=m0.month + 1)
    return m0, m1


def months_back(m0: datetime, n: int) -> datetime:
    """月初往前 n 個月的月初（n=11 → 含本月共 12 個月）。"""
    y, m = m0.year, m0.month - n
    while m <= 0:
        y, m = y - 1, m + 12
    return m0.replace(year=y, month=m)


def is_workday(day: date) -> bool:
    return day.weekday() < 5


def prev_workday(day: date) -> date:
    """前一個工作日（週一 → 上週五）。國定假日不扣 —— 這是參考，不是打卡。"""
    d = day - timedelta(days=1)
    while not is_workday(d):
        d -= timedelta(days=1)
    return d


def budget_burn(total, budget) -> dict:
    """預算消耗：{remaining, pct}；沒預算兩個都 None。burn 表／專案檔案／團隊匯總同一份算法。"""
    if not budget:
        return {"remaining": None, "pct": None}
    total = float(total or 0)
    return {"remaining": round(budget - total, 1), "pct": round(total / budget * 100, 1)}


def parse_ymd(raw: Optional[str]) -> Optional[datetime]:
    """YYYY-MM-DD → datetime；空/壞格式回 None（呼叫端決定要不要 422）。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        return None


def leave_to_dict(o) -> dict:
    """HrLeaveRequest → API dict（api_hr / api_me 共用序列化）。"""
    return {
        "id": o.id, "staff_id": o.staff_id, "staff_name": o.staff_name or "",
        "leave_type": o.leave_type,
        "start_date": day_iso(o.start_date) or "",
        "end_date": day_iso(o.end_date) or "",
        "days": o.days, "reason": o.reason or "",
        "status": o.status,
        "approved_by": o.approved_by or "",
        "approved_at": o.approved_at.isoformat() if o.approved_at else None,
        "created_by": o.created_by or "",
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }


def validate_leave(leave_type: str, start: Optional[datetime],
                   end: Optional[datetime], days: float) -> Optional[str]:
    """請假單欄位驗證。合法回 None，否則回錯誤訊息（中文，直接給 422 detail）。"""
    if leave_type not in LEAVE_TYPES:
        return f"假別需為：{'/'.join(LEAVE_TYPES)}"
    if start is None or end is None:
        return "起訖日期必填"
    if end < start:
        return "迄日不可早於起日"
    if days <= 0:
        return "天數需大於 0"
    if round(days * 2) != days * 2:
        return "天數以 0.5 天為最小單位"
    return None


def leave_balance(annual_days: Optional[int], approved_annual_sum: float) -> dict:
    """特休餘額 — 即時算，不另存 ledger（HR_FIN_PLAN H2）。

    annual_days 未設定（None）→ annual/remaining 回 None（前端顯示「未設定」），
    used 照算讓管理者仍看得到已休天數。
    """
    used = round(approved_annual_sum or 0.0, 1)
    if annual_days is None:
        return {"annual": None, "used": used, "remaining": None}
    return {"annual": annual_days, "used": used,
            "remaining": round(annual_days - used, 1)}


HOURS_PER_WORKDAY = 8          # 參考工時（工作日 × 8），只對照不是打卡標準；儀表板同一個數


def month_workdays(year: int, month: int) -> int:
    """該月週一到週五的天數（不扣國定假日 —— 那是參考值，不是打卡標準）。"""
    return sum(1 for d in range(1, calendar.monthrange(year, month)[1] + 1)
               if is_workday(date(year, month, d)))


def week_key(day) -> str:
    """ISO 週鍵 'YYYY-Www'（跨年那幾天歸 ISO 年，週一起算）。"""
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def hours_rollup(rows, year: int, month: int) -> dict:
    """團隊月表的「幫大家算好」：`rows` = [(staff_name, day(date), project_name, hours), …]。

    每人：合計、填了幾天、平均每天、每週小計、各案小計；全體：各案合計、總時數；
    參考工時＝工作日×8（只是對照，不是標準工時）。純函式，端點只做 I/O。
    🔴 只算實際（hours > 0）：只有計畫的列不是工時、也不算「填了一天」—— 呼叫端不必先濾。"""
    people: dict = {}
    projects: dict = {}
    for name, day, pname, h in rows:
        h = float(h or 0)
        if h <= 0:
            continue
        p = people.setdefault(name or "(空白)", {"name": name or "(空白)", "total": 0.0, "days": set(),
                                                  "weeks": {}, "projects": {}})
        p["total"] += h
        if day:
            p["days"].add(day)
            wk = week_key(day)
            p["weeks"][wk] = p["weeks"].get(wk, 0.0) + h
        p["projects"][pname or "(空白)"] = p["projects"].get(pname or "(空白)", 0.0) + h
        projects[pname or "(空白)"] = projects.get(pname or "(空白)", 0.0) + h
    out_people = []
    for p in people.values():
        days = len(p["days"])
        out_people.append({
            "name": p["name"], "total": round(p["total"], 1), "days_filled": days,
            "avg_per_day": round(p["total"] / days, 1) if days else 0.0,
            "weeks": {k: round(v, 1) for k, v in sorted(p["weeks"].items())},
            "projects": sorted(((k, round(v, 1)) for k, v in p["projects"].items()), key=lambda x: -x[1]),
        })
    out_people.sort(key=lambda x: -x["total"])
    wd = month_workdays(year, month)
    return {
        "workdays": wd, "reference_hours": wd * HOURS_PER_WORKDAY,
        "people": out_people,
        "projects": sorted(((k, round(v, 1)) for k, v in projects.items()), key=lambda x: -x[1]),
        "total": round(sum(projects.values()), 1),
    }


#: 專案「停滯」門檻：進行中（製作）但這麼多天沒工時（docs/WORK_TRACKING_UI_PLAN.md §7 主管角度）
STALE_DAYS = 7


def is_stale(status, last_entry, today, days: int = STALE_DAYS) -> bool:
    """進行中的案、最後一筆工時在 `days` 天以前（或根本沒有）→ 停滯。結案／未開工的不算。"""
    if (status or "") != "製作":
        return False
    if last_entry is None:
        return True
    return (today - last_entry).days > days


#: 「最近有在填的人」的視窗（漏填名單用；儀表板與週一 digest 同一個數字）
ACTIVE_WINDOW_DAYS = 30


def active_fillers(rows, today: date) -> set:
    """`rows` = [(staff_name, day, hours), …] → 最近 ACTIVE_WINDOW_DAYS 天有實際時數的人名
    （只排計畫沒記實際的不算「有在填」，跟 fillers_on 同一條，漏填名單才不會永遠有他）。"""
    since = today - timedelta(days=ACTIVE_WINDOW_DAYS)
    return {n for n, d, h in rows if n and d and d >= since and (h or 0) > 0}


def type_composition(pairs) -> list:
    """`[(work_type, hours), …]` → `[(type, hours, pct), …]` 大到小；沒分類的歸「未分類」；
    只算實際（hours > 0）。"""
    acc: dict = {}
    for t, h in pairs:
        if (h or 0) <= 0:
            continue
        acc[t or "未分類"] = acc.get(t or "未分類", 0.0) + float(h or 0)
    total = sum(acc.values())
    return sorted(((k, round(v, 1), round(v / total * 100) if total else 0) for k, v in acc.items()),
                  key=lambda x: -x[1])


def project_metrics(items) -> dict:
    """一案的形狀：`items` = [(day(date|None), staff_name, work_type, hours), …]。
    總時數、人數、起訖、跨了幾天、分類組成、各人 —— 專案檔案頁與「類似專案並排」共用。
    只算實際（hours > 0），計畫列不進來。"""
    total = 0.0
    people: dict = {}
    days = []
    for day, name, wt, h in items:
        h = float(h or 0)
        if h <= 0:
            continue
        total += h
        people[name or "(空白)"] = people.get(name or "(空白)", 0.0) + h
        if day:
            days.append(day)
    first, last = (min(days), max(days)) if days else (None, None)
    return {
        "total": round(total, 1),
        "people": len(people),
        "by_person": sorted(((k, round(v, 1)) for k, v in people.items()), key=lambda x: -x[1]),
        "first": first.isoformat() if first else None,
        "last": last.isoformat() if last else None,
        "span_days": (last - first).days + 1 if days else 0,
        "composition": type_composition((wt, h) for _d, _n, wt, h in items),
    }


def similar_projects(name: str, client: str, total: float, candidates, limit: int = 5, floor: float = 0.35) -> list:
    """類似專案（自動推薦，人再挑）：案名相似（去前綴）0.5 ＋ 同客戶 0.3 ＋ 時數量級接近 0.2。
    `candidates` = [{"name", "client", "total"}, …]；排掉自己與零時數。回 [(name, score), …]。"""
    key = sheet_project_key(name)
    if not key:
        return []
    sm = difflib.SequenceMatcher(None, "", key)
    out = []
    for c in candidates:
        if c["name"] == name or (c.get("total") or 0) <= 0:
            continue
        sm.set_seq1(sheet_project_key(c["name"]))
        score = 0.5 * sm.ratio()
        if client and c.get("client") and c["client"] == client:
            score += 0.3
        if total > 0:
            ratio = (c["total"] or 0) / total
            if 0.5 <= ratio <= 2.0:
                score += 0.2
        if score >= floor:
            out.append((c["name"], round(score, 2)))
    out.sort(key=lambda x: -x[1])
    return out[:limit]


def fillers_on(rows, day) -> set:
    """`rows` = [(staff_name, day, hours), …] → 那一天有實際時數（>0）的人名；只有計畫不算填了。
    儀表板「昨天漏填」與週一 digest 都吃這一支。"""
    return {n for n, d, h in rows if n and d == day and (h or 0) > 0}


def missing_fillers(active_names, filled_names) -> list:
    """漏填：最近有在填的人裡，那一天沒有任何列的（主管層用，不給全員看）。"""
    return sorted(set(active_names) - set(filled_names))


def digest_text(week_label: str, rollup: dict, missing_days: dict) -> str:
    """週一 digest（Google Chat 純文字，無 emoji）：每人上週合計／填了幾天，漏填天數另列。"""
    lines = [f"【上週工時】{week_label}　全體 {rollup['total']} h"]
    for p in rollup["people"]:
        miss = missing_days.get(p["name"], 0)
        lines.append(f"{p['name']}：{p['total']} h（{p['days_filled']} 天）" + (f"　漏填 {miss} 天" if miss else ""))
    if not rollup["people"]:
        lines.append("上週沒有任何工時紀錄")
    lines.append("→ 後台 人事管理 › 工作追蹤")
    return "\n".join(lines)


#: 工作分類（docs/WORK_TRACKING_UI_PLAN.md §3-A，owner 2026-09-03「照建議」）：固定選項，
#: 跨案可比的形狀（拍攝 40h／剪接 120h）靠它；可不填。
WORK_TYPES = ("前期企劃", "拍攝", "剪接", "動態／特效", "調光", "聲音", "會議溝通", "行政", "其他")


def norm_work_type(v) -> Optional[str]:
    """空＝None；不在清單裡就丟 ValueError（端點回 422）。"""
    s = (v or "").strip()
    if not s:
        return None
    if s not in WORK_TYPES:
        raise ValueError(f"工作分類只能是：{'／'.join(WORK_TYPES)}")
    return s


#: 工作階段種子（docs/JOURNAL_WORKLOG_PLAN.md §12）：**每個分類自己的階段清單**，不是一棵路徑樹。
#: 存 work_stage_nodes（固定兩層：depth1＝分類、名稱以 WORK_TYPES 為 key；depth2＝階段）。
#: 🔴 只在表空時寫入（routers/crm/work_stages.seed_if_empty），之後全由 owner 在編輯器改。
STAGE_SEED = {
    "前期企劃": ("提案", "分鏡", "會議"),
    "拍攝": ("勘景", "現場", "備份"),
    "剪接": ("A-copy", "B-copy", "Fine cut", "Fine cut 修改", "定剪", "輸出"),
    "動態／特效": ("分鏡", "初版", "修改", "定版"),
    "調光": ("初調", "修改", "定版"),
    "聲音": ("配樂", "混音", "定版"),
    "會議溝通": ("內部", "客戶"),
    "行政": ("指派事項", "庶務"),
    "其他": (),
}


def _node_get(n, key, default=None):
    """work_stage_nodes 的列可能是 ORM 物件或 dict —— 純函式兩種都吃。"""
    if isinstance(n, dict):
        return n.get(key, default)
    return getattr(n, key, default)


def stage_categories(nodes, include_inactive: bool = True) -> list:
    """work_stage_nodes 全部列 → `[{id, name, sort, active, stages:[{id, name, sort, active}]}]`。

    分類（depth1）依 WORK_TYPES 的順序、不在九類的排最後；階段依 sort 再名稱。
    include_inactive=False 時停用的階段不列（下拉用）；分類本身永遠列（九格固定）。
    """
    cats = [n for n in nodes if int(_node_get(n, "depth", 1) or 1) == 1]
    kids: dict = {}
    for n in nodes:
        if int(_node_get(n, "depth", 1) or 1) == 2:
            kids.setdefault(_node_get(n, "parent_id", "") or "", []).append(n)
    order = {name: i for i, name in enumerate(WORK_TYPES)}

    def _key(n):
        return (order.get(_node_get(n, "name", ""), len(order)), int(_node_get(n, "sort", 0) or 0), _node_get(n, "name", "") or "")

    out = []
    for c in sorted(cats, key=_key):
        stages = sorted(kids.get(_node_get(c, "id"), []),
                        key=lambda s: (int(_node_get(s, "sort", 0) or 0), _node_get(s, "name", "") or ""))
        out.append({
            "id": _node_get(c, "id"), "name": _node_get(c, "name", "") or "",
            "sort": int(_node_get(c, "sort", 0) or 0), "active": bool(_node_get(c, "active", 1)),
            "stages": [{"id": _node_get(s, "id"), "name": _node_get(s, "name", "") or "",
                        "sort": int(_node_get(s, "sort", 0) or 0), "active": bool(_node_get(s, "active", 1))}
                       for s in stages if include_inactive or _node_get(s, "active", 1)],
        })
    return out


def stages_by_category(nodes) -> dict:
    """`GET /timesheets/options` 的 `stages`：{分類名: [{id, name}]}，只回 active。"""
    return {c["name"]: [{"id": s["id"], "name": s["name"]} for s in c["stages"]]
            for c in stage_categories(nodes, include_inactive=False)}


def stage_index(nodes) -> dict:
    """{stage_id: {id, name, category, active}}（只收 depth2；分類名從 parent 反查）—— 正規化用。"""
    cat_name = {_node_get(n, "id"): (_node_get(n, "name", "") or "")
                for n in nodes if int(_node_get(n, "depth", 1) or 1) == 1}
    return {_node_get(n, "id"): {"id": _node_get(n, "id"), "name": _node_get(n, "name", "") or "",
                                 "category": cat_name.get(_node_get(n, "parent_id", "") or "", ""),
                                 "active": bool(_node_get(n, "active", 1))}
            for n in nodes if int(_node_get(n, "depth", 1) or 1) == 2}


def resolve_stage(stage_id, work_type, index: dict):
    """一列的 stage_id → 節點 dict；空＝None。找不到、或不屬於該列分類 → ValueError（端點回 422）。
    停用的階段**不擋**：舊列重存還帶著它，不該因為 owner 後來停用而存不回去。"""
    s = (stage_id or "").strip()
    if not s:
        return None
    st = index.get(s)
    if st is None:
        raise ValueError("找不到這個工作階段")
    if st["category"] != (work_type or ""):
        raise ValueError(f"工作階段「{st['name']}」不屬於分類「{work_type or '（未填）'}」")
    return st


def row_state(hours, planned_hours) -> str:
    """一列是「只有計畫」還是「有實際」：hours>0 → draft（實際）；否則有 planned → plan；
    兩個都沒有＝不合法（呼叫端先擋）。計畫列的 hours 存 0，燒錄／匯總只算 hours。"""
    if (hours or 0) > 0:
        return "draft"
    if (planned_hours or 0) > 0:
        return "plan"
    raise ValueError("時數或計畫小時至少一個要大於 0")


#: 本人可改／可刪的手填列狀態（docs/TIMESHEET_SELF_ENTRY_PLAN.md D3／D5）：
#: 不審核，所以 plan／draft 都能改；locked 留給日後月結。
EDITABLE_STATUSES = frozenset({"plan", "draft"})   # 不審核：沒有 confirmed／approved


#: can_edit_timesheet 的代碼 → 給人看的原因；HTTP 狀態由代碼決定，不靠中文比對
EDIT_BLOCK_TEXT = {
    "not_owner": "不是你的工時列",
    "not_manual": "Sheet 同步進來的列不能在這裡改，請改試算表",
    "locked": "這一列已鎖，不能再改",
}


def can_edit_timesheet(row, staff_id: str) -> str:
    """本人能不能改這一列：回空字串＝可以，否則回代碼（EDIT_BLOCK_TEXT 的鍵）。

    三個條件缺一不可：是本人的（staff_id）、是手填的（Sheet 同步進來的改 Sheet 那邊再拉）、
    還沒鎖。`row` 只要有 .staff_id／.source／.status。
    """
    if not staff_id or getattr(row, "staff_id", None) != staff_id:
        return "not_owner"
    if getattr(row, "source", "") != "manual":
        return "not_manual"
    if getattr(row, "status", "") not in EDITABLE_STATUSES:
        return "locked"
    return ""


def manual_dup_key(staff_name: str, work_date: Optional[datetime],
                   project_name: str) -> tuple:
    """工時雙來源去重鍵（藍圖 §3.6 階段3：同人+日+專案，手填優先於 Sheet）。

    ingest 落列前與 source='manual' 既有列比對此鍵。日期一律以**本地時區**取
    date()：timestamptz 欄位寫入時是 naive（PG 依伺服器時區解讀）、asyncpg 讀回
    是 aware UTC —— 直接 .date() 在 +08 時區會差一天（17 日 00:00 寫入 → 讀回
    16 日 16:00Z）。astimezone() 對 naive 視為本地時間、對 aware 轉回本地，
    兩種型態都落在同一個本地日。
    """
    return ((staff_name or "").strip(), tw_day(work_date), (project_name or "").strip())


# ── 福委會（docs/BENEFIT_POOL_PLAN.md）純規則 ────────────────────────
#
# owner 2026-08-21：「我有幾個福利池，一個是快樂、一個是進修，這兩塊員工都可以
# 登記，他們登記後我審核通過，就進公司請款。我每年會撥一筆錢進這個池。」
# 池的名字是資料不是程式碼 —— 不寫死成枚舉，owner 想再開一個池就自己開。

BENEFIT_STATUSES = ("待審", "已核准", "已付款", "退回")
# 還在本人手上、可以自己改自己刪的（送出去之前 owner 還沒看過）
BENEFIT_EDITABLE = ("待審", "退回")
# 已經確定要花的錢 —— 餘額扣的是它。🔴 待審**不算**：待審就扣的話退件之後
# 餘額要回沖，那是一種很容易對不起來的帳（畫面另外顯示「審核中」就夠了）。
BENEFIT_COMMITTED = ("已核准", "已付款")
# （「不能改」是 BENEFIT_EDITABLE 的補集 —— 已核准／已付款進了公司請款，
#   帳上掛著一張應付款，這裡改金額兩邊會對不起來。不另立一個常數。）


def benefit_pool_balance(fundings, entries) -> dict:
    """池的用量。`fundings` 是金額序列（每年撥進來的），`entries` 是
    (status, amount) 序列（員工登記的花費，金額一律正數）。

    四個數字分開回，因為它們回答不同的問題：
      funded   歷年撥進來的總額
      used     已經確定要花的（已核准＋已付款）→ 餘額扣的是它
      pending  審核中 → 不扣餘額，但 owner 要看得到「還有多少在路上」
      balance  funded − used，**可以是負的** —— 超支要看得見，
               夾成 0 等於把問題藏起來（真的花超了，畫面就該是紅的）。
    """
    funded = sum(int(a or 0) for a in fundings)
    used = pending = 0
    for status, amount in entries:
        a = int(amount or 0)
        if status in BENEFIT_COMMITTED:
            used += a
        elif status == "待審":
            pending += a
    return {"funded": funded, "used": used, "pending": pending,
            "balance": funded - used, "over": funded - used < 0}


def validate_benefit_entry(title, amount) -> str:
    """員工登記一筆的欄位檢查。回錯誤訊息字串，空字串＝過關。

    項目是自由文字（電影名／餐廳／課程名）—— 不做枚舉。分類這件事由**池**
    承擔（快樂／進修），再加一層項目分類只是逼人每次多選一格。
    """
    if not (title or "").strip():
        return "請填項目（花在什麼上）"
    if int(amount or 0) <= 0:
        return "金額要大於 0"
    return ""


def benefit_can_edit(status: str) -> bool:
    """這筆還在本人手上嗎（可以自己改／自己刪）。"""
    return (status or "") in BENEFIT_EDITABLE


# ── 每人額度（LAZY KIT 那種年度活動，docs/BENEFIT_POOL_PLAN.md §9）──────

QUOTA_MODES = ("shared", "per_person")


def in_window(day, start, end) -> bool:
    """day 落在 [start, end] 之內嗎。三個都是 date（不是 datetime）。

    🔴 呼叫端一律先用 tw_day() 把 timestamptz 轉成本地日期再進來。
    直接拿 datetime 比會差一天 —— 讀回來是 UTC，這個 repo 已經被咬過三次。
    邊界含在內：券寫 2026/01/01–12/31，那兩天當然算數。
    """
    if day is None:
        return False
    if start is not None and day < start:
        return False
    if end is not None and day > end:
        return False
    return True


def staff_allowance_balance(amount, entries) -> dict:
    """某個人在某個活動裡的額度用量。

    `entries` 是 (status, amount, in_window) 序列 —— **期間判定在呼叫端做完**
    再傳進來，這支保持無日期、無 I/O（好測、也不會偷偷多一套時區邏輯）。

    期間外的登記不計入 used —— 它本來就不該被核准（見 validate_against_allowance）。
    """
    quota = int(amount or 0)
    used = pending = 0
    for status, amt, inside in entries:
        if not inside:
            continue
        a = int(amt or 0)
        if status in BENEFIT_COMMITTED:
            used += a
        elif status == "待審":
            pending += a
    # 🔴 `available` 跟共用池不一樣：**待審也佔住**自己的額度。
    #    共用池「待審不扣」是對的（桶子大、退件不用回沖）；但個人額度只有
    #    10,000 時，那條規則會讓同一個人連送三筆 10,000 全部待審都不被擋
    #    —— 擋超額等於形同虛設，等 owner 一核准就爆了。這筆是他自己的待審、
    #    佔的是他自己的額度，退回就放回來，不牽涉別人，所以沒有回沖問題。
    return {"quota": quota, "used": used, "pending": pending,
            "balance": quota - used, "available": quota - used - pending,
            "over": quota - used < 0}


def pool_allowance_rollup(allowances, used_by_staff) -> dict:
    """整個活動的配發概況（管理端看的）。

    `allowances` 是金額序列，`used_by_staff` 是每個人已用金額的序列。
    「還沒動用的人數」是 owner 真正想知道的 —— 券發下去沒人用才是問題。
    """
    granted = sum(int(a or 0) for a in allowances)
    used = sum(int(u or 0) for u in used_by_staff)
    return {"granted": granted, "used": used, "balance": granted - used,
            "people": len(list(allowances)),
            "untouched": sum(1 for u in used_by_staff if int(u or 0) == 0)}


def validate_against_allowance(amount, spend_day, allowance) -> str:
    """每人額度模式下，這筆登記過得了嗎。回錯誤訊息，空字串＝過關。

    🔴 這裡**要擋**，跟共用池不同。共用池超支只轉紅不擋 —— 那是公司該知道的
    事實；個人額度超額是「這張券本來就沒這麼多」，讓它過去只是把問題推到
    請款那一關才爆（那時已經有人以為自己花得起了）。

    `allowance` 是 dict：{amount, valid_from, valid_to, available}，None ＝沒發給他。
    `available` ＝ 額度 − 已用 − **審核中**（見 staff_allowance_balance 的 🔴）。
    """
    if not allowance:
        return "這個活動沒有發給你額度，請找管理員"
    if not in_window(spend_day, allowance.get("valid_from"),
                     allowance.get("valid_to")):
        lo = allowance.get("valid_from")
        hi = allowance.get("valid_to")
        span = f"{lo or '不限'} ~ {hi or '不限'}"
        return f"日期不在活動期間內（{span}）"
    left = int(allowance.get("available") or 0)
    a = int(amount or 0)
    if a > left:
        return f"超過你的額度（還可以用 ${left:,}）"
    return ""


# ── 工時 Sheet 專案名 → 專案（docs/TIMESHEET_IMPORT_PLAN.md §2 D1／§4）─────────

#: Sheet 上不是專案的桶（行政、結案、提案…）—— 不對映、`project_name` 留桶名。
INTERNAL_BUCKETS = frozenset({
    "行政庶務", "結案作業", "提案企劃", "業務開發", "資訊工程", "客戶服務", "動畫小劇場",
})


def split_sheet_name(name: str) -> tuple:
    """「客戶_案名」→ `(客戶, 案名)`；沒有底線＝沒有前綴。**只切第一個底線**（案名自己
    可能含底線），案名的空白收成一格。前綴是 Sheet 為了下拉分組加的，不是案名的一部分
    —— Sheet 記「三立電視台_國民法官劇情短片」，私帳案叫「國民法官劇情短片」。
    這條切法**只有這一份**（匯入、同步、預算灌入、remap、報告都走它）。"""
    s = (name or "").strip()
    if "_" in s:
        cli, key = s.split("_", 1)
    else:
        cli, key = "", s
    return cli.strip(), re.sub(r"\s+", " ", key).strip()


def sheet_project_key(name: str) -> str:
    return split_sheet_name(name)[1]


def lookup_row(pid, name, client="") -> dict:
    """查表裡一列（專案或人員）的形狀 `{"id","name","client"}` —— 直接就是回給前端
    的 JSON，router／腳本不必各自從 tuple 重拼一次欄位順序；人員沒有 client，留空字串。"""
    return {"id": pid, "name": (name or "").strip(), "client": client or ""}


def group_by_name(rows) -> dict:
    """`[lookup_row, …]` → `{name: [row, …]}`；同名**不合併** —— 撞名要在判定時被看見，
    不是在建表時被最後一筆蓋掉。空名跳過。專案與人員都吃這一支。"""
    out: dict = {}
    for row in rows:
        if row["name"]:
            out.setdefault(row["name"], []).append(row)
    return out


class Misses:
    """「沒對到」的收集器：撞案／找不到兩桶，四個寫入點（ingest 專案、ingest 人員、
    budgets、手填）共用 —— 容器與回應鍵名各寫一次就會漂（budgets 曾用 list，
    同名重複會回兩次）。"""

    def __init__(self):
        self.ambiguous: set = set()
        self.unmatched: set = set()

    def note(self, why: str, name: str):
        b = miss_bucket(why)
        if b:
            getattr(self, b).add(name)
        return b

    def report(self, kind: str = "projects") -> dict:
        if kind == "staff":
            return {"staff_ambiguous": sorted(self.ambiguous), "staff_unmatched": sorted(self.unmatched)}
        return {"ambiguous_projects": sorted(self.ambiguous), "unmatched_projects": sorted(self.unmatched)}


def unique_hit(hits) -> tuple:
    """「同名不猜」的唯一判定：剛好一個 → `(row, "exact")`；沒有 → `(None, "none")`；
    兩個以上 → `(None, "ambiguous")`。專案與人員都吃這一支。"""
    hits = list(hits or ())
    if len(hits) == 1:
        return hits[0], "exact"
    return None, ("ambiguous" if hits else "none")


def resolve_staff(name: str, index: dict) -> tuple:
    """人員名 → `(staff_id, why)`，同 `resolve_project` 的契約：空名 empty、同名兩人
    ambiguous（不猜）、沒這人 none。`index` 是 `group_by_name` 的結果。"""
    n = (name or "").strip()
    if not n:
        return None, "empty"
    hit, why = unique_hit(index.get(n))
    return (hit["id"], "exact") if hit else (None, why)


def miss_bucket(why: str):
    """判定結果要不要回報、回報到哪一桶：ambiguous → "ambiguous"、none → "unmatched"，
    其他（對到了／內部桶／空名）→ None。ingest（專案、人員）與 budgets 共用，
    各寫一次就會漂（曾經有一處把內部桶也算成「找不到」）。"""
    return {"ambiguous": "ambiguous", "none": "unmatched"}.get(why)


class ProjectLookup(NamedTuple):
    """對映所需的三張表，一個物件帶著走（呼叫端不必各拆成三個位置參數）。

    `project_map`：owner 決定過的 `{Sheet 原字: project_id}`
    `by_name`／`by_key`：`{全名 / 去前綴案名: [(id, name, client_short_name), …]}`
    """
    project_map: dict
    by_name: dict
    by_key: dict

    @classmethod
    def build(cls, project_map: dict, rows) -> "ProjectLookup":
        """`rows` = `[(id, name, client_short_name), …]`（呼叫端只放**私帳**：owner
        2026-09-02「這張表對應的是私帳的專案」）。"""
        norm = [lookup_row(pid, nm, cli) for pid, nm, cli in rows]
        by_name = group_by_name(norm)                 # 同名不合併、空名跳過，同人員那份
        by_key: dict = {}
        for row in norm:
            if row["name"]:
                by_key.setdefault(sheet_project_key(row["name"]), []).append(row)
        return cls(dict(project_map or {}), by_name, by_key)

    def candidates(self, name: str) -> list:
        """撞案時給人看的候選：去前綴後同名的那幾案 `[(id, name, client), …]`。"""
        return list(self.by_key.get(sheet_project_key(name), ()))


def resolve_project(name: str, lk: ProjectLookup) -> tuple:
    """一個 Sheet 專案原字 → `(project_id | None, reason)`。順序即優先序，第一個
    **唯一**命中就停：

        map        owner 在對映表決定過的（永遠優先）
        bucket     內部桶 → 不對映
        exact      全名精確同名（唯一）
        key        去客戶前綴後同名（唯一）
        key+client 去前綴後撞多案，但 Sheet 前綴＝其中唯一一案的客戶簡稱（以前綴開頭）
        ambiguous  仍 >1 → **不猜**，留給 owner 指定
        none       找不到

    🔴 絕不 fuzzy 自動合併（同 scripts/import_my_projects.py 的鐵則）。
    """
    n = (name or "").strip()
    if not n:
        return None, "empty"
    if n in lk.project_map:
        return lk.project_map[n], "map"
    if n in INTERNAL_BUCKETS:
        return None, "bucket"
    hit, _ = unique_hit(lk.by_name.get(n))
    if hit:
        return hit["id"], "exact"
    cli, key = split_sheet_name(n)
    hits = lk.by_key.get(key) or []
    hit, why = unique_hit(hits)
    if hit:
        return hit["id"], "key"
    if why == "ambiguous":
        # Sheet 前綴是客戶的**簡稱**（「典藏藝術家庭」），clients.short_name 常是全名
        # （「典藏藝術家庭股份有限公司」）—— 認「以前綴開頭」，仍是精確比對不是模糊：
        # 唯一一案的客戶名以這個前綴開頭才算，兩案都符合照樣回 ambiguous。
        hit, _ = unique_hit([h for h in hits if cli and (h["client"] or "").startswith(cli)])
        if hit:
            return hit["id"], "key+client"
        return None, "ambiguous"
    return None, "none"


def remap_target(why: str, current_pid, resolved_pid):
    """remap 對既有一列該不該改、改成什麼 —— 回新 project_id，或 None＝不動。

    三條規則（真值表在 tests/unit/test_timesheet_import.py）：
      · 對映表是 owner 的決定，**永遠覆蓋**（本來自動對到 A、owner 指定 B → 改成 B）
      · 自動規則只補**空的**（不動已經對好的列）
      · **絕不清空**（找不到／撞案不會把既有的 project_id 拿掉）
    """
    if why == "map":
        return resolved_pid if resolved_pid and resolved_pid != current_pid else None
    if resolved_pid and not current_pid:
        return resolved_pid
    return None


def explain_miss(name: str, lk: ProjectLookup) -> dict:
    """一個沒對到的 Sheet 名字要給 owner 看什麼：`{"reason": why}`，撞案附
    `candidates`（`[(id, name, client), …]`），找不到附 `suggestions`（案名清單，只是
    建議）。burn 摘要與匯入腳本的 dry-run 同一份 —— 各寫一次就會出現「報告有建議、
    畫面沒有」。"""
    _pid, why = resolve_project(name, lk)
    out = {"reason": why}
    if why == "ambiguous":
        out["candidates"] = lk.candidates(name)
    elif why == "none":
        out["suggestions"] = [k for k, _sc in suggest_projects(name, lk)]
    return out


def suggest_projects(name: str, lk: ProjectLookup, limit: int = 2, floor: float = 0.6) -> list:
    """找不到時給 owner 看的**建議**（不是自動對映）：去前綴後跟所有私帳案名比相似度，
    回 `[(key, score), …]`，最像的在前、低於 floor 不列。

    🔴 只在報告／讀路徑出現，任何寫入路徑都不准用它 —— 「華南永昌E指通」對「E指沖」
    是打錯字，該由人看一眼決定，不是程式猜。
    """
    a = sheet_project_key(name)
    if not a:
        return []
    # seq2 固定是要找的名字：difflib 對 seq2 做的索引快取整個迴圈都能重用
    sm = difflib.SequenceMatcher(None, "", a)
    scored = []
    for k in lk.by_key:
        sm.set_seq1(k)
        # 短名字被 ratio 罰得太重（「沆涸」對「沆涸 剪輯」只有 0.57）：
        # 一邊整個包含另一邊（≥2 字）就至少當 0.75 —— 仍只是建議
        contains = len(a) >= 2 and len(k) >= 2 and (a in k or k in a)
        # ratio 是 O(n²)；先用 difflib 自己的兩個上界擋掉明顯不像的
        #（get_close_matches 同一招），結果一模一樣
        if not contains and (sm.real_quick_ratio() < floor or sm.quick_ratio() < floor):
            continue
        sc = max(sm.ratio(), 0.75) if contains else sm.ratio()
        if sc >= floor:
            scored.append((k, round(sc, 2)))
    scored.sort(key=lambda x: -x[1])
    return scored[:limit]
