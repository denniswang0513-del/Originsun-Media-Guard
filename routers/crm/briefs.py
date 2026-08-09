"""routers/crm/briefs.py — 提案的**企劃書**（多版）。

企劃矩陣是思考過程，企劃書是輸出物。兩者並存 —— 生成物**不寫回 plan JSONB**
（那是人的共編工作區，有逐格 updated_by 與樂觀鎖，AI 一次寫 12 格會把共編紀錄
整片洗掉，也會蓋掉同事正在打的字）。

「重新生成」是**新增一版**不是覆蓋（owner 2026-08-10）。每一版存生成當下的
矩陣快照 —— 矩陣天天在改，不存的話「這份是根據哪一版寫的」永遠說不清。

生成本身在 services/brief_writer.py；這裡只管存取與編輯。
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException, Request  # type: ignore

from core.bg_status import settle
from core.bg_task import fire

# 提案庫的三模組閘門只有一份（正本與 owner 的決策註解都在 api_proposals）
from routers.api_proposals import _check_auth as _auth

from ._shared import router, _get_factory, _now, _require_db

try:
    from sqlalchemy import func as safunc
    from sqlalchemy.orm import defer
    from ._shared import select
    from db.models import PreprodBrief
except ImportError:  # DB 套件不存在的 agent 環境
    pass


def _dict(b, *, full: bool = False, chars: int = 0) -> dict:
    """清單只回 meta —— 一份企劃書上萬字，列十版就是幾十萬字進網路。

    `chars` 一律由呼叫端帶進來（清單那支從 SQL 算）：這裡不能去 `len(b.content)`
    —— content 是 defer 掉的，一碰就把整份正文拉回來，正好抵銷 defer。
    """
    # 卡太久的 pending 在讀取端改判 failed（伺服器重啟過 → task 沒了）
    status, error = settle(b.status or "ok", b.updated_at, b.error or "")
    d = {
        "id": b.id, "proposal_id": b.proposal_id,
        "status": status, "error": error,
        "template_ids": b.template_ids or [], "options": b.options or {},
        "chars": chars,
        "created_by": b.created_by or "",
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
    }
    if full:
        d["content"] = b.content or ""
        d["plan_snapshot"] = b.plan_snapshot or None
    return d


@router.get("/proposals/{pid}/briefs")
async def list_briefs(pid: str, request: Request):
    """某個提案的所有版本（新到舊，只回 meta）。"""
    _auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        # defer 正文與矩陣快照、字數改由 SQL 算 —— 這支是前端每 5 秒輪詢的
        # 目標，把每一版的全文都拉出來只為了 len() 是純浪費
        rows = (await session.execute(
            select(PreprodBrief, safunc.length(PreprodBrief.content))
            .options(defer(PreprodBrief.content), defer(PreprodBrief.plan_snapshot))
            .where(PreprodBrief.proposal_id == pid)
            .order_by(PreprodBrief.created_at.desc()))).all()
    return {"briefs": [_dict(b, chars=n or 0) for b, n in rows]}


@router.get("/briefs/{bid}")
async def get_brief(bid: str, request: Request):
    """單一版本的全文。"""
    _auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        b = await session.get(PreprodBrief, bid)
        if not b:
            raise HTTPException(status_code=404, detail="找不到這一版企劃書")
        return {"brief": _dict(b, full=True)}


@router.post("/proposals/{pid}/briefs")
async def create_brief(pid: str, request: Request, body: dict = None):
    """手動新增一版（空白或貼上既有內容）。AI 生成走 services/brief_writer。

    留這條路是因為：企劃書不一定要 AI 寫。有人已經在 Word 裡寫好了，貼進來
    一樣要能存版本、一樣要能被「選一段改寫」。
    """
    payload = _auth(request)
    _require_db()
    body = body or {}
    factory = await _get_factory()
    async with factory() as session:
        b = PreprodBrief(
            id=uuid.uuid4().hex, proposal_id=pid,
            content=(body.get("content") or "").strip() or None,
            status="ok", created_by=payload.get("username") or "",
            created_at=_now(), updated_at=_now())
        session.add(b)
        await session.commit()
        return {"status": "ok",
                "brief": _dict(b, full=True, chars=len(b.content or ""))}


@router.post("/proposals/{pid}/briefs/generate")
async def generate_brief(pid: str, request: Request, body: dict):
    """用企劃矩陣生成一版企劃書（跑 claude，數十秒到數分鐘）。

    body:
      matrix_text  前端渲染好的矩陣文字（**方法論的標籤住在前端**，後端只存
                   blob、不知道 key 對應的中文 —— 見 services/brief_writer 檔頭）
      template_ids 參考哪幾份範本骨架（可空）
      options      {length, tone, include:[survey|refs|notes|budget]}

    **背景跑立刻回**，前端靠版本清單的 status 輪詢。新增一版，不覆蓋舊的。
    """
    payload = _auth(request)
    _require_db()
    matrix_text = (body.get("matrix_text") or "").strip()
    template_ids = list(body.get("template_ids") or [])
    options = dict(body.get("options") or {})

    from db.models import PreprodBriefTemplate
    from routers.api_proposals import get_proposal

    # 提案全文走既有端點的同一支 —— client_name/references/survey 的組法只有一份
    prop = (await get_proposal(pid, request))["proposal"]

    factory = await _get_factory()
    async with factory() as session:
        templates = []
        if template_ids:
            templates = [
                {"name": t.name, "skeleton": t.skeleton or ""}
                for t in (await session.execute(
                    select(PreprodBriefTemplate)
                    .where(PreprodBriefTemplate.id.in_(template_ids)))).scalars().all()]
        b = PreprodBrief(
            id=uuid.uuid4().hex, proposal_id=pid, status="pending",
            template_ids=template_ids, options=options,
            plan_snapshot=prop.get("plan"),      # 這一刻的矩陣 —— 之後才說得出根據
            created_by=payload.get("username") or "",
            created_at=_now(), updated_at=_now())
        session.add(b)
        await session.commit()
        out = _dict(b)

    from services.brief_writer import write_brief
    # 走 core.bg_task.fire：強參考（裸 create_task 只被 loop 弱參考）+ 例外守衛，
    # 例外沒被接住的話這一版會永遠停在 pending
    fire(write_brief(b.id, prop=prop, templates=templates,
                     matrix_text=matrix_text, options=options),
         label=f"brief {b.id}")
    return {"status": "started", "brief": out}


@router.patch("/briefs/{bid}")
async def update_brief(bid: str, request: Request, body: dict):
    """就地編輯正文。**每一版都可以改** —— 生成物是草稿不是聖旨，人改完直接存；
    要留下改之前的樣子就再生成一版（版本本來就是為此存在的）。

    這是自動儲存打的那一支（每次停手 800ms），所以刻意不搬正文：載入時 defer
    掉舊的 content 與矩陣快照，回覆也只回字數。整份來回三趟只為了覆寫一個欄位
    是這條路上最貴的浪費。
    """
    _auth(request)
    _require_db()
    if "content" not in body:
        raise HTTPException(status_code=422, detail="content 必填")
    content = body.get("content") or ""
    factory = await _get_factory()
    async with factory() as session:
        b = await session.get(PreprodBrief, bid,
                              options=[defer(PreprodBrief.content),
                                       defer(PreprodBrief.plan_snapshot)])
        if not b:
            raise HTTPException(status_code=404, detail="找不到這一版企劃書")
        b.content = content
        b.updated_at = _now()
        await session.commit()
    return {"status": "ok", "chars": len(content)}


# 反白一段叫 Claude 改寫的預設指令。key 是 UI 上那幾顆鈕。
REWRITE_MODES = {
    "expand": "把這段寫詳細一點：補上具體的作法或畫面，但**不要加入原文沒有的事實**。",
    "shorten": "把這段寫精簡一點：留下判斷與重點，砍掉贅語。",
    "rephrase": "換一個說法重寫這段：意思不變，句法與用詞換過。",
    "softer": "把這段的語氣放軟一點：對客戶說話，減少斷言。",
}


@router.post("/briefs/{bid}/rewrite")
async def rewrite_selection(bid: str, request: Request, body: dict):
    """選一段叫 Claude 改寫 → 直接回新的那一段（body {selection, mode|instruction}）。

    **同步**回傳，跟生成整份不一樣：改一段只要十幾秒，而使用者正盯著那段看 ——
    背景跑再輪詢反而讓「反白→改掉」這件事變得不直覺。

    🔴 回傳的是**新的那一段**，由前端接回原文。後端不做字串取代 —— 同一段文字
    在一份企劃書裡可能出現不只一次，取代第一個命中會改錯地方。
    """
    _auth(request)
    _require_db()
    selection = (body.get("selection") or "").strip()
    if not selection:
        raise HTTPException(status_code=422, detail="selection 必填")
    mode = (body.get("mode") or "").strip()
    instruction = (body.get("instruction") or "").strip() or REWRITE_MODES.get(mode)
    if not instruction:
        raise HTTPException(status_code=422, detail="mode 或 instruction 至少要有一個")

    factory = await _get_factory()
    async with factory() as session:
        b = await session.get(PreprodBrief, bid)
        if not b:
            raise HTTPException(status_code=404, detail="找不到這一版企劃書")
        context = (b.content or "")[:4000]

    # 用 list + join 組 prompt：這一段全是中文與分隔線，硬塞在字串字面裡
    # 逃脫字元很容易寫錯（實際踩過一次）
    prompt = chr(10).join([
        "你正在編輯一份影像製作提案的企劃書（繁體中文）。",
        "",
        "下面是整份企劃書的開頭，只給你**上下文**用，不要改動它：",
        "─────", context, "─────",
        "",
        "現在請改寫**這一段**：",
        "─────", selection, "─────",
        "",
        f"要求：{instruction}",
        "",
        "🔴 只輸出改寫後的那一段本文。不要開場白、不要引號、不要程式碼圍欄、"
        "不要解釋你改了什麼。",
        "🔴 不要杜撰原文沒有的數字、日期、人名、地名、預算。",
    ])
    from services.website.seo_runner import _call_claude, strip_fence
    out, err = await _call_claude(prompt)
    if not out or not out.strip():
        raise HTTPException(status_code=502, detail=err or "claude 沒有回應")
    return {"status": "ok", "text": strip_fence(out).strip()}


@router.delete("/briefs/{bid}")
async def delete_brief(bid: str, request: Request):
    _auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        b = await session.get(PreprodBrief, bid)
        if not b:
            raise HTTPException(status_code=404, detail="找不到這一版企劃書")
        await session.delete(b)
        await session.commit()
    return {"status": "ok"}
