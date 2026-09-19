# -*- coding: utf-8 -*-
"""core/knowledge_logic.py — 「知識庫」（書架＋討論＋結論；獨立應用 /knowledge.html）的**純規則**（無 I/O、不碰 DB、不碰檔案）。

規劃正本：docs/KNOWLEDGE_BASE_PLAN.md（§11：2026-09-18 從私帳拆出來，未必跟財務有關）。I/O 在 services/knowledge_service.py，
叫 claude 的那條線在 services/knowledge_claude.py，端點在 routers/api_knowledge.py。

這裡做五類事：
  1. 路徑安全 —— `is_valid_id`／`new_id`／`slugify_chapter`／`chapter_filename`／`split_files` 的白名單。
     🔴 任何拼路徑前一律先 `is_valid_id`；章檔名由**我們**產，不信 claude 回的路徑。
  2. 全文切法 —— `page_index`（每頁前 80 字給結構 pass 看）、`split_chapters`（照頁界切章）、`chunk_text`（章太長切段）。
  3. 提示 —— 結構／章節／骨架／討論／整理結論五種。全部繁體中文、保留作者原用語（譯本括號附原文）、
     不推薦金融商品、不預測行情。提示本身**中性**（不假設書是財務書）；討論提示只在 `finance=True`
     （meta.tags 含 `FINANCE_TAG`）時走財務顧問角色＋帶顧問快照的**四個數**（`snapshot_lines`），不塞整份 latest.json。
  4. 回覆解析 —— `parse_structure`（容錯：抓第一個 JSON 陣列）、`split_files`（`===== FILE: <name> =====` 分段）。
  5. 標籤 —— `normalize_tags`（去空白、去重、每個 ≤ 20 字、最多 10 個）；`FINANCE_TAG` 是顧問與財務角色認的那一個。

另有結論追加格式 `append_section` 與對話的形狀 `chat_message`／`recent_chat`。
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import date, timedelta
from typing import Any, Optional

# ── 常數 ─────────────────────────────────────────────────────
#: 書的 id：16 hex（URL 用；標題在 meta）。大寫、`..`、斜線、長度不對一律不收。
_ID_RE = re.compile(r"^[0-9a-f]{16}$")
#: 全文裡每頁前面的標記（pymupdf 抽出來時加的）
PAGE_MARK = "[[p.{n}]]"
_PAGE_MARK_RE = re.compile(r"\[\[p\.(\d+)\]\]")
#: 結構 pass 給 claude 看的：每頁前幾個字
INDEX_HEAD_CHARS = 80
#: 結構 pass 另附全書開頭多少字（目錄通常在這裡）
STRUCTURE_HEAD_CHARS = 8000
#: 章文超過這個長度就切段各產再合併
CHAPTER_CHUNK_CHARS = 60000
#: 討論提示帶最近幾則對話
RECENT_CHAT_N = 30
#: 骨架 pass 一次餵進去的章節 md 總量上限（超過就每章等比例砍尾）
SUPPORT_INPUT_CHARS = 200000
#: 骨架 pass 只准產這四個檔（split_files 白名單）
SUPPORT_FILES = ("SKILL.md", "glossary.md", "patterns.md", "cheatsheet.md")
#: 結構抓不到時的退路：每幾頁當一段
FALLBACK_PAGES_PER_CHAPTER = 25
#: 對話角色（同報價助理／公布欄）
ROLE_USER = "user"
ROLE_AI = "ai"
#: 討論回覆結尾「可存成結論：」那一行的前綴（前端「存成結論」與整理結論都認它）
CONCLUSION_MARK = "可存成結論："
#: 標籤（meta.tags）：這一個是「財務書」的記號 —— 顧問 agent 只讀有它的書、討論提示只在有它時帶四個數＋財務顧問角色
FINANCE_TAG = "財務"
#: 標籤上限：每個 ≤ 20 字、最多 10 個
TAG_MAX_LEN = 20
TAGS_MAX = 24        # owner 2026-09-19：「標籤可以很多個」（原本 10）

_FILE_SEP_RE = re.compile(r"^=====\s*FILE:\s*(.+?)\s*=====\s*$", re.M)
_PATH_CHARS_RE = re.compile(r'[\\/:*?"<>|\[\]#%\x00-\x1f]')
_WS_RE = re.compile(r"[\s_]+")
_DASHES_RE = re.compile(r"-{2,}")


# ── 1. 路徑安全 ──────────────────────────────────────────────
def is_valid_id(book_id: Any) -> bool:
    """16 碼小寫 hex 才是合法的書 id。"""
    return isinstance(book_id, str) and bool(_ID_RE.fullmatch(book_id))   # match 會放過尾端換行（URL %0A）


def new_id() -> str:
    """產一個新的書 id（16 hex）。"""
    return secrets.token_hex(8)


def slugify_chapter(title: str, max_len: int = 40) -> str:
    """章名 → 檔名 slug：中文保留、去掉路徑字元、空白換成 `-`；空的回 `chapter`。"""
    s = _PATH_CHARS_RE.sub("", str(title or ""))
    s = _WS_RE.sub("-", s.strip())
    s = _DASHES_RE.sub("-", s).strip("-.")
    s = s[:max_len].strip("-.")
    return s or "chapter"


def chapter_filename(n: int, title: str) -> str:
    """`chNN-<slug>.md`。章號兩位數補零（超過 99 就自然變三位）。"""
    return f"ch{int(n):02d}-{slugify_chapter(title)}.md"


# ── 2. 全文切法 ──────────────────────────────────────────────
def page_texts(full_text: str) -> list:
    """把帶 `[[p.N]]` 標記的全文拆成 `[(頁碼, 該頁文字)]`。標記前的殘字（正常沒有）併進第 1 頁。"""
    text = full_text or ""
    marks = list(_PAGE_MARK_RE.finditer(text))
    if not marks:
        return [(1, text)] if text.strip() else []
    out = []
    lead = text[:marks[0].start()]
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end]
        if i == 0 and lead.strip():
            body = lead + body
        out.append((int(m.group(1)), body))
    return out


def page_index(full_text: str, head_chars: int = INDEX_HEAD_CHARS) -> list:
    """`[(頁碼, 該頁前 N 字)]`，給結構 pass 判章界用。換行壓成空白。"""
    rows = []
    for n, body in page_texts(full_text):
        head = " ".join(body.split())[:head_chars]
        rows.append((n, head))
    return rows


def split_chapters(full_text: str, boundaries: list) -> list:
    """照頁界把全文切成章：`[{n, title, start_page, end_page, text}]`。

    `boundaries` 是 `parse_structure` 回的形狀；超出全書頁數的頁界會被夾回範圍內，
    切不到任何文字的章照樣回（text 空字串），由呼叫端決定要不要跳過。
    """
    pages = dict(page_texts(full_text))
    out = []
    for ch in boundaries or []:
        a, b = int(ch.get("start_page") or 1), int(ch.get("end_page") or 0)
        text = "".join(pages.get(p, "") for p in range(a, max(a, b) + 1))
        out.append({"n": int(ch.get("n") or len(out) + 1), "title": str(ch.get("title") or ""),
                    "start_page": a, "end_page": max(a, b), "text": text})
    return out


def chunk_text(text: str, max_chars: int = CHAPTER_CHUNK_CHARS) -> list:
    """超過 `max_chars` 的文字切成幾段，盡量在段落（空行）、其次換行處切；都沒有就硬切。"""
    text = text or ""
    if max_chars <= 0:
        raise ValueError("max_chars 要大於 0")
    if len(text) <= max_chars:
        return [text] if text else []
    parts = []
    rest = text
    while len(rest) > max_chars:
        window = rest[:max_chars]
        cut = window.rfind("\n\n")
        if cut < max_chars // 2:
            cut = window.rfind("\n")
        if cut < max_chars // 2:
            cut = max_chars
        parts.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest:
        parts.append(rest)
    return parts


def fallback_boundaries(page_count: int, per: int = FALLBACK_PAGES_PER_CHAPTER) -> list:
    """結構 pass 抓不到章節時的退路：每 `per` 頁當一段，標題寫頁碼範圍。"""
    out = []
    n = 0
    for start in range(1, max(1, int(page_count)) + 1, max(1, per)):
        n += 1
        end = min(start + per - 1, int(page_count))
        out.append({"n": n, "title": f"第 {n} 部分（p.{start}–{end}）", "start_page": start, "end_page": end})
    return out


# ── 3. 提示 ──────────────────────────────────────────────────
_COMMON_RULES = """規矩：
- 全部用繁體中文（台灣用語）。
- 保留作者原本的用語與框架名稱；譯本裡看得出原文的，在括號附上原文（例：「安全邊際（margin of safety）」）。
- 忠於書的內容，不加書裡沒有的東西；書裡沒講的就寫「書中未提」。
- 不推薦任何具體金融商品、不預測行情。
- 不用 emoji。
- **直接在回覆裡輸出全部內容**：不要建檔、不要寫計畫、不要問「要不要繼續」——你回的文字就是成品。"""


def _book_line(meta: dict) -> str:
    title = (meta or {}).get("title") or (meta or {}).get("source_name") or "（未命名）"
    author = (meta or {}).get("author") or ""
    pages = (meta or {}).get("pages") or 0
    line = f"書名：{title}"
    if author:
        line += f"\n作者：{author}"
    if pages:
        line += f"\n頁數：{pages}"
    tags = normalize_tags((meta or {}).get("tags"))
    if tags:
        line += "\n標籤：" + "、".join(tags)
    return line


def structure_prompt(index_rows: list, first_chars: str, meta: Optional[dict] = None) -> str:
    """結構 pass：全書每頁前 80 字＋開頭幾千字 → 章清單（頁界）。只要 JSON。"""
    rows = "\n".join(f"p.{n}: {head}" for n, head in index_rows or [])
    return f"""你要幫一本書找出章節結構。下面是每一頁開頭的幾個字（p.N 是頁碼）與全書開頭的內容（目錄通常在裡面）。

