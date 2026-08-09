"""services/brief_template_digest.py — 把企劃範本濃縮成「骨架」。

一份完整企劃可能上萬字。生成企劃書時塞兩三份原文就把 prompt 撐爆，而且每次
生成都重讀同樣的內容 —— 所以上傳後**消化一次**，之後只餵這幾百字的骨架。

🔴 骨架刻意是**純文字、人看得懂、人改得動**：生成出來不對味時直接改那段，
比回頭調 prompt 直觀得多（範本庫畫面上那個 textarea 就是給這件事用的）。

🔴 骨架**不保留客戶名、金額、人名**。範本常常是以前做過的真實提案，裡面夾著
別家客戶的資訊；而骨架是會被反覆餵進 prompt 的東西，那些不該長期躺在裡面。
這條寫在 prompt 裡，也寫在範本庫的畫面上。

claude 的呼叫層一律走 seo_runner 那支 —— 它的 exe 解析有三層 fallback，是吃過
兩次生產事故換來的（master 的 uvicorn 由 start_hidden.vbs 啟動，PATH 很精簡、
環境變數被剝到連 %LOCALAPPDATA% 都沒有）。**不要在這裡寫第二份 which("claude")**。
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

# 餵進 prompt 的原文上限。21 頁的 PDF 約 6 千字，40k 給了很寬的餘裕；
# 真的超過就截斷 —— 骨架要的是結構與語氣，末尾的附錄對它沒有貢獻。
MAX_TEXT_CHARS = 40_000

_PROMPT = """你是一位資深影像製作公司的提案企劃主編。下面是一份**過去寫得好的企劃書**，
我要把它當成未來寫新企劃書時的參考範本。

請把它濃縮成一份「骨架」，讓另一個人（或另一個 AI）看了骨架就能照著同樣的
結構與語氣寫出新的企劃書。**用繁體中文**，直接輸出骨架本文，不要任何開場白、
不要 markdown 程式碼圍欄。

骨架請包含這五段，每段用「## 」開頭：

## 章節結構
依序列出這份範本有哪幾節，每節一行：節名 —— 這一節在回答什麼問題。

## 各節怎麼寫
每節一到兩句：內容的切入角度、詳略程度、大概多長。

## 語氣與句法
這份範本的寫法特徵：敘事還是條列？用不用提問句？第幾人稱？句子長短？
有沒有慣用的開場或收束方式？

## 慣用術語
這份範本反覆出現、值得沿用的**體例用語**（條列，最多 12 個）——
指的是製作流程與文件格式上的說法（例如「拍攝重點」「棚拍訪談（暫定）」
「分場」「備案」）。**題材專屬的詞不要列**（例如某種地形、某類道具、
某個年齡層的活動名稱）。

## 生成前必問
這份範本的結構，有哪些事情是**不問就寫不出來**的？列出 3 到 8 個問題，
每個問題自成一行、以「- 」開頭、寫成直接問企劃人員的問句。
只列**這份範本的結構特別需要、而一般企劃矩陣不會涵蓋**的（例如場地細節、
路線與時程、參與者年齡、法規或許可）。不要列矩陣本來就會問的東西。

🔴 **絕對不要保留任何客戶名稱、人名、公司名、金額、日期或專案代號。**
遇到就抽象化（例如「某客戶」「主角孩童」「預算區間」）。骨架會被反覆使用，
不該夾帶特定案子的資訊。

🔴 只描述**這份範本實際有的東西**。範本沒寫到的章節不要自己補上去。

🔴 **骨架必須是通用的：不綁題材、不綁年齡、不綁場域類型。**
這份骨架之後會被套用在完全不同的案子上（不同主題、不同年齡層、不同場域），
主題與受眾是生成當下才決定的。所以凡是只適用於這一個案子的具體名詞，一律
抽象成它在結構中扮演的角色：

  「山林介紹」→「場域介紹」        「小山友」→「主要參與者」
  「行動糧」  →「隨身道具」        「登山路線與估時」→「行程與時間規劃」
  「六歲小孩」→「目標年齡層」      「客家文化小教室」→「在地知識補充段」

