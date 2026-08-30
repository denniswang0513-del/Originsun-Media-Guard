"""財務管理：科目/對帳單匯入/淨值快照/持倉/科目對映/銀行帳戶/貸款（`db.models` 套件的一段，2026-08-31 拆檔）。

對外一律從 `db.models` 匯入，別直接指名這個檔。
"""
from ._base import (Base, BigInteger, Boolean, Column, DateTime, Float, Index, Integer, JSONB, String, Text, UniqueConstraint, func)

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
    # 🔴 規則**按帳本分家**（owner 2026-08-29「這裡的分類規則不需要和 crm 共用」）：
    # 母公司的類別是 finance_category_map 的平面科目（行政／薪資／交際應酬…），
    # 私帳走的是 cash_taxonomy_nodes 的樹（公司_專案／家用_變動支出…）——
    # 兩套值域根本不重疊，共用一份規則只會讓兩邊都選到對方看不懂的類別。
    # 既有 36 條全歸 parent（ALTER 的 DEFAULT 就是回填），私帳從 0 開始。
    entity = Column(String(16), nullable=False, server_default="parent")
    keyword = Column(String(64), nullable=False)                 # 摘要包含這串就命中
    # 命中後要掛的**分類樹節點**（私帳；owner 2026-08-30「規則的套用可以設定到
    # 所有的分類」）。`category` 只是路徑前兩層的鏡射，表達不了第三層以後 ——
    # 而私帳有 3,346 筆收支就掛在第三層（2026-08-30 實查），那是最需要自動
    # 分類的一批。母公司沒有樹，這一欄留空、照舊只用 category。
    taxonomy_node_id = Column(String(32), nullable=True)
    bank_account_id = Column(String(32), nullable=True)          # soft FK；空=所有帳戶
    category = Column(String(32), nullable=False)                # 命中後填的收支類別
    direction = Column(Integer, nullable=False, default=0)       # -1 支出 / +1 存入 / 0 不確定
    # 🔴 跟 direction 是兩件事，不要混：
    #   direction     = 推方向用的提示（只在餘額鏈推不出方向時才用到）
    #   only_direction = **篩選條件**：這條規則只在該方向的列上套用
    # 為什麼需要它（owner 2026-08-22 的實測）：「薪水」105 筆全在收入側、
    # 「發票」365 筆全在收入側 —— 這些關鍵字本身就分得出方向。但「薪資」
    # 10 收 / 79 支兩側都有：股東匯薪水進來是「代收薪資」、公司發給員工是
    # 「代發薪資」，同一個字兩種類別，光靠關鍵字分不出來。
    only_direction = Column(Integer, nullable=False, server_default="0")  # -1 只支出 / +1 只存入 / 0 不限
    sort_order = Column(Integer, nullable=False, default=100)    # 小的先比（多條命中時誰贏）
    active = Column(Boolean, default=True)
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_bankrule_acct", "entity", "bank_account_id", "active"),
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

class FinanceNetSnapshot(Base):
    """淨值快照（資產儀表板的時序，§8 階段 5，2026-08-24）。

    一列＝某天的資產全貌：`buckets` 是 {桶名: 金額} 的自由字典（owner 私帳的
    桶：生活帳戶/公司現金/公司應收/財富自由總額/…），`total`＝合計。
    拍快照時系統欄自動算（銀行/應收/器材/持股）、手填欄帶上次值 —— auto 欄
    存「當下系統算出的那部分」供事後稽核（快照是歷史，不隨帳目重算）。
    歷史匯入（owner Sheet 2021/3 起 117 列）與新快照同表同構。"""
    __tablename__ = "finance_net_snapshots"

    id = Column(String(32), primary_key=True)
    entity = Column(String(16), nullable=False, server_default="parent")
    snap_date = Column(DateTime(timezone=True), nullable=False)
    buckets = Column(JSONB, nullable=False, default=dict)   # {桶名: 金額}
    total = Column(BigInteger, nullable=False, default=0)
    auto = Column(JSONB, nullable=True)                     # 系統算的子集（稽核）
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("entity", "snap_date",
                                       name="uq_networth_entity_date"),)


