"""core/journal_logic.py — 每週工作日誌純函式：週正規化 + 可編輯窗 + 條目清洗
+ 草稿／送出狀態機 + 「上週做了什麼」分組 + 求助判定（docs/JOURNAL_WORKLOG_PLAN.md §13–§14）。

照 core/hr_logic.py 慣例：純函式、無 I/O，單元測試在 tests/unit/test_journal.py 與
tests/unit/test_journal_worklog_api.py。routers/api_journal.py 只留 I/O。
"""
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

# 單區條目數上限 / 單條字數上限（超過 → 400）
MAX_ENTRIES_PER_SECTION = 50
MAX_ENTRY_LEN = 2000

#: 週記狀態（work_journals.status）；殼不存在＝"none"（只在回應裡出現，不落庫）
JOURNAL_STATUSES = ("draft", "submitted")
#: 條目標記（journal_*.flag）：需要協助／想討論
ENTRY_FLAGS = ("help", "discuss")
#: 只有這兩區可以標記（owner §2-B4：「挑戰」與「其他」）；其他區的 flag 靜默丟掉
FLAG_SECTIONS = ("challenges", "others")


def week_start_of(d) -> date:
    """任何日期 → 該週週一（date）。接受 date 或 datetime；週以週一起算
    （週日輸入 → 回前一個週一）。"""
    if isinstance(d, datetime):
        d = d.date()
    return d - timedelta(days=d.weekday())


def editable_window_ok(week_start: date, today: Optional[date] = None) -> bool:
    """可編輯窗（owner 規則 2026-07-22）：week_start <= 本週週一 + 7 天
    ＝ 過往任何週、當週、下一週恆可編；更遠未來拒絕（403「只能編輯到下一週」）。"""
    today = today or date.today()
    return week_start <= week_start_of(today) + timedelta(days=7)


def clean_entries(raw) -> Tuple[List[str], Optional[str]]:
    """單一區塊條目清洗：strip + 去空（保持原順序）。

    回 (cleaned, err)：err=None 合法；超限回中文訊息（直接給 400 detail）。
    上限在清洗後檢查 — 純空白條目不佔額度。
    """
    cleaned = [s.strip() for s in (raw or []) if isinstance(s, str) and s.strip()]
    if len(cleaned) > MAX_ENTRIES_PER_SECTION:
        return cleaned, f"單區最多 {MAX_ENTRIES_PER_SECTION} 條"
    for s in cleaned:
        if len(s) > MAX_ENTRY_LEN:
            return cleaned, f"單條上限 {MAX_ENTRY_LEN} 字"
    return cleaned, None


def clean_rich_entries(raw, allow_flag: bool = True) -> Tuple[List[dict], Optional[str]]:
    """§13 版的條目清洗：每項可為字串或 {content, project_id, flag}。

    回 (cleaned, err)：cleaned 每項 {content, project_id, flag}（project_id／flag 空＝None）。
    內容的 strip／去空／上限規則與 clean_entries 完全同一份（content 走它）；
    flag 不在 ENTRY_FLAGS → err；allow_flag=False（順利／學到）的 flag 靜默丟掉。
    """
    def _s(v) -> Optional[str]:
        return v.strip() or None if isinstance(v, str) else None

    items = []
    for it in (raw or []):
        if isinstance(it, str):
            items.append({"id": None, "content": it, "project_id": None, "flag": None})
        elif isinstance(it, dict):
            items.append({"id": _s(it.get("id")), "content": it.get("content") if isinstance(it.get("content"), str) else "",
                          "project_id": _s(it.get("project_id")), "flag": _s(it.get("flag"))})
    _, err = clean_entries([i["content"] for i in items])
    if err:
        return [], err
    out = []
    for it in items:
        c = (it["content"] or "").strip()
        if not c:
            continue
        flag = it["flag"] if allow_flag else None
        if flag is not None and flag not in ENTRY_FLAGS:
            return [], f"標記只能是：{'／'.join(ENTRY_FLAGS)}"
        # id 帶回來＝「還是同一條」：PUT 全量替換時沿用它，掛在上面的主管回覆才不會斷
        out.append({"id": it["id"], "content": c, "project_id": it["project_id"], "flag": flag})
    return out, None


def shell_status(shell) -> str:
    """殼 → 對外狀態：沒殼 "none"；status 空（migration 前建的）視為 submitted。"""
    if shell is None:
        return "none"
    return (getattr(shell, "status", None) or "submitted")


def status_after_put(current: Optional[str], requested: Optional[str]) -> str:
    """PUT /journal/mine 的狀態機：body 只接受 status='draft'（或不帶）；送出走 /submit。

    已送出的週記再改**不降回草稿**（§13：送出後仍可改、再送出只更新時間）。
    requested 不合法 → ValueError（端點 422）。
    """
    if requested not in (None, "", "draft"):
        raise ValueError("status 只能是 draft；送出請走 /journal/mine/submit")
    if current == "submitted":
        return "submitted"
    return "draft"


def flag_counts(entries: dict) -> dict:
    """{help: n, discuss: n}（entries＝{區: [{…, flag}]}）。"""
    out = {k: 0 for k in ENTRY_FLAGS}
    for items in (entries or {}).values():
        for it in items or []:
            f = (it or {}).get("flag")
            if f in out:
                out[f] += 1
    return out


def unanswered_flagged(entries: dict, replied_entry_ids) -> list:
    """求助判定（§2-E2）：有標記、而且**沒有任何回覆**的條目。
    entries＝{區: [{id, content, flag, project_id}]}；replied_entry_ids＝有回覆的 entry id 集合。"""
    replied = set(replied_entry_ids or ())
    out = []
    for section, items in (entries or {}).items():
        for it in items or []:
            if (it or {}).get("flag") in ENTRY_FLAGS and it.get("id") not in replied:
                out.append({"section": section, **it})
    return out


def group_worklog(rows) -> list:
    """「上週做了什麼」自動區（§2-B1）：本人該週 timesheets 列 → 按案子分組、每天的內容＋工作階段。

        [{project_id, project_name, days:[{date, items:[{note, work_type, stage_name}]}]}]

    **不含 hours**（owner 鐵則：週記不顯示小時）。列可為 ts_dict 形狀的 dict 或有同名屬性的物件。
    沒內容也沒階段也沒分類的列跳過（沒東西可看）；案子照第一次出現的日期排、天升冪。
    """
    def g(r, k):
        return r.get(k) if isinstance(r, dict) else getattr(r, k, None)

    projects: dict = {}
    order: list = []
    norm = []
    for r in rows or []:
        note = (g(r, "task_note") or "").strip()
        stage = (g(r, "stage_name") or "").strip()
        wt = (g(r, "work_type") or "").strip()
        if not (note or stage or wt):
            continue
        d = g(r, "date") or g(r, "work_date") or ""
        if isinstance(d, datetime):
            d = d.date().isoformat()
        elif isinstance(d, date):
            d = d.isoformat()
        norm.append((str(d)[:10], (g(r, "project_id") or "") or "", (g(r, "project_name") or "") or "", note, wt, stage))
    for d, pid, pname, note, wt, stage in sorted(norm, key=lambda x: x[0]):
        key = pid or pname
        if key not in projects:
            projects[key] = {"project_id": pid, "project_name": pname or "(未填案名)", "days": []}
            order.append(key)
        days = projects[key]["days"]
        if not days or days[-1]["date"] != d:
            days.append({"date": d, "items": []})
        days[-1]["items"].append({"note": note, "work_type": wt, "stage_name": stage})
    return [projects[k] for k in order]
