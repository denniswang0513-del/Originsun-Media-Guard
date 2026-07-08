"""SQLAlchemy ORM models for Originsun Media Guard Pro."""

try:
    from sqlalchemy import Column, String, Text, Boolean, Integer, Float, DateTime, func, Index
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.orm import DeclarativeBase
    _HAS_SQLALCHEMY = True
except ImportError:
    _HAS_SQLALCHEMY = False
    # Provide stubs so module can be imported without crashing
    class _Stub:
        def __call__(self, *a, **kw): return self
        def __getattr__(self, _): return self
    Column = String = Text = Boolean = Integer = Float = DateTime = func = Index = _Stub()
    JSONB = _Stub()
    class DeclarativeBase: pass


class Base(DeclarativeBase):
    pass


class JobHistory(Base):
    __tablename__ = "job_history"

    job_id = Column(String(32), primary_key=True)
    task_type = Column(String(32), nullable=False)
    project_name = Column(String(255), nullable=False, default="")
    status = Column(String(16), nullable=False)
    machine_id = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    error_detail = Column(Text)
    log_file = Column(Text)

    __table_args__ = (
        Index("idx_jh_task_type", "task_type"),
        Index("idx_jh_status", "status"),
        Index("idx_jh_machine", "machine_id"),
        Index("idx_jh_finished", "finished_at"),
    )


class BulletinItem(Base):
    """公布欄待辦提醒（團隊共用一份，存 mediaguard）。"""
    __tablename__ = "bulletin_items"

    id = Column(String(32), primary_key=True)
    title = Column(Text, nullable=False)
    note = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="todo")     # todo / doing / done
    priority = Column(String(8), nullable=False, default="med")      # high / med / low
    category = Column(String(64), nullable=True)
    pinned = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)
    assignee = Column(String(16), nullable=False, default="me")      # me / claude（交辦收件匣）
    conversation = Column(JSONB, nullable=True)                       # 「問 Claude」對話 [{role,text,at}]
    activity = Column(Text, nullable=True)                            # Claude 執行進度/結果 log（tier B）
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    done_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_bulletin_status", "status"),
        Index("idx_bulletin_pinned", "pinned"),
    )


class Agent(Base):
    __tablename__ = "agents"

    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    url = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Bookmark(Base):
    __tablename__ = "bookmarks"

    id = Column(String(32), primary_key=True)
    machine_id = Column(String(64), nullable=False)
    name = Column(String(255), nullable=False)
    task_type = Column(String(32), nullable=False)
    request = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_bk_machine", "machine_id"),
    )


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"

    schedule_id = Column(String(32), primary_key=True)
    machine_id = Column(String(64), nullable=False)
    name = Column(String(255), nullable=False)
    cron = Column(String(64))
    run_at = Column(DateTime(timezone=True))
    task_type = Column(String(32), nullable=False)
    request = Column(JSONB, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    next_run = Column(DateTime(timezone=True))
    last_run = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_sj_machine_enabled", "machine_id", "enabled"),
        Index("idx_sj_next_run", "next_run", postgresql_where=(enabled == True)),  # noqa: E712
    )


class Report(Base):
    __tablename__ = "reports"

    id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    local_path = Column(Text)
    pdf_path = Column(Text)
    public_url = Column(Text)
    drive_url = Column(Text)
    machine_id = Column(String(64))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    file_count = Column(Integer, nullable=False, default=0)
    total_size_str = Column(String(32), nullable=False, default="")

    __table_args__ = (
        Index("idx_rpt_created", "created_at"),
    )


# NOTE: RBAC v2 移除角色層——Role model 已刪除。權限直接綁 users.modules +
# users.access_level（見 User）。users.role / users.role_id 欄位保留 dormant（不再
# 決定權限）；既有 DB 的 roles 表不再被讀寫，可日後手動 DROP。


class User(Base):
    __tablename__ = "users"

    username = Column(String(64), primary_key=True)
    password_hash = Column(String(255), nullable=True)                  # nullable: Google-only 使用者無密碼
    role = Column(String(16), nullable=False, default="editor")         # 舊欄位，過渡期保留
    visible_tabs = Column(JSONB)                                        # 舊欄位，過渡期保留
    role_id = Column(Integer, nullable=True)                            # 舊 RBAC FK（角色層已淘汰，過渡期保留可回退）
    # ── RBAC v2：權限直接綁帳號（移除角色層）。以下兩欄為唯一授權來源；
    #    role/role_id 僅保留作回退，不再決定權限。 ──
    modules = Column(JSONB, nullable=True)                              # 可用模組 key 清單
    access_level = Column(Integer, nullable=True)                       # 3=管理員, 1=一般（唯一硬閘門 Lv3）
    first_login = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # ── Google OAuth 欄位 ──
    google_id = Column(String(255), unique=True, nullable=True, index=True)
    email = Column(String(255), nullable=True)
    avatar_url = Column(String(512), nullable=True)


