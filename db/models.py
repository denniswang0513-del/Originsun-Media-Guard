"""SQLAlchemy ORM models for Originsun Media Guard Pro."""

try:
    from sqlalchemy import Column, String, Text, Boolean, Integer, DateTime, func, Index
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.orm import DeclarativeBase
    _HAS_SQLALCHEMY = True
except ImportError:
    _HAS_SQLALCHEMY = False
    # Provide stubs so module can be imported without crashing
    class _Stub:
        def __call__(self, *a, **kw): return self
        def __getattr__(self, _): return self
    Column = String = Text = Boolean = Integer = DateTime = func = Index = _Stub()
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


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(32), unique=True, nullable=False)
    access_level = Column(Integer, nullable=False, default=1)
    modules = Column(JSONB, nullable=False, default=list)
    description = Column(String(255), nullable=True)


class User(Base):
    __tablename__ = "users"

    username = Column(String(64), primary_key=True)
    password_hash = Column(String(255), nullable=True)                  # nullable: Google-only 使用者無密碼
    role = Column(String(16), nullable=False, default="editor")         # 舊欄位，過渡期保留
    visible_tabs = Column(JSONB)                                        # 舊欄位，過渡期保留
    role_id = Column(Integer, nullable=True)                            # 新 RBAC FK（過渡期 nullable）
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
    status = Column(String(32), nullable=False, default="洽談中")  # 洽談中/進行中/已結案
    am_username = Column(String(64), nullable=True)
    pm_usernames = Column(JSONB, nullable=True)                  # ["user1","user2"]
    shoot_date = Column(DateTime(timezone=True), nullable=True)
    folder_path = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_crmproj_client", "client_id"),
        Index("idx_crmproj_status", "status"),
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
