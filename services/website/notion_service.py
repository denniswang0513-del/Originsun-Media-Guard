"""services/website/notion_service.py
---
Notion 「官網內容上架管理表」→ Astro posts.json + categories.json 同步邏輯。

職責：
- 撈 Notion database schema → 產出 categories.json（動態分類清單）
- 撈 Notion pages（filter: 製作進度 = 上架官網, sort: last_edited_time desc）
- 每篇文章展開 page blocks → 轉成 IPost.body[] (PostBlock[])
- 圖片處理：external URL 原樣保留；file URL（Notion 暫存、1hr 失效）下載到
  website/public/notion-media/{page_id}/{idx}.{ext} 並改寫 src 成相對路徑

決策記錄（對照 docs/WEBSITE_ARCHITECTURE.md M-E-8）：
- slug = Notion「官網編號」(unique_id/auto_increment_id) 純數字字串，永遠不變
- 摘要 = body 第一個 paragraph 前 100 字
- 封面 = Notion page cover；缺則交給 Astro 端用 placeholder
- 分類對應 = Notion「分類」multi_select 第一個非「待分類」值；Pre-production 等
  新分類動態支援（見 CATEGORY_LABEL_ZH_MAP fallback）

所有函式 async，用 httpx.AsyncClient（單次 sync 共用 client 復用 TCP 連線）。
Notion API rate limit: 3 req/s 平均；每篇文章大約 2 req（retrieve page + list blocks），
~30 篇以內一次 sync 會在 10-20 秒內完成。
"""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
REQUEST_TIMEOUT = 30.0
MEDIA_DOWNLOAD_TIMEOUT = 60.0

# Notion database column names (中文欄位名是 source of truth — Notion 端可改，
# 但改了也要同步改這些常數)
PROP_TITLE = "官網標題"
PROP_UNIQUE_ID = "官網編號"
PROP_CATEGORY = "分類"
PROP_STATUS = "製作進度"

# 已知 Notion multi_select option name → 中文 label 對照。Notion 出現新值時，
# _build_category_entry() 會 fallback 用 option.name 當中文 label 並回報 warning。
CATEGORY_LABEL_ZH_MAP: dict[str, str] = {
    "Documentary": "紀錄片",
    "Post-production": "後期製作",
    "Pre-production": "前期製作",
    "Project Review": "案件回顧",
    "Workflow": "工作流程",
}

# Notion「分類」值為「待分類」時，文章不公開（跳過同步）。
SKIP_CATEGORY_VALUES = {"待分類"}

# 哪些「製作進度」值算已公開。目前只有 `上架官網`（使用者決策）。
PUBLISHED_STATUSES = {"上架官網"}

EXCERPT_MAX_CHARS = 100

# 容錯：使用者可能把整段 Notion URL 貼進 database_id 欄位，或殘留前後空白、
# 中間有 dash。Notion API 接受 dashed/undashed 32-hex，但 URL/空白會直接 400。
_NOTION_ID_PATTERN = re.compile(
    r"([0-9a-f]{8})-?([0-9a-f]{4})-?([0-9a-f]{4})-?([0-9a-f]{4})-?([0-9a-f]{12})",
    re.IGNORECASE,
)


def _normalize_notion_id(raw: str) -> str:
    """從各種使用者輸入抽出乾淨的 32-char hex ID。

    - 'https://notion.so/workspace/Page-58dd13b5f07d45e6ad7c7732fc6e0add?v=...' → 抽 ID
    - '58dd13b5-f07d-45e6-ad7c-7732fc6e0add' → 去 dash
    - '  58dd13b5f07d45e6ad7c7732fc6e0add  ' → trim
    - 完全不是 ID 格式 → 原樣回傳（讓下游以 invalid_request_url 失敗，但訊息更清楚）
    """
    if not raw:
        return ""
    s = raw.strip()
    m = _NOTION_ID_PATTERN.search(s)
    if m:
        return "".join(m.groups()).lower()
    return s

