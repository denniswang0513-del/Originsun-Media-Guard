"""HTML 字串 → PDF 檔（Playwright headless Chromium）。

人員履歷 PDF 與報價單 PDF 共用這一條；回傳暫存 PDF 路徑，呼叫端用 FileResponse
送出並在 BackgroundTask 裡刪掉。HTML 暫存檔在這裡就清掉。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from typing import Optional

_DEFAULT_MARGIN = {"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"}


async def _render(tmp_html: str, tmp_pdf: str, margin: dict, footer_html: Optional[str]) -> None:
    """Playwright 本體：開 chromium 把 tmp_html 印成 A4 tmp_pdf。只准由 html_to_pdf（Proactor 執行緒）呼叫。"""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"file:///{tmp_html.replace(os.sep, '/')}", wait_until="networkidle")
        await page.emulate_media(media="print")
        kw = dict(path=tmp_pdf, format="A4", print_background=True, margin=margin)
        if footer_html:
            kw.update(display_header_footer=True, header_template="<span></span>",
                      footer_template=footer_html)
        await page.pdf(**kw)
        await browser.close()


def _run_in_proactor_thread(tmp_html: str, tmp_pdf: str, margin: dict, footer_html: Optional[str]) -> None:
    """🔴 生產主 loop 是 SelectorEventLoop（core/loopsetup：asyncpg 需要），它在 Windows
    起不了 asyncio subprocess —— Playwright 開 Chromium 會丟**空訊息**的 NotImplementedError
    （手機上就是「PDF 生成失敗：」後面什麼都沒有）。所以 Playwright 在工作執行緒裡自己開一個
    ProactorEventLoop 跑；直接 new ProactorEventLoop() 不受 main.py 設的 Selector policy 影響。"""
    loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_render(tmp_html, tmp_pdf, margin, footer_html))
    finally:
        loop.close()
        asyncio.set_event_loop(None)


async def html_to_pdf(html: str, *, prefix: str = "doc_",
                      margin: Optional[dict] = None,
                      footer_html: Optional[str] = None) -> str:
    """把 HTML 渲染成 A4 PDF，回傳暫存檔路徑。footer_html 給了就每頁印頁尾
    （Chromium 頁尾模板：可用 `<span class="pageNumber">`／`totalPages`，字型要自己 inline）。
    Playwright 在工作執行緒的 Proactor loop 裡跑（見 _run_in_proactor_thread），不佔主 loop。"""
    tmp_fd, tmp_html = tempfile.mkstemp(suffix=".html", prefix=prefix)
    os.close(tmp_fd)
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(html)
    tmp_pdf = tmp_html[:-5] + ".pdf"
    try:
        await asyncio.to_thread(_run_in_proactor_thread, tmp_html, tmp_pdf, margin or _DEFAULT_MARGIN, footer_html)
    except Exception:
        for path in (tmp_html, tmp_pdf):
            try:
                os.unlink(path)
            except OSError:
                pass
        raise
    try:
        os.unlink(tmp_html)
    except OSError:
        pass
    return tmp_pdf


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(_ROOT, "templates")


def render_template(name: str, **ctx) -> str:
    """templates/ 下的 Jinja2 模板 → HTML 字串（autoescape）。"""
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html"]))
    return env.get_template(name).render(**ctx)


_MIME = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}


def file_data_uri(path: str) -> str:
    """圖檔 → data URI（PDF 用 file:// 開 HTML，圖片內嵌最穩）；檔不存在／空路徑 → ""。
    相對路徑以 repo 根目錄解析（settings 裡的 logo_path／seal_path 可以寫 frontend/img/xxx.png）。"""
    if not path:
        return ""
    p = path if os.path.isabs(path) else os.path.join(_ROOT, path)
    if not os.path.isfile(p):
        return ""
    import base64
    mime = _MIME.get(os.path.splitext(p)[1].lower(), "application/octet-stream")
    with open(p, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")


def unlink_later(path: str):
    """給 BackgroundTask：檔案送完就刪。"""
    def _rm():
        if os.path.exists(path):
            os.unlink(path)
    return _rm
