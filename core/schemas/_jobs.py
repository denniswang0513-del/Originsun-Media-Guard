"""core/schemas/_jobs.py — 任務／系統／備份／轉檔／報表／TTS 等根層請求（第一段，沒有分節標題）。

拆自 core/schemas.py（2026-09-11，2,000 行剛好卡在單次讀取上限）。
界畫在原本的分節註解上、零 class 搬家；對外仍是 `from core.schemas import X`。
"""
from pydantic import BaseModel  # type: ignore
from typing import List, Optional, Tuple


class BackupRequest(BaseModel):
    task_type: str = "backup"
    job_id: str = ""
    project_name: str
    # 綁定的 CRM 專案（選填）。有值時三根由 enqueue_job 從 DB 覆寫 ——
    # 前端送的路徑一律不算數（owner 2026-09-10「備份頁只是顯示」）。
    # 空＝臨時任務，三根照舊由使用者手打。
    project_id: str = ""
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