# 支援的 Notion block 類型（其餘略過）
SUPPORTED_BLOCK_TYPES = {
    "paragraph", "heading_1", "heading_2", "heading_3",
    "bulleted_list_item", "numbered_list_item",
    "quote", "callout", "image", "video",
    "bookmark", "embed", "link_preview",
}

# 單純 rich_text 包出來的 block 用 lookup 避免複製貼上四個 branch。
# 這些 block 都會保留 inline 連結（rich_text 裡的 href 轉成 markdown-like 格式）。
_SIMPLE_TEXT_BLOCK_BUILDERS: dict[str, Callable[[str], dict]] = {
    "paragraph": lambda t: {"type": "paragraph", "text": t},
    "quote": lambda t: {"type": "quote", "text": t},
    "bulleted_list_item": lambda t: {"type": "list", "items": [t]},
    "numbered_list_item": lambda t: {"type": "list", "items": [t], "ordered": True},
}

_HEADING_LEVELS: dict[str, int] = {"heading_1": 2, "heading_2": 2, "heading_3": 3}


@dataclass
class BlockContext:
    """Per-page invariants passed through block processing."""
    client: httpx.AsyncClient
    media_root: Path
    page_slug: str
    warnings: list[str]
    dry_run: bool


# ══════════════════════════════════════════════════════════
# HTTP 低階包裝
# ══════════════════════════════════════════════════════════

def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


async def _notion_get(
    client: httpx.AsyncClient,
    path: str,
    token: str,
    params: dict | None = None,
) -> dict:
    resp = await client.get(f"{NOTION_API}{path}", headers=_headers(token), params=params)
    resp.raise_for_status()
    return resp.json()


async def _notion_post(
    client: httpx.AsyncClient,
    path: str,
    token: str,
    body: dict,
) -> dict:
    resp = await client.post(f"{NOTION_API}{path}", headers=_headers(token), json=body)
    resp.raise_for_status()
    return resp.json()


# ══════════════════════════════════════════════════════════
# 連線測試
# ══════════════════════════════════════════════════════════

async def check_connection(token: str, db_id: str) -> dict:
    """呼叫 GET /databases/{id} 驗證 token + db_id 皆正確。"""
    if not token or not db_id:
        return {"ok": False, "error": "missing token or database_id"}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            data = await _notion_get(client, f"/databases/{db_id}", token)
            title_rt = data.get("title") or []
            db_title = "".join(rt.get("plain_text", "") for rt in title_rt)
            return {"ok": True, "title": db_title or "(untitled)"}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ══════════════════════════════════════════════════════════
# Rich text / property extraction helpers
# ══════════════════════════════════════════════════════════

def _plain_text(rich_text: list[dict] | None) -> str:
    if not rich_text:
        return ""
    return "".join(rt.get("plain_text", "") for rt in rich_text)


def _rich_text_md(rich_text: list[dict] | None) -> str:
    """轉成 markdown-like 字串保留 inline 連結。Astro 端用 regex 還原成 <a> 標籤。

    範例：[{plain_text: "see ", href: null}, {plain_text: "this", href: "https://x"}]
       → "see [this](https://x)"

    Heading 不需要呼叫此函式（Astro 渲染 heading 時不解析 markdown）；
    paragraph / quote / list / callout 用得到。
    """
    if not rich_text:
        return ""
    parts = []
    for rt in rich_text:
        text = rt.get("plain_text", "")
        href = rt.get("href")
        if href and text:
            # 防止 markdown 衝突：清理 ] ) 等字元（罕見，但安全處理）
            safe_text = text.replace("[", "(").replace("]", ")")
            parts.append(f"[{safe_text}]({href})")
        else:
            parts.append(text)
    return "".join(parts)


def _prop_title(page_properties: dict, key: str = PROP_TITLE) -> str:
    prop = page_properties.get(key) or {}
    return _plain_text(prop.get("title") or [])


