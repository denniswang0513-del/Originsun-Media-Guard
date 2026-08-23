"""
api_finance_stmt.py — 對帳單匯入（銀行明細檔 → 收支明細＋貸款繳款）

2026-08-21 從 api_finance.py 尾端整段搬出來，**只是搬家，一行邏輯沒改**，
URL 一條都沒變（同樣掛 /api/v1/finance）。

搬的理由：api_finance.py 到了 2,374 行，而這一段自成一個子系統 —— 上傳一份
銀行對帳單（PDF/CSV/純文字）→ 解析成列 → 套分類規則 → 人工在預覽逐列決定
（掛專案、掛發票、改類別）→ 可存草稿改天再回來 → 按下匯入才真的寫帳。
它跟檔案前半的科目/對帳/調整/貸款/三表沒有共用狀態。

耦合是單向的，切之前量過：這一段用到前半 5 個名字（見下方 import），
前半用到這一段 **0 個**。所以這裡 import api_finance 不會有循環。

自己開一個 APIRouter（不是 import 前半那顆）—— main.py 的 `_ROUTER_MODULES`
迴圈對每個模組做一次 `include_router(mod.router)`，兩個模組共用同一顆 router
物件會被掛兩次。註冊位置就在 'api_finance' 後面。

🔴 main.py 那個迴圈的 try/except 只印一行 WARN 就跳過 —— 這個檔案 import 錯
會變成整個對帳單匯入靜默 404（服務照常起來）。改完務必打一下
/api/v1/finance/import-rules 與 /finance/bank-statement/drafts。
"""

import json
import re
import uuid
from datetime import datetime, timedelta

from fastapi import (APIRouter, File, Form, HTTPException,  # type: ignore
                     Request, UploadFile)

from core.db_guard import db_factory_or_503 as _factory_or_503
from core.finance_logic import local_day
from core.schemas import (BankImportRulePayload, StatementDraftPayload,
                          StatementImportApply)
from routers.crm._shared import (_assert_rows_open, _fmt_minute, _parse_day,
                                 _username)

# 前半的東西：守門一支、貸款三支（對帳單裡認出來的繳款要寫進既有的攤還表）
from .api_finance import (_acct_and_entity, _get_loan_and_period, _guard,
                          _load_loans_with_payments, _record_loan_payment,
                          recognize_transfer_fees_in)

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


# ── 對帳單匯入（上傳銀行明細 → 自動換算成收支＋貸款繳款）────────────

_STMT_MAX_BYTES = 8 * 1024 * 1024      # 對帳單就是幾頁文字，超過必是傳錯檔


def _statement_text(filename: str, blob: bytes) -> str:
    """上傳檔 → 純文字。PDF 走 core.doc_text（含康熙部首正規化），其餘當文字。"""
    import os
    import tempfile

    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".pdf":
        from core.doc_text import extract_text
        fd, tmp = tempfile.mkstemp(suffix=".pdf")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
            txt, err = extract_text(tmp)     # 同步 CPU-bound，呼叫端已包 to_thread
            if err:
                raise HTTPException(status_code=422, detail=f"讀不了這個 PDF：{err}")
            return txt
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    for enc in ("utf-8-sig", "big5", "cp950"):
        try:
            return blob.decode(enc)
        except UnicodeDecodeError:
            continue
    return blob.decode("utf-8", errors="replace")


async def _loans_for_matching(session, entity: str) -> list:
    """配對用的貸款清單：帶 account_no 與全部期別（含已繳，供重匯偵測）。"""
    loans, by_loan = await _load_loans_with_payments(session, entity)
    out = []
    for l in loans:
        rows_all = by_loan.get(l.id, [])
        # **全部**期別（含已繳）都給配對器：配對是照「到期日最接近」挑的，
        # 已繳期別要在裡面，重匯同一份對帳單才認得出「這期已經記過了」——
        # 少了它，重匯會被配到下一期並蓋上錯的繳款日。
        periods = [{"period_no": r.period_no,
                    "total": (r.principal_due or 0) + (r.interest_due or 0),
                    "due_date": (local_day(r.due_date).strftime("%Y-%m-%d")
                                 if r.due_date else None),
                    "paid": (r.status or "") == "paid"} for r in rows_all]
        # 這裡刻意**不**算「下一期是哪一期」—— _load_loans_with_payments 的
        # docstring 已經說了「分開寫的話很容易長出第二種定義」，而配對器
        # （match_loan_payments）只讀 id/name/account_no/periods，回應也不帶
        # loans。算了沒人看的第二種定義，就是在等它跟 list_loans 那份漂開。
        out.append({
            "id": l.id, "name": l.name, "account_no": l.account_no or "",
            "periods": periods,
        })
    return out


async def _existing_entry_keys(session, acct_id, dates):
    """帳上已有的 (日期, 帶號金額) → 筆數。對帳單匯入的重複偵測用。

    preview 拿它標「已匯過」、apply 拿它**真的擋下來** —— 只活在 preview 顯示層
    的防護等於沒有：表頭全選會把 duplicate 列一起勾起來、送出逾時重按也一樣，
    兩條路都直接寫進 CrmCashEntry，同日同額的收支就變兩份，餘額與三張報表全部
    雙倍（貸款列有 status=="paid" 保護，一般列一個都沒有）。

    回 Counter 而不是 set：對帳單同一天真的可能有兩筆一樣的三十元手續費 ——
    帳上已有一筆就只跳過一筆，第二筆照匯。
    """
    from collections import Counter

    from sqlalchemy import and_, select

    from db.models import CrmCashEntry
    out = Counter()
    if not dates:
        return out
    # 🔴 前後各放寬一天：_parse_day 回 naive datetime，跟 timestamptz 欄位比較時
    # 邊界會偏掉，實測「對帳單最後一天」的列永遠判不出重複 —— 而那正是使用者
    # 重複匯入時最常撞到的一天。真正的比對交給 (日期, 金額) 鍵。
    lo = _parse_day(dates[0]) - timedelta(days=1)
    hi = _parse_day(dates[-1]) + timedelta(days=1)
    rows = (await session.execute(
        select(CrmCashEntry.entry_date, CrmCashEntry.expense, CrmCashEntry.deposit)
        .where(and_(CrmCashEntry.bank_account_id == acct_id,
                    CrmCashEntry.entry_date >= lo,
                    CrmCashEntry.entry_date <= hi)))).all()
    for dt, exp, dep in rows:
        if dt:
            out[(local_day(dt).strftime("%Y-%m-%d"), (dep or 0) - (exp or 0))] += 1
    return out


