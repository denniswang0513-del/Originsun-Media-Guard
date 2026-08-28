# -*- coding: utf-8 -*-
"""api_finance_projects.py — 逐案損益（帳本視角的專案清單／單案明細）。

owner 2026-08-24：「跟我有關的專案我都要看到」。/my-ledger.html 是固定
entity 的帳本頁（刻意沒有 CRM 專案管理），但它缺的正是 owner 最在意的維度 ——
逐案的錢。這支端點提供**帳本視角**的專案彙總：一列一案，帶合約／已收／應收／
掛帳支出／未付應付／淨額。

與 CRM 的 /crm/projects 分工：那支是**專案管理**（階段、派工、客戶、看板），
這支是**逐案的錢**（原 Sheet「結案總表」那張表的系統版），欄位口徑對齊帳本。
兩支都按 entity 過濾，錢的牆一樣在 core/ledger（require_entity）。

口徑（與 Sheet 對照）：
- 營收(含稅) = crm_projects.contract_amount
- 已收 / 應收 = amount_received / amount_receivable
- 掛帳支出   = 掛在該案的收支明細 expense 合計（階段 3 按案碼回掛的 612 筆）
- 未付應付   = 該案 crm_payment_requests 未付款者合計（委外／代開稅款）
- 實收       = 營收 − 委外 − 發票代辦費 − 個人稅款 − 雜支 − 股東往來
- 檢查       = 實收 − Σ工項（應為 0）
  這兩條是 2026-08-24 對 402 案反推驗證出來的 Sheet 算式（實收 395/402、
  檢查 397/402 吻合；不吻合的是 Sheet 自己帳不平的那幾案，照實顯示不修）。
  算式正本在 `core/ledger_project.py` 的 `compute()`（本檔只是 import 它；
  腳本與測試也 import 同一份）—— 前端只顯示，不自己算第二份。

工項與費用欄存在 `crm_projects.ledger_detail`（JSONB），可由 UI 編輯；
工項清單預設十項、settings `my_ledger.income_items` 可覆寫。
"""
from fastapi import APIRouter, HTTPException, Request

from config import load_settings
from core.db_guard import db_factory_or_503 as _factory_or_503
# 欄位定義與算式的正本在 core（腳本與測試也 import 同一份 —— 見該檔頭）
from core.ledger_project import (COST_FIELDS, DEFAULT_FEE_PCT, apply_crm_costs,
                                 client_wire, payout_total, settle_state,
                                 to_collect,
                                 SELECTABLE_SOURCES, SUM_KEYS, apply_source_fee,
                                 code_of, compute, expected_cash_in,
                                 income_items, norm_detail, receivable_status)
from core.schemas import LedgerDetailPayload, LedgerProjectCreate
from routers.crm._shared import _fmt_day

from .api_finance import _guard

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


async def _owned_project(session, project_id: str, ent: str):
    """載入專案 → 404 → 驗它真的屬於這本帳。

    query 的 entity 只驗得了「人有沒有這本帳的權限」，驗不了「這一列是誰的」
    —— 兩支端點都要，抽一份免得第三支忘記。
    """
    from db.models import CrmProject
    p = await session.get(CrmProject, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="找不到此專案")
    if (p.entity or "parent") != ent:
        raise HTTPException(status_code=403, detail="這個專案不屬於目前的帳本")
    return p


def _pid_where(model, project_id: str) -> tuple:
    """單案模式的額外述詞（空＝整本帳）。"""
    return (model.project_id == project_id,) if project_id else ()


