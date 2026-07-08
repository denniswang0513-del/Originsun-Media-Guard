from pydantic import BaseModel, Field  # type: ignore
from typing import List, Optional, Tuple


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


class BulletinUpdate(BaseModel):
    title: Optional[str] = None
    note: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    category: Optional[str] = None
    pinned: Optional[bool] = None
    assignee: Optional[str] = None


class BulletinReorder(BaseModel):
    ordered_ids: List[str]


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
    source_channel: str = ""
    contact_person: str = ""
    contact_method: str = ""
    status: str = "潛在客戶"
    cooperation_note: str = ""
    payment_info: str = ""
    payment_note: str = ""
    notes: str = ""


class CrmProjectPayload(BaseModel):
    name: str
    client_id: str
    status: str = "洽談中"
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


class ProjectExpensePayload(BaseModel):
    category: str
    estimated: int = 0
    actual: int = 0
    sub_item: str = ""
    payee: str = ""
    advance_id: str = ""
    notes: str = ""
    cost_group_id: Optional[str] = None


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
    project_id: Optional[str] = None
    project_label: str = ""
    payment_date: Optional[str] = None
    payment_status: str = "應付款"
    planned_month: str = ""
    advance_by: str = ""
    is_advance: int = 0
    advance_returned: int = 0
    notes: str = ""


class CashEntryPayload(BaseModel):
    entry_date: Optional[str] = None
    expense: Optional[int] = None
    claim: Optional[int] = None
    deposit: Optional[int] = None
    summary: str
    note: str = ""
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
    project_id: Optional[str] = None
    recipient: str = ""
    recipient_phone: str = ""
    recipient_address: str = ""
    notes: str = ""


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


class ReferencePayload(BaseModel):
    """參考片庫（跨提案共用）新增/更新（create 時 url 由端點檢查必填）。"""
    url: Optional[str] = None
    title: Optional[str] = None
    note: Optional[str] = None
    tags: Optional[list] = None
    thumb_url: Optional[str] = None


class IntelSourcePayload(BaseModel):
    """產業情報來源（P-c）新增/更新 — 全欄 Optional 配合部分更新
    （create 時 url 開頭 http 由端點檢查）。"""
    name: Optional[str] = None
    type: Optional[str] = None            # rss / html（html 第一版先跳過不抓）
    url: Optional[str] = None
    keywords: Optional[list] = None       # list[str] 關鍵字；空 = 全收
    enabled: Optional[bool] = None
    note: Optional[str] = None
