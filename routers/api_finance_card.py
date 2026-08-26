# -*- coding: utf-8 -*-
"""api_finance_card.py — 信用卡帳單匯入（卡單明細 → 收支明細）＋自動分類建議。

私帳月結流（docs/LEDGER_ENTITY_PLAN.md 延伸，2026-08-24 owner「上傳對帳單、
信用卡帳單，最好可以自動判讀分類」）的信用卡側。銀行側走既有
api_finance_stmt（餘額鏈）；這裡是卡單side —— 沒有餘額欄，解析在
core/card_statement.py（純函式），本檔只做守衛、建議與寫入。

分類建議三層（preview 逐列附上，人確認後 apply）：
1. 規則層：既有 BankImportRule 關鍵字規則（與銀行對帳單同一張表、同一個
   優先序 —— 不另發明第二套規則系統）。
2. 歷史對映層：已入帳明細按 merchant_key 歸一後的多數決 —— 只在該商家歷史
   **完全一致**時建議（2026-08-24 用 owner 4,459 筆歷史實測：規則+一致歷史
   ≈ 38% 全自動；不一致就寧可留白給下一層）。
3. AI 層：/card-statement/ai-suggest 把剩下的整批丟 claude CLI（共用
   seo_runner._call_claude 的閘與逾時），回建議由人在預覽逐列採納。

寫入規矩（與 scripts/import_my_ledger.py 的卡消費列同款）：
- expense=金額（退款為負）、bank_account_id=NULL（刷卡當下不動銀行）、
  status='card'。月底繳款列在銀行對帳單那側以 transfer 清償 —— 卡單裡的
  繳款列（kind='payment'）預設排除，匯了就是重複支出。
- 手續費列（kind='fee'，國外交易服務費）分類繼承前一筆消費。
- 重複防護兩層：preview 標 duplicate（同帳本同日同額的 card 列）預設不勾；
  apply 再驗一次直接跳過 —— 與 bank-statement/apply 同一個理由：全選與
  逾時重送兩條路都繞得過顯示層。
"""
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from config import load_settings, save_settings
from core.card_statement import (mark_duplicates, merchant_key,
                                 parse_card_statement, suggest_rows)
from core.db_guard import db_factory_or_503 as _factory_or_503
from core.schemas import (CardAiSuggestPayload, CardImportApply,
                          CardLedgerConfig)
from routers.crm._shared import (_assert_rows_open, _fmt_day,
                                 _parse_shoot_date)

from .api_finance import _guard
from .api_finance_stmt import (_STMT_MAX_BYTES, _load_import_rules,
                               _statement_text)

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])

async def _history_map(session, ent: str) -> dict:
    """已入帳明細 → {merchant_key: category}，只收歷史完全一致的商家。"""
    from collections import Counter, defaultdict

    from sqlalchemy import select

    from db.models import CrmCashEntry
    rows = (await session.execute(
        select(CrmCashEntry.summary, CrmCashEntry.category)
        .where(CrmCashEntry.entity == ent,
               CrmCashEntry.category.isnot(None),
               CrmCashEntry.category != ""))).all()
    by_key: dict = defaultdict(Counter)
    for summary, category in rows:
        k = merchant_key(summary or "")
        if k:
            by_key[k][category] += 1
    return {k: next(iter(c)) for k, c in by_key.items()
            if len(c) == 1 and sum(c.values()) >= 2}


