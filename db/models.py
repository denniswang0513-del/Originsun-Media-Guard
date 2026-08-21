"""SQLAlchemy ORM models for Originsun Media Guard Pro."""

try:
    from sqlalchemy import Column, String, Text, Boolean, Integer, BigInteger, Float, Date, DateTime, func, Index, UniqueConstraint
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.orm import DeclarativeBase
    _HAS_SQLALCHEMY = True
except ImportError:
    _HAS_SQLALCHEMY = False
    # Provide stubs so module can be imported without crashing
    class _Stub:
        def __call__(self, *a, **kw): return self
        def __getattr__(self, _): return self
    Column = String = Text = Boolean = Integer = BigInteger = Float = Date = DateTime = func = Index = UniqueConstraint = _Stub()
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
    assignee = Column(String(16), nullable=False, default="me")      # me / claude（交辦收件匣，全隊共用）
    assignee_username = Column(String(64), nullable=True, index=True)  # N0：指派給個人（→ users.username），供「我的待辦」
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

    @classmethod
    def mine_filter(cls, username: str):
        """「與我有關」的唯一定義：指派給我，或我建立的（排除丟給 Claude 的
        交辦收件匣）。api_bulletin(mine=1) 與 api_me 個人工作台共用。"""
        return ((cls.assignee_username == username)
                | ((cls.created_by == username) & (cls.assignee != "claude")))


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
    # ── N0 個人帳號化：登入帳號 ↔ 人力庫人員（soft FK → crm_staff.id）──
    #    nullable：既有帳號未綁定不受影響；個人工作台/工時/獎金全靠這條橋。
    staff_id = Column(String(32), nullable=True, index=True)


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
    # soft FK → clients.id。提案=專案合體（2026-08-06）後可空 —— 前期草稿提案
    # 常常還沒定客戶，卻已經是管線「提案」階段的專案。手建專案仍要求選客戶
    # （CrmProjectPayload.client_id 必填 + 前端擋），只有提案建殼路徑允許空。
    client_id = Column(String(32), nullable=True)
    # 提案庫資產夾名（settings proposals.root 底下的子夾 `{建立日}_{專案名}`）。
    # 首次用到才生成、之後固定存這裡；專案改名時由 core.project_folders 跟著改。
    proposal_folder_name = Column(String(255), nullable=True)
    archive_checklist = Column(JSONB, nullable=True)             # 結案歸檔清單（範本正本在 core/project_archive.py）
    review_kpta = Column(JSONB, nullable=True)                   # 專案回顧 Keep/Problem/Try/Action
    flow_checks = Column(JSONB, nullable=True)                   # 工作流手動里程碑（範本正本在 core/project_flow.py）
    status = Column(String(32), nullable=False, default="洽詢")
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
    # 結案專案的官網製作進度：待製作 / 製作中 / 不上官網。
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
    # N-hr H2：年度特休額度（天）。餘額不另存 ledger — 即時算＝額度 − 當年度
    # 已核准特休 days 合計（core/hr_logic.leave_balance）。
    annual_leave_days = Column(Integer, nullable=True)
    # 零用金備用金（imprest）：這個人手上長期持有多少公司現金。0 ＝ 自己先墊、事後全額請款
    # （實測 8 個零用金帳戶只有 1 人持有 10,000）。應請款＝Σ單據，與這個數字無關 ——
    # 它只決定「期末手上還有多少現金」的顯示。
    petty_float = Column(Integer, nullable=False, default=0)   # 備用金金額（手上長期持有的公司現金）
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
    """官網「作品」實體（一個對外 /works/{slug} 頁面）。

    1:N 改造（2026-07）：id = work id、project_id = 所屬 CRM 專案。
    既有資料 id == project_id（歷史 1:1 時代 PK 直接用 project_id），該列即「主作品」；
    新增子作品 id 用 uuid4().hex。作品身分欄位（title/slug/number/featured/驗收章…）
    以本表為單一真相；crm_projects.public_* 為過渡期鏡射（僅主作品 dual-write）。
    """
    __tablename__ = "crm_project_showcase"

    id = Column(String(32), primary_key=True)  # work id（既有列 == project_id）
    project_id = Column(String(32), nullable=True, index=True)  # 所屬專案（backfill = id）
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
    # ── 作品身分欄位（1:N 改造自 crm_projects.public_* 下放；backfill 見 migrations_website）──
    title = Column(String(200), nullable=True)
    title_en = Column(String(300), nullable=True)
    description_en = Column(Text, nullable=True)
    youtube_id = Column(String(20), nullable=True)  # video_url parse 後快取
    extra_videos = Column(JSONB, nullable=True)  # [{url, caption}] 主影片以外的附加影片
    year = Column(Integer, nullable=True)
    featured = Column(Boolean, nullable=False, default=False)
    featured_image = Column(Text, nullable=True)
    noindex = Column(Boolean, nullable=False, default=False)
    number = Column(Integer, nullable=True)  # 對外連續編號（partial unique idx）
    old_slugs = Column(JSONB, nullable=True)  # 舊 slug 清單（301 轉址來源）
    sort_order = Column(Integer, nullable=False, default=0)
    # 作品系列（跨專案策展集合，soft FK → website_series.id）；作品牆摺疊 + 系列頁
    series_id = Column(Integer, nullable=True, index=True)
    series_order = Column(Integer, nullable=False, default=0)   # 系列內排序（小→大）
    verified_at = Column(DateTime(timezone=True), nullable=True)  # rebuild 後對外頁實測 200
    prod_stage = Column(String(16), nullable=True)  # 待製作|製作中|已上線|不上官網
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
    """支出單據（原「專案雜支明細」）。

    🔴 2026-08-17 起這張表同時是**零用金請款的單據行**（docs/PETTY_CASH_PLAN.md）：
    一筆登記同時餵「專案成本」與「個人請款」兩邊，不再兩處各記一份。
    因此 `project_id` 放寬成可空 —— 公司層級支出（行政/業務推廣/設備耗材）沒有專案，
    實測歷史資料 289/443 列如此。
    """
    __tablename__ = "crm_project_expenses"

    id = Column(String(32), primary_key=True)
    project_id = Column(String(32), nullable=True, index=True)   # 可空＝公司層級支出
    cost_group_id = Column(String(32), nullable=True, index=True)  # FK → crm_project_cost_groups
    category = Column(String(64), nullable=False)               # 交通/住宿/飲食/提案/其他
    estimated = Column(Integer, nullable=False, default=0)      # 預估金額
    actual = Column(Integer, nullable=False, default=0)         # 實際金額
    receipt_url = Column(String(512), nullable=True)            # 收據連結 ← 財務預留
    sub_item = Column(String(128), nullable=True)              # 細項
    payee = Column(String(64), nullable=True)                  # 請款人（非員工時的文字備援）
    advance_id = Column(String(32), nullable=True)             # 關聯預支款 ID
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
    # ── 零用金請款（PETTY_CASH_PLAN §2.1）────────────────────────────────
    # 🔴 認列時點：專案成本一律用 expense_date（消費日），不是請款日/匯款日。
    # created_at 是「登記進系統的時間」，兩者常差好幾週，不能混用。
    expense_date = Column(DateTime(timezone=True), nullable=True, index=True)
    staff_id = Column(String(32), nullable=True, index=True)    # 墊錢的人 → crm_staff.id
    item = Column(String(32), nullable=True)                    # 會計項目 → finance_category_map
    invoice_no = Column(String(32), nullable=True)              # 發票號/收據字號
    has_invoice = Column(Integer, nullable=False, default=0)    # 0/1
    claim_id = Column(String(32), nullable=True, index=True)    # 歸屬請款批次（NULL＝未送出）
    status = Column(String(16), nullable=False, default="草稿")  # 草稿/待審/已核准/已付款/退回
    project_label = Column(String(128), nullable=True)          # 匯入時沒對到專案的原始標籤文字
    # 🔴 費用歸屬人 ≠ 墊款人（owner 2026-08-17：「後期雜支是要王士源付款的」）。
    # staff_id = 誰掏的錢（公司要匯給他）；owner_staff_id = 誰負擔這筆費用
    # （公司要跟他收回）。兩者相同或 NULL ＝ 公司自己吸收，沒有內部往來。
    # 沒有這個欄位的話，「別人幫你墊、但帳算你的」只能靠人腦記，而那正是
    # 王士源那筆 −647 在系統裡湊不出來的原因。
    owner_staff_id = Column(String(32), nullable=True, index=True)
    owner_settled = Column(Integer, nullable=False, default=0)   # 歸屬人是否已還這筆


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
    entity = Column(String(16), nullable=False, server_default="parent")   # 兩本帳：parent=母公司（預設）/mine=我的帳（docs/LEDGER_ENTITY_PLAN.md）
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
    paid_date = Column(DateTime(timezone=True), nullable=True)           # 收款日（AR 收現時間戳，財務階段二）
    file_url = Column(String(512), nullable=True)                        # 已開立的電子發票檔（PDF/圖），存磁碟絕對路徑
    share_token = Column(String(512), nullable=True, index=True)         # 給客戶下載電子發票的短碼（/e/{code}，逐字比對）
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
    entity = Column(String(16), nullable=False, server_default="parent")   # 兩本帳：parent=母公司（預設）/mine=我的帳（docs/LEDGER_ENTITY_PLAN.md）
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
    # 代開自動化的**冪等鍵**（owner 2026-08-21）。原本拿 invoice_number 當鍵，
    # 但無號發票（代開還沒拿到號碼）就沒有鍵可用 → 自動化整個跳過，收款掛上去
    # 也不會進請款單，而且沒有任何跡象。發票 id 一定有、一定唯一。
    source_invoice_id = Column(String(32), nullable=True, index=True)
    project_id = Column(String(32), nullable=True, index=True)          # 關聯專案
    project_label = Column(String(128), nullable=True)                  # 專案標籤（手動填）
    payment_date = Column(DateTime(timezone=True), nullable=True)       # 付款日
    payment_status = Column(String(16), nullable=False, default="未付款") # 未付款/應付款/已付款
    planned_month = Column(String(7), nullable=True)                    # 預計付款月 "2026-04"
    advance_by = Column(String(64), nullable=True)                     # 代墊人（實際收款人）
    is_advance = Column(Integer, nullable=False, default=0)            # 0=一般, 1=預支款
    advance_returned = Column(Integer, nullable=False, default=0)      # 0=未歸還, 1=已歸還
    # 零用金批次產生的應付款（一張批次 → 多張 AP：會計項目 × 認列月份）。
    # 🔴 方向是 AP→批次 而不是批次→AP：一對多的那一邊才存得下。
    reimbursement_id = Column(String(32), nullable=True, index=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_payreq_date", "request_date"),
        Index("idx_payreq_status", "payment_status"),
        Index("idx_payreq_planned_month", "planned_month"),
        Index("idx_payreq_payee", "payee_name"),
    )


