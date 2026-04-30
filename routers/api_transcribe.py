import os
import sys
import subprocess
from fastapi import APIRouter  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore
from core.schemas import TranscribeRequest, AlignRequest  # type: ignore
from core.worker import enqueue_job  # type: ignore

router = APIRouter()

# PyPI package name → import name. The align flow needs both.
# Listed here (not in requirements_agent.txt) because they're heavy and
# only needed by users who actually run align on this agent.
_ALIGN_DEPS = [("stable-ts", "stable_whisper"), ("pysrt", "pysrt")]

@router.post("/api/v1/jobs/transcribe")
async def create_transcribe_job(req: TranscribeRequest):
    project_name = req.project_name or os.path.basename(req.dest_dir) or "unnamed"
    job_id, warning = await enqueue_job(req, project_name, "transcribe")
    resp = {"status": "queued", "job_id": job_id}
    if warning:
        resp["warning"] = warning
    return resp


@router.post("/api/v1/jobs/align")
async def create_align_job(req: AlignRequest):
    """Force-align known transcripts to video audio → timed SRT files.

    See aligner.py for the underlying anchor + interpolation algorithm.
    Reuses the transcribe queue (single-slot AI inference).
    """
    if not req.tasks:
        return JSONResponse({"detail": "tasks 不能為空"}, status_code=400)
    for i, t in enumerate(req.tasks):
        if not t.source or not t.transcript.strip():
            return JSONResponse(
                {"detail": f"第 {i+1} 個任務缺少影片或文字稿"},
                status_code=400,
            )

    project_name = req.project_name or os.path.basename(req.dest_dir) or "align"
    # Use task_type='transcribe' for queue scheduling — shares the same
    # AI inference slot to prevent two GPU jobs running concurrently.
    job_id, warning = await enqueue_job(req, project_name, "transcribe")
    resp = {"status": "queued", "job_id": job_id, "mode": "align"}
    if warning:
        resp["warning"] = warning
    return resp


@router.get("/api/v1/jobs/align/check_deps")
async def check_align_deps():
    """Pre-flight: tell the frontend which align deps are missing on this agent.

    Used by submitAlignJob to skip the failing-server-roundtrip on agents that
    don't have stable-ts / pysrt installed (heavy ML deps not in
    requirements_agent.txt).
    """
    missing = []
    for pkg, import_name in _ALIGN_DEPS:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg)
    return {"ok": len(missing) == 0, "missing": missing}


@router.post("/api/v1/jobs/align/install_deps")
async def install_align_deps():
    """Run pip install for missing align deps in this agent's venv.

    Streamed via JSON response (not SSE) — the frontend just shows the final
    log; install typically takes 30-90s for stable-ts + torch + faster-whisper.
    """
    cmd = [sys.executable, "-m", "pip", "install", "-U"]
    cmd.extend(pkg for pkg, _ in _ALIGN_DEPS)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            encoding="utf-8", errors="replace",
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-2000:],
        }
    except subprocess.TimeoutExpired:
        return JSONResponse(
            {"ok": False, "detail": "pip install 超時(>10 分鐘)"},
            status_code=504,
        )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "detail": f"執行失敗: {e}"},
            status_code=500,
        )
