"""core/schemas/_hr.py — N-hr 出缺勤（請假）＋ 工時手填。

拆自 core/schemas.py（2026-09-11，2,000 行剛好卡在單次讀取上限）。
界畫在原本的分節註解上、零 class 搬家；對外仍是 `from core.schemas import X`。
"""
from pydantic import BaseModel, Field  # type: ignore
from typing import List, Optional

from core.media_exts import sorted_video_exts

# ── N-hr H2 出缺勤（請假）──

class LeaveCreate(BaseModel):
    """管理端建立/代登請假單。日期格式 YYYY-MM-DD。
    2026-09-07 假勤重整（docs/LEAVE_PLAN.md §7）：小時為正本 —— part／start_time／end_time 有給就由
    core.leave_logic.working_hours 算 hours；三者都沒給（舊分頁）才拿 days×8。新欄位一律 Optional＝None
    （Cloudflare 給 .js 4 小時快取，舊分頁不帶新欄位）。"""
    staff_id: str
    leave_type: str            # core.leave_logic.ALL_LEAVE_TYPES
    start_date: str
    end_date: str
    days: Optional[float] = None       # 舊契約（0.5 步進）；新分頁不送
    part: Optional[str] = None         # all/am/pm/range；None＝all
    start_time: Optional[str] = None   # 'HH:MM'（part=range）
    end_time: Optional[str] = None
    hours: Optional[float] = None      # 直接指定（代登時管理員可覆寫）
    reason: Optional[str] = None


class LeaveUpdate(BaseModel):
    """PUT /hr/leave/{id} 只改欄位；`status` 留著只為了認出舊分頁還在送它 → 422（改狀態走 approve／reject／cancel_decide）。"""
    status: Optional[str] = None
    leave_type: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    days: Optional[float] = None
    part: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    hours: Optional[float] = None
    reason: Optional[str] = None


class MeLeavePreview(BaseModel):
    """POST /me/leave/preview：算時數＋錯誤／警告，不寫入。"""
    leave_type: str
    start_date: str
    end_date: str
    part: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None


class MeLeaveCreate(BaseModel):
    """員工自助送單（staff_id 由 token 解析，不收）。欄位同 MeLeavePreview ＋ reason（必填）。

    🔴 **不收 hours／days**：時數一律由起迄／時段算（`leave_service.hours_from_body`）。收了的話
    員工可以送「請五天特休、hours: 0.5」，preview 顯示 40 小時、實際只從時數帳扣 0.5 ——
    而且 MeLeavePreview 本來就沒有這兩欄，兩條路會算出不同答案。管理端要手調時數走 /hr 的 LeaveUpdate。"""
    leave_type: str
    start_date: str
    end_date: str
    part: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    reason: Optional[str] = None


class LeaveCancel(BaseModel):
    """POST /me/leave/{id}/cancel：已核准且 <2 天的撤回要說明（cancel_note）。"""
    note: Optional[str] = None


class LeaveReject(BaseModel):
    """POST /hr/leave/{id}/reject：理由必填（端點驗空字串）。"""
    note: Optional[str] = None


class LeaveCancelDecide(BaseModel):
    """POST /hr/leave/{id}/cancel_decide：消假待審 → approve=True 已撤回／False 回已核准。"""
    approve: bool = True
    note: Optional[str] = None


class CreditCreate(BaseModel):
    """POST /hr/credits 手開時數（管理員）：kind 特休/補休/其他；granted_on 必填、expires_on 可空＝永不到期。"""
    staff_id: str
    kind: str
    hours: float
    granted_on: str
    expires_on: Optional[str] = None
    reason: Optional[str] = None
    shoot_id: Optional[str] = None
    source: Optional[str] = None       # 預設 manual
    note: Optional[str] = None


class HolidayCreate(BaseModel):
    date: str
    name: Optional[str] = None
    kind: Optional[str] = None         # 國定假日/補班日/颱風假；預設國定假日


class HolidayImport(BaseModel):
    """POST /hr/holidays/import：貼行政院人事總處的年度行事曆 CSV 原文。"""
    csv: str
    replace_year: Optional[int] = None  # 給了就先清掉該年的國定假日／補班日（颱風假不動）再匯入


class AnnualLeaveSet(BaseModel):
    annual_leave_days: Optional[int] = None   # None = 清除額度設定


# ── 工時手填（與 Sheet 同步共存；同人+日+專案 手填優先）──