class CrmReimbursement(Base):
    """零用金請款批次 —— 一個人、一段期間、一次請款（PETTY_CASH_PLAN §2.2）。

    單據行（CrmProjectExpense）掛 claim_id 過來；狀態機在批次上，行只跟隨。
    核准時產一張 CrmPaymentRequest(category="零用金") → 之後完全走既有 AP 流程
    （應付帳款月份分組 / 月結鎖帳 / 銀行對帳 / 三表），財務側零新程式碼。
    """
    __tablename__ = "crm_reimbursements"

    id = Column(String(32), primary_key=True)
    staff_id = Column(String(32), nullable=True, index=True)   # 可空＝非員工（外部人員代墊）
    staff_name = Column(String(64), nullable=False)            # 快照（人員改名不影響歷史單）
    period_start = Column(DateTime(timezone=True), nullable=True)
    period_end = Column(DateTime(timezone=True), nullable=True)
    opening_float = Column(Integer, nullable=False, default=0)  # 期初備用金金額快照
    total_claim = Column(Integer, nullable=False, default=0)    # 應請款金額（Σ 單據行；可為負＝應向本人收回的款項）
    closing_float = Column(Integer, nullable=False, default=0)  # 期末金額＝期初 − 已花
    status = Column(String(16), nullable=False, default="草稿")  # 草稿/待審/已核准/已付款/退回
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    approved_by = Column(String(64), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_reimb_staff_status", "staff_id", "status"),
    )


class CrmCashEntry(Base):
    """帳務 — 收支明細（現金流日記帳）。"""
    __tablename__ = "crm_cash_entries"

    id = Column(String(32), primary_key=True)
    entity = Column(String(16), nullable=False, server_default="parent")   # 兩本帳：parent=母公司（預設）/mine=我的帳（docs/LEDGER_ENTITY_PLAN.md）
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
    bank_account_id = Column(String(32), nullable=True)          # 掛哪個銀行帳戶（財務階段二）
    payment_request_id = Column(String(32), nullable=True)       # AP 硬連結 → crm_payment_requests
    loan_payment_id = Column(String(32), nullable=True)          # 貸款繳款硬連結 → finance_loan_payments（treatment=loan，不進損益）
    expense_id = Column(String(32), nullable=True, index=True)   # 零用金逐行落帳硬連結 → crm_project_expenses（重跑不重複記帳）
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class CrmCashInvoiceLink(Base):
    """收款 ↔ 發票的分配明細（多對多，帶金額）。

    為什麼不是 crm_cash_entries.invoice_id 一個欄位就好 —— 真實帳有兩種一對多：
      合併匯款：客戶一次匯 3 張發票的錢 → 一筆收款要掛多張發票
      分期收款：一張發票分兩次收 → 一張發票要掛多筆收款
    兩者都是 (收款, 發票, 這次分配多少) 的關係，一張連結表同時解決。

    `crm_cash_entries.invoice_id` 保留為「主要發票」（既有查詢與 UI 都靠它），
    寫入連結時一併同步成金額最大的那張 —— 舊路徑不會因為這張表而失準。
    """
    __tablename__ = "crm_cash_invoice_links"

    id = Column(String(32), primary_key=True)
    cash_entry_id = Column(String(32), nullable=False, index=True)  # soft FK → crm_cash_entries.id
    invoice_id = Column(String(32), nullable=False, index=True)     # soft FK → crm_invoices.id
    amount = Column(Integer, nullable=False, default=0)             # 這筆收款分配到這張發票的金額
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # 同一筆收款不會對同一張發票分配兩次（要改金額就改那一列）
        UniqueConstraint("cash_entry_id", "invoice_id", name="uq_cashinv_entry_invoice"),
    )


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
    staff_id = Column(String(32), nullable=True, index=True)     # N0 對映（手填列必帶；sheet 列待 N2 回填）
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


