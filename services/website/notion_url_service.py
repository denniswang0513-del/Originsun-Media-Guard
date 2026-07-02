"""services/website/notion_url_service.py
---
單篇「公開 Notion 連結」匯入器（免 token / 免 integration）。

跟 notion_service.py 的差異：
- notion_service：官方 API（需 notion.token + database_id），批次撈整個資料庫。
- 本檔：貼一個「公開分享」的 Notion 頁面 URL → 用 Notion 公開站台的
  loadCachedPageChunkV2 端點抓 recordMap（不需任何金鑰），解析單篇文章。

用途：把任意公開 Notion 頁面（含他人 workspace 的分享頁）一鍵搬進影像專欄。
產出一篇 status='draft' 的文章交給 admin 過目，圖片下載 + WebP + host 到
/uploads/posts/{id}/，套用 Originsun 文章模板（封面圖 / 內文與內文配圖 / 文末內部筆記截斷）。

部署：admin Tab 走 getApiBase() → prod 打 NAS website-api 容器，所以匯入在 NAS 上跑、
圖片直接寫進 NAS 的 /app/uploads（對外即時可見），文章寫進共用 mediaguard，
mark_dirty → 60s debounce → master rebuild。
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import urllib.parse
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_website import WebsitePost
from . import notion_service as nsvc
from . import post_service, rebuild_service

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; OriginsunBlogImporter/1.0)"
_REQUEST_TIMEOUT = 30.0
_IMG_TIMEOUT = 60.0

# 上傳基礎路徑（NAS container：/app/uploads/；對齊 routers/website/admin_posts._UPLOAD_BASE）
_UPLOAD_BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "uploads",
)

# Originsun 文章模板 section markers（與 notion_service 對齊）+ 文末內部筆記截斷
_COVER_MARKERS = {"封面圖", "封面", "cover"}
_BODY_START_MARKERS = {"內文與內文配圖", "內文", "正文", "body", "content"}
# 文末這些 heading 之後都是內部撰寫筆記/草稿，不上稿（heading 文字「包含」即截斷）。
# 涵蓋 Originsun 模板常見的內部區段：撰寫流程、社群草稿、網頁摘要、SEO 關鍵字…
_BODY_END_KEYWORDS = (
    "參考資訊", "撰寫流程", "備註", "內部筆記", "協助撰寫", "關鍵字",
    "文章摘要", "網頁用文章摘要", "社群內容", "社群貼文", "社群文案", "社群草稿",
)
# 用作網頁摘要(excerpt)的內部區段標題（截斷前先撈出來當 SEO 摘要）
_SUMMARY_HEADING_KEYWORDS = ("文章摘要", "網頁摘要")

_HEX32 = re.compile(r"([0-9a-fA-F]{32})")


# ══════════════════════════════════════════════════════════
# URL / rich-text 解析
# ══════════════════════════════════════════════════════════

def _parse_page_ref(raw: str) -> tuple[str, str]:
    """從使用者貼的 URL（或裸 ID）解析出 (dashed_page_id, image_host)。

    image_host = 頁面所在站台 origin（如 https://shihyuanwang.notion.site），
    供 /image/ 代理下載圖片用；裸 ID 時 fallback www.notion.so。
    """
    s = (raw or "").strip()
    m = re.match(r"(https?://[^/]+)", s)
    image_host = m.group(1) if m else "https://www.notion.so"

    path = s.split("?", 1)[0].split("#", 1)[0]
    last = path.rstrip("/").split("/")[-1]
    hm = _HEX32.search(last.replace("-", ""))
    if not hm:
        hm = _HEX32.search(s.replace("-", ""))
    if not hm:
        raise ValueError("無法從連結解析出 Notion 頁面 ID，請確認貼的是公開分享連結")

    h = hm.group(1).lower()
    dashed = f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
    return dashed, image_host


def _rich(arr) -> str:
    """loadPageChunk 的 rich-text（[[text, [[annotation...]]], ...]）→ 純文字。
    inline 連結轉成 markdown [text](url)（與 notion_service 的 Astro 端解析相容）。
    """
    if not arr:
        return ""
    out: list[str] = []
    for seg in arr:
        if not seg:
            continue
        text = seg[0] if seg else ""
        if not isinstance(text, str):
            continue
        href = None
        anns = seg[1] if len(seg) > 1 else None
        for a in (anns or []):
            if a and isinstance(a, list) and a and a[0] == "a" and len(a) > 1:
                href = a[1]
        if href and text:
            safe = text.replace("[", "(").replace("]", ")")
            out.append(f"[{safe}]({href})")
        else:
            out.append(text)
    return "".join(out)


def _extract_date(page_props: dict) -> str | None:
    """掃 page 屬性找第一個內嵌 date（["d", {"start_date": ...}]）回傳 start_date。"""
    for v in (page_props or {}).values():
        for seg in (v or []):
            if not isinstance(seg, list) or len(seg) < 2:
                continue
            for ann in (seg[1] or []):
                if (isinstance(ann, list) and len(ann) > 1
                        and ann[0] == "d" and isinstance(ann[1], dict)):
                    sd = ann[1].get("start_date")
                    if sd:
                        return sd
    return None


def _match_categories(page_props: dict, existing_cats: list[dict]) -> list[str]:
    """把 page 屬性裡的文字 token 跟現有文章分類比對（label_zh/label_en/slug），
    回傳命中的 slug（去重保序）。沒命中 → []，交給 admin 在草稿選。
    """
    tokens: set[str] = set()
    for key, v in (page_props or {}).items():
        if key == "title":            # 標題不拿來比對分類
            continue
        txt = _rich(v)
        for part in re.split(r"[,，;；/、\s]+", txt):
            part = part.strip()
            if part:
                tokens.add(part)
                tokens.add(part.lower())
    matched: list[str] = []
    for c in existing_cats:
        for cand in (c.get("label_zh"), c.get("label_en"), c.get("slug")):
            if cand and (cand in tokens or cand.lower() in tokens):
                if c["slug"] not in matched:
                    matched.append(c["slug"])
                break
    return matched


# ══════════════════════════════════════════════════════════
# Block tree → PostBlock[]
# ══════════════════════════════════════════════════════════

# Notion heading 深度（header 最淺）。最終 level 由 _finalize_headings 正規化：
# 文章用到的「最淺」層級 → H2（renderer 的最大級），更深的 → H3。
# 這樣只用單一層級（如全 sub_sub_header）的文章，section 標題也會是漂亮的 H2。
_HEADING_SRC = {"header": 1, "sub_header": 2, "sub_sub_header": 3}


def _finalize_headings(blocks: list[dict]) -> list[dict]:
    """把 heading 的 _src 深度正規化成 renderer 支援的 level（2/3）。就地修改。"""
    depths = [b["_src"] for b in blocks if b.get("type") == "heading" and "_src" in b]
    shallowest = min(depths) if depths else 1
    for b in blocks:
        if b.get("type") == "heading":
            src = b.pop("_src", shallowest)
            b["level"] = 2 if src == shallowest else 3
    return blocks


# Notion 把整篇內容常巢狀在一個容器 block（甚至 column / toggle）底下，所以
# 一律遞迴 children。numbered_list 用「同層連續 run + counter」處理，才能在
# 「每個項目底下掛描述段落/子清單」的結構下仍維持正確的 1.2.3. 編號。

# 會略過、且不遞迴 children 的 block（表格 / collection / divider 多為版面或內部用）
_SKIP_TYPES = {"divider", "table", "table_row", "collection_view",
               "collection_view_page", "toggle_unsupported"}


def _emit_block(b: dict, nodes: dict) -> list[dict]:
    """單一非清單 block → PostBlock(s)，並遞迴其 children。"""
    t = b.get("type")
    props = b.get("properties") or {}
    fmt = b.get("format") or {}
    txt = _rich(props.get("title"))
    out: list[dict] = []

    if t == "text":
        if txt.strip():
            out.append({"type": "paragraph", "text": txt})
    elif t in _HEADING_SRC:
        if txt.strip():
            out.append({"type": "heading", "_src": _HEADING_SRC[t], "text": txt})
    elif t == "quote":
        if txt.strip():
            out.append({"type": "quote", "text": txt})
    elif t == "callout":
        emoji = (fmt.get("page_icon") or "").strip()
        merged = f"{emoji} {txt}".strip() if emoji else txt
        if merged.strip():
            out.append({"type": "paragraph", "text": merged})
    elif t == "image":
        src = fmt.get("display_source") or _rich(props.get("source"))
        if src:
            out.append({
                "type": "image",
                "_pending": {"src": src, "block_id": b.get("id")},
                "caption": _rich(props.get("caption")) or None,
                "width": "content",
            })
        return out                     # 圖片不遞迴
    elif t in ("video", "embed"):
        src = fmt.get("display_source") or _rich(props.get("source"))
        yt = nsvc._extract_youtube_id(src or "")
        if yt:
            out.append({
                "type": "video", "youtube_id": yt,
                "caption": _rich(props.get("caption")) or None, "width": "content",
            })
        return out
    elif t in _SKIP_TYPES:
        return out                     # 略過且不遞迴

    # text / heading / quote / callout / column / column_list / page / 其餘容器 → 遞迴 children
    out.extend(_walk_children(b.get("content", []), nodes))
    return out


def _emit_numbered_run(run: list[dict], nodes: dict) -> list[dict]:
    """一串同層連續的 numbered_list。無子內容 → 乾淨 ordered list；
    有子內容（步驟含描述/子清單）→ 「N. 標題」段落 + 原位遞迴 children，
    讓編號正確且描述留在各自項目下。"""
    out: list[dict] = []
    has_children = any(nb.get("content") for nb in run)
    if not has_children:
        items = [t for t in (_rich((nb.get("properties") or {}).get("title")) for nb in run)
                 if t.strip()]
        if items:
            out.append({"type": "list", "items": items, "ordered": True})
        return out
    for n, nb in enumerate(run, 1):
        title = _rich((nb.get("properties") or {}).get("title"))
        if title.strip():
            out.append({"type": "paragraph", "text": f"{n}. {title}"})
        out.extend(_walk_children(nb.get("content", []), nodes))
    return out


def _emit_bulleted_run(run: list[dict], nodes: dict) -> list[dict]:
    """一串同層連續的 bulleted_list → 合成單一 unordered list；項目若有子內容
    則先收尾目前清單、原位遞迴子內容。"""
    out: list[dict] = []
    items: list[str] = []
    for nb in run:
        title = _rich((nb.get("properties") or {}).get("title"))
        if title.strip():
            items.append(title)
        kids = nb.get("content", [])
        if kids:
            if items:
                out.append({"type": "list", "items": items})
                items = []
            out.extend(_walk_children(kids, nodes))
    if items:
        out.append({"type": "list", "items": items})
    return out


def _walk_children(ids: list[str], nodes: dict) -> list[dict]:
    """依序展開一層 children；把連續的 numbered/bulleted 收成 run 統一處理。"""
    out: list[dict] = []
    i = 0
    n = len(ids)
    while i < n:
        b = nodes.get(ids[i])
        if not b:
            i += 1
            continue
        t = b.get("type")
        if t in ("numbered_list", "bulleted_list"):
            run = []
            while i < n:
                nb = nodes.get(ids[i])
                if nb and nb.get("type") == t:
                    run.append(nb)
                    i += 1
                else:
                    break
            out.extend(_emit_numbered_run(run, nodes) if t == "numbered_list"
                       else _emit_bulleted_run(run, nodes))
            continue
        out.extend(_emit_block(b, nodes))
        i += 1
    return out


def _extract_web_summary(blocks: list[dict]) -> str | None:
    """撈「網頁用文章摘要」section 下的第一段文字當 excerpt（截斷前呼叫）。"""
    for i, b in enumerate(blocks):
        if b.get("type") == "heading" and any(
                k in (b.get("text") or "") for k in _SUMMARY_HEADING_KEYWORDS):
            for nb in blocks[i + 1:]:
                if nb.get("type") == "heading":
                    break
                if nb.get("type") == "paragraph" and (nb.get("text") or "").strip():
                    return nb["text"].strip()
    return None


def _split_cover_body(blocks: list[dict]) -> tuple[dict | None, list[dict]]:
    """套用 Originsun 模板：抽封面、過濾區段標題、文末內部筆記截斷。"""
    cover: dict | None = None
    body: list[dict] = []
    section = "pre"
    cover_taken = False

    for blk in blocks:
        if blk.get("type") == "heading":
            text = (blk.get("text") or "").strip()
            low = text.lower()
            if text in _COVER_MARKERS or low in _COVER_MARKERS:
                section = "cover"
                continue
            if text in _BODY_START_MARKERS or low in _BODY_START_MARKERS:
                section = "body"
                continue
            if any(k in text for k in _BODY_END_KEYWORDS):
                break  # 文末內部筆記起點 → 不再收

        if section == "cover":
            if blk.get("type") == "image" and not cover_taken:
                cover = blk
                cover_taken = True
                section = "post_cover"
            continue  # 封面 section 內其他內容不進 body

        body.append(blk)

    return cover, body


# ══════════════════════════════════════════════════════════
# HTTP：抓 page chunk + 下載圖片
# ══════════════════════════════════════════════════════════

async def _fetch_nodes(client: httpx.AsyncClient, page_id: str, image_host: str) -> dict:
    """POST loadCachedPageChunkV2，回 {block_id: block_value} 已攤平。"""
    body = {"page": {"id": page_id}, "limit": 300,
            "cursor": {"stack": []}, "verticalColumns": False}
    last_err: Exception | None = None
    for host in (image_host, "https://www.notion.so"):
        try:
            r = await client.post(f"{host}/api/v3/loadCachedPageChunkV2",
                                  json=body, headers={"User-Agent": _UA})
            r.raise_for_status()
            raw = (r.json().get("recordMap") or {}).get("block") or {}
            nodes = {k: v["value"]["value"]
                     for k, v in raw.items()
                     if v.get("value", {}).get("value")}
            if nodes:
                return nodes
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"讀取 Notion 頁面失敗（可能不是公開分享頁）：{last_err}")


async def _download_image(client: httpx.AsyncClient, image_host: str,
                          block_id: str, src: str) -> bytes:
    """經 Notion 站台 /image/ 代理下載（attachment 與過期 S3 簽名都靠它取回）。"""
    enc = urllib.parse.quote(src, safe="")
    url = f"{image_host}/image/{enc}?table=block&id={block_id}&cache=v2"
    try:
        r = await client.get(url, headers={"User-Agent": _UA}, timeout=_IMG_TIMEOUT)
        r.raise_for_status()
        return r.content
    except Exception:
        if src.startswith("http"):  # 純外部圖 fallback 直抓
            r = await client.get(src, headers={"User-Agent": _UA}, timeout=_IMG_TIMEOUT)
            r.raise_for_status()
            return r.content
        raise


def _save_webp(content: bytes, dest_dir: str, base_name: str) -> str:
    """bytes → dest_dir/{base_name}.webp（quality 82）。回相對檔名。"""
    os.makedirs(dest_dir, exist_ok=True)
    from PIL import Image
    img = Image.open(io.BytesIO(content))
    img = img.convert("RGBA") if img.mode in ("RGBA", "LA") else img.convert("RGB")
    out_name = base_name + ".webp"
    img.save(os.path.join(dest_dir, out_name), "WEBP", quality=82, method=6)
    return out_name


async def _host_image(client: httpx.AsyncClient, image_host: str, pending: dict,
                      dest_dir: str, base_name: str) -> str | None:
    """下載 + 轉 WebP + 落地。失敗回 None（caller 記 warning）。"""
    try:
        content = await _download_image(client, image_host,
                                        pending["block_id"], pending["src"])
        return await asyncio.to_thread(_save_webp, content, dest_dir, base_name)
    except Exception as e:  # noqa: BLE001
        logger.warning("[notion-url] image host failed (%s): %s", pending.get("src"), e)
        return None


# ══════════════════════════════════════════════════════════
# 高層 entry
# ══════════════════════════════════════════════════════════

async def import_public_page(session: AsyncSession, url: str) -> dict:
    """主入口：公開 Notion URL → 建立一篇 draft 文章（圖片已 host）。"""
    warnings: list[str] = []
    try:
        page_id, image_host = _parse_page_ref(url)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    # 去重：同 notion_page_id 已匯入過 → 不重複建
    dup = (await session.execute(
        select(WebsitePost.id, WebsitePost.slug)
        .where(WebsitePost.notion_page_id == page_id)
    )).first()
    if dup:
        return {"ok": False, "post_id": dup[0], "slug": dup[1],
                "error": f"這篇已匯入過（文章 slug {dup[1]}）。要重匯請先在「📰 文章」刪除舊的。"}

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=True) as client:
            nodes = await _fetch_nodes(client, page_id, image_host)
            page = nodes.get(page_id) or next(
                (b for b in nodes.values() if b.get("type") == "page"), None)
            if not page:
                return {"ok": False, "error": "找不到頁面內容（連結錯誤或頁面未公開分享）"}

            page_props = page.get("properties") or {}
            title = _rich(page_props.get("title")) or "未命名文章"

            raw = _walk_children(page.get("content", []), nodes)
            raw = nsvc._merge_consecutive_lists(raw)
            web_summary = _extract_web_summary(raw)   # 截斷前先撈內部摘要
            cover_block, body = _split_cover_body(raw)
            _finalize_headings(body)

            existing_cats = await post_service.list_categories(session)
            cat_slugs = _match_categories(page_props, existing_cats)
            excerpt = web_summary or nsvc._extract_excerpt(body)
            date_iso = _extract_date(page_props)

            # 1) 先建 draft 取 id（圖片路徑要 /uploads/posts/{id}/）
            create_data: dict = {
                "title": title,
                "excerpt": excerpt or None,
                "category_slugs": cat_slugs,
                "status": "draft",
                "body": [],
                "notion_page_id": page_id,
            }
            if date_iso:
                try:
                    dt = datetime.fromisoformat(date_iso)
                    now = datetime.now(timezone.utc)
                    dt_aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                    if dt_aware > now:
                        # 來源日期在未來 → 發布後會被當排程稿隱藏。改用今天並警告。
                        warnings.append(
                            f"來源日期 {date_iso} 在未來，已改用今天"
                            "（未來日期發布後會被前台當排程稿隱藏）")
                        create_data["published_at"] = now
                    else:
                        create_data["published_at"] = dt
                except ValueError:
                    warnings.append(f"日期 '{date_iso}' 無法解析，已略過")
            created = await post_service.create_post(session, create_data,
                                                     auto_create_categories=False)
            pid = created["id"]
            dest_dir = os.path.join(_UPLOAD_BASE, "posts", str(pid))

            # 2) host 封面 + 內文圖
            img_count = 0
            cover_url: str | None = None
            if cover_block and cover_block.get("_pending"):
                rel = await _host_image(client, image_host,
                                        cover_block["_pending"], dest_dir, "cover")
                if rel:
                    cover_url = f"/uploads/posts/{pid}/{rel}"
                    img_count += 1
                else:
                    warnings.append("封面圖下載失敗")

            final_body: list[dict] = []
            bi = 0
            for blk in body:
                if blk.get("type") == "image" and blk.get("_pending"):
                    bi += 1
                    rel = await _host_image(client, image_host,
                                            blk["_pending"], dest_dir, f"body-{bi}")
                    if rel:
                        final_body.append({
                            "type": "image",
                            "src": f"/uploads/posts/{pid}/{rel}",
                            "alt": blk.get("alt") or title,
                            "caption": blk.get("caption"),
                            "width": "content",
                        })
                        img_count += 1
                    else:
                        warnings.append(f"內文第 {bi} 張圖下載失敗，已略過")
                else:
                    final_body.append(blk)

            # 3) 回填 body + 封面
            upd: dict = {"body": final_body}
            if cover_url:
                upd["cover_url"] = cover_url
                upd["og_image_url"] = cover_url
            updated = await post_service.update_post(session, pid, upd)

        await rebuild_service.mark_dirty()

        if not body:
            warnings.append("沒有抓到內文（可能此頁沒套用「內文與內文配圖」標題模板）")

        return {
            "ok": True,
            "post_id": pid,
            "slug": updated["slug"],
            "title": title,
            "image_count": img_count,
            "block_count": len(final_body),
            "category_slugs": cat_slugs,
            "published_at": updated.get("published_at"),
            "warnings": warnings,
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("[notion-url-import] failed")
        return {"ok": False, "error": str(e)}
