"""
api_bulletin.py — 公布欄待辦提醒 CRUD + 「問 Claude」諮詢（團隊共用，存 mediaguard）。

權限：check_admin_or_module(request, 'bulletin') — 管理員或帳號有 bulletin 模組即可讀寫。
排序：置頂 → 手動 sort_order → 建立時間（DB 端 order_by）；priority 前端當徽章/篩選用。

「問 Claude」= 唯讀諮詢：背景跑 `claude --print --permission-mode plan`（plan 模式只讀不改），
把回覆寫進該項的 conversation。不執行任何動作 → 安全。
"""
import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request  # type: ignore
from sqlalchemy import select, func  # type: ignore

from core.auth import check_admin_or_module
from core.schemas import BulletinCreate, BulletinUpdate, BulletinReorder, BulletinAsk
from db.models import BulletinItem
import core.state as state

router = APIRouter(prefix="/api/v1", tags=["bulletin"])

# 顯示順序：置頂 → 手動 sort_order → 建立時間。
_ORDER_BY = (BulletinItem.pinned.desc(), BulletinItem.sort_order, BulletinItem.created_at)

# 「問 Claude」序列化（同時間只跑一個 claude 子程序，避免併發爆量/搶額度）。
_ask_lock = asyncio.Lock()


def _to_dict(o) -> dict:
    return {
        "id": o.id, "title": o.title, "note": o.note or "",
        "status": o.status, "priority": o.priority, "category": o.category or "",
        "pinned": bool(o.pinned), "sort_order": o.sort_order,
        "assignee": getattr(o, "assignee", None) or "me",
        "conversation": getattr(o, "conversation", None) or [],
        "activity": getattr(o, "activity", None) or "",
        "created_by": o.created_by,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "done_at": o.done_at.isoformat() if o.done_at else None,
    }


def _require_db():
    if not state.db_online:
        raise HTTPException(status_code=503, detail="資料庫離線，公布欄暫不可用")
    from db.session import get_session_factory
    factory = get_session_factory()
    if factory is None:
        raise HTTPException(status_code=503, detail="資料庫未初始化")
    return factory


@router.get("/bulletin")
async def list_bulletin(request: Request):
    check_admin_or_module(request, "bulletin")
    factory = _require_db()
    async with factory() as session:
        rows = (await session.execute(select(BulletinItem).order_by(*_ORDER_BY))).scalars()
        return {"items": [_to_dict(r) for r in rows]}