{_book_line(meta or {})}

任務：列出這本書的章節（以「章」為單位；前言、序、附錄、致謝如果有實質內容也各算一章，版權頁、目錄頁不算）。
每章給起訖頁碼，章與章之間不重疊、不留空頁；最後一章的 end_page 是最後一頁。
標題照書上寫，不要自己改寫。

只輸出 JSON 陣列，不要 code fence、不要解釋：
[{{"n": 1, "title": "章名", "start_page": 1, "end_page": 12}}, ...]

{_COMMON_RULES}

===== 每頁開頭 =====
{rows}

===== 全書開頭 =====
{(first_chars or "")[:STRUCTURE_HEAD_CHARS]}
"""


def chapter_prompt(meta: dict, ch: dict, text: str, part_i: int = 1, part_n: int = 1,
                   assets: Optional[list] = None) -> str:
    """章節 pass：一章（或一章的一段）→ 固定七段的 markdown。

    `assets`＝這一章頁碼範圍內、從 PDF 抽出來的圖（owner 2026-09-18：「如果章節有重要圖片
    或表格 我希望你也可以截取出來」）。表格不走這裡 —— 它已經在 `text` 裡（見 `table_block`）。
    """
    part = f"（這是本章第 {part_i}/{part_n} 段，只整理這一段的內容；段落標題照樣用七段）" if part_n > 1 else ""
    return f"""你在把一本書的一章整理成之後每次討論、或顧問回答前要讀的筆記。讀的人會拿它對照自己的處境做決定，
所以要把作者的**規則與判斷方式**寫清楚，不是寫讀後感。

{_book_line(meta)}
章：第 {ch.get("n")} 章 {ch.get("title") or ""}（p.{ch.get("start_page")}–{ch.get("end_page")}）{part}

輸出 markdown，固定這七段（標題照這個字、順序不變；某段書裡真的沒有就寫「書中未提」）：
## 核心概念
## 框架
（作者提出的方法、步驟、公式、分類；保留原用語，譯本附原文）
## 心智模型
（作者看事情的方式：他假設什麼、在意什麼、用什麼比喻）
## 反模式
（作者說「不要這樣做」的事，以及為什麼）
## 實例
（書裡的例子，帶數字的把數字留下）
## 重點
（5–10 條，每條一句，可以直接引用）
## 關聯章節
（跟哪幾章有關、怎麼有關；不確定就寫「待補」）

篇幅：整章 1,500–3,000 字；引用原文的地方標頁碼（p.N）。不要重述整章，只留規則、判斷與例子。

{_COMMON_RULES}
{asset_lines(assets_in_range(assets, _int_or_none(ch.get("start_page")) or 0,
                             _int_or_none(ch.get("end_page")) or 10 ** 6))}

===== 章文 =====
{text}

（章文裡的 `[[表 p.N-k]]` 是從 PDF 抽出來的表格。用得到就照抄進「框架」或「實例」那一段，
標明是第幾頁的表；用不到就不要提。）
"""


def support_prompt(meta: dict, chapter_mds: list) -> str:
    """骨架 pass：所有章節筆記 → 一次產 SKILL.md／glossary.md／patterns.md／cheatsheet.md。"""
    toc = "\n".join(f"- 第 {c.get('n')} 章 {c.get('title') or ''}（`chapters/{c.get('file')}`）"
                    for c in chapter_mds or [])
    budget = max(1000, SUPPORT_INPUT_CHARS // max(1, len(chapter_mds or [])))
    body = "\n\n".join(f"===== chapters/{c.get('file')} =====\n{(c.get('md') or '')[:budget]}"
                       for c in chapter_mds or [])
    return f"""下面是一本書每一章的筆記。你要把它們收成四個檔，當作之後每次討論、或顧問回答前先讀的對照卡
（合計要小、要能直接拿來對照讀者自己的處境）。

{_book_line(meta)}

章節：
{toc}

四個檔的要求：
1. SKILL.md（**不超過 4,000 tokens**，約 2,500 中文字）：
   - 「核心心智模型」：作者最根本的三到五個看法，各一段。
   - 「決策規則」：寫成「當你 X 就 Y，因為 Z」的條列（對讀者說話），每條標出自哪一章。
   - 「章節索引表」：表格，欄位＝章／一句話說這章在講什麼／什麼問題該翻這章／檔名（用上面給的 `chapters/...` 相對路徑）。
   - 「主題索引」：讀者最可能帶著的問題（從這本書的主題裡挑）→ 該看哪幾章。
   - 「範圍與限制」：這本書不談什麼、哪些前提在台灣不成立（制度、市場、文化）。
2. glossary.md：作者的專有名詞，每條＝名詞（原文）：一句定義：出自哪章。
3. patterns.md：作者反覆出現的做法與反模式，各一小節：情境／作者的做法／為什麼／反例。
4. cheatsheet.md：**決策規則清單，不是名詞表**。每條都是「當你 X 就 Y，因為 Z」，帶章號；照「先看什麼、再看什麼」排順序，一頁看完。

輸出格式（四段都要有，順序不拘；分隔行要一模一樣）：
===== FILE: SKILL.md =====
（內容）
===== FILE: glossary.md =====
（內容）
===== FILE: patterns.md =====
（內容）
===== FILE: cheatsheet.md =====
（內容）

{_COMMON_RULES}

{body}
"""


def snapshot_lines(latest: Optional[dict]) -> list:
    """顧問快照的四個數 → 幾行字。讀不到的欄位就略過；整份 latest.json **不進提示**。

    形狀照 `D:\\Originsun-Advisor\\latest.json`（fetch_finance.py 存的）：
    `report.basis_date`、`report.report.totals.net_financial`／`cash_usable`、`fortress.runway.months`。
    舊形狀（`report.totals`）也認。
    """
    if not isinstance(latest, dict):
        return []
    rep = latest.get("report") if isinstance(latest.get("report"), dict) else {}
    inner = rep.get("report") if isinstance(rep.get("report"), dict) else rep
    totals = inner.get("totals") if isinstance(inner.get("totals"), dict) else {}
    fortress = latest.get("fortress") if isinstance(latest.get("fortress"), dict) else {}
    runway = fortress.get("runway") if isinstance(fortress.get("runway"), dict) else {}
    out = []
    basis = rep.get("basis_date") or inner.get("basis_date")
    if basis:
        out.append(f"數字日期：{basis}")
    net = totals.get("net_financial")
    if isinstance(net, (int, float)):
        out.append(f"淨值（現金＋證券−負債）：{_wan(net)}")
    cash = totals.get("cash_usable")
    if isinstance(cash, (int, float)):
        out.append(f"可動用現金：{_wan(cash)}")
    months = runway.get("months")
    if isinstance(months, (int, float)):
        out.append(f"可撐月數：{months:g} 個月")
    return out


def _wan(v) -> str:
    """金額 → 「N 萬」（一位小數、整數不留 .0）。同 core.fortress_logic._wan 的口徑。"""
    w = round(float(v) / 10000, 1)
    return f"{int(w)} 萬" if w == int(w) else f"{w} 萬"


_OWNER_LINE = "owner（王士源，1989 年生，台灣，影像製作公司負責人；他不是開發者）"

#: 財務書（meta.tags 含 FINANCE_TAG）：財務顧問角色，帶顧問快照的四個數
_CHAT_ROLE_FINANCE = f"""你讀過下面這本書。你是他的財務顧問，在跟 {_OWNER_LINE}討論這本書的原則怎麼用在他身上。

怎麼講：
- 全部用繁體中文（台灣用語）；講白話、給做法；金額以「萬」為單位；不用術語，要用就先解釋。
- 先回答他問的，再補書裡相關的原則，並說出自哪一章（章名＋頁碼）。要看細節就 Read `chapters/` 裡那一章的檔（相對路徑在下面的章節索引）。
- 他的數字（下面的快照）是題目，書是框架：先看數字再套書。
- 他說到的原則若跟他系統裡的規則（私帳月報、堡壘五層、登記餘額）打架，**兩邊都講**、說你偏向哪邊、為什麼。
- 「他的結論」是他已經認同過的原則，優先於書；「他的筆記」次之。
- 不推薦具體金融商品、不預測行情；不確定的事直說不知道。
- 不用 emoji。回覆是 markdown 純文字（不是 JSON）。
- 回覆結尾若有可以存成結論的原則（一句可執行的話、標出自哪章），用「可存成結論：」開頭獨立一行列出，一行一條；沒有就不要寫這行。"""

#: 其他書：讀過這本書的討論夥伴，**不帶**他的財務數字
_CHAT_ROLE_GENERAL = f"""你讀過下面這本書。你在跟 {_OWNER_LINE}討論這本書的原則怎麼用在他身上。

