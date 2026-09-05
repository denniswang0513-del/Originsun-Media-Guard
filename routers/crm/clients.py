"""routers/crm/clients.py — 客戶管理 + CSV 匯入 + AM/PM 使用者列表。

自 routers/api_crm.py 原樣搬移（純搬移，行為不變）。
"""
from __future__ import annotations

import csv
import io
import uuid

from fastapi import HTTPException, Request, UploadFile, File, Query

from core.crm_logic import normalize_tax_id
from core.schemas import ClientPayload

from ._shared import (router, _check_auth, _require_db, _get_factory,
                      _now, _to_dict, _auto_update_client_status,
                      _CLIENT_TIER_EXCLUDE_STATUSES, map_csv_row)

try:
    from ._shared import (select, or_, sa_update, IntegrityError,
                          Client, CrmProject, User)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

# ── Endpoints ────────────────────────────────────────────────

@router.get("/clients")
async def list_clients(
    request: Request,
    q: str = Query(""),
    status: str = Query(""),
    am: str = Query(""),
    entity: str = Query(""),
):
    """客戶名錄（owner 2026-08-26 定案：**兩邊各一筆＋連結**，CRM 那份是主清單）：
    - 預設（CRM 各呼叫端）＝只回 CRM 客戶（entity!='mine'）——私帳那一筆不混進來。
    - entity=mine（私帳執行專案的客戶下拉）＝CRM 主檔 ＋ **還沒連結的**私帳客戶。
      🔴 已連結的私帳客戶要濾掉：它與 CRM 那筆同名（代稱改成每本帳唯一之後
      本來就會同名），兩筆並排出現在下拉裡沒有人分得出該選哪個 —— 有連結就
      以 CRM 那筆為準（owner：「連結後以 crm 的客戶清單為主要清單」）。
      仍要 mine full scope。
    """
    _require_db()
    if entity == "mine":
        from core.ledger import require_entity
        require_entity(request, "mine", level="full")
    factory = await _get_factory()

    from sqlalchemy import func as _fn, case as _case

    # Subquery: per-client project stats (exclude not-yet-won 階段；
    # 口徑集中在 _shared._CLIENT_TIER_EXCLUDE_STATUSES，與客戶分級同源)
    active_filter = CrmProject.status.notin_(_CLIENT_TIER_EXCLUDE_STATUSES)
    # 兩本帳 §8：成案「數」照算（客戶關係是真的），但金額合計**排除 mine**——
    # owner 私帳的營收不得混進母公司的客戶績效（合夥人看了會多出憑空的營收）。
    from sqlalchemy import and_ as _and

    from core.ledger import not_mine
    money_filter = _and(active_filter, not_mine(CrmProject.entity))
    proj_sub = (
        select(
            CrmProject.client_id,
            _fn.count(_case((active_filter, 1))).label("project_count"),
            _fn.coalesce(_fn.sum(_case((money_filter, CrmProject.contract_amount), else_=0)), 0).label("total_contract"),
        )
        .group_by(CrmProject.client_id)
        .subquery()
    )

    async with factory() as session:
        query = (
            select(Client, proj_sub.c.project_count, proj_sub.c.total_contract)
            .outerjoin(proj_sub, proj_sub.c.client_id == Client.id)
            .order_by(Client.updated_at.desc())
        )
        if entity == "mine":
            # 主檔 ＋ 未連結的私帳客戶（已連結的由 CRM 那筆代表）
            query = query.where(or_(not_mine(Client.entity),
                                    Client.crm_link_id.is_(None)))
        else:
            query = query.where(not_mine(Client.entity))
        if status:
            query = query.where(Client.status == status)
        if am:
            query = query.where(Client.am_username == am)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(
                Client.short_name.ilike(ql),
                Client.full_name.ilike(ql),
                Client.contact_person.ilike(ql),
            ))
        rows = (await session.execute(query)).all()

    result = []
    for c, proj_count, total_contract in rows:
        d = _to_dict(c)
        d["project_count"] = proj_count or 0
        d["total_contract"] = total_contract or 0
        result.append(d)
    return {"clients": result, "total": len(result)}