@router.get("/bulletin/{item_id}")
async def get_bulletin(item_id: str, request: Request):
    """單筆（給「問 Claude」對話輪詢用）。"""
    check_admin_or_module(request, "bulletin")
    factory = _require_db()
    async with factory() as session:
        obj = await session.get(BulletinItem, item_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到項目")
        return _to_dict(obj)


@router.post("/bulletin")
async def create_bulletin(body: BulletinCreate, request: Request):
    payload = check_admin_or_module(request, "bulletin")
    if not (body.title or "").strip():
        raise HTTPException(status_code=422, detail="標題必填")
    factory = _require_db()
    async with factory() as session:
        maxo = (await session.execute(select(func.max(BulletinItem.sort_order)))).scalar() or 0
        obj = BulletinItem(
            id=uuid.uuid4().hex[:12],
            title=body.title.strip(),
            note=(body.note or "").strip() or None,
            status=body.status or "todo",
            priority=body.priority or "med",
            category=(body.category or "").strip() or None,
            pinned=bool(body.pinned),
            assignee=body.assignee or "me",
            sort_order=int(maxo) + 1,
            created_by=(payload or {}).get("sub"),
        )
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
        return _to_dict(obj)


@router.put("/bulletin/{item_id}")
async def update_bulletin(item_id: str, body: BulletinUpdate, request: Request):
    check_admin_or_module(request, "bulletin")
    factory = _require_db()
    async with factory() as session:
        obj = await session.get(BulletinItem, item_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到項目")
        data = body.model_dump(exclude_unset=True)
        # 字串欄位 strip、空→None（title 空則不動）
        for k in ("note", "category"):
            if k in data:
                setattr(obj, k, (data[k] or "").strip() or None)
        if data.get("title"):
            obj.title = data["title"].strip()
        # 直取欄位
        for k in ("priority", "pinned", "status", "assignee"):
            if data.get(k) is not None:
                setattr(obj, k, data[k])
        if "status" in data:
            obj.done_at = datetime.now() if obj.status == "done" else None
        await session.commit()
        await session.refresh(obj)
        return _to_dict(obj)


@router.delete("/bulletin/{item_id}")
async def delete_bulletin(item_id: str, request: Request):
    check_admin_or_module(request, "bulletin")
    factory = _require_db()
    async with factory() as session:
        obj = await session.get(BulletinItem, item_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到項目")
        await session.delete(obj)
        await session.commit()
    return {"deleted": item_id}


@router.post("/bulletin/reorder")
async def reorder_bulletin(body: BulletinReorder, request: Request):
    check_admin_or_module(request, "bulletin")
    factory = _require_db()
    order = {iid: i for i, iid in enumerate(body.ordered_ids)}
    async with factory() as session:
        # 一次撈齊再就地改 sort_order（免 N 次 session.get 往返）。
        rows = (await session.execute(
            select(BulletinItem).where(BulletinItem.id.in_(list(order))))).scalars()
        for r in rows:
            r.sort_order = order[r.id]
        await session.commit()
    return {"ok": True}


# ══════════════════════════════════════════════════════════
# 問 Claude（唯讀諮詢）
# ══════════════════════════════════════════════════════════

_ASK_SYSTEM = (
    "你是「源日影像 Originsun Studio」內部後台「公布欄」的助理。使用者針對一則待辦事項問你。\n"
    "請用繁體中文、精簡實用地回答／起草／研究／教學。你目前是【唯讀諮詢】模式：只給建議與草稿，\n"
    "不會也不能真的去改系統、跑指令或部署。若使用者要你「實際去做」，請說明這要在 Claude Code\n"
    "session 由真人操作，並把該做的步驟條列清楚（可直接複製給工程用）。"
)


def _build_ask_prompt(title: str, note: str, convo: list) -> str:
    lines = [_ASK_SYSTEM, "", f"【待辦事項】{title}"]
    if note:
        lines.append(f"【備註】{note}")
    lines.append("\n【對話（含使用者最新提問）】")
    for m in convo[-12:]:
        who = "使用者" if m.get("role") == "user" else "Claude"
        lines.append(f"{who}：{m.get('text', '')}")
    lines.append("\n（請回覆使用者最新一則提問。）")
    return "\n".join(lines)


async def _run_ask(item_id: str) -> None:
    """背景執行：讀對話 → 唯讀 claude → 回覆寫回 conversation。"""
    from db.session import get_session_factory
    factory = get_session_factory()
    if factory is None:
        return
    async with factory() as session:
        obj = await session.get(BulletinItem, item_id)
        if obj is None:
            return
        title, note, convo = obj.title, obj.note or "", list(obj.conversation or [])
    from services.website.seo_runner import _call_claude
    async with _ask_lock:
        text, err = await _call_claude(_build_ask_prompt(title, note, convo),
                                       extra_args=["--permission-mode", "plan"])
    reply = (text or "").strip() or f"（Claude 沒有回應：{err or '未知錯誤'}）"
    async with factory() as session:
        obj = await session.get(BulletinItem, item_id)
        if obj is None:
            return
        convo = list(obj.conversation or [])
        convo.append({"role": "claude", "text": reply,
                      "at": datetime.now().isoformat(timespec="seconds")})
        obj.conversation = convo
        await session.commit()


@router.post("/bulletin/{item_id}/ask")
async def ask_bulletin(item_id: str, body: BulletinAsk, request: Request):
    """把使用者提問寫進對話 + 背景跑唯讀 claude；前端輪詢 GET /bulletin/{id} 看回覆。"""
    check_admin_or_module(request, "bulletin")
    q = (body.message or "").strip()
    if not q:
        raise HTTPException(status_code=422, detail="訊息必填")
    factory = _require_db()
    async with factory() as session:
        obj = await session.get(BulletinItem, item_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到項目")
        convo = list(obj.conversation or [])
        convo.append({"role": "user", "text": q,
                      "at": datetime.now().isoformat(timespec="seconds")})
        obj.conversation = convo
        await session.commit()
    asyncio.create_task(_run_ask(item_id))
    return {"status": "asking"}


# ── 種子：表空時塞入開場待辦（seed 邏輯與 feature 同住；mirrors db.seed_website）──
async def seed_if_empty(session) -> int:
    """bulletin_items 空時塞入開場待辦清單。回新增筆數（0 = 已有資料、跳過）。"""
    cnt = (await session.execute(select(func.count(BulletinItem.id)))).scalar() or 0
    if cnt:
        return 0
    seed = [
        ("存好備份加密金鑰到密碼管理器（settings 解密用；master 掛了才解得開）", "備份", "high", True),
        ("提供 46 條舊作品網址對照清單 → 補 301 轉址（保 SEO 權重）", "官網", "med", False),
        ("聯絡表單實測一次（www 域 Turnstile 驗證碼 + 收得到信）", "官網", "med", False),
        ("Bing Webmaster Tools 提交 sitemap", "官網", "low", False),
        ("清 CORS 白名單殘留的 test.originsun-studio.com", "系統", "low", False),
        ("新站穩定後停掉舊 WordPress 主機（43.254.17.7）", "系統", "low", False),
        ("翻譯 backlog 165 篇（每日自動清，想加速可手動整批）", "官網", "low", False),
    ]
    for i, (title, cat, pri, pin) in enumerate(seed):
        session.add(BulletinItem(
            id=uuid.uuid4().hex[:12], title=title, category=cat,
            priority=pri, pinned=pin, status="todo", sort_order=i, created_by="system"))
    await session.commit()
    return len(seed)
