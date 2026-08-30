"""財務純邏輯 · 損益表與資產負債表（只 import `_core`）。

build_pnl / merge_pnl / _finalize_pnl（權責認列，locked 月快照可加總）、
transfer 流量的「位置」判定（兩本帳 §8）、build_balance_sheet（推導式，
check.diff 誠實外顯）、apply_ledger_project_costs（私帳逐案改寫）。

對外一律從 `core.finance_logic` 匯入，別直接指名這個檔。
"""
from __future__ import annotations

import json
from ._core import (COST_GROUPS, OPEX_GROUPS, _ADVANCE_EXPENSE_LABEL, _BANK_FEE_LABEL, _DEPRECIATION_LABEL, _LOAN_INTEREST_LABEL, _UNMAPPED_INCOME_LABEL, _pct, bank_fee_total, classify_cash_entry, depreciation_rows, equipment_net_rows, in_amount, invoice_collected, invoice_ex_tax, is_passthrough_category, iter_expense_items, iter_revenue_invoices, loan_interest_total, map_account, month_of, out_amount, paired_expense_category, passthrough_fee_income, vat_position)

# ── 損益表 ───────────────────────────────────────────────────

def _new_pnl_prim() -> dict:
    """損益表中間彙總（可加總的原始桶 — build 與 merge 共用 finalize）。"""
    return {
        "revenue": {},   # key -> {"label", "amount"}
        "by_collection": {"collected": 0, "receivable": 0, "cash": 0},
        "cost": {},      # group -> {label: amount}
        "opex": {},
        "nonop_income": {},   # label -> amount
        "nonop_expense": {},
        "income_tax": 0,
        "vat": {"output": 0, "input": 0, "paid": 0},
    }


def _bump(d: dict, key: str, amount: int) -> None:
    d[key] = d.get(key, 0) + amount


def _bump_line(d: dict, key: str, label: str, amount: int) -> None:
    row = d.setdefault(key, {"label": label, "amount": 0})
    row["amount"] += amount


def _dispatch_slot(prim: dict, group: str, label: str, amount: int) -> None:
    """expense_slot / iter_expense_items 判定好的 (group, label, amount) 入桶。"""
    if not amount:
        return
    if group == "業外支出":
        _bump(prim["nonop_expense"], label, amount)
    elif group == "稅":
        prim["income_tax"] += amount
    elif group in COST_GROUPS:
        _bump(prim["cost"].setdefault(group, {}), label, amount)
    else:
        _bump(prim["opex"].setdefault(group, {}), label, amount)


def _dispatch_income(prim: dict, acct: dict | None, amount: int) -> None:
    if not amount:
        return
    g = (acct or {}).get("pnl_group")
    label = (acct or {}).get("name") or _UNMAPPED_INCOME_LABEL
    if g == "營業收入":
        _bump_line(prim["revenue"], "cash", "現金收入（未開票）", amount)
        prim["by_collection"]["cash"] += amount
    elif g == "業外收入":
        _bump(prim["nonop_income"], label, amount)
    else:
        _bump(prim["nonop_income"], _UNMAPPED_INCOME_LABEL, amount)