async def _crm_costs(session, ent: str, project_id: str = "") -> dict:
    """`{project_id: {"misc": 行政雜支合計, "outsource": 人員費用合計}}`。

    來源是 CRM 專案帳目那兩張表（它們沒有 entity 欄 —— 掛在專案上，跟著專案走）。
    清單那支一次聚合、不逐案 N+1；單案讀取傳 `project_id` 收斂成兩個索引查詢
    —— 不傳的話等於為了兩個數字掃全帳本，而詳情每開一次、每存一次都付一遍。
    entity 從專案那側圈定。

    🔴 人員費用排除 `phase='行政雜支'` 的成本行 —— 那個階段值跟
    crm_project_expenses 講的是同一件事，兩邊都算就是重複計算
    （2026-08-28 實測目前該階段的實際金額為 0，但那是現況不是保證）。
    """
    from sqlalchemy import func as fn
    from sqlalchemy import select

    from db.models import CrmProject, CrmProjectCostLine, CrmProjectExpense
    # 🔴 只算**沒跟公司請過款**的（`claim_id` 為空）。跟公司請款拿回來的那些，
    # 錢最後是公司出的 —— 私帳沒有承擔，算進來就是憑空多一筆成本，
    # 逐案損益的實收會被壓低（owner 2026-08-28 拍板）。
    #     自己吸收 500 → 私帳成本 500
    #     跟公司請款拿回 500 → 私帳成本 0
    exp_q = (select(CrmProjectExpense.project_id,
                    fn.coalesce(fn.sum(CrmProjectExpense.actual), 0))
             .join(CrmProject, CrmProject.id == CrmProjectExpense.project_id)
             .where(CrmProject.entity == ent,
                    CrmProjectExpense.claim_id.is_(None),
                    *_pid_where(CrmProjectExpense, project_id))
             .group_by(CrmProjectExpense.project_id))
    line_q = (select(CrmProjectCostLine.project_id,
                     fn.coalesce(fn.sum(CrmProjectCostLine.actual_amount), 0))
              .join(CrmProject, CrmProject.id == CrmProjectCostLine.project_id)
              .where(CrmProject.entity == ent,
                     CrmProjectCostLine.phase != "行政雜支",
                     *_pid_where(CrmProjectCostLine, project_id))
              .group_by(CrmProjectCostLine.project_id))
    out: dict = {}
    for pid, v in (await session.execute(exp_q)).all():
        out.setdefault(pid, {})["misc"] = int(v or 0)
    for pid, v in (await session.execute(line_q)).all():
        out.setdefault(pid, {})["outsource"] = int(v or 0)
    return out


async def _crm_lines(session, project_id: str) -> dict:
    """這個案子在 CRM 專案帳目裡的**明細**（owner 2026-08-28「轉過來的 crm 明細
    要列出」）。上面 `_crm_costs` 給的是合計，這裡是它加總的那幾列。

    `people` 帶 `claimed`：是否已經有委外請款單指向這一行（`cost_line_id`）——
    沒有硬連結的話只能靠人名＋金額目測，同一個人同金額出現兩次就分不出來。
    """
    from sqlalchemy import select

    from db.models import (CrmPaymentRequest, CrmProjectCostLine,
                           CrmProjectExpense, CrmStaff)
    exp = (await session.execute(
        select(CrmProjectExpense)
        .where(CrmProjectExpense.project_id == project_id)
        .order_by(CrmProjectExpense.expense_date))).scalars().all()
    lines = (await session.execute(
        select(CrmProjectCostLine)
        .where(CrmProjectCostLine.project_id == project_id,
               CrmProjectCostLine.phase != "行政雜支",
               CrmProjectCostLine.actual_amount.isnot(None),
               CrmProjectCostLine.actual_amount != 0)
        .order_by(CrmProjectCostLine.sort_order))).scalars().all()
    sids = {x for l in lines for x in (l.actual_staff_id, l.estimated_staff_id) if x}
    names = dict((await session.execute(
        select(CrmStaff.id, CrmStaff.name).where(CrmStaff.id.in_(sids)))).all()) if sids else {}
    claimed = set((await session.execute(
        select(CrmPaymentRequest.cost_line_id)
        .where(CrmPaymentRequest.project_id == project_id,
               CrmPaymentRequest.cost_line_id.isnot(None)))).scalars())
    return {
        "misc": [{"date": _fmt_day(e.expense_date), "category": e.category or "",
                  "item": e.sub_item or e.item or "", "amount": int(e.actual or 0),
                  "payee": e.payee or names.get(e.staff_id, ""),
                  # 跟公司請過款的不算私帳成本（見 _crm_costs），列出來但標示
                  "billed_to_company": bool(e.claim_id)} for e in exp],
        "people": [{"id": l.id, "phase": l.phase or "", "item": l.item_name or "",
                    "who": names.get(l.actual_staff_id or l.estimated_staff_id, ""),
                    "amount": int(l.actual_amount or 0),
                    "claimed": l.id in claimed} for l in lines],
    }