def _prop_unique_id(page_properties: dict, key: str = PROP_UNIQUE_ID) -> int | None:
    prop = page_properties.get(key) or {}
    unique_id = prop.get("unique_id") or {}
    num = unique_id.get("number")
    return num if isinstance(num, int) else None


def _prop_select_value(page_properties: dict, key: str) -> str:
    prop = page_properties.get(key) or {}
    sel = prop.get("select") or {}
    return sel.get("name") or ""


def _prop_multi_select(page_properties: dict, key: str) -> list[str]:
    prop = page_properties.get(key) or {}
    items = prop.get("multi_select") or []
    return [o.get("name", "") for o in items if o.get("name")]


def _page_cover_url(page: dict) -> str | None:
    cover = page.get("cover") or {}
    ctype = cover.get("type")
    if ctype == "external":
        return (cover.get("external") or {}).get("url")
    if ctype == "file":
        return (cover.get("file") or {}).get("url")
    return None


# ══════════════════════════════════════════════════════════
# Database schema → categories
# ══════════════════════════════════════════════════════════

def _category_slug(name: str) -> str:
    """Notion option name → URL-safe category slug.

    'Post-production' → 'post-production'
    '紀錄片' → '紀錄片'（中文保留，URL 可 encode）
    """
    if not name:
        return "other"
    s = name.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    # 一-鿿 = CJK Unified Ideographs range (preserve Chinese chars)
    s = re.sub(r"[^a-z0-9一-鿿\-]", "", s)
    return s or "other"


def _build_category_entry(option: dict, warnings: list[str]) -> dict:
    """Notion option → 我們的 category dict。
    沒有中文對照時 warning + 中英都用英文名當 fallback。
    """
    name = option.get("name", "") or "other"
    slug = _category_slug(name)
    if name in CATEGORY_LABEL_ZH_MAP:
        label_zh = CATEGORY_LABEL_ZH_MAP[name]
    else:
        label_zh = name
        warnings.append(f"新分類 '{name}' 尚未設定中文對照，將用原名顯示")
    return {
        "id": slug,
        "name": name,
        "label_zh": label_zh,
        "label_en": name,
        "color": option.get("color") or "default",
        "count": 0,  # 由 caller 後續累加
    }


async def fetch_category_schema(
    client: httpx.AsyncClient,
    token: str,
    db_id: str,
) -> tuple[list[dict], list[str]]:
    """撈 database schema 中「分類」multi_select 的所有 options。

    回傳：(categories, warnings)
    - categories: [{id, name, label_zh, label_en, color, count=0}, ...]
      已排除 SKIP_CATEGORY_VALUES（如「待分類」）
    - warnings: 新分類沒對照等警告
    """
    warnings: list[str] = []
    data = await _notion_get(client, f"/databases/{db_id}", token)
    props = data.get("properties") or {}
    category_prop = props.get(PROP_CATEGORY) or {}
    if category_prop.get("type") != "multi_select":
        warnings.append("Notion 表沒有『分類』multi_select 欄位")
        return [], warnings

    options = (category_prop.get("multi_select") or {}).get("options") or []
    result = []
    for opt in options:
        if opt.get("name") in SKIP_CATEGORY_VALUES:
            continue
        result.append(_build_category_entry(opt, warnings))
    return result, warnings


# ══════════════════════════════════════════════════════════
# Query pages
# ══════════════════════════════════════════════════════════

async def _paginate(
    fetch_page: Callable[[str | None], Awaitable[dict]],
) -> list[dict]:
    """Generic Notion paginator. `fetch_page(cursor)` returns a Notion list
    response (keys: results / has_more / next_cursor)."""
    all_results: list[dict] = []
    cursor: str | None = None
    while True:
        data = await fetch_page(cursor)
        all_results.extend(data.get("results") or [])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return all_results


