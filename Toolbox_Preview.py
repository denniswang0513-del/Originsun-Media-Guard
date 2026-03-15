import customtkinter as ctk  # type: ignore

class AppPreview(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Originsun Media Guard Pro - UI Preview")
        self.geometry("1000x900")
        ctk.set_appearance_mode("dark")

        # Create Tabview
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=20, pady=20)

        # Tab 1: Ingest (Current App)
        self.tab_ingest = self.tabview.add("主控台 (保護備份與轉檔)")
        # Tab 2: Toolbox
        self.tab_toolbox = self.tabview.add("媒體工具箱 (獨立作業)")

        self._build_ingest_tab()
        self._build_toolbox_tab()

    def _build_ingest_tab(self):
        lbl = ctk.CTkLabel(self.tab_ingest, text="(這裡會完全保留您現在使用的所有介面，包含路徑設定、拖拉來源、任務勾選、進度條與日誌，一模一樣)")
        lbl.pack(pady=300)

    def _build_toolbox_tab(self):
        # Header
        hdr = ctk.CTkLabel(self.tab_toolbox, text="獨立媒體後處理工具箱 (無需備份即可直接執行)", font=ctk.CTkFont(size=20, weight="bold"))
        hdr.pack(pady=(20, 30))

        # Tool 1: Checksum
        f1 = ctk.CTkFrame(self.tab_toolbox)
        f1.pack(fill="x", padx=40, pady=10)
        ctk.CTkLabel(f1, text="資料夾 XXH64 完整性比對", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=15, pady=(10, 5))
        r1 = ctk.CTkFrame(f1, fg_color="transparent")
        r1.pack(fill="x", padx=10, pady=5)
        ctk.CTkEntry(r1, placeholder_text="來源資料夾路徑...").pack(side="left", fill="x", expand=True, padx=(5, 10))
        ctk.CTkEntry(r1, placeholder_text="目標資料夾路徑...").pack(side="left", fill="x", expand=True, padx=(0, 10))
        ctk.CTkButton(r1, text="開始比對", width=100).pack(side="right", padx=5)

        # Tool 2: Transcode
        f2 = ctk.CTkFrame(self.tab_toolbox)
        f2.pack(fill="x", padx=40, pady=10)
        ctk.CTkLabel(f2, text="️ 獨立生成 Proxy (ProRes LT)", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=15, pady=(10, 5))
        r2 = ctk.CTkFrame(f2, fg_color="transparent")
        r2.pack(fill="x", padx=10, pady=5)
        ctk.CTkEntry(r2, placeholder_text="包含影片的資料夾...").pack(side="left", fill="x", expand=True, padx=(5, 10))
        ctk.CTkButton(r2, text="選取輸出位置", width=120, fg_color="#555").pack(side="left", padx=(0, 10))
        ctk.CTkButton(r2, text="開始轉檔", width=100, fg_color="#d48a04").pack(side="right", padx=5)

        # Tool 3: Concat
        f3 = ctk.CTkFrame(self.tab_toolbox)
        f3.pack(fill="x", padx=40, pady=10)
        ctk.CTkLabel(f3, text="獨立壓印時間碼串帶 (Reel)", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=15, pady=(10, 5))
        r3 = ctk.CTkFrame(f3, fg_color="transparent")
        r3.pack(fill="x", padx=10, pady=5)
        ctk.CTkEntry(r3, placeholder_text="包含多段碎片的資料夾...").pack(side="left", fill="x", expand=True, padx=(5, 10))
        ctk.CTkButton(r3, text="開始串帶", width=100, fg_color="#228b22").pack(side="right", padx=5)

        # Toolbox Log
        lf = ctk.CTkFrame(self.tab_toolbox, fg_color="transparent")
        lf.pack(fill="both", expand=True, padx=40, pady=20)
        ctk.CTkLabel(lf, text="工具箱專屬日誌", font=ctk.CTkFont(weight="bold")).pack(anchor="w")
        ctk.CTkTextbox(lf, height=150).pack(fill="both", expand=True, pady=(5, 0))

if __name__ == "__main__":
    app = AppPreview()
    app.mainloop()
