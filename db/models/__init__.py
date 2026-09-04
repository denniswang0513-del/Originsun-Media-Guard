"""SQLAlchemy ORM models（db/models/ 套件）。

2026-08-31 從單檔 db/models.py（2,121 行）拆成套件 —— 它同時是「被最多模組
依賴」（85 個）、「超過單次讀取上限」與「三週改動第二多」（47 次）的檔案：
爆炸半徑最大、又最常被半盲修改。工法同 core/finance_logic：**純行段切割、
一個類別都沒搬家**（AST 逐名驗證過），分段照原檔的領域分節。

    _base（SQLAlchemy import 層＋stub 退路＋Base）← 其餘五段

🔴 新增模型要**同時**加進下面對應那段的清單，否則
`from db.models import 新表` 會 ImportError —— 刻意的：寧可 import 時大聲炸，
也不要半張表悄悄消失在對外表面之外。段落內部照舊 append-only；
哪一段超過 2,000 行（tests/unit/test_files_stay_readable.py 在守）就再切它。

新表 checklist 不變：這裡宣告 → db/migrations.py 加 ADD COLUMN/CREATE TABLE
→ core/schemas.py → router。
"""
from ._base import Base, _HAS_SQLALCHEMY  # noqa: F401

# 系統執行面（任務史/公布欄/機器/書籤/排程/報表）＋ User ＋ Client
from ._system import (  # noqa: F401
    Agent, Bookmark, BulletinItem, Client, JobHistory, Report, ScheduledJob, User)

# CRM：專案/報價/人力/作品 ＋ 帳務三表（發票/請款/收支）＋ ApiKey
from ._crm import (  # noqa: F401
    ApiKey, CrmCashEntry, CrmCashInvoiceLink, CrmCashPaymentLink, CrmCashSplit,
    CrmCashSplitAdvanceLink,
    CrmCostLineTemplate, CrmInvoice, CrmInvoiceTrash, CrmPaymentRequest, CrmProject,
    CrmProjectCostGroup, CrmProjectCostLine, CrmProjectExpense, CrmProjectShowcase,
    CrmProjectStaff, CrmQuotation, CrmQuotationItem, CrmQuotationTemplate,
    CrmReimbursement, CrmStaff, CrmStaffPortfolio, WEBSITE_TEAM_OVERRIDE_FIELDS)

# Work OS：工時/請假/里程碑/月結 ＋ 前期（場景/提案/企劃/參考）＋ 情報/門戶/器材/素材
from ._workos import (  # noqa: F401
    CrmShoot, Equipment, EquipmentCheckout, EquipmentMaintenance, FinanceMonthClose,
    FootageIndex, HrLeaveRequest, IntelItem, IntelSource, PROPOSAL_CHILD_TABLES,
    PaymentMilestone, PortalComment, PortalReviewLink, PreprodBrief,
    PreprodBriefTemplate, PreprodLocation, PreprodLocationPhoto,
    PreprodLocationUsage, PreprodMeetingNote, PreprodProposal, PreprodProposalRef,
    PreprodQuoteAnalysis, PreprodQuoteFile, PreprodReference, PreprodReferenceLink,
    PreprodReferenceShot, StaffRateHistory, Timesheet, TimesheetConflict, TimesheetProjectMap, TimesheetTombstone)

# 財務管理：科目/對帳單匯入/淨值快照/持倉/科目對映/銀行帳戶/貸款
from ._finance import (  # noqa: F401
    BankAccount, BankImportDraft, BankImportRule, BankReconciliation,
    BankStatementLine, FinanceAccount, FinanceAdjustment, FinanceCategoryMap,
    FinanceHolding, FinanceLoan, FinanceLoanPayment, FinanceNetSnapshot)

# 媒體紀錄 ＋ 週誌 ＋ 福委會 ＋ 收支分類樹
from ._workspace import (  # noqa: F401
    CashTaxonomyNode, CrmExpenseLink, HrBenefitAllowance, HrBenefitEntry,
    HrBenefitFunding, HrBenefitPool, JournalChallenge, JournalLearning,
    JournalOther, JournalWin, ProjectMediaFile, ProjectMediaLog, WorkJournal)
