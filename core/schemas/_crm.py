"""core/schemas/_crm.py — CRM（客戶／專案／報價／人員／帳務／匯款通知）。

拆自 core/schemas.py（2026-09-11，2,000 行剛好卡在單次讀取上限）。
界畫在原本的分節註解上、零 class 搬家；對外仍是 `from core.schemas import X`。
"""
from pydantic import BaseModel, Field, field_validator  # type: ignore
from typing import List, Literal, Optional

from core.crm_logic import normalize_tax_id

# ── CRM Schemas ──

class ClientPayload(BaseModel):
    short_name: str
    full_name: str = ""
    tax_id: str = ""
    am_username: Optional[str] = None

    # 🔴 統編補回前導 0：Excel/Sheets 把 00973926 存成數字 973926，匯出 CSV 就少了
    # 兩個 0。規則正本 core.crm_logic.normalize_tax_id（有單元測試）。
    _norm_tax_id = field_validator("tax_id")(lambda v: normalize_tax_id(v))
    source_channel: str = ""
    contact_person: str = ""
    contact_method: str = ""
    status: str = "潛在客戶"
    cooperation_note: str = ""
    payment_info: str = ""
    payment_note: str = ""
    notes: str = ""
    # 兩本帳：None＝建立落 'parent'；更新一律不換帳本（endpoint exclude 掉）
    entity: Optional[str] = None


class PayoutCreate(BaseModel):
    """匯款通知：出納**勾的**那幾筆綁成一次匯款（不是「這個月的全部」——
    部分匯款、補匯都是常態）。paid_date 沒帶＝今天。owner 2026-09-11。"""
    payment_ids: List[str] = []
    paid_date: str = ""
    entity: str = ""


class BackupRootsPayload(BaseModel):
    """備份頁回存專案三根（owner 2026-09-10）。只有**原本是空的**那幾根會被寫入，
    已設定過的一律跳過 —— 見 routers/api_backup.save_backup_roots。"""
    local_root: str = ""
    nas_root: str = ""
    proxy_root: str = ""


class CrmProjectPayload(BaseModel):
    name: str
    client_id: str
    status: str = "洽詢"
    am_username: Optional[str] = None
    pm_usernames: List[str] = []
    shoot_date: Optional[str] = None
    start_date: Optional[str] = None
    completion_date: Optional[str] = None
    project_type: str = ""
    folder_path: str = ""
    # 備份三根（owner 2026-09-10）。收 UI 打的任何形式（T:\、UNC、/share/…），
    # 寫入端過 core.drive_map.to_canonical 正規化成 UNC 再存 —— 見 CrmProject 欄位註解。
    backup_local_root: str = ""
    backup_nas_root: str = ""
    backup_proxy_root: str = ""
    description: str = ""
    notes: str = ""
    # 財務
    contract_amount: Optional[int] = None
    tax_rate: int = 5
    profit_target_pct: int = 20
    misc_budget_pct: int = 5
    # 帳務
    payment_status: str = "未到帳"
    amount_receivable: Optional[int] = None
    amount_received: Optional[int] = None
    transfer_fee: Optional[int] = None


class CrmProjectPatchPayload(CrmProjectPayload):
    """Partial-update body for PUT /projects/{id}. Inherits CrmProjectPayload
    and overrides the required fields (name, client_id) to Optional so the
    cell-by-cell auto-save can send just the dirty field. Endpoint reads via
    `model_dump(exclude_unset=True)` so non-sent fields are skipped, not
    overwritten with their defaults.
    """
    name: Optional[str] = None
    client_id: Optional[str] = None
    # 非專案欄位：狀態轉「未成案/成案」時給提案衛星列的原因（組織學習欄），
    # update_project 會 pop 掉不 setattr（提案=專案合體，2026-08-06）
    outcome_reason: Optional[str] = None


class ProjectExpensePayload(BaseModel):
    category: str
    estimated: int = 0
    actual: int = 0
    sub_item: str = ""
    payee: str = ""
    advance_id: str = ""
    notes: str = ""
    cost_group_id: Optional[str] = None
    # 消費日（YYYY-MM-DD，空＝不填）。專案成本認列用它，不用登記日。
    expense_date: str = ""


