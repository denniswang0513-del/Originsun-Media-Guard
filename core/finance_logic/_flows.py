"""財務純邏輯 · 現金流量表與檢核（import `_core`，另需 `_statements.is_account_move`）。

build_cashflow / merge_cf / cash_entry_activity（直接法）、transfer 配對、
statement_warnings + statement_interpretation（檢核警示與白話解讀）、
階段五的儀表板/稅務包純函式（帳齡、客戶集中度、runway）。

對外一律從 `core.finance_logic` 匯入，別直接指名這個檔。
"""
from __future__ import annotations

import calendar
from datetime import date, datetime
from ._core import (CF_ACTIVITY_DRILLS, FEE_TOLERANCE, INTERNAL_MOVEMENT_TREATMENTS, _as_date, _pct, cash_entry_flow, classify_cash_entry, in_amount, is_card_kind, is_shareholder_kind, local_day, map_account, map_info, month_of, out_amount)
from ._statements import (is_account_move)

# ── 現金流量表（直接法）──────────────────────────────────────

def cash_entry_activity(entry: dict, cat_map: dict, accounts: dict, *,
                        internal: bool = True):
    """單筆收支 → 現金流量活動。None = 本金不列入（內部移動）。

    走對映科目的 cf_activity（'none'/查無 → operating）—— 貸款撥款/繳款
    （treatment='loan'）對映科目 2400 cf_activity=financing，自然落籌資活動且
    不進損益；硬連結結清（ar/ap）、稅、passthrough、unmapped → operating
    （最不錯的預設，unmapped 另由 statement_warnings 計數）。

    🔴 `internal`：這一列**是不是真的只是內部移動**（錢搬到另一個追蹤中的帳戶）。
    只有那種才不列入活動 —— 兩腳互相抵銷，記不記都不影響總額。
    transfer/advance 的另一半是「錢真的離開了現金池」（買股票、繳卡費、
    代墊給家人）：銀行餘額掉了卻不記淨流，「期初＋淨流 ≠ 期末」的差額就是
    它們（owner 2026-08-29 實測私帳本期 −4,365,820）。
    呼叫端（cashflow_lines）用 transfer_pairs 配對的結果決定這個旗標；
    預設 True＝維持舊行為，給還沒帶旗標的呼叫端。
    """
    t = classify_cash_entry(entry, cat_map or {})
    if t in INTERNAL_MOVEMENT_TREATMENTS and internal:
        return None
    acct = map_account(cat_map or {}, accounts or {}, "cash", entry.get("category"))
    act = (acct or {}).get("cf_activity")
    if act in ("investing", "financing"):
        return act
    return "operating"


def cash_account_ids(bank_accounts):
    """哪些銀行帳戶算「現金」。None 進 → None 出（呼叫端沒給就別擋）。

    🔴 跟 split_bank_lines 是**同一條規則**：期初／期末只取 ["cash"]，迭代那側
    必須一致，否則股東往來上每記一筆就多一筆勾稽差額（v2.4.142 修過一次）。
    具名之後兩邊指向同一個述詞，加一個非現金桶（票據存款、履約保證專戶）時
    不會只改到一半。認不得的性質落到現金（同 split_bank_lines 的預設）。

    唯一的差別是**帳戶已被刪掉**的情況：那個 id 不在集合裡 → 迭代這側當非現金
    跳過。這是對的，因為餘額那側（bank_balances_asof）也是從 bank_accounts 生的，
    刪掉的帳戶連期初期末都不會出現 —— 兩側一起消失才平。
    """
    if bank_accounts is None:
        return None
    # 🔴 **卡片也不是現金**：split_bank_lines 把 CARD_KIND 另分一桶（負債由
    # card_outstanding 出），所以期初/期末的現金總額裡沒有它 —— 這裡漏排除的話
    # 迭代那側就會把刷卡列當成現金流動。
    # 2026-08-29 實測私帳：勾稽差額 1,508,164 剛好等於兩張卡的刷卡流量
    # （台新 −644,215 ＋ 富邦 −863,949）。以前沒炸是因為刷卡列多半是
    # treatment='transfer'、被「內部移動」那條擋掉了；改成「配得成對才算內部
    # 移動」之後它們就漏進來了 —— 這條規則本來就該與 split_bank_lines 一致，
    # 那支的 docstring 也是這樣寫的。
    return {b.get("id") for b in bank_accounts
            if not is_shareholder_kind(b.get("acct_kind"))
            and not is_card_kind(b.get("acct_kind"))}