async def _existing_card_keys(session, ent: str, dates: list):
    """帳上已有的卡費 (日期, |金額|) → **筆數**。preview 標記、apply 擋下。

    兩條規則照銀行側的正本（api_finance_stmt._existing_entry_keys），都是實測換來的：
    🔴 回 Counter 不是 set —— 同一天真的可能刷兩筆一樣的錢（兩杯一樣的咖啡）。
       用 set 的話帳上有一筆就把第二筆也吃掉，而且完全沒有跡象。
    🔴 前後各放寬一天 —— _parse_shoot_date 造的是 UTC 午夜，跟 timestamptz 比較
       時邊界會偏，「卡單最後一天」的列永遠判不出重複，而那正是重複匯入時最常
       撞到的一天。真正的比對交給 (日期, 金額) 鍵。
    """
    from collections import Counter
    from datetime import timedelta

    from sqlalchemy import select

    from db.models import CrmCashEntry
    out = Counter()
    if not dates:
        return out
    lo = _parse_shoot_date(min(dates)) - timedelta(days=1)
    hi = _parse_shoot_date(max(dates)) + timedelta(days=1)
    rows = (await session.execute(
        select(CrmCashEntry.entry_date, CrmCashEntry.expense)
        .where(CrmCashEntry.entity == ent,
               CrmCashEntry.status == "card",
               CrmCashEntry.entry_date >= lo,
               CrmCashEntry.entry_date <= hi))).all()
    for d, e in rows:
        if d:
            out[(_fmt_day(d), abs(int(e or 0)))] += 1
    return out


@router.post("/card-statement/preview")
async def preview_card_statement(
        request: Request,
        entity: str = "",
        text: str = Form(""),
        file: UploadFile | None = File(None)):
    """上傳/貼上信用卡帳單 → 解析＋三層分類建議。**只讀不寫**。"""
    # 先驗身份再碰檔案（與 bank-statement/preview 同一個理由）
    ent = _guard(request, entity, level="full")
    text = (text or "").strip()
    if file is not None:
        blob = await file.read()
        if len(blob) > _STMT_MAX_BYTES:
            raise HTTPException(status_code=422, detail="檔案過大（上限 8MB）")
        import asyncio
        text = await asyncio.to_thread(_statement_text, file.filename or "", blob)
    if not text.strip():
        raise HTTPException(status_code=422, detail="沒有收到卡單內容（檔案或貼上的文字）")

    res = parse_card_statement(text)
    if not res.ok:
        return {"ok": False, "errors": res.errors, "warnings": res.warnings,
                "skipped": res.skipped[:20]}

    factory = _factory_or_503()
    async with factory() as session:
        hist = await _history_map(session, ent)
        rules = await _load_import_rules(session)
        seen = await _existing_card_keys(session, ent, [r.date for r in res.rows])

    out = suggest_rows(res.rows, hist, rules, seen)   # Counter 直接進去（消耗式比對）
    n_sug = sum(1 for x in out if x["category"])
    return {
        "ok": True,
        "rows": out,
        "payments": [{"date": p.date, "amount": p.amount, "note": p.note}
                     for p in res.payments],
        "warnings": res.warnings, "skipped": res.skipped[:20],
        "summary": {"count": len(out), "total_spend": res.total_spend,
                    "total_refund": res.total_refund,
                    "suggested": n_sug, "unsuggested": len(out) - n_sug},
    }