怎麼講：
- 全部用繁體中文（台灣用語）；講白話、給做法；不用術語，要用就先解釋。
- 先回答他問的，再補書裡相關的原則，並說出自哪一章（章名＋頁碼）。要看細節就 Read `chapters/` 裡那一章的檔（相對路徑在下面的章節索引）。
- 他的處境（影像製作公司、台灣、他在對話裡說的事）是題目，書是框架：先看他的處境再套書。
- 「他的結論」是他已經認同過的原則，優先於書；「他的筆記」次之。
- 不推薦具體金融商品、不預測行情；不確定的事直說不知道。
- 不用 emoji。回覆是 markdown 純文字（不是 JSON）。
- 回覆結尾若有可以存成結論的原則（一句可執行的話、標出自哪章），用「可存成結論：」開頭獨立一行列出，一行一條；沒有就不要寫這行。"""


def chat_prompt(meta: dict, *, skill: str = "", cheatsheet: str = "", chapters: Optional[list] = None,
                notes: str = "", conclusion: str = "", snapshot: Optional[list] = None,
                history: Optional[list] = None, text: str = "", finance: bool = False,
                extend: str = "") -> str:
    """討論提示：角色框架＋骨架＋章節索引＋筆記＋結論＋最近對話＋這一則。

    `finance=True`（meta.tags 含 FINANCE_TAG）＝財務顧問角色＋帶顧問快照的四個數；
    False＝一般「讀過這本書的討論夥伴」，**不帶任何數字**（給了 `snapshot` 也丟掉）。
    """
    toc = "\n".join(f"- 第 {c.get('n')} 章 {c.get('title') or ''}：`chapters/{c.get('file')}`"
                    for c in chapters or []) or "（還沒編譯章節）"
    hist = "\n".join(f"{'owner' if m.get('role') == ROLE_USER else '你'}：{m.get('text') or ''}"
                     for m in recent_chat(history or [], RECENT_CHAT_N)) or "（這是第一則）"
    if finance:
        role = _CHAT_ROLE_FINANCE
        snap_block = "===== 他現在的數字（顧問快照）=====\n" + ("\n".join(snapshot or []) or "（讀不到顧問快照）") + "\n\n"
    else:
        role = _CHAT_ROLE_GENERAL
        snap_block = ""
    return f"""{role}

===== 這本書 =====
{_book_line(meta)}

===== 骨架（SKILL.md）=====
{skill or "（還沒編譯）"}

===== 決策規則（cheatsheet.md）=====
{cheatsheet or "（還沒編譯）"}

===== 章節索引（要看細節就 Read 這些檔）=====
{toc}

===== 他的結論（優先於書）=====
{conclusion or "（還沒有）"}

===== 他的筆記 =====
{notes or "（還沒有）"}

{(extend + chr(10) + chr(10)) if extend.strip() else ""}{snap_block}===== 最近的對話 =====
{hist}

===== 他這一則 =====
{text}
"""


def conclude_prompt(meta: dict, chat: list, existing: str = "") -> str:
    """整理結論：整段討論＋既有結論 → 3–7 條、每條一句可執行的原則、標出自哪章。"""
    hist = "\n".join(f"{'owner' if m.get('role') == ROLE_USER else '你'}：{m.get('text') or ''}"
                     for m in chat or []) or "（沒有對話）"
    return f"""下面是 owner（王士源）跟你討論一本書的整段對話。把討論裡**他認同了的**原則收成結論。

{_book_line(meta)}

要求：
- 3–7 條；每條一句、可執行（有動作、有條件或數字）、標出自哪一章（章名）。
- 只收他認同或自己說出來的，他反對的不收；跟「既有結論」重複的不再收，衝突的寫成「修正：…」。
- 純 markdown 條列（`- ` 開頭），不要標題、不要開場白、不要 code fence。

{_COMMON_RULES}

===== 既有結論 =====
{existing or "（還沒有）"}

===== 對話 =====
{hist}
"""


# ── 4. 回覆解析 ──────────────────────────────────────────────
def parse_structure(text: str, page_count: int = 0) -> list:
    """claude 回的章清單 → `[{n, title, start_page, end_page}]`。

    容錯：抓文字裡第一個 JSON 陣列（前後有解釋文字、code fence 都無所謂）；
    頁碼夾回 1..page_count（page_count 給 0 就不夾）；end 空的用下一章的 start−1 補；
    照 start_page 排序、重新編號。解不出來或一章都沒有就回空清單（呼叫端走退路）。
    """
    raw = _first_json_array(text or "")
    if not isinstance(raw, list):
        return []
    rows = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        start = _int_or_none(item.get("start_page"))
        if start is None or start < 1:
            continue
        rows.append({"title": title, "start_page": start, "end_page": _int_or_none(item.get("end_page"))})
    rows.sort(key=lambda r: r["start_page"])
    out = []
    for i, r in enumerate(rows):
        end = r["end_page"]
        nxt = rows[i + 1]["start_page"] - 1 if i + 1 < len(rows) else (page_count or end or r["start_page"])
        if end is None or end < r["start_page"]:
            end = nxt
        if i + 1 < len(rows):
            end = min(end, nxt)          # claude 回的 end 跨進下一章 → 夾回；同一頁不餵兩章
        if page_count:
            end = min(end, page_count)
            if r["start_page"] > page_count:
                continue
        out.append({"n": len(out) + 1, "title": r["title"] or f"第 {len(out) + 1} 章",
                    "start_page": r["start_page"], "end_page": max(r["start_page"], end)})
    return out


def _first_json_array(text: str):
    i = text.find("[")
    while i != -1:
        depth = 0
        in_str = False
        esc = False
        for j in range(i, len(text)):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i:j + 1])
                    except ValueError:
                        break
        i = text.find("[", i + 1)
    return None


def _int_or_none(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def split_files(text: str) -> dict:
    """`===== FILE: <name> =====` 分段 → `{name: content}`。名字**只准白名單**（SUPPORT_FILES），其餘丟掉。

    claude 回的檔名可能帶路徑（`./SKILL.md`）或大小寫不同 —— 取 basename 後**逐字**比對，
    比不上就不收：這是唯一會照它回的名字寫檔的地方，不能寬鬆。
    """
    out = {}
    marks = list(_FILE_SEP_RE.finditer(text or ""))
    for i, m in enumerate(marks):
        name = m.group(1).strip().replace("\\", "/").rsplit("/", 1)[-1]
        if name not in SUPPORT_FILES:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        content = text[m.end():end].strip("\n")
        if content.strip():
            out[name] = content.rstrip() + "\n"
    return out


# ── 5. 標籤 ──────────────────────────────────────────────────
def normalize_tags(tags: Any) -> list:
    """標籤正規化：只收字串、去頭尾空白、空的丟掉、每個截到 TAG_MAX_LEN 字、去重（保序）、最多 TAGS_MAX 個。
    不是 list 就當沒有（meta 舊檔沒這個鍵）。"""
    if not isinstance(tags, (list, tuple)):
        return []
    out = []
    for t in tags:
        if not isinstance(t, str):
            continue
        s = " ".join(t.split())[:TAG_MAX_LEN].strip()
        if s and s not in out:
            out.append(s)
        if len(out) >= TAGS_MAX:
            break
    return out


def has_tag(meta: dict, tag: str) -> bool:
    """meta.tags 有沒有這個標籤（走 normalize_tags，所以「 財務 」也算）。"""
    return tag in normalize_tags((meta or {}).get("tags"))


# ── 結論與對話的形狀 ─────────────────────────────────────────
def append_section(existing: str, text: str, when: str) -> str:
    """結論追加：`## <when>` 一段接在既有內容後面（段與段之間空一行；既有內容沒換行結尾就補）。"""
    body = (text or "").strip()
    if not body:
        return existing or ""
    head = (existing or "").rstrip()
    block = f"## {when}\n\n{body}\n"
    return (head + "\n\n" + block) if head else block


def chat_message(role: str, text: str, at: str) -> dict:
    """對話裡的一則（形狀同報價助理／公布欄：role / text / at）。"""
    return {"role": role, "text": text, "at": at}


def recent_chat(chat: list, n: int = RECENT_CHAT_N) -> list:
    """最近 n 則（只留 user／ai 兩種角色）。"""
    rows = [m for m in (chat or []) if isinstance(m, dict) and m.get("role") in (ROLE_USER, ROLE_AI)]
    return rows[-n:] if n > 0 else []


def conclusion_lines(reply: str) -> list:
    """從 AI 回覆裡撈「可存成結論：」之後的條目（前端「存成結論」預設帶這幾行）。沒有就回空。"""
    text = reply or ""
    i = text.find(CONCLUSION_MARK)
    if i == -1:
        return []
    tail = text[i + len(CONCLUSION_MARK):]
    out = []
    for ln in tail.splitlines():
        s = ln.strip().lstrip("-*•").strip()
        if s:
            out.append(s)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 研究助理（docs/KNOWLEDGE_BASE_PLAN.md §9）—— 定期依這本書的主題找網路上的研究
# owner 2026-09-18：「不限制中文，如果是英文或其他語言也可以，附上出處、翻譯與摘要給我」
#
# 🔴 這一段產出的每個欄位都來自**網頁**（不可信輸入，會有 prompt injection）。
#    規則：只當資料寫進 `延伸.md` 與回給前端，永遠不執行、不寫進設定、不觸發任何動作。
# ══════════════════════════════════════════════════════════════════════════

#: 一次最多收幾則（§9.2：一本一週一次、一次 ≤ 8 則）
EXTEND_MAX_ITEMS = 8
#: 評分的三個值（空字串＝還沒評）
EXTEND_RATINGS = ("", "useful", "useless")
#: `延伸.md` 的欄位分隔符。內容裡出現同一個字元時寫入前換成半形，讀回來才不會被切錯。
EXTEND_SEP = "｜"
#: 每行的欄位順序。第一欄 `n` 是**這本書裡的流水號**（只增不重用）——評分的端點靠它認人，
#: 用陣列位置的話刪掉一則後面全部錯位。
EXTEND_FIELDS = ("n", "date", "title_zh", "url", "title_original", "lang", "source",
                 "published", "summary_zh", "chapter_guess", "why_it_matters", "rating")
#: 給提示看的「他的情境」——研究要貼著這個人，不是泛泛的主題搜尋
EXTEND_READER = "台灣、影像製作公司負責人、自己記一本私帳"
#: 「研究方向」一本一句話（他自己寫的，權重排在結論之前）。一兩句就夠，太長會蓋掉書本身。
FOCUS_MAX = 300
#: 評過的例子各帶幾則進提示（太多會把提示灌滿，也沒必要）
EXTEND_EXAMPLES = 8
#: 只找最近多久的東西（太舊的書裡通常就有了）
EXTEND_RECENT_DAYS = 30


def _esc_cell(v) -> str:
    """一格的內容：壓成單行、把分隔符換成半形，避免把一行切壞。"""
    return " ".join(str(v or "").split()).replace(EXTEND_SEP, "|")


def extend_line(item: dict) -> str:
    """一則 → `延伸.md` 的一行（機器寫、人也讀得懂）。"""
    return "- " + EXTEND_SEP.join(_esc_cell(item.get(k)) for k in EXTEND_FIELDS)


def parse_extend_md(md: str) -> list:
    """`延伸.md` → `[{…}]`。欄位數不對的行直接跳過（人手改壞一行不該讓整頁掛掉）。"""
    out = []
    for ln in str(md or "").splitlines():
        ln = ln.strip()
        if not ln.startswith("- "):
            continue
        cells = ln[2:].split(EXTEND_SEP)
        if len(cells) != len(EXTEND_FIELDS):
            continue
        item = {k: cells[i].strip() for i, k in enumerate(EXTEND_FIELDS)}
        n = _int_or_none(item.get("n"))
        if not item.get("url") or not n or n < 1:
            continue
        item["n"] = n
        if item.get("rating") not in EXTEND_RATINGS:
            item["rating"] = ""
        out.append(item)
    return out


def next_extend_n(items: list) -> int:
    """下一個流水號：現有最大值 +1（刪過的號碼不重用）。"""
    return max([_int_or_none(i.get("n")) or 0 for i in (items or [])] or [0]) + 1


def norm_url(url: str) -> str:
    """去重用的正規化：小寫網域、去掉 #片段 與常見的追蹤參數、去掉結尾斜線。"""
    u = " ".join(str(url or "").split())
    if not u:
        return ""
    u = u.split("#", 1)[0]
    if "?" in u:
        base, q = u.split("?", 1)
        keep = [kv for kv in q.split("&")
                if kv and not kv.split("=", 1)[0].lower().startswith(("utm_", "fbclid", "gclid", "spm"))]
        u = base + ("?" + "&".join(keep) if keep else "")
    if "://" in u:
        scheme, rest = u.split("://", 1)
        host, _, path = rest.partition("/")
        u = scheme.lower() + "://" + host.lower() + ("/" + path if path else "")
    return u.rstrip("/")