class PettyExpensePayload(BaseModel):
    """零用金：本人登記一筆墊付（docs/PETTY_CASH_PLAN.md）。

    ⚠️ 沒有 `staff_id` 欄位 —— 請款人一律從 token 解（own-scope 的定義）。
    `project_id` 可留白＝公司層級支出（行政/業務推廣），實測歷史 289/443 如此。
    """
    expense_date: str = ""          # YYYY-MM-DD，空 → 今天
    actual: int = 0                 # 實付金額（可為負＝退款/沖回）
    summary: str = ""               # 摘要（存 sub_item）
    item: str = ""                  # 會計項目 → finance_category_map
    category: str = "其他"           # 手機頁粗分類（交通/住宿/飲食…），與 item 不同軸
    project_id: Optional[str] = None
    invoice_no: str = ""
    notes: str = ""


class PettySubmitPayload(BaseModel):
    notes: str = ""


class PettyFromCashPayload(BaseModel):
    """收支明細 → 零用金單據（收支表上的「推送零用金請款」）。

    `preview=True` 只試算不寫 —— 前端先把「會建成什麼樣」給人看過再送出，
    因為推完之後那筆錢就進了公司的請款流程，退回要動到三張表。

    ⚠️ 沒有 `staff_id` —— 請款人是身分不是欄位（同 PettyExpensePayload）。
    代為推送走路徑參數 `/petty/staff/{staff_id}/from-cash`。
    """
    entry_id: str = ""              # 一次一列（owner 拍板「不是一次一批」）
    item: str = ""                  # 空 → 由類別對映推（對不出來後端會擋）
    project_id: Optional[str] = None
    notes: str = ""
    preview: bool = False


class ExpenseLinkPayload(BaseModel):
    """發一條雜支登記的分享連結（token）。

    `kind`：`project`（整個專案）或 `group`（單一拍攝日子表）。
    `rotate=True` ＝「重置連結」：舊連結當場失效。
    """
    kind: str
    target_id: str
    rotate: bool = False


class ProjectExpensePatchPayload(BaseModel):
    """供 inline edit 用的部分更新 payload — 所有欄位 optional。"""
    category: Optional[str] = None
    estimated: Optional[int] = None
    actual: Optional[int] = None
    sub_item: Optional[str] = None
    payee: Optional[str] = None
    advance_id: Optional[str] = None
    notes: Optional[str] = None
    cost_group_id: Optional[str] = None
    expense_date: Optional[str] = None   # YYYY-MM-DD；空字串＝清掉消費日


# 報價的四個 payload 已搬到 core/schemas_quotes.py（schemas.py 觸及 2000 行上限）；
# 這裡 re-export，`from core.schemas import QuotationPayload` 照舊可用。
from core.schemas_quotes import (  # noqa: F401,E402
    PriceItemPayload, PriceMatchItem, PriceMatchPayload, QuotationItemPayload,
    QuotationPayload, QuotationTemplatePayload, QuoteChatPayload,
)


class StaffPayload(BaseModel):
    # name 設為 Optional 以支援「部分更新」(PUT 只送變動欄位，如官網呈現 section 只送
    # show_on_website + website_*)。create_staff 端會明確檢查 name 必填，不靠 schema 強制。
    name: Optional[str] = None
    role: str = ""
    daily_rate: int = 0
    hourly_rate: int = 0
    phone: str = ""
    email: str = ""
    id_number: str = ""
    # 代稱（綽號／常打的寫法）。🔴 Optional 不是 str=""：舊分頁的 PUT 不帶這欄，
    # 空字串預設會把別人剛填的洗掉（CLAUDE.md「加新欄位一律 Optional」）。
    alias: Optional[str] = None
    address: str = ""
    bank_name: str = ""
    bank_account: str = ""
    portfolio_url: str = ""
    status: str = "在職"
    notes: str = ""
    # H1 員工檔案完整化 — 全 Optional 配合 exclude_unset 部分更新；
    # 日期收 'YYYY-MM-DD' 字串，端點層轉 datetime（staff.py _STAFF_DATE_FIELDS）。
    employment_type: Optional[str] = None     # 正職/兼職/約聘/freelance
    hire_date: Optional[str] = None
    leave_date: Optional[str] = None
    emergency_contact: Optional[str] = None
    # 官網呈現覆寫（與「官網管理 › 關於我們」團隊卡同步寫同一批 crm_staff 欄位）。
    # 全為 Optional/None → 配合 update_staff 的 model_dump(exclude_unset=True)，
    # 未送的欄位不會被寫入，避免任一側部分更新覆蓋另一側的覆寫值。
    show_on_website: Optional[bool] = None
    website_title: Optional[str] = None
    website_photo_url: Optional[str] = None
    website_bio: Optional[str] = None
    website_sort_order: Optional[int] = None


class StaffQuickAddPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    role: Optional[str] = ""


class ResumePayload(BaseModel):
    bio: str = ""
    skills: list = []
    education: list = []
    experience: list = []
    awards: list = []
    resume_visible: bool = False
    resume_editable: bool = True


class PortfolioPayload(BaseModel):
    title: str
    url: str
    role_desc: str = ""
    sort_order: int = 0


class ProjectStaffPayload(BaseModel):
    staff_id: str
    role_in_project: str = ""
    phase: str = ""
    days: int = 1
    rate_override: Optional[int] = None
    actual_days: Optional[int] = None
    actual_cost: Optional[int] = None
    payment_status: Optional[str] = None
    payment_date: Optional[str] = None
    notes: str = ""


class PaymentRequestPayload(BaseModel):
    request_date: Optional[str] = None
    amount: int = 0
    summary: str
    category: str = "專案外包"
    payee_name: str = ""
    payee_id: str = ""
    payee_type: str = ""
    needs_invoice: int = 0
    invoice_number: str = ""
    invoice_amount: Optional[int] = None
    # 代開請款單指向的那張發票。號碼可能是空的（代開還沒拿到號），id 才是可靠的鍵
    # —— 付款時要靠它把發票收尾到「已撥款」。
    source_invoice_id: Optional[str] = None
    # 從「委外人員名單」一鍵請款時帶的成本行 id（防重複請款）
    cost_line_id: Optional[str] = None
    expense_id: Optional[str] = None      # 行政雜支那一行（crm_project_expenses.id）
    project_id: Optional[str] = None
    project_label: str = ""
    payment_date: Optional[str] = None
    payment_status: str = "應付款"
    # 🔴 空值一定要收得下 None。前端的共用收值（crm-utils.enableInlineEdit）對
    # date/month 型別一律 `val = val || null` —— 那對 request_date/payment_date
    # 是對的（Optional），但「預計付款月」留空時送來的也是 null，而這欄宣告 str
    # → 整張單存不了，錯誤訊息只有一句 "Input should be a valid string"，
    # 完全看不出是哪一欄（owner 2026-08-24 就這樣被擋住，改不了記帳錯誤）。
    # 存的形狀維持空字串（全 repo 都用 `planned_month or ""` 比對），只在入口收斂。
    planned_month: str = ""
    _norm_planned_month = field_validator("planned_month", mode="before")(
        lambda v: "" if v is None else v)
    advance_by: str = ""
    is_advance: int = 0
    advance_returned: int = 0
    notes: str = ""
    # 兩本帳：None＝建立時落 'parent'、更新時維持既有值；🔴 不可給 "parent" 當預設——整包 model_dump 寫回會把我的帳列洗回母公司
    entity: Optional[str] = None


class ProjectLedgerMovePayload(BaseModel):
    """把專案搬到另一本帳（`entity`：'mine' 或 'parent'）。

    🔴 這是全 repo「更新一律不得換帳本」的**唯一例外**，所以走專屬端點而不是
    塞進 PUT /projects/{id} —— 換帳本要有自己的守衛（只有私帳 full scope 能按、
    身上掛了錢就擋）與自己的痕跡，混進一般更新裡遲早被當成普通欄位寫過去。
    """
    entity: str = ""
    # 搬到私帳時的案源（源日／代開發票／執行業務所得）；空＝後端推（身上有內部代開
    # 發票→代開發票，否則源日）。搬回母帳時忽略。
    source: Optional[str] = None