async def query_published_pages(
    client: httpx.AsyncClient,
    token: str,
    db_id: str,
) -> list[dict]:
    """撈所有『製作進度 ∈ PUBLISHED_STATUSES』的 page，照 last_edited_time 降序。"""
    filter_clauses = [
        {"property": PROP_STATUS, "select": {"equals": s}} for s in PUBLISHED_STATUSES
    ]

    async def _fetch(cursor: str | None) -> dict:
        body: dict[str, Any] = {
            "filter": {"or": filter_clauses} if len(filter_clauses) > 1 else filter_clauses[0],
            "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
            "page_size": 100,
        }
        if cursor:
            body["start_cursor"] = cursor
        return await _notion_post(client, f"/databases/{db_id}/query", token, body)

    return await _paginate(_fetch)


async def list_block_children(
    client: httpx.AsyncClient,
    token: str,
    block_id: str,
) -> list[dict]:
    """撈某個 block 底下所有 children block。Nested children（e.g. toggle 內）
    目前不遞迴展開（避免 API 風暴），MVP 以 top-level 為主。
    """
    async def _fetch(cursor: str | None) -> dict:
        params: dict[str, Any] = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        return await _notion_get(client, f"/blocks/{block_id}/children", token, params=params)

    return await _paginate(_fetch)


# ══════════════════════════════════════════════════════════
# Block → PostBlock
# ══════════════════════════════════════════════════════════

_YT_PATTERNS = [
    re.compile(r"youtu\.be/([A-Za-z0-9_\-]{6,})"),
    re.compile(r"youtube\.com/watch\?v=([A-Za-z0-9_\-]{6,})"),
    re.compile(r"youtube\.com/embed/([A-Za-z0-9_\-]{6,})"),
    re.compile(r"youtube\.com/shorts/([A-Za-z0-9_\-]{6,})"),
]


def _extract_youtube_id(url: str) -> str | None:
    for pat in _YT_PATTERNS:
        m = pat.search(url or "")
        if m:
            return m.group(1)
    return None


def _image_block_url(image_block: dict) -> tuple[str, bool]:
    """回 (url, is_notion_file)。is_notion_file=True 時 URL 會 1hr 後失效。"""
    itype = image_block.get("type")
    if itype == "external":
        return (image_block.get("external") or {}).get("url", ""), False
    if itype == "file":
        return (image_block.get("file") or {}).get("url", ""), True
    return "", False


