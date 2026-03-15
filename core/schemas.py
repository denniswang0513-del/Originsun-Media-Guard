from pydantic import BaseModel  # type: ignore
from typing import List, Tuple

class BackupRequest(BaseModel):
    task_type: str = "backup"
    project_name: str
    local_root: str
    nas_root: str
    proxy_root: str
    cards: List[Tuple[str, str]]
    do_hash: bool = True
    do_transcode: bool = True
    do_concat: bool = True
    do_report: bool = False

class TranscodeRequest(BaseModel):
    task_type: str = "transcode"
    sources: List[str]
    dest_dir: str

class ConcatRequest(BaseModel):
    task_type: str = "concat"
    sources: List[str]
    dest_dir: str
    custom_name: str = ""
    resolution: str = "1080P"
    codec: str = "ProRes"
    burn_timecode: bool = True
    burn_filename: bool = False

class VerifyRequest(BaseModel):
    task_type: str = "verify"
    pairs: List[Tuple[str, str]]
    mode: str = "quick"

class ReportJobRequest(BaseModel):
    task_type: str = "report"
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

class TranscribeRequest(BaseModel):
    task_type: str = "transcribe"
    sources: List[str]
    dest_dir: str
    model_size: str = "turbo"
    output_srt: bool = True
    output_txt: bool = True
    output_wav: bool = False
    generate_proxy: bool = False
    individual_mode: bool = False

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
