"""
api_tts.py — TTS & Voice Clone router
Endpoints:
  POST /api/v1/tts_jobs             — standard edge-tts
  POST /api/v1/tts_jobs/clone       — voice cloning (XTTS v2)
  POST /api/v1/tts_jobs/profile     — clone using saved NAS profile
  GET  /api/v1/voice_profiles       — list NAS voice profiles
  POST /api/v1/voice_profiles       — add new profile to NAS
  DELETE /api/v1/voice_profiles/{id}— delete profile
  POST /api/v1/voice_profiles/{id}/cache — cache NAS profile locally
  GET  /api/v1/tts/xtts_status      — check XTTS model downloaded
  POST /api/v1/tts/xtts_download    — trigger XTTS model download
"""
import os
import uuid
import json
import shutil
import asyncio
from fastapi import APIRouter, HTTPException  # pyre-ignore[21]
from fastapi.responses import StreamingResponse  # pyre-ignore[21]
from pydantic import BaseModel  # pyre-ignore[21]
import edge_tts  # pyre-ignore[21]
from typing import Optional

from config import load_settings  # pyre-ignore[21]
from tts_engine import run_edge_tts, run_voice_clone, xtts_is_ready, download_xtts_model  # pyre-ignore[21]

router = APIRouter(prefix="/api/v1", tags=["TTS"])

# ─── Helpers ──────────────────────────────────────────────
def _get_nas_library_path() -> str:
    settings = load_settings()
    return settings.get(
        "voice_library_nas_path",
        r"\\192.168.1.132\Container\AI_Workspace\agents\Originsun Media Guard Pro\voice"
    )

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

class VoiceCloneRequest(BaseModel):
    text: str
    reference_audio: str
    output_dir: str
    output_name: str = "clone_output"

class VoiceProfileRequest(BaseModel):
    text: str
    profile_id: str
    output_dir: str
    output_name: str = "profile_output"

class NewProfileRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    reference_audio: str
    language: str = "zh"

