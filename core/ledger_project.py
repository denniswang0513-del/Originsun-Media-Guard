# -*- coding: utf-8 -*-
"""core/ledger_project.py — 逐案損益的欄位定義與算式（純函式，無 I/O）。

owner 私帳「結案總表」那張表的規則正本。放 core 而不是留在 router，理由與
core/crm_logic.py 檔頭同一條：**錢流判定是帳務的 source of truth，endpoint 只
負責把 DB 加總餵進來**。實際代價當天就付了 —— scripts/backfill_my_ledger_detail
一開始複製了一份 `computed_net()`（router 沒法便宜地被腳本 import），於是
docstring 自稱正本的算式在第一天就有兩份（/simplify 2026-08-25 抓到）。

算式是 2026-08-25 對 402 案反推 Sheet 驗證出來的：
  實收 = 營收(含稅) − 委外 − 發票代辦費 − 個人稅款 − 雜支 − 股東往來  （395/402 吻合）
  檢查 = 實收 − Σ工項（應為 0）                                    （397/402 吻合）
不吻合的是 Sheet 自己帳不平的那幾案，照實算、用「檢查」欄顯示出來，不修不猜。

🔴 `invoice_fee` 是獨立欄不由 `tax_fee + buy_invoice` 推導 —— 402 案裡有 1 案
兩者不等（IGER DAY 講座側錄），推導會靜默改掉來源資料。
"""
from __future__ import annotations

import re

# 工項（收入拆分）預設清單 —— 對齊 owner 原 Sheet 的欄序。
# settings `my_ledger.income_items` 可覆寫（見 income_items()）。
DEFAULT_INCOME_ITEMS = ["前期製作", "動態攝影", "剪輯", "調光", "動態效果",
                        "平面攝影", "諮詢", "教學", "錄混音", "其他"]

# ledger_detail 的費用欄（鍵 → 中文標籤）。UI 照這個順序畫；匯入腳本用它的
# 反向對照（中文標籤 → 鍵）把 Sheet 欄位對進來 —— 清單只有這一份。
COST_FIELDS = [
    ("outsource", "委外費用"),
    ("tax_fee", "稅金"),
    ("buy_invoice", "買發票"),
    ("invoice_fee", "發票代辦費"),
    ("personal_tax", "個人稅款"),
    # owner 2026-08-28「私帳的專案裡增加一個項目行政雜支」—— 這一欄本來就在，
    # 只是 390 個私帳案一個都沒填過（Sheet 時代雜支沒進來）。改成他要的名字，
    # 值改由 CRM 專案帳目撐（見 CRM_BACKED）。
    # 🔴 舊標籤「雜支」是匯入腳本的 Sheet 欄名反查鍵 —— 別名補在
    # scripts/backfill_my_ledger_detail.py 的 COST_COLS。
    ("misc", "行政雜支"),
    ("shareholder", "股東往來"),
]

COST_KEYS = [k for k, _label in COST_FIELDS]

# ── CRM 專案帳目 → 逐案損益的兩個費用欄（owner 2026-08-28）─────────────
# 專案推送到私帳之後，它在 CRM 那邊的「專案帳目」還在記：
#   行政雜支（crm_project_expenses）      → `misc`
#   人員費用（crm_project_cost_lines 實際）→ `outsource`
#
# 🔴 **不複製、即時算**。owner 上一輪要的「兩本帳的編修甚至可以同步」用這個方式
# 達成 —— 資料只有一份（CRM 那張表），私帳這邊是它的視圖，所以永遠不會漂。
#
# 規則（owner 2026-08-28 拍板「甲」）：**CRM 合計 ＋ 手填那幾筆**，兩邊都算進去。
# 一開始是 B2「有 CRM 就用它、沒有才用手填」（＝覆蓋），但那讓「CRM 上沒有、
# 在私帳這邊手動加的委外」在有成本行的案子上憑空消失。改成相加之後：
#   · 落庫的 `ledger_detail[key]` 永遠只有**手填**那部分
#   · 讀取時才把 CRM 合計加上去（顯示值＝手填＋CRM）
# 歷史 390 個私帳案一個都沒有 CRM 成本行（2026-08-28 實測 mine=0 / parent=21），
# 相加之後它們的顯示值仍是原本的手填值，沒有任何一案被動到。
#
# 🔴 相加制的代價是**同一筆錢不能被算兩次**：私帳逐案損益的「委外人員一鍵請款」
# 建的請款單帶 `cost_line_id`（指向它是哪一行 CRM 成本行）—— 那張單**不可以**
# 再累加進 outsource，否則 CRM 成本行算一次、累加器再算一次。守衛在
# routers/crm/finance._sync_mine_project_outsource（單一出口）。
# 🔴 只放鍵，不放標籤 —— 標籤的正本是上面的 COST_FIELDS。這裡曾經帶過一份
# {"outsource": "人員費用"}，跟 COST_FIELDS 的「委外費用」當場打架而且沒人讀。
CRM_BACKED = ("misc", "outsource")


