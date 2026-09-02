"""core/hr_logic.py — 人事（請假/工時）純函式：判定邏輯 + 解析/序列化。

照 CLAUDE.md 慣例：純函式、無 I/O。請假／額度那半給 api_hr 與 api_me
（tests/unit/test_hr_logic.py）；工時 Sheet 對映那半給 api_timesheets、
services/timesheet_lookup、scripts/import_timesheets（tests/unit/test_timesheet_import.py）。
router 端只留 I/O。
"""
import re
from datetime import datetime
from typing import NamedTuple, Optional

LEAVE_TYPES = ("特休", "病假", "事假", "公假", "婚假", "喪假", "其他")
LEAVE_STATUSES = ("待審", "已核准", "已退回")
ANNUAL_TYPE = "特休"


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
        "start_date": o.start_date.strftime("%Y-%m-%d") if o.start_date else "",
        "end_date": o.end_date.strftime("%Y-%m-%d") if o.end_date else "",
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


def manual_dup_key(staff_name: str, work_date: Optional[datetime],
                   project_name: str) -> tuple:
    """工時雙來源去重鍵（藍圖 §3.6 階段3：同人+日+專案，手填優先於 Sheet）。

    ingest 落列前與 source='manual' 既有列比對此鍵。日期一律以**本地時區**取
    date()：timestamptz 欄位寫入時是 naive（PG 依伺服器時區解讀）、asyncpg 讀回
    是 aware UTC —— 直接 .date() 在 +08 時區會差一天（17 日 00:00 寫入 → 讀回
    16 日 16:00Z）。astimezone() 對 naive 視為本地時間、對 aware 轉回本地，
    兩種型態都落在同一個本地日。
    """
    return ((staff_name or "").strip(),
            work_date.astimezone().date() if work_date else None,
            (project_name or "").strip())


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

    🔴 呼叫端一律先用 local_day() 把 timestamptz 轉成本地日期再進來。
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
    import difflib
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