# ── 對帳單分類規則（bank_import_rules）──────────────────────
#
# 這一層在 finance_category_map **之前**：
#     銀行摘要文字 →〔本組端點〕→ 類別 →〔category_map〕→ 會計科目
# 右半邊本來就是資料驅動的，左半邊原本寫死在 core/bank_statement.KEYWORD_RULES。


def _rule_priority_key(bank_account_id: str = ""):
    """規則的命中優先序。**清單顯示與實際比對共用這一支** —— 各寫一份的話，
    畫面上說「由上而下比對，先命中的先贏」就會是假的。

    帳戶專屬的排在通用的前面：合庫寫「攤還本息」、一銀寫「中小７月」，同一件事
    兩種寫法 —— 綁這個帳戶的規則要贏過「所有帳戶」的。同層再照 sort_order。
    沒指定帳戶時（清單頁沒選帳戶），所有綁帳戶的一律排在通用的前面。
    """
    def key(r):
        if bank_account_id:
            tier = 0 if r.bank_account_id == bank_account_id else (
                2 if r.bank_account_id else 1)
        else:
            tier = 0 if r.bank_account_id else 1
        return (tier, r.sort_order, r.keyword or "")
    return key


async def _load_import_rules(session, bank_account_id: str = ""):
    """回 [(關鍵字, category, 方向, 只在哪個方向)]，已照優先序排好給 _classify 用。"""
    from sqlalchemy import select

    from db.models import BankImportRule
    rows = (await session.execute(
        select(BankImportRule).where(BankImportRule.active.is_(True)))).scalars().all()
    # 別家帳戶專屬的規則不參與這次比對（tier 2）
    rows = [r for r in rows
            if not r.bank_account_id or r.bank_account_id == bank_account_id]
    rows.sort(key=_rule_priority_key(bank_account_id))
    return [(r.keyword, r.category, int(r.direction or 0),
             int(r.only_direction or 0)) for r in rows]


def _rule_dict(r) -> dict:
    return {"id": r.id, "keyword": r.keyword, "category": r.category,
            "bank_account_id": r.bank_account_id or "",
            "sort_order": r.sort_order, "active": bool(r.active),
            "only_direction": int(r.only_direction or 0),
            "note": r.note or ""}


@router.get("/import-rules")
async def list_import_rules(request: Request, bank_account_id: str = ""):
    """規則清單。顯示順序＝命中優先序（共用 _rule_priority_key）。

    帶 bank_account_id 就是「這個帳戶實際會怎麼比」；不帶則是綁帳戶的一律在前
    （多帳戶時那不等於任何一個帳戶的真實順序，所以畫面要嘛選帳戶、要嘛別宣稱）。
    """
    _guard(request, level="full")
    from sqlalchemy import select

    from db.models import BankImportRule
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(select(BankImportRule))).scalars().all()
    rows.sort(key=_rule_priority_key(bank_account_id))
    return {"items": [_rule_dict(r) for r in rows]}


async def _apply_rule_payload(r, payload, *, require_all: bool, session):
    """驗規則欄位並寫進 r。新增與修改共用 —— 各寫一份的話，防呆會只加在一邊。

    require_all：新增時關鍵字與類別都必填；修改時允許只送要改的那個。
    """
    kw = (payload.keyword or "").strip()
    cat = (payload.category or "").strip()
    if require_all and (not kw or not cat):
        raise HTTPException(status_code=422, detail="關鍵字與類別都要填")
    if kw:
        _assert_usable_keyword(kw)
    await _assert_known_category(cat, session)
    r.keyword = kw or r.keyword
    r.category = cat or r.category
    r.bank_account_id = payload.bank_account_id or None
    od = int(payload.only_direction or 0)
    if od not in (-1, 0, 1):
        raise HTTPException(status_code=422, detail="方向條件只能是 -1／0／+1")
    r.only_direction = od
    r.sort_order = payload.sort_order
    r.active = payload.active
    r.note = (payload.note or "")[:255]
    return r


@router.post("/import-rules")
async def create_import_rule(payload: BankImportRulePayload, request: Request):
    _guard(request, level="full")
    from db.models import BankImportRule
    factory = _factory_or_503()
    async with factory() as session:
        r = await _apply_rule_payload(
            BankImportRule(id=uuid.uuid4().hex, direction=0), payload,
            require_all=True, session=session)
        session.add(r)
        await session.commit()
        return _rule_dict(r)


@router.put("/import-rules/{rule_id}")
async def update_import_rule(rule_id: str, payload: BankImportRulePayload,
                             request: Request):
    _guard(request, level="full")
    from db.models import BankImportRule
    factory = _factory_or_503()
    async with factory() as session:
        r = await session.get(BankImportRule, rule_id)
        if not r:
            raise HTTPException(status_code=404, detail="規則不存在")
        await _apply_rule_payload(r, payload, require_all=False, session=session)
        r.updated_at = datetime.now()
        await session.commit()
        return _rule_dict(r)


@router.delete("/import-rules/{rule_id}")
async def delete_import_rule(rule_id: str, request: Request):
    _guard(request, level="full")
    from db.models import BankImportRule
    factory = _factory_or_503()
    async with factory() as session:
        r = await session.get(BankImportRule, rule_id)
        if not r:
            raise HTTPException(status_code=404, detail="規則不存在")
        await session.delete(r)
        await session.commit()
    return {"ok": True}


# 日期樣（含 09-08 這種沒有年份的）與純數字串 —— 這種關鍵字會把「所有那天/
# 那個號碼的交易」一網打盡，而且完全看不出是錯的。
# 尾端允許殘留的分隔符 —— `2026/08/` 正是原本那個 bug 的字面值（slice(0,8) 切出來的），
# 不收尾綴的話它會漏網，而它恰好是最危險的那一個。
_KW_DATEISH = re.compile(r"^[\d０-９]{1,4}([/\-.][\d０-９]{0,2}){1,2}$")
_KW_NUMISH = re.compile(r"^[\d０-９]+$")