# ─── Standard TTS ─────────────────────────────────────────
@router.post("/tts_jobs")
async def tts_standard(req: TtsRequest):
    os.makedirs(req.output_dir, exist_ok=True)
    output_path = os.path.join(req.output_dir, req.output_name + ".mp3")
    try:
        await run_edge_tts(req.text, req.voice, req.rate, req.pitch, output_path)
        return {"status": "done", "output": output_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
class TtsEstimateRequest(BaseModel):
    text: str
    voice: str
    rate: Optional[str] = None
    pitch: Optional[str] = None
import subprocess
import json

@router.post("/tts/estimate")
async def estimate_tts_duration(req: TtsEstimateRequest):
    if not req.text or not req.voice:
        raise HTTPException(status_code=400, detail="Text and voice are required")
        
    try:
        import sys
        import tempfile
        import os
        
        # Write text to a temporary UTF-8 file to prevent Windows subprocess unicode mangling
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
            tf.write(req.text)
            text_file_path = tf.name
            
        # Construct the execution arguments
        req_rate = getattr(req, "rate", None)
        req_pitch = getattr(req, "pitch", None)
        
        cmd = [sys.executable, "-m", "edge_tts", "--file", text_file_path, "--voice", req.voice]
        
        if req_rate and str(req_rate) not in ["+0%", "0%", "None"]:
            cmd.append(f"--rate={req_rate}")
        if req_pitch and str(req_pitch) not in ["+0Hz", "0Hz", "None"]:
            cmd.append(f"--pitch={req_pitch}")
            
        # We tell edge-tts to write audio to NUL (throwaway) and subtitles to stdout
        # Instead of parsing the WordBoundary chunks in python, we can just read the VTT.
        import os
        devnull = "NUL" if os.name == "nt" else "/dev/null"
        cmd.extend(["--write-media", devnull])
        # Note: edge-tts prints subtitles to stdout if --write-subtitles isn't provided but it is requested by stdout?
        # Actually edge-tts does not output VTT to stdout by default. 
        # But we can write sub to a temp file.
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".vtt", delete=False) as f:
            vtt_path = f.name
            
        cmd.extend(["--write-subtitles", vtt_path])
        
        # Execute with an inherited environment instead of empty (otherwise asyncio fails to load on Windows)
        import os
        env = os.environ.copy()
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if result.returncode != 0:
            if os.path.exists(vtt_path): os.remove(vtt_path)
            if os.path.exists(text_file_path): os.remove(text_file_path)
            raise Exception(f"Edge-TTS failed: {result.stderr}")
            
        # Parse VTT for the last timestamp
        total_seconds = 0.0
        with open(vtt_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            # VTT format: 00:00:00.012 --> 00:00:00.125
            for line in reversed(lines):
                if "-->" in line:
                    end_time_str = line.split("-->")[1].strip()
                    # Parse HH:MM:SS.mmm
                    parts = end_time_str.split(":")
                    if len(parts) == 3:
                        h, m, s = parts
                        total_seconds = int(h) * 3600 + int(m) * 60 + float(s.replace(',', '.'))
                    break
                    
        if os.path.exists(vtt_path): os.remove(vtt_path)
        if os.path.exists(text_file_path): os.remove(text_file_path)
        
        # Calculate speaking rate (characters per second)
        char_count = len(req.text)
        cps_val = float(char_count) / float(total_seconds) if float(total_seconds) > 0.0 else 0.0
        cps = round(cps_val, 1)  # pyre-ignore[6, 43]
        
        return {
            "status": "success",
            "duration_seconds": round(float(total_seconds), 2),  # pyre-ignore[6, 43]
            "chars_per_second": cps,
            "char_count": char_count
        }
    except Exception as e:
        print(f"Estimation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ─── Voice Clone (instant) ────────────────────────────────
@router.post("/tts_jobs/clone")
async def tts_clone(req: VoiceCloneRequest):
    if not xtts_is_ready():
        raise HTTPException(status_code=428, detail="XTTS v2 模型未下載，請先下載")
    os.makedirs(req.output_dir, exist_ok=True)
    output_path = os.path.join(req.output_dir, req.output_name + ".wav")
    try:
        await run_voice_clone(req.text, req.reference_audio, output_path)
        return {"status": "done", "output": output_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── Voice Profile (from library) ─────────────────────────
@router.post("/tts_jobs/profile")
async def tts_from_profile(req: VoiceProfileRequest):
    if not xtts_is_ready():
        raise HTTPException(status_code=428, detail="XTTS v2 模型未下載，請先下載")

    nas_path = _get_nas_library_path()
    profiles  = _load_profiles(nas_path)
    profile   = next((p for p in profiles if p["id"] == req.profile_id), None)
    if not profile:
        raise HTTPException(status_code=404, detail="找不到聲音角色")

    # Prefer cached local copy, fall back to NAS path
    cache_audio = os.path.join(_get_cache_path(), profile["name"], "reference.wav")
    ref_audio   = cache_audio if os.path.exists(cache_audio) else os.path.join(nas_path, profile["reference_audio"])

    os.makedirs(req.output_dir, exist_ok=True)
    output_path = os.path.join(req.output_dir, req.output_name + ".wav")
    try:
        await run_voice_clone(req.text, ref_audio, output_path, profile.get("language", "zh"))
        return {"status": "done", "output": output_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── Voice Library ─────────────────────────────────────────
@router.get("/voice_profiles")
def list_profiles():
    nas_path = _get_nas_library_path()
    cache_path = _get_cache_path()
    profiles = _load_profiles(nas_path)
    # Annotate cached_locally
    for p in profiles:
        local_ref = os.path.join(cache_path, p["name"], "reference.wav")
        p["cached_locally"] = os.path.exists(local_ref)
    return profiles

@router.post("/voice_profiles")
def add_profile(req: NewProfileRequest):
    nas_path = _get_nas_library_path()
    profiles = _load_profiles(nas_path)

    profile_id = str(uuid.uuid4())
    dest_dir   = os.path.join(nas_path, req.name)
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
    profile  = next((p for p in profiles if p["id"] == profile_id), None)
    if not profile:
        raise HTTPException(status_code=404, detail="找不到聲音角色")
    # Remove audio folder from NAS
    folder = os.path.join(nas_path, profile["name"])
    if os.path.exists(folder):
        shutil.rmtree(folder)
    profiles = [p for p in profiles if p["id"] != profile_id]
    _save_profiles(nas_path, profiles)
    return {"status": "deleted"}

@router.post("/voice_profiles/{profile_id}/cache")
def cache_profile_locally(profile_id: str):
    nas_path   = _get_nas_library_path()
    cache_path = _get_cache_path()
    profiles   = _load_profiles(nas_path)
    profile    = next((p for p in profiles if p["id"] == profile_id), None)
    if not profile:
        raise HTTPException(status_code=404, detail="找不到聲音角色")

    src  = os.path.join(nas_path, profile["name"], "reference.wav")
    dest_dir = os.path.join(cache_path, profile["name"])
    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(src, os.path.join(dest_dir, "reference.wav"))
    return {"status": "cached", "path": dest_dir}

# ─── XTTS Model Management ────────────────────────────────
@router.get("/tts/xtts_status")
def xtts_status():
    return {"ready": xtts_is_ready()}

@router.post("/tts/xtts_download")
async def xtts_download():
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, download_xtts_model)
        return {"status": "done"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
