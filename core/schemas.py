from pydantic import BaseModel, Field, field_validator  # type: ignore
from typing import List, Optional, Tuple

from core.crm_logic import normalize_tax_id


class BackupRequest(BaseModel):
    task_type: str = "backup"
    job_id: str = ""
    project_name: str
    local_root: str
    nas_root: str
    proxy_root: str
    cards: List[Tuple[str, str]]
    do_hash: bool = True
    do_transcode: bool = True
    do_concat: bool = True
    do_report: bool = False
    # Concat settings
    concat_resolution: str = "720P"
    concat_codec: str = "H.264 (NVENC)"
    concat_burn_tc: bool = True
    concat_burn_fn: bool = False
    # Report settings
    report_name: str = ""
    report_output: str = ""
    report_filmstrip: bool = True
    report_techspec: bool = True
    report_hash: bool = False
    compute_hosts: list = []

class TranscodeRequest(BaseModel):
    task_type: str = "transcode"
    job_id: str = ""
    project_name: str = ""
    sources: List[str]
    dest_dir: str
    compute_hosts: list = []

class ClipSpec(BaseModel):
    """Per-clip advanced edit spec for concat (order, trim, color grading)."""
    path: str
    trim_in: float = 0.0
    trim_out: float = -1.0  # -1 = use full duration
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    gamma: float = 1.0
    color_temp: float = 0.0
    tint: float = 0.0
    shadows: float = 0.0
    midtones: float = 0.0
    highlights: float = 0.0
    curve_points: Optional[List[Tuple[float, float]]] = None


class ConcatRequest(BaseModel):
    task_type: str = "concat"
    job_id: str = ""
    project_name: str = ""
    sources: List[str]
    dest_dir: str
    custom_name: str = ""
    resolution: str = "1080P"
    codec: str = "ProRes"
    burn_timecode: bool = True
    burn_filename: bool = False
    compute_hosts: list = []
    # Advanced edit: if provided, each clip is trimmed + color-graded individually
    # before concatenation. Overrides 'sources' ordering (uses clip order).
    advanced_clips: Optional[List[ClipSpec]] = None
    # Crossfade transition between adjacent clips (only applies when
    # advanced_clips has >= 2 entries; per-transition auto-skip when any
    # side's effective length < duration).
    xfade_enabled: bool = False
    xfade_type: str = "fade"
    xfade_duration: float = 1.0

class VerifyRequest(BaseModel):
    task_type: str = "verify"
    job_id: str = ""
    project_name: str = ""
    pairs: List[Tuple[str, str]]
    mode: str = "quick"
    compute_hosts: list = []

class ReportJobRequest(BaseModel):
    task_type: str = "report"
    job_id: str = ""
    source_dir: str
    output_dir: str
    nas_root: str = ""
    report_name: str = ""
    do_filmstrip: bool = True
    do_techspec: bool = True
    do_hash: bool = False
    do_gdrive: bool = False
    do_gchat: bool = False
    do_line: bool = False
    exclude_dirs: list = []
    client_sid: str = ""       # Socket.IO client sid（報表完成時只通知該客戶端）

class TranscribeRequest(BaseModel):
    task_type: str = "transcribe"
    job_id: str = ""
    project_name: str = ""
    sources: List[str]
    dest_dir: str
    model_size: str = "turbo"
    output_srt: bool = True
    output_txt: bool = True
    output_wav: bool = False
    generate_proxy: bool = False
    individual_mode: bool = False
    compute_hosts: list = []


class AlignTask(BaseModel):
    """One video ⇄ one transcript pair for forced alignment."""
    source: str                          # Video file path
    transcript: str                      # Raw transcript content (txt or srt body)
    transcript_format: str = "auto"      # "auto" | "txt" | "srt"


class AlignRequest(BaseModel):
    """Force-align known transcripts to video audio, output SRT.

    Per CLAUDE.md規劃 v4：使用者文字段落保留為字幕邊界，不自動分行；
    錨點 + 線性內插保證頭尾對齊；字幕時間後處理（frame snap / min duration / gap）。
    """
    task_type: str = "align"
    job_id: str = ""
    project_name: str = ""
    tasks: List[AlignTask]               # 1-to-1 video⇄transcript pairs
    dest_dir: str
    model_size: str = "turbo"
    language: str = "zh"
    anchor_threshold: float = 0.4
    # Subtitle timing polish (專業字幕後處理)
    subtitle_polish: bool = True
    fps_override: Optional[float] = None  # None = auto-detect via ffprobe
    min_duration: float = 1.0
    max_duration: float = 7.0
    min_gap_frames: int = 2
    hold_until_next: bool = True          # Each cue.end = next.start (Netflix-style continuous)
    encoding_bom: bool = True             # UTF-8 BOM for Premiere compatibility
    compute_hosts: list = []

class TtsRequest(BaseModel):
    task_type: str = "tts"
    job_id: str = ""
    project_name: str = ""
    text: str
    voice: str = "zh-TW-HsiaoChenNeural"
    rate: int = 0
    pitch: int = 0
    output_dir: str
    output_name: str = "tts_output"
    use_taiwan: bool = True
    compute_hosts: list = []


class TtsCloneRequest(BaseModel):
    task_type: str = "tts"
    job_id: str = ""
    project_name: str = ""
    text: str
    reference_audio: str
    output_dir: str
    output_name: str = "clone_output"
    speed: float = 1.0
    pitch: int = 0
    ref_text: Optional[str] = None
    use_taiwan: bool = True
    mode: str = "clone"  # "clone" to distinguish from standard tts
    compute_hosts: list = []


class TimelineClip(BaseModel):
    path: str
    trim_in: float = 0.0
    trim_out: float = -1.0  # -1 = full duration
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    color_temp: float = 0.0


class TimelineExportRequest(BaseModel):
    task_type: str = "timeline_export"
    job_id: str = ""
    clips: List[TimelineClip]
    output_dir: str
    output_name: str = "output.MOV"
    resolution: str = "1080P"
    codec: str = "H.264 (NVENC)"


class DroneMetaScanRequest(BaseModel):
    paths: List[str]


class DroneMetaFileSetting(BaseModel):
    path: str
    trim_in: float = 0.0        # 秒，0 = 從頭
    trim_out: float = -1.0      # 秒，-1 = 到尾
    date_time_override: str = ""  # Per-file datetime override (ISO format), empty = use global
    # 色彩調整
    brightness: float = 0.0     # -1.0 ~ 1.0 (additive)
    contrast: float = 1.0       # 0.0 ~ 2.0
    saturation: float = 1.0     # 0.0 ~ 3.0
    gamma: float = 1.0          # 0.1 ~ 3.0
    color_temp: float = 0.0     # -1.0 ~ 1.0（藍↔黃）
    tint: float = 0.0           # -1.0 ~ 1.0（洋紅↔青綠）
    shadows: float = 0.0        # -1.0 ~ 1.0
    midtones: float = 0.0
    highlights: float = 0.0
    curve_points: Optional[List[Tuple[float, float]]] = None


class DroneMetaRequest(BaseModel):
    task_type: str = "drone_meta"
    job_id: str = ""
    project_name: str = ""
    file_index: int = 1
    files: List[DroneMetaFileSetting]
    output_dir: str = ""
    date_time: str = ""         # ISO format (fallback, per-file override preferred)
    drone_make: str = "Autel Robotics"
    drone_model: str = "EVO Lite+"
    lens_make: str = "Autel Robotics"
    lens_model: str = "EVO Lite+ Camera"
    # 串帶
    do_concat: bool = False
    concat_dest_dir: str = ""
    concat_custom_name: str = ""
    concat_resolution: str = "1080P"
    concat_codec: str = "H.264 (NVENC)"
    concat_burn_timecode: bool = True
    concat_burn_filename: bool = False
    concat_xfade_enabled: bool = False
    concat_xfade_type: str = "fade"
    concat_xfade_duration: float = 1.0


