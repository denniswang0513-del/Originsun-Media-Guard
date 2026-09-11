# -*- coding: utf-8 -*-
"""開機時跑的 DB migration SQL —— **只有資料，沒有控制流程**。

執行在 `main.py` 的 startup：那裡有 8 個獨立的 try/except 區塊，各自拿自己的
session factory、各自吞掉自己的錯誤（一個 ALTER 失敗不該讓別的或整個 startup
掛掉）。那個結構刻意留在 main.py，這裡只放要跑的句子。

🔴 **加欄位就在對應的清單末端加一行**，不要回頭去改 main.py。

規矩（踩過的坑都在這）：
- 一律 `ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` —— 每次開機都會跑。
- 帶 `NOT NULL DEFAULT` 之前先想清楚**既有列會被填成什麼**。
  `crm_project_expenses.status` 就是為此刻意不帶 DEFAULT：帶了的話既有雜支
  會全部變成「草稿」，憑空出現在請款清單裡。
- `UPDATE` 句要寫成重跑命中 0 列（冪等），因為它每次開機都會執行。
- 需要讀資料才能判斷的回填（例如「這個類別屬於哪本帳」）**不要寫在這裡**，
  那種要寫成 Python，排在對應的種子之後跑 —— 見
  `db/seed_finance.backfill_category_map_entity`。
"""

# ── RBAC v2：角色層移除後，把每個帳號的 modules/access_level 從舊角色回填 ──
# 🔴 這一份是**函式**不是常數：它要把 core.auth.ALL_MODULES 內插進 SQL，
# 而那份清單是活的（新增模組時會變）。其餘七份都是純字面常數。
def rbac_v2_backfill(all_modules_json: str) -> list:
    """`all_modules_json` ＝ `json.dumps(list(ALL_MODULES))`。"""
    return [
        # 1) 有 role_id 的使用者：複製其角色的 modules + access_level
        "UPDATE users u SET modules = r.modules, access_level = r.access_level "
        "FROM roles r WHERE u.role_id = r.id AND u.modules IS NULL",
        # 2) admin 保險（萬一沒有 role_id）：給全模組 + Lv3
        f"UPDATE users SET access_level = 3, modules = '{all_modules_json}'::jsonb "
        "WHERE username = 'admin' AND modules IS NULL",
        # 3) 其餘殘留 NULL → 一般使用者、空模組（管理員可再授權）
        "UPDATE users SET access_level = 1 WHERE access_level IS NULL",
        "UPDATE users SET modules = '[]'::jsonb WHERE modules IS NULL",
    ]

# ── CRM 查詢索引（報價/發票/請款/專案） ─────────────────────────
CRM_INDEXES = [
        "CREATE INDEX IF NOT EXISTS idx_quote_created ON crm_quotations(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_invoice_issue_status ON crm_invoices(issue_status)",
        "CREATE INDEX IF NOT EXISTS idx_invoice_pay_status ON crm_invoices(payment_status)",
        "CREATE INDEX IF NOT EXISTS idx_payreq_planned_month ON crm_payment_requests(planned_month)",
        "CREATE INDEX IF NOT EXISTS idx_payreq_payee ON crm_payment_requests(payee_name)",
        # 提案=專案合體：專案列表附掛提案子狀態的 scalar subquery 用
        "CREATE INDEX IF NOT EXISTS idx_pprop_project ON preprod_proposals(project_id, updated_at)",
        # 工時：看板／總表／匯出都只用日期範圍過濾（staff_name,work_date 的複合索引吃不到）；場次器材預約查 shoot_id
        "CREATE INDEX IF NOT EXISTS idx_ts_work_date ON timesheets(work_date)",
        "CREATE INDEX IF NOT EXISTS ix_equipment_checkouts_shoot_id ON equipment_checkouts(shoot_id)",
        # §14 工作流的聚合查詢（EXPLAIN 實測補的四顆）：
        # ① timesheets 用名稱對映那一臂原本是 Seq Scan，而「查不到」
        #    才是年輕專案的常態 —— 也就是每次開進度分頁都掃全表
        "CREATE INDEX IF NOT EXISTS idx_ts_project_name ON timesheets(project_name)",
        # ② 場景使用履歷只索引了 location_id
        "CREATE INDEX IF NOT EXISTS idx_ploc_usage_project ON preprod_location_usages(project_id)",
        # ③④ 單欄索引下 planner 只能挑一個，另一個變 Filter；
        #    inv_unpaid 是 COUNT 不能短路 → 會走遍全公司的未收款發票
        "CREATE INDEX IF NOT EXISTS idx_invoice_project_status ON crm_invoices(project_id, payment_status)",
        "CREATE INDEX IF NOT EXISTS idx_quote_project_status ON crm_quotations(project_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_quote_share_token ON crm_quotations(share_token)",
        # 提案=專案合體：前期草稿提案還沒定客戶也要能是專案
        # （ALTER 冪等 — 已 DROP 過再跑一次不會錯）
        "ALTER TABLE crm_projects ALTER COLUMN client_id DROP NOT NULL",
    ]

