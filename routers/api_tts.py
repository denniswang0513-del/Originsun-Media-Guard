"""
api_tts.py — TTS router (Edge-TTS + F5-TTS Voice Clone + 台灣正音)
Endpoints:
  POST /api/v1/tts_jobs             — standard edge-tts (with taiwan normalizer)
  POST /api/v1/tts_jobs/clone       — voice cloning via F5-TTS
  GET  /api/v1/tts/voices           — list available edge-tts voices
  GET  /api/v1/tts/preview          — stream preview audio
  POST /api/v1/tts/estimate         — estimate audio duration
  GET  /api/v1/tts/f5_status        — check F5-TTS availability
  GET  /api/v1/tts/dictionary       — read taiwan_dict.json
  POST /api/v1/tts/dictionary       — update taiwan_dict.json (hot reload)
  GET  /api/v1/voice_profiles       — list NAS voice profiles
  POST /api/v1/voice_profiles       — add new profile to NAS
  DELETE /api/v1/voice_profiles/{id}— delete profile
  POST /api/v1/voice_profiles/{id}/cache — cache NAS profile locally
"""
import os
import time
import uuid
import json
import shutil
import subprocess
from fastapi import APIRouter, HTTPException, BackgroundTasks  # pyre-ignore[21]
from fastapi.responses import StreamingResponse  # pyre-ignore[21]
from pydantic import BaseModel  # pyre-ignore[21]
import edge_tts  # pyre-ignore[21]
from typing import Optional

from config import load_settings  # pyre-ignore[21]
from tts_engine import run_edge_tts, run_f5_tts_clone, f5_tts_is_available, f5_model_is_ready, f5_model_status, download_f5_model  # pyre-ignore[21]

router = APIRouter(prefix="/api/v1", tags=["TTS"])

# ─── Helpers ──────────────────────────────────────────────
def _get_nas_library_path() -> str:
    settings = load_settings()
    # Check legacy key first, then new nas_paths.voice_dir
    legacy = settings.get("voice_library_nas_path", "")
    if legacy:
        return legacy
    return settings.get("nas_paths", {}).get("voice_dir", "")

def _get_cache_path() -> str:
    settings = load_settings()
    return settings.get("voice_library_cache_path", "./voices_cache")

def _profiles_file(nas_path: str) -> str:
    return os.path.join(nas_path, "profiles.json")