class Client(Base):
    """CRM 客戶資料。"""
    __tablename__ = "clients"

    id = Column(String(32), primary_key=True)
    short_name = Column(String(64), nullable=False, unique=True)  # 客戶代稱
    full_name = Column(String(255), nullable=True, default="")  # 全稱 / 抬頭
    tax_id = Column(String(16), nullable=True, default="")      # 統一編號
    am_username = Column(String(64), nullable=True)             # AM，FK → users
    source_channel = Column(String(64), nullable=True, default="")   # 來源管道
    contact_person = Column(String(128), nullable=True, default="")  # 客戶聯絡人
    contact_method = Column(String(128), nullable=True, default="")  # 聯絡方式
    status = Column(String(32), nullable=True, default="潛在客戶")
    cooperation_note = Column(Text, nullable=True)              # 合作契機
    payment_info = Column(String(255), nullable=True, default="")  # 匯款資訊（銀行/帳號）
    payment_note = Column(Text, nullable=True)                     # 匯款備註
    notes = Column(Text, nullable=True)                         # 備註
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_client_am", "am_username"),
        Index("idx_client_status", "status"),
    )


class CrmProject(Base):
    """CRM 專案資料。"""
    __tablename__ = "crm_projects"

    id = Column(String(32), primary_key=True)
    name = Column(String(255), nullable=False)
    client_id = Column(String(32), nullable=False)              # soft FK → clients.id
    status = Column(String(32), nullable=False, default="洽談中")
    am_username = Column(String(64), nullable=True)
    pm_usernames = Column(JSONB, nullable=True)
    shoot_date = Column(DateTime(timezone=True), nullable=True)
    start_date = Column(DateTime(timezone=True), nullable=True)       # 起始日
    completion_date = Column(DateTime(timezone=True), nullable=True)  # 結案日
    project_type = Column(String(64), nullable=True, default="")      # 紀實影片/活動紀實/廣告/形象/MV
    folder_path = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    # 財務
    contract_amount = Column(Integer, nullable=True)                  # 合約金額（含稅）
    tax_rate = Column(Integer, nullable=False, default=5)             # 稅率 %
    profit_target_pct = Column(Integer, nullable=False, default=20)   # 目標毛利率 %
    misc_budget_pct = Column(Integer, nullable=False, default=5)      # 雜支預算比例 %
    budget_hours = Column(Float, nullable=True)                       # 工時預算池（小時，N2 階段0 對齊 Sheet）
    # 帳務
    payment_status = Column(String(32), nullable=True, default="未到帳")  # 未到帳/部分到帳/全額到帳
    amount_receivable = Column(Integer, nullable=True)                # 應收帳款
    amount_received = Column(Integer, nullable=True)                  # 已收帳款
    transfer_fee = Column(Integer, nullable=True)                     # 帳款匯費
    # NOTE: receipt_path 已下放到 crm_project_cost_groups（每張子表獨立資料夾）。
    # 啟動時 migration 會把舊值搬到該專案 sort_order 最小的子表，再 DROP COLUMN。

    # Phase M: 對外官網展示
    # (實體欄位 + 索引由 db/migrations_website.py 建立；這裡的宣告 + __table_args__
    #  中的 Index() 是給 Base.metadata.create_all() 的全新 DB / 測試用，名稱與
    #  migration 保持一致避免 fresh DB vs migrated DB 索引命名分歧)
    public = Column(Boolean, nullable=True, default=False)
    public_slug = Column(String(100), nullable=True)
    public_title = Column(String(200), nullable=True)
    public_client = Column(String(100), nullable=True)
    public_youtube_id = Column(String(20), nullable=True)
    public_description = Column(Text, nullable=True)
    public_credits = Column(JSONB, nullable=True)
    # credits 雙模式：'block' = 用 public_credits（JSONB blocks）；'text' = 用 public_credits_text（純文字）
    public_credits_mode = Column(String(16), nullable=False, default="text")
    public_credits_text = Column(Text, nullable=True)
    public_year = Column(Integer, nullable=True)
    public_featured = Column(Boolean, nullable=True, default=False)
    public_sort_order = Column(Integer, nullable=True, default=0)
    public_published_at = Column(DateTime(timezone=True), nullable=True)
    public_number = Column(Integer, nullable=True)  # 1, 2, 3...slug 沒設時用
    # SEO 301 來源舊 slug — admin 改 public_slug 時自動 append（軟+硬 301 雙保險）
    public_old_slugs = Column(JSONB, nullable=False, default=list)
    # OG image — _sync_showcase_to_public 從 sc.cover_url 鏡像；admin 不直接 PUT
    public_cover_url = Column(Text, nullable=True)
    # 首頁輪播精選圖（admin 直接上傳/設定；不被 showcase 鏡像覆蓋）。
    # 首頁取圖：public_featured_image → 成果展示第一張 → YouTube 縮圖。
    public_featured_image = Column(Text, nullable=True)
    # per-work SEO 索引控制（false = 跟著站級 meta.indexable；true = 強制 noindex）
    public_noindex = Column(Boolean, nullable=True, default=False)
    # ── Phase M 英文版：_en 翻譯欄（transcreation；空則前端 fallback 中文）──
    # public_client_en 為「手動指定」專用 — AI 翻譯 runner 不翻客戶名（專有名詞）
    public_title_en = Column(String(300), nullable=True)
    public_description_en = Column(Text, nullable=True)
    public_client_en = Column(String(150), nullable=True)

    # ── 結案製作（website production）工作階段 — 後台「結案製作」看板用 ──
    # 已結案專案的官網製作進度：待製作 / 製作中 / 不上官網。
    # None 視為「待製作」；專案上線後（public=True）此欄不再參與 stage 推導
    # （GET /projects/closing 以 public 優先 → '已上線'）。
    website_prod_stage = Column(String(16), nullable=True)
    # N-now 上架驗收：rebuild 後對外頁實測 200 的時間戳（None=尚未驗證）
    website_verified_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_crmproj_client", "client_id"),
        Index("idx_crmproj_status", "status"),
        Index("idx_crmproj_public", "public"),
        Index("idx_crmproj_slug", "public_slug"),
        Index("idx_crmproj_featured", "public_featured"),
    )