_EXTEND_SHAPE = (
    '{"url":"", "title_original":"原文標題", "title_zh":"中譯標題", "lang":"原文語言（zh-TW／en／ja…）", '
    '"source":"出處（媒體或機構名）", "published":"YYYY-MM-DD 或空", '
    '"summary_zh":"繁體中文三到五句的摘要", '
    '"why_it_matters":"一句話說為什麼值得他看（要扣住他的原則或情境）", '
    '"chapter_guess":"最相關的章（沒把握就空）"}'
)

_EXTEND_RULES = """- `summary_zh` 與 `title_zh` 一律**繁體中文**（台灣用語）。不是中文的原文也照翻，不要只給原文。
- `title_original` 保留原文標題原樣（他要對得回去）。
- 只給你**真的打開看過**的網址；不要生成看起來像的連結。
- 不要付費牆後面的內容、不要只有影片沒有文字的東西。
- 摘要用自己的話寫，不要整段照抄原文。
- 不推薦任何具體金融商品、不預測行情。
- 不用 emoji。"""


def normalize_focus(text: Any) -> str:
    """「研究方向」：去頭尾空白、壓掉連續空行、切到 `FOCUS_MAX`。空字串＝沒設定。"""
    lines = [ln.rstrip() for ln in str(text or "").strip().splitlines()]
    out = []
    for ln in lines:
        if not ln and (not out or not out[-1]):
            continue
        out.append(ln)
    return "\n".join(out).strip()[:FOCUS_MAX]


def _rated_examples(items: Optional[list]) -> list:
    """他評過的那幾則 → 提示裡的正反例。

    2026-09-18 之前「有用／沒用」只決定討論要不要帶那一則，**下一次搜尋完全看不到** ——
    同一類東西還是會再找回來（只是換個網址）。標題比網址更能講清楚他要什麼。
    """
    good = [i for i in (items or []) if i.get("rating") == "useful"][-EXTEND_EXAMPLES:]
    bad = [i for i in (items or []) if i.get("rating") == "useless"][-EXTEND_EXAMPLES:]
    if not good and not bad:
        return []
    out = ["", "## 他看過之後的回饋（照這個調整你要找什麼）"]
    if good:
        out += ["他覺得**有用**的（多找這種）："] + [
            "- {}｜{}".format(i.get("title_zh", ""), i.get("source", "")) for i in good]
    if bad:
        out += ["他覺得**沒用**的（不要再找這種）："] + [
            "- {}｜{}".format(i.get("title_zh", ""), i.get("source", "")) for i in bad]
    return out


def extend_prompt(meta: dict, *, skill: str = "", conclusion: str = "", notes: str = "",
                  focus: str = "", rated: Optional[list] = None,
                  seen_urls: Optional[list] = None, max_items: int = EXTEND_MAX_ITEMS) -> str:
    """定期研究那一發的提示。主題不由程式 parse —— 原文一起餵，讓它自己出查詢。

    權重由高到低：`focus`（他自己寫的一句話，最直接）＞ `結論`（他認同過的原則）
    ＞ 骨架的核心框架 ＞ 標籤；再加上他對前幾輪的評分當正反例。
    """
    parts = [
        f"你是這個人的研究助理。他讀了下面這本書，你要去網路上找**最近 {EXTEND_RECENT_DAYS} 天內**值得他看的新資料。",
        "",
        _book_line(meta),
        f"他的情境：{EXTEND_READER}。",
    ]
    if focus.strip():
        parts += ["", "## 他指定的研究方向（**最優先**，跟下面衝突時以這段為準）", focus.strip()[:FOCUS_MAX]]
    if conclusion.strip():
        parts += ["", "## 他已經認同的原則（最重要，研究要貼著這些走）", conclusion.strip()[:3000]]
    if notes.strip():
        parts += ["", "## 他自己的筆記", notes.strip()[:1500]]
    if skill.strip():
        parts += ["", "## 這本書的骨架（核心心智模型與決策規則）", skill.strip()[:4000]]
    parts += [
        "",
        "## 要找什麼",
        "先自己想 3 到 5 個查詢，再去搜。優先序：",
        "1. 同行評審論文、工作報告（working paper）、官方統計與研究機構報告",
        "2. 作者本人的新文章、訪談、有文字稿的 podcast",
        "3. 對這本書框架的實際應用，或有根據的批評",
        "4. 跟他情境相關的討論",
        "",
        "**語言不限**：繁中、英文、日文或其他都可以。同一個主題用不同語言各搜一輪 —— "
        "好東西常常只有英文有，不要因為是外文就跳過。",
        "",
        "## 怎麼回",
        f"回一個 JSON 陣列，最多 {max(1, int(max_items))} 則，每則：",
        _EXTEND_SHAPE,
        "",
        "規矩：",
        _EXTEND_RULES,
    ]
    parts += _rated_examples(rated)
    seen = [u for u in (norm_url(x) for x in (seen_urls or [])) if u]
    if seen:
        parts += ["", "## 已經收錄過的，不要再回（共 %d 筆）" % len(seen)] + ["- " + u for u in seen[:200]]
    parts += ["", "找不到就回 `[]`。**不要為了湊數把不相關或舊的東西放進來。**",
              "只輸出 JSON 陣列，前後不要別的字。"]
    return "\n".join(parts)


def extend_one_prompt(url: str, meta: dict, *, conclusion: str = "") -> str:
    """手動「收錄這篇」：他貼一個網址，打開來整理成同一個形狀（§9.2 第一條路）。"""
    parts = [
        "打開下面這個網址，讀完它，整理成一則筆記給這本書的讀者。",
        "",
        "網址：" + " ".join(str(url or "").split()),
        _book_line(meta),
        f"他的情境：{EXTEND_READER}。",
    ]
    if conclusion.strip():
        parts += ["", "## 他已經認同的原則（判斷「為什麼值得他看」時扣住這些）", conclusion.strip()[:2000]]
    parts += [
        "",
        "回**一個** JSON 物件（不是陣列，形狀跟定期研究那批一樣）：",
        _EXTEND_SHAPE,
        "",
        "規矩：",
        _EXTEND_RULES,
        "",
        '打不開或是要訂閱才看得到，就回 `{"error":"打不開或需要訂閱"}`。只輸出 JSON，前後不要別的字。',
    ]
    return "\n".join(parts)


def _first_json_object(text: str):
    """跟 `_first_json_array` 同款，但抓第一個平衡的 `{}`。"""
    i = text.find("{")
    while i != -1:
        depth = 0
        in_str = False
        esc = False
        for j in range(i, len(text)):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i:j + 1])
                    except ValueError:
                        break
        i = text.find("{", i + 1)
    return None