#: 轉存配對允許的日期差。跨行當天到，但兩邊的對帳單常各記各的日期
#: （實測 2026-08：轉出記 08-09、轉入記 08-10）。
TRANSFER_PAIR_WINDOW_DAYS = 3

#: 沒有日期的列排最前面（舊資料有 entry_date 為空的）
_EPOCH = date(1970, 1, 1)

#: 銀行把一筆匯款退回時的字樣。認出來才不會被當成「配不到對手」一直叫 ——
#: 沖正的一進一出在**同一個帳戶**，本來就配不到轉出/轉入的對，但它們互相抵銷，
#: 數字沒有被扭曲。要有這個字樣才算，不能只看「同帳戶、同金額、方向相反」——
#: 那個形狀在真實帳上很常見（同日收一筆付一筆），亂認會把兩筆無關的錢抵掉。
REVERSAL_MARKERS = ("沖正", "退匯", "退回", "更正")


def _is_reversal(e) -> bool:
    return any(m in ((e.get("summary") or "") + (e.get("note") or ""))
               for m in REVERSAL_MARKERS)


def transfer_pairs(entries, *, window_days=TRANSFER_PAIR_WINDOW_DAYS,
                   tolerance=FEE_TOLERANCE):
    """帳戶間轉存的配對 → (pairs, unpaired_out, unpaired_in)。

    一筆跨行轉存在帳上是**兩列**：轉出帳戶一列支出、轉入帳戶一列存入。本金是
    內部移動（不算現金流量活動），但銀行收的跨行手續費是真的離開公司了。

    🔴 判準是「支出 vs 配對的存入」，不是「支出裡看起來有沒有零頭」：
      · 支出 == 存入            → 已經對了（手續費若有，已經分開記在 bank_fee）
      · 0 < 支出 − 存入 ≤ 容差   → 手續費**還埋在支出裡**，要拆出來
      · 其他                     → 配不到，交給人看，別猜

    這條規則被四個地方共用（對帳系統的卡片、一鍵認列、對帳單匯入的預帶、
    現金流量表那句提示）—— 各寫一份的話，畫面說「未成對」而按鈕說「沒事」。

    ⚠ 2026-08-24 差點出事：第一版判準是「支出裡含著手續費」，那只對**剛被手動
      加過匯費**的列成立；歷史 37 筆早就是支出＝本金、匯費分開記，照那個判準會
      被各減 15。所以一定要跟配對的那一列比。

    回的是 (pairs, unpaired_out, unpaired_in, reversals)。`reversals` ＝銀行把
    匯款退回的那種一進一出（同帳戶、同金額、摘要寫著沖正）—— 它們互相抵銷，
    不是問題，先撿出來才不會混在「配不到」裡一直叫。

    entries：dict 清單，要有 entry_date / deposit / expense / bank_fee /
    bank_account_id / id / summary。呼叫端負責只傳 treatment=='transfer' 的列。
    """
    def _d(e):
        """日期 → 可相減的 datetime/date。

        🔴 字串也要吃：`local_day` 對非 datetime 是原樣回，而這裡會做
        `(a - b).days` —— 餵字串就 TypeError。生產路徑一律是 DB 撈出來的
        datetime，所以這條路兩個月沒被踩到；2026-08-29 現金流量表改成
        「配得成對才算內部移動」之後，本函式被引擎每次都呼叫，
        任何一筆字串日期就會讓整張三表炸掉。
        """
        v = local_day(e.get("entry_date"))
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v[:19].replace("Z", ""))
            except ValueError:
                return None
        return v or None

    outs = [e for e in entries if int(e.get("expense") or 0) > 0]
    ins = [e for e in entries if int(e.get("deposit") or 0) > 0]

    # 0. 先撿沖正：同帳戶、同金額、方向相反，而且**有沖正字樣**（任一側寫著就算）
    reversals, rev_out, rev_in = [], set(), set()
    for oi, o in enumerate(outs):
        if oi in rev_out:
            continue
        for ii, cand in enumerate(ins):
            if ii in rev_in:
                continue
            if int(o.get("expense") or 0) != int(cand.get("deposit") or 0):
                continue
            if o.get("bank_account_id") != cand.get("bank_account_id"):
                continue
            od, idt = _d(o), _d(cand)
            if od and idt and abs((idt - od).days) > window_days:
                continue
            if not (_is_reversal(o) or _is_reversal(cand)):
                continue
            reversals.append({"out": o, "in": cand})
            rev_out.add(oi)
            rev_in.add(ii)
            break
    outs = [o for i, o in enumerate(outs) if i not in rev_out]
    ins = [c for i, c in enumerate(ins) if i not in rev_in]
    outs.sort(key=lambda e: (_d(e) or _EPOCH, int(e.get("expense") or 0)))
    used, pairs, unpaired_out = set(), [], []

    for o in outs:
        exp, od = int(o.get("expense") or 0), _d(o)
        best = None
        for i, cand in enumerate(ins):
            if i in used:
                continue
            dep, idt = int(cand.get("deposit") or 0), _d(cand)
            if od and idt and abs((idt - od).days) > window_days:
                continue
            # 同一個帳戶不會轉給自己（帳戶沒填就不擋 —— 舊資料很多沒填）
            if (o.get("bank_account_id") and cand.get("bank_account_id")
                    and o["bank_account_id"] == cand["bank_account_id"]):
                continue
            gap = exp - dep
            if not 0 <= gap <= tolerance:
                continue
            # 差額小的優先；同差額取日期近的
            key = (gap, abs(((idt - od).days if od and idt else 0)))
            if best is None or key < best[0]:
                best = (key, i, cand, gap)
        if best is None:
            unpaired_out.append(o)
            continue
        _k, idx, cand, gap = best
        used.add(idx)
        pairs.append({
            "out": o, "in": cand, "gap": gap,
            # 手續費還埋在支出裡 → 可以一鍵拆出來（總流出不變）
            "fee_inside": gap > 0,
        })
    unpaired_in = [c for i, c in enumerate(ins) if i not in used]
    return pairs, unpaired_out, unpaired_in, reversals