def _assert_usable_keyword(kw: str):
    """關鍵字的形狀防呆。

    🔴 這條擋的是**會生效但意思完全不對**的規則：
    - `2026/08/` → 那個月的交易全歸這一類（原本預覽的「＋規則」預設值就長這樣，
      因為摘要裡留著銀行的入帳日欄，owner 2026-08-20 看畫面時發現）
    - `150725950595` → 一個帳號，只會中那一筆，看起來卻像設好了規則
    - 單一個字 → 「中」會把「中小」「中信」「台中」全部掃進來

    錯的規則不會報錯，只會靜靜把帳分到錯的地方，所以寧可在建立時就擋。
    """
    kw = (kw or "").strip()
    if len(kw) < 2:
        raise HTTPException(status_code=422, detail="關鍵字至少兩個字 —— 單字會誤中一大片")
    if _KW_DATEISH.match(kw):
        raise HTTPException(
            status_code=422,
            detail=f"「{kw}」看起來是日期 —— 那會把那天/那個月的交易全歸成同一類。"
                   f"請改用摘要裡描述性質的字，例如「電信費」。")
    if _KW_NUMISH.match(kw):
        raise HTTPException(
            status_code=422,
            detail=f"「{kw}」是純數字（多半是帳號或流水號）—— 只會中那一筆，"
                   f"卻看起來像設好了規則。請改用描述性質的字。")


async def _assert_known_category(cat: str, session):
    """規則只能填 finance_category_map 認得的類別。

    🔴 不然規則會產出一個報表看不懂的值，那些列全部靜靜落到「未歸類」——
    而且是**匯入當下就分好類**的假象，比沒分類更難發現。

    session 必填 —— 呼叫端一定已經開著一個。在已開的 session 裡再開一個等於
    同時佔兩條連線，那是會被照抄的形狀（而且那條分支根本沒人走）。
    """
    if not cat:
        return
    from routers.crm._shared import cash_category_texts
    known = set(await cash_category_texts(session))
    if cat not in known:
        raise HTTPException(
            status_code=422,
            detail=f"「{cat}」不在收支科目對映裡 —— 規則只能填報表認得的類別。"
                   f"要新增科目請到帳務設定。")


@router.post("/import-rules/apply-unclassified")
async def apply_rules_to_unclassified(request: Request, entity: str = ""):
    """把現行規則套用到**還沒分類**的既有收支列。

    🔴 只碰 category 是空的列。已經有類別的（不管是規則分的還是人手改的）一律
    不動 —— 規則是會改的，讓它回頭重寫已經進帳的分類，等於每次調規則都在
    重寫歷史帳（owner 2026-08-20 拍板：不自動追溯）。
    """
    ent = _guard(request, entity, level="full")
    from sqlalchemy import or_, select

    from core.bank_statement import _classify
    from core.finance_logic import cash_entry_flow
    from db.models import CrmCashEntry
    factory = _factory_or_503()
    changed, by_cat = 0, {}
    async with factory() as session:
        rows = (await session.execute(
            select(CrmCashEntry).where(
                CrmCashEntry.entity == ent,
                or_(CrmCashEntry.category.is_(None), CrmCashEntry.category == "")))
        ).scalars().all()
        # 規則依帳戶不同 → 逐帳戶載一次（同帳戶的列共用）
        cache = {}
        for e in rows:
            acct = e.bank_account_id or ""
            if acct not in cache:
                cache[acct] = await _load_import_rules(session, acct)
            # 🔴 一定要傳 signed，否則帶方向條件的規則會被整批跳過
            #    （_classify：不知道方向就不假裝知道）。「薪資」兩側都有、
            #    靠方向分成代收／代發 —— 那正是這條路要分的東西。
            #    正負號用 cash_entry_flow 的定義（正=流入、負=流出）。
            cat, _d = _classify(f"{e.summary or ''} {e.note or ''}", cache[acct],
                                signed=cash_entry_flow(e))
            if cat:
                e.category = cat
                e.updated_at = datetime.now()
                changed += 1
                by_cat[cat] = by_cat.get(cat, 0) + 1
        if changed:
            await session.commit()
    return {"ok": True, "scanned": len(rows), "changed": changed, "by_category": by_cat}


@router.post("/bank-statement/preview")
async def preview_bank_statement(
        request: Request,
        bank_account_id: str = Form(""),
        text: str = Form(""),
        file: UploadFile | None = File(None)):
    """上傳/貼上銀行對帳單 → 解析＋分類＋配貸款期別。**只讀不寫**。

    輸入（multipart）：file=對帳單檔（PDF/CSV/TXT）或 text=貼上的文字，
    外加 bank_account_id。回每一列的建議動作與所有警告 —— 前端逐列給人確認，
    確認後才打 /bank-statement/apply。

    重複匯入偵測：同帳戶同日同金額已經有收支明細 → 標 duplicate=True 預設不勾。
    """

    # 🔴 先驗身份再碰檔案。原本是讀完 8MB、跑完 PDF 文字抽取（CPU-bound）才在
    # 拿到帳戶之後守衛 —— 未登入的人也能讓這台機器去解析他上傳的 PDF。
    # 下面拿到 acct 之後那次 entity 守衛照留（這次守的是「有沒有權限」，
    # 那次守的是「這個帳本在不在你的 scope 裡」），兩次不是重複。
    _guard(request, level="full")
    acct_id = (bank_account_id or "").strip()
    if not acct_id:
        raise HTTPException(status_code=422, detail="請先選擇這份對帳單是哪個銀行帳戶")
    text = (text or "").strip()
    if file is not None:
        blob = await file.read()
        if len(blob) > _STMT_MAX_BYTES:
            raise HTTPException(status_code=422, detail="檔案過大（上限 8MB）")
        import asyncio
        text = await asyncio.to_thread(_statement_text, file.filename or "", blob)
    if not text.strip():
        raise HTTPException(status_code=422, detail="沒有收到對帳單內容（檔案或貼上的文字）")

    factory = _factory_or_503()
    async with factory() as session:
        acct, ent = await _acct_and_entity(session, request, acct_id)

        return await _build_statement_preview(session, acct, ent, text)


