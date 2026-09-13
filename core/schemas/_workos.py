"""core/schemas/_workos.py — 書籤／週記／工作階段／影像紀錄分塊上傳／福委會。

拆自 core/schemas.py（2026-09-11，2,000 行剛好卡在單次讀取上限）。
界畫在原本的分節註解上、零 class 搬家；對外仍是 `from core.schemas import X`。
"""
from pydantic import BaseModel, Field, model_validator  # type: ignore
from typing import List, Optional, Union


# ── 書籤（任務路徑預設；2026-07-21 復活 — 前端存 UNC 路徑組，全機隊共用）──

class BookmarkCreateRequest(BaseModel):
    name: str
    task_type: str = "backup"
    request: dict = {}


class BookmarkUpdateRequest(BaseModel):
    name: Optional[str] = None
    request: Optional[dict] = None


# ── 每週工作日誌（journal）──

class JournalPut(BaseModel):
    """PUT /api/v1/journal/mine — 四區塊全量替換（strip/去空/上限在 core.journal_logic）。

    每項可為字串或 {id?, content, project_id?, flag?}（§13：掛案子、求助標記；帶 id 沿用同一條）。
    `status` 只接受 'draft'（草稿自動存）；送出走 POST /journal/mine/submit。
    🔴 四區的欄位名＝routers.api_journal._SECTION_MODELS 的 key（測試釘死）；status 不是區。
    """
    wins: List[Union[str, dict]] = []
    challenges: List[Union[str, dict]] = []
    learnings: List[Union[str, dict]] = []
    others: List[Union[str, dict]] = []       # 其他主題（2026-07-24 第四問）
    status: Optional[str] = None


class JournalReactPost(BaseModel):
    """POST /api/v1/journal/react — 對某一條按心情（再按同一種＝取消）。kind：like／love／laugh。"""
    journal_id: str
    entry_table: str
    entry_id: str
    kind: str


class JournalReplyPost(BaseModel):
    """POST /api/v1/journal/reply — 主管回覆某一條（entry_table＝四張條目表之一的 __tablename__）。"""
    journal_id: str
    entry_table: str
    entry_id: str
    content: str = ""


# ── 工作階段（work_stage_nodes；docs/JOURNAL_WORKLOG_PLAN.md §12）──

class WorkStageNodePayload(BaseModel):
    """新增一個階段：parent_id＝分類節點（必填，階段只能掛在分類底下）。"""
    parent_id: str = ""
    name: str = ""


class WorkStageNodeUpdate(BaseModel):
    """改名／排序／停用（部分更新，只送要改的欄）。"""
    name: Optional[str] = None
    sort: Optional[int] = None
    active: Optional[int] = None


# ── 影像紀錄分塊上傳（繞開 Cloudflare 的 100MB 單請求上限）──

class ChunkBeginRequest(BaseModel):
    """POST /public/media-log/{token}/upload/begin

    filename/size/mtime 三個一組是**檔案的身分**：upload_id 由它們推導，
    所以同一個檔重傳一定落回同一個半成品（續傳）。client_key 是瀏覽器自己
    存在 localStorage 的隨機字串 —— 沒有它，兩個人同時傳同名同大小的檔會
    共用同一個 .part。
    """
    filename: str
    size: int = Field(gt=0, description="檔案總位元組數")
    mtime: int = 0               # File.lastModified（毫秒）；拿不到就 0
    client_key: str = ""


class ChunkFinishRequest(BaseModel):
    """POST /public/media-log/{token}/upload/{upload_id}/finish"""
    filename: str
    size: int = Field(gt=0, description="期望的總長度；與實收不符 → 409")
    category: str = ""
    uploader_name: str = ""


class CashInvoiceLink(BaseModel):
    """一筆收款分配到一張發票的金額（合併匯款 / 分期收款）。

    `amount` ＝ **這張發票被認列收到多少**（含被匯費吃掉的部分）—— 它直接寫進
    crm_cash_invoice_links.amount，發票的「已收／尚欠」讀的就是它。
    `fee` ＝ 其中被匯出行扣走、沒有真的進到我們帳戶的那幾十塊。
    所以真正入帳的現金 = amount − fee，而 Σfee 會寫進 crm_cash_entries.bank_fee
    並把 deposit 補回去（見 core.finance_logic.recognize_receipt_fee —— 淨流入不變）。
    """
    invoice_id: str
    amount: int
    fee: int = 0


