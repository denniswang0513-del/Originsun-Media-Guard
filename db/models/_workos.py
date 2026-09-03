"""Work OS：工時/請假/里程碑/月結 ＋ 前期（場景/提案/企劃/參考）＋ 情報/門戶/器材/素材（`db.models` 套件的一段，2026-08-31 拆檔）。

對外一律從 `db.models` 匯入，別直接指名這個檔。
"""
from ._base import (Base, BigInteger, Boolean, Column, DateTime, Float, Index, Integer, JSONB, String, Text, UniqueConstraint, func)

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
    hours = Column(Float, nullable=False, default=0.0)           # 實際小時；計畫列＝0 直到「完成」
    planned_hours = Column(Float, nullable=True)                 # 計畫小時（docs/WORK_TRACKING_UI_PLAN.md §2；Sheet 列 NULL）
    work_type = Column(String(32), nullable=True)                # 工作分類（core.hr_logic.WORK_TYPES；可空）
    note = Column(Text, nullable=True)                           # 管理員備註（總表手動調整時寫；員工端不顯示）
    remark = Column(Text, nullable=True)                         # 員工備註（我的一天填；內容之外的補充，大家看得到）
    edited_at = Column(DateTime(timezone=True), nullable=True)   # 總表改過（管理員）；Sheet 同格之後再變＝記衝突不自動蓋
    edited_by = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False, default="import")  # import／draft（實際）／plan（只有計畫）
    source = Column(String(16), nullable=False, default="sheet")  # sheet/manual/schedule
    row_hash = Column(String(40), nullable=False, unique=True)   # 去重：date|staff|project|task|hours
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_ts_project", "project_id"),
        Index("idx_ts_staff_date", "staff_name", "work_date"),
    )


class TimesheetTombstone(Base):
    """總表刪掉的 Sheet 列（owner 2026-09-03：拉進來的資料以總表為準）。

    Sheet 沒有列 id，冪等靠內容指紋 row_hash；刪掉列只刪 timesheets 的話，下次拉取指紋不在庫裡
    就會再插回來。這裡留指紋，ingest 看到就跳過。Sheet 那格之後若改了（指紋變）會當新列進來 ——
    那是新內容，不是這裡擋的範圍。快照欄只給人看「當時刪的是哪列」。"""
    __tablename__ = "timesheet_tombstones"

    row_hash = Column(String(40), primary_key=True)
    staff_name = Column(String(64), nullable=False, default="")
    project_name = Column(String(255), nullable=False, default="")
    work_date = Column(DateTime(timezone=True), nullable=True)
    hours = Column(Float, nullable=False, default=0.0)
    deleted_by = Column(String(64), nullable=False, default="")
    deleted_at = Column(DateTime(timezone=True), server_default=func.now())


class TimesheetConflict(Base):
    """Sheet 進來的列與總表改過的同一列（同人同日同案）內容不同：不自動插、不自動蓋，記下來等
    owner 在總表選（keep_mine 用總表的／use_sheet 用 Sheet 的／keep_both 兩列都留）。
    incoming_hash 唯一：同一個 Sheet 版本只記一次衝突。"""
    __tablename__ = "timesheet_conflicts"

    id = Column(String(32), primary_key=True)
    row_id = Column(String(32), nullable=False, index=True)        # 總表那列
    incoming_hash = Column(String(40), nullable=False, unique=True)
    incoming = Column(JSONB, nullable=False, default=dict)         # {date, staff, project, task, hours}（Sheet 原字）
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution = Column(String(16), nullable=True)                # keep_mine／use_sheet／keep_both／orphan
    resolved_by = Column(String(64), nullable=True)


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


class TimesheetProjectMap(Base):
    """工時 Sheet 的專案原字 → `crm_projects.id`（docs/TIMESHEET_IMPORT_PLAN.md D1）。

    Sheet 寫「三立電視台_國民法官劇情短片」，私帳案叫「國民法官劇情短片」；
    去掉客戶前綴後大多自動對得到，剩下的是同名撞案與內部作業（數字在規劃文件
    的 dry-run 報告，會隨 owner 指定而變）—— 那些**由 owner 決定一次**，記在這張表，
    之後 Apps Script 每小時同步進來的列自動吃到，不做一次性回填（先例：finance_category_map）。

    🔴 對映表永遠優先於任何自動規則（core.hr_logic.resolve_project 第一段）。
    """
    __tablename__ = "timesheet_project_map"

    sheet_name = Column(String(255), primary_key=True)          # Sheet 原字（含客戶前綴）
    project_id = Column(String(32), nullable=False, index=True) # soft FK → crm_projects.id
    decided_by = Column(String(64), nullable=True)              # username
    decided_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    note = Column(String(255), nullable=True)


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
    # 兩本帳 §8 延伸（2026-08-24）：器材的折舊/淨值歸哪本帳。器材清單本身共用
    # 可見（owner 拍板「只有錢區隔」）；報表引擎按 entity 各餵各的折舊。
    entity = Column(String(16), nullable=False, server_default="parent")
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


