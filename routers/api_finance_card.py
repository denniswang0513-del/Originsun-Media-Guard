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

from core.card_statement import (merchant_key, parse_card_statement,
                                 suggest_rows)
from core.db_guard import db_factory_or_503 as _factory_or_503
from core.schemas import CardAiSuggestPayload, CardImportApply
from routers.crm._shared import _assert_rows_open, _parse_shoot_date

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


async def _existing_card_keys(session, ent: str, dates: list) -> set:
    """同帳本、落在卡單日期範圍內的既有 card 列 → {(date, |金額|)}。"""
    from sqlalchemy import select

    from db.models import CrmCashEntry
    if not dates:
        return set()
    lo = _parse_shoot_date(min(dates))
    hi = _parse_shoot_date(max(dates))
    rows = (await session.execute(
        select(CrmCashEntry.entry_date, CrmCashEntry.expense)
        .where(CrmCashEntry.entity == ent,
               CrmCashEntry.status == "card",
               CrmCashEntry.entry_date >= lo,
               CrmCashEntry.entry_date <= hi))).all()
    return {(d.strftime("%Y-%m-%d"), abs(int(e or 0))) for d, e in rows if d}


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

    out = suggest_rows(res.rows, hist, rules, seen)
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
    from db.models import CrmCashEntry
    factory = _factory_or_503()
    async with factory() as session:
        dated = []
        for r in payload.rows:
            d = _parse_shoot_date(r.date)
            if not d:
                raise HTTPException(status_code=422, detail=f"日期無法解析：{r.date}")
            dated.append((f"{r.date} {(r.note or '')[:20]}", d))
        await _assert_rows_open(session, dated, entity=ent)
        seen = await _existing_card_keys(session, ent, [r.date for r in payload.rows])
        made, skipped = 0, 0
        for r, (_lbl, d) in zip(payload.rows, dated):
            if (r.date, abs(int(r.amount))) in seen:
                skipped += 1
                continue
            item = (r.category or "").split("_", 1)
            session.add(CrmCashEntry(
                id=uuid.uuid4().hex, entity=ent, entry_date=d,
                expense=int(r.amount),
                summary=(r.note or "（卡單）")[:250],
                note="[卡單匯入]",
                category=r.category or None,
                item=item[1] if len(item) > 1 else None,
                status="card", has_invoice=0,
            ))
            seen.add((r.date, abs(int(r.amount))))
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

    # 白名單 = 對映表 ∪ 帳上在用（與 cash options 同精神：兩個來源缺一不可）
    from sqlalchemy import distinct, select

    from db.models import CrmCashEntry, FinanceCategoryMap
    factory = _factory_or_503()
    async with factory() as session:
        mapped = (await session.execute(
            select(FinanceCategoryMap.category_text)
            .where(FinanceCategoryMap.source == "cash",
                   FinanceCategoryMap.active.is_(True)))).scalars().all()
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
    # 容錯抽 JSON（claude 偶帶 fence/前後語）
    s = out.strip()
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