def _one_extend(raw, today: str, n: int) -> Optional[dict]:
    """一則的正規化。網址是必要的；兩個標題至少要有一個。"""
    if not isinstance(raw, dict):
        return None
    url = norm_url(raw.get("url"))
    if not url.startswith(("http://", "https://")):
        return None
    zh = _esc_cell(raw.get("title_zh"))
    orig = _esc_cell(raw.get("title_original"))
    if not zh and not orig:
        return None
    return {
        "n": int(n),
        "date": _esc_cell(today),
        "url": url,
        "title_zh": zh or orig,
        "title_original": orig or zh,
        "lang": _esc_cell(raw.get("lang")) or "?",
        "source": _esc_cell(raw.get("source")),
        "published": _esc_cell(raw.get("published")),
        "summary_zh": _esc_cell(raw.get("summary_zh")),
        "why_it_matters": _esc_cell(raw.get("why_it_matters")),
        "chapter_guess": _esc_cell(raw.get("chapter_guess")),
        "rating": "",
    }


def parse_extend(text: str, today: str, *, start_n: int = 1, seen_urls: Optional[list] = None,
                 max_items: int = EXTEND_MAX_ITEMS) -> list:
    """研究那一發的回覆 → 乾淨的清單，流水號從 `start_n` 接著發。

    壞的、重複的、已收錄過的都丟掉；超過上限就截斷。
    """
    arr = _first_json_array(text)
    if not isinstance(arr, list):
        return []
    seen = {u for u in (norm_url(x) for x in (seen_urls or [])) if u}
    out = []
    for raw in arr:
        item = _one_extend(raw, today, int(start_n) + len(out))
        if not item or item["url"] in seen:
            continue
        seen.add(item["url"])
        out.append(item)
        if len(out) >= max(1, int(max_items)):
            break
    return out


def parse_extend_one(text: str, today: str, *, n: int = 1) -> tuple:
    """手動收錄那一發 → `(item 或 None, 錯誤字串)`。"""
    obj = _first_json_object(text)
    if not isinstance(obj, dict):
        return None, "看不懂回覆"
    if obj.get("error"):
        return None, _esc_cell(obj["error"])[:80]
    item = _one_extend(obj, today, n)
    return (item, "") if item else (None, "回覆缺少網址或標題")


def extend_digest(items: list, *, limit: int = 20) -> str:
    """討論提示要帶的那一段：只帶「有用」與「還沒評」的最近幾則（評成沒用的不再出現）。"""
    keep = [i for i in (items or []) if i.get("rating") != "useless"][-max(1, int(limit)):]
    if not keep:
        return ""
    lines = ["## 這本書的延伸（後來網路上出現的東西；**不是作者說的**，引用時要講清楚是誰說的）"]
    for i in keep:
        lang = i.get("lang") or ""
        tail = f"（{lang}）" if lang and not lang.startswith("zh") else ""
        lines.append("- {}{}｜{}｜{}".format(i.get("title_zh", ""), tail, i.get("source", ""),
                                            i.get("summary_zh", "")))
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# 研究週報／月報（owner 2026-09-18：「我想要有個週報或月報」）
# 報告本身寫成 md 檔（「這些生產的資料都用 md 檔整理起來」），Discord 推摘要。
# 這裡只組字，不碰檔案也不發通知（I/O 在 services/knowledge_report.py）。
# ══════════════════════════════════════════════════════════════════════════

#: Discord 一則的長度上限是 2000 字元；留點餘裕，超過就收尾說「完整的在報告裡」
PUSH_MAX_CHARS = 1900
#: 推播摘要每本書最多列幾則（其餘只講數量）
PUSH_PER_BOOK = 3


def week_span(monday: date) -> tuple:
    """那一週的 `(週一, 週日)`（兩端都含）。"""
    return monday, monday + timedelta(days=6)


def last_week(today: date) -> tuple:
    """上一個完整的一週：`(標籤, 週一, 週日)`。週一早上跑的話講的就是剛過完的那週。"""
    monday = today - timedelta(days=today.weekday() + 7)
    start, end = week_span(monday)
    y, w, _ = start.isocalendar()
    return f"{y}-W{w:02d}", start, end


def last_month(today: date) -> tuple:
    """上一個完整的月：`(標籤, 一號, 月底)`。"""
    first_this = today.replace(day=1)
    end = first_this - timedelta(days=1)
    start = date(end.year, end.month, 1)
    return f"{end.year}-{end.month:02d}", start, end


def in_span(item: dict, start: date, end: date) -> bool:
    """這一則是不是落在區間內（`date` 是收錄日，不是原文發表日）。壞日期一律不算。"""
    try:
        y, m, d = (int(x) for x in str(item.get("date") or "").split("-"))
        return start <= date(y, m, d) <= end
    except (TypeError, ValueError):
        return False


def _item_lines(it: dict) -> list:
    """一則在 md 裡長這樣：標題（連結）＋出處那行＋摘要＋為什麼值得看。"""
    lang = it.get("lang") or ""
    tail = f"（{lang}）" if lang and not lang.startswith("zh") else ""
    head = f"### [{it.get('title_zh') or '（無標題）'}]({it.get('url', '')}){tail}"
    meta = "｜".join(x for x in (it.get("source"), it.get("published"), it.get("chapter_guess")) if x)
    out = [head]
    if tail and it.get("title_original"):
        out.append(f"原文標題：{it['title_original']}")
    if meta:
        out.append(meta)
    if it.get("summary_zh"):
        out += ["", it["summary_zh"]]
    if it.get("why_it_matters"):
        out += ["", f"**為什麼值得看**：{it['why_it_matters']}"]
    return out + [""]


def _totals(groups: list) -> tuple:
    """`(總則數, 還沒評的則數)`。`groups` 是 `[(書名, [則])]`。"""
    items = [i for _, rows in groups for i in rows]
    return len(items), sum(1 for i in items if not i.get("rating"))


def weekly_report_md(label: str, start, end, groups: list) -> str:
    """一週的完整報告（md 檔的內容）。`groups` 照書分組，空的書不會進來。"""
    n, unrated = _totals(groups)
    out = [f"# 研究週報 {label}", "",
           f"{start:%Y-%m-%d} 到 {end:%Y-%m-%d}｜{len(groups)} 本書｜{n} 則"]
    if unrated:
        out.append(f"其中 {unrated} 則還沒評過「有用／沒用」—— 評過的會影響下一輪要找什麼。")
    out += ["", "> 這些是網路上的東西，**不是書的作者說的**。引用時要講清楚是誰說的。", ""]
    for title, rows in groups:
        out += [f"## {title}（{len(rows)} 則）", ""]
        for it in rows:
            out += _item_lines(it)
    return "\n".join(out).rstrip() + "\n"


def _count_by(items: list, key: str, top: int = 5) -> list:
    """`[(值, 幾則)]`，多的排前面；空值不算。"""
    tally = {}
    for i in items:
        v = (i.get(key) or "").strip()
        if v:
            tally[v] = tally.get(v, 0) + 1
    return sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[:top]


def monthly_report_md(label: str, start, end, groups: list) -> str:
    """一個月的總結：數字、你覺得有用的那幾則、還沒評的有多少、來源排行。"""
    items = [i for _, rows in groups for i in rows]
    good = [i for i in items if i.get("rating") == "useful"]
    bad = [i for i in items if i.get("rating") == "useless"]
    unrated = [i for i in items if not i.get("rating")]
    out = [f"# 研究月報 {label}", "",
           f"{start:%Y-%m-%d} 到 {end:%Y-%m-%d}｜{len(groups)} 本書｜{len(items)} 則", "",
           f"- 你覺得有用：{len(good)} 則",
           f"- 你覺得沒用：{len(bad)} 則",
           f"- 還沒評：{len(unrated)} 則"]
    srcs = _count_by(items, "source")
    if srcs:
        out += ["", "**這個月的來源**：" + "、".join(f"{k}（{v}）" for k, v in srcs)]
    langs = _count_by(items, "lang", top=6)
    if langs:
        out.append("**語言**：" + "、".join(f"{k}（{v}）" for k, v in langs))
    if good:
        out += ["", "## 你覺得有用的", ""]
        for it in good:
            out += _item_lines(it)
    if unrated:
        out += ["", f"## 還沒評的（{len(unrated)} 則）", "",
                "評過之後，下一輪就會照這個調整要找什麼。", ""]
        for it in unrated:
            out.append(f"- [{it.get('title_zh') or '（無標題）'}]({it.get('url', '')})"
                       f"｜{it.get('source', '')}")
    if bad:
        out += ["", f"## 你刷掉的（{len(bad)} 則，之後不會再找這類）", ""]
        for it in bad:
            out.append(f"- {it.get('title_zh') or ''}｜{it.get('source', '')}")
    return "\n".join(out).rstrip() + "\n"


def report_push_text(title: str, groups: list, *, tail: str = "",
                     per_book: int = PUSH_PER_BOOK, max_chars: int = PUSH_MAX_CHARS) -> str:
    """推到 Discord 的那一則：他在手機上看，所以連結要直接放進來（點得到）。

    太長就收尾說剩下幾則在報告檔裡 —— Discord 超過 2000 字元會整則被退掉，不是截斷。
    """
    n, unrated = _totals(groups)
    lines = [title, f"{len(groups)} 本書、{n} 則。"]
    if unrated:
        lines.append(f"{unrated} 則還沒評過有用或沒用。")
    lines.append("")
    left = 0
    for book, rows in groups:
        lines.append(f"[{book}]")
        for it in rows[:per_book]:
            lines.append(f"・{it.get('title_zh') or ''}｜{it.get('source', '')}")
            if it.get("url"):
                lines.append(f"  {it['url']}")
        if len(rows) > per_book:
            left += len(rows) - per_book
            lines.append(f"  （這本還有 {len(rows) - per_book} 則）")
        lines.append("")
    if left:
        lines.append(f"沒列出來的 {left} 則在完整報告裡。")
    text = "\n".join(lines).rstrip()
    foot = ("\n" + tail) if tail else ""
    if len(text) + len(foot) <= max_chars:
        return text + foot
    # 🔴 先切本文、最後才接 tail（報告連結）：截斷的那一則正是唯一會說「完整的在報告檔裡」的，
    # 連結接在切斷點之後就一起被切掉（2026-09-19 /polish BUG-5）。
    suffix = "\n…（太長了，完整的在報告檔裡）"
    room = max(0, max_chars - len(suffix) - len(foot))
    cut = text[:room].rsplit("\n", 1)[0]
    return cut + suffix + foot