class FinanceHolding(Base):
    """證券持股（資產儀表板的自動報價層，§8 階段 5）。

    持股手維護（買賣時改一列）、價格自動抓（services/quote_fetcher —— TWSE/
    stooq 免 key 公開源；`quote_symbol` 空＝manual，現值取 `manual_value`）。
    抓不到就沿用 last_price 並標舊價 —— 報價源斷線不能擋拍快照。"""
    __tablename__ = "finance_holdings"

    id = Column(String(32), primary_key=True)
    entity = Column(String(16), nullable=False, server_default="parent")
    broker = Column(String(64), nullable=True)              # 富邦證券/盈透/Firstrade…
    symbol = Column(String(32), nullable=True)              # 2330/0050/VTI…（顯示用）
    name = Column(String(128), nullable=False)
    shares = Column(Float, nullable=True)                   # 股數（manual 列可空）
    currency = Column(String(8), nullable=False, default="TWD")
    # 報價源代號："tse:2330" / "yahoo:VTI"（LSE 如 "yahoo:VWRA.L"）/ ""=manual
    # 🔴 只認 tse: 與 yahoo: 兩個前綴（services/quote_fetcher.py）。stooq 在
    # 2026-08-24 實測已加反爬、**不要填** —— 未知前綴的失敗長得跟「網路抓不到」
    # 一模一樣（都只是進 failed 清單），沒人查得出是格式寫錯。
    quote_symbol = Column(String(64), nullable=True, default="")
    last_price = Column(Float, nullable=True)               # 最近抓到的單價（原幣）
    price_at = Column(DateTime(timezone=True), nullable=True)
    manual_value = Column(BigInteger, nullable=True)        # 手動現值（TWD）
    # 投資成本（**這一列自己的幣別**，同 last_price 的慣例）—— 有它才算得出
    # 損益與報酬率。owner 2026-08-29：券商 App 上看到的是「+17,885,232」，
    # 系統只記得市值，那個數字生不出來。
    # 🔴 TWD 換算在讀取端（_holding_cost）做，不落庫 —— 匯率天天變，存成台幣
    # 就會凍住某一天的匯率，而市值那側是即時換算的，兩邊用不同匯率算損益。
    # 🔴 Float 不是整數（同 shares／last_price）：碎股的成本是有小數的美金，
    # 取整會把報酬率算歪 —— Firstrade 的 VEA 成本 48.42 美元存成 48，
    # 報酬率就從 16.7% 變 17.8%（2026-08-29 實例）。
    cost_total = Column(Float, nullable=True)
    sort_order = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class FinanceCategoryMap(Base):
    """收支/請款/發票 category → 科目 對映（引擎的翻譯層）。

    使用者照舊填中文 category，報表引擎查這張表決定科目與會計處理方式
    （treatment）。種子提供預設值，後台可改。

    🔴 `entity`：**`source='cash'` 的對映按帳本分家**（owner 2026-08-30
    「母公司的就是母公司，私帳就是私帳，要完全分開」）。兩本帳的收支類別值域
    根本不重疊 —— 母公司是平的科目（行政／薪資／交際應酬…），私帳是
    `cash_taxonomy_nodes` 那棵樹鏡射出來的複合鍵（家用_變動支出…）。不分家的話
    母公司的規則下拉會列出 38 個屬於私帳的類別，設下去照樣生效，那筆錢就分到一個
    公司報表沒有的類別去，然後在三表裡變成「未歸類」——而且不會噴錯。

    `payment`／`invoice` 那兩種**兩本帳共用**（私帳的 34 張請款用的就是母公司
    那批類別：其他、專案外包）。它們一律留在 'parent'，讀取端也不過濾。
    """
    __tablename__ = "finance_category_map"

    id = Column(String(32), primary_key=True)
    entity = Column(String(16), nullable=False, server_default="parent")
    source = Column(String(16), nullable=False)                  # cash/payment/invoice
    category_text = Column(String(64), nullable=False)           # 原始 category 中文值
    account_id = Column(String(32), nullable=False)              # soft FK → finance_accounts.id
    # direct_expense/direct_income/ap_settlement/ar_settlement/transfer/
    # tax_vat/tax_income/advance/passthrough/loan
    treatment = Column(String(24), nullable=False)
    active = Column(Boolean, default=True)

    # 唯一鍵帶 entity：分家之後兩本帳可以各有一個同名類別（母公司的「其他」
    # 與私帳的「其他」是不同的東西，各自對到不同科目）。
    __table_args__ = (Index("uq_fincatmap_entity_source_text",
                            "entity", "source", "category_text", unique=True),)


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
    # opening/correction/owner_in/owner_out/accountant/writeoff/other/vat
    # （vat 是唯一不落權益的 —— 它加在應付營業稅上，見 build_balance_sheet）
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


