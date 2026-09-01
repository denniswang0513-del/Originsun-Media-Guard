"""CRM：專案/報價/人力/作品 ＋ 帳務三表（發票/請款/收支）＋ ApiKey（`db.models` 套件的一段，2026-08-31 拆檔）。

對外一律從 `db.models` 匯入，別直接指名這個檔。
"""
from ._base import (Base, Boolean, Column, DateTime, Float, Index, Integer, JSONB, String, Text, UniqueConstraint, func)

class CrmProject(Base):
    """CRM 專案資料。"""
    __tablename__ = "crm_projects"

    id = Column(String(32), primary_key=True)
    # 兩本帳 §8（2026-08-24 owner 拍板「專案與客戶全面共用、只有錢分帳」）：
    # entity 決定這個專案的**錢流歸屬**（'parent'=母公司／'mine'=owner 私帳），
    # 專案本身（名稱/階段/看板/官網上架）對所有 crm_projects 使用者可見。
    # 錢的牆：金額欄位靠 core/money 的 mine-aware 抹除、money 端點靠
    # money_dep 的 mine-scope 檢查、統計聚合排除 mine —— 見 core/ledger。
    entity = Column(String(16), nullable=False, server_default="parent")
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
    # 逐案損益的明細欄（兩本帳 §8 / owner 私帳的「結案總表」形式，2026-08-24）。
    # {outsource, tax_fee, buy_invoice, invoice_fee, personal_tax, misc,
    #  shareholder, split:{工項: 金額}}。實收與檢查是**算出來的**不落庫：
    #   實收 = contract_amount − outsource − invoice_fee − personal_tax − misc − shareholder
    #   檢查 = 實收 − Σsplit（應為 0；Sheet 有 5 案本來就不為 0）
    # 🔴 invoice_fee 存原值不由 tax_fee+buy_invoice 推導 —— 402 案有 1 案兩者
    # 不等（IGER DAY 講座側錄），推導會靜默改掉來源資料。
    ledger_detail = Column(JSONB, nullable=True)
    # 私帳案推送到專案管理（owner 2026-08-25）：1＝出現在母公司管線、列表標
    # 「後期專案」。只對 entity='mine' 有意義；錢流歸屬不變（仍在私帳，金額
    # 對無 mine scope 者照抹）。
    # 🔴 這個旗標**不影響金額可見性** —— 2026-08-28 一度讓它放寬，當天被 owner
    # 收回：「推到私帳沒有私帳的權限就要看不到了」。抹除只看 entity。
    crm_pushed = Column(Integer, nullable=False, server_default="0")
    # 連結私帳（owner 2026-08-29）：這一列（私帳案）是**哪個母公司專案**的收入分身。
    # 公司把後期發給我做 → 公司那邊是成本行、我這邊是收入，兩本帳各記各的。
    #
    # 🔴 跟 entity 是兩回事，別混：`entity` 說「這案的錢算哪本帳」（搬家，一案只在
    # 一本）；`source_project_id` 說「我這案的收入來自公司那案」（分身，兩案並存）。
    # 只寫在 entity='mine' 的那一列上，母公司那列不動任何欄位。
    # soft FK → crm_projects.id（全庫慣例，無 ForeignKey；刪母公司案會留下懸空
    # 指標，那一列從此不出現在可連結清單裡 —— 目前只能用 SQL 解開）
    # 索引由 main.py 的 startup migration 建（idx_projects_source_project）——
    # 這裡再加 index=True 會多一條同欄位的 btree，寫入時白付兩次維護成本。
    source_project_id = Column(String(32), nullable=True)
    # 🔴 這個 CRM 案的私帳收入歸到哪一案（**多對一**：owner 2026-09-01
    # 「可以多筆專案連結到一筆私帳」）。與 `source_project_id` 的差別是方向：
    # 那一欄記在**私帳案**上（「我是誰的分身」），一個欄位只裝得下一個來源，
    # 所以第二個 CRM 案就連不上去了。連結記在來源這一側才表達得了多對一；
    # 私帳案那側仍保留 source_project_id＝**第一個**來源（清單顯示、
    # 「這是分身」的判定沿用它，不必全樹改讀）。
    mine_link_id = Column(String(32), nullable=True)
    flow_checks = Column(JSONB, nullable=True)                 # 工作流手動里程碑（範本正本在 core/project_flow.py）
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
    # 🔴 名字容易讀反：這一欄裝的是**費用歸屬人**（原本該收這筆錢的那個人），
    # 不是代墊人 —— 代墊人是 `payee_name`（他才是實際去領錢的）。
    # 例：王士源先掏錢付蘇家弘的製片費 → payee_name=王士源、advance_by=蘇家弘。
    # 有值＝這張單是代墊。（2026-09-02 對齊：詳情面板原本把它標成「代墊人
    # （實際收款人）」，跟實際存的相反。）
    advance_by = Column(String(64), nullable=True)                     # 代墊時的費用歸屬人（原收款人）
    is_advance = Column(Integer, nullable=False, default=0)            # 0=一般, 1=預支款
    advance_returned = Column(Integer, nullable=False, default=0)      # 0=未歸還, 1=已歸還
    # 零用金批次產生的應付款（一張批次 → 多張 AP：會計項目 × 認列月份）。
    # 🔴 方向是 AP→批次 而不是批次→AP：一對多的那一邊才存得下。
    reimbursement_id = Column(String(32), nullable=True, index=True)
    # 從私帳逐案損益的「委外人員名單」一鍵請款時，記住是哪一行成本行
    # （crm_project_cost_lines.id）—— 沒有它就只能靠人名＋金額目測，同一個人
    # 同一筆金額在同一案出現兩次時分不出誰請過了。
    cost_line_id = Column(String(32), nullable=True, index=True)
    # 同上，但指向「行政雜支」那一行（crm_project_expenses.id）——
    # 雜支與人員費用來自兩張不同的表，所以兩條硬連結各自一欄。
    # 兩者的語意一樣：**這張請款單是 CRM 某一行的鏡射**，不是新的一筆錢。
    expense_id = Column(String(32), nullable=True, index=True)
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
    note = Column(Text, nullable=True)                           # 附註（使用者手寫）
    # 銀行/卡單那側原始資訊（分期餘額、商家溢出行、跨轉摘要…）—— owner 2026-08-26
    # 「像原本表那樣有兩個備註欄：一個銀行帳戶本來的資訊、一個我的附註」。
    # 機器來源進這欄，note 留給人。
    bank_memo = Column(Text, nullable=True)
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
    # 分類樹的**葉節點**（→ cash_taxonomy_nodes.id）。上面的 category/item/sub_item
    # 是這條路徑前三層的鏡射，第四層以後只有這裡看得到（見 CashTaxonomyNode）。
    taxonomy_node_id = Column(String(32), nullable=True, index=True)
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
    # 這張發票的匯款被匯出行扣掉、沒進到我們帳戶的那幾十塊（owner 2026-08-24）。
    #
    # 🔴 為什麼要逐張存而不是只留 crm_cash_entries.bank_fee 的加總：關聯面板
    #    重新打開時要**畫得出來**。原本沒有這欄 → 面板每次載入那格都是空的，
    #    按一下儲存就送 fee=0，deposit 退回去、bank_fee 被清掉（2026-08-24
    #    實測確認：帶 fee 存完 deposit=149,900/bank_fee=30，不帶 fee 重存一次
    #    就變回 149,870/None）。那是使用者完全看不出來的靜默回退。
    #    entries.bank_fee 仍是這一筆的加總（現金流與報表讀它），這欄是歸屬。
    fee = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # 同一筆收款不會對同一張發票分配兩次（要改金額就改那一列）
        UniqueConstraint("cash_entry_id", "invoice_id", name="uq_cashinv_entry_invoice"),
    )