class DroneWatcherSnapshot(BaseModel):
    """快照主面板當前設定 — 排程執行時完全照此設定重現。"""
    drone_model_key: str = "autel_evo_lite_plus"
    custom_make: str = ""
    custom_model: str = ""
    custom_lens_make: str = ""
    custom_lens_model: str = ""
    file_index: int = 1
    do_concat: bool = True
    concat_custom_name: str = ""
    concat_resolution: str = "1080P"
    concat_codec: str = "H.264 (NVENC)"
    concat_burn_timecode: bool = True
    concat_burn_filename: bool = False
    concat_xfade_enabled: bool = False
    concat_xfade_type: str = "fade"
    concat_xfade_duration: float = 1.0


class DroneWatcherConfig(BaseModel):
    enabled: bool = False
    run_time: str = "02:00"           # 每日執行時間 HH:MM
    source_root: str = ""              # 來源根目錄（內含多個子卡資料夾）
    dest_root: str = ""                # MAX_* 輸出的根目錄
    concat_dest_root: str = ""         # 串帶輸出根目錄（空則與 dest_root 同）
    snapshot: DroneWatcherSnapshot = DroneWatcherSnapshot()


class BulletinCreate(BaseModel):
    title: str
    note: Optional[str] = None
    status: str = "todo"        # todo / doing / done
    priority: str = "med"       # high / med / low
    category: Optional[str] = None
    pinned: bool = False
    assignee: str = "me"        # me / claude（交辦收件匣）
    assignee_username: Optional[str] = None  # N0: 指派到個人（users.username）


class BulletinUpdate(BaseModel):
    title: Optional[str] = None
    note: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    category: Optional[str] = None
    pinned: Optional[bool] = None
    assignee: Optional[str] = None
    assignee_username: Optional[str] = None  # N0: "" = 取消指派


class BulletinReorder(BaseModel):
    ordered_ids: List[str]


class MeProfileUpdate(BaseModel):
    """個人工作台 — 本人可編輯的 crm_staff 白名單欄位（N0）。

    嚴格白名單：費率/狀態/僱用型態/身分證/銀行/website_* 覆寫欄位一律不開放
    （由管理員在人力資源管；website_* 動了會觸發官網 rebuild）。
    """
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    emergency_contact: Optional[str] = None
    portfolio_url: Optional[str] = None
    photo_url: Optional[str] = None
    bio: Optional[str] = None
    skills: Optional[list] = None
    education: Optional[list] = None
    experience: Optional[list] = None
    awards: Optional[list] = None


class MeTodoUpdate(BaseModel):
    """個人工作台 — 本人待辦僅可改狀態。"""
    status: str  # todo / doing / done


class SeriesQuickAddPayload(BaseModel):
    """showcase-edit 系列下拉「找不到 → 新增」快速建立（只收名稱，slug 自動生成）。"""
    title_zh: str


# ── N-hr H2 出缺勤（請假）──

class LeaveCreate(BaseModel):
    """管理端建立/代登請假單。日期格式 YYYY-MM-DD。"""
    staff_id: str
    leave_type: str            # 特休/病假/事假/公假/婚假/喪假/其他
    start_date: str
    end_date: str
    days: float = 1.0          # 0.5 步進
    reason: Optional[str] = None


class LeaveUpdate(BaseModel):
    status: Optional[str] = None       # 待審/已核准/已退回（核准寫核可人+時間戳）
    leave_type: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    days: Optional[float] = None
    reason: Optional[str] = None


class MeLeaveCreate(BaseModel):
    """員工自助送單（staff_id 由 token 解析，不收）。"""
    leave_type: str
    start_date: str
    end_date: str
    days: float = 1.0
    reason: Optional[str] = None


class AnnualLeaveSet(BaseModel):
    annual_leave_days: Optional[int] = None   # None = 清除額度設定


# ── 工時手填（與 Sheet 同步共存；同人+日+專案 手填優先）──

class TimesheetManualRow(BaseModel):
    work_date: str             # YYYY-MM-DD
    project_id: Optional[str] = None
    project_name: str = ""     # 無 id 時以名稱對映（同 ingest 邏輯）
    task_note: Optional[str] = None
    hours: float


class TimesheetManualRequest(BaseModel):
    staff_id: str              # 管理端代填指定人員
    rows: List[TimesheetManualRow]


class MeTimesheetCreate(TimesheetManualRow):
    """員工自助補登一筆工時（staff 由 token 解析）— 欄位同 TimesheetManualRow。"""


class BulletinAsk(BaseModel):
    message: str


class DownloadModelRequest(BaseModel):
    model_size: str

class ListDirRequest(BaseModel):
    path: str
    exts: List[str] = [".mov", ".mp4", ".mkv", ".mxf", ".avi", ".mts", ".m2ts", ".r3d", ".braw"]

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
    video_exts: List[str] = [".mov", ".mp4", ".mkv", ".mxf", ".avi", ".mts", ".m2ts", ".r3d", ".braw"]
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


# ── CRM Schemas ──

class ClientPayload(BaseModel):
    short_name: str
    full_name: str = ""
    tax_id: str = ""
    am_username: Optional[str] = None

    # 🔴 統編補回前導 0：Excel/Sheets 把 00973926 存成數字 973926，匯出 CSV 就少了
    # 兩個 0。規則正本 core.crm_logic.normalize_tax_id（有單元測試）。
    _norm_tax_id = field_validator("tax_id")(lambda v: normalize_tax_id(v))
    source_channel: str = ""
    contact_person: str = ""
    contact_method: str = ""
    status: str = "潛在客戶"
    cooperation_note: str = ""
    payment_info: str = ""
    payment_note: str = ""
    notes: str = ""
    # 兩本帳：None＝建立落 'parent'；更新一律不換帳本（endpoint exclude 掉）
    entity: Optional[str] = None


class CrmProjectPayload(BaseModel):
    name: str
    client_id: str
    status: str = "洽詢"
    am_username: Optional[str] = None
    pm_usernames: List[str] = []
    shoot_date: Optional[str] = None
    start_date: Optional[str] = None
    completion_date: Optional[str] = None
    project_type: str = ""
    folder_path: str = ""
    description: str = ""
    notes: str = ""
    # 財務
    contract_amount: Optional[int] = None
    tax_rate: int = 5
    profit_target_pct: int = 20
    misc_budget_pct: int = 5
    # 帳務
    payment_status: str = "未到帳"
    amount_receivable: Optional[int] = None
    amount_received: Optional[int] = None
    transfer_fee: Optional[int] = None


class CrmProjectPatchPayload(CrmProjectPayload):
    """Partial-update body for PUT /projects/{id}. Inherits CrmProjectPayload
    and overrides the required fields (name, client_id) to Optional so the
    cell-by-cell auto-save can send just the dirty field. Endpoint reads via
    `model_dump(exclude_unset=True)` so non-sent fields are skipped, not
    overwritten with their defaults.
    """
    name: Optional[str] = None
    client_id: Optional[str] = None
    # 非專案欄位：狀態轉「未成案/成案」時給提案衛星列的原因（組織學習欄），
    # update_project 會 pop 掉不 setattr（提案=專案合體，2026-08-06）
    outcome_reason: Optional[str] = None


class ProjectExpensePayload(BaseModel):
    category: str
    estimated: int = 0
    actual: int = 0
    sub_item: str = ""
    payee: str = ""
    advance_id: str = ""
    notes: str = ""
    cost_group_id: Optional[str] = None
    # 消費日（YYYY-MM-DD，空＝不填）。專案成本認列用它，不用登記日。
    expense_date: str = ""


class PettyExpensePayload(BaseModel):
    """零用金：本人登記一筆墊付（docs/PETTY_CASH_PLAN.md）。

    ⚠️ 沒有 `staff_id` 欄位 —— 請款人一律從 token 解（own-scope 的定義）。
    `project_id` 可留白＝公司層級支出（行政/業務推廣），實測歷史 289/443 如此。
    """
    expense_date: str = ""          # YYYY-MM-DD，空 → 今天
    actual: int = 0                 # 實付金額（可為負＝退款/沖回）
    summary: str = ""               # 摘要（存 sub_item）
    item: str = ""                  # 會計項目 → finance_category_map
    category: str = "其他"           # 手機頁粗分類（交通/住宿/飲食…），與 item 不同軸
    project_id: Optional[str] = None
    invoice_no: str = ""
    notes: str = ""


