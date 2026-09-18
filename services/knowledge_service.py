# -*- coding: utf-8 -*-
"""services/knowledge_service.py — 「知識庫」（獨立應用 /knowledge.html）的 I/O（規劃正本 docs/KNOWLEDGE_BASE_PLAN.md §2、§3.2、§11）。

一本書一個資料夾 `books/<id>/`（檔案，不進 DB、不進 git）：
  source.pdf／full_text.txt／meta.json／SKILL.md／chapters/chNN-<slug>.md／glossary.md／patterns.md／
  cheatsheet.md／chat.json／結論.md／筆記.md。

規則全在 core/knowledge_logic.py（純函式），叫 claude 走 services/knowledge_claude.py。
🔴 任何拼路徑前先 `is_valid_id`（`book_dir` 統一擋）；章檔名由我們產、支援檔名走 `split_files` 白名單。
🔴 編譯與討論都不動 DB；owner 的財務數字只讀 `ADVISOR_LATEST` 的四個欄位（`knowledge_logic.snapshot_lines`），
而且只有 meta.tags 含 `FINANCE_TAG`（財務）的書才帶（其他書走一般討論夥伴角色，不帶任何數字）。

執行中狀態只活在記憶體（模組層 `_stage`／`_chat_partial`／`_chat_stage`）；落檔的是 meta.status。
行程重啟時 meta.status 可能停在 compiling —— `list_books`／`book_detail` 看到「status=compiling 但沒人在跑」就當 failed 回。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import threading
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from core import knowledge_logic as kl
from core.bg_task import fire
from services.knowledge_claude import call_claude, claude_available

logger = logging.getLogger(__name__)

DEFAULT_ROOT = r"D:\Originsun-Knowledge\books"
#: 顧問快照（fetch_finance.py 存的）—— 固定位置，不從書架根目錄推（2026-09-18 書架搬到 D:\Originsun-Knowledge 之後兩者不同層了）
ADVISOR_LATEST = r"D:\Originsun-Advisor\latest.json"
#: 上傳上限（PDF）
MAX_UPLOAD_BYTES = 300 * 1024 * 1024
#: 每次 claude 呼叫的逾時：編譯（一章可能上萬字）與討論／整理結論
COMPILE_TIMEOUT_SEC = 20 * 60
CHAT_TIMEOUT_SEC = 10 * 60
#: 研究助理（要上網搜好幾輪，比討論久一點，但別久到卡住閘門）
EXTEND_TIMEOUT_SEC = 12 * 60
# 編譯到一半行程重啟（發版、dev reload）：meta 停在 compiling 沒 error，畫面要講得出來
INTERRUPTED_MSG = "編譯被中斷（主機重啟過）。再按一次「讀這本書」會從缺的章接著補，已產的不會重做。"
#: 預設模型（settings `ai.models.knowledge` 可覆蓋；都要過 quote_chat.pick_model 白名單）
DEFAULT_MODEL = "opus"

META_FILE = "meta.json"
SOURCE_FILE = "source.pdf"
FULLTEXT_FILE = "full_text.txt"
CHAT_FILE = "chat.json"
CONCLUSION_FILE = "結論.md"
NOTES_FILE = "筆記.md"
EXTEND_FILE = "延伸.md"
CHAPTERS_DIR = "chapters"
ASSETS_DIR = "assets"                     # 從 PDF 抽出來的圖（§9.7）
SUPPORT_RAW_FILE = "_support_raw.txt"     # 骨架 pass 解不出四個檔時留的原文（除錯用）

_TW = ZoneInfo("Asia/Taipei")

# ── 執行中狀態（只活在記憶體）──
_stage: dict = {}            # id → 編譯進度字串（'第 3/12 章' 之類）；不在裡面＝沒在編
_chat_partial: dict = {}     # id → 這一輪串到目前的回覆
_chat_stage: dict = {}       # id → 'queued'｜'running'；不在裡面＝沒在跑
_concluding: set = set()     # 正在整理結論的 id
_extending: dict = {}        # id → 'queued'｜'searching'…；不在裡面＝沒在找資料


class BookNotFound(Exception):
    """id 不合法或資料夾不存在（端點回 404）。"""


class ChapterNotFound(Exception):
    """書在、那一章不在（端點回 404，訊息要講是章不是書）。"""


class BookBusy(Exception):
    """這本書正在編譯／討論中（端點回 409）。"""


class BookHasNoFile(Exception):
    """「待補」的書還沒有 PDF，不能編（端點回 409）。"""


class BookHasFile(Exception):
    """這本已經有檔，不能再補（端點回 409；要換檔＝刪掉重上）。"""


#: meta.status 的值集合：pending（待補：只有書名，還沒 PDF）→ uploaded → compiling → compiled｜failed
STATUS_PENDING = "pending"
STATUSES = (STATUS_PENDING, "uploaded", "compiling", "compiled", "failed")


class ExtendNotFound(Exception):
    """書在、那一則延伸不在（端點回 404）。刪過的流水號不重用，所以這通常代表畫面舊了。"""


# ── 路徑 ─────────────────────────────────────────────────────
def root() -> str:
    """書架根目錄：settings `knowledge.root`，沒設就預設 D:\\Originsun-Knowledge\\books。"""
    from config import load_settings
    kn = load_settings().get("knowledge") or {}
    return str(kn.get("root") or "").strip() or DEFAULT_ROOT


def book_dir(book_id: str, *, must_exist: bool = True) -> str:
    """`books/<id>`。id 不合法一律 BookNotFound（不讓 `..`／斜線碰到路徑）。"""
    if not kl.is_valid_id(book_id):
        raise BookNotFound(book_id)
    path = os.path.join(root(), book_id)
    if must_exist and not os.path.isfile(os.path.join(path, META_FILE)):
        raise BookNotFound(book_id)
    return path


def _now_iso() -> str:
    return datetime.now(_TW).isoformat(timespec="seconds")


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _write_text(path: str, text: str) -> None:
    """先寫暫存再 replace：寫到一半斷掉不會留半個檔。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text or "")
    os.replace(tmp, path)


