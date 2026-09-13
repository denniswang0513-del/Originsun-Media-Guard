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

# 營業稅率 —— 正本在 core.finance_logic（發票未稅／稅額吃的是同一個）。
# 這裡轉出，讓呼叫端不必為了一個常數多 import 一個模組。
from core.finance_logic import VAT_DIVISOR, VAT_PCT

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
# routers/crm/finance._apply_outsource（單一出口）。
# 🔴 只放鍵，不放標籤 —— 標籤的正本是上面的 COST_FIELDS。這裡曾經帶過一份
# {"outsource": "人員費用"}，跟 COST_FIELDS 的「委外費用」當場打架而且沒人讀。
CRM_BACKED = ("misc", "outsource")


def apply_crm_costs(detail: dict, crm: dict | None) -> tuple:
    """把 CRM 算出來的合計併進 detail。回 `(detail, sources)`。

    `crm`：`{"misc": 合計, "outsource": 合計}`；0 或缺席＝CRM 那邊沒資料。
    `outsource` 有第二個寫入者：`routers/crm/finance._apply_outsource`
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


# ── 連結私帳（owner 2026-08-29）────────────────────────────────────
#: 鏡射案的案源。源日＝現金收款（不抽代辦費、不算源頭代扣），正是公司內部
#: 轉單該有的形狀 —— 手動建的 109 個歷史案也都是這個值。
MIRROR_SOURCE = "源日"
#: ledger_detail 裡記「上次同步時母帳掛給我的成本行合計」的鍵（norm_detail 保留它）。
MIRROR_TOTAL_KEY = "mirror_total"

# ── 收款方式（owner 2026-09-13，母帳 `crm_projects.billing_mode`）────────────
# 表單上一個下拉取代「推送到私帳」先問是哪一種的彈窗：
#   company     源日專案 —— 公司的案（預設；NULL 視同它）。跟後期沒有自動連結，
#               公司付我一部分再按「推送到私帳」建分身。
#   passthrough 後期代開 —— 我的後期案、客戶走源日代開發票。儲存即在私帳建對應的案
#               （案源＝代開發票、收入＝母帳合約額、代辦費自動）；發票預設「內部代開」。
#   cash        現金收款 —— 源日收現金（owner：「這裡是源日的收款狀態」），**不**碰後期。
BILLING_MODES = ("company", "passthrough", "cash")
BILLING_LABELS = {"company": "源日專案", "passthrough": "後期代開", "cash": "現金收款"}
#: 哪種收款方式會自動要一個私帳分身、分身的案源是什麼（不在表裡＝不自動建）
BILLING_MIRROR_SOURCE = {"passthrough": "代開發票"}


def billing_mode_of(raw) -> str:
    """欄位值 → 三值之一（NULL／垃圾＝company）。"""
    v = str(raw or "").strip()
    return v if v in BILLING_MODES else "company"


def _fmt_n(n) -> str:
    return f"{int(n or 0):,}"


def link_note(mode: str, mine, detail, *, stale=None, delta: int = 0, crm_total: int = 0,
              passthrough_invoices: int = 0) -> dict:
    """「後期連結」那一行系統備註（母帳專案表單／詳情用；前端只畫，不拼句子）。

    `mine`＝連到的私帳案 `(id, name, contract)`，沒連＝None；`detail`＝私帳案的 ledger_detail
    （已 norm）；`stale`／`delta`＝mirror_stale 的結果、`crm_total`＝母帳現在掛給我的成本行合計
    （詳情那顆「重新同步」鈕要講的數字 —— 跟這一行同一趟拿，不再另外打 mirror-check）。回的 `text` 是一整句。
    """
    mode = billing_mode_of(mode)
    out = {"mode": mode, "mode_label": BILLING_LABELS[mode], "linked": mine is not None,
           "mine_id": "", "mine_name": "", "source": "", "contract": 0, "invoice_fee": 0,
           "fee_pct": DEFAULT_FEE_PCT, "tax_fee": 0, "buy_invoice": 0, "fee_deducted": True,
           "stale": None, "delta": 0, "crm_total": 0, "mirror_at": "",
           "passthrough_invoices": int(passthrough_invoices or 0), "text": ""}
    if mine is None:
        if mode == "passthrough":
            out["text"] = "儲存後會自動在私帳建對應的案（案源＝代開發票、代辦費自動算）。"
        elif mode == "cash":
            out["text"] = "源日收現金，沒有發票；跟後期沒有自動連結。"
        else:
            out["text"] = "公司的案，沒有後期連結。公司付你一部分再按「推送到私帳」。"
        return out
    d = detail if isinstance(detail, dict) else {}
    mid, mname, contract = mine[0], mine[1] or "", int(mine[2] or 0)
    src = str(d.get("source") or MIRROR_SOURCE)
    out.update({"mine_id": mid, "mine_name": mname, "source": src, "contract": contract,
                "invoice_fee": int(d.get("invoice_fee") or 0),
                "fee_pct": float(d.get("fee_pct") or DEFAULT_FEE_PCT),
                "tax_fee": int(d.get("tax_fee") or 0), "buy_invoice": int(d.get("buy_invoice") or 0),
                "fee_deducted": fee_deducted(d), "stale": stale, "delta": int(delta or 0),
                "crm_total": int(crm_total or 0), "mirror_at": str(d.get(MIRROR_AT_KEY) or "")})
    parts = [f"已連結後期 → {mname}", f"案源 {src}", f"收入 {_fmt_n(contract)}"]
    if src == "代開發票":
        pct = out["fee_pct"]
        pct_s = f"{pct:g}%"
        parts.append(f"代辦費 {pct_s} ＝ {_fmt_n(out['invoice_fee'])}"
                     f"（稅金 {_fmt_n(out['tax_fee'])} ＋ 買發票 {_fmt_n(out['buy_invoice'])}）"
                     + ("・已扣" if out["fee_deducted"] else "・未扣（全額匯入，代辦費另付）"))
    elif src == "源日":
        parts.append("不抽代辦費")
    if stale is True:
        parts.append("私帳落後（母帳成本行改了）")
    elif stale is False:
        parts.append("一致")
    if out["mirror_at"]:
        parts.append(f"上次同步 {out['mirror_at']}")
    if out["passthrough_invoices"]:
        parts.append(f"內部代開發票 {out['passthrough_invoices']} 張")
    out["text"] = " ・ ".join(parts)
    return out


def mirror_amount(line) -> int:
    """一行成本要記多少進私帳收入：實際優先、沒填才用預估。

    跟 CRM 其他地方同口徑（專案結算看 actual、執行預算看 estimated）。
    兩個都空＝這工項還沒發生，回 0 讓上層濾掉 —— 把估算當成收入記進去，
    私帳的營收就會領先現實。
    """
    return int(getattr(line, "actual_amount", None)
               or getattr(line, "estimated_amount", None) or 0)


def mirror_lines(cost_lines, staff_id: str) -> dict:
    """母公司專案的成本行 → 私帳的收入分身。

    回 `{"lines": [{phase,item,amount}], "split": {工項: 金額}, "total": int}`。

    🔴 認人只認 `actual_staff_id`（實際派給誰），沒填才退回 `estimated_staff_id`
    —— 預估掛我、實際換人做的那些不是我的收入。

    🔴 同名工項要**相加**進 split（一案可能有兩行「導演」，實測 OMRON 那案就
    有兩行腳本、兩行導演）。dict 直接指派會讓後面那行吃掉前面那行。
    """
    out, split, total = [], {}, 0
    for ln in cost_lines or []:
        who = getattr(ln, "actual_staff_id", None) or getattr(ln, "estimated_staff_id", None)
        if who != staff_id:
            continue
        amt = mirror_amount(ln)
        if not amt:
            continue
        item = (getattr(ln, "item_name", "") or "").strip() or "其他"
        out.append({"phase": getattr(ln, "phase", "") or "",
                    "item": item, "amount": amt})
        split[item] = split.get(item, 0) + amt
        total += amt
    return {"lines": out, "split": split, "total": total}


def merge_split(current: dict, incoming: dict, *, add: bool) -> tuple:
    """把一個 CRM 案鏡射過來的收入分項併進私帳案的 split，回 `(merged, delta)`。

    `add=True`＝一個私帳案承接多個 CRM 案（owner 2026-09-01「可以多筆專案連結
    到一筆私帳」）：同名工項相加。`add=False`＝overwrite，用這一個 CRM 案的錢
    取代（第二個來源進來時 overwrite 會把前一個的錢洗掉，所以多對一只有 add
    說得通）。

    🔴 回的是 **delta 不是 total**：私帳案的 `contract_amount` 未必等於
    Σ(split)——他可能自己填過非工項的合約金額。呼叫端拿 delta 去加，
    才不會在「順手改成 sum(merged.values())」的時候靜默改掉已連結案的金額。

    同名工項要相加這條規則跟 `mirror_lines` 是同一條（生產庫的 OMRON 那案有
    兩行導演、兩行腳本），那邊做的是單一案內、這邊是跨案。
    """
    merged = dict(current or {}) if add else {}
    delta = 0
    for k, v in (incoming or {}).items():
        amt = int(v or 0)
        merged[k] = int(merged.get(k, 0)) + amt
        delta += amt
    return merged, delta


def mirror_detail(split: dict) -> dict:
    """鏡射案的 ledger_detail：只有收入分項、案源與「這次同步的合計」（＝Σsplit，
    跟 mirror_lines 的 total 相等），成本欄全 0。

    委外／代開發票／稅金那些是**我自己的**成本，公司管不著 —— 建立時留空，
    之後他在私帳自己填。再同步時也只覆蓋 split（見 routers/crm/projects.py）。
    """
    total = sum(int(v or 0) for v in (split or {}).values())
    return norm_detail({"split": split, "source": MIRROR_SOURCE, MIRROR_TOTAL_KEY: total})


#: 可以被人「接手」、不再自動覆寫的欄位。加第二欄只要往這裡加一個字串。
#: `invoice_fee`（owner 2026-09-13「代開費用……但我要改還是可以改」）：代辦費仍照費率
#: 試算，改過就凍住；稅金／買發票永遠是它的組成（買發票＝代辦費 − 稅金）。
MANUAL_FIELDS = frozenset({"personal_tax", "invoice_fee"})

#: 「代辦費已扣除」（owner 2026-09-13）：代開的代辦費是**源頭代扣**（代開業者匯款前先扣走）
#: —— 營收仍是合約額，應收＝營收 − 代辦費。這是預設；取消勾選＝那筆費用沒有先扣、
#: 客戶（或源日）會把全額匯進來，應收＝營收，代辦費之後自己付。
#: 🔴 只在 **False** 時落庫（缺鍵＝True），前端一律用 `fee_deducted !== false` 讀。
FEE_DEDUCTED_KEY = "fee_deducted"
#: 上次從母帳同步的日期（YYYY-MM-DD；給「後期連結」那行備註講「上次同步」）
MIRROR_AT_KEY = "mirror_at"


def fee_deducted(d) -> bool:
    """代辦費有沒有在源頭先扣掉（缺鍵＝有）。"""
    return (d if isinstance(d, dict) else {}).get(FEE_DEDUCTED_KEY) is not False


def withheld_total(d: dict) -> int:
    """源頭代扣合計：個人稅款一律；代辦費只在「已扣除」時算（`fee_deducted`）。"""
    d = d if isinstance(d, dict) else {}
    total = int(d.get("personal_tax") or 0)
    if fee_deducted(d):
        total += int(d.get("invoice_fee") or 0)
    return total


def manual_fields(d) -> set:
    """一份 ledger_detail 裡「哪幾欄由人決定」。**讀法只有這一份。**

    收三件事：讀 `manual` 清單、吸收舊資料的 `tax_manual` 布林鍵、用
    `MANUAL_FIELDS` 白名單夾（前端亂送的鍵不能凍住任意欄位）。
    🔴 兩個呼叫端各寫一遍的話會漂 —— 而且曾經真的漂過：`apply_source_fee`
    那份少了白名單，只是靠「隨後會被 norm_detail 濾掉」的呼叫順序在兜。
    """
    d = d if isinstance(d, dict) else {}
    out = set(d.get("manual") or ())
    if d.get("tax_manual"):        # 舊資料：單一布林鍵的年代
        out.add("personal_tax")
    return out & MANUAL_FIELDS


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
    # 「這幾欄由人決定，不要自動覆寫」（見 apply_source_fee）—— 不保留的話
    # 每次正規化都把它洗掉，等於旗標從來沒存在過。
    # 🔴 存成**清單**不是每欄一個布林鍵：`invoice_fee` 的手動覆寫遲早會來
    # （owner 對代開費說過「預設帶出內容，但我可以細調」），到時只是清單多一
    # 個字串，不用再長一個 `fee_manual` 鍵 ＋ 一段保留邏輯 ＋ 一份前端鏡射。
    manual = manual_fields(d)
    if manual:
        out["manual"] = sorted(manual)
    try:
        pct = float(d.get("fee_pct"))
        if pct > 0 and pct != DEFAULT_FEE_PCT:
            out["fee_pct"] = pct
    except (TypeError, ValueError):
        pass
    # 上次從母帳鏡射過來時的成本行合計（見 mirror_stale）—— 不保留的話每一次
    # PUT 都把它洗掉，「私帳落後多少」就永遠判不出來。
    try:
        mt = int(d.get(MIRROR_TOTAL_KEY))
        if mt > 0:
            out[MIRROR_TOTAL_KEY] = mt
    except (TypeError, ValueError):
        pass
    # 「代辦費已扣除」只在取消時落庫（缺鍵＝已扣，見 FEE_DEDUCTED_KEY）
    if d.get(FEE_DEDUCTED_KEY) is False:
        out[FEE_DEDUCTED_KEY] = False
    ma = str(d.get(MIRROR_AT_KEY) or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ma):
        out[MIRROR_AT_KEY] = ma
    return out


def mirror_stale(detail, current_total: int):
    """私帳有沒有落後母帳 → `(stale, delta)`。

    `stale` 三值：True＝母帳成本行改了（`delta`＝現在 − 上次同步）、False＝一致、
    None＝判不出來（舊連結沒記過 `mirror_total`；重新同步一次就會記）。
    🔴 比的是**上次同步的合計**，不是私帳現在的 Σsplit —— 私帳那份他可能自己
    改過（照實際請款填），那不是「落後」，是他的決定。
    """
    d = detail if isinstance(detail, dict) else {}
    try:
        last = int(d.get(MIRROR_TOTAL_KEY))
    except (TypeError, ValueError):
        return None, 0
    if last <= 0:
        return None, 0
    return int(current_total or 0) != last, int(current_total or 0) - last


# ── 連結後以母帳為準（owner 2026-09-12「兩邊的專案表一樣都可以，以母帳為準」）──
#: 連結的兩案之間「這是哪一個案」的識別欄。金額欄**永遠不在這裡**：母帳的
#: contract_amount 是公司跟客戶的合約額、私帳的是「我拿到的那段」，同一個欄位
#: 兩種意思（差 3～17 倍，見 docs/LEDGER_UNIFY_PLAN.md §2.2）。錢的橋只有一座：
#: 母帳成本行 → 私帳收入（mirror_lines）。`name` 也不在：它是 Sheet 工時對映的
#: 查表鍵，顯示走 linked_display_name。`status` 不在：私帳只有結案／製作，由
#: 結案日推導（母帳的「歸檔」「提案」在私帳沒有意義）。
LINK_SYNC_FIELDS = ("client_id", "project_type", "shoot_date", "start_date",
                    "completion_date", "description")


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def sync_from_parent(parent, mine, *, explicit=(), skip=()) -> tuple:
    """連結的一對案：識別欄以母帳為準，就地改兩個物件 → `(私帳改了的欄, 母帳補了的欄)`。

    兩條規則（§8.2）：
      1. 母帳有值 → 私帳跟著（連結當下一次、母帳之後每次改都跟）。
      2. 母帳空白 → **不清私帳**，反而拿私帳的補進母帳（母帳 216 案沒結案日、
         私帳全部有 —— 是私帳補母帳，不是母帳洗私帳）。
    🔴 `client_id` 只做規則 1：私帳客戶（entity=mine 的 Client）不能直接寫進母帳案，
    要先對到／建出母帳客戶 —— 那是 I/O，由呼叫端（routers/crm/projects._write_link）
    用 `_crm_client_for` 補。這支是純函式，不碰 session。
    N:1（一個私帳案承接多個母帳案）由呼叫端擋：母帳互相矛盾時不猜。

    `explicit`：母帳 PUT 裡使用者**親手送上來**的欄位。這幾欄不做規則 2，而且母帳
    送空白＝要清空（清結案日＝重開案）→ 私帳跟著清；不能在同一交易被私帳的值補回去，
    那是他的決定不是「母帳沒填」。（沒有 explicit 的呼叫端＝連結當下，空白才是「沒填」。）
    `skip`：這幾欄兩個方向都不碰（呼叫端判定「兩邊其實是同一個東西」，例如私帳
    客戶已連結到母帳那筆客戶）。
    """
    changed, filled = [], []
    for f in LINK_SYNC_FIELDS:
        if f in skip:
            continue
        pv, mv = getattr(parent, f, None), getattr(mine, f, None)
        if not _blank(pv) or f in explicit:
            if pv != mv and not (_blank(pv) and _blank(mv)):
                setattr(mine, f, pv)
                changed.append(f)
        elif not _blank(mv) and f != "client_id":
            setattr(parent, f, mv)
            filled.append(f)
    if "completion_date" in changed:
        # 私帳的狀態只有結案／製作，由結案日推導（同 new_ledger_project）
        mine.status = "結案" if mine.completion_date else "製作"
    return changed, filled


def apply_source_fee(contract: int, d: dict, *, keep=()) -> dict:
    """案源＝代開發票 → 三個欄位自動算（**唯一的自動費用規則**，寫入端呼叫；
    其他案源不動使用者填的數字）。owner 2026-08-25：「稅金5%+買發票＝代辦
    發票的%數，這邊可以直接自動計算」，Sheet 實證吻合（82,000 →
    稅金 3,905 + 買發票 2,655 = 代辦費 6,560 = 8%）：

        發票代辦費 = round(營收 × 費率)          —— 給代開業者的總服務費
        稅金       = round(營收 / 1.05 × 5%)     —— 其中的營業稅（未稅 × 5%）
        買發票     = 代辦費 − 稅金               —— 其中的開票佣金

    稅金與買發票是代辦費的**組成**，不再另外進實收的減項（compute 只扣
    invoice_fee —— 三個都扣就是同一筆錢扣兩次）。

    `keep`：這次呼叫端**明確送來**的欄位，不覆寫。
    🔴 個人稅款的自動值是**試算不是規定**（owner 2026-09-01「一開始先試算，
    但有些狀況讓我可以調整 —— 有些客戶會拆單，所以我不用先繳」）。原本每次
    存檔都無條件覆寫，使用者改了也存不進去（改完按儲存，數字自己跳回來）。
    建立新案時沒有這一欄可送 → keep 是空的 → 照樣自動試算。
    """
    src = d.get("source")
    if src not in ("代開發票", "執行業務所得"):
        return d
    # 自動值是**試算不是規定**：送來的值 ≠ 試算 → 這格從此由人決定；改回試算值 → 交還自動。
    # 🔴 旗標要**落庫**，不能只看「這次有沒有送」：只改別欄的那種存檔（例如單改行政雜支）
    # 不會送這一欄，下一秒就把人調好的數字洗回試算值。
    field = "invoice_fee" if src == "代開發票" else "personal_tax"
    c = int(contract or 0)
    auto = (round(c * float(d.get("fee_pct") or DEFAULT_FEE_PCT) / 100) if src == "代開發票"
            else withholding(contract))
    manual = manual_fields(d)
    if field in keep:
        manual.discard(field)
        if int(d.get(field) or 0) != auto:
            manual.add(field)
    if field not in manual:
        d[field] = auto
    if src == "代開發票":
        # 稅金與買發票是代辦費的**組成**（買發票＝代辦費 − 稅金），代辦費手改時跟著重算
        d["tax_fee"] = round(c / VAT_DIVISOR * (VAT_PCT / 100))
        d["buy_invoice"] = int(d["invoice_fee"] or 0) - d["tax_fee"]
    d.pop("tax_manual", None)      # 一律換成清單形狀，別留兩種真相
    if manual:
        d["manual"] = sorted(manual)
    else:
        d.pop("manual", None)
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
    return int(contract or 0) - withheld_total(d)


#: 代辦費的**組成**（稅金＋買發票）：代辦費已在源頭扣走時，這兩欄跟著不用再匯
FEE_PARTS = ("tax_fee", "buy_invoice")


def payout_total(d: dict) -> int:
    """③ 這一案總共要自己匯出去多少（委外＋雜支；代辦費沒先扣時再加稅金＋買發票）。

    owner 2026-09-13：「不用再匯，因為已經有這個支出付掉了」—— 稅金與買發票是代辦費的組成，
    代辦費已扣除（`fee_deducted`，預設）＝代開業者匯款前就拿走了，再列成「要匯出去的」就是同一筆錢算兩次。
    取消已扣除（全額匯進來）時它們才真的要自己匯。
    """
    keys = PAYOUT_FIELDS if not fee_deducted(d) else tuple(k for k in PAYOUT_FIELDS if k not in FEE_PARTS)
    return sum(int((d or {}).get(k) or 0) for k in keys)


def settle_state(to_collect_amt: int, ap_open: int, payout: int) -> dict:
    """收付狀態 —— 列表最左欄與狀態篩選**共用這一份**。

    回 `{"in": ..., "out": ..., "done": bool}`：
      in  'ok' 收齊／'wait' 還沒收完／'over' 溢收（客戶多付）
      out 'ok' 付清／'wait' 還沒付完／'none' 這案本來就沒有要付的
      done 兩邊都完成 → 畫面收成一個記號（owner 2026-08-29
           「如果該收該付都完成，我希望變成一個色塊是結案」）

    🔴 這裡的「結案」只看錢清了沒，跟專案的**結案日**無關：沒填結案日但錢都
    清了一樣算結案；有結案日但還欠錢，照樣亮兩個記號。名字一樣、判準不同，
    所以寫在這裡而不是塞進 close_date 的邏輯裡。
    """
    tin = "over" if to_collect_amt < 0 else ("wait" if to_collect_amt > 0 else "ok")
    tout = "none" if not payout else ("wait" if ap_open > 0 else "ok")
    return {"in": tin, "out": tout,
            "done": tin == "ok" and tout in ("ok", "none")}


#: 狀態篩選的值域（前端下拉與 `settle_match` 共用；空字串＝全部）
SETTLE_FILTERS = ("done", "in", "out", "either", "over")


def settle_match(state: dict, want: str) -> bool:
    """這一案符不符合狀態篩選。`want` 空＝全部。"""
    if not want:
        return True
    if want == "done":
        return bool(state.get("done"))
    if want == "in":
        return state.get("in") == "wait"
    if want == "out":
        return state.get("out") == "wait"
    if want == "either":
        return state.get("in") == "wait" or state.get("out") == "wait"
    if want == "over":
        return state.get("in") == "over"
    return True


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
    if d.get("source") == "代開發票" and fee_deducted(d):
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


def receivable_fields(contract: int, received: int, d: dict) -> tuple:
    """(應收, 收款狀態) —— 「營收或代扣成本動了就重算這兩欄」的算式正本。

    基準是**實際會進帳的錢**（營收 − 源頭代扣），不是營收。四個寫入端共用：
    帳本新增／編輯（routers/api_finance_projects）、收支同步與請款同步
    （routers/crm/finance）。各自抄一份的下場是同一案在應收帳款與逐案損益
    給出兩個數字 —— 2026-08-26 清查到 349 案 amount_receivable 是 NULL，
    正是「有人忘了補這兩行」的痕跡。
    """
    exp = expected_cash_in(int(contract or 0), d)
    recv = int(received or 0)
    return exp - recv, receivable_status(exp, recv)


def compute(contract: int, d: dict) -> tuple:
    """(實收, 檢查)。算式正本 —— 前端與匯入腳本都只呼叫這支，不各算一份。"""
    net = (contract - d["outsource"] - d["invoice_fee"]
           - d["personal_tax"] - d["misc"] - d["shareholder"])
    return net, net - sum(d["split"].values())


def parent_receipt_fields(contract: int, received: int, fee: int) -> tuple:
    """母公司案（CRM）的 (應收, 收款狀態) —— 從**掛在本案的收入列**推導
    （owner 2026-09-04「發票與收支連結好了的話，收到多少款＋匯費 等於合約金額的時候，這裡要改已到帳」）。

    received＝Σ 掛在本案（列自己掛的、或掛在本案發票上的）收入列的 deposit，
    fee＝Σ 那些列的 bank_fee。

    🔴 本 repo 的收支慣例（core.finance_logic：cash_entry_flow ＝ deposit − expense − bank_fee）：
    deposit 記的是**客戶匯出的毛額**，bank_fee 是銀行從中扣走的，淨入帳＝deposit − bank_fee。
    所以拿 deposit 比合約就對了；2026-09-04 曾寫成 deposit＋fee（匯費算兩次 → 東仁社宅溢收 −15），
    owner 2026-09-05 抓到。fee 只給顯示用（實入帳＝received − fee）。
    到齊＝全額到帳、有就部分到帳、沒有＝未到帳；應收＝合約 − 毛額（溢收為負，看得見）。
    """
    gross = int(received or 0)
    c = int(contract or 0)
    return c - gross, receivable_status(c, gross)


def linked_display_name(mine_name: str, parent_names, custom: str = "") -> tuple:
    """一個案要顯示成什麼名字。**這條鏈只有這一份**（owner 2026-09-05）。

    依序取第一個有值的，回 `(顯示名, 副標)`：

      1. `custom`（`crm_projects.display_name`，人工指定）→ `(自訂名, 私帳原名)`
      2. 恰好連到 **1 個**母帳案 → `(母帳案名, 私帳原名)`
      3. 其餘（沒連、或 N:1）→ `(私帳原名, "")`

    副標＝私帳原名，只在顯示名不等於它時才回（相同就沒有東西好副標的）。

    N:1 自動規則不套用是因為挑不出哪一個才對 —— 生產庫現有兩例
    （`總統創新獎頒獎影片` 連著產科會與獎盃製作兩案、`南山人壽頒獎影片`
    連著王經理與謝經理），強行取第一個等於隨機。那兩案由 owner 自己填
    `display_name`（第 1 層），沒填就退回原名 ＋ 呼叫端標「連結 N 個母帳案」。

    🔴 **這是顯示層的事，`crm_projects.name` 一律不改寫**，兩個理由：
      · Sheet 工時的 `resolve_project()` 靠 `project_name` 查
        `services.timesheet_lookup.load_project_lookup()` 的名稱索引 —— 改掉私帳
        案名，之後新進的 Sheet 列就對不到案（既有列 project_id 已寫入，
        所以症狀會延遲一週才出現）。這也正是要有第 1 層的原因：想改名字時
        改 `display_name`，不要去動那個有負擔的 `name`。
      · 私帳原名是 owner 自己的語彙（「開村影片」vs「蟾蜍山｜煥民新村 館所介紹」），
        改掉找不回來，而收支明細／對帳／匯入預覽都還在用它認案。

    🔴 搜尋要吃**三個**名字（自訂／母帳／私帳原名）—— 顯示名換掉之後還要
    搜得到，否則這個功能會變成「找不到東西」。述詞在呼叫端，別忘了。

    規劃見 docs/LEDGER_UNIFY_PLAN.md §2.1。
    """
    orig = mine_name or ""
    names = [n for n in (parent_names or ()) if n]
    shown = (custom or "").strip() or (names[0] if len(names) == 1 else orig)
    return shown, ("" if shown == orig else orig)
