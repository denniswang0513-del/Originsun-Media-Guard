"""系統執行面（任務史/公布欄/機器/書籤/排程/報表）＋ User ＋ Client（`db.models` 套件的一段，2026-08-31 拆檔）。

對外一律從 `db.models` 匯入，別直接指名這個檔。
"""
from ._base import (Base, Boolean, Column, DateTime, Index, Integer, JSONB, String, Text, UniqueConstraint, func)

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
    # 兩本帳（owner 2026-08-26「我的客戶先不要混到 crm 系統」）：mine＝私帳
    # 專用客戶，CRM 客戶管理與各下拉一律看不到；等 owner 確認對應後才併回 parent。
    entity = Column(String(16), nullable=False, server_default="parent")
    # 私帳客戶 → CRM 客戶的對應連結（soft FK → clients.id，entity='parent' 列）。
    # owner 2026-08-26「跟 crm 同步，但是用連結的方式」—— 只記對應、不搬資料；
    # 之後「併過去」以此為依據。只有 mine 列會有值。
    crm_link_id = Column(String(32), nullable=True)

    __table_args__ = (UniqueConstraint("entity", "short_name",
                                       name="uq_client_entity_short_name"),)
    # 客戶代稱：**每本帳各自唯一**（owner 2026-08-26「兩邊各一筆＋連結」——
    # 同一家公司 CRM 一筆、私帳一筆，名字本來就該一樣；全域唯一會讓第二筆
    # 被迫改名，那是替約束服務而不是替帳服務）。
    short_name = Column(String(64), nullable=False)  # 客戶代稱
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