def build_pnl(months, *, invoices=(), payments=(), cash_entries=(), equipment=(),
              advance_expenses=(), loan_payments=(), cat_map=None,
              accounts=None) -> dict:
    """損益表（權責認列，期間 = 月集合；各來源在函式內按月過濾）。

    認列規則（階段三規格落地）：
    - 營業收入 = 收款發票（payment_type=收款、issue_status≠作廢、category=專案）
      未稅額 by invoice_date 月；by_collection 依收現狀態拆 已收/應收，
      另加 direct_income 現金收款（對映到營業收入科目、未開票）單列 cash。
    - 業外收入 = 代開發票的**淨手續費**（面額 − 應匯 − 銷項稅，
      見 passthrough_fee_income；**不是** commission 欄，那欄是要匯出去的錢）
      + direct_income 收支按對映科目（利息收入等）。
    - 營業成本/費用 = ①請款單（非 is_advance，by request_date，category 走
      source='payment' 對映；transfer/passthrough 不計）②direct_expense 收支
      （by entry_date，source='cash' 對映）③器材月折舊（直線法 → 營業成本-費）
      ④預支核銷支出（見下）。
    - 去重鐵則：invoice_id/payment_request_id/advance_payment_id 硬連結的收支
      是 AR/AP/預支的「現金結清動作」不再計損益（權責認列點在發票/請款）；
      transfer（轉存）/passthrough（代開過水）/loan（貸款撥款/繳款）也不進損益。
    - 利息費用（階段四）：權責按攤還表 due_date 認列進業外支出（不管繳沒繳）
      — loan_payments 由 caller 餵 finance_loan_payments 全表；貸款繳款收支
      （treatment='loan'）只走現金流量表（科目 2400 cf_activity=financing）。
    - 預支核銷支出（CrmProjectExpense 查證結論，2026-07-11）：
      crm_project_expenses 有 advance_id 軟 FK、無支出日期欄（僅 created_at）。
      「有掛 advance_id」的支出明細：其現金對應（發款收支）treatment='advance'
      已被排除在損益外 → 計入營業成本-費（by created_at 月）不會重複。
      「未掛 advance_id」的專案雜支多與請款單/收支明細重疊（同筆錢兩處登記）
      → v1 不計，避免重複計算；caller（service）只餵 advance 掛鉤列。
    - 匯費（bank_fee）：任何收支（含 transfer/advance）的匯費都是真實費用，
      彙總成「營業費用-管理／銀行手續費」單列。
    - 未歸類：direct_expense 查無科目 → 營業費用-管理／未歸類支出；
      未歸類收入 → 業外收入／未歸類收入（statement_warnings 另計數提醒）。
    - 稅區：income_tax = tax_income 收支（近似法 — 以繳納現金入帳月認列，
      非申報所屬年度）；vat_info 為資訊列（不進損益小計）。
    - direct_expense 的 deposit（退款）沖回同科目；direct_income 的支出亦然。

    輸出 shape 見 CLAUDE/前端契約（revenue/cost/gross/opex/operating/
    non_operating/pretax/tax/net/monthly_avg）。rate 均為百分比 1 位小數，
    營收 0 時為 None。
    """
    cat_map = cat_map or {}
    accounts = accounts or {}
    mset = set(months)
    prim = _new_pnl_prim()

    for inv in iter_revenue_invoices(invoices, mset):
        ex = invoice_ex_tax(inv)
        _bump_line(prim["revenue"], "invoiced", "開立發票營收", ex)
        key = "collected" if invoice_collected(inv) else "receivable"
        prim["by_collection"][key] += ex

    for inv in invoices:  # 代開手續費（業外收入）
        if (inv.get("issue_status") or "") == "作廢":
            continue
        if month_of(inv.get("invoice_date")) not in mset:
            continue
        if is_passthrough_category(inv.get("category") or "專案"):
            # 🔴 不是 commission —— 那欄是「要匯出去的錢」。見 passthrough_fee_income。
            c = passthrough_fee_income(inv)
            if c:
                _bump(prim["nonop_income"], "代開手續費（已扣銷項稅）", c)

    # 費用側（請款 + direct_expense/unmapped/tax_income 收支）單一迭代來源
    for _src, _row, group, label, amount in iter_expense_items(
            payments, cash_entries, cat_map, accounts, mset):
        _dispatch_slot(prim, group, label, amount)

    for e in cash_entries:  # 收入側（direct_income + unmapped 的 deposit）
        if month_of(e.get("entry_date")) not in mset:
            continue
        t = classify_cash_entry(e, cat_map)
        if t == "direct_income":
            acct = map_account(cat_map, accounts, "cash", e.get("category"))
            _dispatch_income(prim, acct, in_amount(e))
            # 有「支出版」對映時，支出側已由 iter_expense_items 認列成費用，
            # 這裡再沖一次營收就會雙算（見 paired_expense_category）
            if not paired_expense_category(e.get("category"), cat_map):
                _dispatch_income(prim, acct, -out_amount(e))
        elif t == "unmapped":
            _dispatch_income(prim, None, in_amount(e))

    fee = bank_fee_total(cash_entries, mset)
    if fee:
        _bump(prim["opex"].setdefault("營業費用-管理", {}), _BANK_FEE_LABEL, fee)

    dep = sum(r["amount"] for r in depreciation_rows(equipment, months))
    if dep:
        _bump(prim["cost"].setdefault("營業成本-費", {}), _DEPRECIATION_LABEL, dep)

    adv = sum(int(x.get("amount") or 0) for x in advance_expenses
              if month_of(x.get("date")) in mset)
    if adv:
        _bump(prim["cost"].setdefault("營業成本-費", {}), _ADVANCE_EXPENSE_LABEL, adv)

    li = loan_interest_total(loan_payments, mset)
    if li:
        _bump(prim["nonop_expense"], _LOAN_INTEREST_LABEL, li)

    v = vat_position(invoices, cash_entries, cat_map, months)
    prim["vat"] = {"output": v["output"], "input": v["input"], "paid": v["paid"]}
    return _finalize_pnl(prim, max(len(mset), 1))