class HrLeaveRequest(Base):
    """請假單（N-hr H2 極簡版：申請 + 核可 + 額度；不做打卡鐘）。

    審核採「三欄形」慣例（藍圖 §7-E 的極簡審核；欄位形狀與 api_portal 一致，
    N2 工時核可 / N3 獎金簽核建表時沿用同三欄，轉換邏輯各自實作）：
    `status` + `approved_by` + `approved_at`。狀態離開「已核准」時兩欄清空；
    已核准再核准回 409。特休餘額即時算不另存 ledger
    （crm_staff.annual_leave_days − 當年度已核准特休合計）。
    """
    __tablename__ = "hr_leave_requests"

    id = Column(String(32), primary_key=True)                     # uuid4 hex
    staff_id = Column(String(32), nullable=False, index=True)     # soft FK → crm_staff.id
    staff_name = Column(String(64), nullable=False, default="")   # 冗餘顯示用（申請當下快照）
    leave_type = Column(String(16), nullable=False)               # 特休/病假/事假/公假/婚假/喪假/其他
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    days = Column(Float, nullable=False, default=1.0)             # 0.5 步進（H2 文件以天計）
    reason = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="待審")    # 待審/已核准/已退回
    approved_by = Column(String(64), nullable=True)               # 核可人 username
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(64), nullable=True)                # 申請/代登者 username
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_leave_status", "status"),
        Index("idx_leave_staff_start", "staff_id", "start_date"),
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
    entity = Column(String(16), nullable=False, server_default="parent")  # 兩本帳：parent=母公司（預設）/mine=我的帳（docs/LEDGER_ENTITY_PLAN.md）
    month = Column(String(7), nullable=False)                    # 'YYYY-MM'（unique 改為 (entity, month) 複合）
    closed_by = Column(String(64), nullable=False, default="")
    closed_at = Column(DateTime(timezone=True), server_default=func.now())
    snapshot = Column(JSONB, nullable=True)                      # {income, expense, by_category, entry_count}
    reopened_by = Column(String(64), nullable=True)              # reopen 留稽核痕跡
    reopened_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("entity", "month",
                                       name="uq_month_close_entity_month"),)


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
    notes = Column(Text, nullable=True)                          # 基本資料備註（自由文字，共編可編；§9.7）
    plan = Column(JSONB, nullable=True)                          # 企劃矩陣（docs/PROPOSAL_PLANNER.md §3；逐格帶 updated_at/by）
    survey = Column(JSONB, nullable=True)                        # 現況盤點表（欄目正本在 core/proposal_survey.py，這裡只存值）
    # 重點提案：勾起來的資產（core/pinned_assets.py）。取代舊的「對外分享」子夾——
    # 勾選是策展、不搬檔案；pins_public 才是「客戶看不看得到」那道授權開關。
    pinned_assets = Column(JSONB, nullable=True)                  # list[{rel,is_dir,thumb_url,...}]
    pins_public = Column(Boolean, nullable=False, default=False)  # 預設關：勾選 ≠ 給客戶看
    # 這筆提案在專案資產夾底下的子夾（相對專案夾）。空 = 家就是專案夾根
    # （合體前建的既有提案都是這樣，零遷移）。一個專案多筆提案時，各自
    # 一個子夾才不會把幾個 concept 的檔案混在一起。
    folder_subpath = Column(String(255), nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_pprop_client", "client_id"),
                      Index("idx_pprop_status", "status"),
                      # 提案=專案合體：GET /crm/projects 每列 scalar subquery
                      # 以 project_id 過濾 + updated_at DESC LIMIT 1 — 走這顆
                      Index("idx_pprop_project", "project_id", "updated_at"))