class PettySubmitPayload(BaseModel):
    notes: str = ""


class PettyFromCashPayload(BaseModel):
    """收支明細 → 零用金單據（收支表上的「推送零用金請款」）。

    `preview=True` 只試算不寫 —— 前端先把「會建成什麼樣」給人看過再送出，
    因為推完之後那筆錢就進了公司的請款流程，退回要動到三張表。

    ⚠️ 沒有 `staff_id` —— 請款人是身分不是欄位（同 PettyExpensePayload）。
    代為推送走路徑參數 `/petty/staff/{staff_id}/from-cash`。
    """
    entry_id: str = ""              # 一次一列（owner 拍板「不是一次一批」）
    item: str = ""                  # 空 → 由類別對映推（對不出來後端會擋）
    project_id: Optional[str] = None
    notes: str = ""
    preview: bool = False


class ExpenseLinkPayload(BaseModel):
    """發一條雜支登記的分享連結（token）。

    `kind`：`project`（整個專案）或 `group`（單一拍攝日子表）。
    `rotate=True` ＝「重置連結」：舊連結當場失效。
    """
    kind: str
    target_id: str
    rotate: bool = False


class ProjectExpensePatchPayload(BaseModel):
    """供 inline edit 用的部分更新 payload — 所有欄位 optional。"""
    category: Optional[str] = None
    estimated: Optional[int] = None
    actual: Optional[int] = None
    sub_item: Optional[str] = None
    payee: Optional[str] = None
    advance_id: Optional[str] = None
    notes: Optional[str] = None
    cost_group_id: Optional[str] = None
    expense_date: Optional[str] = None   # YYYY-MM-DD；空字串＝清掉消費日


class QuotationItemPayload(BaseModel):
    group_name: str = ""
    description: str
    unit: str = "式"
    quantity: int = 1
    unit_price: int = 0
    internal_cost: int = 0
    note: str = ""


class QuotationPayload(BaseModel):
    status: str = "草稿"
    quote_date: Optional[str] = None
    valid_until: Optional[str] = None
    discount: int = 0
    tax_rate: int = 5
    final_price: Optional[int] = None
    payment_stages: List[dict] = []
    terms: str = ""
    items: List[QuotationItemPayload] = []


class QuotationTemplatePayload(BaseModel):
    name: str
    description: str = ""
    tax_rate: int = 5
    terms: str = ""
    payment_stages: List[dict] = []
    items: List[QuotationItemPayload] = []


class StaffPayload(BaseModel):
    # name 設為 Optional 以支援「部分更新」(PUT 只送變動欄位，如官網呈現 section 只送
    # show_on_website + website_*)。create_staff 端會明確檢查 name 必填，不靠 schema 強制。
    name: Optional[str] = None
    role: str = ""
    daily_rate: int = 0
    hourly_rate: int = 0
    phone: str = ""
    email: str = ""
    id_number: str = ""
    address: str = ""
    bank_name: str = ""
    bank_account: str = ""
    portfolio_url: str = ""
    status: str = "在職"
    notes: str = ""
    # H1 員工檔案完整化 — 全 Optional 配合 exclude_unset 部分更新；
    # 日期收 'YYYY-MM-DD' 字串，端點層轉 datetime（staff.py _STAFF_DATE_FIELDS）。
    employment_type: Optional[str] = None     # 正職/兼職/約聘/freelance
    hire_date: Optional[str] = None
    leave_date: Optional[str] = None
    emergency_contact: Optional[str] = None
    # 官網呈現覆寫（與「官網管理 › 關於我們」團隊卡同步寫同一批 crm_staff 欄位）。
    # 全為 Optional/None → 配合 update_staff 的 model_dump(exclude_unset=True)，
    # 未送的欄位不會被寫入，避免任一側部分更新覆蓋另一側的覆寫值。
    show_on_website: Optional[bool] = None
    website_title: Optional[str] = None
    website_photo_url: Optional[str] = None
    website_bio: Optional[str] = None
    website_sort_order: Optional[int] = None


class StaffQuickAddPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    role: Optional[str] = ""


class ResumePayload(BaseModel):
    bio: str = ""
    skills: list = []
    education: list = []
    experience: list = []
    awards: list = []
    resume_visible: bool = False
    resume_editable: bool = True


class PortfolioPayload(BaseModel):
    title: str
    url: str
    role_desc: str = ""
    sort_order: int = 0


class ProjectStaffPayload(BaseModel):
    staff_id: str
    role_in_project: str = ""
    phase: str = ""
    days: int = 1
    rate_override: Optional[int] = None
    actual_days: Optional[int] = None
    actual_cost: Optional[int] = None
    payment_status: Optional[str] = None
    payment_date: Optional[str] = None
    notes: str = ""


class PaymentRequestPayload(BaseModel):
    request_date: Optional[str] = None
    amount: int = 0
    summary: str
    category: str = "專案外包"
    payee_name: str = ""
    payee_id: str = ""
    payee_type: str = ""
    needs_invoice: int = 0
    invoice_number: str = ""
    invoice_amount: Optional[int] = None
    # 代開請款單指向的那張發票。號碼可能是空的（代開還沒拿到號），id 才是可靠的鍵
    # —— 付款時要靠它把發票收尾到「已撥款」。
    source_invoice_id: Optional[str] = None
    # 從「委外人員名單」一鍵請款時帶的成本行 id（防重複請款）
    cost_line_id: Optional[str] = None
    expense_id: Optional[str] = None      # 行政雜支那一行（crm_project_expenses.id）
    project_id: Optional[str] = None
    project_label: str = ""
    payment_date: Optional[str] = None
    payment_status: str = "應付款"
    # 🔴 空值一定要收得下 None。前端的共用收值（crm-utils.enableInlineEdit）對
    # date/month 型別一律 `val = val || null` —— 那對 request_date/payment_date
    # 是對的（Optional），但「預計付款月」留空時送來的也是 null，而這欄宣告 str
    # → 整張單存不了，錯誤訊息只有一句 "Input should be a valid string"，
    # 完全看不出是哪一欄（owner 2026-08-24 就這樣被擋住，改不了記帳錯誤）。
    # 存的形狀維持空字串（全 repo 都用 `planned_month or ""` 比對），只在入口收斂。
    planned_month: str = ""
    _norm_planned_month = field_validator("planned_month", mode="before")(
        lambda v: "" if v is None else v)
    advance_by: str = ""
    is_advance: int = 0
    advance_returned: int = 0
    notes: str = ""
    # 兩本帳：None＝建立時落 'parent'、更新時維持既有值；🔴 不可給 "parent" 當預設——整包 model_dump 寫回會把我的帳列洗回母公司
    entity: Optional[str] = None


class ProjectLedgerMovePayload(BaseModel):
    """把專案搬到另一本帳（`entity`：'mine' 或 'parent'）。

    🔴 這是全 repo「更新一律不得換帳本」的**唯一例外**，所以走專屬端點而不是
    塞進 PUT /projects/{id} —— 換帳本要有自己的守衛（只有私帳 full scope 能按、
    身上掛了錢就擋）與自己的痕跡，混進一般更新裡遲早被當成普通欄位寫過去。
    """
    entity: str = ""


class ProjectMirrorPayload(BaseModel):
    """連結私帳：母公司專案 → 私帳的收入分身。

    `target_id` 空＝建新的私帳案；有值＝連結到既有那一案（他手動建過 109 案，
    名字跟母公司的不一定一樣，所以要留這條路）。

    `mode`＝要連結的那一案**已經填過工項**時怎麼辦（owner 2026-08-30
    「跳出幾個選擇讓我決定要怎麼做」）：
      overwrite  私帳的工項換成母公司成本行算出來的（建新案一律走這條）
      keep       只建立連結，私帳的金額一毛不動
      import     反過來：把私帳的工項寫成母公司的 CRM 成本行（掛給我），
                 私帳不動。匯入後那些成本行在 CRM 照常可編。
    """
    target_id: str = ""
    mode: str = "overwrite"