class CrmQuotation(Base):
    """CRM 報價單。"""
    __tablename__ = "crm_quotations"

    id = Column(String(32), primary_key=True)
    project_id = Column(String(32), nullable=False)             # soft FK → crm_projects
    version = Column(Integer, nullable=False, default=1)        # v1, v2, v3...
    status = Column(String(32), nullable=False, default="草稿")  # 草稿/已寄送/已簽核/已拒絕
    quote_date = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    subtotal = Column(Integer, nullable=False, default=0)       # 整體規劃費（項目加總）
    discount = Column(Integer, nullable=False, default=0)       # 專案折扣（正數）
    tax_rate = Column(Integer, nullable=False, default=5)       # 稅率 %
    tax_amount = Column(Integer, nullable=False, default=0)     # 稅額
    total = Column(Integer, nullable=False, default=0)          # 含稅總計
    final_price = Column(Integer, nullable=True)                # 最終報價（手動填，可與 total 不同）
    payment_stages = Column(JSONB, nullable=True)               # [{"label":"腳本","pct":20},...]
    terms = Column(Text, nullable=True)                         # 備註/條款
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_quote_project", "project_id"),
        Index("idx_quote_status", "status"),
        Index("idx_quote_created", "created_at"),
    )


class CrmQuotationItem(Base):
    """報價單項目明細。"""
    __tablename__ = "crm_quotation_items"

    id = Column(String(32), primary_key=True)
    quotation_id = Column(String(32), nullable=False, index=True)  # soft FK → crm_quotations
    group_name = Column(String(64), nullable=True, default="")  # 群組（前期作業/拍攝期/後製剪輯）
    sort_order = Column(Integer, nullable=False, default=0)
    description = Column(String(512), nullable=False)           # 項目描述
    unit = Column(String(32), nullable=False, default="式")     # 單位（式/天/人/場/次/部/首）
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Integer, nullable=False, default=0)     # 單價（元）
    amount = Column(Integer, nullable=False, default=0)         # 小計 = quantity × unit_price
    note = Column(String(512), nullable=True)                   # 備註（如出班價說明）
    internal_cost = Column(Integer, nullable=False, default=0)  # 內部成本（元）


class CrmQuotationTemplate(Base):
    """報價範本。"""
    __tablename__ = "crm_quotation_templates"

    id = Column(String(32), primary_key=True)
    name = Column(String(128), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    tax_rate = Column(Integer, nullable=False, default=5)
    terms = Column(Text, nullable=True)
    payment_stages = Column(JSONB, nullable=True)
    items = Column(JSONB, nullable=True)                        # [{group_name, description, unit, quantity, unit_price}]
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class CrmStaff(Base):
    """CRM 人員資料庫。"""
    __tablename__ = "crm_staff"

    id = Column(String(32), primary_key=True)
    name = Column(String(64), nullable=False)
    role = Column(String(64), nullable=False, default="")       # 職能（攝影師/剪輯師/導演...）
    daily_rate = Column(Integer, nullable=False, default=0)     # 日費
    hourly_rate = Column(Integer, nullable=False, default=0)    # 時薪
    phone = Column(String(32), nullable=True)
    email = Column(String(128), nullable=True)
    id_number = Column(String(16), nullable=True)               # 身分證字號
    address = Column(String(255), nullable=True)                # 住址（勞報用）
    bank_name = Column(String(64), nullable=True)
    bank_account = Column(String(32), nullable=True)
    portfolio_url = Column(String(512), nullable=True)          # 作品集連結
    status = Column(String(32), nullable=False, default="在職")  # 在職/離職/兼職
    notes = Column(Text, nullable=True)
    # H1 員工檔案完整化（HR_FIN_PLAN）
    employment_type = Column(String(16), nullable=True)          # 正職/兼職/約聘/freelance
    hire_date = Column(DateTime(timezone=True), nullable=True)   # 到職日
    leave_date = Column(DateTime(timezone=True), nullable=True)  # 離職日
    emergency_contact = Column(String(128), nullable=True)       # 緊急聯絡人（姓名+電話）
    # Resume / portfolio fields
    photo_url = Column(String(512), nullable=True)
    bio = Column(Text, nullable=True)
    skills = Column(JSONB, nullable=True)           # ["Premiere", "DaVinci", "FX6"]
    education = Column(JSONB, nullable=True)         # [{"school":"...", "degree":"...", "year":"..."}]
    experience = Column(JSONB, nullable=True)        # [{"company":"...", "role":"...", "period":"...", "desc":"..."}]
    awards = Column(JSONB, nullable=True)            # [{"title":"...", "year":"...", "desc":"..."}]
    resume_visible = Column(Boolean, nullable=True, default=False)
    edit_token = Column(String(512), nullable=True)
    resume_editable = Column(Boolean, nullable=True, default=True)
    # Phase M: 對外官網團隊頁顯示開關
    show_on_website = Column(Boolean, nullable=True, default=False)
    # Phase M: 官網團隊頁顯示覆寫（不動 CRM 正本 name/role/photo_url/bio；空 → fallback 正本）
    website_title = Column(String(128), nullable=True)        # 官網顯示職稱（空 → fallback role）
    website_photo_url = Column(String(512), nullable=True)    # 官網頭像（空 → fallback photo_url）
    website_bio = Column(Text, nullable=True)                 # 官網簡介（空 → fallback bio）
    website_sort_order = Column(Integer, nullable=True, default=0)  # 官網團隊頁排序
    # showcase-edit quick_add 來源追蹤
    created_via = Column(String(20), nullable=True, default="admin")
    created_for_project_id = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_staff_role", "role"),
        Index("idx_staff_status", "status"),
    )