class CrmCashPaymentLink(Base):
    """匯款 ↔ 請款單的分配明細（多對多，帶金額）。

    形狀完全照 CrmCashInvoiceLink —— 收款那側早就有同樣的兩種一對多，付款這側
    一模一樣（owner 2026-08-22）：
      合併匯款：出納同一天把某人的三張請款單併成一筆匯出 → 一筆匯款掛多張單
      分次支付：一張請款單分兩次付 → 一張單掛多筆匯款

    🔴 為什麼不是 crm_cash_entries.payment_request_id 一個欄位就好：它是一對一。
    實測生產 322 筆結清請款單的收支，硬連結**一筆都沒有** —— 因為掛不上去。
    於是「哪一筆匯款結清哪一張請款單」在系統裡是空的，只能靠人工對帳。

    `crm_cash_entries.payment_request_id` 保留為「主要請款單」（既有查詢與
    classify_cash_entry 的硬連結優先序都靠它），寫入連結時同步成金額最大的那張。
    """
    __tablename__ = "crm_cash_payment_links"

    id = Column(String(32), primary_key=True)
    cash_entry_id = Column(String(32), nullable=False, index=True)       # soft FK → crm_cash_entries.id
    payment_request_id = Column(String(32), nullable=False, index=True)  # soft FK → crm_payment_requests.id
    amount = Column(Integer, nullable=False, default=0)                  # 這筆匯款分配到這張單的金額
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # 同一筆匯款不會對同一張請款單分配兩次（要改金額就改那一列）
        UniqueConstraint("cash_entry_id", "payment_request_id",
                         name="uq_cashpay_entry_request"),
    )