@router.post("/clients")
async def create_client(req: ClientPayload, request: Request):
    # 客戶主檔統一後（owner 2026-08-26）：一律建 **CRM 客戶**，不再有私帳專屬
    # 客戶 —— 私帳建案順手建的客戶也直接進主檔（否則又開始分裂）。
    # 🔴 寫入守衛因此放寬一格：帳本主人（lv1＋finance_mine）可以**建**客戶，
    # 改/刪既有客戶仍是 Lv3 —— 建客戶是低風險（名稱/統編），而 owner 不是 Lv3
    # 是刻意的（指名制），不放寬他就建不了案。
    ent = "parent"
    try:
        _check_auth(request)
    except HTTPException:
        from core.ledger import require_entity
        require_entity(request, "mine", level="full")
    _require_db()
    factory = await _get_factory()

    now = _now()
    async with factory() as session:
        # Check duplicate by full_name + tax_id
        full_name = (req.full_name or "").strip()
        tax_id = (req.tax_id or "").strip()
        if full_name or tax_id:
            dup = (await session.execute(
                select(Client).where(Client.full_name == full_name, Client.tax_id == tax_id)
            )).scalars().first()
            if dup:
                raise HTTPException(status_code=409,
                    detail=f"已存在相同抬頭＋統編的客戶「{dup.short_name}」")

        client = Client(id=uuid.uuid4().hex, entity=ent, created_at=now, updated_at=now,
                        **req.model_dump(exclude={"entity"}))
        try:
            session.add(client)
            await session.commit()
            await session.refresh(client)
        except IntegrityError:
            raise HTTPException(status_code=409, detail=f"客戶代稱「{req.short_name}」已存在")
    return {"status": "ok", "client": _to_dict(client)}


@router.get("/clients/{client_id}")
async def get_client(client_id: str):
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        client = await session.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="找不到此客戶")
    return _to_dict(client)


@router.put("/clients/{client_id}")
async def update_client(client_id: str, req: ClientPayload, request: Request):
    from core.auth import check_logged_in
    check_logged_in(request)      # 先擋匿名，載列後按列帳本驗（同 CRM 帳務慣例）
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        client = await session.get(Client, client_id)
        if not client:
            raise HTTPException(status_code=404, detail="找不到此客戶")
        _client_write_guard(request, client)
        old_am = client.am_username
        # status 由「客戶分級」自動管理（_auto_update_client_status）。客戶編輯表單
        # 不送 status，若照 ClientPayload 預設值（'潛在客戶'）寫回會把分級洗掉 ——
        # 這正是「改業務後客戶屬性跳掉」的成因。排除它，再依專案數重算。
        # entity 一律不換帳本（同收支/請款的規矩：換帳＝刪掉重建才留得下痕跡）
        for k, v in req.model_dump(exclude={"status", "entity"}).items():
            setattr(client, k, v)
        client.updated_at = _now()
        await _auto_update_client_status(session, client_id)  # 重算分級（保留手動『暫停合作』）
        await session.commit()
        await session.refresh(client)

        # Sync AM to active projects when client AM changed
        if req.am_username and req.am_username != old_am:
            await session.execute(
                sa_update(CrmProject)
                .where(CrmProject.client_id == client_id)
                .where(CrmProject.status.in_(["投標", "開發", "洽詢", "提案", "製作"]))
                .values(am_username=req.am_username)
            )
            await session.commit()

    return {"status": "ok", "client": _to_dict(client)}


def _client_write_guard(request, client):
    """客戶寫入守衛：mine 列開給 mine full scope、parent 列維持 Lv3 ——
    與 routers/crm/finance._mine_or_admin_write 同一條政策（客戶表在另一個
    領域檔，故就地一份小的；行為要一致）。"""
    if (client.entity or "parent") == "mine":
        from core.ledger import require_entity
        require_entity(request, "mine", level="full")
    else:
        _check_auth(request)


@router.delete("/clients/{client_id}")
async def delete_client(client_id: str, request: Request):
    from core.auth import check_logged_in
    check_logged_in(request)
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        client = await session.get(Client, client_id)
        if not client:
            raise HTTPException(status_code=404, detail="找不到此客戶")
        _client_write_guard(request, client)
        await session.delete(client)
        await session.commit()
    return {"status": "ok"}


# ── CSV Import ───────────────────────────────────────────────

# 欄位別名對照表（Notion export + Google Sheets 兩種格式）
_COL_MAP = {
    "short_name":        ["客戶代稱", "name", "客戶名稱", "名稱", "客戶", "代稱", "簡稱"],
    "full_name":         ["全稱", "full_name", "抬頭", "legal_name", "發票抬頭", "公司名稱", "公司", "公司全稱", "公司抬頭"],
    "tax_id":            ["統編", "統一編號", "tax_id", "統編/身分證", "統編/身份證"],
    "am_username":       ["am", "業務", "am_username"],
    "source_channel":    ["來源管道", "source_channel"],
    "contact_person":    ["客戶聯絡人", "聯絡人", "contact_person", "客戶聯絡人/聯絡..."],
    "contact_method":    ["聯絡方式", "聯絡管道", "contact_method"],
    "status":            ["狀態", "status"],
    "cooperation_note":  ["合作契機", "cooperation_note"],
    "payment_info":      ["匯款資訊", "匯款帳號", "payment_info"],
    "payment_note":      ["匯款備註", "payment_note"],
    "notes":             ["備註", "notes"],
}