def _load_profiles(nas_path: str) -> list:
    f = _profiles_file(nas_path)
    if not os.path.exists(f):
        return []
    try:
        with open(f, encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return []

def _save_profiles(nas_path: str, profiles: list):
    os.makedirs(nas_path, exist_ok=True)
    with open(_profiles_file(nas_path), "w", encoding="utf-8") as fp:
        json.dump(profiles, fp, ensure_ascii=False, indent=2)

# ─── Schemas ──────────────────────────────────────────────
class TtsRequest(BaseModel):
    text: str
    voice: str = "zh-TW-HsiaoChenNeural"
    rate: int = 0
    pitch: int = 0
    output_dir: str
    output_name: str = "tts_output"

class NewProfileRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    reference_audio: str
    language: str = "zh"

class VoiceCloneRequest(BaseModel):
    text: str
    reference_audio: str
    output_dir: str
    output_name: str = "clone_output"
    speed: float = 1.0   # 0.5 ~ 1.5, default normal speed
    pitch: int = 0       # -10 ~ +10 semitones, default no shift
    ref_text: Optional[str] = None  # Manual transcription of reference audio (skips Whisper)

class TtsEstimateRequest(BaseModel):
    text: str
    voice: str
    rate: Optional[str] = None
    pitch: Optional[str] = None

# ─── Standard TTS (with Taiwan normalizer) ────────────────
@router.post("/tts_jobs")
async def tts_standard(req: TtsRequest, background_tasks: BackgroundTasks):
    from utils.taiwan_normalizer import normalize_for_taiwan_tts  # pyre-ignore[21]
    from core.logger import _emit_sync  # pyre-ignore[21]

    normalized_text = normalize_for_taiwan_tts(req.text)
    os.makedirs(req.output_dir, exist_ok=True)
    output_path = os.path.join(req.output_dir, req.output_name + ".mp3")

    def _on_progress(data: dict):
        _emit_sync("tts_edge_progress", data)

    async def _run_edge():
        _t0 = time.time()
        try:
            await run_edge_tts(
                normalized_text, req.voice, req.rate, req.pitch, output_path,
                progress_cb=_on_progress,
            )
            _elapsed = time.time() - _t0
            _emit_sync("tts_edge_progress", {
                "phase": "done", "pct": 100,
                "msg": "生成完成！", "output": output_path,
                "elapsed_sec": _elapsed,
            })
        except Exception as e:
            _emit_sync("tts_edge_progress", {
                "phase": "error", "pct": 0, "msg": str(e),
            })

    background_tasks.add_task(_run_edge)
    return {"status": "queued", "output": output_path}

_cached_voices = None

@router.get("/tts/voices")
async def get_edge_voices():
    global _cached_voices
    if _cached_voices is None:
        try:
            _cached_voices = await edge_tts.list_voices()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch voices: {e}")
    return {"voices": _cached_voices}

@router.get("/tts/preview")
async def preview_edge_voice(voice: str, text: str = "您好，我是這個聲音的樣式"):
    if not voice:
        raise HTTPException(status_code=400, detail="Voice parameter is required")

    async def _stream_generator():
        try:
            communicate = edge_tts.Communicate(text, voice)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    yield chunk["data"]
        except Exception as e:
            print(f"TTS Preview stream error: {e}")

    return StreamingResponse(_stream_generator(), media_type="audio/mpeg")

# ─── Estimate Duration ────────────────────────────────────
@router.post("/tts/estimate")
async def estimate_tts_duration(req: TtsEstimateRequest):
    if not req.text or not req.voice:
        raise HTTPException(status_code=400, detail="Text and voice are required")

    try:
        import sys
        import tempfile

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
            tf.write(req.text)
            text_file_path = tf.name

        req_rate = getattr(req, "rate", None)
        req_pitch = getattr(req, "pitch", None)

        cmd = [sys.executable, "-m", "edge_tts", "--file", text_file_path, "--voice", req.voice]

        if req_rate and str(req_rate) not in ["+0%", "0%", "None"]:
            cmd.append(f"--rate={req_rate}")
        if req_pitch and str(req_pitch) not in ["+0Hz", "0Hz", "None"]:
            cmd.append(f"--pitch={req_pitch}")

        devnull = "NUL" if os.name == "nt" else "/dev/null"
        cmd.extend(["--write-media", devnull])

        with tempfile.NamedTemporaryFile(suffix=".vtt", delete=False) as f:
            vtt_path = f.name

        cmd.extend(["--write-subtitles", vtt_path])

        env = os.environ.copy()
        # Remove PYTHONHASHSEED if invalid (uvicorn may set non-numeric values)
        env.pop("PYTHONHASHSEED", None)
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if result.returncode != 0:
            if os.path.exists(vtt_path): os.remove(vtt_path)
            if os.path.exists(text_file_path): os.remove(text_file_path)
            raise Exception(f"Edge-TTS failed: {result.stderr}")

        total_seconds = 0.0
        with open(vtt_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            for line in reversed(lines):
                if "-->" in line:
                    end_time_str = line.split("-->")[1].strip()
                    parts = end_time_str.split(":")
                    if len(parts) == 3:
                        h, m, s = parts
                        total_seconds = int(h) * 3600 + int(m) * 60 + float(s.replace(',', '.'))
                    break

        if os.path.exists(vtt_path): os.remove(vtt_path)
        if os.path.exists(text_file_path): os.remove(text_file_path)

        char_count = len(req.text)
        cps_val = float(char_count) / float(total_seconds) if float(total_seconds) > 0.0 else 0.0
        cps = round(cps_val, 1)

        return {
            "status": "success",
            "duration_seconds": round(float(total_seconds), 2),
            "chars_per_second": cps,
            "char_count": char_count
        }
    except Exception as e:
        print(f"Estimation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ─── Voice Clone (F5-TTS) ─────────────────────────────────
@router.post("/tts_jobs/clone")
async def tts_clone(req: VoiceCloneRequest, background_tasks: BackgroundTasks):
    if not f5_tts_is_available():
        raise HTTPException(status_code=428, detail="F5-TTS 未安裝，請執行: pip install f5-tts")

    from utils.taiwan_normalizer import normalize_for_taiwan_tts  # pyre-ignore[21]
    from core.logger import _emit_sync  # pyre-ignore[21]

    normalized_text = normalize_for_taiwan_tts(req.text)
    os.makedirs(req.output_dir, exist_ok=True)
    output_path = os.path.join(req.output_dir, req.output_name + ".wav")

    def _on_progress(data: dict):
        _emit_sync("tts_clone_progress", data)

    async def _run_clone():
        _t0 = time.time()
        try:
            await run_f5_tts_clone(
                normalized_text, req.reference_audio, output_path,
                progress_cb=_on_progress, speed=req.speed, pitch=req.pitch,
                ref_text=req.ref_text
            )
            _elapsed = time.time() - _t0
            _emit_sync("tts_clone_progress", {
                "phase": "done", "pct": 100,
                "msg": "生成完成！", "output": output_path,
                "elapsed_sec": _elapsed,
            })
        except Exception as e:
            _emit_sync("tts_clone_progress", {
                "phase": "error", "pct": 0, "msg": str(e)
            })

    background_tasks.add_task(_run_clone)
    return {"status": "queued", "output": output_path}

@router.get("/tts/f5_status")
def f5_status():
    status = f5_model_status()
    return {
        "available": f5_tts_is_available(),
        "model_ready": status["local"],
        "model_cached": status["cached"],
    }

@router.post("/tts/f5_download")
async def f5_download_model_endpoint(background_tasks: BackgroundTasks):
    from core.logger import _emit_sync  # pyre-ignore[21]

    def _on_progress(data: dict):
        _emit_sync("f5_model_download", data)

    def _run_download():
        try:
            download_f5_model(progress_cb=_on_progress)
            _emit_sync("f5_model_download", {
                "phase": "done", "pct": 100, "msg": "F5-TTS 模型下載完成！"
            })
        except Exception as e:
            _emit_sync("f5_model_download", {
                "phase": "error", "pct": 0, "msg": f"下載失敗: {e}"
            })

    background_tasks.add_task(_run_download)
    return {"status": "downloading"}

@router.post("/tts/f5_install")
async def f5_install_package(background_tasks: BackgroundTasks):
    """pip install f5-tts in background."""
    import sys, subprocess
    from core.logger import _emit_sync  # pyre-ignore[21]

    def _run_install():
        try:
            _emit_sync("f5_pip_install", {"phase": "installing", "pct": 50, "msg": "正在安裝 f5-tts 套件..."})
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "f5-tts"],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode == 0:
                _emit_sync("f5_pip_install", {"phase": "done", "pct": 100, "msg": "f5-tts 套件安裝完成！請重啟伺服器。"})
            else:
                err = result.stderr.strip().split('\n')[-1] if result.stderr else "未知錯誤"
                _emit_sync("f5_pip_install", {"phase": "error", "pct": 0, "msg": f"安裝失敗: {err}"})
        except Exception as e:
            _emit_sync("f5_pip_install", {"phase": "error", "pct": 0, "msg": f"安裝失敗: {e}"})

    background_tasks.add_task(_run_install)
    return {"status": "installing"}

# ─── Taiwan Dictionary API (Hot Reload) ──────────────────
DICT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "taiwan_dict.json")

@router.get("/tts/dictionary")
def get_dictionary():
    if not os.path.exists(DICT_PATH):
        return {"vocab_mapping": {}, "pronunciation_hacks": {}}
    try:
        with open(DICT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"讀取字典失敗: {e}")

@router.post("/tts/dictionary")
def update_dictionary(data: dict):
    try:
        # Validate structure
        if "vocab_mapping" not in data or "pronunciation_hacks" not in data:
            raise HTTPException(
                status_code=400,
                detail="JSON 必須包含 vocab_mapping 和 pronunciation_hacks 兩個物件"
            )
        with open(DICT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"status": "ok", "message": "字典已更新"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"寫入字典失敗: {e}")

# ─── Voice Library (NAS) ─────────────────────────────────
@router.get("/voice_profiles")
def list_profiles():
    nas_path = _get_nas_library_path()
    cache_path = _get_cache_path()
    profiles = _load_profiles(nas_path)
    for p in profiles:
        local_ref = os.path.join(cache_path, p["name"], "reference.wav")
        p["cached_locally"] = os.path.exists(local_ref)
    return profiles

@router.post("/voice_profiles")
def add_profile(req: NewProfileRequest):
    nas_path = _get_nas_library_path()
    profiles = _load_profiles(nas_path)

    profile_id = str(uuid.uuid4())
    dest_dir = os.path.join(nas_path, req.name)
    os.makedirs(dest_dir, exist_ok=True)
    dest_audio = os.path.join(dest_dir, "reference.wav")
    shutil.copy2(req.reference_audio, dest_audio)

    from datetime import datetime
    new_profile = {
        "id": profile_id,
        "name": req.name,
        "description": req.description,
        "reference_audio": os.path.join(req.name, "reference.wav"),
        "language": req.language,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "cached_locally": False
    }
    profiles.append(new_profile)
    _save_profiles(nas_path, profiles)
    return new_profile

@router.delete("/voice_profiles/{profile_id}")
def delete_profile(profile_id: str):
    nas_path = _get_nas_library_path()
    profiles = _load_profiles(nas_path)
    profile = next((p for p in profiles if p["id"] == profile_id), None)
    if not profile:
        raise HTTPException(status_code=404, detail="找不到聲音角色")
    folder = os.path.join(nas_path, profile["name"])
    if os.path.exists(folder):
        shutil.rmtree(folder)
    profiles = [p for p in profiles if p["id"] != profile_id]
    _save_profiles(nas_path, profiles)
    return {"status": "deleted"}

@router.post("/voice_profiles/{profile_id}/cache")
def cache_profile_locally(profile_id: str):
    nas_path = _get_nas_library_path()
    cache_path = _get_cache_path()
    profiles = _load_profiles(nas_path)
    profile = next((p for p in profiles if p["id"] == profile_id), None)
    if not profile:
        raise HTTPException(status_code=404, detail="找不到聲音角色")

    src = os.path.join(nas_path, profile["name"], "reference.wav")
    dest_dir = os.path.join(cache_path, profile["name"])
    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(src, os.path.join(dest_dir, "reference.wav"))
    return {"status": "cached", "path": dest_dir}