class ProjectMirrorPayload(BaseModel):
    """連結私帳：母公司專案 → 私帳的收入分身。

    `target_id` 空＝建新的私帳案；有值＝連結到既有那一案（他手動建過 109 案，
    名字跟母公司的不一定一樣，所以要留這條路）。

    `mode`＝要連結的那一案**已經填過工項**時怎麼辦（owner 2026-08-30
    「跳出幾個選擇讓我決定要怎麼做」）：
      overwrite  私帳的工項換成母公司成本行算出來的（建新案一律走這條）
      keep       只建立連結，私帳的金額一毛不動
      import     反過來：把私帳的工項寫成母公司的 CRM 成本行（掛給我），
                 私帳不動。匯入後那些成本行在 CRM 照常可編。
    """
    target_id: str = ""
    mode: str = "overwrite"
    # owner 2026-09-12「雖然是代開發票，但是專案公司也留一份帳」：分身的案源
    # （空＝源日）。代開發票→私帳那案的收入用**母帳合約額**（那張發票的面額就是
    # 他的錢，不是掛給他的成本行），代辦費由 apply_source_fee 自動算。
    source: Optional[str] = None


class CashTaxonomyNodePayload(BaseModel):
    """在收支分類樹上長一個節點（收支明細的「＋自訂…」）。

    `parent_id` 空＝第一層。**不收 depth**：深度是位置的性質不是欄位，由後端從
    父節點推 —— 讓前端送，遲早會送出一個 depth 跟 parent 對不上的節點。
    """
    parent_id: str = ""
    name: str = ""
    entity: Optional[str] = None


class CashTaxonomyNodeUpdate(BaseModel):
    """改分類樹的節點（後台編輯器）。**部分更新**：只送要改的欄。

    name / parent_id 是**資料遷移**不是改標籤 —— 第一、二層的名字就是 category
    複合鍵，改了要連 finance_category_map 的對映一起改，否則那批帳從三表消失。
    後端 update_cash_taxonomy_node 一手包辦，呼叫端不要自己補。
    """
    name: Optional[str] = None
    parent_id: Optional[str] = None
    active: Optional[int] = None
    entity: Optional[str] = None


class CashEntryPayload(BaseModel):
    """收支明細。**PUT 走 exclude_unset 部分更新** —— 所以每個欄位都可以不送，
    包括 summary（建立時由 create_cash_entry 明確驗必填）。整包寫回會把前端沒送的
    欄位洗成這裡的預設值，收支明細有 20 幾欄而編輯視窗只送 10 個。"""
    entry_date: Optional[str] = None
    expense: Optional[int] = None
    claim: Optional[int] = None
    deposit: Optional[int] = None
    summary: Optional[str] = None
    note: str = ""
    bank_memo: str = ""       # 銀行/卡單原始資訊（note＝使用者手寫附註）
    category: str = ""
    item: str = ""
    sub_item: str = ""
    payee: str = ""
    status: str = ""
    has_invoice: int = 0
    invoice_number: str = ""
    project_label: str = ""
    project_id: Optional[str] = None
    payment_date: Optional[str] = None
    payment_status: str = ""
    invoice_id: Optional[str] = None
    bank_fee: Optional[int] = None
    advance_payment_id: Optional[str] = None
    bank_account_id: Optional[str] = None       # 掛哪個銀行帳戶（財務階段二）
    payment_request_id: Optional[str] = None    # AP 硬連結 → crm_payment_requests
    # 分類樹的葉節點（cash_taxonomy_nodes.id）。送了就是**它說了算** ——
    # category/item/sub_item 由後端 _sync_taxonomy 從路徑推導，前端送的不算數。
    # 送空字串＝清掉分類（連三欄一起清）。沒送＝這次沒動分類。
    taxonomy_node_id: Optional[str] = None
    # 兩本帳：None＝建立時落 'parent'、更新時維持既有值；🔴 不可給 "parent" 當預設——整包 model_dump 寫回會把我的帳列洗回母公司
    entity: Optional[str] = None