class CashTaxonomyNodePayload(BaseModel):
    """在收支分類樹上長一個節點（收支明細的「＋自訂…」）。

    `parent_id` 空＝第一層。**不收 depth**：深度是位置的性質不是欄位，由後端從
    父節點推 —— 讓前端送，遲早會送出一個 depth 跟 parent 對不上的節點。
    """
    parent_id: str = ""
    name: str = ""
    entity: Optional[str] = None


class CashTaxonomyNodeUpdate(BaseModel):
    """改分類樹的節點（後台編輯器）。**部分更新**：只送要改的欄。

    name / parent_id 是**資料遷移**不是改標籤 —— 第一、二層的名字就是 category
    複合鍵，改了要連 finance_category_map 的對映一起改，否則那批帳從三表消失。
    後端 update_cash_taxonomy_node 一手包辦，呼叫端不要自己補。
    """
    name: Optional[str] = None
    parent_id: Optional[str] = None
    active: Optional[int] = None
    entity: Optional[str] = None


class CashEntryPayload(BaseModel):
    """收支明細。**PUT 走 exclude_unset 部分更新** —— 所以每個欄位都可以不送，
    包括 summary（建立時由 create_cash_entry 明確驗必填）。整包寫回會把前端沒送的
    欄位洗成這裡的預設值，收支明細有 20 幾欄而編輯視窗只送 10 個。"""
    entry_date: Optional[str] = None
    expense: Optional[int] = None
    claim: Optional[int] = None
    deposit: Optional[int] = None
    summary: Optional[str] = None
    note: str = ""
    bank_memo: str = ""       # 銀行/卡單原始資訊（note＝使用者手寫附註）
    category: str = ""
    item: str = ""
    sub_item: str = ""
    payee: str = ""
    status: str = ""
    has_invoice: int = 0
    invoice_number: str = ""
    project_label: str = ""
    project_id: Optional[str] = None
    payment_date: Optional[str] = None
    payment_status: str = ""
    invoice_id: Optional[str] = None
    bank_fee: Optional[int] = None
    advance_payment_id: Optional[str] = None
    bank_account_id: Optional[str] = None       # 掛哪個銀行帳戶（財務階段二）
    payment_request_id: Optional[str] = None    # AP 硬連結 → crm_payment_requests
    # 分類樹的葉節點（cash_taxonomy_nodes.id）。送了就是**它說了算** ——
    # category/item/sub_item 由後端 _sync_taxonomy 從路徑推導，前端送的不算數。
    # 送空字串＝清掉分類（連三欄一起清）。沒送＝這次沒動分類。
    taxonomy_node_id: Optional[str] = None
    # 兩本帳：None＝建立時落 'parent'、更新時維持既有值；🔴 不可給 "parent" 當預設——整包 model_dump 寫回會把我的帳列洗回母公司
    entity: Optional[str] = None


class CostLinePayload(BaseModel):
    phase: str
    item_name: str
    sort_order: int = 0
    cost_group_id: Optional[str] = None
    estimated_unit_price: Optional[int] = None
    estimated_quantity: Optional[int] = None
    estimated_unit_type: Optional[str] = None
    estimated_amount: Optional[int] = None
    estimated_staff_id: Optional[str] = None
    estimated_notes: str = ""
    actual_unit_price: Optional[int] = None
    actual_quantity: Optional[int] = None
    actual_unit_type: Optional[str] = None
    actual_amount: Optional[int] = None
    actual_staff_id: Optional[str] = None
    actual_notes: str = ""


class CostGroupCreate(BaseModel):
    name: str
    shoot_date: Optional[str] = None        # "YYYY-MM-DD"
    notes: Optional[str] = None
    sort_order: int = 0
    budget_amount: Optional[int] = None
    misc_budget_amount: Optional[int] = None
    profit_target_pct: Optional[int] = None
    receipt_path: Optional[str] = None


class CostGroupUpdate(BaseModel):
    name: Optional[str] = None
    shoot_date: Optional[str] = None
    notes: Optional[str] = None
    sort_order: Optional[int] = None
    budget_amount: Optional[int] = None
    misc_budget_amount: Optional[int] = None
    profit_target_pct: Optional[int] = None
    # 空字串視為清空（搭配 model_dump(exclude_none=True) 讓未送的欄位不動）
    receipt_path: Optional[str] = None


class CostGroupDuplicate(BaseModel):
    name: str
    shoot_date: Optional[str] = None


class CostLineUpdatePayload(BaseModel):
    item_name: Optional[str] = None
    sort_order: Optional[int] = None
    estimated_unit_price: Optional[int] = None
    estimated_quantity: Optional[int] = None
    estimated_unit_type: Optional[str] = None
    estimated_amount: Optional[int] = None
    estimated_staff_id: Optional[str] = None
    estimated_notes: Optional[str] = None
    actual_unit_price: Optional[int] = None
    actual_quantity: Optional[int] = None
    actual_unit_type: Optional[str] = None
    actual_amount: Optional[int] = None
    actual_staff_id: Optional[str] = None
    actual_notes: Optional[str] = None


class ShowcasePayload(BaseModel):
    description: str = ""
    video_url: str = ""
    credits: list = []
    process_mode: str = "gallery"
    slug: str = ""


class InvoicePayload(BaseModel):
    payment_type: str = "收款"
    payment_status: str = "未收款"
    issue_status: str = "已開立"
    invoice_number: str = ""
    invoice_date: Optional[str] = None
    title: str
    applicant: str = ""
    category: str = "專案"
    invoice_kind: str = "電子發票"
    amount_ex_tax: Optional[int] = None
    amount_total: Optional[int] = None
    tax_amount: Optional[int] = None
    commission: Optional[int] = None
    company_name: str = ""
    tax_id: str = ""
    item_type: str = ""

    # 🔴 統編補回前導 0：Excel/Sheets 把 00973926 存成數字 973926，匯出 CSV 就少了
    # 兩個 0。規則正本 core.crm_logic.normalize_tax_id（有單元測試）。
    _norm_tax_id = field_validator("tax_id")(lambda v: normalize_tax_id(v))
    project_id: Optional[str] = None
    recipient: str = ""
    recipient_phone: str = ""
    recipient_address: str = ""
    notes: str = ""
    # 兩本帳：None＝建立時落 'parent'、更新時維持既有值；🔴 不可給 "parent" 當預設——整包 model_dump 寫回會把我的帳列洗回母公司
    entity: Optional[str] = None


class TimesheetRow(BaseModel):
    """工時 Sheet 單列（Apps Script 上行；欄位對齊 Sheet：日期/員工/專案/內容/時數）。"""
    date: str = ""            # "2026/6/30" 等，後端容錯解析
    staff: str = ""
    project: str = ""
    task: str = ""
    hours: float = 0.0
    budget: Optional[float] = None   # Sheet 的專案預算時數欄（有帶就鏡射 crm_projects.budget_hours）


class TimesheetIngestRequest(BaseModel):
    rows: List[TimesheetRow]


class MilestonePayload(BaseModel):
    """付款節點（B3）新增/更新。"""
    project_id: Optional[str] = None      # create 必填；update 不動
    label: str = ""
    amount: Optional[int] = None
    due_date: Optional[str] = None        # 'YYYY-MM-DD'
    status: Optional[str] = None          # 未到期/待請款/已請款/已收款
    invoice_id: Optional[str] = None
    sort_order: Optional[int] = None
    note: Optional[str] = None


class MonthClosePayload(BaseModel):
    """月結（F1）：鎖定/重開指定月份。"""
    month: str                            # 'YYYY-MM'