async def _rollups(session, ent: str):
    """({project_id: 掛帳支出}, {project_id: 未付應付})。一次聚合，不逐案 N+1。

    🔴 未付應付**排除預支款**（is_advance）—— 對齊 core.finance_logic
    ap_open_payments 與「應付帳款」視圖的口徑。預支不是應付（那是先給出去的
    週轉金，核銷後才變成成本），混進來會讓同一個專案在逐案損益與應付帳款上
    出現兩個數字，那正是這套帳要消滅的東西（/simplify 2026-08-25）。

    entity 條件本身就把範圍圈定了 —— 不再另外帶 id 清單進 IN（那是零選擇性
    的白工，而且清單端本來就整份拉）。
    """
    from sqlalchemy import func as fn
    from sqlalchemy import or_, select

    from db.models import CrmCashEntry, CrmPaymentRequest
    cash_q = (select(CrmCashEntry.project_id,
                     fn.coalesce(fn.sum(CrmCashEntry.expense), 0))
              .where(CrmCashEntry.entity == ent,
                     CrmCashEntry.project_id.isnot(None))
              .group_by(CrmCashEntry.project_id))
    ap_q = (select(CrmPaymentRequest.project_id,
                   fn.coalesce(fn.sum(CrmPaymentRequest.amount), 0))
            .where(CrmPaymentRequest.entity == ent,
                   CrmPaymentRequest.project_id.isnot(None),
                   # 🔴 NULL 也是「不是預支」。生產的 is_advance 可為 NULL，
                   # 而 SQL 的 `NULL = 0` 是 NULL → 那一列會整個消失在未付應付
                   # 裡，卻還留在應付帳款上（core.finance_logic 走 Python
                   # truthiness、crm/finance.py:1971 也是這樣防的）—— 正好是
                   # 這支端點要消滅的「同一案兩個數字」。
                   or_(CrmPaymentRequest.is_advance == 0,
                       CrmPaymentRequest.is_advance.is_(None)),
                   CrmPaymentRequest.payment_status != "已付款")
            .group_by(CrmPaymentRequest.project_id))
    cash = {pid: int(e or 0) for pid, e in (await session.execute(cash_q)).all()}
    ap = {pid: int(a or 0) for pid, a in (await session.execute(ap_q)).all()}
    return cash, ap