class PreprodBrief(Base):
    """提案的**企劃書**（Markdown）—— 企劃矩陣是思考過程，這個是輸出物。

    多版並存（owner 2026-08-10）：「重新生成」是**新增一版**不是覆蓋。矩陣天天
    在改，不留版本的話「這份企劃書是根據哪一版矩陣寫的」永遠說不清 —— 所以
    每一版都存**生成當下的矩陣快照**。

    🔴 生成物**不寫回 plan JSONB**：那是人的共編工作區，有逐格 updated_by 與
    樂觀鎖，AI 一次寫 12 格會把共編紀錄整片洗掉、也會蓋掉同事正在打的字。
    """
    __tablename__ = "preprod_briefs"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    proposal_id = Column(String(32), nullable=False, index=True)
    content = Column(Text, nullable=True)                        # Markdown 正文
    # 生成當下的輸入：用了哪幾份範本、勾了哪些資料、篇幅語氣
    template_ids = Column(JSONB, nullable=True)                  # list[str]
    options = Column(JSONB, nullable=True)                       # {length, tone, include:[...]}
    plan_snapshot = Column(JSONB, nullable=True)                 # 那一刻的矩陣
    status = Column(String(16), nullable=True)                   # pending/ok/failed
    error = Column(Text, nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class PreprodBriefTemplate(Base):
    """企劃範本庫 —— 生成企劃書時給 Claude 參考的「好範本」。

    實體檔案放在 `{提案資產根目錄}/_範本/` 底下（owner 指定的落點）；這張表存
    索引 + **消化出來的骨架**。骨架才是生成時真正餵進 prompt 的東西：一份完整
    企劃可能上萬字，塞兩三份就把 prompt 撐爆，而且每次生成都重讀同樣的內容。

    🔴 骨架刻意存純文字而不是 JSONB：它是**人要看、人會改**的東西（不滿意就
    直接改那段，比調 prompt 直觀）。結構化只會逼使用者透過表單改字。

    「設為範本」是**複製**一份到 _範本，不是記指標 —— 提案會繼續改版（v2/v3），
    範本應該是凍結的參考；原檔被刪或改名時範本也不該跟著斷。
    """
    __tablename__ = "preprod_brief_templates"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    name = Column(String(255), nullable=False)                   # 顯示名（可改）
    filename = Column(String(255), nullable=False)               # _範本 底下的檔名
    # 從某個專案的資產檔案「設為範本」時記來源（純為了回溯，斷了也不影響使用）
    source_project_id = Column(String(32), nullable=True)
    source_rel = Column(String(512), nullable=True)
    skeleton = Column(Text, nullable=True)                       # 消化後的骨架（餵 prompt 的就是它）
    status = Column(String(16), nullable=True)                   # pending/ok/failed
    error = Column(Text, nullable=True)                          # 消化失敗的理由（給人看）
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class PreprodQuoteFile(Base):
    """提案的**外部報價檔**（多版本）—— 客戶回簽版、廠商比價版這類不是 CRM
    報價模組產的檔案。CRM 報價（crm_quotations）仍是主清單，這張表只登記
    「上傳了哪些檔、第幾版」；實體檔案住在專案資產夾的「報價單」子夾。

    🔴 報價單含金額，是內部敏感資料 —— 這張表的任何欄位都**不准**出現在
    公開 `?t=` token 端點（同 budget_range 的既有慣例），檔案也不進 web root
    靜態直出，下載一律走帶權限的端點。
    """
    __tablename__ = "preprod_quote_files"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    proposal_id = Column(String(32), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)         # 每提案 max+1
    rel = Column(String(512), nullable=False)                    # 相對專案資產夾（含報價單子夾）
    filename = Column(String(255), nullable=False)               # 落地後的檔名
    note = Column(Text, nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PreprodQuoteAnalysis(Base):
    """報價單的 AI 分析（多次分析各一筆，照 PreprodBrief 的多版模式）。

    inputs 存分析當下用了哪些檔/哪些 CRM 報價版本的快照 —— 檔案與報價之後
    都會繼續長，不存的話「這份分析是根據什麼寫的」說不清。status 生命週期
    與企劃書相同（pending/ok/failed + 讀取端 core.bg_status.settle 自癒）。
    """
    __tablename__ = "preprod_quote_analyses"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    proposal_id = Column(String(32), nullable=False, index=True)
    content = Column(Text, nullable=True)                        # Markdown
    inputs = Column(JSONB, nullable=True)                        # {files:[...], crm_quotations:[...]}
    status = Column(String(16), nullable=True)                   # pending/ok/failed
    error = Column(Text, nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class PreprodMeetingNote(Base):
    """提案的**會議記錄**（多筆，一次會議一筆）。

    與企劃書/報價分析不同：這裡沒有版本快照 —— 會議記錄是**人寫的原始素材**，
    是創意發想與企劃書的輸入，不是產出物。AI 只做**輸入輔助**：上傳錄音檔 →
    whisper 逐字稿 → claude 整理（services/meeting_transcriber）；`content` 是
    人的正本，AI 只在它**全空**時代填一次，之後絕不覆蓋。

    🔴 內部資料：不出**提案層**的公開 `?t=` 共編端點（客戶會議也可能記到
    內部判斷、競品、報價底線）。唯一的例外是**單篇唯讀分享**（owner
    2026-08-15 拍板）：對某一筆明確按「分享」才鑄 `share_token`，公開端點
    只出 met_at/title/attendees/content 四欄 —— 逐字稿、AI 整理、錄音、
    提案歸屬都不出。
    """
    __tablename__ = "preprod_meeting_notes"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    proposal_id = Column(String(32), nullable=False, index=True)
    met_at = Column(DateTime(timezone=True), nullable=True)      # 會議日期（日曆日，同 pitch_date 下錨）
    title = Column(String(255), nullable=True)                   # 會議主題
    attendees = Column(String(512), nullable=True)               # 出席者（自由文字，不綁人員庫）
    content = Column(Text, nullable=True)                        # 記錄本文（人的正本）
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    # ── 錄音管線（status 生命週期同企劃書：pending/ok/failed + settle 自癒；
    #    沒傳過錄音 = NULL）──
    audio_rel = Column(String(512), nullable=True)               # 錄音檔（相對專案資產夾）
    transcript = Column(Text, nullable=True)                     # whisper 逐字稿（[時間] 一句一行）
    ai_summary = Column(Text, nullable=True)                     # claude 整理（Markdown）
    status = Column(String(16), nullable=True)
    error = Column(Text, nullable=True)
    phase = Column(String(64), nullable=True)                    # pending 期間的階段字（辨識中 37% …）
    # 單篇唯讀分享：按「分享」才鑄（new_share_token）、撤銷＝清空；
    # 驗證走逐字比對（v2.4.52 起的慣例 —— jwt_secret 輪替不殺分享連結）
    share_token = Column(String(512), nullable=True)


# 提案刪除時要一併清的子表（proposal_id 歸屬列）。清單住在表定義旁邊 ——
# 新增一張帶 proposal_id 的表時，決策點就在你剛寫完 model 的下一行，而不是
# 在 api_proposals.delete_proposal 的深處（PreprodBrief 就曾因清單只存在
# handler 裡而漏掉，孤兒列沒人清）。刻意不含：PreprodReferenceLink（多型
# 關聯，條件是 target_type+target_id 對）、PreprodProposalRef（LEGACY 凍結）、
# IntelItem（proposal_id 是回填參照，情報不屬於提案）。
PROPOSAL_CHILD_TABLES = (PreprodBrief, PreprodQuoteFile, PreprodQuoteAnalysis,
                         PreprodMeetingNote)


class PreprodReference(Base):
    """參考片庫 — 獨立於單一提案的共用參考片，跨提案重用。
    v2（docs/REFERENCE_LIBRARY.md）：每支片一個小頁面 —— facets 分類族、
    research 研究四欄、截圖（preprod_reference_shots）、引用（preprod_reference_links）。"""
    __tablename__ = "preprod_references"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    url = Column(String(512), nullable=False)                    # 參考片連結（YouTube/Vimeo…）
    title = Column(String(255), nullable=True)
    note = Column(Text, nullable=True)                           # 一句話心得（Notion「備註」）
    tags = Column(JSONB, nullable=True)                          # list[str] — v2 起正本在 facets.keyword
    thumb_url = Column(String(512), nullable=True)               # 縮圖（可選）
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # ── v2 ──
    description = Column(Text, nullable=True)                    # 長文說明（Notion「說明」）
    facets = Column(JSONB, nullable=True)                        # 分類族：category/brand/studio/paragon/technique/emotion/keyword/study
    research = Column(JSONB, nullable=True)                      # 研究四欄（逐格 {answer,updated_at,updated_by}）
    curated = Column(Boolean, nullable=True)                     # 建檔完成
    provider = Column(String(16), nullable=True)                 # youtube/vimeo/facebook/link（parse_video_url 快取）
    video_id = Column(String(64), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    # ── 影片封存（docs/REFERENCE_LIBRARY.md §12）——「已建檔」＝影片已存進 NAS ──
    archive_status = Column(String(16), nullable=True)           # NULL=待處理/pending/downloading/done/retry/unavailable/excluded
    archive_path = Column(String(512), nullable=True)            # NAS 上的影片檔 UNC 路徑
    archive_error = Column(Text, nullable=True)                  # 最後一次失敗原因（人可讀）
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archive_tries = Column(Integer, nullable=True)


class PreprodReferenceShot(Base):
    """參考影片截圖（v2 階段 2）—— 圖存 uploads/references/{reference_id}/{id}.webp，
    標示存 annotations JSONB（相對座標的圖形清單，不燒進圖 → 可再編輯、原圖不失真）。"""
    __tablename__ = "preprod_reference_shots"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    reference_id = Column(String(32), nullable=False, index=True)  # soft FK → preprod_references.id
    image_url = Column(String(512), nullable=False)              # /uploads/references/{rid}/{id}.webp
    timecode = Column(String(16), nullable=True)                 # 影片時間碼，如 "0:42"
    caption = Column(String(255), nullable=True)                 # 這張要看什麼
    annotations = Column(JSONB, nullable=True)                   # {v:1, shapes:[{type,x,y,...}]}
    sort_order = Column(Integer, nullable=True)
    created_by = Column(String(64), nullable=True)               # 內部帳號 or 「名字(外部)」（顯示用）
    created_key = Column(String(64), nullable=True)              # 公開路徑的瀏覽器不可見識別（「只刪自己的」憑證）
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PreprodReferenceLink(Base):
    """參考影片 ↔ 引用對象（v2 階段 3 的多型引用；取代只綁提案的 PreprodProposalRef）。

    target_type: 'proposal' | 'crm_project'（未來可加 'work'）。同一支片可以同時被
    提案與專案引用，note 記「本案為什麼引用它」—— 同一支片在不同案子的用途不同。
    刪引用只斷連結，片庫本體保留（共用資產語意）。
    """
    __tablename__ = "preprod_reference_links"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    reference_id = Column(String(32), nullable=False, index=True)  # soft FK → preprod_references.id
    target_type = Column(String(16), nullable=False)
    target_id = Column(String(32), nullable=False, index=True)
    note = Column(Text, nullable=True)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("reference_id", "target_type", "target_id",
                                       name="uq_ref_link"),)


class PreprodProposalRef(Base):
    """[LEGACY，階段 3 起停寫] 提案 ↔ 參考片舊關聯表。

    資料已由 main.py startup 一次性搬進 preprod_reference_links（冪等 INSERT）。
    保留一版當回滾餘裕，確認無誤後可刪表。**不要再往這裡寫**。
    """
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


class Equipment(Base):
    """器材庫（B4，docs/BIZ_PLAN.md B4 段）— 成本真相的最後一塊：折舊攤提+稼動率。"""
    __tablename__ = "equipment"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    name = Column(String(128), nullable=False)
    category = Column(String(32), nullable=True)                 # 機身/鏡頭/燈光/收音/週邊/其他
    serial = Column(String(64), nullable=True)                   # 序號
    purchase_date = Column(DateTime(timezone=True), nullable=True)
    purchase_cost = Column(Integer, nullable=True)               # 購入成本
    depreciation_months = Column(Integer, nullable=False, default=36)  # 直線攤提月數
    status = Column(String(16), nullable=False, default="在庫")   # 在庫/出勤/維修/除役
    retired_date = Column(DateTime(timezone=True), nullable=True)  # 除役日（折舊自該月停止，財務階段二）
    note = Column(Text, nullable=True)
    cover_url = Column(String(512), nullable=True)               # /uploads/equipment/{eid}/{fname}
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_equip_category", "category"),
                      Index("idx_equip_status", "status"))


class EquipmentCheckout(Base):
    """器材領用/歸還紀錄 — returned_at 為空＝出勤中；due_at 過期未還＝逾期。"""
    __tablename__ = "equipment_checkouts"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    equipment_id = Column(String(32), nullable=False, index=True)  # soft FK → equipment.id
    project_id = Column(String(32), nullable=True)               # soft FK → crm_projects.id
    person = Column(String(64), nullable=True)                   # 領用人（自由輸入）
    out_at = Column(DateTime(timezone=True), nullable=True)      # 領用時間
    due_at = Column(DateTime(timezone=True), nullable=True)      # 應還日
    returned_at = Column(DateTime(timezone=True), nullable=True)  # 歸還時間（空＝未歸還）
    condition_note = Column(String(255), nullable=True)          # 歸還時狀況備註
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_eqco_equipment_returned", "equipment_id", "returned_at"),)


class EquipmentMaintenance(Base):
    """器材保養履歷（日期+費用+內容）— 保養成本納入器材持有成本。"""
    __tablename__ = "equipment_maintenance"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    equipment_id = Column(String(32), nullable=False, index=True)  # soft FK → equipment.id
    date = Column(DateTime(timezone=True), nullable=True)        # 保養日期
    cost = Column(Integer, nullable=True)                        # 保養費用
    note = Column(String(255), nullable=True)                    # 保養內容
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class FootageIndex(Base):
    """內部素材庫（B5，docs/BIZ_PLAN.md B5 段）— 逐字稿 + metadata 全文檢索。
    掃描既有影片 + 同名 .txt/.srt 逐字稿建索引；重用一段素材 = 省一天拍攝。
    file_path unique：同檔重掃更新不重複。transcript 用 pg_trgm 加速 ILIKE。"""
    __tablename__ = "footage_index"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    project_id = Column(String(32), nullable=True, index=True)   # soft FK → crm_projects.id
    project_name = Column(String(255), nullable=False, default="")  # 猜不到就存資料夾名
    file_path = Column(String(1024), nullable=False, unique=True)   # 絕對路徑（upsert key）
    file_name = Column(String(255), nullable=True)
    ext = Column(String(16), nullable=True)
    duration_sec = Column(Float, nullable=True)
    resolution = Column(String(32), nullable=True)               # "1920x1080"
    fps = Column(String(16), nullable=True)
    shot_date = Column(DateTime(timezone=True), nullable=True)   # 檔案 mtime
    transcript = Column(Text, nullable=True)                     # 同名 .txt/.srt 純文字
    tags = Column(JSONB, nullable=True)                          # list[str]
    thumb_strip_url = Column(String(512), nullable=True)         # 縮圖條（後續 hook 回填）
    size_bytes = Column(BigInteger, nullable=True)               # BigInteger：大檔超過 Integer 上限
    indexed_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_footage_project", "project_id"),)


# ═══════════════════════════════════════════════════════════════════
# 財務管理階段二（權責制三表地基）— 科目引擎 + 銀行帳戶 + 對帳 + 調整表
# 設計原則：非會計背景也要容易用 — 科目代碼藏在引擎裡（name_plain 給白話說明），
# 使用者日常只碰收支明細/請款/發票，報表由 category 對映自動歸科目。
# ═══════════════════════════════════════════════════════════════════

class FinanceAccount(Base):
    """會計科目表 — 權責制三表（損益/資產負債/現金流）的分類骨架。

    is_system=True 的種子科目不可刪（引擎依賴）；cf_activity 決定現金流量表
    的活動分類；pnl_group 決定損益表的呈現分組（NULL = 不進損益表）。"""
    __tablename__ = "finance_accounts"

    id = Column(String(32), primary_key=True)
    code = Column(String(16), unique=True, nullable=False)       # 科目代碼（1100/5100…，藏在引擎）
    name = Column(String(64), nullable=False)                    # 科目名稱（銀行存款/外包成本…）
    name_plain = Column(String(128), nullable=True)              # 白話說明（給非會計背景看）
    parent_id = Column(String(32), nullable=True)                # 上層科目（soft FK → finance_accounts.id）
    acct_type = Column(String(16), nullable=False)               # asset/liability/equity/income/expense
    cf_activity = Column(String(16), nullable=False, default="operating")  # operating/investing/financing/none
    pnl_group = Column(String(32), nullable=True)                # 損益表分組（營業收入/外包成本/營業費用/業外/稅）
    is_system = Column(Boolean, default=False)                   # 種子科目不可刪
    sort_order = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BankImportRule(Base):
    """銀行對帳單摘要 → 收支類別 的分類規則（使用者可編）。

    這一層在 finance_category_map **之前**：

        銀行摘要文字 →〔本表〕→ 類別 →〔finance_category_map〕→ 會計科目

    右半邊本來就是資料驅動的，左半邊原本寫死在 core/bank_statement.KEYWORD_RULES
    的 14 條裡 —— 那些是從合庫與一銀的對帳單反推的，換一家銀行、換一種摘要用語
    就要改程式（owner 2026-08-20：希望自己能設規則）。

    bank_account_id 是關鍵：合庫寫「攤還本息」、一銀寫「中小７月」，同一件事
    兩種寫法；規則綁到帳戶才不會互相誤觸。留空 = 套用到所有帳戶。

    🔴 direction 不開放給使用者設（UI 沒有這欄）。它只在「第一列沒有前一列餘額、
    也沒有印總計」時才用得到 —— 那是餘額鏈推不出方向的邊緣情況，讓人去理解
    「+1/-1/0」不划算。種子沿用原本 KEYWORD_RULES 的方向值，使用者新增的一律 0
    （0 = 看不出方向，解析器會標記該列要人確認且不預設勾選）。
    """
    __tablename__ = "bank_import_rules"

    id = Column(String(32), primary_key=True)
    keyword = Column(String(64), nullable=False)                 # 摘要包含這串就命中
    bank_account_id = Column(String(32), nullable=True)          # soft FK；空=所有帳戶
    category = Column(String(32), nullable=False)                # 命中後填的收支類別
    direction = Column(Integer, nullable=False, default=0)       # -1 支出 / +1 存入 / 0 不確定
    sort_order = Column(Integer, nullable=False, default=100)    # 小的先比（多條命中時誰贏）
    active = Column(Boolean, default=True)
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_bankrule_acct", "bank_account_id", "active"),
    )


class BankImportDraft(Base):
    """對帳單匯入的草稿 —— 掛好專案與發票、確認無誤之後再匯入（owner 2026-08-21）。

    一份對帳單幾十列，每列要決定分類、掛哪個專案、對到哪張發票，中途常常要去查
    別的資料。沒有草稿的話，人一離開就得從上傳重來一次（那些決定全部重做）。

    🔴 存的是**原始文字**加上人工決定，不是解析結果的快照。開啟時重新解析，
    才拿得到最新的：
      · 重複判定（草稿放兩天，中間可能有幾列已經從別的路進帳了 → 不該再匯一次）
      · 貸款期別配對（同上）
      · 專案與發票清單（發票可能已經收齊，不該再出現在候選裡）
    人工決定用（日期, 金額, 摘要）貼回去 —— 不是用列序號，因為重新解析的列序
    在對帳單本身沒變的前提下雖然一樣，但拿內容當鍵比較不會錯得無聲。
    """
    __tablename__ = "bank_import_drafts"

    id = Column(String(32), primary_key=True)
    entity = Column(String(16), nullable=False, default="parent")
    bank_account_id = Column(String(32), nullable=False)
    name = Column(String(120), nullable=False, default="")
    source_text = Column(Text, nullable=False)          # 原始貼上／檔案抽出的文字
    decisions = Column(Text, nullable=False, default="[]")   # JSON：人工決定
    row_count = Column(Integer, nullable=False, default=0)
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_bankdraft_acct", "entity", "bank_account_id"),
    )