class LocationPayload(BaseModel):
    """場景庫（P-a）新增/更新 — 全欄 Optional 配合部分更新（create 時 name 由端點檢查必填）。"""
    name: Optional[str] = None
    category: Optional[str] = None        # 咖啡廳/工廠/辦公室/戶外/官署…
    region: Optional[str] = None          # 縣市
    address: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    permit_required: Optional[int] = None  # 0/1 需申請拍攝許可
    permit_note: Optional[str] = None
    fee_note: Optional[str] = None
    attributes: Optional[dict] = None     # 電源/收音/自然光/停車/廁所… 自由 dict
    tags: Optional[list] = None
    note: Optional[str] = None
    status: Optional[str] = None          # 可用/黑名單/已消失
    cover_url: Optional[str] = None


class LocationUsagePayload(BaseModel):
    """場景使用履歷（哪個專案用過＋評分＋踩雷心得）。"""
    project_id: Optional[str] = None
    used_date: Optional[str] = None       # 'YYYY-MM-DD'
    rating: Optional[int] = None          # 1-5
    lesson: Optional[str] = None


class ProposalPayload(BaseModel):
    """提案庫（P-b）新增/更新 — 全欄 Optional 配合部分更新（create 時 title 由端點檢查必填）。"""
    title: Optional[str] = None
    client_id: Optional[str] = None       # soft FK → clients.id
    project_id: Optional[str] = None      # soft FK → crm_projects.id（成案後回填）
    quotation_id: Optional[str] = None    # soft FK → crm_quotations.id
    ptype: Optional[str] = None           # 形象/廣告/紀錄片/政府標案/社群/其他
    status: Optional[str] = None          # 草稿/已提案/入圍/成案/未成案/擱置
    pitch_date: Optional[str] = None      # 'YYYY-MM-DD'
    budget_range: Optional[str] = None
    deck_url: Optional[str] = None
    outcome_reason: Optional[str] = None  # 轉成案/未成案時必填（端點檢查）
    tags: Optional[list] = None
    notes: Optional[str] = None           # 基本資料備註（自由文字）


class ReferencePayload(BaseModel):
    """參考片庫（跨提案共用）新增/更新（create 時 url 由端點檢查必填）。"""
    url: Optional[str] = None
    title: Optional[str] = None
    note: Optional[str] = None
    tags: Optional[list] = None
    thumb_url: Optional[str] = None


class ProposalPlanPayload(BaseModel):
    """企劃矩陣整份寫入（docs/PROPOSAL_PLANNER.md §6.3）— 僅用於
    「開始企劃 / 載入範例 / 清空」；日常輸入走逐格 PATCH。"""
    clear: bool = False                   # true = 清空整份 plan（其餘欄位忽略）
    template_id: Optional[str] = None
    template_version: Optional[str] = None
    theme: Optional[str] = ""
    cells: Optional[dict] = None          # {lens: {how: {answer, ...}}}
    directions: Optional[dict] = None     # {how: {answer, ...}}
    field_values: Optional[dict] = None   # {lens: {field: str}}


class ProposalPlanCellPatch(BaseModel):
    """企劃矩陣逐格寫入 — 共編下的日常輸入單位（樂觀鎖見端點）。"""
    kind: str                             # cell / direction / theme / memo / field
    lens: Optional[str] = None            # kind=cell/field 必填
    how: Optional[str] = None             # kind=cell/direction 必填
    field: Optional[str] = None           # kind=field 必填
    answer: str = ""
    base_updated_at: Optional[str] = None  # 該格載入時的時間戳；伺服器較新→409
    guest_name: Optional[str] = None       # 公開共編（token 路徑）的署名；authed 路徑忽略


class ReferencePatch(BaseModel):
    """參考影片庫 v2 逐欄/逐格寫入（docs/REFERENCE_LIBRARY.md）。
    kind=field → key+value；kind=facet → key(分類族)+value(list 或 studio 字串)；
    kind=research → row_id+col+value（帶 base_updated_at 做樂觀鎖）。"""
    kind: str
    key: Optional[str] = None
    col: Optional[str] = None             # kind=research：purpose/idea/material/moment
    row_id: Optional[str] = None          # kind=research 必填
    value: Optional[object] = None
    base_updated_at: Optional[str] = None
    guest_name: Optional[str] = None      # 公開共編署名；authed 路徑忽略


class ReferenceResearchRow(BaseModel):
    """公開路徑加研究列 —— 只需要署名（列 id 由伺服器產）。"""
    guest_name: Optional[str] = None


class ProposalPublicInfoPatch(BaseModel):
    """公開共編路徑（?t= 連結）改基本資料 — 逐欄寫入，欄名由端點白名單擋。
    刻意設計成「一次一欄」而非整包 payload：免登入路徑不給批次覆寫的能力。"""
    field: str                            # 僅放行 ptype / pitch_date / tags / notes
    value: Optional[object] = None        # str（ptype/notes/pitch_date）或 list[str]（tags）


class ProposalSurveyPatch(BaseModel):
    """現況盤點單格寫入（登入與公開共編同一形狀）。欄目合法性、公開可否編輯、
    長度上限全在 core.proposal_survey —— 這裡只收形狀。"""
    key: str
    field: str                            # content / note
    value: Optional[str] = None


class ProposalSurveyRowPayload(BaseModel):
    """新增自訂盤點欄目（只有登入路徑能加列）。"""
    label: str


class IntelSourcePayload(BaseModel):
    """產業情報來源（P-c）新增/更新 — 全欄 Optional 配合部分更新
    （create 時 url 開頭 http 由端點檢查）。"""
    name: Optional[str] = None
    type: Optional[str] = None            # rss / html（html 第一版先跳過不抓）
    url: Optional[str] = None
    keywords: Optional[list] = None       # list[str] 關鍵字；空 = 全收
    enabled: Optional[bool] = None
    note: Optional[str] = None


class PortalLinkPayload(BaseModel):
    """看片門戶（B1）送審連結建立/更新 — 全欄 Optional 配合部分更新
    （create 時 project_id/video_path 必填 + 檔案存在性由端點檢查）。"""
    project_id: Optional[str] = None
    version_label: Optional[str] = None   # 初剪/一修/定剪…
    video_path: Optional[str] = None      # master 本機影片路徑
    status: Optional[str] = None          # 待審/修改中/已核准
    expires_at: Optional[str] = None      # 'YYYY-MM-DD'；空字串 = 清除到期日


class PortalCommentPayload(BaseModel):
    """客戶端時間軸留言（公開、token 授權）— body 必填由端點檢查（上限 2000 字）。"""
    timecode_sec: float = 0
    body: str = ""
    author_name: Optional[str] = None


class PortalApprovePayload(BaseModel):
    """客戶端一鍵核准（公開、token 授權）— author_name 必填由端點檢查。"""
    author_name: Optional[str] = None


class EquipmentPayload(BaseModel):
    """器材庫（B4）新增/更新 — 全欄 Optional 配合部分更新（create 時 name 由端點檢查必填）。"""
    name: Optional[str] = None
    category: Optional[str] = None        # 機身/鏡頭/燈光/收音/週邊/其他
    serial: Optional[str] = None
    purchase_date: Optional[str] = None   # 'YYYY-MM-DD'；空字串 = 清除
    purchase_cost: Optional[int] = None
    depreciation_months: Optional[int] = None  # 直線攤提月數
    status: Optional[str] = None          # 在庫/出勤/維修/除役
    note: Optional[str] = None
    cover_url: Optional[str] = None
    # 帳本（兩本帳 §8）：建立時可指定（需該帳本的 full 權限），**更新時不可換**
    # —— 換帳本＝把一件器材的折舊整個搬到另一本損益表，那不是編輯是搬遷。
    entity: Optional[str] = None


class EquipmentCheckoutPayload(BaseModel):
    """器材領用 — person 必填由端點檢查；已有未歸還紀錄回 409。"""
    person: Optional[str] = None          # 領用人（自由輸入）
    project_id: Optional[str] = None      # soft FK → crm_projects.id
    due_at: Optional[str] = None          # 應還日 'YYYY-MM-DD'


class EquipmentReturnPayload(BaseModel):
    """器材歸還 — 只補狀況備註；無未歸還紀錄回 404。"""
    condition_note: Optional[str] = None


class EquipmentMaintenancePayload(BaseModel):
    """器材保養紀錄（date 必填由端點檢查）。"""
    date: Optional[str] = None            # 'YYYY-MM-DD'
    cost: Optional[int] = None
    note: Optional[str] = None


