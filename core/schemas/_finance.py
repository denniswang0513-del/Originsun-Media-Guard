"""core/schemas/_finance.py — 財務管理階段二～四（api_finance）。

拆自 core/schemas.py（2026-09-11，2,000 行剛好卡在單次讀取上限）。
界畫在原本的分節註解上、零 class 搬家；對外仍是 `from core.schemas import X`。
"""
from pydantic import BaseModel, field_validator  # type: ignore
from typing import List, Optional


# ── 財務管理階段二（routers/api_finance.py）────────────────────

class FinanceCategoryMapItem(BaseModel):
    """category → 科目 對映單列（PUT /finance/category-map 批次 upsert 用）。"""
    source: str                            # cash/payment/invoice
    category_text: str
    account_id: str                        # → finance_accounts.id
    treatment: str                         # direct_expense/direct_income/ap_settlement/...


class FinanceCategoryMapPut(BaseModel):
    items: List[FinanceCategoryMapItem] = []


class MarginModelRow(BaseModel):
    group: str = ""          # 類別：規格品／客製化／開發中／其他
    type: str                # 項目＝crm_projects.project_type
    margin_pct: float        # 預期毛利 %
    note: str = ""


class MarginModelPut(BaseModel):
    """私帳設定：預期毛利表＋人力日成本（工時預算＝合約未稅 ×（1−毛利）÷ 日成本 × 每日工時）。"""
    daily_cost: float
    hours_per_day: float = 8
    rows: List[MarginModelRow] = []
    aliases: dict = {}       # 舊案型 → 你的版本


class MarginUnifyPayload(BaseModel):
    """統一案型：{舊案型: 你的版本}，專案（兩本帳）一併改過去。"""
    aliases: dict = {}


class BookkeepingFeePut(BaseModel):
    """調整記帳費（換會計、漲價）。使用者只填這三格，歷史由後端自己留。

    `months_per_year` ＝ 一年計幾個月（雙月收一次，超過 12 的部分併在 5 月那期）。
    """
    effective_from: str                    # "YYYY-MM"，從哪一期開始用新價
    monthly: int                           # 月費
    months_per_year: int = 12


class BankAccountPayload(BaseModel):
    """銀行帳戶新增/更新 — create 時 name 必填由端點檢查。"""
    name: Optional[str] = None
    bank_name: Optional[str] = None
    account_no: Optional[str] = None
    acct_kind: Optional[str] = None        # bank / cash / shareholder_*
    staff_id: Optional[str] = None         # 股東往來綁的人員
    # 前端「不綁」送的是空字串；存 "" 會讓 own-scope 查詢配到一堆空字串帳戶
    _blank_staff = field_validator("staff_id")(lambda v: (v or "").strip() or None)
    opening_balance: Optional[int] = None
    opening_date: Optional[str] = None     # 'YYYY-MM-DD'
    is_default: Optional[bool] = None
    active: Optional[bool] = None
    sort_order: Optional[int] = None
    note: Optional[str] = None
    # 兩本帳：None＝建立時落 'parent'、更新時維持既有值；🔴 不可給 "parent" 當預設——整包 model_dump 寫回會把我的帳列洗回母公司
    entity: Optional[str] = None


class ReconciliationPayload(BaseModel):
    """銀行對帳：送對帳單月底餘額，system_balance 由後端算。"""
    bank_account_id: str
    month: str                             # 'YYYY-MM'
    statement_balance: int
    note: Optional[str] = None


class StatementLineIn(BaseModel):
    """對帳單明細單列（匯入/手動 key 共用）。amount 有號：正=存入、負=支出。"""
    line_date: Optional[str] = None        # 'YYYY-MM-DD'
    description: str = ""
    amount: int


class StatementLinesBulkPayload(BaseModel):
    """對帳單明細批次新增。

    🔴 month 是選填的：每一列自己的 line_date 就決定它屬於哪個月，一份跨月的
    對帳單不該逼人切成三次傳（owner 2026-08-20）。只有「該列沒有日期」時才退回
    用 month；連 month 都沒給就擋下來，不猜。

    replace=true 只清**這批真的涵蓋到的月份**，不是日期區間 —— 按區間清的話，
    檔案裡剛好沒有交易的那個月會被連坐清空，那個月已經勾銷好的紀錄就沒了。
    """
    bank_account_id: str
    month: Optional[str] = None            # 'YYYY-MM'，只當沒有 line_date 時的後備
    lines: List[StatementLineIn]
    replace: bool = False