@router.post("/card-statement/apply")
async def apply_card_statement(payload: CardImportApply, request: Request,
                               entity: str = ""):
    """把確認過的卡單列寫進收支明細。重複列（同日同額既有 card）直接跳過。"""
    ent = _guard(request, entity, level="full")
    if not payload.rows:
        raise HTTPException(status_code=422, detail="沒有要匯入的列")
    if not any(r.selected for r in payload.rows):
        raise HTTPException(status_code=422, detail="沒有勾選任何列")
    from db.models import CrmCashEntry
    factory = _factory_or_503()
    async with factory() as session:
        dated = []
        for r in payload.rows:
            d = _parse_shoot_date(r.date)
            if not d:
                raise HTTPException(status_code=422, detail=f"日期無法解析：{r.date}")
            dated.append((f"{r.date} {(r.note or '')[:20]}", d))
        seen = await _existing_card_keys(session, ent, [r.date for r in payload.rows])
        # 重複判定與 preview 共用同一份實作 —— payload 帶的是整份卡單（含沒勾
        # 的列），apply 看到的輸入才與 preview 相同，兩邊不會給出不同答案。
        dups = mark_duplicates([(r.date, abs(int(r.amount))) for r in payload.rows], seen)
        todo = [(r, d) for r, (_l, d), dup in zip(payload.rows, dated, dups)
                if r.selected and not dup]
        skipped = sum(1 for r, dup in zip(payload.rows, dups) if r.selected and dup)
        if not todo:
            return {"status": "ok", "made": 0, "skipped_duplicates": skipped}
        # 月結鎖只驗**真的要寫**的那幾列 —— 沒勾的列不該讓整批 409
        await _assert_rows_open(
            session, [(f"{r.date} {(r.note or '')[:20]}", d) for r, d in todo],
            entity=ent)
        made = 0
        card_id = (payload.card_account_id or "").strip() or None
        if card_id:
            # 卡別必須是這本帳的**卡片**帳戶 —— 掛到銀行帳戶上，刷卡金額會被
            # 當成銀行流水，餘額與卡債一起錯
            from core.finance_logic import CARD_KIND
            from db.models import BankAccount
            acct = await session.get(BankAccount, card_id)
            if (not acct or (acct.entity or "parent") != ent
                    or (acct.acct_kind or "") != CARD_KIND):
                raise HTTPException(status_code=422, detail="卡別不存在或不是信用卡帳戶")
        for r, d in todo:
            item = (r.category or "").split("_", 1)
            session.add(CrmCashEntry(
                id=uuid.uuid4().hex, entity=ent, entry_date=d,
                expense=int(r.amount),
                bank_account_id=card_id,      # 卡別身分（見 CardImportApply 說明）
                summary=(r.note or "（卡單）")[:250],
                # 雙備註（2026-08-26）：note 留給人手寫；卡單來源由 status='card'
                # ＋created_at 說明，不再寫「[卡單匯入]」標記汙染附註欄
                category=r.category or None,
                item=item[1] if len(item) > 1 else None,
                status="card", has_invoice=0,
            ))
            made += 1
        await session.commit()
    return {"status": "ok", "made": made, "skipped_duplicates": skipped}


@router.post("/card-statement/ai-suggest")
async def ai_suggest_categories(payload: CardAiSuggestPayload, request: Request,
                                entity: str = ""):
    """規則與歷史都沒答案的列 → claude CLI 整批建議。同步（月結一次性使用）。

    回 {suggestions: {index: category}}；claude 不可用或回不出 JSON → 422 帶
    診斷（不猜、不留半套）。建議值一律驗證在 options 白名單內才回。
    """
    ent = _guard(request, entity, level="full")
    if not payload.rows:
        raise HTTPException(status_code=422, detail="沒有要判讀的列")
    if len(payload.rows) > 300:
        raise HTTPException(status_code=422, detail="一次最多 300 列")

    # 白名單 = 對映表 ∪ 帳上在用（與 cash options 同精神：兩個來源缺一不可）。
    # 對映表那半走 crm._shared.cash_category_texts —— 那支自稱唯一正本，是因為
    # 這份清單曾經散在四個地方、active 的寫法還不一致，結果自家匯入寫出來的列
    # 使用者在編輯視窗裡選不到自己的類別。
    from sqlalchemy import distinct, select

    from db.models import CrmCashEntry
    from routers.crm._shared import cash_category_texts
    factory = _factory_or_503()
    async with factory() as session:
        mapped = await cash_category_texts(session)
        in_use = (await session.execute(
            select(distinct(CrmCashEntry.category))
            .where(CrmCashEntry.entity == ent,
                   CrmCashEntry.category.isnot(None)))).scalars().all()
    options = sorted({c for c in [*mapped, *in_use] if c})

    import json
    lines = "\n".join(f"{i}\t{r.note}\t{r.amount}" for i, r in enumerate(payload.rows))
    prompt = (
        "你是記帳分類器。下面是信用卡/銀行交易（index、商家摘要、金額，Tab 分隔），"
        "請為每一列從【選項】中挑最合適的分類。\n"
        "規則：只能用選項裡的字串，一字不差；沒把握就跳過該列（不要硬猜）；"
        "只輸出 JSON 物件（index 字串 → 分類），不要任何其他文字。\n"
        f"【選項】{json.dumps(options, ensure_ascii=False)}\n"
        f"【交易】\n{lines}\n")
    from services.website.seo_runner import _call_claude
    out, err = await _call_claude(prompt)
    if out is None:
        raise HTTPException(status_code=422, detail=f"AI 判讀失敗：{err}")
    # 容錯抽 JSON。fence 交給 seo_runner.strip_fence —— 那是 claude 回應的
    # 後處理正本（同一層、同一個 _call_claude 旁邊）；自己再寫一次的版本會漏掉
    # ```json 圍欄，只靠大括號掃描碰運氣。
    from services.website.seo_runner import strip_fence
    s = strip_fence(out)
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        raise HTTPException(status_code=422, detail="AI 回覆不含 JSON 物件")
    try:
        raw = json.loads(s[a:b + 1])
    except ValueError:
        raise HTTPException(status_code=422, detail="AI 回覆的 JSON 解析失敗")
    ok_set = set(options)
    suggestions = {str(k): v for k, v in raw.items()
                   if isinstance(v, str) and v in ok_set
                   and str(k).isdigit() and int(k) < len(payload.rows)}
    return {"suggestions": suggestions, "options": options}


