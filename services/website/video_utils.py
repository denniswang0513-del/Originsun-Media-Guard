"""services/website/video_utils.py
---
主影片連結多平台解析（YouTube / Vimeo / Facebook / 一般連結）。

正本：`_YT_PATTERNS` + `_extract_youtube_id` 從 notion_service.py 搬入
（該檔留 re-export，既有 import 點全部不動）。新增 `parse_video_url()`
產「影片描述子」dict — 對外站 Astro 依 provider 選 embed 元件。

描述子形狀（前後端共同契約，改欄位名要同步 website/src）：
  {"provider": "youtube"|"vimeo"|"facebook"|"link",
   "video_id": str | None,      # youtube/vimeo 才有
   "embed_url": str | None}     # link 無 embed（前端渲染成外連）
空字串 / 非 URL 文字 → None（沒填主影片）。

⚠ 編輯器有一份 JS 鏡像（frontend/showcase-edit.html `_videoProvider()`，純提示用）—
這裡的 pattern 若增減平台（如加 Instagram），記得同步那份，否則提示會講錯話。

只用 stdlib（re + urllib.parse）— 無任何第三方依賴。
"""
from __future__ import annotations

import re
import urllib.parse

# ── YouTube（從 notion_service.py 原樣搬入，行為不變）──
_YT_PATTERNS = [
    re.compile(r"youtu\.be/([A-Za-z0-9_\-]{6,})"),
    re.compile(r"youtube\.com/watch\?v=([A-Za-z0-9_\-]{6,})"),
    re.compile(r"youtube\.com/embed/([A-Za-z0-9_\-]{6,})"),
    re.compile(r"youtube\.com/shorts/([A-Za-z0-9_\-]{6,})"),
]

# ── Vimeo：標準分享連結 vimeo.com/<純數字 id> ──
_VIMEO_PATTERN = re.compile(r"vimeo\.com/(\d+)")

# ── Facebook：三種常見影片 URL 形態（粉專影片 / watch 頁 / fb.watch 短網址）。
# FB 不給穩定的 video id 抽取（URL 形態太多），一律走 plugins/video.php
# 包整條原始 URL 的 embed 路線 → video_id 恆為 None。──
_FB_PATTERNS = [
    re.compile(r"facebook\.com/[^\s]+/videos/"),
    re.compile(r"facebook\.com/watch"),
    re.compile(r"fb\.watch/"),
]


def _extract_youtube_id(url: str) -> str | None:
    for pat in _YT_PATTERNS:
        m = pat.search(url or "")
        if m:
            return m.group(1)
    return None


def parse_video_url(url: str) -> dict | None:
    """解析主影片連結 → 影片描述子 dict；空值/非 URL → None。

    判定順序：youtube → vimeo → facebook → 一般 http(s) 連結。
    已知平台 pattern 命中就算數（容忍沒帶 scheme 的貼法，如 youtu.be/xxx）；
    都沒中時，只有 http:// 或 https:// 開頭才視為「一般連結」，
    其餘（純文字垃圾）→ None。
    """
    u = (url or "").strip()
    if not u:
        return None

    yt_id = _extract_youtube_id(u)
    if yt_id:
        # youtube-nocookie：隱私增強模式（不落追蹤 cookie），embed 行為與一般網域相同
        return {"provider": "youtube", "video_id": yt_id,
                "embed_url": f"https://www.youtube-nocookie.com/embed/{yt_id}"}

    m = _VIMEO_PATTERN.search(u)
    if m:
        vid = m.group(1)
        return {"provider": "vimeo", "video_id": vid,
                "embed_url": f"https://player.vimeo.com/video/{vid}"}

    if any(p.search(u) for p in _FB_PATTERNS):
        # FB embed 是把整條原始 URL encode 進 href 參數（safe="" 連 / 也 encode）
        href = urllib.parse.quote(u, safe="")
        return {"provider": "facebook", "video_id": None,
                "embed_url": ("https://www.facebook.com/plugins/video.php"
                              f"?href={href}&show_text=false")}

    if u.startswith("http://") or u.startswith("https://"):
        return {"provider": "link", "video_id": None, "embed_url": None}

    return None