async def _download_media(
    client: httpx.AsyncClient,
    url: str,
    dest_dir: Path,
    name_hint: str,
) -> str | None:
    """下載 Notion file URL 到 dest_dir/{name_hint}.{ext}，回傳相對 /notion-media/ 路徑。

    失敗回 None（caller 負責 fallback 保留原 URL 並記 warning）。
    """
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        # 先 HEAD 問副檔名；Notion file URL 常帶 X-Amz-* query 沒副檔名
        async with client.stream("GET", url, timeout=MEDIA_DOWNLOAD_TIMEOUT) as resp:
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "").split(";")[0].strip()
            ext = mimetypes.guess_extension(ct) or ".bin"
            if ext == ".jpe":
                ext = ".jpg"
            dest_file = dest_dir / f"{name_hint}{ext}"
            with open(dest_file, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
        # dest_dir = .../public/notion-media/{slug}; parent.parent = .../public
        # → 相對路徑 = notion-media/{slug}/{name}.{ext} → URL: /notion-media/{slug}/{name}.{ext}
        # （Astro 把 public/ 整個 serve 在 root）
        rel = dest_file.relative_to(dest_dir.parent.parent)
        return "/" + str(rel).replace("\\", "/")
    except Exception as e:
        logger.warning("[notion-media] download failed for %s: %s", url, e)
        return None


async def _image_to_post_block(
    img: dict, block_idx: int, ctx: BlockContext,
) -> dict | None:
    url, is_notion_file = _image_block_url(img)
    if not url:
        return None
    caption = _plain_text(img.get("caption") or [])
    if is_notion_file:
        if ctx.dry_run:
            ctx.warnings.append(
                f"文章 #{ctx.page_slug}：包含 Notion 上傳圖片（正式 sync 時會下載到本機）"
            )
        else:
            media_dir = ctx.media_root / ctx.page_slug
            local = await _download_media(
                ctx.client, url, media_dir, f"block-{block_idx:03d}"
            )
            if local:
                url = local
            else:
                ctx.warnings.append(
                    f"文章 #{ctx.page_slug}：圖片下載失敗，保留 Notion URL（~1hr 後會失效）"
                )
    return {"type": "image", "src": url, "caption": caption or None, "width": "content"}


def _video_to_post_block(video: dict, ctx: BlockContext) -> dict | None:
    if video.get("type") == "external":
        url = (video.get("external") or {}).get("url", "")
        yt_id = _extract_youtube_id(url)
        if yt_id:
            caption = _plain_text(video.get("caption") or [])
            return {
                "type": "video",
                "youtube_id": yt_id,
                "caption": caption or None,
                "width": "content",
            }
    # 內建 file video 暫不支援（要 rehost 很大）
    ctx.warnings.append(f"文章 #{ctx.page_slug}：影片非 YouTube 外部連結，暫不支援")
    return None


def _callout_to_post_block(callout: dict) -> dict | None:
    emoji = (callout.get("icon") or {}).get("emoji", "")
    text = _rich_text_md(callout.get("rich_text"))
    merged = f"{emoji} {text}".strip() if emoji else text
    return {"type": "paragraph", "text": merged} if merged else None


def _link_block_to_post_block(block: dict, btype: str) -> dict | None:
    """bookmark / embed / link_preview → 渲染成 paragraph with markdown link。

    Notion 三種「外部資源」block 結構幾乎一樣：都有 url，bookmark 有 caption。
    全部 normalize 成 [caption-or-url](url) 的 paragraph，Astro 會解析成 <a>。
    """
    data = block.get(btype) or {}
    url = data.get("url", "").strip()
    if not url:
        return None
    caption = _plain_text(data.get("caption") or [])
    label = caption.strip() or url
    safe_label = label.replace("[", "(").replace("]", ")")
    return {"type": "paragraph", "text": f"[{safe_label}]({url})"}


async def _block_to_post_block(
    block: dict, block_idx: int, ctx: BlockContext,
) -> dict | None:
    """單一 Notion block → PostBlock dict（或 None 表示跳過）。"""
    btype = block.get("type")
    if btype not in SUPPORTED_BLOCK_TYPES:
        return None

    builder = _SIMPLE_TEXT_BLOCK_BUILDERS.get(btype)
    if builder is not None:
        # 用 _rich_text_md 保留 inline 連結（href 變成 [text](url)）
        text = _rich_text_md(block[btype].get("rich_text"))
        return builder(text) if text else None

    # heading_1 映射到 level 2 是因為 PostBlock 不支援 h1（h1 留給頁面 title）
    # heading 不解析 markdown link，內嵌連結會以純文字呈現
    heading_level = _HEADING_LEVELS.get(btype)
    if heading_level is not None:
        return {
            "type": "heading", "level": heading_level,
            "text": _plain_text(block[btype].get("rich_text")),
        }

    if btype == "callout":
        return _callout_to_post_block(block["callout"])
    if btype == "image":
        return await _image_to_post_block(block["image"], block_idx, ctx)
    if btype == "video":
        return _video_to_post_block(block["video"], ctx)
    if btype in ("bookmark", "embed", "link_preview"):
        return _link_block_to_post_block(block, btype)
    return None


def _merge_consecutive_lists(post_blocks: list[dict]) -> list[dict]:
    """Notion 每個 bullet/numbered item 是獨立 block。合併連續同類型成單一 list。"""
    merged: list[dict] = []
    for blk in post_blocks:
        if (
            merged
            and blk.get("type") == "list"
            and merged[-1].get("type") == "list"
            and bool(merged[-1].get("ordered")) == bool(blk.get("ordered"))
        ):
            merged[-1]["items"].extend(blk["items"])
        else:
            merged.append(blk)
    return merged


def _extract_excerpt(post_blocks: list[dict], limit: int = EXCERPT_MAX_CHARS) -> str:
    """取第一個 paragraph 的 text，截 limit 字。"""
    for blk in post_blocks:
        if blk.get("type") == "paragraph":
            text = (blk.get("text") or "").strip()
            if text:
                return text[:limit] + ("…" if len(text) > limit else "")
    return ""


# Originsun 文章模板：以特定 heading 文字標記封面區與正文起點
COVER_HEADING_TEXTS = {"封面圖", "封面", "Cover", "cover"}
BODY_START_HEADING_TEXTS = {"內文與內文配圖", "內文", "正文", "Body", "Content"}


def _split_cover_and_body(
    blocks: list[dict],
) -> tuple[str | None, list[dict]]:
    """根據 Originsun 文章模板的 section markers 切出封面與正文。

    模板約定：
      heading "封面圖"           ← 標記封面區（區內第一張 image 當 cover_url）
      image                       ← 取為 cover
      heading "內文與內文配圖"   ← 標記正文起點
      ...                          ← 正文內容

    沒任何 marker 的文章 → 視為自由格式，全部 block 當正文（cover_url=None）。
    只有「封面圖」沒有「內文與內文配圖」 → 圖後內容自動視為正文。
    區段標題本身不進 body（避免重複看到「封面圖」「內文與內文配圖」字樣）。
    """
    cover_url: str | None = None
    body: list[dict] = []
    section = "pre"  # pre / cover / post_cover / body
    cover_taken = False

    for blk in blocks:
        if blk.get("type") == "heading":
            text = (blk.get("text") or "").strip()
            if text in COVER_HEADING_TEXTS:
                section = "cover"
                continue
            if text in BODY_START_HEADING_TEXTS:
                section = "body"
                continue

        if section == "cover":
            if blk.get("type") == "image" and not cover_taken:
                cover_url = blk.get("src")
                cover_taken = True
                section = "post_cover"  # 封面已收，後續若無 body marker 自動視為正文
            # 封面 section 內其他內容（caption、額外 heading）不進 body
            continue

        # pre / post_cover / body 都進 body
        body.append(blk)

    return cover_url, body


# ══════════════════════════════════════════════════════════
# Process pages → posts
# ══════════════════════════════════════════════════════════

async def _process_pages(
    client: httpx.AsyncClient,
    token: str,
    pages: list[dict],
    media_root: Path,
    categories: list[dict],
    dry_run: bool,
) -> tuple[list[dict], list[dict], list[str]]:
    """把已撈回的 page list 逐篇展開 blocks → IPost-compatible dicts。

    回傳：(posts, skipped, warnings)
    傳入的 `categories` 會被 in-place 更新 `count` 欄位（每篇 +1）。
    """
    posts: list[dict] = []
    skipped: list[dict] = []
    warnings: list[str] = []
    cat_by_name: dict[str, dict] = {c["name"]: c for c in categories}

    logger.info("[notion] processing %d published pages", len(pages))

    for page in pages:
        props = page.get("properties") or {}
        title = _prop_title(props)
        page_id = page.get("id", "")

        unique_id = _prop_unique_id(props)
        if unique_id is None:
            skipped.append({"title": title or "(無標題)", "reason": "缺『官網編號』"})
            continue
        if not title:
            skipped.append({"title": f"#{unique_id}", "reason": "缺『官網標題』"})
            continue

        slug = str(unique_id)

        multi = _prop_multi_select(props, PROP_CATEGORY)
        category_name = next((c for c in multi if c not in SKIP_CATEGORY_VALUES), "")
        if not category_name:
            skipped.append({"title": title, "reason": "分類為『待分類』或空，暫不發布"})
            continue

        cat_entry = cat_by_name.get(category_name)
        if cat_entry:
            category_id = cat_entry["id"]
            cat_entry["count"] += 1
        else:
            category_id = _category_slug(category_name)
            warnings.append(f"文章 #{slug} 分類 '{category_name}' 在 schema 不存在")

        try:
            raw_blocks = await list_block_children(client, token, page_id)
        except Exception as e:
            warnings.append(f"文章 #{slug}：無法讀取內容（{e}）")
            raw_blocks = []

        ctx = BlockContext(
            client=client, media_root=media_root, page_slug=slug,
            warnings=warnings, dry_run=dry_run,
        )
        # Gather per-page block processing — pure rich-text blocks no-op await；
        # image blocks do HTTP streams from S3 (not Notion API, no rate-limit concern).
        raw_bodies = await asyncio.gather(
            *[_block_to_post_block(rb, idx, ctx) for idx, rb in enumerate(raw_blocks)]
        )
        body = _merge_consecutive_lists([b for b in raw_bodies if b])

        # 套用「封面圖 / 內文與內文配圖」模板切分：拆出 cover、過濾區段標題
        template_cover, body = _split_cover_and_body(body)

        excerpt = _extract_excerpt(body)
        if not excerpt:
            warnings.append(f"文章 #{slug}：內文沒有 paragraph，摘要為空")

        label_zh = cat_entry["label_zh"] if cat_entry else category_name
        label_en = cat_entry["label_en"] if cat_entry else category_name

        # 封面優先級：Notion page cover（頁面頂部拖曳那張）> 模板「封面圖」section 的圖
        # （page cover 若是 file type 會 1hr 過期；模板 cover 已下載到 public/notion-media，
        #  所以實務上模板 cover 較穩。如果 page cover 是 external URL（unsplash 等）也 OK。）
        cover = _page_cover_url(page) or template_cover or ""

        # A2: 排序用 last_edited_time（使用者決策）
        posts.append({
            "slug": slug,
            "title": title,
            "category": category_id,
            "category_label_zh": label_zh,
            "category_label_en": label_en,
            "cover_url": cover,
            "excerpt": excerpt,
            "published_at": page.get("last_edited_time") or "",
            "body": body,
        })

    return posts, skipped, warnings


# ══════════════════════════════════════════════════════════
# 高層 entry: full sync
# ══════════════════════════════════════════════════════════

def _sync_result(
    *, ok: bool, posts: list[dict], categories: list[dict],
    skipped: list[dict], warnings: list[str], t0: float, error: str | None,
) -> dict:
    return {
        "ok": ok,
        "posts": posts,
        "categories": categories,
        "skipped": skipped,
        "warnings": warnings,
        "duration_ms": int((time.time() - t0) * 1000),
        "error": error,
    }


def _sync_error(t0: float, error: str) -> dict:
    return _sync_result(
        ok=False, posts=[], categories=[], skipped=[], warnings=[], t0=t0, error=error,
    )


async def run_full_sync(
    token: str,
    db_id: str,
    media_root: Path,
    dry_run: bool = False,
) -> dict:
    """完整同步 entry point：schema + pages + blocks。

    Args:
        media_root: e.g. {repo}/website/public/notion-media/
                    每個 page 下載到 {media_root}/{slug}/
        dry_run: True 時不下載媒體也不寫檔，只回傳 summary

    Returns dict matching `_sync_result()` shape.
    """
    t0 = time.time()
    db_id = _normalize_notion_id(db_id)
    if not db_id:
        return _sync_error(t0, "database_id 為空")
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            # Schema fetch 與 page list 兩個獨立 API call，平行
            (categories, warn_schema), pages = await asyncio.gather(
                fetch_category_schema(client, token, db_id),
                query_published_pages(client, token, db_id),
            )
            posts, skipped, warn_posts = await _process_pages(
                client, token, pages, media_root, categories, dry_run,
            )
        return _sync_result(
            ok=True, posts=posts, categories=categories, skipped=skipped,
            warnings=warn_schema + warn_posts, t0=t0, error=None,
        )
    except httpx.HTTPStatusError as e:
        return _sync_error(t0, f"Notion API HTTP {e.response.status_code}: {e.response.text[:300]}")
    except Exception as e:
        logger.exception("[notion-sync] failed")
        return _sync_error(t0, str(e))