class CashPaymentLinkItem(BaseModel):
    """對映 CashInvoiceLink（收款側的雙生子）—— 兩者要一起改。

    沒有 `fee`：付款側的匯費是**整筆匯出**收一次（跨行手續費），不是逐張請款單
    被扣，所以它掛在 payload 層（CashPaymentLinksPayload.fee /
    StatementImportRow.payment_fee）而不是這裡。
    """
    payment_request_id: str
    amount: int


class StatementImportRow(BaseModel):
    """對帳單匯入 apply 的一列 —— 前端把 preview 回來的列原樣送回（可改分類/
    可取消勾選）。金額帶號：正=存入、負=支出。"""
    date: str
    amount: int
    description: Optional[str] = ""
    category: Optional[str] = ""
    # 私帳的分類是一棵樹（cash_taxonomy_nodes），`category` 只是路徑前兩層的
    # 鏡射。挑了節點就送它，寫入端用 `_sync_taxonomy` 推三欄 —— 只送 category
    # 的話那一列沒有節點，收支明細的分類篩選與路徑顯示就看不到它。
    taxonomy_node_id: Optional[str] = None
    # 這一列的備註。預覽時由命中的規則帶出來（BankImportRule.apply_note），
    # 使用者可以改。空＝寫入時填制式的「銀行對帳單匯入」。
    note: Optional[str] = ""
    # 匯進去之後直接送「源日請款」（＝推成母公司零用金的草稿單據）。
    # owner 2026-08-30：功能本來就有（收支明細每列的選單），他要的是匯入當下
    # 就能勾，不必匯完再回收支表走一遍。只對**私帳的流出列**有意義。
    petty_claim: bool = False
    # 源日請款的會計項目。空＝由 `petty_item_for` 從類別推（推不出來後端會擋，
    # 理由回到匯入結果裡）—— 前端在勾選當下就會請人挑，所以正常會帶值。
    petty_item: str = ""
    # 指定成貸款繳款時要帶：配到哪筆貸款的哪一期（preview 已配好或使用者手選）
    loan_id: Optional[str] = None
    period_no: Optional[int] = None
    # 預覽時當場掛的專案與發票（owner 2026-08-20）。
    # 🔴 專案只有專案類的 category 收得下（core/project_link.CASH_CATEGORIES），
    #    發票只有收入列有意義 —— 兩者都由寫入端再驗一次，不信前端。
    project_id: Optional[str] = None
    # 一列可以掛多張發票（合併匯款：客戶一次匯三張的錢）。分配表本來就是多對多。
    # 只有這一種表示法 —— 「主要發票」是從分配表推出來的（金額最大那張），
    # 推導規則只放在 replace_invoice_allocs，前端不留第二份。
    invoices: List[CashInvoiceLink] = []
    # 支出列的鏡像：一筆匯出常常是**一個人的好幾張請款單併著發**（出納統一匯款）。
    # 🔴 只有支出列有意義，由寫入端再驗一次（同 invoices 只認收入列）。
    payments: List[CashPaymentLinkItem] = []
    # 🔴 匯費在兩側的形狀**刻意不同**，不是漏寫：
    #   · 收款側逐張（CashInvoiceLink.fee）—— 匯出行是對每張發票的匯款各扣一次
    #   · 付款側整列一個 —— 跨行手續費是對「這一筆匯出」收的，涵蓋幾張請款單
    #     都一樣。所以它跟 CashPaymentLinksPayload.fee 同形狀，不另立第二種表示法。
    # None ＝ 不認列（不動 bank_fee）。
    payment_fee: Optional[int] = None
    # 拆項（帳目一筆、內容拆裂 —— owner 2026-08-31）：一筆母公司匯款裝著
    # 專案款＋代墊回款＋薪資，匯入當下就拆。Σ 必須等於 |amount|，寫入端
    # （routers/crm/cash_splits._apply_splits）強制；有拆項時本列的
    # category/taxonomy/project_id 一律忽略（分類的正本在拆項）。
    splits: List["CashSplitItem"] = []

    @model_validator(mode="after")
    def _splits_are_exclusive(self):
        """🔴 拆項與本列的其他記帳意圖互斥 —— 規則放 schema 這一層，兩條路
        （匯入／存草稿）與前端的「讓位清單」才不會各自漂。

        寫入端對拆項列會跳過發票/請款/匯費/源日請款的處理；不擋的話，使用者
        在預覽勾好的東西會**靜默消失**（正好是會特地去拆的那種複雜列）。"""
        if self.splits:
            kept = [n for n, v in (("invoices", self.invoices),
                                   ("payments", self.payments),
                                   ("payment_fee", self.payment_fee),
                                   ("petty_claim", self.petty_claim)) if v]
            if kept:
                raise ValueError(
                    f"這一列已拆項，不能同時帶 {'、'.join(kept)} —— "
                    "拆項列的發票/請款/匯費/源日請款要先取消（拆項才是內容的正本）")
        return self