# 官網團隊頁覆寫欄位（單一真相）：admin_team PUT 白名單 + CRM staff PUT 「動到官網欄位才 rebuild」
# 的判定都引用這份，避免兩個 router 各自維護一份而漂移。
WEBSITE_TEAM_OVERRIDE_FIELDS = (
    "show_on_website", "website_title", "website_photo_url",
    "website_bio", "website_sort_order",
)


class CrmProjectShowcase(Base):
    """專案完稿結案 / 作品展示。"""
    __tablename__ = "crm_project_showcase"

    id = Column(String(32), primary_key=True)  # same as project_id (1:1)
    cover_url = Column(String(512), nullable=True)
    description = Column(Text, nullable=True)
    video_url = Column(String(512), nullable=True)
    gallery = Column(JSONB, nullable=True)  # [{url, caption}]
    process_mode = Column(String(16), nullable=False, default='gallery')  # gallery|media|timeline
    process_items = Column(JSONB, nullable=True)  # [{type, url, caption, phase, video_url}]
    credits = Column(JSONB, nullable=True)  # [{name, role, staff_id, resume_url}]
    # credits 雙模式：'block' = 用 credits（JSONB blocks）；'text' = 用 credits_text（純文字貼上）
    credits_mode = Column(String(16), nullable=False, default="text")
    credits_text = Column(Text, nullable=True)
    # NOTE: 舊 freeform tags 已廢除，統一改用 website_categories（kind=tag）。
    # 啟動時 migration DROP COLUMN，舊資料一併刪除（使用者確認）。
    slug = Column(String(128), nullable=True, unique=True)
    published = Column(Boolean, nullable=False, default=False)
    published_at = Column(DateTime(timezone=True), nullable=True)
    edit_token = Column(String(512), nullable=True)
    editable = Column(Boolean, nullable=False, default=True)
    # AI 參考資料：製作人上傳的文件抽取文字 + 補充說明，餵給 AI 寫描述 / SEO。
    ai_reference_files = Column(JSONB, nullable=True)  # [{name, text, chars}]
    ai_reference_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class CrmStaffPortfolio(Base):
    """人員作品集。"""
    __tablename__ = "crm_staff_portfolio"

    id = Column(String(32), primary_key=True)
    staff_id = Column(String(32), nullable=False, index=True)
    title = Column(String(256), nullable=False)
    url = Column(String(512), nullable=False)
    thumbnail_url = Column(String(512), nullable=True)
    role_desc = Column(String(256), nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CrmProjectStaff(Base):
    """專案派工。"""
    __tablename__ = "crm_project_staff"

    id = Column(String(32), primary_key=True)
    project_id = Column(String(32), nullable=False, index=True)
    staff_id = Column(String(32), nullable=False, index=True)
    role_in_project = Column(String(64), nullable=True)         # 在此專案的職務
    phase = Column(String(32), nullable=True, default="")      # 前期製作/現場拍攝/後期製作
    days = Column(Integer, nullable=False, default=1)           # 預估天數
    rate_override = Column(Integer, nullable=True)              # 覆寫日費
    cost = Column(Integer, nullable=False, default=0)           # 預估費用
    actual_days = Column(Integer, nullable=True)                # 實際天數
    actual_cost = Column(Integer, nullable=True)                # 實際花費
    payment_status = Column(String(32), nullable=True)          # 未付/已付/已開勞報 ← 財務預留
    payment_date = Column(DateTime(timezone=True), nullable=True)  # ← 財務預留
    notes = Column(Text, nullable=True)


class CrmProjectExpense(Base):
    """專案雜支明細（歸屬於 cost_group）。"""
    __tablename__ = "crm_project_expenses"

    id = Column(String(32), primary_key=True)
    project_id = Column(String(32), nullable=False, index=True)
    cost_group_id = Column(String(32), nullable=True, index=True)  # FK → crm_project_cost_groups
    category = Column(String(64), nullable=False)               # 交通/住宿/飲食/提案/其他
    estimated = Column(Integer, nullable=False, default=0)      # 預估金額
    actual = Column(Integer, nullable=False, default=0)         # 實際金額
    receipt_url = Column(String(512), nullable=True)            # 收據連結 ← 財務預留
    sub_item = Column(String(128), nullable=True)              # 細項
    payee = Column(String(64), nullable=True)                  # 請款人
    advance_id = Column(String(32), nullable=True)             # 關聯預支款 ID
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)


