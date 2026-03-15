import re

with open("Anent_MediaGuard_Pro.py", "r", encoding="utf-8") as f:
    code = f.read()

target = r"    def setup_ui\(self\) -> None:.*?# 預設不顯示，備份成功後才 pack\s*"

replacement = r"""    def setup_ui(self) -> None:
        # ── 標題 (保持置頂)
        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.pack(pady=(15, 5), padx=20, fill="x")
        ctk.CTkLabel(title_frame, text="Originsun Media Guard Pro",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")

        # ── 建立 Tabview
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self.tab_ingest = self.tabview.add("保護備份與轉檔 (主控台)")
        self.tab_toolbox = self.tabview.add("媒體工具箱 (獨立作業)")

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
            text="可將資料夾拖曳至此處新增來源\n或點擊右上角「+ 新增」按鈕選擇",
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
        
        self.tool_verify_src = ctk.StringVar()
        self.tool_verify_dst = ctk.StringVar()
        self.tool_transcode_src = ctk.StringVar()
        self.tool_transcode_dst = ctk.StringVar(value=self.proxy_root)
        self.tool_concat_src = ctk.StringVar()
        self.tool_concat_dst = ctk.StringVar()
        
        def pick_dir(var: ctk.StringVar, title: str) -> None:
            p = filedialog.askdirectory(title=title)
            if p:
                var.set(p)
                
        def create_tool_row(master_frame: ctk.CTkFrame, title: str, 
                            src_var: ctk.StringVar, dst_var, 
                            btn_text: str, btn_color: str, cmd) -> None:
            f = ctk.CTkFrame(master_frame)
            f.pack(fill="x", padx=20, pady=10)
            ctk.CTkLabel(f, text=title, font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=15, pady=(10, 5))
            
            r = ctk.CTkFrame(f, fg_color="transparent")
            r.pack(fill="x", padx=10, pady=(0, 10))
            
            src_f = ctk.CTkFrame(r, fg_color="transparent")
            src_f.pack(side="left", fill="x", expand=True, padx=(5, 5))
            ctk.CTkEntry(src_f, textvariable=src_var, placeholder_text="來源資料夾...").pack(side="left", fill="x", expand=True)
            ctk.CTkButton(src_f, text="選取", width=55, fg_color="#444", hover_color="#666", 
                          command=lambda sv=src_var: pick_dir(sv, "選取來源資料夾")).pack(side="left", padx=(5, 0))
            
            if dst_var is not None:
                dst_f = ctk.CTkFrame(r, fg_color="transparent")
                dst_f.pack(side="left", fill="x", expand=True, padx=(5, 5))
                ctk.CTkEntry(dst_f, textvariable=dst_var, placeholder_text="目標資料夾...").pack(side="left", fill="x", expand=True)
                ctk.CTkButton(dst_f, text="選取", width=55, fg_color="#444", hover_color="#666", 
                              command=lambda dv=dst_var: pick_dir(dv, "選取目標資料夾")).pack(side="left", padx=(5, 0))
            
            btn_f = ctk.CTkFrame(r, fg_color="transparent")
            btn_f.pack(side="right", padx=(5, 5))
            ctk.CTkButton(btn_f, text=btn_text, width=100, fg_color=btn_color, command=cmd).pack()
            
        create_tool_row(parent, "📁 資料夾 XXH64 完整性比對", 
                        self.tool_verify_src, self.tool_verify_dst, 
                        "開始比對", "#555", self._run_standalone_verify)
                        
        create_tool_row(parent, "🎞️ 獨立生成 Proxy (ProRes LT)", 
                        self.tool_transcode_src, self.tool_transcode_dst, 
                        "開始轉檔", "#d48a04", self._run_standalone_transcode)
                        
        create_tool_row(parent, "🎬 獨立壓印時間碼串帶 (Reel)", 
                        self.tool_concat_src, self.tool_concat_dst, 
                        "開始串帶", "#228b22", self._run_standalone_concat)

        lf = ctk.CTkFrame(parent, fg_color="transparent")
        lf.pack(fill="both", expand=True, padx=20, pady=10)
        
        self.toolbox_progress_bar = ctk.CTkProgressBar(lf, height=14, corner_radius=7)
        self.toolbox_progress_bar.pack(fill="x", pady=(0, 10))
        self.toolbox_progress_bar.set(0)
        
        ctk.CTkLabel(lf, text="工具箱專屬日誌", font=ctk.CTkFont(weight="bold")).pack(anchor="w")
        self.toolbox_log_box = ctk.CTkTextbox(lf, text_color="white")
        self.toolbox_log_box.pack(fill="both", expand=True, pady=(5, 0))

    def _toolbox_log(self, msg: str) -> None:
        def _insert() -> None:
            from datetime import datetime
            self.toolbox_log_box.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
            self.toolbox_log_box.see("end")
        self.after(0, _insert)

    def _run_standalone_verify(self) -> None:
        self.toolbox_progress_bar.set(0)
        self.toolbox_progress_bar.configure(progress_color="#555")
        self._toolbox_log("準備執行: XXH64 完整性比對 (尚未實作後端邏輯)")
        
    def _run_standalone_transcode(self) -> None:
        self.toolbox_progress_bar.set(0)
        self.toolbox_progress_bar.configure(progress_color="#d48a04")
        self._toolbox_log("準備執行: 獨立生成 Proxy (尚未實作後端邏輯)")

    def _run_standalone_concat(self) -> None:
        self.toolbox_progress_bar.set(0)
        self.toolbox_progress_bar.configure(progress_color="#228b22")
        self._toolbox_log("準備執行: 獨立壓印時間碼串帶 (尚未實作後端邏輯)")
\n"""

new_code, count = re.subn(target, replacement, code, flags=re.DOTALL)

with open("Anent_MediaGuard_Pro.py", "w", encoding="utf-8") as f:
    f.write(new_code)

print(f"Patched {count} occurrences.")