class FinanceCategoryMap(Base):
    """收支/請款/發票 category → 科目 對映（引擎的翻譯層）。

    使用者照舊填中文 category，報表引擎查這張表決定科目與會計處理方式
    （treatment）。種子提供預設值，後台可改。"""
    __tablename__ = "finance_category_map"

    id = Column(String(32), primary_key=True)
    source = Column(String(16), nullable=False)                  # cash/payment/invoice
    category_text = Column(String(64), nullable=False)           # 原始 category 中文值
    account_id = Column(String(32), nullable=False)              # soft FK → finance_accounts.id
    # direct_expense/direct_income/ap_settlement/ar_settlement/transfer/
    # tax_vat/tax_income/advance/passthrough/loan
    treatment = Column(String(24), nullable=False)
    active = Column(Boolean, default=True)

    __table_args__ = (UniqueConstraint("source", "category_text",
                                       name="uq_fincatmap_source_text"),)


class BankAccount(Base):
    """銀行帳戶（含零用金）— 收支明細掛帳戶後可算餘額、對帳。"""
    __tablename__ = "bank_accounts"

    id = Column(String(32), primary_key=True)
    entity = Column(String(16), nullable=False, server_default="parent")  # 兩本帳：parent=母公司（預設）/mine=我的帳（docs/LEDGER_ENTITY_PLAN.md）
    name = Column(String(64), nullable=False)                    # 帳戶顯示名（XX 銀行活存）
    bank_name = Column(String(64), nullable=True)                # 銀行名稱
    account_no = Column(String(32), nullable=True)               # 帳號（後幾碼即可）
    acct_kind = Column(String(16), nullable=False, default="bank")  # bank / cash=零用金 / shareholder_*=股東往來
    # 股東往來帳戶綁到哪位人員（soft FK → crm_staff.id）。只有 shareholder_* 用得到 ——
    # 綁了之後那位股東登入 /my.html 就看得到自己的往來餘額（own-scope，看不到別人的）。
    staff_id = Column(String(32), nullable=True, index=True)
    opening_balance = Column(Integer, nullable=False, default=0)  # 期初餘額（基準日）
    opening_date = Column(DateTime(timezone=True), nullable=True)  # 期初基準日
    is_default = Column(Boolean, default=False)                  # 預設帳戶（新收支預設掛這）
    active = Column(Boolean, default=True)                       # 停用後不出現在選單（不刪保歷史）
    sort_order = Column(Integer, default=0)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BankReconciliation(Base):
    """銀行對帳紀錄 — 每帳戶每月一筆：對帳單餘額 vs 系統餘額，差額歸零才算平。"""
    __tablename__ = "bank_reconciliations"

    id = Column(String(32), primary_key=True)
    bank_account_id = Column(String(32), nullable=False)         # soft FK → bank_accounts.id
    month = Column(String(7), nullable=False)                    # 'YYYY-MM'
    statement_balance = Column(Integer, nullable=False)          # 銀行對帳單月底餘額
    system_balance = Column(Integer, nullable=False)             # 系統算出的月底餘額
    diff = Column(Integer, nullable=False)                       # statement − system
    status = Column(String(16), nullable=False)                  # balanced / diff
    note = Column(Text, nullable=True)
    reconciled_by = Column(String(64), nullable=True)
    reconciled_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("bank_account_id", "month",
                                       name="uq_bankrecon_account_month"),)