@router.get("/project-ledger")
async def project_ledger(request: Request, entity: str = ""):
    """本帳本的逐案損益清單（含合計）。

    刻意不收 q／unpaid_only：前端一次拉完 402 列後在本地篩（每敲一個字重抓
    整份是白工，見 subviews/projects.js）。留著沒人走的分支只會讓下一個要改
    rollup 規則的人去推理一個從未執行過的模式（/simplify 2026-08-25）。
    """
    ent = _guard(request, entity, level="full")
    from sqlalchemy import select

    from db.models import Client, CrmProject
    factory = _factory_or_503()
    async with factory() as session:
        query = (select(CrmProject, Client.short_name)
                 .outerjoin(Client, Client.id == CrmProject.client_id)
                 .where(CrmProject.entity == ent)
                 # 依結案日新→舊；未結案（無日期）排最前 —— 這張表是照結案日
                 # 整理的（owner：先整理專案再記帳），不是照最後編輯時間。
                 # 🔴 id 是必要的第三鍵：同批匯入的案 updated_at 到微秒都相同，
                 # 沒有它 Postgres 平手順序不保證 —— 前端就地重排（穩定排序）
                 # 與重新載入會給出兩種順序（e2e r5 實抓：媒體顧問 vs 鍾馗嫁妹）
                 .order_by(CrmProject.completion_date.desc().nullsfirst(),
                           CrmProject.updated_at.desc(), CrmProject.id))
        rows = (await session.execute(query)).all()
        cash, ap = await _rollups(session, ent)
        crm_costs = await _crm_costs(session, ent)

    items, tot = [], {"contract": 0, "received": 0, "receivable": 0,
                      "spent": 0, "ap_open": 0, "net": 0,
                      "outsource": 0, "invoice_fee": 0, "unbalanced": 0}
    for p, cname in rows:
        spent = cash.get(p.id, 0)
        ap_open = ap.get(p.id, 0)
        contract = int(p.contract_amount or 0)
        # CRM 專案帳目蓋過手填（B2，owner 2026-08-28）—— 規則在 core，不在這裡
        detail, _src = apply_crm_costs(norm_detail(p.ledger_detail),
                                       crm_costs.get(p.id))
        net, check = compute(contract, detail)
        _tc = to_collect(int(p.contract_amount or 0),
                         int(p.amount_received or 0), detail)
        item = {
            "id": p.id, "name": p.name, "client": cname or "",
            "status": p.status or "", "type": p.project_type or "",
            "close_date": _fmt_day(p.completion_date),
            # 排序的次要鍵 —— 前端改完結案日要就地重排，少了它排不出與這裡
            # ORDER BY 相同的順序（同一天結案的案子就會落在不同位置）
            "updated_at": p.updated_at.isoformat() if p.updated_at else "",
            "contract": contract,
            "received": int(p.amount_received or 0),
            "receivable": int(p.amount_receivable or 0),
            "payment_status": p.payment_status or "",
            "spent": spent, "ap_open": ap_open,
            # 五件事（owner 2026-08-29）：② 客戶會匯多少 ③ 我要匯出去多少
            # ④⑤ 的「還剩多少」由前端用 營收−實收、應付−已付 算（同一份資料）
            "client_wire": client_wire(int(p.contract_amount or 0), detail),
            "payout": payout_total(detail),
            # ④ 還沒收到多少 —— 走 to_collect（一條規則吃全額/淨額兩種記法），
            # 不是 amount_receivable（那欄是營收−已收，收齊的代開案會差一個代辦費）
            "to_collect": _tc,
            # 收付狀態（最左欄＋篩選共用一份判定）
            "settle": settle_state(_tc, ap_open, payout_total(detail)),
            "detail": detail, "net": net, "check": check,
        }
        items.append(item)
        # 加總只從 item 取 —— 上面已經算好的數字不要在這裡再算一次
        for k in SUM_KEYS:
            tot[k] += item[k]
        tot["outsource"] += detail["outsource"]
        tot["invoice_fee"] += detail["invoice_fee"]
        if check:
            tot["unbalanced"] += 1
    return {"projects": items, "totals": tot, "count": len(items)}


@router.post("/project-ledger")
async def create_ledger_project(payload: LedgerProjectCreate, request: Request,
                                entity: str = ""):
    """在帳本視角新增專案（owner 2026-08-25「我這裡需要有地方新增專案」）。

    落在目前帳本（同一道 require_entity full 門）。案碼寫進 notes 的
    `案碼:XXX` 行 —— 與匯入同一個鍵，Sheet 對齊同步靠它。
    🔴 案碼重複直接 409：2026010 撞碼曾讓回填把 EP5 的費用寫進攝影授課，
    同一顆雷不裝第二次。
    """
    ent = _guard(request, entity, level="full")
    import uuid as _uuid

    from sqlalchemy import select

    from db.models import Client, CrmProject
    from routers.crm._shared import _now, _parse_shoot_date
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="專案名稱必填")
    code = (payload.code or "").strip()
    close = None
    if (payload.close_date or "").strip():
        close = _parse_shoot_date(payload.close_date.strip())
        if not close:
            raise HTTPException(status_code=422, detail=f"結案日無法解析：{payload.close_date}")
    factory = _factory_or_503()
    async with factory() as session:
        if payload.client_id:
            if not await session.get(Client, payload.client_id):
                raise HTTPException(status_code=404, detail="找不到指定的客戶")
        if code:
            # 撞碼防線走 code_of 唯一解析器逐列比（本帳 ~400 列、一次查詢）
            # —— 曾用 SQL LIKE 行尾錨點，與回填腳本的 regex 對「案碼:XXX 補」
            # 這種尾註列給出不同答案；一個協定只能有一個解析器。
            notes_rows = (await session.execute(
                select(CrmProject.notes).where(CrmProject.entity == ent)
            )).scalars().all()
            if any(code_of(n) == code for n in notes_rows):
                raise HTTPException(status_code=409,
                                    detail=f"案碼 {code} 已存在 —— 收支按案碼回掛，重複會讓兩案分不清彼此的帳")
        pid = _uuid.uuid4().hex
        # 案源規則（owner 2026-08-25）：源日＝現金收款；代開發票＝自動代辦費
        # （contract × fee_pct，預設 8%）—— 規則正本在 core.ledger_project
        contract = int(payload.contract_amount or 0)
        d = norm_detail({"source": payload.source,
                         "fee_pct": payload.fee_pct})
        d = norm_detail(apply_source_fee(contract, d))
        session.add(CrmProject(
            id=pid, name=name, client_id=payload.client_id or None,
            entity=ent, status="結案" if close else "製作",
            completion_date=close,
            contract_amount=contract or None,
            ledger_detail=d,
            # 🔴 應收要在建立時就初始化 —— 應收視圖讀的是存起來的
            # amount_receivable（收支同步是增量制，不會替 NULL 補課）。
            # 漏掉的話新案永遠不進應收帳款（owner 2026-08-26 實測）。
            # 基準＝實際會進帳的錢（營收−源頭代扣，見 expected_cash_in）。
            amount_received=0,
            amount_receivable=expected_cash_in(contract, d),
            payment_status="未到帳",
            notes=f"[私帳新增] 案碼:{code}" if code else "[私帳新增]",
            created_at=_now(), updated_at=_now(),
        ))
        await session.commit()
    return {"status": "ok", "id": pid}


