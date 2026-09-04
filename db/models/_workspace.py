"""媒體紀錄 ＋ 週誌 ＋ 福委會 ＋ 收支分類樹（`db.models` 套件的一段，2026-08-31 拆檔）。

對外一律從 `db.models` 匯入，別直接指名這個檔。
"""
from ._base import (Base, BigInteger, Boolean, Column, Date, DateTime, Float, Index, Integer, JSONB, String, Text, UniqueConstraint, func)

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
    # 草稿→送出（docs/JOURNAL_WORKLOG_PLAN.md §13）：draft／submitted；migration 把既有列補成 submitted
    status = Column(String(16), nullable=True, default="draft")
    submitted_at = Column(DateTime(timezone=True), nullable=True)   # 再送出會更新
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
    project_id = Column(String(32), nullable=True)               # 掛案子（§2-B3；soft FK → crm_projects.id）
    flag = Column(String(16), nullable=True)                     # help／discuss（§2-B4；core.journal_logic.ENTRY_FLAGS）
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class JournalChallenge(Base):
    """遇到哪些挑戰 — 同構 JournalWin。"""
    __tablename__ = "journal_challenges"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    journal_id = Column(String(32), index=True, nullable=False)  # soft FK → work_journals.id
    content = Column(Text)
    project_id = Column(String(32), nullable=True)               # 掛案子（§2-B3；soft FK → crm_projects.id）
    flag = Column(String(16), nullable=True)                     # help／discuss（§2-B4；core.journal_logic.ENTRY_FLAGS）
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class JournalLearning(Base):
    """學到了什麼 — 同構 JournalWin；獨立成表供跨週彙整（GET /journal/learnings 學習庫）。"""
    __tablename__ = "journal_learnings"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    journal_id = Column(String(32), index=True, nullable=False)  # soft FK → work_journals.id
    content = Column(Text)
    project_id = Column(String(32), nullable=True)               # 掛案子（§2-B3；soft FK → crm_projects.id）
    flag = Column(String(16), nullable=True)                     # help／discuss（§2-B4；core.journal_logic.ENTRY_FLAGS）
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class JournalOther(Base):
    """其他主題 — 同構 JournalWin（owner 2026-07-24 加的第四問，自由主題）。"""
    __tablename__ = "journal_others"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    journal_id = Column(String(32), index=True, nullable=False)  # soft FK → work_journals.id
    content = Column(Text)
    project_id = Column(String(32), nullable=True)               # 掛案子（§2-B3；soft FK → crm_projects.id）
    flag = Column(String(16), nullable=True)                     # help／discuss（§2-B4；core.journal_logic.ENTRY_FLAGS）
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class JournalReply(Base):
    """主管對週記某一條的回覆（§2-B5）。回覆不改原文；(entry_table, entry_id) 指到四張條目表之一。
    條目在 PUT 全量替換時盡量沿用 id（帶 id 或內容相同），回覆才跟得住。"""
    __tablename__ = "journal_replies"

    id = Column(String(32), primary_key=True)                    # uuid4 hex
    journal_id = Column(String(32), index=True, nullable=False)  # soft FK → work_journals.id
    entry_table = Column(String(32), nullable=False)             # journal_wins／journal_challenges／journal_learnings／journal_others
    entry_id = Column(String(32), index=True, nullable=False)
    username = Column(String(64), nullable=False)                # 回覆的人（token sub）
    content = Column(Text)
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
    name = Column(String(128), nullable=False)                   # 快樂 / 進修 / LAZY KIT / 健檢
    status = Column(String(16), nullable=False, default="open")  # open/closed
    sort_order = Column(Integer, nullable=False, default=0)
    # 額度怎麼配（docs/BENEFIT_POOL_PLAN.md §9）：
    #   shared      = 全公司共用一桶（快樂／進修，現況）
    #   per_person  = 每人一份（LAZY KIT 那種年度活動；超額與期間外都**擋**）
    quota = Column(String(16), nullable=False, server_default="shared")
    # 說明（給員工看的）＋ 附件（廠商 PDF、券的圖）。健檢方案就是靠這兩欄被看到
    # （owner 2026-08-21：「可以就是一個可以打字、附上文件的說明」）。
    # 🔴 說明不共用 notes —— 那欄是管理者備註，混在一起兩邊都讀不乾淨。
    description = Column(Text, nullable=True)
    attachments = Column(JSONB, nullable=True)      # [{name, path, size}]
    # 有效期間。**留空＝不限**（共用池就是兩邊留空）
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)
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