def _finalize_pnl(prim: dict, n_months: int) -> dict:
    rev_lines = [{"key": k, "label": r["label"], "amount": r["amount"]}
                 for k, r in prim["revenue"].items() if r["amount"]]
    rev_lines.sort(key=lambda x: -x["amount"])
    revenue_total = sum(x["amount"] for x in rev_lines)

    def _groups(bucket, group_names, drill_side):
        out, total = [], 0
        for g in group_names:
            lines = [{"label": lb, "amount": a}
                     for lb, a in (bucket.get(g) or {}).items() if a]
            lines.sort(key=lambda x: -x["amount"])
            gt = sum(x["amount"] for x in lines)
            label = g.split("-", 1)[-1]
            out.append({"group": g, "label": label, "total": gt, "lines": lines,
                        "drill": f"{drill_side}.{label}"})  # ∈ VALID_DRILL_KINDS
            total += gt
        return out, total

    cost_groups, cost_total = _groups(prim["cost"], COST_GROUPS, "cost")
    opex_groups, opex_total = _groups(prim["opex"], OPEX_GROUPS, "opex")
    gross = revenue_total - cost_total
    operating = gross - opex_total
    nonop_inc = sorted(({"label": k, "amount": a}
                        for k, a in prim["nonop_income"].items() if a),
                       key=lambda x: -x["amount"])
    nonop_exp = sorted(({"label": k, "amount": a}
                        for k, a in prim["nonop_expense"].items() if a),
                       key=lambda x: -x["amount"])
    nonop_total = sum(x["amount"] for x in nonop_inc) - sum(x["amount"] for x in nonop_exp)
    pretax = operating + nonop_total
    income_tax = prim["income_tax"]
    net = pretax - income_tax
    v = prim["vat"]
    return {
        "revenue": {"total": revenue_total, "lines": rev_lines,
                    "by_collection": dict(prim["by_collection"]),
                    "drill": "revenue"},
        "cost": {"total": cost_total, "groups": cost_groups},
        "gross": {"amount": gross, "rate": _pct(gross, revenue_total)},
        "opex": {"total": opex_total, "groups": opex_groups},
        "operating": {"amount": operating, "rate": _pct(operating, revenue_total),
                      "expense_rate": _pct(opex_total, revenue_total)},
        "non_operating": {"income": nonop_inc, "expense": nonop_exp,
                          "total": nonop_total},
        "pretax": pretax,
        "tax": {"income_tax": income_tax,
                "vat_info": {"output": v["output"], "input": v["input"],
                             "paid": v["paid"],
                             "net": v["output"] - v["input"] - v["paid"]}},
        "net": {"amount": net, "rate": _pct(net, revenue_total)},
        "monthly_avg": {"revenue": round(revenue_total / n_months),
                        "cost": round(cost_total / n_months),
                        "opex": round(opex_total / n_months)},
    }


def restate_revenue_accrual(pnl: dict, accrual_revenue: int, *,
                            cash_revenue: int | None = None, n_months: int = 0) -> dict:
    """把一份已 finalize 的損益表換成**權責**營收，並讓底下所有小計/比率跟著走。

    為什麼要有這支：私帳的年度表是權責口徑（本期**結案**案的合約合計），
    系統原本逐筆按入帳日認列＝現金口徑。同一本帳、同一段期間，兩個口徑差的是
    應收與跨期收款 —— 不是誰算錯（2026-08-27 三年逐一核對，權責數字與 owner
    的表分毫不差：5,928,950／6,955,899／8,103,670）。

    只換營收那一列與其下游（毛利/營益/稅前/稅後/比率/月均）；成本、費用、
    業外、稅一律不動。現金認列數保留在 by_collection.cash，兩個口徑都看得到。
    """
    out = dict(pnl)
    rev = dict(pnl.get("revenue") or {})
    cash = int(rev.get("total") or 0) if cash_revenue is None else int(cash_revenue)
    total = int(accrual_revenue or 0)
    by_col = dict(rev.get("by_collection") or {})
    by_col["cash"] = cash
    rev.update({"total": total, "basis": "accrual",
                "lines": [{"key": "accrual", "label": "本期結案案（合約合計）",
                           "amount": total}] if total else [],
                "by_collection": by_col})
    out["revenue"] = rev
    cost_total = int((pnl.get("cost") or {}).get("total") or 0)
    opex_total = int((pnl.get("opex") or {}).get("total") or 0)
    gross = total - cost_total
    operating = gross - opex_total
    pretax = operating + int((pnl.get("non_operating") or {}).get("total") or 0)
    net = pretax - int((pnl.get("tax") or {}).get("income_tax") or 0)
    out["gross"] = {"amount": gross, "rate": _pct(gross, total)}
    out["operating"] = {"amount": operating, "rate": _pct(operating, total),
                        "expense_rate": _pct(opex_total, total)}
    out["pretax"] = pretax
    out["net"] = {"amount": net, "rate": _pct(net, total)}
    n = n_months or _implied_months(pnl, cash)
    if n:
        avg = dict(pnl.get("monthly_avg") or {})
        avg["revenue"] = round(total / n)
        out["monthly_avg"] = avg
    return out


_PROJECT_CASH_MIRROR_LABEL = "專案雜支"
_OUTSOURCE_LABEL = "委外費用"
_PROJECT_MISC_LABEL = "專案雜支（逐案）"