class FootageScanRequest(BaseModel):
    """B5 素材庫：掃描資料夾建索引。"""
    root_path: str
    project_id: Optional[str] = None
    project_name: Optional[str] = None


class FootageTagsPayload(BaseModel):
    """B5 素材庫：更新素材 tags。"""
    tags: List[str] = []


# ── 財務管理階段二（routers/api_finance.py）────────────────────

class FinanceCategoryMapItem(BaseModel):
    """category → 科目 對映單列（PUT /finance/category-map 批次 upsert 用）。"""
    source: str                            # cash/payment/invoice
    category_text: str
    account_id: str                        # → finance_accounts.id
    treatment: str                         # direct_expense/direct_income/ap_settlement/...


class FinanceCategoryMapPut(BaseModel):
    items: List[FinanceCategoryMapItem] = []


class BookkeepingFeePut(BaseModel):
    """調整記帳費（換會計、漲價）。使用者只填這三格，歷史由後端自己留。

    `months_per_year` ＝ 一年計幾個月（雙月收一次，超過 12 的部分併在 5 月那期）。
    """
    effective_from: str                    # "YYYY-MM"，從哪一期開始用新價
    monthly: int                           # 月費
    months_per_year: int = 12


class BankAccountPayload(BaseModel):
    """銀行帳戶新增/更新 — create 時 name 必填由端點檢查。"""
    name: Optional[str] = None
    bank_name: Optional[str] = None
    account_no: Optional[str] = None
    acct_kind: Optional[str] = None        # bank / cash / shareholder_*
    staff_id: Optional[str] = None         # 股東往來綁的人員
    # 前端「不綁」送的是空字串；存 "" 會讓 own-scope 查詢配到一堆空字串帳戶
    _blank_staff = field_validator("staff_id")(lambda v: (v or "").strip() or None)
    opening_balance: Optional[int] = None
    opening_date: Optional[str] = None     # 'YYYY-MM-DD'
    is_default: Optional[bool] = None
    active: Optional[bool] = None
    sort_order: Optional[int] = None
    note: Optional[str] = None
    # 兩本帳：None＝建立時落 'parent'、更新時維持既有值；🔴 不可給 "parent" 當預設——整包 model_dump 寫回會把我的帳列洗回母公司
    entity: Optional[str] = None


class ReconciliationPayload(BaseModel):
    """銀行對帳：送對帳單月底餘額，system_balance 由後端算。"""
    bank_account_id: str
    month: str                             # 'YYYY-MM'
    statement_balance: int
    note: Optional[str] = None


class StatementLineIn(BaseModel):
    """對帳單明細單列（匯入/手動 key 共用）。amount 有號：正=存入、負=支出。"""
    line_date: Optional[str] = None        # 'YYYY-MM-DD'
    description: str = ""
    amount: int


class StatementLinesBulkPayload(BaseModel):
    """對帳單明細批次新增。

    🔴 month 是選填的：每一列自己的 line_date 就決定它屬於哪個月，一份跨月的
    對帳單不該逼人切成三次傳（owner 2026-08-20）。只有「該列沒有日期」時才退回
    用 month；連 month 都沒給就擋下來，不猜。

    replace=true 只清**這批真的涵蓋到的月份**，不是日期區間 —— 按區間清的話，
    檔案裡剛好沒有交易的那個月會被連坐清空，那個月已經勾銷好的紀錄就沒了。
    """
    bank_account_id: str
    month: Optional[str] = None            # 'YYYY-MM'，只當沒有 line_date 時的後備
    lines: List[StatementLineIn]
    replace: bool = False


class StatementLineUpdatePayload(BaseModel):
    """對帳單明細列只開放補交易日/註記 — 金額摘要要改就刪列重加。"""
    line_date: Optional[str] = None        # 'YYYY-MM-DD'（無日期匯入列補記前要先補）
    note: Optional[str] = None


class StatementLineMatchPayload(BaseModel):
    entry_id: str


class StatementLineCreateEntryPayload(BaseModel):
    """補記入帳：從對帳單明細建收支明細（category → 科目走既有對映）。"""
    category: str = ""
    summary: Optional[str] = None          # 預設帶對帳單摘要
    payee: str = ""


class StatementAutoMatchPayload(BaseModel):
    bank_account_id: str
    month: str                             # 'YYYY-MM'


class FinanceAdjustmentPayload(BaseModel):
    """財務調整列 — 不得指向銀行類科目（code 11xx），後端驗證擋下。"""
    adj_date: Optional[str] = None         # 'YYYY-MM-DD'（create 必填由端點檢查）
    account_id: Optional[str] = None       # create 必填由端點檢查
    amount: Optional[int] = None           # 有號金額
    adj_type: Optional[str] = None         # opening/correction/owner_in/owner_out/accountant/writeoff/other/vat（vat→應付營業稅，其餘→權益）
    description: Optional[str] = None      # create 必填由端點檢查
    # 兩本帳：None＝建立時落 'parent'、更新時維持既有值；🔴 不可給 "parent" 當預設——整包 model_dump 寫回會把我的帳列洗回母公司
    entity: Optional[str] = None


class BulkAssignAccountPayload(BaseModel):
    """收支明細整批掛銀行帳戶。"""
    bank_account_id: str
    only_unassigned: bool = True


class SetupWizardBankAccount(BaseModel):
    name: str
    bank_name: Optional[str] = None
    account_no: Optional[str] = None
    acct_kind: str = "bank"
    opening_balance: int = 0


class FinanceSetupWizardPayload(BaseModel):
    """財務設定精靈：一次建帳戶 + 掛歷史收支 + 設基準月 + 期初權益。"""
    baseline_month: str                    # 'YYYY-MM'
    bank_accounts: List[SetupWizardBankAccount] = []
    default_account_index: int = 0
    assign_history: bool = True
    equity_amount: Optional[int] = None    # 非空 → 建 3100 期初權益 opening 調整列


# ── 財務管理階段四：銀行貸款（routers/api_finance.py）──────────

class LoanPayload(BaseModel):
    """銀行貸款新增/更新 — create 時 name/principal/term_months/start_date
    必填由端點檢查；改利率/期數等結構欄位時後端只重生未繳期別。"""
    name: Optional[str] = None
    lender: Optional[str] = None
    principal: Optional[int] = None        # 原始本金（新台幣整數）
    annual_rate: Optional[float] = None    # 年利率 %（2.85 = 2.85%）
    term_months: Optional[int] = None      # 期數（opening_balance 模式=剩餘期數）
    method: Optional[str] = None           # annuity/straight/interest_only
    grace_months: Optional[int] = None     # 寬限期（只付息不還本）
    start_date: Optional[str] = None       # 'YYYY-MM-DD'
    first_payment_date: Optional[str] = None  # 'YYYY-MM-DD'（空=起貸日下月同日）
    bank_account_id: Optional[str] = None  # 預設扣款帳戶
    account_no: Optional[str] = None       # 銀行放款帳號（對帳單匯入配對用）
    opening_balance: Optional[int] = None  # 導入舊貸=當下剩餘本金（攤還表只生剩餘期）
    note: Optional[str] = None
    # 兩本帳：None＝建立時落 'parent'、更新時維持既有值；🔴 不可給 "parent" 當預設——整包 model_dump 寫回會把我的帳列洗回母公司
    entity: Optional[str] = None


class LoanPayPayload(BaseModel):
    """貸款繳款：自動建收支明細（expense=實扣金額、category=貸款繳款）。"""
    bank_account_id: Optional[str] = None  # 空 → 用貸款預設扣款帳戶
    paid_date: Optional[str] = None        # 'YYYY-MM-DD'，空 → 今天
    # 銀行實際扣了多少（正整數）。空 → 照攤還表本+息。
    # 對帳單匯入會自動帶銀行那個數字；手動繳款時對著存摺填才會準。
    amount: Optional[int] = None


# ── 書籤（任務路徑預設；2026-07-21 復活 — 前端存 UNC 路徑組，全機隊共用）──