def paired_transfer_ids(cash_entries, cat_map, accounts) -> set:
    """配得成對的帳戶間轉存（含銀行退匯的沖正對）的 id 集合。

    「成對」＝錢搬到另一個**追蹤中的**帳戶，兩腳在現金總額裡互相抵銷，
    所以不列入活動分類；配不到對手的那些，錢是真的離開了現金池。
    配對規則走 `transfer_pairs`，與對帳系統的「帳戶間轉存」面板同一支 ——
    畫面說「這組成對了」而現金流量表卻當它沒成對，就是兩份規則各判各的。
    """
    moves = [e for e in cash_entries if is_account_move(e, cat_map, accounts)]
    pairs, _uo, _ui, revs = transfer_pairs(moves)
    ids = set()
    for grp in (pairs, revs):
        for p in grp:
            ids.add((p["out"] or {}).get("id"))
            ids.add((p["in"] or {}).get("id"))
    ids.discard(None)
    return ids


def cashflow_lines(cash_entries, months, *, cat_map=None, accounts=None,
                   bank_accounts=None):
    """現金流量表「哪幾列算數、各算多少」的**唯一正本** → (rows, stats)。

    rows：[{"entry", "activity", "amount", "treatment", "is_fee"}]
    stats：{"advance_net", "transfer_net", "unassigned", "noncash"}（給註記用）

    🔴 為什麼要有這一支：表上的數字（build_cashflow）與點進去的明細
    （statements_drilldown）本來各跑一次同一座階梯，於是每補一條規則就得記得
    改兩個地方。實際發生過兩次 ——
      · 非現金帳戶（股東往來）那條只加在表上 → 兩邊差 777,000（v2.4.146 修）
      · **轉存/預支的跨行手續費**：本金不算活動、但手續費是真的流出去的錢，
        表上算了、鑽取整筆跳過 → 2026 年兩邊差 150（十筆各 15 元）。
    這支存在之後，「哪幾列算數」只有一個答案，鑽取合計恆等於表上那格。
    """
    cat_map = cat_map or {}
    accounts = accounts or {}
    mset = set(months)
    cash_ids = cash_account_ids(bank_accounts)
    # 🔴 哪幾列是**真的內部移動**：配得成對的轉存（兩腳都在追蹤中的帳戶）。
    # 配對在整份收支上做、不限本期 —— 一筆轉存的兩腳可能跨月，只看本期會把
    # 期界上的那幾組判成「配不到」。
    paired = paired_transfer_ids(cash_entries, cat_map, accounts)
    rows = []
    stats = {"advance_net": 0, "transfer_net": 0, "unassigned": 0, "noncash": 0}
    for e in cash_entries:
        if month_of(e.get("entry_date")) not in mset:
            continue
        if not e.get("bank_account_id"):
            stats["unassigned"] += 1        # 不影響任何帳戶餘額
            continue
        if cash_ids is not None and e["bank_account_id"] not in cash_ids:
            stats["noncash"] += 1           # 股東往來等：不在現金總額裡
            continue
        t = classify_cash_entry(e, cat_map)
        internal = t == "advance" or e.get("id") in paired
        if t in INTERNAL_MOVEMENT_TREATMENTS and internal:
            principal = in_amount(e) - out_amount(e)
            stats["advance_net" if t == "advance" else "transfer_net"] += principal
            fee = int(e.get("bank_fee") or 0)
            if fee:
                # 本金是內部移動，手續費不是 —— 那筆錢真的離開公司了
                rows.append({"entry": e, "activity": "operating", "amount": -fee,
                             "treatment": t, "is_fee": True})
            continue
        rows.append({"entry": e,
                     "activity": cash_entry_activity(e, cat_map, accounts,
                                                     internal=internal),
                     "amount": cash_entry_flow(e), "treatment": t, "is_fee": False})
    return rows, stats