def apply_crm_costs(detail: dict, crm: dict | None) -> tuple:
    """把 CRM 算出來的合計併進 detail。回 `(detail, sources)`。

    `crm`：`{"misc": 合計, "outsource": 合計}`；0 或缺席＝CRM 那邊沒資料。
    `outsource` 有第二個寫入者：`routers/crm/finance._sync_mine_project_outsource`
    把掛在私帳專案上、類別＝專案外包的請款單累加進 detail —— 那正是「手填那幾筆」
    的來源，相加制之下兩者相安。唯一要擋的是帶 `cost_line_id` 的那種（一鍵請款
    ＝CRM 成本行的鏡射），守衛在那支函式裡。

    `sources`：`{欄位鍵: {"crm": 合計, "manual": 落庫的手填值}}` —— 前端據此
    拆開顯示（輸入框只放手填那部分，改它才不會把 CRM 的數字存成副本）。
    """
    out = dict(detail or {})
    sources = {}
    for key in CRM_BACKED:
        v = int((crm or {}).get(key) or 0)
        if v:
            manual = int(out.get(key) or 0)
            out[key] = manual + v
            sources[key] = {"crm": v, "manual": manual}
    return out, sources


# 逐案清單裡要加總成合計列的鍵（一份清單，別在端點裡再列一次）
SUM_KEYS = ("contract", "received", "receivable", "spent", "ap_open", "net")


def income_items(settings: dict | None = None) -> list:
    """工項清單。settings 傳 None 時回預設（純函式，讀設定由呼叫端負責）。"""
    items = ((settings or {}).get("my_ledger") or {}).get("income_items")
    return [str(x) for x in items if str(x).strip()] if items else list(DEFAULT_INCOME_ITEMS)


# 案源（owner 2026-08-25/26）：源日＝現金收款；代開發票＝營收 × 服務費率的
# 代辦費（預設 8%，191 個歷史案實證全部 8.00%；逐案可調）；執行業務所得＝
# 源頭代扣自動算（見 apply_source_fee）。
# 🔴 自接＝歷史值（361 案），**不再可選**（owner 2026-08-26「下拉把自接移除」）
# —— 留在 SOURCES 白名單讓舊案的 meta 不被 norm_detail 洗掉，UI 下拉用
# SELECTABLE_SOURCES。
SOURCES = ("自接", "源日", "代開發票", "執行業務所得")
SELECTABLE_SOURCES = ("源日", "代開發票", "執行業務所得")
DEFAULT_FEE_PCT = 8.0

# 執行業務所得的源頭代扣（典藏媒體顧問實帳驗證：42,000 → 4,200＋886＝5,086）
WITHHOLD_TAX_PCT = 10.0          # 所得扣繳；單次稅額 ≤ 2,000 免扣
WITHHOLD_TAX_EXEMPT = 2000
NHI_PCT = 2.11                   # 二代健保補充保費；單次 < 20,000 免扣
NHI_MIN_PAYMENT = 20000


def withholding(contract: int) -> int:
    """執行業務所得單次給付的源頭代扣合計（所得扣繳＋二代健保）。"""
    c = int(contract or 0)
    tax = round(c * WITHHOLD_TAX_PCT / 100)
    if tax <= WITHHOLD_TAX_EXEMPT:
        tax = 0
    nhi = round(c * NHI_PCT / 100) if c >= NHI_MIN_PAYMENT else 0
    return tax + nhi