# ── 卡片餘額與還款記帳 ────────────────────────────────────────
#
# 為什麼不把信用卡做成 bank_accounts 的一種 acct_kind：
# core/finance_logic.split_bank_lines 把「不是股東往來」的帳戶一律歸進 cash，
# 卡債（負債）會變成資產負債表上的負現金 —— 靜默弄壞三表。所以卡片餘額用
# 收支列推導，不進 bank_accounts。
#
# 口徑：未繳卡費 = 期初 + Σ刷卡 − Σ還款淨額
#   刷卡    = status='card' 的列（expense；退刷為負）
#   還款淨額 = 還款類別的列（expense − deposit），且**不是**刷卡列
#   期初    = 資料起點之前就存在的卡債。owner 的資料從 2023/10 起，
#            卡片在那之前已有餘額 —— 不給期初就永遠對不平（2026-08-25 實測：
#            刷卡 3,720,973 − 還款 3,757,961 = −36,988，而 Sheet 當時是 +2,428）。
#
# 期初與還款類別存 settings['card_ledger'][entity]，每本帳各自一份。

def _card_cfg(ent: str) -> dict:
    from core.card_statement import card_cfg
    return card_cfg(load_settings(), ent)


def _card_cfg_from(cfg: dict) -> dict:
    """正規化一本帳的卡片設定。吃 dict 而不是自己讀檔 —— PUT 改完還款類別後
    要用**改過的**那份重算，從磁碟讀回來的是舊的（/simplify 第 4 輪抓到）。
    正本在 core.card_statement.normalize_card_cfg（報表引擎的卡債列也走那份）。"""
    from core.card_statement import normalize_card_cfg
    return normalize_card_cfg(cfg)


async def _card_numbers(ent: str, cfg: dict) -> dict:
    """{charges, charge_count, repayments} —— 依 cfg 給的還款類別算。

    設定由呼叫端傳進來（不自己讀檔），PUT 才能用剛改好的類別重算。
    """
    from sqlalchemy import func as fn
    from sqlalchemy import select

    from db.models import CrmCashEntry
    factory = _factory_or_503()
    async with factory() as session:
        # 一次掃過去，用 FILTER 分三個聚合 —— 原本是三支 WHERE 幾乎相同的
        # 查詢各跑一次（刷卡合計／刷卡筆數／還款），對 NAS Postgres 就是三個
        # 來回。這支每次開收支表都會被呼叫。
        is_card = CrmCashEntry.status == "card"
        is_repay = (CrmCashEntry.status.is_distinct_from("card")
                    & CrmCashEntry.category.in_(cfg["repay_categories"]))
        row = (await session.execute(
            select(
                fn.coalesce(fn.sum(CrmCashEntry.expense).filter(is_card), 0),
                fn.count().filter(is_card),
                fn.coalesce(fn.sum(CrmCashEntry.expense).filter(is_repay), 0),
                fn.coalesce(fn.sum(CrmCashEntry.deposit).filter(is_repay), 0),
            ).where(CrmCashEntry.entity == ent))).one()
        charges, n_charges = int(row[0] or 0), int(row[1] or 0)
        repay_out, repay_in = int(row[2] or 0), int(row[3] or 0)
    return {"charges": charges, "charge_count": n_charges,
            "repayments": repay_out - repay_in}