class CostLinePayload(BaseModel):
    phase: str
    item_name: str
    sort_order: int = 0
    cost_group_id: Optional[str] = None
    estimated_unit_price: Optional[int] = None
    estimated_quantity: Optional[int] = None
    estimated_unit_type: Optional[str] = None
    estimated_amount: Optional[int] = None
    estimated_staff_id: Optional[str] = None
    estimated_notes: str = ""
    actual_unit_price: Optional[int] = None
    actual_quantity: Optional[int] = None
    actual_unit_type: Optional[str] = None
    actual_amount: Optional[int] = None
    actual_staff_id: Optional[str] = None
    actual_notes: str = ""


class CostGroupCreate(BaseModel):
    name: str
    shoot_date: Optional[str] = None        # "YYYY-MM-DD"
    notes: Optional[str] = None
    sort_order: int = 0
    budget_amount: Optional[int] = None
    misc_budget_amount: Optional[int] = None
    profit_target_pct: Optional[int] = None
    receipt_path: Optional[str] = None


class CostGroupUpdate(BaseModel):
    name: Optional[str] = None
    shoot_date: Optional[str] = None
    notes: Optional[str] = None
    sort_order: Optional[int] = None
    budget_amount: Optional[int] = None
    misc_budget_amount: Optional[int] = None
    profit_target_pct: Optional[int] = None
    # 空字串視為清空（搭配 model_dump(exclude_none=True) 讓未送的欄位不動）
    receipt_path: Optional[str] = None


class CostGroupDuplicate(BaseModel):
    name: str
    shoot_date: Optional[str] = None


class CostLineUpdatePayload(BaseModel):
    item_name: Optional[str] = None
    sort_order: Optional[int] = None
    estimated_unit_price: Optional[int] = None
    estimated_quantity: Optional[int] = None
    estimated_unit_type: Optional[str] = None
    estimated_amount: Optional[int] = None
    estimated_staff_id: Optional[str] = None
    estimated_notes: Optional[str] = None
    actual_unit_price: Optional[int] = None
    actual_quantity: Optional[int] = None
    actual_unit_type: Optional[str] = None
    actual_amount: Optional[int] = None
    actual_staff_id: Optional[str] = None
    actual_notes: Optional[str] = None


class ShowcasePayload(BaseModel):
    description: str = ""
    video_url: str = ""
    credits: list = []
    process_mode: str = "gallery"
    slug: str = ""


class InvoicePayload(BaseModel):
    payment_type: str = "收款"
    payment_status: str = "未收款"
    issue_status: str = "已開立"
    invoice_number: str = ""
    invoice_date: Optional[str] = None
    title: str
    applicant: str = ""
    category: str = "專案"
    invoice_kind: str = "電子發票"
    amount_ex_tax: Optional[int] = None
    amount_total: Optional[int] = None
    tax_amount: Optional[int] = None
    commission: Optional[int] = None
    company_name: str = ""
    tax_id: str = ""
    item_type: str = ""

    # 🔴 統編補回前導 0：Excel/Sheets 把 00973926 存成數字 973926，匯出 CSV 就少了
    # 兩個 0。規則正本 core.crm_logic.normalize_tax_id（有單元測試）。
    _norm_tax_id = field_validator("tax_id")(lambda v: normalize_tax_id(v))
    project_id: Optional[str] = None
    project_ids: Optional[List[str]] = None     # 掛的案可複數（沒送＝保留既有；見 core.project_link.normalize_invoice_projects）
    recipient: str = ""
    recipient_phone: str = ""
    recipient_address: str = ""
    notes: str = ""
    # 兩本帳：None＝建立時落 'parent'、更新時維持既有值；🔴 不可給 "parent" 當預設——整包 model_dump 寫回會把我的帳列洗回母公司
    entity: Optional[str] = None


class TimesheetRow(BaseModel):
    """工時 Sheet 單列（Apps Script 上行；欄位對齊 Sheet：日期/員工/專案/內容/時數）。"""
    date: str = ""            # "2026/6/30" 等，後端容錯解析
    staff: str = ""
    project: str = ""
    task: str = ""
    hours: float = 0.0