# 案碼協定：匯入/新增都把 `案碼:XXX` 寫進 notes（前綴不同：[私帳匯入]/[私帳新增]），
# 收支回掛與撞碼防線都靠它。
_CODE_NOTE_RE = re.compile(r"案碼:(\S+)")


def code_of(notes) -> str:
    """notes 裡的案碼；無案碼（含匯入時寫的「無」）→ 空字串。

    🔴 這個協定的**唯一解析器**。讀取端曾經各解各的（撞碼防線用 SQL LIKE
    行尾錨點、回填腳本用 regex）——「案碼:2026010 補」這種尾註列 regex 認、
    LIKE 不認，防線就漏了。解析只留這一份，讀案碼的一律呼叫這支。
    """
    m = _CODE_NOTE_RE.search(notes or "")
    code = m.group(1) if m else ""
    return "" if code == "無" else code


def norm_detail(raw) -> dict:
    """ledger_detail → 固定形狀（缺鍵補 0、split 只留非零數字）。

    0 值工項會被清掉 —— 那正是「使用者把格子清空」的意思（＝刪掉這個工項）。
    另保留兩個 meta 鍵：source（案源）與 fee_pct（服務費率；只在非預設時存）。
    """
    d = raw if isinstance(raw, dict) else {}
    out = {k: int(d.get(k) or 0) for k in COST_KEYS}
    split = d.get("split") if isinstance(d.get("split"), dict) else {}
    out["split"] = {str(k): int(v) for k, v in split.items()
                    if isinstance(v, (int, float)) and int(v)}
    src = str(d.get("source") or "").strip()
    if src in SOURCES:
        out["source"] = src
    try:
        pct = float(d.get("fee_pct"))
        if pct > 0 and pct != DEFAULT_FEE_PCT:
            out["fee_pct"] = pct
    except (TypeError, ValueError):
        pass
    return out


def apply_source_fee(contract: int, d: dict) -> dict:
    """案源＝代開發票 → 三個欄位自動算（**唯一的自動費用規則**，寫入端呼叫；
    其他案源不動使用者填的數字）。owner 2026-08-25：「稅金5%+買發票＝代辦
    發票的%數，這邊可以直接自動計算」，Sheet 實證吻合（82,000 →
    稅金 3,905 + 買發票 2,655 = 代辦費 6,560 = 8%）：

        發票代辦費 = round(營收 × 費率)          —— 給代開業者的總服務費
        稅金       = round(營收 / 1.05 × 5%)     —— 其中的營業稅（未稅 × 5%）
        買發票     = 代辦費 − 稅金               —— 其中的開票佣金

    稅金與買發票是代辦費的**組成**，不再另外進實收的減項（compute 只扣
    invoice_fee —— 三個都扣就是同一筆錢扣兩次）。"""
    if d.get("source") == "代開發票":
        c = int(contract or 0)
        pct = float(d.get("fee_pct") or DEFAULT_FEE_PCT)
        d["invoice_fee"] = round(c * pct / 100)
        d["tax_fee"] = round(c / 1.05 * 0.05)
        d["buy_invoice"] = d["invoice_fee"] - d["tax_fee"]
    elif d.get("source") == "執行業務所得":
        # 個人稅款＝源頭代扣自動算（owner 2026-08-26「新增一個執行業務所得的
        # 項目自動算」）；應收基準同步吃到（expected_cash_in 減 personal_tax）
        d["personal_tax"] = withholding(contract)
    return d


#: **源頭代扣** —— 客戶匯款前就被扣走的錢，永遠不會經過我們的手。
#: 代開業者的代辦費、執行業務所得的個人稅款。
WITHHELD_FIELDS = ("invoice_fee", "personal_tax")

#: **要自己匯出去的** —— 委外、行政雜支、稅金、買發票。
#: 🔴 不含源頭代扣：那筆錢沒進來過，再算一次「要付出去」就是重複。
PAYOUT_FIELDS = ("outsource", "misc", "tax_fee", "buy_invoice")