class HrBenefitAllowance(Base):
    """發給某個人的額度（LAZY KIT 那種年度活動）。

    🔴 **資格不做成規則，做成名單**（docs/BENEFIT_POOL_PLAN.md §9.3）：
    「到職滿一年」「每兩年一次」這種條件寫進程式就要永遠維護它的例外。
    這張表本來就一列一個人 —— 誰在名單上就是誰有資格。

    期間放在列上而不是只放在池上：同一個活動有人年中才到職、效期不同，
    放在列上才表達得出來。留空＝繼承池的期間。
    """
    __tablename__ = "hr_benefit_allowances"

    id = Column(String(32), primary_key=True)
    pool_id = Column(String(32), nullable=False, index=True)   # soft FK → hr_benefit_pools.id
    staff_id = Column(String(32), nullable=False, index=True)  # soft FK → crm_staff.id
    staff_name = Column(String(64), nullable=False)            # 快照，同 HrBenefitEntry
    amount = Column(Integer, nullable=False, default=0)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # 一個人在一個活動裡只有一份額度 —— 重複發是帳對不起來的起點
        Index("uq_benefit_allowance", "pool_id", "staff_id", unique=True),
    )


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


class CashTaxonomyNode(Base):
    """收支分類樹 —— 私帳的類別／項目／子項目…（**深度不限**）。

    🔴 為什麼是樹不是三個欄位：owner 的 Google Sheet 只有三個固定分類欄
    （類別／項目／子項目），某一支要長第四、五層時他就把值溢出到旁邊的欄
    （「款別」「專案標籤」）—— owner 原話「那時候表沒地方擴充 只好先暫時放在
    專案的位置」。實測 2026-08-27：

        家用 ▸ 變動支出 ▸ 醫療保健 ▸ 乳癌治療 ▸ 台北馬偕      （五層）
        公司 ▸ 專案     ▸ 已收帳款/已付稅款/委外已付           （三層）

    深度因分支而異，所以欄位數固定的模型一定會再溢出一次。

    🔴 與 `crm_cash_entries` 的關係：那邊的 `category`/`item`/`sub_item`
    **不動、繼續寫**，改成本樹路徑**前三層的鏡射**（規則正本
    `core.cash_taxonomy.mirror_from_path`）。第四層以後只活在 `taxonomy_node_id`
    的路徑裡。這樣「加深度」不必碰 finance_category_map 對映、三表、卡債、
    core.project_link、銀行匯入規則、petty_item_for 任何一個消費端。

    🔴 `entity` 只放 'mine'。母公司那本用的是 finance_category_map 的**平面**
    科目（行政／薪資／交際應酬…），混進來私帳的類別下拉會從 5 個爆成 37 個
    （2026-08-27 實測，註解在 routers/crm/finance.py 的 cash options）。
    """
    __tablename__ = "cash_taxonomy_nodes"

    id = Column(String(32), primary_key=True)
    entity = Column(String(16), nullable=False, server_default="mine")
    # 🔴 根節點用**空字串**不是 NULL —— Postgres 的 UNIQUE 視 NULL 互不相等，
    # 用 NULL 當根，第一層就擋不掉重複。
    parent_id = Column(String(32), nullable=False, server_default="", index=True)
    name = Column(String(64), nullable=False)
    depth = Column(Integer, nullable=False, default=1)      # 1=類別 2=項目 3=子項目 …
    sort = Column(Integer, nullable=False, default=0)
    # 停用＝新列挑不到，**舊列照樣顯示**（分類會退流行，但歷史不該消失）
    active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("entity", "parent_id", "name", name="uq_cash_tax_node"),
        Index("idx_cash_tax_entity_parent", "entity", "parent_id", "sort"),
    )