def apply_ledger_project_costs(pnl: dict, *, outsource: int = 0, tax: int = 0,
                               misc: int = 0) -> dict:
    """私帳：成本的委外/雜支與稅改用**逐案（權責）**的數，並拿掉現金那一面。

    owner 的年度表這三項全是從專案管理表逐案加總的（2026-08-27 三年核對全中）。
    🔴 收支明細裡「公司_專案」的支出列＝匯給外包與繳稅的**現金那一面**，
    引擎會把它們認成「專案雜支」成本 —— 與這裡的權責數是同一批錢，
    兩邊都留就是重複計（FY2025 會多算 1,118,775）。所以先把那條成本行拿掉。

    稅：`income_tax` 直接換成逐案稅款（發票代辦費＋報稅案個人稅款）。系統原本
    靠收支的 tax_income 對映認稅，私帳沒有那種列 → 一直是 0。
    """
    out = dict(pnl)
    cost = dict(pnl.get("cost") or {})
    groups = []
    for g in cost.get("groups") or ():
        g = dict(g)
        g["lines"] = [ln for ln in (g.get("lines") or ())
                      if ln.get("label") != _PROJECT_CASH_MIRROR_LABEL]
        if g["label"] == "工":
            if outsource:
                g["lines"] = list(g["lines"]) + [{"label": _OUTSOURCE_LABEL,
                                                  "amount": outsource}]
        elif g["label"] == "費":
            if misc:
                g["lines"] = list(g["lines"]) + [{"label": _PROJECT_MISC_LABEL,
                                                  "amount": misc}]
        g["lines"].sort(key=lambda x: -x["amount"])
        g["total"] = sum(int(x["amount"]) for x in g["lines"])
        groups.append(g)
    cost["groups"] = groups
    cost["total"] = sum(g["total"] for g in groups)
    out["cost"] = cost

    revenue = int((pnl.get("revenue") or {}).get("total") or 0)
    opex_total = int((pnl.get("opex") or {}).get("total") or 0)
    gross = revenue - cost["total"]
    operating = gross - opex_total
    pretax = operating + int((pnl.get("non_operating") or {}).get("total") or 0)
    tax_block = dict(pnl.get("tax") or {})
    tax_block["income_tax"] = int(tax or 0)
    out["tax"] = tax_block
    out["gross"] = {"amount": gross, "rate": _pct(gross, revenue)}
    out["operating"] = {"amount": operating, "rate": _pct(operating, revenue),
                        "expense_rate": _pct(opex_total, revenue)}
    out["pretax"] = pretax
    out["net"] = {"amount": pretax - int(tax or 0),
                  "rate": _pct(pretax - int(tax or 0), revenue)}
    avg = dict(pnl.get("monthly_avg") or {})
    n = _implied_months(pnl, int((pnl.get("revenue") or {}).get("by_collection", {})
                                 .get("cash") or 0)) or 0
    if avg.get("cost") and cost["total"] and n:
        avg["cost"] = round(cost["total"] / n)
        out["monthly_avg"] = avg
    return out


def _implied_months(pnl: dict, cash_revenue: int) -> int:
    """從既有 monthly_avg 反推期間月數（caller 沒給 n_months 時的備援）。"""
    avg = int((pnl.get("monthly_avg") or {}).get("revenue") or 0)
    if not avg or not cash_revenue:
        return 0
    return max(1, round(cash_revenue / avg))


def merge_pnl(parts, n_months: int) -> dict:
    """多份已 finalize 的損益表（鎖定月快照 + live 期間）合併為一份。

    行金額線性可加 → 走「拆回原始桶 → 重新 finalize」路，比率/月均以合併後
    總額重算（n_months = 整段期間月數，含快照月）。單一 part 也可過（等於
    以 n_months 重算月均）。
    """
    prim = _new_pnl_prim()
    for p in parts:
        for ln in p["revenue"]["lines"]:
            _bump_line(prim["revenue"], ln.get("key") or ln["label"],
                       ln["label"], ln["amount"])
        bc = p["revenue"].get("by_collection") or {}
        for k in ("collected", "receivable", "cash"):
            prim["by_collection"][k] += int(bc.get(k) or 0)
        for side in ("cost", "opex"):
            for g in p[side]["groups"]:
                for ln in g["lines"]:
                    _bump(prim[side].setdefault(g["group"], {}),
                          ln["label"], ln["amount"])
        for ln in p["non_operating"]["income"]:
            _bump(prim["nonop_income"], ln["label"], ln["amount"])
        for ln in p["non_operating"]["expense"]:
            _bump(prim["nonop_expense"], ln["label"], ln["amount"])
        prim["income_tax"] += int(p["tax"]["income_tax"] or 0)
        vi = p["tax"].get("vat_info") or {}
        for k in ("output", "input", "paid"):
            prim["vat"][k] += int(vi.get(k) or 0)
    return _finalize_pnl(prim, max(n_months, 1))