class CrmCashSplit(Base):
    """收支列的內部拆項 —— 「帳目一筆、內容拆裂」（owner 2026-08-31 定案）。

    母公司匯給 owner 的一筆錢（+350,436）同時裝著專案款、代墊回款、薪資 ——
    三種會計性質不同的錢。帳目上維持**一筆**（收支明細與對帳工作台 1↔1 都
    不動），拆裂放在這張表：每個拆項各自帶分類樹節點與專案連結。

    🔴 不變式：Σ(splits.amount) ＝ 父列的 deposit 或 expense（哪側非零跟哪側）
      —— 寫入端（routers/crm/cash_splits._apply_splits）強制；三表引擎在載入層
      展開時，差額會變成「未歸類」殘項誠實外顯，不會靜默吞掉。
    🔴 父列有拆項後**不再自帶分類與專案**（分類的正本在拆項）—— 清單顯示
      「已拆 N 項」；批次改分類／改金額要先解除拆項。
    🔴 category/item/sub_item 是 taxonomy_node_id 的鏡射，寫入走 cash.py 的
      `_sync_taxonomy` 同一份規則 —— 不另拼第二份。
    """
    __tablename__ = "crm_cash_splits"
    id = Column(String(32), primary_key=True)
    entry_id = Column(String(32), nullable=False, index=True)   # soft FK → crm_cash_entries.id
    amount = Column(Integer, nullable=False)                     # 恆正；方向跟父列
    # 發票代開費（源日代開發票、扣完費用才匯）：amount 是實匯淨額，fee 外加。
    # 專案已收按毛額（amount+fee）結清；三表展開時毛額進營收、fee 走 bank_fee
    # 同一條費用鏈 —— 數學同 recognize_receipt_fee（淨流不變）。只在收入側
    # 且掛專案的拆項有意義（寫入端擋其他組合）。
    fee = Column(Integer, nullable=True)
    category = Column(String(128), nullable=True)
    item = Column(String(64), nullable=True)
    sub_item = Column(String(64), nullable=True)
    taxonomy_node_id = Column(String(32), nullable=True)
    project_id = Column(String(32), nullable=True, index=True)   # soft FK → crm_projects.id
    note = Column(String(255), nullable=True)
    sort = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CrmCashSplitAdvanceLink(Base):
    """拆項 ↔ 代墊支出列的逐筆結清連結（「對起來」的另一半）。

    公司_代墊的流出（owner 先墊的錢）等回款；回款拆項勾了哪幾列、各沖多少，
    記在這裡 —— 逐列可推「已回款/未回款」。1300 科目餘額本來就靠兩側同分類
    自動軋平，這張表只負責**逐筆**歸屬。

    🔴 歷史回款（2026-08 以前的 144 列流入）沒有逐筆連結 —— 帳齡從啟用日起算，
    總額仍以 1300 科目餘額為準。
    """
    __tablename__ = "crm_cash_split_advance_links"
    id = Column(String(32), primary_key=True)
    split_id = Column(String(32), nullable=False, index=True)         # soft FK → crm_cash_splits.id
    advance_entry_id = Column(String(32), nullable=False, index=True)  # soft FK → crm_cash_entries.id（代墊流出列）
    amount = Column(Integer, nullable=False)                           # 沖到那一列多少
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("split_id", "advance_entry_id",
                         name="uq_cashsplit_advance"),
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


