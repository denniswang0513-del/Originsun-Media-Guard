from fastapi import APIRouter  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore
import os
import shutil
from core.schemas import (  # type: ignore
    TranscodeRequest, MergeHostOutputsRequest, VerifyProxiesRequest,
    VerifyStandaloneProxiesRequest, CompareSourceRequest, MergeOutputRequest
)
from core.worker import enqueue_job  # type: ignore

router = APIRouter()

@router.post("/api/v1/jobs/transcode")
async def create_transcode_job(req: TranscodeRequest):
    project_name = req.project_name or os.path.basename(req.dest_dir) or "unnamed"
    job_id, warning = enqueue_job(req, project_name, "transcode")
    resp = {"status": "queued", "job_id": job_id}
    if warning:
        resp["warning"] = warning
    return resp

@router.post("/api/v1/merge_host_outputs")
async def merge_host_outputs(req: MergeHostOutputsRequest):
    base = os.path.join(req.proxy_root, req.project_name)
    if not os.path.isdir(base):
        return {"status": "error", "message": f"目錄不存在: {base}"}

    merged = 0
    errors = []
    for subdir in os.listdir(base):
        if not subdir.startswith("HostDispatch_"):
            continue
        src_dir = os.path.join(base, subdir)
        if not os.path.isdir(src_dir):
            continue
            
        for root, dirs, files in os.walk(src_dir):
            for fname in files:
                src_file = os.path.join(root, fname)
                rel_path = os.path.relpath(src_file, src_dir)
                dst_file = os.path.join(base, rel_path)
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                
                try:
                    if os.path.exists(dst_file):
                        dst_dir = os.path.dirname(dst_file)
                        dst_file = os.path.join(dst_dir, f"_dup_{subdir}_{fname}")
                    shutil.move(src_file, dst_file)
                    merged += 1  # type: ignore
                except Exception as e:
                    errors.append(f"{rel_path}: {e}")
                    
        try:
            shutil.rmtree(src_dir)
        except Exception:
            pass

    return {"status": "ok", "merged": merged, "errors": errors}

@router.post("/api/v1/verify_proxies")
async def verify_proxies(req: VerifyProxiesRequest):
    missing = []
    base_dir = os.path.join(req.proxy_root, req.project_name)
    
    if not os.path.isdir(base_dir):
        for card_name, files in req.expected_files.items():
            for f in files: missing.append({"card_name": card_name, "file": f})
        return {"status": "ok", "missing_files": missing}
        
    for card_name, files in req.expected_files.items():
        card_dir = os.path.join(base_dir, card_name)
        for f in files:
            expected_path = os.path.join(card_dir, f)
            if not os.path.exists(expected_path):
                missing.append({"card_name": card_name, "file": f})
                
    return {"status": "ok", "missing_files": missing}

@router.post("/api/v1/verify_standalone_proxies")
async def verify_standalone_proxies(req: VerifyStandaloneProxiesRequest):
    missing_sources = []
    video_exts = {".mov", ".mp4", ".mkv", ".mxf", ".avi", ".mts", ".m2ts", ".r3d", ".braw"}
    proxy_exts = {".mov", ".mp4"}

    source_stems = {}
    for token in req.sources:
        if not token: continue
        if os.path.isfile(token) and str(token).lower().endswith(tuple(video_exts)):  # type: ignore
            stem = os.path.splitext(os.path.basename(str(token)))[0].lower()
            if stem not in source_stems:
                source_stems[stem] = token
        elif os.path.isdir(token):
            for root, _, fnames in os.walk(str(token)):  # type: ignore
                for fname in fnames:
                    if fname.lower().endswith(tuple(video_exts)):  # type: ignore
                        stem = os.path.splitext(fname)[0].lower()
                        if stem not in source_stems:
                            source_stems[stem] = os.path.join(root, fname).replace("\\", "/")  # type: ignore

    proxy_stems = set()
    if os.path.isdir(req.dest_dir):
        for root, _, fnames in os.walk(req.dest_dir):
            for fname in fnames:
                if os.path.splitext(fname)[1].lower() in proxy_exts:
                    stem = os.path.splitext(fname)[0].lower()
                    if stem.endswith("_proxy"): stem = stem[:-6]
                    proxy_stems.add(stem)

    for stem, original_path in source_stems.items():
        if stem not in proxy_stems:
            missing_sources.append(original_path)

    return {"status": "ok", "missing_sources": missing_sources}

@router.post("/api/v1/compare_source")
async def compare_source(req: CompareSourceRequest):
    if not os.path.isdir(req.source_dir):
        return {"status": "error", "message": f"來源目錄不存在: {req.source_dir}"}

    src_exts = {e.lower() for e in req.video_exts}
    proxy_exts = {e.lower() for e in req.proxy_exts}

    source_stems = {}
    for root, _, fnames in os.walk(req.source_dir):
        for f in fnames:
            if os.path.splitext(f)[1].lower() in src_exts:
                stem = os.path.splitext(f)[0].lower()
                if req.flat_proxy: key = stem
                else:
                    rel_dir = os.path.relpath(root, req.source_dir)
                    if rel_dir == ".": rel_dir = ""
                    key = os.path.join(rel_dir, stem).replace("\\", "/").lower()
                if key not in source_stems:
                    source_stems[key] = os.path.join(root, f).replace("\\", "/")

    proxy_stems = set()
    if os.path.isdir(req.output_dir):
        for root, _, fnames in os.walk(req.output_dir):
            for f in fnames:
                if os.path.splitext(f)[1].lower() in proxy_exts:
                    stem = os.path.splitext(f)[0].lower()
                    if stem.endswith("_proxy"): stem = str(stem)[:-6]  # type: ignore
                    if req.flat_proxy: key = stem
                    else:
                        rel_dir = os.path.relpath(root, req.output_dir)
                        if rel_dir == ".": rel_dir = ""
                        key = os.path.join(rel_dir, stem).replace("\\", "/").lower()
                    proxy_stems.add(key)

    missing = [path for key, path in source_stems.items() if key not in proxy_stems]

    return {
        "status": "ok",
        "source_count": len(source_stems),
        "proxy_count": len(proxy_stems),
        "missing_count": len(missing),
        "missing": missing,
    }
