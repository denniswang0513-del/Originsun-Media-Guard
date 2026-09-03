# -*- coding: utf-8 -*-
"""代開發票的收款 → 待請款自動化（owner 2026-08-20 拍板）。

業務流：公司幫人代開發票 → 客戶匯款進來、發票標「已收款」→ 自動出現一張
「應付款」請款單（金額 = 發票的代開應匯 commission = 面額 × 92%，8% 是稅與
手續費）→ 在應付帳款付掉 → 發票走到「已付款」，整條收尾。
歷史資料實測：92% 是主流（105 筆），發票庫 209 張有 commission。
"""
from tests.unit._srcscan import finance_src, code_only, func_body, repo_src  # noqa: E402

# 內容本身（不是路徑）：finance.py 2026-08-30 拆成四個檔，
# finance_src() 把它們串起來 —— 斷言釘的是規則，不是函式在哪個檔案。
SRC = finance_src()


def _body(fn):
    return code_only(func_body(SRC, fn))


def test_every_status_change_path_triggers_the_sync():
    """三條會把發票標成/退出「已收款」的路，都要掛 _sync_passthrough_request。

    漏一條的後果：從那條路收款的代開發票不會進待請款區 —— 該匯給代開人的錢
    就靜靜躺著，直到對方來催。
    """
    for fn in ('async def update_invoice(',      # 手動改狀態（最常見的觸發點）
               'async def _resettle_invoice(',   # 收支連動（掛發票/分配面板/刪收支）
               'async def batch_receive('):      # 批次標已收款
        assert '_sync_passthrough_request' in _body(fn), \
            f'{fn} 沒有觸發代開待請款同步'


def test_pay_and_unpay_complete_the_invoice_lifecycle_symmetrically():
    """付掉 → 發票進「已撥款」；取消付款 → 退回「待撥款」。

    只做一半會留下「請款單應付款、發票卻已撥款」的矛盾 —— 待請款區有一張，
    發票卻說錢已經匯了。

    2026-08-24：這條規則本來 inline 寫在這兩支裡，而**改付款狀態的路徑不只
    這兩條** —— resettle_payment_requests（依帳上實付重算）也會標已付款，卻沒有
    收尾那一段。生產上因此留下一張「請款單已付款、發票卻停在待撥款」的票
    （ZK19927752，owner 指出來的那張）。規則已收進
    routers.crm.finance.sync_remit_status（兩個方向都在裡面），行為本身由
    tests/unit/test_remit_status_sync.py 直接驗；這裡只確認這兩支仍然呼叫它 ——
    漏掉任何一支，就是上面那種矛盾的來源。
    """
    for fn in ('async def batch_pay(', 'async def batch_unpay('):
        assert 'sync_remit_status(session,' in _body(fn),             f'{fn} 沒有收尾發票的撥款狀態'

def test_sync_guards_are_all_present():
    """核心函式的三道防線：

    1. 只認代開（category 含「代開」）—— 一般發票收款不該生請款單
    2. 已存在的就不建 —— 歷史匯入的 183 筆與重複觸發靠這條擋
    3. 反向只刪「自動產生且還是應付款」的 —— 人手建的與已付款的絕不動
    """
    body = _body('async def _sync_passthrough_request(')
    assert 'is_passthrough_category(inv.category)' in body
    assert 'if existing:' in body and 'return' in body
    assert '_AUTO_KAI_NOTE in (p.notes or "")' in body
    assert '"應付款"' in body


def test_idempotency_key_is_the_invoice_id_not_its_number():
    """🔴 owner 2026-08-21：無號的內部代開發票掛上收款後不會進請款單。

    原因是冪等鍵拿 invoice_number 當主鍵，沒號碼就直接 return —— 該匯給代開人
    的錢靜靜躺著，畫面上也看不出少了什麼。發票 id 一定有、一定唯一。
    """
    body = _body('async def _sync_passthrough_request(')
    assert 'source_invoice_id=inv.id' in body, '建出來的請款單沒有寫回冪等鍵'
    assert 'if not no:' not in body, '還在「沒號碼就不建」—— 無號代開會被靜默跳過'
    # 找既有那張的鍵在 _passthrough_requests_for（批次預載）——
    # 逐張查會用 replace() 掃全部請款單，重算 25 張就掃 25 遍
    lookup = _body('async def _passthrough_requests_for(')
    assert 'CrmPaymentRequest.source_invoice_id.in_(ids)' in lookup
    # 舊資料退路：183 張歷史自動單建立時還沒有這個欄位，不比號碼會重建一張
    assert 'if nos:' in lookup
    assert 'invoice_number' in lookup

    from db.models import CrmPaymentRequest
    assert 'source_invoice_id' in CrmPaymentRequest.__table__.columns

    helper = _body('async def _kai_invoice_of(')
    assert 'p.source_invoice_id' in helper and 'invoice_number' in helper,         '付款/取消付款那條路沒有跟著改鍵 —— 無號代開會卡在應付款'