# ══════════════════════════════════════════════════════════════════════════
# 章節裡的圖與表（owner 2026-09-18：「如果章節有重要圖片 或表格 我希望你也可以截取出來」）
#
# 圖：上傳時從 PDF 抽出來存成檔（`assets/p042-1.jpg`），章節提示只告訴 claude
#     「第幾頁有一張圖、旁邊的字是什麼」，由它決定要不要在章節 md 裡引用。
#     🔴 claude **看不到圖**，所以它寫的說明只能根據頁碼與附近的文字 —— 提示裡要講明這件事，
#     不然它會開始描述一張它沒看過的圖。
# 表：抽成 markdown **直接塞進全文**，這樣切章時自動跟著走，claude 看得到內容也引用得到。
# ══════════════════════════════════════════════════════════════════════════

#: 圖檔名：`p042-1.jpg`。頁碼三位以上、同頁第幾張、副檔名白名單。
_ASSET_RE = re.compile(r"^p(\d{3,5})-(\d{1,2})\.(png|jpg)$")
#: 比這個小的當裝飾（頁首小圖示、線條），不留。實測那本書的 40x40 就是這種。
MIN_ASSET_PX = 200
#: 一本書最多留幾張（PDF 有時整頁都是掃描圖，那樣留下來沒有意義也很佔空間）
MAX_ASSETS = 80
#: 一頁最多留幾張
MAX_ASSETS_PER_PAGE = 4
#: 圖說：取圖下方這麼多字當線索（通常是「圖 1-1 …」那一行）
CAPTION_CHARS = 60
#: 這一頁的本文少於這麼多字，就當它是封面或整頁掃描 —— 那種圖沒有上下文，claude 也無從說明
MIN_PAGE_CHARS = 80
#: 圖佔掉這麼多版面就是整頁的底圖／掃描，不是插圖
MAX_PAGE_COVER = 0.9
#: 表格小於這個規模就不算表（多半是排版用的格線）
MIN_TABLE_ROWS = 2
MIN_TABLE_COLS = 2


def is_valid_asset(name) -> bool:
    """🔴 任何拼路徑前先過這裡（同書 id 的規矩）。"""
    return bool(_ASSET_RE.match(str(name or "")))


def asset_name(page: int, k: int, ext: str) -> str:
    """`(42, 1, 'jpeg')` → `p042-1.jpg`。副檔名不在白名單就回空字串（呼叫端跳過那張）。

    產出的名字自己也要過 `is_valid_asset`：k 是「這一頁所有內嵌圖」的列舉序號，第 100 張起
    會拼出 `p001-100.jpg` 這種白名單不認的名字 —— 檔寫進 assets/ 但永遠列不出來、也清不掉
    （2026-09-19 /polish BUG-12）。
    """
    e = {"jpeg": "jpg", "jpg": "jpg", "png": "png"}.get(str(ext or "").lower(), "")
    name = f"p{int(page):03d}-{int(k)}.{e}" if e else ""
    return name if is_valid_asset(name) else ""


def asset_page(name: str) -> int:
    """`p042-1.jpg` → 42；不合白名單回 0。"""
    m = _ASSET_RE.match(str(name or ""))
    return int(m.group(1)) if m else 0


def keep_image(width: int, height: int) -> bool:
    """留不留這張：兩邊都要夠大。頁首小圖示、分隔線、色塊一律丟掉。"""
    return int(width or 0) >= MIN_ASSET_PX and int(height or 0) >= MIN_ASSET_PX


def keep_page_image(page_chars: int, cover: float) -> bool:
    """這一頁的這張圖算不算「重要的圖」。

    實測（「台灣菜」503 頁）：封面與整頁掃描那幾頁本文是 0 字、圖佔 52%～100%，
    而真正的插圖都在有本文的頁面上、佔兩成版面，而且抓得到「圖2.1 …」那種圖說。
    """
    return int(page_chars or 0) >= MIN_PAGE_CHARS and float(cover or 0) < MAX_PAGE_COVER


def table_block(page: int, k: int, md: str) -> str:
    """表格塞進全文時的樣子。標題那行讓 claude 知道它是第幾頁的第幾個表。"""
    body = (md or "").strip()
    return f"\n[[表 p.{int(page)}-{int(k)}]]\n{body}\n" if body else ""


def keep_table(rows: int, cols: int) -> bool:
    """表格夠大才算表（小於 MIN_TABLE_ROWS×MIN_TABLE_COLS 多半是排版格線）。"""
    return int(rows or 0) >= MIN_TABLE_ROWS and int(cols or 0) >= MIN_TABLE_COLS


def assets_in_range(assets: Optional[list], start_page: int, end_page: int) -> list:
    """這一章頁碼範圍內的圖。"""
    return [a for a in (assets or [])
            if start_page <= int(a.get("page") or 0) <= end_page]


#: 章節 md 最後那一段圖說的標題（產完會被取走，不留在文章裡）
CAPTION_HEADING = "圖說"


def parse_captions(md: str, known: Optional[list] = None) -> tuple:
    """把章節 md 裡的 `## 圖說` 段取走：回 `(拿掉那幾段的 md, {檔名: 圖說})`。

    `known` 給了就只認在清單裡的檔名（claude 亂掰一個檔名不該進 meta）。

    🔴 一章切成好幾段時，**每一段**末尾都有自己的 `## 圖說`（提示對每段都這樣要求），
    合併後的 md 裡就有好幾段。每一段從標題起算、到下一個 `---`（段與段的分隔）或下一個
    標題為止；只認第一段並把後面整個切掉的話，第二段起的章文會跟著消失
    （2026-09-19 /polish BUG-1：一章 > 60,000 字的厚書實際只剩第一段，而且沒有任何徵兆）。
    """
    text = str(md or "")
    heading = re.compile(r"^##\s*" + re.escape(CAPTION_HEADING) + r"\s*$", re.M)
    if not heading.search(text):
        return text, {}
    allow = {str(n) for n in (known or [])}
    caps = {}
    kept: list = []
    in_caps = False
    for ln in text.splitlines():
        if heading.match(ln):
            in_caps = True
            continue
        if in_caps and (ln.strip() == "---" or ln.startswith("#")):
            in_caps = False              # 這一段的圖說到此為止；分隔線／下一個標題照樣留在文章裡
        if not in_caps:
            kept.append(ln)
            continue
        row = ln.strip().lstrip("-*•").strip()
        if "｜" not in row and "|" not in row:
            continue
        name, _, cap = row.replace("|", "｜").partition("｜")
        name = name.strip().strip("`").removeprefix("assets/")
        cap = " ".join(cap.split())
        if cap and is_valid_asset(name) and (not allow or name in allow):
            caps[name] = cap[:200]
    return "\n".join(kept).rstrip() + "\n", caps


def asset_lines(assets: list) -> str:
    """章節提示裡「這一章有哪些圖」那一段。沒有圖就回空字串（整段不出現）。

    2026-09-19 改：**不再叫它插圖**。實測三本書，模型引用的比例是 3/50、0/10、0/35 ——
    靠它自己選，大部分的圖永遠不會出現。現在圖由程式一律列在章末（附圖說與頁數），
    這一段只是讓它知道有哪些圖、可以在文中提到。
    """
    if not assets:
        return ""
    out = ["", "===== 這一章有這幾張圖（已經從 PDF 抽出來了）=====",
           "🔴 **你看不到圖**。下面只有頁碼與圖旁邊的文字。不要描述你沒看過的畫面（不要寫「圖中可見…」）。",
           "這幾張**一定**會列在你這篇的最後面（附圖說與頁數），所以漏掉哪一張都不要緊。",
           "如果某張圖明顯對得上你正在寫的段落，就在那一段後面插一行 `![說明](assets/檔名)`，讀起來比較順；",
           "對不上就不要硬插。本文提到某張圖時，照它的編號與頁碼寫（例如「圖3-1（p.76）」）。",
           "",
           f"另外在**整篇的最後面**加一段 `## {CAPTION_HEADING}`，每張圖一行：",
           "`檔名｜一句圖說`（用半形直線分隔）。圖說照原書的圖說與本文寫成通順的一句，",
           "把原文黏到的頁碼、下一段開頭那種雜訊去掉。這一段會被程式取走，不會留在文章裡。"]
    for a in assets:
        cap = (a.get("caption") or "").strip()
        out.append(f"- `assets/{a.get('name')}`（第 {a.get('page')} 頁"
                   + (f"，附近的字：{cap}）" if cap else "）"))
    return "\n".join(out)


# ══════════════════════════════════════════════════════════════════════════
# 書籍基本資訊（owner 2026-09-19：「我希望有個 tab 可以填書的基本資訊」）
# 存在 `meta.info`。全部選填、全部純文字；書名與作者不在這裡（那兩個是 meta 的正本）。
# 🔴 欄位表是正本，前端 `frontend/js/knowledge/info.js` 的標籤要跟這裡對得起來
#    （test_knowledge_info 會比對兩邊的鍵）。
# ══════════════════════════════════════════════════════════════════════════