@router.get("/project-ledger/{project_id}")
async def project_ledger_detail(project_id: str, request: Request,
                                entity: str = ""):
    """單案明細：掛帳的收支逐筆 + 未付應付逐張 + 匯入時保留的原始備註。"""
    ent = _guard(request, entity, level="full")
    from sqlalchemy import select

    from db.models import Client, CrmCashEntry, CrmPaymentRequest
    factory = _factory_or_503()
    async with factory() as session:
        p = await _owned_project(session, project_id, ent)
        client = await session.get(Client, p.client_id) if p.client_id else None
        entries = (await session.execute(
            select(CrmCashEntry)
            .where(CrmCashEntry.project_id == project_id,
                   CrmCashEntry.entity == ent)
            .order_by(CrmCashEntry.entry_date))).scalars().all()
        pays = (await session.execute(
            select(CrmPaymentRequest)
            .where(CrmPaymentRequest.project_id == project_id,
                   CrmPaymentRequest.entity == ent)
            .order_by(CrmPaymentRequest.created_at))).scalars().all()
        _crm = (await _crm_costs(session, ent, project_id)).get(project_id)
        _lines = await _crm_lines(session, project_id)
    _d, _cost_src = apply_crm_costs(norm_detail(p.ledger_detail), _crm)
    _net, _check = compute(int(p.contract_amount or 0), _d)
    return {
        "project": {
            "id": p.id, "name": p.name, "client": client.short_name if client else "",
            "status": p.status or "", "type": p.project_type or "",
            "close_date": _fmt_day(p.completion_date),
            "contract": int(p.contract_amount or 0),
            "received": int(p.amount_received or 0),
            "receivable": int(p.amount_receivable or 0),
            "payment_status": p.payment_status or "",
            "crm_pushed": int(p.crm_pushed or 0),
            "detail": _d, "net": _net, "check": _check,
            # 哪幾個費用欄的值是從 CRM 專案帳目算來的（前端據此標來源並鎖住）
            "cost_sources": _cost_src,
            # 匯入時把案碼/案源/税別等收在這裡（純文字）—— 逐案對照 Sheet 用
            "notes": p.notes or "",
        },
        # CRM 專案帳目的明細（合計在 detail 的 misc/outsource，這是它的組成）
        "crm_lines": _lines,
        "income_items": income_items(load_settings()),
        # 下拉只給可選的（自接＝歷史值不再可選；舊案的值由前端就地補一個選項）
        "sources": list(SELECTABLE_SOURCES), "default_fee_pct": DEFAULT_FEE_PCT,
        "cost_fields": [{"key": k, "label": lb} for k, lb in COST_FIELDS],
        "entries": [{
            "id": e.id, "date": _fmt_day(e.entry_date), "summary": e.summary,
            "category": e.category or "", "deposit": int(e.deposit or 0),
            "expense": int(e.expense or 0), "status": e.status or "",
        } for e in entries],
        "payments": [{
            "id": x.id, "summary": x.summary, "amount": int(x.amount or 0),
            "category": x.category or "", "payee": x.payee_name or "",
            "payment_status": x.payment_status or "",
            "payment_date": _fmt_day(x.payment_date),
        } for x in pays],
    }


