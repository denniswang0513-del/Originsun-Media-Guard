from pydantic import BaseModel  # type: ignore
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