def _map_row(header_map: dict, row: dict) -> dict:
    """Map a CSV row to client fields using pre-computed header_map."""
    return map_csv_row(_COL_MAP, header_map, row)


@router.post("/clients/import_csv")
async def import_csv(request: Request, file: UploadFile = File(...)):
    _check_auth(request)
    _require_db()

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("big5", errors="replace")

    # Auto-detect delimiter
    first_line = text.split('\n', 1)[0].strip()
    dialect = None
    if first_line:
        try:
            dialect = csv.Sniffer().sniff(first_line, delimiters=',\t;')
        except csv.Error:
            pass
    reader = csv.DictReader(io.StringIO(text), dialect=dialect) if dialect else csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = headers
    header_map = {h.lower(): h for h in headers if h}
    imported = updated = skipped = 0

    factory = await _get_factory()

    async with factory() as session:
        # Build dedup index: (full_name, tax_id) → Client
        all_clients = (await session.execute(select(Client))).scalars().all()
        existing_keys = {(c.full_name or "", c.tax_id or "") for c in all_clients}

        for row in reader:
            data = _map_row(header_map, row)
            full_name = data.get("full_name", "").strip()
            # 統編補回前導 0 —— 這條路是 Client(**data) 直接建、繞過
            # ClientPayload 的驗證器，所以要自己過一次（規則同一支）
            tax_id = normalize_tax_id(data.get("tax_id"))
            data["tax_id"] = tax_id

            if not full_name and not tax_id:
                skipped += 1
                continue

            # Dedup by (抬頭, 統編)
            key = (full_name, tax_id)
            if key in existing_keys:
                skipped += 1
                continue

            # No short_name → use full_name
            if not data.get("short_name"):
                data["short_name"] = full_name or tax_id

            now = _now()
            new_client = Client(id=uuid.uuid4().hex, created_at=now, updated_at=now, **data)
            session.add(new_client)
            existing_keys.add(key)
            imported += 1

        await session.commit()

    result = {"status": "ok", "imported": imported, "updated": updated, "skipped": skipped}
    if skipped > 0 and imported == 0:
        result["hint"] = f"CSV 欄位：{[h for h in headers if h]}。需包含「抬頭」或「統編」欄位才能匯入。若全部重複則跳過。"
    return result


# ── 私帳客戶對應（owner 2026-08-26「跟 crm 同步，但是用連結的方式」）──────
# 只記「私帳客戶 ↔ CRM 客戶」的對應連結、不搬資料 —— 之後「併過去」以此為依據。

def _norm_client_name(s: str) -> str:
    """比對用正規化：去空白與常見公司尾綴（「寬微廣告有限公司」≈「寬微廣告」）。"""
    import re as _re
    s = _re.sub(r"\s+", "", s or "")
    stripped = _re.sub(r"(股份有限公司|有限公司|股份公司|工作室)$", "", s)
    return stripped or s


def _suggest_crm_match(mine_name: str, parents: list):
    """在 CRM 客戶裡找**唯一**的名稱吻合候選（正規化後相等或互為前綴，≥3 字）。
    多個候選＝不猜（回 None，讓 owner 自己挑）。"""
    n = _norm_client_name(mine_name)
    if len(n) < 3:
        return None
    hits = [p for p in parents
            if (lambda m: m == n or m.startswith(n) or n.startswith(m))
               (_norm_client_name(p["short_name"]))]
    return hits[0] if len(hits) == 1 else None


# 🔴 路徑刻意不用 /clients/mine-links —— 會被更早註冊的 /clients/{client_id}
# 吃掉（client_id="mine-links" → 404），而且誰重排函式順序誰踩雷
@router.get("/clients-mine-links")
async def mine_client_links(request: Request):
    """連結清單的資料源（owner 2026-08-26「留下這個連結的清單好做日後使用」）：
    私帳客戶（含對應狀態＋名稱建議）＋CRM 客戶名錄（picker 用）＋私帳直接用
    CRM 客戶的那些（本來就同一筆，不需要連結）。"""
    from core.ledger import not_mine, require_entity
    require_entity(request, "mine", level="full")
    _require_db()
    factory = await _get_factory()
    from sqlalchemy import func as _fn
    async with factory() as session:
        mine_cnt = dict((await session.execute(
            select(CrmProject.client_id, _fn.count())
            .where(CrmProject.entity == "mine", CrmProject.client_id.isnot(None))
            .group_by(CrmProject.client_id))).all())
        par_cnt = dict((await session.execute(
            select(CrmProject.client_id, _fn.count())
            .where(not_mine(CrmProject.entity), CrmProject.client_id.isnot(None))
            .group_by(CrmProject.client_id))).all())
        mine_rows = (await session.execute(
            select(Client).where(Client.entity == "mine")
            .order_by(Client.short_name))).scalars().all()
        parent_rows = (await session.execute(
            select(Client).where(not_mine(Client.entity))
            .order_by(Client.short_name))).scalars().all()
    parents = [{"id": c.id, "short_name": c.short_name} for c in parent_rows]
    pmap = {p["id"]: p["short_name"] for p in parents}
    mine = []
    for c in mine_rows:
        link = c.crm_link_id or ""
        sug = None if link else _suggest_crm_match(c.short_name, parents)
        mine.append({
            "id": c.id, "short_name": c.short_name,
            "tax_id": c.tax_id or "", "full_name": c.full_name or "",
            "n_projects": int(mine_cnt.get(c.id, 0)),
            "crm_link_id": link, "crm_link_name": pmap.get(link, ""),
            "suggest_id": sug["id"] if sug else "",
            "suggest_name": sug["short_name"] if sug else "",
        })
    # 私帳用到的 CRM 客戶（統一主檔後這就是主表；mine 併完為空）
    shared = [{"id": c.id, "short_name": c.short_name,
               "full_name": c.full_name or "", "tax_id": c.tax_id or "",
               "n_projects": int(mine_cnt.get(c.id, 0)),
               "n_parent": int(par_cnt.get(c.id, 0))}
              for c in parent_rows if c.id in mine_cnt]
    shared.sort(key=lambda x: -x["n_projects"])
    return {"mine": mine, "crm": parents, "shared": shared}