# ── 財務 + CRM 的欄位增修（貸款、專案、工時、兩本帳…）—— 最大的一批 ─────────────────────────
FINANCE_AND_CRM_COLUMNS = [
        # 對帳單匯入：貸款的銀行放款帳號（合庫備註帶 315614 這種號碼，
        # 靠它認出扣款屬於哪筆貸款 —— 金額配對在銀行月付固定、我們重算
        # 的情況下會差幾十元，帳號才是可靠的鍵）
        "ALTER TABLE finance_loans ADD COLUMN IF NOT EXISTS account_no VARCHAR(32)",
        # 銀行實扣金額（空＝照攤還表）。銀行按實際天數算息會跟我們
        # 重算的差幾元，記攤還表數字會讓銀行餘額逐期累積偏差、
        # 對帳工作台也永遠勾不掉那些列。
        "ALTER TABLE finance_loan_payments ADD COLUMN IF NOT EXISTS paid_amount INTEGER",
        # 結案製作看板：結案專案的官網製作階段（待製作/製作中/不上官網）
        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS website_prod_stage VARCHAR(16)",
        # N2 階段0：專案時數預算池（對齊工時 Sheet 的預算欄，藍圖 §3 現況修正）
        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS budget_hours DOUBLE PRECISION",
        # N-now 上架驗收：rebuild 後對外頁 200 驗證通過的時間戳
        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS website_verified_at TIMESTAMP WITH TIME ZONE",
        # §14 工作流：五軌進度裡「推不出來」的手動里程碑（開拍/剪輯完成）。
        # 值在 DB、欄目在 core/project_flow.py —— 與 archive_checklist /
        # proposal_survey 同一套慣例，所以不另建表。
        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS flow_checks JSONB",
        # H1 員工檔案完整化（HR_FIN_PLAN）
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS employment_type VARCHAR(16)",
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS hire_date TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS leave_date TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS emergency_contact VARCHAR(128)",
        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS sub_item VARCHAR(128)",
        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS payee VARCHAR(64)",
        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS advance_id VARCHAR(32)",
        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS advance_by VARCHAR(64)",
        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS is_advance INTEGER DEFAULT 0",
        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS advance_returned INTEGER DEFAULT 0",
        # 代開自動化的冪等鍵改用發票 id（無號發票也有鍵）
        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS source_invoice_id VARCHAR(32)",
        "CREATE INDEX IF NOT EXISTS idx_payreq_src_invoice ON crm_payment_requests(source_invoice_id)",
        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS advance_payment_id VARCHAR(32)",
        # 兩本帳：私帳客戶分家（owner 2026-08-26「先不要混到 crm」）
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
        # 收支雙備註（owner 2026-08-26）：銀行原始資訊與手寫附註分欄
        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS bank_memo TEXT",
        # 拆項的發票代開費（owner 2026-08-31：源日代開發票、扣完費用才匯）
        # —— amount 記實匯淨額、fee 外加，專案已收按毛額結清
        "ALTER TABLE crm_cash_splits ADD COLUMN IF NOT EXISTS fee INTEGER",
        # 專案「連結私帳」改成多對一（owner 2026-09-01「可以多筆專案連結到
        # 一筆私帳」）：連結記在**來源**那一側，一個私帳案可以承接多個 CRM 案
        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS mine_link_id VARCHAR(32)",
        "CREATE INDEX IF NOT EXISTS idx_project_mine_link"
        " ON crm_projects (mine_link_id)",
        # 顯示名覆寫（owner 2026-09-05「可以在顯示層讓我自訂名稱嗎」）——
        # 連到多個母帳案時自動規則挑不出誰對，由 owner 自己命名。
        # 空＝走自動規則。鏈的正本 core.ledger_project.linked_display_name。
        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS"
        " display_name VARCHAR(255)",
        # 合約金額的來源標記（owner 2026-09-05「母帳如果沒有 先填私帳的
        # 後面再改」）：'mine'＝從私帳帶過來的**佔位**，還沒人確認過。
        # 佔位是「我拿到的那段」不是公司合約額，一定偏低 —— 母公司專案毛利
        # 與現金流預測都要排除它，見 docs/LEDGER_UNIFY_PLAN.md §2.2。
        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS"
        " contract_amount_source VARCHAR(8)",
        # 規則命中後要寫進那一列的備註（owner 2026-09-01「也可以記憶備註」）。
        # 跟既有的 `note`（規則自己的備忘）是兩件事，見 BankImportRule 的註解。
        "ALTER TABLE bank_import_rules ADD COLUMN IF NOT EXISTS"
        " apply_note VARCHAR(255)",
        # 私帳客戶 → CRM 客戶對應連結（owner 2026-08-26「用連結的方式同步」）
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS crm_link_id VARCHAR(32)",
        # 客戶代稱改「每本帳唯一」—— 同一家公司 CRM 一筆＋私帳一筆
        # （owner 2026-08-26「兩邊各一筆＋連結」），名字要能一樣
        "ALTER TABLE clients DROP CONSTRAINT IF EXISTS clients_short_name_key",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_client_entity_short_name"
        " ON clients (entity, short_name)",
        # ── 零用金請款（docs/PETTY_CASH_PLAN.md）──────────────
        # 支出單據行擴充：一筆登記同時餵專案成本與個人請款。
        # project_id 放寬成可空 —— 公司層級支出（行政/業務推廣）沒有
        # 專案，歷史資料 289/443 列如此。既有 241 列不受影響。
        "ALTER TABLE crm_project_expenses ALTER COLUMN project_id DROP NOT NULL",
        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS expense_date TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS staff_id VARCHAR(32)",
        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS item VARCHAR(32)",
        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS invoice_no VARCHAR(32)",
        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS has_invoice INTEGER DEFAULT 0",
        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS claim_id VARCHAR(32)",
        # 🔴 這欄**不能**帶 DEFAULT：帶了的話 ADD COLUMN 會把既有列
        # 全部填成「草稿」，而草稿在零用金的語意是「還沒送出的請款」——
        # 既有雜支會憑空出現在請款清單裡。留 NULL 再由下一句補終態。
        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS status VARCHAR(16)",
        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS project_label VARCHAR(128)",
        "CREATE INDEX IF NOT EXISTS idx_expense_staff_claim "
        "ON crm_project_expenses (staff_id, claim_id)",
        "CREATE INDEX IF NOT EXISTS idx_expense_date ON crm_project_expenses (expense_date)",
        # 既有列是「已經在專案裡的雜支」，不是待請款單據 —— 補一個終態。
        # 條件帶 claim_id IS NULL：日後真正的草稿都有批次或會自己走
        # ORM 預設值，不會被這句掃到（這句每次 boot 都跑）。
        "UPDATE crm_project_expenses SET status='已付款' "
        "WHERE status IS NULL AND claim_id IS NULL",
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS petty_float INTEGER DEFAULT 0",
        "UPDATE crm_staff SET petty_float=0 WHERE petty_float IS NULL",
        # 2026-09-07：請款分頁彈窗曾把「發票代開」的發票 id 寫進 project_id（思沙龍 EP01/EP02、快樂學游泳），
        # 專案欄空白、應付帳款連不回案。改回發票掛的案（發票沒案就清空）；每次 boot 跑、改過就不再命中。
        "UPDATE crm_payment_requests p SET project_id = i.project_id FROM crm_invoices i "
        "WHERE p.source_invoice_id IS NOT NULL AND p.project_id = p.source_invoice_id AND i.id = p.source_invoice_id",
        # 同一批單的 payment_status 被彈窗洗成空字串（表單沒那格卻照送）；應付帳款只認「應付款／未付款」
        "UPDATE crm_payment_requests SET payment_status='應付款' WHERE payment_status IS NULL OR payment_status=''",
        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS expense_id VARCHAR(32)",
        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS reimbursement_id VARCHAR(32)",
        # 費用歸屬人（後期雜支那類「別人墊、帳算你的」）
        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS owner_staff_id VARCHAR(32)",
        "ALTER TABLE crm_project_expenses ADD COLUMN IF NOT EXISTS owner_settled INTEGER DEFAULT 0",
        "CREATE INDEX IF NOT EXISTS idx_expense_owner ON crm_project_expenses (owner_staff_id, owner_settled)",
        "CREATE INDEX IF NOT EXISTS idx_payreq_reimb ON crm_payment_requests (reimbursement_id)",
        "CREATE INDEX IF NOT EXISTS idx_cash_expense ON crm_cash_entries (expense_id)",
        # receipt_path 從 crm_projects 下放到 crm_project_cost_groups。
        # 第一行先確保 cost_groups 有此欄位；接著一次性把舊值搬到
        # 該專案 sort_order 最小的子表（且子表還沒設值時）；最後 DROP
        # 掉 crm_projects 的舊欄位。三句都 idempotent，跑多次無害。
        "ALTER TABLE crm_project_cost_groups ADD COLUMN IF NOT EXISTS receipt_path VARCHAR(512)",
        """
        UPDATE crm_project_cost_groups cg
           SET receipt_path = p.receipt_path
          FROM crm_projects p
         WHERE cg.project_id = p.id
           AND p.receipt_path IS NOT NULL
           AND p.receipt_path <> ''
           AND (cg.receipt_path IS NULL OR cg.receipt_path = '')
           AND cg.sort_order = (
               SELECT MIN(sort_order) FROM crm_project_cost_groups
                WHERE project_id = p.id
           )
        """,
        "ALTER TABLE crm_projects DROP COLUMN IF EXISTS receipt_path",
        # freeform sc.tags 廢除，統一走 website_categories（kind=tag）。
        # 舊資料一併刪除（使用者確認）。
        "ALTER TABLE crm_project_showcase DROP COLUMN IF EXISTS tags",
        # B5 素材庫：pg_trgm 加速 transcript ILIKE（extension 沒權限就
        # 降級純 ILIKE，查詢語法相同——這兩句失敗都無妨，有 try/except 容錯）。
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        "CREATE INDEX IF NOT EXISTS idx_footage_transcript_trgm "
        "ON footage_index USING gin (transcript gin_trgm_ops)",
        # ── 收支分類樹 ─────────────────────────────────────
        # cash_taxonomy_nodes **新表由 startup 的 create_all 建**
        # （schema 正本＝db.models.CashTaxonomyNode，跟 hr_leave_requests
        # 同慣例 —— 這裡再抄一份 DDL 就是第二份會漂的 schema）。
        # 既有表的加欄與其索引才走這裡：
        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS taxonomy_node_id VARCHAR(32)",
        # 委外請款單 ↔ 專案成本行（私帳「委外人員名單」一鍵請款）
        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS cost_line_id VARCHAR(32)",
        "CREATE INDEX IF NOT EXISTS idx_payreq_cost_line "
        "ON crm_payment_requests (cost_line_id)",
        # 雜支請款單 ↔ 專案雜支行（私帳「行政雜支明細」逐項請款）
        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS "
        "expense_id VARCHAR(32)",
        "CREATE INDEX IF NOT EXISTS idx_payreq_expense "
        "ON crm_payment_requests (expense_id)",
        # 證券持股的投資成本（owner 2026-08-29）—— 有它才算得出損益
        "ALTER TABLE finance_holdings ADD COLUMN IF NOT EXISTS "
        "cost_total DOUBLE PRECISION",
        # 2026-08-29 當天從 BIGINT 改浮點（碎股成本有小數）——
        # 欄位當天才建、生產零筆資料，改型別無損
        "ALTER TABLE finance_holdings ALTER COLUMN cost_total "
        "TYPE DOUBLE PRECISION",
        "CREATE INDEX IF NOT EXISTS idx_cash_taxonomy_node "
        "ON crm_cash_entries (taxonomy_node_id)",
        # 假勤重整（docs/LEAVE_PLAN.md §7.2）：小時成為正本，舊列 days×8 一次回填。
        # 欄位在 main.py `_crm_cols` 加（那段先跑），這句每次開機跑、填過就命中 0 列。
        "UPDATE hr_leave_requests SET hours = days * 8 WHERE hours IS NULL",
    ]