def build_cashflow(months, *, opening, closing, cash_entries=(),
                   cat_map=None, accounts=None, bank_accounts=None) -> dict:
    """現金流量表（直接法）。opening/closing = {"total", "by_account":[{name,amount}]}
    由 caller 以 bank_balances_asof 算（期間前一月月底 / 期末月月底）。

    規則：
    - 只計「有掛**現金類**帳戶」的收支。兩種都要排除，理由相同 ——
      它們不影響 opening/closing 的現金總額，計入就破壞恆等式：
        · 未掛帳戶（不影響任何帳戶餘額）→ 排除 + note 提醒；損益表則照計。
        · 🔴 掛在**非現金帳戶**上的（股東往來 shareholder_loan/capital）——
          期初/期末只取 split_bank_lines(...)["cash"]，這些帳戶根本不在裡面。
          🔴 帳戶清單直接吃 `bank_accounts`，**「什麼算現金」的判定就只有這一份**
          （跟 split_bank_lines 同一個述詞）。本來是呼叫端自己再做一次
          `not is_shareholder_kind(...)` 的 comprehension —— 那等於同一條規則
          兩份，之後多一個非現金桶（票據存款、履約保證專戶）時它會從期初期末
          消失、卻不會從迭代裡消失，差額靜靜長出來。
          不給 `bank_accounts` 時退回舊行為（只擋未掛帳戶），給舊呼叫端與單元測試用。
    - transfer/advance 本金不列入活動（規格：內部移動）；其 bank_fee 是真實
      流出 → 計入 operating。轉存若兩邊成對登記，本金跨帳戶互抵不影響總額；
      未成對差額與預支往來淨流都寫進 check.notes 解釋 diff 來源。
    - 每筆流量 = cash_entry_flow（deposit − expense − bank_fee − claim），
      與餘額推導同一公式 → 乾淨帳（無預支/未成對轉存）時恆等式自然成立。
    - 器材購入不另計：規格 v1 決策 — 現金流全部由收支明細 classify 派生，
      科目 cf_activity=investing 者（如對映到 1500 的 category）自然落
      investing；直接用 equipment 表另計會與收支重複。
    - 自檢：check.diff = closing.total − opening.total − net，≠0 誠實外顯。
    """
    acts = {"operating": 0, "investing": 0, "financing": 0}
    rows, stats = cashflow_lines(cash_entries, months, cat_map=cat_map,
                                 accounts=accounts, bank_accounts=bank_accounts)
    for r in rows:
        acts[r["activity"]] += r["amount"]
    advance_net = stats["advance_net"]
    transfer_net = stats["transfer_net"]
    unassigned = stats["unassigned"]
    noncash = stats["noncash"]
    net = acts["operating"] + acts["investing"] + acts["financing"]
    # closing − (opening + net)：正 = 期末實際比活動推算多
    diff = int(closing.get("total") or 0) - (int(opening.get("total") or 0) + net)
    notes = []
    if advance_net:
        notes.append(f"員工預支往來淨流 {advance_net:+,} 元未列入活動分類（內部移動）")
    if transfer_net:
        notes.append(f"帳戶間轉存未完全成對，差額 {transfer_net:+,} 元")
    if unassigned:
        notes.append(f"{unassigned} 筆未掛帳戶收支未列入")
    if noncash:
        # 卡片列佔大宗（刷卡當下不動銀行，錢是繳卡費那天才出去）—— 只寫
        # 「股東往來」會讓人以為帳有問題，實際上那是對的
        notes.append(f"{noncash} 筆掛在非現金帳戶（信用卡／股東往來）的收支未列入現金流"
                     "——刷卡當下沒有動到銀行，錢在繳卡費那天才出去")
    return {"opening": opening, "closing": closing,
            "operating": acts["operating"], "investing": acts["investing"],
            "financing": acts["financing"], "net": net,
            "drills": dict(CF_ACTIVITY_DRILLS),
            "check": {"diff": diff, "notes": notes}}