def read_meta(book_id: str) -> dict:
    d = book_dir(book_id)
    try:
        with open(os.path.join(d, META_FILE), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        raise BookNotFound(book_id)
    return meta if isinstance(meta, dict) else {}


def write_meta(book_id: str, meta: dict) -> None:
    d = book_dir(book_id, must_exist=False)
    _write_text(os.path.join(d, META_FILE), json.dumps(meta, ensure_ascii=False, indent=2))


# 每本書一把鎖：_update_meta 是讀-改-寫，編譯（事件迴圈那條）與端點（to_thread 那條）會同時改同一份 meta，
# 沒鎖＝後寫的把先寫的鍵蓋掉（toc／status／tags 任何一個），而且完全靜默。只擋同一行程；跨行程靠 dev／prod 分 root。
_META_LOCKS: dict = {}
_META_LOCKS_GUARD = threading.Lock()


def _meta_lock(book_id: str) -> threading.Lock:
    with _META_LOCKS_GUARD:
        return _META_LOCKS.setdefault(book_id, threading.Lock())


def _update_meta(book_id: str, **fields) -> dict:
    with _meta_lock(book_id):
        meta = read_meta(book_id)
        meta.update(fields)
        write_meta(book_id, meta)
        return meta


# ── 上傳 ─────────────────────────────────────────────────────
def is_pdf(content: bytes) -> bool:
    """看檔頭不看副檔名。"""
    return bool(content) and content[:4] == b"%PDF"


def _pymupdf():
    try:
        import pymupdf as _mu
    except ImportError:  # 舊版只有 fitz（會印 deprecation warning）
        import fitz as _mu
    return _mu


def extract_text(content: bytes) -> tuple:
    """pymupdf 抽全文：回 `(text, pages)`，每頁前加 `[[p.N]]`。同步、吃 CPU —— 呼叫端丟 to_thread。"""
    text, pages, _ = extract_all(content, assets_to=None)
    return text, pages


def extract_all(content: bytes, *, assets_to: Optional[str] = None) -> tuple:
    """抽全文＋表格＋圖：回 `(text, pages, assets)`。同步、吃 CPU —— 呼叫端丟 to_thread。

    owner 2026-09-18：「如果章節有重要圖片 或表格 我希望你也可以截取出來」。
      表：抽成 markdown **直接塞進全文**那一頁後面 —— 切章時自動跟著走，claude 看得到內容。
      圖：存成檔案（`assets_to` 給資料夾才存），只回清單；claude 看不到圖，
          章節提示只帶頁碼與旁邊的文字（見 knowledge_logic.asset_lines）。
    一頁抽圖／找表都可能對怪 PDF 拋例外 —— 那一頁跳過就好，不要讓整本書進不來。
    """
    doc = _pymupdf().open(stream=content, filetype="pdf")
    if assets_to:
        os.makedirs(assets_to, exist_ok=True)
    assets: list = []
    try:
        parts = []
        for i, page in enumerate(doc, start=1):
            parts.append(kl.PAGE_MARK.format(n=i) + "\n" + (page.get_text() or "") + "\n")
            parts.extend(_page_tables(page, i))
            if assets_to and len(assets) < kl.MAX_ASSETS:
                assets.extend(_page_images(doc, page, i, assets_to))
        return "".join(parts), doc.page_count, assets
    finally:
        doc.close()


def save_images(content: bytes, dest: str) -> list:
    """只走圖的那一趟（不抽文字，比 `extract_all` 快很多）。回 `[{name, page, caption}]`。"""
    doc = _pymupdf().open(stream=content, filetype="pdf")
    os.makedirs(dest, exist_ok=True)
    out: list = []
    try:
        for i, page in enumerate(doc, start=1):
            if len(out) >= kl.MAX_ASSETS:
                break
            out.extend(_page_images(doc, page, i, dest))
        return out
    finally:
        doc.close()


def _page_tables(page, n: int) -> list:
    """這一頁的表格 → 塞進全文的那幾段。"""
    out = []
    try:
        tables = page.find_tables().tables
    except Exception:
        return out
    for k, t in enumerate(tables, start=1):
        try:
            if not kl.keep_table(t.row_count, t.col_count):
                continue
            out.append(kl.table_block(n, k, t.to_markdown()))
        except Exception:
            continue
    return out


def _caption_near(page, bbox) -> str:
    """圖下方那一行字（多半是圖說）。抓不到就回空。"""
    try:
        import pymupdf as _mu
    except ImportError:
        import fitz as _mu
    try:
        below = _mu.Rect(bbox.x0 - 20, bbox.y1, bbox.x1 + 20, bbox.y1 + 80)
        return " ".join((page.get_text("text", clip=below) or "").split())[:kl.CAPTION_CHARS]
    except Exception:
        return ""


def _page_images(doc, page, n: int, dest: str) -> list:
    """這一頁值得留的圖 → 寫檔，回 `[{name, page, caption}]`。

    先看這一頁本身：沒有本文（封面、整頁掃描）就整頁跳過 —— 那種圖沒有上下文，
    claude 看不到圖也說不出什麼，留著只是佔空間（見 knowledge_logic.keep_page_image）。
    """
    out = []
    try:
        imgs = page.get_images(full=True)
        if not imgs:
            return out
        page_chars = len((page.get_text() or "").strip())
        page_area = float(page.rect.width * page.rect.height) or 1.0
    except Exception:
        return out
    for k, info in enumerate(imgs, start=1):
        if len(out) >= kl.MAX_ASSETS_PER_PAGE:
            break
        try:
            d = doc.extract_image(info[0])
            if not kl.keep_image(d.get("width"), d.get("height")):
                continue
            rects = page.get_image_rects(info[0])
            cover = (rects[0].width * rects[0].height / page_area) if rects else 0.0
            if not kl.keep_page_image(page_chars, cover):
                continue
            name = kl.asset_name(n, k, d.get("ext"))
            if not name:
                continue
            with open(os.path.join(dest, name), "wb") as f:
                f.write(d["image"])
            out.append({"name": name, "page": n,
                        "caption": _caption_near(page, rects[0]) if rects else ""})
        except Exception:
            continue
    return out


def _new_meta(book_id: str, title: str, *, author: str = "", tags=None) -> dict:
    """一本新書的 meta 骨架（還沒有檔：pages 0、status pending）。"""
    return {
        "id": book_id,
        "title": (title or "").strip() or "未命名",
        "author": (author or "").strip(),
        "source_name": "",
        "pages": 0,
        "chars": 0,
        "uploaded_at": _now_iso(),
        "status": STATUS_PENDING,
        "error": "",
        "compiled_at": "",
        "chapters": 0,
        "toc": [],
        "model": "",
        "tags": kl.normalize_tags(tags),
    }


def _write_source(book_id: str, content: bytes, source_name: str, extracted: Optional[tuple] = None) -> dict:
    """把 PDF 放進這本書：source.pdf＋full_text.txt，meta 補頁數／字數／原檔名、status→uploaded。
    文字抽取在這裡做（呼叫端用 to_thread 包）；`extracted=(text, pages)` 是先抽好的（save_upload 要在建資料夾前抽）。"""
    text, pages = extracted if extracted is not None else extract_text(content)
    d = book_dir(book_id, must_exist=False)
    os.makedirs(os.path.join(d, CHAPTERS_DIR), exist_ok=True)
    with open(os.path.join(d, SOURCE_FILE), "wb") as f:
        f.write(content)
    _write_text(os.path.join(d, FULLTEXT_FILE), text)
    name = os.path.basename(str(source_name or "").replace("\\", "/"))
    # 圖：資料夾這時才存在。抽不出來不該讓整本書進不來（掃描檔、壞的內嵌圖都可能炸）
    captions = {}
    try:
        captions = {a["name"]: a["caption"] for a in save_images(content, os.path.join(d, ASSETS_DIR))}
    except Exception:
        logger.exception("知識庫：抽圖失敗 %s（書照樣收下）", book_id)
    return _update_meta(book_id, source_name=name, pages=pages, chars=len(text),
                        asset_captions=captions,
                        status="uploaded", error="", uploaded_at=_now_iso())


def create_pending(title: str, *, author: str = "", tags=None) -> dict:
    """「待補」：先建書名（＋作者／標籤），PDF 之後用 `attach_file` 補。回 summary。"""
    if not (title or "").strip():
        raise ValueError("書名不能空白")
    book_id = kl.new_id()
    d = book_dir(book_id, must_exist=False)
    os.makedirs(os.path.join(d, CHAPTERS_DIR), exist_ok=True)
    write_meta(book_id, _new_meta(book_id, title, author=author, tags=tags))
    return _summary(read_meta(book_id), book_id)


def attach_file(book_id: str, content: bytes, source_name: str) -> dict:
    """給「待補」的書補 PDF；已經有檔的書丟 BookHasFile（要換檔＝刪掉重上，不做覆蓋——
    覆蓋會讓已編的章節對不上新的頁碼而且沒人知道）。回 summary。"""
    meta = read_meta(book_id)
    if meta.get("status") != STATUS_PENDING or os.path.isfile(os.path.join(book_dir(book_id), SOURCE_FILE)):
        raise BookHasFile(book_id)
    meta = _write_source(book_id, content, source_name)
    return _summary(meta, book_id)


def save_upload(content: bytes, source_name: str, title: str = "") -> dict:
    """上傳一本新書（一步到位：建 meta＋放檔）；回 meta。"""
    extracted = extract_text(content)       # 先抽：壞檔在這裡就丟，不留一個沒檔的空資料夾在書架上
    book_id = kl.new_id()
    base = os.path.splitext(os.path.basename(str(source_name or "").replace("\\", "/")))[0]
    d = book_dir(book_id, must_exist=False)
    os.makedirs(os.path.join(d, CHAPTERS_DIR), exist_ok=True)
    write_meta(book_id, _new_meta(book_id, (title or "").strip() or base))
    return _write_source(book_id, content, source_name, extracted)


# ── 讀 ───────────────────────────────────────────────────────
def _effective_status(meta: dict, book_id: str) -> str:
    """meta 停在 compiling 但沒人在編（行程重啟過）→ 當 failed 回，不然畫面永遠轉圈。"""
    status = meta.get("status") or "uploaded"
    if status == "compiling" and book_id not in _stage:
        return "failed"
    return status


def _chapter_rows(book_id: str, meta: dict) -> list:
    """目錄裡**真的有檔**的章（編譯失敗一半時只列產出來的）。"""
    d = book_dir(book_id)
    out = []
    for c in meta.get("toc") or []:
        fname = c.get("file") or kl.chapter_filename(c.get("n") or 0, c.get("title") or "")
        if os.path.isfile(os.path.join(d, CHAPTERS_DIR, fname)):
            # 頁碼一起給：前端的「圖輯」要靠它把圖分到各章（owner 2026-09-19）
            out.append({"n": c.get("n"), "title": c.get("title") or "", "file": fname,
                        "start_page": c.get("start_page"), "end_page": c.get("end_page")})
    return out


def _summary(meta: dict, book_id: str) -> dict:
    d = book_dir(book_id)
    status = _effective_status(meta, book_id)
    extend_total, extend_new = _extend_counts(d)      # 讀一次就好（書架每本都會叫）
    return {
        "id": book_id,
        "title": meta.get("title") or "",
        "author": meta.get("author") or "",
        "status": status,
        # 行程重啟把編譯砍掉時 meta 沒機會寫 error —— 給一句人話，不然畫面只看到「失敗」
        "error": ((meta.get("error") or INTERRUPTED_MSG) if status == "failed" else ""),
        "pages": meta.get("pages") or 0,
        "chapters": len(_chapter_rows(book_id, meta)),
        "uploaded_at": meta.get("uploaded_at") or "",
        "compiled_at": meta.get("compiled_at") or "",
        "has_conclusion": bool(_read_text(os.path.join(d, CONCLUSION_FILE)).strip()),
        "has_notes": bool(_read_text(os.path.join(d, NOTES_FILE)).strip()),
        "watch": bool(meta.get("watch")),
        "focus": meta.get("focus") or "",
        "info": meta.get("info") or {},
        "project": kl.normalize_project(meta.get("project")),
        "has_extend": extend_total > 0,
        "extend_new": extend_new,
        "stage": _stage.get(book_id, ""),
        "tags": kl.normalize_tags(meta.get("tags")),
    }


def list_books(tag: str = "") -> list:
    """書架（沒有根目錄就回空清單，不建）。`tag` 非空＝只列 meta.tags 含它的書。"""
    base = root()
    if not os.path.isdir(base):
        return []
    want = (tag or "").strip()
    out = []
    for name in sorted(os.listdir(base)):
        if not kl.is_valid_id(name):
            continue
        try:
            row = _summary(read_meta(name), name)
        except BookNotFound:
            continue
        if want and want not in row["tags"]:
            continue
        out.append(row)
    out.sort(key=lambda r: r.get("uploaded_at") or "", reverse=True)
    return out


def book_detail(book_id: str) -> dict:
    """meta＋stage＋骨架＋章節索引＋結論＋筆記（不含 chat）。"""
    meta = read_meta(book_id)
    d = book_dir(book_id)
    row = _summary(meta, book_id)
    row.update({
        "source_name": meta.get("source_name") or "",
        "chars": meta.get("chars") or 0,
        "model": meta.get("model") or "",
        "skill": _read_text(os.path.join(d, "SKILL.md")),
        "cheatsheet": _read_text(os.path.join(d, "cheatsheet.md")),
        "glossary": _read_text(os.path.join(d, "glossary.md")),
        "patterns": _read_text(os.path.join(d, "patterns.md")),
        "chapter_list": _chapter_rows(book_id, meta),
        "conclusion": _read_text(os.path.join(d, CONCLUSION_FILE)),
        "notes": _read_text(os.path.join(d, NOTES_FILE)),
        "concluding": book_id in _concluding,
        "chatting": book_id in _chat_stage,
    })
    return row


def read_chapter(book_id: str, n: int) -> dict:
    """第 n 章的 md（照 toc 找檔，不拼使用者給的名字）＋**這一章頁碼範圍內的圖**。

    圖一律回 —— 章末的相簿由前端畫（owner 2026-09-19：「不用一定要插入頁面，
    可以在文章底部用相簿、圖說方式（標注頁數）呈現」）。
    """
    meta = read_meta(book_id)
    toc = {int(c.get("n") or 0): c for c in (meta.get("toc") or [])}
    for c in _chapter_rows(book_id, meta):
        if int(c.get("n") or 0) != int(n):
            continue
        t = toc.get(int(n)) or {}
        assets = kl.assets_in_range(list_assets(book_id),
                                    int(t.get("start_page") or 0),
                                    int(t.get("end_page") or 10 ** 6))
        return {**c, "start_page": t.get("start_page"), "end_page": t.get("end_page"),
                "assets": assets,
                "md": _read_text(os.path.join(book_dir(book_id), CHAPTERS_DIR, c["file"]))}
    raise ChapterNotFound(n)


def update_book(book_id: str, *, title: Optional[str] = None, author: Optional[str] = None,
                tags: Optional[list] = None, watch: Optional[bool] = None,
                focus: Optional[str] = None, info: Optional[dict] = None,
                project: Optional[dict] = None) -> dict:
    fields = {}
    if project is not None:
        # 掛在哪一個案子（§9.11）。送 `{}` 就是取消掛案 —— 跟 info 一樣整包換掉。
        fields["project"] = kl.normalize_project(project)
    if info is not None:
        fields["info"] = kl.normalize_info(info)      # 書籍基本資訊（§9.8；整包換掉，不逐鍵合併）
    if focus is not None:
        fields["focus"] = kl.normalize_focus(focus)    # 研究助理要往哪邊找（§9；空字串＝沒設定）
    if watch is not None:
        fields["watch"] = bool(watch)      # 研究助理每週要不要管這本（§9.3；預設沒有這個鍵＝關）
    if title is not None and title.strip():
        fields["title"] = title.strip()
    if author is not None:
        fields["author"] = author.strip()
    if tags is not None:
        fields["tags"] = kl.normalize_tags(tags)
    meta = _update_meta(book_id, **fields) if fields else read_meta(book_id)
    return _summary(meta, book_id)


def delete_book(book_id: str) -> None:
    if book_id in _stage or book_id in _chat_stage or book_id in _concluding:
        raise BookBusy(book_id)
    shutil.rmtree(book_dir(book_id), ignore_errors=True)


# ── 結論／筆記／對話（檔案）──
def read_chat(book_id: str) -> list:
    try:
        rows = json.loads(_read_text(os.path.join(book_dir(book_id), CHAT_FILE)) or "[]")
    except ValueError:
        rows = []
    return rows if isinstance(rows, list) else []


def _write_chat(book_id: str, chat: list) -> None:
    _write_text(os.path.join(book_dir(book_id), CHAT_FILE), json.dumps(chat, ensure_ascii=False, indent=1))


def add_chat_message(book_id: str, role: str, text: str) -> dict:
    msg = kl.chat_message(role, text, _now_iso())
    chat = read_chat(book_id)
    chat.append(msg)
    _write_chat(book_id, chat)
    return msg


def chat_state(book_id: str) -> dict:
    """前端 1 秒輪詢的那包：整段對話＋這一輪串到哪＋卡在閘門還是在跑。"""
    return {"chat": read_chat(book_id), "partial": _chat_partial.get(book_id, ""),
            "stage": _chat_stage.get(book_id, ""), "concluding": book_id in _concluding}


def append_conclusion(book_id: str, text: str) -> str:
    path = os.path.join(book_dir(book_id), CONCLUSION_FILE)
    when = datetime.now(_TW).strftime("%Y-%m-%d %H:%M")
    new = kl.append_section(_read_text(path), text, when)
    _write_text(path, new)
    return new


def write_conclusion(book_id: str, text: str) -> None:
    _write_text(os.path.join(book_dir(book_id), CONCLUSION_FILE), text or "")


def write_notes(book_id: str, text: str) -> None:
    _write_text(os.path.join(book_dir(book_id), NOTES_FILE), text or "")


# ── 模型 ─────────────────────────────────────────────────────
def pick_model(requested: str = "") -> str:
    """前端選的 → settings `ai.models.knowledge` → 預設 opus；一律過 quote_chat 白名單。"""
    from config import load_settings
    from core.quote_chat import pick_model as _pick
    models = (load_settings().get("ai") or {}).get("models") or {}
    return _pick(requested, str(models.get("knowledge") or DEFAULT_MODEL))


# ── 編譯（三個 pass）──
def assets_dir(book_id: str, *, make: bool = False) -> str:
    d = os.path.join(book_dir(book_id), ASSETS_DIR)
    if make:
        os.makedirs(d, exist_ok=True)
    return d


def asset_path(book_id: str, name: str) -> str:
    """🔴 檔名一律先過白名單，再拼路徑（同章節檔名的規矩）。
    id 直接走 `book_dir()`，不經過 `assets_dir()` —— 包一層也不行（見
    test_ids_only_become_paths_through_book_dir，那條刻意只認 `book_dir(`）。"""
    if not kl.is_valid_asset(name):
        raise ChapterNotFound(f"{book_id}/{name}")
    return os.path.join(book_dir(book_id), ASSETS_DIR, name)


def list_assets(book_id: str) -> list:
    """`[{name, page, caption}]`，照頁碼排。圖說存在 meta（檔案本身不帶字）。"""
    d = assets_dir(book_id)
    if not os.path.isdir(d):
        return []
    caps = (read_meta(book_id).get("asset_captions") or {})
    rows = [{"name": n, "page": kl.asset_page(n), "caption": caps.get(n, "")}
            for n in os.listdir(d) if kl.is_valid_asset(n)]
    return sorted(rows, key=lambda a: (a["page"], a["name"]))


def ensure_assets(book_id: str) -> list:
    """舊書補抽圖（2026-09-18 之前上傳的沒有這一步）。已經有就直接回。

    🔴 **只抽圖，不碰 full_text.txt**。表格是在上傳時就塞進全文的（見 `extract_all`），
    這裡重抽全文等於把現在那一份整個蓋掉 —— 誰在那裡放了什麼都會不見。
    舊書要有表格，就把那本重新上傳一次。
    """
    have = list_assets(book_id)
    if have:
        return have
    src = os.path.join(book_dir(book_id), SOURCE_FILE)
    if not os.path.exists(src):
        return []
    with open(src, "rb") as f:
        content = f.read()
    assets = save_images(content, assets_dir(book_id, make=True))
    _update_meta(book_id, asset_captions={a["name"]: a["caption"] for a in assets})
    return list_assets(book_id)


def _support_missing(d: str) -> list:
    return [n for n in kl.SUPPORT_FILES if not os.path.isfile(os.path.join(d, n))]


def reset_compiled(book_id: str) -> None:
    """整本重編：丟掉目錄、章節、四個支援檔＋上次骨架失敗留的原文（原檔、全文、結論、筆記、對話不動）。"""
    d = book_dir(book_id)
    shutil.rmtree(os.path.join(d, CHAPTERS_DIR), ignore_errors=True)
    os.makedirs(os.path.join(d, CHAPTERS_DIR), exist_ok=True)
    for n in kl.SUPPORT_FILES + (SUPPORT_RAW_FILE,):
        try:
            os.remove(os.path.join(d, n))
        except OSError:
            pass
    _update_meta(book_id, toc=[], chapters=0, compiled_at="", error="", status="uploaded")


def resume_interrupted() -> list:
    """開機把被重啟打斷的編譯接回去：meta 停在 compiling、但這支行程沒在跑的 → 重新 fire（從缺的章接著補，不重做）。

    2026-09-18 發 2.5.52 時三本正在編的書被 8000 重啟砍掉，得人工一本一本 POST /compile；這裡自動做。
    沒 claude CLI（機隊、NAS）什麼都不動——狀態留著，畫面照樣顯示「被中斷」，下次有 CLI 的行程再接。
    呼叫端（main 開機）只在 master 叫；dev 的書架已分 root，各接各的。
    """
    if not claude_available():
        return []
    base = root()
    if not os.path.isdir(base):
        return []
    resumed = []
    for name in sorted(os.listdir(base)):
        if not kl.is_valid_id(name) or name in _stage:
            continue
        try:
            meta = read_meta(name)
        except BookNotFound:
            continue
        if meta.get("status") != "compiling":
            continue
        _stage[name] = "接回中斷的編譯"
        fire(compile_book(name, meta.get("model") or ""), label=f"knowledge resume {name}")
        resumed.append(name)
    if resumed:
        logger.info("[knowledge] 接回 %d 本被中斷的編譯：%s", len(resumed), "、".join(resumed))
    return resumed


def start_compile(book_id: str, model: str = "", *, force: bool = False) -> dict:
    """端點呼叫：檢查沒在跑 → 標 compiling → 回 meta。真正的工作交給 `compile_book`（背景）。"""
    if book_id in _stage:
        raise BookBusy(book_id)
    if read_meta(book_id).get("status") == STATUS_PENDING:      # 先確認書在（不在丟 404）
        raise BookHasNoFile(book_id)                              # 待補：還沒 PDF，沒東西可編
    if force:
        reset_compiled(book_id)
    model = pick_model(model)
    meta = _update_meta(book_id, status="compiling", error="", model=model)
    _stage[book_id] = "準備中"        # 再佔位，不然合法 id 但資料夾不在時 _stage 會留一個永遠 409 的鬼
    return meta


async def compile_book(book_id: str, model: str) -> None:
    """三個 pass：結構 → 每章 → 骨架。失敗寫 meta.status=failed＋error；已產的章保留（重編從缺的補）。"""
    try:
        await _compile(book_id, model)
    except Exception as e:                      # noqa: BLE001 — 最後一道，狀態要落檔
        logger.exception("[knowledge] 編譯 %s 失敗：%s", book_id, e)
        try:
            _update_meta(book_id, status="failed", error=str(e)[:500])
        except BookNotFound:
            pass
    finally:
        _stage.pop(book_id, None)


async def _compile(book_id: str, model: str) -> None:
    # 圖：2026-09-18 之前上傳的書還沒抽過，編譯前補一次（已經有就直接回，不重跑）。
    # 抽不出來不該擋住編譯 —— 沒有圖的章節照樣有用。
    try:
        assets = await asyncio.to_thread(ensure_assets, book_id)
    except Exception:
        logger.exception("知識庫：補抽圖失敗 %s（編譯照跑）", book_id)
        assets = []
    meta = read_meta(book_id)
    d = book_dir(book_id)
    full_text = _read_text(os.path.join(d, FULLTEXT_FILE))
    # 每頁都有 [[p.N]] 標記，整段 strip() 永遠非空 —— 要看標記後面有沒有字
    if not any(body.strip() for _, body in kl.page_texts(full_text)):
        raise RuntimeError("這本 PDF 抽不出文字（可能是掃描圖檔）")
    page_count = int(meta.get("pages") or 0)

    # pass 1：結構（有目錄就沿用 —— 重編只補缺的）
    toc = list(meta.get("toc") or [])
    if not toc:
        _stage[book_id] = "找章節結構"
        prompt = kl.structure_prompt(kl.page_index(full_text), full_text[:kl.STRUCTURE_HEAD_CHARS], meta)
        text, err = await call_claude(prompt, model=model, cwd=d, timeout_sec=COMPILE_TIMEOUT_SEC)
        if text is None:
            raise RuntimeError(f"結構：{err}")
        toc = kl.parse_structure(text, page_count)
        if not toc:
            logger.warning("[knowledge] %s 抓不到章節結構，改用每 %d 頁一段", book_id, kl.FALLBACK_PAGES_PER_CHAPTER)
            toc = kl.fallback_boundaries(page_count)
        for c in toc:
            c["file"] = kl.chapter_filename(c["n"], c["title"])      # 檔名由我們產，不信 claude 回的路徑
        meta = _update_meta(book_id, toc=toc)

    # pass 2：每章
    chapters = kl.split_chapters(full_text, toc)
    total = len(chapters)
    for i, ch in enumerate(chapters, start=1):
        fname = toc[i - 1].get("file") or kl.chapter_filename(ch["n"], ch["title"])
        path = os.path.join(d, CHAPTERS_DIR, fname)
        if os.path.isfile(path):
            continue
        _stage[book_id] = f"第 {i}/{total} 章"
        if not ch["text"].strip():
            _write_text(path, f"# 第 {ch['n']} 章 {ch['title']}（p.{ch['start_page']}–{ch['end_page']}）\n\n（這幾頁抽不出文字）\n")
            continue
        parts = kl.chunk_text(ch["text"])
        outs = []
        for pi, part in enumerate(parts, start=1):
            if len(parts) > 1:
                _stage[book_id] = f"第 {i}/{total} 章（{pi}/{len(parts)} 段）"
            text, err = await call_claude(kl.chapter_prompt(meta, ch, part, pi, len(parts), assets=assets),
                                          model=model, cwd=d, timeout_sec=COMPILE_TIMEOUT_SEC)
            if text is None:
                raise RuntimeError(f"第 {ch['n']} 章：{err}")
            outs.append(text.strip())
        head = f"# 第 {ch['n']} 章 {ch['title']}（p.{ch['start_page']}–{ch['end_page']}）\n\n"
        body = "\n\n---\n\n".join(outs) if len(outs) > 1 else (outs[0] if outs else "")
        # AI 寫的圖說（owner 2026-09-19「可以製作圖說」）：取走那一段、蓋掉從 PDF 刮下來的原文。
        # 它沒寫或寫錯檔名就維持原文 —— 圖說不會變成空的。
        body, caps = kl.parse_captions(body, [a["name"] for a in assets])
        _write_text(path, head + body + "\n")
        fields = {"chapters": len(_chapter_rows(book_id, read_meta(book_id)))}
        if caps:
            fields["asset_captions"] = {**(read_meta(book_id).get("asset_captions") or {}), **caps}
        _update_meta(book_id, **fields)

    # pass 3：骨架（四個檔一次產；缺任何一個就重產整組）
    if _support_missing(d):
        _stage[book_id] = "整理骨架"
        rows = []
        for c in _chapter_rows(book_id, read_meta(book_id)):
            rows.append({**c, "md": _read_text(os.path.join(d, CHAPTERS_DIR, c["file"]))})
        text, err = await call_claude(kl.support_prompt(meta, rows), model=model, cwd=d,
                                      timeout_sec=COMPILE_TIMEOUT_SEC)
        if text is None:
            raise RuntimeError(f"骨架：{err}")
        files = kl.split_files(text)
        missing = [n for n in kl.SUPPORT_FILES if n not in files]
        if missing:
            # 原文留一份在書資料夾（不是支援檔、不會被讀進提示），不然「少了四個檔」根本查不出它回了什麼
            _write_text(os.path.join(d, SUPPORT_RAW_FILE), text)
            raise RuntimeError("骨架少了：" + "、".join(missing) + f"（claude 的原文存在 {SUPPORT_RAW_FILE}）")
        for name, content in files.items():
            _write_text(os.path.join(d, name), content)

    _update_meta(book_id, status="compiled", error="", compiled_at=_now_iso(),
                 chapters=len(_chapter_rows(book_id, read_meta(book_id))), model=model)


# ── 討論／整理結論 ────────────────────────────────────────────
def advisor_snapshot() -> list:
    """顧問快照的四個數（固定讀 `ADVISOR_LATEST`；讀不到就空）。"""
    try:
        with open(ADVISOR_LATEST, encoding="utf-8") as f:
            return kl.snapshot_lines(json.load(f))
    except (OSError, ValueError):
        return []


def start_chat(book_id: str, text: str) -> dict:
    """端點呼叫：追加 user 那則、標 queued；真正的工作交給 `run_chat`（背景）。"""
    if book_id in _chat_stage:
        raise BookBusy(book_id)
    msg = add_chat_message(book_id, kl.ROLE_USER, text)
    _chat_stage[book_id] = "queued"
    _chat_partial[book_id] = ""
    return msg


async def run_chat(book_id: str, model: str = "") -> None:
    """背景：組提示 → 串流叫 claude（cwd＝這本書、只准 Read）→ AI 那則寫回 chat.json。"""
    try:
        meta = read_meta(book_id)
        d = book_dir(book_id)
        chat = read_chat(book_id)
        last_user = next((m for m in reversed(chat) if m.get("role") == kl.ROLE_USER), {})
        finance = kl.has_tag(meta, kl.FINANCE_TAG)      # 只有標了「財務」的書才走顧問角色＋帶數字
        prompt = kl.chat_prompt(
            meta,
            skill=_read_text(os.path.join(d, "SKILL.md")),
            cheatsheet=_read_text(os.path.join(d, "cheatsheet.md")),
            chapters=_chapter_rows(book_id, meta),
            notes=_read_text(os.path.join(d, NOTES_FILE)),
            conclusion=_read_text(os.path.join(d, CONCLUSION_FILE)),
            snapshot=advisor_snapshot() if finance else [],
            history=chat[:-1] if chat and chat[-1] is last_user else chat,
            text=last_user.get("text") or "",
            finance=finance,
            extend=kl.extend_digest(read_extend(book_id)),
        )

        def _partial(s: str) -> None:
            _chat_partial[book_id] = s

        def _stage_cb(s: str) -> None:
            _chat_stage[book_id] = s

        text, err = await call_claude(prompt, model=pick_model(model), cwd=d, allowed_tools="Read",
                                      timeout_sec=CHAT_TIMEOUT_SEC, on_partial=_partial, on_stage=_stage_cb)
        reply = (text or "").strip() if text is not None else "（叫不動 claude：" + (err or "未知錯誤") + "）"
        add_chat_message(book_id, kl.ROLE_AI, reply or "（沒有回覆）")
    except BookNotFound:
        pass
    finally:
        _chat_partial.pop(book_id, None)
        _chat_stage.pop(book_id, None)


def start_conclude(book_id: str) -> None:
    if book_id in _concluding:
        raise BookBusy(book_id)
    if not read_chat(book_id):
        raise ValueError("還沒有討論，沒東西可以整理")
    _concluding.add(book_id)


async def run_conclude(book_id: str, model: str = "") -> None:
    """背景：整段討論＋既有結論 → 3–7 條 → 追加 `結論.md`。失敗就追加一段錯誤（讓人看得到）。"""
    try:
        meta = read_meta(book_id)
        d = book_dir(book_id)
        existing = _read_text(os.path.join(d, CONCLUSION_FILE))
        prompt = kl.conclude_prompt(meta, read_chat(book_id), existing)
        text, err = await call_claude(prompt, model=pick_model(model), cwd=d, allowed_tools="Read",
                                      timeout_sec=CHAT_TIMEOUT_SEC)
        body = (text or "").strip() if text is not None else "（整理結論失敗：" + (err or "未知錯誤") + "）"
        append_conclusion(book_id, body or "（沒有回覆）")
    except BookNotFound:
        pass
    finally:
        _concluding.discard(book_id)


# ── 研究助理（docs/KNOWLEDGE_BASE_PLAN.md §9）────────────────
# 定期／手動去網路上找跟這本書有關的新研究，整理成 `延伸.md` 一則一行。
#
# 🔴 安全：這一發是**唯一**會讀網頁的地方，網頁內容是不可信輸入（prompt injection）。
#    因此：(1) 只給 WebSearch／WebFetch，不給 Read／Bash／Write；(2) cwd 用臨時空目錄，
#    讓它連這本書的檔案都看不到，跑完刪掉；(3) 回來的東西只當資料寫檔，不執行、不寫設定。
_EXTEND_TOOLS = "WebSearch,WebFetch"
_EXTEND_HEAD = ("# 延伸（研究助理找到的）\n"
                "# 一則一行，欄位：" + "｜".join(kl.EXTEND_FIELDS) + "\n"
                "# 這些是網路上的東西，不是書的作者說的。\n")


def read_extend(book_id: str) -> list:
    """`延伸.md` → 清單（舊到新）。"""
    return kl.parse_extend_md(_read_text(os.path.join(book_dir(book_id), EXTEND_FILE)))


def _write_extend(book_id: str, items: list) -> None:
    body = "\n".join(kl.extend_line(i) for i in items)
    _write_text(os.path.join(book_dir(book_id), EXTEND_FILE), _EXTEND_HEAD + body + ("\n" if body else ""))


def _extend_counts(d: str) -> tuple:
    """`(總則數, 還沒評分的則數)`。書架清單每本都會叫一次，所以只讀檔、不叫 claude。"""
    items = kl.parse_extend_md(_read_text(os.path.join(d, EXTEND_FILE)))
    return len(items), sum(1 for i in items if not i.get("rating"))


def extend_state(book_id: str) -> dict:
    """「延伸」分頁那包：整份清單＋現在有沒有在找＋上一輪沒收到東西時的那句人話。"""
    return {"items": read_extend(book_id), "stage": _extending.get(book_id, ""),
            "note": read_meta(book_id).get("extend_note") or ""}


def rate_extend(book_id: str, n: int, rating: str) -> dict:
    """給第 n 則評「有用／沒用」。n 是**檔內流水號**，不是第幾筆。"""
    if rating not in kl.EXTEND_RATINGS:
        raise ValueError("評分只能是 useful／useless／空白")
    items = read_extend(book_id)
    hit = next((i for i in items if i.get("n") == int(n)), None)
    if hit is None:
        raise ExtendNotFound(f"{book_id}#{n}")
    hit["rating"] = rating
    _write_extend(book_id, items)
    return hit


_PUBLIC_DOCS = {"conclusion": CONCLUSION_FILE, "notes": NOTES_FILE,
                "skill": "SKILL.md", "cheatsheet": "cheatsheet.md"}


def read_doc(book_id: str, which: str) -> str:
    """讀這本書的某一份文件（給 knowledge_share 用）。名字只認白名單，不拼外面來的字。"""
    fname = _PUBLIC_DOCS.get(str(which or ""))
    if not fname:
        raise ChapterNotFound(which)
    return _read_text(os.path.join(book_dir(book_id), fname))


def chapter_rows_public(book_id: str) -> list:
    """章節清單（給 knowledge_share 用）。"""
    return _chapter_rows(book_id, read_meta(book_id))


def now_iso() -> str:
    """給 services/knowledge_share.py 用（那支不該碰私有的 `_now_iso`）。"""
    return _now_iso()


def update_meta_share(book_id: str, share: Optional[dict]) -> None:
    """開關公開分享（§9.9）。`None`＝關掉，連鍵一起拿掉。

    🔴 要拿鍵所以走不了 `_update_meta`，但鎖一樣要拿：這是讀-改-寫，跟編譯／補圖那條
    （別的執行緒的 `_update_meta`）對撞會把對方剛寫的鍵（asset_captions、toc…）或這裡的
    share 塊靜默蓋掉（2026-09-19 /polish BUG-6：第一版 docstring 說有鎖、實作沒有）。
    """
    with _meta_lock(book_id):
        meta = read_meta(book_id)
        meta.pop("share", None)
        if share:
            meta["share"] = share
        write_meta(book_id, meta)


def set_watch_last(book_id: str, week: str) -> None:
    """研究助理跑過這本了（`2026-W38`）—— 同一週不再重跑（services/knowledge_watch.py）。"""
    _update_meta(book_id, watch_last=week)


def watch_last(book_id: str) -> str:
    return read_meta(book_id).get("watch_last") or ""


def start_extend(book_id: str, url: str = "") -> None:
    """端點呼叫：標 queued，真正的工作交給 `run_extend`（背景）。`url` 有給＝手動收錄那一篇。"""
    if book_id in _extending:
        raise BookBusy(book_id)
    if url and not url.strip().lower().startswith(("http://", "https://")):
        raise ValueError("網址要以 http:// 或 https:// 開頭")       # 壞網址跟書在不在無關，先擋
    book_dir(book_id)       # 再確認書在（不在丟 404）—— 不帶網址那條原本完全不碰書，端點回 200 才在背景 BookNotFound
    # 先比對再花錢：同一篇再貼一次的話，叫 claude 讀完兩分鐘之後也只是被丟掉
    if url and kl.norm_url(url) in {kl.norm_url(i.get("url")) for i in read_extend(book_id)}:
        raise ValueError("這篇已經收過了")
    _extending[book_id] = "queued"


async def run_extend(book_id: str, model: str = "", url: str = "") -> None:
    """背景：組提示 → 叫 claude 上網 → 新的幾則追加到 `延伸.md`（重複的網址不會再進來）。"""
    tmp = ""
    try:
        meta = read_meta(book_id)
        d = book_dir(book_id)
        old = read_extend(book_id)
        conclusion = _read_text(os.path.join(d, CONCLUSION_FILE))
        if url:
            prompt = kl.extend_one_prompt(url, meta, conclusion=conclusion)
        else:
            prompt = kl.extend_prompt(
                meta,
                skill=_read_text(os.path.join(d, "SKILL.md")),
                conclusion=conclusion,
                notes=_read_text(os.path.join(d, NOTES_FILE)),
                focus=meta.get("focus") or "",
                rated=old,                              # 他評過「有用／沒用」的那幾則當正反例
                seen_urls=[i.get("url") for i in old],
            )

        def _stage_cb(s: str) -> None:
            _extending[book_id] = s or "searching"

        _extending[book_id] = "searching"
        # 臨時空目錄：跑這一發時它連書的資料夾都看不到（跟 §9 的安全規則一致）
        tmp = tempfile.mkdtemp(prefix="kb_extend_")
        text, err = await call_claude(prompt, model=pick_model(model), cwd=tmp,
                                      allowed_tools=_EXTEND_TOOLS, timeout_sec=EXTEND_TIMEOUT_SEC,
                                      on_stage=_stage_cb)
        if text is None:
            _extend_note(book_id, "（找資料失敗：" + (err or "未知錯誤") + "）")
            return
        today = datetime.now(_TW).strftime("%Y-%m-%d")
        seen = [i.get("url") for i in old]
        if url:
            item, why = kl.parse_extend_one(text, today, n=kl.next_extend_n(old))
            if item is None:
                _extend_note(book_id, "（這篇收不進來：" + why + "）")
                return
            fresh = [] if kl.norm_url(item["url"]) in {kl.norm_url(u) for u in seen} else [item]
        else:
            fresh = kl.parse_extend(text, today, start_n=kl.next_extend_n(old), seen_urls=seen)
        if fresh:
            # 🔴 寫入前**重讀**，不是用開頭讀的 `old`：claude 跑了幾分鐘，期間他在畫面上按的
            # 「有用／沒用」（rate_extend）已經寫進檔了，拿 old 蓋回去等於把那幾下洗掉、而且沒有徵兆
            # （2026-09-19 /polish BUG-3）。同一本同時只會有一輪（_extending 擋著），流水號不會撞。
            _write_extend(book_id, read_extend(book_id) + fresh)
        _update_meta(book_id, extended_at=_now_iso(),
                     extend_note="" if fresh else "（這一輪沒有找到新的東西）")
    except BookNotFound:
        pass
    finally:
        _extending.pop(book_id, None)
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def _extend_note(book_id: str, msg: str) -> None:
    """找不到東西時給一句人話 —— 不然畫面上只是安靜地什麼都沒發生。"""
    _update_meta(book_id, extend_note=msg, extended_at=_now_iso())


__all__ = [
    "ADVISOR_LATEST", "BookBusy", "BookNotFound", "ChapterNotFound", "ExtendNotFound", "MAX_UPLOAD_BYTES", "add_chat_message",
    "advisor_snapshot", "append_conclusion", "book_detail", "book_dir", "chat_state", "compile_book", "delete_book",
    "extend_state", "extract_text", "is_pdf", "list_books", "pick_model", "rate_extend", "read_chapter", "read_chat",
    "read_extend", "read_meta", "root", "run_chat", "run_conclude", "run_extend", "save_images", "save_upload",
    "set_watch_last",
    "start_chat",
    "asset_path", "assets_dir", "ensure_assets", "extract_all", "list_assets",
    "start_compile", "start_conclude", "start_extend", "update_book", "watch_last", "write_conclusion", "write_meta",
    "write_notes",
]