class BankStatementLine(Base):
    """銀行對帳單明細列 — 對帳工作台的「銀行說發生了什麼」側，逐筆與收支明細勾銷。

    工作底稿性質（非帳務資料）：新增/編輯/刪除/配對都只動這張表；唯一寫真帳的
    動作是「補記入帳」（建 CrmCashEntry，受月結守衛）。狀態為推導值不落庫：
    matched（有 matched_entry_id）> noted（有 note）> unmatched。"""
    __tablename__ = "bank_statement_lines"

    id = Column(String(32), primary_key=True)
    bank_account_id = Column(String(32), nullable=False)         # soft FK → bank_accounts.id
    month = Column(String(7), nullable=False)                    # 'YYYY-MM' 對帳月份
    line_date = Column(DateTime(timezone=True), nullable=True)   # 對帳單交易日
    description = Column(String(255), nullable=True)             # 對帳單摘要
    amount = Column(Integer, nullable=False)                     # 有號：正=存入、負=支出
    matched_entry_id = Column(String(32), nullable=True)         # soft FK → crm_cash_entries.id
    note = Column(Text, nullable=True)                           # 未配對說明（時間差等）
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_stmtline_acct_month", "bank_account_id", "month"),
        # 一筆收支只能被一列認領 — 不變式下沉到 DB（端點先驗回友善 409，這裡是後盾；
        # 既有表補建走 main.py startup 的 CREATE UNIQUE INDEX IF NOT EXISTS）
        Index("uq_stmtline_matched_entry", matched_entry_id, unique=True,
              postgresql_where=matched_entry_id.isnot(None)),
    )