class CrmProjectCostGroup(Base):
    """專案成本子表 — 一張完整財務表單（預算 / 成本 / 雜支）。"""
    __tablename__ = "crm_project_cost_groups"

    id = Column(String(32), primary_key=True)
    project_id = Column(String(32), nullable=False)
    name = Column(String(128), nullable=False)                 # 主表 / 5-15 外景 / 棚拍
    shoot_date = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    # 預算（全可 NULL，UI 會提示未設）
    budget_amount = Column(Integer, nullable=True)             # 成本預算（未稅）
    misc_budget_amount = Column(Integer, nullable=True)        # 雜支預算
    profit_target_pct = Column(Integer, nullable=True)         # 可 override 專案預設
    receipt_path = Column(String(512), nullable=True)          # 此子表收據資料夾（空字串/NULL = 用 fallback）
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_costgroup_project", "project_id", "sort_order"),
    )


class CrmCostLineTemplate(Base):
    """成本估算範本。"""
    __tablename__ = "crm_cost_line_templates"

    id = Column(String(32), primary_key=True)
    name = Column(String(128), nullable=False)
    items = Column(JSONB)       # [{phase, item_name, sort_order}, ...]
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CrmProjectCostLine(Base):
    """專案成本估算明細（費用預估 vs 費用結算）— 歸屬於 cost_group。"""
    __tablename__ = "crm_project_cost_lines"

    id = Column(String(32), primary_key=True)
    project_id = Column(String(32), nullable=False, index=True)
    cost_group_id = Column(String(32), nullable=True, index=True)  # FK → crm_project_cost_groups
    phase = Column(String(32), nullable=False)              # 前期製作/現場拍攝/後期製作/行政雜支
    item_name = Column(String(128), nullable=False)         # 導演/剪輯/動態攝影...
    sort_order = Column(Integer, nullable=False, default=0)
    # 費用預估
    estimated_unit_price = Column(Integer, nullable=True)   # 單價
    estimated_quantity = Column(Integer, nullable=True)      # 數量
    estimated_unit_type = Column(String(16), nullable=True)  # 單位類別（式/日/班/時/支/套/件）
    estimated_amount = Column(Integer, nullable=True)        # 金額 = 單價 × 單位
    estimated_staff_id = Column(String(32), nullable=True)  # soft FK → crm_staff
    estimated_notes = Column(String(255), nullable=True)
    # 費用結算
    actual_unit_price = Column(Integer, nullable=True)
    actual_quantity = Column(Integer, nullable=True)
    actual_unit_type = Column(String(16), nullable=True)
    actual_amount = Column(Integer, nullable=True)
    actual_staff_id = Column(String(32), nullable=True)     # soft FK → crm_staff
    actual_notes = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_costline_project", "project_id"),
        Index("idx_costline_phase", "project_id", "phase"),
    )


class CrmInvoice(Base):
    """帳務 — 發票登記。"""
    __tablename__ = "crm_invoices"

    id = Column(String(32), primary_key=True)
    payment_type = Column(String(16), nullable=False, default="收款")   # 收款/付款
    payment_status = Column(String(16), nullable=False, default="未收款")  # 未收款/已收款/作廢
    issue_status = Column(String(16), nullable=False, default="已開立")  # 已開立/作廢
    invoice_number = Column(String(32), nullable=True)                  # 發票編號
    invoice_date = Column(DateTime(timezone=True), nullable=True)       # 填表時間
    title = Column(String(255), nullable=False)                         # 名稱（案件/項目）
    applicant = Column(String(64), nullable=True)                       # 申請人
    category = Column(String(32), nullable=True, default="專案")         # 專案/內部代開
    invoice_kind = Column(String(32), nullable=True)                    # 紙本發票/電子發票
    amount_ex_tax = Column(Integer, nullable=True)                      # 未稅價
    amount_total = Column(Integer, nullable=True)                       # 發票金額（含稅）
    tax_amount = Column(Integer, nullable=True)                         # 稅額
    commission = Column(Integer, nullable=True)                         # 代開應區（代開費）
    company_name = Column(String(255), nullable=True)                   # 抬頭
    tax_id = Column(String(16), nullable=True)                          # 統編
    item_type = Column(String(64), nullable=True)                       # 品項（影片製作/展場攝影...）
    project_id = Column(String(32), nullable=True)                      # 可選關聯 → crm_projects
    recipient = Column(String(128), nullable=True)                       # 紙本發票收件人
    recipient_phone = Column(String(32), nullable=True)                  # 紙本發票收件電話
    recipient_address = Column(String(255), nullable=True)               # 紙本發票收件地址
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_invoice_date", "invoice_date"),
        Index("idx_invoice_payment", "payment_type"),
        Index("idx_invoice_project", "project_id"),
        Index("idx_invoice_issue_status", "issue_status"),
        Index("idx_invoice_pay_status", "payment_status"),
    )