# ── 專案成本行的欄位 ─────────────────────────
COST_LINE_COLUMNS = [
        "ALTER TABLE crm_project_cost_lines ADD COLUMN IF NOT EXISTS estimated_unit_price INTEGER",
        "ALTER TABLE crm_project_cost_lines ADD COLUMN IF NOT EXISTS estimated_quantity INTEGER",
        "ALTER TABLE crm_project_cost_lines ADD COLUMN IF NOT EXISTS actual_unit_price INTEGER",
        "ALTER TABLE crm_project_cost_lines ADD COLUMN IF NOT EXISTS actual_quantity INTEGER",
        "ALTER TABLE crm_project_cost_lines ADD COLUMN IF NOT EXISTS estimated_unit_type VARCHAR(16)",
        "ALTER TABLE crm_project_cost_lines ADD COLUMN IF NOT EXISTS actual_unit_type VARCHAR(16)",
    ]

# ── 人員檔案 + 結案上架的欄位 ─────────────────────────
STAFF_AND_SHOWCASE_COLUMNS = [
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS photo_url VARCHAR(512)",
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS bio TEXT",
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS skills JSONB",
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS education JSONB",
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS experience JSONB",
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS awards JSONB",
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS resume_visible BOOLEAN DEFAULT FALSE",
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS edit_token VARCHAR(512)",
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS resume_editable BOOLEAN DEFAULT TRUE",
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS created_via VARCHAR(20) DEFAULT 'admin'",
        "ALTER TABLE crm_staff ADD COLUMN IF NOT EXISTS created_for_project_id VARCHAR(32)",
    ]