class TimesheetManualRow(BaseModel):
    """一個工作項：實際小時（hours）或計畫小時（planned_hours）至少一個 > 0
    （docs/WORK_TRACKING_UI_PLAN.md §2：只有計畫的列 status=plan、hours 存 0）。"""
    work_date: str             # YYYY-MM-DD
    project_id: Optional[str] = None
    project_name: str = ""     # 無 id 時以名稱對映（同 ingest 邏輯）
    task_note: Optional[str] = None
    remark: Optional[str] = None      # 員工備註（內容之外的補充）
    start_time: Optional[str] = None  # 起「HH:MM」；不帶＝不動、"" ＝清空（格子的起訖要存，owner 2026-09-06）
    end_time: Optional[str] = None    # 訖「HH:MM」
    hours: Optional[float] = None
    planned_hours: Optional[float] = None
    work_type: Optional[str] = None   # core.hr_logic.WORK_TYPES 之一，可空
    stage_id: Optional[str] = None    # 工作階段（work_stage_nodes；須屬於該列分類）。更新時 "" ＝清空、不帶＝不動
    bulletin_id: Optional[str] = None # 從待辦帶入的公布欄項目；列標成實際＝那筆待辦 done
    plan: Optional[bool] = None       # 「我的一週」排的卡（owner 2026-09-08）：沒時數也是 plan 不是 pending；更新時不帶＝沿用列上的狀態


class TimesheetManualRequest(BaseModel):
    staff_id: str              # 管理端代填指定人員
    rows: List[TimesheetManualRow]


class MeTimesheetBatch(BaseModel):
    """員工一次填多列（/my.html「我的工時」新增 grid）。"""
    rows: List[TimesheetManualRow]


class MeTimesheetUpdate(TimesheetManualRow):
    """員工改自己的一列（本人＋手填＋未鎖，規則在 core.hr_logic.can_edit_timesheet；不審核）。"""


class TimesheetMergeRequest(BaseModel):
    """合併同案（owner 2026-09-07）：date＝哪一天（空＝今天）；dry_run＝只回預覽不動資料。"""
    date: str = ""
    dry_run: bool = False


class TimesheetMergeUndo(BaseModel):
    log_id: str


class MilestoneItem(BaseModel):
    """彈窗裡的一列（POST /milestones/week/save 的 items[]）：有 id＝改既有的，沒 id＝新增；delete＝刪。"""
    id: Optional[str] = None
    project_id: str = ""
    title: str = ""
    due_date: Optional[str] = None
    assignee_staff_id: Optional[str] = None
    assignee_name: Optional[str] = None
    note: Optional[str] = None
    done: Optional[bool] = None
    delete: bool = False


class MilestoneSave(BaseModel):
    week_start: str
    items: List[MilestoneItem] = []


class MilestoneDone(BaseModel):
    done: bool = True


class MilestoneDefer(BaseModel):
    week_start: str = ""


class TimesheetRowAdminUpdate(TimesheetManualRow):
    """管理員在總表改任一列（含 Sheet 列）；多一個管理員備註。"""
    note: Optional[str] = None


class TimesheetRowsBatch(BaseModel):
    """總表批次調整：勾選的列一次改專案／分類／備註／管理員備註（只改有給的欄）。"""
    ids: List[str]
    project_name: Optional[str] = None
    project_id: Optional[str] = None
    work_type: Optional[str] = None
    remark: Optional[str] = None
    note: Optional[str] = None


class TimesheetConflictResolve(BaseModel):
    """總表決定衝突：keep_mine 用總表的／use_sheet 用 Sheet 的／keep_both 兩列都留。"""
    choice: str


class BulletinAsk(BaseModel):
    message: str


class DownloadModelRequest(BaseModel):
    model_size: str

class ListDirRequest(BaseModel):
    path: str
    # 預設值 = core.media_exts 那份正本（前端不送 exts 就吃這個）
    exts: List[str] = Field(default_factory=sorted_video_exts)

class MergeOutputRequest(BaseModel):
    proxy_root: str
    project_name: str

class MergeHostOutputsRequest(BaseModel):
    proxy_root: str
    project_name: str

class VerifyProxiesRequest(BaseModel):
    proxy_root: str
    project_name: str
    expected_files: dict

class VerifyStandaloneProxiesRequest(BaseModel):
    sources: List[str]
    dest_dir: str

class CompareSourceRequest(BaseModel):
    source_dir: str
    output_dir: str
    video_exts: List[str] = Field(default_factory=sorted_video_exts)
    proxy_exts: List[str] = [".mov", ".mp4"]
    flat_proxy: bool = False

class OpenFileRequest(BaseModel):
    path: str

class ValidatePathsRequest(BaseModel):
    paths: List[str]

class ReorderRequest(BaseModel):
    ordered_job_ids: List[str]


class ScheduleCreateRequest(BaseModel):
    name: str
    cron: Optional[str] = None                   # "0 2 * * *" (重複排程用)
    run_at: Optional[str] = None                 # ISO datetime (單次排程用)
    task_type: str = "backup"                    # backup/transcode/concat/verify/transcribe/tts/clone
    request: dict                                # 對應任務類型的完整設定 dict
    enabled: bool = True


class ScheduleUpdateRequest(BaseModel):
    name: Optional[str] = None
    cron: Optional[str] = None
    run_at: Optional[str] = None
    task_type: Optional[str] = None
    enabled: Optional[bool] = None
    request: Optional[dict] = None