class CashSplitAdvanceLinkItem(BaseModel):
    """拆項對一列代墊流出的沖銷（逐筆結清）。"""
    entry_id: str
    amount: int


class CashSplitItem(BaseModel):
    """一個拆項。分類：私帳送 taxonomy_node_id（樹節點是正本）；
    母公司可只送 category/sub_item（平面科目）。
    fee＝發票代開費（源日代開發票、扣完費用才匯）：amount 是實匯淨額、
    fee 外加，專案已收按毛額（amount+fee）結清 —— 只在收入側掛專案的
    拆項有效（寫入端擋其他組合）。"""
    amount: int
    fee: int = 0
    taxonomy_node_id: str = ""
    category: str = ""
    sub_item: str = ""
    project_id: str = ""
    note: str = ""
    advances: List[CashSplitAdvanceLinkItem] = []


class CashSplitsPayload(BaseModel):
    """整組取代一列的拆項；空 list ＝ 解除拆項。"""
    splits: List[CashSplitItem] = []


class StatementDraftRow(StatementImportRow):
    """草稿存的一列 —— 比 apply 多一個「勾了沒」。

    apply 只收勾選的列（沒勾的根本不會送上來），草稿要**全部**列都存，
    連同「這列我不打算匯」的決定一起 —— 不然下次開啟又全部變回預設勾選。
    """
    selected: bool = False


class StatementDraftPayload(BaseModel):
    """存草稿。id 有值＝更新既有草稿（同一份對帳單改到一半再存一次）。

    source_text 是**原始對帳單文字**：開啟時重新解析，才拿得到最新的重複判定
    與最新的專案／發票清單（見 db.models.BankImportDraft 的說明）。
    """
    id: Optional[str] = None
    bank_account_id: str
    source_text: str
    rows: List[StatementDraftRow] = []
    # 沒有 name：名字由後端從帳戶＋日期區間＋列數自動組（_auto_draft_name）。
    # 沒有改名 UI，而且重存會依當下的列重新命名 —— 真要做改名該是另一支端點。


class StatementImportApply(BaseModel):
    """確認後寫入。rows 只放使用者勾選要匯的列 —— 沒勾的不會出現在這裡。"""
    bank_account_id: str
    rows: List[StatementImportRow] = []


class CardImportRow(BaseModel):
    """信用卡帳單匯入的一列（api_finance_card）。amount 帶號：正=消費、負=退款。"""
    date: str
    amount: int
    note: str = ""
    category: Optional[str] = None
    # 🔴 沒有預設值：舊版前端（只送勾選的列）如果因為快取活著，帶預設 True 會
    # 讓後端對著被裁過的清單重跑重複判定 —— 也就是無聲退回修掉的那個 bug。
    # 必填的話它會 422 大聲壞掉，那是對的失敗方式。
    selected: bool


class CardImportApply(BaseModel):
    """卡單確認後寫入。不掛銀行帳戶（刷卡當下不動銀行 — status='card'）。

    🔴 rows 是**整份卡單**（沒勾的列也要送，用 selected=false 標）。只送勾選
    的列會讓 apply 對著被裁過的清單再扣一次「帳上已有」的筆數 —— 使用者刻意
    勾的列就這樣無聲消失（/simplify 第 4 輪）。整份送進來，apply 才看得到
    preview 看到的那份輸入，兩邊的重複判定才會是同一個答案。
    """
    rows: List[CardImportRow] = []
    # 哪一張卡（owner 2026-08-27「信用卡匯入時也可以區隔哪一個銀行的信用卡」）：
    # bank_accounts 裡 acct_kind='card' 的那筆 id；空＝未指定卡別（沿用舊行為）。
    # 🔴 刷卡列仍**不掛銀行帳戶**（status='card'，刷卡當下不動銀行）——
    # 這個欄位存在 bank_account_id 上是「卡別身分」，不是現金帳戶。
    card_account_id: Optional[str] = None


class CardAiSuggestRow(BaseModel):
    note: str = ""
    amount: int = 0


class CardAiSuggestPayload(BaseModel):
    """規則/歷史都沒答案的列 → claude 整批建議分類。"""
    rows: List[CardAiSuggestRow] = []


