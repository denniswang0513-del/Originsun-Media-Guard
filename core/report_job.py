import os
import asyncio
import uuid
import shutil
import json as _json
from datetime import datetime as _dt

from core.socket_mgr import sio  # type: ignore
from core.logger import _write_log_to_file  # type: ignore
import core.state as state  # type: ignore
from core.schemas import ReportJobRequest  # type: ignore
from core_engine import MediaGuardEngine, ReportManifest, FileRecord, SUPPORTED_EXTS  # type: ignore
from report_generator import save_report, generate_pdf_from_html  # type: ignore
from config import load_settings

def _fmt_size_py(size: int) -> str:
    for u in ("B","KB","MB","GB","TB"):
        if size < 1024: return f"{size:.1f} {u}"
        size //= 1024  # type: ignore
    return f"{size} TB"

async def _emit_rpt(phase: str, pct: float, msg: str, typ: str = "info"):
    _write_log_to_file(msg)
    await sio.emit("report_progress", {"phase": phase, "pct": pct, "msg": msg, "type": typ})

async def _emit_log(typ: str, msg: str):
    _write_log_to_file(msg)
    await sio.emit("report_progress", {"phase": "", "pct": 0, "msg": msg, "type": typ})

async def _check_pause():
    if not state._report_pause_event.is_set():
        await _emit_log("system", "⏸ 報表暫停中，等待繼續...")
        await state._report_pause_event.wait()
    await asyncio.sleep(0)