class TimesheetPullSettings(BaseModel):
    """PUT /timesheets/pull —— 只送要改的欄；None＝不動。"""
    enabled: Optional[bool] = None
    sheet_id: Optional[str] = None
    cron: Optional[str] = None


class TimesheetDigestSettings(BaseModel):
    """PUT /timesheets/digest —— 週一 digest 開關與 cron。"""
    enabled: Optional[bool] = None
    cron: Optional[str] = None


class TimesheetBudgetSet(BaseModel):
    """PUT /timesheets/project_budget —— 專案檔案頁直接改預算小時（管理員）。"""
    project_id: str
    budget_hours: Optional[float] = None   # None／0＝清掉


class TimesheetIngestRequest(BaseModel):
    rows: List[TimesheetRow]
    # sheet＝Apps Script 每小時同步；import＝歷史一次性匯入（docs/TIMESHEET_IMPORT_PLAN.md D3）。
    # 兩者走**同一條**寫入（同 row_hash、同手填優先去重），只差落庫的標記。
    source: Literal["sheet", "import"] = "sheet"


class TimesheetProjectMapItem(BaseModel):
    """owner 對一個 Sheet 專案原字的決定：對到哪一案。"""
    sheet_name: str
    project_id: str
    note: str = ""


class TimesheetProjectMapRequest(BaseModel):
    items: List[TimesheetProjectMapItem]


class TimesheetBudgetItem(BaseModel):
    """Sheet「專案狀態」一列的預算（剩餘＋實際）→ 對到的案的 budget_hours。"""
    sheet_name: str
    budget_hours: float


class TimesheetBudgetRequest(BaseModel):
    items: List[TimesheetBudgetItem]


class MilestonePayload(BaseModel):
    """付款節點（B3）新增/更新。"""
    project_id: Optional[str] = None      # create 必填；update 不動
    label: str = ""
    amount: Optional[int] = None
    due_date: Optional[str] = None        # 'YYYY-MM-DD'
    status: Optional[str] = None          # 未到期/待請款/已請款/已收款
    invoice_id: Optional[str] = None
    sort_order: Optional[int] = None
    note: Optional[str] = None


class MonthClosePayload(BaseModel):
    """月結（F1）：鎖定/重開指定月份。"""
    month: str                            # 'YYYY-MM'


class LocationPayload(BaseModel):
    """場景庫（P-a）新增/更新 — 全欄 Optional 配合部分更新（create 時 name 由端點檢查必填）。"""
    name: Optional[str] = None
    category: Optional[str] = None        # 咖啡廳/工廠/辦公室/戶外/官署…
    region: Optional[str] = None          # 縣市
    address: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    permit_required: Optional[int] = None  # 0/1 需申請拍攝許可
    permit_note: Optional[str] = None
    fee_note: Optional[str] = None
    attributes: Optional[dict] = None     # 電源/收音/自然光/停車/廁所… 自由 dict
    tags: Optional[list] = None
    note: Optional[str] = None
    status: Optional[str] = None          # 可用/黑名單/已消失
    cover_url: Optional[str] = None


class LocationUsagePayload(BaseModel):
    """場景使用履歷（哪個專案用過＋評分＋踩雷心得）。"""
    project_id: Optional[str] = None
    used_date: Optional[str] = None       # 'YYYY-MM-DD'
    rating: Optional[int] = None          # 1-5
    lesson: Optional[str] = None


class ProposalPayload(BaseModel):
    """提案庫（P-b）新增/更新 — 全欄 Optional 配合部分更新（create 時 title 由端點檢查必填）。"""
    title: Optional[str] = None
    client_id: Optional[str] = None       # soft FK → clients.id
    project_id: Optional[str] = None      # soft FK → crm_projects.id（成案後回填）
    quotation_id: Optional[str] = None    # soft FK → crm_quotations.id
    ptype: Optional[str] = None           # 形象/廣告/紀錄片/政府標案/社群/其他
    status: Optional[str] = None          # 草稿/已提案/入圍/成案/未成案/擱置
    pitch_date: Optional[str] = None      # 'YYYY-MM-DD'
    budget_range: Optional[str] = None
    deck_url: Optional[str] = None
    outcome_reason: Optional[str] = None  # 轉成案/未成案時必填（端點檢查）
    tags: Optional[list] = None
    notes: Optional[str] = None           # 基本資料備註（自由文字）