def merge_cf(parts, opening, closing) -> dict:
    """多份現金流量表（鎖定月快照 + live 期間）合併：三活動線性相加；
    opening/closing 由 caller 以整段期間重算傳入（餘額推導不受鎖月影響）；
    check.diff 以合併後數字重算；notes 去重串接。"""
    acts = {"operating": 0, "investing": 0, "financing": 0}
    notes = []
    for p in parts:
        for k in acts:
            acts[k] += int(p.get(k) or 0)
        for line in ((p.get("check") or {}).get("notes") or []):
            if line not in notes:
                notes.append(line)
    net = acts["operating"] + acts["investing"] + acts["financing"]
    diff = int(closing.get("total") or 0) - int(opening.get("total") or 0) - net
    return {"opening": opening, "closing": closing, **acts, "net": net,
            "drills": dict(CF_ACTIVITY_DRILLS),
            "check": {"diff": diff, "notes": notes}}


# ── 檢核警示 + 白話解讀 ──────────────────────────────────────

def statement_warnings(cash_entries, payments, cat_map, months=None,
                       loan_payments=()) -> dict:
    """報表品質警示：未歸類（cash+payment category 查無對映）、未掛帳戶、
    未填日期（undated 不受期間過濾 — 沒日期本來就進不了任何期間）、
    貸款繳款落在攤還表涵蓋範圍外。

    🔴 最後那條的來歷：利息費用是**按攤還表的 due_date 權責認列**
    （iter_loan_interest），攤還表沒有那個月的期別就認列 0。而貸款建檔時很容易
    只填「從下期起的剩餘期數」—— 2026-08-24 實測，五筆貸款的攤還表全都是從
    2026-09 起算，帳上 2024-03 ~ 2026-08 共 30 個月、每月都真的在繳息，損益表的
    利息費用卻整段是空的，而且沒有任何一張表出過聲（現金流照樣勾稽為 0，因為
    繳款走 financing 那側跟攤還表無關）。錢真的出去了、帳上也記了，只有損益表
    看不到 —— 這種缺口不會自己浮出來，只能明講。
    """
    mset = set(months) if months is not None else None
    covered = {m for m in (month_of(p.get("due_date")) for p in (loan_payments or ()))
               if m}
    unmapped = unassigned = undated = 0
    loan_gap_months = set()
    for e in cash_entries:
        m = month_of(e.get("entry_date"))
        if m is None:
            undated += 1
            continue
        if mset is not None and m not in mset:
            continue
        treatment = classify_cash_entry(e, cat_map or {})
        if treatment == "unmapped":
            unmapped += 1
        # 刷卡列（status='card'）**刻意**不掛帳戶 —— 刷卡當下不動銀行，
        # 錢由月底還款離開、債掛在 BS 的「信用卡未繳」。把它們算進「未掛帳戶」
        # 會讓私帳永遠掛著一條四位數的假警語，真的忘了掛的列反而被淹掉。
        if not e.get("bank_account_id") and (e.get("status") or "") != "card":
            unassigned += 1
        # 只看流出：撥款（存入）落在攤還表首期之前是正常的，繳款不是
        if treatment == "loan" and int(e.get("expense") or 0) > 0 and m not in covered:
            loan_gap_months.add(m)
    for p in payments:
        if p.get("is_advance"):
            continue
        m = month_of(p.get("request_date"))
        if mset is not None and (m is None or m not in mset):
            continue
        if not map_info(cat_map or {}, "payment", p.get("category")):
            unmapped += 1
    messages = []
    if unmapped:
        messages.append(f"{unmapped} 筆收支/請款尚未歸類科目（暫列未歸類）")
    if unassigned:
        messages.append(f"{unassigned} 筆收支未掛銀行帳戶（不列入現金流量表）")
    if undated:
        messages.append(f"{undated} 筆收支未填日期（無法定位月份，不列入報表）")
    if loan_gap_months:
        span = (f"{min(loan_gap_months)}" if len(loan_gap_months) == 1
                else f"{min(loan_gap_months)} ~ {max(loan_gap_months)}")
        messages.append(
            f"{len(loan_gap_months)} 個月的貸款繳款落在攤還表涵蓋範圍外（{span}）"
            f"—— 這些月份的利息費用不會認列，請把貸款的期數/首期繳款日補成"
            f"實際的生命週期")
    return {"unmapped": unmapped, "unassigned": unassigned, "undated": undated,
            "loan_gap_months": sorted(loan_gap_months), "messages": messages}


