import os
import time
import shutil
import xxhash  # type: ignore
import subprocess
import threading
import base64
import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Any, Dict, List, Tuple, Optional

from utils.formatting import fmt_size

# ─────────────────────────────────────────────────────────
# Report Data Structures
# ─────────────────────────────────────────────────────────

@dataclass
class FileRecord:
    """Per-file metadata collected during a backup job, used in the HTML report."""
    filename: str = ""          # Original filename
    src_path: str = ""          # Full source path
    size_bytes: int = 0         # File size in bytes
    xxh64: str = ""             # XXH64 checksum (empty if do_hash=False)
    fps: float = 0.0            # Detected frame rate
    resolution: str = ""        # e.g. "3840x2160"
    codec: str = ""             # e.g. "h264", "prores"
    duration: float = 0.0       # Clip duration in seconds
    film_strip_b64: str = ""    # Base64-encoded JPG film strip (empty if do_report=False)

@dataclass
class ReportManifest:
    """Project-level metadata for a backup job — the single source of truth for report generation."""
    project_name: str = ""
    local_root: str = ""
    nas_root: str = ""
    proxy_root: str = ""
    total_files: int = 0
    total_bytes: int = 0
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    files: List[FileRecord] = field(default_factory=list)

SUPPORTED_EXTS = ('.mov', '.mp4', '.mkv', '.mxf', '.r3d', '.ari', '.braw', '.dng', '.avi', '.mts', '.m2ts')

def _short_hash(h: Optional[str]) -> str:
    if not h: return "None"
    out = ""
    for i, char in enumerate(str(h)):
        if i >= 8: break
        out += char
    return out


