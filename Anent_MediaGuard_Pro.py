import os
import sys
import io
import json
import time
import shutil
import xxhash  # type: ignore[import]
import threading
import subprocess
from collections import deque
from datetime import datetime, timedelta
import customtkinter as ctk  # type: ignore[import]
from tkinter import filedialog, messagebox
try:
    from tkinterdnd2.TkinterDnD import DnDWrapper as _DnDWrapper, _require as _dnd_require  # type: ignore[import]
    from tkinterdnd2 import DND_FILES  # type: ignore[import]
    _HAS_DND = True
except ImportError:
    _HAS_DND = False
    class _DnDWrapper:  # type: ignore[no-redef]
        pass


# 強制 stdout 使用 UTF-8，避免 Windows cp950 崩潰
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 支援的影片格式（擴充專業格式）
SUPPORTED_EXTS = ('.mov', '.mp4', '.mkv', '.mxf', '.r3d', '.ari', '.braw', '.dng', '.avi', '.mts', '.m2ts')


# AnentEngine 已整合至 core_engine.MediaGuardEngine，此處以別名方式引用避免重複實作
# get_xxh64 與 is_file_stable 均已定義於 core_engine.MediaGuardEngine
from core_engine import MediaGuardEngine as _CoreEngine  # type: ignore[attr-defined]

class AnentEngine:
    """Thin alias — delegates to MediaGuardEngine so logic lives in one place."""
    get_xxh64    = staticmethod(_CoreEngine.get_xxh64)    # type: ignore[attr-defined]
    is_file_stable = staticmethod(_CoreEngine.is_file_stable)  # type: ignore[attr-defined]