class CrmPaymentRequest(Base):
    """帳務 — 請款單。"""
    __tablename__ = "crm_payment_requests"

    id = Column(String(32), primary_key=True)
    request_date = Column(DateTime(timezone=True), nullable=True)       # 日期
    amount = Column(Integer, nullable=False, default=0)                 # 請款金額
    summary = Column(String(255), nullable=False)                       # 摘要
    category = Column(String(32), nullable=True, default="專案外包")      # 專案外包/零用金/轉存/發票代開
    payee_name = Column(String(64), nullable=True)                      # 收款人姓名
    payee_id = Column(String(16), nullable=True)                        # 收款人身分證
    payee_type = Column(String(32), nullable=True)                      # 勞報/內部人員
    needs_invoice = Column(Integer, nullable=False, default=0)          # 是否需代開發票 0/1
    invoice_number = Column(String(32), nullable=True)                  # 代開發票號碼
    invoice_amount = Column(Integer, nullable=True)                     # 代開發票金額
    project_id = Column(String(32), nullable=True, index=True)          # 關聯專案
    project_label = Column(String(128), nullable=True)                  # 專案標籤（手動填）
    payment_date = Column(DateTime(timezone=True), nullable=True)       # 付款日
    payment_status = Column(String(16), nullable=False, default="未付款") # 未付款/應付款/已付款
    planned_month = Column(String(7), nullable=True)                    # 預計付款月 "2026-04"
    advance_by = Column(String(64), nullable=True)                     # 代墊人（實際收款人）
    is_advance = Column(Integer, nullable=False, default=0)            # 0=一般, 1=預支款
    advance_returned = Column(Integer, nullable=False, default=0)      # 0=未歸還, 1=已歸還
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_payreq_date", "request_date"),
        Index("idx_payreq_status", "payment_status"),
        Index("idx_payreq_planned_month", "planned_month"),
        Index("idx_payreq_payee", "payee_name"),
    )


class CrmCashEntry(Base):
    """帳務 — 收支明細（現金流日記帳）。"""
    __tablename__ = "crm_cash_entries"

    id = Column(String(32), primary_key=True)
    entry_date = Column(DateTime(timezone=True), nullable=True, index=True)
    expense = Column(Integer, nullable=True)                     # 支出
    claim = Column(Integer, nullable=True)                       # 請款
    deposit = Column(Integer, nullable=True)                     # 存入
    summary = Column(String(255), nullable=False)                # 摘要
    note = Column(Text, nullable=True)                           # 附註
    category = Column(String(32), nullable=True)                 # 類別：請款/收支/轉存
    item = Column(String(64), nullable=True)                     # 項目：專案/設備耗材/行政/轉存
    sub_item = Column(String(64), nullable=True)                 # 子項目
    payee = Column(String(128), nullable=True)                   # 收款人（姓名_身分證）
    status = Column(String(32), nullable=True)                   # 狀態
    has_invoice = Column(Integer, nullable=False, default=0)     # 發票 0/1
    invoice_number = Column(String(32), nullable=True)
    project_label = Column(String(128), nullable=True)           # 專案標籤
    project_id = Column(String(32), nullable=True)               # 關聯專案
    payment_date = Column(DateTime(timezone=True), nullable=True)
    payment_status = Column(String(16), nullable=True)           # 已付款/未付款
    invoice_id = Column(String(32), nullable=True)               # 關聯發票 ID
    bank_fee = Column(Integer, nullable=True)                    # 匯費（計入支出）
    advance_payment_id = Column(String(32), nullable=True)       # 關聯預支款 ID
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    """API Key for programmatic access (OpenClaw, scripts, CI/CD)."""
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_hash = Column(String(64), unique=True, nullable=False, index=True)  # SHA-256 hex
    key_prefix = Column(String(12), nullable=False)        # "osk_a1b2" 前幾字元，列表辨識用
    name = Column(String(64), nullable=False)              # 使用者命名（如 "OpenClaw"）
    username = Column(String(64), nullable=False)          # 所屬使用者
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)  # null = 永不過期
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    __table_args__ = (
        Index("idx_ak_username", "username"),
        Index("idx_ak_active", "is_active"),
    )


class Timesheet(Base):
    """工時紀錄（N2 工時鏈；階段 0 先收 Google Sheet 同步，藍圖 §3.6）。

    粒度=小時（對齊團隊 Sheet 實務）。status 生命週期供 N2 用：
    import（Sheet 同步）→ 未來 draft/confirmed/approved/locked（手填/排班預填）。
    staff 先存名字字串，N0 個人帳號化後補 staff_id 對映。
    """
    __tablename__ = "timesheets"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    work_date = Column(DateTime(timezone=True), nullable=True)
    staff_name = Column(String(64), nullable=False, default="")
    project_id = Column(String(32), nullable=True)               # soft FK → crm_projects.id（名稱對映成功時）
    project_name = Column(String(255), nullable=False, default="")  # Sheet 原始專案名（含「行政庶務」內部桶）
    task_note = Column(Text, nullable=True)                      # 工作內容
    hours = Column(Float, nullable=False, default=0.0)
    status = Column(String(16), nullable=False, default="import")
    source = Column(String(16), nullable=False, default="sheet")  # sheet/manual/schedule
    row_hash = Column(String(40), nullable=False, unique=True)   # 去重：date|staff|project|task|hours
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_ts_project", "project_id"),
        Index("idx_ts_staff_date", "staff_name", "work_date"),
    )