async def _build_statement_preview(session, acct, ent, text):
    """解析對帳單 → 逐列建議 + 選項清單。**只讀不寫**。

    上傳預覽與「開啟草稿」共用這一支：草稿存的是**原始文字**，開啟時重新解析，
    才拿得到最新的重複判定（草稿放兩天，中間可能有幾列已經從別的路進帳了）
    與最新的專案／發票清單。人工掛好的專案與發票由呼叫端貼回去。
    """
    from core.bank_statement import (LOAN_CATEGORY, match_loan_payments,
                                     parse_statement)
    acct_id = acct.id
    # 分類規則走 DB（使用者可編），不是寫死那 14 條
    res = parse_statement(text, rules=await _load_import_rules(session, acct_id))
    if not res.ok:
        return {"ok": False, "errors": res.errors,
                "warnings": res.warnings, "rows": []}

    loans = await _loans_for_matching(session, ent)
    matches = match_loan_payments(res.rows, loans)
    # line_no → 該列被配到的貸款期別
    by_line = {}
    for m in matches:
        for ln in m["lines"]:
            by_line[ln] = m

    # 重複偵測：同帳戶、同日、同金額已存在 → 這列多半已經匯過
    existing = await _existing_entry_keys(
        session, acct_id, sorted({r.date for r in res.rows}))

    # 科目對映：category 沒對映的要標出來（不然匯進去報表變未歸類）
    from routers.crm._shared import cash_category_texts
    mapped = set(await cash_category_texts(session))

    # 預覽要能當場掛專案／發票（owner 2026-08-20）→ 選項一起回，
    # 前端不用再多打兩支 API（那兩支還在不同的 prefix 下）。
    from sqlalchemy import or_, select

    from core.project_link import CASH_CATEGORIES
    from db.models import Client, CrmInvoice, CrmProject
    # 挑選視窗要看得出「是哪個案子」—— 只有名稱不夠（同名/近名的案子很多）
    cmap = {c.id: c.short_name for c in
            (await session.execute(select(Client))).scalars().all()}
    projects = [{
        "id": p.id, "name": p.name,
        "client": cmap.get(p.client_id, ""),
        "status": p.status or "",
        "start": str(p.start_date)[:10] if p.start_date else "",
    } for p in (await session.execute(
        select(CrmProject).order_by(CrmProject.created_at.desc()))).scalars().all()]
    # 發票只列**還沒收齊**的收款發票 —— 已經收完的列出來只會讓人選錯
    # 方向與作廢在 SQL 就篩掉：撈回來再用 Python 篩的話，下一步的
    # _invoice_collections 會拿到全部 400 個 id，而不是真正需要的那 150 個。
    from routers.crm.finance import _invoice_collections, collection_fields
    invs = (await session.execute(
        select(CrmInvoice).where(
            CrmInvoice.entity == ent,
            or_(CrmInvoice.payment_type == "收款",
                CrmInvoice.payment_type.is_(None)),
            or_(CrmInvoice.issue_status != "作廢",
                CrmInvoice.issue_status.is_(None))))).scalars().all()
    coll = await _invoice_collections(session, [i.id for i in invs])
    open_invs = []
    for i in invs:
        # 🔴「收齊了沒」走 collection_fields（含 NT$50 匯費容差），不要自己算
        # `outstanding > 0` —— 被匯費短收 30 元的那 42 張歷史發票，在收支明細的
        # 下拉裡是「收齊、不列出」，在這個挑選視窗卻會冒出來說「尚欠 $30」，
        # 引人再掛一次款。欄名也用發票的正式欄名，兩個挑選器才有共用的可能。
        c = collection_fields(i.amount_total, coll.get(i.id))
        if c["settled"]:
            continue
        open_invs.append({
            "id": i.id, "invoice_number": i.invoice_number or "",
            "title": i.title or "", "company_name": i.company_name or "",
            "date": str(i.invoice_date)[:10] if i.invoice_date else "",
            "amount_total": int(i.amount_total or 0), **c})
    # 排序交給前端 —— 它要依「跟該列入帳金額的接近程度」排，而那是逐列不同的
    open_invs.sort(key=lambda x: (x["date"] or "", x["invoice_number"]), reverse=True)

    # 支出列的鏡像：請款單只列**還沒付滿**的（owner 2026-08-23：「項目勾請款單時，
    # 可以讓我勾是哪一筆請款單的項目來對帳，像是勾發票那樣」）。
    # 🔴「付滿了沒」走 amount_is_settled（同一條 NT$50 容差）—— 被跨行手續費短付
    #    30 元的那些，用 `已付 < 金額` 判會一直冒出來引人再掛一次款。判準跟
    #    resettle_payment_requests 是同一支，不然挑選視窗與請款單狀態會各講各的。
    from routers.crm.finance import _payment_allocated
    from core.finance_logic import amount_is_settled
    from db.models import CrmPaymentRequest
    aps = (await session.execute(
        select(CrmPaymentRequest).where(
            CrmPaymentRequest.entity == ent))).scalars().all()
    paid_map = await _payment_allocated(session, [a.id for a in aps])
    open_pays = []
    for a in aps:
        got = paid_map.get(a.id, 0)
        if amount_is_settled(got, a.amount):
            continue
        open_pays.append({
            "id": a.id, "summary": a.summary or "",
            "payee_name": a.payee_name or "",
            "category": a.category or "",
            "date": str(a.request_date)[:10] if a.request_date else "",
            "amount_total": int(a.amount or 0),
            # 欄名刻意跟發票那側一致（paid/outstanding）—— 兩個挑選視窗才有
            # 共用同一份 render 的可能，不必為了欄名各寫一份
            "paid": got, "outstanding": int(a.amount or 0) - got,
            "is_advance": int(a.is_advance or 0),
            "planned_month": a.planned_month or "",
        })
    open_pays.sort(key=lambda x: (x["date"] or "", x["summary"]), reverse=True)

    out_rows = []
    for r in res.rows:
        m = by_line.get(r.line_no)
        # 兩條重複線：①同帳戶同日同金額已有收支 ②配到的貸款期別已經記過繳款
        # （②必要 —— 繳款收支存的是攤還表金額，跟銀行實扣差幾十元，①抓不到）
        dup = (existing[(r.date, r.amount)] > 0
               or (m or {}).get("confidence") == "already_paid")
        if dup and existing[(r.date, r.amount)] > 0:
            existing[(r.date, r.amount)] -= 1   # multiset：一筆抵一筆
        out_rows.append({
            "date": r.date, "amount": r.amount, "description": r.note[:120],
            "category": r.category, "inferred": r.inferred,
            # 🔴 方向是推出來的列**不預設勾選**：我們才剛跟使用者說「這列的方向
            # 是猜的」，不能又讓表頭那顆全選一按就把猜測寫進帳。要它就自己勾。
            "duplicate": dup, "selected": not dup and not r.inferred,
            # 🔴 兩種都會落到報表的「未歸類」：有類別但沒對映、以及**完全沒類別**
            # （摘要沒中任何 KEYWORD_RULES）。舊寫法只認前者，後者靜默通過 ——
            # 那是更該提醒的一種，連該歸哪裡都不知道。
            "unmapped_category": (not r.category) or (r.category not in mapped),
            "loan_id": (m or {}).get("loan_id"),
            "loan_name": (m or {}).get("loan_name", ""),
            "period_no": (m or {}).get("period_no"),
            "match_confidence": (m or {}).get("confidence", ""),
            "is_loan": r.category == LOAN_CATEGORY and r.amount < 0,
        })
    return {
        "ok": True, "rows": out_rows, "warnings": res.warnings, "errors": [],
        # 原始文字原樣回給前端 —— 存草稿時要送回來（上傳的是 PDF 時，
        # 文字是後端抽出來的，前端手上沒有）
        "source_text": text,
        # 帳戶跟著回 —— 上傳與開啟草稿兩條路才不用各自記一份（一條從表單欄位
        # 拿、一條從草稿頭拿，同一個值兩個來源）
        "bank_account_id": acct.id,
        "projects": projects,
        "invoices": open_invs,
        # 支出列用的候選（還沒付滿的請款單）。跟 invoices 是同一個位置的兩側 ——
        # 一列不可能同時掛發票與請款單（方向互斥），前端也就共用同一格。
        "payment_requests": open_pays,
        # 哪些類別收得下專案 —— 前端拿它決定專案下拉何時可用（規則同收支明細）
        "project_categories": list(CASH_CATEGORIES),
        # 有科目對映的類別。前端改分類時要用同一條規則判「會不會落到未歸類」，
        # 不然改成一個沒對映的類別之後那個提醒就消失了。
        # （也省掉前端跨 prefix 去打 /crm/cash-entries/options 那一支）
        "mapped_categories": sorted(mapped),
        "summary": {"count": len(out_rows), "total_in": res.total_in,
                    "total_out": res.total_out,
                    "duplicates": sum(1 for r in out_rows if r["duplicate"]),
                    "loan_rows": sum(1 for r in out_rows if r["is_loan"])},
    }