def test_resettle_never_downgrades_a_remitted_passthrough_invoice():
    """收款重算不可以把代開發票從「已轉撥」降回「已收款」。

    已轉撥 = 錢已經匯給代開人，是比已收款更後面的階段 —— 有人動到那筆收款
    （改金額、重掛發票）觸發重算時，匯出去的事實不能被蓋掉。
    """
    # 代開三段：未收款 → 待撥款（錢進來、還沒轉給代開人）→ 已撥款
    # （owner 2026-08-21 定名；先前叫「已轉撥」，再先前叫「已付款」）
    from routers.crm.finance import INVOICE_PENDING_REMIT, INVOICE_REMITTED
    assert (INVOICE_PENDING_REMIT, INVOICE_REMITTED) == ('待撥款', '已撥款')
    body = _body('async def _resettle_invoice(')
    assert ('INVOICE_REMITTED' in body
            and 'is_passthrough_category(inv.category)' in body), \
        '_resettle_invoice 會把已匯出的代開發票降回已收款'


def test_commission_rate_has_one_home():
    """代開應匯 = 面額 × (1 − 費率)。費率是公司政策，只能有一份。

    🔴 本來一份在**每個使用者的 localStorage**（內部 8%／外部 10%）、一份寫死在
    後端（0.92）。後果：兩台電腦設不同費率就算出不同的應匯金額；外部代開畫面
    說 10%、實際產出的應付款卻是 8%（少匯給對方 2%）；而從手機登記頁、CSV 匯入、
    對帳單匯入建的發票根本不會跑那段 JS。
    """
    from core.finance_logic import (PASSTHROUGH_FEE_RATES,
                                    passthrough_commission)
    assert PASSTHROUGH_FEE_RATES == {"內部代開": 8.0, "外部代開": 10.0}
    # 內部 8%：100,000 → 92,000（跟歷史資料 105 筆反推的主流值一致）
    assert passthrough_commission(100000, "內部代開") == 92000
    assert passthrough_commission(43050, "內部代開") == 39606
    # 外部 10%：這條以前是錯的（也走 0.92）
    assert passthrough_commission(100000, "外部代開") == 90000
    # 非代開不算；後台改過費率就用改過的
    assert passthrough_commission(100000, "專案") == 0
    assert passthrough_commission(100000, "內部代開", {"內部代開": 5}) == 95000

    body = _body('async def _sync_passthrough_request(')
    assert 'passthrough_commission(' in body
    assert '0.92' not in body, '又把費率寫死回去了'
    js = repo_src('frontend/tabs/crm/crm-invoices.js')
    assert 'inv_commission_fees' not in js, '費率又跑回 localStorage 了'


def test_the_invoice_picker_is_not_filtered_by_payment_type():
    """🔴 owner 2026-08-21：請款單詳情的「代開發票」選單找不到要的發票。

    選單原本抓 /invoices?payment_type=收款，但資料裡的 payment_type 是「款項狀態」
    那欄拆出來的方向 —— 代開發票一旦收了錢／撥出去就變成「付款」，於是這個
    專門挑代開發票的選單反而看不到 183 張代開發票（生產實測）。
    """
    js = repo_src('frontend/tabs/crm/crm-payments.js')
    i = js.index('async function _loadInvoiceList(')
    body = js[i:i + 400]
    assert 'payment_type' not in body, '選單又被 payment_type 過濾了 —— 代開發票會消失'


def test_picking_an_invoice_does_not_overwrite_the_project():
    """挑到的發票要寫 source_invoice_id，不是 project_id。

    舊寫法 `project_id = _invoice_sel` 把發票 id 塞進專案欄：專案指向一個不存在
    的專案（畫面空白），而且把使用者在「專案」下拉挑的值整個丟掉。
    """
    js = repo_src('frontend/tabs/crm/crm-payments.js')
    i = js.index("if (payload.category === '發票代開')")
    body = js[i:i + 700]
    assert 'payload.source_invoice_id = payload._invoice_sel' in body
    assert 'payload.project_id = payload._invoice_sel' not in body, \
        '又把發票 id 寫進 project_id 了'
    from core.schemas import PaymentRequestPayload
    assert 'source_invoice_id' in PaymentRequestPayload.model_fields


