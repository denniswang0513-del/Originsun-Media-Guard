"""services/brief_writer.py — 用企劃矩陣生成一份**企劃書**（Markdown）。

輸入拼裝的順序就是給 Claude 的閱讀順序：範本骨架（怎麼寫）→ 提案資料（寫什麼）
→ 規格（多長、什麼語氣）。

🔴 **矩陣的文字由前端渲染後送過來**，後端不重建。方法論的定義（三視角 × 四提問
的標籤、每格的提示問句）住在 `frontend/tabs/proposals/plan-templates.js`；後端
只存 plan blob，不知道 `participant`/`fit` 這些 key 對應的中文是什麼。要讓後端
自己組就得複製一份方法論定義 —— 那個東西改版時兩邊一定會分岔。

🔴 **空格不准編**。這是整個功能最大的風險：AI 把沒填的格子補得很漂亮，企劃
人員以為那是自己想過的，帶進提案會議才發現不是。所以空格在輸入裡就標成
「（未填）」，並要求輸出把它們列進「待補清單」。
"""
from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)

LENGTHS = {
    "brief": "精簡版：一頁的份量，每節二到四句，只留最關鍵的判斷。",
    "full": "完整版：一份可以直接寄給客戶的提案企劃書。",
    "deep": "逐節詳寫：每一節都展開，含具體作法與備案。",
}
TONES = {
    "pitch": "提案說服：對外，寫給客戶看。自信但不浮誇，重點是讓對方看見畫面。",
    "plain": "實務直白：對內，寫給製作團隊看。講清楚要做什麼、哪裡有風險。",
}

_PROMPT = """你是一位資深影像製作公司的提案企劃主編，正在替一個新案子寫**企劃書**。

{skeletons}
────────────── 這個案子的資料 ──────────────
{facts}
────────────── 資料結束 ──────────────

## 規格
- {length}
- {tone}
- 用**繁體中文**，輸出 Markdown。直接寫企劃書本文，不要開場白、不要程式碼圍欄。

## 🔴 鐵則
1. **只用上面實際有的內容寫。** 標成「（未填）」的格子代表企劃人員還沒想，
   **不准替他想**、不准用常識補、不准寫「可以考慮…」把它填滿。
2. 全文最後加一節 `## 待補`，把所有「（未填）」而你判斷這份企劃書**需要**的
   項目列出來，一項一行，寫成問句（要問企劃人員什麼才能補上）。沒有就寫
   「目前沒有明顯缺口」。
3. 不要杜撰數字、日期、人名、地名、預算。資料裡沒有就不要出現。
"""


def _cell(v) -> str:
    """矩陣格子的值 → 文字。空的一律標「（未填）」—— 讓 AI 看得見缺口。"""
    s = (v or "").strip() if isinstance(v, str) else ""
    return s or "（未填）"


def build_facts(prop: dict, *, matrix_text: str, include: set) -> str:
    """把提案資料組成給 Claude 讀的段落。`include` 決定哪幾塊要進去。"""
    out = [f"## 提案標題\n{prop.get('title') or '（未填）'}"]
    meta = []
    if prop.get("client_name"):
        meta.append(f"客戶：{prop['client_name']}")
    if prop.get("ptype"):
        meta.append(f"類型：{prop['ptype']}")
    if prop.get("pitch_date"):
        meta.append(f"提案日：{prop['pitch_date']}")
    if "budget" in include and prop.get("budget_range"):
        meta.append(f"預算範圍：{prop['budget_range']}")
    if meta:
        out.append("## 基本資料\n" + "\n".join(meta))

    out.append("## 企劃矩陣\n" + (matrix_text.strip() or "（整份矩陣都還沒填）"))

    if "survey" in include:
        rows = [r for r in (prop.get("survey") or []) if (r.get("value") or "").strip()]
        if rows:
            out.append("## 現況盤點\n" + "\n".join(
                f"- {r.get('label') or r.get('key')}：{r['value'].strip()}" for r in rows))
    if "refs" in include:
        refs = prop.get("references") or []
        if refs:
            out.append("## 參考影片\n" + "\n".join(
                f"- {r.get('title') or r.get('url')}"
                + (f"：{r['note'].strip()}" if (r.get('note') or '').strip() else '')
                for r in refs))
    if "notes" in include and (prop.get("notes") or "").strip():
        out.append("## 內部備註\n" + prop["notes"].strip())
    return "\n\n".join(out)


def build_skeletons(templates: list) -> str:
    """選定範本的骨架 → prompt 的第一段。沒選就明說「照方法論自己寫」。"""
    good = [t for t in templates if (t.get("skeleton") or "").strip()]
    if not good:
        return ("這次沒有指定參考範本 —— 請照一般提案企劃書的章節寫，"
                "重點放在把下面的資料組織成連貫的敘事。\n")
    parts = ["以下是**過去寫得好的企劃書骨架**。請照它的章節結構與語氣寫這一份"
             "（內容當然要換成這個案子的）：\n"]
    for i, t in enumerate(good, 1):
        parts.append(f"────── 參考範本 {i}：{t.get('name') or ''} ──────\n"
                     f"{t['skeleton'].strip()}\n")
    return "\n".join(parts)


async def write_brief(brief_id: str, *, prop: dict, templates: list,
                      matrix_text: str, options: dict) -> tuple[bool, str]:
    """跑生成並把結果寫回那一版。回 `(成功, 訊息)`。"""
    include = set(options.get("include") or ["survey", "refs"])
    prompt = _PROMPT.format(
        skeletons=build_skeletons(templates),
        facts=build_facts(prop, matrix_text=matrix_text, include=include),
        length=LENGTHS.get(options.get("length") or "full", LENGTHS["full"]),
        tone=TONES.get(options.get("tone") or "pitch", TONES["pitch"]),
    )
    from services.website.seo_runner import _call_claude
    out, err = await _call_claude(prompt)
    if not out or not out.strip():
        return await _save(brief_id, status="failed",
                           error=err or "claude 沒有回應")
    from services.brief_template_digest import _strip_fence
    content = _strip_fence(out).strip()
    await _save(brief_id, content=content, status="ok", error=None)
    logger.info("[brief_writer] %s 生成完成（%d 字）", brief_id, len(content))
    return True, content


async def _save(brief_id: str, *, content=None, status=None, error=...) -> tuple:
    from core.db_guard import db_factory_or_503
    from db.models import PreprodBrief
    from routers.crm._shared import _now
    factory = db_factory_or_503()
    async with factory() as session:
        b = await session.get(PreprodBrief, brief_id)
        if not b:
            return False, "找不到這一版企劃書"
        if content is not None:
            b.content = content
        if status is not None:
            b.status = status
        if error is not ...:
            b.error = error
        b.updated_at = _now()
        await session.commit()
    return (status == "ok"), (error if error is not ... and error else "")


def new_id() -> str:
    return uuid.uuid4().hex