def _card_view(cfg: dict, n: dict) -> dict:
    return {"opening": cfg["opening"], **n,
            "outstanding": cfg["opening"] + n["charges"] - n["repayments"],
            "repay_categories": cfg["repay_categories"]}


@router.get("/card-summary")
async def card_summary(request: Request, entity: str = ""):
    """卡片餘額：期初 + 刷卡 − 還款。回傳各分項供 UI 說明數字怎麼來的。

    另附 `by_card`：逐卡刷卡金額（owner 2026-08-27「區隔哪一個銀行的信用卡」）。
    🔴 只拆刷卡那半 —— 還款是從銀行付的、帳上沒記還的是哪張卡，硬分是猜的，
    所以「未繳」仍是全域一個數（見 core.card_statement.charges_by_card）。
    """
    ent = _guard(request, entity, level="full")
    cfg = _card_cfg(ent)
    view = _card_view(cfg, await _card_numbers(ent, cfg))
    from sqlalchemy import select

    from core.card_statement import charges_by_card
    from core.finance_logic import CARD_KIND
    from db.models import BankAccount, CrmCashEntry
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmCashEntry.entry_date, CrmCashEntry.expense,
                   CrmCashEntry.deposit, CrmCashEntry.status,
                   CrmCashEntry.bank_account_id)
            .where(CrmCashEntry.entity == ent, CrmCashEntry.status == "card"))).all()
        cards = (await session.execute(
            select(BankAccount).where(BankAccount.entity == ent,
                                      BankAccount.acct_kind == CARD_KIND)
            .order_by(BankAccount.sort_order, BankAccount.name))).scalars().all()
    charges = charges_by_card([{"entry_date": d, "expense": e, "deposit": dp,
                                "status": st, "bank_account_id": b}
                               for d, e, dp, st, b in rows])
    view["by_card"] = [{"id": a.id, "name": a.name, "charges": charges.get(a.id, 0),
                        "opening": int(a.opening_balance or 0)} for a in cards]
    view["unassigned_charges"] = charges.get("", 0)
    return view


@router.put("/card-summary")
async def update_card_summary(payload: CardLedgerConfig, request: Request,
                              entity: str = ""):
    """設定卡片期初／還款類別。

    `derive_opening_from`：給「現在實際欠多少」，反推期初 —— 對帳單上的未繳
    金額是使用者手上唯一可信的數字，要他自己回推 2023 年的期初不合理。
    """
    ent = _guard(request, entity, level="full")
    settings = load_settings()
    book = settings.setdefault("card_ledger", {}).setdefault(ent, {})
    data = payload.model_dump(exclude_unset=True)
    if "repay_categories" in data and data["repay_categories"] is not None:
        book["repay_categories"] = [str(x).strip() for x in data["repay_categories"]
                                    if str(x).strip()]
    cfg = _card_cfg_from(book)          # 用剛改好的設定算，不從磁碟讀回舊的
    n = await _card_numbers(ent, cfg)
    if data.get("derive_opening_from") is not None:
        # 目標未繳 = 期初 + 刷卡 − 還款 → 期初 = 目標 − (刷卡 − 還款)
        book["opening"] = int(data["derive_opening_from"]) - (n["charges"] - n["repayments"])
    elif data.get("opening") is not None:
        book["opening"] = int(data["opening"])
    save_settings(settings)
    cfg["opening"] = int(book["opening"])
    return _card_view(cfg, n)