# ── 結案上架的 AI 參考資料欄位 ─────────────────────────
SHOWCASE_AI_COLUMNS = [
        "ALTER TABLE crm_project_showcase ADD COLUMN IF NOT EXISTS ai_reference_files JSONB",
        "ALTER TABLE crm_project_showcase ADD COLUMN IF NOT EXISTS ai_reference_notes TEXT",
    ]

# ── 成本子表（cost_groups）的關聯欄位 ─────────────────────────
COST_GROUP_COLUMNS = [
        "ALTER TABLE crm_project_cost_lines ADD COLUMN IF NOT EXISTS cost_group_id VARCHAR(32)",
        "ALTER TABLE crm_project_expenses  ADD COLUMN IF NOT EXISTS cost_group_id VARCHAR(32)",
        "CREATE INDEX IF NOT EXISTS idx_cl_group  ON crm_project_cost_lines(cost_group_id)",
        "CREATE INDEX IF NOT EXISTS idx_exp_group ON crm_project_expenses(cost_group_id)",
    ]

# ── 財務／兩本帳／對帳單匯入的欄位與索引 ─────────────────────────
FINANCE_LEDGER_COLUMNS = [
        # 收支明細掛銀行帳戶 + AP 硬連結（→ crm_payment_requests）
        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS bank_account_id VARCHAR(32)",
        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS payment_request_id VARCHAR(32)",
        # 貸款繳款硬連結（→ finance_loan_payments；treatment=loan 不進損益，階段四）
        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS loan_payment_id VARCHAR(32)",
        "CREATE INDEX IF NOT EXISTS idx_cash_bank_account ON crm_cash_entries(bank_account_id)",
        "CREATE INDEX IF NOT EXISTS idx_cash_payment_request ON crm_cash_entries(payment_request_id)",
        # 發票收款日（AR 收現時間戳，收支關聯收款時自動回填）
        "ALTER TABLE crm_invoices ADD COLUMN IF NOT EXISTS paid_date TIMESTAMPTZ",
        # 器材除役日（折舊自該月停止）
        "ALTER TABLE equipment ADD COLUMN IF NOT EXISTS retired_date TIMESTAMPTZ",
        # 對帳工作台：一筆收支只能被一列對帳單明細認領（既有表補建；
        # 新環境 create_all 隨 model 建）— 也是 matched_entry_id 查詢的索引
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_stmtline_matched_entry "
        "ON bank_statement_lines(matched_entry_id) WHERE matched_entry_id IS NOT NULL",
        # 已開立的電子發票檔（存磁碟絕對路徑；根目錄 settings.invoices_root）
        "ALTER TABLE crm_invoices ADD COLUMN IF NOT EXISTS file_url VARCHAR(512)",
        # 客戶下載電子發票的分享連結 token
        "ALTER TABLE crm_invoices ADD COLUMN IF NOT EXISTS share_token VARCHAR(512)",
        # 分享碼是免登入端點的查詢條件（索引命中，不讓匿名請求逼出全表掃描），
        # 同時擋短碼碰撞。partial index：NULL 不算重複
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_share_token "
        "ON crm_invoices (share_token) WHERE share_token IS NOT NULL",
        # ── 兩本帳（公司實體）：錢流 7 表加 entity 欄
        # （parent=母公司（預設）/mine=我的帳，docs/LEDGER_ENTITY_PLAN.md §1.1）
        # 兩本帳 §8：專案的錢流歸屬（2026-08-24 解凍，專案/客戶共用、錢分帳）
        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
        # 兩本帳 §8 延伸：器材折舊歸屬（owner 私帳 123 項器材，階段 4）
        "ALTER TABLE equipment ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
        # 逐案損益明細（工項拆分 + 委外/稅費），見 db/models.CrmProject.ledger_detail
        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS ledger_detail JSONB",
        # 私帳案推送到專案管理（標「後期專案」），見 db/models.CrmProject.crm_pushed
        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS crm_pushed INTEGER NOT NULL DEFAULT 0",
        # 連結私帳：私帳案 → 它是哪個母公司專案的收入分身
        # （見 db/models.CrmProject.source_project_id）
        # 分類規則按帳本分家（既有列 DEFAULT 'parent' ＝回填）
        "ALTER TABLE bank_import_rules ADD COLUMN IF NOT EXISTS "
        "entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
        # 規則可以指定到分類樹的任何一層（見 BankImportRule）
        "ALTER TABLE bank_import_rules ADD COLUMN IF NOT EXISTS "
        "taxonomy_node_id VARCHAR(32)",
        "CREATE INDEX IF NOT EXISTS ix_bankrule_entity "
        "ON bank_import_rules (entity, bank_account_id, active)",
        "ALTER TABLE crm_projects ADD COLUMN IF NOT EXISTS source_project_id VARCHAR(32)",
        "CREATE INDEX IF NOT EXISTS idx_projects_source_project "
        "ON crm_projects (source_project_id)",
        "ALTER TABLE crm_invoices ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
        "ALTER TABLE crm_payment_requests ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
        "ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
        "ALTER TABLE bank_accounts ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
        # 股東往來帳戶綁人員（2026-08-21）：綁了那位股東登入就看得到自己的往來
        "ALTER TABLE bank_accounts ADD COLUMN IF NOT EXISTS staff_id VARCHAR(32)",
        # 對帳單匯入規則的方向條件（2026-08-22）：
        # 「薪資」兩側都有（收＝代收薪資、支＝代發薪資），
        # 光靠關鍵字分不出來，要能限定方向。
        "ALTER TABLE bank_import_rules ADD COLUMN IF NOT EXISTS only_direction INTEGER NOT NULL DEFAULT 0",
        # 科目對映按帳本分家（2026-08-30，見 db.models.FinanceCategoryMap）。
        # 既有列走 DEFAULT 'parent'，私帳那批由下面的一次性回填改判
        # （回填要用到分類樹，所以放在 seed_cash_taxonomy 之後跑）。
        "ALTER TABLE finance_category_map ADD COLUMN IF NOT EXISTS "
        "entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
        # 舊唯一鍵沒有 entity，分家之後兩本帳不能各有一個同名類別 ——
        # 換成帶 entity 的那把。用 INDEX 不用 CONSTRAINT 是為了
        # IF NOT EXISTS（Postgres 的 ADD CONSTRAINT 沒有這個語法）。
        "ALTER TABLE finance_category_map "
        "DROP CONSTRAINT IF EXISTS uq_fincatmap_source_text",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_fincatmap_entity_source_text "
        "ON finance_category_map (entity, source, category_text)",
        # 福委會登記的心得筆記（2026-08-21，非必填）
        "ALTER TABLE hr_benefit_entries ADD COLUMN IF NOT EXISTS reflection TEXT",
        # 福委會：年度活動（每人額度＋期間）與說明／附件
        # （2026-08-21，docs/BENEFIT_POOL_PLAN.md §9）。
        # 既有兩個池走預設值 shared ＝ 行為完全不變。
        "ALTER TABLE hr_benefit_pools ADD COLUMN IF NOT EXISTS quota VARCHAR(16) NOT NULL DEFAULT 'shared'",
        "ALTER TABLE hr_benefit_pools ADD COLUMN IF NOT EXISTS description TEXT",
        "ALTER TABLE hr_benefit_pools ADD COLUMN IF NOT EXISTS attachments JSONB",
        "ALTER TABLE hr_benefit_pools ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ",
        "ALTER TABLE hr_benefit_pools ADD COLUMN IF NOT EXISTS valid_to TIMESTAMPTZ",
        "CREATE INDEX IF NOT EXISTS idx_bank_acct_staff ON bank_accounts (staff_id)",
        "ALTER TABLE finance_adjustments ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
        "ALTER TABLE finance_loans ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
        "ALTER TABLE finance_month_close ADD COLUMN IF NOT EXISTS entity VARCHAR(16) NOT NULL DEFAULT 'parent'",
        # （v1 曾用 'own' 當預設值，只存在於 dev DB，且從未 commit／發版。
        #  2026-08-19 已確認 dev 七表 own=0、default 全是 'parent'，
        #  prod 這根欄是本次才建、直接就是 'parent' —— 一次性的
        #  SET DEFAULT / UPDATE own→parent 已無事可做，不留在啟動路徑上
        #  每次開機空掃七張錢流表。）
        # month_close 的 unique 從全域 month 改為 (entity, month) 複合
        "ALTER TABLE finance_month_close DROP CONSTRAINT IF EXISTS finance_month_close_month_key",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_month_close_entity_month "
        "ON finance_month_close (entity, month)",
    ]