class MediaGuardEngine:
    def __init__(self, logger_cb: Optional[Callable[[str], None]] = None, error_cb: Optional[Callable[[str], None]] = None):
        self._log_cb = logger_cb if logger_cb else print
        self._err_cb = error_cb if error_cb else print
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        
        self._log_buffer: List[str] = []
        self._err_buffer: List[str] = []
        
    def log(self, msg: str):
        buf_msg = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        self._log_buffer.append(buf_msg)
        self._log_cb(msg)
        
    def err(self, msg: str):
        buf_msg = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        self._err_buffer.append(buf_msg)
        self._log_buffer.append(f"[{datetime.now().strftime('%H:%M:%S')}] [ERROR] {msg}")
        self._err_cb(msg)

    def clear_logs(self):
        self._log_buffer.clear()
        self._err_buffer.clear()

    def save_job_log(self, local_root: str, task_name: str = "工作日誌"):
        try:
            os.makedirs(local_root, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            log_file = os.path.join(local_root, f"MediaGuard_Log_{timestamp}.txt")
            
            with open(log_file, "w", encoding="utf-8-sig") as f:
                f.write(f"=== Originsun Media Guard Pro {task_name} ===\n")
                f.write(f"完成時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write("--- 執行日誌 ---\n")
                f.write("\n".join(self._log_buffer) + "\n\n")
                
                if self._err_buffer:
                    f.write("--- 錯誤紀錄 ---\n")
                    f.write("\n".join(self._err_buffer) + "\n")
                    
            self.log(f"[OK] 日誌已自動儲存至: {log_file}")
        except Exception as e:
            self.err(f"[X] 無法儲存工作日誌: {str(e)}")

    def request_stop(self):
        self._stop_event.set()
        self._pause_event.clear()

    def request_pause(self):
        self._pause_event.set()

    def request_resume(self):
        self._pause_event.clear()

    def _check_pause_stop(self) -> bool:
        while self._pause_event.is_set():
            time.sleep(0.5)
            if self._stop_event.is_set():
                return True
        return self._stop_event.is_set()

    @staticmethod
    def get_xxh64(filepath: str) -> str:
        hash_obj = xxhash.xxh64()
        with open(filepath, 'rb') as f:
            while chunk := f.read(1024 * 1024):
                hash_obj.update(chunk)
        return hash_obj.hexdigest()

    @staticmethod
    def is_file_stable(filepath: str) -> bool:
        try:
            s1 = os.path.getsize(filepath)
            if s1 == 0:
                return False
            if time.time() - os.path.getmtime(filepath) > 10:
                fd = os.open(filepath, os.O_RDONLY)
                os.close(fd)
                return True
            time.sleep(5)
            s2 = os.path.getsize(filepath)
            if s1 != s2:
                return False
            fd = os.open(filepath, os.O_RDONLY)
            os.close(fd)
            return True
        except Exception:
            return False

    def copy_file_chunked(self, src: str, dst: str, progress_cb: Optional[Callable[[int, int, float], None]] = None) -> bool:
        CHUNK = 4 * 1024 * 1024
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        file_size = os.path.getsize(src)
        copied: int = 0
        t_start = time.time()
        
        stopped = False
        with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
            while True:
                if self._check_pause_stop():
                    stopped = True
                    break
                chunk = fsrc.read(CHUNK)
                if not chunk:
                    break
                fdst.write(chunk)
                copied += len(chunk)  # type: ignore
                
                if progress_cb is not None:
                    elapsed = time.time() - t_start
                    speed = copied / elapsed if elapsed > 0 else 0
                    import typing
                    cb: typing.Any = progress_cb
                    if cb:
                        cb(int(copied), int(file_size), float(speed))
                        
        if stopped:
            try:
                if os.path.exists(dst):
                    os.remove(dst)
            except Exception as e:
                self.err(f"無法清除未完成檔案 {dst}: {e}")
            return False

        return True

    @staticmethod
    def _create_progress_callback(
        phase: str,
        total_bytes: int,
        total_files: int,
        card_name: str,
        rel_path: str,
        t_last_chunk: List[float],
        speed_samples: Any,
        done_state: List[int], # [done_bytes, done_files]
        on_progress
    ):
        def _prog(copied: int, fsize: int, speed: float):
            if on_progress is not None:
                now = time.time()
                interval = now - t_last_chunk[0]
                if interval > 0:
                    speed_samples.append(speed)
                t_last_chunk[0] = now
                
                avg_speed = sum(speed_samples) / len(speed_samples) if speed_samples else speed
                curr_total_done: int = done_state[0] + int(copied)
                remaining_bytes = total_bytes - curr_total_done
                eta_sec = (remaining_bytes / avg_speed) if avg_speed > 0 else None
                on_progress({
                    "phase": phase,
                    "status": "copying",
                    "current_file": f"{card_name}/{os.path.basename(rel_path)}",
                    "file_pct": (copied / fsize) * 100 if fsize else 100,
                    "total_pct": (curr_total_done / total_bytes) * 100 if total_bytes else 100,
                    "speed_mbps": avg_speed / (1024 * 1024),
                    "eta_sec": eta_sec,
                    "done_files": done_state[1],
                    "total_files": total_files
                })
        return _prog

    def _check_disk_space(self, path: str, required_bytes: int, buffer_pct: float = 0.05) -> Tuple[bool, int]:
        """檢查目的地磁碟可用空間是否足夠。回傳 (足夠, 可用位元組)。"""
        try:
            check_path = path
            while not os.path.exists(check_path):
                parent = os.path.dirname(check_path)
                if parent == check_path:
                    self.log("[Engine] 磁碟空間預檢：無法定位磁碟根目錄，跳過檢查")
                    return True, -1
                check_path = parent
            free = shutil.disk_usage(check_path).free
            needed = int(required_bytes * (1 + buffer_pct))
            return free >= needed, free
        except Exception as exc:
            self.log(f"[Engine] 磁碟空間預檢失敗（{exc}），跳過檢查")
            return True, -1

    # ── Checkpoint helpers for resume-from-interruption ──

    _CHECKPOINT_FILE = ".originsun_checkpoint.json"

    @staticmethod
    def _load_checkpoint(local_dir: str) -> Dict[str, Any]:
        """讀取中斷點檔案，回傳 completed dict。找不到或損壞時回傳空 dict。"""
        cp_path = os.path.join(local_dir, MediaGuardEngine._CHECKPOINT_FILE)
        if not os.path.exists(cp_path):
            return {}
        try:
            with open(cp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("completed", {})
        except Exception:
            return {}

    @staticmethod
    def _save_checkpoint(local_dir: str, project_name: str, completed: Dict[str, Any]) -> None:
        """將 completed dict 寫入中斷點檔案（原子寫入）。"""
        cp_path = os.path.join(local_dir, MediaGuardEngine._CHECKPOINT_FILE)
        tmp_path = cp_path + ".tmp"
        data = {
            "project_name": project_name,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "completed": completed,
        }
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp_path, cp_path)
        except Exception:
            # 寫入失敗不影響備份本身
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    @staticmethod
    def _remove_checkpoint(local_dir: str) -> None:
        """備份完整完成後刪除中斷點檔案。"""
        cp_path = os.path.join(local_dir, MediaGuardEngine._CHECKPOINT_FILE)
        try:
            if os.path.exists(cp_path):
                os.remove(cp_path)
        except OSError:
            pass

    @staticmethod
    def _is_checkpoint_done(completed: Dict[str, Any], key: str, phase: str, dest_path: str, src_size: int) -> bool:
        """檢查某檔案在指定階段是否已由 checkpoint 標記完成且目的地檔案仍完好。"""
        entry = completed.get(key)
        if not entry or not entry.get(phase):
            return False
        # 雙重驗證：checkpoint 記錄存在 + 目的地檔案大小一致
        try:
            return os.stat(dest_path).st_size == src_size
        except OSError:
            return False

    def run_backup_job(
        self,
        sources: List[Tuple[str, str]], # (CardName, RootPath)
        local_root: str,
        nas_root: str,
        project_name: str,
        do_hash: bool = True,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_conflict: Optional[Callable[[Dict[str, Any]], str]] = None,
        manifest=None,      # ReportManifest | None — populated during backup for optional post-job report
        do_report: bool = False
    ):
        """核心無頭備份邏輯 (Generator/Callback base)"""
        self._stop_event.clear()
        self._pause_event.clear()
        
        local_dir = os.path.join(local_root, project_name)
        nas_dir = os.path.join(nas_root, project_name) if nas_root else ""
        
        all_items: List[Tuple[str, str, str]] = []
        # 收集需要在目的地建立的空資料夾（來源有但沒有任何檔案的目錄）
        empty_dirs: List[Tuple[str, str]] = []   # (card, rel_dir)
        for card, src_root in sources:
            for dirpath, dirnames, filenames in os.walk(src_root):
                if not filenames and not dirnames:
                    # 真正的空資料夾（無檔案、無子目錄）
                    rel_dir = os.path.relpath(dirpath, src_root).lstrip("\\/")
                    empty_dirs.append((card, rel_dir))
                for fname in filenames:
                    src_abs = os.path.join(dirpath, fname)
                    rel = os.path.relpath(src_abs, src_root).lstrip("\\/")
                    all_items.append((card, rel, src_abs))
                    
        total_files = len(all_items)
        if total_files == 0:
            self.log("[Engine] 沒有找到任何檔案可以備份。")
            return
            
        total_bytes = sum(os.path.getsize(abs_p) for _, _, abs_p in all_items if os.path.exists(abs_p))
        done_state_l = [0, 0] # [done_bytes_l, done_files_l]
        # 5-sample rolling speed deque — same logic as the desktop app's _smooth_eta
        from collections import deque as _deque
        speed_samples = _deque(maxlen=5)
        t_last_chunk = [time.time()]  # mutable so inner func can update it
        
        self.log(f"[Engine] 準備備份 {total_files} 個檔案，總計 {fmt_size(total_bytes)}")

        # ── 磁碟空間預檢 ──
        for label, target in [("本機", local_dir), ("NAS", nas_dir)]:
            if not target:
                continue
            ok, free = self._check_disk_space(target, total_bytes)
            if not ok:
                self.err(f"⚠ {label}空間不足：需要 {fmt_size(total_bytes)}，"
                         f"可用 {fmt_size(free)}（含 5% 安全餘量）")
                return

        # ── 中斷點續傳：載入 checkpoint ──
        cp_completed = self._load_checkpoint(local_dir)
        if cp_completed:
            resumed = sum(1 for v in cp_completed.values()
                          if v.get("local") and (not nas_dir or v.get("nas")))
            self.log(f"[Engine] 偵測到中斷點，{resumed}/{total_files} 個檔案可跳過")

        # ── 預先建立空資料夾（攝影機卡片常見的 metadata 目錄） ──
        if empty_dirs:
            self.log(f"[Engine] 同步 {len(empty_dirs)} 個空資料夾結構...")
            for card, rel_dir in empty_dirs:
                l_dir = os.path.join(local_dir, card, rel_dir)
                os.makedirs(l_dir, exist_ok=True)
                if nas_dir:
                    n_dir = os.path.join(nas_dir, card, rel_dir)
                    try:
                        os.makedirs(n_dir, exist_ok=True)
                    except OSError as e:
                        self.err(f"建立 NAS 空資料夾失敗: {rel_dir} - {e}")

        # ── 檢查重複的交互機制 ──
        def check_duplicate(engine: "MediaGuardEngine", src: str, dest: str, target_type: str, f_size: int, rel_path: str) -> Tuple[str, str]:
            if not os.path.exists(dest): return "copy", dest
            try:
                d_size = os.path.getsize(dest)
                if f_size != d_size:
                    sign = "+" if d_size > f_size else ""
                    reason = (f"大小差異（疑似損壞的舊備份）\n"
                              f"  來源：{f_size:,} bytes\n"
                              f"  目標：{d_size:,} bytes  ({sign}{d_size - f_size:,})")
                    conflict_type = "size_mismatch"
                else:
                    s_time = os.path.getmtime(src)
                    d_time = os.path.getmtime(dest)
                    mtime_diff = abs(s_time - d_time)
                    if mtime_diff > 2.0:
                        reason = (f"修改時間不同（大小相同）\n"
                                  f"  差異：{mtime_diff:.1f} 秒")
                        conflict_type = "time_mismatch"
                    else:
                        engine.log(f"[==] 檔案相同(大小/時間)，自動略過寫入: {rel_path}")
                        return "skip", dest

                if on_conflict:
                    action = on_conflict({
                        "rel_path": rel_path,
                        "reason": reason,
                        "conflict_type": conflict_type,
                        "target": target_type
                    })
                    if action == "skip":
                        return "skip", dest
                    if action == "rename":
                        base, ext = os.path.splitext(dest)
                        idx = 1
                        new_dest = f"{base}({idx}){ext}"
                        while os.path.exists(new_dest):
                            idx = idx + 1
                            new_dest = f"{base}({idx}){ext}"
                        return "copy", new_dest
                    if action == "verify" and conflict_type == "time_mismatch":
                        engine.log(f"[校驗] 計算 XXH64: {rel_path}")
                        h_src = engine.get_xxh64(src)
                        h_dst = engine.get_xxh64(dest)
                        if h_src == h_dst:
                            engine.log(f"[==] XXH64 相同，自動略過寫入: {rel_path}")
                            return "skip", dest
                        else:
                            action2 = on_conflict({
                                "rel_path": rel_path,
                                "reason": f"Hash 不符\n來源: {_short_hash(h_src)}...\n目標: {_short_hash(h_dst)}...",
                                "conflict_type": "hash_mismatch",
                                "target": target_type
                            })
                            if action2 == "skip": return "skip", dest
                            if action2 == "rename":
                                base, ext = os.path.splitext(dest)
                                idx = 1
                                new_dest = f"{base}({idx}){ext}"
                                while os.path.exists(new_dest):
                                    idx = idx + 1
                                    new_dest = f"{base}({idx}){ext}"
                                return "copy", new_dest
                            return "copy", dest
                    return "copy", dest
                else:
                    return "copy", dest
            except Exception as e:
                engine.log(f"[!] 檢查重複檔案失敗: {e}")
                return "copy", dest

        # ==========================================
        # 階段一：備份至本機 (Local)
        # ==========================================
        self.log(f"[Engine] 開始階段一：備份至本機...")
        for card, rel, src_abs in all_items:
            if self._check_pause_stop():
                self.log("[Engine] 任務已依指示中斷。")
                break

            cp_key = f"{card}/{rel}"
            l_dest = os.path.join(local_dir, card, rel)

            try:
                file_size = os.path.getsize(src_abs)
            except OSError:
                continue

            # 中斷點跳過：本機階段已完成
            if self._is_checkpoint_done(cp_completed, cp_key, "local", l_dest, file_size):
                self.log(f"[==] 中斷點跳過（本機）: {cp_key}")
                done_state_l[1] += 1
                done_state_l[0] += file_size
                continue

            action_l, l_dest = check_duplicate(self, src_abs, l_dest, "local", file_size, rel)
            skip_copy_l = action_l == "skip"

            if skip_copy_l:
                # 重複檢查確認檔案已存在且相同 → 補寫 checkpoint 加速下次續傳
                entry = cp_completed.setdefault(cp_key, {"size": file_size})
                entry["local"] = True
                done_state_l[1] += 1
                done_state_l[0] += file_size
                continue

            if src_abs.lower().endswith(SUPPORTED_EXTS):
                while not self.is_file_stable(src_abs):
                    if self._check_pause_stop(): break
                    time.sleep(2)
            
            try:
                prog_cb = self._create_progress_callback(
                    "backup_local", total_bytes, total_files, card, rel,
                    t_last_chunk, speed_samples, done_state_l, on_progress
                )
                if not self.copy_file_chunked(src_abs, l_dest, prog_cb):
                    break
            except Exception as e:
                self.err(f"寫入本機失敗: {rel} - {e}")

            try:
                shutil.copystat(src_abs, l_dest)
            except OSError:
                pass

            if do_hash:
                h_src = self.get_xxh64(src_abs)
                h_loc = self.get_xxh64(l_dest)
                if h_src == h_loc:
                    self.log(f"[OK] 本機寫入成功 {card}/{rel} ({_short_hash(h_src)}...)")
                else:
                    self.err(f"[XXH64 FAIL] 本機寫入失敗 {card}/{rel}")
            else:
                self.log(f"[OK] 本機寫入成功 {card}/{rel}")

            # 記錄 checkpoint（每 50 個檔案寫一次，避免過多 I/O）
            entry = cp_completed.setdefault(cp_key, {"size": file_size})
            entry["local"] = True

            done_state_l[1] += 1
            done_state_l[0] += file_size

            if done_state_l[1] % 50 == 0:
                self._save_checkpoint(local_dir, project_name, cp_completed)

        # 階段一結束：flush checkpoint 記錄
        if cp_completed:
            self._save_checkpoint(local_dir, project_name, cp_completed)

        # ==========================================
        # 階段二：備份至 NAS (NAS)
        # ==========================================
        if nas_dir and not self._stop_event.is_set():
            self.log(f"[Engine] 開始階段二：備份至 NAS...")
            done_bytes_n = 0
            done_files_n = 0
            speed_samples_n: List[float] = []
            done_state_n = [done_bytes_n, done_files_n]
            
            for card, rel, src_abs in all_items:
                engine = self
                if engine._check_pause_stop():
                    engine.log("[Engine] 任務已依指示中斷。")
                    break

                cp_key = f"{card}/{rel}"
                n_dest = os.path.join(nas_dir, card, rel)

                try:
                    file_size = os.path.getsize(src_abs)
                except OSError:
                    continue

                # 中斷點跳過：NAS 階段已完成
                if self._is_checkpoint_done(cp_completed, cp_key, "nas", n_dest, file_size):
                    self.log(f"[==] 中斷點跳過（NAS）: {cp_key}")
                    done_state_n[1] += 1
                    done_state_n[0] += file_size
                    continue

                action_n, n_dest = check_duplicate(self, src_abs, n_dest, "nas", file_size, rel)
                skip_copy_n = action_n == "skip"

                if skip_copy_n:
                    # 重複檢查確認檔案已存在且相同 → 補寫 checkpoint 加速下次續傳
                    entry = cp_completed.setdefault(cp_key, {"size": file_size})
                    entry["nas"] = True
                    done_state_n[1] += 1
                    done_state_n[0] += file_size
                    continue

                if src_abs.lower().endswith(SUPPORTED_EXTS):
                    while not engine.is_file_stable(src_abs):
                        if engine._check_pause_stop(): break
                        time.sleep(2)

                try:
                    prog_cb_n = self._create_progress_callback(
                        "backup_nas", total_bytes, total_files, card, rel,
                        t_last_chunk, speed_samples_n, done_state_n, on_progress
                    )
                    if not engine.copy_file_chunked(str(src_abs), n_dest, prog_cb_n):
                        break
                except Exception as e:
                    engine.err(f"寫入 NAS 失敗: {rel} - {e}")

                try:
                    shutil.copystat(str(src_abs), n_dest)
                except OSError:
                    pass

                if do_hash:
                    h_src = engine.get_xxh64(src_abs)
                    h_nas = engine.get_xxh64(n_dest)
                    if h_src == h_nas:
                        engine.log(f"[OK] NAS 寫入成功 {card}/{rel} ({_short_hash(h_src)}...)")
                    else:
                        engine.err(f"[XXH64 FAIL] NAS 寫入失敗 {card}/{rel}")
                else:
                    engine.log(f"[OK] NAS 寫入成功 {card}/{rel}")

                # 記錄 checkpoint（每 50 個檔案寫一次）
                entry = cp_completed.setdefault(cp_key, {"size": file_size})
                entry["nas"] = True

                done_state_n[1] += 1
                done_state_n[0] += file_size

                if done_state_n[1] % 50 == 0:
                    self._save_checkpoint(local_dir, project_name, cp_completed)

            # 階段二結束：flush checkpoint 記錄
            if cp_completed:
                self._save_checkpoint(local_dir, project_name, cp_completed)

        # ── 備份後補齊空資料夾（防止中途被刪除） ──
        if not self._stop_event.is_set() and empty_dirs:
            for card, rel_dir in empty_dirs:
                l_dir = os.path.join(local_dir, card, rel_dir)
                os.makedirs(l_dir, exist_ok=True)
                if nas_dir:
                    try:
                        os.makedirs(os.path.join(nas_dir, card, rel_dir), exist_ok=True)
                    except OSError:
                        pass

        # ── 備份後快速二次掃描 (檔名與大小) ──
        if not self._stop_event.is_set():
            self.log("[Engine] 執行快速二次掃描 (檢查檔名與大小)...")
            mismatch_count: int = 0
            scan_total = len(all_items)
            for scan_idx, (card, rel, src_abs) in enumerate(all_items):
                if self._check_pause_stop(): break
                try:
                    src_size = os.path.getsize(src_abs)
                except OSError:
                    continue
                l_dest = os.path.join(local_dir, card, rel)
                n_dest = os.path.join(nas_dir, card, rel) if nas_dir else ""

                # 二次掃描進度回報 (掃描本身)
                if on_progress is not None:
                    on_progress({  # type: ignore
                        "phase": "rescan",
                        "status": "scanning",
                        "current_file": f"{card}/{os.path.basename(rel)}",
                        "file_pct": 0,
                        "total_pct": (scan_idx / scan_total) * 100 if scan_total else 100,
                        "done_files": scan_idx,
                        "total_files": scan_total,
                        "speed_mbps": None,
                        "eta_sec": None
                    })

                need_recopy_l = not os.path.exists(l_dest) or os.path.getsize(l_dest) != src_size
                need_recopy_n = bool(n_dest and (not os.path.exists(n_dest) or os.path.getsize(n_dest) != src_size))
                
                if need_recopy_l or need_recopy_n:
                    mismatch_count += 1  # type: ignore
                    t_str = " 與 ".join([t for t, need in [("本機", need_recopy_l), ("NAS", need_recopy_n)] if need])
                    self.log(f"[!] 二次掃描發現 {t_str} 缺失或大小不符：{rel}，自動執行補齊...")

                    def _rescan_prog(copied: int, fsize: int, speed: float, _rel: str = rel, _card: str = card, _si: int = scan_idx, _st: int = scan_total):
                        if on_progress is not None:
                            eta = ((fsize - copied) / speed) if speed > 0 else None
                            on_progress({  # type: ignore
                                "phase": "rescan",
                                "status": "recopying",
                                "current_file": f"[補齊] {_card}/{os.path.basename(_rel)}",
                                "file_pct": (copied / fsize) * 100 if fsize else 100,
                                "total_pct": (_si / _st) * 100 if _st else 100,
                                "done_files": _si,
                                "total_files": _st,
                                "speed_mbps": speed / (1024 * 1024) if speed else None,
                                "eta_sec": eta
                            })

                    if need_recopy_l:
                        try:
                            if not self.copy_file_chunked(str(src_abs), l_dest, _rescan_prog): break
                        except Exception as e:
                            self.err(f"二次掃描補齊本機失敗: {rel} ({e})")
                    if need_recopy_n:
                        try:
                            self.copy_file_chunked(str(src_abs), n_dest, _rescan_prog)
                        except Exception as e:
                            self.err(f"二次掃描補齊 NAS 失敗: {rel} ({e})")
                    try:
                        shutil.copystat(str(src_abs), l_dest)
                        if n_dest: shutil.copystat(str(src_abs), n_dest)
                    except Exception:
                        pass
                        
            if not self._stop_event.is_set():
                if mismatch_count == 0:
                    self.log("[OK] 二次掃描完成，所有檔案皆齊全。")
                else:
                    self.log(f"[!] 二次掃描完成，共補齊了 {mismatch_count} 個檔案。")
                self.log("[Engine] 備份任務全部完成。")
                # 備份完整完成，移除中斷點檔案
                self._remove_checkpoint(local_dir)
                if on_progress is not None:
                    on_progress({"phase": "backup", "status": "completed", "total_pct": 100})  # type: ignore
                

    @staticmethod
    def _get_video_duration(filepath: str) -> float:
        """Call ffprobe to get video duration in seconds"""
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", filepath
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 text=True, timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
            return float(res.stdout.strip())
        except Exception:
            return 0.0

    @staticmethod
    def get_video_metadata(filepath: str) -> Dict[str, Any]:
        """Use ffprobe to extract FPS, resolution, codec, and duration from a video file."""
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,codec_name:format=duration",
            "-of", "json", filepath
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 text=True, timeout=10,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
            data = json.loads(res.stdout)
            stream = data.get("streams", [{}])[0]
            fmt    = data.get("format", {})
            codec = stream.get("codec_name", "")
            width = stream.get("width", 0)
            height = stream.get("height", 0)
            resolution = f"{width}x{height}" if width and height else ""
            r_fr = stream.get("r_frame_rate", "0/1")
            try:
                num, den = map(int, r_fr.split("/"))
                fps = round(num / den, 3) if den else 0.0
            except Exception:
                fps = 0.0
            try:
                duration = float(fmt.get("duration", 0) or 0)
            except Exception:
                duration = 0.0
            return {"fps": fps, "resolution": resolution, "codec": codec, "duration": duration}
        except Exception:
            return {"fps": 0.0, "resolution": "", "codec": "", "duration": 0.0}

    @staticmethod
    def generate_film_strip(filepath: str, frames: int = 15, thumb_width: int = 240, quality: int = 85) -> str:
        """Extract `frames` equally-spaced thumbnails, stitch them horizontally, and return as Base64 JPG string."""
        try:
            from PIL import Image  # type: ignore
        except ImportError:
            try:
                import subprocess as _sp2, sys as _sys2
                _sp2.run(
                    [_sys2.executable, "-m", "pip", "install", "Pillow", "--quiet",
                     "--no-warn-script-location"],
                    capture_output=True, timeout=90
                )
                from PIL import Image  # type: ignore
            except Exception:
                return ""  # Pillow install failed — skip filmstrip gracefully

        duration = MediaGuardEngine._get_video_duration(filepath)
        if duration <= 0:
            return ""

        interval = duration / (frames + 1)
        thumb_list: List[str] = []
        HNO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)

        with tempfile.TemporaryDirectory() as tmpdir:
            import concurrent.futures

            def extract_frame(idx: int):
                t = interval * (idx + 1)
                out_path = os.path.join(tmpdir, f"frame_{idx:03d}.jpg")
                cmd = [
                    "ffmpeg", "-y", "-nostdin",
                    "-ss", str(t),
                    "-i", filepath,
                    "-vf", f"scale={thumb_width}:-1",
                    "-vframes", "1",
                    "-q:v", "2",
                    out_path
                ]
                try:
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=15, creationflags=HNO_WINDOW)
                    if os.path.exists(out_path):
                        return out_path
                except Exception:
                    pass
                return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=frames) as executor:
                results = list(executor.map(extract_frame, range(frames)))

            thumb_list = [p for p in results if p is not None]

            if not thumb_list:
                return ""

            # Stitch horizontally
            images = [Image.open(p).convert("RGB") for p in thumb_list]
            total_w = sum(img.width for img in images)
            max_h   = max(img.height for img in images)
            strip   = Image.new("RGB", (total_w, max_h), (20, 20, 20))
            x_off = 0
            for img in images:
                strip.paste(img, (x_off, 0))
                x_off += img.width

            # Encode as Base64 JPG
            import io
            buf = io.BytesIO()
            strip.save(buf, format="JPEG", quality=quality)
            return base64.b64encode(buf.getvalue()).decode("utf-8")

    def run_transcode_job(
        self,
        sources: List[str],
        dest_dir: str,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None
    ):
        """獨立 Proxy 轉檔邏輯"""
        if os.environ.get("MOCK_FFMPEG") == "1":
            self.log("MOCK: transcode skipped")
            return
        self._stop_event.clear()
        self._pause_event.clear()

        # 收集支援的檔案
        files = []
        for token in sources:
            if not token: continue
            if os.path.isfile(token) and token.lower().endswith(SUPPORTED_EXTS):
                files.append(token)
            elif os.path.isdir(token):
                for root, _, fnames in os.walk(token):
                    for fname in sorted(fnames):
                        if fname.lower().endswith(SUPPORTED_EXTS):
                            files.append(os.path.join(root, fname))

        total = len(files)
        if total == 0:
            self.log("[Engine] 未找到任何支援的影片檔可轉檔。")
            return

        self.log(f"[Engine] 共找到 {total} 個影片，開始 Proxy 轉檔...")
        err_count: int = 0

        for i, src_file in enumerate(files):
            if self._check_pause_stop():
                self.log("[Engine] 轉檔任務已中斷。")
                break

            # 若來源包含陣列中的資料夾，嘗試維持一層資料夾結構
            rel_dir = ""
            for token in sources:
                if os.path.isdir(token) and src_file.startswith(token):
                    rel = os.path.relpath(src_file, token)
                    parent = os.path.basename(os.path.dirname(src_file))
                    if parent and parent != os.path.basename(token):
                        rel_dir = parent
                    break

            out_dir = os.path.join(dest_dir, rel_dir) if rel_dir else dest_dir
            os.makedirs(out_dir, exist_ok=True)

            base_name = os.path.splitext(os.path.basename(src_file))[0]
            proxy_out = os.path.join(out_dir, f"{base_name}_proxy.mov")

            self.log(f"[{i+1}/{total}] 正在轉檔: {os.path.basename(src_file)}")
            duration = self._get_video_duration(src_file)

            cmd = [
                "ffmpeg", "-y", "-nostdin",
                "-i", src_file,
                "-map", "0:v", "-map", "0:a?",
                "-vf", "scale=-2:720",
                "-c:v", "prores_ks", "-profile:v", "1",
                "-c:a", "copy",
                "-progress", "pipe:1",
                "-nostats",
                proxy_out
            ]

            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
            t_start = time.time()

            # 解析 FFmpeg 進度
            for pline in (proc.stdout or []):
                if self._check_pause_stop():
                    proc.terminate()
                    break
                
                pline = pline.strip()
                if pline.startswith("out_time_ms="):
                    try:
                        ms = int(pline.split("=")[1])
                        if duration > 0:
                            frac = min(1.0, (ms / 1_000_000) / duration)
                            base_pct = i / total
                            slot_size = 1.0 / total
                            curr_pct = (base_pct + (frac * slot_size)) * 100

                            if on_progress is not None:
                                on_progress({  # type: ignore
                                    "phase": "transcode",
                                    "status": "processing",
                                    "current_file": os.path.basename(src_file),
                                    "file_pct": frac * 100,
                                    "total_pct": curr_pct,
                                    "done_files": i,
                                    "total_files": total
                                })
                    except Exception:
                        pass

            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            if self._stop_event.is_set():
                self.log(f"[X] {os.path.basename(src_file)} 轉檔已強制中止。")
                if os.path.exists(proxy_out):
                    try:
                        os.remove(proxy_out)
                    except:
                        pass
                break

            if proc.returncode != 0:
                self.err(f"[!] 轉檔失敗: {os.path.basename(src_file)}")
                err_count += 1  # type: ignore
            else:
                self.log(f"[OK] 完成: {os.path.basename(proxy_out)}")

        if not self._stop_event.is_set():
            if err_count == 0:
                self.log("[Engine] 轉檔任務全部完成！")
            else:
                self.err(f"[Engine] 轉檔結束，發生 {err_count} 個錯誤。")
                
            if on_progress is not None:
                on_progress({"phase": "transcode", "status": "completed", "total_pct": 100})  # type: ignore
                
    def run_concat_job(
        self,
        sources: List[str],
        dest_dir: str,
        custom_name: str = "",
        resolution: str = "1080P",
        codec: str = "ProRes",
        burn_timecode: bool = True,
        burn_filename: bool = False,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None
    ):
        """獨立時間碼壓印串帶邏輯"""
        self._stop_event.clear()
        self._pause_event.clear()

        # 收集影片
        files = []
        for token in sources:
            if not token: continue
            if os.path.isfile(token) and token.lower().endswith(SUPPORTED_EXTS):
                files.append(token)
            elif os.path.isdir(token):
                for root, _, fnames in os.walk(token):
                    for fname in sorted(fnames):
                        if fname.lower().endswith(SUPPORTED_EXTS):
                            files.append(os.path.join(root, fname))

        if not files:
            self.log("[Engine] 未找到任何支援的影片檔可串帶。")
            return

        # [v1.0.179] 強制全局預先排序 (Global Array Serialization)
        # 確保不論輸入多少個各自獨立的資料夾來源，往後產生 concat_list 和計算 drawtext timestamp 時，
        # 皆百分之百共用同一套絕對排序基準，從物理上根絕時間軸張冠李戴的漂移現象。
        files = sorted(files)

        self.log(f"[Engine] 共找到 {len(files)} 個影片碎片，準備串聯...")

        import tempfile
        import uuid
        os.makedirs(dest_dir, exist_ok=True)
        concat_list = os.path.join(tempfile.gettempdir(), f"Originsun_concat_list_{uuid.uuid4().hex[:8]}.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            for p in files:
                safe_p = p.replace("'", "'\\''").replace("\\", "/")
                # 取得準確秒數並強制寫入 concat list 中作為 duration 鎖定
                dur = self._get_video_duration(p)
                f.write(f"file '{safe_p}'\n")
                if dur > 0:
                    f.write(f"duration {dur:.3f}\n")

        # 決定檔名與副檔名
        c_upper = codec.upper()
        ext = ".mp4" if ("H264" in c_upper or "H.264" in c_upper or "NVENC" in c_upper or "X264" in c_upper) else ".mov"
        if custom_name:
            folder_name = custom_name
        else:
            first_src = sources[0] if sources else ""
            if os.path.isdir(first_src):
                folder_name = os.path.basename(first_src) or "Reel"
            else:
                folder_name = "Standalone_Reel"
        reel_out = os.path.join(dest_dir, f"{folder_name}{ext}")
        
        # 預先嘗試刪除已存在的輸出檔案，以避免 FFmpeg -y 在 NAS 上直接覆寫造成的各種死鎖問題
        if os.path.exists(reel_out):
            try:
                os.remove(reel_out)
            except OSError as e:
                self.err(f"[系統阻擋] 無法覆寫 {os.path.basename(reel_out)} (檔案可能正被開啟、或遭系統鎖定): {e}")
                return
                
        self.log(f"目標輸出: {os.path.basename(reel_out)} (編碼: {codec})")

        scale_filter = ""
        if resolution == "720P":
            scale_filter = "scale=-2:720"
        elif resolution == "1080P":
            scale_filter = "scale=-2:1080"
        elif resolution == "Ultra HD":
            scale_filter = "scale=-2:2160"

        win_dir = os.environ.get("WINDIR", "C:\\Windows")
        
        # 尋找支援中文字體的微軟正黑體，若無則降級為 Arial
        msjh_path = os.path.join(win_dir, "Fonts", "msjh.ttc")
        arial_path = os.path.join(win_dir, "Fonts", "arial.ttf")
        chosen_font = msjh_path if os.path.exists(msjh_path) else arial_path
        
        font_path = chosen_font.replace("\\", "/").replace(":", "\\:")
        
        # 決定編碼參數
        if "NVENC" in codec:
            vcodec_args = ["-c:v", "h264_nvenc", "-pix_fmt", "yuv420p", "-preset", "p4", "-cq", "23"]
            acodec_args = ["-c:a", "aac", "-b:a", "192k"]
        elif "軟體" in codec or "x264" in codec.lower():
            vcodec_args = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "23"]
            acodec_args = ["-c:a", "aac", "-b:a", "192k"]
        else:
            # 預設 ProRes
            vcodec_args = ["-c:v", "prores_ks", "-profile:v", "1"]
            acodec_args = ["-c:a", "copy"]

        # 計算時長與動態檔名壓印指令
        concat_duration: float = 0.0
        
        # 由於 FFmpeg 不支援透過 sendcmd 在執行時期動態熱抽換 `textfile` 參數，
        # 我們改採為每段影片建立一個獨立的 drawtext 濾鏡元件，並透過 enable=between(t,...) 來控制各自的顯示區間。
        # 為避免命令列長度超過 Windows 限制，我們將所有濾鏡串聯寫入單一的 Filter Script 檔案中。
        filter_script_path = os.path.join(tempfile.gettempdir(), f"Originsun_filters_{uuid.uuid4().hex[:8]}.txt")
        current_time_sec = 0.0
        
        # 動態折行邏輯 (以預期畫面寬度 80% 為界)
        target_width_px = 720 # Default or minimum
        if resolution == "1080P":
            target_width_px = 1080
        elif resolution == "Ultra HD":
            target_width_px = 2160
            
        # 抓取第一支影片判斷是否為直式
        is_vertical = False
        if files:
            try:
                probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", files[0]]
                probe_res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000))
                if probe_res.returncode == 0:
                    import json as _j
                    p_data = _j.loads(probe_res.stdout)
                    streams = p_data.get("streams", [])
                    if streams:
                        w = int(streams[0].get("width", 1920))
                        h = int(streams[0].get("height", 1080))
                        is_vertical = w < h
            except Exception:
                pass
        
        actual_canvas_width = target_width_px if not is_vertical else (target_width_px * 9 // 16)
        max_text_width = actual_canvas_width * 0.85 # 放寬至畫面 85% 作為最大單行寬度容忍值

        # Pre-Scan: 全局掃描找出最長檔名的長度，決定整體的統一字體縮小倍率
        # (將此步驟上移，確保右上角的時間碼也能共用這套精準的縮小邏輯)
        global_max_w = 0
        if burn_filename:
            for p in files:
                estimated_w = 0
                for char in os.path.basename(p):
                    if ord(char) > 127: estimated_w += 36
                    else: estimated_w += 20
                if estimated_w > global_max_w:
                    global_max_w = estimated_w
                    
        global_fontsize = 36
        if global_max_w > max_text_width:
            scale_ratio = max_text_width / global_max_w
            global_fontsize = max(16, int(36 * scale_ratio)) # 底線保底 16px

        # 組合濾鏡 (Filters)
        filters = []
        if scale_filter:
            filters.append(scale_filter)
        if burn_timecode:
            # [v1.0.180] 將時間碼的字級鎖死等於檔名字級，徹底達成兩個角落 1:1 的完美對稱視覺
            filters.append(f"drawtext=fontfile='{font_path}':text='%{{pts\\:hms}}':x=w-tw-20:y=20:fontsize={global_fontsize}:fontcolor=white@0.5:box=1:boxcolor=black@0.25:boxborderw=6")

        for idx, p in enumerate(files):
            dur = self._get_video_duration(p)
            end_time = current_time_sec + dur
            
            if burn_filename:
                base_name = os.path.basename(p)
                # 直接在 filter script 內使用安全的 escape 字串，免除實體文字檔傳遞
                escaped_name = base_name.replace(":", "\\:").replace("'", "'\\''").replace("%", "\\%")
                
                filters.append(
                    f"drawtext=fontfile='{font_path}':text='{escaped_name}':"
                    f"x=20:y=h-th-20:fontsize={global_fontsize}:fontcolor=white@0.8:box=1:boxcolor=black@0.4:boxborderw=6:"
                    f"enable='between(t,{current_time_sec:.3f},{end_time:.3f})'"
                )
                
            current_time_sec = end_time
            concat_duration += dur

        self.log(f"總時長估算: {concat_duration:.2f} 秒，啟動 FFmpeg...")

        vf_arg = []
        if filters:
            with open(filter_script_path, "w", encoding="utf-8") as fs:
                # FFmpeg script 中的濾鏡必須用逗號分隔以串接成一條鍊
                fs.write(",\n".join(filters))
            # 使用 -filter_script:v 將長篇幅的影片濾鏡載入，保持與 -vf 相同的單一輸入流映射邏輯
            vf_arg = ["-filter_script:v", filter_script_path]

        used_nvenc = "NVENC" in codec
        fallback_attempted = False

        # ── 可能執行兩次：第一次 NVENC，失敗則 fallback x264 ──
        while True:
            cmd = [
                "ffmpeg", "-y", "-nostdin",
                "-f", "concat", "-safe", "0",
                "-i", concat_list,
                "-map", "0:v", "-map", "0:a?"
            ] + vf_arg + vcodec_args + acodec_args + [
                "-progress", "pipe:1",
                "-nostats",
                reel_out
            ]

            # 加入 CREATE_NO_WINDOW 避免在背景打擾到使用者
            creation_flags = 0
            if hasattr(subprocess, 'CREATE_NO_WINDOW'):
                creation_flags = getattr(subprocess, 'CREATE_NO_WINDOW')

            err_log_file = os.path.join(tempfile.gettempdir(), f"Originsun_concat_err_{uuid.uuid4().hex[:8]}.log")
            f_err = open(err_log_file, "w", encoding="utf-8")
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=f_err, encoding="utf-8", errors="replace", creationflags=creation_flags)

            t_start = time.time()
            for cline in (proc.stdout or []):
                if self._check_pause_stop():
                    proc.terminate()
                    break

                cline = cline.strip()
                if cline.startswith("out_time_ms="):
                    try:
                        cms = int(cline.split("=")[1])
                        if concat_duration > 0:
                            cfrac = min(1.0, (cms / 1_000_000) / float(concat_duration))
                            elapsed = time.time() - t_start
                            speed_mbps = float(os.path.getsize(reel_out) / (1024*1024)) / elapsed if elapsed > 0 and os.path.exists(reel_out) else 0.0

                            if on_progress is not None:
                                on_progress({  # type: ignore
                                    "phase": "concat",
                                    "status": "processing",
                                    "current_file": os.path.basename(reel_out),
                                    "file_pct": cfrac * 100,
                                    "total_pct": cfrac * 100,
                                    "speed_mbps": speed_mbps,
                                    "done_files": 1,
                                    "total_files": 1
                                })
                    except Exception:
                        pass

            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            try:
                f_err.close()
            except:
                pass

            # ── NVENC 失敗 → 自動降級為 x264 軟體編碼 ──
            if proc.returncode != 0 and used_nvenc and not fallback_attempted:
                err_txt = ""
                try:
                    with open(err_log_file, "r", encoding="utf-8") as fe:
                        err_txt = fe.read()
                except:
                    pass
                try:
                    os.remove(err_log_file)
                except:
                    pass
                # 清理失敗的輸出檔
                if os.path.exists(reel_out):
                    try: os.remove(reel_out)
                    except: pass

                self.log("[!] NVENC 硬體編碼失敗，自動切換為 x264 軟體編碼重試...")
                vcodec_args = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "23"]
                acodec_args = ["-c:a", "aac", "-b:a", "192k"]
                used_nvenc = False
                fallback_attempted = True
                continue  # 重新執行 while 迴圈

            break  # 正常結束或非 NVENC 失敗，跳出迴圈

        try:
            os.remove(concat_list)
            if os.path.exists(cmd_file_path):
                os.remove(cmd_file_path)
        except Exception:
            pass

        if self._stop_event.is_set():
            self.log("[X] 串帶已中止。")
            if os.path.exists(reel_out):
                try: os.remove(reel_out)
                except: pass
        elif proc.returncode != 0:
            err_txt = "Unknown stderr"
            try:
                with open(err_log_file, "r", encoding="utf-8") as fe:
                    err_txt = fe.read()
            except:
                pass
            if len(err_txt) > 500:
                err_txt = "..." + err_txt[-500:]  # type: ignore
            self.err(f"[!] 串帶失敗: \n{err_txt}")
        else:
            suffix = "（已自動降級為 x264 軟體編碼）" if fallback_attempted else ""
            self.log(f"[Engine] 串帶壓印任務完成！{suffix}")
            if on_progress is not None:
                on_progress({"phase": "concat", "status": "completed", "total_pct": 100})  # type: ignore

        try:
            if os.path.exists(err_log_file):
                os.remove(err_log_file)
        except:
            pass
            
    def run_verify_job(
        self,
        pairs: List[Tuple[str, str]],
        mode: str = "quick",
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None
    ):
        """獨立檔案比對邏輯"""
        self._stop_event.clear()
        self._pause_event.clear()

        if not pairs:
            self.err("[Engine] 沒有設定任何比對組！")
            return
            
        for s, d in pairs:
            if not s or not d:
                self.err("[Engine] 路徑檢查失敗：每一組來源都必須指定相對應的目標資料夾進行比對！")
                return

        self.log(f"[Engine] 開始獨立校驗，共 {len(pairs)} 組比對任務")
        total_err_count: int = 0

        for s_idx, (src_input, dst_input) in enumerate(pairs):
            if self._check_pause_stop():
                self.log("[Engine] 任務已依指示中斷。")
                break

            self.log(f"\n[比對任務 {s_idx+1}/{len(pairs)}]\n來源: {src_input}\n目標: {dst_input}")
            
            # 掃描來源檔案
            all_files: List[str] = []
            verify_empty_dirs: List[str] = []   # 來源中的空資料夾（相對路徑）
            if ";" in src_input or os.path.isfile(src_input):
                for fpath in src_input.split(";"):
                    fpath = fpath.strip()
                    if fpath and os.path.isfile(fpath):
                        all_files.append(fpath)
            elif os.path.isdir(src_input):
                for root, dirnames, files in os.walk(src_input):
                    if not files and not dirnames:
                        verify_empty_dirs.append(os.path.relpath(root, src_input))
                    for f in files:
                        all_files.append(os.path.join(root, f))
            
            # 決定是「資料夾對資料夾」還是「檔案對檔案」模式
            dst_is_flat = not os.path.isdir(dst_input)
            dst_files: List[str] = []
            if dst_is_flat:
                for fpath in dst_input.split(";"):
                    fpath = fpath.strip()
                    if fpath:
                        dst_files.append(fpath)
            
            total = len(all_files)
            if total == 0:
                self.log("[!] 此組來源內沒有任何檔案，跳過。")
                continue
                
            self.log(f"→ 掃描到 {total} 個檔案。開始{'快速（大小）' if mode == 'quick' else '進階（XXH64）'}比對...")
            err_count: int = 0

            # ── 檢查空資料夾是否存在於目標 ──
            if verify_empty_dirs and os.path.isdir(dst_input):
                for rel_dir in verify_empty_dirs:
                    dst_dir = os.path.join(dst_input, rel_dir)
                    if not os.path.exists(dst_dir):
                        self.err(f"目標缺少空資料夾: {rel_dir}/")
                        err_count += 1
                        os.makedirs(dst_dir, exist_ok=True)
                        self.log(f"[補齊] 已建立空資料夾: {rel_dir}/")
                    else:
                        self.log(f"[OK] 空資料夾存在: {rel_dir}/")
            
            for i, src_abs in enumerate(all_files):
                if self._check_pause_stop():
                    break

                if dst_is_flat:
                    if i >= len(dst_files):
                        self.err(f"無對應目標: {os.path.basename(src_abs)} (目標列表只有 {len(dst_files)} 個)")
                        err_count = err_count + 1
                        continue
                    dst_abs = dst_files[i]
                    rel_path = os.path.basename(src_abs)
                else:
                    if os.path.isdir(src_input):
                        rel_path = os.path.relpath(src_abs, src_input)
                    else:
                        rel_path = os.path.basename(src_abs)
                    dst_abs = os.path.join(dst_input, rel_path)
                
                if not os.path.exists(dst_abs):
                    self.err(f"目標檔案遺失: {rel_path} (在 {dst_input} 中找不到)")
                    err_count = err_count + 1
                    continue
                
                # ─ 快速比對：只比較檔案大小
                if mode == "quick":
                    src_sz = os.path.getsize(src_abs)
                    dst_sz = os.path.getsize(dst_abs)
                    if src_sz == dst_sz:
                        self.log(f"[OK] {rel_path}  ({src_sz:,} bytes)")
                    else:
                        self.err(f"大小不符: {rel_path}\n      來源: {src_sz:,} bytes\n      目標: {dst_sz:,} bytes")
                        err_count = err_count + 1
                # ─ 進階比對：XXH64 完整雜湊
                else:
                    h_src = self.get_xxh64(src_abs)
                    h_dst = self.get_xxh64(dst_abs)
                    if h_src == h_dst:
                        self.log(f"[OK] {rel_path} ({h_src[:12]}...)")  # type: ignore
                    else:
                        self.err(f"Hash 不符: {rel_path}\n      來源: {h_src}\n      目標: {h_dst}")
                        err_count = err_count + 1

                pct = ((i + 1) / total) * 100
                if on_progress is not None:
                    try:
                        on_progress({  # type: ignore
                            "phase": "verify",
                            "status": "processing",
                            "current_file": rel_path,
                            "file_pct": pct,
                            "total_pct": pct,
                            "done_files": i + 1,
                            "total_files": total
                        })
                    except Exception:
                        pass
            
            total_err_count += err_count  # type: ignore

        if not self._stop_event.is_set():
            if total_err_count == 0:
                self.log("\n[Engine] 獨立校驗全部完成：所有比對組檔案皆完美吻合！")
            else:
                self.err(f"\n[Engine] 獨立校驗完成：共發現 {total_err_count} 個不符錯誤！")
            
            if on_progress is not None:
                on_progress({"phase": "verify", "status": "completed", "total_pct": 100})  # type: ignore
                
        first_dst = pairs[0][1] if pairs else ""
        if first_dst:
            # log is saved in worker
            pass