def _draft_key(date, amount, description):
    """人工決定貼回去用的鍵。摘要取前 120 字 —— 存的時候也是這個長度。"""
    return (str(date or ""), int(amount or 0), (description or "")[:120])


@router.get("/bank-statement/drafts")
async def list_statement_drafts(request: Request, entity: str = "parent"):
    """未匯入的草稿清單（對帳系統卡片上顯示）。"""
    from sqlalchemy import select

    from db.models import BankImportDraft
    ent = _guard(request, entity, level="full")   # 草稿＝記帳層的東西，跟匯入同級
    factory = _factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(BankImportDraft)
            .where(BankImportDraft.entity == ent)
            .order_by(BankImportDraft.updated_at.desc()))).scalars().all()
        # 草稿的名字（_auto_draft_name）本來就以帳戶名開頭，不必再多撈一次帳戶 ——
        # 那個查詢還是**不分帳本**全撈的，只為了填一個前端沒在讀的欄位
        return {"drafts": [{
            "id": d.id, "name": d.name,
            "bank_account_id": d.bank_account_id,
            "updated_at": _fmt_minute(d.updated_at),
        } for d in rows]}


@router.post("/bank-statement/drafts")
async def save_statement_draft(payload: StatementDraftPayload, request: Request):
    """存草稿（id 有值＝更新）。**不寫任何帳** —— 只是把做到一半的決定收起來。"""
    from db.models import BankImportDraft
    factory = _factory_or_503()
    async with factory() as session:
        acct, ent = await _acct_and_entity(session, request, payload.bank_account_id)
        if not (payload.source_text or "").strip():
            raise HTTPException(status_code=422, detail="草稿沒有對帳單內容")

        decisions = [{
            "date": r.date, "amount": r.amount,
            "description": (r.description or "")[:120],
            "category": r.category or "",
            "loan_id": r.loan_id, "period_no": r.period_no,
            "project_id": r.project_id,
            # 🔴 fee 要一起存：原本只存 invoice_id/amount，於是存草稿再打開，
            #    填好的匯費就沒了（重開後那列變成「還差 30」，人再填一次）。
            "invoices": [{"invoice_id": x.invoice_id, "amount": x.amount,
                          "fee": int(x.fee or 0)}
                         for x in (r.invoices or [])],
            "payments": [{"payment_request_id": x.payment_request_id,
                          "amount": x.amount}
                         for x in (r.payments or [])],
            "payment_fee": r.payment_fee,
            "selected": bool(r.selected),
        } for r in payload.rows]

        d = None
        if payload.id:
            d = await session.get(BankImportDraft, payload.id)
            if d and (d.entity or "parent") != ent:
                raise HTTPException(status_code=409, detail="草稿與帳戶分屬不同帳本")
        if d is None:
            d = BankImportDraft(id=uuid.uuid4().hex, entity=ent)
            session.add(d)
        d.bank_account_id = payload.bank_account_id
        d.name = _auto_draft_name(acct, payload.rows)
        d.source_text = payload.source_text
        d.decisions = json.dumps(decisions, ensure_ascii=False)
        d.row_count = len(decisions)
        d.created_by = _username(request)
        d.updated_at = datetime.now()
        await session.commit()
        return {"ok": True, "id": d.id, "name": d.name}