def statement_interpretation(pnl, bs, cf, *, ar_over_60=0) -> list:
    """三表白話解讀（規則句，資料不足的句子不出，上限 8 條）。"""
    out = []
    rev = pnl["revenue"]["total"]
    if rev > 0:
        gross = pnl["gross"]["amount"]
        out.append(f"本期每收 100 元營收，付完直接製作成本剩約 {round(gross * 100 / rev)} 元"
                   f"（毛利率 {pnl['gross']['rate']}%）")
        net = pnl["net"]["amount"]
        if net >= 0:
            out.append(f"扣完全部開銷與稅後，每 100 元營收約留下 {round(net * 100 / rev)} 元"
                       f"（淨利率 {pnl['net']['rate']}%）")
        else:
            out.append(f"本期淨虧損 {abs(net):,} 元 — 支出大於收入")
        recv = pnl["revenue"]["by_collection"].get("receivable") or 0
        if recv > 0:
            out.append(f"本期營收中還有 {recv:,} 元未收款（占 {_pct(recv, rev)}%），"
                       "現金還沒真的進來")
    cash_total = sum(x["amount"] for x in (bs or {}).get("assets", {}).get("current", [])
                     if str(x.get("key", "")).startswith("cash"))
    monthly_spend = pnl["monthly_avg"]["cost"] + pnl["monthly_avg"]["opex"]
    if cash_total > 0 and monthly_spend > 0:
        out.append(f"帳上現金 {cash_total:,} 元，約可支撐 "
                   f"{round(cash_total / monthly_spend, 1)} 個月的平均開銷")
    if ar_over_60 and ar_over_60 > 0:
        out.append(f"應收帳款中有 {ar_over_60:,} 元開立超過 60 天未收，建議優先催收")
    op = (cf or {}).get("operating") or 0
    if op:
        out.append(f"本期營運現金流 {op:+,} 元"
                   f"（{'日常營運有淨現金流入' if op > 0 else '日常營運正在消耗現金'}）")
    if bs:
        dl = bs["ratios"]["labels"].get("debt_ratio")
        if dl:
            out.append(dl)
        if bs["check"]["diff"]:
            out.append(f"資產負債表檢核差額 {bs['check']['diff']:,} 元，"
                       "帳務尚有未對齊項目，數字解讀請保留餘裕")
    return out[:8]