async def _run_report_job(req: ReportJobRequest):
    _dt_str = _dt.now().strftime("%Y%m%d_%H%M%S")
    reports_dir = os.path.join(str(req.output_dir), "Originsun_Reports")
    os.makedirs(reports_dir, exist_ok=True)

    _target_log_dir = req.output_dir if req.output_dir else req.source_dir
    state._current_task_log_file = os.path.join(_target_log_dir, f"{req.report_name or 'Report'}_Log_{_dt_str}.txt")
    _write_log_to_file(f"=== Originsun Media Guard Pro 視覺報表任務開始 ===")

    try:
        # Phase 1: Scan
        await _emit_rpt("scan", 0, "🔍 掃描影片中...")
        all_files: list = []
        _exclude_abs = [os.path.abspath(ex) for ex in (req.exclude_dirs or []) if ex]
        for root, dirs, fnames in os.walk(req.source_dir):
            if _exclude_abs:
                _valid_dirs = [
                    d for d in dirs
                    if not any(
                        os.path.abspath(os.path.join(str(root), str(d))).startswith(ex)
                        for ex in _exclude_abs
                    )
                ]
                dirs[:] = _valid_dirs  # type: ignore
            for fn in fnames:
                if fn.lower().endswith(SUPPORTED_EXTS):
                    all_files.append(os.path.join(root, fn))  # type: ignore[arg-type]
        all_files.sort()
        total = len(all_files)
        await _emit_rpt("scan", 100, f"✅ 掃描完成：找到 {total} 個影片", "ok")

        if total == 0:
            await _emit_log("error", "未找到任何支援的影片檔！")
            return

        # Phase 2: Meta / Hash
        manifest = ReportManifest(
            project_name=os.path.basename(req.source_dir),
            local_root=req.source_dir,
            nas_root=req.nas_root,
            start_time=_dt.now()
        )
        for i, fp in enumerate(all_files):
            await _check_pause()
            pct = ((i + 1) / total) * 100
            fname = os.path.basename(fp)
            await _emit_rpt("meta", pct, f"[{i+1}/{total}] 讀取 Meta: {fname}")
            rec = FileRecord(filename=fname, src_path=fp, size_bytes=os.path.getsize(fp))

            if req.do_techspec:
                meta = await asyncio.to_thread(MediaGuardEngine.get_video_metadata, fp)
                rec.fps = meta.get("fps", 0.0)
                rec.resolution = meta.get("resolution", "")
                rec.codec = meta.get("codec", "")
                rec.duration = meta.get("duration", 0.0)

            if req.do_hash:
                engine_inst = MediaGuardEngine()
                xxh = await asyncio.to_thread(engine_inst.get_xxh64, fp)  # type: ignore
                rec.xxh64 = xxh or ""
            manifest.files.append(rec)

        manifest.total_files = total
        manifest.total_bytes = sum(f.size_bytes for f in manifest.files)
        await _emit_rpt("meta", 100, "✅ Meta 讀取完成", "ok")

        # Phase 3: Film Strip
        if req.do_filmstrip:
            for i, rec in enumerate(manifest.files):
                await _check_pause()
                pct = ((i + 1) / total) * 100
                await _emit_rpt("strip", pct, f"[{i+1}/{total}] 生成膠卷: {rec.filename}")
                try:
                    rec.film_strip_b64 = await asyncio.to_thread(MediaGuardEngine.generate_film_strip, rec.src_path)
                except Exception as _se:
                    await _emit_log("info", f"膠卷略過 {rec.filename}: {_se}")
        await _emit_rpt("strip", 100, "✅ 膠卷生成完成", "ok")

        # Phase 4: Render HTML
        await _emit_rpt("render", 20, "📝 正在渲染 HTML 報表...")
        manifest.end_time = _dt.now()
        _rpt_name = req.report_name.strip() if req.report_name.strip() else _dt.now().strftime("%Y%m%d") + "_Report"
        local_html = await asyncio.to_thread(save_report, manifest, reports_dir, _rpt_name)

        await _emit_rpt("render", 50, "📄 正在轉換 PDF 文件...")
        local_pdf = local_html.replace(".html", ".pdf")
        pdf_success = await generate_pdf_from_html(local_html, local_pdf)
        if pdf_success:
            await _emit_log("system", f"📄 PDF 建立成功: {local_pdf}")
        else:
            local_pdf = ""

        # Phase 5: Publish Public URL
        await _emit_rpt("render", 60, "🌐 發佈至公開 Web 伺服器...")
        public_url = ""
        nas_web_dir = r"\\192.168.1.132\Container\AI_Workspace\Originsun_Web\FileReport"
        try:
            os.makedirs(nas_web_dir, exist_ok=True)
            nas_html = os.path.join(nas_web_dir, os.path.basename(local_html))
            shutil.copy2(local_html, nas_html)
            if local_pdf:
                nas_pdf = os.path.join(nas_web_dir, os.path.basename(local_pdf))
                shutil.copy2(local_pdf, nas_pdf)
            public_url = f"http://filereport.originsun-studio.com/{os.path.basename(local_html)}"
            await _emit_log("system", f"🌐 專案已發佈至 Web Server: {public_url}")
        except Exception as _we:
            await _emit_log("info", f"Web Server 發佈與備份失敗: {_we}")

        # Update reports_index.json
        index_path = os.path.join(reports_dir, "reports_index.json")
        report_id = str(uuid.uuid4()).replace("-", "")[0:8]  # type: ignore[misc]
        entry = {
            "id": report_id,
            "name": os.path.basename(local_html),
            "local_path": local_html,
            "pdf_path": local_pdf,
            "public_url": public_url,
            "drive_url": "",
            "created_at": _dt.now().strftime("%Y-%m-%d %H:%M"),
            "file_count": total,
            "total_size_str": _fmt_size_py(manifest.total_bytes),
        }
        nas_index_path = os.path.join(nas_web_dir, "reports_index.json")
        try:
            history: dict = {"reports": []}
            if os.path.exists(index_path):
                with open(index_path, "r", encoding="utf-8") as _f:
                    history = _json.load(_f)
            history["reports"].insert(0, entry)
            with open(index_path, "w", encoding="utf-8") as _f:
                _json.dump(history, _f, ensure_ascii=False, indent=2)

            if index_path != nas_index_path:
                nas_history: dict = {"reports": []}
                if os.path.exists(nas_index_path):
                    with open(nas_index_path, "r", encoding="utf-8") as _nf:
                        nas_history = _json.load(_nf)
                nas_history["reports"].insert(0, entry)
                with open(nas_index_path, "w", encoding="utf-8") as _nf:
                    _json.dump(nas_history, _nf, ensure_ascii=False, indent=2)
        except Exception as _ie:
            await _emit_log("info", f"索引更新失敗: {_ie}")

        # Optional Google Drive upload
        drive_url = ""
        if req.do_gdrive:
            await _emit_rpt("render", 70, "☁️ 上傳至 Google Drive...")
            try:
                _settings = load_settings()
                from drive_sync import upload_to_drive  # type: ignore
                drive_url = await asyncio.to_thread(upload_to_drive, local_html, _settings.get("gdrive_folder_id") or None)
                entry["drive_url"] = drive_url
                
                def _update_drive_url_in_index(path: str):
                    if os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as _f:
                            _hist = _json.load(_f)
                        for r in _hist.get("reports", []):
                            if r.get("id") == report_id:
                                r["drive_url"] = drive_url
                        with open(path, "w", encoding="utf-8") as _f:
                            _json.dump(_hist, _f, ensure_ascii=False, indent=2)

                _update_drive_url_in_index(index_path)
                if index_path != nas_index_path:
                    _update_drive_url_in_index(nas_index_path)
                await _emit_log("system", f"[OK] Drive: {drive_url}")
            except Exception as _de:
                await _emit_log("info", f"[!] Google Drive 略過: {_de}")

        # Notifications
        await _emit_rpt("render", 85, "📱 發送通知...")
        try:
            from notifier import notify_tab  # type: ignore
            _rpt_url = req.report_name or "report"
            if drive_url: _rpt_url = drive_url
            await asyncio.to_thread(notify_tab, "report_success",
                project_name=manifest.project_name, 
                file_count=manifest.total_files, 
                total_size=manifest.total_bytes,
                report_url=_rpt_url
            )
        except Exception as _ne:
            await _emit_log("info", f"通知發送失敗: {_ne}")

        await _emit_rpt("render", 100, "🎉 報表完成！", "ok")
        await sio.emit("report_job_done", {
            "report_name": os.path.basename(local_html),
            "local_path":  local_html,
            "pdf_path": local_pdf,
            "public_url": public_url,
            "drive_url":   drive_url,
        })

    except asyncio.CancelledError:
        await sio.emit("report_progress", {"phase": "", "pct": 0, "msg": "🛑 報表工作已被強制中止", "type": "error"})
        raise
    except Exception as _err:
        await sio.emit("report_progress", {"phase": "", "pct": 0, "msg": f"❌ 報表任務失敗: {_err}", "type": "error"})
    finally:
        state._current_report_task = None
        state._current_task_log_file = None
        await sio.emit("report_progress", {"phase": "__done__", "pct": 0, "msg": "", "type": "system"})