class AnentApp(ctk.CTk, _DnDWrapper):  # type: ignore[misc]
    def __init__(self) -> None:
        super().__init__()
        self.title("Originsun Media Guard Pro")
        self.geometry("1000x1080")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.source_paths: list[str] = []
        self.source_names: list[str] = []   # 自訂資料夾名稱（與 source_paths 一一對應）
        # 儲存 Tkinter name_var 的 trace_id，以便刪除 UI 節點時能安全註銷 (防止記憶體洩漏)
        self.source_traces: dict[str, tuple[ctk.StringVar, str]] = {}
        self.project_name = ctk.StringVar(value=datetime.now().strftime("%Y%m%d"))

        # 路徑儲存
        self.local_root = "C:/ANENT_Work"
        self.nas_root   = "Z:/ANENT_Backup"
        self.proxy_root = "D:/ANENT_Proxies"

        # 執行緒控制
        self._stop_event  = threading.Event()
        self._pause_event = threading.Event()

        # 繼續執行狀態
        self._processed_files: set[str] = set()
        self._can_resume = False

        # 覆蓋選擇對話框（執行緒間溝通）
        self._overwrite_result: bool = False
        self._overwrite_event = threading.Event()
        self._overwrite_all: bool | None = None

        # 衝突對話框結果（字串："overwrite"/"skip"/"verify"/"rename"/"overwrite_all"/"skip_all")
        self._conflict_result: str = ""
        self._conflict_event = threading.Event()

        # 當前執行中的 FFmpeg 子進程（用於中止時立即殺程序）
        self._current_proc: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._current_output_file: str | None = None  # 用於中止時刪除未完成檔案
        self._current_backup_dests: list[str] = []

        # 統一進度條相位設定
        self._prog_offset: float = 0.0
        self._prog_scale:  float = 1.0

        # ETA 平滑化（平均最近 5 個檔案的傳輸速度）
        self._speed_samples: deque[float] = deque(maxlen=5)

        # 設定持久化
        self._config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
        self._load_settings()

        self.setup_ui()

        # 拖放支援：多重繼承 DnDWrapper + 載入 tkdnd
        if _HAS_DND:
            try:
                _dnd_require(self)        # 將 tkdnd 載入到 Tk interpreter
                self.drop_target_register(DND_FILES)
                self.dnd_bind('<<Drop>>', self._on_drop)
                self.after(200, self._bind_scroll_drop)
            except Exception as _e:
                pass

    # ─────────────────────────────────────────────────────────
    # UI 建構
    # ─────────────────────────────────────────────────────────
    def setup_ui(self) -> None:
        # ── 標題 (保持置頂)
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.pack(pady=(15, 5), padx=20, fill="x")
        ctk.CTkLabel(title_frame, text="Originsun Media Guard Pro",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")

        # ── 建立 Tabview
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self.tab_ingest = self.tabview.add("備份並轉檔")
        self.tab_toolbox = self.tabview.add("媒體工具箱")

        self._build_ingest_tab()
        self._build_toolbox_tab()

    def _build_ingest_tab(self) -> None:
        parent = self.tab_ingest
        # ── 專案名稱
        f1 = ctk.CTkFrame(parent)
        f1.pack(pady=5, padx=20, fill="x")
        ctk.CTkLabel(f1, text="建立目的資料夾:", width=110).pack(side="left", padx=10)
        ctk.CTkEntry(f1, textvariable=self.project_name, width=340).pack(side="left", pady=10)
        
        def _set_today() -> None:
            self.project_name.set(datetime.now().strftime("%Y%m%d"))
            self._save_settings()
            
        ctk.CTkButton(f1, text="設為今日", width=80, fg_color="#454545", hover_color="#666666",
                      command=_set_today).pack(side="left", padx=10)

        # ── 路徑設定（點選按鈕）
        path_frame = ctk.CTkFrame(parent)
        path_frame.pack(pady=5, padx=20, fill="x")
        ctk.CTkLabel(path_frame, text="目的地路徑設定",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(8, 4))
        path_defs = [
            ("專案素材區", "local_root",  self.local_root),
            ("異地備份區",  "nas_root",    self.nas_root),
            ("專案Proxy區","proxy_root",  self.proxy_root),
        ]
        self.path_labels: dict[str, ctk.CTkLabel] = {}
        for label, attr, default in path_defs:
            row = ctk.CTkFrame(path_frame, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=3)
            ctk.CTkButton(row, text=label, width=140,
                          command=lambda a=attr, l=label: self.pick_folder(a, l)
                          ).pack(side="left", padx=(0, 8))
            lbl = ctk.CTkLabel(row, text=default, anchor="w",
                               fg_color=("#d0d0d0", "#2b2b2b"), corner_radius=6,
                               width=680, padx=8)
            lbl.pack(side="left", fill="x", expand=True)
            self.path_labels[attr] = lbl

        # ── 下半部固定容器 (避免按鈕被擠出邊緣，並且維持版面高度連繫)
        bottom_frame = ctk.CTkFrame(parent, fg_color="transparent")
        bottom_frame.pack(side="bottom", fill="x", pady=(0, 10))

        # ── 來源記憶卡清單
        f2 = ctk.CTkFrame(parent)
        f2.pack(pady=(5, 10), padx=20, fill="both", expand=True)
        hdr = ctk.CTkFrame(f2, fg_color="transparent")
        hdr.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(hdr, text="檔案來源資料夾",
                     font=ctk.CTkFont(weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="+ 新增", width=90,
                      command=self.add_source).pack(side="right")
        self.card_scroll = ctk.CTkScrollableFrame(f2)
        self.card_scroll.pack(fill="both", expand=True, padx=10, pady=5)
        self.card_rows: list[ctk.CTkFrame] = []

        # 拖放提示文字
        self._drop_hint = ctk.CTkLabel(
            self.card_scroll,
            text="可將資料夾拖曳至此處新增來源\\n或點擊右上角「+ 新增」按鈕選擇",
            text_color=("#888", "#666"),
            font=ctk.CTkFont(size=13),
            justify="center",
        )
        self._drop_hint.pack(expand=True, pady=30)

        # ── 任務勾選
        f3 = ctk.CTkFrame(bottom_frame)
        f3.pack(pady=5, padx=20, fill="x")
        ctk.CTkLabel(f3, text="執行項目",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(8, 2))
        check_row = ctk.CTkFrame(f3, fg_color="transparent")
        check_row.pack(fill="x", padx=10, pady=5)
        self.c1 = ctk.CTkCheckBox(check_row, text="Proxy轉檔(ProRes LT_720P_proxy)")
        self.c1.select(); self.c1.pack(side="left", padx=15)
        self.c2 = ctk.CTkCheckBox(check_row, text="串帶 (壓印時間碼)")
        self.c2.select(); self.c2.pack(side="left", padx=15)
        self.c4 = ctk.CTkCheckBox(check_row, text="XXH64 完整性校驗")
        self.c4.pack(side="left", padx=15)

        # ── 進度區塊
        prog_frame = ctk.CTkFrame(bottom_frame)
        prog_frame.pack(pady=5, padx=20, fill="x")
        prog_hdr = ctk.CTkFrame(prog_frame, fg_color="transparent")
        prog_hdr.pack(fill="x", padx=10, pady=(8, 2))
        
        self.file_count_lbl = ctk.CTkLabel(prog_hdr, text="進度：尚未開始",
                                            font=ctk.CTkFont(weight="bold"))
        self.file_count_lbl.pack(side="left")
        
        self._is_detail_open = True
        self.detail_btn = ctk.CTkButton(prog_hdr, text="⬆ 收起詳細", width=80, height=24,
                                        fg_color="transparent", hover_color="#444", text_color="#aaa",
                                        command=self.toggle_detail_progress)
        self.detail_btn.pack(side="right", padx=10)

        self.eta_lbl = ctk.CTkLabel(prog_hdr, text="",
                                     font=ctk.CTkFont(size=12),
                                     text_color=("#555", "#aaa"))
        self.eta_lbl.pack(side="right")
        
        self.progress_bar = ctk.CTkProgressBar(prog_frame, height=18, corner_radius=9)
        self.progress_bar.pack(fill="x", padx=10, pady=(2, 8))
        self.progress_bar.set(0)

        # ── 詳細進度區塊
        self.detail_prog_frame = ctk.CTkFrame(prog_frame, fg_color="transparent")
        self.detail_prog_frame.pack(fill="x", pady=(0, 8))
        
        def _make_detail_bar(label_text: str, color: str) -> tuple[ctk.CTkProgressBar, ctk.CTkLabel]:
            row = ctk.CTkFrame(self.detail_prog_frame, fg_color="transparent")
            row.pack(fill="x", padx=15, pady=2)
            lbl = ctk.CTkLabel(row, text=label_text, width=45, anchor="e", font=ctk.CTkFont(size=12))
            lbl.pack(side="left")
            bar = ctk.CTkProgressBar(row, height=10, corner_radius=5, progress_color=color)
            bar.pack(side="left", fill="x", expand=True, padx=10)
            bar.set(0)
            pct_lbl = ctk.CTkLabel(row, text="0%", width=45, font=ctk.CTkFont(size=12))
            pct_lbl.pack(side="right")
            return bar, pct_lbl
            
        self.bar_backup, self.lbl_backup = _make_detail_bar("[備份]", "#1f538d")
        self.bar_trans, self.lbl_trans   = _make_detail_bar("[轉檔]", "#d48a04")
        self.bar_concat, self.lbl_concat = _make_detail_bar("[串帶]", "#228b22")

        # ── 日誌視窗
        log_hdr = ctk.CTkFrame(bottom_frame, fg_color="transparent")
        log_hdr.pack(fill="x", padx=23, pady=(5, 0))
        ctk.CTkLabel(log_hdr, text="執行日誌", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")

        self.log_box = ctk.CTkTextbox(bottom_frame, height=120, text_color="white")
        self.log_box.pack(pady=(2, 0), padx=20, fill="x")

        # ── 錯誤日誌視窗
        err_hdr = ctk.CTkFrame(bottom_frame, fg_color="transparent")
        err_hdr.pack(fill="x", padx=23, pady=(5, 0))
        ctk.CTkLabel(err_hdr, text="錯誤紀錄", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")

        self.error_log_box = ctk.CTkTextbox(bottom_frame, height=60, text_color="#ff4444")
        self.error_log_box.pack(pady=(2, 5), padx=20, fill="x")

        # ── 控制按鈕列
        btn_row = ctk.CTkFrame(bottom_frame, fg_color="transparent")
        btn_row.pack(pady=12)

        self.start_btn = ctk.CTkButton(
            btn_row, text="開始",
            fg_color="#1f538d", hover_color="#2a6cbf",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=120, height=42,
            command=self.start_thread)
        self.start_btn.pack(side="left", padx=10)

        self.pause_btn = ctk.CTkButton(
            btn_row, text="暫停",
            fg_color="#7a5500", hover_color="#b07a00",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=120, height=42,
            state="disabled",
            command=self.toggle_pause)
        self.pause_btn.pack(side="left", padx=10)

        self.stop_btn = ctk.CTkButton(
            btn_row, text="中止",
            fg_color="#8b0000", hover_color="#cc2200",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=120, height=42,
            state="disabled",
            command=self.request_stop)
        self.stop_btn.pack(side="left", padx=10)

        # ── 下一步按鈕（備份完成後才顯示）
        self.next_step_btn = ctk.CTkButton(
            parent, text="進行下一步（轉檔 / 串帶 / XML）",
            fg_color="#1a6b3a", hover_color="#27a05a",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=42,
            command=self.start_post_process)
        # 預設不顯示，備份成功後才 pack
        
    def _build_toolbox_tab(self) -> None:
        from tkinter import filedialog
        parent = self.tab_toolbox
        
        class ToolboxDynamicTool(ctk.CTkFrame):
            def __init__(self, master, title: str, btn_text: str, btn_color: str, run_cmd, has_global_dst: bool = True, is_paired: bool = False, has_custom_filename: bool = False, resolution_options: list[str] | None = None):
                super().__init__(master)  # type: ignore[call-arg]
                self.title = title
                self.run_cmd = run_cmd
                self.has_global_dst = has_global_dst
                self.is_paired = is_paired
                self.has_custom_filename = has_custom_filename
                
                self.src_vars: list[ctk.StringVar] = []
                self.dst_vars: list[ctk.StringVar] = []
                self.src_rows: list[ctk.CTkFrame] = []
                self.global_dst_var = ctk.StringVar() if has_global_dst else None
                self.custom_filename_var = ctk.StringVar() if has_custom_filename else None
                
                self.pack(fill="x", padx=16, pady=6)
                self.configure(border_width=1, border_color="#3a3a3a", corner_radius=8)
                
                # ── Header（標題 + 執行按鈕，深色背景）
                hdr = ctk.CTkFrame(self, fg_color="#252525", corner_radius=7)
                hdr.pack(fill="x", padx=1, pady=(1, 0))
                
                ctk.CTkLabel(hdr, text=title, font=ctk.CTkFont(size=13, weight="bold"),
                             text_color="#e0e0e0").pack(side="left", padx=(12, 0), pady=6)
                
                # 比對模式切換鈕（緊跟標題右方）
                self.compare_mode_var: ctk.StringVar | None = None
                if is_paired:
                    self.compare_mode_var = ctk.StringVar(value="quick")
                    ctk.CTkSegmentedButton(
                        hdr,
                        values=["快速（大小）", "進階（XXH64）"],
                        variable=self.compare_mode_var,
                        command=None,
                        width=240, height=24,
                        font=ctk.CTkFont(size=12),
                    ).pack(side="left", padx=(10, 0), pady=5)
                    self.compare_mode_var.set("快速（大小）")
                    
                # 解析度下拉選單
                if resolution_options:
                    self.res_var = ctk.StringVar(value=resolution_options[0])
                    ctk.CTkOptionMenu(
                        hdr,
                        values=resolution_options,
                        variable=self.res_var,
                        width=120, height=24,
                        font=ctk.CTkFont(size=12),
                        fg_color="#333", button_color="#444", button_hover_color="#555"
                    ).pack(side="left", padx=(10, 0), pady=6)
                    
                # 1. 執行按鈕 (優先靠右)
                self.btn_execute = ctk.CTkButton(hdr, text=btn_text, width=84, height=26, fg_color=btn_color,
                              corner_radius=5, command=self.execute)
                self.btn_execute.pack(side="right", padx=(6, 10), pady=4)
                              
                # 2. 自訂檔名輸入框 (改為靠左，緊跟解析度)
                if self.has_custom_filename:
                    ctk.CTkLabel(hdr, text="檔名:", text_color="#aaaaaa", font=ctk.CTkFont(size=12)).pack(side="left", padx=(15, 2), pady=6)
                    ctk.CTkEntry(hdr, textvariable=self.custom_filename_var,
                                 height=24, corner_radius=5, width=120,
                                 placeholder_text="(選填，免副檔)").pack(side="left", padx=(0, 6), pady=6)
                    
                # 3. 目的資料夾 (靠左，與標題並排，佔滿剩餘空間)
                if has_global_dst:
                    ctk.CTkButton(hdr, text="目的資料夾", height=24, width=80, fg_color="#3d3d3d",
                                  hover_color="#555", corner_radius=5,
                                  command=self.pick_global_dst).pack(side="left", padx=(6, 4))
                    ctk.CTkEntry(hdr, textvariable=self.global_dst_var,
                                 height=24, corner_radius=5,
                                 placeholder_text="目標輸出資料夾...").pack(side="left", fill="x", expand=True, padx=(0, 6))
                    
                # ── 來源區塊 (Sources)
                src_hdr = ctk.CTkFrame(self, fg_color="transparent")
                src_hdr.pack(fill="x", padx=8, pady=(4 if not is_paired else 8, 0))
                ctk.CTkLabel(src_hdr, text="檔案來源", font=ctk.CTkFont(size=15, weight="bold"), text_color="#aaaaaa").pack(side="left", padx=4)
                
                self.rows_container = ctk.CTkFrame(self, fg_color="transparent")
                self.rows_container.pack(fill="x", padx=8, pady=(4, 0))
                
                # 預設空列
                self.add_row(prompt_user=False)
                
                # 收合在來源區塊最下方的新增按鈕
                add_f = ctk.CTkFrame(self, fg_color="transparent")
                add_f.pack(fill="x", padx=8, pady=(0, 8))
                ctk.CTkButton(add_f, text="+ 資料夾", width=62, height=22, fg_color="#1a4a7a",
                              hover_color="#1f538d", corner_radius=5,
                              command=lambda: self.add_row(is_dir=True)).pack(side="left", padx=(4, 4))
                ctk.CTkButton(add_f, text="+ 影片檔", width=62, height=22, fg_color="#1a4a7a",
                              hover_color="#1f538d", corner_radius=5,
                              command=lambda: self.add_row(is_dir=False)).pack(side="left")
                
                
                
            def add_row(self, is_dir: bool = True, prompt_user: bool = True):
                from tkinter import filedialog
                
                new_val = ""
                if prompt_user:
                    if is_dir:
                        p = filedialog.askdirectory(title="選取來源資料夾")
                        if p: 
                            new_val = p
                        else:
                            return
                    else:
                        paths = filedialog.askopenfilenames(title="選取來源影片檔")
                        if paths: 
                            new_val = ";".join(paths)
                        else:
                            return
                            
                    # 檢查並覆蓋初始的空白列
                    if new_val and len(self.src_vars) == 1 and not self.src_vars[0].get():
                        self.src_vars[0].set(new_val)
                        # 更新文字顏色
                        for child in self.src_rows[0].winfo_children():
                            if isinstance(child, ctk.CTkEntry):
                                child.configure(text_color="white")  # type: ignore[union-attr]
                        return

                var = ctk.StringVar()
                if new_val:
                    var.set(new_val)
                    
                row_f = ctk.CTkFrame(self.rows_container, fg_color="transparent")
                row_f.pack(fill="x", pady=2)
                
                ctk.CTkEntry(row_f, textvariable=var, height=24, placeholder_text="來源路徑...", text_color="#aaa" if not var.get() else "white").pack(side="left", fill="x", expand=True, padx=(5, 5))
                
                dst_v = None
                if self.is_paired:
                    dst_v = ctk.StringVar()
                    self.dst_vars.append(dst_v)
                    ctk.CTkEntry(row_f, textvariable=dst_v, height=24, placeholder_text="對應的目標資料夾或目標影片檔...").pack(side="left", fill="x", expand=True, padx=(5, 5))
                    
                    def _pick_row_dst(v=dst_v, s_var=var):
                        src_path = s_var.get().strip()
                        if ";" in src_path or os.path.isfile(src_path):
                            paths = filedialog.askopenfilenames(title="選取對應目標影片檔")
                            if paths: 
                                v.set(";".join(paths))
                        else:
                            p = filedialog.askdirectory(title="選取對應目標資料夾")
                            if p: 
                                v.set(p)
                        
                    ctk.CTkButton(row_f, text="選取", width=50, height=24, fg_color="#444", command=_pick_row_dst).pack(side="left", padx=(0, 5))
                
                def remove_self(r=row_f, v=var, dv=dst_v):
                    r.destroy()
                    if v in self.src_vars:
                        idx = self.src_vars.index(v)
                        self.src_vars.pop(idx)
                        self.src_rows.pop(idx)
                        if self.is_paired and dv in self.dst_vars:
                            self.dst_vars.pop(idx)
                        
                ctk.CTkButton(row_f, text="−", width=24, height=24, fg_color="#8b0000", hover_color="#cc2200",
                              command=remove_self).pack(side="left", padx=(0, 5))
                              
                self.src_rows.append(row_f)
                self.src_vars.append(var)
                
                
            def pick_global_dst(self):
                from tkinter import filedialog
                if self.has_global_dst and self.global_dst_var:
                    p = filedialog.askdirectory(title="選取目標資料夾")
                    if p: self.global_dst_var.set(p)  # type: ignore[union-attr]
                    
            def execute(self):
                if self.is_paired:
                    valid_pairs = []
                    for s_var, d_var in zip(self.src_vars, self.dst_vars):
                        s = s_var.get().strip()
                        d = d_var.get().strip()
                        if s or d:
                            valid_pairs.append((s, d))
                    # 分流快速 vs 進階比對
                    mode = "quick"
                    if self.compare_mode_var:
                        mode = "advanced" if "XXH64" in self.compare_mode_var.get() else "quick"
                    self.run_cmd(valid_pairs, mode)
                else:
                    valid_srcs = [v.get().strip() for v in self.src_vars if v.get().strip()]
                    combined_src = ";".join(valid_srcs)
                    dst = self.global_dst_var.get().strip() if self.has_global_dst and self.global_dst_var else ""  # type: ignore[union-attr]
                    
                    kwargs = {}
                    if self.has_custom_filename:
                        c_var = self.custom_filename_var
                        kwargs['custom_name'] = c_var.get().strip() if c_var is not None else ""
                    if hasattr(self, 'res_var'):
                        kwargs['resolution'] = self.res_var.get()
                        
                    self.run_cmd(combined_src, dst, **kwargs)

        # 實例化三個工具區塊
        self.tool_verify = ToolboxDynamicTool(parent, "檔案比對", "開始比對", "#555", self._run_standalone_verify, has_global_dst=False, is_paired=True)
        self.tool_transcode = ToolboxDynamicTool(parent, "Proxy轉檔(ProRes LT_720P_proxy)", "開始轉檔", "#d48a04", self._run_standalone_transcode)
        self.tool_concat = ToolboxDynamicTool(parent, "串帶(壓印時間碼)", "開始串帶", "#228b22", self._run_standalone_concat, has_custom_filename=True, resolution_options=["720P", "1080P", "Ultra HD"])

        # 取消事件、暫停事件與當前活躍 subprocess
        self._toolbox_cancel_event = threading.Event()
        self._toolbox_pause_event = threading.Event()
        self._toolbox_active_proc = None

        lf = ctk.CTkFrame(parent, fg_color="transparent")
        lf.pack(fill="both", expand=True, padx=20, pady=(8, 10))

        # 進度列 + 暫停按鈕 + 停止按鈕
        pb_row = ctk.CTkFrame(lf, fg_color="transparent")
        pb_row.pack(fill="x", pady=(0, 6))
        
        self.toolbox_progress_bar = ctk.CTkProgressBar(pb_row, height=14, corner_radius=7)
        self.toolbox_progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.toolbox_progress_bar.set(0)
        
        self.toolbox_stop_btn = ctk.CTkButton(
            pb_row, text="停止", width=72, height=28,
            fg_color="#7a1a1a", hover_color="#a02020",
            corner_radius=6, command=self._toolbox_stop
        )
        self.toolbox_stop_btn.pack(side="right", padx=(4, 0))
        self.toolbox_stop_btn.configure(state="disabled")
        
        self.toolbox_pause_btn = ctk.CTkButton(
            pb_row, text="暫停", width=84, height=28,
            fg_color="#3a5a3a", hover_color="#4a7a4a",
            corner_radius=6, command=self._toolbox_toggle_pause
        )
        self.toolbox_pause_btn.pack(side="right", padx=(4, 0))
        self.toolbox_pause_btn.configure(state="disabled")
        
        # 進度資訊標籤列（左：進度詳情，右：預計剩餘——與主控台相同樣式）
        info_row = ctk.CTkFrame(lf, fg_color="transparent")
        info_row.pack(fill="x", pady=(0, 4))
        
        self.toolbox_file_count_lbl = ctk.CTkLabel(
            info_row, text="進度：尚未開始",
            font=ctk.CTkFont(size=14), text_color="#888888",
            anchor="w"
        )
        self.toolbox_file_count_lbl.pack(side="left", fill="x", expand=True)
        
        self.toolbox_eta_lbl = ctk.CTkLabel(
            info_row, text="",
            font=ctk.CTkFont(size=14), text_color="#888888",
            anchor="e"
        )
        self.toolbox_eta_lbl.pack(side="right")
        
        # 工具箱日誌區與錯誤日誌區
        log_frame = ctk.CTkFrame(parent, fg_color="transparent")
        log_frame.pack(fill="both", expand=True, padx=20, pady=(5, 10))
        
        # 左右切分 (7:3)
        log_frame.grid_columnconfigure(0, weight=7)
        log_frame.grid_columnconfigure(1, weight=3)
        log_frame.grid_rowconfigure(1, weight=1)
        
        ctk.CTkLabel(log_frame, text="執行日誌", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.toolbox_log_box = ctk.CTkTextbox(log_frame, text_color="white")
        self.toolbox_log_box.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        
        ctk.CTkLabel(log_frame, text="錯誤報告", font=ctk.CTkFont(weight="bold")).grid(row=0, column=1, sticky="w", padx=(5, 0))
        self.toolbox_err_log_box = ctk.CTkTextbox(log_frame, text_color="#ff4444")
        self.toolbox_err_log_box.grid(row=1, column=1, sticky="nsew", padx=(5, 0))

    def _toolbox_stop(self) -> None:
        """中止當前進行中的任務"""
        self._toolbox_cancel_event.set()
        self._toolbox_pause_event.clear()  # 确保先解暫停，工作線才能讀取取消事件
        if self._toolbox_active_proc and self._toolbox_active_proc.poll() is None:
            try:
                self._toolbox_active_proc.terminate()
                self._toolbox_active_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._toolbox_active_proc.kill()
                self._toolbox_active_proc.wait()
            except Exception:
                pass
        self._toolbox_log("使用者已中止任務。")
        self.after(0, lambda: self.toolbox_stop_btn.configure(state="disabled"))
        self.after(0, lambda: self.toolbox_pause_btn.configure(state="disabled", text="暫停", fg_color="#3a5a3a"))

    def _toolbox_toggle_pause(self) -> None:
        """暫停 / 繼續任務（與主控台行為一致）"""
        if self._toolbox_pause_event.is_set():
            # 目前是暫停狀態 → 繼續
            self._toolbox_pause_event.clear()
            self.after(0, lambda: self.toolbox_pause_btn.configure(
                text="暫停", fg_color="#3a5a3a"))
            self._toolbox_log("[>>] 繼續執行...")
        else:
            # 目前是執行中 → 暫停
            self._toolbox_pause_event.set()
            self.after(0, lambda: self.toolbox_pause_btn.configure(
                text="繼續 (已暫停)", fg_color="#7a5500"))
            self._toolbox_log("[||] 已暫停，等待繼續...")

    def _toolbox_task_start(self) -> None:
        """任務開始時啟用兩顆按鈕、重置取消/暫停事件"""
        self._toolbox_cancel_event.clear()
        self._toolbox_pause_event.clear()
        self.after(0, lambda: self.tool_verify.btn_execute.configure(state="disabled"))
        self.after(0, lambda: self.tool_transcode.btn_execute.configure(state="disabled"))
        self.after(0, lambda: self.tool_concat.btn_execute.configure(state="disabled"))
        self.after(0, lambda: self.toolbox_stop_btn.configure(state="normal"))
        self.after(0, lambda: self.toolbox_pause_btn.configure(
            state="normal", text="暫停", fg_color="#3a5a3a"))
        self.after(0, lambda: self.toolbox_file_count_lbl.configure(text="進度：準備中...", text_color="#aaaaaa"))
        self.after(0, lambda: self.toolbox_eta_lbl.configure(text=""))
        self.after(0, lambda: self.toolbox_progress_bar.set(0))

    def _toolbox_task_end(self) -> None:
        """任務結束時禁用兩顆按鈕、確保暫停事件已清除"""
        self._toolbox_active_proc = None
        self._toolbox_pause_event.clear()
        self.after(0, lambda: self.tool_verify.btn_execute.configure(state="normal"))
        self.after(0, lambda: self.tool_transcode.btn_execute.configure(state="normal"))
        self.after(0, lambda: self.tool_concat.btn_execute.configure(state="normal"))
        self.after(0, lambda: self.toolbox_stop_btn.configure(state="disabled"))
        self.after(0, lambda: self.toolbox_pause_btn.configure(state="disabled", text="暫停", fg_color="#3a5a3a"))
        self.after(0, lambda: self.toolbox_file_count_lbl.configure(text="進度：已完成", text_color="#555"))
        self.after(0, lambda: self.toolbox_eta_lbl.configure(text=""))

    def _toolbox_log(self, msg: str) -> None:
        def _insert() -> None:
            from datetime import datetime
            self.toolbox_log_box.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n\n")
            self.toolbox_log_box.see("end")
        self.after(0, _insert)

    def _toolbox_log_err(self, msg: str) -> None:
        def _insert() -> None:
            from datetime import datetime
            self.toolbox_err_log_box.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n\n")
            self.toolbox_err_log_box.see("end")
        self.after(0, _insert)
        # 主日誌也同步記一行錯誤
        self._toolbox_log(f"[] {msg.splitlines()[0]}")

    def _toolbox_update_progress(
        self, pct: float, done: int, total: int, label: str, eta_sec: float | None = None
    ) -> None:
        """Update the two progress info labels to match the main console's format.
        pct: 0.0–1.0 overall progress
        label: short right-side detail e.g. '[1/34] filename.mov (36.5%)'
        """
        from datetime import timedelta
        def _upd() -> None:
            pct_pct = pct * 100
            self.toolbox_file_count_lbl.configure(
                text=f"進度：{pct_pct:.2f}%  {done}/{total}〃{label}",
                text_color="#aaaaaa")
            if eta_sec and eta_sec > 0:  # type: ignore[operator]
                self.toolbox_eta_lbl.configure(
                    text=f"預計剩餘：{str(timedelta(seconds=int(eta_sec)))}",
                    text_color="#aaaaaa")
            else:
                self.toolbox_eta_lbl.configure(text="")
        self.after(0, _upd)

    def _run_standalone_verify(self, pairs: list[tuple[str, str]], mode: str = "quick") -> None:
        if not pairs:
            self.show_error("路徑錯誤", "沒有設定任何比對組！")
            return
            
        for s, d in pairs:
            if not s or not d:
                self.show_error("路徑檢查失敗", "每一組來源都必須指定相對應的目標資料夾進行比對！")
                return
            
        self.toolbox_progress_bar.set(0)
        self.toolbox_progress_bar.configure(progress_color="#555")
        
        self.toolbox_log_box.delete("0.0", "end")
        self.toolbox_err_log_box.delete("0.0", "end")
        self._toolbox_log(f"--- 開始獨立校驗 ---")
        self._toolbox_log(f"共 {len(pairs)} 組比對任務")
        
        def _worker() -> None:
            try:
                total_err_count = 0
                for s_idx, (src_input, dst_input) in enumerate(pairs):
                    self._toolbox_log(f"\n[比對任務 {s_idx+1}/{len(pairs)}]")
                    self._toolbox_log(f"來源: {src_input}")
                    self._toolbox_log(f"目標: {dst_input}")
                    
                    # 掃描來源檔案
                    all_files: list[str] = []
                    if ";" in src_input or os.path.isfile(src_input):
                        for fpath in src_input.split(";"):
                            fpath = fpath.strip()
                            if fpath and os.path.isfile(fpath):
                                all_files.append(fpath)
                    elif os.path.isdir(src_input):
                        for root, _, files in os.walk(src_input):
                            for f in files:
                                all_files.append(os.path.join(root, f))
                    
                    # 決定是「資料夾對資料夾」還是「檔案對檔案」模式
                    dst_is_flat = not os.path.isdir(dst_input)  # dst 是直接指向一個或多個檔案
                    dst_files: list[str] = []
                    if dst_is_flat:
                        for fpath in dst_input.split(";"):
                            fpath = fpath.strip()
                            if fpath:
                                dst_files.append(fpath)
                    
                    total = len(all_files)
                    if total == 0:
                        self._toolbox_log("[!] 此組來源內沒有任何檔案，跳過。")
                        continue
                    self._toolbox_log(f"→ 掃描到 {total} 個檔案。開始{'快速（大小）' if mode == 'quick' else '進階（XXH64）'}比對...")
                    
                    err_count = 0
                    for i, src_abs in enumerate(all_files):
                        if dst_is_flat:
                            # 直接以位置對應：第i個來源對應第i個目標
                            if i >= len(dst_files):
                                self._toolbox_log_err(f"無對應目標: {os.path.basename(src_abs)} (目標列表只有 {len(dst_files)} 個)")
                                err_count += 1
                                continue
                            dst_abs = dst_files[i]
                            rel_path = os.path.basename(src_abs)
                        else:
                            # 資料夾模式：以相對路徑推算目標路徑
                            if os.path.isdir(src_input):
                                rel_path = os.path.relpath(src_abs, src_input)
                            else:
                                rel_path = os.path.basename(src_abs)
                            dst_abs = os.path.join(dst_input, rel_path)
                        
                        if not os.path.exists(dst_abs):
                            self._toolbox_log_err(f"目標檔案遺失: {rel_path} (在 {dst_input} 中找不到)")
                            err_count += 1
                            continue
                        
                        # ─ 快速比對：只比較檔案大小
                        if mode == "quick":
                            src_sz = os.path.getsize(src_abs)
                            dst_sz = os.path.getsize(dst_abs)
                            if src_sz == dst_sz:
                                self._toolbox_log(f"[OK] {rel_path}  ({src_sz:,} bytes)")
                            else:
                                self._toolbox_log_err(f"大小不符: {rel_path}\n      來源: {src_sz:,} bytes\n      目標: {dst_sz:,} bytes")
                                err_count += 1
                        # ─ 進階比對：XXH64 完整雜湊
                        else:
                            src_hash = AnentEngine.get_xxh64(src_abs)
                            dst_hash = AnentEngine.get_xxh64(dst_abs)
                            if src_hash == dst_hash:
                                self._toolbox_log(f"[OK] {rel_path} ({src_hash[:12]}...)")  # type: ignore[index]
                            else:
                                self._toolbox_log_err(f"Hash 不符: {rel_path}\n      來源: {src_hash}\n      目標: {dst_hash}")
                                err_count += 1
                            
                        pct = (i + 1) / total
                        self.after(0, lambda p=pct: self.toolbox_progress_bar.set(p))
                    
                    total_err_count += err_count
                
                self.after(0, lambda: self.toolbox_progress_bar.set(1.0))
                if total_err_count == 0:
                    self._toolbox_log("\n獨立校驗全部完成：所有比對組檔案皆完美吻合！")
                    self.show_info("校驗完成", "所有檔案皆完美吻合！")
                else:
                    self._toolbox_log_err(f"\n獨立校驗完成：共發現 {total_err_count} 個不符錯誤！")
                    self.show_error("校驗失敗", f"總共發現 {total_err_count} 個檔案不吻合，請仔細檢查錯誤報告。")
                    
            except Exception as e:
                import traceback
                self._toolbox_log_err(traceback.format_exc())
                self.show_error("核心錯誤", str(e))
                
        threading.Thread(target=_worker, daemon=True).start()
        
    def _run_standalone_transcode(self, src: str, dst: str) -> None:
        if not src or not dst:
            self.show_error("路徑錯誤", "來源與輸出資料夾都必須選擇！")
            return
            
        self.toolbox_progress_bar.set(0)
        self.toolbox_progress_bar.configure(progress_color="#d48a04")
        self.toolbox_log_box.delete("0.0", "end")
        self.toolbox_err_log_box.delete("0.0", "end")
        self._toolbox_log(f"--- 開始獨立 Proxy 轉檔 ---")
        self._toolbox_task_start()
        
        def _worker() -> None:
            try:
                # 收集所有支援的影片檔（統一以分號拆分，再逐一判斷檔案或資料夾）
                files = []
                for token in src.split(";"):
                    token = token.strip()
                    if not token:
                        continue
                    if os.path.isfile(token):
                        if token.lower().endswith(SUPPORTED_EXTS):
                            files.append(token)
                    elif os.path.isdir(token):
                        for root, _, fnames in os.walk(token):
                            for fname in sorted(fnames):
                                if fname.lower().endswith(SUPPORTED_EXTS):
                                    files.append(os.path.join(root, fname))
                            
                total = len(files)
                if total == 0:
                    self._toolbox_log("[!] 來源資料夾內未找到任何支援的影片檔。")
                    return
                    
                self._toolbox_log(f"共找到 {total} 個影片，準備轉檔...")
                err_count = 0
                
                for i, src_file in enumerate(files):
                    # ── 暫停等待（與主控台一致）
                    while self._toolbox_pause_event.is_set():
                        if self._toolbox_cancel_event.is_set():
                            break
                        time.sleep(0.1)
                    # ── 取消檢查
                    if self._toolbox_cancel_event.is_set():
                        break
                    
                    if os.path.isdir(src):
                        rel = os.path.relpath(src_file, src)
                        parent_dir = os.path.basename(os.path.dirname(src_file))
                    else:
                        rel = os.path.basename(src_file)
                        parent_dir = "" # 多選檔案時，直接放在目標根目錄
                        
                    base_name = os.path.splitext(os.path.basename(rel))[0]
                    
                    # 嘗試保持一層父資料夾結構，如果沒有父資料夾則直接丟在 dst
                    if parent_dir and os.path.isdir(src) and parent_dir != os.path.basename(src):
                        out_dir = os.path.join(dst, parent_dir)
                    else:
                        out_dir = dst
                        
                    os.makedirs(out_dir, exist_ok=True)
                    proxy_out = os.path.join(out_dir, f"{base_name}_proxy.mov")
                    
                    self._toolbox_log(f"[{i+1}/{total}] 處理: {os.path.basename(src_file)}")
                    
                    duration = self._get_video_duration(src_file)
                    cmd = [
                        "ffmpeg", "-y", "-nostdin",
                        "-i", src_file,
                        "-map", "0:v", "-map", "0:a?",
                        "-vf", "scale=trunc(oh*a/2)*2:720",
                        "-c:v", "prores_ks", "-profile:v", "1",
                        "-c:a", "copy",
                        "-progress", "pipe:1",
                        "-nostats",
                        proxy_out
                    ]
                    
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                    self._toolbox_active_proc = proc
                    t_start = time.time()
                    
                    for pline in (proc.stdout or []):
                        if self._toolbox_cancel_event.is_set():
                            proc.terminate()
                            break
                        pline = pline.strip()
                        if pline.startswith("out_time_ms="):
                            try:
                                ms = int(pline.split("=")[1])
                                if duration > 0:  # type: ignore[operator]
                                    frac = min(1.0, (ms / 1_000_000) / duration)
                                    base_pct = i / total
                                    slot_size = 1.0 / total
                                    curr_pct = base_pct + (frac * slot_size)
                                    self.after(0, lambda p=curr_pct: self.toolbox_progress_bar.set(p))
                                    # ETA 計算：根據本檔已經過的時間預估全長
                                    elapsed = time.time() - t_start
                                    if frac > 0.01 and elapsed > 0:  # type: ignore[operator]
                                        total_file_est = elapsed / frac  # type: ignore[operator]
                                        file_remaining = total_file_est * (1 - frac)  # type: ignore[operator]
                                        # 加上待處理檔的估算 (假設各檔平均耗時相同)
                                        files_left = total - i - 1  # type: ignore[operator]
                                        eta = file_remaining + files_left * total_file_est
                                    else:
                                        eta = None
                                    label = f"[{i+1}/{total}] {os.path.basename(src_file)} ({min(100.0, frac*100):.1f}%)"
                                    self._toolbox_update_progress(curr_pct, i + 1, total, f"轉檔  {label}", eta)
                            except Exception:
                                pass
                                
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                        
                    if self._toolbox_cancel_event.is_set():
                        self._toolbox_log(f"  {os.path.basename(src_file)} 已中止。")
                        # 刪除不完整的剖面檔
                        try:
                            if os.path.exists(proxy_out):
                                os.remove(proxy_out)
                                self._toolbox_log(f"  已刪除不完整檔案: {os.path.basename(proxy_out)}")
                        except Exception:
                            pass
                        break
                    if proc.returncode != 0:
                        self._toolbox_log_err(f"轉檔失敗: {os.path.basename(src_file)}")
                        err_count += 1  # type: ignore[operator]
                    else:
                        self._toolbox_log(f"  -> 完成: {os.path.basename(proxy_out)}")
                
                # ── 結束判斷
                if self._toolbox_cancel_event.is_set():
                    self._toolbox_log("程序已中止，尚未處理的檔案已略過。")
                    self.show_warning("程序中止", "任務已由使用者中止，已完成的檔案保留，其餘略過。")
                elif err_count == 0:  # type: ignore[operator]
                    self.after(0, lambda: self.toolbox_progress_bar.set(1.0))
                    self._toolbox_log("轉檔任務全部完成！")
                    self.show_info("轉檔完成", "所有影片 Proxy 已產生完畢！")
                else:
                    self.after(0, lambda: self.toolbox_progress_bar.set(1.0))
                    self._toolbox_log_err(f"任務結束，共發生 {err_count} 個錯誤。")  # type: ignore[operator]
                    self.show_error("任務結束", f"轉檔完成，但發生了 {err_count} 個錯誤。")
                    
            except Exception as e:
                import traceback
                self._toolbox_log_err(traceback.format_exc())
                self.show_error("核心錯誤", str(e))
            finally:
                self._toolbox_task_end()
                
        threading.Thread(target=_worker, daemon=True).start()

    def _run_standalone_concat(self, src: str, dst: str, custom_name: str = "", resolution: str = "1080P") -> None:
        if not src or not dst:
            self.show_error("路徑錯誤", "來源與輸出資料夾都必須選擇！")
            return
            
        self.toolbox_progress_bar.set(0)
        self.toolbox_progress_bar.configure(progress_color="#228b22")
        self.toolbox_log_box.delete("0.0", "end")
        self.toolbox_err_log_box.delete("0.0", "end")
        self._toolbox_log(f"--- 開始獨立串帶壓印 ---")
        self._toolbox_task_start()
        
        def _worker() -> None:
            try:
                # 收集影片檔（統一分號拆分，再逐一判斷檔案或資料夾，遞迴掃描）
                files = []
                for token in src.split(";"):
                    token = token.strip()
                    if not token:
                        continue
                    if os.path.isfile(token):
                        if token.lower().endswith(SUPPORTED_EXTS):
                            files.append(token)
                    elif os.path.isdir(token):
                        for root, _, fnames in os.walk(token):
                            for fname in sorted(fnames):
                                if fname.lower().endswith(SUPPORTED_EXTS):
                                    files.append(os.path.join(root, fname))    
                if not files:
                    self._toolbox_log("[!] 來源資料夾內未找到任何影片檔。")
                    return
                    
                self._toolbox_log(f"共找到 {len(files)} 個影片碎片，準備串聯...")
                
                os.makedirs(dst, exist_ok=True)
                concat_list = os.path.join(dst, "_standalone_concat_list.txt")
                with open(concat_list, "w", encoding="utf-8") as f:
                    for p in sorted(files):
                        # 防止單引號讓 FFmpeg 解析提早結束崩潰
                        safe_p = p.replace("'", "'\\''").replace("\\", "/")
                        f.write(f"file '{safe_p}'\n")
                        
                if custom_name:
                    folder_name = custom_name
                elif os.path.isdir(src):
                    folder_name = os.path.basename(src) or "Reel"
                else:
                    folder_name = "Standalone_Reel"
                reel_out = os.path.join(dst, f"{folder_name}.mov")
                
                self._toolbox_log(f"目標輸出: {os.path.basename(reel_out)}")
                
                scale_filter = ""
                if resolution == "720P":
                    scale_filter = "scale=trunc(oh*a/2)*2:720,"
                elif resolution == "1080P":
                    scale_filter = "scale=trunc(oh*a/2)*2:1080,"
                elif resolution == "Ultra HD":
                    scale_filter = "scale=trunc(oh*a/2)*2:2160,"
                
                # 動態抓取系統字型路徑
                win_dir = os.environ.get("WINDIR", "C:\\Windows")
                font_path = os.path.join(win_dir, "Fonts", "arial.ttf").replace("\\", "/")
                # FFmpeg 內的字串若含 : 需要做脫逸
                font_path = font_path.replace(":", "\\:")
                
                tc_filter = scale_filter + f"drawtext=fontfile='{font_path}':text='%{{pts\\:hms}}':x=w-tw-20:y=20:fontsize=48:fontcolor=white@0.5:box=1:boxcolor=black@0.25:boxborderw=6"
                
                concat_duration = sum(AnentApp._get_video_duration(p) for p in files)
                self._toolbox_log(f"總時長計算完畢: {concat_duration:.2f} 秒，啟動 FFmpeg...")
                
                cmd = [
                    "ffmpeg", "-y", "-nostdin",
                    "-f", "concat", "-safe", "0",
                    "-i", concat_list,
                    "-map", "0:v", "-map", "0:a?",
                    "-vf", tc_filter,
                    "-c:v", "prores_ks", "-profile:v", "1",
                    "-c:a", "copy",
                    "-progress", "pipe:1",
                    "-nostats",
                    reel_out
                ]
                
                err_log_file = os.path.join(dst, "_concat_err.log")
                f_err = open(err_log_file, "w", encoding="utf-8")
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=f_err, encoding="utf-8", errors="replace")
                self._toolbox_active_proc = proc
                t_concat_start = time.time()
                
                for cline in (proc.stdout or []):
                    if self._toolbox_cancel_event.is_set():
                        proc.terminate()
                        break
                    cline = cline.strip()
                    if cline.startswith("out_time_ms="):
                        try:
                            cms = int(cline.split("=")[1])
                            if concat_duration > 0:  # type: ignore[operator]
                                cfrac = min(1.0, (cms / 1_000_000) / concat_duration)  # type: ignore[operator]
                                self.after(0, lambda p=cfrac: self.toolbox_progress_bar.set(p))
                                elapsed = time.time() - t_concat_start
                                if cfrac > 0.01 and elapsed > 0:  # type: ignore[operator]
                                    eta = elapsed / cfrac * (1 - cfrac)  # type: ignore[operator]
                                else:
                                    eta = None
                                self._toolbox_update_progress(
                                    cfrac, 1, 1,
                                    f"串帶  {os.path.basename(reel_out)} ({min(100.0, cfrac*100):.1f}%)",
                                    eta
                                )
                        except Exception:
                            pass
                            
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    
                try:
                    os.remove(concat_list)
                except Exception:
                    pass
                try:
                    f_err.close()
                except:
                    pass
                    
                if self._toolbox_cancel_event.is_set():
                    self._toolbox_log("程序已中止。")
                    # 刪除不完整的串帶檔
                    try:
                        if os.path.exists(reel_out):
                            os.remove(reel_out)
                            self._toolbox_log(f"已刪除不完整串帶檔: {os.path.basename(reel_out)}")
                    except Exception:
                        pass
                    self.show_warning("程序中止", "任務已由使用者中止，不完整的串帶檔已删除。")
                elif proc.returncode != 0:
                    try:
                        with open(err_log_file, "r", encoding="utf-8") as fe:
                            err_txt = fe.read()
                    except Exception:
                        err_txt = "Unknown stderr"
                    if len(err_txt) > 500:
                        err_txt = "..." + str(err_txt)[-500:]  # type: ignore[index,arg-type]
                    self._toolbox_log_err(f"串帶失敗: \n{err_txt}")
                    self.show_error("串帶錯誤", "FFmpeg 執行失敗，請檢查錯誤報告。")
                else:
                    self.toolbox_progress_bar.set(1.0)
                    self._toolbox_log("串帶任務完成！")
                    self.show_info("串帶完成", "時間碼壓印串帶已成功產出！")
                    
            except Exception as e:
                import traceback
                self._toolbox_log_err(traceback.format_exc())
                self.show_error("核心錯誤", str(e))
            finally:
                try:
                    f_err.close()
                    if os.path.exists(err_log_file):
                        os.remove(err_log_file)
                except:
                    pass
                self._toolbox_task_end()
                
        threading.Thread(target=_worker, daemon=True).start()

    # ─────────────────────────────────────────────────────────
    # 路徑 / 記憶卡操作
    # ─────────────────────────────────────────────────────────
    def pick_folder(self, attr: str, label: str) -> None:
        p = filedialog.askdirectory(title=f"選擇{label}")
        if p:
            setattr(self, attr, p)
            self.path_labels[attr].configure(text=p)
            self._save_settings()

    def add_source(self) -> None:
        p = filedialog.askdirectory(title="選擇記憶卡資料夾")
        if not p:
            return
        if p in self.source_paths:
            self.show_warning("重複", f"此路徑已加入：\n{p}")
            return
        self.source_paths.append(p)
        default_name = f"Card_{chr(65 + len(self.source_names))}"
        self.source_names.append(default_name)
        self._add_card_row(p, default_name)
    def _add_card_row(self, path: str, name: str) -> None:
        idx = len(self.card_rows)
        # 新增第一筆時隱藏提示文字
        if idx == 0 and hasattr(self, '_drop_hint'):
            self._drop_hint.pack_forget()
        row = ctk.CTkFrame(self.card_scroll, fg_color=("#e8e8e8", "#2e2e2e"), corner_radius=6)
        row.pack(fill="x", pady=2)

        # 資料夾名稱輸入框
        name_var = ctk.StringVar(value=name)
        name_entry = ctk.CTkEntry(row, textvariable=name_var, width=120, placeholder_text="資料夾名")
        name_entry.pack(side="left", padx=(6, 4), pady=4)

        # 名稱變更時同步回 source_names
        def _on_name_change(*_: object, p: str = path, var: ctk.StringVar = name_var) -> None:
            if p in self.source_paths:
                curr_idx = self.source_paths.index(p)
                if curr_idx < len(self.source_names):
                    self.source_names[curr_idx] = var.get() or f"Card_{chr(65 + curr_idx)}"
        
        trace_id = name_var.trace_add("write", _on_name_change)
        # 紀錄 var 與 trace_id 準備未來註銷
        self.source_traces[path] = (name_var, trace_id)

        # 最右側按鈕區
        btn_frame = ctk.CTkFrame(row, fg_color="transparent")
        btn_frame.pack(side="right", padx=4, pady=4)

        # 刪除按鈕
        ctk.CTkButton(btn_frame, text="X", width=30, fg_color="#8b0000",
                      hover_color="#cc0000",
                      command=lambda p=path, r=row: self.remove_source(p, r)
                      ).pack(side="right", padx=(4, 0))

        # 更改路徑按鈕
        def _change_path(p: str = path) -> None:
            new_p = filedialog.askdirectory(title="選擇新的記憶卡資料夾", initialdir=p)
            if new_p:
                if new_p in self.source_paths and new_p != p:
                    self.show_warning("重複", f"此路徑已加入：\n{new_p}")
                    return
                # 更新 list 與 UI
                idx = self.source_paths.index(p)
                self.source_paths[idx] = new_p
                path_lbl.configure(text=new_p)
                # 重新綁定刪除與更改按鈕的預設參數 (hacky but works for UI replace)
                btn_frame.winfo_children()[0].configure(command=lambda: self.remove_source(new_p, row))  # delete btn
                btn_frame.winfo_children()[1].configure(command=lambda: _change_path(new_p))            # change btn
                
        # 建立更改按鈕（放在 btn_frame 左側，其實是倒過來 pack）
        ctk.CTkButton(btn_frame, text="更改", width=40, fg_color="#444", hover_color="#666",
                      command=_change_path).pack(side="right", padx=(0, 4))

        # 路徑標籤 (置右)
        path_lbl = ctk.CTkLabel(row, text=path, anchor="e", padx=8, text_color="#aaa",
                                fg_color="transparent")
        path_lbl.pack(side="right", fill="x", expand=True)
        self.card_rows.append(row)

    def remove_source(self, path: str, row_widget: ctk.CTkFrame) -> None:
        if path in self.source_paths:
            idx = self.source_paths.index(path)
            
            # 清除 Tkinter 變數監聽器，防止 Zombie Listener 佔用記憶體
            if hasattr(self, 'source_traces') and path in self.source_traces:
                var, t_id = self.source_traces[path]
                try:
                    var.trace_remove("write", t_id)
                except Exception:
                    pass
                self.source_traces.pop(path, None)  # type: ignore[call-overload]
                
            self.source_paths.pop(idx)
            if idx < len(self.source_names):
                self.source_names.pop(idx)
        row_widget.destroy()
        self.card_rows = [r for r in self.card_rows if r.winfo_exists()]
        # 清單清空時重新顯示拖放提示
        if not self.card_rows and hasattr(self, '_drop_hint'):
            self._drop_hint.pack(expand=True, pady=30)

    # ─────────────────────────────────────────────────────────
    # 設定持久化
    # ─────────────────────────────────────────────────────────
    def _bind_scroll_drop(self) -> None:
        """setup_ui 完成後，嘗試將拖放目標綁出 card_scroll"""
        if not _HAS_DND:
            return
        try:
            self.card_scroll.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self.card_scroll.dnd_bind('<<Drop>>', self._on_drop)  # type: ignore[attr-defined]
        except Exception:
            pass

    def _load_settings(self) -> None:
        try:
            with open(self._config_path, encoding="utf-8") as f:
                cfg: dict[str, str] = json.load(f)
            self.local_root = cfg.get("local_root", self.local_root)
            self.nas_root   = cfg.get("nas_root",   self.nas_root)
            self.proxy_root = cfg.get("proxy_root", self.proxy_root)
            if "build_folder" in cfg:
                self.project_name.set(cfg["build_folder"])
        except Exception:
            pass   # 第一次啟動或檔案損壞，使用預設值

    def _save_settings(self) -> None:
        try:
            cfg = {
                "local_root":   self.local_root,
                "nas_root":     self.nas_root,
                "proxy_root":   self.proxy_root,
                "build_folder": self.project_name.get(),
            }
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────
    # 拖放支援
    # ─────────────────────────────────────────────────────────
    def _on_drop(self, event: object) -> None:  # type: ignore[type-arg]
        raw: str = getattr(event, "data", "")
        # tkinterdnd2 在 Windows 上用 {} 包裹含空格路徑
        import re
        paths = re.findall(r"\{([^}]+)\}|(\S+)", raw)
        for grp in paths:
            p = (grp[0] or grp[1]).strip()
            if os.path.isdir(p) and p not in self.source_paths:
                self.source_paths.append(p)
                default_name = f"Card_{chr(65 + len(self.source_names))}"
                self.source_names.append(default_name)
                self._add_card_row(p, default_name)

    # ─────────────────────────────────────────────────────────
    # Windows 系統通知
    # ─────────────────────────────────────────────────────────
    def _notify_windows(self, title: str, body: str) -> None:
        ps = (
            f'Add-Type -AssemblyName System.Windows.Forms;'
            f'$n=[System.Windows.Forms.NotifyIcon]::new();'
            f'$n.Icon=[System.Drawing.SystemIcons]::Information;'
            f'$n.Visible=$true;'
            f'$n.ShowBalloonTip(6000,"{title}","{body}",[System.Windows.Forms.ToolTipIcon]::Info)'
        )
        try:
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────
    # ffprobe 取得影片時長（秒）
    # ─────────────────────────────────────────────────────────
    @staticmethod
    def _get_video_duration(path: str) -> float:
        """用 ffprobe 取得影片時長（秒），失敗回傳 0.0"""
        try:
            out = subprocess.check_output([
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path
            ], stderr=subprocess.DEVNULL, timeout=10)
            return float(out.strip())
        except Exception:
            return 0.0

    # ─────────────────────────────────────────────────────────
    # ETA 平滑化
    # ─────────────────────────────────────────────────────────
    def _smooth_eta(self, bytes_done: int, bytes_remaining: int, elapsed: float) -> float | None:
        """用最近 5 筆速度樣本的平均值計算 ETA"""
        if elapsed > 0 and bytes_done > 0:
            current_speed = bytes_done / elapsed   # bytes/sec（本次區間）
            self._speed_samples.append(current_speed)
        if not self._speed_samples or bytes_remaining <= 0:
            return None
        avg_speed = sum(self._speed_samples) / len(self._speed_samples)
        return bytes_remaining / avg_speed if avg_speed > 0 else None



    # ─────────────────────────────────────────────────────────
    # 執行緒安全 UI 更新
    # ─────────────────────────────────────────────────────────
    def log(self, msg: str) -> None:
        def _insert() -> None:
            self.log_box.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
            self.log_box.see("end")
        self.after(0, _insert)

    def log_error(self, msg: str) -> None:
        def _insert_err() -> None:
            self.error_log_box.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
            self.error_log_box.see("end")
        self.after(0, _insert_err)
        self.log(msg)  # 保持主要日誌也有紀錄

    def _save_job_log(self) -> None:
        """任務結束後將 UI 上的日誌存檔到專案素材區"""
        def _save() -> None:
            try:
                p_folder = self.project_name.get().strip() or datetime.now().strftime("%Y%m%d")
                local_dir = os.path.join(self.local_root, p_folder)
                if not os.path.isdir(local_dir):
                    os.makedirs(local_dir, exist_ok=True)
                
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                log_file = os.path.join(local_dir, f"MediaGuard_Log_{timestamp}.txt")
                
                main_log = self.log_box.get("0.0", "end").strip()
                err_log = self.error_log_box.get("0.0", "end").strip()
                
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write("=== Originsun Media Guard Pro 工作日誌 ===\n")
                    f.write(f"專案名稱: {p_folder}\n")
                    f.write(f"完成時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                    f.write("--- 執行日誌 ---\n")
                    f.write(main_log + "\n\n")
                    if err_log:
                        f.write("--- 錯誤紀錄 ---\n")
                        f.write(err_log + "\n")
                        
                self.log_box.insert("end", f"\n[{datetime.now().strftime('%H:%M:%S')}] [OK] 日誌已自動儲存至: \n{log_file}\n")
                self.log_box.see("end")
            except Exception as e:
                self.error_log_box.insert("end", f"\n[{datetime.now().strftime('%H:%M:%S')}] [X] 無法儲存工作日誌: {str(e)}\n")
                self.error_log_box.see("end")
        
        # 延遲 500ms 確保所有的 UI log 都已經印在畫面上再抓取
        self.after(500, _save)


    def show_info(self, title: str, message: str) -> None:
        self.after(0, lambda: self._custom_msgbox(title, message, "info"))

    def show_warning(self, title: str, message: str) -> None:
        self.after(0, lambda: self._custom_msgbox(title, message, "warning"))

    def show_error(self, title: str, message: str) -> None:
        self.after(0, lambda: self._custom_msgbox(title, message, "error"))

    def _custom_msgbox(self, title: str, message: str, m_type: str = "info") -> None:
        """自訂的深色系訊息方塊，包含 (info, warning, error)"""
        top = ctk.CTkToplevel(self)
        top.title(title)
        top.geometry("450x250")
        top.resizable(False, False)
        top.attributes("-topmost", True)
        top.grab_set()  # 模態對話框
        
        # 決定顏色
        if m_type == "error":
            color = "#ff4444"
        elif m_type == "warning":
            color = "#ffaa00"
        else:
            color = "#2a6cbf"
            
        lbl_title = ctk.CTkLabel(top, text=title, font=ctk.CTkFont(size=18, weight="bold"), text_color=color)
        lbl_title.pack(pady=(20, 10))
        
        # 為了容納多行訊息或 traceback，使用可滾動的 Textbox，並設為唯讀
        msg_box = ctk.CTkTextbox(top, width=400, height=100, wrap="word")
        msg_box.pack(padx=20, pady=5)
        msg_box.insert("0.0", message)
        msg_box.configure(state="disabled")

        btn_frame = ctk.CTkFrame(top, fg_color="transparent")
        btn_frame.pack(pady=10)

        # 方案三：錯誤的話多一個「複製詳細錯誤」按鈕
        if m_type == "error":
            def _copy_err() -> None:
                self.clipboard_clear()
                self.clipboard_append(message)
                btn_copy.configure(text="已複製！")
                top.after(2000, lambda: btn_copy.configure(text="複製詳細錯誤"))
                
            btn_copy = ctk.CTkButton(btn_frame, text="複製詳細錯誤", fg_color="#333", hover_color="#555", command=_copy_err)
            btn_copy.pack(side="left", padx=10)

        btn_ok = ctk.CTkButton(btn_frame, text="確定", command=top.destroy, width=100)
        btn_ok.pack(side="left", padx=10)

    def ask_yes_no(self, title: str, message: str, yes_text: str="是", no_text: str="否", on_yes: object=None) -> None:
        """非阻塞式的自訂 Yes/No 詢問對話框"""
        top = ctk.CTkToplevel(self)
        top.title(title)
        top.geometry("400x200")
        top.resizable(False, False)
        top.attributes("-topmost", True)
        top.grab_set()

        ctk.CTkLabel(top, text=title, font=ctk.CTkFont(size=18, weight="bold"), text_color="#ffaa00").pack(pady=(20, 10))
        ctk.CTkLabel(top, text=message, font=ctk.CTkFont(size=14), wraplength=350).pack(pady=5)

        btn_frame = ctk.CTkFrame(top, fg_color="transparent")
        btn_frame.pack(pady=(15, 10))

        def _on_yes() -> None:
            top.destroy()
            if callable(on_yes):
                on_yes()

        ctk.CTkButton(btn_frame, text=yes_text, width=100, fg_color="#1f538d", hover_color="#2a6cbf", command=_on_yes).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text=no_text, width=100, fg_color="#555", hover_color="#777", command=top.destroy).pack(side="left", padx=10)

    def toggle_detail_progress(self) -> None:
        self._is_detail_open = not self._is_detail_open
        if self._is_detail_open:
            self.detail_btn.configure(text="⬆ 收起詳細")
            self.detail_prog_frame.pack(fill="x", pady=(0, 8))
        else:
            self.detail_btn.configure(text="⬇ 詳細進度")
            self.detail_prog_frame.pack_forget()

    def set_progress(self, value: float, done: int, total: int,
                     eta_sec: float | None = None,
                     label: str | None = None,
                     phase: str = "backup") -> None:
        bar_val = min(1.0, self._prog_offset + value * self._prog_scale)
        def _update() -> None:
            # 依據 phase 設定顏色與子進度條數值
            if phase == "backup":
                self.progress_bar.configure(progress_color="#1f538d")
                self.bar_backup.set(value)
                self.lbl_backup.configure(text=f"{value*100:.0f}%")
            elif phase == "transcode":
                self.progress_bar.configure(progress_color="#d48a04")
                self.bar_backup.set(1.0); self.lbl_backup.configure(text="100%")
                self.bar_trans.set(value)
                self.lbl_trans.configure(text=f"{value*100:.0f}%")
            elif phase == "concat":
                self.progress_bar.configure(progress_color="#228b22")
                self.bar_backup.set(1.0); self.lbl_backup.configure(text="100%")
                self.bar_trans.set(1.0); self.lbl_trans.configure(text="100%")
                self.bar_concat.set(value)
                self.lbl_concat.configure(text=f"{value*100:.0f}%")

            self.progress_bar.set(bar_val)
            pct = bar_val * 100
            if label:
                self.file_count_lbl.configure(
                    text=f"進度：{pct:.2f}%  {done}/{total}〃{label}")
            else:
                self.file_count_lbl.configure(
                    text=f"進度：{done} / {total} 個項目  ({pct:.2f}%)")
            if eta_sec is not None and eta_sec > 0:
                self.eta_lbl.configure(
                    text=f"預計剩餘：{str(timedelta(seconds=int(eta_sec)))}")
            else:
                self.eta_lbl.configure(text="")
        self.after(0, _update)

    def reset_progress(self) -> None:
        self.after(0, lambda: [
            self.progress_bar.set(0),
            self.progress_bar.configure(progress_color="#1f538d"),
            self.bar_backup.set(0), self.lbl_backup.configure(text="0%"),
            self.bar_trans.set(0),  self.lbl_trans.configure(text="0%"),
            self.bar_concat.set(0), self.lbl_concat.configure(text="0%"),
            self.file_count_lbl.configure(text="進度：尚未開始"),
            self.eta_lbl.configure(text="")
        ])

    # ─────────────────────────────────────────────────────────
    # 衝突對話框（執行緒安全）
    # ─────────────────────────────────────────────────────────

    def _ask_conflict_verify(self, rel_path: str, reason: str,  # type: ignore[return]
                              show_verify: bool = True) -> str:
        """對話框：覆蓋 / 全部覆蓋 / 略過 / 全部略過 [/ XXH64 校驗]
        回傳: 'overwrite' | 'overwrite_all' | 'skip' | 'skip_all' | 'verify'
        """
        if self._overwrite_all is True:
            return "overwrite"
        if self._overwrite_all is False:
            return "skip"

        self._conflict_event.clear()

        def _show() -> None:
            from tkinter import Toplevel, Label
            win = Toplevel(self)
            win.title("檔案衝突")
            win.grab_set()
            win.resizable(False, False)

            body = f"目標已有同名檔案，內容可能不同：\n  {rel_path}\n\n{reason}\n\n請選擇處理方式："
            Label(win, text=body, justify="left",
                  padx=20, pady=12, wraplength=500).pack()

            btn_frame = ctk.CTkFrame(win, fg_color="transparent")
            btn_frame.pack(pady=(0, 12))

            def choose(result: str) -> None:
                if result == "overwrite_all":
                    self._overwrite_all = True
                elif result == "skip_all":
                    self._overwrite_all = False
                self._conflict_result = result
                win.destroy()
                self._conflict_event.set()

            ctk.CTkButton(btn_frame, text="覆蓋此檔", width=100,
                          fg_color="#1f538d",
                          command=lambda: choose("overwrite")).pack(side="left", padx=4)
            ctk.CTkButton(btn_frame, text="全部覆蓋", width=100, fg_color="#7a5500",
                          command=lambda: choose("overwrite_all")).pack(side="left", padx=4)
            ctk.CTkButton(btn_frame, text="略過此檔", width=100, fg_color="#444",
                          command=lambda: choose("skip")).pack(side="left", padx=4)
            ctk.CTkButton(btn_frame, text="全部略過", width=100, fg_color="#444",
                          command=lambda: choose("skip_all")).pack(side="left", padx=4)
            ctk.CTkButton(btn_frame, text="更改檔名", width=100, fg_color="#1a4d1a",
                          command=lambda: choose("rename")).pack(side="left", padx=4)
            if show_verify:
                ctk.CTkButton(btn_frame, text="XXH64 校驗", width=110,
                              fg_color="#1a4d1a",
                              command=lambda: choose("verify")).pack(side="left", padx=4)

        self.after(0, _show)
        self._conflict_event.wait()
        return self._conflict_result

    def _ask_hash_conflict(self, rel_path: str, detail: str) -> str:
        """對話框： XXH64 確認內容不同時的處理
        回傳: 'overwrite' | 'skip' | 'rename'
        """
        self._conflict_event.clear()

        def _show() -> None:
            from tkinter import Toplevel, Label
            win = Toplevel(self)
            win.title("XXH64 校驗 — 內容不同")
            win.grab_set()
            win.resizable(False, False)

            body = (
                f"XXH64 校驗確認檔案內容不同：\n  {rel_path}\n\n"
                f"{detail}\n\n請選擇處理方式："
            )
            Label(win, text=body, justify="left",
                  padx=20, pady=12, wraplength=500).pack()

            btn_frame = ctk.CTkFrame(win, fg_color="transparent")
            btn_frame.pack(pady=(0, 12))

            def choose(result: str) -> None:
                self._conflict_result = result
                win.destroy()
                self._conflict_event.set()

            ctk.CTkButton(btn_frame, text="覆蓋目標", width=110,
                          fg_color="#1f538d",
                          command=lambda: choose("overwrite")).pack(side="left", padx=5)
            ctk.CTkButton(btn_frame, text="略過此檔", width=110, fg_color="#444",
                          command=lambda: choose("skip")).pack(side="left", padx=5)
            ctk.CTkButton(btn_frame, text="更改檔名", width=110, fg_color="#1a4d1a",
                          command=lambda: choose("rename")).pack(side="left", padx=5)

        self.after(0, _show)
        self._conflict_event.wait()
        return self._conflict_result  # type: ignore[return-value]

    @staticmethod
    def _find_rename_dest(dest: str) -> str:  # type: ignore[return]
        """尋找可用的更改檔名目標：檔名(1).ext, (2).ext ..."""
        base, ext = os.path.splitext(dest)
        n = 1
        while True:
            candidate = f"{base}({n}){ext}"
            if not os.path.exists(candidate):
                return candidate
            n += 1

    def _ask_skip_or_verify(self, rel_path: str) -> str:
        """步驟③輕量對話框：大小/時間均相同時，讓使用者決定是否執行 XXH64
        回傳: 'skip' | 'verify'
        """
        self._conflict_event.clear()

        def _show() -> None:
            from tkinter import Toplevel, Label
            win = Toplevel(self)
            win.title("大小與時間相同")
            win.grab_set()
            win.resizable(False, False)

            Label(win,
                  text=f"此檔案大小與修改時間均相同：\n  {rel_path}\n\n"
                       f"可直接略過（信任 Metadata），\n或執行 XXH64 校驗確認內容一致。\n\n請選擇：",
                  justify="left", padx=20, pady=12, wraplength=460).pack()

            btn_frame = ctk.CTkFrame(win, fg_color="transparent")
            btn_frame.pack(pady=(0, 12))

            def choose(result: str) -> None:
                self._conflict_result = result
                win.destroy()
                self._conflict_event.set()

            ctk.CTkButton(btn_frame, text="安靜略過", width=120, fg_color="#444",
                          command=lambda: choose("skip")).pack(side="left", padx=6)
            ctk.CTkButton(btn_frame, text="XXH64 校驗", width=120, fg_color="#1a4d1a",
                          command=lambda: choose("verify")).pack(side="left", padx=6)

        self.after(0, _show)
        self._conflict_event.wait()
        return self._conflict_result  # type: ignore[return-value]  # set by _show before event

    # ─────────────────────────────────────────────────────────
    # 執行控制
    # ─────────────────────────────────────────────────────────
    def start_thread(self) -> None:
        if not self.source_paths:
            self.show_warning("提示", "請先新增至少一個來源記憶卡！")
            return

        is_resume = self._can_resume
        self._stop_event.clear()
        self._pause_event.clear()
        self._overwrite_all = None   # 每次開始重置「全部覆蓋/略過」狀態

        if not is_resume:
            self._processed_files.clear()
            self.reset_progress()
            
        self._current_backup_dests.clear()
        self._can_resume = False
        self.after(0, lambda: (
            self.start_btn.configure(state="disabled", text="執行中..."),
            self.pause_btn.configure(state="normal", text="暫停"),
            self.stop_btn.configure(state="normal")
        ))
        threading.Thread(target=self.main_workflow, daemon=True).start()

    def toggle_pause(self) -> None:
        if self._pause_event.is_set():
            self._pause_event.clear()
            self.pause_btn.configure(text="暫停")
            self.log("[>>] 繼續執行...")
        else:
            self._pause_event.set()
            self.pause_btn.configure(text="繼續 (已暫停)")
            self.log("[||] 已暫停，等待繼續...")

    def request_stop(self) -> None:
        def _do_stop() -> None:
            self._stop_event.set()
            self._pause_event.clear()
            # 若對話框正在等待，解除阻塞
            self._overwrite_result = False
            self._overwrite_event.set()
            self._conflict_result = "skip"
            self._conflict_event.set()
            # 立即殺死當前 FFmpeg 子進程
            proc_ref = self._current_proc
            if proc_ref is not None:
                try:
                    proc_ref.kill()
                except Exception:
                    pass
                self._current_proc = None
            # 刪除未完成的輸出檔案
            out_ref = self._current_output_file
            if out_ref and os.path.exists(out_ref):
                try:
                    os.remove(out_ref)
                    self.log(f"[X] 已刪除: {os.path.basename(out_ref)}")
                except Exception:
                    pass
                    
            # 刪除備份到一半的檔案
            for b_out in self._current_backup_dests:
                if b_out and os.path.exists(b_out):
                    try:
                        os.remove(b_out)
                        self.log(f"[X] 已清理未完整備份檔: {os.path.basename(b_out)}")
                    except Exception:
                        pass
            
            self.log("[X] 已中止！等待處理強制退出...")
            self._finish_controls(stopped=True)

        self.ask_yes_no("確認中止",
                        "確定要立即中止所有動作嗎？\n"
                        "未完成的輸出檔案將被刪除。\n"
                        "（已備份的完整檔案不受影響）",
                        yes_text="中止", no_text="取消",
                        on_yes=_do_stop)

    def _check_pause_stop(self) -> bool:
        """工作迴圈中呼叫；被中止則回傳 True"""
        while self._pause_event.is_set():
            time.sleep(0.5)
            if self._stop_event.is_set():
                return True
        return self._stop_event.is_set()

    def _finish_controls(self, stopped: bool = False) -> None:
        def _update() -> None:
            self._can_resume = False
            self.start_btn.configure(state="normal", text="開始")
            if stopped:
                self._processed_files.clear()

            self.pause_btn.configure(state="disabled", text="暫停")
            self.stop_btn.configure(state="disabled")
        self.after(0, _update)

    # ─────────────────────────────────────────────────────────
    # 核心工作流程
    # ─────────────────────────────────────────────────────────
    def main_workflow(self) -> None:
        stopped_mid = False
        try:
            p_folder = self.project_name.get().strip() or datetime.now().strftime("%Y%m%d")

            local_path = os.path.join(self.local_root, p_folder)
            nas_path   = os.path.join(self.nas_root,   p_folder)
            proxy_path = os.path.join(self.proxy_root, p_folder)

            os.makedirs(local_path, exist_ok=True)
            os.makedirs(nas_path,   exist_ok=True)
            os.makedirs(proxy_path, exist_ok=True)

            self.log(f"專案資料夾: {p_folder}")

            # 計算進度條機年（備份造尶1，轉檔+串帶依選項加權）
            _has_transcode = self.c1.get()
            _has_concat    = self.c2.get()
            # 備份: 40–60–70‥100％ 依实際選項分配
            if _has_transcode and _has_concat:
                backup_scale = 0.40;  transcode_scale = 0.40;  concat_scale = 0.20
            elif _has_transcode:
                backup_scale = 0.50;  transcode_scale = 0.50;  concat_scale = 0.0
            elif _has_concat:
                backup_scale = 0.70;  transcode_scale = 0.0;   concat_scale = 0.30
            else:
                backup_scale = 1.0;   transcode_scale = 0.0;   concat_scale = 0.0
            # 備存供後處理使用
            self._transcode_scale = transcode_scale
            self._concat_scale    = concat_scale
            self._backup_scale    = backup_scale

            # 備份階段護 offset/scale
            self._prog_offset = 0.0
            self._prog_scale  = backup_scale

            # ── 掃描所有來源（完整目錄結構，所有檔案類型）
            # all_items: (card_name, rel_path, src_file_abs)
            all_items: list[tuple[str, str, str]] = []
            for i, src_root in enumerate(self.source_paths):
                card = self.source_names[i] if i < len(self.source_names) else f"Card_{chr(65 + i)}"
                for dirpath, _, filenames in os.walk(src_root):
                    for fname in filenames:
                        src_abs = os.path.join(dirpath, fname)
                        # 修復：如果 rel 產生含有不預期前綴斜線或是不良格式，強制去掉開頭的斜線或磁碟機代號殘留
                        rel = os.path.relpath(src_abs, src_root)
                        if rel.startswith(os.sep):
                            rel = rel[len(os.sep):]
                        if rel == ".":
                            rel = os.path.basename(src_abs)
                        # 如果 src_root 是 "U:\"，rel 可能會變成錯的絕對路徑，確保清理
                        rel = rel.lstrip("\\/")
                        
                        all_items.append((card, rel, src_abs))

            total = len(all_items)
            if total == 0:
                self.log("[!] 所有來源資料夾均為空。")
                self.show_info("ANENT", "未找到任何檔案。")
                return

            # 以檔案大小加權計算進度 (並包裝 try-except 以防系統檔 os.path.getsize 報錯)
            total_bytes: int = 0
            for _, _, s in all_items:
                try:
                    total_bytes += os.path.getsize(s)  # type: ignore[operator]
                except OSError as e:
                    self.log_error(f"[!] 無法取得檔案大小，略過: {s} -> {e}")

            if total_bytes == 0:
                total_bytes = 1  # 防影 /0
            
            done_bytes: int = 0

            # 計算已完成數量與 bytes（繼續執行模式）
            already_done_bytes: int = 0
            for k, _, s in all_items:
                # 重建當時儲存的 item_key
                try:
                    src_idx = self.source_names.index(k) if k in self.source_names else -1
                    root_p = self.source_paths[src_idx] if src_idx >= 0 else ""
                    rel_p = os.path.relpath(s, root_p) if root_p else ""
                    item_key = f"{k}/{rel_p}"
                except Exception:
                    continue
                
                if item_key in self._processed_files and os.path.exists(s):
                    try:
                        already_done_bytes += os.path.getsize(s)
                    except OSError:
                        pass

            already_done = len(self._processed_files)
            any_error = False
            t_start = time.time()

            self.log(f"共 {total} 個項目" +
                     (f"，已完成 {already_done} 個，繼續剩餘 {total - already_done} 個..." if already_done else "，開始備份..."))

            done_bytes = already_done_bytes   # 繼續執行模式下已完成的 bytes
            done = already_done

            for card, rel, src_abs in all_items:
                self._current_backup_dests.clear()
                
                # 已處理過則略過（繼續執行模式）
                item_key = f"{card}/{rel}"
                if item_key in self._processed_files:
                    continue

                # 暫停 / 中止檢查
                if self._check_pause_stop():
                    stopped_mid = True
                    break

                # 計算目標路徑（保留完整子目錄結構）
                l_dest = os.path.join(local_path, card, rel)
                n_dest = os.path.join(nas_path,   card, rel)

                try:
                    os.makedirs(os.path.dirname(l_dest), exist_ok=True)
                except Exception as _e:
                    self.log_error(f"[!] 建立本機目錄失敗: {os.path.dirname(l_dest)} → {_e}")
                    raise
                skip_nas = False
                if self.nas_root and n_dest:
                    try:
                        os.makedirs(os.path.dirname(n_dest), exist_ok=True)
                    except Exception as _e:
                        self.log_error(f"[!] 建立 NAS 目錄失敗: {os.path.dirname(n_dest)} → {_e} (仍將繼續本機備份)")
                        skip_nas = True

                # ── 5步驟重複偵測 (L2 大小/時間 + L4 XXH64)
                skip_file    = False
                auto_skipped = False
                rename_l: str | None = None
                rename_n: str | None = None

                for dest in (l_dest, n_dest):
                    if not os.path.exists(dest):
                        continue

                    # ① 檔名相同，檢查大小
                    src_size  = os.path.getsize(src_abs)
                    dest_size = os.path.getsize(dest)

                    if src_size != dest_size:
                        # 大小不同，疑似損壞 → 覆蓋/略過（無 XXH64 選項）
                        sign   = "+" if dest_size > src_size else ""
                        reason = (
                            f"大小差異（疑似損壞的舊備份）\n"
                            f"  來源：{src_size:,} bytes\n"
                            f"  目標：{dest_size:,} bytes  ({sign}{dest_size - src_size:,})"
                        )
                        self.log(f"[!] 同名但大小不同: {card}/{rel}")
                        action = self._ask_conflict_verify(
                            f"{card}/{rel}", reason, show_verify=False)
                        if action in ("skip", "skip_all"):
                            skip_file = True
                        elif action == "rename":
                            rename_l = self._find_rename_dest(l_dest)
                            rename_n = self._find_rename_dest(n_dest)
                        # overwrite / overwrite_all → 直接複製（do nothing）
                        break

                    # ② 大小相同，檢查修改時間 (±2秒容差)
                    src_mtime  = os.path.getmtime(src_abs)
                    dest_mtime = os.path.getmtime(dest)
                    mtime_diff = abs(src_mtime - dest_mtime)

                    if mtime_diff > 2.0:
                        # 時間不同 → 覆蓋/略過/XXH64
                        from datetime import datetime as _dt
                        try:
                            src_t  = _dt.fromtimestamp(src_mtime).strftime("%Y-%m-%d %H:%M:%S")
                        except OSError:
                            src_t  = "<無效或過長的時間戳>"
                        try:
                            dest_t = _dt.fromtimestamp(dest_mtime).strftime("%Y-%m-%d %H:%M:%S")
                        except OSError:
                            dest_t = "<無效或過長的時間戳>"
                        reason = (
                            f"修改時間不同（大小相同）\n"
                            f"  來源：{src_t}\n"
                            f"  目標：{dest_t}\n"
                            f"  差異：{mtime_diff:.1f} 秒"
                        )
                        self.log(f"[!] 同名同大小但時間不同: {card}/{rel}")
                        action = self._ask_conflict_verify(f"{card}/{rel}", reason)
                        if action in ("skip", "skip_all"):
                            skip_file = True
                        elif action == "rename":
                            rename_l = self._find_rename_dest(l_dest)
                            rename_n = self._find_rename_dest(n_dest)
                        elif action == "verify":  # 計算 XXH64
                            self.log(f"[校驗] 計算 XXH64: {card}/{rel}")
                            src_h = AnentEngine.get_xxh64(src_abs)
                            dst_h = AnentEngine.get_xxh64(dest)
                            if src_h == dst_h:
                                self.log(f"[==] XXH64 相同，安靜略過: {card}/{rel}  ({src_h[:12]}...)")  # type: ignore[index]
                                auto_skipped = True
                            else:
                                detail = (
                                    f"來源 Hash：{src_h[:16]}...\n"  # type: ignore[index]
                                    f"目標 Hash：{dst_h[:16]}..."   # type: ignore[index]
                                )
                                act2 = self._ask_hash_conflict(f"{card}/{rel}", detail)
                                if act2 == "skip":
                                    skip_file = True
                                elif act2 == "rename":
                                    rename_l = self._find_rename_dest(l_dest)
                                    rename_n = self._find_rename_dest(n_dest)
                        # overwrite / overwrite_all → 直接複製
                        break

                    # ③ 大小與時間均相同 → 信任 Metadata，安靜略過
                    self.log(f"[==] 大小/時間相同，安靜略過: {card}/{rel}")
                    auto_skipped = True
                    break

                # 處理結果
                file_bytes = os.path.getsize(src_abs) if os.path.exists(src_abs) else 0
                if skip_file:
                    done += 1  # type: ignore[operator]
                    done_bytes += file_bytes
                    self._processed_files.add(item_key)
                    elapsed = time.time() - t_start
                    pct = done_bytes / total_bytes  # type: ignore[operator]
                    spd = done_bytes - already_done_bytes  # type: ignore[operator]
                    eta: float | None = (elapsed / spd) * (total_bytes - done_bytes) if spd > 0 else None  # type: ignore[operator]
                    self.set_progress(pct, done, total, eta)
                    continue

                if auto_skipped:
                    done += 1  # type: ignore[operator]
                    done_bytes += file_bytes
                    self._processed_files.add(item_key)
                    elapsed = time.time() - t_start
                    pct = done_bytes / total_bytes  # type: ignore[operator]
                    spd = done_bytes - already_done_bytes  # type: ignore[operator]
                    eta = (elapsed / spd) * (total_bytes - done_bytes) if spd > 0 else None  # type: ignore[operator]
                    self.set_progress(pct, done, total, eta)
                    continue

                # 更改檔名：更新目標路徑
                if rename_l and rename_n:
                    l_dest = rename_l
                    n_dest = rename_n
                    self.log(f"[更名] 備份為: {os.path.basename(l_dest)}")



                # 等待檔案穩定（僅影片格式）
                if src_abs.lower().endswith(SUPPORTED_EXTS):
                    retry = 0
                    while not AnentEngine.is_file_stable(src_abs):
                        if self._check_pause_stop():
                            stopped_mid = True
                            break
                        retry += 1
                        self.log(f"等待檔案釋放 ({retry}): {rel}")
                        time.sleep(3)
                        if retry > 20:
                            self.log_error(f"[X] 等待超時，跳過: {rel}")
                            any_error = True
                            break
                    if stopped_mid:
                        break

                # 分塊複製——每寫入 CHUNK_SIZE bytes 更新一次進度；中止時立即停手
                CHUNK = 4 * 1024 * 1024   # 4 MB per chunk

                def _copy_chunked(src: str, dst: str, is_recheck: bool = False) -> bool:
                    """Copy src → dst in chunks. Returns False if aborted mid-copy."""
                    if dst not in self._current_backup_dests:
                        self._current_backup_dests.append(dst)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
                        copied = 0
                        while True:
                            # 暫停或中止檢查
                            if self._check_pause_stop():
                                return False
                            chunk = fsrc.read(CHUNK)
                            if not chunk:
                                break
                            fdst.write(chunk)
                            copied += len(chunk)  # type: ignore[operator]
                            chunk_done = done_bytes + copied
                            pct2 = chunk_done / total_bytes
                            rem2 = total_bytes - chunk_done
                            elapsed2 = time.time() - t_start
                            
                            # 計算全局平均速度 (MB/s) 以及預估剩餘時間 (ETA)
                            speed_mb = 0.0
                            eta2 = None
                            if elapsed2 > 0:
                                speed_bps = (chunk_done - already_done_bytes) / elapsed2
                                speed_mb = speed_bps / (1024 * 1024)
                                if speed_bps > 0:
                                    eta2 = rem2 / speed_bps
                                
                            if is_recheck:
                                # 二次掃描專屬獨立回報 (不去觸碰主進度的 100%)
                                self.set_progress(1.0, total, total, eta2,
                                                  label=f"[錯誤重傳] {card}/{os.path.basename(rel)} "
                                                        f"({copied / file_bytes * 100:.1f}%) - {speed_mb:.1f} MB/s")
                            else:
                                self.set_progress(pct2, done + 1, total, eta2,
                                                  label=f"[備份] {card}/{os.path.basename(rel)} "
                                                        f"({copied / file_bytes * 100:.1f}%) - {speed_mb:.1f} MB/s")
                    return True

                try:
                    import shutil
                    free_space = shutil.disk_usage(os.path.dirname(l_dest)).free
                    if file_bytes > free_space:
                        self.log_error(f"[X] 目標磁碟空間不足！需要: {file_bytes / (1024**3):.2f} GB，剩餘: {free_space / (1024**3):.2f} GB")
                        self.show_error("空間不足", "目標磁碟空間不足，無法繼續本機備份！")
                        stopped_mid = True
                        break

                    if not _copy_chunked(src_abs, l_dest):  # type: ignore[arg-type]
                        stopped_mid = True
                        break
                except OSError as _e:
                    if _e.errno == 28 or getattr(_e, 'winerror', 0) == 112:
                        self.log_error(f"[X] 備份途中磁碟空間耗盡！ ({_e})")
                        self.show_error("空間不足", "寫入本機時發生空間不足錯誤，已中止任務！")
                        stopped_mid = True
                        break
                    self.log_error(f"[skip] 無法讀取來源，略過: {card}/{rel}  ({_e})")
                    any_error = True
                    continue

                if not skip_nas and self.nas_root and n_dest:
                    try:
                        import shutil
                        free_nas = shutil.disk_usage(os.path.dirname(n_dest)).free
                        if file_bytes > free_nas:
                            self.log_error(f"[!] NAS 空間不足，略過此檔的 NAS 備份")
                        elif not _copy_chunked(src_abs, n_dest):  # type: ignore[arg-type]
                            stopped_mid = True
                            break
                    except OSError as _e:
                        if _e.errno == 28 or getattr(_e, 'winerror', 0) == 112:
                            self.log_error(f"[!] NAS 磁碟空間耗盡，後續檔案放棄 NAS 備份！")
                            skip_nas = True
                        else:
                            self.log_error(f"[!] NAS 複製失敗（本機已完成）: {card}/{rel} → {_e}")
                    except Exception as _e:
                        self.log_error(f"[!] NAS 複製失敗（本機已完成）: {card}/{rel} → {_e}")
                        
                import shutil as _shutil
                try:
                    _shutil.copystat(src_abs, str(l_dest))
                    if not skip_nas and self.nas_root and n_dest:
                        _shutil.copystat(src_abs, str(n_dest))
                except Exception:
                    pass


                # XXH64 校驗（若勾選）
                if self.c4.get():
                    src_hash   = AnentEngine.get_xxh64(src_abs)
                    local_hash = AnentEngine.get_xxh64(l_dest)
                    nas_hash   = AnentEngine.get_xxh64(n_dest)  # type: ignore[arg-type]
                    hash_preview: str = src_hash[:12]  # type: ignore[index]
                    if src_hash == local_hash == nas_hash:
                        self.log(f"[OK] {card}/{rel}  ({hash_preview}...)")
                    else:
                        self.log_error(f"[X] 複製中止或失敗: {card}/{rel}")
                        any_error = True
                else:
                    self.log(f"[OK] {card}/{rel}  (已複製，略過校驗)")

                self._processed_files.add(item_key)

                # 對齊檔案完成 → ETA 平滑 + bytes 進度
                done += 1  # type: ignore[operator]
                done_bytes += file_bytes  # type: ignore[operator]
                elapsed = time.time() - t_start
                pct = done_bytes / total_bytes  # type: ignore[operator]
                remaining = total_bytes - done_bytes  # type: ignore[operator]
                eta = self._smooth_eta(int(file_bytes), int(remaining), elapsed)  # type: ignore[arg-type]
                self.set_progress(pct, done, total, eta,
                                  label=f"[備份] {card}/{os.path.basename(rel)}")

            # ── 備份後快速二次掃描 (檔名與大小) ──
            if not stopped_mid:
                self.log("[>>] 執行快速二次掃描 (檢查檔名與大小)...")
                self.after(0, lambda: self.eta_lbl.configure(text="二次檢查中..."))
                mismatch_count = 0
                for card, rel, src_abs in all_items:
                    if self._check_pause_stop():
                        stopped_mid = True
                        break
                    
                    l_dest = os.path.join(local_path, card, rel)
                    n_dest = os.path.join(nas_path,   card, rel)
                    try:
                        src_size = os.path.getsize(src_abs)
                    except OSError:
                        continue  # 系統檔略過
                    
                    need_recopy_l = False
                    need_recopy_n = False
                    
                    if not os.path.exists(l_dest) or os.path.getsize(l_dest) != src_size:
                        need_recopy_l = True
                    if self.nas_root and n_dest and (not os.path.exists(n_dest) or os.path.getsize(n_dest) != src_size):
                        need_recopy_n = True
                        
                    if need_recopy_l or need_recopy_n:
                        mismatch_count += 1  # type: ignore[operator]
                        targets = []
                        if need_recopy_l: targets.append("本機")
                        if need_recopy_n: targets.append("NAS")
                        t_str = " 與 ".join(targets)
                        self.log(f"[!] 二次掃描發現 {t_str} 缺失或大小不符：{card}/{rel}，自動執行補齊...")
                        
                        if need_recopy_l:
                            try:
                                if not _copy_chunked(src_abs, l_dest, is_recheck=True):
                                    stopped_mid = True
                                    break
                            except OSError as _e:
                                self.log_error(f"[X] 二次掃描補齊失敗，無法讀取來源: {card}/{rel} ({_e})")
                                any_error = True
                        if need_recopy_n and not stopped_mid:
                            try:
                                _copy_chunked(src_abs, n_dest, is_recheck=True)
                            except Exception as _e:
                                self.log_error(f"[!] NAS 補齊失敗: {card}/{rel} → {_e}")
                                
                        import shutil as _shutil
                        try:
                            _shutil.copystat(src_abs, str(l_dest))
                            if n_dest:
                                _shutil.copystat(src_abs, str(n_dest))
                        except Exception:
                            pass

                if not stopped_mid:
                    if mismatch_count == 0:
                        self.log("[OK] 二次掃描完成，所有檔案皆齊全。")
                    else:
                        self.log(f"[!] 二次掃描完成，共補齊了 {mismatch_count} 個檔案。")

            # ── 結束處理
            if stopped_mid:
                self.log(f"[X] 已強制中止 ({done}/{total})。")
                self.after(0, lambda: self.eta_lbl.configure(text="已中止"))
                self._save_job_log()
            else:
                self.set_progress(1.0, total, total, 0)
                self.after(0, lambda: self.eta_lbl.configure(text="完成！"))
                if any_error:
                    self.show_error("ANENT 警告", "備份過程中發生錯誤，請檢查日誌！")
                else:
                    self.show_info("ANENT 安全通知",
                                   "雙重備份 (本機 + NAS) 已完成！\n您可以安全拔除記憶卡。")
                self.log("備份與校驗任務完成！")
                self._processed_files.clear()
                self._notify_windows("ANENT 安全通知",
                                     "雙重備份已完成！可安全拔除記憶卡。")

                # 有轉檔或串帶勾選時，備份完成後自動執行後處理
                has_post = self.c1.get() or self.c2.get()
                if has_post:
                    tasks = ", ".join(filter(None, [
                        "轉檔" if self.c1.get() else "",
                        "串帶" if self.c2.get() else "",
                    ]))
                    self.log(f"[>>] 自動進行後處理（{tasks}）...")
                    self.after(200, self.start_post_process)  # 稍延 200ms 讓 UI 更新
                else:
                    self.log("所有任務完成！")
                    self._save_job_log()

        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            self.log_error(f"[X] 未預期錯誤: {str(e)}\n{tb_str}")
            self.show_error("ANENT 錯誤", str(e))
            self._save_job_log()
        finally:
            self._finish_controls(stopped=stopped_mid)

    # ─────────────────────────────────────────────────────────
    # 後處理工作流程（轉檔 / 串帶 / XML）
    # ─────────────────────────────────────────────────────────
    def start_post_process(self) -> None:
        """(備份完成後自動呼叫) 啟動後處理執行緒"""
        # next_step_btn 已移除，不再需要 pack_forget
        self.after(0, lambda: (
            self.start_btn.configure(state="disabled"),
            self.pause_btn.configure(state="normal", text="暫停"),
            self.stop_btn.configure(state="normal")
        ))
        self._stop_event.clear()
        self._pause_event.clear()
        threading.Thread(target=self.post_process_workflow, daemon=True).start()

    def post_process_workflow(self) -> None:
        """後處理：① 所有轉檔完成 → ② 每張卡串帶+時間碼壓印 → ③ Premiere XML"""
        aborted = False
        try:
            p_folder = self.project_name.get().strip() or datetime.now().strftime("%Y%m%d")
            local_path = os.path.join(self.local_root, p_folder)
            proxy_path = os.path.join(self.proxy_root, p_folder)

            # ── 取得所有已備份的影片檔（本機端），按卡分組
            cards_files: dict[str, list[str]] = {}   # card -> [abs_src_path]
            for i, _ in enumerate(self.source_paths):
                card = self.source_names[i] if i < len(self.source_names) else f"Card_{chr(65+i)}"
                card_dir = os.path.join(local_path, card)
                if not os.path.isdir(card_dir):
                    continue
                files: list[str] = []
                for dirpath, _, filenames in os.walk(card_dir):
                    for fname in sorted(filenames):
                        if fname.lower().endswith(SUPPORTED_EXTS):
                            files.append(os.path.join(dirpath, fname))
                if files:
                    cards_files[card] = files

            total_cards = len(cards_files)
            if total_cards == 0:
                self.log("[!] 未找到影片檔案可進行後處理。")
                return

            total_v = sum(len(v) for v in cards_files.values())
            self.log(f"後處理開始：{total_v} 個影片檔案，{total_cards} 張卡...")

            # 設定後處理階段的 offset/scale
            backup_scale    = getattr(self, '_backup_scale',    0.4)
            transcode_scale = getattr(self, '_transcode_scale', 0.4)
            concat_scale    = getattr(self, '_concat_scale',    0.2)

            # ══════════════════════════════════════════════
            # 階段 ①：轉檔 ProRes LT _proxy
            # ══════════════════════════════════════════════
            proxy_files: dict[str, list[str]] = {}   # card -> [proxy_abs_path]

            if self.c1.get():
                self.log("--- [階段 1/3] 轉檔 ProRes LT ---")
                done_v: int = 0
                # 對齊轉檔階段
                self._prog_offset = backup_scale
                self._prog_scale  = transcode_scale
                for card, src_list in cards_files.items():
                    card_proxy_files: list[str] = []
                    card_dir = os.path.join(local_path, card)
                    for src_file in src_list:
                        if self._check_pause_stop():
                            aborted = True
                            break

                        rel       = os.path.relpath(src_file, card_dir)
                        base_name = os.path.splitext(os.path.basename(rel))[0]
                        # 只保留影片直接上一層資料夾（例如 CLIP/），不複製整條巢狀路徑
                        parent_dir = os.path.basename(os.path.dirname(src_file))
                        out_dir   = os.path.join(proxy_path, card, parent_dir)
                        os.makedirs(out_dir, exist_ok=True)
                        proxy_out = os.path.join(out_dir, f"{base_name}_proxy.mov")

                        self.log(f"[轉檔] {card}/{os.path.basename(src_file)}")
                        self._current_output_file = proxy_out

                        # 取得影片時長（用於框級進度）
                        duration = self._get_video_duration(src_file)

                        cmd = [
                            "ffmpeg", "-y", "-nostdin",
                            "-i", src_file,
                            "-map", "0:v", "-map", "0:a?",
                            "-vf", "scale=trunc(oh*a/2)*2:720",
                            "-c:v", "prores_ks", "-profile:v", "1",
                            "-c:a", "copy",
                            "-progress", "pipe:1",   # 即時進度輸出
                            "-nostats",
                            proxy_out
                        ]
                        proc = subprocess.Popen(cmd,
                                                stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL,
                                                text=True)
                        self._current_proc = proc

                        # 讀 stdout 解析 out_time_ms 更新進度
                        # 時間與進度追蹤
                        t_start = time.time()
                        file_fraction = 0.0
                        _stdout = proc.stdout
                        for pline in (_stdout or []):  # type: ignore[union-attr]
                            pline = pline.strip()
                            if pline.startswith("out_time_ms="):
                                try:
                                    ms = int(pline.split("=")[1])
                                    if duration > 0:
                                        file_fraction = min(1.0, (ms / 1_000_000) / duration)

                                        # 計算該檔案剩餘時間 (ETA)
                                        elapsed = time.time() - t_start
                                        eta2 = None
                                        if file_fraction > 0 and elapsed > 0:
                                            # 單檔預估總需時間 - 已用時間
                                            eta2 = (elapsed / file_fraction) - elapsed

                                        slot_start = backup_scale + (done_v / total_v) * transcode_scale  # type: ignore[operator]
                                        slot_size  = transcode_scale / max(total_v, 1)  # type: ignore[operator]
                                        bar_frac   = (slot_start + file_fraction * slot_size)  # type: ignore[operator]
                                        def _upd(bv: float = bar_frac, frac: float = file_fraction,
                                                 fn: str = os.path.basename(src_file), e: float | None = eta2) -> None:
                                            self.set_progress((done_v + frac) / total_v, done_v, total_v, e,  # type: ignore[operator]
                                                              label=f"[轉檔中] {card}/{fn} ({int(min(100.0, frac*100))}%)", phase="transcode")
                                        self.after(0, _upd)
                                except Exception:
                                    pass

                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                            
                        self._current_proc = None
                        self._current_output_file = None

                        if self._stop_event.is_set():
                            aborted = True
                            break

                        if proc.returncode != 0:
                            self.log_error(f"[X] 轉檔出錯: {card}/{os.path.basename(src_file)}")
                            any_error = True
                        else:
                            self.log(f"[OK] {base_name}_proxy.mov")
                            card_proxy_files.append(proxy_out)

                        done_v += 1  # type: ignore[operator]
                        self.set_progress(done_v / total_v, done_v, total_v, None,
                                         label=f"[轉檔] {card}/{os.path.basename(src_file)}", phase="transcode")

                    proxy_files[card] = card_proxy_files
                    if aborted:
                        break
            else:
                # 未勾選轉檔，串帶時直接用原檔
                proxy_files = cards_files  # type: ignore[assignment]

            if aborted:
                self.log("[X] 後處理已中止。")
                return

            # ══════════════════════════════════════════════
            # 階段 ②：串帶 + 時間碼壓印（每張卡各串一條）
            # ══════════════════════════════════════════════
            if self.c2.get():
                self.log("--- [階段 2/3] 串帶 + 時間碼壓印 ---")
                # 對齊串帶階段
                self._prog_offset = backup_scale + transcode_scale
                self._prog_scale  = concat_scale
                done_concat: int  = 0
                total_concat: int = len([c for c, pl in proxy_files.items() if pl])
                for card, plist in proxy_files.items():
                    if self._check_pause_stop():
                        aborted = True
                        break
                    if not plist:
                        self.log(f"[!] {card} 無可串帶檔案，略過。")
                        continue

                    # 建立 FFmpeg concat 清單
                    concat_dir  = os.path.join(proxy_path, card)
                    os.makedirs(concat_dir, exist_ok=True)
                    concat_list = os.path.join(concat_dir, "_concat_list.txt")
                    with open(concat_list, "w", encoding="utf-8") as f:
                        for p in sorted(plist):
                            safe_p = p.replace("'", "'\\''").replace("\\", "/")
                            f.write(f"file '{safe_p}'\n")

                    reel_out = os.path.join(concat_dir,
                                            f"{self.project_name.get()}_{card}_reel.mov")
                    self.log(f"[串帶] {card} → {os.path.basename(reel_out)}")
                    self._current_output_file = reel_out

                    win_dir = os.environ.get("WINDIR", "C:\\Windows")
                    font_path = os.path.join(win_dir, "Fonts", "arial.ttf").replace("\\", "/")
                    font_path = font_path.replace(":", "\\:")
                    # drawtext 時間碼濾鏡（右上角，50% 透明度）
                    tc_filter = (
                        f"drawtext=fontfile='{font_path}'"
                        ":text='%{pts\\:hms}'"
                        ":x=w-tw-20:y=20"             # 右上角
                        ":fontsize=48:fontcolor=white@0.5"  # 白字 50% 透明
                        ":box=1:boxcolor=black@0.25:boxborderw=6"  # 陰影框 25%
                    )
                    # 取得所有子檔的對時長用於框級進度
                    concat_duration: float = sum(self._get_video_duration(p) for p in plist)

                    cmd = [
                        "ffmpeg", "-y", "-nostdin",
                        "-f", "concat", "-safe", "0",
                        "-i", concat_list,
                        "-map", "0:v", "-map", "0:a?",
                        "-vf", tc_filter,
                        "-c:v", "prores_ks", "-profile:v", "1",
                        "-c:a", "copy",
                        "-progress", "pipe:1",    # 即時進度
                        "-nostats",
                        reel_out
                    ]
                    proc = subprocess.Popen(cmd,
                                            stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL,
                                            text=True)
                    self._current_proc = proc

                    # 讀 stdout 解析 out_time_ms
                    t_start_c = time.time()
                    _cstdout = proc.stdout
                    for cline in (_cstdout or []):  # type: ignore[union-attr]
                        cline = cline.strip()
                        if cline.startswith("out_time_ms="):
                            try:
                                cms = int(cline.split("=")[1])
                                if concat_duration > 0:  # type: ignore[operator]
                                    cfrac = min(1.0, (cms / 1_000_000) / concat_duration)  # type: ignore[operator]
                                    
                                    # 計算串帶剩餘時間 (ETA)
                                    elapsed_c = time.time() - t_start_c
                                    eta_c = None
                                    if cfrac > 0 and elapsed_c > 0:
                                        eta_c = (elapsed_c / cfrac) - elapsed_c

                                    slot_s = backup_scale + transcode_scale + done_concat / max(total_concat, 1) * concat_scale  # type: ignore[operator]
                                    slot_w = concat_scale / max(total_concat, 1)  # type: ignore[operator]
                                    bv = min(1.0, slot_s + cfrac * slot_w)  # type: ignore[operator]
                                    pct_c = bv * 100
                                    def _upd_c(bv2: float = bv, pp: float = pct_c,
                                               fn: str = os.path.basename(reel_out), e: float | None = eta_c) -> None:
                                        self.set_progress((done_concat + cfrac) / total_concat if total_concat else 1.0,  # type: ignore[operator]
                                                          done_concat, total_concat, e,
                                                          label=f"[串帶中] {card} ({min(100.0, cfrac*100):.1f}%)", phase="concat")
                                    self.after(0, _upd_c)
                            except Exception:
                                pass

                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                        
                    self._current_proc = None
                    self._current_output_file = None

                    if self._stop_event.is_set():
                        aborted = True
                        break

                    if proc.returncode != 0:
                        err = stderr_bytes.decode(errors='replace')[:200]  # type: ignore[attr-defined]
                        self.log_error(f"[X] 串帶失敗: {err}")
                        any_error = True
                    else:
                        self.log(f"[OK] 串帶完成: {os.path.basename(reel_out)}")

                    # 更新進度
                    done_concat += 1  # type: ignore[operator]
                    self.set_progress(done_concat / total_concat if total_concat else 1.0,
                                      done_concat, total_concat, None,
                                      label=f"[串帶] {card} → {os.path.basename(reel_out)}", phase="concat")

                    # 清除 concat 清單
                    try:
                        os.remove(concat_list)
                    except Exception:
                        pass

            if aborted:
                self.log("[X] 後處理已中止。")
                self._save_job_log()
                return

            # ══════════════════════════════════════════════
            # 階段 ③：已移除 XML, 結束
            # ══════════════════════════════════════════════
            self.log("後處理任務全部完成！")
            self.show_info("ANENT 後處理完成", "轉檔與串帶已全部完成！")
            self._notify_windows("ANENT 完成", "轉檔與串帶已全部完成。")
            self._save_job_log()


        except Exception as e:
            self.log_error(f"[X] 後處理發生錯誤: {str(e)}")
            self.show_error("ANENT 後處理錯誤", str(e))
            self._save_job_log()
        finally:
            self._current_proc = None
            self._current_output_file = None
            self._finish_controls(stopped=aborted)


if __name__ == "__main__":
    app = AnentApp()
    app.mainloop()