class BookmarkCreateRequest(BaseModel):
    name: str
    task_type: str = "backup"
    request: dict = {}


class BookmarkUpdateRequest(BaseModel):
    name: Optional[str] = None
    request: Optional[dict] = None


# ── 每週工作日誌（journal）──

class JournalPut(BaseModel):
    """PUT /api/v1/journal/mine — 四區塊全量替換（strip/去空/上限在 core.journal_logic）。"""
    wins: List[str] = []
    challenges: List[str] = []
    learnings: List[str] = []
    others: List[str] = []       # 其他主題（2026-07-24 第四問）


# ── 影像紀錄分塊上傳（繞開 Cloudflare 的 100MB 單請求上限）──

class ChunkBeginRequest(BaseModel):
    """POST /public/media-log/{token}/upload/begin

    filename/size/mtime 三個一組是**檔案的身分**：upload_id 由它們推導，
    所以同一個檔重傳一定落回同一個半成品（續傳）。client_key 是瀏覽器自己
    存在 localStorage 的隨機字串 —— 沒有它，兩個人同時傳同名同大小的檔會
    共用同一個 .part。
    """
    filename: str
    size: int = Field(gt=0, description="檔案總位元組數")
    mtime: int = 0               # File.lastModified（毫秒）；拿不到就 0
    client_key: str = ""


class ChunkFinishRequest(BaseModel):
    """POST /public/media-log/{token}/upload/{upload_id}/finish"""
    filename: str
    size: int = Field(gt=0, description="期望的總長度；與實收不符 → 409")
    category: str = ""
    uploader_name: str = ""


class CashInvoiceLink(BaseModel):
    """一筆收款分配到一張發票的金額（合併匯款 / 分期收款）。

    `amount` ＝ **這張發票被認列收到多少**（含被匯費吃掉的部分）—— 它直接寫進
    crm_cash_invoice_links.amount，發票的「已收／尚欠」讀的就是它。
    `fee` ＝ 其中被匯出行扣走、沒有真的進到我們帳戶的那幾十塊。
    所以真正入帳的現金 = amount − fee，而 Σfee 會寫進 crm_cash_entries.bank_fee
    並把 deposit 補回去（見 core.finance_logic.recognize_receipt_fee —— 淨流入不變）。
    """
    invoice_id: str
    amount: int
    fee: int = 0


class CashPaymentLinkItem(BaseModel):
    """對映 CashInvoiceLink（收款側的雙生子）—— 兩者要一起改。

    沒有 `fee`：付款側的匯費是**整筆匯出**收一次（跨行手續費），不是逐張請款單
    被扣，所以它掛在 payload 層（CashPaymentLinksPayload.fee /
    StatementImportRow.payment_fee）而不是這裡。
    """
    payment_request_id: str
    amount: int


class StatementImportRow(BaseModel):
    """對帳單匯入 apply 的一列 —— 前端把 preview 回來的列原樣送回（可改分類/
    可取消勾選）。金額帶號：正=存入、負=支出。"""
    date: str
    amount: int
    description: Optional[str] = ""
    category: Optional[str] = ""
    # 私帳的分類是一棵樹（cash_taxonomy_nodes），`category` 只是路徑前兩層的
    # 鏡射。挑了節點就送它，寫入端用 `_sync_taxonomy` 推三欄 —— 只送 category
    # 的話那一列沒有節點，收支明細的分類篩選與路徑顯示就看不到它。
    taxonomy_node_id: Optional[str] = None
    # 指定成貸款繳款時要帶：配到哪筆貸款的哪一期（preview 已配好或使用者手選）
    loan_id: Optional[str] = None
    period_no: Optional[int] = None
    # 預覽時當場掛的專案與發票（owner 2026-08-20）。
    # 🔴 專案只有專案類的 category 收得下（core/project_link.CASH_CATEGORIES），
    #    發票只有收入列有意義 —— 兩者都由寫入端再驗一次，不信前端。
    project_id: Optional[str] = None
    # 一列可以掛多張發票（合併匯款：客戶一次匯三張的錢）。分配表本來就是多對多。
    # 只有這一種表示法 —— 「主要發票」是從分配表推出來的（金額最大那張），
    # 推導規則只放在 replace_invoice_allocs，前端不留第二份。
    invoices: List[CashInvoiceLink] = []
    # 支出列的鏡像：一筆匯出常常是**一個人的好幾張請款單併著發**（出納統一匯款）。
    # 🔴 只有支出列有意義，由寫入端再驗一次（同 invoices 只認收入列）。
    payments: List[CashPaymentLinkItem] = []
    # 🔴 匯費在兩側的形狀**刻意不同**，不是漏寫：
    #   · 收款側逐張（CashInvoiceLink.fee）—— 匯出行是對每張發票的匯款各扣一次
    #   · 付款側整列一個 —— 跨行手續費是對「這一筆匯出」收的，涵蓋幾張請款單
    #     都一樣。所以它跟 CashPaymentLinksPayload.fee 同形狀，不另立第二種表示法。
    # None ＝ 不認列（不動 bank_fee）。
    payment_fee: Optional[int] = None


class StatementDraftRow(StatementImportRow):
    """草稿存的一列 —— 比 apply 多一個「勾了沒」。

    apply 只收勾選的列（沒勾的根本不會送上來），草稿要**全部**列都存，
    連同「這列我不打算匯」的決定一起 —— 不然下次開啟又全部變回預設勾選。
    """
    selected: bool = False


class StatementDraftPayload(BaseModel):
    """存草稿。id 有值＝更新既有草稿（同一份對帳單改到一半再存一次）。

    source_text 是**原始對帳單文字**：開啟時重新解析，才拿得到最新的重複判定
    與最新的專案／發票清單（見 db.models.BankImportDraft 的說明）。
    """
    id: Optional[str] = None
    bank_account_id: str
    source_text: str
    rows: List[StatementDraftRow] = []
    # 沒有 name：名字由後端從帳戶＋日期區間＋列數自動組（_auto_draft_name）。
    # 沒有改名 UI，而且重存會依當下的列重新命名 —— 真要做改名該是另一支端點。


class StatementImportApply(BaseModel):
    """確認後寫入。rows 只放使用者勾選要匯的列 —— 沒勾的不會出現在這裡。"""
    bank_account_id: str
    rows: List[StatementImportRow] = []


class CardImportRow(BaseModel):
    """信用卡帳單匯入的一列（api_finance_card）。amount 帶號：正=消費、負=退款。"""
    date: str
    amount: int
    note: str = ""
    category: Optional[str] = None
    # 🔴 沒有預設值：舊版前端（只送勾選的列）如果因為快取活著，帶預設 True 會
    # 讓後端對著被裁過的清單重跑重複判定 —— 也就是無聲退回修掉的那個 bug。
    # 必填的話它會 422 大聲壞掉，那是對的失敗方式。
    selected: bool


class CardImportApply(BaseModel):
    """卡單確認後寫入。不掛銀行帳戶（刷卡當下不動銀行 — status='card'）。

    🔴 rows 是**整份卡單**（沒勾的列也要送，用 selected=false 標）。只送勾選
    的列會讓 apply 對著被裁過的清單再扣一次「帳上已有」的筆數 —— 使用者刻意
    勾的列就這樣無聲消失（/simplify 第 4 輪）。整份送進來，apply 才看得到
    preview 看到的那份輸入，兩邊的重複判定才會是同一個答案。
    """
    rows: List[CardImportRow] = []
    # 哪一張卡（owner 2026-08-27「信用卡匯入時也可以區隔哪一個銀行的信用卡」）：
    # bank_accounts 裡 acct_kind='card' 的那筆 id；空＝未指定卡別（沿用舊行為）。
    # 🔴 刷卡列仍**不掛銀行帳戶**（status='card'，刷卡當下不動銀行）——
    # 這個欄位存在 bank_account_id 上是「卡別身分」，不是現金帳戶。
    card_account_id: Optional[str] = None


class CardAiSuggestRow(BaseModel):
    note: str = ""
    amount: int = 0