class FinanceAdjustment(Base):
    """財務調整表 — 期初建帳/更正/業主往來/會計師調整等非日常分錄。

    🔴 鐵則：不得指向銀行類科目（code 11xx）— 影響現金的修正一律走收支明細
    （否則銀行餘額與對帳脫鉤）。後端 POST/PUT 驗證擋下。金額有號：正=增、負=減。"""
    __tablename__ = "finance_adjustments"

    id = Column(String(32), primary_key=True)
    entity = Column(String(16), nullable=False, server_default="parent")  # 兩本帳：parent=母公司（預設）/mine=我的帳（docs/LEDGER_ENTITY_PLAN.md）
    adj_date = Column(DateTime(timezone=True), nullable=False)   # 調整生效日（月結守衛看這個月）
    account_id = Column(String(32), nullable=False)              # soft FK → finance_accounts.id
    amount = Column(Integer, nullable=False)                     # 有號金額（新台幣整數）
    # opening/correction/owner_in/owner_out/accountant/writeoff/other
    adj_type = Column(String(24), nullable=False)
    description = Column(String(255), nullable=False)            # 說明（必填 — 稽核可讀）
    created_by = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_finadj_date", "adj_date"),
                      Index("idx_finadj_account", "account_id"))


class FinanceLoan(Base):
    """銀行貸款（財務階段四）— 建檔即由 core.finance_logic.amortization_schedule
    生成攤還表（finance_loan_payments）。

    利息費用權責按攤還表 due_date 進損益「業外支出」（不管繳沒繳）；
    繳款現金流由 pay 端點自動建收支明細（category=貸款繳款 → 科目 2400
    cf_activity=financing、treatment='loan' 不進損益）；BS 貸款餘額
    = 起始本金 − Σ已繳期別 principal_due（逐筆貸款分列非流動負債）。

    opening_balance：導入舊貸時填「當下剩餘本金」，此時 term_months = 剩餘期數，
    攤還表只生剩餘期（principal 仍記原始本金供參考）。"""
    __tablename__ = "finance_loans"

    id = Column(String(32), primary_key=True)
    entity = Column(String(16), nullable=False, server_default="parent")  # 兩本帳：parent=母公司（預設）/mine=我的帳（docs/LEDGER_ENTITY_PLAN.md）
    name = Column(String(128), nullable=False)                   # 貸款顯示名（XX 銀行週轉金）
    lender = Column(String(64), nullable=True)                   # 貸款銀行/機構
    principal = Column(Integer, nullable=False, default=0)       # 原始本金（新台幣整數）
    annual_rate = Column(Float, nullable=False, default=0.0)     # 年利率 %（2.85 = 2.85%）
    term_months = Column(Integer, nullable=False, default=0)     # 期數（opening_balance 模式=剩餘期數）
    method = Column(String(16), nullable=False, default="annuity")  # annuity/straight/interest_only
    grace_months = Column(Integer, nullable=False, default=0)    # 寬限期（只付息不還本）
    start_date = Column(DateTime(timezone=True), nullable=True)  # 撥款/起貸日
    first_payment_date = Column(DateTime(timezone=True), nullable=True)  # 首期繳款日（空=起貸日下月同日）
    bank_account_id = Column(String(32), nullable=True)          # 預設扣款帳戶（soft FK → bank_accounts.id）
    account_no = Column(String(32), nullable=True)               # 銀行放款帳號 —— 對帳單匯入靠它認出這筆是哪個貸款的扣款
    opening_balance = Column(Integer, nullable=True)             # 導入舊貸=當下剩餘本金（空=全新貸款）
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FinanceLoanPayment(Base):
    """貸款攤還期別 — 一期一列，建檔時由攤還純函式生成；已繳列不可變
    （PUT /loans 只重生未繳期別）。cash_entry_id 連到 pay 自動建的收支明細。"""
    __tablename__ = "finance_loan_payments"

    id = Column(String(32), primary_key=True)
    loan_id = Column(String(32), nullable=False, index=True)     # soft FK → finance_loans.id
    period_no = Column(Integer, nullable=False)                  # 期別（1 起算）
    due_date = Column(DateTime(timezone=True), nullable=False)   # 到期日（利息權責認列月）
    principal_due = Column(Integer, nullable=False, default=0)   # 本期應還本金
    interest_due = Column(Integer, nullable=False, default=0)    # 本期應付利息
    paid_at = Column(DateTime(timezone=True), nullable=True)     # 實際繳款日
    # 銀行**實際扣款**金額。空＝照攤還表（principal_due + interest_due）。
    # 🔴 為什麼要分開存：銀行按實際天數算息、進位也不同，跟我們用公式重算的會差
    # 幾元（實測合庫 315614 攤還表 4,456／銀行實扣 4,470，315611 是 25,286／25,290）。
    # 記攤還表那個數字的話，系統的銀行餘額每期歪十幾元且累積，而對帳工作台要求
    # 金額完全相等才勾得掉 —— 那些列會永遠配不上。現金看銀行的，本息拆分看攤還表。
    paid_amount = Column(Integer, nullable=True)
    cash_entry_id = Column(String(32), nullable=True)            # 關聯收支明細（pay 自動建）
    status = Column(String(16), nullable=False, default="scheduled")  # scheduled/paid（逾期為即時推導，不落庫）

    __table_args__ = (UniqueConstraint("loan_id", "period_no",
                                       name="uq_loanpay_loan_period"),)


# ═══════════════════════════════════════════════════════════════════
# CRM 影像紀錄（工作過程劇照/花絮收集）— routers/crm/media_log.py
# 每專案一條 token 公開連結（/media-log.html?token=…）供免登入上傳/瀏覽；
# 原始檔存管理員設定的資料夾（settings.json media_log.root），縮圖走 WebP。
# ═══════════════════════════════════════════════════════════════════

