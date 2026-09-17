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

import json
import logging
import os
import shutil
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from core import knowledge_logic as kl
from services.knowledge_claude import call_claude

logger = logging.getLogger(__name__)

DEFAULT_ROOT = r"D:\Originsun-Knowledge\books"
#: 顧問快照（fetch_finance.py 存的）—— 固定位置，不從書架根目錄推（2026-09-18 書架搬到 D:\Originsun-Knowledge 之後兩者不同層了）
ADVISOR_LATEST = r"D:\Originsun-Advisor\latest.json"
#: 上傳上限（PDF）
MAX_UPLOAD_BYTES = 300 * 1024 * 1024
#: 每次 claude 呼叫的逾時：編譯（一章可能上萬字）與討論／整理結論
COMPILE_TIMEOUT_SEC = 20 * 60
CHAT_TIMEOUT_SEC = 10 * 60
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
CHAPTERS_DIR = "chapters"
SUPPORT_RAW_FILE = "_support_raw.txt"     # 骨架 pass 解不出四個檔時留的原文（除錯用）

_TW = ZoneInfo("Asia/Taipei")

# ── 執行中狀態（只活在記憶體）──
_stage: dict = {}            # id → 編譯進度字串（'第 3/12 章' 之類）；不在裡面＝沒在編
_chat_partial: dict = {}     # id → 這一輪串到目前的回覆
_chat_stage: dict = {}       # id → 'queued'｜'running'；不在裡面＝沒在跑
_concluding: set = set()     # 正在整理結論的 id


class BookNotFound(Exception):
    """id 不合法或資料夾不存在（端點回 404）。"""


class ChapterNotFound(Exception):
    """書在、那一章不在（端點回 404，訊息要講是章不是書）。"""


class BookBusy(Exception):
    """這本書正在編譯／討論中（端點回 409）。"""


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


def _update_meta(book_id: str, **fields) -> dict:
    meta = read_meta(book_id)
    meta.update(fields)
    write_meta(book_id, meta)
    return meta


# ── 上傳 ─────────────────────────────────────────────────────
def is_pdf(content: bytes) -> bool:
    """看檔頭不看副檔名。"""
    return bool(content) and content[:4] == b"%PDF"


def extract_text(content: bytes) -> tuple:
    """pymupdf 抽全文：回 `(text, pages)`，每頁前加 `[[p.N]]`。同步、吃 CPU —— 呼叫端丟 to_thread。"""
    try:
        import pymupdf as _mu
    except ImportError:  # 舊版只有 fitz（會印 deprecation warning）
        import fitz as _mu
    doc = _mu.open(stream=content, filetype="pdf")
    try:
        parts = []
        for i, page in enumerate(doc, start=1):
            parts.append(kl.PAGE_MARK.format(n=i) + "\n" + (page.get_text() or "") + "\n")
        return "".join(parts), doc.page_count
    finally:
        doc.close()


def save_upload(content: bytes, source_name: str, title: str = "") -> dict:
    """存原檔＋全文＋meta；回 meta。文字抽取在這裡做（呼叫端用 to_thread 包）。"""
    text, pages = extract_text(content)
    book_id = kl.new_id()
    d = book_dir(book_id, must_exist=False)
    os.makedirs(os.path.join(d, CHAPTERS_DIR), exist_ok=True)
    with open(os.path.join(d, SOURCE_FILE), "wb") as f:
        f.write(content)
    _write_text(os.path.join(d, FULLTEXT_FILE), text)
    base = os.path.splitext(os.path.basename(str(source_name or "").replace("\\", "/")))[0]
    meta = {
        "id": book_id,
        "title": (title or "").strip() or base or "未命名",
        "author": "",
        "source_name": os.path.basename(str(source_name or "").replace("\\", "/")),
        "pages": pages,
        "chars": len(text),
        "uploaded_at": _now_iso(),
        "status": "uploaded",
        "error": "",
        "compiled_at": "",
        "chapters": 0,
        "toc": [],
        "model": "",
        "tags": [],
    }
    write_meta(book_id, meta)
    return meta


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
            out.append({"n": c.get("n"), "title": c.get("title") or "", "file": fname})
    return out


def _summary(meta: dict, book_id: str) -> dict:
    d = book_dir(book_id)
    status = _effective_status(meta, book_id)
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
    """第 n 章的 md（照 toc 找檔，不拼使用者給的名字）。"""
    meta = read_meta(book_id)
    for c in _chapter_rows(book_id, meta):
        if int(c.get("n") or 0) == int(n):
            return {**c, "md": _read_text(os.path.join(book_dir(book_id), CHAPTERS_DIR, c["file"]))}
    raise ChapterNotFound(n)


def update_book(book_id: str, *, title: Optional[str] = None, author: Optional[str] = None,
                tags: Optional[list] = None) -> dict:
    fields = {}
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


def start_compile(book_id: str, model: str = "", *, force: bool = False) -> dict:
    """端點呼叫：檢查沒在跑 → 標 compiling → 回 meta。真正的工作交給 `compile_book`（背景）。"""
    if book_id in _stage:
        raise BookBusy(book_id)
    if force:
        reset_compiled(book_id)
    model = pick_model(model)
    meta = _update_meta(book_id, status="compiling", error="", model=model)   # 先確認書在（不在丟 404）
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
            text, err = await call_claude(kl.chapter_prompt(meta, ch, part, pi, len(parts)),
                                          model=model, cwd=d, timeout_sec=COMPILE_TIMEOUT_SEC)
            if text is None:
                raise RuntimeError(f"第 {ch['n']} 章：{err}")
            outs.append(text.strip())
        head = f"# 第 {ch['n']} 章 {ch['title']}（p.{ch['start_page']}–{ch['end_page']}）\n\n"
        body = "\n\n---\n\n".join(outs) if len(outs) > 1 else (outs[0] if outs else "")
        _write_text(path, head + body + "\n")
        _update_meta(book_id, chapters=len(_chapter_rows(book_id, read_meta(book_id))))

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


__all__ = [
    "ADVISOR_LATEST", "BookBusy", "BookNotFound", "MAX_UPLOAD_BYTES", "add_chat_message", "advisor_snapshot", "append_conclusion",
    "book_detail", "book_dir", "chat_state", "compile_book", "delete_book", "extract_text", "is_pdf", "list_books",
    "pick_model", "read_chapter", "read_chat", "read_meta", "root", "run_chat", "run_conclude", "save_upload",
    "start_chat", "start_compile", "start_conclude", "update_book", "write_conclusion", "write_meta", "write_notes",
]