class PaymentMilestone(Base):
    """付款節點（B3 現金流：訂金/期中/尾款；N1 上線後可綁 trigger_phase 自動提醒）。"""
    __tablename__ = "payment_milestones"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    project_id = Column(String(32), nullable=False)              # soft FK → crm_projects.id
    label = Column(String(64), nullable=False, default="")       # 訂金/期中/尾款…
    amount = Column(Integer, nullable=True)                      # 金額（含稅）
    due_date = Column(DateTime(timezone=True), nullable=True)
    trigger_phase = Column(String(32), nullable=True)            # N1 phase 綁定（預留）
    status = Column(String(16), nullable=False, default="未到期")  # 未到期/待請款/已請款/已收款
    invoice_id = Column(String(32), nullable=True)               # 關聯發票（開票後回填）
    sort_order = Column(Integer, nullable=False, default=0)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_pm_project", "project_id"),
                      Index("idx_pm_status_due", "status", "due_date"))


class StaffRateHistory(Base):
    """人員日費率歷史（H1）— 費率調整不改寫歷史；N2 工時成本與 B2 複盤按
    work_date 當時費率取值，否則歷史毛利被現在費率污染。"""
    __tablename__ = "staff_rate_history"

    id = Column(String(32), primary_key=True)
    staff_id = Column(String(32), nullable=False)                # soft FK → crm_staff.id
    day_rate = Column(Integer, nullable=False, default=0)
    effective_from = Column(DateTime(timezone=True), nullable=False)
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_srh_staff_from", "staff_id", "effective_from"),)


class FinanceMonthClose(Base):
    """月結鎖帳（F1）— 鎖定月份的收支不可改；snapshot 留當月彙總供報表重現。"""
    __tablename__ = "finance_month_close"

    id = Column(String(32), primary_key=True)
    month = Column(String(7), nullable=False, unique=True)       # 'YYYY-MM'
    closed_by = Column(String(64), nullable=False, default="")
    closed_at = Column(DateTime(timezone=True), server_default=func.now())
    snapshot = Column(JSONB, nullable=True)                      # {income, expense, by_category, entry_count}
    reopened_by = Column(String(64), nullable=True)              # reopen 留稽核痕跡
    reopened_at = Column(DateTime(timezone=True), nullable=True)


class PreprodLocation(Base):
    """場景庫（P-a）— 場勘成果資產化：勘過一次＝永久資產（docs/PREPROD_PLAN.md A 段）。"""
    __tablename__ = "preprod_locations"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    name = Column(String(128), nullable=False)
    category = Column(String(32), nullable=True)                 # 咖啡廳/工廠/辦公室/戶外/官署…
    region = Column(String(16), nullable=True)                   # 縣市
    address = Column(String(255), nullable=True)
    contact_name = Column(String(64), nullable=True)
    contact_phone = Column(String(32), nullable=True)
    permit_required = Column(Integer, nullable=False, default=0)  # 0/1 需申請拍攝許可
    permit_note = Column(Text, nullable=True)                    # 申請流程/窗口備註
    fee_note = Column(String(255), nullable=True)                # 費用註記
    attributes = Column(JSONB, nullable=True)                    # 自由 dict：電源/收音/自然光/停車/廁所/可用時段…
    tags = Column(JSONB, nullable=True)                          # list[str]
    note = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="可用")   # 可用/黑名單/已消失
    cover_url = Column(String(512), nullable=True)               # 第一張照片自動帶入
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_ploc_category", "category"),
                      Index("idx_ploc_region", "region"))


class PreprodLocationPhoto(Base):
    """場景照片索引（檔案存 uploads/locations/{location_id}/）。"""
    __tablename__ = "preprod_location_photos"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    location_id = Column(String(32), nullable=False, index=True)  # soft FK → preprod_locations.id
    url = Column(String(512), nullable=True)                     # /uploads/locations/{lid}/{fname}
    caption = Column(String(255), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PreprodLocationUsage(Base):
    """場景使用履歷 — 哪些專案用過＋評分＋踩雷心得（lesson 是資產的靈魂）。"""
    __tablename__ = "preprod_location_usages"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    location_id = Column(String(32), nullable=False, index=True)  # soft FK → preprod_locations.id
    project_id = Column(String(32), nullable=True)               # soft FK → crm_projects.id
    used_date = Column(DateTime(timezone=True), nullable=True)
    rating = Column(Integer, nullable=True)                      # 1-5
    lesson = Column(Text, nullable=True)                         # 心得/踩雷
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PreprodProposal(Base):
    """提案庫（P-b）— 提案智財資產化 + win/loss 學習迴圈（docs/PREPROD_PLAN.md B 段）。"""
    __tablename__ = "preprod_proposals"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    title = Column(String(255), nullable=False)
    client_id = Column(String(32), nullable=True)                # soft FK → clients.id
    project_id = Column(String(32), nullable=True)               # soft FK → crm_projects.id（成案後回填）
    quotation_id = Column(String(32), nullable=True)             # soft FK → crm_quotations.id
    ptype = Column(String(32), nullable=True)                    # 形象/廣告/紀錄片/政府標案/社群/其他
    status = Column(String(16), nullable=False, default="草稿")   # 草稿/已提案/入圍/成案/未成案/擱置
    pitch_date = Column(DateTime(timezone=True), nullable=True)  # 提案日
    budget_range = Column(String(64), nullable=True)             # 預算範圍（自由文字）
    deck_url = Column(String(512), nullable=True)                # /uploads/proposals/{pid}/{fname}（簡報原檔）
    outcome_reason = Column(Text, nullable=True)                 # 成案/未成案必填原因 — 組織學習欄
    tags = Column(JSONB, nullable=True)                          # list[str]
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_pprop_client", "client_id"),
                      Index("idx_pprop_status", "status"))