def client_wire(contract: int, d: dict) -> int:
    """② 客戶最終會匯進來多少 ＝ 營收 − 源頭代扣。

    🔴 與 `expected_cash_in` 的差別：那支只在案源＝代開發票時才扣代辦費
    （它服務的是收款狀態判定）；這支**一律扣**，因為 owner 2026-08-29 說明
    「我收到的就已經是扣除後的了」—— 帳上記全額收入＋代辦費支出兩列，是為了
    讓帳面看得見全額，實際匯進來的一直是淨額。
    """
    return int(contract or 0) - sum(int((d or {}).get(k) or 0) for k in WITHHELD_FIELDS)


def payout_total(d: dict) -> int:
    """③ 這一案總共要自己匯出去多少（委外＋雜支＋稅金＋買發票）。"""
    return sum(int((d or {}).get(k) or 0) for k in PAYOUT_FIELDS)


def to_collect(contract: int, received: int, d: dict) -> int:
    """④ 這一案**還沒收到**多少 ＝ 應收（客戶會匯的總數）− 已收。

    🔴 一條規則要同時吃兩種記法（owner 2026-08-29「從新的帳開始，未收就會是
    應收為基準」）：
    - **舊帳**：代開的收款記**全額**，代辦費另記一筆支出。收齊時 已收＝營收，
      應收−已收 ＝ −代辦費（負的）。
    - **新帳**：收款直接記**淨額**。收齊時 已收＝應收，差額 0。
    兩種都該顯示「收齊了」。所以**負差額在源頭代扣的範圍內就當 0** ——
    那不是溢收，是兩種記法的落差。

    超出那個範圍的負數**照實顯示**：那是真的溢收（客戶多付了），
    夾成 0 會讓它永遠沒人發現。
    """
    short = to_collect_gross(contract, received, d)
    withheld = int(contract or 0) - client_wire(contract, d)
    return 0 if -withheld <= short < 0 else short


def to_collect_gross(contract: int, received: int, d: dict) -> int:
    """未收的原始差額（應收 − 已收），沒有夾。給要看溢收的地方用。"""
    return client_wire(contract, d) - int(received or 0)


def expected_cash_in(contract: int, d: dict) -> int:
    """這一案**實際會匯進來**的錢 —— 應收與收款狀態都以此為基準，不是營收。

    源頭代扣的錢永遠不會進帳，掛在應收上就是永遠清不掉的殘尾
    （owner 2026-08-26：「那個是代扣勞保，本來就要扣掉的」）：
    - 個人稅款（personal_tax）＝執行業務所得的源頭代扣。已對帳實證：
      42,000 × 10%（所得扣繳）＋ 42,000 × 2.11%（二代健保補充保費）
      ＝ 4,200 ＋ 886 ＝ 5,086，與典藏媒體顧問案的個人稅款分毫吻合；
      全庫 0 筆「收滿又記稅」的自繳反例。五月報稅退回的所得稅走獨立的
      「綜合所得稅退稅」案，不回沖到本案。
    - 代開發票的代辦費（invoice_fee）＝代開業者匯款前先扣走。
    """
    base = int(contract or 0) - int(d.get("personal_tax") or 0)
    if d.get("source") == "代開發票":
        base -= int(d.get("invoice_fee") or 0)
    return base


def receivable_status(expected: int, received: int) -> str:
    """收款狀態判定 —— 收支同步（_sync_mine_project_received）與執行專案的
    新增/更新端點共用同一條。expected＝expected_cash_in（不是營收 ——
    2026-08-26 之前用營收當基準，34 個源頭代扣案永遠「部分到帳」）。"""
    if expected > 0 and received >= expected:
        return "全額到帳"
    if received > 0:
        return "部分到帳"
    return "未到帳"


def compute(contract: int, d: dict) -> tuple:
    """(實收, 檢查)。算式正本 —— 前端與匯入腳本都只呼叫這支，不各算一份。"""
    net = (contract - d["outsource"] - d["invoice_fee"]
           - d["personal_tax"] - d["misc"] - d["shareholder"])
    return net, net - sum(d["split"].values())
