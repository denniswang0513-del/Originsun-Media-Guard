# -*- coding: utf-8 -*-
"""/my-ledger.html 版面釘子 —— 固定視窗高的殼要能捲。

2026-08-24 實測：財務 tab 的 #finance-content 帶 inline `overflow:hidden`
（原意是防 flex 橫向撐破），在主系統沒事（整頁跟著 body 捲），但
/my-ledger.html 是 `height:calc(100vh-40px); overflow:hidden` 的殼 ——
縱向也被關掉且**外部樣式表覆寫不了 inline** → 資產儀表板圖表以下整段
看不到、哪一層都捲不動。修法是兩半，缺一不可，所以兩半都釘。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_finance_content_blocks_horizontal_overflow_only():
    """inline 只准擋橫向 —— 寫 `overflow:hidden` 會連縱向一起關掉。"""
    html = _read("frontend/tabs/finance/finance.html")
    main_tag = next(ln for ln in html.splitlines() if 'id="finance-content"' in ln)
    assert "overflow-x:hidden" in main_tag, main_tag
    assert "overflow:hidden" not in main_tag, "inline overflow:hidden 會讓固定高的殼捲不動"


def test_my_ledger_makes_content_scrollable():
    """殼頁自己補縱向捲動（主系統靠 body 捲，本頁沒有 body 捲可用）。"""
    css = _read("frontend/my-ledger.html")
    assert "#app-view #finance-content" in css
    assert "overflow-y: auto" in css


def test_assets_fx_rate_not_string_divided():
    """匯率顯示不可拿 fmtNum() 的字串去做算術（字串 / 1000 → NaN，實測畫面出現）。"""
    js = _read("frontend/tabs/finance/subviews/assets.js")
    fx_line = next(ln for ln in js.splitlines() if "美元匯率" in ln)
    assert "fmtNum" not in fx_line, fx_line
    assert "toFixed" in fx_line, fx_line


def test_project_ledger_table_is_height_bounded():
    """逐案損益的表格要框在可視高度內（清單自己內捲）。

    2026-08-25 實測：不框高度時 #fpl-list-body 會長到 16,884px、內捲永不發生 ——
    (1) 表頭跟著整頁捲走；(2) 更嚴重的是詳情面板是清單的 flex 兄弟，捲到第 300
    列點開，詳情畫在整個表格頂端（往上一萬多 px）＝看不到。
    """
    js = _read("frontend/tabs/finance/subviews/projects.js")
    assert "_fitBody" in js
    fn = js.split("function _fitBody()")[1].split("\n}")[0]
    # 量出來的、不是寫死 px：要拿捲動容器的可視底部減表格頂端
    assert "getBoundingClientRect" in fn
    assert "finance-content" in fn
    assert "addEventListener('resize', _fitBody)" in js, "視窗縮放要重算"