class CrmExpenseLink(Base):
    """雜支登記的分享連結（token 模式，照 ProjectMediaLog 的形狀）。

    為什麼需要它：8/14 的 `_crm_read_guard` 之後，雜支頁的 `/public/...` 端點
    一律要登入 —— 但現場登記雜支的人（外部場記／臨時人員）沒有帳號。token 才是
    這種「發一條連結給特定一件事」的正確憑證，「專案 id 猜不到」不是。

    `id` = 目標 id（專案／子表），`kind` 決定那條連結的範圍。兩種 id 都是
    uuid4 hex，共用一張表不會撞。
    """
    __tablename__ = "crm_expense_links"

    id = Column(String(32), primary_key=True)                    # 專案 id 或子表 id
    kind = Column(String(16), nullable=False)                    # project | group
    edit_token = Column(String(512), nullable=True)
    enabled = Column(Boolean, default=True)                      # False = 整條連結停用（403）
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProjectMediaLog(Base):
    """影像紀錄分享連結（1:1 專案）— token 模式照 CrmProjectShowcase.edit_token
    （scope="media_log"，reuse/重發語意見 media_log._mint_media_log_token）。"""
    __tablename__ = "project_media_log"

    id = Column(String(64), primary_key=True)                    # = project_id
    edit_token = Column(String(512), nullable=True)
    enabled = Column(Boolean, default=True)                      # False = 公開連結整條停用（403）
    folder_name = Column(String(255), nullable=True)             # 原檔子資料夾名（首次生成後固定：{建立日期}_{專案名}）
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProjectMediaFile(Base):
    """影像紀錄單一檔案 — 原檔在 stored_path（管理員資料夾），縮圖在
    /uploads/projects/{project_id}/media_log/{id}.webp（無縮圖 = 空字串）。"""
    __tablename__ = "project_media_files"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    project_id = Column(String(64), index=True, nullable=False)  # soft FK → crm_projects.id
    filename = Column(String(400), default="")                   # 原檔名（顯示/下載名）
    stored_path = Column(Text, default="")                       # 原檔絕對路徑
    thumb_url = Column(String(400), default="")                  # /uploads/...webp，無縮圖=空字串
    media_type = Column(String(10), default="image")             # image|video
    duration_sec = Column(Float, nullable=True)                  # video 才有
    category = Column(String(50), default="")                    # 空=未分類
    uploader_name = Column(String(100), default="")
    size_bytes = Column(BigInteger, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ═══════════════════════════════════════════════════════════════════
# 每週工作日誌（journal）— routers/api_journal.py
# 每人每週一份（username + week_start 唯一）；三區塊分開建表（owner 明確要求：
# 「學到了什麼」要能獨立跨週彙整成學習庫）。全員可讀、本人可寫。
# ═══════════════════════════════════════════════════════════════════

class WorkJournal(Base):
    """週誌殼 — 一人一週一份；week_start = 該週週一（伺服端正規化，core.journal_logic）。"""
    __tablename__ = "work_journals"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    username = Column(String(64), index=True, nullable=False)    # token sub（users.username）
    week_start = Column(Date, index=True, nullable=False)        # 該週週一
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("username", "week_start",
                                       name="uq_journal_user_week"),)


class JournalWin(Base):
    """順利的事與想感謝的人 — 週誌條目（PUT 全量替換，sort_order=列表順序）。"""
    __tablename__ = "journal_wins"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    journal_id = Column(String(32), index=True, nullable=False)  # soft FK → work_journals.id
    content = Column(Text)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class JournalChallenge(Base):
    """遇到哪些挑戰 — 同構 JournalWin。"""
    __tablename__ = "journal_challenges"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    journal_id = Column(String(32), index=True, nullable=False)  # soft FK → work_journals.id
    content = Column(Text)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class JournalLearning(Base):
    """學到了什麼 — 同構 JournalWin；獨立成表供跨週彙整（GET /journal/learnings 學習庫）。"""
    __tablename__ = "journal_learnings"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    journal_id = Column(String(32), index=True, nullable=False)  # soft FK → work_journals.id
    content = Column(Text)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class JournalOther(Base):
    """其他主題 — 同構 JournalWin（owner 2026-07-24 加的第四問，自由主題）。"""
    __tablename__ = "journal_others"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    journal_id = Column(String(32), index=True, nullable=False)  # soft FK → work_journals.id
    content = Column(Text)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ── 福委會（docs/BENEFIT_POOL_PLAN.md）──────────────────────────────
#
# owner 2026-08-21 的原話：「我有幾個福利池，一個是快樂、一個是進修，這兩塊員工
# 都可以登記，他們登記後我審核通過，就進公司請款。我每年會撥一筆錢進這個池。」
#
# 所以是三張表：池（快樂／進修）、撥款（每年進池的那筆）、登記（員工花的那筆）。
# 餘額 ＝ Σ撥款 − Σ已核准的登記。
#
# 為什麼撥款自己一張表、不跟登記混在同一本帶號流水帳（Notion 原本是那樣）：
# 撥款沒有請款人、不需要審核、也不該進公司請款 —— 跟員工登記是兩種東西。
# 混在一起的話，員工登記時把金額填成正數就變成一筆「撥款」，池憑空多錢。

class HrBenefitPool(Base):
    """一個福利池（快樂／進修）。跨年度滾動 —— 每年撥款進來，餘額往下扣。"""
    __tablename__ = "hr_benefit_pools"

    id = Column(String(32), primary_key=True)
    entity = Column(String(16), nullable=False, server_default="parent")  # 兩本帳
    name = Column(String(128), nullable=False)                   # 快樂 / 進修
    status = Column(String(16), nullable=False, default="open")  # open/closed
    sort_order = Column(Integer, nullable=False, default=0)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_benefit_pool_entity", "entity", "status"),
    )


class HrBenefitFunding(Base):
    """撥款：每年公司放進池裡的那筆錢。"""
    __tablename__ = "hr_benefit_fundings"

    id = Column(String(32), primary_key=True)
    pool_id = Column(String(32), nullable=False, index=True)   # soft FK → hr_benefit_pools.id
    year = Column(Integer, nullable=False, index=True)
    amount = Column(Integer, nullable=False, default=0)
    fund_date = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class HrBenefitEntry(Base):
    """員工登記的一筆花費。登記即待審 —— owner 審核通過就進公司請款。"""
    __tablename__ = "hr_benefit_entries"

    id = Column(String(32), primary_key=True)
    pool_id = Column(String(32), nullable=False, index=True)   # soft FK → hr_benefit_pools.id
    staff_id = Column(String(32), nullable=True, index=True)   # soft FK → crm_staff.id
    # 快照：人員改名不該讓歷史單跟著變（同 CrmReimbursement.staff_name）
    staff_name = Column(String(64), nullable=False)
    title = Column(String(255), nullable=False)                # 項目：電影名／餐廳／課程名
    amount = Column(Integer, nullable=False, default=0)        # 正數（花掉多少）
    spend_date = Column(DateTime(timezone=True), nullable=True, index=True)
    receipt_url = Column(String(512), nullable=True)
    # 心得筆記（員工寫的）。🔴 不共用 notes —— 那一欄裝退回原因（append [退回] xxx），
    # 混在一起兩邊都讀不乾淨。單據與心得**都不是必填**（owner 2026-08-21：
    # 「並不是一定要上傳才能請款，這樣才符合各種使用情境」），有就標示給 owner 看。
    reflection = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="待審")  # 待審/已核准/已付款/退回
    # 核准時產的那張應付款。冪等與退回時的「撤掉幽靈負債」都靠這個硬連結
    payment_request_id = Column(String(32), nullable=True, index=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_benefit_entry_pool_status", "pool_id", "status"),
        Index("idx_benefit_entry_staff", "staff_id", "status"),
    )