@router.post("/clients/{client_id}/crm-create")
async def create_crm_client_from_mine(client_id: str, request: Request):
    """私帳客戶 → 在母帳（CRM）建一筆同樣的客戶並連結（owner 2026-09-05
    「連結不到的客戶……直接有一個按鈕讓我在母帳建立新客戶」，目標是
    「私帳的客戶母帳都有包含」）。

    只複製客戶主檔那幾欄（代稱／全稱／統編）—— 匯款資訊、聯絡人、合作契機
    是私帳自己談的關係，不往母帳倒。已連結的直接回現況（按第二次不會多建）。

    🔴 `short_name` 有 `(entity, short_name)` 唯一約束 —— 母帳已經有同代稱的
    那一家時**不建新的，直接連過去**，否則會撞約束回 500，而人要的本來就是
    那一筆。
    """
    from core.auth import check_logged_in
    from core.ledger import not_mine
    check_logged_in(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        c = await session.get(Client, client_id)
        if not c:
            raise HTTPException(status_code=404, detail="找不到此客戶")
        _client_write_guard(request, c)
        if (c.entity or "parent") != "mine":
            raise HTTPException(status_code=422, detail="只有私帳客戶能建立 CRM 對應")
        if c.crm_link_id:
            return {"status": "ok", "crm_link_id": c.crm_link_id, "created": False}
        exist = (await session.execute(
            select(Client).where(not_mine(Client.entity),
                                 Client.short_name == c.short_name))).scalars().first()
        if exist is None:
            exist = Client(id=uuid.uuid4().hex, entity="parent",
                           short_name=c.short_name, full_name=c.full_name or "",
                           tax_id=c.tax_id or "", status="潛在客戶")
            session.add(exist)
            created = True
        else:
            created = False
        c.crm_link_id = exist.id
        c.updated_at = _now()
        await session.commit()
        return {"status": "ok", "crm_link_id": exist.id,
                "crm_link_name": exist.short_name, "created": created}


@router.put("/clients/{client_id}/crm-link")
async def set_client_crm_link(client_id: str, request: Request):
    """設定/清除私帳客戶的 CRM 對應（body: {crm_link_id: id|null}）。
    只有 mine 列能設；目標必須是 parent 客戶 —— 連到另一個私帳客戶＝把對應
    做成環，之後併過去會無所適從。"""
    from core.auth import check_logged_in
    check_logged_in(request)
    _require_db()
    body = await request.json()
    target = (body.get("crm_link_id") or "").strip() or None
    factory = await _get_factory()
    async with factory() as session:
        client = await session.get(Client, client_id)
        if not client:
            raise HTTPException(status_code=404, detail="找不到此客戶")
        _client_write_guard(request, client)
        if (client.entity or "parent") != "mine":
            raise HTTPException(status_code=422, detail="只有私帳客戶能設定 CRM 對應")
        if target:
            t = await session.get(Client, target)
            if not t or (t.entity or "parent") == "mine":
                raise HTTPException(status_code=422, detail="對應目標必須是 CRM（母公司）客戶")
        client.crm_link_id = target
        client.updated_at = _now()
        await session.commit()
    return {"status": "ok", "crm_link_id": target or ""}


# ── Users for AM/PM pickers ──────────────────────────────────

@router.get("/users")
async def list_crm_users():
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        rows = (await session.execute(
            select(User.username, User.avatar_url, User.email)
        )).all()

    return {"users": [
        {"username": r[0], "avatar_url": r[1] or "", "email": r[2] or ""}
        for r in rows
    ]}