# ── transfer 流量的「位置」（兩本帳 §8，2026-08-25）──────────────
#
# treatment='transfer' 把一筆錢從損益排除 —— 但錢沒有消失，它去了某個**位置**：
# 業主往來（權益）、員工往來-預支（資產）、器材（資產，走清冊另計）、
# 銀行互轉（現金內移動，無位置）。修正前引擎只做了「排除」這一半，位置全部
# 沒記 —— owner 私帳的 BS 因此差了 -7,188,302（生活/家用/股利等 transfer 流出
# 幾百萬，權益的「業主往來」卻掛 0）。
#
# 🔴 對母公司是空操作（實測 parent 的 transfer 類別對到 equity/員工往來科目的
# 列數 = 0；parent 的股東流動走 split_bank_lines 的帳戶機制，是另一條路）。


#: 科目名 → 位置桶。equity 由 acct_type 判（業主往來、期初權益…都算），
#: 其餘按科目名指認 —— 同為 asset 的器材設備走清冊、銀行存款是現金內移動，
#: 「asset 一律入位置」是錯的（見 equity_transfer_position 的說明）。
_TRANSFER_POSITION_BY_NAME = {
    "員工往來-預支": "advance",
    "家用往來": "household",
    "器材設備": "capital",
    "其他金融資產": "financial",
}

#: 配對面板的範圍：錢**搬到另一個地方**而不是被花掉／被提走的那兩桶。
#: bank＝銀行之間互轉、financial＝換匯／定存／證券／沒追蹤的銀行。
#: 🔴 少了這一層，「不進損益」的私人開銷（業主往來 2,039 筆、家用往來 910 筆）
#: 全都會變成配對候選 —— 2026-08-29 實測配出 48 組假的轉存（$136 配 $90 說
#: 手續費 46）。owner：「你表列的這些都不是互轉」。
ACCOUNT_MOVE_POSITIONS = frozenset({"bank", "financial"})


def transfer_position(acct) -> str:
    """一筆 `treatment='transfer'` 的錢**去了哪裡** → 位置桶。

    'equity' 業主往來（提取／注入）／'advance' 員工預支／'household' 家用代墊／
    'capital' 器材／'financial' 其他金融資產／'bank' 銀行之間互轉（無位置）。

    這份分類服務兩件事：資產負債表要把錢記在對的位置（equity_transfer_position），
    轉存配對面板要知道哪些列才算「帳戶間移動」（ACCOUNT_MOVE_POSITIONS）。
    各判各的就會漂 —— 加一個位置科目時只有其中一邊會跟上。
    """
    acct = acct or {}
    if acct.get("acct_type") == "equity":
        return "equity"
    return _TRANSFER_POSITION_BY_NAME.get(acct.get("name") or "", "bank")


def is_account_move(entry, cat_map, accounts) -> bool:
    """這一列是不是**帳戶間移動的一腳**（配對面板的取數判準）。"""
    cm = (cat_map or {}).get(("cash", entry.get("category") or ""))
    if not cm or cm.get("treatment") != "transfer":
        return False
    acct = (accounts or {}).get(cm.get("account_id")) or {}
    return transfer_position(acct) in ACCOUNT_MOVE_POSITIONS


def equity_transfer_position(cash_entries, cat_map, accounts, as_of_month,
                             cap_floor=None) -> dict:
    """{'owner_net': 業主往來淨額(注入−提取，直接可加進權益 owner 線),
        'advance_net': 員工往來-預支未收回餘額(資產),
        'household_net': 家用往來未收回餘額(資產 —— 代墊家用，owner 2026-08-26),
        'financial_net': 其他金融資產（換匯/定存/證券/沒追蹤的銀行；
                         owner 2026-08-29）,
        'cap_flow': 「器材設備」transfer 淨流出（資本化差額警語用）}。

    位置是**存量**：只設上限（月 ≤ as_of），不設 baseline 下限 —— 與調整列
    同一條規則（期初列本來就開在基準月）。卡片消費列（status='card'）也算：
    刷卡買生活＝當下就是業主提取，錢之後才由還款離開銀行。

    分類依據＝科目的 acct_type（equity → 業主往來線）；員工往來-預支按科目名
    指認 —— 同為 asset 的器材設備走清冊（再記流量就重複）、銀行存款走卡債
    邏輯，asset 一律入位置反而是錯的，所以這裡刻意不用 acct_type 泛化。

    cap_flow **不是** BS 位置（器材淨值走清冊），只是同一趟掃描順手量出的
    「收支上的器材流出」，供對照清冊在期購置量差額；它有自己的下限
    cap_floor（期初累計月，> floor 才算 —— 期初裡的購置已含在基準裡）。
    """
    owner = advance = household = financial = cap = 0
    for e in cash_entries:
        cm = cat_map.get(("cash", e.get("category") or ""))
        if not cm or cm.get("treatment") != "transfer":
            continue
        m = month_of(e.get("entry_date"))
        if not m or (as_of_month and m > as_of_month):
            continue
        dep, exp = int(e.get("deposit") or 0), int(e.get("expense") or 0)
        pos = transfer_position(accounts.get(cm.get("account_id")))
        if pos == "equity":
            owner += dep - exp          # 注入為正、提取為負
        elif pos == "advance":
            advance += exp - dep        # 給出去未收回＝資產
        elif pos == "household":
            household += exp - dep      # 代墊家用未收回＝資產（還款沖銷）
        elif pos == "financial":
            financial += exp - dep      # 搬去證券/定存/沒追蹤的銀行＝還是你的錢
        elif pos == "capital":
            if cap_floor is None or m > cap_floor:
                cap += exp - dep        # 資本化流出（退款為負）
    return {"owner_net": owner, "advance_net": advance,
            "household_net": household, "financial_net": financial,
            "cap_flow": cap}