@router.put("/project-ledger/{project_id}")
async def update_project_ledger(project_id: str, payload: LedgerDetailPayload,
                                request: Request, entity: str = ""):
    """編輯單案的工項與費用欄。回存後的明細與重算的實收/檢查。

    🔴 只寫有送的欄（exclude_unset）—— 整包寫回會把沒送的欄洗成 0。
    split 有送＝整份取代（工項是一組值）；0 值在 norm_detail 會被清掉，
    等於「把這個工項刪掉」，那正是使用者把格子清空的意思。
    """
    ent = _guard(request, entity, level="full")
    from routers.crm._shared import _now, _parse_shoot_date
    factory = _factory_or_503()
    async with factory() as session:
        p = await _owned_project(session, project_id, ent)
        data = payload.model_dump(exclude_unset=True)
        # 🔴 先 pop —— 下面的迴圈把剩餘鍵全當費用欄寫進 ledger_detail JSON
        pushed = data.pop("crm_pushed", None)
        if pushed is not None:
            p.crm_pushed = 1 if pushed else 0
        contract = data.pop("contract_amount", None)
        if contract is not None:
            p.contract_amount = int(contract)
        # 結案日：owner 的流程是「先整理專案再記帳」，日期在這張表就要能改。
        # 空字串＝清空（未結案）。日期慣例走 _parse_shoot_date（UTC 午夜）。
        if "close_date" in data:
            raw = (data.pop("close_date") or "").strip()
            if raw:
                d = _parse_shoot_date(raw)
                if not d:
                    raise HTTPException(status_code=422, detail=f"結案日無法解析：{raw}")
                p.completion_date = d
            else:
                p.completion_date = None
        d = norm_detail(p.ledger_detail)
        # 🔴 CRM 撐著的費用欄（行政雜支／人員費用）：落庫的永遠只有**手填**那部分，
        # CRM 合計是讀取時即時加上去的（甲案，見 core.ledger_project.apply_crm_costs）。
        # 前端那格送回來的也是手填值 —— 送合計就會把 CRM 的數字存成一份會走味的
        # 副本（CRM 那邊改了、這裡卻停在舊數字），所以前端與這裡都只認手填。
        crm_now = (await _crm_costs(session, ent, project_id)).get(project_id) or {}
        # 案源與費率是 meta（字串/浮點），不能進下面那個 int() 迴圈
        if "source" in data:
            d["source"] = data.pop("source") or ""
        if "fee_pct" in data:
            d["fee_pct"] = data.pop("fee_pct")
        for k, v in data.items():
            if v is None:
                continue
            d[k] = ({str(x): int(y) for x, y in v.items()
                     if isinstance(y, (int, float))} if k == "split" else int(v))
        # 🔴 案源＝代開發票 → 代辦費由費率算（唯一的自動費用規則；其他案源
        # 不碰使用者填的數字）。在 norm 前套，contract 用改完的值。
        d = apply_source_fee(int(p.contract_amount or 0), d)
        d = norm_detail(d)          # 再正規化一次（清掉 0 值工項）
        p.ledger_detail = d
        # 營收或代扣成本（個人稅款/代辦費）動了 → 應收與收款狀態一律重算
        # （基準＝實際會進帳的錢；規則正本 expected_cash_in，與收支同步同一條）
        _recv = int(p.amount_received or 0)
        _exp = expected_cash_in(int(p.contract_amount or 0), d)
        p.amount_receivable = _exp - _recv
        p.payment_status = receivable_status(_exp, _recv)
        p.updated_at = _now()
        await session.commit()
        d, cost_src = apply_crm_costs(d, crm_now)
        net, check = compute(int(p.contract_amount or 0), d)
    return {"status": "ok", "detail": d, "net": net, "check": check,
            "cost_sources": cost_src,
            "contract": int(contract) if contract is not None else None,
            "close_date": _fmt_day(p.completion_date),
            "crm_pushed": int(p.crm_pushed or 0),
            "updated_at": p.updated_at.isoformat() if p.updated_at else ""}
