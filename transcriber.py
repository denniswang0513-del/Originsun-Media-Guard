import os
import time
import shutil
import subprocess
import tempfile
import json
from typing import List, Callable, Optional


def _detect_device():
    """安全偵測 CUDA，torch 不存在時自動用 CPU"""
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            return "cuda", "float16"
    except ImportError:
        pass
    return "cpu", "int8"


def _cleanup_gpu():
    """安全釋放 GPU 記憶體，torch 不存在時跳過"""
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _ensure_dependencies():
    """啟動時檢查 faster-whisper 和 torch，缺少則自動安裝"""
    import sys

    # 1. 確保 torch 存在（faster-whisper 內部需要）
    try:
        import torch  # type: ignore
    except ImportError:
        print("[Transcriber] 正在安裝必要套件 torch (CPU-only) ...")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "torch", "--index-url", "https://download.pytorch.org/whl/cpu",
            "--quiet", "--no-warn-script-location"
        ])
        print("[Transcriber] torch 安裝完成。")

    # 2. 確保 faster-whisper 存在
    try:
        import faster_whisper  # type: ignore
    except ImportError:
        print("[Transcriber] 正在安裝必要套件 faster-whisper ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "faster-whisper"])
        import faster_whisper  # type: ignore


def _get_video_info(file_path: str) -> dict:
    """使用 ffprobe 取得影片時長與是否包含影像軌"""
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", file_path
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        data = json.loads(res.stdout)
        
        has_video = any(s.get("codec_type") == "video" for s in data.get("streams", []))
        duration = float(data.get("format", {}).get("duration", 0))
        
        return {"has_video": has_video, "duration": duration}
    except Exception:
        return {"has_video": False, "duration": 0}


def _format_timestamp(seconds: float) -> str:
    """將秒數轉為 SRT 時間碼格式 HH:MM:SS,mmm"""
    ms = int((seconds % 1) * 1000)
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _run_individual_mode(
    sources: List[str],
    dest_dir: str,
    timestamp: str,
    model_size: str,
    output_srt: bool,
    output_txt: bool,
    output_wav: bool,
    generate_proxy: bool,
    _report_progress: Callable,
    _is_canceled: Callable,
    _run_ff_cancelable: Callable
) -> dict:
    """獨立模式：每個來源檔案各自輸出同名的 .srt / .txt"""
    from faster_whisper import WhisperModel  # type: ignore

    valid_exts = {'.mp4', '.mov', '.mp3', '.wav', '.m4a', '.flac', '.mkv', '.webm', '.ts', '.avi'}

    # 展開資料夾
    expanded = []
    for src in sources:
        if os.path.exists(src):
            if os.path.isdir(src):
                for root, _, files in os.walk(src):
                    for f in files:
                        if os.path.splitext(f)[1].lower() in valid_exts:
                            expanded.append(os.path.join(root, f))
            else:
                expanded.append(src)

    total = len(expanded)
    if total == 0:
        return {"success": False, "error": "未能從來源目錄或檔案中找到支援的影音檔。"}

    # 載入模型（只載一次）
    _report_progress(5.0, f"啟動AI辨識引擎 (Model: {model_size})... 初次載入可能較久")
    device, compute_type = _detect_device()
    models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    os.makedirs(models_dir, exist_ok=True)

    model = None
    output_files = []
    result = {"success": False, "error": "Unknown error"}

    try:
        model = WhisperModel(model_size, device=device, compute_type=compute_type, download_root=models_dir)

        for idx, src in enumerate(expanded):
            if _is_canceled():
                raise Exception("使用者強制中止任務")

            base_name = os.path.splitext(os.path.basename(src))[0]
            file_pct_start = (idx / total) * 100
            file_pct_end = ((idx + 1) / total) * 100

            _report_progress(file_pct_start + 2, f"[{idx+1}/{total}] 提取音訊: {os.path.basename(src)}")

            # 產生 Proxy 若勾選
            if generate_proxy:
                info = _get_video_info(src)
                if info.get("has_video", False):
                    proxy_out = os.path.join(dest_dir, f"{timestamp}_{base_name}_Proxy.mp4")
                    _report_progress(file_pct_start + 4, f"[{idx+1}/{total}] 產生影像串帶: {base_name}")
                    video_cmd = [
                        "ffmpeg", "-y", "-i", src,
                        "-vf", "scale=-2:720,fps=30",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                        proxy_out
                    ]
                    _run_ff_cancelable(video_cmd)
                    if os.path.exists(proxy_out):
                        output_files.append(proxy_out)

            # 提取 WAV 到暫存
            temp_wav = os.path.join(dest_dir, f"_temp_{base_name}.wav")
            audio_cmd = [
                "ffmpeg", "-y", "-i", src,
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                temp_wav
            ]
            _run_ff_cancelable(audio_cmd)

            if not os.path.exists(temp_wav):
                _report_progress(file_pct_end, f"[{idx+1}/{total}] ⚠️ 無法提取音訊: {os.path.basename(src)}")
                continue

            # 辨識
            _report_progress(file_pct_start + (file_pct_end - file_pct_start) * 0.3, f"[{idx+1}/{total}] AI辨識中: {base_name}")
            segments_gen, info = model.transcribe(temp_wav, beam_size=5)

            srt_lines = []
            txt_lines = []
            seg_count = 0
            total_dur = info.duration

            for segment in segments_gen:
                if _is_canceled():
                    raise Exception("使用者強制中止任務")
                seg_count += 1
                start = _format_timestamp(segment.start)
                end = _format_timestamp(segment.end)
                text = segment.text.strip()
                srt_lines.append(f"{seg_count}\n{start} --> {end}\n{text}\n\n")
                txt_lines.append(f"[{start}] {text}\n")

                seg_pct = file_pct_start + ((segment.end / total_dur) * (file_pct_end - file_pct_start)) if total_dur > 0 else file_pct_start
                if seg_count % 10 == 0:
                    _report_progress(min(seg_pct, file_pct_end - 1), f"[{idx+1}/{total}] 辨識中 {base_name}... {start}")

            # 輸出
            if output_srt:
                srt_path = os.path.join(dest_dir, f"{timestamp}_{base_name}.srt")
                with open(srt_path, "w", encoding="utf-8-sig") as f:
                    f.writelines(srt_lines)
                output_files.append(srt_path)

            if output_txt:
                txt_path = os.path.join(dest_dir, f"{timestamp}_{base_name}.txt")
                with open(txt_path, "w", encoding="utf-8-sig") as f:
                    f.writelines(txt_lines)
                output_files.append(txt_path)

            # 清理暫存 WAV
                if output_wav:
                    final_wav = os.path.join(dest_dir, f"{timestamp}_{base_name}.wav")
                    # 只重命名，成功則不需刪除
                    try:
                        shutil.move(temp_wav, final_wav)
                        output_files.append(final_wav)
                    except Exception:
                        pass
                else:
                    try:
                        os.remove(temp_wav)
                    except Exception:
                        pass

            _report_progress(file_pct_end, f"[{idx+1}/{total}] ✅ 完成: {base_name}")

        _report_progress(100.0, f"全部 {total} 個檔案辨識完成！")
        result = {
            "success": True,
            "dest_dir": dest_dir,
            "file_count": total,
            "output_files": output_files
        }

    except Exception as e:
        result = {"success": False, "error": str(e)}

    finally:
        if model is not None:
            del model
        try:
            _cleanup_gpu()
        except Exception:
            pass
            
    return result


def run_transcribe_job(
    sources: List[str],
    dest_dir: str,
    project_name: str,
    model_size: str = "turbo",
    output_srt: bool = True,
    output_txt: bool = True,
    output_wav: bool = False,
    generate_proxy: bool = False,
    individual_mode: bool = False,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    check_cancel_cb: Optional[Callable[[], bool]] = None
):
    """
    執行完整的 AI 逐字稿流程:
    1. 提取所有來源的 WAV 音檔
    2. 結合所有 WAV 音檔 (並生成影像串帶 若適用)
    3. 音訊轉文字 (faster-whisper)
    4. 輸出 SRT / TXT

    individual_mode=True 時，每個來源檔案各自獨立輸出。
    """
    _ensure_dependencies()
    from faster_whisper import WhisperModel  # type: ignore

    def _report_progress(pct: float, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    def _is_canceled() -> bool:
        return check_cancel_cb() if check_cancel_cb else False
            
    def _run_ff_cancelable(cmd: List[str], cwd: Optional[str] = None):
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=cwd)
        while True:
            if p.poll() is not None:
                break
            if _is_canceled():
                p.terminate()
                p.wait()
                raise Exception("使用者強制中止任務")
            time.sleep(0.5)
        return p.returncode

    os.makedirs(dest_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    # ── 如果是獨立模式，走獨立路徑 ──
    if individual_mode:
        return _run_individual_mode(
            sources, dest_dir, timestamp, model_size,
            output_srt, output_txt, output_wav, generate_proxy,
            _report_progress, _is_canceled, _run_ff_cancelable
        )

    # ── 以下為合併模式（原始邏輯）──
    temp_dir = tempfile.mkdtemp(prefix="origin_transcribe_")
    combined_wav = os.path.join(dest_dir, f"{timestamp}_{project_name}_Combined.wav")
    combined_proxy = os.path.join(dest_dir, f"{timestamp}_{project_name}_Proxy.mp4")
    srt_out = os.path.join(dest_dir, f"{timestamp}_{project_name}.srt")
    txt_out = os.path.join(dest_dir, f"{timestamp}_{project_name}.txt")

    model = None

    try:
        # ── 階段 0：展開資料夾來源 ──
        expanded_sources = []
        valid_exts = {'.mp4', '.mov', '.mp3', '.wav', '.m4a', '.flac', '.mkv', '.webm', '.ts', '.avi'}
        for src in sources:
            if os.path.exists(src):
                if os.path.isdir(src):
                    for root, _, files in os.walk(src):
                        for f in files:
                            if os.path.splitext(f)[1].lower() in valid_exts:
                                expanded_sources.append(os.path.join(root, f))
                else:
                    expanded_sources.append(src)

        # ── 階段 1：提取並處理所有輸入檔案 ──
        total_files = len(expanded_sources)
        if total_files == 0:
            raise Exception("未能從來源目錄或檔案中找到支援的影音檔。")

        temp_wav_list = []
        temp_ts_list = []  # 用於影像 Proxy
        has_any_video = False
        
        for idx, src in enumerate(expanded_sources):
            if not os.path.exists(src):
                _report_progress(((idx) / total_files) * 30, f"跳過不存在的檔案: {os.path.basename(src)}")
                continue

            info = _get_video_info(src)
            name_ext = os.path.basename(src)
            _report_progress(((idx) / total_files) * 30, f"處理輸入檔 ({idx+1}/{total_files}): {name_ext}")

            # 1. 提取為 16kHz 單聲道 WAV
            temp_wav = os.path.join(temp_dir, f"audio_{idx}.wav")
            audio_cmd = [
                "ffmpeg", "-y", "-i", src,
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                temp_wav
            ]
            _run_ff_cancelable(audio_cmd)
            if os.path.exists(temp_wav):
                temp_wav_list.append(temp_wav)

            # 2. 如果需要影像串帶且有影像軌，則轉為 720p 30fps 的 ts
            if generate_proxy and info["has_video"]:
                has_any_video = True
                temp_ts = os.path.join(temp_dir, f"video_{idx}.ts")
                video_cmd = [
                    "ffmpeg", "-y", "-i", src,
                    "-vf", "scale=-2:720,fps=30",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                    "-f", "mpegts", temp_ts
                ]
                _run_ff_cancelable(video_cmd)
                if os.path.exists(temp_ts):
                    temp_ts_list.append(temp_ts)
            elif generate_proxy and temp_wav_list:
                # 若無影像(純音樂)但要求 proxy，造黑畫面 ts，使用 -shortest 自動與音檔對齊長度
                has_any_video = True
                temp_ts = os.path.join(temp_dir, f"video_{idx}.ts")
                video_cmd = [
                    "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=30",
                    "-i", temp_wav,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                    "-shortest", "-f", "mpegts", temp_ts
                ]
                _run_ff_cancelable(video_cmd)
                if os.path.exists(temp_ts):
                    temp_ts_list.append(temp_ts)


        # ── 階段 2：合併音檔 (與影像) ──
        if not temp_wav_list:
            raise Exception("未能從任何來源檔案中提取出有效的音訊。")

        _report_progress(35.0, f"正在合併 {len(temp_wav_list)} 段音訊...")
            
        # 合併 WAV
        # 使用 os.path.basename 確保相對路徑，避免 Windows 帳戶名稱中有單引號造成的 FFmpeg 語法錯誤
        wav_concat_file = os.path.join(temp_dir, "wav_list.txt")
        with open(wav_concat_file, "w", encoding="utf-8") as f:
            for w in temp_wav_list:
                f.write(f"file '{os.path.basename(w)}'\n")
                
        concat_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", wav_concat_file, "-c", "copy", combined_wav
        ]
        # Popen 在 cwd 中執行，而 ffmpeg 遇到 concat 會相對於清單檔案目錄解析，因此需指定 cwd 為 temp_dir
        _run_ff_cancelable(concat_cmd, cwd=temp_dir)

        # 合併 TS -> MP4 Proxy
        if generate_proxy and has_any_video and temp_ts_list:
            _report_progress(40.0, "正在生成影像參考串帶 (Proxy)...")
                
            ts_concat_file = os.path.join(temp_dir, "ts_list.txt")
            with open(ts_concat_file, "w", encoding="utf-8") as f:
                for t in temp_ts_list:
                    f.write(f"file '{os.path.basename(t)}'\n")
                    
            concat_proxy_cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", ts_concat_file, "-c", "copy", combined_proxy
            ]
            _run_ff_cancelable(concat_proxy_cmd, cwd=temp_dir)

        
        # ── 階段 3：AI 語音辨識 ──
        _report_progress(45.0, f"啟動AI辨識引擎 (Model: {model_size})... 初次載入可能較久")

        device, compute_type = _detect_device()
        
        models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
        os.makedirs(models_dir, exist_ok=True)
        
        model = WhisperModel(model_size, device=device, compute_type=compute_type, download_root=models_dir)

        _report_progress(50.0, f"開始分析總音檔: {os.path.basename(combined_wav)}")

        segments_generator, info = model.transcribe(combined_wav, beam_size=5)
        
        # ── 階段 4：輸出檔案 ──
        srt_lines = []
        txt_lines = []
        seg_count = 0
        total_dur = info.duration

        for segment in segments_generator:
            if _is_canceled():
                raise Exception("使用者強制中止任務")
                
            seg_count += 1
            start = _format_timestamp(segment.start)
            end = _format_timestamp(segment.end)
            text = segment.text.strip()
            
            srt_lines.append(f"{seg_count}\n{start} --> {end}\n{text}\n\n")
            txt_lines.append(f"[{start}] {text}\n")
            
            # Progress calculation (50% -> 99%)
            pct = 50.0 + ((segment.end / total_dur) * 49.0) if total_dur > 0 else 75.0
            if seg_count % 10 == 0:
                _report_progress(min(pct, 99.0), f"辨識中... 處理至 {start}")

        if output_srt:
            with open(srt_out, "w", encoding="utf-8-sig") as f:
                f.writelines(srt_lines)
                
        if output_txt:
            with open(txt_out, "w", encoding="utf-8-sig") as f:
                f.writelines(txt_lines)

        _report_progress(100.0, "清理暫存檔案...")

        return {
            "success": True,
            "dest_dir": dest_dir,
            "file_count": len(sources),
            "output_srt": srt_out if output_srt else None,
            "output_txt": txt_out if output_txt else None,
            "combined_audio": combined_wav if output_wav and os.path.exists(combined_wav) else None,
            "proxy_video": combined_proxy if (generate_proxy and has_any_video and os.path.exists(combined_proxy)) else None
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
        
    finally:
        # ── 階段 5：清理暫存目錄與釋放 AI 記憶體 ──
        if model is not None:
            del model
            
        try:
            _cleanup_gpu()
        except Exception:
            pass
            
        # 強制執行垃圾回收，釋放可能殘留的 Windows 檔案控制代碼 (File Handles)
        import gc
        gc.collect()
        time.sleep(1)
        
        # 使用重試機制刪除，避免 Windows 檔案鎖死導致卡住
        for attempt in range(3):
            try:
                if not output_wav and os.path.exists(combined_wav):
                    os.remove(combined_wav)
                break
            except Exception:
                time.sleep(1)

        for attempt in range(3):
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
                break
            except Exception:
                time.sleep(1)