# ── CRM／工作台的 ADD COLUMN 清單：(table, column, type) ──────────────────────
# 🔴 2026-09-11 從 main.py::_on_startup 搬來（那支函式 765 行、其中 130 行是這份資料）。
# 開機時逐條 `ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {c} {type}`，失敗 rollback 不擋啟動。
# **本週的新欄位加在這裡的末端**，不要回 main.py。新表走 create_all，這裡只補舊庫的欄位。
CRM_COLUMNS = [
    ("crm_projects", "start_date", "TIMESTAMPTZ"),
    ("crm_projects", "completion_date", "TIMESTAMPTZ"),
    ("crm_projects", "project_type", "VARCHAR(64) DEFAULT ''"),
    ("crm_projects", "contract_amount", "INTEGER"),
    ("crm_projects", "tax_rate", "INTEGER DEFAULT 5"),
    ("crm_projects", "profit_target_pct", "INTEGER DEFAULT 20"),
    ("crm_projects", "misc_budget_pct", "INTEGER DEFAULT 5"),
    ("crm_projects", "payment_status", "VARCHAR(32) DEFAULT '未到帳'"),
    ("crm_projects", "amount_receivable", "INTEGER"),
    ("crm_projects", "amount_received", "INTEGER"),
    ("crm_projects", "transfer_fee", "INTEGER"),
    ("crm_quotation_items", "internal_cost", "INTEGER DEFAULT 0"),
    ("crm_quotations", "spec", "TEXT"),
    ("crm_quotations", "share_token", "VARCHAR(64)"),
    ("crm_quotations", "chat", "JSONB"),
    ("crm_quotations", "pdf_snapshot", "JSONB"),
    ("crm_invoices", "share_snapshot", "JSONB"),
    ("crm_project_staff", "phase", "VARCHAR(32) DEFAULT ''"),
    ("crm_project_staff", "actual_days", "INTEGER"),
    ("crm_project_staff", "actual_cost", "INTEGER"),
    ("crm_project_staff", "payment_status", "VARCHAR(32)"),
    ("crm_project_staff", "payment_date", "TIMESTAMPTZ"),
    ("crm_staff", "address", "VARCHAR(255)"),
    ("crm_payment_requests", "planned_month", "VARCHAR(7)"),
    ("crm_invoices", "recipient", "VARCHAR(128)"),
    ("crm_invoices", "recipient_phone", "VARCHAR(32)"),
    ("crm_invoices", "recipient_address", "VARCHAR(255)"),
    ("crm_invoices", "project_ids", "TEXT"),
    ("crm_cash_entries", "updated_at", "TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP"),
    ("crm_cash_entries", "invoice_id", "VARCHAR(32)"),
    ("crm_cash_entries", "bank_fee", "INTEGER"),
    # N-hr H2 出缺勤 + 工時 staff_id 對映（hr_leave_requests 新表由 create_all 建）
    ("crm_staff", "annual_leave_days", "INTEGER"),
    ("timesheets", "staff_id", "VARCHAR(32)"),
    # 工作追蹤 P1（docs/WORK_TRACKING_UI_PLAN.md §2）：計畫小時＋工作分類
    ("timesheets", "planned_hours", "DOUBLE PRECISION"),
    ("timesheets", "work_type", "VARCHAR(32)"),
    ("timesheets", "note", "TEXT"),
    ("timesheets", "edited_at", "TIMESTAMPTZ"),
    ("timesheets", "edited_by", "VARCHAR(64)"),
    ("timesheets", "remark", "TEXT"),
    # 工作階段＋待辦互連（docs/JOURNAL_WORKLOG_PLAN.md §12／§2-C4）；work_stage_nodes 新表由 create_all 建
    ("timesheets", "stage_id", "VARCHAR(32)"),
    ("timesheets", "planned_by", "VARCHAR(64)"),      # 兼職排班：誰幫排的（2026-09-08）
    ("timesheets", "stage_name", "VARCHAR(64)"),
    ("timesheets", "bulletin_id", "VARCHAR(32)"),
    # 我的一天格子的起／訖（owner 2026-09-06：重新整理不能消失）
    ("timesheets", "sheet_key", "VARCHAR(255)"),
    ("timesheets", "start_time", "VARCHAR(5)"),
    ("timesheets", "end_time", "VARCHAR(5)"),
    # 週記草稿→送出（§13）＋ 條目掛案子／求助標記（§2-B3／B4）；journal_replies 新表由 create_all 建
    ("work_journals", "status", "VARCHAR(16)"),
    ("work_journals", "submitted_at", "TIMESTAMPTZ"),
    ("journal_wins", "project_id", "VARCHAR(32)"),
    ("journal_wins", "flag", "VARCHAR(16)"),
    ("journal_challenges", "project_id", "VARCHAR(32)"),
    ("journal_challenges", "flag", "VARCHAR(16)"),
    ("journal_learnings", "project_id", "VARCHAR(32)"),
    ("journal_learnings", "flag", "VARCHAR(16)"),
    ("journal_others", "project_id", "VARCHAR(32)"),
    ("journal_others", "flag", "VARCHAR(16)"),
    # 影像紀錄：子資料夾名（首次生成後固定，見 media_log._ensure_folder_name）
    ("project_media_log", "folder_name", "VARCHAR(255)"),
    # 提案庫資產夾名（core.project_folders，2026-08-06）
    ("crm_projects", "proposal_folder_name", "VARCHAR(255)"),
    # 結案歸檔清單 + 專案回顧 KPTA（core/project_archive.py）
    ("crm_projects", "archive_checklist", "JSONB"),
    ("crm_projects", "review_kpta", "JSONB"),
    # 提案企劃矩陣（docs/PROPOSAL_PLANNER.md）
    ("preprod_proposals", "plan", "JSONB"),
    ("preprod_proposals", "notes", "TEXT"),   # 基本資料備註（§9.7）
    # 現況盤點表（core/proposal_survey.py — 對齊 owner 的 Notion 專案啟動面版）
    ("preprod_proposals", "survey", "JSONB"),
    # 重點提案勾選（core/pinned_assets.py — 取代舊的「對外分享」子夾）
    ("preprod_proposals", "pinned_assets", "JSONB"),
    ("preprod_proposals", "pins_public", "BOOLEAN DEFAULT FALSE"),
    # 提案在專案資產夾底下的子夾（一專案多提案時各自分開）
    ("preprod_proposals", "folder_subpath", "VARCHAR(255)"),
    # 參考影片庫 v2（docs/REFERENCE_LIBRARY.md）
    ("preprod_references", "description", "TEXT"),
    ("preprod_references", "facets", "JSONB"),
    ("preprod_references", "research", "JSONB"),
    ("preprod_references", "curated", "BOOLEAN"),
    ("preprod_references", "provider", "VARCHAR(16)"),
    ("preprod_references", "video_id", "VARCHAR(64)"),
    ("preprod_references", "updated_at", "TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP"),
    ("preprod_reference_shots", "created_key", "VARCHAR(64)"),
    # 會議記錄：錄音 → 逐字稿 → AI 整理（services/meeting_transcriber）
    ("preprod_meeting_notes", "audio_rel", "VARCHAR(512)"),
    ("preprod_meeting_notes", "transcript", "TEXT"),
    ("preprod_meeting_notes", "ai_summary", "TEXT"),
    ("preprod_meeting_notes", "status", "VARCHAR(16)"),
    ("preprod_meeting_notes", "error", "TEXT"),
    ("preprod_meeting_notes", "phase", "VARCHAR(64)"),
    # 單篇會議記錄唯讀分享（owner 2026-08-15）
    ("preprod_meeting_notes", "share_token", "VARCHAR(512)"),
    # 影片封存（docs/REFERENCE_LIBRARY.md §12）
    ("preprod_references", "archive_status", "VARCHAR(16)"),
    ("preprod_references", "archive_path", "VARCHAR(512)"),
    ("preprod_references", "archive_error", "TEXT"),
    ("preprod_references", "archived_at", "TIMESTAMPTZ"),
    ("preprod_references", "archive_tries", "INTEGER"),
    # 收款↔發票分配的逐張匯費（owner 2026-08-24）。沒有這欄的話
    # 關聯面板每次載入那格都是空的 → 按一下儲存就送 fee=0，
    # deposit 退回去、bank_fee 被清掉（靜默回退，畫面看不出來）。
    ("crm_cash_invoice_links", "fee", "INTEGER NOT NULL DEFAULT 0"),
    # 行事曆：器材預約列掛在哪一場拍攝（docs/SHOOT_CALENDAR_PLAN.md）
    ("equipment_checkouts", "shoot_id", "VARCHAR(32)"),
    # 假勤重整（docs/LEAVE_PLAN.md §7.2）：小時正本／半天時段／退回與消假說明／日曆三欄；
    # hr_leave_credits／allocations／holidays 新表由 create_all 建，舊列 hours 回填在 db/migrations.py
    ("hr_leave_requests", "hours", "DOUBLE PRECISION"),
    ("hr_leave_requests", "part", "VARCHAR(8) NOT NULL DEFAULT 'all'"),
    ("hr_leave_requests", "start_time", "VARCHAR(5)"),
    ("hr_leave_requests", "end_time", "VARCHAR(5)"),
    ("hr_leave_requests", "reject_note", "TEXT"),
    ("hr_leave_requests", "cancel_note", "TEXT"),
    ("hr_leave_requests", "google_event_id", "VARCHAR(255)"),
    ("hr_leave_requests", "synced_at", "TIMESTAMPTZ"),
    ("hr_leave_requests", "sync_error", "TEXT"),
    # 備份三根掛在專案上（owner 2026-09-10）：canonical UNC，
    # 空＝這個案沒設定，備份頁退回手動輸入
    ("crm_projects", "backup_local_root", "TEXT"),
    ("crm_projects", "backup_nas_root", "TEXT"),
    ("crm_projects", "backup_proxy_root", "TEXT"),
    # 匯款通知（owner 2026-09-11）：這一筆屬於哪一次匯款。
    # crm_payouts 新表由 create_all 建，這裡只補舊庫的欄位。
    ("crm_payment_requests", "payout_id", "VARCHAR(32)"),
    # 人員代稱（owner 2026-09-11）：綽號 → 人，帳務那邊靠它對到帳號
    ("crm_staff", "alias", "TEXT"),
]