# ═════════════════════════════════════════════════════════════════
# 階段五：儀表板 / 稅務包純函式（零 DB，配黃金測試）
# ═════════════════════════════════════════════════════════════════

# AR 帳齡固定五桶（key, 中文 label；順序 = 前端呈現順序）
_AGING_BUCKETS = (
    ("current", "未逾期"),
    ("d1_30", "1-30 天"),
    ("d31_60", "31-60 天"),
    ("d61_90", "61-90 天"),
    ("over90", "90 天以上"),
)


def _month_end_date(month: str) -> date:
    """'YYYY-MM' → 該月月底 date（帳齡基準日）。"""
    y, m = int(month[:4]), int(month[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def aging_buckets(open_invoices, as_of_month: str) -> dict:
    """AR 帳齡分桶（未收款發票以 invoice_date 到 as_of 月底的天數分桶）。

    open_invoices = 未收款發票 dict 列（含 invoice_date（datetime/str）、
    amount_total（含稅）；金額用 amount_total）。天數 =（as_of 月底 − 開票日）：
    ≤0 天 current（當期未逾期）、1-30 d1_30、31-60 d31_60、61-90 d61_90、
    >90 over90。日期一律過 local_day（timestamptz 回讀 UTC → 本地）再取日；
    無法解析日期的列不計（也不進 total）。
    回 {"buckets":[{key,label,amount,count}...(固定五桶順序)], "total": int}。
    """
    ref = _month_end_date(as_of_month)
    agg = {k: {"amount": 0, "count": 0} for k, _ in _AGING_BUCKETS}
    for inv in open_invoices:
        d = _as_date(local_day(inv.get("invoice_date")))
        if d is None:
            continue
        days = (ref - d).days
        if days <= 0:
            key = "current"
        elif days <= 30:
            key = "d1_30"
        elif days <= 60:
            key = "d31_60"
        elif days <= 90:
            key = "d61_90"
        else:
            key = "over90"
        agg[key]["amount"] += int(inv.get("amount_total") or 0)
        agg[key]["count"] += 1
    buckets = [{"key": k, "label": lb, "amount": agg[k]["amount"],
                "count": agg[k]["count"]} for k, lb in _AGING_BUCKETS]
    return {"buckets": buckets, "total": sum(b["amount"] for b in buckets)}


def client_concentration(revenue_by_client, *, top=3) -> dict:
    """客戶集中度（revenue_by_client = {client_name: amount}，services 端已加總）。

    clients 依金額 desc；pct = amount/total*100（百分比數字，1 位小數）。
    top_n_pct = 前 top 家金額佔比；warn = top_n_pct > 50。
    total=0 → 各 pct 0.0、top_n_pct=0.0、warn False。
    回 {"clients":[{name,amount,pct}...], "top_n":top, "top_n_pct":float, "warn":bool}。
    """
    total = sum(int(v or 0) for v in revenue_by_client.values())
    clients = sorted(
        ({"name": name, "amount": int(amt or 0),
          "pct": round(int(amt or 0) * 100 / total, 1) if total else 0.0}
         for name, amt in revenue_by_client.items()),
        key=lambda x: -x["amount"])
    top_amount = sum(c["amount"] for c in clients[:top])
    top_n_pct = round(top_amount * 100 / total, 1) if total else 0.0
    return {"clients": clients, "top_n": top, "top_n_pct": top_n_pct,
            "warn": top_n_pct > 50}


def runway_months(cash_total, avg_monthly_net) -> float | None:
    """現金跑道（月）：帳上現金還能支撐幾個月的平均淨消耗。

    avg_monthly_net ≥ 0（不燒錢）→ None（前端顯示「充裕/無限」）；
    否則 round(cash_total / (-avg_monthly_net), 1)；cash_total ≤ 0 → 0.0。
    """
    if (avg_monthly_net or 0) >= 0:
        return None
    if (cash_total or 0) <= 0:
        return 0.0
    return round(cash_total / (-avg_monthly_net), 1)
