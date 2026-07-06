"""Utility endpoints: file open, folder pick, shortcut creation, path resolution.

Split from api_system.py — all endpoint paths remain unchanged.
"""
import os
import re
import subprocess
import sys
import asyncio
import locale
import time
from fastapi import APIRouter, UploadFile, File, Form  # type: ignore
from core.schemas import OpenFileRequest, ValidatePathsRequest  # type: ignore

router = APIRouter()


def _is_session_0() -> bool:
    """Return True if this process runs in Session 0 (Services session).

    tkinter file/folder dialogs spawned from Session 0 render to the
    non-interactive desktop and are invisible to logged-in users in
    Session 1. Detect early so picker endpoints can fail-fast with a
    helpful message instead of hanging on the 120s subprocess timeout.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("Kernel32.dll")
        ses = wintypes.DWORD()
        if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(ses)):
            return False
        return ses.value == 0
    except Exception:
        return False


_SESSION_0_HINT = (
    "主控端跑在 Session 0(Services session),tkinter picker 視窗會渲染到非互動桌面,"
    "你看不到也點不到。修法:雙擊主控端桌面的 start_hidden.vbs 重啟,或登出 Windows 再登入"
    "(OriginsunBoot 排程任務會自動把主控端拉回你的 Session 1)。"
)


@router.post("/api/v1/utils/open_file")
async def open_local_file(req: OpenFileRequest):
    try:
        import webbrowser as _wb
        safe_path = req.path.replace("\\", "/")
        if not safe_path.startswith("file://"): safe_path = f"file:///{safe_path}"
        _wb.open(safe_path)
        return {"status": "ok", "opened": safe_path}
    except Exception as e: return {"status": "error", "message": str(e)}

@router.post("/api/v1/utils/open_folder")
async def open_local_folder(req: OpenFileRequest):
    try:
        folder_path = os.path.dirname(req.path)
        if os.path.exists(folder_path):
            import subprocess
            subprocess.Popen(f'explorer /select,"{req.path}"')
            return {"status": "ok", "opened": folder_path}
        return {"status": "error", "message": f"資料夾不存在: {folder_path}"}
    except Exception as e: return {"status": "error", "message": str(e)}

_VIDEO_FILETYPES = (
    "(('Video', '*.mov *.mp4 *.mkv *.mxf *.avi *.mts *.m2ts *.MOV *.MP4'),"
    " ('All files', '*.*'))"
)


def _run_picker_subprocess(mode: str, title: str):
    """Run tkinter file/folder dialog in a fresh subprocess.

    Modes: 'folder' → askdirectory (str), 'file' → askopenfilename (str),
    'files' → askopenfilenames with video filter (list[str]).

    Returns either the picked path/list of paths, or a dict with key
    `_error`/`_message` if pre-check failed (Session 0). Endpoints unwrap
    the dict and surface error in the JSON response so frontend can show
    an actionable message instead of silently waiting 120s.
    """
    if _is_session_0():
        return {"_error": "session_0", "_message": _SESSION_0_HINT}

    import json as _j
    if mode == "folder":
        dialog_call = f"filedialog.askdirectory(title={title!r})"
        out_expr = "{'path': result or ''}"
    elif mode == "files":
        dialog_call = f"filedialog.askopenfilenames(title={title!r}, filetypes={_VIDEO_FILETYPES})"
        out_expr = "{'paths': list(result) if result else []}"
    else:
        dialog_call = f"filedialog.askopenfilename(title={title!r})"
        out_expr = "{'path': result or ''}"
    script = (
        "import tkinter as tk\n"
        "from tkinter import filedialog\n"
        "import json, sys\n"
        "root = tk.Tk()\n"
        "root.attributes('-topmost', True)\n"
        "root.withdraw()\n"
        f"result = {dialog_call}\n"
        "root.destroy()\n"
        f"sys.stdout.write(json.dumps({out_expr}))\n"
    )
    try:
        res = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=120,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if res.returncode == 0 and res.stdout.strip():
            data = _j.loads(res.stdout.strip())
            return data.get("paths", []) if mode == "files" else data.get("path", "")
    except Exception:
        pass
    return [] if mode == "files" else ""


def _unwrap_picker_result(result, list_mode: bool):
    """Translate _run_picker_subprocess return into endpoint JSON shape."""
    if isinstance(result, dict) and "_error" in result:
        if list_mode:
            return {"paths": [], "error": result["_error"], "message": result["_message"]}
        return {"path": "", "error": result["_error"], "message": result["_message"]}
    return {"paths": result} if list_mode else {"path": result}


@router.get("/api/v1/utils/pick_folder")
async def api_pick_folder(title: str = "選擇資料夾"):
    result = await asyncio.to_thread(_run_picker_subprocess, "folder", title)
    return _unwrap_picker_result(result, list_mode=False)

@router.get("/api/v1/utils/pick_file")
async def api_pick_file(title: str = "選擇檔案"):
    result = await asyncio.to_thread(_run_picker_subprocess, "file", title)
    return _unwrap_picker_result(result, list_mode=False)

@router.get("/api/v1/utils/pick_files")
async def api_pick_files(title: str = "選擇影片（可多選）"):
    result = await asyncio.to_thread(_run_picker_subprocess, "files", title)
    return _unwrap_picker_result(result, list_mode=True)


@router.get("/api/v1/utils/read_text")
async def read_text_file(path: str, max_bytes: int = 4 * 1024 * 1024):
    """Read a small text file (default cap 4 MB). Used by align mode to
    auto-load matched .txt/.srt files when batch-importing a video folder."""
    try:
        if not os.path.isfile(path):
            return {"ok": False, "error": "file not found"}
        size = os.path.getsize(path)
        if size > max_bytes:
            return {"ok": False, "error": f"檔案過大 ({size} bytes > {max_bytes})"}
        with open(path, "rb") as f:
            raw = f.read()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        for enc in ("utf-8", "utf-16", "big5", "gbk"):
            try:
                return {"ok": True, "text": raw.decode(enc), "encoding": enc}
            except UnicodeDecodeError:
                continue
        return {"ok": False, "error": "無法解碼（嘗試 utf-8/utf-16/big5/gbk 都失敗）"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@router.get("/api/v1/utils/browse_dir")
async def browse_dir(path: str = ""):
    """列出指定路徑下的子資料夾（供網頁版目錄瀏覽器使用）。"""
    import string
    if not path:
        # Return available drive letters on Windows
        drives = []
        for letter in string.ascii_uppercase:
            dp = f"{letter}:\\"
            if os.path.isdir(dp):
                drives.append({"name": f"{letter}:\\", "path": dp})
        return {"items": drives, "current": ""}

    abs_path = os.path.abspath(path)
    if not os.path.isdir(abs_path):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="路徑不存在")

    items = []
    try:
        for entry in sorted(os.scandir(abs_path), key=lambda e: e.name.lower()):
            if entry.is_dir() and not entry.name.startswith('.'):
                items.append({"name": entry.name, "path": entry.path})
    except PermissionError:
        pass
    return {"items": items, "current": abs_path}

@router.post("/api/v1/utils/upload_file")
async def upload_file_to_path(dest_path: str = Form(""), file: UploadFile = File(...)):
    """上傳檔案到指定伺服器路徑（限定 browse_roots 內）。"""
    from fastapi import HTTPException
    if not dest_path:
        raise HTTPException(status_code=400, detail="缺少 dest_path")
    abs_dest = os.path.abspath(dest_path)
    # 安全驗證：限定在 browse_roots 或 uploads/ 目錄內
    from config import load_settings
    settings = load_settings()
    roots = settings.get("browse_roots", [])
    uploads_dir = os.path.abspath(os.path.join(os.getcwd(), "uploads"))
    allowed = abs_dest.startswith(uploads_dir)
    for r in roots:
        if abs_dest.startswith(os.path.abspath(r)):
            allowed = True
            break
    if not allowed:
        raise HTTPException(status_code=403, detail="目的路徑不在允許範圍內")
    os.makedirs(abs_dest, exist_ok=True)
    # 防止路徑穿越：只取檔名
    safe_name = os.path.basename(file.filename or "upload")
    filepath = os.path.join(abs_dest, safe_name)
    # 串流寫入避免大檔案佔滿記憶體
    import shutil
    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"status": "ok", "path": filepath}


@router.post("/api/v1/utils/create_shortcut")
async def create_desktop_shortcut():
    import subprocess
    import locale
    try:
        vbs_path = os.path.join(os.getcwd(), "Create_Shortcut.vbs")

        ico_path = os.path.join(os.getcwd(), "logo.ico")

        vbs_code = f"""
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
chromePath1 = WshShell.ExpandEnvironmentStrings("%ProgramFiles%") & "\\Google\\Chrome\\Application\\chrome.exe"
chromePath2 = WshShell.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\\Google\\Chrome\\Application\\chrome.exe"
chromePath3 = WshShell.ExpandEnvironmentStrings("%LocalAppData%") & "\\Google\\Chrome\\Application\\chrome.exe"
chromePath = ""
If fso.FileExists(chromePath1) Then
    chromePath = chromePath1
