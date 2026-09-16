"""私帳月報的純規則（docs/MONTHLY_REPORT.md）—— owner 2026-09-17「我希望你在我更新帳戶後 自動提供一份月報給我」
「我希望月報可以給我財務建議與財務分析」。

輸入是別的模組已經算好的東西（堡壘 payload、登記餘額 payload、資產儀表板的桶、本月收支、淨值快照、上一份月報），
這裡只做三件事：**排成一份月報的形狀、跟上個月比、依規則產生體檢與建議**。沒有 I/O，可直接測。

建議是**規則**不是文案：每一條有門檻（例如單一持股佔證券 25% 以上才出現集中度那條），數字變了建議就變、消失。
金額一律整數元；畫面自己換成「萬」。
"""
from __future__ import annotations

from typing import Optional

from core.fortress_logic import RUNWAY_GREEN, tone as runway_tone

#: 建議的等級順序（畫面照這個排；bad 最前）
LEVEL_ORDER = {"bad": 0, "warn": 1, "info": 2, "ok": 3}
#: 集中度門檻：單一持股佔證券現值多少算「偏高」／「高」
CONCENTRATION_WARN = 0.25
CONCENTRATION_BAD = 0.35
#: 名字裡有這些字的持股視為**指數型／分散的基金**，不算「一檔股票」（集中度那條只盯單一公司）
BROAD_FUND_WORDS = ("vanguard", "ishares", "spdr", "etf", "all-world", "total ", "world", "s&p", "0050", "006208", "vti", "vwra", "vt ",
                    "台灣50", "臺灣50", "台灣 50", "台50", "指數", "高股息", "定存", "活存", "現金", "外幣", "保險", "壽", "未拆明細", "複委託")
#: 這些指數型基金裡台積電佔一半左右（2024 起 >50%，這裡用約數）：最大的單一股票是台積電時，穿透後的曝險要提一句
TSMC_LOOKTHROUGH_NAMES = ("0050", "006208", "台灣50", "臺灣50", "台50")
TSMC_LOOKTHROUGH_SHARE = 0.55


def is_broad_fund(name: str) -> bool:
    n = (name or "").strip().lower()
    return any(w in n for w in BROAD_FUND_WORDS)


#: 應收超過幾個月的必要支出才提醒
RECEIVABLE_MONTHS = 2
#: 信用卡未繳超過幾個月的必要支出才提醒（半個月是正常的帳單週期，不是警訊）
CARD_MONTHS = 1.0
#: |還沒補的明細| 小於這個就當補齊（幾十元的零頭不值得叫）
UNFILLED_IGNORE = 100


def _open_unfilled(a: dict) -> bool:
    """這個帳戶登記後還有沒補的明細（零頭不算）。"""
    u = a.get("unfilled")
    return u is not None and abs(int(u)) >= UNFILLED_IGNORE


def _is_idle(a: dict) -> bool:
    """閒置帳戶：餘額 0 又從沒登記過（玉山、樂天那種）—— 不算「沒登記」、也不算進帳戶總數。"""
    return int(a.get("balance") or 0) == 0 and a.get("unfilled") is None


def _unregistered(accounts: list) -> list:
    """這個月還沒登記的帳戶（閒置的不算）。"""
    return [a for a in accounts if not a.get("registered_this_month") and not _is_idle(a)]
#: 必要支出的樣本月數低於這個就標「樣本少」
NEED_SAMPLE_OK = 6
#: 走勢最多帶幾個快照點
TREND_POINTS = 24


def _wan(n) -> str:
    """元 → 「12.3 萬」／「1.2 億」（負數前面加 −）。跟兩個前端的 fmtWan／wan 同一個口徑。"""
    v = float(n or 0)
    a = abs(v)
    if a >= 1e8:
        s = f"{a / 1e8:.2f}".rstrip("0").rstrip(".") + " 億"
    else:
        s = f"{a / 1e4:.1f}".rstrip("0").rstrip(".") + " 萬"
    return ("−" if v < 0 else "") + s


