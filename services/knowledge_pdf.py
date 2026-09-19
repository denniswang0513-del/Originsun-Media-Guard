# -*- coding: utf-8 -*-
"""services/knowledge_pdf.py — 把一本書整理出來的東西印成 PDF（§9.10）。

owner 2026-09-19：「這裡多一個 pdf 下載，讓大家可以下載資料」—— 他要把書裡的研究
當作專案研究資料發給人，收到分享連結的人也要拿得走一份。

兩個入口，**同一份 HTML**：
  - 書頁（私有）：整本，他自己看得到的都印。
  - 分享頁（公開）：只印他**勾選**的那幾段（`knowledge_share.public_view` 已經篩過了，
    這支只負責排版）。

🔴 圖一律**內嵌成 data URI**：PDF 是用 `file://` 開一份暫存 HTML 來印的，
   相對路徑與需要授權的網址都抓不到（`html_pdf.file_data_uri` 的註解也寫著這件事）。
"""
from __future__ import annotations

import html as _html
import os
import re
from typing import Optional

from core import knowledge_logic as kl

#: 一份 PDF 最多內嵌幾張圖（每張都是 base64，太多會讓檔案大到寄不出去）
MAX_IMAGES = 60


def _esc(v) -> str:
    return _html.escape(str(v or ""), quote=True)


def _md(text: str, images: Optional[dict] = None) -> str:
    """markdown → HTML。能力對齊分享頁那份（標題／粗體／行內 code／清單／表格／圖片）。

    `images`＝`{檔名: data URI}`；沒給或找不到的那張就整個跳過（PDF 裡不要有破圖）。
    """
    lines = _esc(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list = []
    i = 0
    ul = False

    def close_ul():
        nonlocal ul
        if ul:
            out.append("</ul>")
            ul = False

    def inline(s: str) -> str:
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)

    def is_sep(s: str) -> bool:
        return bool(re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", s))

    def cells(s: str) -> list:
        t = s.strip()
        if t.startswith("|"):
            t = t[1:]
        if t.endswith("|"):
            t = t[:-1]
        return [c.strip() for c in t.split("|")]

    while i < len(lines):
        line = lines[i].strip()
        if not line:
            close_ul()
            i += 1
            continue
        img = re.match(r"^!\[([^\]]*)\]\((assets/[A-Za-z0-9._-]+)\)$", line)
        if img:
            close_ul()
            i += 1
            uri = (images or {}).get(img.group(2).split("/", 1)[1], "")
            if uri:
                out.append(f'<figure><img src="{uri}">'
                           + (f"<figcaption>{img.group(1)}</figcaption>" if img.group(1) else "")
                           + "</figure>")
            continue
        h = re.match(r"^(#{1,4})\s+(.+)$", line)
        if h:
            close_ul()
            n = min(len(h.group(1)) + 1, 4)
            out.append(f"<h{n}>{inline(h.group(2))}</h{n}>")
            i += 1
            continue
        if "|" in line and i + 1 < len(lines) and is_sep(lines[i + 1]):
            close_ul()
            head = cells(line)
            rows = []
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(cells(lines[i]))
                i += 1
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(r[k] if k < len(r) else '')}</td>"
                                                  for k in range(len(head))) + "</tr>" for r in rows)
                       + "</tbody></table>")
            continue
        li = re.match(r"^[-*]\s+(.+)$", line)
        if li:
            if not ul:
                out.append("<ul>")
                ul = True
            out.append(f"<li>{inline(li.group(1))}</li>")
            i += 1
            continue
        close_ul()
        out.append(f"<p>{inline(line)}</p>")
        i += 1
    close_ul()
    return "".join(out)