ElseIf fso.FileExists(chromePath2) Then
    chromePath = chromePath2
ElseIf fso.FileExists(chromePath3) Then
    chromePath = chromePath3
End If
serverUrl = "http://localhost:8000"
icoPath = "{ico_path}"
sLinkFile = WshShell.SpecialFolders("Desktop") & "\\Originsun Media Guard Web.lnk"
Set oLink = WshShell.CreateShortcut(sLinkFile)
If chromePath <> "" Then
    oLink.TargetPath = chromePath
    oLink.Arguments = "--app=" & serverUrl & " --start-fullscreen"
    If fso.FileExists(icoPath) Then
        oLink.IconLocation = icoPath
    Else
        oLink.IconLocation = chromePath & ", 0"
    End If
Else
    oLink.TargetPath = serverUrl
    If fso.FileExists(icoPath) Then
        oLink.IconLocation = icoPath
    End If
End If
oLink.Description = "Originsun Media Guard Pro Web Edition"
oLink.WindowStyle = 1
oLink.Save
MsgBox "桌面捷徑已成功建立！請查看桌面上的「Originsun Media Guard Web」。", vbInformation, "安裝成功"
"""
        sys_enc = locale.getpreferredencoding()
        with open(vbs_path, "w", encoding=sys_enc, errors="replace") as f:
            f.write(vbs_code.strip())

        subprocess.run(["cscript.exe", "//nologo", vbs_path], check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
        return {"status": "success", "message": "桌面捷徑已成功建立！\n請查看桌面上的「Originsun Media Guard Web」。"}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "message": f"腳本執行失敗 (cscript 錯誤): {str(e)}"}
    except Exception as e:
        return {"status": "error", "message": f"建立失敗: {str(e)}"}

@router.post("/api/v1/validate_paths")
async def validate_paths(req: ValidatePathsRequest):
    """檢查每個路徑的磁碟機是否存在、路徑本身是否存在（供遠端主機派發前驗證）"""
    results = {}
    for p in req.paths:
        drive = os.path.splitdrive(p)[0]  # e.g. "S:"
        drive_exists = os.path.exists(drive + os.sep) if drive else True
        path_exists = os.path.exists(p)
        results[p] = {
            "drive": drive,
            "drive_exists": drive_exists,
            "path_exists": path_exists,
        }
    return {"status": "ok", "results": results}

@router.get("/api/v1/utils/resolve_drop")
async def api_resolve_drop(name: str):
    def _find():
        import re
        import subprocess

        clean_name = name.strip()

        match = re.search(r'\(([a-zA-Z]):\)', clean_name)
        if match: return f"{match.group(1).upper()}:\\"
        match = re.search(r'^([a-zA-Z]):$', clean_name)
        if match: return f"{match.group(1).upper()}:\\"
        match = re.search(r'^([a-zA-Z])_drive$', clean_name, re.IGNORECASE)
        if match: return f"{match.group(1).upper()}:\\"

        drives = [f"{chr(d)}:\\" for d in range(65, 91) if os.path.exists(f"{chr(d)}:\\")]

        for drive in drives:
            try:
                out = subprocess.check_output(f'vol {drive[0]}:', text=True, shell=True, stderr=subprocess.DEVNULL)
                if clean_name.lower() in out.lower(): return drive
            except Exception: pass

        try:
            ps_script = """
            [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
            $app = New-Object -ComObject Shell.Application
            foreach ($win in $app.Windows()) {
                try {
                    $sel = $win.Document.SelectedItems()
                    if ($sel -ne $null) {
                        foreach ($item in $sel) { Write-Output $item.Path }
                    }
                    $folderPath = $win.Document.Folder.Self.Path
                    if ($folderPath -ne $null -and $folderPath -ne "") {
                        Write-Output "FOLDER:$folderPath"
                    }
                } catch {}
            }
            """
            paths = subprocess.check_output(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script], encoding="utf-8", errors="replace", stderr=subprocess.DEVNULL).splitlines()
            lower_name = clean_name.lower()

            selected_items = []
            open_folders = []
            for p in paths:
                p = p.strip()
                if not p: continue
                if p.startswith("FOLDER:"): open_folders.append(p.removeprefix("FOLDER:"))
                else: selected_items.append(p)

            exact_matches = [p for p in selected_items if os.path.basename(p).lower() == lower_name]
            if exact_matches: return max(exact_matches, key=len)

            folder_candidates = []
            for folder in open_folders:
                candidate = os.path.join(folder, clean_name)
                if os.path.exists(candidate): folder_candidates.append(candidate)
            if folder_candidates: return max(folder_candidates, key=len)
        except Exception: pass

        user_profile = os.environ.get('USERPROFILE', '')
        known_system_dirs = ["Desktop", "Downloads", "Documents", "Videos", "Pictures", "Music"]
        if user_profile:
            for sysdir in known_system_dirs:
                if clean_name.lower() == sysdir.lower():
                    full = os.path.join(user_profile, sysdir)
                    if os.path.exists(full): return full
                    onedrive = os.environ.get('OneDriveConsumer', os.environ.get('OneDrive', ''))
                    if onedrive:
                        od_path = os.path.join(onedrive, sysdir)
                        if os.path.exists(od_path): return od_path

        if user_profile:
            for subdir in known_system_dirs:
                candidate = os.path.join(user_profile, subdir, clean_name)
                if os.path.exists(candidate): return candidate

        if len(clean_name) >= 2:
            for drive in drives:
                candidate = os.path.join(drive, clean_name)
                if os.path.exists(candidate): return candidate

            for drive in drives:
                try:
                    for sub in os.scandir(drive):
                        if sub.is_dir():
                            candidate = os.path.join(sub.path, clean_name)
                            if os.path.exists(candidate): return candidate
                except Exception: continue

        return ""

    path = await asyncio.to_thread(_find)  # type: ignore
    return {"path": path}


# ── NAS Browser (for external access) ──────────────────────────────────
VIDEO_EXTS = {".mov", ".mp4", ".mkv", ".mxf", ".avi", ".mts", ".m2ts", ".r3d", ".braw"}

@router.get("/api/v1/browse")
async def browse_directory(path: str = "", show_files: bool = False):
    """List subdirectories (and optionally video files) under a given path.
    Restricted to browse_roots whitelist in settings.json."""
    from config import load_settings  # type: ignore
    settings = load_settings()
    roots = settings.get("browse_roots", [])

    # Auto-detect roots from all available drive letters if not configured
    if not roots:
        import string
        for letter in string.ascii_uppercase:
            drive = f"{letter}:/"
            if os.path.isdir(drive):
                roots.append(drive)
        # Also add NAS UNC paths from settings
        for k, v in settings.get("nas_paths", {}).items():
            if v and v.startswith("\\\\"):
                root = "\\\\".join(v.split("\\")[:4]) + "\\"  # e.g. \\192.168.1.132\Container
                if root not in roots:
                    roots.append(root)

    # Normalize for comparison
    def _norm(p):
        return p.replace("\\", "/").rstrip("/").lower()

    # If no path specified, return available roots
    if not path:
        available = []
        for r in roots:
            r_clean = r.replace("\\", "/").rstrip("/")
            try:
                if os.path.isdir(r):
                    available.append({"name": r_clean, "path": r_clean, "type": "root"})
            except Exception:
                pass
        return {"status": "ok", "path": "", "entries": available, "roots": [r.replace("\\", "/").rstrip("/") for r in roots]}

    # Security: path must start with one of the allowed roots
    norm_path = _norm(path)
    allowed = any(norm_path.startswith(_norm(r)) for r in roots)
    if not allowed:
        return {"status": "error", "message": f"Access denied: {path}"}

    if not os.path.isdir(path):
        return {"status": "error", "message": f"Directory not found: {path}"}

    entries = []
    try:
        with os.scandir(path) as it:
            for entry in sorted(it, key=lambda e: (not e.is_dir(), e.name.lower())):
                if entry.name.startswith(".") or entry.name.startswith("@"):
                    continue  # Skip hidden/system dirs (NAS @Recycle etc.)
                if entry.is_dir():
                    entries.append({
                        "name": entry.name,
                        "path": entry.path.replace("\\", "/"),
                        "type": "dir"
                    })
                elif show_files:
                    try:
                        size = entry.stat().st_size
                    except Exception:
                        size = 0
                    entries.append({
                        "name": entry.name,
                        "path": entry.path.replace("\\", "/"),
                        "type": "file",
                        "size": size
                    })
    except PermissionError:
        return {"status": "error", "message": f"Permission denied: {path}"}

    return {
        "status": "ok",
        "path": path.replace("\\", "/"),
        "entries": entries,
        "parent": os.path.dirname(path).replace("\\", "/") if path else ""
    }


# ── Drive letter → UNC mapping (for distributed transcode) ─────────────
_drive_map_cache: dict = {}
_drive_map_ts: float = 0.0
_drive_map_lock = asyncio.Lock()


@router.get("/api/v1/utils/drive_map")
async def get_drive_map():
    """Return mapping of drive letters to UNC paths.
    Used by frontend to convert paths before sending to remote agents."""
    global _drive_map_cache, _drive_map_ts
    now = time.time()
    # Cache for 60 seconds
    if _drive_map_cache and now - _drive_map_ts < 60:
        return {"status": "ok", "mappings": _drive_map_cache}

    async with _drive_map_lock:
        # Re-check after acquiring lock (another request may have refreshed)
        if _drive_map_cache and time.time() - _drive_map_ts < 60:
            return {"status": "ok", "mappings": _drive_map_cache}

        result = await asyncio.to_thread(_scan_drive_mappings)
        _drive_map_cache = result
        _drive_map_ts = time.time()
        return {"status": "ok", "mappings": result}


# 公司通用磁碟對應表（磁碟代號 → UNC）。各台機器把同一 NAS 掛成不同磁碟代號
# （master 掛 T:/S:，禮瑜電腦掛 G/H/I/J…），磁碟代號「不能跨機器共用」，但 UNC
# 全公司通用。有了這張中央表，任何機器都能把別台機器的磁碟代號路徑（如 T:\…）
# 轉成 UNC → 送到任何 host 都讀得到（修「網路磁碟未掛載」串帶/轉檔失敗）。
# 覆寫優先序：中央表(base) < settings.json 的 drive_unc_map < 當台實際 net use。
_STUDIO_DRIVE_MAP: dict = {
    "N:": r"\\192.168.1.132\Originsun",
    "P:": r"\\192.168.1.132\00_Inbox",
    "Q:": r"\\192.168.1.132\PreProduction",
    "R:": r"\\192.168.1.132\Project_ShortTerm",
    "S:": r"\\192.168.1.132\Project_Midterm",
    "T:": r"\\192.168.1.132\Project_Longterm",
    "U:": r"\\192.168.1.132\ProjectYuan",
    "V:": r"\\192.168.1.130\storage 01",
}


def _scan_drive_mappings() -> dict:
    """Scan network drive letter → UNC path mappings via `net use`,
    再與公司通用中央表合併（跨機器可攜；當台實際 net use 最優先）。"""
    sys_enc = locale.getpreferredencoding() or 'utf-8'
    mappings: dict[str, str] = {}
    try:
        r = subprocess.run(
            ['net', 'use'], capture_output=True, text=True,
            encoding=sys_enc, errors='replace', timeout=5,
        )
        drives = re.findall(r'OK\s+([A-Z]:)', r.stdout)
        # Query each drive individually for accurate share name (handles spaces)
        for drv in drives:
            try:
                r2 = subprocess.run(
                    ['net', 'use', drv], capture_output=True, text=True,
                    encoding=sys_enc, errors='replace', timeout=3,
                )
                for line in r2.stdout.split('\n'):
                    idx = line.find('\\\\')
                    if idx >= 0:
                        unc = line[idx:].rstrip()
                        if unc:
                            mappings[drv.upper()] = unc
                        break
            except Exception:
                pass
    except Exception:
        pass

    # settings.json 可覆寫/擴充中央表（NAS 分享增減時免改碼）
    override: dict = {}
    try:
        from config import load_settings
        raw = (load_settings() or {}).get("drive_unc_map") or {}
        if isinstance(raw, dict):
            override = {str(k).upper(): v for k, v in raw.items() if v}
    except Exception:
        pass

    # 合併：中央表(base) < settings 覆寫 < 當台實際 net use（最優先，machine 有掛的才算數）
    return {**_STUDIO_DRIVE_MAP, **override, **mappings}