class CardLedgerConfig(BaseModel):
    """卡片餘額設定（api_finance_card）。opening 與 derive_opening_from 二選一。"""
    opening: Optional[int] = None
    derive_opening_from: Optional[int] = None   # 給「現在實際欠多少」，反推期初
    repay_categories: Optional[list] = None


class HoldingPayload(BaseModel):
    """資產儀表板 — 持股（api_finance_assets）。quote_symbol 空＝manual。"""
    broker: Optional[str] = None
    symbol: Optional[str] = None
    name: str = ""
    shares: Optional[float] = None
    currency: str = "TWD"
    quote_symbol: Optional[str] = ""
    last_price: Optional[float] = None
    manual_value: Optional[int] = None
    cost_total: Optional[float] = None    # 投資成本（該列的幣別；碎股有小數）
    sort_order: int = 0
    active: bool = True
    note: Optional[str] = None


class LedgerProjectCreate(BaseModel):
    """執行專案 — 新增（api_finance_projects）。owner 的流程是先整理專案再記帳，
    所以入口在帳本頁；工項/費用建立後在詳情編。"""
    name: str
    client_id: Optional[str] = None
    code: str = ""                  # 案碼（owner 自己的案件編號；可空）
    close_date: str = ""            # 'YYYY-MM-DD'；空＝未結案
    contract_amount: Optional[int] = None
    source: str = ""                # 案源：自接/源日(現金收款)/代開發票(自動代辦費)
    fee_pct: Optional[float] = None  # 服務費率 %（代開發票用；預設 8）


class LedgerDetailPayload(BaseModel):
    # crm_pushed：推送/取消推送到專案管理（0/1）。不是損益欄位 —— PUT 端點會先
    # pop 掉，**不能**落進 ledger_detail JSON（那個迴圈把剩餘鍵全當費用欄寫）。
    crm_pushed: Optional[int] = None
    source: Optional[str] = None     # 案源（meta，經 norm_detail 白名單）
    fee_pct: Optional[float] = None  # 服務費率 %（代開發票；預設 8）
    fee_deducted: Optional[bool] = None  # 代辦費已扣除（meta，缺＝True；只在 False 落庫）
    # 顯示名覆寫（owner 2026-09-05）。空字串＝清掉退回自動規則。同樣要在
    # PUT 端點先 pop 掉，不能落進 ledger_detail JSON。
    display_name: Optional[str] = None
    """逐案損益的可編輯欄（api_finance_projects）。

    費用欄用 Optional：只送有改的欄，None＝維持原值（整包 model_dump 寫回
    會把沒送的欄洗成 0 —— 這個 repo 咬過那個坑）。split 送就是整份取代
    （工項是一組值，逐項 patch 沒有語意）。
    """
    outsource: Optional[int] = None
    tax_fee: Optional[int] = None
    buy_invoice: Optional[int] = None
    invoice_fee: Optional[int] = None
    personal_tax: Optional[int] = None
    misc: Optional[int] = None
    shareholder: Optional[int] = None
    split: Optional[dict] = None
    contract_amount: Optional[int] = None    # 營收(含稅)，直接落 crm_projects
    close_date: Optional[str] = None         # 結案日 'YYYY-MM-DD'；''＝清空


# 註：本組 payload 刻意**沒有** entity —— 帳本一律由 query 的 entity 經
# `_guard` 決定，payload 說了不算（不然等於讓請求體自己挑要寫進哪本帳）。
class NetSnapshotPayload(BaseModel):
    """淨值快照。total 由後端加總（前端算的不收）。"""
    snap_date: str = ""
    buckets: dict = {}
    note: Optional[str] = None
    entity: Optional[str] = None