def mine_project_positions(projects, months, as_of_month) -> dict:
    """私帳的專案側位置（2026-08-26：「兩張表對不起來」的根因修正）。

    owner 的年度表是**權責**（結案日認列營收）、系統私帳損益是**現金**（入帳
    認列）—— 已分毫實證：FY2026 結案 131 案合約合計 8,103,670 = 表上實際營收。
    回傳：
    - receivable：BS 應收線 ＝ Σ(結案月 ≤ as_of 且應收 > 0)。🔴 私帳沒有發票，
      invoice AR 恆 0 —— 應收的正本是執行專案（amount_receivable，基準見
      ledger_project.expected_cash_in）。溢收（負值）不拿來抵別案。
      ⚠ as-of 近似：歷史「已收」是匯入基準值（無逐筆日期），無法回溯早於
      匯入日的期末 —— 對現在與往後的期末是準的。
    - accrual_revenue：Σ(結案月 ∈ 期間) 合約金額 —— 權責 vs 現金的橋接警語用。
    - receivable_rows：BS 下鑽明細。
    - outsource / tax / misc：Σ(結案月 ∈ 期間) 逐案的委外費用、稅款、雜支
      （ledger_detail）。owner 的年度表這三項就是逐案加總出來的 —— 2026-08-27
      三年逐一核對：委外 294,715／506,566／485,902、稅 —／292,271／443,302 全中
      （稅 ＝ 發票代辦費〔營業稅＋買發票〕＋ 報稅案的個人稅款）。
      🔴 這三項與收支明細裡「公司_專案」的**支出列是同一批錢的兩面**（那些列就是
      匯給外包與繳稅的現金），兩邊都認會重複計 —— 用了這裡就要排除那些現金列。
    """
    mset = set(months or [])
    recv = accrual = 0
    outsource = tax = misc = 0
    rows = []
    for p in projects or ():
        m = month_of(p.get("completion_date"))
        if m and m in mset:
            accrual += int(p.get("contract_amount") or 0)
            d = p.get("ledger_detail") or {}
            if isinstance(d, str):
                try:
                    d = json.loads(d)
                except ValueError:
                    d = {}
            outsource += int(d.get("outsource") or 0)
            tax += int(d.get("invoice_fee") or 0) + int(d.get("personal_tax") or 0)
            misc += int(d.get("misc") or 0)
        ar = int(p.get("amount_receivable") or 0)
        if ar > 0 and m and (not as_of_month or m <= as_of_month):
            recv += ar
            rows.append({"id": p.get("id"), "name": p.get("name"),
                         "close_month": m, "amount": ar})
    rows.sort(key=lambda x: -x["amount"])
    return {"receivable": recv, "accrual_revenue": accrual,
            "receivable_rows": rows, "outsource": outsource, "tax": tax, "misc": misc}


def card_outstanding(cash_entries, card_cfg: dict, as_of_month) -> int:
    """信用卡未繳餘額（負債）＝期初 + 刷卡（status='card' 的支出）− 還款
    （還款類別的 支−存）。口徑與 api_finance_card.card_summary 同一條式子 ——
    那邊是即時值，這邊是 as_of 截止的存量。"""
    charges = repay = 0
    repay_cats = set(card_cfg.get("repay_categories") or [])
    for e in cash_entries:
        m = month_of(e.get("entry_date"))
        if not m or (as_of_month and m > as_of_month):
            continue
        dep, exp = int(e.get("deposit") or 0), int(e.get("expense") or 0)
        if (e.get("status") or "") == "card":
            charges += exp - dep
        elif (e.get("category") or "") in repay_cats:
            repay += exp - dep
    opening = int(card_cfg.get("opening") or 0)
    # 🔴 沒有卡片帳的帳本（無期初、無刷卡列）一律 0 —— 否則一筆恰好叫
    # 「信用卡」類別的雜列就會在母公司 BS 憑空長出一條**負數負債**
    # （今天母公司 0 筆，這是結構防呆不是現況修補）。
    if opening == 0 and charges == 0:
        return 0
    return opening + charges - repay


# ── 資產負債表 ───────────────────────────────────────────────