#: `(鍵, 幾個字的上限)`。順序就是畫面上的順序。
INFO_FIELDS = (
    ("subtitle", 200),          # 副書名
    ("original_title", 200),    # 原文書名
    ("translator", 100),        # 譯者
    ("publisher", 100),         # 出版社
    ("published", 40),          # 出版年
    ("edition", 40),            # 版次
    ("isbn", 40),
    ("link", 500),              # 購買或借閱的連結
    ("why", 500),               # 我為什麼讀這本
    ("read_at", 40),            # 讀完的日期
)
INFO_KEYS = tuple(k for k, _ in INFO_FIELDS)
_INFO_MAX = dict(INFO_FIELDS)


def normalize_info(raw: Any) -> dict:
    """只收白名單的鍵、壓成單行、各自截長。空字串的鍵不留（meta 不要堆一堆空值）。

    `link` 另外擋掉非 http／https —— 那一格會被畫成可以點的連結（同延伸那邊的規矩）。
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key in INFO_KEYS:
        val = " ".join(str(raw.get(key) or "").split())[:_INFO_MAX[key]].strip()
        if key == "link" and val and not val.lower().startswith(("http://", "https://")):
            val = ""
        if val:
            out[key] = val
    return out


#: 標題尾巴的頁碼：`（p.9）`／`（p.9-12）`／`（p.9–12）`（半形與全形括號、連字號與 en dash）
_TITLE_PAGES_RE = re.compile(r"[（(]\s*p\.?\s*[\d\s,.\-–—]*[）)]\s*$", re.I)
#: 開頭的 markdown 標題行
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


#: 比對標題時要丟掉的東西：空白與所有標點（全形半形都算）。
#: 模型抄標題時常常換一種寫法 —— `第二章 : 從…` vs `第二章：從…`、
#: `-` vs `—`。只留文字與數字來比，那幾種寫法就是同一個標題。
_PUNCT_RE = re.compile(r"[^\w一-鿿]+", re.U)
#: 「第二個標題是不是在重複章名」時，短的那一邊至少要這麼長才算數
_TITLE_MIN_OVERLAP = 6


def _squash(s: str) -> str:
    return _PUNCT_RE.sub("", str(s or ""))


def _title_key(line: str) -> str:
    """標題行 → 拿來比對「是不是同一個標題」的字：去掉 #、去掉尾巴的頁碼、只留文字。"""
    m = _HEADING_RE.match(line.strip())
    if not m:
        return ""
    return _squash(_TITLE_PAGES_RE.sub("", m.group(2)))


def drop_repeated_heading(md: str, title: str = "") -> str:
    """一章開頭連著兩個**同一個標題**時，把第二個拿掉（owner 2026-09-19：「做去重」）。

    編譯時我們寫了 `# 第 N 章 章名（p.X–Y）`，模型的正文又寫了一次
    `# 第 N 章 章名（p.X）` —— 頁碼寫法不同，但講的是同一章。

    只看**開頭連著的那兩行**，兩種情況才動手：
      1. 去掉頁碼之後一模一樣
      2. 第二行是 `#`（最上層），而且**整章的章名在裡面**
         —— 模型有時候會多寫幾個字或把頁碼抄錯，例如
         `# 第 8 章 第五章 結論（p.169–173）` 後面接
         `# 第五章 結論（p.160–164）筆記`。章名是 toc 給的，不是猜的。

    其餘原樣不動：第二行是 `## 核心概念` 那種次級標題、或根本是內容，都留著。
    寧可少去重一次，也不要把真的有兩層標題的那種章節吃掉一行。
    """
    text = str(md or "")
    lines = text.split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is None:
        return text
    key = _title_key(lines[first])
    if not key:
        return text
    second = next((i for i in range(first + 1, len(lines)) if lines[i].strip()), None)
    if second is None:
        return text
    second_key = _title_key(lines[second])
    tkey = _squash(title)
    same = second_key == key
    # 只有最上層的 `#` 算「又寫了一次章名」——`## 核心概念` 不算。
    # 兩邊互相包含都算：模型有時候多寫幾個字，有時候把長章名縮短
    #（`第十七章 : 天皇吃牛肉了！…- 蛋包飯、咖哩飯…` → `第十七章：天皇吃牛肉了！…`）。
    # 🔴 兩個方向的寬鬆程度不一樣：
    #    · 第二行**整個章名都在裡面** → 一定是重複，章名再短也算（「作者簡介」「序言」）。
    #    · 反過來，第二行只是章名的一小段 → 要至少 6 個字才算。不然章名叫
    #      「第一章 前言與背景」時，底下真的有一節 `# 前言` 會被誤刪。
    overlap = bool(tkey) and second_key != "" and (
        tkey in second_key or (second_key in tkey and len(second_key) >= _TITLE_MIN_OVERLAP))
    repeats_title = overlap and lines[second].strip().startswith("# ")
    if not (same or repeats_title):
        return text
    head, tail = lines[:second], lines[second + 1:]
    # 兩個標題之間與之後各有一個空行，只拿掉標題會剩下兩個空行疊在一起
    if head and not head[-1].strip() and tail and not tail[0].strip():
        head = head[:-1]
    return "\n".join(head + tail).lstrip("\n")


def safe_filename(name: str, fallback: str = "book.pdf") -> str:
    """要送去當下載檔名的字：把路徑分隔與 Windows 不收的字換掉、砍掉過長的。

    🔴 這是**唯一**一份 —— 研究筆記那份（`knowledge_pdf.filename_for`）與原書那份
    共用它，不然兩邊各寫一次正則，哪天漏了 `\\` 就變成「檔名裡帶路徑」。
    """
    # 分隔符**換成底線**而不是取最後一段：書名裡真的有斜線時（「甲/乙」）整段留著才看得懂，
    # 而只要一個分隔符都不剩，就爬不出資料夾。上傳時存的 source_name 本來就已經是純檔名。
    base = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(name or "")).strip(" .")
    return base[:80] or fallback


# ══════════════════════════════════════════════════════════════════════════
# 每一章一集 podcast（§9.13；owner 2026-09-19：「每一章一個podcast」「內容深度深一點」）
#
# 兩個人對談：主持人（提問、翻成白話）＋來賓（講內容）。聲音是 owner 挑的。
#
# 🔴 **兩趟**，不是一次寫完。實測第一版一次寫完整集：3,711 字、13.8 分鐘，而且把那一章
#    大量的具體東西（人名、年份、頁碼、外文原詞）整批跳過 —— 模型要在一個回合裡顧到整章，
#    只能挑幾個點講。改成「先拆段列出具體素材 → 再一段一段寫」之後：5,565 字、19.8 分鐘，
#    12 個抽查的具體項目全部出現。深度是被那份清單逼出來的，不是叫它「寫長一點」。
# ══════════════════════════════════════════════════════════════════════════

#: 音檔與逐字稿放這裡（在那本書自己的資料夾底下 → 跟著書架的根目錄走、刪書一起刪）
PODCAST_DIR = "podcast"
#: 主持人／來賓的聲音（owner 2026-09-19 聽過六個樣本後挑的：雲希＋曉臻）
PODCAST_HOST = ("zh-CN-YunxiNeural", "主持人")
PODCAST_GUEST = ("zh-TW-HsiaoChenNeural", "來賓")
#: 語速（+0% 太慢，聽起來像在念稿）
PODCAST_RATE = "+6%"
#: 一章拆幾段
PODCAST_SECTIONS = (6, 8)
#: 整集的目標字數上限（中文約 4.5 字／秒 → 6,200 字≈23 分鐘）
PODCAST_TARGET_CHARS = 6200
#: 稿子最多是原章節的幾倍。🔴 沒有這條的話短章節會被硬撐：2026-09-19 實測一個 173 字的
#: 測試章節被寫成 5,815 字（**33 倍**）—— 那種長度的內容只可能是掰的或重複的。
#: 鯨豚第二章 5,045 字 → 5,565 字（1.1 倍）才是正常的比例。
PODCAST_EXPAND_MAX = 1.5
#: 稿子的下限（太短的話一集三分鐘也還能聽）
PODCAST_MIN_CHARS = 1200
#: 章節短於這個字數就不做 —— 硬做只會生出掰的內容
PODCAST_MIN_CHAPTER = 600
#: 一句最多幾個字（太長聽起來像念稿，也不好斷句）
PODCAST_LINE_MAX = 60
_POD_RE = re.compile(r"^ch(\d{2})\.(mp3|json)$")


def podcast_name(n: int, ext: str = "mp3") -> str:
    """`(5, 'mp3')` → `ch05.mp3`。章號不合理或副檔名不對就回空字串。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    name = f"ch{n:02d}.{ext}" if 1 <= n <= 99 and ext in ("mp3", "json") else ""
    return name if _POD_RE.match(name) else ""


def is_valid_podcast(name: str) -> bool:
    """🔴 檔名一律先過白名單再拼路徑（同 `is_valid_asset` 的規矩）。"""
    return bool(_POD_RE.match(str(name or "")))


def podcast_chapter(name: str) -> int:
    """`ch05.mp3` → 5；不合白名單回 0。"""
    m = _POD_RE.match(str(name or ""))
    return int(m.group(1)) if m else 0


def parse_podcast_script(text: str) -> list:
    """claude 回的那一坨 → `[{who, text}]`。找不到 JSON 陣列、或形狀不對就回空清單。

    只收 `who` 是主持人或來賓的那幾句 —— 模型偶爾會自己多發明一個角色。
    """
    raw = str(text or "")
    a, b = raw.find("["), raw.rfind("]")
    if a < 0 or b <= a:
        return []
    try:
        rows = json.loads(raw[a:b + 1])
    except ValueError:
        return []
    if not isinstance(rows, list):
        return []
    who_ok = {PODCAST_HOST[1], PODCAST_GUEST[1]}
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        who = str(r.get("who") or "").strip()
        line = " ".join(str(r.get("text") or "").split()).strip()
        if who in who_ok and line:
            out.append({"who": who, "text": line})
    return out


def podcast_minutes(lines) -> float:
    """這份稿大概幾分鐘（中文約 4.5 字／秒）。"""
    chars = sum(len(r.get("text") or "") for r in (lines or []))
    return round(chars / 4.5 / 60, 1)


def podcast_budget(chapter_chars: int, sections: int) -> int:
    """一段要寫幾個字。總長跟著**原章節的長度**走，不是固定值。

    🔴 固定 6,200 字的話，短章節會被硬撐到二十分鐘（實測 173 字的章節被寫成 5,815 字）。
    規則只說「不准掰」，但一直逼它產字，它就會開始重複或延伸 —— 從源頭限長比較實在。
    """
    total = max(PODCAST_MIN_CHARS, min(PODCAST_TARGET_CHARS,
                                       int(max(0, chapter_chars) * PODCAST_EXPAND_MAX)))
    # 每段的下限壓低（150）：下限訂太高的話，段數一多總長就被墊回去，
    # 「跟著章節長度走」那件事就失效了（實測 6 段 × 300 = 1,800，比上面算的總長還大）。
    return max(150, int(total / max(1, sections)))


def podcast_outline_prompt(body: str) -> str:
    """第一趟：把一章拆成幾段，每段列出**那一段裡真的有的具體東西**。"""
    lo, hi = PODCAST_SECTIONS
    return f"""把下面這一章的整理筆記拆成 {lo} 到 {hi} 段，給我一份大綱。