class ReferencePayload(BaseModel):
    """參考片庫（跨提案共用）新增/更新（create 時 url 由端點檢查必填）。"""
    url: Optional[str] = None
    title: Optional[str] = None
    note: Optional[str] = None
    tags: Optional[list] = None
    thumb_url: Optional[str] = None


class ProposalPlanPayload(BaseModel):
    """企劃矩陣整份寫入（docs/PROPOSAL_PLANNER.md §6.3）— 僅用於
    「開始企劃 / 載入範例 / 清空」；日常輸入走逐格 PATCH。"""
    clear: bool = False                   # true = 清空整份 plan（其餘欄位忽略）
    template_id: Optional[str] = None
    template_version: Optional[str] = None
    theme: Optional[str] = ""
    cells: Optional[dict] = None          # {lens: {how: {answer, ...}}}
    directions: Optional[dict] = None     # {how: {answer, ...}}
    field_values: Optional[dict] = None   # {lens: {field: str}}


class ProposalPlanCellPatch(BaseModel):
    """企劃矩陣逐格寫入 — 共編下的日常輸入單位（樂觀鎖見端點）。"""
    kind: str                             # cell / direction / theme / memo / field
    lens: Optional[str] = None            # kind=cell/field 必填
    how: Optional[str] = None             # kind=cell/direction 必填
    field: Optional[str] = None           # kind=field 必填
    answer: str = ""
    base_updated_at: Optional[str] = None  # 該格載入時的時間戳；伺服器較新→409
    guest_name: Optional[str] = None       # 公開共編（token 路徑）的署名；authed 路徑忽略


class ReferencePatch(BaseModel):
    """參考影片庫 v2 逐欄/逐格寫入（docs/REFERENCE_LIBRARY.md）。
    kind=field → key+value；kind=facet → key(分類族)+value(list 或 studio 字串)；
    kind=research → row_id+col+value（帶 base_updated_at 做樂觀鎖）。"""
    kind: str
    key: Optional[str] = None
    col: Optional[str] = None             # kind=research：purpose/idea/material/moment
    row_id: Optional[str] = None          # kind=research 必填
    value: Optional[object] = None
    base_updated_at: Optional[str] = None
    guest_name: Optional[str] = None      # 公開共編署名；authed 路徑忽略


class ReferenceResearchRow(BaseModel):
    """公開路徑加研究列 —— 只需要署名（列 id 由伺服器產）。"""
    guest_name: Optional[str] = None


class ProposalPublicInfoPatch(BaseModel):
    """公開共編路徑（?t= 連結）改基本資料 — 逐欄寫入，欄名由端點白名單擋。
    刻意設計成「一次一欄」而非整包 payload：免登入路徑不給批次覆寫的能力。"""
    field: str                            # 僅放行 ptype / pitch_date / tags / notes
    value: Optional[object] = None        # str（ptype/notes/pitch_date）或 list[str]（tags）


class ProposalSurveyPatch(BaseModel):
    """現況盤點單格寫入（登入與公開共編同一形狀）。欄目合法性、公開可否編輯、
    長度上限全在 core.proposal_survey —— 這裡只收形狀。"""
    key: str
    field: str                            # content / note
    value: Optional[str] = None


class ProposalSurveyRowPayload(BaseModel):
    """新增自訂盤點欄目（只有登入路徑能加列）。"""
    label: str


class IntelSourcePayload(BaseModel):
    """產業情報來源（P-c）新增/更新 — 全欄 Optional 配合部分更新
    （create 時 url 開頭 http 由端點檢查）。"""
    name: Optional[str] = None
    type: Optional[str] = None            # rss / html（html 第一版先跳過不抓）
    url: Optional[str] = None
    keywords: Optional[list] = None       # list[str] 關鍵字；空 = 全收
    enabled: Optional[bool] = None
    note: Optional[str] = None