class CardAiSuggestPayload(BaseModel):
    """規則/歷史都沒答案的列 → claude 整批建議分類。"""
    rows: List[CardAiSuggestRow] = []


class CardLedgerConfig(BaseModel):
    """卡片餘額設定（api_finance_card）。opening 與 derive_opening_from 二選一。"""
    opening: Optional[int] = None
    derive_opening_from: Optional[int] = None   # 給「現在實際欠多少」，反推期初
    repay_categories: Optional[list] = None


class HoldingPayload(BaseModel):
    """資產儀表板 — 持股（api_finance_assets）。quote_symbol 空＝manual。"""
    broker: Optional[str] = None
    symbol: Optional[str] = None
    name: str = ""
    shares: Optional[float] = None
    currency: str = "TWD"
    quote_symbol: Optional[str] = ""
    last_price: Optional[float] = None
    manual_value: Optional[int] = None
    cost_total: Optional[float] = None    # 投資成本（該列的幣別；碎股有小數）
    sort_order: int = 0
    active: bool = True
    note: Optional[str] = None


class LedgerProjectCreate(BaseModel):
    """執行專案 — 新增（api_finance_projects）。owner 的流程是先整理專案再記帳，
    所以入口在帳本頁；工項/費用建立後在詳情編。"""
    name: str
    client_id: Optional[str] = None
    code: str = ""                  # 案碼（owner 自己的案件編號；可空）
    close_date: str = ""            # 'YYYY-MM-DD'；空＝未結案
    contract_amount: Optional[int] = None
    source: str = ""                # 案源：自接/源日(現金收款)/代開發票(自動代辦費)
    fee_pct: Optional[float] = None  # 服務費率 %（代開發票用；預設 8）


class LedgerDetailPayload(BaseModel):
    # crm_pushed：推送/取消推送到專案管理（0/1）。不是損益欄位 —— PUT 端點會先
    # pop 掉，**不能**落進 ledger_detail JSON（那個迴圈把剩餘鍵全當費用欄寫）。
    crm_pushed: Optional[int] = None
    source: Optional[str] = None     # 案源（meta，經 norm_detail 白名單）
    fee_pct: Optional[float] = None  # 服務費率 %（代開發票；預設 8）
    """逐案損益的可編輯欄（api_finance_projects）。

    費用欄用 Optional：只送有改的欄，None＝維持原值（整包 model_dump 寫回
    會把沒送的欄洗成 0 —— 這個 repo 咬過那個坑）。split 送就是整份取代
    （工項是一組值，逐項 patch 沒有語意）。
    """
    outsource: Optional[int] = None
    tax_fee: Optional[int] = None
    buy_invoice: Optional[int] = None
    invoice_fee: Optional[int] = None
    personal_tax: Optional[int] = None
    misc: Optional[int] = None
    shareholder: Optional[int] = None
    split: Optional[dict] = None
    contract_amount: Optional[int] = None    # 營收(含稅)，直接落 crm_projects
    close_date: Optional[str] = None         # 結案日 'YYYY-MM-DD'；''＝清空


# 註：本組 payload 刻意**沒有** entity —— 帳本一律由 query 的 entity 經
# `_guard` 決定，payload 說了不算（不然等於讓請求體自己挑要寫進哪本帳）。
class NetSnapshotPayload(BaseModel):
    """淨值快照。total 由後端加總（前端算的不收）。"""
    snap_date: str = ""
    buckets: dict = {}
    note: Optional[str] = None
    entity: Optional[str] = None


class BankImportRulePayload(BaseModel):
    # 命中後要掛的分類樹節點（私帳可指定到任何一層；空＝只用 category）
    taxonomy_node_id: Optional[str] = None
    """對帳單摘要 → 收支類別 的分類規則（使用者可編）。

    🔴 direction 不在這裡 —— 它只在「第一列推不出方向」的邊緣情況用得到，
    讓人去理解 +1/-1/0 不划算。使用者新增的規則一律 0（＝看不出方向，
    解析器會標記該列要人確認）。種子那 14 條的方向值保留在 DB 裡。
    """
    keyword: str
    category: str
    bank_account_id: Optional[str] = None    # 空 = 套用到所有帳戶
    sort_order: int = 100
    # 只在這個方向的列上套用：-1 只支出 / +1 只存入 / 0 不限。
    # 跟 direction（推方向的提示）是兩件事 —— 見 BankImportRule 的註解。
    only_direction: int = 0
    active: bool = True
    note: str = ""


class CashInvoiceLinksPayload(BaseModel):
    """整組取代某筆收款的發票分配 —— 送空 items 就是全部解除。"""
    items: List[CashInvoiceLink] = []


class CashPaymentLinksPayload(BaseModel):
    """一筆匯款掛哪幾張請款單（整組取代）—— 送空 items 就是全部解除。

    `fee` 由呼叫端決定要不要把差額認成匯費：alloc_verdict 的 payment 側判為
    fee 時前端會把它一起送回來，寫進 crm_cash_entries.bank_fee（見
    recognize_bank_fee —— 那條路把匯費算成管理費用與現金流出）。
    None ＝ 不動既有的 bank_fee。
    """
    items: List[CashPaymentLinkItem] = []
    fee: Optional[int] = None


# ── 福委會（docs/BENEFIT_POOL_PLAN.md）───────────────────────────────

class BenefitPoolPayload(BaseModel):
    """福利池（快樂／進修）。

    `entity` 預設 **None** 不是 'parent' —— 給實體值當預設的話，舊前端整包
    model_dump 寫回會把另一本帳的列洗過去（docs/LEDGER_ENTITY_PLAN.md §2.3
    的教訓，兩本帳的 payload 一律這樣）。
    """
    name: str = ""
    status: str = "open"            # open/closed
    sort_order: int = 0
    notes: str = ""
    entity: Optional[str] = None
    # 年度活動（docs/BENEFIT_POOL_PLAN.md §9）
    quota: str = "shared"           # shared（共用桶）/ per_person（每人一份）
    description: str = ""           # 說明（給員工看的，健檢方案就是這欄）
    valid_from: str = ""            # YYYY-MM-DD，空＝不限
    valid_to: str = ""
    # 🔴 attachments **刻意不在 payload 裡**：附件只能走上傳／刪除那兩支端點。
    #    放進來的話，任何一個沒帶這欄的舊表單整包寫回就會把附件清空
    #    （「整包 model_dump 寫回會被預設值洗掉」的老坑）。


class BenefitAllowancePayload(BaseModel):
    """發給某個人的額度。`staff_id` 由管理端指定（這不是 own-scope 端點）。"""
    staff_id: str = ""
    amount: int = 0
    valid_from: str = ""            # 空＝繼承池的期間
    valid_to: str = ""
    notes: str = ""


class BenefitFundingPayload(BaseModel):
    """撥款：每年公司放進池裡的那筆錢。"""
    year: int = 0                   # 0 → 今年
    amount: int = 0
    fund_date: str = ""             # YYYY-MM-DD，空 → 今天
    notes: str = ""


class BenefitEntryPayload(BaseModel):
    """員工登記的一筆花費。

    `staff_id` 只有**管理端代登**那條路會用；本人自助的端點忽略這欄、
    一律從 token 解（own-scope 的定義，同零用金 PettyExpensePayload）。
    項目是自由文字 —— 分類這件事由池承擔（快樂／進修），不再多一層枚舉。
    """
    pool_id: str = ""
    staff_id: str = ""
    title: str = ""                 # 電影名／餐廳／課程名
    amount: int = 0                 # 正數
    spend_date: str = ""            # YYYY-MM-DD，空 → 今天
    # 心得筆記（非必填）。與 notes 分開 —— notes 裝退回原因
    reflection: str = ""
    notes: str = ""


class TransferFeeRecognize(BaseModel):
    """把帳戶間轉存的配對差額認列成跨行手續費（總流出不變）。

    `entry_ids` 空 ＝ 全部可認列的都做。後端一律重算判準，不信這裡送來的金額
    （見 routers/api_finance.recognize_transfer_fees 的說明）。
    """
    entry_ids: List[str] = []