def build_balance_sheet(as_of_month: str, *, bank_lines=(), receivable_total=0,
                        advance_balance=0, equipment=(), adjustments=(),
                        payable_total=0, vat_payable=0, loan_rows=(),
                        cumulative_net=0, note_counts=None,
                        shareholder_loan_lines=(),
                        shareholder_capital_lines=(),
                        owner_flow_net=0, card_outstanding=0,
                        household_net=0, financial_net=0) -> dict:
    """資產負債表（as_of = 期末月月底；推導式，非複式簿記）。

    - 資產：各銀行帳戶推導餘額分列 + 應收帳款 + 員工往來-預支（未結清預支
      餘額，caller 以 compute_advance_status 即時算 — 為即時值非期末歷史值）
      + 器材淨值（除役者出表）。
    - 負債：流動 = 應付帳款 + 應付營業稅（caller 傳 baseline 起累計 net，
      再加 adj_type='vat' 的調整列 —— 見下）；
      非流動 = 銀行貸款逐筆分列（loan_rows 由 loan_outstanding_rows 算，
      階段四）。流動比率分母只算流動負債；負債比率吃負債總計。
    - 權益：期初調整（opening）+ 業主往來（owner_in − owner_out，amount 取
      正值填寫）+ 累積損益（baseline..as_of 累計淨利，caller 算）+ 其他調整
      （correction/accountant/writeoff/other 合計）。調整列按 adj_date ≤ as_of
      過濾（不設 baseline 下限 — 期初列本來就開在基準月）。
    - 🔴 adj_type='vat' 是唯一**不落權益**的調整型別：它加在應付營業稅上。
      為什麼要有它：vat_payable 是從發票推出來的（銷項−進項−已繳），發票沒記
      全的年份會算出「繳的比該繳的多」→ 負數負債。那不是政府欠你，是帳的缺口。
      補發票不可行時（owner 2026-08-23：2024–2025 帳沒記好但稅都有繳好），
      用一筆具名、有日期、有說明的調整沖平那個年代 —— 比讓報表掛著一個
      解釋不了的負數誠實，也比偷偷把負數夾成 0 誠實（那會連真的溢繳都看不見）。
    - 檢核誠實外顯：diff = 資產 −（負債+權益），≠0 時附可能原因清單
      （note_counts 來自 statement_warnings）。推導式三表在器材購置已費用化、
      預支即時值等情況天生會有 diff — 掩蓋比外顯危險。
    - 比率門檻文案照 owner Excel：負債比 <65% 資金運用效能不良、65-75 良好、
      >80 需要增資（75-80 補「偏高」過渡帶）。
    """
    current = [{"key": f"cash:{b.get('id') or i}", "label": b.get("name") or "現金",
                "amount": int(b.get("amount") or 0)}
               for i, b in enumerate(bank_lines)]
    # drill 只掛有列級明細可下鑽的行（應收/應付）— 其餘 BS 行為推導值無明細
    current.append({"key": "receivable", "label": "應收帳款",
                    "amount": int(receivable_total or 0), "drill": "receivable"})
    current.append({"key": "advance", "label": "員工往來-預支",
                    "amount": int(advance_balance or 0)})
    if household_net:
        # 家用代墊（私帳；owner 2026-08-26）：墊出去未收回＝別人欠你的錢。
        # 母公司恆 0 → 不畫（零列＝雜訊）；有值才上，語意同員工往來。
        current.append({"key": "household", "label": "家用代墊",
                        "amount": int(household_net or 0)})
    if financial_net:
        # 其他金融資產（私帳；owner 2026-08-29）：換匯／定存／買股票／匯到
        # 沒被系統追蹤的銀行 —— 錢沒有被花掉也不是提取，只是換了個地方放。
        # 之前這些全落在業主往來，等於帳上說 owner 提走了 610 萬。
        current.append({"key": "financial", "label": "其他金融資產",
                        "amount": int(financial_net or 0)})
    eq_rows = equipment_net_rows(equipment, as_of_month)
    noncurrent = [{"key": "equipment", "label": "器材淨值",
                   "amount": eq_rows["net_total"]}]
    assets_total = sum(x["amount"] for x in current) + sum(x["amount"] for x in noncurrent)
    for x in current + noncurrent:
        x["pct"] = _pct(x["amount"], assets_total)

    # 調整列先算 —— 其中 vat 型別要進負債側的應付營業稅，不是權益。
    # （按 adj_date ≤ as_of 過濾；期初列本來就開在基準月，不設下限。）
    opening = owner = other = vat_adj = 0
    for a in adjustments:
        m = month_of(a.get("adj_date"))
        if not m or (as_of_month and m > as_of_month):
            continue
        amt = int(a.get("amount") or 0)
        t = a.get("adj_type") or ""
        if t == "opening":
            opening += amt
        elif t == "owner_in":
            owner += amt
        elif t == "owner_out":
            owner -= amt
        elif t == "vat":
            vat_adj += amt
        else:
            other += amt

    liab_current = [
        {"key": "payable", "label": "應付帳款",
         "amount": int(payable_total or 0), "drill": "payable"},
        {"key": "vat_payable", "label": "應付營業稅",
         "amount": int(vat_payable or 0) + vat_adj},
    ]
    # 信用卡未繳（私帳；母公司無卡片帳 → 0 → 不出列）
    if int(card_outstanding or 0):
        liab_current.append({"key": "card", "label": "信用卡未繳",
                             "amount": int(card_outstanding)})
    # 股東借款逐筆分列（owner 2026-08-21）。餘額＝公司欠該股東多少 —— 是負債，
    # 🔴 不可以混進上面的 bank_lines（那會讓現金憑空多出股東墊付的錢）。
    liab_current += [{"key": f"sh_loan:{x.get('id')}",
                      "label": f"股東往來－{x.get('name') or '?'}",
                      "amount": int(x.get("amount") or 0)}
                     for x in shareholder_loan_lines]
    # 非流動負債：銀行貸款逐筆分列（loan_outstanding_rows 已保證 key/label/amount；
    # BS 行為推導值無明細 drill）
    liab_noncurrent = [{"key": x["key"], "label": x["label"], "amount": x["amount"]}
                       for x in loan_rows]
    liab_current_total = sum(x["amount"] for x in liab_current)
    liab_total = liab_current_total + sum(x["amount"] for x in liab_noncurrent)
    for x in liab_current + liab_noncurrent:
        x["pct"] = _pct(x["amount"], assets_total)

    equity_lines = [
        # 股東投資款＝股東投入的資本，落在權益不是負債（與借款的差別就在這裡）
        *[{"key": f"sh_cap:{x.get('id')}",
           "label": f"股東投資款－{x.get('name') or '?'}",
           "amount": int(x.get("amount") or 0)}
          for x in shareholder_capital_lines],
        {"key": "opening", "label": "期初調整", "amount": opening},
        # 調整列（手記）＋ transfer 流量自動推導（equity_transfer_position）——
        # 私帳的日常提取每月都在流動，靜態調整列跟不上，必須由流量推
        {"key": "owner", "label": "業主往來", "amount": owner + int(owner_flow_net or 0)},
        {"key": "retained", "label": "累積損益", "amount": int(cumulative_net or 0)},
        {"key": "adjustments", "label": "其他調整", "amount": other},
    ]
    equity_total = sum(x["amount"] for x in equity_lines)
    for x in equity_lines:  # 權益側 pct 分母同樣 = 資產總計（owner Excel 呈現）
        x["pct"] = _pct(x["amount"], assets_total)

    diff = assets_total - liab_total - equity_total
    notes = []
    if diff != 0:
        nc = note_counts or {}
        if nc.get("unmapped"):
            notes.append(f"{nc['unmapped']} 筆收支/請款尚未歸類科目")
        if nc.get("unassigned"):
            notes.append(f"{nc['unassigned']} 筆收支未掛銀行帳戶")
        if nc.get("undated"):
            notes.append(f"{nc['undated']} 筆收支未填日期（無法定位月份）")
        notes.append("器材購置若已走收支明細費用化、或早於基準月，其淨值會造成差額")
        notes.append("員工預支餘額為即時推導值，非期末歷史值")

    current_assets = sum(x["amount"] for x in current)
    current_liab = liab_current_total  # 流動比率分母只算流動負債（貸款屬非流動）
    current_ratio = round(current_assets / current_liab, 2) if current_liab else None
    debt_ratio = _pct(liab_total, assets_total)
    labels = {}
    if current_ratio is None:
        labels["current_ratio"] = "無流動負債"
    elif current_ratio >= 2:
        labels["current_ratio"] = "流動比率 ≥ 2：短期償債能力充足"
    elif current_ratio >= 1:
        labels["current_ratio"] = "流動比率 1–2：尚可"
    else:
        labels["current_ratio"] = "流動比率 < 1：流動資產不足以覆蓋流動負債"
    if debt_ratio is not None:
        if debt_ratio < 65:
            labels["debt_ratio"] = "負債比率 <65%：資金運用效能不良（可更積極運用資金）"
        elif debt_ratio <= 75:
            labels["debt_ratio"] = "負債比率 65–75%：良好"
        elif debt_ratio <= 80:
            labels["debt_ratio"] = "負債比率 75–80%：偏高，留意償債壓力"
        else:
            labels["debt_ratio"] = "負債比率 >80%：需要增資"

    return {
        "as_of": as_of_month,
        "assets": {"current": current, "noncurrent": noncurrent, "total": assets_total},
        "liabilities": {"current": liab_current, "noncurrent": liab_noncurrent,
                        "total": liab_total},
        "equity": {"lines": equity_lines, "total": equity_total},
        "check": {"diff": diff, "notes": notes},
        "ratios": {"current_ratio": current_ratio, "debt_ratio": debt_ratio,
                   "labels": labels},
    }