def _auto_draft_name(acct, rows):
    """沒給名字就自己取：帳戶 + 日期區間 + 列數。夠用來認出是哪一份。"""
    days = sorted(r.date for r in rows if r.date)
    span = f"{days[0]}～{days[-1]}" if days else "（空）"
    return f"{acct.name} {span}（{len(rows)} 筆）"[:120]


@router.get("/bank-statement/drafts/{draft_id}")
async def open_statement_draft(draft_id: str, request: Request):
    """開啟草稿 → **重新解析**原始文字，再把人工決定貼回去。

    🔴 不是把當初的解析結果原封不動端回來。草稿可能放了兩天，這中間：
      · 有幾列已經從別的路進帳了（重複判定要重算，否則會匯第二次）
      · 有些發票已經收齊了（不該再出現在候選清單裡）
      · 貸款期別可能已經被別的匯入認領了
    所以重新跑一次 preview，只把「人做的決定」蓋回去。
    """
    from db.models import BankAccount, BankImportDraft
    factory = _factory_or_503()
    async with factory() as session:
        d = await session.get(BankImportDraft, draft_id)
        if not d:
            raise HTTPException(status_code=404, detail="草稿不存在（可能已經匯入或被刪掉）")
        acct = await session.get(BankAccount, d.bank_account_id)
        if not acct:
            raise HTTPException(status_code=404, detail="這份草稿的帳戶已經不在了")
        ent = _guard(request, acct.entity or "parent", level="full")

        out = await _build_statement_preview(session, acct, ent, d.source_text)
        head = {"draft_id": d.id, "draft_name": d.name,
                "bank_account_id": d.bank_account_id}
        if not out.get("ok"):
            return {**out, **head}

        # 人工決定貼回去。同一份對帳單裡可能有兩列一模一樣（同日同額同摘要），
        # 所以一個鍵存一串、依序 pop —— 用 dict 直接覆蓋會讓兩列共用同一個決定。
        saved = {}
        for x in json.loads(d.decisions or "[]"):
            saved.setdefault(
                _draft_key(x.get("date"), x.get("amount"), x.get("description")),
                []).append(x)
        restored = 0
        for r in out["rows"]:
            bucket = saved.get(_draft_key(r["date"], r["amount"], r["description"]))
            if not bucket:
                continue
            x = bucket.pop(0)
            restored += 1
            r["category"] = x.get("category") or r["category"]
            r["project_id"] = x.get("project_id")
            r["invoices"] = x.get("invoices") or []
            r["payments"] = x.get("payments") or []
            r["payment_fee"] = x.get("payment_fee")
            # 🔴 重複的列一律不勾，即使草稿裡勾了 —— 存草稿之後才被匯進去的列，
            # 使用者當初勾的時候還不重複。這裡以「現在的帳」為準。
            r["selected"] = bool(x.get("selected")) and not r["duplicate"]
        # draft_missing = 存檔時有決定、重新解析後對不回去的列數。會發生在
        # 對帳單被重新下載過（摘要或金額改了）—— 那些決定就這樣消失了，
        # 前端要說出來，不然人以為自己上次沒做完。
        return {**out, **head, "draft_restored": restored,
                "draft_missing": sum(len(v) for v in saved.values())}


@router.delete("/bank-statement/drafts/{draft_id}")
async def delete_statement_draft(draft_id: str, request: Request):
    from db.models import BankImportDraft
    factory = _factory_or_503()
    async with factory() as session:
        d = await session.get(BankImportDraft, draft_id)
        if not d:
            return {"ok": True}
        _guard(request, d.entity or "parent", level="full")
        await session.delete(d)
        await session.commit()
        return {"ok": True}