判準很簡單：**換一個題材之後這句話還成立嗎？** 不成立就抽象化。

全文請控制在 800 字以內。

────────────── 範本全文開始 ──────────────
{text}
────────────── 範本全文結束 ──────────────
"""


async def digest_template(tid: str) -> tuple[bool, str]:
    """消化一份範本 → `(成功, 訊息)`。狀態與骨架直接寫回 DB。

    冪等：重跑會覆蓋骨架（人手改過的也會被蓋掉 —— 呼叫端要先問過使用者）。
    """
    from core.db_guard import db_factory_or_503
    from core.doc_text import extract_text
    from db.models import PreprodBriefTemplate
    from routers.crm.brief_templates import _templates_dir

    factory = db_factory_or_503()
    async with factory() as session:
        t = await session.get(PreprodBriefTemplate, tid)
        if not t:
            return False, "找不到這個範本"
        filename = t.filename

    d = await _templates_dir()
    path = os.path.join(d, filename) if d else ""
    if not path or not os.path.isfile(path):
        return await _fail(tid, "範本檔案不見了（NAS 搆不到，或已被手動刪除）")

    text, err = await asyncio.to_thread(extract_text, path)
    if err:
        return await _fail(tid, f"抽文字失敗：{err}")

    from services.website.seo_runner import _call_claude
    out, call_err = await _call_claude(
        _PROMPT.format(text=text[:MAX_TEXT_CHARS]))
    if not out:
        # 原文先存起來 —— 下次重跑不必再解一次 PDF，而且人可以自己讀
        await _save(tid, text=text)
        return await _fail(tid, call_err or "claude 沒有回應")

    skeleton = _strip_fence(out).strip()
    if not skeleton:
        await _save(tid, text=text)
        return await _fail(tid, "claude 回了空白")

    await _save(tid, text=text, skeleton=skeleton, status="ok", error=None)
    logger.info("[brief_digest] %s 消化完成（骨架 %d 字）", tid, len(skeleton))
    return True, skeleton


# 骨架裡「## 生成前必問」那一段的條列 —— **從骨架文字推導**，不另存欄位：
# 人改骨架時問題清單就跟著變，不會有第二份資料在旁邊漂移。
_Q_HEAD = "生成前必問"


def parse_questions(skeleton: str) -> list:
    """骨架 → 生成前必問的清單。沒有那一段就回空。"""
    out, inside = [], False
    for line in (skeleton or "").splitlines():
        t = line.strip()
        if t.startswith("#"):
            inside = _Q_HEAD in t
            continue
        if not inside or not t:
            continue
        if t[0] in "-*・":
            t = t[1:].strip()
        elif t[:2].rstrip(".、) ").isdigit():
            t = t.split(" ", 1)[-1].strip() if " " in t else t.lstrip("0123456789.、) ")
        else:
            continue
        if t:
            out.append(t)
    return out[:8]


def _strip_fence(s: str) -> str:
    """Claude 偶爾還是會包 ``` 圍欄，即使叫它不要 —— 剝掉而不是重試。"""
    t = (s or "").strip()
    if not t.startswith("```"):
        return t
    lines = t.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


async def _save(tid: str, *, text=None, skeleton=None, status=None, error=...) -> None:
    from core.db_guard import db_factory_or_503
    from db.models import PreprodBriefTemplate
    from routers.crm._shared import _now
    factory = db_factory_or_503()
    async with factory() as session:
        t = await session.get(PreprodBriefTemplate, tid)
        if not t:
            return
        if text is not None:
            t.extracted_text = text
        if skeleton is not None:
            t.skeleton = skeleton
        if status is not None:
            t.status = status
        if error is not ...:
            t.error = error
        t.updated_at = _now()
        await session.commit()


async def _fail(tid: str, msg: str) -> tuple[bool, str]:
    await _save(tid, status="failed", error=msg)
    logger.warning("[brief_digest] %s 消化失敗：%s", tid, msg)
    return False, msg