class PreprodReference(Base):
    """參考片庫 — 獨立於單一提案的共用參考片，跨提案重用。"""
    __tablename__ = "preprod_references"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    url = Column(String(512), nullable=False)                    # 參考片連結（YouTube/Vimeo…）
    title = Column(String(255), nullable=True)
    note = Column(Text, nullable=True)
    tags = Column(JSONB, nullable=True)                          # list[str]
    thumb_url = Column(String(512), nullable=True)               # 縮圖（可選）
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PreprodProposalRef(Base):
    """提案 ↔ 參考片 多對多關聯（刪提案只刪關聯列，reference 是共用資產保留）。"""
    __tablename__ = "preprod_proposal_refs"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    proposal_id = Column(String(32), nullable=False, index=True)  # soft FK → preprod_proposals.id
    reference_id = Column(String(32), nullable=False)            # soft FK → preprod_references.id


class IntelSource(Base):
    """產業情報來源白名單（P-c，docs/PREPROD_PLAN.md C 段）— 白名單外不抓。"""
    __tablename__ = "intel_sources"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    name = Column(String(128), nullable=False, default="")
    type = Column(String(8), nullable=False, default="rss")      # rss / html（html 第一版先跳過）
    url = Column(String(512), nullable=False)
    keywords = Column(JSONB, nullable=True)                      # list[str] 關鍵字過濾；空 = 全收
    enabled = Column(Integer, nullable=False, default=1)         # 0/1 kill switch（逐源）
    last_fetched_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class IntelItem(Base):
    """產業情報項目 — 只存標題+摘要+原文連結（不轉貼全文，版權）。
    claude 摘要/分類/評分/抽截止日；claude 不可用時降級存原始 title（category=未分類）。"""
    __tablename__ = "intel_items"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    source_id = Column(String(32), nullable=False, index=True)   # soft FK → intel_sources.id
    url = Column(String(512), nullable=False)
    url_hash = Column(String(40), nullable=False, unique=True)   # sha1(url) 去重
    title = Column(String(512), nullable=False, default="")
    summary = Column(Text, nullable=True)
    category = Column(String(16), nullable=False, default="未分類")  # 標案/補助/產業/技術/競品/未分類
    score = Column(Integer, nullable=False, default=0)           # 0-100 商機相關性
    deadline = Column(DateTime(timezone=True), nullable=True)    # 標案/補助截止日（claude 抽取）
    status = Column(String(16), nullable=False, default="new")   # new/starred/archived/converted
    proposal_id = Column(String(32), nullable=True)              # 轉提案後回填 → preprod_proposals.id
    fetched_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_intel_status_score", "status", "score"),)


class PortalReviewLink(Base):
    """看片審批客戶門戶（B1，docs/BIZ_PLAN.md B1 段）— 送審連結：
    一個剪輯版本＝一條 token 連結，客戶免登入用 /review.html?token= 看片留言核准。"""
    __tablename__ = "portal_review_links"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    project_id = Column(String(32), nullable=False, index=True)  # soft FK → crm_projects.id
    version_label = Column(String(64), nullable=True)            # 初剪/一修/定剪…
    video_path = Column(String(512), nullable=True)              # master 本機影片路徑（絕不回給客戶端）
    token = Column(String(64), unique=True, nullable=False)      # secrets.token_urlsafe(32)
    status = Column(String(16), nullable=False, default="待審")   # 待審/修改中/已核准
    approved_by = Column(String(64), nullable=True)              # 客戶核准時留名
    approved_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # null = 永不過期
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PortalComment(Base):
    """看片門戶意見 — 時間軸精準留言（客戶端免登入、留名即可）。"""
    __tablename__ = "portal_comments"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    link_id = Column(String(32), nullable=False, index=True)     # soft FK → portal_review_links.id
    timecode_sec = Column(Float, nullable=False, default=0)      # 留言時間點（秒）
    body = Column(Text, nullable=False)
    author_name = Column(String(64), nullable=True)
    resolved = Column(Integer, nullable=False, default=0)        # 0/1 已處理
    created_at = Column(DateTime(timezone=True), server_default=func.now())