@router.post("/bank-statement/apply")
async def apply_bank_statement(payload: StatementImportApply, request: Request):
    """把確認過的列寫進帳：一般列 → 收支明細；貸款繳款列 → 走繳款流程
    （標期別已繳＋自動建帶 loan_payment_id 硬連結的收支，與手動按繳款同一條路）。

    月結守衛：任一列落鎖定月 → 整批 409，不做半套。
    """
    _guard(request, level="full")
    if not payload.rows:
        raise HTTPException(status_code=422, detail="沒有要匯入的列")
    from collections import Counter

    from core.finance_logic import apply_payment_fee, apply_receipt_fee
    from db.models import CrmCashEntry
    # 專案／發票／請款單的寫入規則只有一套正本，在 crm/finance —— 這裡借用，不另寫
    from routers.crm.finance import (_enforce_cash_project_link,
                                     replace_invoice_allocs_bulk,
                                     replace_payment_allocs,
                                     resolve_invoice_allocs,
                                     resolve_payment_allocs)

    factory = _factory_or_503()
    async with factory() as session:
        # 整份對帳單要用到的發票一次撈完。逐列各撈一次的話，一份 60 列的月對帳單
        # 就是 25 個往返，一年份好幾百個 —— 而且全部關在同一個交易裡（那段時間
        # crm_invoices 上是有寫鎖的）。
        from sqlalchemy import select as _select

        from db.models import CrmInvoice
        inv_ids = {x.invoice_id for r in payload.rows for x in (r.invoices or [])}
        inv_by_id = {i.id: i for i in (await session.execute(
            _select(CrmInvoice).where(CrmInvoice.id.in_(inv_ids))
        )).scalars().all()} if inv_ids else {}

        acct, ent = await _acct_and_entity(session, request, payload.bank_account_id)

        # 先驗全部月份（整批原子性：有一列落鎖定月就全部不做）。
        # 🔴 用 _assert_rows_open 而不是逐列 _assert_month_open —— 後者每呼叫一次就
        # 重撈一次鎖定月集合（一年份對帳單 = 1,000+ 次多餘往返），而且只會在第一個
        # 違規列就中斷；批次版一次撈完、409 把所有違規列一起列出來。
        dated = []
        for r in payload.rows:
            d = _parse_day(r.date)
            if not d:
                raise HTTPException(status_code=422, detail=f"日期無法解析：{r.date}")
            dated.append((f"{r.date} {(r.description or '')[:20]}", d))
        await _assert_rows_open(session, dated, entity=ent)

        # 🔴 重複防護不能只活在 preview 的顯示層：表頭那顆全選會把「已匯過」的
        # 列一起勾起來，送出逾時重按一次也一樣 —— 兩條路都直接寫進 CrmCashEntry，
        # 同日同額的收支就變兩份，帳戶餘額與三張報表全部雙倍。貸款列本來就有
        # status=="paid" 擋著，一般列在這之前一個防護都沒有。
        seen = await _existing_entry_keys(
            session, payload.bank_account_id, sorted(r.date for r in payload.rows))
        made_entries = made_payments = 0
        skipped_dup = []
        linked = []          # 有掛發票的收款列 → commit 前要進分配表並重算發票狀態
        pay_linked = []      # 有掛請款單的支出列 → 同上（付款狀態走同一條容差規則）
        alloc_fees: dict = {}   # (收支 id, 發票 id) → 逐張匯費（收款側才有）
        # 對帳工作台的「銀行說發生了什麼」那一欄。
        #
        # 🔴 這支端點原本只寫帳（右欄），左欄留白 —— 用它匯完一整年，打開工作台
        # 會看到「帳上 30 筆、銀行 0 筆」，看起來像銀行整年沒有任何交易。工作台
        # 自己那條匯入路徑只填左欄不寫帳，兩邊各做一半，誰都沒說完整的話。
        # 這裡兩欄一起填，並且直接把兩邊配起來（matched_entry_id）：帳本來就是
        # 從這份對帳單記的，本來就是同一件事，不需要人再去勾一次。
        # 🔴 銀行常把「一期」拆成好幾列扣：一銀 150 萬每月是 29,418 + 3,269 兩列、
        # 50 萬是 9,806 + 1,090 兩列（分兩次撥款的關係，貸款備註裡就寫著）。先把
        # 同一期的列加總，逐列記帳時才知道這一期銀行到底扣了多少。
        split_totals = Counter()
        for r in payload.rows:
            if r.loan_id and r.period_no:
                split_totals[(r.loan_id, r.period_no)] += abs(int(r.amount or 0))

        stmt_lines = []
        for r, (_label, d) in zip(payload.rows, dated):
            if r.loan_id and r.period_no:
                loan, row = await _get_loan_and_period(session, r.loan_id, r.period_no)
                if (loan.entity or "parent") != ent:
                    raise HTTPException(status_code=409, detail="貸款與帳戶分屬不同帳本")
                # r.amount 是帶號的（支出為負）→ 取絕對值當實扣。
                # 這才是銀行真的扣的錢，攤還表只決定本息拆分。
                amt = abs(int(r.amount or 0))
                total_due = int(row.principal_due or 0) + int(row.interest_due or 0)
                # 同上：unpay 過的期別 paid_amount 可能還留著舊數字，不能拿來判定
                already = (int(row.paid_amount or 0)
                           if (row.status or "") == "paid" else 0)
                key = (r.date, -amt)
                # 重跑防護分兩層，缺一不可：
                #  ① 帳上已經有同日同額的列 → 這份對帳單匯過了，跳過。
                #  ② 這一期已經記滿攤還表的金額 → 不管日期金額對不對都別再加
                #     （例如先手動按過繳款、之後才匯對帳單）。
                # 🔴 從前這裡是「status == paid 就整列跳過」，於是分筆扣的第二列
                # 被無聲丟掉 —— 沒建收支、連 skipped_duplicates 都不會提一句，
                # 而錢真的從銀行出去了（一銀兩筆合計每月憑空少 4,359）。
                if seen[key] > 0:
                    seen[key] -= 1
                    skipped_dup.append(f"{r.date} {int(r.amount or 0):+,}")
                    continue
                if already and already >= total_due:
                    skipped_dup.append(f"{r.date} {int(r.amount or 0):+,}（該期已記滿）")
                    continue
                ent_line = _record_loan_payment(
                    session, loan, row, d, payload.bank_account_id,
                    note="銀行對帳單匯入", actual_amount=amt,
                    split_total=split_totals[(r.loan_id, r.period_no)])
                made_payments += 1
                stmt_lines.append((r, ent_line))
            else:
                amt = int(r.amount or 0)
                if not amt:
                    continue
                key = (r.date, amt)
                if seen[key] > 0:
                    seen[key] -= 1          # multiset：帳上有幾筆就跳過幾筆
                    skipped_dup.append(f"{r.date} {amt:+,}")
                    continue
                ce = CrmCashEntry(
                    id=uuid.uuid4().hex, entry_date=d,
                    expense=(-amt if amt < 0 else None),
                    deposit=(amt if amt > 0 else None),
                    summary=(r.description or "銀行對帳單")[:255],
                    note="銀行對帳單匯入",
                    category=(r.category or "").strip() or None,
                    bank_account_id=payload.bank_account_id, entity=ent)
                # 預覽時當場掛的專案／發票（owner 2026-08-20）。
                # 🔴 走 crm/finance 的既有 helper，不在這裡自己寫一套：
                #  - 專案要過 _enforce_cash_project_link（行政/薪資掛專案會讓
                #    專案毛利多算一筆不屬於它的錢）
                #  - 發票要進**分配表**而不是只寫 invoice_id —— 列表那欄讀 invoice_id、
                #    但「已收金額」與關聯面板讀 crm_cash_invoice_links，只寫一邊
                #    會變成兩處各講各的（這一輪已經修過同樣的洞）
                if r.project_id:
                    ce.project_id = r.project_id
                    _enforce_cash_project_link(ce)
                # 發票：一列可以掛多張（合併匯款 —— 客戶一次匯三張的錢）。
                # 驗證與寫入都走 crm/finance 的共用兩支 —— 分配表有三條寫入路徑
                # （這裡、關聯面板、編輯視窗），各寫一份的話規則遲早分岔。
                allocs = [(x.invoice_id, int(x.amount or 0)) for x in (r.invoices or [])]
                # 匯費：客戶匯 149,900、銀行只入 149,870 → 那 30 元是匯出行扣的。
                # 發票要算收齊（分配 149,900），現金只能增加 149,870 —— 所以把
                # deposit 補成客戶實付、fee 掛 bank_fee，淨流入維持銀行說的數字
                # （對帳工作台比的就是淨流，補錯這一列就永遠配不上）。
                fee_total = sum(int(x.fee or 0) for x in (r.invoices or []))
                # 認列走 core.finance_logic 的 apply_receipt_fee —— deposit 與
                # bank_fee 必須一起動，寫回的動作留在呼叫端就會有第二種寫法。
                # ⚠ 這裡的守衛比關聯面板嚴（那邊是 `fee is not None`，fee=0 會把
                #    bank_fee 清掉，因為它在改一列**既有**的資料）。ce 是這一圈
                #    當場新建的，bank_fee 本來就是 None，沒有舊值要清；amt > 0
                #    則是因為只有收款側有 deposit 可補。
                if fee_total and amt > 0:
                    apply_receipt_fee(ce, fee_total)
                # 支出列的鏡像：掛請款單（出納統一匯款，一個人的多張常併成一筆匯出）。
                # 🔴 匯費在這一側是**整列一個**（跨行手續費對「這筆匯出」收一次，
                #    不管涵蓋幾張請款單），所以不是把逐項的 fee 加總 —— 兩側形狀
                #    刻意不同，見 core.schemas.StatementImportRow.payment_fee。
                #    方向也相反：付款側守「總流出不變」，從 expense 搬進 bank_fee。
                pay_allocs = [(x.payment_request_id, int(x.amount or 0))
                              for x in (r.payments or [])]
                if r.payment_fee and amt < 0:
                    apply_payment_fee(ce, int(r.payment_fee))
                session.add(ce)
                made_entries += 1
                stmt_lines.append((r, ce))
                if allocs and amt > 0:
                    linked.append((ce, await resolve_invoice_allocs(
                        session, allocs, ent, by_id=inv_by_id)))
                    # 逐張匯費也要進連結表 —— 不存的話關聯面板重開時那格是空的，
                    # 按一次儲存就把 deposit 的補回值抹掉（見 models 那欄的說明）
                    for x in (r.invoices or []):
                        if int(x.fee or 0):
                            alloc_fees[(ce.id, x.invoice_id)] = int(x.fee)
                if pay_allocs and amt < 0:
                    pay_linked.append((ce, await resolve_payment_allocs(
                        session, pay_allocs, ent)))

        # 工作台可能已經自己匯過同一個月（它那條路只填左欄不寫帳）。那些列就是
        # 這幾筆交易，不該再建一份 —— 找同日同額且還沒配對的，直接拿來配。
        from collections import defaultdict

        from sqlalchemy import and_, select

        from db.models import BankStatementLine
        spare = defaultdict(list)
        if stmt_lines:
            months = {r.date[:7] for r, _ce in stmt_lines}
            for ln in (await session.execute(
                    select(BankStatementLine).where(and_(
                        BankStatementLine.bank_account_id == payload.bank_account_id,
                        BankStatementLine.month.in_(months),
                        BankStatementLine.matched_entry_id.is_(None))))).scalars():
                key = (local_day(ln.line_date).strftime("%Y-%m-%d")
                       if ln.line_date else "", int(ln.amount or 0))
                spare[key].append(ln)
        for r, ce in stmt_lines:
            key = (r.date, int(r.amount or 0))
            if spare[key]:
                spare[key].pop().matched_entry_id = ce.id       # 沿用工作台已有的那列
                continue
            session.add(BankStatementLine(
                id=uuid.uuid4().hex,
                bank_account_id=payload.bank_account_id,
                month=r.date[:7],
                line_date=_parse_day(r.date),
                description=(r.description or "")[:255],
                # 左欄記**銀行說的**金額。貸款列的收支金額也已經改記實扣，
                # 兩邊會一致 —— 工作台要求金額相等才算配對得上。
                amount=int(r.amount or 0),
                matched_entry_id=ce.id,
                created_by="銀行對帳單匯入"))
        # 分配表與發票收款狀態 —— 要等收支列有 id（flush）之後才做得了
        if linked or pay_linked:
            await session.flush()
        if linked:
            # 整批一次寫 —— 逐筆呼叫時，每列都要「查舊連結、刪、flush、重算」，
            # 而這些收支列是剛建出來的，舊連結必定是空的
            await replace_invoice_allocs_bulk(session, linked, fees=alloc_fees)
        # 帳戶間轉存的跨行手續費：這一批匯進來的轉出列，如果能跟帳上既有的
        # 轉入列配對、而且差幾十塊，那幾十塊就是手續費 —— 當場拆出來
        # （總流出不變）。owner 2026-08-24：「不太可能每次都逐一填寫」。
        # ⚠ 只補得到**對手已經在帳上**的：兩邊常來自不同銀行的對帳單、不同時間
        #   匯入。補不到的留給對帳系統那張卡片（同一支規則）。
        for ce, rows_ in pay_linked:
            # ⚠ 付款側目前只有單筆版（沒有 *_bulk）。一份對帳單的支出列通常個位數，
            #    而收入列動輒幾十列 —— 發票那側是因為量級才做了 bulk。真的變慢再收，
            #    先不為了對稱而多一支平行實作。
            await replace_payment_allocs(session, ce, rows_)
        await session.flush()
        try:
            fees_done = await recognize_transfer_fees_in(session, ent,
                                                         month_guard=False)
        except Exception as e:      # noqa: BLE001 — 收尾失敗不該讓整批匯入白做
            print(f"[stmt apply] 轉存手續費認列跳過: {type(e).__name__}: {e}")
            fees_done = []
        await session.commit()
    return {"ok": True, "entries": made_entries, "loan_payments": made_payments,
            "statement_lines": len(stmt_lines),
            "linked_invoices": sum(len(v) for _ce, v in linked),
            "linked_payments": sum(len(v) for _ce, v in pay_linked),
            "transfer_fees": len(fees_done),
            "skipped_duplicates": skipped_dup}