_CSS = """
@page { size: A4; margin: 18mm 16mm 20mm; }
body { font-family: "Noto Sans TC", "Microsoft JhengHei", sans-serif; font-size: 10.5pt;
  line-height: 1.75; color: #1b1b1b; }
h1 { font-size: 19pt; margin: 0 0 4pt; }
.who { color: #666; font-size: 11pt; }
.facts { color: #666; font-size: 9.5pt; margin-top: 2pt; }
.tags { color: #666; font-size: 9pt; margin-top: 6pt; }
h2.sec { font-size: 13pt; margin: 22pt 0 4pt; padding-top: 8pt; border-top: 1px solid #ddd;
  page-break-after: avoid; }
.note { color: #666; font-size: 9pt; margin: 0 0 8pt; }
.item { border: 1px solid #e2e2e2; border-radius: 4pt; padding: 8pt 10pt; margin-bottom: 8pt;
  page-break-inside: avoid; }
.item .t { font-size: 11pt; font-weight: 600; }
.item .u { color: #444; font-size: 8.5pt; word-break: break-all; }
.item .src, .item .why { color: #666; font-size: 9pt; margin-top: 3pt; }
.item .sum { font-size: 10pt; margin-top: 4pt; }
h3.ch { font-size: 12pt; margin: 16pt 0 4pt; page-break-after: avoid; }
table { border-collapse: collapse; font-size: 9pt; margin: 6pt 0; width: 100%; }
th, td { border: 1px solid #ddd; padding: 3pt 5pt; text-align: left; vertical-align: top; }
th { background: #f4f4f4; }
figure { margin: 8pt 0; page-break-inside: avoid; }
figure img { max-width: 100%; }
figcaption { color: #666; font-size: 8.5pt; margin-top: 2pt; }
ul { padding-left: 14pt; margin: 4pt 0; }
code { background: #f2f2f2; padding: 0 2pt; }
.foot { color: #777; font-size: 8.5pt; margin-top: 18pt; padding-top: 8pt; border-top: 1px solid #ddd; }
"""


def _item(it: dict) -> str:
    src = "　".join(x for x in (it.get("source"), it.get("published")) if x)
    return ('<div class="item">'
            f'<div class="t">{_esc(it.get("title_zh") or it.get("title_original"))}</div>'
            + (f'<div class="u">{_esc(it.get("url"))}</div>' if it.get("url") else "")
            + (f'<div class="src">出處：{_esc(src)}</div>' if src else "")
            + (f'<div class="sum">{_esc(it.get("summary_zh"))}</div>' if it.get("summary_zh") else "")
            + (f'<div class="why">為什麼值得看：{_esc(it.get("why_it_matters"))}</div>'
               if it.get("why_it_matters") else "")
            + "</div>")


def build_html(d: dict, images: Optional[dict] = None) -> str:
    """`knowledge_share.public_view` 那個形狀（或書頁的全量）→ 一份可以印的 HTML。

    沒有的段落就不出現 —— 公開那條已經照勾選篩過了，這裡不用再判一次。
    """
    info = d.get("info") or {}
    facts = "　・　".join(_esc(info[k]) for k in ("publisher", "published", "translator", "edition", "isbn")
                         if info.get(k))
    who = "《" + _esc(d.get("title")) + "》" + ("　" + _esc(d.get("author")) if d.get("author") else "")
    parts = [f'<h1>{_esc(d.get("title"))}</h1>']
    if d.get("author"):
        parts.append(f'<div class="who">{_esc(d["author"])}</div>')
    if info.get("subtitle"):
        parts.append(f'<div class="facts">{_esc(info["subtitle"])}</div>')
    if facts:
        parts.append(f'<div class="facts">{facts}</div>')
    if d.get("tags"):
        parts.append('<div class="tags">' + "　".join("#" + _esc(t) for t in d["tags"]) + "</div>")

    if d.get("extend"):
        parts.append(f'<h2 class="sec">延伸研究（{len(d["extend"])}）</h2>')
        parts.append('<p class="note">讀這本書之後在網路上找到的資料，每一則都附原始出處與連結。'
                     "這些是公開資料，不是這本書的作者說的。</p>")
        parts += [_item(i) for i in d["extend"]]
    if d.get("conclusion"):
        parts += ['<h2 class="sec">結論</h2>', _md(d["conclusion"], images)]
    if d.get("notes"):
        parts += ['<h2 class="sec">筆記</h2>', _md(d["notes"], images)]
    if d.get("skill"):
        parts += ['<h2 class="sec">骨架</h2>',
                  '<p class="note">以下是針對原書內容整理的筆記，不是原文。</p>',
                  _md(d["skill"], images) + _md(d.get("cheatsheet") or "", images)]
    if d.get("chapters"):
        parts += ['<h2 class="sec">章節重點</h2>',
                  '<p class="note">以下是針對原書內容整理的筆記，不是原文。</p>']
        for c in d["chapters"]:
            md = c.get("md") or ""
            # 每章的 md 第一行本來就是 `# 第 N 章 …`（編譯時寫進去的）。再補一個標題
            # 會變成同一行印兩次 —— 只有那一行不見了才由我們補。
            if not md.lstrip().startswith("#"):
                parts.append(f'<h3 class="ch">第 {_esc(c.get("n"))} 章 {_esc(c.get("title"))}</h3>')
            parts.append(_md(md, images))
    if d.get("gallery"):
        parts.append(f'<h2 class="sec">圖輯（{len(d["gallery"])}）</h2>')
        for a in d["gallery"]:
            uri = (images or {}).get(a.get("name"), "")
            if not uri:
                continue
            parts.append(f'<figure><img src="{uri}"><figcaption>{_esc(a.get("caption") or "（原書沒有圖說）")}'
                         f'<br>出自{who}，第 {_esc(a.get("page"))} 頁</figcaption></figure>')

    parts.append('<div class="foot">以上是針對' + who + "整理的資料，書本身的內容不在這裡。"
                 "要看原書請支持原作者與出版社。<br>由 Knowledge 知識庫整理"
                 + (f"・{_esc(str(d.get('shared_at'))[:10])}" if d.get("shared_at") else "") + "</div>")
    return ("<!doctype html><html lang=\"zh-Hant\"><head><meta charset=\"utf-8\">"
            f"<title>{_esc(d.get('title'))}</title><style>{_CSS}</style></head><body>"
            + "".join(parts) + "</body></html>")


