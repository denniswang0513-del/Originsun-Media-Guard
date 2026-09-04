"""報表膠卷圖進視窗才解碼（2026-09-04）：一份 532 檔的報表 36 MB、532 張 2400px 膠卷全解碼 ≈ 700 MB，
手機瀏覽器往下捲就被砍（「無法開啟這個網頁」）。base64 留在 data-src、IntersectionObserver 進場才給 src、
離場卸掉；PDF 轉檔前呼叫 window.__reportLoadAll 把整份載齊。
實測（headless Chromium 390×844 捲完 532 檔）：同時解碼峰值 24 張、__reportLoadAll 後 532/532。"""
from tests.unit._srcscan import repo_src


def test_film_strips_are_not_inline_src():
    tpl = repo_src("templates/report.html")
    assert ' src="data:image/jpeg;base64,' not in tpl, "膠卷圖不能直接放 src，手機會整頁崩掉"
    assert 'data-src="data:image/jpeg;base64,{{ rec.film_strip_b64 }}"' in tpl
    assert 'width="2400" height="135"' in tpl, "沒尺寸的話卸掉 src 版面會跳"
    assert "IntersectionObserver" in tpl and "window.__reportLoadAll" in tpl
    assert "removeAttribute('src')" in tpl, "離開視窗要卸掉，不然只是延後崩"
    assert "content-visibility: auto" in tpl


def test_pdf_render_loads_every_strip_first():
    src = repo_src("report_generator.py")
    body = src.split("async def _render_pdf", 1)[1]
    assert "__reportLoadAll" in body
    assert body.index("__reportLoadAll") < body.index("page.pdf("), "要在 page.pdf 之前把圖載齊"