class StatementLineUpdatePayload(BaseModel):
    """對帳單明細列只開放補交易日/註記 — 金額摘要要改就刪列重加。"""
    line_date: Optional[str] = None        # 'YYYY-MM-DD'（無日期匯入列補記前要先補）
    note: Optional[str] = None


class StatementLineMatchPayload(BaseModel):
    entry_id: str


class StatementLineCreateEntryPayload(BaseModel):
    """補記入帳：從對帳單明細建收支明細（category → 科目走既有對映）。"""
    category: str = ""
    summary: Optional[str] = None          # 預設帶對帳單摘要
    payee: str = ""


class StatementAutoMatchPayload(BaseModel):
    bank_account_id: str
    month: str                             # 'YYYY-MM'


class FinanceAdjustmentPayload(BaseModel):
    """財務調整列 — 不得指向銀行類科目（code 11xx），後端驗證擋下。"""
    adj_date: Optional[str] = None         # 'YYYY-MM-DD'（create 必填由端點檢查）
    account_id: Optional[str] = None       # create 必填由端點檢查
    amount: Optional[int] = None           # 有號金額
    adj_type: Optional[str] = None         # opening/correction/owner_in/owner_out/accountant/writeoff/other/vat（vat→應付營業稅，其餘→權益）
    description: Optional[str] = None      # create 必填由端點檢查
    # 兩本帳：None＝建立時落 'parent'、更新時維持既有值；🔴 不可給 "parent" 當預設——整包 model_dump 寫回會把我的帳列洗回母公司
    entity: Optional[str] = None


class BulkAssignAccountPayload(BaseModel):
    """收支明細整批掛銀行帳戶。"""
    bank_account_id: str
    only_unassigned: bool = True


class SetupWizardBankAccount(BaseModel):
    name: str
    bank_name: Optional[str] = None
    account_no: Optional[str] = None
    acct_kind: str = "bank"
    opening_balance: int = 0


class FinanceSetupWizardPayload(BaseModel):
    """財務設定精靈：一次建帳戶 + 掛歷史收支 + 設基準月 + 期初權益。"""
    baseline_month: str                    # 'YYYY-MM'
    bank_accounts: List[SetupWizardBankAccount] = []
    default_account_index: int = 0
    assign_history: bool = True
    equity_amount: Optional[int] = None    # 非空 → 建 3100 期初權益 opening 調整列


# ── 財務管理階段四：銀行貸款（routers/api_finance.py）──────────

class LoanPayload(BaseModel):
    """銀行貸款新增/更新 — create 時 name/principal/term_months/start_date
    必填由端點檢查；改利率/期數等結構欄位時後端只重生未繳期別。"""
    name: Optional[str] = None
    lender: Optional[str] = None
    principal: Optional[int] = None        # 原始本金（新台幣整數）
    annual_rate: Optional[float] = None    # 年利率 %（2.85 = 2.85%）
    term_months: Optional[int] = None      # 期數（opening_balance 模式=剩餘期數）
    method: Optional[str] = None           # annuity/straight/interest_only
    grace_months: Optional[int] = None     # 寬限期（只付息不還本）
    start_date: Optional[str] = None       # 'YYYY-MM-DD'
    first_payment_date: Optional[str] = None  # 'YYYY-MM-DD'（空=起貸日下月同日）
    bank_account_id: Optional[str] = None  # 預設扣款帳戶
    account_no: Optional[str] = None       # 銀行放款帳號（對帳單匯入配對用）
    opening_balance: Optional[int] = None  # 導入舊貸=當下剩餘本金（攤還表只生剩餘期）
    note: Optional[str] = None
    # 兩本帳：None＝建立時落 'parent'、更新時維持既有值；🔴 不可給 "parent" 當預設——整包 model_dump 寫回會把我的帳列洗回母公司
    entity: Optional[str] = None


class LoanPayPayload(BaseModel):
    """貸款繳款：自動建收支明細（expense=實扣金額、category=貸款繳款）。"""
    bank_account_id: Optional[str] = None  # 空 → 用貸款預設扣款帳戶
    paid_date: Optional[str] = None        # 'YYYY-MM-DD'，空 → 今天
    # 銀行實際扣了多少（正整數）。空 → 照攤還表本+息。
    # 對帳單匯入會自動帶銀行那個數字；手動繳款時對著存摺填才會準。
    amount: Optional[int] = None