class PortalLinkPayload(BaseModel):
    """看片門戶（B1）送審連結建立/更新 — 全欄 Optional 配合部分更新
    （create 時 project_id/video_path 必填 + 檔案存在性由端點檢查）。"""
    project_id: Optional[str] = None
    version_label: Optional[str] = None   # 初剪/一修/定剪…
    video_path: Optional[str] = None      # master 本機影片路徑
    status: Optional[str] = None          # 待審/修改中/已核准
    expires_at: Optional[str] = None      # 'YYYY-MM-DD'；空字串 = 清除到期日


class PortalCommentPayload(BaseModel):
    """客戶端時間軸留言（公開、token 授權）— body 必填由端點檢查（上限 2000 字）。"""
    timecode_sec: float = 0
    body: str = ""
    author_name: Optional[str] = None


class PortalApprovePayload(BaseModel):
    """客戶端一鍵核准（公開、token 授權）— author_name 必填由端點檢查。"""
    author_name: Optional[str] = None


class EquipmentPayload(BaseModel):
    """器材庫（B4）新增/更新 — 全欄 Optional 配合部分更新（create 時 name 由端點檢查必填）。"""
    name: Optional[str] = None
    category: Optional[str] = None        # 機身/鏡頭/燈光/收音/週邊/其他
    serial: Optional[str] = None
    purchase_date: Optional[str] = None   # 'YYYY-MM-DD'；空字串 = 清除
    purchase_cost: Optional[int] = None
    depreciation_months: Optional[int] = None  # 直線攤提月數
    status: Optional[str] = None          # 在庫/出勤/維修/除役
    note: Optional[str] = None
    cover_url: Optional[str] = None
    # 帳本（兩本帳 §8）：建立時可指定（需該帳本的 full 權限），**更新時不可換**
    # —— 換帳本＝把一件器材的折舊整個搬到另一本損益表，那不是編輯是搬遷。
    entity: Optional[str] = None


class EquipmentCheckoutPayload(BaseModel):
    """器材領用 — person 必填由端點檢查；已有未歸還紀錄回 409。"""
    person: Optional[str] = None          # 領用人（自由輸入）
    project_id: Optional[str] = None      # soft FK → crm_projects.id
    due_at: Optional[str] = None          # 應還日 'YYYY-MM-DD'


class EquipmentReturnPayload(BaseModel):
    """器材歸還 — 只補狀況備註；無未歸還紀錄回 404。"""
    condition_note: Optional[str] = None


class EquipmentMaintenancePayload(BaseModel):
    """器材保養紀錄（date 必填由端點檢查）。"""
    date: Optional[str] = None            # 'YYYY-MM-DD'
    cost: Optional[int] = None
    note: Optional[str] = None


class ProjectTypeOpPayload(BaseModel):
    """CRM「案型清單」的一個動作：add／rename／remove，直接改私帳毛利表那份（案型的正本）。"""
    op: str = ""
    name: str = ""
    new_name: str = ""


class ShootCreate(BaseModel):
    """拍攝場次（行事曆）新增；日期 'YYYY-MM-DD'、時間 'HH:MM'。"""
    project_id: Optional[str] = None
    title: Optional[str] = None
    date: Optional[str] = None
    end_date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    location_id: Optional[str] = None
    location_text: Optional[str] = None
    crew: Optional[list] = None            # [{staff_id, name}]
    equipment_ids: Optional[List[str]] = None
    notes: Optional[str] = None
    cost_group_id: Optional[str] = None


class ShootUpdate(ShootCreate):
    """部分更新：只動有送的欄位（看 model_fields_set）；equipment_ids 有送才做器材差異。"""


class ShootStatusPayload(BaseModel):
    status: Optional[str] = None           # core.shoot_logic.SHOOT_STATUSES


class ShootEquipmentPayload(BaseModel):
    equipment_ids: Optional[List[str]] = None   # 空＝整場


class CalendarConfigPayload(BaseModel):
    calendar_id: Optional[str] = None


class FootageScanRequest(BaseModel):
    """B5 素材庫：掃描資料夾建索引。"""
    root_path: str
    project_id: Optional[str] = None
    project_name: Optional[str] = None


class FootageTagsPayload(BaseModel):
    """B5 素材庫：更新素材 tags。"""
    tags: List[str] = []