def images_for(book_id: str, names) -> dict:
    """`{檔名: data URI}`。PDF 用 `file://` 開，所以圖一定要內嵌。"""
    from services import knowledge_service as ks
    from services.html_pdf import file_data_uri
    out = {}
    for name in list(names)[:MAX_IMAGES]:
        if not kl.is_valid_asset(name):
            continue
        try:
            path = ks.asset_path(book_id, name)
        except Exception:
            continue
        if os.path.isfile(path):
            uri = file_data_uri(path)
            if uri:
                out[name] = uri
    return out


def wanted_images(d: dict) -> list:
    """這一份要內嵌哪幾張：圖輯全部 ＋ 文章裡插進去的那幾張。"""
    names = [a.get("name") for a in (d.get("gallery") or []) if a.get("name")]
    blobs = [d.get("conclusion") or "", d.get("notes") or "", d.get("skill") or "", d.get("cheatsheet") or ""]
    blobs += [c.get("md") or "" for c in (d.get("chapters") or [])]
    for text in blobs:
        names += [m.split("/", 1)[1] for m in re.findall(r"assets/[A-Za-z0-9._-]+", text)]
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


#: 🔴 守在**呼叫的時候**不是 import 的時候 —— `html_pdf` 把 `import playwright` 放在函式裡，
#   所以這個模組 import 得起來。NAS 的容器刻意不裝 Playwright（同報價單那支的說明）。
PDF_UNAVAILABLE = "下載 PDF 需要主控主機在線（這台沒有產 PDF 的元件）"


async def pdf_response(book_id: str, d: dict):
    """一本書（或一份公開分享）→ 可以下載的 PDF。書頁與公開頁共用這一個出口。

    `d` 就是 `knowledge_share.full_view` / `public_view` 那一包 —— 公開那條已經照勾選
    篩過了，這裡不會再多拿任何東西。
    """
    import asyncio

    from fastapi import HTTPException
    from starlette.background import BackgroundTask

    from core.no_store import no_store_file
    from services.html_pdf import html_to_pdf, unlink_later
    try:
        # 🔴 這一段是**同步的重工**：最多 60 張圖各讀一次檔再轉 base64，再組出幾百 KB 的 HTML。
        #    留在事件迴圈上的話，一個人按下載會讓整台主控停住好幾秒（2026-09-19 /polish
        #    BUG-9 把讀檔搬進執行緒，但這一段當時在我手上、它沒動到）。
        html = await asyncio.to_thread(lambda: build_html(d, images_for(book_id, wanted_images(d))))
        tmp_pdf = await html_to_pdf(html, prefix="knowledge_")
    except (ImportError, ModuleNotFoundError) as exc:
        raise HTTPException(status_code=503, detail=PDF_UNAVAILABLE) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF 生成失敗：{exc}")
    # 書是私有的，公開那份是原書的內容 —— 兩邊都不留快取副本，送完就刪
    return no_store_file(tmp_pdf, media_type="application/pdf", filename=filename_for(d),
                         background=BackgroundTask(unlink_later(tmp_pdf)))


def filename_for(d: dict) -> str:
    """下載時的檔名：`書名-研究筆記.pdf`（不能用的字在 `kl.safe_filename` 統一換掉）。"""
    return kl.safe_filename(f"{str(d.get('title') or 'knowledge')[:60]}-研究筆記.pdf",
                            "knowledge-研究筆記.pdf")