def _pct(a, b) -> Optional[float]:
    return round(float(a) / float(b), 4) if b else None


def _i(v) -> int:
    try:
        return int(round(float(v or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


# ── 各段 ──────────────────────────────────────────────────────────────
def totals_block(buckets: dict, liabilities: dict, cash_usable=None) -> dict:
    """總資產＝銀行現金＋證券＋應收＋器材淨值；負債＝卡費＋貸款；net_worth＝總資產−負債（含應收、器材，帳面）。
    **標題用的是 net_financial**（現金＋證券−負債）—— 跟財富階梯、財富自由同一個定義；應收是稅前帳面、器材是折舊後帳面，
    都不是能花的錢，不能跟階梯的淨值長得一樣卻差 400 萬。cash_usable＝堡壘第 1–3 層現金（可撐月數用的那個，含現金類帳戶）。"""
    cash = _i(buckets.get("銀行現金"))
    sec = _i(buckets.get("證券現值"))
    recv = _i(buckets.get("應收帳款"))
    eq = _i(buckets.get("固定資產淨值"))
    liab = _i((liabilities or {}).get("total"))
    assets = cash + sec + recv + eq
    return {"cash": cash, "securities": sec, "receivable": recv, "equipment": eq, "assets": assets,
            "liabilities": liab, "card": _i((liabilities or {}).get("card")), "loan": _i((liabilities or {}).get("loan")),
            "net_worth": assets - liab, "financial": cash + sec, "net_financial": cash + sec - liab,
            "cash_usable": _i(cash_usable) if cash_usable is not None else cash}


def compare(now: dict, prev: Optional[dict]) -> dict:
    """{key: 差額}；prev 沒有那個 key 就不比（None）。"""
    if not prev:
        return {k: None for k in now}
    return {k: (now[k] - _i(prev[k]) if prev.get(k) is not None else None) for k in now}


def flow_block(month_cash: dict, delta: dict, through: str = "") -> dict:
    """多出來的錢從哪來：本月收入／支出／家用（收支明細，**不含**轉帳、信用卡還款、投資買賣 —— 呼叫端已排掉）
    ＋證券增減（含買賣、匯率）＋應收增減＋「登記餘額與帳上的差」。

    register_gap ＝ Δ銀行現金 − 帳戶真正的淨流（bank_net：deposit−expense−bank_fee−claim）。帳戶餘額是「登記數＋登記後的流水」，
    所以這個差就是登記後還沒記進帳的明細（補齊會歸 0）—— 不是「解釋不了」，別的東西不可能落在這裡。
    要有上一份月報的分項（Δ現金／Δ證券）才算得出來；沒有就 None。through：數字算到哪一天（月中產生的月報要標）。"""
    dep, exp, house = _i(month_cash.get("deposit")), _i(month_cash.get("expense")), _i(month_cash.get("household_expense"))
    net = dep - exp
    bank_net = month_cash.get("bank_net")
    cash_delta = delta.get("cash")
    gap = (cash_delta - _i(bank_net)) if (cash_delta is not None and bank_net is not None) else None
    return {"deposit": dep, "expense": exp, "household": house, "net": net, "has_entries": bool(dep or exp),
            "bank_net": _i(bank_net) if bank_net is not None else None,
            "securities_change": delta.get("securities"), "receivable_change": delta.get("receivable"),
            "register_gap": gap, "through": through,
            "savings_rate": _pct(net, dep) if dep > 0 else None}


def accounts_block(register: dict, prev: Optional[dict], month: str) -> list:
    """各帳戶：本月數字、上月數字（上一份月報同名帳戶）、差、這個月登記了沒、還沒補的明細。變動大的先排。"""
    prev_by = {a["name"]: a for a in ((prev or {}).get("accounts") or [])}
    out = []
    for a in register.get("accounts") or []:
        p = prev_by.get(a["name"])
        pv = _i(p["balance"]) if p and p.get("balance") is not None else None
        out.append({"name": a["name"], "balance": _i(a.get("balance")), "prev": pv,
                    "delta": (_i(a.get("balance")) - pv) if pv is not None else None,
                    "anchor_date": a.get("anchor_date"), "unfilled": a.get("unfilled"),
                    "registered_this_month": bool(a.get("anchor_date") and str(a["anchor_date"]).startswith(month))})
    out.sort(key=lambda x: (-(abs(x["delta"]) if x["delta"] is not None else -1), -x["balance"]))
    return out


def brokers_block(register: dict, prev: Optional[dict]) -> list:
    prev_by = {b["broker"]: b for b in ((prev or {}).get("brokers") or [])}
    out = []
    for b in register.get("brokers") or []:
        p = prev_by.get(b["broker"])
        pv = _i(p["total"]) if p and p.get("total") is not None else None
        out.append({"broker": b["broker"], "total": _i(b.get("total")), "prev": pv,
                    "delta": (_i(b.get("total")) - pv) if pv is not None else None,
                    "plug": _i(b.get("plug")), "count": int(b.get("count") or 0)})
    out.sort(key=lambda x: -x["total"])
    return out


def concentration(fortress: dict) -> dict:
    """集中度：**單一公司的股票**佔證券現值的比重（最大的一檔＋前三檔）。指數型基金（VWRA、0050…）本身就是幾百
    幾千家公司，不算「一檔股票」—— 不然全球股 ETF 佔 42% 會被寫成「一檔股票決定你四成資產」。
    用堡壘的 accounts（kind=holding，已換台幣）。"""
    hs = sorted([(a.get("name") or "", _i(a.get("balance"))) for a in fortress.get("accounts") or []
                 if a.get("kind") == "holding" and _i(a.get("balance")) > 0], key=lambda x: -x[1])
    total = sum(v for _, v in hs)
    stocks = [(n, v) for n, v in hs if not is_broad_fund(n)]
    top = stocks[0] if stocks else ("", 0)
    funds_pct = _pct(sum(v for n, v in hs if is_broad_fund(n)), total)
    # 穿透：台積電直接持有＋台灣50 類基金裡的那一半（約數；一檔名字對到兩個關鍵字也只算一次）
    lookthrough = None
    if "台積電" in top[0]:
        via = sum(v for n, v in hs if any(k in n.lower() for k in TSMC_LOOKTHROUGH_NAMES)) * TSMC_LOOKTHROUGH_SHARE
        if via > 0:
            lookthrough = {"value": int(round(top[1] + via)), "pct": _pct(top[1] + via, total)}
    return {"total": total, "top_name": top[0], "top_value": top[1], "top_pct": _pct(top[1], total) if stocks else None,
            "top3_pct": _pct(sum(v for _, v in stocks[:3]), total) if stocks else None, "funds_pct": funds_pct,
            "biggest_name": hs[0][0] if hs else "", "biggest_pct": _pct(hs[0][1], total) if hs else None,
            "lookthrough": lookthrough}


def fortress_block(fortress: dict) -> dict:
    tests = [{"key": t.get("key"), "title": t.get("title"), "state": t.get("state"), "verdict": t.get("verdict")}
             for t in fortress.get("tests") or []]
    cnt = {"ok": 0, "warn": 0, "bad": 0, "na": 0}
    for t in tests:
        cnt[t["state"] if t["state"] in cnt else "na"] += 1
    layers = sorted([{"no": L.get("no"), "name": L.get("name"), "have": _i(L.get("have")), "target": L.get("target"),
                      "gap": L.get("gap"), "pct": L.get("pct")} for L in fortress.get("layers") or []], key=lambda x: -(x["no"] or 0))
    r = fortress.get("runway") or {}
    need = fortress.get("monthly_need") or {}
    return {"runway": r.get("months"), "tone": r.get("tone"),
            "need": _i(need.get("used")), "need_sample_months": int(need.get("sample_months") or 0),
            "need_override": need.get("override") is not None,
            "tests": tests, "counts": cnt, "layers": layers}


def ladder_fire_block(fortress: dict) -> dict:
    lad = fortress.get("ladder") or {}
    fire = fortress.get("fire") or {}
    pj = fortress.get("projection") or {}
    y10 = next((r for r in pj.get("rows") or [] if int(r.get("year") or 0) == 10), None)
    rungs = lad.get("rungs") or []
    nxt = next((r for r in rungs if int(r.get("no") or 0) == int(lad.get("rung") or 0) + 1), None)
    return {"rung": lad.get("rung"), "rung_name": lad.get("name"),
            "to_next": lad.get("to_next"), "next_name": (nxt or {}).get("name"),
            "fire_allowed": _i(fire.get("allowed")), "fire_spend": _i(fire.get("spend")),
            "fire_ratio33": fire.get("ratio33"),
            "fire_rate": fire.get("current_rate"), "fire_state": fire.get("state"), "fire_pretax": bool(fire.get("pretax")),
            "y10_nominal": _i(y10.get("nominal")) if y10 else None, "y10_real": _i(y10.get("real")) if y10 else None,
            "growth_rate": pj.get("rate"), "inflation": pj.get("inflation")}


# ── 體檢（四個面向）───────────────────────────────────────────────────
def health_block(ft: dict, conc: dict, accounts: list, flow: dict) -> list:
    runway = ft.get("runway")
    tone = ft.get("tone") or runway_tone(runway)      # 跟堡壘同一套顏色（那頁綠的話這裡不能寫「普通」）
    if runway is None or tone == "na":
        liq = ("na", "算不出來", "必要支出還沒有資料")
    elif tone == "g":
        liq = ("ok", "好", f"現金撐 {runway:g} 個月")
    elif tone == "a":
        liq = ("warn", "緊", f"現金撐 {runway:g} 個月，{RUNWAY_GREEN} 個月以上才算穩")
    else:
        liq = ("bad", "弱", f"現金只撐 {runway:g} 個月")
    war = next((t for t in ft.get("tests") or [] if t.get("key") == "war"), None) or {}
    ws = war.get("state") or "na"
    verdict = (war.get("verdict") or "").split("。")[0]      # 用那題自己的判語（沒標海外時不是「會被迫賣資產」）
    res = {"ok": ("ok", "好", "極端情境撐得住"), "warn": ("warn", "緊", verdict or "極端情境撐得住但很緊"),
           "bad": ("bad", "弱", verdict or "極端情境撐不住"), "na": ("na", "算不出來", "資料不足")}[ws if ws in ("ok", "warn", "bad") else "na"]
    tp = conc.get("top_pct")
    lt = conc.get("lookthrough")
    eff = float(lt["pct"]) if (lt and tp is not None) else tp
    via = "（含台灣50 裡的）" if lt else ""
    if tp is None:
        con = ("ok", "分散", f"都是分散的基金（最大 {conc.get('biggest_name') or '—'} 佔 {float(conc.get('biggest_pct') or 0) * 100:.0f}%）") \
            if conc.get("total") else ("na", "沒有持股", "")
    elif eff >= CONCENTRATION_BAD:
        con = ("bad", "高", f"{conc['top_name']} 佔證券約 {eff * 100:.0f}%{via}")
    elif eff >= CONCENTRATION_WARN:
        con = ("warn", "偏高", f"{conc['top_name']} 佔證券約 {eff * 100:.0f}%{via}")
    else:
        con = ("ok", "分散", f"最大的單一股票 {conc['top_name']} 佔 {tp * 100:.0f}%")
    unfilled = [a for a in accounts if _open_unfilled(a)]
    unreg = _unregistered(accounts)
    active = [a for a in accounts if not _is_idle(a)]
    if not flow.get("has_entries") and unreg and len(unreg) == len(active):
        rec = ("bad", "缺", "本月沒有收支明細，也沒登記餘額")
    elif unfilled or not flow.get("has_entries"):
        rec = ("warn", "缺", (f"{len(unfilled)} 個帳戶登記後還沒補明細" if unfilled else "本月還沒有收支明細"))
    elif unreg:
        rec = ("warn", "不全", f"{len(unreg)} 個帳戶這個月還沒登記")
    else:
        rec = ("ok", "齊", "明細補齊、帳戶都登記了")
    return [{"key": k, "label": label, "state": s, "grade": g, "text": t}
            for k, label, (s, g, t) in (("liquidity", "流動性", liq), ("resilience", "韌性（極端情境）", res),
                                        ("concentration", "集中度", con), ("records", "收支紀錄", rec))]


# ── 建議（規則）─────────────────────────────────────────────────────
def _rule_war(fortress: dict, ft: dict):
    war = next((t for t in ft["tests"] if t["key"] == "war"), None)
    if not war or war["state"] != "bad":
        return None
    fx_unflagged = [a["name"] for a in fortress.get("accounts") or []
                    if (a.get("currency") or "TWD") != "TWD" and not (a.get("flags") or {}).get("offshore")]
    if fx_unflagged:
        names = "、".join(fx_unflagged[:4]) + ("…" if len(fx_unflagged) > 4 else "")
        return {"level": "bad", "key": "war_flags", "title": "台海戰爭題是紅的，但可能只是沒標海外。",
                "text": f"這題只認標了「實體」或「海外」的錢。{names} 是外幣部位，卻沒標「海外」。",
                "how": "到堡壘的帳戶分層，把海外券商的持股勾「海外」＋「美元」；標完再看這題。另外真的放一個月生活費的現金在家，銀行停擺四週也不怕。"}
    return {"level": "bad", "key": "war", "title": "台海戰爭題是紅的：頭一個月拿得到的錢不夠。",
            "text": war.get("verdict") or "", "how": "把一個月生活費放成實體現金或海外帳戶，這題就過。"}


def _rule_layers(ft: dict):
    l1 = next((L for L in ft["layers"] if L["no"] == 1), None)
    if not l1:
        return None
    gaps = [(L["no"], L["name"], _i(L["gap"])) for L in ft["layers"] if L["no"] in (2, 3, 4) and _i(L["gap"]) > 0]
    if not gaps:
        return None
    need = sum(g for _, _, g in gaps)
    surplus = l1["have"] - _i(l1["target"])
    where = "、".join(f"第 {no} 層{name}差 {_wan(g)}" for no, name, g in gaps)
    if surplus >= need:
        return {"level": "warn", "key": "layers", "title": f"第 1 層多了 {_wan(surplus)}，第 2 到 4 層卻還沒填滿。",
                "text": f"{where}。錢是夠的（第 1 層目標只要 {_wan(l1['target'])}），只是全部掛在第 1 層，分層等於沒做。",
                "how": f"到堡壘的帳戶分層，把 {_wan(need)} 的帳戶改標到第 2 到 4 層（例如把儲蓄戶、定存標到第 3 層），第 1 層留 {_wan(l1['have'] - need)} 還是夠用。"
                       "帳戶是整個標的，湊不齊就實際轉一筆到獨立帳戶再標；分到第 4 層的錢不算可撐月數，那個數字會變小一點。"}
    return {"level": "warn", "key": "layers", "title": f"第 2 到 4 層合計還差 {_wan(need)}。",
            "text": f"{where}。第 1 層只多 {_wan(max(0, surplus))}，分完還不夠。",
            "how": "先把第 1 層多的分過去，剩下的差額用每月結餘補；應收收回來優先進第 3 層。"}


def _rule_concentration(conc: dict):
    tp = conc.get("top_pct")
    if tp is None or tp < CONCENTRATION_WARN:
        return None
    lt = conc.get("lookthrough")
    eff = float(lt["pct"]) if lt else tp                     # 穿透後的比重（有的話）決定等級
    lvl = "bad" if eff >= CONCENTRATION_BAD else "warn"
    via = f"；加上台灣50 類基金裡的那一半，實際約 {float(lt['pct']) * 100:.0f}%（{_wan(lt['value'])}）" if lt else ""
    how = (f"已經超過 {int(CONCENTRATION_BAD * 100)}%：考慮分批賣到 30% 以下（賣出會有稅與二代健保，分年賣），新資金一律進別的標的。"
           if lvl == "bad" else
           f"不必賣，先訂上限：新資金優先進別的標的；超過 {int(CONCENTRATION_BAD * 100)}% 再賣一部分換分散的 ETF。")
    return {"level": lvl, "key": "concentration",
            "title": f"{conc['top_name']} 一檔佔證券 {tp * 100:.0f}%，一檔股票決定你 {eff * 100:.0f}% 的投資。",
            "text": f"{conc['top_name']} {_wan(conc['top_value'])}{via}；單一股票前三檔合計佔 {float(conc.get('top3_pct') or 0) * 100:.0f}%，"
                    f"指數型基金佔 {float(conc.get('funds_pct') or 0) * 100:.0f}%（那些本身就是分散的，不算）。",
            "how": how + "這條每月月報會自動盯。"}


def _rule_records(accounts: list, brokers: list, flow: dict):
    unfilled = [a for a in accounts if _open_unfilled(a)]
    unreg = _unregistered(accounts)
    plugs = [b for b in brokers if b.get("plug")]
    if not unfilled and not unreg and not plugs and flow.get("has_entries"):
        return None
    parts = []
    if unfilled:
        tot = sum(_i(a["unfilled"]) for a in unfilled)
        parts.append(f"{len(unfilled)} 個帳戶登記後還沒補明細（合計 {_wan(tot)}）")
    if not flow.get("has_entries"):
        parts.append("本月收支明細是空的，「存了多少」算不出來")
    if unreg:
        last = max((str(a.get("anchor_date") or "") for a in unreg), default="")
        parts.append(f"{len(unreg)} 個帳戶這個月還沒登記餘額" + (f"（上次 {last[5:].replace('-', '/')}）" if last else ""))
    if plugs:
        parts.append("、".join(f"{b['broker']}未拆明細 {_wan(b['plug'])}" for b in plugs))
    if unfilled:
        how = f"先補{'、'.join(a['name'] for a in unfilled[:3])}這幾戶的明細，補到「還沒補的明細」歸 0。"
    elif unreg:
        how = f"到「登記餘額」把{'、'.join(a['name'] for a in unreg[:3])}這幾戶今天的餘額登記一次。"
    else:
        how = "把本月的收支明細記進去。"
    return {"level": "warn", "key": "records", "title": "帳還沒記齊，這份月報有幾格是空的。",
            "text": "；".join(parts) + "。",
            "how": how + "每月登記一次全部帳戶、明細補齊，下個月就能算出存款率。"}


def _rule_receivable(totals: dict, ft: dict):
    need = ft.get("need") or 0
    if not need or totals["receivable"] < need * RECEIVABLE_MONTHS:
        return None
    share = _pct(totals["receivable"], totals["financial"])
    return {"level": "info", "key": "receivable",
            "title": f"應收 {_wan(totals['receivable'])} 還在外面（稅前帳面數）。",
            "text": f"佔金融資產約 {float(share or 0) * 100:.0f}%。這是案子的帳面數字，收回來才算數；未逾期的不用催。",
            "how": "每月固定催一次逾期最久的案；月報下個版本會把逾期最久的五案列出來。"}


def _rule_fire(lf: dict):
    if lf.get("fire_state") == "na":
        return None
    if lf.get("fire_state") != "ok":
        gap = lf["fire_spend"] - lf["fire_allowed"]
        return {"level": "warn", "key": "fire_gap", "title": f"還沒到財富自由：不工作每月可花 {_wan(lf['fire_allowed'])}，現在每月花 {_wan(lf['fire_spend'])}。",
                "text": f"差 {_wan(gap)}／月；達成率 {float(lf.get('fire_ratio33') or 0) * 100:.0f}%（33 倍法則）。",
                "how": "兩條路：支出降到可花的數字以下，或金融資產再長；每月月報會盯這個比例。"}
    if lf.get("fire_pretax"):
        return {"level": "info", "key": "fire_tax", "title": "財富自由那格是稅前數字。",
                "text": f"每月可花 {_wan(lf['fire_allowed'])} 沒扣股利所得稅與二代健保補充保費。",
                "how": "到堡壘的財富自由段填「每月稅與補充保費」的估計，數字才是真的。"}
    return None


def _rule_card(register: dict, ft: dict):
    card = _i(register.get("card_outstanding"))
    need = ft.get("need") or 0
    if not need or card < need * CARD_MONTHS:
        return None
    return {"level": "warn", "key": "card", "title": f"信用卡未繳 {_wan(card)}，超過一個月的生活費。",
            "text": "卡費在堡壘裡算預留，會先扣掉可撐月數。", "how": "帳單日前把它繳掉，或確認是不是有大筆刷卡還沒對到。"}


def _rule_savings(flow: dict, ft: dict):
    if not flow.get("has_entries"):
        return None
    if flow["net"] < 0:
        return {"level": "warn", "key": "savings", "title": f"本月支出比收入多 {_wan(-flow['net'])}。",
                "text": f"收入 {_wan(flow['deposit'])}、支出 {_wan(flow['expense'])}，其中家用 {_wan(flow['household'])}。",
                "how": "看一下支出裡有沒有投資或轉帳被記成支出；如果是真的入不敷出，下個月先盯家用。"}
    rate = flow.get("savings_rate")
    if rate is not None:
        return {"level": "ok", "key": "savings", "title": f"本月存下 {_wan(flow['net'])}，存款率 {rate * 100:.0f}%。",
                "text": f"收入 {_wan(flow['deposit'])}、支出 {_wan(flow['expense'])}。", "how": ""}
    return None


def _rule_need_sample(ft: dict):
    if ft.get("need_override") or ft.get("need_sample_months", 0) >= NEED_SAMPLE_OK:
        return None
    return {"level": "info", "key": "need_sample",
            "title": f"必要支出只有 {ft.get('need_sample_months', 0)} 個月的樣本。",
            "text": f"可撐月數與財富自由都用這個數字（{_wan(ft.get('need'))}／月）算，樣本少就不準。",
            "how": "補齊明細到 6 個月，或到堡壘直接填一個你認定的每月必要支出。"}


def _rule_strengths(totals: dict, ft: dict, lf: dict):
    good = []
    if not totals["loan"]:
        good.append("沒有貸款")
    if (ft.get("runway") or 0) >= 12:
        good.append(f"現金撐 {ft['runway']:g} 個月")
    if ft["counts"]["bad"] == 0 and ft["counts"]["ok"] >= 4:
        good.append("壓力測試沒有紅燈")
    if lf.get("fire_state") == "ok":
        caveat = []
        if lf.get("fire_pretax"):
            caveat.append("稅前")
        if not ft.get("need_override") and ft.get("need_sample_months", 0) < NEED_SAMPLE_OK:
            caveat.append(f"{ft.get('need_sample_months', 0)} 個月樣本")
        good.append("已達財富自由門檻" + (f"（{'、'.join(caveat)}）" if caveat else ""))
    if not good:
        return None
    war_bad = any(t["key"] == "war" and t["state"] == "bad" for t in ft.get("tests") or [])
    return {"level": "ok", "key": "strengths", "title": "做得好的地方：" + "、".join(good) + "。",
            "text": "在這個基礎上把上面幾件做完，" + ("台海那題可以轉綠。" if war_bad else "體檢四格可以全綠。"), "how": ""}


def advice_block(fortress: dict, ft: dict, conc: dict, accounts: list, brokers: list, flow: dict,
                 totals: dict, lf: dict, register: dict) -> list:
    items = [x for x in (
        _rule_war(fortress, ft), _rule_layers(ft), _rule_concentration(conc), _rule_records(accounts, brokers, flow),
        _rule_card(register, ft), _rule_savings(flow, ft), _rule_receivable(totals, ft), _rule_fire(lf),
        _rule_need_sample(ft), _rule_strengths(totals, ft, lf),
    ) if x]
    items.sort(key=lambda x: LEVEL_ORDER.get(x["level"], 9))
    for i, x in enumerate(items, 1):
        x["no"] = i
    return items


def todo_block(accounts: list, brokers: list, advice: list) -> list:
    out = []
    unfilled = [a for a in accounts if _open_unfilled(a)]
    if unfilled:
        out.append("補明細：" + "、".join(f"{a['name']}（差 {_wan(a['unfilled'])}）" for a in unfilled[:5]))
    unreg = [a["name"] for a in _unregistered(accounts)]
    if unreg:
        out.append(f"登記餘額：{len(unreg)} 個帳戶這個月還沒登記（{'、'.join(unreg[:4])}{'…' if len(unreg) > 4 else ''}）")
    plugs = [b for b in brokers if b.get("plug")]
    if plugs:
        out.append("拆證券明細：" + "、".join(f"{b['broker']} {_wan(b['plug'])}" for b in plugs))
    for a in advice:
        if a["level"] in ("bad", "warn") and a["key"] in ("war_flags", "war", "layers", "concentration", "card"):
            out.append(a["title"].rstrip("。"))
    return out


# ── 組裝 ──────────────────────────────────────────────────────────────
def build_report(month: str, basis_date: str, fortress: dict, register: dict, buckets: dict, month_cash: dict,
                 snapshots: list, prev: Optional[dict] = None, generated_at: str = "") -> dict:
    """一份月報。prev＝上一份月報的 payload（沒有＝第一份；那時只拿最近一次淨值快照當總資產的比較基準）。"""
    liabilities = (fortress.get("ladder") or {}).get("liabilities") or {}
    totals = totals_block(buckets, liabilities, cash_usable=(fortress.get("cash") or {}).get("l1_3"))
    prev_totals = (prev or {}).get("totals")
    prev_label = (prev or {}).get("month")
    if not prev_totals and snapshots:
        # 第一份：快照只有總數（＝總資產），其他分項不比
        last = [s for s in snapshots if str(s.get("date") or "") < basis_date]
        if last:
            prev_totals = {"assets": _i(last[-1].get("total"))}
            prev_label = f"快照 {str(last[-1].get('date'))[:10]}"
    delta = compare(totals, prev_totals)
    flow = flow_block(month_cash, delta, through=basis_date)
    accounts = accounts_block(register, prev, month)
    brokers = brokers_block(register, prev)
    conc = concentration(fortress)
    ft = fortress_block(fortress)
    lf = ladder_fire_block(fortress)
    health = health_block(ft, conc, accounts, flow)
    advice = advice_block(fortress, ft, conc, accounts, brokers, flow, totals, lf, register)
    trend = [{"date": str(s.get("date"))[:10], "total": _i(s.get("total"))} for s in snapshots][-TREND_POINTS:]
    trend.append({"date": basis_date, "total": totals["assets"], "now": True})
    return {
        "month": month, "basis_date": basis_date, "generated_at": generated_at, "first": prev is None,
        "totals": totals, "prev": {"label": prev_label, "totals": prev_totals} if prev_totals else None, "delta": delta,
        "flow": flow, "trend": trend, "accounts": accounts, "brokers": brokers, "concentration": conc,
        "fortress": ft, "ladder_fire": lf, "health": health, "advice": advice,
        "todo": todo_block(accounts, brokers, advice),
    }