def test_update_payment_does_not_wipe_the_invoice_key():
    """整包 model_dump + 欄位有預設值 + 前端不送 = 該欄被清空（repo 的老坑）。

    這個欄位被清空特別安靜：請款單付掉時找不到要收尾的發票，代開發票會永遠
    停在待撥款。

    2026-08-24：原本是替 source_invoice_id 單獨開一道 `data.pop(...)` 守衛。
    但同一個坑不只咬這一欄 —— 編輯面板只送 13 個欄位，needs_invoice /
    invoice_amount / project_label / advance_by / is_advance / advance_returned
    全都會被洗掉（量過生產：824 張裡各有 186／186／141 張中槍）。改成整支端點
    走 exclude_unset 部分更新（全 repo 20+ 個更新端點的既定慣例），這一欄就跟
    其他欄位一樣「沒送就不會被碰」，不需要專屬守衛。
    """
    body = _body('async def update_payment(')
    assert 'exclude_unset=True' in body,         'update_payment 又變回整包寫回 —— source_invoice_id 會被清空'
    assert 'req.model_dump(exclude=date_fields' not in body,         '還留著整包 model_dump 的舊路徑'


def test_passthrough_category_has_one_definition():
    """🔴 「這張是不是代開」本來有三種寫法：兩處 `"代開" in category` 子字串、
    一處集合比對、前端再一份。彼此不一致 —— 一個叫「代開」的類別會走過路狀態、
    生請款單，卻沒有科目對映、也不進業外收入。
    """
    from core.finance_logic import (INVOICE_PASSTHROUGH_CATEGORIES,
                                    is_passthrough_category)
    assert INVOICE_PASSTHROUGH_CATEGORIES == ("內部代開", "外部代開")
    assert is_passthrough_category("內部代開") and not is_passthrough_category("代開")

    src = SRC
    assert '"代開" in inv.category' not in src, '又出現子字串比對'
    # 科目對映的種子從同一組推，不另列一份
    seed = repo_src('db/seed_finance.py')
    assert 'INVOICE_PASSTHROUGH_CATEGORIES' in seed
    from db.seed_finance import SEED_CATEGORY_MAP
    kai = {r["category_text"] for r in SEED_CATEGORY_MAP
           if r["source"] == "invoice" and r["treatment"] == "passthrough"}
    assert kai == set(INVOICE_PASSTHROUGH_CATEGORIES)


def test_blank_payment_status_never_reaches_the_database():
    """🔴 空字串不會觸發欄位 default —— SQLAlchemy 的 default 只在「key 根本沒出現」
    時才跑。手機登記頁改成「不自己決定狀態」之後送的是 ''，於是每張從手機開的
    發票都存了一個空白狀態：沒有 badge，也不在 `payment_status NOT IN (...)`
    這類過濾的任何一邊（2026-08-21 實測，v2.4.119 上線 30 分鐘後抓到）。

    兩個寫入門都要在入口把它定案。
    """
    from core.finance_logic import initial_invoice_status
    assert initial_invoice_status("收款") == "已收款"
    assert initial_invoice_status("收款", unpaid=True) == "未收款"
    assert initial_invoice_status("付款") == "已撥款"
    assert initial_invoice_status("") == "已收款"      # 沒講方向 → 當收款

    for fn in ('async def create_invoice(', 'async def update_invoice('):
        body = _body(fn)
        assert 'initial_invoice_status(' in body, f'{fn} 沒有補上空白狀態'
        assert 'normalize_invoice_status(' in body, f'{fn} 沒有正規化舊字'

    # 手機登記頁（frontend/invoice.html）2026-09-03 起只剩轉址到 /m/crm.html#invoice；
    # 「手機端不寫死款項狀態」的規則改由 tests/unit/test_crm_mobile_frontend 守


def test_invoice_direction_is_never_guessed_in_the_browser():
    """🔴 方向（收款／付款）被前端硬塞成「收款」時，183 張代開付款發票會翻面。

    生產實測：payment_type='付款' 有 183 張、合計 10,656,093 —— 正是
    receivables_summary 註解裡「應收虛增成 4.7 倍」的那一批。列表的 inline 編輯
    早就修好了（原值帶回），但**編輯視窗**漏掉：打開任何一張代開發票按儲存就翻面。
    快速新增列那條也錯：它用 `狀態 === 已撥款 ? 付款 : 收款`，漏掉「待撥款」——
    那也是付款方向，於是待撥款的代開發票會被當成收款、跑進應收帳款。
    """
    from core.finance_logic import invoice_direction
    assert invoice_direction("待撥款") == "付款"      # 漏掉的就是這個
    assert invoice_direction("已撥款") == "付款"
    assert invoice_direction("已轉撥") == "付款"      # 改名前的舊字
    assert invoice_direction("未付款") == "付款"
    assert invoice_direction("已收款") == "收款"
    assert invoice_direction("未收款") == "收款"
    assert invoice_direction("作廢") == "作廢"

    js = repo_src('frontend/tabs/crm/crm-invoices.js')
    assert "payload.payment_type = '收款';" not in js, '編輯視窗又會把方向翻成收款'
    assert "payment_type: val('inv-qa-pay')" not in js, '快速新增列又自己判方向'
    assert '_editingPaymentType' in js, '編輯時沒有原值帶回'
    body = _body('async def create_invoice(')
    assert 'invoice_direction(' in body, '後端沒有從狀態推方向'