每一段要列出**那一段裡真的有的具體東西**：人名、機構名、地名、年份、數字、書名、頁碼、
作者說的原話。這是要拿來做 podcast 的素材清單，所以越具體越好 —— 抽象的形容詞不要。

🔴 只能寫筆記裡有的。筆記沒有的一個字都不要補。

輸出**只有一個 JSON 陣列**，不要任何其他文字、不要 markdown 程式碼框。每個元素：
{{"title": "這一段在講什麼（10 字以內）",
  "point": "這一段的核心主張（一句話）",
  "facts": ["具體的東西 1", "具體的東西 2"]}}

────── 這一章的整理筆記 ──────
{body}
"""


def podcast_section_prompt(section: dict, *, body: str, budget: int,
                           opening: str, closing: str) -> str:
    """第二趟：把其中一段寫成對談。`opening`／`closing` 由呼叫端決定（第一段要報開場白）。"""
    facts = "\n".join("・" + str(f) for f in (section.get("facts") or []))
    return f"""你是中文 podcast 的編劇。把下面這一段素材寫成**兩個人的對談**。

角色：
- 「{PODCAST_HOST[1]}」：好奇、會追問「根據是什麼」「作者怎麼知道」「那這代表什麼」。負責翻成白話。
- 「{PODCAST_GUEST[1]}」：講內容的人。會給**具體的**名字、年份、數字，會說明前因後果。

硬規則：
1. **只能講下面素材裡有的東西**，一個字都不准自己補。素材沒有的就不要提。
2. 下面列的「要講到的具體內容」**每一項都要出現在對談裡**，而且要講清楚它是什麼、為什麼重要 ——
   不是報菜名。這一段的深度就靠這個。
3. {PODCAST_HOST[1]}至少**追問三次**（不是附和，是真的問下去：為什麼、憑什麼、那反過來呢）。
4. 每一句不超過 {PODCAST_LINE_MAX} 個字；長的拆開，中間讓對方接話。
5. 繁體中文、台灣用語。年份用阿拉伯數字。
6. 這一段的總字數 **{budget} 字上下**。
7. {opening}
8. {closing}

輸出**只有一個 JSON 陣列**，不要任何其他文字、不要程式碼框。
元素：{{"who": "{PODCAST_HOST[1]}" 或 "{PODCAST_GUEST[1]}", "text": "那句話"}}

────── 這一段在講什麼 ──────
{section.get("point", "")}

────── 要講到的具體內容（每一項都要） ──────
{facts}

────── 這一段的原始筆記（只能從這裡取材） ──────
{body}
"""


def podcast_opening(title: str, chapter_label: str, first: bool) -> str:
    """第一段要先講「這不是原書內容」那一句（同 PDF、分享頁的版權界線）。"""
    if not first:
        return "直接從這一段的內容開始，不用再自我介紹。"
    return (f'第一句固定由{PODCAST_HOST[1]}說：'
            f'「以下是針對《{title}》{chapter_label}整理的討論，不是原書內容。」')


def podcast_closing(next_hint: str, last: bool) -> str:
    if not last:
        return f"這一段結束時，讓{PODCAST_HOST[1]}用一句話小結，然後帶到下一個話題。"
    tail = f"，並預告下一集要聊{next_hint}" if next_hint else ""
    return f"最後由{PODCAST_HOST[1]}收尾{tail}。"


# ══════════════════════════════════════════════════════════════════════════
# 這本書掛在哪一個案子（owner 2026-09-19：「這裡也要可以連結現有的專案列表」）
#
# 存在 `meta.project` = `{id, label}`。**label 是拍下來的那一刻的字**，不是每次去查 ——
# 書在知識庫這邊，案子在 CRM 的資料庫那邊；NAS 與離線的機器讀得到書卻讀不到 DB，
# 每次都要查的話那個連結會在一半的機器上是空白的。案名改了就下次重選一次。
#
# 🔴 清單怎麼列**不在這裡** —— owner 2026-09-19：「列表的列出來的方式，和工作時數
#    填寫的規則相同」，那份規則住在 `services/project_picker.list_options`（工時補登與
#    備份頁共用的同一支）。這裡只管存下來的形狀。
# ══════════════════════════════════════════════════════════════════════════

#: 專案 id 的樣子（CRM 是 uuid 字串）。白名單比「什麼都收」安全：這個值會被拼進網址。
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
PROJECT_LABEL_MAX = 120


def normalize_project(raw: Any) -> dict:
    """`{id, label}`；不合格式、或送 `None`／空的都回 `{}`（＝沒有掛案子）。"""
    if not isinstance(raw, dict):
        return {}
    pid = str(raw.get("id") or "").strip()
    if not PROJECT_ID_RE.match(pid):
        return {}
    label = " ".join(str(raw.get("label") or "").split())[:PROJECT_LABEL_MAX].strip()
    return {"id": pid, "label": label or pid}


# ══════════════════════════════════════════════════════════════════════════
# 公開分享要放哪些東西（§9.9；owner 2026-09-19：「公開分享的內容讓我勾選」）
#
# 書名與作者**一定**會出去 —— 那是這份分享在講哪一本書，不能匿名。其餘全部由他勾。
# 預設只開三項（基本資訊、標籤、延伸）：那三項要嘛是他填的書目資料，要嘛是網路上的公開資料。
# 其他預設關，因為不是他自己的私事就是原書的內容。
# ══════════════════════════════════════════════════════════════════════════

#: `(鍵, 預設開不開, 這是誰的東西)`。順序就是畫面上的順序。
SHARE_PARTS = (
    ("info", True, "你填的書目資料"),
    ("tags", True, "你自己下的標籤"),
    ("extend", True, "網路上的公開資料，每則附出處"),
    ("conclusion", False, "你自己寫的"),
    ("notes", False, "你自己寫的"),
    ("skill", False, "原書的內容整理，公開要注意版權"),
    ("chapters", False, "原書的內容整理，公開要注意版權"),
    ("gallery", False, "原書裡的圖，公開要注意版權"),
    # owner 2026-09-19：「公開連結的公開設定 也讓我勾要不要讓人下載 pdf」「研究報告與書籍都可以」
    # · 研究筆記預設開著 —— 當初做那顆鈕就是為了「讓大家可以下載資料」。
    # · 原書預設關著 —— 那是整本書，公開等於把書放上網。要開是他的決定，但不預設替他開。
    ("pdf", True, "讓對方把上面勾的東西印成一份 PDF 帶走"),
    ("source", False, "讓對方下載整本原書的 PDF；那是整本書，公開要注意版權"),
    # owner 2026-09-19 那一批 podcast：預設關著（跟原書同一個理由 —— 內容是原書的整理）
    ("podcast", False, "讓對方線上聽每一章的 podcast"),
)
SHARE_PART_KEYS = tuple(k for k, _, _ in SHARE_PARTS)
#: 「能力」型的勾（不是內容的一段）：沒有對應的資料鍵，由端點自己認。
#: 分這兩組是為了讓「每一段都要在 `kl.shares(...)` 底下」那條測試繼續看得懂自己在檢查什麼。
SHARE_ABILITIES = ("pdf", "source", "podcast")
#: 「內容」型的勾：勾了才會在 `knowledge_share._build` 的回傳裡出現那個鍵。
SHARE_CONTENT_KEYS = tuple(k for k in SHARE_PART_KEYS if k not in SHARE_ABILITIES)
#: 沒設定過的舊分享用這一組（＝2026-09-19 之前那版的行為）
SHARE_DEFAULT = tuple(k for k, on, _ in SHARE_PARTS if on)


def normalize_parts(raw: Any) -> list:
    """只收白名單的鍵、去重、照 `SHARE_PARTS` 的順序排。

    `None`＝沒帶（呼叫端自己決定要不要沿用舊的）；空清單＝真的一項都不分享。
    """
    if raw is None:
        return list(SHARE_DEFAULT)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return list(SHARE_DEFAULT)
    picked = {str(x) for x in raw}
    return [k for k in SHARE_PART_KEYS if k in picked]


def shares(parts: Optional[list], key: str) -> bool:
    """這一項有沒有被勾。`parts` 是 None（舊的分享還沒設定過）就用預設那組。"""
    return key in (list(parts) if parts is not None else list(SHARE_DEFAULT))