class BankImportRulePayload(BaseModel):
    # 命中後要掛的分類樹節點（私帳可指定到任何一層；空＝只用 category）
    taxonomy_node_id: Optional[str] = None
    """對帳單摘要 → 收支類別 的分類規則（使用者可編）。

    🔴 direction 不在這裡 —— 它只在「第一列推不出方向」的邊緣情況用得到，
    讓人去理解 +1/-1/0 不划算。使用者新增的規則一律 0（＝看不出方向，
    解析器會標記該列要人確認）。種子那 14 條的方向值保留在 DB 裡。
    """
    # 🔴 新增時兩個都必填，但**必填是端點在驗的**（`_apply_rule_payload` 的
    # require_all）不是 schema —— PUT 是部分更新，只送 {"active": false} 這種
    # 請求不可以被 schema 擋在門外。schema 這裡設成選填，新增那條路照樣 422。
    keyword: str = ""
    category: str = ""
    bank_account_id: Optional[str] = None    # 空 = 套用到所有帳戶
    sort_order: int = 100
    # 只在這個方向的列上套用：-1 只支出 / +1 只存入 / 0 不限。
    # 跟 direction（推方向的提示）是兩件事 —— 見 BankImportRule 的註解。
    only_direction: int = 0
    active: bool = True
    note: str = ""              # 規則自己的備忘（給人看的）
    # 命中後要寫進那一列收支的備註（owner 2026-09-01「也可以記憶備註」）。
    # 跟上面的 `note` 是兩件事 —— 見 BankImportRule 的註解。
    apply_note: str = ""


class CashInvoiceLinksPayload(BaseModel):
    """整組取代某筆收款的發票分配 —— 送空 items 就是全部解除。"""
    items: List[CashInvoiceLink] = []


class CashPaymentLinksPayload(BaseModel):
    """一筆匯款掛哪幾張請款單（整組取代）—— 送空 items 就是全部解除。

    `fee` 由呼叫端決定要不要把差額認成匯費：alloc_verdict 的 payment 側判為
    fee 時前端會把它一起送回來，寫進 crm_cash_entries.bank_fee（見
    recognize_bank_fee —— 那條路把匯費算成管理費用與現金流出）。
    None ＝ 不動既有的 bank_fee。
    """
    items: List[CashPaymentLinkItem] = []
    fee: Optional[int] = None


# ── 福委會（docs/BENEFIT_POOL_PLAN.md）───────────────────────────────

class BenefitPoolPayload(BaseModel):
    """福利池（快樂／進修）。

    `entity` 預設 **None** 不是 'parent' —— 給實體值當預設的話，舊前端整包
    model_dump 寫回會把另一本帳的列洗過去（docs/LEDGER_ENTITY_PLAN.md §2.3
    的教訓，兩本帳的 payload 一律這樣）。
    """
    name: str = ""
    status: str = "open"            # open/closed
    sort_order: int = 0
    notes: str = ""
    entity: Optional[str] = None
    # 年度活動（docs/BENEFIT_POOL_PLAN.md §9）
    quota: str = "shared"           # shared（共用桶）/ per_person（每人一份）
    description: str = ""           # 說明（給員工看的，健檢方案就是這欄）
    valid_from: str = ""            # YYYY-MM-DD，空＝不限
    valid_to: str = ""
    # 🔴 attachments **刻意不在 payload 裡**：附件只能走上傳／刪除那兩支端點。
    #    放進來的話，任何一個沒帶這欄的舊表單整包寫回就會把附件清空
    #    （「整包 model_dump 寫回會被預設值洗掉」的老坑）。


class BenefitAllowancePayload(BaseModel):
    """發給某個人的額度。`staff_id` 由管理端指定（這不是 own-scope 端點）。"""
    staff_id: str = ""
    amount: int = 0
    valid_from: str = ""            # 空＝繼承池的期間
    valid_to: str = ""
    notes: str = ""


class BenefitFundingPayload(BaseModel):
    """撥款：每年公司放進池裡的那筆錢。"""
    year: int = 0                   # 0 → 今年
    amount: int = 0
    fund_date: str = ""             # YYYY-MM-DD，空 → 今天
    notes: str = ""


class BenefitEntryPayload(BaseModel):
    """員工登記的一筆花費。

    `staff_id` 只有**管理端代登**那條路會用；本人自助的端點忽略這欄、
    一律從 token 解（own-scope 的定義，同零用金 PettyExpensePayload）。
    項目是自由文字 —— 分類這件事由池承擔（快樂／進修），不再多一層枚舉。
    """
    pool_id: str = ""
    staff_id: str = ""
    title: str = ""                 # 電影名／餐廳／課程名
    amount: int = 0                 # 正數
    spend_date: str = ""            # YYYY-MM-DD，空 → 今天
    # 心得筆記（非必填）。與 notes 分開 —— notes 裝退回原因
    reflection: str = ""
    notes: str = ""


class TransferFeeRecognize(BaseModel):
    """把帳戶間轉存的配對差額認列成跨行手續費（總流出不變）。

    `entry_ids` 空 ＝ 全部可認列的都做。後端一律重算判準，不信這裡送來的金額
    （見 routers/api_finance.recognize_transfer_fees 的說明）。
    """
    entry_ids: List[str] = []


